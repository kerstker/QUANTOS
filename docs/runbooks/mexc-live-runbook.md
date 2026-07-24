# MEXC 现货实盘 runbook(人工首单路径)

状态:human-ready 代码面已就绪(2026-07 MEXC 对接 Part 1)。MEXC **无现货 testnet**,
毕业路径 = 干跑 → 只读首验 → 人工 mandate + attestation → 显式 `--arm-live` →
≤$100 首单 → 对账 → 规模化(每轮新多方批准)。设计契约见 `docs/MEXC_INTEGRATION.md`。

**AI agent 绝不执行本文件的任何一步**:不放 key、不跑 smoke、不签 mandate、不带
`--arm-live` 运行驱动器。每一步都是人工操作。

---

## 0. 前置事实(先读再动手)

- 适配器 `live/exchange/mexc_spot.py`:mainnet 构造在默认 SafetyConfig 下即
  `SafetyViolation`;唯一合法 base_url = `https://api.mexc.com`。
- 驱动器 `tools/live_track_advance.py --venue mexc --strategy btc_spot_trend`:
  不带 `--submit --arm-live` 直接 SystemExit(mainnet-only spot 的 L2 结构门)。
- `--arm-live` 是**唯一** arming 路径(人工 CLI 专属):构造 armed SafetyConfig
  (`allow_ai_direct_trading=False`、`require_human_approval=True` 恒保持)。
  ops API(8440)的固定 argv **永不**携带它(governance 测试钉死)。
- 即便 armed,以下任一缺失仍 fail-closed 零提交:validation attestation、
  人工 mandate(AUTO 一律拒绝 → NEEDS_HUMAN_REVIEW)、kill-switch 未跳闸、
  pre-trade gate 通过、启动 key 权限校验(§10)、现货现金检查(§1.4)。
- 账本只有**数量腿**(LiveLedger 无 quote 现金腿):现金腿以交易所账户为准,
  驱动层 pre-submit 用自由 USDT 做现金门;对账对的是数量,不是成本。

## 1. 首单 checklist(按序,每步不过就停)

1. **放 key**:在 MEXC 控制台创建现货 API key —— 权限只勾 spot trade + read,
   **提现/划转必须关闭**,并绑定 IP 白名单(API 无法验证,只能网页确认)。
   写入 `key/mexc_spot.json`(gitignored):
   `{"api_key": "...", "api_secret": "...", "base_url": "https://api.mexc.com"}`
   或设 env `MEXC_API_KEY` / `MEXC_API_SECRET`。
2. **权限校验**:`python -m tools.verify_key_permissions --venue mexc`
   → 必须 SAFE(canTrade=true ∧ canWithdraw=false)。UNSAFE/UNKNOWN 一律停。
3. **只读 smoke**:`python -m tools.mexc_readonly_smoke`
   → 校验 server_time / exchangeInfo / account 字段形状,落
   `evidence/mexc_mainnet_smoke.json`,verdict 必须 PASS。此前 console
   `mexc-readiness` 端点该项诚实报 `UNVERIFIED_MAINNET_RESPONSE`。
4. **数据就位**:`python -m tools.live_data_refresh`(第 5 源落
   `data/cache/crypto_mexc.db` + `mexc_btc_klines.json`),然后
   `python -m tools.dry_run_btc_spot_multi` 重跑 MEXC 主口径干跑 +
   前向 paper-track(paper_track.db)。
5. **手写 mandate**:复制 `key/live_mandate_mexc.json.template` 为
   `key/live_mandate_mexc.json` 并亲笔填数字:`venue="mainnet"`、
   `exchange="mexc"`、`allowed_instruments=["BTC"]`,首单额度建议
   `max_single_order_notional ≤ 100`、`max_gross_notional ≤ 100`、
   `max_net_notional ≤ 100`、`expires_at` 给短窗口。代码不代填任何数字。
6. **手写 attestation**:在 `key/live_validation_attestations.json` 为
   `btc_spot_trend` 加 `human_override` 条目(模板生成:
   `python -m tools.create_validation_attestation_template`),真人署名 + 理由。
7. **研究证据桥**:`evidence/alpha_paper_cycle.json` 目前只覆盖
   carry/crowd_contra/top_contra——`btc_spot_trend` 未注册时,研究桥会
   fail-closed 报 `alpha_paper_status_missing` 并跳批。把 btc_spot_trend 纳入
   paper cycle 是一步**独立的研究治理决定**(扩 `tools/alpha_paper_cycle.py`
   的 ALPHAS 注册表并重生成证据),不是本 runbook 能绕过的门。
8. **首单**(人工,显式 arm):
   `python -m tools.live_track_advance --venue mexc --strategy btc_spot_trend --submit --arm-live`
   → 观察 stdout tier 与 `data/cache/live_track_status_mexc_btc_spot_trend.json`:
   期望 `tier=AUTO`、首笔 ≤$100、成交确认轮询到 FILLED。
9. **对账**:看 status 的 `parity`/`reconciliation`;ops API(8440)
   dashboard 的 mexc 分支(余额/现货权益/委托/成交,只读);
   `data/cache/live_audit_mexc.db` 哈希链。残差异常 → ARM kill-switch,回到阶段1。
10. **首单响应形状验证**:首笔真实成交后,人工核对适配器对
    orderId/executedQty/cummulativeQuoteQty 的解析与交易所网页一致
    (smoke 证据里 `order_response_shape=UNVERIFIED_UNTIL_FIRST_FILL` 由此解除)。
11. **规模化**:每次提额 = 新 mandate(重签)+ 重签 attestation;上限提升本身
    是一次新的多方人工批准(`services/governance/approval.py`)。

## 2. 事故路径

- kill-switch ARM(任何异常):`data/cache/live_kill_switch_mexc.*`(per-venue
  SQLite 权威 + JSON 镜像),latch 后全拒,复位必须人工。
- 崩溃恢复:下一轮驱动器启动自动 `resolve_unconfirmed_orders`(回查交易所权威
  状态补记),不留僵尸单;撤单与成交赛跑时以撤单后 `get_order` 回查为准。
- 现金不足:驱动层 pre-submit 现金门 fail-closed 升人工,不会发出买不起的 BUY。

## 3. 诚实边界

- 现货权益 = USDT free + 持仓×最新价;无法定价的持仓按 0 计(保守低估,偏向硬停)。
- `positions()` 的 `entry_price` 恒 0.0(账户快照无法恢复成本基)——upnl/强平价/
  杠杆字段在现货语义下不存在,展示层诚实隐藏。
- 以上全部是运行时 chokepoint;单进程内挡不住受信代码绕过(Phase 14 进程隔离的范畴)。
