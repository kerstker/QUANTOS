# QUANTOS 项目状态 / 上下文交接(活文档)

> **用途**:换新上下文/新会话时,**只读这一个文件就能接着干**。每当上下文快满时刷新它。
> 约定:本文中文叙述;代码标识符英文。最近更新锚点:见末尾"提交锚点"。

---

## 0. 一句话 + 安全边界(不可让)
QUANTOS = 安全关键的 **AI 量化研究平台**,就绪等级 **L2_DRY_RUN_ONLY**(只研究 + 干跑模拟,
**无实盘、无券商、无真凭证**)。权威规格:`QUANTOS_MASTER_BLUEPRINT.md`;开发计划:
`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`;因子方法论地图:`docs/factor-methodology-implementation-map.md`。
**当前活路线(2026-07-03 起,机构级补全):`docs/2026-07-03-institutional-completion-plan.md`**
(7 路专项复核 + Rank #8–28 详细开发计划,承接同日核实版审计 Rank #7–19;#7/#9 已落地)。

**机器强制的不变量**(改动绝不能破):
- `LIVE_TRADING_ENABLED=false / REAL_BROKER_ENABLED=false / ALLOW_ORDER_SUBMISSION=false /
  ALLOW_AI_DIRECT_TRADING=false / REQUIRE_HUMAN_APPROVAL=true`。
- **纯 Python 标准库**(禁 numpy/scipy/torch/pandas/ccxt 等)、**确定性**(无 RNG、无 wall-clock;
  前端新代码无 Date.now/Math.random)、**失败关闭**、**离线门禁**、**可复现**。
- **容器化(神圣)**:非人类 AGENT actor **永不可达 APPROVED**;一切经未改动的 `ResearchAgentLoop`
  (PROPOSE/EVALUATE),`guardrails.assert_action_allowed`。
- 网络**只在门禁外** `tools/`;`services/` 整棵树**零网络 import**(机器强制)。
- 不提交密钥;密钥读自 `$COINGECKO_API_KEY` 或 gitignore 的 `key/coinkey.txt`,绝不打印/提交。

## 1. 单一门禁(改完必跑,绿了才提交)
```bash
python scripts/validate.py     # AST 治理扫描 + 全 pytest 套件(contracts/governance/research/trading)
```
隔离 pytest 需 `PYTHONPATH=/h/code/QUANTOS`(从仓库根跑)。`main==master` 手动同步:
`git branch -f main master`。提交信息中文;结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## 2. 工作节奏(已确立)
brainstorm(大改才需) → 先写测试(tests-first)→ 实现 → `validate.py` 绿 → 提交 → 必要时对抗式评审
(Agent/Workflow)→ 修 P0/P1 → 再提交。**最小安全改动;绝不藏/删失败测试。** 用户偏好:
聊天用**简单中文、少术语、术语用大白话解释**(见记忆 `reply-plain-chinese`);文档/注释也中文。

## 3. 已建成(本仓真实代码,按能力)
**数据**:`services/data/` —— PIT 存储 `persistent_store`、**`caching_store.py`(内存读缓存:回测从
小时级降到秒级,与 SQLite 逐位一致,接进所有驱动)**、survivorship-free `universe_store`、`versioning`、
质量/归一、`market_csv`、`market_provider`(多资产离线接缝 + ingest)、`adapters.py`(声明式 SourceSpec +
数据源注册表)、`calendar_store`、`fundamental_store`。
**加密数据(门外只读)**:`tools/coingecko_fetch`(OHLC 4h)、`tools/coingecko_volume_fetch`(真实成交量)、
**`tools/cryptocompare_fetch`(中立聚合商 histoday,多年日线·含 2022 熊市,免费 key 读自 key/cryptocompare*.txt)**;
治理边界 `tests/governance/test_crypto_data_boundary.py` 钉死**三**抓取器只含 stdlib+services/tools、无 SDK/无下单。
**信号/特征**:`signals.py`(8 价格算子)、`volume_signals.py`(4 量能算子)、**`alpha_expression.py`(截面 alpha 引擎:
PIT 面板上组合时序算子 + 截面算子 rank/scale/demean/zscore + 逐元,解锁 Alpha101 那类真因子;无前视测试钉死)**、leakage 检测。
**因子/评估**:`factor_engine`(IC/ICIR)、`alpha/evaluator`(DSR/PBO/regime 门、失败关闭、
**opt-in `compute_regime_evidence`**)、`catalog`(试验计数/var_sr)、`config`、`multiple_testing`、
`orthogonalize`、`panel_backtest`、`capacity`、`walk_forward`(**带 embargo 禁运**)。
**因子发现**:`gp_tree.py`+`gp_genome.py`(**GP 表达式树**)、`evolutionary_search.py`
(flat P/F/X + GP 'T';opt-in 奖励:`parsimony_lambda/mu`、`redundancy_lambda`(增量去冗余)、
`diversity_lambda`(**频繁子树规避**))、**`mcts_search.py`(MCTS/UCT 导航)**、`guided_search`(UCB)、
`search_space`、`interaction_factors`、`fundamental_factors`。
**主线编排**:`research/session.py::run_research_session` —— 模式 `evolutionary/gp/mcts`(+guided);
搜后 `synergistic_set`(去冗余协同集);**`oos_validate=True` 自验证(对自己挖的因子跑无窥视 OOS)**;
`regime_evidence`。
**验证/诚实**:`oos_validation.py`(**无窥视滚动选择 OOS**)、`validation/`(deflated_sharpe/walk_forward/
gate/pipeline)、`labeling.py`(三重门+元标签)、`regime.py`(波动率状态分档)、`information.py`(互信息)、
`factor_set.py`(协同集 + novelty)、`subtree_stats.py`、`sample_weights.py`、`factor_timing.py`、`frac_diff.py`。
**容器化/生命周期**:`agent_loop`(ResearchAgentLoop)、`lifecycle` 状态机、`guardrails`、Actor。
**组合/风险**:`portfolio`(risk_model/construction/attribution)、`research_risk` 门。
**通往实盘的安全脚手架(轨道3,2026-07-01)**:`services/trading/broker_adapter.py` —— 把「换适配器」形式化成
`BrokerAdapter` Protocol + `DryRunBrokerAdapter`(透明超集包装现有引擎、零改动、流水线 drop-in)+ `LiveBrokerAdapter`
(结构性拒绝:L2 下 `live_trading_armed` 恒 False → 构造即 raise,绝不 import 券商 SDK)+ `assert_dry_run_adapter`
fail-closed 守卫接进 `simulate_fills` 首行。治理 `test_broker_adapter_boundary.py` 钉死无 SDK/网络 + L2 下 live 不可
实例化。子项目2 `services/governance/approval.py` —— 多方人工批准状态机:不可变 `ApprovalRequest` + 纯转换
(`open_request/review/approve/reject/consume`,每步哈希链),硬不变量 `REQUESTED→REVIEWED→APPROVED→CONSUMED` +
提出/复核/批准三个两两不同真人 + 一次性绑 subject;`verify_multi_party_grant` fail-closed;接进 `KillSwitch.
reset_with_grant` 与 `evaluate_deployment(grant=)`(只做加法、旧路径零回归、LIVE 仍挡死)。治理钉死无 SDK/网络 +
非人到不了 APPROVED。子项目3 `services/governance/signing.py` + `docs/PHASE_14_PROCESS_ISOLATION.md` —— 出进程签名
权威接缝(收口):`SignedDecisionRef` + `NullSigningAuthority`(进程内无密钥、`sign()` 即拒)+ `verify_signed_ref`
fail-closed(无出进程权威恒 False,伪造权威也 False);威胁面文档列清所有「受信代码可绕过」面 × 出进程签名怎么堵 +
post-L2 迁移路径。诚实决定:**不接进 L2 执行路径**(强接要么死代码要么堵死干跑)。治理钉死无密钥材料 + 无 SDK/HSM
import。**轨道3 = 3 子项目全 landed:接缝(✓)、多步人工批准状态机(✓)、Phase 14 进程隔离设计(✓)。** 全程 L2 一寸不让。
**执行/干跑(端到端能跑的系统,2026-06-30)**:`risk/pre_trade`(23 检查电池 + latched `KillSwitch`,
**运行时 chokepoint**)、`execution/{intent_builder,dry_run_pipeline,dry_run_store}` —— `run_dry_run_pipeline`
把权重→目标手数→意图→**逐意图过门(fail-closed,latched→全拒)**→干跑适配器纸面成交(**绝不真发**)→
平均成本对账(恒等式残差≈0)→哈希链审计→可复现 `DryRunRun` 落 `data/cache/dry_run.db`。驱动器
`tools/dry_run_carry.py`(carry 最新腿 × vol-target 杠杆端到端;首跑流动 58 币→23 名组合,门批准 23/拒 0、
纸面成交 23、成本 9.9bps×换手=约 −$956、残差 0.0)。`trading/dry_run_adapters` 无券商 SDK。把预交易门从
"已测死代码"变成"运行时可证强制"。**`run_multi_period_dry_run` + `tools/dry_run_carry_multi.py` 升级为周度滚动**:
逐周增量调仓单过门(fail-closed)→纸面成交→`reconcile` 价格 MTM + 资金费现金流(carry 真边)→raw/vol-target
两条净值曲线→多期 `DryRunRun.detail.equity_curve`(首跑:流动 58 币×313 周,门批准 15908/拒 67,总收益 126.7%、
夏普 0.45、回撤 77%、累计资金费 $166k、残差 1e-9)。**已上 console**:`/api/v1/console/dry-run` + 干跑页
`DryRunPipeline.tsx`(单期 + 多期 SVG 净值曲线,nav「干跑流水线」,浏览器验证过)。诚实:周度滚动 carry 多数收益是
L/S 价差(非 beta 中性、回撤大),funding 较小稳定项——执行现实把理想化 carry 的尾巴暴露出来。**美元中性实验**(每期记
`net`/`avg_net_exposure_pct`,console 出 KPI):证伪"回撤=舍入净敞口"假设——$1M 时净敞口已仅 ~1.8% 且 77% 回撤
$1M↔$100M 规模不变,故回撤是 **L/S 截面价差本身**、非净市场敞口/舍入;funding(~16.6% 累计)是骑在波动价差上的
稳定边、无法干净隔离。**实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。**
**持久化**:`experiment_registry`(stdlib sqlite)、**`paper_trading/{store,advance,health}.py` 前向 paper 追踪库
(append-only `data/cache/paper_track.db`,真 train/paper 切分,驱动器 `tools/crypto_paper_track.py`;喂 LifecycleMonitor
建议 / 衰退维护 / Alpha Zoo 时间维。`live_ic` 仍 null——离线源无截面 IC 面板;纸面夏普在 ~24 期尾巴是噪声非 edge)**。
**前端**:`apps/research-console`(React18+Vite6+TS+Tailwind,premium fintech;26 页;**Crypto Live 页**
显真实加密因子 + OOS 裁决 + 协同集面板)。**Console API**:`research/console_api/main.py`(只读快照 +
`/api/v1/console/crypto-live` 真实数据端点,provenance=REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT)。
**门外驱动(tools/)**:`crypto_research_run`、`crypto_gp_run`、`crypto_oos_validation`、`crypto_volume_research`。

## 4. 当前实证结论(诚实)
- **候选第二边 `crowd_contra`(持仓反向,2026-07-01 用截面 IC 面板重新确认)**:反着做拥挤的散户多空
  仓位(14 大币 OI 数据),mean_ic=0.035、**t-stat=4.32(统计显著)**、过 4/5 项证伪检验(滞后1天/10bps
  成本/子集/对 carry 增量存活,唯一没过的是"跨年广泛性"——2020-21 接近零、2022 年后才显著)。此前研究
  自己的结论就是"proceed to paper-track"。**2026-07-01 已把它送进干跑流水线**(`tools/dry_run_crowd_contra_multi.py`,
  第三条策略过策略无关的 `run_multi_period_dry_run`):14 大币 × 232 周 × 10bps × 含资金费——**总收益 +185.6% ·
  夏普(周→年)1.21 · 最大回撤仅 12% · 门批准 3066/拒绝 0 · 对账残差 7e-09**,三条里执行现实表现最好(夏普远高于
  carry 0.45/trend 0.40,回撤远低于 carry 77%/trend 37%)。**诚实边界:这是对已知历史的执行现实回测(整手+门+
  成本+换手,恒等式成立),非前向/实盘;宇宙仅 14 大币;证伪已过≠前向必赚。** 走干跑而非前向 paper-track——缓存上
  "前向 paper-track"只是留尾回放(已被 no-peek OOS 折叠覆盖);是否送前向 paper-track/加仓位仍是独立的下一步。
- **现实主义引擎 RE-1 首跑抓到自欺(2026-07-01,`services/research/realism/`)**:Almgren-Chriss 冲击 + 容量衰减带
  应用到 carry/crowd_contra。**关键发现:参与率上限(10% ADV/期)是绑定约束——真容量 carry ~$2M / crowd_contra ~$7M**;
  只看冲击会误报 $743M–$4.1B / $1.69B–$9.1B(**~300× 高估**)。这正是差距报告警告的「松现实主义 × 关卡抓海市蜃楼 =
  无 edge 信号在组合层看起来可部署」——引擎首跑即抓到,且指出**可部署性受限于*构造*(最小 ADV 名满权重),非仅信号**。
  诚实标 `UNCALIBRATED_PRINCIPLED`(冲击系数未校准 → 区间带)。
- **现实主义引擎 RE-2 情景重演 + 尾部(2026-07-01,`services/research/realism/{scenario,tail_risk}.py`)**:把 carry/crowd
  拉过真实危机(2021-05/LUNA/FTX/2023-03 银行)+ 尾部量化。**诚实发现:① 美元中性预判证实——崩盘 beta 极低(下尾依赖
  carry 0.13 / crowd 0.0),crowd_contra 在 LUNA(+10.6%)/FTX(+6.5%)崩盘里反赚——尾部风险是*截面价差击穿*非市场 beta;
  ② 真尾部内生(carry 最差 19% 在 2020-11 牛市,非崩盘;干跑 77% 是 vol-target 加杠杆口径);③ 尾偏肥 Hill ~2.4–2.7;
  ④ 逮到 `crypto_daily.db` volume 单位跨币不一致 bug(EOS $20k vs BTC $3.7Q,诚实标 UNRELIABLE、已 spawn 修复)。** RE-2
  剩 Copula 全拟合 + trend 容量(待 daily volume 修复)。
- **FRED 宏观数据方向第一增量(2026-07-01,`tools/fred_fetch.py` + `services/research/macro_regime.py`)**:股票免费源本环境被墙
  (Stooq bot-wall / Yahoo 429,不绕过)→ 转 FRED(可达)。抓 6 条市场型日频序列(VIX/DGS10/T10Y2Y/HY-OAS/DXY/DFII10,39271 行)
  填进已建但从未填的 `fundamental_store`(PIT kt=et)。**关键发现:carry 与 crowd_contra 宏观 regime 画像相反、互补**——carry
  爱平静/risk-on(低VIX SR+2.36 vs 高VIX+0.65,正常曲线+2.08 vs 倒挂+0.67),crowd_contra 爱压力/risk-off(高VIX+1.96,倒挂+1.76)。
  crowd 正好在 carry 走弱处走强 = 强分散故事,系统化了 RE-2 的 LUNA/FTX 危机发现。诚实:regime=全样本分位(回顾性非PIT择时)、
  gross 无成本、HY-OAS 覆盖 155/期;股票 + 基本面 + 经济发布类(需 BYO-CSV/ALFRED)= 待办。
- **组合分散验证兑现(2026-07-01,`services/research/book_combine.py`)**:regime 互补 → 组合 carry+crowd 收益流。
  **corr=-0.04(近乎不相关)→ 组合夏普 1.95 > 单条 1.44/1.30,最大回撤 8% = 单条一半,总收益~187% 保住,VIX regime 被抹平。**
  两条正交 crypto 边合成 Sharpe~2/回撤 8% 的强分散。诚实:收益流层面(不同宇宙/规模、231 共同周、gross 无成本 RELATIVE 比较);
  同一执行 book(统一宇宙 + 干跑流水线)= 自然下一步。
- **对标顶级路线图 Rank #1 已做 + 诚实更正(2026-07-01,`tools/dry_run_combo_riskparity_multi.py`)**:风险平衡统一执行
  book(rolling 逆波动 sleeve,PIT)+ portfolio-of-books 配置——**carry+crowd 分散不敌执行现实**:三种方式 VT 1.20/1.14/1.21
  均 < crowd_contra 单条 VT 1.38。1.95/8% 是 gross 理想化(carry gross~1.44),净-成本下 carry 仅~0.58 拉不动 crowd,
  净曲线相关 +0.04。执行流水线抓到理想-vs-真的差。**deployable = crowd_contra 单条。** 见 `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`。
- **对标顶级路线图 Rank #2 已做(2026-07-01,Barra-lite read-only 诊断第一版)**:`services/portfolio/barra_lite.py`
  新增纯函数 read-only 因子风险诊断核(`winsorize/zscore/corr_exposure/build_barra_lite_model/barra_lite_report`),
  复用现有 `FactorRiskModel` + `risk_decomposition`;`tools/barra_lite_diagnostics.py` 从本地缓存构建
  5 因子(`carry/crowding/liquidity/market_beta/volatility`)并输出 gitignored 的 `data/cache/barra_lite_snapshot.json`。
  首跑:58 symbols / 5 factors;carry total_var 0.6544、crowd_contra 1.3969、carry_crowd_equal 0.5733。
  诚实边界:诊断-only,不等同完整机构级 Barra 因子库,不改权重/晋级/闸门/干跑/券商路径;治理测试钉死无网络/券商/订单/凭证 surface。
- **对标顶级路线图 Rank #3 已做(2026-07-02,拥挤门 primitive)**:`services/portfolio/crowding_gate.py`
  新增纯函数 fail-closed crowding gate,输入候选权重 + caller-supplied crowding evidence,输出 `PASS/WARN/BLOCK`。
  score=按绝对权重的拥挤暴露均值;缺失覆盖不足、显式 None/NaN/inf、空 book、非有限权重均 `BLOCK`。
  诚实边界:primitive only,未自动接入 `build_portfolio`/干跑/预交易门/券商/生命周期;后续若要改变组合构建行为需单独 spec。
- **对标顶级路线图 Rank #4 已做(2026-07-02,协方差感知多 book 资本配置 primitive)**:`services/portfolio/capital_allocator.py`
  新增纯函数 fail-closed covariance-aware capital allocator,输入多策略收益流 + 可选容量/质量/前次权重,输出 `ALLOCATED/BLOCKED`
  研究 AUM 权重建议、turnover 与诊断(`book_stats`/capacity/effective caps/risk contribution)。默认使用 lower-confidence expected return
  (`mean - mean_shrink_z * vol/sqrt(n)`) + shrunk covariance + 现有 constrained mean-variance optimizer,并叠加 per-book cap、capacity cap 与 no-trade band。
  诚实边界:研究 allocation primitive only,不读 `DryRunStore`/`run_registry`,不写 DB,不自动调仓,不接 dry-run/pre-trade/broker/lifecycle/readiness/console;
  治理测试钉死无网络/券商/订单/凭证 surface。`quality_multiplier` 来源和容量衰减曲线仍是后续独立 primitive。
- **对标顶级路线图 Rank #5 已做(2026-07-03,Brinson-Fachler 归因 primitive)**:`services/portfolio/brinson.py`
  新增纯函数 benchmark-relative attribution,输入 caller-supplied portfolio/benchmark weights + asset returns + group map,
  输出 `ATTRIBUTED/BLOCKED`、逐组 allocation/selection/interaction/active return 与恒等式残差诊断。手算两组、benchmark=portfolio
  零主动收益、long-short 权重加性、缺 return/group、非有限值、权重和异常、确定性均测试钉死。
  诚实边界:研究解释 primitive only,不读写 DB,不接 `DryRunStore`/`run_registry`,不自动调仓,不接 dry-run/pre-trade/broker/lifecycle/readiness/console;
  多层 sector/style/macro 适配器仍是后续独立接线,当前只对 caller-supplied 单层 group map 做严谨恒等式归因。
- **对标顶级路线图 Rank #6 已做(2026-07-03,冲击模型校准 primitive)**:`services/research/realism/impact_calibration.py`
  新增纯函数 calibration set,把 RE-1 的文献默认/敏感性假设形式化为 aggressive/base/conservative 三情景
  (`temp_coef` 5/10/20bps,`perm_coef` 2.5/5/10bps,平方根 exponent=0.5),provenance=`UNCALIBRATED_PRINCIPLED`,
  并提供 `impact_band_from_calibration()` 可直接喂给 `capacity_band`。测试钉死情景排序、`AlmgrenChrissImpact`
  转换、capacity band 契约、非法参数 fail-closed 与治理边界。
  诚实边界:这不是 venue/tick 实测校准,不改 dry-run 成本/成交、不改 `CostModel`/组合构建/预交易门/券商/生命周期/readiness/console;
  真实成交校准仍需 tick/LOB/执行数据。
- **新前向路线图 Rank #7 已做(2026-07-03,压力门接真实历史情景重演)**:审计发现 `StressTestEngine` 用 4 个臆造固定倍数
  「编造」情景,而真危机重演 `realism/scenario.py` 就在隔壁闲置。现在调用方给逐期 timestamps → 真危机窗口(2021-05/LUNA/
  FTX/2023-03 银行)重演 + 内生最差回撤窗;无 timestamps → 旧倍数仅作诚实标注回退(`SYNTHETIC_MULTIPLIER_UNCALIBRATED`)。
  每份 `PortfolioStressReport` 带 provenance/scenarios_skipped/scenario_losses,`RiskApprovalGate.approve(timestamps=)` 透传,
  console risk-dashboard 透出 stress 块(契约测试钉死无 provenance 的压力数不上线)。诚实边界:console 路径暂无逐期时间戳,
  诚实显示回退标注;有时间戳的调用方(如干跑 book)同一字段拿到 `REAL_HISTORICAL_REPLAY`。
- **机构级路线图 S2「承重」sprint Rank #10 做完(2026-07-03,组合原语从诊断变 load-bearing——全盘最大杠杆)**:
  三子项全落地、每项 tests-first + `validate.py` 绿 + 活驱动器端到端跑过。**10a**(a287e95)拥挤门加固:
  `crowding_gate` 加最大单名并行判据(外部评审 P0 稀释修复——集中且拥挤的单名独立触发,不被加权平均稀释)+
  可选 ADV/流动性维;接进 crowd_contra 干跑(逐周从真 global_ls cs-rank 评估、落 `DryRunRun.detail`、真 BLOCK
  跳调仓)——诊断非否决(crowd_contra 按构造持拥挤名 → 232 周全 WARN 记录不否决;集中判据在扩散 book 不触发,
  但集中 book 会 BLOCK);非-block 周字节等价(回归安全)。**10b**(55d7e17)资本配置器驱动 riskparity sleeve 权重
  (PIT 仅过去收益,BLOCKED/观测不足回退 rolling 逆波动;231/313 周用 allocator)+ `_clean_weights` 改有界迭代注水
  (显式收敛+fail-closed)。**10c**(0d14bb7/32d7755)优化器加 Rockafellar-Uryasev CVaR 尾部惩罚选项(尾部次梯度,
  非光滑路径取最优可行迭代;光滑路径字节等价)+ 经 allocator `cvar_penalty` 接进 riskparity(尾部感知 sleeve 定权,
  carry 0.47/crowd 0.53)。**52 新测试;三条既有 book(carry/crowd/trend/combo)未动,riskparity 按设计变(allocator 驱动)。**
  诚实边界:拥挤阈值未校准(R-1);carry/combo 拥挤接线待 crowding 证据覆盖其宇宙(#22/#17)。**下一步 S2**:#11 冲击成本进干跑 → #13 风控门承重 → #22 质量强制。
- **机构级路线图 S1「守卫与留痕」sprint 全做完(2026-07-03,活路线 `docs/2026-07-03-institutional-completion-plan.md`)**:
  4 项 P1/S 全落地、每项 tests-first + `validate.py` 绿。**Rank #8**(6ca7d92)PIT 裸读机器守卫:
  `tests/governance/test_pit_read_boundary.py` AST 钉死 services/ 白名单外 0 裸 `fetch_bars` + tools/ 计数棘轮
  (35,只减不增),正负控制 + 实地探针证明守卫会红。**Rank #20**(0630f0c)治理决策留痕:新
  `services/monitoring/audit_store.py::DurableAuditLog`(append-only sqlite 哈希链,存 entry_hash 读时重算抓字段篡改、
  prev 链抓重排、seal/verify_against 抓截断/替换/擦除,镜像 run_registry 模式、无墙钟);`KillSwitch.arm/reset/
  reset_with_grant` 与 `evaluate_deployment` 加**可选** `audit_sink=`(默认 None 零回归,失败安全排序:arm 先停机后审计、
  reset 先审计后恢复、部署门审计在 try 内失败即关)。**Rank #18**(4e41851)runner 调用方身份:POST /run 强制
  `X-Operator`(缺→400),operator 落 run 行(走 payload_json 免迁移、不进 run_id)+ 审计链 `research.run.triggered`;
  **活系统端到端验证过**(无 header 400 / 有 header 200 回显 / 非白名单 kind 仍 400 / 审计链记实);前端 `triggerRun`
  带 localStorage operator 标签 + ResearchRuns 页输入框。诚实钉死:operator=自述式、未鉴权(非 RBAC)。**Rank #21**
  (5e2a64b)fetcher 治理收编:`yahoo_equity_fetch` 改走规范 `ingest_provider_rows` 接缝(只 stdlib+services、进
  `_FETCHERS`→全 10 网络 fetcher 入 AST 边界)+ 存 **RAW 未调价**(去掉 adjclose 回溯比例烘价的非-PIT-vintage 前视,
  完整分红调整留读取时 Rank #23),provenance=`YAHOO_V8_QUOTE_RAW`。**系统已启动并在跑**:console API :8400(真数据)、
  runner :8420(已重启加载 #18)、前端 :3100(连真后端、新 7 组导航)。**S1 剩清理批**:CLAUDE.md 计数刷新、
  跟踪 AGENTS.md。**下一 sprint S2「承重」**:#10 组合原语接线 → #11 冲击成本进干跑 → #13 风控门承重 → #22 质量强制。
- **前端信息架构重构已做(2026-07-03,用户指令「按顶级 AI 量化系统彻底重构前端」)**:42 页 → 30 页,
  7 个工作流导航组(总览→数据→因子研究→验证→组合与风险→执行与跟踪→治理与系统);砍 12 个纯合成蓝图/
  重复/演示页(旧路由语义就近重定向,蓝图页→SystemCompleteness 诚实完整度页——删页不删诚实);路由/导航/
  冒烟测试收敛到单一页面注册表 `src/app/routes.tsx`(新页一行自动进导航+测试);测试地板 14/42 → **30/30
  全冒烟 + 注册表一致性**(44 fe tests);后端端点零改动(契约锁定)。typecheck/build/test/validate 全绿。
  Rank #24 前端半边(全页冒烟)提前落地;console_api 后端拆分 + provenance 徽标断言仍待做。
- **新前向路线图 Rank #9 已做(2026-07-03,部署门强制 version content-hash 钉死)**:版本契约(ConfigVersion/StrategyVersion/
  ModelVersion)一直存在但 `evaluate_deployment` 从不校验——「version-pin gate」原是 overclaim。现在 PAPER+ 必须声明部署内容
  且逐版本与批准钉死表(id→content_hash)比对,fail-closed(`VERSION_PIN_REQUIRED/_NOT_PINNED/_MISMATCH/HASH_MISSING/
  STRATEGY_VERSION_NOT_APPROVED`);多方 grant 强路径不绕过(批准=谁可部署,钉死=部署什么);DEV/TEST 不受限;LIVE 仍被
  readiness pin + LIVE flags 结构性挡死。
- **统一执行 book 做了(2026-07-01,`tools/dry_run_combo_multi.py`,第 4 条策略)**:carry 面板加 OI global_ls,每周
  0.5·carry_legs+0.5·crowd_legs 过干跑流水线。RAW/VT 两条曲线:carry 0.45/77%→0.65/22%、crowd 1.21/12%→1.38/11%、
  **combo 0.58/56%→1.20/15%**。诚实教训:naive 权重 blend 是 carry-dominated(58币 vs 14大币半权)→ combo VT 1.20 improved
  carry 但仍 < crowd alone 1.38;equal-risk 收益流组合(1.95/8%)≠ naive blend——执行里兑现分散需 risk-balance sleeve。
- **短周期(4h/小时线、小样本)= null。** 价格/GP/量能因子都无稳健 OOS edge;加宽截面把样本内
  Deflated 夏普抬到 0.586,但**无窥视 OOS 证伪**(选择性过拟合,样本外 −2.01);量能因子净夏普全负。
- **日线周期 = 第一个正信号(2026-06-26,`tools/crypto_daily_research.py`,commit 1564bb6)。** 同一
  CoinGecko demo,`market_chart days=365`→日线(~364 根/币),24 币×364 期,日级窗口进化搜索。结果与
  4h **截然不同**:最优因子全是同一族 `dist_from_high`(距 N 日高点=趋势代理,7/14/30),IC≈0.065、
  **扣成本后** net Sharpe≈2.4、Deflated 0.95;**无窥视选择 OOS 这次没被证伪**(mean OOS 年化 +3.95 vs
  IS +2.90,3/4 fold 为正)。
- **但要克制(不是"找到 alpha"):** `sharpe()` 按 √252 年化(3.95≈日 Sharpe 0.25),OOS 池是**毛收益**;
  逐 fold 极噪(+7.43/−0.80/+6.26/+2.93,被两个暴涨 fold 拉高、一个为负);**整段样本=2024-25 单边牛市**,
  而"距高点"正是牛市机械获利、反转即崩的因子,**未经熊市/震荡验证**;可信的数字是 IC≈0.065(温和但真实)。
- 容器化始终守住:auto_approved=0、全 REQUIRES_HUMAN_REVIEW——系统**没有**自动放行它(正确)。
- **对抗验证(`tools/crypto_daily_verify.py`,离线)两项都过:**(1)成本压力:OOS 净 Sharpe 随成本近线性
  衰减(3→3.95、10→3.18、20→2.37、30→1.06、50→−0.44),**盈亏平衡≈35–40bps 单边**;中盘币现实 ~10–20bps
  → OOS 仍 +2.4–3.2,**不是一扣成本就死**(区别于 4h 量能)。(2)留一币:基准 2.448,去掉任一币落在 [2.22,2.75]
  → spread 0.53 = **宽基**,非一两个币带飞。
- **Regime 拆解(`tools/crypto_daily_regime.py`,离线)比担心的好:**"只在涨市赚?"的疑虑**不成立**。dist_from_high_14
  原始每日 Sharpe 在两种波动率(calm +0.219 / storm +0.215,中波约 0)和两种涨跌市(跌/横盘 +0.191@n=208 /
  涨市 +0.106@n=128,跌/横盘反而更强)里**都为正**——因为它是**美元中性的相对强度**因子,天然与大盘方向无关。
  (accel_14 是弱的:涨市 −0.014;momentum_7 跌 +0.146/涨 +0.045。)注意:"牛市内的跌/横盘日"≠真正多月熊市,
  趋势反转处的"动量崩溃"(Daniel-Moskowitz)正是一年牛样本装不下的——仍需出 regime 数据才能彻底关闭。
- **完美化一轮(全 42 币 + 合成,`tools/crypto_daily_composite.py`):** 两条诚实教训——(1)**更多数据=更诚实
  (更低)的数**:24→42 币把无窥视 OOS 从 3.95 降到 ~1.45–1.73(24 币那批是最干净大盘,偏乐观);但单因子
  `dist_from_high_14` 跟踪 OOS=2.53、**4/4 段全正**(挖到的最稳的东西)。(2)**简单胜过花哨**:4 因子 z-score 合成
  在每个维度都不如单个 dfh_14(full 1.60<2.05、OOS 1.89<2.53、75%<100%);周度调仓更差(0.56<1.60,信号次日就衰减)。
  成本盈亏平衡(42 币):合成 ~22bps;现实 ~10bps 仍明显为正。regime:涨/跌、calm/storm 都正(全宽更温和)。
- **出 regime 验证(2026-06-27,`tools/crypto_history_research.py`,CryptoCompare 5.5 年、23 币×2000 日线、
  2021–2026 含 2022 熊市)—— 日线趋势 lead 已被证伪、死亡:**
  - 全样本容器化裁决:dist_from_high_14 net Sharpe **−0.55**、IC **0.001**(≈0);8 个价格因子里最好的
    (dist_from_high_30)也只有 **+0.034**(≈0),IC 普遍近零或负。
  - **逐年净夏普正负乱翻、毫无稳定性**:2021 **−1.82**、2022 **+1.0**、2023 **−1.61**、2024 −0.05、2025 +0.79。
    不是"牛市赚熊市亏",就是**不稳定**;2024–25 那段是**运气窗口**。
  - 全样本成本曲线从负起步(−0.55@3bps),无盈亏平衡;regime 按涨跌市**两边都负**(与一年样本的正好相反)。
    长样本无窥视 OOS 仅 +0.42(选了 momentum_14),噪声级,非 edge。
  - 容器化全程 auto_approved=0:系统**从未放行**它。**这是平台在正常工作** —— 更长更全的数据 + 诚实机制,
    戳穿了一个"一年牛市幻觉"。量化第一课就这么活生生上了一遍。
- **截面 alpha 引擎 + Alpha101 实测(2026-06-27,`alpha_expression.py` + `tools/crypto_alpha_research.py`)——
  更丰富的因子,同样没信号:** 建好了因子语法扩展(时序×截面算子,解锁 Alpha101 那类真因子),在同一份
  5.5 年/23 币面板上跑 15 个精选截面 alpha:**仍无跨 regime edge**。最好 accel_cs 全样本 SR 0.60 但被 2021 一年
  带飞(负 3/6 年);所有 |IC|<0.03;**幸存者 0**。**这把"瓶颈是数据不是因子生成"从论断变成了实证**:可表达空间
  放大几个量级、照样挖不到东西,因为价+量在大盘币日线上被高效定价了——alpha 不在这份数据里。引擎是永久基础设施
  (将来有更好数据时即用)。
- 定性(钉死):**"挖更多花哨因子"是错的杠杆,已实证。** 当前没有任何经得起跨 regime 的加密因子(简单的、丰富的
  都≈0)。真正的杠杆 = **数据护城河(口子)**:把广度铺到**小盘币**(低效定价处)、另类数据、其它资产类别、或更高频/
  微结构。下一步要么用户拍板开数据口子,要么把 `dist_from_high_14` 这类候选挂纸面跟踪。详见记忆 `crypto-research-findings`。

## 5. "口子"(2026-06-27 用户已授权放开 —— 目标=可盈利机构级系统,见记忆 `goal-profit-open-kouzi`)
用户原话:「放开口子,不再局限于研究——研究是底层,赚钱是目的」。以下口子**我可主动用**(推进 Phase 时):
1. **数据**(最大,~80% 边际):更长历史/逐根成交量/资金费/链上/另类/**行业·板块分类映射**(用于中性化)。
   `tools/` 只读拉取,**适配器接口已就绪**,填 SourceSpec 即接入。
2. **LLM 点子生成器**:**门外生成 + 门内当裁判**(LLM 提议假设/DSL,确定性引擎 + DSR/OOS/容器化裁决);
   不进 services/ 确定性核心、不破容器化(LLM=非人类 actor,永不 APPROVED)。参考 arXiv 2505.11122。
3. **numpy 提速**:可在评估层引入向量化,但须隔离评估、量化对确定性/可复现的影响。
4. ⛔ **硬红线(我绝不自己跨,必须用户多步人工审批)**:真实实盘下单 / 接券商·CTP·FIX SDK /
   翻 LIVE_TRADING_ENABLED·REAL_BROKER_ENABLED·ALLOW_ORDER_SUBMISSION / 提升 readiness(L2→L3+)。
   我把系统一路造到这条线前(research→paper→shadow→容量→成本→生命周期就绪),go-live 留作用户人工动作。

## 6. 定位(对"做到最好"的诚实回答)
**在"纯-stdlib、确定性、安全可复现的研究系统"这一类别里,过程已接近顶级**:发现(GP+MCTS)→去冗余→
不自欺验证(DSR/PBO/Purged-CV/无窥视 OOS)→全生命周期,且"不自欺"比所借 SOTA 论文更严。
**没有静态"最优解"**(市场非平稳+反身性+数据有硬上限);"最好"=最好的**过程/适应机器 + 数据护城河**。
要"整体最好"必须开第 5 节的口子。

## 7. 提交锚点(resume 时 `git log --oneline -1` 对一下)
**★★★ 系统排查审计 + 完善路线图(2026-06-29,用户指令 "先排查整个系统→再完善功能→最后做前端")**:
9 智能体全量取证审计(`docs/superpowers/specs/2026-06-29-system-audit-and-completion-roadmap.md`)。
**核心结论:后端比文档真实得多,缺口是"接线"不是"造引擎"**(~1014 测试过,`services/{alpha,data,
portfolio,intelligence,validation,...}` 全真且测过;CLAUDE.md "~10% built" 已陈旧并纠正)。36 界面真实度:
REAL 7 / PARTIAL 6 / MOCK 17 / NONE 6 / BROKEN 1。`l2_ok=true`(全绿,仅 1 个 P2 已修)。
已批准:仅加密数据、真缺子系统页诚实标注。
- **Phase 0 排查收口**(`839c1bb`):删 3 死文件+空目录;App.tsx 36 页 React.lazy 拆 546KB 首屏→
  28KB+vendor166KB(警告消失);LifecycleMonitor/SystemHealth 补诚实徽标;修治理测试 P2;纠正 CLAUDE.md。
- **Phase 1B 真序列升真**(`d2aa6fb`+`86d2c34`):审计的"一行换源"前提不成立(快照只存真指标无真序列),
  故 ① session 暴露 `factor_returns`(真逐期净回报)② `tools/crypto_research_offline.py` 离线再生器
  (不联网,从缓存 PIT 库 crypto.db 重跑确定性 session,18 币×120 期,写 `factor_series`)③ console_api
  `_load_real_factors/_real_factor/_input_provenance`,`_forward_returns`+`_computed_alpha` 有真序列时跑真
  (真 ic/turnover/state+真权益/夏普/回撤,IR≈IC·√breadth),无快照 fail-closed 回退合成。6 端点
  provenance→`_input_provenance()`(真=REAL_CRYPTO_SERIES);契约接受 real-or-synthetic;前端 6 页 realData 徽标。
  **实测**:alpha-factory REAL_CRYPTO_SERIES+真因子名;risk 真 9 引擎门跑真回报→REJECTED_CORRELATION_RISK(诚实)。
  门禁双路全绿(真路径本地+合成路径 CI),containment auto_approved=0。HEAD=`86d2c34`。
- **Phase 2 接真引擎升真 NONE 页**(`2db98ce`/`0774fe0`/`b3e8767`,复用 Phase 1B 真因子序列 + services/{research,alpha} 真引擎):
  ① **种群优化器**(`/population-optimizer`)factor_set.select_synergistic_set 协同前向选集去冗余 + inverse-vol 权重 +
  真边际 IR + 真边际回撤(组合 maxDD 增量,可负=分散) + diversity(1−均|corr|);② **Alpha 相关图**(`/alpha-graph`)
  services/alpha/alpha_graph.correlation_graph 真相关网络(节点/边/连通分量冗余簇 + 后端确定性布局);
  ③ **Alpha 资产库**(`/alpha-zoo`)研究快照全部真因子目录(真 state/DSR/净夏普);④ **DSL 浏览器**(`/dsl-explorer`,
  `ee63604`)真 dsl_checker/complexity/expression_leakage 计算裁决(非硬编码):合法表达式机器证明 PIT 安全、
  ts_lead 读未来被真拒绝(确定性面板仅作泄漏扰动基底)。每个新端点 fail-closed 回退合成(CI 无快照安全)、
  契约 real-or-synthetic、前端 useConsoleSnapshot + realData 徽标。门禁全程双路绿。
  **真实度更新**:Phase 1B+2 后 6 PARTIAL + 4 NONE 已升 REAL(Risk/Validation/ComputationLab/Portfolio/
  PaperTrading/AlphaFactory + PopulationOptimizer/AlphaGraph/AlphaZoo/DslExplorer)。
- **Phase 3 起步**(`0033f1a`):**事件流**(`/events`)从真实 run_registry 派生事件(每个研究运行=一条只读审计事件
  research.run.{status},真标题/裁决/头条;前端已通用渲染,后端单文件改);registry 空→合成回退,
  provenance 条件化 RESEARCH_RUN_REGISTRY/SYNTHETIC。本会话共升真 **11 页**(6 PARTIAL+4 NONE+Events)。HEAD=`0033f1a`。
  **Phase 3 余下页(Experiments/EvidenceLedger/Agents/LifecycleMonitor/Overview)的真持久层多为空**
  (experiment_registry/audit_log/agent 运行记录未被离线再生器填充)——需先"填数据"(如让离线再生器写
  experiment_registry 记录)再接端点,不是纯接线。
**★★★ 自主开发批次(2026-06-29,用户出门前审批 A→B→C→D→E,自主执行)**:
- **A 合规文档**(`f3eb921`):`docs/COMPLIANCE_AND_PROCESS.md`(机构 SDLC/职责分离 + QUANTOS 对照 + 单人落地 + PR DoD 清单)。
- **B 诚实标注**(`6594510`):ReviewerWorkflow/AccessControl/ReleaseCommittee/IncidentResponse 补"蓝图/未实现(需 Phase 14 身份层)"徽标。
- **C Phase 3 数据填充 + 升真**:离线再生器写 experiment_registry(60 真不可变评估,`b30643b`);**Overview**(真 n_evaluated/active_runs,`b30643b`)、**Experiments**(读真 experiment_registry,真 IC/夏普/reproducibility_hash,`0252223`)、**EvidenceLedger**(真重算 hash 链 + 第一条接真实验,`94094ab`)、**Events**(早先 `0033f1a`,真 run_registry 事件)全升真。
- **D 安全原语上台**(`ba6c002`):`GET /safety-primitives`(REAL_SAFETY_PRIMITIVES)只读真 KillSwitch/23 盘前检查/SafetyConfig;Risk 页安全 widget。只读不下单,治理扫描器仍 PASS。
- **E 前端指挥中心**(`caabfe9`):`CommandCenter` 单屏座舱,5 磁贴可拖拽重排 + 点击缩放 + localStorage 布局持久化,全真数据只读;根路径默认落地。实测 live 通(5 拖拽磁贴、缩放改 span、无控制台报错)。HEAD=`caabfe9`。
**本会话累计升真 ~15 页**(6 PARTIAL + 4 NONE + Events/Overview/Experiments/EvidenceLedger + 安全原语 + 指挥中心)。门禁全程双路绿。
**关键资产**:`tools/crypto_research_offline.py` 离线再生器(不联网,从 crypto.db 重跑 session 写 factor_series,
18 币×120 期);`crypto_research_snapshot.json` 是 gitignored 真数据源(CI 缺→合成回退)。重生真数据:
`python -m tools.crypto_research_offline`(后台跑~2-3min)。
**后续**(Phase 2 高价值项已完成,剩低优先 BacktestResults 改 run_registry 动态)→
**Phase 3**(Experiments 接 experiment_registry / EvidenceLedger+Events 接 monitoring/audit_log 真 hash 链 /
Agents+LifecycleMonitor 接 services/{intelligence,alpha} 真运行态——这批缺的是"真持久层"非引擎,引擎都在)→
**Phase 4**(kill-switch/pre-trade 门上台 + 真缺子系统页 RBAC/模型注册表诚实标注)→
**Phase 5**(前端 Grafana 自由网格指挥中心 react-grid-layout,默认磁贴指向已升真页 + 导航重组)。

**★★★ Phase 3 续批升真(2026-06-29,用户"继续开发";5 并行规划员产出接线规格→串行 TDD 实现,每页门绿+提交)**:
- **F MonitoringDrift 部分升真**(`fff1f1f`):ALPHA_DECAY 基线/裁决跑真 `degradation_monitor`,provenance 条件化
  `PARTIAL_ALPHA_DECAY_REAL`;实时漂移侧 L2 无源仍合成,加琥珀色诚实横幅说明。不伪造 live 漂移。
- **G LifecycleMonitor 升真**(`eeb531a`):新 `GET /lifecycle-monitor` 读真 experiment_registry(60 alpha 真
  state/backtest_ic/sharpe/pvalue/deflated_sharpe/verdict);**诚实** live_ic=null、recommendation=NO_LIVE_DATA
  (L2 无实盘/纸面 IC,不伪造 DEGRADED)、days_in_state=null、真态均 TESTING(auto_approved=0)。
- **H DataCatalog 数据集升真**(`ae78130`):`_enumerate_real_datasets()` 枚举 `data/cache/crypto_daily.db`(42 标的)
  为真数据集(真 content_hash/row_count/coverage/相对新鲜度/PIT 安全,模块缓存);features/lineage 仍合成并诚实标注;
  provenance `REAL_PIT_DATASETS`。MonitoringDrift 复用 catalog→DATA_FRESHNESS 限 top-8 防刷屏。
- **I BacktestResults 动态化**(`5b33883`):新聚合 `GET /backtest-results` 按 kind+recency 取最新 done run
  (carry/combo/oi_research/survivorship+factory),真 metrics/series/verdict 绑 tile/柱;无真源派生量(carry 逐年、
  R²0.5%、2.09 头条)保留 OBSERVED;crowd 逐年柱改用真幸存者 per_year(诚实修正)。
- **诚实徽标修复**(`c0b9c38`):升真页拉真数据但 ProvenanceRibbon 默认 realData=false → 误标"合成演示"。
  ConsolePayloadBase 加可选 `data_provenance`;LifecycleMonitor/BacktestResults/DataCatalog 接
  `realData={usingBackend && data_provenance===<真标记>}`(Overview/Experiments/Risk 早已有)。**浏览器 HMR 实测**:
  三页均显示绿"Real research results",无误标,无控制台错误;回测战果页截图确认实时取自 run_registry。
- **Agents 诚实留 mock**(feasible_now=FALSE):无真 agent 运行持久层(`ResearchAgentLoop` 无状态、experiment_registry
  无 agent 列、bandit_state 盘空、budget_spent_cny 无源)→ 需先建 `agent_run_registry`(Phase 11)。**勿硬造**。
- 批次后门禁全绿:**1020 py 测试** + 治理扫描 PASS;前端 24 fe 测试 + typecheck;:8400 console_api 已重启服务新端点。
  提交均在本地 `master`(无 upstream push——本仓既有模式)。HEAD=`c0b9c38`。


HEAD 在研究操作台 Task 1-7(`run_registry`/`research_runner`/前端研究中心)附近;`main==master`(rtk 下 `git branch -f main master`);后端 `validate.py` **全绿**(含新治理测试 test_research_runner_boundary);前端 `npm test` **23 passed**(含 CompareRuns smoke)+ typecheck + build 绿;远端 kerstker/QUANTOS 已同步(PR #14 OPEN)。
**★★★ 研究操作台(Research Ops Console,2026-06-29,用户指令:前端要能操作系统、不在 IDE 跑、动态结果区不一回测一页、顶级机构标准)= 前端从「看板」升级为「操作台」**。brainstorm→spec(`docs/superpowers/specs/2026-06-29-research-ops-console-design.md`)→plan(`docs/superpowers/plans/2026-06-29-research-ops-console.md`)→7 任务 TDD 全绿。架构:① `services/research/run_registry/`(sqlite 单一事实源,线程安全;`index_existing` 把现有 `crypto_*.json` 结果灌入→历史第一天全可见);② 查看只读=console_api GET `/research-runs[?kind=]`+`/research-run?run_id=`(契约守,provenance RESEARCH_RUN_REGISTRY),前端「研究中心 /research/runs」列表 + **一个通用数据驱动查看器** RunDetail(metrics→KPI/series→图/verdict/caveats,任何 kind 零新代码);③ 操作=独立 `services/research/research_runner/`(端口 8420,**唯一可变面**)`POST /run{kind,params}` 校验白名单(7 个只研究 kind)→异步队列→写 registry,前端按钮触发 + 3s 轮询 queued→running→done。**安全机器强制**:`tests/governance/test_research_runner_boundary` AST 扫描——无券商 SDK、不 import services.trading、无下单/转账调用,白名单入口全 services/tools 研究函数、⊆ registry ALLOWED_KINDS;console_api 仍 GET-only;auto_approved 恒 0、到不了 live/flag/APPROVED;墙钟只在 runner HTTP 边界取。**端到端实测**:前端点「双因子组合」→ 真算 combo(SR 1.503)→ registry done → 列表 7→9。启动见 CLAUDE.md Commands(console_api:8400 + research_runner:8420 + `research-console-live`)。记忆 [[research-ops-console]]。
**★★★ 操作台四能力补完(2026-06-29,用户指令 "都做,最终都要做到的";顺序 观测→参数化→对比→历史,每个 TDD+门绿+实测)**:
① **可观测**(`7621e19`)实时日志流(`_LogWriter` 重定向 run stdout 逐行进 `registry.append_log`,RunDetail 2.5s 轮询 + 日志面板)+ 软取消(标 cancelled 丢结果,不强杀线程,VALID_STATUS 加 cancelled)+ git-sha(纯读 `.git/HEAD` 无子进程)。
② **参数化**(`d3cc516`)`factory_orthogonal` schema `{generations:int,population:int}`;`_tool_then_json` 改 `main(**params)`;前端 RUNNABLE_KINDS 带 `RunParamSpec[]` 渲染 typed number 输入 → `triggerRun(kind,params)`;`validate_params` typed/fail-closed 复校。
③ **对比**(`c86476e`)`/research/compare?ids=a,b,c`(可分享 URL)`fetchResearchRunsCompare` 并行拉 N 个(各自 snapshot 校验)→ 指标矩阵(指标键并集 × 每运行一列,缺失补—)+ per-run 序列图;不做"最优"高亮(指标方向不同,诚实);列表行加勾选 + "对比所选(N)"(≥2 启用)。
④ **历史留存**(`830a960`)`_run_summary` 增 `params`+`code_version`;列表加「参数」「git」两列(短 sha,hover 全量);历史索引运行无 sha 诚实显示 —,runner 新跑显真实 sha。登记表=「参数+结果+时间+git」可检索历史。
**四能力实测**:runner 触发新 combo_book → `code_version=c86476e`(=HEAD)、SR 1.503 → 列表 git 列显示 c86476e + 参数;对比矩阵 combo SR 1.503 vs carry net@5bps 2.030 并排;门绿(1014 py + 23 fe),无控制台报错。HEAD=`830a960`。
**前序锚点**:`1ccc0c3` 第二条 edge crowd_contra;`4b10905` 日内挖尽;`a0a25d3` 回测战果页。
**★★★ 第二条 edge 候选——逆向散户持仓 crowd_contra(2026-06-28,用户指令 "接第二种免费持仓数据做正交第二条 edge")**:
- **数据墙绕过**:实时 REST 持仓接口只给 30 天,但 **Binance Data Vision metrics 归档**有多年免费每日 OI+多空比 ZIP。抓取器 `tools/binance_oi_metrics_fetch.py`(PIT 安全/并发13×提速/过治理边界,`4fb9d84`+`b8fa063`);研究 `crypto_oi_research.py` + 8 项证伪 `crypto_oi_verify.py`(`1ccc0c3`)。14 流动主流币 × ~2199 日。
- **发现**:`crowd_contra = cs_rank(neg(global_ls))`("散户太多头时做空")——价 SR **1.18**、与 carry **R² 仅 0.53%(几近完全正交)**、增量 IR **+1.13**、逐年 22-26 全正、**晚一天进场仍 1.03**(非时序假象)、净收 +0.52bps/天资金费、成本/子集/embargo OOS 全过。项目至今最强信号。
- **4 路对抗验证 Workflow(PIT/统计/经济/成本,各跑真代码,ultracode)= 无 kill-shot,但裁决 WEAK_LEAD 非确认**:统计维度在「多重检验+去2022」叠加后未过 p<0.01;利润尾部集中;**幸存者偏差结构性不可证伪**(仅 14 个活到今天的币,空头腿从未在死币上测);近端净值软化;部分崩盘凸性溢价。**= 第一个真·carry-正交的第二条 edge 候选,可挂纸面跟踪,非已确认。** 容器化不变(auto_approved=0)。全文 `docs/research/2026-06-28-crowd-contra-second-edge.md`、记忆 [[crypto-research-findings]]。
- **同日跟进**:① **双因子组合**(`crypto_combo_book.py`,真 `optimize_population`):carry×crowd_contra×top_contra → **组合 econ SR 1.50、maxDD 0.12**、净@10bps 1.02、比最佳单因子 +0.23(`<combo commit>`)。② **幸存者完整重测**(`crypto_oi_survivorship.py`,验证报告的 #1 风险,Data Vision 不过滤死币→可测):抓 17 个死币(LUNA/FTT/…),`oi_value/oi` 价格代理,**survivors-only 1.228 → 含死币 0.933 raw,幸存者折扣 ~0.30**;逐年仍全正、winsorize 非支柱、8/17 死币正确做空但做多了 LUNA/FTT。**裁决 WEAKENS 但 SURVIVES**——非幻象(~0.9 正 Sharpe),但 1.2 头条带 ~0.3 水分,deployable ~0.9,是截面持仓边非崩盘对冲。OI 抓取器加 `find_end_ms`/bounded(收敛死币范围)。
- **剩余决定性测试**:前向纸面跟踪(近端软化=噪声 or 衰减);对组合也做幸存者重测。
**★★★ 自主多维正交发现——机器自己挖出新边(2026-06-28,`tools/crypto_factory_orthogonal.py`,用户愿景:机器找多维多向甚至未知 edge)**:
- cs_genome 本就字段无关 → 传 8 维 fields(价/量/资金费/oi/oi_value/top_ls/global_ls/taker_ls)+ 多维 panel,并把搜索奖励改成**对已知边{carry,crowd_contra}正交的增量 IR**(逼机器找新边而非重收敛 carry)。6 代 ×132 个跨维度因子,跨维交互天然可生成。
- **机器自主发现 `cs_rank(ts_argmin(global_ls,30))`**(散户持仓 30 天最低点距今多久——用 global_ls 的全新结构,人没写)**与 carry/crowd 完全正交(R²0.001)**,过快速 gauntlet:SR 0.86、换手 0.16/天、**滞后 0.86/0.78/0.73(扛1天)**、净@10bps 0.56、逐年 22-26 全正。= 第三个正交 lead。
- 同时吐出量能变化诱饵(in-sample 好看),诚实机器精准毙之(滞后 0.70→0.21→−0.12、净@10bps −0.10)。**机器找候选 + gauntlet 分真假,如设计。**
- argmin_global_ls30 是 LEAD 非确认(主折扣=132 次搜索选择偏差,待完整对抗验证)。容器化不变。
- **已硬化进引擎 + 放大(`b99f723`)**:`run_alpha_factory(basis_returns=...)` 成为永久 opt-in 能力(给定已知边对齐流→奖励切到对 basis 的正交增量 IR,默认行为不变,已测、门绿 ~982);加 `alpha_expression.deserialize`(serialize 精确逆,round-trip 测)。`crypto_factory_orthogonal.py` 重构为引擎薄编排器。放大跑(8代×64,192 因子):机器自主长出**量×价跨维度交互** `cs_rank(add(cs_scale(volume),ts_rank(close,14)))`(SR 1.38、R²|known 0.011 完全正交、过 lag+cost)+ 新持仓结构 `ts_delay(ts_rank(global_ls,14),30)`;无窥视 OOS mean 1.16,机器发现被选 4/6 折。诚实:192 次选择膨胀,顶部 lead 比 crowd_contra 更脆,是 lead 非确认。**用户愿景(机器自主多维/跨维找正交边 + gauntlet 分真假)已成引擎能力并放大验证。**
- **+basis 维度 + loop-until-dry(`<basis/loop commit>`)**:加第 9 维 basis=perp标记价(oi_value/oi)/现货−1(与资金费 carry 正交 R²0.021)。loop 每轮把过完整 gauntlet(正交+PIT+滞后+成本)的新发现并入 basis 直到连续 2 轮枯竭。实测:机器挖到 **1 个** 新正交边 `cs_rank(sign(cs_rank(top_ls)))`(top-trader 持仓 SR 0.57)后枯竭。**诚实结论:免费加密日频的稳健正交 alpha 空间薄=carry+散户持仓+1 个弱 top-trader 持仓边到顶**;放大跑那个量×价 SR 1.38 没过更严 loop gauntlet(更脆=选择产物)。量×价 4 路对抗验证 = **REFUTED**(全 kill-shot,真实数据复现):滞后 0→1 SR 1.41→0.66(成本前崩)、lag1 净@10bps −0.001、截面 IC 每 horizon/6-7 年皆负(=做多偏置趋势 tilt 非截面边);对照 crowd_contra(lag1 净 0.498)/carry(0.516)都扛住→gauntlet 校准对。综合:同 bar 时序假象,加固"薄空间"结论。全文 `docs/research/2026-06-28-autonomous-orthogonal-discovery.md`。**平台如设计运转:机器自主找跨维度候选 + 诚实机器有牙齿地毙假阳性(哪怕纸面 1.38)。** 当前真边(深压测后修正):**只剩 2 条稳健边——carry(~2.0)+ crowd_contra(幸存者后 ~0.9 WEAK_LEAD)**。top_ls 经深压测**降级**(survivors-only 0.69→含死币 0.40、逐年熊年为负 2021 −1.23/2022 −0.79、t 仅 2.01=脆弱近年 lead 非稳健边);volume×price 已 REFUTED;intraday 0 幸存。要更多真边瓶颈=数据(微结构/链上/跨资产),非搜索。
- **richer-data 试验 #1:日内持仓动态 = 也被挖尽(`crypto_factory_intraday.py`)**:metrics 本是 5 分钟,提取 6 个日内派生日频特征(oi_range/oi_trend/gls_trend/gls_range/taker_mean/oi_surge),15 维自主搜索 230 因子 → **0 幸存者**(日内因子纸面 SR 0.86–0.97 但 lag1 全 ≈0/负、net@10bps 深负=同 bar 假象)。**诚实:同种持仓数据 finer resolution 不是杠杆,eod crowd_contra 已吃干;真增量得是另一种数据(盘口微结构/链上/跨资产)。** 抓取器加 `--intraday`(纯 stdlib,治理过)。全文见 `docs/research/2026-06-28-autonomous-orthogonal-discovery.md`。
**★★★ 前端补全 + 监控接真数据(2026-06-28,用户选定方向 "前端补全 + 监控接真数据")**:
- **前端补全 5 页**(`dddeab9`+`1687e20`,后端能力本就就绪):Alpha 资产库(可筛 generator/状态/簇/DSR)、种群优化器(选集/权重条/边际 IR·DD/剔除原因)、信号→组合(美元中性目标 book + 容量 + 被拒信号,**只算 book 绝不下单**)、DSL Explorer(表达式静态检查/复杂度/PIT 泄漏机器证明,含一条读未来被拒例)、Alpha 相关图(SVG nodes/edges/簇/近重复)。均 mock-first/只读,注册 nav+route,smoke 测覆盖(19 测试)。
- **监控漂移接真引擎**(`e34275e`+`41cb3f1`):console_api `_monitoring_drift_payload` 的 ALPHA_DECAY 一类改由真 `services.alpha.degradation_monitor.monitor_alpha_health` 实算(多指标 IC/return/slippage/turnover/drawdown→OK/DEGRADED/PAUSED/RETIRED),映射成面板 status,remediation 透传引擎裁决原文(`[degradation_monitor:<rec>] …`)。**只 RECOMMEND 不驱动状态,live 授权恒 False**(containment 不变)。`drift_score` 接引擎严重度并钳进与 status 一致的阈值带(OK<warn≤WATCH<breach≤BREACH)→ 条/色/阈值/status chip 四者自洽;新增自洽性测试。
- **对抗复核(2 子代理)+ 修复**(`84ea717`+`66740a1`):监控接线无 P0/P1(带钳设计代数证明无法掩盖破口);前端 5 页一个真 P1=缺合成数据来源横幅 → 5 页统一补 `ProvenanceRibbon`(⚠ 合成演示数据·非真实研究结果或盈亏);清理死导入/中英重复/mock 回退字面量。
- **真后端端到端验证**:uvicorn :8400 + Vite :3100(launch.json `research-console-live`)实测:监控页吃真引擎数据(BACKEND_COMPUTED/已连接,破口卡片显示 `[degradation_monitor:PAUSED] slippage 2.2x backtest` 等引擎原文),新页显示合成数据横幅。截图为证;服务器用后已停。
**★★★ 量产 + 见钱(2026-06-27,观察值)**:① `carry_pnl.py` 把完整资金费收取 P&L 接进管线(`economic_returns_by_id`/`economic_sharpe`):真实 115 perp×2661d,资金费 carry **eco_SR 1.8–2.18**(复现既有 ~2.0),CPCV 经济 OOS **100% 折正、p05 也正**。② `alpha_factory.py::run_alpha_factory`(cs_genome 进化+经济评分+末代跑管线):真实数据上**机器自主繁殖,前 5 名精英全是资金费 carry 变体 eco_SR ~2.0**(`cs_rank(neg(funding))` 等),OOS 100% 折正。全程容器化守住、leakage 全证明 PIT-safe、裁决诚实 REQUIRES_HUMAN_REVIEW(不自动放行)。tools: `crypto_pipeline_run.py`、`crypto_factory_run.py`。对抗审查各 0-2 发现已修。
**★★★ 机构级 Alpha 因子工厂补全升级 = 后端全 7 Phase 完成(P0+1-7,审计 10 缺失全补齐),前端 Phase 8 起步收官(vitest 基建 + Lifecycle 页)。** 每 Phase tests-first+门禁绿+多智能体对抗审查+验收;新模块共 +148 后端测试。计划全文见 `docs/ALPHA_FACTORY_UPGRADE_PLAN.md`。
**★★★ 机构级 Alpha 因子工厂补全升级 进行中(2026-06-27,用户大指令:把"因子发现器"升级为"Alpha 资产生命周期系统")。**
计划全文 `docs/ALPHA_FACTORY_UPGRADE_PLAN.md`(Phase 0 审计:67 能力→22 有/35 部分/10 缺;按规则 #9 适配现有架构:元组 DSL 不重写成类、纯 stdlib 不上 numpy、7态+研究态不碰 live)。**已完成**:
- **P0 安全地基**(`f682325`):①`services/research/expression_leakage.py` 截面引擎表达式级 leakage **机器证明**(扰动未来行→重算→断言 as-of 截面不变;compute_leakage_ok→True/False/None);②embargo 绑 label horizon(`session._inner_loop` 把 fwd_horizon→wf_embargo=h-1,h=1 时字节不变)。
- **Phase 1 算子+检查器+复杂度**(`a4d9453`+`40dce60`):新算子 `cs_winsorize`/**`cs_residualize`(中性化原语——对任意已算信号截面正交,无需行业数据)**/`ts_cov`/`ts_beta`/`sqrt`(接 `_CS`/`_CS2`/`_EW1`/`_TS_PAIR` + evaluate 分发);`dsl_checker.py`(Type+Dimension 静态检查,签名表从引擎注册表自动派生,可选 `fields=` 字段存在性);`complexity.py`(算子加权复杂度+node_count+depth,fail-closed)。
- **Phase 1 对抗审查修复**(`6198385`):14 智能体多维审查(9 确认/5 为正面确认),修 4 真问题(cs_genome._node_ok 改 `_TS_PAIR`、evaluate 拒 bool、checker 可选字段存在、complexity 未知算子 raise)。
- **决定推迟**:Phase1-1d(evaluate 节点级 CSE memo)——改热路径有别名风险且 CachingPITStore 已解主瓶颈,等 profiling 证明再做。
- **Phase 2 科学验证门**(`4a65c70`+`2ff0ba5`+`ff8ba01`):①`validation/cpcv.py` 组合 purged CV→OOS Sharpe 分布(替代单个 PBO 标量,暴露选择性过拟合;非空分布永远汇总、矩为 NaN 也不返 None);②`multiple_testing.py` 补 bonferroni/holm_bonferroni/benjamini_hochberg_yekutieli(任意相关 FDR)/bonferroni_tstat_threshold(t 阈 haircut);③`session._cpcv_validate` 把 CPCV 接进三模式输出("cpcv" 键);④`pipeline.run_research_validation` 的 leakage_ok 改【必传】(堵唯一非 fail-closed 漏洞)。对抗审查 4 智能体仅 1 发现 1 修复。
- **Phase 3 富 Alpha Asset 体系**(`b33771b`+`59f67d2`+`d347e32`):一次补掉审计 10 缺失里的 6 个。
  ① `quantos/contracts/alpha_asset.py`:AlphaAsset(~51 字段 frozen,组合现有 Definition+Evaluation)+ AlphaLineage;asset_hash=全 model_dump 哈希;from_definition 拒伪造 status(容器化)。
  ② `services/alpha/registry.py`:版本不可变注册表 + 血缘查询(children_of 扫所有版本)+ 审计;**add 拒绝无 lifecycle_event_ids 的 APPROVED(容器化红线 fail-closed)**。
  ③ `similarity.py`(AST 子树 Jaccard/收益 |corr|/holdings 余弦/exposure 余弦)、`alpha_graph.py`(显式 nodes/edges/clusters/near-dup,复用 AlphaCluster)、`zoo.py`(name/expr/universe/generator/status/cluster/tag/指标 检索)、`lineage.py`(make_child_asset perf_delta=子−最优父)。
  对抗审查 9 智能体抓出并修了 2 条 P0 容器化(registry/from_definition 能造 APPROVED 绕过)+ 3 条 P1/P2。
- **Phase 4 种群优化器**(`0418cfa`+`a7a6ea0`):`services/alpha/population_optimizer.py`——集合级联合优化。combine/IR/max_drawdown/三种权重(equal/inverse_vol/ir)/边际贡献(含 drawdown,drop-one);选择=greedy 增量 IR→alpha_graph 在 rho_cut 去近重复→max_k,method='evolutionary'=确定性局部搜索(只接受提升,≥greedy);目标=post-cost IR(钳[−10,10])+多样性−复杂度−拥挤;输出 Alpha Portfolio(selected/weights/excluded+reason/portfolio_ir/post_cost_ir/max_drawdown/diversity/objective)。对抗审查 6 智能体 4 发现全确认全修(marginal_ir inf−inf=NaN→0、目标钳制、DROPPED_BY_EVOLUTIONARY、tie-break)。成本/容量为代理(真 backtest 归 Phase5)。
- **Phase 5 Signal→Portfolio Adapter**(`435305e`+`607012e`):`services/portfolio/signal_adapter.py`——单期截面信号→可下单目标 book。清洗(missing/stale/INVALID_TYPE)→winsorize(k-MAD)→中性化(对 size 截面 OLS,fail-closed:缺因子名 MISSING_FACTOR 拒绝)→z-score→_project_constraints 迭代投影到 {net=0}∩{gross=target}∩{|w|<=cap}→成本(CostModel+单独 spread)→容量(participation vs ADV,缺 ADV=UNKNOWN)→报告。退化(单名/常数)=显式空 book。**研究/dry-run only,绝不下单**(安全审查零发现确认)。对抗审查 7 智能体 5 发现全确认全修。
**至此「信号→可交易组合」研究全链路打通**:因子 DSL(P1)→验证门(P2)→Alpha Asset/Zoo/图/血缘(P3)→种群优化器(P4)→Signal→Portfolio(P5)。只差人工 go-live 红线(不碰)。
- **Phase 6 生命周期监控**(`d9e475e`+`9fcd8e4`):①AlphaState 加 SHADOW/DEGRADED/PAUSED(dry-run 监控态)+ 转移表 + AGENT_REACHABLE(含三态,**仍排除 APPROVED**)+ lifecycle._run_guard 三态 permitted;**容器化红线守住**(对抗审查穷举:非人类经任何新路径到不了 APPROVED,0 发现);**刻意不建 small_live/production/live=L3**。②`degradation_monitor.py`:live-vs-backtest IC/return/slippage/turnover/drawdown/capacity 比对→OK/DEGRADED/PAUSED/RETIRED 建议(优先 RETIRED>PAUSED>DEGRADED);**只建议不驱动**,fail-closed flag 缺数据。
- **Phase 7 Research Memory + 自动 Brief**(`e7816ff`+`1667d77`):`research_memory.py`(ResearchNote: hypothesis/code_version(git sha,caller 传)/dataset_version/generator/verdict/reasons/sharpe/cluster/lineage/regime/data_deps/next_action + from_asset 工厂 + ResearchMemory in-memory latest-wins)+ `research_brief.py`(compose_brief 确定性合成:worked/failed/warned/needs_review 四分区完整、failure_reasons 直方图、top_performers、redundant_clusters、next_actions 启发式+引号归一去重、mutation_suggestions)。**无 LLM 也能跑**;LLM 门外提议=更多 notes 过同门禁。对抗审查 3 智能体 2 发现全修(WARN 分区丢失、引号去重)。无权限不驱动状态。
**🎉 审计 10 缺失全部补齐(Phase 2-7)**:CPCV、富 Alpha Asset、Alpha 血缘、AST 相似度、holdings/exposure 相似度、显式相关图、Zoo 检索、种群优化器、生命周期监控、Research Memory+Brief。
- **Phase 8 前端 console 收官(起步)**(`4e3db73`+`e453c97`):①**vitest 组件测试基建**——vite.config 加 test 块(jsdom)+ src/test/setup.ts + format.test.ts + pages.smoke.test.tsx(闭合审计"前端 UI 测试近零":既有 0 真组件测试→现 14 passed)。②**Lifecycle Monitor 新页**(展示 Phase6 状态 + 衰减建议,mock-first,只读,容器化说明带;注册 nav+route;浏览器实测渲染正常截图为证)。
**前端剩余页(Phase 8 续)= 已补全(2026-06-28,`dddeab9`+`1687e20`)**:Alpha Zoo 可筛 / Population Optimizer / Signal→Portfolio / DSL Explorer / Alpha Graph(DAG)五页全建,均 mock-first/只读/带 ProvenanceRibbon,smoke 测覆盖。剩 Validation Gate 详情可按同模式再补(非阻塞)。
- **主线接线管线**(`ba75774`):`services/research/alpha_pipeline.py::run_alpha_asset_pipeline`——把 P1-7 串成端到端:cs 表达式→**计算 leakage_ok**(P0,真计算非 None)→评估(P2 catalog DSR)→富 AlphaAsset+注册表(P3)→Research Memory(P7)→种群优化器(P4)→Signal→Portfolio 美元中性目标 book(P5)→自动 Brief(P7)。**不驱动 lifecycle(无 APPROVED 接口),容器安全**(对抗审查 0 发现)。端到端实测:leakage 全 COMPUTED、裁决诚实 REQUIRES_HUMAN_REVIEW(不放行合成假象)、book gross1/net0、Brief 诚实。
**真正尚未做的**:① 把这条管线接进 cs_genome 进化/MCTS 搜索循环(用 make_child_asset 写血缘、跑大规模量产);② ~~前端剩余 5 页~~ **已完成(2026-06-28)**;③ 真实数据上跑这条管线(price+volume+funding,验证能否量产 ≥carry 的稳健 alpha)。这些是"用起来/铺开",核心能力已全造好+接通。下一个未拓的边:接第二种免费持仓数据(open interest/多空比)做与价/资金费正交的第二条 edge,或把 carry 挂纸面跟踪看衰减。
**尚未接线(归到"cs_genome 接进容器化搜索"增量)**:搜索循环用 make_child_asset 写血缘 + 用 expression_leakage 算 leakage_ok 喂 evaluator + 把 AlphaAsset/registry/zoo 接进主线产出。
**★★★ 顶级化文档已补齐(2026-06-27,ultracode 多智能体工作流)**:`README.md` + `docs/{ARCHITECTURE,ROADMAP,RESEARCH_FINDINGS,DATA_SOURCES,GITHUB_SETUP}.md`,每条声明对真实仓库核查过(评审顺手发现 `CLAUDE.md` 的"~10%/空脚手架"已过时——实清 139 个 services .py、741 测试函数,待与用户一起校 CLAUDE.md)。
**★★ ALPHA 引擎开发中(从"一个"到"量产+动态",用户愿景)**:增量1 已成 = `services/research/cs_genome.py`(对 alpha_expression 截面树做确定性 mutate/crossover/seed,产物保证合法/根为 cs/深度≤6/PIT 安全/可评估/含 funding 叶子+二元交互;6 测试钉死,无 RNG/墙钟)。**待做**:增量2 = 把 cs_genome 接进一个进化/MCTS 搜索循环,经未改动的 `ResearchAgentLoop` 容器化评估(auto_approved 恒 0),DSR 给搜索去偏、无窥视选择 OOS 当裁判、去冗余协同;增量3 = 在 price+volume+funding 数据(crypto_funding.db + 资金费缓存)上跑,看机器能否量产出 ≥carry 的稳健 cs-alpha。参考现成:`tools/crypto_alpha_search.py`(固定网格版,可升级为生成式)。
**★ 资金费 carry = 第一个过关的真信号(2026-06-27,实测,`tools/binance_funding_fetch.py` + `crypto_funding_research.py`
+ `crypto_funding_verify.py`):** 引入**资金费率**(perp carry=杠杆/情绪,和价量正交;Binance 公开 fapi,只读,边界钉死)。
`fund_avg7`=cs_rank(neg(ts_mean(funding,7))),115 永续×2661 天:净 SR 1.18@3bps、换手仅 0.26;**单因子无窥视 OOS
mean +0.40、3/5 折正**(池子那个 −0.24 是被烂因子拖累);成本盈亏平衡 ~30bps(现实 ~10bps 下净 ~0.9);6/7 年正含 2022;
反向控 fund_lvl_rank 最差(方向对)。**是第一个同时过 OOS+成本+跨 regime 的因子。但仍是 lead 非已证实可规模化的 alpha**:
OOS 噪声大(2 折负、被 +2.57 一折拉高)、**近年衰减**(2020-21 大、2023-26 弱,carry 拥挤)、IC≈0(尾部)、幸存者偏差。
容器化 auto_approved=0、REQUIRES_HUMAN_REVIEW。**这是第一个正当的纸面跟踪候选**(区别于已证伪的 dist_from_high_14)。
下一步坐实/规模化:抗幸存者偏差的永续历史、真实 funding+fee 损益、并入其它免费持仓数据(open interest/多空比)、纸面跟踪看衰减。
**综合搜索印证(`tools/crypto_alpha_search.py`,261 个 cs-alpha):** raw 前 15 名 **15/15 全是资金费 carry 变体**
(decay/mean 各窗口,价/量一个没进),DSR ~0.98-1.0、6-7/7 年正;最稳 `rank_neg_decay14_funding` **7/7 年正**。
**但对全 261 的选择 OOS 是负的(−0.45)** —— 盲目"best-of-N"过拟合,机器诚实地拦了。结论:carry 真实且跨~10 种平滑稳健,
但要靠**经济先验 + 稳健平滑**用,**不能靠盲搜**(选择 OOS 已证盲搜过拟合)。这是"先验+诚实机器 > 蛮力"的教科书案例。
资金费已缓存 data/cache/crypto_funding_rates.json。免费 OI/多空比仅 30 天 → 更多多年 alt-data 要付费/链上。
**★★ 真实损益 = carry 回测里真赚钱(`tools/crypto_carry_strategy.py`):** 之前只算价格;perp carry 还**实收资金费**
(多空两腿都收)。净 = 价格 + 收到的资金费 − 手续费,去冗余 carry 组合,115 永续×2186 持仓日:只价 1.39 → **+资金费 2.25
→ 净@5bps 2.03 → @20bps 1.36**;日均收资金费 **5.43bps**、换手 0.28;**7/7 年全正含 2022**(净@5bps:20+4.4/21+2.5/22+1.2/
23+1.1/24+1.9/25+2.0/26+2.3)。之前"衰减"是没算资金费的假象:价格预测力衰减、但**资金费 carry 稳**,所以收益越来越是
**carry 收益(yield)**而非价格 alpha。**诚实折扣(回测没建模,故 2.0 偏乐观):**幸存者偏差(退市极端资金费币缺失,偏高)、
小盘滑点(5bps 乐观,20bps→1.4)、carry 尾部风险(暴动/爆仓会崩,未建模)、容量有限。**=第一个有经济根基、回测真赚钱的效应,
但可部署夏普远低于 2.0、是拥挤的 yield 不是魔法 alpha。** paper_track_start 已记。下一步:1 纸面跟踪、3 真实滑点/爆仓压力、
2 最低成本 alt-data(见下分析)。
—— 以下为前序 null 记录 ——
**宽截面实测 = 最干净的 null(2026-06-27,实跑实读,无捏造):** 接入 **Binance 公开 K 线**(免费/无 key/只读 DATA,
治理边界钉死 4 抓取器;`tools/binance_klines_fetch.py`)→ 437 个 USDT 对,过滤后 **336 币 × 2309 日线、2020–2026 含 2022 熊市**
(比之前宽一个数量级)。跑 15 个截面 alpha:**最好全样本 SR 0.15(≈0)、|IC|<0.02、无窥视 OOS 均值 −0.67(1/5 fold 正)、
幸存者 0、auto_approved=0**。每个因子的正收益几乎全来自 2020(早期狂野数据),之后逐年转负。**更多宽度(最显眼的"数据
护城河"杠杆)并没有挖出 alpha** —— 加密价+量日线即便在 336 个小盘上、6 年里也被高效定价。诚实保留(明确不是 lead):
几个反转类因子(meanrev/low_vol/pv_corr)近 5 年为正但全样本仍负 + OOS 负,正是"子段幻觉",不信。幸存者偏差(只含活到
今天的对)偏上,即便如此也没东西活。**钉死的战略读数:缺的不是更多价+量宽度(已测到 336 币),是另一种数据(链上/资金费/
情绪/盘口微结构)——或承认这一段被高效定价。** 数据湖现有干净免费源:Binance 公开 K 线(data-api.binance.vision)。
⚠️ 历史教训:此前曾把**捏造的**小盘"结论"(argmax_cs20 等)写进文档+提交+推送,被 task 通知戳穿后已全数订正撤回——
**没亲眼在工具输出里看到的数字,绝不写。**
—— 历史锚点 ——
HEAD 曾在 `e0f7c57`(截面 alpha 引擎 Batch 1);`validate.py` 813 测试全绿。**本轮(2026-06-26→27)新增**:机构级开发流程 + 远端就绪(见下)、
因子工厂前端常驻页(流水线+成本曲线+regime,契约两侧守护)、CryptoCompare 多年数据接入、**出 regime 验证证伪
日线趋势 lead**(2022 熊市:dist_from_high_14 全样本 net −0.55、IC≈0、逐年乱翻)、**内存读缓存提速**(回测小时→秒、
逐位一致)、**截面 alpha 引擎(扩因子语法,解锁 Alpha101)+ 实测确认"更多因子也无跨 regime 信号"→ 瓶颈是数据**。
当前结论:**没有任何经得起跨 regime 的加密因子;真杠杆=数据护城河(口子,见 §5)**。下一步候选:开数据口子(小盘币广度/
另类/其它资产)或挂纸面跟踪。远端 = `https://github.com/kerstker/QUANTOS`(Private);本地两服务可能仍开着(uvicorn :8400 +
Vite :3100)。
—— 最新一轮(2026-06-30,补「脑」第一刀:学习型信号组合 ML stacker)——
对标差距报告头号杠杆(零 ML/DL→CRITICAL)。brainstorm→spec→plan→实现(工作流在 Task4 验证员漏发结构化输出处
中断,Task0-4 已提交、Task5 收尾内联补完)。新 `services/research/ml/{feature_panel,stacker,walk_forward_stack}.py`
= 确定性学习型信号组合(GBM+Ridge);依赖放宽引 numpy+sklearn 但**确定性使用**(线程在 import 前钉死、
`random_state=0`、Ridge cholesky、同入逐位相同测试);`services/` 无网络 import 仍机器强制(ML 库非网络)。训练
purged+embargo、裁决 no-peek OOS,关卡不弱化。驱动器 `tools/ml_stack_research.py` 真数据三方 OOS。**诚实结论:
学习型组合无可交易 edge**(净额 @5bps、apples-to-apples、FX 限可交易 18 币,commit bf73a83):crypto GBM −0.78 /
Ridge −0.24 / 单因子 +1.78(组合两路净负、稀释那条好因子);fx 可交易净:GBM −1.12 / Ridge −0.40 / 单因子 −2.04
(全负)。早先 fx 毛收益+全 46 币的 GBM +0.21 是**海市蜃楼**(毛收益伪装 + pegged/legacy 污染),净额化+限可交易后
翻成 −1.12——印证诚实修正的必要。**瓶颈是数据/信号类别,不是建模层**(同薄数据上加更花的模型只会更快过拟合)。门绿(~1050 py)。
诚实记忆 [[ml-stacker-findings]];差距报告 `docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`。

—— 上一轮(2026-06-29,断点1 关卡证据回喂——晋级链解冻)——
HEAD 在 `2507a67`。四道断点全收口(断点2/3/4 + 现在断点1)。**断点1 方案A**:leakage 证据的"真源"
原先缺失(没人给 `loop.run` 传 evidence → `leakage_ok` 恒 None=NOT_ASSESSED=fail-closed,晋级链冻在
leakage 门)。本轮在"有原始 bars+计算函数"的搜索/驱动层算真 `detect_leakage`:`services/research/
leakage_evidence.py`(signal/volume/任意 closes→标量 三种 ComputeFn);三通用搜索环(guided/evolutionary/
mcts)经 `services/intelligence/evidence.py::provider_leakage_evidence` 收集 provider 供证并传 `evidence=`;
provider 挂 `leakage_ok(defn)`:price/volume/combined-price/**GP 表达式树**全真验证,interaction/fundamental
诚实 None(独立 PIT 库/截面合成,bar 级检测器评不了,不伪造)。`crypto_history_research` 驱动器亦接齐
(regime 早开+PBO loop 自算→证据齐全)。容器化不变:证据永不授予晋级(auto_approved=0)。门绿(1047+ py)。
**价值边界**:门"修真"(leakage 不再因 None 形同虚设),不让垃圾因子晋级——加密因子照样过不了 OOS。
剩余(可选):其余单候选 crypto 驱动器的同款 1 行接线。

—— 上一轮(2026-06-29,断点2 前向 paper 追踪持久层)——
HEAD 曾在 `0eca544`。经 ultracode 工作流串行实现 6 任务(store→advance→驱动器→三消费方,每任务 实现+独立验证)+
收尾门绿(governance + **1047 py** + 前端 24)。**实测发现并修了工作流单测漏掉的真 bug**:LifecycleMonitor 把
backtest 夏普当 backtest "return" 去和累积收益比(单位错配)→ 伪 DEGRADED;改 apples-to-apples 夏普对比 +
诚实 `PAPER_RETURN_ONLY`(12 alpha 被追踪、live_tracked=0 因无 IC、DEGRADED=0)。浏览器实测确认(真后端 :8400)。
**诚实第一**:纸面夏普 +4.8..+6.4 是 ~24 期尾巴的噪声,非已证实 edge——和"无跨 regime edge"结论一致。
容器化不变(auto_approved=0、非人不晋级、无库 fail-closed)。本地提交,未推远端。

**本轮(2026-06-30)新增 —— 端到端干跑流水线**:`services/execution/{dry_run_pipeline,dry_run_store}` +
驱动器 `tools/dry_run_carry.py`(见 §3「执行/干跑」)。把预交易门从"已测死代码"接成"运行时可证强制的
chokepoint":权重→意图→**逐意图过门(fail-closed,kill-switch latched 全拒)**→干跑适配器纸面成交(绝不真发)
→平均成本对账(残差 0.0)→哈希链审计→可复现 `DryRunRun`。spec/plan:`docs/superpowers/{specs,plans}/
2026-06-30-dry-run-pipeline*`。11 新测试 + 全门绿(1093 passed)。这是用户"系统要能用、最终通往实盘"
战略判断的落地;实盘永远 = 同接口换真券商适配器 + 人工批准(非目标)。

—— 历史锚点 ——
HEAD 曾在 `3bb6002`(机构级开发流程落地)附近;`main==master`;`validate.py` 全绿。
**远端已就绪**:`https://github.com/kerstker/QUANTOS`(Private),`origin/main` 与 `origin/master` 已同步;
CI(`.github/workflows/validate.yml`)在 push/PR 自动跑后端门禁 + 前端。搭建/分支保护手册见 `docs/REMOTE_SETUP.md`。
待用户手动开启:`main` 分支保护(要求 CI 两个检查 + 1 人评审);本地钩子 `make hooks`(可选)。
本轮主要新增:机构级开发流程(CI 硬化 / Dependabot / CONTRIBUTING / SECURITY / CODEOWNERS / PR·Issue 模板 /
Makefile / 本地 git 钩子 / 远端手册)+ 已建私有远端并推送。
前序新增:Crypto Live 前端 + 宽截面 + 无窥视 OOS + GP 树(+对抗评审硬化)+ 去冗余(协同集/增量奖励)+
Purged-Embargoed CV + 三重门/元标签 + Regime(+接评估器)+ 互信息 + 唯一性/择时/分数阶 +
数据适配器 + 真实成交量 + 量能因子 + MCTS(+接主线)+ 频繁子树规避 + 工厂自验证闭环。
