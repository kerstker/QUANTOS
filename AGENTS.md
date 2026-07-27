# QUANTOS — Agent Operating Guide

QUANTOS is a **safety-critical AI quantitative trading research platform**.
Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14). Program plan:
[`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md).

Readiness: **L2_DRY_RUN_ONLY (default)** — 分层表述,三层实况:

- **L2 研究 + 干跑(默认态)**:研究与全量测试默认运行在 L2,mainnet
  实盘被多重结构性人工门封锁(见下)。
- **Testnet 已联通**:`live/` 执行栈已存在并联调 —— Binance/Bybit
  期货 testnet 适配器(`live/exchange/`)、`live/pipeline.py::submit_live_batch`
  (attestation→plan→gate→mandate→ledger→place→confirm→audit 全链)、
  `tools/live_ops_api.py`(port 8440 只读运维 API)。
- **Mainnet 被多重人工门结构封锁**:mainnet 适配器构造即
  `raise SafetyViolation`(`SafetyConfig.live_trading_armed` 默认 False);
  下单必须经过 validation attestation + 人工 mandate(AUTO 拒绝)+
  pre-trade gate + kill-switch + 账本预记账;MEXC 现货 mainnet 适配器
  (建设中)同样遵循。MEXC 无现货 testnet → 毕业路径 = 干跑 → 小额真实 +
  人工 mandate → 规模化。无真凭证落盘承诺不变(`key/` gitignored,
  只写格式文档,绝不写真实密钥)。

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
[`docs/engineering/specs/2026-06-29-system-audit-and-completion-roadmap.md`](docs/engineering/specs/2026-06-29-system-audit-and-completion-roadmap.md).
**The backend is substantially built and tested (1631+ tests pass). The console-wiring
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
- `tools/deribit_fetch.py` + `services/research/vol_factors.py` + `tools/vol_research.py` — **第四数据类
  (options/vol,2026-07-24)**:Deribit 公共 API v2(无 key 只读,治理钉进 `test_crypto_data_boundary._FETCHERS`)
  三数据面。① **DVOL 日频**(BTC/ETH 均 2021-03-24 起 1948 天,`continuation` 翻页);② **永续+到期期货日线**
  (BTC-PERPETUAL 2018-08 起 2901 天;已结算合约按「每月最后周五」约定名探测重建历史——`get_instruments?expired=true`
  已不回全量名单,但已知名字的已结算合约仍给全量日线;**实测坑:响应把结算价常数前填到今天**(BTC-25MAR22 自
  expiry-1 起 44019.0 重复 1500+ 天)——`fetch_chart_rows` 硬截到期日 + `trim_constant_tail` 去填充,零 post-expiry
  违规已验证);③ **期权 book 快照**(无 greeks → Black-76 自算 delta → 25Δ skew 取最近 ±0.25 行;**免费 skew 历史
  不存在**,按日 key 落 `deribit_options_skew.json` 从 2026-07-24 向前积累)。PIT `data/cache/deribit_vol.db`
  (31,232 bars / 145 keys,kt=et,水位线幂等,重跑零新行已验证)。`vol_factors.py` 纯核(dvol_level/change、
  iv_rv_spread(DVOL 是百分数非小数,年化 √365)、annualized_basis、term_structure_slope、时序 zscore、bs_delta、
  skew_25d、ts_ic_stats n<10 全 None)。驱动器落 `evidence/vol_research.json`。**诚实结论:24 次检验
  (6 因子 × 2 标的 × 2 horizon)无一过多重比较+非重叠双门槛——最强 BTC dvol_zscore_30d h1 t=3.08 但 h7
  非重叠 t=1.87 不确认;ETH 全弱(|t|<2)。regime overlay:carry 高 DVOL 桶 SR 1.67 < 低 2.03 < 中 2.69
  (高 vol 桶最差,与 VIX「carry 爱平静」同向、温和);crowd_contra 低 DVOL 2.73 > 高 1.37 > 中 0.84
  (与 VIX「crowd 爱压力」**方向相反**,诚实并存两口径)。** 诊断非门禁;skew 因子待快照积累后再评。
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
- `services/research/factor_decomp.py` + `tools/common_risk_research.py` →
  `evidence/common_risk_decomposition.json` — **W3 策略簇 & 公共风险分解(spec §7,2026-07-24)**:
  §7.1 因子库 10 因子全由既有本地数据构造(BTC/ETH 市场、4 周趋势、1 周反转、已实现波动 Δ、
  成交量流动性 Δ、OI 规模 Δ、资金费周累计、基差 Δ、OI 拥挤度 Δ;清算/Amihud/skew/链上/跨资产
  诚实标 MISSING);per 策略 OLS(lstsq 闭式,确定性;**残差均值被截距吸收恒 0 → 「边」看
  alpha=截距的 t + mean_attribution 均值归因**);聚类复用 `services/alpha/cluster.py`
  complete-linkage(|ρ|≥0.5 UNCALIBRATED,反链式);breadth=1/ΣW_c²(逆波动权重);
  26 周滚动 mean|ρ|>全样本+0.25 冲击(UNCALIBRATED)。**首跑结论(192 共同周,4 book 策略):
  ① carry/crowd/top 的边都不是伪装 beta**——R² 仅 0.045–0.198,alpha +8.0%~+20.3%/y
  (t 1.0~1.93);carry 对市场资金费水平反而是**负**载(标准化 β=−0.36,t=−3.78:截面 carry
  的边在价差,市场级拥挤多头 regime 是逆风);② **无隐藏共同敞口**——raw 与残差相关矩阵
  最大 |ρ| 0.15,4 策略在 0.3/0.5/0.7 三档阈值下全是单例簇,breadth 3.78/4(raw=残差),
  VT 口径同结论;③ 相关冲击当前平静(0.144 vs 阈值 0.32),历史仅 2024-W17~19 三周;
  ④ 基准验证:等权主流币篮子 R²=0.81·β_BTC t=4.5·alpha≈0(机器工作正常;
  `btc_spot_trend_hold` 非干净买入持有——119/216 周空仓——不能当基准,诚实记录)。
  诊断非门禁;W5 组合层消费簇结构。测试 `tests/research/test_factor_decomp.py` +
  `tests/tools/test_common_risk_research.py`(34)。
- `services/research/exit_policies.py` + `tools/exit_policy_lab.py` →
  `evidence/exit_policy_lab.json` — **W4 退出策略实验室(spec §8,2026-07-24)**:EXIT 升为
  一等独立研究对象。纯核:`ExitDecision{HOLD|REDUCE|EXIT, target_fraction∈[0,1],
  reason_code, policy_version}` + 8 策略(baseline_signal_only 恒为对照 / fixed_stop /
  atr_stop / chandelier / time_stop / scale_out / vol_adaptive_stop(σ×√t 距离)/
  risk_budget_reduce(W6 仓位级镜像、**无闩锁**));**只减不加**(执行=信号×fraction,
  退出永不制造信号外敞口);硬止损触发后锁定至信号复位(防「同信号再进场→再止损」抖动),
  risk_budget 乘数随修复即时回升(episode 由信号驱动,不减仓状态也能延续水位线)。无前视
  双保险:MarketState 访问器机器过滤 `event_time≤as_of`(性质测试:追加极端未来 bar
  决策逐字段不变)+ 驱动器 `fetch_bars_pit`。接缝 = `apply_exit_policy` **权重流后处理器**
  (管线门/对账/成本零改动);全 book 名单全期定价 + 不叠 crowding gate = 与 canonical
  存盘 run 的两点刻意偏差(实验室内自洽)。8 策略 × 2 书(crowd 236 周@10bps / btc 迟滞
  72 周@5bps)过同一 `run_multi_period_dry_run`,成本合计由零成本孪生跑精确反解。预注册
  选择规则(exit_lab_selection.v1):ΔSharpe>0 ∧ Δmdd≤2pp ∧ 正增量不被 <5 期全覆盖 ∧
  PF≥基线,四条件全过才算 worth-further-study;排名多准则、**禁止以最终净 PnL 单独选择**
  (§2.3)。**首跑诚实结论:两书底层信号在纯信号退出下均盈利,7 策略全灭——crowd 每个
  止损变体都毁 Sharpe(反向书的边在扛拥挤回撤,止损砍掉的正是信号要扛的坑;scale_out 最
  差 ΔSharpe −0.42;time_stop 最温和:Δmdd −4.4pp 但 PF 不达标);btc 迟滞信号自带
  动量退出 → 止损 0 触发,唯一「有效」的 scale_out(ΔSharpe +0.11)增量集中于 1 期、被
  集中度规则硬否(CONCENTRATED_DELTA)。实验室的诚实答案 = signal-only exits are fine。**
  测试 `tests/research/test_exit_policies.py`(29)+ `tests/tools/test_exit_policy_lab.py`(6)。
  诊断非门禁。
- `services/research/allocators.py` + `tools/allocator_research.py` →
  `evidence/allocator_research.json` — **W5 条件组合配置器(spec §9,2026-07-24)**:策略层
  资本配置从等权基线升级为「簇风险预算 + vol target + regime 条件化 + 稳健回退」并在
  **锁定 OOS** 上对照 6 个保留配置器。纯核:6 配置器(equal_weight 不可删对照 /
  inverse_vol(包装 book_combine)/ cluster_risk_parity(W3 簇图,簇内逆波动×簇间逆波动
  预算×簇帽投影)/ constrained_max_sharpe(trailing 均值向 0 收缩 0.5 + 确定性单形网格
  0.02 + 约束)/ turnover_penalized(死区部分调整,实现换手=max(0,S−2λ),λ 精确单调、
  凸组合保持可行域、初始建仓不罚)/ regime_conditioned(stress>0.5 才向 fallback 倾斜,
  **低置信 → 逐位精确回退**,性质测试钉死))+ `apply_constraints` 注水投影(非负/策略帽
  0.40/簇帽 0.40,不可行诚实标 feasible=False 绝不超帽)+ `build_regime_series`(全 PIT:
  VIX/DVOL expanding 三分位、资金费/已实现波动 trailing z、W3 相关冲击的截断不变变体;
  **t 决策只用 ≤t−1 已完结周**,FRED 全样本分位是描述性的此处不用)+ 走前引擎
  (承重可行性校验,违规 ValueError)。评估:4 book 策略公共 192 周 RAW 曲线,**锁定切分
  fit≤2024-12 / locked≥2025-W01(81 周唯一评分窗)**,trailing-only 周度 PIT 再平衡,
  成本 10bps×Σ|Δw|(干跑同口径)。**首跑结论:主判定 ADOPTED-AS-RESEARCH-FINDING——
  turnover_penalized_max_sharpe 净夏普 1.669 > EW 1.468(Δ+0.201>0.1,回撤 0.026≤1.5×);
  但敏感性硬否稳健性——剔除 top_contra 后 EW_REMAINS(best 0.787 vs EW 0.771,Δ仅
  +0.016):配置层全部超额是 top_contra(治理 PAUSED、前向证据为负)锁定窗尾边的载体,
  单条 top_contra 1.782 高于所有配置器;regime 条件化本轮结构性空转(W3 簇图全单例 →
  CRP≡IV,base 与 fallback 重合,4 个 stress 周触发 tilt 但权重零变化,机制经测试就绪
  待多成员簇);VT 杠杆诊断块(scale~1.6)不进判定;crowd_contra 单条锁定窗净夏普仅
  0.128(边在 2025+ 衰减,与前向 BLOCKED 一致)。** 测试
  `tests/research/test_allocators.py`(29)+ `tests/tools/test_allocator_research.py`(9)。
  诊断非门禁,不接 live(live 回撤控制器独立缩放 deploy)。
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
  quarantined `trading/pre_trade_risk`). The kill-switch now has an append-only
  SQLite authority: ARM/RESET state and canonical event outbox commit together,
  official live drivers use CAS, and the console reads the authority fail-closed.
- `services/alpha/` (26 modules / 185 tests) — catalog, lifecycle SM, evaluator,
  BH-FDR ranker, population_optimizer, decay, crowding/capacity, correlation/
  cluster, orthogonalize, zoo, alpha_graph, lineage, degradation_monitor.
- `services/data/` (89 tests) — PIT store, survivorship-free universe, versioning,
  quality, normalization, PIT corporate actions, catalog. 存储层提供
  `fetch_bars_pit(instrument, start, end)`(SqlitePITStore + CachingPITStore,2026-07-23):
  批量读但逐 bar 过滤 `knowledge_time <= event_time`——仅对「每根 bar 只在自己的
  event_date 消费」的 panel builder 是 PIT 安全的,不能替代决策时点 `as_of(t)`;
  services/ 裸 `fetch_bars` 仍被 AST 治理钉死在白名单 infra。
- `services/portfolio/` (57 tests) — constrained optimizer (feasibility-checked),
  PSD-checked factor risk model, construction, attribution, signal_adapter.
- `services/intelligence/` (59 tests) — guardrailed non-human `ResearchAgentLoop`
  + guided/evolutionary/GP/MCTS search strategies + bandit persistence.
- `services/{features,execution,monitoring,deployment}/` — leakage detector +
  feature registry; execution boundary + intent builder; hash-chained audit log +
  health; fail-closed deployment/version-pin gate. All tested.
- `services/lifecycle/` — **三个正交状态机(727 §3/§11,C1–C3 已落地)**:把混合的
  `AlphaState` 拆为 `research_state`(研究是否成立)/ `deployment_state`(跑在哪里,
  唯一带人类专属边的机器:进入 MICRO_LIVE/LIVE 必须真人 + Grant,L2 下
  `REAL_CAPITAL_BLOCKED_AT_L2` 结构性拒绝,PAUSED→LIVE 无边)/ `health_state`
  (当前是否健康,带原因码)+ `projection`(旧 `AlphaState` → 三元组的加法投影,
  UNKNOWN_LEGACY 诚实哨兵零入边)。纯数据+纯函数、无 IO、不 import `services/*`,
  故编排器 `services/alpha/lifecycle.py` 可采用而无循环依赖。**C3 已切换**:
  `degradation_monitor`/`maintenance`/`retirement` 按部署态筛选、衰退经
  `AlphaLifecycleManager.advance_health` 走 health 机器——降级不再覆盖部署位
  (VALIDATED+PAPER+DEGRADED 首次可表达)、monitor 的 PAUSED 建议映射 FAILED+自动
  降险、原因码机器可读;legacy 字段与报告 action 串逐位不变(旧套件零修改通过),
  HEALTHY 恢复刻意未接线(§11.3 人门)。持久化:C2 双写 + C3 `lossy_json` 第 7 列,
  发散三元组完整往返。行为变化评审清单见设计文档 §7.3
  `docs/engineering/specs/2026-07-27-three-orthogonal-state-machines-design.md`。
  C4(console 三维展示)已落地:lifecycle-monitor 行带三态 + triple_source
  provenance(STORED/PROJECTED/SYNTHETIC/UNKNOWN),前端 R/D/H 列,契约测试锁定。
- `services/orchestration/` — **727 §2.5 自治内核(K1–K3 + K4a 已落地,84 测试)**:
  K1 `command_envelope`(727 §4.2 信封,stable_hash fail-closed)+
  `idempotency.py::SqliteCommandLog`(持久幂等命令日志,RECEIVED→COMPLETED/FAILED
  CAS 状态机,重链哈希链,in_flight 只上面绝不自动重试)+ `command_bus.py`
  (幂等受理→handler→publish_once 因果事件→落结果;重复命令不产生重复副作用)。
  K2 `leader_lease.py::SqliteLeaderLeaseStore`(单实例 Leader + fencing token +
  任务租约:token 单调递增释放不归零,stale leader 的 require_owner/renew/release
  一律 fence,过期判定 now>=expires)。K3 `scheduler.py`(持久化确定性调度器:
  leader 门控兑现 + SKIPPED_NOT_LEADER 审计、按计划时刻推进不漂移、错过补跑合并
  COALESCED 诚实留痕、幂等补跑=K1 重放(key 编 attempts)、确定性退避 + DLQ
  只上面、未知结果大声抛出;三 store 可共库)。K4a `policy_engine.py`(统一
  策略评估接口:注册确定性纯函数,fail-closed 三规则——未注册/判定器异常/
  非法 verdict 绝不放行,ABSTAIN≠FAIL,evidence_hash 只经 create;不迁移任何
  现有判定器)+ `event_router.py`(声明式类型→handler 路由表,多 handler 注册序,
  RouterDispatchError 聚合不回滚,at-least-once 幂等是 handler 责任,replay_from
  增量对账)。确定性核心(时间全为调用方
  int ms,不起线程)、无网络、**单节点诚实边界——非跨主机共识(post-L2)**。
  设计文档 `docs/engineering/specs/2026-07-27-autonomy-kernel-design.md`
  (K 系列切片计划;K4b workflow/recovery/门面未做)。
- `quantos/contracts/event_schemas.py` +
  `services/monitoring/{event_bus,event_projection,domain_event_runtime}.py` —
  versioned strict event schemas, a single-node durable Event log, append-only
  consumer journal (leases, offsets, retries, DLQ), an idempotent read projection,
  and a load-bearing single-node consumer-group coordinator. Group topology is
  immutable; heartbeat membership, correlation-stable partitioning, deterministic
  eager rebalance, generation fencing, and cross-restart partition offsets are
  tamper-evident. The official projection deliberately stays at one partition to
  preserve total order, heartbeats before processing, and leaves on clean exit.
  Latest schemas use Unix epoch microseconds; the affected historical
  kill-switch/promotion/deployment v1.0 records retain an explicit millisecond
  interpretation and v1.1 moves those types to microseconds without magnitude
  guessing. `evidence/event_schema_catalog.json` publishes 11 event types / 16
  definitions with compatibility policy, timestamp units, payload JSON Schemas,
  hashes, and implementation bindings; stale or tampered catalogs fail the unified
  gate and supply-chain manifest. Real same-store transactional outboxes now bind dry-run completion
  plus its exact research-risk subset decision
  to `DryRunRun`, alpha promotion to the experiment catalog snapshot, alpha
  maintenance transitions to the authoritative maintenance snapshot, and
  kill-switch ARM/RESET to its authoritative state chain. A fifth outbox binds
  deployment evaluation state to its canonical event; L2 state is restricted to
  `DENIED` or `ELIGIBLE_NOT_APPLIED`, never `DEPLOYED`. A sixth outbox commits each
  tamper-evident market-data refresh record with a redacted canonical data event.
  Verified per-partition lag/age, group DLQ counts, and append-only storage
  thresholds are surfaced by the console and a schedulable alert driver; retention
  remains alert-only and never deletes audit history. Dispatch is
  publish-then-ack and explicitly at-least-once. A separate schedulable recovery
  drain opens all six owner stores with `create=False`, redacts failures, drains
  independent sources, and advances the shared projection once; source processes
  no longer need to rerun after a commit-before-publish crash. JSON is only an atomic
  compatibility mirror. Actual deployment application/execution and real live
  pre-trade producers remain open. Cross-host membership/consensus, broker/replication/HA,
  strong worker identity, automatic externally sealed archival, and independent
  alert-delivery SLA remain open;
  the SQLite coordinator is not represented as a distributed event platform.
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
- `quantos/contracts/identity.py` + `services/governance/strong_identity.py` +
  `services/promotion_api/main.py` — **强身份 M2 基线(2026-07-12)**:晋级 API 已删除共享
  gov token 与自报操作者，承重入口只接受 allowlist 非对称签名 OIDC access token，强制
  issuer/audience/时效/MFA、RFC 8705 `cnf.x5t#S256` mTLS 绑定与动作 RBAC；actor/audit ID 只来自
  `issuer|subject`，缺外部策略或可信 TLS scope 即拒。浏览器端已实现 Authorization Code + PKCE S256:
  Web Crypto 生成 state/nonce/verifier，版本化 sessionStorage 只存一次性事务，access token 只驻内存。
  13 个攻击案例 + 三方 subject 分离证据在 `evidence/strong_identity_baseline.json`。诚实边界:外部 IdP
  运营、可信 TLS、密钥托管轮换、身份生命周期/撤销/访问复核及 AI workload 排除证据未提供，因此
  只到 M2/PARTIAL，`GOV.IDENTITY.STRONG_AUTH` 红线不关闭，绝不授权 live。
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
- `tools/crowd_contra_variants.py` + `evidence/crowd_contra_variants.json` — **预注册 crowd_contra
  变体族(2026-07-24,17 trial 含基线重跑)**:16 变体(分位扫描 0.20/0.25/0.35、节奏 3d/14d、
  taker/top 复合、分位×节奏网格、oi_delta 与 crowd+carry 信号级复合)过同一台 5 项证伪机器检验
  (按变体自身分位+节奏构造流;基线 cadence=1 锚定复现 verify 数字)+ IC 面板 + 容量带 +
  同一条 run_multi_period_dry_run(变体**不写** dry_run.db)。预注册相对判定:基线本次重跑
  VT 1.095/回撤 9.7%/证伪 4/5(2020-21 早年平,broad_across_years 失;宇宙已扩到 24 币,与
  历史 14 币记录不同属数据演进),**7 非基线 CANDIDATE**(q20、3d×3、14d、q20_14d、taker 复合),
  9 REJECTED 全记录。Trial 纪律:17 候选经 run_registered_candidate_batch 三节奏批注册
  (registry 2436→2453),DSR 按全 trial 总体泄气——**调整口径深度怀疑**(3d 变体 DSR~0.001,
  基线 0.84 最高;17 次同属一个信号族,有效独立赌注 ≪17)。7 个 CANDIDATE 已注册进
  `tools/forward_track_advance.py::_ALPHAS`(新增 dict spec:expr/quantile/cadence_days,
  原三条纯 tuple 路径逐位不变;3d/14d 变体走每日兑现网格 + 变体节奏参照)——仅前向 paper
  证据积累,非晋级;live 轨道 `_STRATEGIES`/mandate/attestation 一字未动。
- `live/` — **实盘执行栈(已存在,testnet 已联调)**:`live/exchange/base.py`
  `VenueAdapter` Protocol + `live/exchange/retry.py`(`call_with_backoff`)+
  `live/exchange/binance_futures.py` / `bybit_futures.py`(纯 stdlib
  urllib+hmac,无交易所 SDK;构造注入 `http_request/clock/sleep` 可测;
  mainnet base 在默认 SafetyConfig 下构造即 `raise SafetyViolation`;
  未知 base_url raise ValueError)。`live/pipeline.py::submit_live_batch`
  全链:validation attestation → 计划 → pre-trade gate → 过滤器 →
  mandate(AUTO 拒绝/人工复核)→ 账本预记账 → 下单 → 确认 → 审计。
  `live/governance/{mandate,mandate_store,validation_attestation}.py` 人工门。
  **OMS canonical 状态机(2026-07-23 接入)**:`live/execution/oms_state.py` 把
  `quantos/contracts/order.py::OrderState` 状态机接进 `LiveLedger`——venue 状态串
  fail-closed 映射到 canonical(`PENDING_NEW→RISK_APPROVED`、`NEW→SUBMITTED`、
  `PREVIEW_NOT_SUBMITTED→FAILED` 等 9 条,未知串 raise ValueError),账本新增
  `canonical_state` 列(lazy 迁移,兼容存量 testnet 账本),`update_status` 强制
  **可达性**(BFS over ALLOWED_TRANSITIONS,快照语义:允许合并未记录的中间跳,
  禁止 terminal 改写/状态回退),幂等重放语义不变;`ReconciliationStatus`
  (`IN_SYNC`/`RECONCILIATION_REQUIRED`)显式化对账状态(仍 report-only/硬停,
  绝不自动纠正)。**适配器行情读(2026-07-23)**:`BinanceFuturesAdapter` 新增
  `premium_index()`(标记价/指数价/最新资金费率/下次结算时间)与
  `funding_rates()`(历史资金费率,limit 钳 [1,1000])——公开无签名端点,
  fail-soft None,与 `book_ticker` 同法;合约 `PremiumIndexSnapshot`/
  `FundingRateSnapshot` 在 `live/contracts.py`(NaN/Inf 拒)。策略级资金费数据
  此前只有日频离线快照,这是首个执行面实时读。**资金费实时交叉校验
  (2026-07-23)**:`live/execution/funding_check.py` 纯模块(镜像 `price_check.py`)
  + `tools/live_track_advance.py` 接线——对 `_FUNDING_CHECK_STRATEGIES`
  (目前仅 `carry_oi_majors`,资金费信号策略)逐目标币用 venue `premium_index`
  实时读比对日频快照(`venue_rate×3` vs 当日累计费率,abs 0.001 + rel 2.0
  组合带宽,**未校准的过期/垃圾探测器非精确等式**;快照 None/过期/next_funding_time
  荒谬 → fail-closed 冻结该币),冻结名单并集进价格检查的 `frozen_names` 一起进
  `submit_live_batch(frozen=...)`;`PreviewVenue` 加 `premium_index` 透传使
  preview 跑同一检查;无该方法的场地(bybit/mexc)干净跳过。carry 的信号本身
  就是资金费率——没有实时校验等于拿昨天的费率下今天的单,此洞已补。
  **Binance Futures mainnet 代码面 human-ready(2026-07-23,与 MEXC 对称)**:
  `_VENUES["binance_mainnet"]`(MAINNET_BASE,env `BINANCE_MAINNET_API_KEY/SECRET`,
  `mainnet_only: True` 标记——未 arm 即 SystemExit;通路=人工 `--arm-live`
  (armed preview,2026-07-24:PreviewVenue 结构性零提交)或人工 `--submit --arm-live`;
  启动强制 `check_binance_key_permissions`(`/sapi/v1/account/apiRestrictions`,
  提现已关 ∧ IP 白名单才 SAFE,否则 fail-closed 拒绝);`key/live_mandate_binance_mainnet
  .json.template`(占位 expires_at=0 恒过期,绝不 AUTO)+ 只读首验
  `tools/binance_readonly_smoke.py`(4 个 GET,无凭据零网络,落
  `evidence/binance_mainnet_smoke.json`,首单前 `order_response_shape=
  UNVERIFIED_UNTIL_FIRST_FILL`)+ 首单 runbook
  `docs/runbooks/binance-futures-live-runbook.md`(干跑→paper-track→testnet→
  smoke→mandate+attestation→≤$100 首单→对账→规模化)。真凭证/真 mandate/
  attestation 仍只能真人提供;ops API 的 argv 绝不携带 `--submit`(2026-07-24
  治理收紧,真提交只留人工 CLI),`--arm-live` 只出现在 `/preview` 的
  armed-preview 固定 argv(PreviewVenue 结构性无法提交)。
  `tools/live_ops_api.py`(port 8440,X-Ops-Token,**只读运维、绝不直接下单**;
  2026-07-24 新增 `POST /api/v1/liveops/preview` 演练端点——fixed argv 同步跑驱动器
  preview 路径并回传 status JSON 含 `would_submit`,mainnet-only venue 自动
  `--arm-live`,全模块 argv 绝不 `--submit`)
  + `tools/live_track_advance.py::_VENUES` 场地注册表 + `tools/live_data_refresh.py`
  行情刷新流水线。治理边界机器钉死:网络 import 只在 `live/exchange/` 与 `tools/`,
  `services/` 永不 import `live/`。
  **REST gap-fill(2026-07-26,消除 Data Vision 发布滞后死窗)**:`tools/binance_rest_gap_fill.py`
  —— Data Vision 日归档在 UTC 日收盘后 ~10-16h 才发布,死窗内 admission 恒 BLOCK;
  现 perp_mark_prices/oi_metrics 两源在归档 pass 后对「最新已收盘日缺失」的币用 fapi 公共
  REST(markPriceKlines 1d + futures/data 指标族 5m eod 行,端点时间戳语义已对真实数据
  逐字段钉死:OI 族 window-end eod=次日00:00行,taker window-start eod=当日23:55行)补占位。
  规则:**归档 canonical**——占位标 `REST_GAP_FILL`(mark 缓存顶层 `provenance` 段 /
  OI record `source` 字段),每次刷新先回归档重试、发布即覆盖;REST 绝不覆盖归档行、
  绝不入当日未收盘 bar;占位日以专用 dataset id(`*_rest_gap_fill`)进 raw archive,
  lineage backfill 对占位日走 REST 重取+回放校验(同源同严度,不放宽)。
  **W6 动态回撤状态机 + 可解释风险乘数(spec §10,2026-07-24 落地,
  2026-07-25 接真制品)**:
  `live/execution/drawdown_controller.py`(纯核,无 IO)——
  NORMAL/CAUTION/DEFENSIVE/RECOVERY/HALT 状态机(阈值全部 UNCALIBRATED 工程默认:
  5%/10%/20%;RECOVERY 双门 = 最近 3 次连续观测 dd<5% ∧ 最近 3 次无创新低,缺一
  停在 CAUTION;慢速 ramp 0.1→0.5、第 6 次观测才回 NORMAL,绝不瞬时满杠杆;
  数据异常/场地事故→HALT,相关冲击→至少 DEFENSIVE)+ `risk_multiplier =
  min(各分量, 状态天花板)`(NORMAL 1.0/CAUTION 0.5/DEFENSIVE 0.25/RECOVERY ramp/
  HALT 0.0;UNKNOWN 分量解析为保守默认 0.5——缺证据不放满配;min 语义 + [0,1]
  钳制 = 任何模型输出都覆盖不了硬风险上限)。驱动器接线(加法式):
  `drawdown_control_decision` 可测桥(equity/peak 来自
  `record_and_validate_peak_equity`,venue health 来自本轮 book_ticker 探测,
  **相关冲击已接 W3 真制品** `evidence/common_risk_decomposition.json`
  (`correlation_shock_from_artifact` 纯读者:`correlation_shock.in_shock_now`
  + status==OK 才可信,True→至少 DEFENSIVE;缺失/畸形→None 无意见),
  **衰退已接 W1 真制品** `evidence/live_degradation.json`
  (`degradation_component_from_artifact` 按 strategy×venue 取 `reports[]`,
  7d staleness 守门(注入 now)、ratio null(sign-flip/replay≈0)/SMALL_SAMPLE→
  UNKNOWN 绝不把无意义比值翻成数字、可信 DESCRIPTIVE_ONLY ratio 按 UNCALIBRATED
  分档 ≥0.7→1.0 / 0.4–0.7→0.5 / <0.4→0.25;2026-07-24 的猜版 schema 分支已随
  真制品落地删除),vol/liquidity/capacity 无实盘制品→UNKNOWN)→
  `deploy_aum = min(capital_base, 0.95*mandate) × multiplier`(≤1 只缩不放);
  HALT/DEFENSIVE→跳批(管线无 reduce-only 概念,不虚构;闩锁式全局 halt 仍是
  kill-switch,本机不复制)。快照落 `live_drawdown_state{suffix}.json`(独立
  单写者文件,不塞进 risk_state——save_peak_equity 重建载荷会丢外加键),
  status JSON 加法键 `drawdown_control` 全量审计。测试
  `tests/live/test_drawdown_controller.py`(68)。
- `live/exchange/mexc_spot.py` + `tools/mexc_klines_fetch.py` +
  `tools/dry_run_btc_spot_multi.py` — **MEXC 零手续费 BTC 现货对接(代码面 human-ready,
  2026-07-23 Part 1 接线完成)**:MEXC Spot API v3(Binance 兼容,`https://api.mexc.com`,签名=
  HMAC-SHA256(urlencoded query)+ `X-MEXC-APIKEY` 头)现货适配器,遵循与
  Binance/Bybit 相同的构造注入与安全不变量(mainnet 默认构造即 SafetyViolation;
  MEXC 无现货 testnet → 毕业路径 = 干跑 → 小额真实 + 人工 mandate → 规模化,
  见 `docs/MEXC_INTEGRATION.md`)。接线实况:MEXC klines 已是 `tools/live_data_refresh.py`
  第 5 幂等源(落 `crypto_mexc.db` + `mexc_btc_klines.json`);`btc_spot_trend` 干跑主口径
  已切 MEXC PIT(CoinGecko `_cg` 对照)+ paper_track.db 前向持久层;`_VENUES["mexc"]` /
  `_STRATEGIES["btc_spot_trend"]` 已注册进 `tools/live_track_advance.py`(现货现金门 +
  现货口径风控限额 + §10 启动 key 校验);§10 凭证加载器 `tools/mexc_credentials.py` +
  `verify_key_permissions.py` MEXC 分支;`--arm-live` arming(ops API 只在 `/preview`
  的 armed-preview 固定 argv 携带,绝不提交,治理钉死);只读首验 `tools/mexc_readonly_smoke.py`(人工执行,未跑前
  readiness 诚实报 UNVERIFIED_MAINNET_RESPONSE);ops API 现货 dashboard 分支 +
  `docs/runbooks/mexc-live-runbook.md` 首单 checklist。永不实现提现/划转(治理扫描机器禁止)。
- `services/console/main.py`（正式组合根）+ `services/console/api/main.py`（只读 HTTP 路由/展示层）— FastAPI **read-only** API (45+ GET
  endpoints, console 8400 + live_ops 8440), now **mostly REAL** (distinct provenance markers; fail-closed to
  `SYNTHETIC_DETERMINISTIC` only when an artifact is absent, e.g. CI). REAL: `research-runs`/
  `research-run` (run_registry sqlite), `crypto-live` (CoinGecko offline snapshot),
  `fx-research` (ECB/Frankfurter snapshots + tradable gauntlet), `lifecycle-monitor`
  (experiment_registry artifacts + real paper-track `live_ic`/recommendation), `alpha-zoo`
  (12-dim real eval + time/活体征 dims), `backtest-results` (run_registry by role),
  `data-catalog` (real PIT datasets), `safety-primitives` (real KillSwitch/gate), `portfolio`
  (real optimizer/risk-parity), risk-dashboard/validation (real engines), `dry-run` (DryRunStore
  runs), `mexc-market`/`mexc-strategy`/`mexc-readiness` (MEXC PIT 现货快照 + OHLCV bars +
  btc_spot_trend 干跑指标/净值 vs 买入持有基准 + human-ready 就绪检查清单;2026-07-25 前端
  MexcReadiness 页并入 GoLive 的 mexc 场地卡「MEXC 专项检查」展开区——后端端点保留,
  前端 MEXC 两页 + GoLive 消费),
  `architecture`+`system-completeness` (**REAL_REPO_INVENTORY** — live read-only repo
  introspection via `_repo_inventory()`: per-domain module/LOC + test/page/endpoint counts, grounding
  the curated maturity map in hard counts). The residual synthetic surfaces are the genuinely-missing
  governance/process subsystems (incident/committee/RBAC/…), kept HONESTLY LABELED (not faked — faking
  would break the honesty posture); synthetic root `_factor_values`/`_forward_returns` (`math.sin/cos`);
  contract-guarded by `tests/research/test_console_api_contract.py`。
  **Go-Live 上线向导(2026-07-24)**:`tools/go_live_readiness.py`(离线驱动器,无网络——
  聚合 `_VENUES` 逐场地就绪:key 权限证据/mandate 校验(加载/过期/域/仪器/额度)/
  attestation/research readiness/smoke 证据/kill-switch/last-run + 当前信号腿 →
  mandate 缺口引导(缺哪些币/建议额度/最小 deploy 约束),落 `evidence/go_live_readiness.json`)
  + `verify_key_permissions.py --write-evidence`(脱敏权限证据落盘)+ console 只读端点
  `/api/v1/console/go-live-readiness`(provenance `REAL_GO_LIVE_READINESS`,缺证据诚实
  NO_SNAPSHOT)+ 前端页 `GoLive.tsx`(nav「上线向导」,execution 组:场地 checklist 卡/
  信号腿表/mandate 缺口与建议额度(只读可复制 JSON,**前端永不代填 mandate/凭证**)/最终
  两步 CLI 展示——**按键刻意留人,无下单按钮**)。 smoke 修正(2026-07-24):
  `binance_readonly_smoke` account 检查与适配器同法语义——非 USDT 行(USDC 本位/季度合约
  零仓位,真实账户恒有)跳过而非误判 FAIL。**容量安全规则(W7 §11.3,2026-07-24)**:
  guidance 新增 `capacity_safety` 块 + 各有容量带策略的 `capacity_utilization_note`——
  spec「实际部署资金 ≤ 安全容量/3,除非人工治理审批」。safe 取容量带**最保守角**
  (`calibrated_range.low` = 5% 参与率×单日执行,与 binding aum 现存者的最小值;
  UNCALIBRATED_PRINCIPLED 诊断带);缺证据 passes=null+告警、绝不编造容量;违反只大声进
  warnings、**不 clamp**(guidance 是建议)。首跑:deploy $1000 对 crowd safe $3.58M
  =0.028%、carry safe $0.98M =0.10%、top 同 crowd 带,全 PASS;btc_spot_trend 无带不注。
  **crowd_contra 变体族(2026-07-24,预注册 17 试验)**:`tools/crowd_contra_variants.py`
  ——机构级纪律:配方+判定bar 先于结果写入(quantile×cadence×composite 16 变体 +
  baseline 试#0),全试验进 DSR 记账(注册表 2436→2453),过关者**只进前向 paper**
  (`forward_track_advance._ALPHAS` 字典化注册 7 个候选,实盘 `_STRATEGIES`/mandate
  一字未动)。结果:边对构造细节稳健(7/16 过相对 bar:q20/3d/q25_3d/q35_3d/14d/
  q20_14d/crowd_taker),3d 样本内 VT 1.55–1.66 优于 7d 基线 1.10,**但 DSR 通缩
  后 3d「改进」≈0.001(17 个相关试验的多重比较税)**——3d 是否真加分交给新注册的
  前向钟裁决。证据 `evidence/crowd_contra_variants.json`。
  **Alpha 生命周期驾驶舱(2026-07-24)**:`tools/alpha_lifecycle_cockpit.py` →
  `evidence/alpha_lifecycle_cockpit.json`(20 alphas × 10 阶段证据聚合:发掘 IC/
  证伪/压力/回测/容量/组合/纸面/实盘/衰弱/退役,全 fail-soft ABSENT 诚实标,派生
  阶段梯 CANDIDATE<VALIDATED<DRY_RUN<PAPER_TRACKED<LIVE_TRACK 显式入 legend;
  `lifecycle_state` 恒 null——无任何落盘 SM 状态,不编造)+ console 端点
  `/api/v1/console/alpha-lifecycle`(`REAL_ALPHA_LIFECYCLE`)+ 前端页
  `AlphaLifecycle.tsx`(nav「生命周期驾驶舱」,alpha 组:4 焦点策略全宽卡 + 研究
  候选紧凑表 + 前向证据缺口琥珀标)。首跑实况:crowd_contra=LIVE_TRACK(10/10)、
  carry=PAPER_TRACKED、top/btc_spot=DRY_RUN;三条 OI alpha 前向证据全部
  BLOCKED(0–1/8 期)——前向积累是当前真瓶颈。
  **W2 均值回复簇(2026-07-25,预注册 8 变体)**:日频残差/截面反转/beta 中和/vol 归一
  全系(`tools/mean_reversion_cluster.py`)——**8/8 REJECTED**(失败 bar 一致:证伪+VT
  Sharpe+回撤;试验记账 2459→2467)。结论:日频均值回复不是可用第 5 簇(微观结构 MR
  无本地数据,诚实出界)。证据 `evidence/mean_reversion_cluster.json`。
  **W2 跨所簇(2026-07-25,Phase 0 深度实测 + 预注册 6 变体)**:Binance × Bybit 两所
  (MEXC 无 perp funding 公共史、OKX 无适配器,预注册声明出界)。`tools/bybit_klines_fetch.py`
  (Bybit V5 公共 klines+funding,无 key 只读,治理 `_FETCHERS` 钉死)→ `data/cache/bybit_perp.db`
  (24 OI 大币 44,060 日 bar,最浅 APT 2022-10-19;重跑 0 新行幂等已验证)+
  `bybit_funding_rates.json`(日求和,与 binance_funding_fetch 同口径;BTC 2020-03-25 起)。
  `tools/cross_venue_research.py` → `evidence/cross_venue_research.json`:6 预注册假设
  (basis 20d z 正反两方向/delta/水平/60d 窗 + 资金费价差 contrarian,双所日网格对齐覆盖率
  86.5%)过同一台四阶段机器(IC/5 项证伪/容量带/干跑)。**6/6 REJECTED**(失败 bar 一致:
  t-stat 全部 |t|<1.3、证伪 ≤2/5、VT<0.8;最强 xv_funding_spread_rev VT 0.80/回撤 0.18
  但 t=0.74、1/5 证伪;DSR 全 0;试验记账 2482→2488)。结论:日频跨所偏离不是可用第 6 簇
  (intraday 同步盘口偏离无本地历史,诚实出界);执行现实诚实块记录在案——本筛把信号模拟在
  Binance 定价 book 上是 first-pass,两腿跨所执行(转账/腿风险/限频)结构性未覆盖。
  **W4 退出政策实验室(2026-07-25)**:`services/research/exit_policies.py` + `tools/
  exit_policy_lab.py`——8 政策 × 2 book 全标准矩阵(ΔSharpe/Δmdd/PF/右尾/集中度)。
  **诚实结论:signal-only 退出已最优**——7 政策全灭(crowd 基线 Sharpe 1.27;scale_out
  砍的正是盈利尾部;btc 唯一"有效"项增量集中 1 期被集中度规则硬否,正是为防净 PnL
  单指标骗人设计)。无前视双保险(访问器机器过滤 + 追加未来 bar 性质测试)。
  **W3 共同风险拆解(2026-07-25)**:`services/research/factor_decomp.py` +
  `tools/common_risk_research.py`——10 因子库(5 项 MISSING 诚实标:清算/skew 史等)。
  **四策略无隐藏共同暴露(残差 |ρ|≤0.15,全单簇,effective breadth 3.78/4,分散为真)**;
  carry 边=截面价差(全市场资金费 β=−0.36 负载,非伪装 beta);相关冲击信号产出
  (当前 0.144 vs 阈值 0.320,平静)→ 已接线进 W6 控制器(2026-07-25,
  `correlation_shock_from_artifact`,见 W6 条)。
  **§6.2 晋升门槛三闸(W2 slice 2,2026-07-25)**:`services/research/promotion_checks.py`
  ——平台缺失的三项晋升检查补为**可复用纯组件**:`cost_stress_report`(各成本档夏普
  衰减 + 弹性 ΔSharpe/bps + 线性外推 breakeven,edge 不必每档为正但弹性必须摆出)、
  `perturbation_report`(参数网格上部署点不得是孤立尖峰 SPIKE_OPTIMUM、也不得低于
  邻域中位数;PASS/WARN/FAIL + 完整表面)、`concentration_report`(剔最佳标的/最佳
  月份/极端单期后 edge 须仍正;三维取最差);缺数据恒 None 不假通过,阈值全部
  UNCALIBRATED 工程默认随证据落盘。`attribute_period_contributions` 逐标的美元
  归因与 `run_multi_period_dry_run` 同语义(整手/决策价成交/平 bps/资金费;标记缺失
  回退最后成交价镜像 reconcile)——**恒等式实测残差 $0.000000**(Σ归因 ≡ 管线权益
  增量 $1,215,255.85)。驱动器 `tools/promotion_gate_crowd_contra.py` →
  `evidence/promotion_gate_crowd_contra.json`:15 点 quantile{0.20–0.40}×cadence
  {3,7,14} 网格全注册(register-then-evaluate,registry 2467→2482,Δ15;12/15 与
  variants 族重叠属保守重复计数)、成本 {10,15,20}bps、集中度走部署点基档簿;
  机器互证:与 variants 族 12 重叠点 sharpe_vt 差全 0.0、与 dry_run.db 正典存档
  Δsharpe −0.0002(236 vs 235 周数据微演进)。**首跑 crowd_contra 成绩单:成本
  PASS(VT 1.095→1.072→1.048 @10/15/20bps 单调,弹性 0.00466/bps,breakeven
  ~245bps)、集中度 PASS(24 标的 top1 SOL 26.8%/top3 64.2%,剔 top3 残差
  +$434.9k;最佳周 16.8%/top3 周 37.6%;最佳月 2023-12 18.0%/top3 月 48.3%,
  剔除后全仍正)、扰动 FAIL(center_rank 0.357:部署点 q30/7d VT 1.095 < 邻域
  中位 1.277——3d 行 1.31–1.75 及 q20_7d 1.55/q40_7d 1.36 样本内更优;spike_gap
  为负=非参数孤岛,边对构造稳健,但部署点非局部最优。该「邻域更优」正是
  variants 族预注册发现里 DSR 泄气至 ~0.001 的同一现象——7 候选已在前向
  paper-track 裁决,FAIL 是「参数选择待前向复核」的诚实标记,非立即换参依据)→
  overall FAIL(取最差)。** 判定只落证据文件,不驱动任何自动晋级/下单;
  `_STRATEGIES`/mandate/attestation/live 一字未动。测试
  `tests/research/test_promotion_checks.py`(31)+
  `tests/tools/test_promotion_gate_crowd_contra.py`(12)。
  **W8 top_contra 隔离(2026-07-25)**:经非人维护机器权威通道 **PAUSED**(730 天
  真实执行 paper 回撤 30.8% 越 0.30 阈),live 路径双重独立封锁(maintenance_skip +
  research readiness)。规格书 §16.3 明令完成。
  **top_contra 隔离(W8 §16.3/§12,2026-07-24)**:spec「top_contra 应立即 QUARANTINED」
  经既有维护机器落地——canonical `AlphaState` **无 QUARANTINED 枚举**,其 §12 语义
  (异常/证据不足,只减仓)映射 `PAUSED`(reversible halt:停止新增敞口、绝不自动减仓、
  可逆待诊断;RETIRED 是锁定 OOS 仍无效后的独立治理决策,本轮不做)。`tools/
  alpha_maintenance_sweep.py` 新增 `top_contra_quarantine_health` 证据通道:现实执行
  连续 paper(`binance_usdt_continuous_paper.json`,730 天 PIT+taker 费+冲击)已实现
  **max_drawdown 0.308 ≥ monitor 既有 0.30 阈值** → 经审计 manager(非人 actor,
  AGENT_REACHABLE de-risking)PAPER_TRADING→PAUSED 落 alpha_maintenance.db 权威 +
  事件 outbox;证据引用(证伪 4/5 broad_across_years=false、前向 BLOCKED 0/8、连续
  paper 统计)原样落快照 `quarantine_evidence` 块。live 驱动器
  `maintenance_skip("top_contra")` 现返回 `maintenance_paused:*`(与既有
  `alpha_paper_blocked` 双保险,--submit 跳批,仓位不动);cockpit decay 阶段自动呈现 +
  `build_warnings` 新增 maintenance 降级告警。隔离本身**无需人步**;剩余人步 = §16.3
  诊断(信号方向/数据对齐/选择偏差、分市场分制度)、解除隔离(重新过人类晋级门)或
  正式 RETIRED 的治理签署;「不得直接上线反向版本」无任何 wiring(本slice未建反向策略)。
  **全 alpha 真实执行 paper 同口径丈量(2026-07-25)**:`tools/binance_usdc_alpha_retest.py`
  策略注册与前向 paper 轨**共享同一注册表**(`forward_track_advance._ALPHAS` 经同一
  `_alpha_spec` 归一,expr/quantile/cadence 零漂移,等价性测试钉死)——3 originals +
  equal_weight_combo + 7 个预注册 crowd_contra 变体 CANDIDATE 全部过同一台 730 天现实执行
  机器(PIT mark 价 + 4bps taker + 当前价差校准 + Almgren-Chriss 冲击 + 资金费 +
  整手/最小名义过滤 + 对账恒等式 ≤1.3e-08);`_cadence_rows` 承载 3d/14d 节奏(7d 与旧
  周度网格逐位一致;originals + combo 权重逐位不变——combo 仍只混 3 originals)。
  窗口 2024-07-25→2026-07-23(729 完整日;网格随窗口重锚,与 2026-07-24 运行逐决策不可
  直接比)。结论(Sharpe 序):equal_weight_combo 1.36 > crowd_contra_14d 1.19 >
  crowd_contra 1.12 > q20_14d 0.86 > carry 0.78 > q20 0.73 > top_contra 0.68 >
  crowd_taker_combo 0.64 > q35_3d 0.64 > 3d 0.52 = q25_3d 0.52。**3d 变体在现实执行下
  成本翻倍(~$60k vs 基线 ~$32k)、净 Sharpe 垫底——DSR 深度怀疑(~0.001)在执行维度
  独立兑现**;14d 变体(1.19/回撤 11.7%)略优于基线,与 DSR 0.38 的温和怀疑并存,交
  前向钟裁决。q25_3d ≡ 3d(14 币宇宙 k=round(N·q) 同为 4 → 逐位相同组合,诚实标
  `identical_portfolio_as`,非独立证据)。**窗口敏感诚实注:top_contra 本窗回撤
  0.207 < 0.30 跳闸线(2026-07-24 窗 0.308 越线促成 PAUSED——权威快照不动,下轮
  sweep 读到的将是越线证据已消失)。** 台账
  `binance_usdt_continuous_paper_24m.db` 1881 行(418 legacy + 1463 本窗;重放 0 插入
  幂等端到端已证)。汇总 `tools/all_alpha_realistic_paper.py` →
  `evidence/all_alpha_realistic_paper.json`(11 策略标准指标 + 判定 bar = 对账/完整日
  机械完整性 + 0.30 回撤跳闸线 + 净成本边为正;btc_spot_trend 走 MEXC 独立路径只引用
  dry_run.db 不硬塞 Binance 引擎;均值回复 8/8 REJECTED 与 vol 类无候选按纪律只记录)。
  诊断非门禁,不驱动任何晋级/下单。
  **W10 研究预算契约(§14.1/§14.2,2026-07-25)**:`services/research/research_budget.py`
  (纯核,无 IO/RNG/墙钟)——`ResearchBudget` 冻结 dataclass(max_trials/max_compute_hours/
  max_feature_count/max_parameter_dimensions/locked_oos_period/allowed_data_sources/
  stopping_rules,**全部可空 = 不约束**)+ `check_budget`(逐维 OK/UNCONSTRAINED/
  EXCEEDED/UNVERIFIED,**两个 None 严格区分:limit=None 放行、usage=None 按
  `unverified_dimension` fail-closed 拒绝**,全维度不短路)+ `evaluate_stopping_rules`
  (max_trials/no_improvement_trials,越高越好指标)+ 类型化 `BudgetExhausted`。
  注册点加法集成:`run_registered_candidate_batch` 新增可选
  `budget=/parent_hypothesis=/search_space=/data_source=/uses_locked_oos=`——声明预算
  则**任何落盘前**先校验、耗尽即 `BudgetExhausted`(一字节不进台账),hypothesis +
  search_space 强制非空;不声明则行为逐字节不变(2482-trial 注册表与全部既有驱动器
  零改动通过)。usage 口径:trials=本批声明族大小(族扩员超声明 = 新实验须抬预算;
  跨批累计记账需持久消耗台账,本 slice 不做,诚实标注)、feature/参数维=批内候选
  最大基数、**compute 不可计量 → 声明 compute 预算必被 unverified 拒(只许声明
  可验证维度)**。预算声明+逐维校验结果作 additive 字段记入 run artifact
  `budget_declaration`(随驱动器证据落盘)。模板采用者:`tools/mean_reversion_cluster.py`
  (RESEARCH_BUDGET max_trials=8 钉死真实族 + PARENT_HYPOTHESIS/SEARCH_SPACE;
  **诚实注记:本族 8 trial 已在预算前制度下消耗(2026-07-24,2459→2467),预算是
  前向约束不回溯**)。§14.1 其余结构性禁令(智能体不得改 locked OOS/晋级/删实验)
  由既有治理承载,本轮验证不重建。测试 `tests/research/test_research_budget.py`(65)+
  `tests/intelligence/test_registered_candidate_batch_budget.py`(17)+
  `tests/tools/test_mean_reversion_cluster_budget.py`(3);诊断非门禁,不驱动晋级/下单。
  **Live-vs-Replay 衰减引擎(W1,2026-07-24)**:`live/execution/degradation.py`(纯核:
  DegradationReport 每 venue×strategy×窗口——live_net/replay_net/degradation_ratio
  (replay≈0[含 0.1%·aum 地板]/异号/任一侧 UNKNOWN → None+reason,绝不硬算)、
  成分分解(fee/funding 实际值账本不记录恒 UNKNOWN、TCA 滑点、position/timing 残差,
  恒等式 residual≈0 钉死)、逐日瀑布)+ `tools/live_degradation.py`(只读驱动器,
  全 sqlite `?mode=ro`)→ `evidence/live_degradation.json` + console 端点
  `/api/v1/console/live-degradation`(`REAL_LIVE_DEGRADATION`,缺证据诚实 NO_SNAPSHOT,
  契约测试锁定)。**Replay 构造(诚实选择)**:权重只取记录在案的 contemporaneous
  artifacts(前向台账 forward_decisions 为纸面权威;其缺失的 decided_ts 用
  live_parity*.jsonl submit 记录,标 `LIVE_PARITY_RECORD`)——绝不拿今天数据重算信号;
  经济学 = 干跑流水线同款原语(plan_target_quantities+flat 10bps+reconcile+资金费公式,
  与 run_multi_period_dry_run 曲线等价性测试钉死),决策日后的价格只做 MTM。
  **首跑实况(testnet 20 天)**:binance:crowd_contra live +$103.5 vs replay −$35.0
  → 比值诚实 None(异号——live 从未执行 07-16/07-21 两期纸面调仓,碰巧躲过了纸面
  新仓的亏损;最大"漏损"成分是 position/timing +$112.6,执行滑点 −33.9bps 加权
  = 相对决策价的价格改善,testnet 无冲击);bybit_demo 分母过小 → None(0.1%·aum
  地板)。诚实边界:testnet 无真实冲击,度量的是运营/模型漏损非生产冲击。
  **投资者证据层(W9 §13,2026-07-25)**:`live/execution/investor_snapshot.py`(纯核:
  日频 NAV[每 UTC 日取最后采样]+现金流调整(外部出入金=非交易资金划转,
  `nav_adjusted=raw−cum(flows)`;**账本不记录 venue 出入金 → flows 恒 None、
  basis=RAW_FLOWS_UNRECORDED,绝不把无记录当零**)、gross/net/fee/funding/slippage
  逐策略分解(fee/funding actual 恒 UNKNOWN)、live/paper/replay 三线(**paper 线 W1
  artifact 不承载 → ABSENT,绝不拿 replay 冒充**)、maxDD/恢复天数/最差日周、
  账户级对账块、kill-switch 事故链(权威 SQLite 优先/legacy JSON 镜像/不可读
  fail-closed tripped,provenance 如实标)、版本清单(无 artifact 钉 strategy_version/
  code_hash → 恒 UNKNOWN)、容量摘要;`compute_snapshot_hash`=SHA512(canonical
  {data_cutoff, ledger_root, strategy_versions},sort_keys+版本排序使键序无关,
  UNKNOWN 版本以显式 null 入哈希;**now_ms 绝不进哈希,同一数据截止点重跑同哈希**——
  实测两次重跑逐位相同)+ `tools/investor_snapshot.py`(只读驱动器,全 sqlite
  `?mode=ro`)→ `evidence/investor_snapshot.json` + console 端点
  `/api/v1/console/investor-snapshot`(`REAL_INVESTOR_SNAPSHOT`,缺证据诚实
  NO_SNAPSHOT,契约测试锁定)。**首跑实况**:5 venue 中 binance(testnet)/
  bybit_demo ACTIVE、binance_mainnet/bybit/mexc ABSENT_NO_ACTIVITY(诚实);
  binance NAV $5,135.97(10 天,2026-07-05→07-14,+3.74%,maxDD 0.33%,恢复 1 天,
  对账 in_sync)、bybit_demo NAV $169,721.93(9 天,maxDD 1.455%);crowd_contra
  live_net binance +$103.49/bybit_demo +$121.15 vs replay −$34.97/+$2.16(比值
  None,同 W1 结论);kill_switch_tripped_any 三值(None=有 venue 无记录,绝不报全清);
  §13.3 双组合视图(Showcase/Growth)单轨道未实例化 → 双 ABSENT。测试
  `tests/live/test_investor_snapshot.py`(17)+ `tests/tools/test_investor_snapshot.py`(6)。
- `apps/research-console/src/` — React 18 + Vite 6 + TS (strict) + Tailwind. **2026-07-03
  信息架构重构:42 页 → 30 页 · 7 个工作流导航组(总览→数据→因子研究→验证→组合与风险→执行与跟踪→
  治理与系统)**。路由/导航/冒烟测试的唯一真源 = `src/app/routes.tsx` 页面注册表(新页加一行,
  自动进导航 + 自动被冒烟覆盖);删掉的 12 页 = 纯合成蓝图(incident/reviewer/access/committee/
  promotion/model-risk)+ 合成或重复(Agents/Phase3A/ScenarioStress/BacktestExplainability/
  SignalToPortfolio/Overview),旧路由语义就近重定向(蓝图页 → SystemCompleteness,那里
  REAL_REPO_INVENTORY 诚实列出缺失子系统——删页不删诚实);后端 console_api 端点零改动
  (契约测试锁定)。测试地板:**全量注册页冒烟 + 注册表一致性测试**(125 fe tests)。
  **2026-07-25 第三轮 IA 整合**:MexcReadiness 并入 GoLive(唯一真重复;5 项独有检查 +
  四阶段毕业路径迁入 mexc 场地卡「MEXC 专项检查」展开区,`/research/mexc-readiness` →
  `/research/go-live` 重定向,后端端点保留);LifecycleMonitor(实验登记 SM + paper-track)
  从「执行」挪入「验证」组;执行组按就绪→上线→跟踪流程重排;15 个退役页死 fetcher +
  其独占类型清除。
  **机构工作台改版(已落地 2026-07-23)**:设计原则与组件清单见
  [`docs/FRONTEND_WORKBENCH_REDESIGN.md`](docs/FRONTEND_WORKBENCH_REDESIGN.md)。共享组件层
  `src/ui/workbench.tsx`(EquityCurveChart 多序列净值/StatusBanner 常驻 kill-switch 条/AsOfStamp/
  EndpointFootnote/StateView/ConfirmActionButton/MandateGateChip/MarketTape 顶栏滚动带),
  DataTable 粘性表头 + compact 密度,MEXC 交易台两页(市场 CandleChart/策略净值 vs 买入持有基准;
  就绪检查清单 2026-07-25 并入 GoLive mexc 场地卡),LiveDesk 现货分支。

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
