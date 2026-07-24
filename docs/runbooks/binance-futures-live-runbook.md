# Binance USDT-M 期货 mainnet 实盘 runbook(人工首单路径)

状态:human-ready 代码面已就绪(2026-07 Binance mainnet 接线,镜像 MEXC 对接 Part 1)。
与 MEXC 不同,Binance **有期货 testnet**(`--venue binance`,已联调在跑)——mainnet
是同一适配器类换 `MAINNET_BASE` + 人工多步门。毕业路径 = 干跑 → 前向 paper-track →
testnet 在跑 → 只读 smoke → 人工 mandate + attestation → 显式 `--arm-live` →
≤$100 首单 → 对账 → 规模化(每轮新多方批准)。

**AI agent 绝不执行本文件的任何一步**:不放 key、不跑 smoke、不签 mandate、不带
`--arm-live` 运行驱动器。每一步都是人工操作。

---

## 0. 前置事实(先读再动手)

- 适配器 `live/exchange/binance_futures.py`:`MAINNET_BASE = https://fapi.binance.com`
  构造在默认 SafetyConfig 下即 `SafetyViolation`;venue 注册
  `_VENUES["binance_mainnet"]`(`tools/live_track_advance.py`)与 testnet `"binance"`
  同一适配器类、同一期货语义(`_binance_balances`/`_binance_margin`),**无现货分支**
  (无现货现金门、无现货口径限额——期货 venue 走期货形 gate 默认)。
- 驱动器 `tools/live_track_advance.py --venue binance_mainnet --strategy <id>`:
  不带 `--submit --arm-live` 直接 SystemExit(`mainnet_only` L2 结构门)。
- `--arm-live` 是**唯一** arming 路径(人工 CLI 专属):构造 armed SafetyConfig
  (`allow_ai_direct_trading=False`、`require_human_approval=True` 恒保持)。
  ops API(8440)的固定 argv **永不**携带它(governance 测试钉死)。
- 即便 armed,以下任一缺失仍 fail-closed 零提交:启动 key 权限校验(apiRestrictions
  SAFE)、validation attestation、人工 mandate(AUTO 一律拒绝 → NEEDS_HUMAN_REVIEW)、
  kill-switch 未跳闸、pre-trade gate 通过、跨源核价/资金费交叉核对无冻结。
- 凭据与 `tools/verify_key_permissions.py` 的 MAINNET 诊断配置是同一份:
  env `BINANCE_MAINNET_API_KEY` / `BINANCE_MAINNET_API_SECRET`,或
  `key/binance_mainnet_key.json`(gitignored),形状 `{"api_key", "api_secret"}`。
  与 testnet 凭据(`BINANCE_TESTNET_*` / `key/binance_test_key.json`)是**完全不同的
  一套**,绝不混用。
- 未 arm 的进程(ops API dashboard、风险哨兵)对配置了凭据的 binance_mainnet 只能得到
  诚实 gated 报告(`SafetyViolation` 错误行)——这是结构门在工作,不是故障;只读
  mainnet dashboard 镜像(MEXC 那种 ungated 现货分支)是显式后续项。

## 1. 首单 checklist(按序,每步不过就停)

1. **放 key**:在 Binance 控制台创建 API key —— 权限只勾 futures trade + read,
   **提现必须关闭**,并绑定 IP 白名单。写入 `key/binance_mainnet_key.json`
   (gitignored):`{"api_key": "...", "api_secret": "..."}`
   或设 env `BINANCE_MAINNET_API_KEY` / `BINANCE_MAINNET_API_SECRET`。
2. **权限校验**:`python -m tools.verify_key_permissions --venue binance`
   → 必须 SAFE(`enableWithdrawals=false` ∧ `ipRestrict=true`,经
   `/sapi/v1/account/apiRestrictions` 只读查询)。UNSAFE/UNKNOWN 一律停。
   首次真实运行时人工核对响应字段名与模块 docstring 的诚实标注一致。
3. **只读 smoke**:`python -m tools.binance_readonly_smoke`
   → 校验 server_time / exchangeInfo / premiumIndex / account 字段形状,落
   `evidence/binance_mainnet_smoke.json`,verdict 必须 PASS。未跑前本 venue 的
   readiness 诚实视为 `UNVERIFIED_MAINNET_RESPONSE`。
4. **数据就位**:`python -m tools.live_data_refresh`(资金费日快照
   `crypto_funding_rates.json` 是 carry 策略 funding 交叉核对的离线侧证据),
   并重跑对应策略的干跑(`tools/dry_run_carry_multi` / `dry_run_crowd_contra_multi`)
   + 前向 paper-track(paper_track.db)。
5. **手写 mandate**:复制 `key/live_mandate_binance_mainnet.json.template` 为
   `key/live_mandate_binance_mainnet.json` 并亲笔填数字:`venue="mainnet"`、
   `exchange="binance"`、`allowed_instruments` 按策略信号腿填写,首单额度建议
   `max_single_order_notional ≤ 100`、`max_gross_notional ≤ 100`、
   `max_net_notional ≤ 100`、`expires_at` 给短窗口。代码不代填任何数字。
6. **手写 attestation**:在 `key/live_validation_attestations.json` 为目标策略加
   `human_override` 条目(模板生成:
   `python -m tools.create_validation_attestation_template`),真人署名 + 理由。
7. **研究证据桥**:确认 `evidence/alpha_paper_cycle.json` 覆盖目标策略
   (carry_oi_majors / crowd_contra 已注册;新策略未注册时研究桥 fail-closed 报
   `alpha_paper_status_missing` 并跳批——纳入 paper cycle 是一步**独立的研究治理
   决定**,不是本 runbook 能绕过的门)。
8. **首单**(人工,显式 arm):
   `python -m tools.live_track_advance --venue binance_mainnet --strategy carry_oi_majors --submit --arm-live`
   → 观察 stdout tier 与
   `data/cache/live_track_status_binance_mainnet_carry_oi_majors.json`:
   期望 `tier=AUTO`、首笔 ≤$100、成交确认轮询到 FILLED。carry_oi_majors 是资金费信号
   策略,驱动器会用 venue `premium_index` 实时读做资金费交叉核对(陈旧/垃圾 → 冻结
   该币,仓位原样不动)。
9. **对账**:看 status 的 `parity`/`reconciliation`;
   `data/cache/live_audit_binance_mainnet.db` 哈希链;
   `data/cache/live_track_binance_mainnet.db` 账本。残差异常 → ARM kill-switch,
   回到阶段1。kill-switch 复位:
   `python -m tools.live_kill_switch_reset --venue binance_mainnet`
   (多方人工批准,`services/governance/approval.py`)。
10. **首单响应形状验证**:首笔真实成交后,人工核对适配器对 orderId/executedQty/
    avgPrice 的解析与交易所网页一致(smoke 证据里
    `order_response_shape=UNVERIFIED_UNTIL_FIRST_FILL` 由此解除)。
11. **规模化**:每次提额 = 新 mandate(重签)+ 重签 attestation;上限提升本身
    是一次新的多方人工批准。

## 2. 事故路径

- kill-switch ARM(任何异常):`data/cache/live_kill_switch_binance_mainnet.*`
  (per-venue SQLite 权威 + JSON 镜像),latch 后全拒,复位必须人工(多方批准,
  见上 §1.9)。
- 崩溃恢复:下一轮驱动器启动自动 `resolve_unconfirmed_orders`(回查交易所权威
  状态补记),不留僵尸单;撤单与成交赛跑时以撤单后 `get_order` 回查为准。
- 风险越限:哨兵/驱动器评估回撤/敞口/维持保证金比率(`_binance_margin` 的
  totalMarginBalance/totalMaintMargin),越限 → arm kill-switch + 独立紧急减仓通道
  (强制 reduce-only,不经过被挡住的共用 gate)。
- 资金费信号失真:carry 策略的 funding 交叉核对冻结该币(仓位原样不动),查
  status JSON 的 `funding_check` 块。

## 3. 诚实边界

- 期货余额口径:`aum = availableBalance`、`equity = totalMarginBalance`(含未实现
  盈亏);容量基数用 equity(mandate 提额后 available 随保证金占用漂移)。
- 持仓归并:Hedge Mode 下同标的 LONG/SHORT 两腿按净额归并(适配器
  `positions()`,2026-07-04 审查发现 #13 的修复)。
- ops API dashboard / 风险哨兵(未 arm 进程)对本 venue 报诚实 gated 错误
  (构造即 SafetyViolation);只读 mainnet 账户展示镜像是显式后续项,不冒充已就绪。
- 以上全部是运行时 chokepoint;单进程内挡不住受信代码绕过(Phase 14 进程隔离的
  范畴,见 `docs/PHASE_14_PROCESS_ISOLATION.md`)。
