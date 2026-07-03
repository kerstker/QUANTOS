# QUANTOS 系统全面核查 · 落地架构图 · 前向开发路线图

> **生成：2026-07-03。** 方法：16 个子代理并行核查(13 个领域各自读**真实代码**核实 `CLAUDE.md` 的断言 + 2 个横切代理核查测试/安全边界/文档诚实度 + 1 个综合代理对标顶级机构重新打分)。**本文档不复述文档,只报核实过的事实。**
> 对标对象:顶级 AI 量化(RenTech / Two Sigma / Citadel / HRT / Jane Street + 现代 ML 基金)。打分基准:**作为「研究 / 干跑平台」**(非「能赚钱的实盘基金」)。
> 配套:[`QUANTOS_MASTER_BLUEPRINT.md`](../QUANTOS_MASTER_BLUEPRINT.md)(11 层权威蓝图)、[`QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`](QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md)(Rank #1–6 已完成的活路线)、[`QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`](QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md)(诚实差距基线)。本文档是这三份之上的**核实版合并交付**,承接 Rank #6,给出 Rank #7 起的前向路线。

---

## 0. 一句话总判

**QUANTOS 是一个经过严格测试、诚实标注的 L2 研究 / 干跑平台,它的正确性纪律和安全纪律已接近机构标准;但对标顶级实盘基金的差距是结构性的、且多数是 L2 边界下的故意选择:免费日频数据、纯组合式(非学习型)信号生成、只做诊断而尚未接入组合行为的原语。因此下一批最高杠杆的活不是造新数学,而是把已经建好的机器接成"承重"的——把拥挤门 / 资本配置 / 冲击 / 情景 / CVaR 接进真实的组合与干跑行为。**

核查覆盖的硬指标(全部实测):

| 指标 | 实测值 | 说明 |
|---|---|---|
| Python 文件 | 279 | services + tools + scripts + quantos |
| services 代码量 | ~22,813 LOC | 14 个域,扁平 `services/<domain>/*.py` |
| 测试文件 | 169 | `find tests -name test_*.py` |
| 收集到的测试 | **1,280** | `pytest --collect-only`(文档说 ~1047–1173,**实际更多**) |
| 治理套件 | 62 全过 | `pytest tests/governance`,1.86s |
| risk+contracts 抽样 | 106 全过 | 0 fail / 0 error |
| Gate 状态 | **GREEN** | 抽样三套全绿,无逃避式 skip |
| 券商 / CTP / FIX SDK import | **0** | 211 文件扫描器 + grep 双清 |
| L2 五项安全不变量 | 默认全安全 | 详见 §6 |

---

## 1. 核实过的现状地图(13 个领域)

成熟度定义:`REAL_TESTED`=真逻辑 + 有测试;`PARTIAL`=真但不完整 / 部分未接;`STUB`=脚手架 / 占位;`MISSING`=空。分数 = 该域**作为研究 / 干跑平台**的 0–10 分。

| # | 领域 | 蓝图层 | 模块 | 测试 | LOC | 成熟度 | 分 |
|---|---|---|---:|---:|---:|:---:|:---:|
| 01 | 数据层 `services/data` | 01 Data | 18 | 89 | 1232 | `REAL_TESTED` | 7.0 |
| 02 | 特征层 `services/features` | 02 Feature | 6 | 16 | 249 | `REAL_TESTED` | 6.5 |
| 03 | 因子+回测引擎 `research/{factor_engine,backtest_engine}` | 03 Research OS | 17 | 43 | 1554 | `REAL_TESTED` | 6.5 |
| 05 | 验证栈 `research/validation`+`oos_validation` | 05 Validation | 9 | 45 | 754 | `REAL_TESTED` | **8.0** |
| 04+06 | Alpha 搜索+工厂 `research/{gp_tree,cs_genome,…}`+`services/alpha` | 04+06 Alpha | 26 真 | 272+ | 2741 | `REAL_TESTED` | 7.5 |
| 07 | 组合+结构化风险 `services/portfolio` | 07 Portfolio | 13 | 124 | 1713 | `REAL_TESTED` | 6.8 |
| 08 | 风控门+熔断 `services/risk/{research_risk,pre_trade}` | 08 Risk | 12 | 36 | 1180 | `REAL_TESTED` | 7.5 |
| 07/09 | 纸面交易+干跑流水线 `paper_trading`+`execution/dry_run_pipeline` | 07/09 Paper | 12 | 65 | 858 | `REAL_TESTED` | 6.5 |
| 08/10 | 执行+券商边界 `services/{execution,trading}` | 08/10 Execution | 14 | 64 | 1059 | `REAL_TESTED` | 7.5 |
| 11 | AI 智能体 `services/intelligence` | 11 AI Agent | 10 | 77 | 920 | `PARTIAL` | 6.0 |
| 11 | 治理/监控/部署 `services/{governance,monitoring,deployment}` | 11 Gov/Ops | 7 | 61 | 714 | `PARTIAL` | 6.5 |
| 03+ | 现实主义/ML/宏观 `research/{realism,ml,macro_regime}` | 03 Research+ | 10 | 54 | 638 | `PARTIAL` | 6.5 |
| 10 | 前端控制台+只读 API `apps/research-console`+`console_api` | 10 Console | 47 | 9 | 5160 | `PARTIAL` | 6.0 |

### 核实要点(每域一句 + 抓到的文档漂移)

- **01 数据层 · CONFIRMED 为主。** PIT 双时点(读取层过滤 `knowledge_time<=as_of`,证伪一条 `knowledge_time=500` 的记录在 `as_of=200` 被排除)、生存者无偏区间宇宙、内容哈希版本化、质量检查全真有测试。**漂移:**「PIT 企业行动」实为**只做拆股 + 现金分红计提**,完整分红回调价(总收益序列)自披露推迟到 Phase 4;`catalog.py` 展示的内容哈希 / PIT 证明跑在**合成 bar** 上(注释诚实但 API 表面看不出)。**隐患:**`persistent_store.fetch_bars`(原始非 PIT 窗口读)与 PIT 安全的 `as_of()` 同类共存,只有注释挡着——**这正是整层要防的前视脚印**(见 Rank #8)。
- **02 特征层 · CONFIRMED。** `detect_leakage`(3 套未来扰动不变性检验,真承重——是 alpha 评估门的证据源)+ 版本化注册表 + 3 个滚动算子。**边界:**注册表纯内存(重启即失),无物化 / 向量化 / 另类数据。
- **03 因子+回测引擎 · CONFIRMED。** 硬化 AST 因子编译器(默认拒绝、无 eval/exec、前视名拒绝)、真 IC/rank-IC/ICIR/Sharpe(n<10→None 诚实小样本守卫)、单标的 + 事件驱动回测的**精确对账恒等式**(残差≈0),`ic_panel` 复现 `crowd_contra mean_ic=0.035 / t-stat=4.3211` 逐字一致。**边界:**纯 Python bar 级、冲击系数未校准、无向量化 / 分布式。
- **05 验证栈 · 最强维(8.0)。** DSR / CPCV / PBO / purged-embargo walk-forward / no-peek 选择 OOS 全真且 fail-closed,**已被 ~20 个 tools 驱动器用来证伪自己**(crypto/FX 趋势 OOS 全负)。**惊喜:**综合代理原以为「全局多重检验试验账本」是缺口——**实际已存在**(`services/alpha/multiple_testing.py`:BH/BHY/Bonferroni/Holm + 相关性 effective-trials 折减,接进 `catalog.py`),这是 OVERSTATED 的**反向**(缺口比想的小)。**真残差:**`gate.py` 自认「只信调用方给的数字、不证明回测无前视」——缺的是**端到端 provenance 保证**(见 Rank #12)。
- **04+06 Alpha 搜索+工厂 · 最成熟域。** GP 树进化搜索 + 截面 DSL + 协同去冗余集选择(Gram-Schmidt 残差化增量 IR)+ 真 BH-FDR + fail-closed 多门评估器(IC→回测→catalog DSR→walk-forward→ValidationGate,**从不自动晋级**)+ 结构上非人到不了 APPROVED 的生命周期。272 测试实跑全过。**漂移:**「28 模块 / 185 测试」——2 个是**空壳目录**(`alpha_discovery_engine/`、`alpha_validation/` 零文件),实际测试 272–286;唯一自披露占位是 `population_optimizer.py` 的换手成本 proxy=0.0(标了 Phase 5)。
- **07 组合+结构化风险 · CONFIRMED 但漂移多。** PSD 校验因子风险模型(LDLᵀ)、Dykstra 投影约束优化器(带自披露的 P0 可行性修复)、恒等式校验 Brinson、fail-closed 拥挤门全真。**漂移:**① 「capital_allocator 用 fractional Kelly」——**FALSE**,repo 里 grep 不到任何 kelly,现码已升级为**收缩协方差 + 均值方差**(Kelly 是更早版本,文档把两代设计混成一句);② 「Barra-lite 5 因子」——**因子数不定**、由调用方喂,非硬编码 5 因子;③ 「57 测试」——**实为 124**(过时低估)。**关键缺口:**风险模型是**调用方喂**非估计;目标函数只做方差(对平台自己发现的肥左尾 Hill 2.4–2.7 结构性盲);拥挤门 / 资本配置 / barra-lite **只出诊断、未接组合构建行为**(见 Rank #10)。
- **08 风控门+熔断 · CONFIRMED。** pre-trade 门 23 项真检查(每项异常→`UNKNOWN_RISK_ERROR`,只有 APPROVED/REJECTED 逃逸,构造即 `assert_research_safe`)、latched KillSwitch、9 引擎研究风控门。**漂移:**「9 引擎各算真风险数学」部分 OVERSTATED——**StressTestEngine 用的是 4 个臆造固定倍数**(1.2×/1.5×/1.1×/1.3× 负收益和),而真实危机重演引擎 `scenario.py` 就在隔壁**闲置未接**(见 Rank #7)。
- **07/09 纸面+干跑流水线 · CONFIRMED。** 真重对账 PnL(有符号平均成本、恒等式残差≈0,不再是 `notional*0.001` 占位)、前向 paper-track 持久层、端到端干跑流水线是**真运行时 fail-closed 闸门**。**漂移:**「DryRunRun 追加式持久」——实为 `INSERT OR REPLACE` 按 run_id 覆盖,可复现但非严格 append-only。**冲击模型未接进干跑成本路径**(还是平 bps,见 Rank #11)。
- **08/10 执行+券商边界 · CONFIRMED。** `BrokerAdapter` Protocol、透明 `DryRunBrokerAdapter`、`LiveBrokerAdapter` **构造即无条件 raise**(L2 下 armed 恒 False,即便强凑也再 raise)、`assert_dry_run_adapter` fail-closed、治理钉死无 SDK。执行保真度按 L2 故意不做(无 tick/LOB/SOR)。
- **11 AI 智能体 · PARTIAL。** 安全半边真且机器强制(非人结构上到不了 APPROVED / 复位不了 kill-switch / 交易不了,77 测试)——**领先多数研究平台**;智能半边是**闭式枚举打分**(UCB1+GP+MCTS 在小固定词表上,手调 reward)。**漂移:**`services/research/research_agent_loop/` 是**空壳**(真 loop 在 `services/intelligence/agent_loop.py`);「LLM/RL/在线学习/DL」**全无**(命名误导)。
- **11 治理/监控/部署 · PARTIAL。** 哈希链审计、多方批准(REQUESTED→REVIEWED→APPROVED→CONSUMED,真要 3 个两两不同真人)、拒绝式 NullSigningAuthority 全真。**漂移:**`services/deployment/` 只有 1 个真模块(`gate.py`)非 2;`evaluate_deployment` **从不校验** version/content-hash(契约存在但闸门没接,「version-pin gate」轻微 overclaim,见 Rank #9)。
- **03+ 现实主义/ML/宏观 · PARTIAL。** Almgren-Chriss 冲击 + 容量带(抓到 ~300× 高估)、情景重演 + 尾部(Hill 2.4–2.7、崩盘 beta≈0)、确定性 GBM+Ridge stacking(**诚实结论:当前数据无可交易 edge**)、FRED 宏观 regime。**边界:**多个承重发现只落在 **gitignored** 的 `data/cache/*.json`,repo 内不可复现(见 Rank #19)。
- **10 前端控制台+只读 API · PARTIAL。** 42 页(文档说 43,差一)、`console_api` 41 个 GET 路由(文档说 ~31,**低估**)、大部分 REAL 带 provenance 标记、守卫的 research_runner 是唯一可变面。**隐患:**`console_api/main.py` 是 **5160 LOC 单体**(重构候选);前端只有 **9 个测试(单个 smoke)**;research_runner **无调用方身份审计**(见 Rank #18)。

> **诚实度总评:** 抓到的漂移**全部不影响安全**,多为过时计数、自披露的推迟、或命名误导;没有一条是「假装做了实则没有」的欺骗。最该修的文档漂移是 capital_allocator 的「fractional Kelly」措辞(与现码不符)。**平台的诚实标注姿态是真的。**

---

## 2. 落地架构图(11 层 · 安全脊柱 · 单向管线)

可视版见随本次交付的架构图 Artifact。文字版分层栈(每层标最成熟子域的成熟度):

```
                          ┌───────────────────────────────────────────────┐
                          │  人类最终权威 (多方批准 · kill-switch 复位)      │  ← 只有 HUMAN 能到 APPROVED
                          └───────────────────────────────────────────────┘
   安全脊柱 (贯穿全层,机器强制)                          研究 → 干跑单向管线 (禁止 AI→直接下单)
 ┌──────────────────────┐   ┌──────────────────────────────────────────────────────────────┐
 │ L2 五项不变量默认全安全 │   │ 01 数据层        PIT双时点 · 生存者无偏 · 版本化 · 质量        [REAL] │
 │ 治理扫描器(211 文件0违规)│   │ 02 特征层        泄漏检测 · 特征注册表                        [REAL] │
 │ 0 券商/CTP/FIX SDK    │   │ 03 研究 OS       因子引擎 · AST编译器 · 单+事件回测(对账恒等式) [REAL] │
 │ 哈希链审计            │   │      + 现实主义   冲击/容量 · 情景重演 · 尾部 · ML stack · 宏观  [PART] │
 │ 契约层 SafetyConfig   │   │ 05 验证门        DSR · CPCV/PBO · walk-forward · no-peek OOS   [REAL]★│
 │ /RiskDecision/Actor  │   │ 04+06 Alpha工厂  GP树 · 截面DSL · 去冗余集 · 评估器 · 生命周期    [REAL] │
 │ 券商接缝(LiveAdapter  │   │ 07 组合层        约束优化器 · 因子风险模型 · Brinson · 资本配置   [REAL] │
 │  构造即拒绝)          │   │ 08 风控层        pre-trade门(23检查) · KillSwitch · 9引擎研究门  [REAL] │
 │ 多方批准(3不同真人)   │   │ 07/09 纸面/干跑  重对账PnL · 前向paper-track · 端到端干跑闸门     [REAL] │
 │ 签名接缝(Null拒绝)    │   │ 08/10 执行边界   意图构建 · BrokerAdapter · 干跑适配器(绝不真发)  [REAL] │
 └──────────────────────┘   │ 11 AI智能体      护栏化非人ResearchAgentLoop · 进化/GP/MCTS/bandit[PART] │
                            │ 10 控制台        42页只读 + 守卫可变 research_runner + 只读API    [PART] │
                            │ 11 治理/运维     审计 · 部署门 · 多方批准 · 签名接缝              [PART] │
                            └──────────────────────────────────────────────────────────────┘
   ★ = 全系统最强子系统(验证门 8.0)。   [REAL]=REAL_TESTED  [PART]=PARTIAL
```

**单向数据流(蓝图 §5,已核实边界隔离):**
`研究假设 → 数据验证 → 特征 → 回测 → 验证门 → Alpha 注册 → 组合构建 → pre-trade 风控门 → 纸面/干跑 → 人类批准 →(执行边界,L2 停在此)`。禁止流 `AI→生成策略→直接下单` 被机器结构性挡死。

**空壳 / PLANNED(零代码,诚实标注,非缺陷):**
`services/gateway`、`services/learning`;嵌套 DDD 空目录 `data/{data_quality_service,feature_store_service,market_data_service,tick_service}`、`alpha/{alpha_discovery_engine,alpha_validation}`、`research/{research_agent_loop,dataset_builder,experiment_tracker,model_registry}`。**真代码在扁平 `services/<domain>/*.py`,不在这些壳里。** 建议:要么删、要么显式文档标 PLANNED,避免 doc/code 漂移(见 Rank 清理项)。

---

## 3. 对标顶级 · 八维评分(核实后更新)

| # | 维度 | 现分 | 原分 | Δ | 一句站位(基于核实证据) |
|---|---|:---:|:---:|:---:|---|
| 1 | 数据护城河 | **5.0** | 4.5 | ↑ | PIT 纪律优秀有测试,但护城河轴是**广度/授权**——实质仍是 bar 级、BYO-CSV/免费源、sqlite 研究规模、无 tick/L2、只拆股。范式对,缺料。 |
| 2 | Alpha/信号生成 | **6.5** | 6.5 | = | 搜索机器是全 repo 最能测的域、严谨超多数已发表管线;但生成纯组合式、固定词表、无学习型/LLM 提议器,经验上**只有 crowd_contra 一条边活下来**——机器跑赢产出。 |
| 3 | 回测与验证 | **7.5** | 7.5 | = | **最强维**:DSR/CPCV/PBO/walk-forward/no-peek 真且已证伪自己;假想的「缺全局试验账本」其实已建。残差是 provenance 保证,非方法学。 |
| 4 | 组合构建与风险 | **6.8** | 7.1 | ↓ | 码真且异常能测,但**下调**:风险模型是喂的非估的、目标只做方差(对肥尾盲)、拥挤门/资本配置/barra-lite **只诊断未接行为**、无跨 book 汇总。 |
| 5 | 执行与微结构 | **5.0** | 4.3 | ↑ | **上调**:执行接缝优秀 fail-closed(拒绝式 LiveAdapter、干跑闸门、无 SDK)+ 真干跑流水线 + 已落地冲击原语;但保真度近无(无 tick/LOB、冲击未校准且**未接进干跑成本**)。 |
| 6 | 基建/MLOps/规模 | **5.5** | 5.5 | = | 复现纪律强(确定性 ML、契约钉死 API↔前端、1280 测试 gate-green);但无规模story:sqlite 单文件、纯 Python 无向量化/GPU、注册表纯内存、承重数字只在 gitignored 制品。 |
| 7 | AI/智能体 | **5.3** | 5.2 | ↑ | 安全半边真且机器强制(非人到不了 APPROVED)——领先多数研究平台;智能半边是闭式枚举打分 + 手调 reward,**零 LLM/RL/在线学习/DL**(与命名不符)。 |
| 8 | 治理/安全/资本路径 | **6.9** | 6.2 | ↑ | **上调**:每条大安全断言对抗式核实**全 ACCURATE**(全安全默认、0 SDK/211 文件、扫描器过、3 不同真人哈希链批准、拒绝式签名、构造即 raise 的 LiveAdapter)。残差:version-pin 契约未接、「human」actor 背后无真身份/RBAC。 |

**总判:** 作为研究/干跑平台,明显强于典型量化研究 repo;对标顶级实盘基金,差距结构性且多为 L2 故意选择。**最高杠杆不是造新数学,而是让已建原语承重 + 硬化 provenance + 拓宽离线数据。天花板是数据广度与信号类,不是正确性。**

---

## 4. 前向开发路线图(承接 Rank #6,从 Rank #7 起)

> 已完成(见 [`QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`](QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md)):**Rank #1** 风险平衡执行 book · **#2** Barra-lite 诊断 · **#3** 拥挤门原语 · **#4** 协方差资本配置原语 · **#5** Brinson-Fachler 归因 · **#6** 冲击校准原语。**共同教训:原语都建了,但多数只出诊断、未驱动行为。** Rank #7+ 的主线就是**收口这件事**。
> 分类:`L2_SAFE_NOW`=不破 L2 现在能做;`NEEDS_EXTERNAL`=需数据/算力/团队;`POST_L2`=边界外不该做。impact P0–P3 / effort S–XL。

### 第一批 —— 立刻做(纯接线,P1/S,最高 impact/effort)

| Rank | 项 | 维 | 分类 | I/E | 为什么 |
|:--:|---|:--:|:--:|:--:|---|
| **7** | **把真实历史情景重演 `realism/scenario.py`(LUNA/FTX/SVB 窗口)接进研究风控 `StressTestEngine`**,替掉它现在臆造的 4 个固定倍数 | 4/8 | `L2_SAFE_NOW` | P1/S | 两块都已建已测,压力引擎在**编造**情景而真危机重演就在隔壁**闲置**。纯接线、可信度回报高、diff 极小——**全盘最佳 impact/effort**。 |
| **8** | **加结构守卫/lint 禁止 `persistent_store.fetch_bars`(原始非 PIT)出现在任何回测/研究路径**,把注释级防护变机器强制 | 1/3 | `L2_SAFE_NOW` | P1/S | 这正是整个数据层要防的**前视脚印**,现在只有注释挡着,一次误用就静默重引入前视。治理式 AST/import 守卫便宜且安全关键。 |
| **9** | **在 `evaluate_deployment()` 内强制 version/content-hash 钉死**:PAPER/STAGING 部署前比对请求的 ConfigVersion/StrategyVersion/ModelVersion.content_hash 与批准的钉死哈希 | 8 | `L2_SAFE_NOW` | P1/S | 版本契约已存在但**闸门从不校验**——「version-pin gate」措辞现在 overclaim。补上契约→闸门这一环让资本路径诚实,改动小自洽。 |

### 第二批 —— 本迭代(让原语承重,P1/M,主线)

| Rank | 项 | 维 | 分类 | I/E | 为什么 |
|:--:|---|:--:|:--:|:--:|---|
| **10** | **让组合原语承重**:把拥挤门 + 协方差资本配置接进干跑 book 构建,**并给优化器加 CVaR/尾部感知目标选项** | 4 | `L2_SAFE_NOW` | P1/M | **全盘最大的「已建但不驱动行为」杠杆。** 原语已测但只出 console 诊断;而 RE-2 发现肥左尾(Hill 2.4–2.7),纯方差优化器结构性盲。把诊断变构造行为,是前 6 个 Rank 兑现价值的地方。 |
| **11** | **把已落地冲击模型 + `impact_calibration` 接进干跑流水线成本模型**,替掉平 `cost_bps` | 5 | `L2_SAFE_NOW` | P1/M | 冲击校准刚做完(Rank #6)但干跑还用平 bps、无参与率/冲击现实,容量/冲击发现(carry ~$2M/crowd ~$7M)**从没到达干跑 PnL**。收口这个环让可部署性结论真正约束模拟 book。 |
| **12** | **给验证门加端到端 provenance 保证**:一个测试证明每个晋级 alpha 的试验数 + var_sr **溯源到共享 catalog**(非本地 override),外加任何 DSR/PBO 数到达 `gate.evaluate` 前强制复现哈希 | 3 | `L2_SAFE_NOW` | P1/M | 试验账本已存在(比想的好),真残差是 `gate.py` 信调用方给的数、只有单测。顶级台子按 provenance 把关——把文档化的信任假设变机器校验。 |
| **13** | **把 9 引擎研究风控门从只读诊断升级为真闸门**:任何拟议组合进干跑/paper-track 前 fail-closed 跑它 | 4/8 | `L2_SAFE_NOW` | P1/M | 风控覆盖现在不对称——pre-trade 是活闸门,9 引擎研究门只喂快照。让它 enforcing(非装饰)收口「算了组合风险」与「组合风险能挡晋级」之间的缝。 |
| **14** | **把全样本三分位 regime 标签换成滚动/扩展窗 PIT regime 分类器**,让 carry/crowd_contra 宏观互补从回顾性变可交易可测 | 2/4 | `L2_SAFE_NOW` | P1/M | 平台找到的最强分散故事(相反宏观画像、corr ~−0.04)现在靠**显式非 PIT** 的回顾标签。PIT 分类器才把叙事变成门/优化器能合法据以行动的东西。 |
| **15** | **给 FeatureRegistry 加持久层(sqlite,仿 run_registry)+ 最小 PIT 特征值物化store(按 (feature_id, as_of) 键)** | 6 | `L2_SAFE_NOW` | P1/M | 泄漏检测器真承重,但注册表纯内存(重启全失)、每次滚动特征都从原始 bar 重算。持久化 + 物化移除这一层最大的基建脆弱点。 |

### 第三批 —— 更大投入(数据 + 智能,P1/L,天花板杠杆)

| Rank | 项 | 维 | 分类 | I/E | 为什么 |
|:--:|---|:--:|:--:|:--:|---|
| **16** | **用 ML 引导的候选提议器打破固定词表 DSL 天花板**:学习型提议喂给现有搜索环,再过**同一个** fail-closed 验证门 | 2 | `L2_SAFE_NOW` | P1/L | 命名过的最高杠杆点,但被证据缓和:确定性 GBM+Ridge stacker **无可交易 edge**、结论瓶颈=数据/信号类非建模。框定为**扩大生成搜索空间**(非又一个事后组合器),验证严谨不变,诚实预期——高潜力、真有 null 结果风险。 |
| **17** | **拓宽离线数据**:经 ALFRED vintage / BYO-CSV 把股票 + 基本面摄进现有 `fundamental_store`(机器已建,免费股票源本环境被墙) | 1 | `NEEDS_EXTERNAL` | P1/L | 数据护城河是最低分维、广度是唯一杠杆。双时点 store / fundamental_store / BYO-CSV 摄入路径已建已测——缺的是拿到 vintage-correct 数据(ALFRED 保 restatement 安全),这是数据/授权任务多过工程任务。 |

### 第四批 —— 巩固(P2/M,补审计与复现)

| Rank | 项 | 维 | 分类 | I/E | 为什么 |
|:--:|---|:--:|:--:|:--:|---|
| **18** | 给 research_runner `POST /run` 加调用方身份 + 谁触发的审计;console_api 加最小读审计 | 8/6 | `L2_SAFE_NOW` | P2/M | 唯一可变面现在**无谁触发的记录**——即便 L2 也是真问责缺口。不是 P1 因为面是研究-only、localhost 绑定,但它是任何未来多用户姿态的诚实前提。 |
| **19** | 复现硬化:给容量/情景/尾部/宏观的**承重数字**提交确定性快照制品(或离线重跑 harness),它们现在只在 gitignored `data/cache/*.json` | 6 | `L2_SAFE_NOW` | P2/M | 多个 CLAUDE.md 引用的承重发现(~300× 高估、Hill 2.4–2.7、regime 互补)不重跑网络驱动器就无法从 repo 复现。机器验证是真,但引用数字的可审计性弱。 |

### 持续清理项(随手做)

- 修 `CLAUDE.md` / 文档漂移:capital_allocator「fractional Kelly」→「收缩协方差均值方差」;测试计数刷新(portfolio 57→124、alpha→272+、endpoints ~31→41、pages 43→42);deployment「2 files」→1。
- 删或显式标 PLANNED:所有零文件的嵌套 DDD 空壳目录(§2 列表)。
- `console_api/main.py`(5160 LOC 单体)按域拆分 + 补前端测试(现只 1 个 smoke)。

---

## 5. L2 安全边界内故意不做(不记为「落后于顶级」)

以下是**安全边界本身**,补 = 破边界,**不该补**:实盘交易 / 真券商连接 / 订单提交 / AI 直接交易;真实资金路径(PB / 抵押 / 清算 / 结算);SOR / 场所选择 / 最优执行 RL / colocation / tick-to-trade;毫秒级 intra-day 实时风控接 OMS/PB;真实成交 TCA 闭环;监管报送(MiFID II / CAT / Reg SCI);做市 / 反博弈;部署 / 进程物理隔离(Phase 14 设计已备,实现 post-L2)。

**需外部资源(钱与组织,非代码缺陷):** 厂商授权数据(Bloomberg/Refinitiv/CRSP/Compustat/TAQ)+ 采购/合规团队;真正量级另类数据(卫星/刷卡/AIS/NLP);分布式/GPU 集群;tick/L2 订单簿历史;专职研究 + SRE 团队。

---

## 6. L2 五项不变量(核实:默认全安全、机器强制)

`quantos/contracts/config.py:21-25` 实测:`live_trading_enabled=False` · `real_broker_enabled=False` · `allow_order_submission=False` · `allow_ai_direct_trading=False` · `require_human_approval=True`。`live_trading_armed` 是所有前提的 computed AND;`assert_research_safe()` fail-closed;`services/` 下零券商 SDK(grep + 211 文件扫描器双清);治理扫描器正控(植入密钥 / `import ctp` / live flag / 真 armed SafetyConfig / `place_order`)全捕获。**L2 边界完整,横幅 `L2_DRY_RUN_ONLY` 诚实且正确。**

---

## 7. 建议立刻开发:Rank #7

**把 `realism/scenario.py` 的真实历史危机重演接进研究风控 `StressTestEngine`**,替掉现在臆造的 4 个固定倍数。理由:全盘最佳 impact/effort——两块都已建已测,纯接线、diff 极小、可信度回报高,且直接修掉本次核查抓到的一处实质 overstatement(压力测试在编造情景)。按 brainstorm→spec→plan→build,全程 L2 一寸不让,完成后在 `QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md` 标 ✓ 附提交哈希。

---
*本文档基于 2026-07-03 的 16 代理并行核查。所有分数、计数、成熟度均来自对真实代码的核实,非文档复述。随开发推进更新。*
