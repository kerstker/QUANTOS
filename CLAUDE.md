# QUANTOS — Agent Operating Guide

QUANTOS is a **safety-critical AI quantitative trading research platform**.
Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14). Program plan:
[`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md).

Readiness: **L2_DRY_RUN_ONLY** — research + dry-run simulation only. No live
trading, no broker connectivity, no real credentials.
**2026-07-04 用户拍板启动通往小资金实盘的 Phase 3A 人工门路线**(非绕过边界):审查+分阶段计划
= [`docs/2026-07-04-live-readiness-review-and-plan.md`](docs/2026-07-04-live-readiness-review-and-plan.md)。
已拍板:Binance 先行(venue 可插拔,Bybit/Hyperliquid 后续)、分级授权(mandate 限额内自动、越限升人工)、
真钱前 ≥8 周前向证据、$3k–5k 起步。Stage 0(前向证据钟)已 2026-07-04 落地,钟已起走;
readiness 仍是 L2,直到 Go/No-Go 人工评审通过。

## Safety boundary (non-negotiable — blueprint §1)

- `LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`,
  `ALLOW_ORDER_SUBMISSION=false`, `ALLOW_AI_DIRECT_TRADING=false`,
  `REQUIRE_HUMAN_APPROVAL=true`. Live trading must remain impossible without
  explicit multi-step human approval.
- Never commit secrets / broker credentials; never import a broker/CTP/FIX SDK;
  never bypass or weaken risk gates; never use future data / introduce
  lookahead; AI agents never place live orders or touch credentials.
- Keep research / paper / live strictly isolated; strategy (intent) separate
  from risk (permission); broker logic behind adapters.
- Tests-first; smallest safe change; never hide or delete failing tests.

These invariants are machine-enforced by the governance scanner (below) and the
tests in `tests/governance/`.

**Threat model (two layers).** The agent guardrails (`services/intelligence`),
the alpha lifecycle state machine, and the pre-trade risk gate are RUNTIME
controls over the *actor* and *evidence* passed in: a non-human actor cannot reach
APPROVED, reset the kill-switch, trade live, or touch credentials. They do NOT —
and in a single process cannot — defend against TRUSTED CODE that constructs a
`HUMAN` `Actor`, calls `AlphaLifecycleManager`/`KillSwitch`/`catalog._set_state`
directly, or otherwise bypasses the chokepoint. That build-time surface is
governed by code review, the governance scanner, and (Phase 14) deployment/process
isolation. The autonomous agent path holds only a non-human actor and reaches the
state machine/gate only through the hardened `ResearchAgentLoop`; direct manager/
catalog access is for human-controlled workflows and is not an agent API.
**2026-07-12 强身份注:`promotion_api` 已删除本地共享治理 token 与自报操作者标签。
承重入口只接受 allowlist 非对称算法签名的 OIDC access token，强制 issuer/audience/时效/MFA、
RFC 8705 mTLS 证书绑定和动作 RBAC；actor/audit ID 只来自不可变 `issuer|subject`。浏览器已实现
Authorization Code + PKCE S256，一次性事务仅存 sessionStorage，access token 仅驻内存。缺外部
IdP 公钥策略或可信 TLS scope 即 fail-closed。当前证据只到 M2；外部 IdP 运营、可信 TLS、密钥托管、
撤销/访问复核及 AI workload 无法人类化的运营证据仍是 M4 红线。APPROVED ≠ 真钱:LIVE 仍被
readiness pin + 五旗帜结构性挡死。**

## What actually exists (verified 2026-06-29 by full-system audit)

Authoritative reality map + completion roadmap:
[`docs/engineering/specs/2026-06-29-system-audit-and-completion-roadmap.md`](docs/engineering/specs/2026-06-29-system-audit-and-completion-roadmap.md).
**The backend is substantially built and tested (~1047 tests pass). The console-wiring
gap is now largely closed: the four ALPHA-pipeline breaks are all wired + tested — 断点1
关卡证据回喂 evaluator (real `detect_leakage`/regime/PBO evidence through every search loop),
断点2 前向 paper 追踪持久层, 断点3 console 真组合 (optimizer + real returns), 断点4 衰退/退役调度器.
Most console pages now serve real data; remaining synthetic surfaces are genuinely-missing
subsystems (kept honest), not unwired engines.**

Real, tested backend engines (`*_TESTED` in the audit):
- `services/research/factor_engine/` — real IC/ICIR/Sharpe math + hardened AST
  factor-expression compiler. `services/research/backtest_engine/` — single +
  event-driven backtests with exact PnL-reconciliation identity.
- `services/research/validation/` — Deflated-Sharpe, combinatorial purged CV,
  purged/embargoed walk-forward gate. `services/research/oos_validation.py` —
  no-peek selection OOS.
- `services/research/{alpha_factory,alpha_expression,cs_genome,gp_tree,factor_set,
  session}.py` — real evolutionary/GP search, cross-sectional DSL, synergistic
  de-redundant set selection, and the deterministic research-session core.
  The supported orchestrator entry point is
  `services/intelligence/research_session.py::run_research_session`, which fixes
  the guardrailed search runtime and keeps `services/research` from importing
  `services/intelligence`.
- `services/research/ic_panel.py` — **截面 IC 诊断面板**核心(泛化自 `tools/crypto_oi_research.py::
  _rank_ic`):`compute_period_ic_series`(逐期截面 Spearman rank IC 序列)+ `aggregate_ic_series`
  (mean/ICIR/hit-rate/t-stat/逐年,n<10 全 None)+ `signal_from_window_op`/`signal_composite`(窗口算子
  + 截面 z-score 组合,复刻 daily composite 逻辑)。纯函数、无 IO/RNG/墙钟。命名宇宙/因子注册表
  `tools/ic_panel_registry.py`(4 宇宙 × 5 因子:carry_composite/crowd_contra/trend_composite + 2 基线,
  新候选加一行即可)+ 驱动器 `tools/ic_panel_research.py` 落 `ic_panel_snapshot.json`。**Surfaced
  read-only** 于 `/api/v1/console/ic-panel` + 页 `ICPanel.tsx`(nav「IC 面板」)。诚实边界:**初筛诊断,
  非晋级门禁**——不碰 evaluator/AlphaCatalog,不驱动任何自动晋级。首跑发现:**`crowd_contra`(反着做
  拥挤的散户多空仓位)mean_ic=0.035、t-stat=4.32(统计显著)**——此前过 4/5 项证伪检验但从未送入
  paper-track 的候选第二边,现在有了横向对比证据;`carry_composite` 对价格 IC≈0(诚实——carry 的边在
  资金费现金流、非价格方向预测);`momentum_7_wide`(336 币宽宇宙)IC 明显为负(-0.063,与既有"宽宇宙
  动量类因子多数年份负夏普"结论一致)。是否把 crowd_contra 送入 paper-track/干跑,是另一个独立决策。
  **2026-07-06 战线④(持有期口径)**:+`compute_horizon_ic_series(signal, price, horizon)`
  ——非重叠 h 期持有截面 IC(刻意无 stride/重叠参数,重叠收益会让 naive t 虚高;h=1 与旧函数
  **逐位相等**,parity 工具 `tools/ic_panel_h1_parity_check.py` 验证过)。注册表可选
  `holding_periods`(慢速持仓类 [1,7,14]),快照 `schema_version: 2` 只加字段,契约测试绑定
  v2。**首跑发现:crowd_contra h1 t=0.66→h7 2.91→h14 3.35(持有期口径效应在面板内复现);
  carry_composite h1 t=0.19→h7 2.75(新信息:周度口径下显著);momentum/reversal 宽宇宙日频
  ±16 → h7 缩到 ±5(印证微观结构反弹)。可部署 book 不变(crowd_contra 单条)。**
- `services/research/paper_trading/domain/pnl.py` — **real reconciled PnL
  attribution** (realized+unrealized−costs, `reconciliation.py`/`consistency.py`).
  No longer the `notional*0.001` placeholder this file used to claim.
- `services/research/{run_registry,research_runner,experiment_registry}/` —
  durable sqlite run store + the guarded mutating runner + immutable experiment
  artifacts/DSR trial-count persistence.
- `services/research/paper_trading/forward.py` + `tools/{live_data_refresh,forward_track_advance,
  alert_sink}.py` — **真前向 paper-track(实盘路线 Stage 0,2026-07-04)**:与 advance.py 历史切分的
  定义性区别 = **决策先落库、结果后兑现**(`forward_decisions` append-only 台账,decided_ts=数据时间 +
  recorded_at=墙钟证明先后;无前视 min_gap 6.5 天、覆盖率 <0.8 fail-closed 留 pending、决策不可改写)。
  周度驱动器与干跑同一条信号路径(`build_panel`+`CROWD_CONTRA`/`CARRY`+`_legs`),增量刷新三源
  (klines/funding/OI,幂等 fail-soft,staleness 三维口径——退市币停更≠告警),告警走本地 jsonl 台账
  + 可选 Telegram(密钥只 env)。**首条决策 2026-07-02(数据时间)已落库(crowd_contra +
  carry_oi_majors 各 14 腿),8 周证据钟已起走;gross+funding 口径,非晋级门禁**——真钱 Go/No-Go
  是 Phase 3A 人工评审。周一 09:00 schtasks 调度(人工注册,错过自动 catch-up)。15 tests +
  治理边界扩充(3 个新工具钉进只读数据边界)。
- `live/`(实盘路线 Stage 1,2026-07-04,repo-root 独立树,`services/` 零改动)— **测试网执行栈**:
  `contracts.py`(两级下单类型,刻意不合并——`OrderIntent.quantity:int` 是风控门货币,复用既有
  `PreTradeRiskGate` 9 引擎零重造;`LiveOrderRequest.quantity:float` 是 venue 提交货币,小资金真实
  分数仓位如 0.015 BTC 不能被迫凑整数手数)、`exchange/binance_futures.py`(纯 stdlib
  urllib+hmac 签名 REST,可注入 transport;mainnet 构造在 L2 默认下结构性 `raise SafetyViolation`,
  与 `LiveBrokerAdapter` 对称;testnet 不受 `SafetyConfig` 管辖——那治理真钱/真券商,测试网是交易所
  官方沙盒零真实资本,见 spec §1.1)、`execution/{filters,position_ledger,reconciliation}.py`
  (交易所步长/最小名义额过滤 fail-soft + 冪等 client_order_id;append-only 本地仓位账本;对账纯函数
  只报告 drift 不自动修正)、`governance/mandate.py`(`PreTradeRiskGate` 批准之后的第二层分级
  授权——限额内 `AUTO` 机械执行、超限/mandate 缺失或过期/venue 不匹配/任何评估异常 →
  `NEEDS_HUMAN_REVIEW`,fail-closed 到底绝不默认 AUTO)、`pipeline.py`(`submit_live_batch`:决策→
  缩放整数喂 gate(`UNIT_SCALE`=1e6,1 unit=1e-6 币,仅 gate 出入段使用)→换算回真实浮点→交易所
  过滤器→mandate 分级→AUTO 提交/REVIEW 留存不提交→ledger→`GlobalAuditLog`,全程复用既有
  `screen_intents`/`build_intents`/风控门/审计链,零重造风险逻辑)。**87 tests(79 单元 +
  8 治理边界)全绿,含端到端缩放往返integration 测试**。**关键架构事实,勿"优化"掉**:`live/`
  故意不在通用治理扫描器(`run_paper_to_live_governance_check.py`)的 `SCAN_SUBDIRS`(`quantos`+
  `services`)里——那个扫描器的 `FORBIDDEN_APIS` categorically 禁止 `place_order`(研究/纸面树里
  这是对的红线),但 `place_order` 正是 `live/` 执行缝的正当职责;`live/` 的等价保护是专属
  `tests/governance/test_live_execution_boundary.py`(镜像 broker_adapter/signing 边界测试风格:
  只 exchange/binance_futures.py 含网络 import、无交易所 SDK、无提幣/划转字面量、mainnet 不可实例化、
  无硬编码密钥、`services/` 单向不得 import `live/`)。**真实测试网联调已完成**(同日):
  exchange_filters/account/positions/get_order(不存在)四个只读调用 100% 对上真实响应;一次真实
  place_order(BUY 0.002 BTC)成功但**恰恰证伪了"同步 ACK = 最终成交"的隐藏假设**(回 status=NEW/
  filled_qty=0.0)——随即用 ultracode workflow 做了一次 5 维度对抗式审查(24 发现/15 确认),
  修复:**成交必须轮询 `get_order` 确认终态才记账**(不再信同步 ACK)、venue 调用异常后先
  `get_order` 回查再判定真失败(避免误判致重复下单)、记账失败不再让真实成功的订单被误记为失败
  (新增 `LiveBatchResult.ledger_write_errors` 单独追踪)、`exchange_filters()` 失败 fail-closed、
  Hedge Mode 双向持仓不再被覆盖丢失一侧净敞口、HTTP 响应非 JSON 兜底。**记录在案暂不修复**:
  1000X 前缀合约命名(限流退避/时钟偏移校正/崩溃恢复已在下面的 2026-07-04 续作中修复)。
  全部记录在设计文档 §0。
  **调度驱动器已落地**(同日):`live/governance/mandate_store.py`(mandate 文件加载器,
  fail-closed,数字由用户自己签署,不代填)+ `live/execution/reconciliation.py::preflight_check`
  (drift 超容忍度硬停,回答审查发现 #7)+ `tools/live_track_advance.py`(周度驱动器,与 Stage 0
  同一条 crowd_contra 信号路径;默认 preview 模式——唯读方法透传真实 venue、`place_order` 只记录
  不真提交;`--submit` 也不够,没有用户签署的 `key/live_mandate.json` 时 `evaluate_mandate` 仍
  fail-closed 到 `NEEDS_HUMAN_REVIEW`,两层独立保护)。gate 裸数量阈值(`fat_finger_qty` 等)不
  按 `UNIT_SCALE` 折算,而是设成有效无限——多资产 book 里这类阈值不感知价格本来就不是对的工具,
  真正保护交给名义额类检查。**真实联调**:preview 模式对着真实测试网跑通,对账前置在真实数据上
  正确拦下一次真实 drift(交易所侧遗留的 0.002 BTC 测试仓位 vs 全新本地账本),核对补记后全链路
  正常跑完(真实 AUM $4993.81、真实 gate 评估、mandate 缺失 → 安全零提交)。
  **仓位/回撤监控已落地**(同日,`live/execution/risk_monitor.py`,分两级——同 mandate 的分级
  授权哲学):①`evaluate_risk_state`+`respond_to_breach`(纯函数算真实回撤/总敞口/净敞口,越限
  即 `arm` 既有 `KillSwitch`——任何 actor 皆可触发,零新风险,已接进驱动器的对账后第二道前置
  检查,峰值权益持久化 `data/cache/live_risk_state.json`)全自动、真实联调跑通;②
  `compute_flatten_orders`(算 REDUCE-ONLY 减仓量的纯函数,15 tests)**故意没接自动提交**——
  真实架构冲突:`check_kill_switch` 一旦触发会挡下包括 reduce-only 在内的所有意图,①触发 kill
  switch 后②想走同一个 gate 送减仓单会被同一个 kill switch 挡下,越紧急越送不出去;解法(改
  共用 gate 认识 reduce-only 语义 / 给紧急减仓开独立安全路径)都是需要人工拍板的架构决定,不是
  该由代码悄悄选一个的,留白记录在设计文档 §9。**125 tests(117 单元 + 8 治理边界)全绿**。
  设计:`docs/engineering/specs/2026-07-04-live-execution-stack-design.md` §7/§9。撤单/改单、
  Bybit/Hyperliquid 具体实现(协议已留口子)、console 页面、减仓自动提交(见上)= 非目标(留后续)。
  **venue 可插拔第二个实现 + 运维硬化 + 崩溃恢复已落地**(2026-07-05,同一条 Stage 1 树):
  `live/exchange/bybit_futures.py`(Bybit V5 统一账户,同一 `VenueAdapter` Protocol;与 Binance
  真实协议差异——签名走 header 不走 query、统一 `{retCode,result}` 信封、Hedge Mode 用
  `positionIdx`;从第一天就走轮询确认终态,没有重蹈 Binance 最初"同步 ACK=最终成交"的覆辙)+
  `live/exchange/retry.py`(共享限流感知退避 `call_with_backoff`,两个 adapter 各自定义
  `is_rate_limited`,签名每次重试都重新计算避免退避跨越 recvWindow)+ 两个 adapter 都加
  `sync_clock()`(拉交易所服务器时间校正本机偏移,fail-soft)+ `live/pipeline.py::
  resolve_unconfirmed_orders`(驱动器启动时崩溃恢复——核对本地账本非终态订单,`get_order`
  回查权威状态补记 fill,回答"驱动器在确认成交前被杀掉"的缺口,已接进 `tools/
  live_track_advance.py` 每轮读仓位之前)+ 顺带修的纵深防御:preview 模式现在用一次性草稿
  账本写(`current_positions` 仍读真实账本),不管 mandate 判成什么 tier 都不会把占位记录
  污染进真实账本 + `tools/create_live_mandate_template.py`(生成 `key/live_mandate.json.
  template`,所有风险数字故意填不可解析占位符防误当真实 mandate 加载,`venue` 字段例外预填
  `"testnet"`——结构性协议匹配,非风险数字;真实限额依然 100% 用户自己签署)。真实测试网
  preview 联调再次跑通。**live/+治理边界测试从 125 增至 178,全绿;设计文档 §10–§13。**
  Hyperliquid(msgpack+Keccak+secp256k1 签名,依赖模型与 HMAC 系彻底不同)是需要人工拍板的
  依赖决定,写了决策文档不建假实现(§11)。
  **机构级补全 9 包已全部落地**(2026-07-05 第二批,9 commits,live/+治理测试 178→267,全量
  门禁 1437 全绿;设计+实施结果 = `docs/engineering/specs/2026-07-05-institutional-live-stack-
  design.md`;运维手册 = `docs/runbooks/live-trading-runbook.md`):① OMS 撤单(两所
  `cancel_order`)+ 超时策略(轮询打满→撤单→**回查权威状态**记账——撤单/成交赛跑与部分成交
  都按交易所真实量入账;崩溃恢复对遗留开单也主动撤,防止与本轮订单叠加)+ **Bybit 状态归一化**
  (原样 "Filled" vs 流水线 "FILLED" 的既有缺口,不修则 Bybit 驱动下订单永远确认不了);
  ② **紧急减仓独立通道**(用户拍板,补掉 §9 留白):`live/execution/emergency.py` 不咨询共用
  gate/kill-switch(治理测试钉死不 import),结构窄校验网格测试证明"只可能减少敞口",
  `LiveOrderRequest.reduce_only` 两所原生透传,`flatten_target_for_breach` 策略 = 回撤越限全平/
  敞口越限等比减回限额 90%/纯数据质量问题不自动交易(交给人),交易所过滤器在此**刻意
  fail-open**(对减仓单而言"不提交"才是危险动作——与加仓路径相反的不对称),已接驱动器
  breach 分支(preview 同样只记录);③ TCA(账本 `live_tca` 表惰性建表平滑迁移 + 带方向滑点
  bps + `tca_summary` 对照回测 10bps 假设,流水线/紧急通道都 fail-soft 记录,绝不影响仓位
  记账);④ live-paper 对账(`live/execution/parity.py`:Stage 0 **记录在案**权重——非重算
  信号,重算会掩盖信号漂移——vs live 账本仓位 + 确定性 client_order_id 订单归因,逐周
  `data/cache/live_parity.jsonl`;真实联调正确抓到交易所遗留 0.002 BTC 测试仓);⑤ `--venue
  bybit` 驱动器选项 + **mandate 必填 `exchange` 域**(两所 testnet 都叫 "testnet",不签交易所
  域会互相误认;unset/mismatch 皆 fail-closed 升人工)+ per-venue 账本/状态/风控峰值/对账路径
  与余额解析(Bybit V5 信封与 Binance 形状不同);⑥ TWAP 切单(`live/execution/slicer.py`
  确定性子单 ID/步长感知/总量绝不超母单,`submit_live_batch(slicer=)` 默认 None 行为不变,
  子单序列内失败即中止剩余——诚实定位:规模化预留,现阶段不启用);⑦ console:驱动器把
  TCA/订单统计/venue 写进状态快照(**console 只读快照,`services/` 不 import `live/` 的单向
  边界不破**),`/api/v1/console/live-trading` + LiveTrading 页加 TCA/对账/订单统计/紧急减仓
  卡片,TS 类型 + 契约测试同 commit;⑧ 运维 runbook(周度周期/故障处置/key 与 mandate 生命
  周期/preview→--submit 检查清单);⑨ 策略研究一轮**诚实证伪**:5 新候选过 IC 面板(注册表
  各加一行),日频 `reversal_7_wide` t=16.4/`low_vol_wide` t=15.7 统计巨大,但新建
  `tools/wide_factor_gauntlet.py`(周度 L/S 分位 + 换手计费 0/10/25/50bps)一轮打掉——
  reversal 周度毛夏普 0.21、10bps 即负(**日频大 IC = 微观结构买卖价差反弹,持有不过夜就
  没了,与 FX +0.70 mirage 同类**),low_vol 毛 0.49 净@25bps 0.26 无可交易边,funding
  变体/OI 变化率无信号;**可部署 book 不变(crowd_contra 单条)**;诚实性核查:crowd_contra
  日频面板 t=0.66 vs 周度重算 t=2.99(n=232)= 慢速持仓因子的持有期口径效应、非数据损坏
  (相对首跑 4.32 随样本延长有软化,仍显著);runner 白名单 +`ic_panel`/+`wide_gauntlet`
  (前端可触发,`ALLOWED_KINDS` 同步)。遗留:Bybit 真实联调(等 key 填入
  `key/bybit_test_key.json`,真实下单前单独确认)、mandate 真实数字(用户签)、Hyperliquid
  (维持推迟)、IC 面板 per-factor 持有期口径(候选改进)。
  **双所真实上线 + 机构级差距审计与修复**(2026-07-05 续二,commits a359aeb…2af05f7):
  用户提额 mandate 至 10000 USDT 后真实调仓,**暴露 CRITICAL bug**——旧 `client_order_id`
  键(策略+decided_ts+标的)在同一决策的调仓增量批次间撞 ID:Binance 允许已成交 ID 复用→
  新单真实执行但账本 `INSERT OR IGNORE` 静默吞掉记账(仓位漂移 2.6x 不报错);Bybit 按
  `orderLinkId` 去重→根本没执行还因轮询查到旧单 FILLED 误报成功。修复:订单 ID =
  决策前缀(16)+hash(方向|数量)(16),同订单重试仍幂等、不同增量视为不同订单。**Bybit
  Demo 双所并行落地**(testnet 水龙头失灵,改用官方 Demo Trading——`DEMO_BASE`,demo 专属
  资金申领端点)。**交易台**(`tools/live_ops_api.py:8440` + LiveDesk.tsx)——类交易所实时
  界面,双所权益/仓位/uPnL/流水/净值曲线,操作只经驱动器 CLI 子进程(与人工 CLI 同一条治理
  链)。**48-agent 10 维度机构级差距审计**(全审计+对抗式核实,加权 51%——元发现:原始
  15 个 P0 核实后仅 5 个真站得住,其余被"testnet/demo 零真实资本+Phase 3A 评审兜底"降级
  P1,但评审放行当天会集体转正,必须放行前修完)后修复:①价格新鲜度门此前 `price_as_of=now`
  结构性恒真,现传真实 `decided_ts`;②跨源核价(CoinGecko 独立源,接线时抓到"决策价 vs
  当前价"苹果比橘子的假阳性陷阱,加了新鲜度守卫);③Gate 账户上下文此前全部硬编码虚构
  (positions=()/day_pnl=0/peak_equity=aum,双层防御实质单层),现接真实仓位/日内盈亏/峰值;
  ④firm 级敞口聚合 + 拥挤度门(两个已建未接线的原语接上,真实数据分别抓到 8 个跨所重叠
  标的和 0.509 拥挤分);⑤GlobalAuditLog→DurableAuditLog;⑥live-paper 偏差自动升级(真实
  抓到 7.1% AUM 的 parity gap);⑦死人开关+灾备;⑧ops API 认证;⑨**kill-switch 跨进程
  闩锁**(比审计原文更根本的发现:此前每次运行全新构造 `KillSwitch()`,latch 跨进程从未
  真正生效,现持久化 + 驱动器/哨兵共享状态)+ 强制三方复位工具;⑩NEEDS_HUMAN_REVIEW
  持久化队列(此前只计数、进程退出即丢,现持久化+ops API+LiveDesk 面板,诚实边界:只记录
  决定不自动重提交)。**417→427 live+治理测试,全量门禁全绿**。仍未做:研究晋级前端闭环、
  多方批准持久化存储、LIMIT/条件单、strategy_id 仓位隔离。设计文档 §14。
  **(2026-07-06 战线①②③④全部落地:晋级闭环+多方批准持久化 ✓(见 services/governance 条)、
  LIMIT 限价单 ✓(见下)、strategy_id 仓位隔离 ✓(见下)、IC 持有期+ALFRED ✓。条件单
  仍未做。)**
  **LIMIT 限价单已落地**(2026-07-06 战线②,默认不启用):`LiveOrderRequest` 加
  `order_type`("MARKET"/"LIMIT")/`limit_price`/`post_only`,全部默认值——不传时两所适配器
  HTTP 请求形状、`client_order_id` 哈希、驱动器行为与改动前逐字节相同(回归锁测试钉死)。
  最小可用版 = **可成交限价(marketable limit)= 市价单加价格上限**,非做市。定价核
  `live/execution/pricing.py::resolve_anchor` 不只防 `book_ticker` 网络失败,还防"成功但
  退化"的响应(testnet/demo 流动性稀薄,交叉盘/零价/与决策价偏离离谱的天价 mid 是常态不是
  异常)——结构校验 + 偏离容忍度门(5%,工程占位),任一不过回退决策价锚。mandate 名义额对
  LIMIT 取保守口径(BUY: max(决策价,限价)/SELL: min,零成本纵深防御,未知 order_type 恒升
  人工)。account/strategy 仓位口径分离(战线③)与 LIMIT 定价装配顺序共存:过滤器取整后、
  mandate 评估前插入定价段。MARKET 继续尊重调用方 `max_confirm_polls`(不静默改行为);
  LIMIT 用独立更长轮询窗口(挂单不成交是正常路径,默认 30 次远大于 MARKET 的 5)。
  preview 模式加 `PREVIEW_NOT_SUBMITTED` 终态短路(顺带修了一个预先存在的 MARKET-preview
  轮询空转小 bug)。账本 order_type/limit_price 双轨迁移(新库 `_SCHEMA` 直接带列 + 老库
  惰性 ALTER,nullable 无 DEFAULT——历史订单诚实读回 NULL,不猜测回填)。紧急减仓通道恒
  MARKET,治理测试源码级钉死绝不构造 LIMIT。CLI `--order-type {market,limit}`(默认
  market,mandate schema 不动——理由:LIMIT 只收紧价格风险不扩大授权,已签署 mandate 全部
  继续有效)。真实 Binance 测试网 preview 联调跑通(13 腿全部拿到真实盘口锚定价,零
  anchor-fallback 告警)。**真实 `--submit` LIMIT 下单待用户单独放行**。band_pct/挂单时长/
  偏离容忍度均为工程占位数字,真实使用前需用户确认或改数。设计:
  `docs/engineering/specs/2026-07-06-limit-order-execution-design.md`。
  **strategy_id 仓位隔离已落地**(2026-07-06 战线③):核心裁定 = strategy_id 只是**本地账本
  分区维度**;venue/保证金/风控/对账/紧急减仓全部维持账户级(交易所不知道策略;per-strategy
  vs venue 对账 = 拿本地分解自己对自己、伪造独立性,reconciliation.py 明令禁止)。结构性
  不变量:各策略分区求和恒等于账户级 `all_positions()` 全集(测试钉死)。账本三表加 NOT NULL
  `strategy_id`(新库无默认——裸 INSERT 报错不静默错标;老库惰性迁移 ALTER ADD COLUMN
  DEFAULT 'crowd_contra',存量回填值经**只读核查为历史事实**:32-hex 决策订单前缀逐一反算
  `decision_prefix("crowd_contra",…)` 命中;并发迁移吞 duplicate column——真实环境常驻 ops API
  抢先迁移恰好证明该路径 load-bearing)。`submit_live_batch(account_positions=)` 口径分离:
  目标增量对**本策略**分账仓位算(否则第二策略的 delta 会平掉第一策略的仓),mandate
  per-instrument cap 投影收**账户级**全量(否则两策略各贴 cap、账户 2×cap 双双 AUTO);崩溃
  恢复 fill 从订单行继承归属;紧急减仓记保留账本 `EMERGENCY_STRATEGY_ID`(不在紧急时刻做归属
  决策,差额落保留账本、恒等式不破)。驱动器 `--strategy` + `_STRATEGIES` 注册表(未注册
  SystemExit 先于任何网络)、`_venue_paths(strategy_id=)` 路径单一真源(默认策略沿历史路径,
  只 status 分策略)、第二策略共享 mandate 首跑落单前即告警。真实账本已迁移并逐行比对备份
  (binance 25 fills / bybit_demo 20 fills 全 crowd_contra;备份
  `data/backup/pre-strategy-isolation-20260706/`)。+40 tests,全量门禁 1559 绿。设计:
  `docs/engineering/specs/2026-07-06-strategy-position-isolation-design.md`。
- `services/research/paper_trading/{store,advance,health}.py` — 断点2 forward paper-track
  persistence (append-only `data/cache/paper_track.db` of held-out OOS paper P&L/IC, honest
  train/paper split). `services/research/leakage_evidence.py` + `services/intelligence/
  evidence.py` — 断点1 leakage evidence SOURCE (wrap a SIGNAL/VOLUME op or GP tree as a real
  `detect_leakage` ComputeFn; providers expose `leakage_ok(defn)`; the search loops feed it as
  `evidence=` to the evaluator gate). `services/alpha/maintenance.py` — 断点4 non-human
  degradation/retirement sweep (the missing caller for `degradation_monitor`). All tested.
- `services/research/ml/{feature_panel,stacker,walk_forward_stack}.py` — the first ML layer: a DETERMINISTIC
  learned signal-combination (stacking) over the factor zoo (numpy + scikit-learn, PINNED + used deterministically:
  threads=1, `random_state=0`, Ridge cholesky; same-input→bit-identical test). GBM + Ridge, trained purged+embargo,
  judged no-peek OOS. Driver `tools/ml_stack_research.py`. Honest finding: NO tradable edge (crypto combination
  hurts/GBM overfits; FX +0.21 is gross + contaminated-universe) — bottleneck is data/signal-class, not modeling.
  numpy/sklearn are non-network so `services/` no-network-import is still machine-enforced.
- `services/execution/{impact,impact_calibration}.py` + `services/research/realism/capacity_curve.py` — **现实主义引擎 RE-1(市场冲击 + 容量)**:把回测/干跑的
  成本现实从「未校准、默认关闭的 sqrt stub」升到 **Almgren-Chriss 冲击**(临时平方根 + 永久线性,参与率驱动,纯函数)
  + **容量衰减带**(扫 AUM → 净冲击后夏普 → `critical_aum`;冲击系数无法校准 → 扫 5–20bps 给**区间带**、标
  `UNCALIBRATED_PRINCIPLED`,不报假单点)。**诚实容量 = min(冲击-夏普容量, 参与率上限10%)**——两约束里更紧那个。
  驱动器 `tools/capacity_research.py` 应用到 carry+crowd_contra,落 `capacity_snapshot.json`。**关键发现:参与率是绑定
  约束**;校准敏感性(参与率 5–20% × 执行铺开 1–5 天)给诚实区间——**carry $1–20M / crowd_contra $3.6–72M**(最保守角
  10%×单日 = carry $2M / crowd $7M,现实执行铺开 5 天约 $10–20M / $35–70M)。冲击-only 会误报 $B(~300× 高估),正是「松现实主义抬高可部署性」的自欺,
  引擎首跑即抓到(可部署性受限于*构造*——最小 ADV 名满权重——而非仅信号)。诊断非门禁:不改 CostModel 默认、不驱动晋级/下单。
  trend(PIT as_of 无 volume 矩阵)本轮未算,留 RE-1 后续. `services/research/realism/` 纯离线、无网络(治理钉死)。
- `services/research/realism/{scenario,tail_risk}.py` + `tools/scenario_tail_research.py` — **现实主义 RE-2(历史情景重演 +
  尾部风险)**:`scenario.py`(命名危机窗口 `window_stats` + `max_drawdown_window`,危机=数据覆盖到的 2021-05/2022-05 LUNA/
  2022-11 FTX/2023-03 银行)+ `tail_risk.py`(历史 VaR/CVaR + Hill 尾指数 + 与 BTC 下尾依赖=崩盘 beta)。应用到 carry+crowd
  (`REAL_HISTORICAL_REPLAY`)。**诚实发现:① 美元中性预判证实——carry/crowd 崩盘 beta 极低(下尾依赖 0.13/0.0),
  crowd_contra 在 LUNA(+10.6%)/FTX(+6.5%)崩盘里反而赚——这些边的尾部风险是*截面价差击穿*,不是市场 beta;② 真尾部
  内生——carry 最差回撤 19%(gross 无杠杆)发生在 2020-11 牛市而非任何崩盘;注:干跑报 77% 是 vol-target 加杠杆后的口径,
  两个都对、不同度量;③ 尾偏肥(Hill 尾指数 ~2.4–2.7,类 t 分布);④ **2026-07-13 根因更正**:`crypto_daily.db`
  的 CoinGecko volume 本来就是统一的 USD 24h rolling notional，错误在 trend 容量消费端又乘一次 close，制造了
  BTC ~$3.7Q vs EOS ~$20k 的假“单位混乱”。现以 `VolumeUnit/VolumeSemantics` 机器约束并修复双乘；真实中位 ADV
  BTC ~$43.5B/EOS ~$119.6K，trend 短样本容量约 $47.9K(参与率绑定、未校准诊断)。** crowd 2021-05 窗口无数据
  (早期 OI global_ls 稀疏,诚实标注)。
- `services/research/macro_regime.py` + `tools/{fred_fetch,macro_regime_research}.py` — **FRED 宏观数据方向(第一增量)**:
  股票免费源本环境被墙(Stooq bot-wall / Yahoo 429,不绕过),**FRED `fredgraph.csv` 可达**。`fred_fetch.py`(网络在 tools/
  出 gate)抓 6 条**市场型日频序列**(VIX/DGS10/T10Y2Y/HY-OAS/DXY/DFII10,39271 行)填进**已建但从未填的** `fundamental_store`
  (`macro_fred.db`,`knowledge_time=event_time` EOD 可知无前视)。`macro_regime.py`(纯核:三分位/符号 regime/收益分桶)。
  驱动器把 carry/crowd_contra 逐周 gross 收益按宏观 regime 分桶(PIT `latest_as_of`)。**关键发现:两条边宏观 regime 画像
  相反、互补——carry 爱平静/risk-on(低VIX SR+2.36 vs 高VIX+0.65;正常曲线+2.08 vs 倒挂+0.67),crowd_contra 爱压力/risk-off
  (高VIX+1.96 vs 低+1.25;倒挂+1.76 vs 正常+0.88)——crowd 正好在 carry 走弱处走强 = 强分散故事**,系统化了 RE-2 的 LUNA/FTX
  危机发现。诚实边界:regime 标签=全样本分位(回顾性描述,非 PIT 可交易择时);gross 无成本(RELATIVE 模式才是结论);经济发布类
  (CPI/GDP,需 ALFRED vintage)+ 股票(BYO-CSV/ALFRED)= 显式待办;HY-OAS 仅 2023-07+(FRED 限)信用维覆盖 155/期。诊断非门禁。
  **2026-07-06 战线④(ALFRED vintage)**:经济发布类机器已落地——`tools/alfred_fetch.py`
  (CPIAUCSL/UNRATE/GDPC1 全 vintage,knowledge_time=**realtime_start**(首次真实可知日,
  绝不用期首冒充)、独立 `macro_alfred.db` 不混 macro_fred.db、无 key fail-closed 零网络请求、
  错误路径对 URL 脱敏(key 在 query 里)、分页不齐 raise;13 tests 全 fake transport)+
  `macro_regime.py` PIT 批量游标 + 驱动器新维 `inflation_CPI_yoy`/`labor_UNRATE`(缺库诚实
  整体缺席,绝不用最新修正值冒充 vintage)。**等待用户放免费 FRED API key(env `FRED_API_KEY`
  或 `key/fred_api_key.txt`)后 `python -m tools.alfred_fetch` 首跑**;fetcher 已钉进
  test_crypto_data_boundary(顺带补钉 fred_fetch 一直不在名单的存量缺口)。
- `services/research/book_combine.py` + `tools/combo_book_research.py` — **组合分散验证(兑现 regime 互补故事)**:把
  carry+crowd_contra 逐周 gross 收益按 ISO 年-周对齐(`align_on_common_weeks`)+ 等权/逆波动组合。**分散强兑现:
  corr(carry,crowd)=-0.04(近乎不相关)→ 组合夏普 1.95 > 单条 1.44/1.30,最大回撤 8% = 单条(16%/15%)一半,总收益~187%
  保住;VIX regime 依赖被抹平(high+2.44/low+1.65/mid+1.82)。** = 两条正交 crypto 边(资金费 carry + OI 持仓反向)合成
  Sharpe~2/回撤 8% 的强分散。诚实:收益流层面(不同宇宙 58币/14币、不同规模、231 共同周),gross 无成本 → RELATIVE 比较;
  同一执行 book(统一宇宙 + 干跑流水线)= 后续。诊断非门禁。**⚠ 更正(见下 risk-parity 条):此 1.95/8% 是 gross
  理想化,净-成本执行不成立。**
- `services/research/book_combine.py::rolling_inverse_vol_weights` + `tools/dry_run_combo_riskparity_multi.py` —
  **风险平衡统一执行 book(路线图 Rank #1,已做)**:每周按 rolling 逆波动(PIT trailing 窗口)分配 carry/crowd 两个
  sleeve,过干跑流水线,第 5 条策略 `carry_crowd_risk_parity`。**关键诚实结论:carry+crowd 分散不敌执行现实**——三种
  方式(naive-merge VT 1.20 / risk-parity-merge VT 1.14 / portfolio-of-books 配置 VT 1.21)**均 < crowd_contra 单条 VT 1.38**。
  根因:1.95/8% 是 gross(carry gross~1.44);净-成本+整手+vol-target 下 carry 仅~0.58、拉不动 crowd;净曲线相关 +0.04
  (非 gross -0.04)。执行流水线抓到理想-vs-真的差。**deployable = crowd_contra 单条。** dry_run.db 现 5 条策略并列存证。
- `tools/dry_run_combo_multi.py` — **统一执行 book(把组合从收益流相加升级成真的一个 book 过干跑流水线)**:carry 面板
  加 OI `global_ls` 矩阵,每周权重 = 0.5·carry_legs + 0.5·crowd_legs,过门 + 净-成本 + 资金费 + vol-target,第 4 条策略
  `carry_crowd_combo` 落 dry_run.db。**诚实结论(4 条对比,RAW 无杠杆 / VT vol-target 可部署两条曲线都存):carry
  0.45/77% → 0.65/22%;crowd_contra 1.21/12% → 1.38/11%;combo 0.58/56% → 1.20/15%;trend 0.40/37% → …。** 注:`pnl.sharpe`
  报 RAW 曲线;VT(vol-target 高波动降杠杆)是可部署口径。**关键教训:统一 book 的 naive 权重 blend 是 carry-dominated
  (carry 58 币 vs crowd 14 大币半权)→ combo VT 1.20 虽 improved carry 但仍 < crowd_contra alone 1.38;equal-risk 分散
  (收益流组合 1.95/8%)≠ naive 权重 blend——要在执行里兑现分散需 risk-balance 两个 sleeve(下一步)。** 实盘=非目标。
- `services/risk/research_risk/` — 9-engine fail-closed gate, **now tested**
  (`tests/risk/test_research_risk_gate.py`). `services/risk/pre_trade/` — real
  fail-closed pre-trade gate + latched `KillSwitch` (the successor to the
  quarantined `trading/pre_trade_risk`); tested but **not yet surfaced** in any
  endpoint/page.
- `services/alpha/` (26 modules / 185 tests) — catalog, lifecycle SM, evaluator,
  BH-FDR ranker, population_optimizer, decay, crowding/capacity, correlation/
  cluster, orthogonalize, zoo, alpha_graph, lineage, degradation_monitor.
- `services/data/` (89 tests) — PIT store, survivorship-free universe, versioning,
  quality, normalization, PIT corporate actions, catalog.
- `services/portfolio/` (57 tests) — constrained optimizer (feasibility-checked),
  PSD-checked factor risk model, construction, attribution, signal_adapter.
- `services/intelligence/` (59 tests) — guardrailed non-human `ResearchAgentLoop`
  + guided/evolutionary/GP/MCTS search strategies + bandit persistence.
- `services/{features,execution,monitoring,deployment}/` — leakage detector +
  feature registry; execution boundary + intent builder; hash-chained audit log +
  health; fail-closed deployment/version-pin gate. All tested.
- `services/trading/dry_run_adapters/` — real, tested, fail-closed dry-run
  adapters (no broker SDK).
- `services/trading/broker_adapter.py` — **broker adapter 接缝(轨道3 子项目1)**:把"实盘=换适配器+
  人工批准"里的「换适配器」形式化成 `BrokerAdapter` Protocol(`submit_order`/`fetch_fill`/`is_dry_run`)。
  `DryRunBrokerAdapter` 是透明超集(薄包装 `DryRunAdapterEngine`,零改动它;既暴露接缝方法又透传
  `process`/`simulate_fill` 故在现有流水线 drop-in)。`LiveBrokerAdapter` 是接缝的 live 槽位但**结构性拒绝**:
  L2 下 `SafetyConfig.live_trading_armed` 恒 False → 构造即 `raise SafetyViolation`,即便强凑 armed 也因
  无真实现再 raise,方法也 raise,**绝不 import 券商/网络 SDK**。`assert_dry_run_adapter` fail-closed 守卫
  接进 `dry_run_pipeline.simulate_fills` 首行(接缝载荷承重,只放行证明为干跑的适配器)。治理
  `tests/governance/test_broker_adapter_boundary.py` 机器钉死"无 SDK/网络 import + L2 下 live 不可实例化"。
  诚实边界:守卫是运行时防误用 chokepoint,**非**受信内进程代码信任边界(那是轨道3 子项目3 进程隔离)。
  真券商适配器实现 = post-L2、人工门控 = 显式非目标。
- `services/governance/approval.py` — **多方人工批准状态机(轨道3 子项目2)**:把「人工批准」从单人一张条子抬到
  **多个不同真人 + 全程哈希链审计**。不可变 `ApprovalRequest` 聚合 + 纯转换 `open_request/review/approve/reject/
  consume`(每步追加自带哈希链的 `ApprovalStep`,与 `AuditEvent` 同法防篡改)。硬不变量:`REQUESTED→REVIEWED→
  APPROVED→CONSUMED`,**提出/复核/批准三个两两不同的真人**,批准一次性、绑定 subject,非人/单人到不了 APPROVED。
  核心无墙钟/无随机(`at:int`、id 由调用方传)。`verify_multi_party_grant(req, subject)` fail-closed(唯一放行:
  CONSUMED ∧ subject 绑定 ∧ ≥3 不同真人 ∧ 哈希链自洽,否则/任何异常 False)。**接进现有两门(只做加法、零回归)**:
  `KillSwitch.reset_with_grant(actor, grant, *, at)` 与 `evaluate_deployment(..., grant=)` 新入口走多方校验,旧单
  `ApprovalRecord` 路径一字不动;LIVE 仍被 readiness pin + LIVE flags 结构性挡死(grant 合法也不解锁 LIVE)。治理
  `tests/governance/test_multi_party_approval_boundary.py` 钉死"无 SDK/网络 import + 非人到不了 APPROVED"。诚实边界:
  强化的是*批准的构造路径*,单进程内挡不住受信代码凑三个 HUMAN Actor 绕过(那是子项目3)。
- `services/governance/{approval_store,promotion_api/}.py` + `services/alpha/promotion.py` —
  **晋级闭环(战线①,2026-07-06)**:多方批准 append-only sqlite 持久化(`approvals.db`,
  write-only-adds 严格前缀检查 + 同事务链头承诺 n_steps/head_hash 抓整体截断 + fail-closed
  重载逐条重算哈希;并发写者靠主键撞 IntegrityError → `ApprovalStoreConflict`)+ 晋级执行核
  (`execute_promotion`:预检全部不烧 grant → **先烧 grant 再 advance**(崩溃/失败即作废,
  绝不留可复花 grant)→ 烧后全路径落 `DurableAuditLog`;证据只来自 evaluation artifact 的
  `net_sharpe`/`catalog_trial_count` + paper_track.db,artifact 被篡改经 `verify_evaluation`
  判废,任一缺失 guard fail-closed 拒绝——grant 路径比旧单人路径**更严**:eval_trial_count
  取不到即拒,不像旧路径 None 静默跳过)+ 独立 mutating 服务 promotion_api(OIDC 非对称验签 +
  MFA + RFC 8705 mTLS token binding + 动作 RBAC,GET 也认证,CORS 仅 3100,默认不启动且缺外部
  身份策略即拒)+ 前端 `PromotionQueue.tsx`(Bearer 身份、候选证据卡/步骤时间线/历史)。
  `AlphaLifecycleManager.advance(grant=, eval_trial_count=)` 纯加法入口,旧 ApprovalRecord
  路径一字不动;grant.request_id 记进 approval_id/evidence_hash(转换链与三方批准相连)。
  62 tests(后端 56 + 前端 6)。设计+评审对照:`docs/engineering/specs/
  2026-07-06-promotion-closed-loop-design.md`。
- `services/governance/signing.py` + `docs/PHASE_14_PROCESS_ISOLATION.md` — **出进程签名权威接缝(轨道3 子项目3,
  收口)**:全代码库反复出现的同一句诚实注脚——「单进程内挡不住受信代码」(`verify_ref` 无密钥哈希、直接凑三个
  `HUMAN` Actor、直接调 manager/catalog、重算 broker 守卫)——本子项目一次性回答它:真防御只能在进程外。
  `SignedDecisionRef`(纯值:payload_hash/signature/key_id/issued_by)+ `SigningAuthority` Protocol +
  `NullSigningAuthority`(L2 默认:进程内**无密钥**、`sign()` 即 `raise SigningUnavailable`)+ `verify_signed_ref`
  fail-closed(无出进程权威 → 恒 False;**即便受信代码伪造 is_out_of_process=True 的权威也 False**)。治理
  `tests/governance/test_signing_boundary.py` 钉死"无密钥材料(无 PEM 头/`*_KEY=` 字面量)+ 无券商/网络/HSM/密钥
  SDK import + L2 签不出验不过"。**关键诚实决定:不接进 L2 执行路径**(L2 无真权威,强接要么死代码要么堵死干跑,
  都不诚实)——独立的拒绝式制品 + 文档,与 `LiveBrokerAdapter` 对称;真出进程签名器/真进程隔离部署 = post-L2 = 显式
  非目标(迁移路径见文档 §5)。**轨道3 三子项目全 landed:券商接缝 ✓ / 多方人工批准 ✓ / Phase 14 进程隔离设计 ✓。**
- `services/execution/dry_run_pipeline.py` + `dry_run_store.py` — **端到端干跑流水线**
  (the successor that turns the pre-trade gate from "tested dead code" into a RUNTIME
  chokepoint). `run_dry_run_pipeline`: 权重→目标手数→`build_intents`→逐意图过
  `PreTradeRiskGate.evaluate` + kill-switch (**fail-closed**, latched→全拒)→干跑适配器
  受理(纸面、**绝不真发**)→`PaperFill`→`reconcile` 平均成本 PnL(恒等式残差≈0)→
  `GlobalAuditLog` 哈希链→可复现 `DryRunRun` 落 `data/cache/dry_run.db`. Deterministic core
  (墙钟/seed only at the driver boundary). Driver `tools/dry_run_carry.py` runs the carry
  book end-to-end (latest COMPOSITE legs × vol-target leverage). `run_multi_period_dry_run` +
  `tools/dry_run_carry_multi.py` 把它升级为 **周度滚动**:逐周增量调仓单都过门(fail-closed)→
  纸面成交→`reconcile` 价格 MTM + 资金费现金流(carry 的真边)→raw/vol-target 两条净值曲线→多期
  `DryRunRun.detail.equity_curve`. **Surfaced read-only** on `/api/v1/console/dry-run` + page
  `DryRunPipeline.tsx` (nav 「干跑流水线」; single-snapshot + multi-period SVG 净值曲线, browser-verified).
  Honest finding: 周度滚动 carry 多数收益是 L/S 价差(非 beta 中性、回撤大 ~77%),funding 是较小稳定项——
  执行现实把理想化 carry 的尾巴暴露出来(美元中性实验:净敞口已 ~1.8%、回撤 $1M↔$100M 规模不变 → 截面价差本身,
  非舍入). 17 tests + contract + governance (no broker/network import; `services/execution` 不 import `tools/`;
  orchestrator NOT in research_runner). 策略无关已验证:第二驱动器 `tools/dry_run_trend_multi.py`(日线
  dist_from_high+momentum 趋势,`cash_flow_fn=None`)走同一条 `run_multi_period_dry_run`(18 币×47 周,
  +7.5%/回撤 37%),与 carry 共存 dry_run.db(console 历史表两条并列)——流水线是策略无关的执行骨架,非 carry 专用.
  第三驱动器 `tools/dry_run_crowd_contra_multi.py`(`crowd_contra=cs_rank(neg(global_ls))` 截面拥挤反向 OI 因子,
  含资金费现金流)走同一条 `run_multi_period_dry_run`(14 大币×232 周×10bps),三条策略并列于 dry_run.db.
  **诚实结论(crowd_contra 执行现实检验):总收益 +185.6% · 夏普(周→年)1.21 · 最大回撤仅 12% · 门批准 3066/拒绝 0 ·
  对账残差 7e-09** —— 三条里执行现实表现最好的一条(夏普远高于 carry 0.45/trend 0.40,回撤远低于 carry 77%/trend 37%),
  与它 IC t-stat 4.32、过 4/5 证伪一致。诚实边界:这是对**已知历史**的执行现实回测(整手+门+成本+换手,残差≈0 恒等式成立),
  **非前向/实盘**;宇宙仅 14 大币;证伪已过≠前向必赚——是否送前向 paper-track/加仓位是另一独立决策。
  实盘 = 同接口换真券商适配器 + 人工批准 = 显式非目标.
- `services/console/main.py`（正式组合根）+ `services/console/api/main.py`（只读 HTTP 路由/展示层）— FastAPI **read-only** API (~31 GET
  endpoints), now **mostly REAL** (distinct provenance markers; fail-closed to
  `SYNTHETIC_DETERMINISTIC` only when an artifact is absent, e.g. CI). REAL: `research-runs`/
  `research-run` (run_registry sqlite), `crypto-live` (CoinGecko offline snapshot),
  `fx-research` (ECB/Frankfurter snapshots + tradable gauntlet), `lifecycle-monitor`
  (experiment_registry artifacts + real paper-track `live_ic`/recommendation), `alpha-zoo`
  (12-dim real eval + time/活体征 dims), `backtest-results` (run_registry by role),
  `data-catalog` (real PIT datasets), `safety-primitives` (real KillSwitch/gate), `portfolio`
  (real optimizer/risk-parity), risk-dashboard/validation (real engines), `dry-run` (DryRunStore
  runs), `architecture`+`system-completeness` (**REAL_REPO_INVENTORY** — live read-only repo
  introspection via `_repo_inventory()`: per-domain module/LOC + test/page/endpoint counts, grounding
  the curated maturity map in hard counts). The residual synthetic surfaces are the genuinely-missing
  governance/process subsystems (incident/committee/RBAC/…), kept HONESTLY LABELED (not faked — faking
  would break the honesty posture); synthetic root `_factor_values`/`_forward_returns` (`math.sin/cos`);
  contract-guarded by `tests/research/test_console_api_contract.py`.
- `apps/research-console/src/` — React 18 + Vite 6 + TS (strict) + Tailwind. **2026-07-03
  信息架构重构:42 页 → 30 页 · 7 个工作流导航组(总览→数据→因子研究→验证→组合与风险→执行与跟踪→
  治理与系统)**。路由/导航/冒烟测试的唯一真源 = `src/app/routes.tsx` 页面注册表(新页加一行,
  自动进导航 + 自动被冒烟覆盖);删掉的 12 页 = 纯合成蓝图(incident/reviewer/access/committee/
  promotion/model-risk)+ 合成或重复(Agents/Phase3A/ScenarioStress/BacktestExplainability/
  SignalToPortfolio/Overview),旧路由语义就近重定向(蓝图页 → SystemCompleteness,那里
  REAL_REPO_INVENTORY 诚实列出缺失子系统——删页不删诚实);后端 console_api 端点零改动
  (契约测试锁定)。测试地板:**30/30 页全冒烟 + 注册表一致性测试**(44 fe tests)。

Quarantined / broken:
- `services/trading/pre_trade_risk/main.py` — **NON-FUNCTIONAL** (missing domain
  modules + undefined-name bug). Superseded by `services/risk/pre_trade`. Do not
  wire; delete or rebuild in Phase 8.

Shared contracts:
- `quantos/contracts/` — canonical domain contracts (SafetyConfig §1.2,
  RejectionReason §8, OrderState §7, RiskDecision). Single source of truth.

Genuinely empty (PLANNED, no code): `services/{gateway,learning}` (no auth/online-
learning layer), the nested DDD sub-trees under `services/{alpha,data,research}/*`
(`alpha_discovery_engine`, `*_service`, `model_registry`, `experiment_tracker`,
`dataset_builder`, …), `platform/`, `schemas/`, `proto/`, `config/`. The real code
lives in the FLAT `services/<domain>/*.py` modules, not these shells.

## Commands

```bash
# Full fail-closed gate (governance scanner + all runnable test suites)
python scripts/validate.py

# Governance scanner only
python apps/research-console/scripts/run_paper_to_live_governance_check.py

# Frontend (mock-first; opens http://localhost:3100/research)
cd apps/research-console && npm ci && npm run typecheck && npm run build && npm test

# Frontend against the REAL read-only backend (Phase 12 wiring)
#   1) start the console API (read-only computed snapshots, no broker/OMS):
python -m uvicorn services.console.main:app --host 127.0.0.1 --port 8400
#   2) point the console at it and disable mock mode (CORS allows :3100 only):
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_MOCK_MODE=false npm run dev

# Research-ops console (operate research FROM the frontend): also start the GUARDED runner.
#   research_runner is the ONLY mutating surface — POST /run triggers a WHITELISTED research kind
#   (backtest/search/data refresh) async into the run_registry; it touches NO order/broker/credential
#   path (machine-enforced by tests/governance/test_research_runner_boundary.py). console_api stays GET-only.
python -m uvicorn services.research_runner.main:app --host 127.0.0.1 --port 8420
#   then run the frontend with both URLs (the `research-console-live` launch.json config sets these):
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_RUNNER_URL=http://127.0.0.1:8420 \
  VITE_MOCK_MODE=false npm run dev   # → /research/runs lists every run; buttons trigger new ones
#   2026-07-06: POST /run + /cancel now require a local shared-secret header (X-Runner-Token,
#   mirrors live_ops_api's X-Ops-Token) — first launch generates key/research_runner_token.txt
#   (gitignored); paste its contents into the "Runner Token" field on /research/runs.
```

Note: all backend services now live under the repo-root `services/` tree
(unified in Phase 1; `apps/research-console/` is frontend-only). `scripts/validate.py`
runs the full suite from the repo root — prefer it over a bare `pytest`.

Phase 12 wiring is contract-guarded on BOTH sides: `tests/research/test_console_api_contract.py`
(in the gate) asserts every endpoint returns the exact top-level keys the
frontend `*Payload` types read, and the console API client validates the
read-only snapshot markers at runtime (`assertConsoleSnapshot`) so a drifted /
non-snapshot 200 surfaces as a visible error + honest mock fallback rather than
silently rendering `undefined`. Change a payload shape → change both the TS type
and the contract test in the same commit.
