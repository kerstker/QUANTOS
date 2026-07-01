# QUANTOS — Agent Operating Guide

QUANTOS is a **safety-critical AI quantitative trading research platform**.
Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14). Program plan:
[`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md).

Readiness: **L2_DRY_RUN_ONLY** — research + dry-run simulation only. No live
trading, no broker connectivity, no real credentials.

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

## What actually exists (verified 2026-06-29 by full-system audit)

Authoritative reality map + completion roadmap:
[`docs/superpowers/specs/2026-06-29-system-audit-and-completion-roadmap.md`](docs/superpowers/specs/2026-06-29-system-audit-and-completion-roadmap.md).
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
  de-redundant set selection, reproducible orchestrator.
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
- `services/research/paper_trading/domain/pnl.py` — **real reconciled PnL
  attribution** (realized+unrealized−costs, `reconciliation.py`/`consistency.py`).
  No longer the `notional*0.001` placeholder this file used to claim.
- `services/research/{run_registry,research_runner,experiment_registry}/` —
  durable sqlite run store + the guarded mutating runner + immutable experiment
  artifacts/DSR trial-count persistence.
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
- `services/research/realism/{impact,capacity_curve}.py` — **现实主义引擎 RE-1(市场冲击 + 容量)**:把回测/干跑的
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
  两个都对、不同度量;③ 尾偏肥(Hill 尾指数 ~2.4–2.7,类 t 分布);④ trend 容量把 `crypto_daily.db` 的 volume 单位跨币
  不一致 bug 逮出来(EOS ~$20k vs BTC ~$3.7Q,~17 数量级不可能真实)——诚实标 `UNRELIABLE_VOLUME_UNITS` 不报假数,已
  spawn 独立修复任务。** 诊断非门禁。crowd 2021-05 窗口无数据(早期 OI global_ls 稀疏,诚实标注)。
- `services/research/macro_regime.py` + `tools/{fred_fetch,macro_regime_research}.py` — **FRED 宏观数据方向(第一增量)**:
  股票免费源本环境被墙(Stooq bot-wall / Yahoo 429,不绕过),**FRED `fredgraph.csv` 可达**。`fred_fetch.py`(网络在 tools/
  出 gate)抓 6 条**市场型日频序列**(VIX/DGS10/T10Y2Y/HY-OAS/DXY/DFII10,39271 行)填进**已建但从未填的** `fundamental_store`
  (`macro_fred.db`,`knowledge_time=event_time` EOD 可知无前视)。`macro_regime.py`(纯核:三分位/符号 regime/收益分桶)。
  驱动器把 carry/crowd_contra 逐周 gross 收益按宏观 regime 分桶(PIT `latest_as_of`)。**关键发现:两条边宏观 regime 画像
  相反、互补——carry 爱平静/risk-on(低VIX SR+2.36 vs 高VIX+0.65;正常曲线+2.08 vs 倒挂+0.67),crowd_contra 爱压力/risk-off
  (高VIX+1.96 vs 低+1.25;倒挂+1.76 vs 正常+0.88)——crowd 正好在 carry 走弱处走强 = 强分散故事**,系统化了 RE-2 的 LUNA/FTX
  危机发现。诚实边界:regime 标签=全样本分位(回顾性描述,非 PIT 可交易择时);gross 无成本(RELATIVE 模式才是结论);经济发布类
  (CPI/GDP,需 ALFRED vintage)+ 股票(BYO-CSV/ALFRED)= 显式待办;HY-OAS 仅 2023-07+(FRED 限)信用维覆盖 155/期。诊断非门禁。
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
- `services/research/console_api/main.py` — FastAPI **read-only** API (~31 GET
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
- `apps/research-console/src/` — React 18 + Vite 6 + TS (strict) + Tailwind; **37
  pages, route-lazy code-split** (+ FX Research). The audit doc's original split
  (7 REAL / 6 PARTIAL / 17 MOCK / 6 NONE) predates this session — REAL has grown
  substantially after the 4-break + paper-track + FX wiring (CryptoLive, FxResearch,
  ResearchRuns, LifecycleMonitor, BacktestResults, DataCatalog, Portfolio, AlphaZoo, …).

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
python -m uvicorn services.research.console_api.main:app --host 127.0.0.1 --port 8400
#   2) point the console at it and disable mock mode (CORS allows :3100 only):
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_MOCK_MODE=false npm run dev

# Research-ops console (operate research FROM the frontend): also start the GUARDED runner.
#   research_runner is the ONLY mutating surface — POST /run triggers a WHITELISTED research kind
#   (backtest/search/data refresh) async into the run_registry; it touches NO order/broker/credential
#   path (machine-enforced by tests/governance/test_research_runner_boundary.py). console_api stays GET-only.
python -m uvicorn services.research.research_runner.main:app --host 127.0.0.1 --port 8420
#   then run the frontend with both URLs (the `research-console-live` launch.json config sets these):
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_RUNNER_URL=http://127.0.0.1:8420 \
  VITE_MOCK_MODE=false npm run dev   # → /research/runs lists every run; buttons trigger new ones
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
