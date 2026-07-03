# QUANTOS 机构级补全路线图与详细开发计划

> **生成:2026-07-03。** 承接同日《系统全面核查 · 前向路线图》(Rank #7–19;其中 **#7 压力重演、#9 版本钉死已当日落地**)。
> **方法:** 7 路专项只读深审(数据层 / 组合层 / 执行干跑层 / 验证与风控门 / regime 与特征 / 治理运维 / 复现底座),每路带机构级核查清单,所有结论有 file:line 证据;并吸收三份基线:[`2026-07-03-quantos-architecture-and-roadmap.md`](2026-07-03-quantos-architecture-and-roadmap.md)(核实版审计)、[`QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`](QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md)(诚实差距)、[`2026-07-02-quantos-gap-analysis-roadmap.md`](2026-07-02-quantos-gap-analysis-roadmap.md)(外部评审,其中尚未消化的项本文全部收编)。
> **定位:** 把「排序表」升级为**机构级、可直接开工**的开发计划——每项给出精确接缝(文件:行)、tests-first 测试清单、验收标准(DoD)、工作量、依赖、诚实边界。
> **对标:** 顶级 AI 量化机构(RenTech / Two Sigma / Citadel / HRT / Jane Street 及现代 ML 基金)的**研究 / 干跑平台**维度;实盘维度按 L2 边界显式不做(§7)。

---

## 0. 总判与本次复核新发现

**总判(复核后与晨间审计一致):** QUANTOS 的正确性纪律与安全纪律已接近机构标准;差距不在「造新数学」,而在三件事——**① 已建原语不承重**(拥挤门/资本配置/优化器/风控门只出诊断,从不驱动权重或挡晋级);**② 证据链有断点**(验证门信任调用方浮点、复现哈希只算不验、审计链不落盘、承重数字不可从 repo 重算);**③ 数据广度与现实主义还差一层**(冲击成本没进干跑、质量检查不强制、企业行动不全、股票数据刚起步)。本计划就是把这三件事**系统性收口到机构级**。

### 本次 7 路复核在晨间审计之上的**新增**实质发现(全部进入计划)

1. **组合原语「零接线」拿到精确证据**:`crowding_gate`/`capital_allocator`/约束优化器在全 repo 的 import 只出现于 tests/docs;三个干跑驱动器的权重全部来自 `_legs()` 分位数多空规则,优化器从未被调用(`tools/dry_run_carry_multi.py:73`)。外部评审点名的拥挤门 4 弱点(稀释/无流动性维/无跨 book/阈值未校准)**全部未修**;资本配置器容量再分配是单轮修正非收敛注水(`capital_allocator.py:187-208`);`quality_multiplier` 无任何来源(悬空扩展点)。
2. **验证门信任边界比审计描述更窄也更具体**:evaluator 路径的 `trial_count`/DSR 其实已是 catalog 供源(`evaluator.py:124,128`);真正裸信调用方的是 **`pbo` 浮点参数**(`evaluator.py:82,160`)与 **standalone pipeline 路径的本地默认**(`pipeline.py:94`,catalog override 是可选的、仅注释约定);**`reproducibility_hash` 只算不验**(无任何地方重算比对,`registry.py:69` 仅查非空);**BHY 与 `effective_trials` 相关性折减是「实现了但没接进晋级守卫」的死代码**(`guards.py` 只用 plain BH)。
3. **哈希链审计日志是纯内存、从未落盘**(`monitoring/audit_log.py:28`),且未接进任何服务——kill-switch 搬闸/复位(`kill_switch.py:65-78`)与部署门放行/拒绝(`deployment/gate.py`)这两类**治理决策事后无痕**。
4. **console `system-health` 的延迟数是硬编码合成**(`console_api/main.py:587-612`),而真的 `monitoring/health.py::build_snapshot` 快照库从未上 HTTP。
5. **数据质量「算而不 gate」**:`quality.py` 全套检查在接入时计算(`file_ingest.py:66`)但从不检查 `.passed` 阻断;backtest 引擎零引用。**volume 量纲 bug 源头未修**(仍靠 `scenario_tail_research.py:116-127` 下游诚实标注)。
6. **干跑参与率门被占位符废掉**:`check_liquidity` 有真参与率检查(`risk/pre_trade/checks.py:191`),但所有驱动器喂 `est_daily_volume=1e12`,永不触发;而 drivers 面板里**已载 volume 矩阵却算完流动性分层就丢弃**(`dry_run_carry_multi.py:58,83`)——ADV 给冲击成本与参与率门同源复用的料是现成的。
7. **`tools/yahoo_equity_fetch.py` 两处隐患**:未纳入 `_FETCHERS` 治理名单(`test_crypto_data_boundary.py:12-19`,违反「每个出门 fetcher 必进名单」约定);写入 adjclose 回溯调整价 = **非 PIT-vintage**(历史某日看到的未调价 ≠ 今日 adjclose),且用当前成分 = 幸存者偏差(已自披露)。
8. **前端测试实况 14/42**:42 页只有 14 页有渲染冒烟,`ProvenanceRibbon` 零断言;CI 无 coverage 阈值/依赖审计/lockfile,解释器三版漂移(pyproject ≥3.11 / CI 3.12 / 本地 3.13)。
9. **复现底座量化**:11 个承重制品(capacity/ic_panel/scenario_tail/macro_regime/combo_book/barra_lite/crypto_research/ml_stack 快照 + dry_run.db + paper_track.db + macro_fred.db)全部只活在 gitignored `data/cache/`;**全部快照是离线可重算的叶子**,但根库 crypto_funding.db 57MB 不可入库 → 唯一诚实方案 = 数字指纹 + 离线重算 harness + 缺缓存诚实跳过的门测试。`scripts/validate.py` 对快照零引用——承重数字完全在门禁之外。
10. **已解决 / 虚惊(诚实记录)**:capital_allocator「fractional Kelly」漂移**已清**(CLAUDE/PROJECT_STATE 已无 Kelly 字样);前端新页确定性**干净**(所有 `Date.now/Math.random` 命中均为「断言无此物」的注释);嵌套 DDD 空壳目录**多数未被 git 跟踪**(本地幻影,非仓库缺陷);AGENTS.md 与 CLAUDE.md **逐字节一致**但未跟踪(将静默漂移)。

---

## 1. 机构级六支柱对标(现状 → 本计划完成后的目标)

| 支柱 | 现状分 | 目标分 | 关键差距(证据见 §0/§3) | 对应 Rank |
|---|:--:|:--:|---|---|
| Ⅰ 数据平台 | 5.0 | 6.0 | 裸读无守卫、质量不强制、量纲未把关、企业行动不全、股票 vintage/幸存者 | #8 #21 #22 #23 #17 |
| Ⅱ 研究平台与模型风险管理(MRM) | 7.5 | 8.5 | pbo 裸信、repro hash 不验、BHY/有效试验数死代码、特征层无持久 | #12 #15 |
| Ⅲ 组合构建与风险 | 6.8 | 8.0 | 原语零接线、目标函数对肥尾盲、风控门只诊断、无跨 book 汇总 | #10 #13 #25 |
| Ⅳ 执行现实主义 | 5.0 | 6.5 | 平 bps 成本、参与率门被废、无 TCA、无成本 provenance | #11 |
| Ⅴ 治理与运营 | 6.9 | 8.0 | 治理决策不留痕、runner 无身份、观测性合成、事故/演练缺失 | #20 #18 #26 #27 |
| Ⅵ 基建与复现 | 5.5 | 7.0 | 承重数字不可重算、无 lockfile、console 单体、前端测试地板薄 | #19 #24 #28 |

> 打分基准与晨间审计一致(作为研究/干跑平台)。目标分 = 本计划四个迭代 + 持续轨道全部落地后的诚实预期;**不承诺** alpha 产出(那由数据与市场决定),只承诺**过程与证据链的机构级**。

---

## 2. 路线图总表

### 编号说明(避免三套编号打架)

- **本文编号延续 2026-07-03 核实版路线图**(Rank #7–19 语义不变,#20 起为本次复核新增)。
- 旧《TOPTIER_REMEDIATION_ROADMAP》表中 #7–13 的七行**全部收编**:旧#7 CVaR → 本文 **#10c**;旧#8 相关漂移检测 → **backlog**(依赖 #14);旧#9 全局试验账本 → 已建成,残差即 **#12**;旧#10 可复现硬化 → **#19+#28**;旧#11 股票+基本面 → **#17**;旧#12 crowd_contra 送前向 paper-track → **研究决策 R-3**;旧#13 数据漂移检测 → **backlog**(依赖 #26)。
- 2026-07-02 外部评审未消化项收编:拥挤稀释修复/流动性维/跨 book 汇总/阈值校准 → **#10a、#25、R-1**;quality_multiplier 来源 → **R-2**;决策审计留痕 → **#20**;容量注水收敛 → **#10b**。

### 总表(✓=已完成;S≤2天 / M≤1周 / L>1周,单人+代理节奏)

| Rank | 项 | 支柱 | I/E | 依赖 | 一句话验收 |
|:--:|---|:--:|:--:|:--:|---|
| 7 ✓ | 压力门接真实历史情景重演 | Ⅲ | P1/S | — | 已落地(cf1b869) |
| 9 ✓ | 部署门强制 version content-hash 钉死 | Ⅴ | P1/S | — | 已落地(eebe019) |
| **8 ✓** | PIT 裸读机器守卫(AST 治理测试 + 白名单) | Ⅰ | P1/S | — | 已落地(6ca7d92):services/ 白名单外 0 裸读 + tools/ 计数棘轮 35;正负控制 + 实地探针验证守卫会红 |
| **20 ✓** | 治理决策留痕:审计链落盘 + kill-switch/部署门决策接入 | Ⅴ | P1/S | — | 已落地(0630f0c):DurableAuditLog(append-only sqlite,防篡改/防重排/seal),kill-switch+部署门可选 audit_sink,28 测 |
| **18 ✓** | research_runner 调用方身份 + 审计(自述式 operator) | Ⅴ | P1/S | #20 | 已落地(4e41851):X-Operator 强制(缺→400)、落 run 行 + 审计链;活系统端到端验证;前端带 operator 标签 |
| **21 ✓** | 数据抓取器治理收编 + yahoo 原始价列(PIT-vintage) | Ⅰ | P1/S | — | 已落地(5e2a64b):yahoo 进 `_FETCHERS`(全 10 fetcher 入网)+ 走规范摄入存 RAW 未调价,8 测 |
| **10 ✓** | 组合原语承重(a 拥挤门修复+接线 / b 资本配置换逆波动 / c 优化器 CVaR 选项) | Ⅲ | P1/M | #8 | 已落地:10a(a287e95)拥挤门加固+crowd_contra 接线;10b(55d7e17)allocator 驱动 riskparity sleeve;10c(0d14bb7/32d7755)CVaR 选项经 allocator 接进 riskparity。三子项全 load-bearing。carry/combo 拥挤接线待 crowding 证据覆盖(#22/#17) |
| **11 ✓** | 冲击成本进干跑 + 参与率门同源 ADV + TCA-lite 区间对比 | Ⅳ | P1/M | — | 已落地(cbf8609):pipeline 可选 AC 冲击成本(逐笔参与率驱动,缺 ADV 回退平 bps,恒等式残差不破)+ cost_model/cost_provenance/TCA-lite 落 detail;carry 驱动用 close*volume 同源喂冲击+参与率门(拒 95 笔真容量约束,曾 0)。活跑 IMPACT_AC/6365 笔/TCA in-band/残差 4e-9。crowd/combo(OI 无 spot volume)+ trend(volume 不可靠,#22)暂平 bps |
| **13 ✓** | 9 引擎风控门承重(诚实分级:4 真引擎 fail-closed 断言) | Ⅲ | P1/M | #7 | 已落地(93fd065):`dry_run_guard.evaluate_dry_run_risk` 只跑 4 真引擎(market/drawdown/concentration/stress,各 fail-closed;stress 用 timestamps→REAL_HISTORICAL_REPLAY),5 引擎 SKIPPED_NO_HONEST_INPUT 绝不伪造;接进 crowd_contra 干跑落 detail(VT 序列 3/4,market-VaR 仅默认阈值日频紧未过,诚实标注)。晋级门 guard_to_paper 接线 + 5 引擎喂真输入 = 后续 |
| **22 ◑** | 数据质量强制 + volume 量纲修复回填 | Ⅰ | P1/M | — | 强制机制已落地(e241c00):`_ingest_records/ingest_bars_csv/ingest_provider_rows` 加 `strict=`(质量不过 fail-closed,yahoo fetcher 接 strict=True)+ `quality.check_volume_units` 跨币量纲守卫(scenario_tail 用它作单一真源)。**volume 回填 + trend 容量重算需网络(coingecko,本环境被墙)= 待网络** |
| **12 ◑** | 验证门端到端 provenance(CatalogEvidence + verify_evaluation + BHY/有效试验数接守卫) | Ⅱ | P1/M | — | verify 半边已落地(c7d0e5e):`ExperimentRegistry.verify_evaluation` 重算 content_hash 抓篡改(改指标/改哈希/未知 id 全 fail-closed,4 测)。**剩:CatalogEvidence 封套绑 pbo/trial_count + BHY/有效试验数接 guard_to_validated + console 透出——较大设计改动,待做** |
| **15 ◑** | 特征注册表持久化 + PIT 特征物化存储 | Ⅱ | P1/M | — | 持久层已落地(c5ae7a2):`SqliteFeatureRegistryStore`(镜像 run_registry,含 leakage_passed+code_version)+ `SqliteFeatureValueStore`(克隆 fundamental_store 双时点 + feature_version,PIT/截断不变/fail-closed,10 测)。**剩:rolling_feature 写通道 + 离线填充 + console 读(升真)= 采纳待做** |
| **19 ◑** | 复现底座:evidence 数字指纹 + 离线重算 harness + 门测试 | Ⅵ | P1/M | — | 已落地(9cc4c25/478b5bb):`evidence/{dry_run_books,ic_panel,capacity}.json` 指纹(5 book 结论 + crowd_contra t-stat 4.32 + 容量 ~300× 发现,共享 extractor 保 build/verify 同源)+ `tools/build_evidence_digests.py` + 门测试(缓存在场重算断言/缺席诚实 skip,drift 实测会红)。**剩:扩到 scenario_tail/macro_regime + 完整 rebuild_snapshots.py = 待做** |
| **24 ◑** | console_api 拆分(8 路由组)+ 前端测试地板(42/42) | Ⅵ | P1/M | — | 前端测试地板已落地(a4e4350,IA 重构):30/30 页注册表驱动冒烟 + App 集成 + `assertConsoleSnapshot` payload/provenance 守卫 + format(47 测);28/30 页带 ProvenanceRibbon。**剩:console_api 5167 行单体拆路由组=大重构,宜专注新会话做(契约测试作安全网)** |
| **14** | PIT regime 分类器(扩展窗分位 + 免估计规则优先) | Ⅱ/Ⅲ | P1/M | — | 截断不变性测试过;信用维诚实标 `NON_PIT_INSUFFICIENT` |
| **23 ✓** | 企业行动总收益序列(backtest 侧组装) | Ⅰ | P2/S | — | 已落地(bdd2a79):`corporate_actions.total_return_series` 把拆股调整价 + PIT 分红再投资组装成总收益序列(分红收益率对前价 scale-free,无需 ex-date 参考价——补上 adjusted_close 推迟的 Phase-4 缺口);只用可知行动(无前视),5 测。价值/低波/股息因子可用总收益价 |
| **25** | 跨 book 公司级汇总(read-only 诊断) | Ⅲ | P2/S | #10 | 5 本书总 gross/net/因子暴露一页可见 |
| **17** | 股票+基本面数据拓宽(治理收编后的 yahoo + 历史成分 + ALFRED) | Ⅰ | P1/L | #21 #22 | 股票宇宙 survivorship-free;经济数据 vintage-correct |
| **16** | ML 引导候选提议器(spec 先行,同一验证门) | Ⅱ | P1/L | #12 | 提议器产物过同一 fail-closed 门;诚实 null 预期 |
| **26** | 观测性升真(health 快照上 HTTP + 请求日志) | Ⅴ | P2/S | — | system-health 100% 真源;两服务有结构化请求日志 |
| **27** | 事故账本 + kill-switch 演练 | Ⅴ | P2/S | #20 | 事故页升真(append-only);演练 runbook + 端到端测试 |
| **28** | CI/环境机构化(lockfile + python pin + coverage + 依赖审计) | Ⅵ | P2/M | — | CI 有 coverage 阈值与 pip-audit/npm audit;解释器统一 |

---

## 3. 逐项详细开发计划

> 统一节奏(既定):大改先 brainstorm → spec → plan;一律 **tests-first**;改完 `python scripts/validate.py` 绿才提交;必要时对抗式评审修 P0/P1 再提交。**全程 L2 五不变量一寸不让。**

### 第一批「守卫与留痕」(全 S,一个迭代内)

#### Rank #8 — PIT 裸读机器守卫
- **为什么(机构对标)**:前视是量化研究的头号自欺;机构级要求「PIT 纪律机器强制」,现在 `SqlitePITStore.fetch_bars`(`persistent_store.py:87-95`,自注释 NOT lookahead-safe)与 PIT 安全的 `as_of()` 双胞胎共存,仅注释挡着;研究驱动器有 28+ 裸调用点(建时间网格 + 全样本 ADV)。
- **接缝与设计**:新增 `tests/governance/test_pit_read_boundary.py`,**精确复刻** `test_crypto_data_boundary.py` 的 AST 模式(`_import_roots` + 遍历树):AST 查目标模块内 `Attribute(attr='fetch_bars')` 调用;**白名单豁免**(合法 infra 用法):`caching_store.py`(安全包装器)、`calendar_store.py`(自标注完备性审计)、`console_api`(catalog 元数据)、`session.py`(dataset 版本哈希)、`ingestion.py`(数据源本体)、tests/。tools/ 研究驱动器的「建网格」用法迁移到一个新的 PIT 安全辅助 `persistent_store.event_time_grid()`(只回 event_time 列表,不回 bar 值)或按驱动器逐个豁免+计数钉死(防新增)。
- **tests-first**:① 正控制(临时造一个含 `fetch_bars` 的研究模块 → 测试必红);② 负控制(白名单成员不报);③ 当前 repo 违规计数 == 钉死值(只减不增)。
- **DoD**:治理测试进 `validate.py` 常绿;新研究代码裸读即红。
- **工作量/依赖**:S;无依赖。
- **诚实边界**:tools/ 存量「全样本 ADV」用法是轻度前视(容量估计用),第一步先钉住不增长,存量迁移随 #11(ADV 同源化)一并收敛。

#### Rank #20(新)— 治理决策留痕:审计链落盘 + 两类决策接入
- **为什么**:机构治理的底线是「每个治理决策事后可审」;现在 `GlobalAuditLog` 哈希链**纯内存**(`audit_log.py:28`)且只被干跑流水线临时实例化;kill-switch 搬闸/复位历史是单个可覆写状态(`kill_switch.py:46-78`),部署门放行/拒绝返回后即蒸发(`deployment/gate.py`)。
- **接缝与设计**:① 新 `services/monitoring/audit_store.py`:append-only sqlite 沉淀 `AuditEvent`(镜像 `run_registry/store.py` 模式:`CREATE TABLE IF NOT EXISTS` + lock + `INSERT OR IGNORE`;时间由调用方注入,店内无墙钟),提供 `append/verify_chain/seal`;② `KillSwitch.arm/reset/reset_with_grant` 与 `evaluate_deployment` 增加**可选** `audit_sink=` 参数(默认 None = 现行为,零回归),传入即把决策(actor/decision/reasons/version-pins 摘要)追加进链;③ 干跑流水线的临时 `GlobalAuditLog` 改为可选落盘同一 store。
- **tests-first**:① 链自洽(append N 条 → verify 过;篡改任意一条 → fail);② kill-switch 复位与部署决策各产生一条可验证事件;③ 治理边界测试:audit_store 无网络/无券商 import;④ 决策路径零回归(不传 sink 行为不变)。
- **DoD**:两类治理决策 100% 可落链;链可跨会话重开验证。
- **工作量/依赖**:S;无。
- **诚实边界**:这是**进程内**防篡改(哈希链),不是出进程签名权威(那是 Phase 14);措辞不越界。

#### Rank #18 — research_runner 调用方身份 + 审计
- **为什么**:唯一可变面(POST /run)现在**无谁触发的记录**(`RunRequest={kind,params}`,`research_runner/main.py:37-55`;runs 表无 actor 列)。
- **接缝与设计**:FastAPI `Header(...)` 强制 `X-Operator`(缺失 400);run_registry 加 `operator TEXT` 列(或入 `payload_json`);同时向 #20 的 audit_store 追加一条 `research.run.triggered` 事件。**诚实措辞钉死**:operator 是「自述式标签,未鉴权(NOT RBAC/authn)」——不假装身份层。
- **tests-first**:缺 header 400;run 行含 operator;审计链含事件;治理测试(runner 仍研究-only 白名单)不动。
- **DoD**:每个 run 可回答「谁触发的」;前端 runs 页显示 operator。
- **工作量/依赖**:S;#20(审计沉淀)。

#### Rank #21(新)— 数据抓取器治理收编 + yahoo PIT-vintage 修复
- **为什么**:`yahoo_equity_fetch.py` 逃逸在 `_FETCHERS` 治理名单外(`test_crypto_data_boundary.py:12-19` 约定「新 fetcher 必进名单」);且它写 adjclose 回溯调整价(非 PIT-vintage)+ 当前成分(幸存者偏差,已自披露)。
- **接缝与设计**:① `_FETCHERS` 加入 yahoo fetcher(其 urllib 已在 `_ALLOWED_FETCHER`);② 股票库 schema 增存**原始未调 close**(调整价另列或另 key),保住 vintage 可追溯;③ 幸存者偏差保持显式标注(历史成分灌 `universe_store` 是 #17 的活)。
- **tests-first**:治理测试覆盖 yahoo(stdlib+services only / 无 SDK / 无下单);写入行含原始价;provenance 标记不变。
- **DoD**:全部 10 个网络 fetcher 在治理名单内;股票数据可还原任意历史 vintage 的未调价。
- **工作量/依赖**:S;无。

#### 清理批(随第一批一起,不占 Rank)
CLAUDE.md 计数刷新(endpoints 31→41、pages 37→42、portfolio 57→124、alpha 185→272+/190 定义口径、总测试 ~1047→1280+);**跟踪 AGENTS.md**(与 CLAUDE.md 逐字节一致,不跟踪必漂移);删 0B 残留 `nonexistent-paper-track.db`;`services/trading/pre_trade_risk/`(唯一被跟踪的报废壳)按 CLAUDE 决议删除或立 Phase-8 重建 issue;本地幻影空目录清扫(未跟踪,rm 即可)。

### 第二批「承重」(全 M,本计划的主线)

#### Rank #10 — 组合原语承重(三个子项)
- **为什么**:全盘最大的「已建不驱动行为」杠杆(grep 证实零接线);且 RE-2 发现肥左尾(Hill 2.4–2.7),纯方差目标结构性盲;外部评审的拥挤门 4 弱点全未修。
- **10a 拥挤门修复 + 干跑接线**:
  - 修复(先):`evaluate_crowding_gate` 现有循环(`crowding_gate.py:96-105`)内加**最大单名并行判据**(任一名 `abs_weight/gross > c` 且其拥挤度 > hi → 独立 BLOCK/WARN,不被加权平均稀释);新增可选 `adv_by_symbol=` 流动性维(退出天数 = 权重名义/ADV,超阈值 WARN)。阈值仍标 `UNCALIBRATED`(校准是 R-1)。
  - 接线(后):**驱动器侧**(方案 a,最小改动——流水线无权重变换钩子,`run_multi_period_dry_run` 只收现成 `weights_by_period`):在每期 `weights_by_period[t]` 构建后、append 前(carry `:74-78` / crowd `:48-51` / riskparity `:85-89`)调 `evaluate_crowding_gate(weights_t, global_ls_window_mean)`——拥挤证据复用 `barra_lite_diagnostics.py:137` 的 `global_ls` 滚动窗均值配方(面板里现成);BLOCK → 该周跳过调仓(权重承袭),记入 run detail。
- **10b 资本配置器承重**:`dry_run_combo_riskparity_multi.py:76` 的 `rolling_inverse_vol_weights` **换成** `allocate_capital({"carry":…, "crowd":…})`(输出同形:非负 sleeve 权重和 1);**BLOCKED → 回退现行逆波动**(fail-closed 不断跑);同时把单轮容量再分配改为**有界迭代注水**(收敛或 fail-closed,补外部评审 P1)。
- **10c 优化器尾部感知选项**:`min_variance_weights` 增可选 `cvar_scenarios=`/`cvar_penalty=`:在 `_full_obj`(`constrained_optimizer.py:71-74`)加 `ρ·CVaR_α(scenarios·w)` 惩罚项,其次梯度(最差 (1−α) 尾情景指示)并入 `grad`(`:79-81`)——**惩罚式而非完整 LP**(Dykstra 投影集不便宜地建模 T 个单侧半空间;LP 是后续第二步)。情景矩阵 = 驱动器已有的 sleeve/资产收益面板。**注意耦合**:驱动器绕过优化器,CVaR 只有随 10b 权重走优化器后才咬合。
- **tests-first**:10a 稀释用例(90% 低拥挤 + 10% 极端拥挤 → 单名判据 BLOCK 而旧加权平均 PASS)、ADV 维、fail-closed 全保留;10b BLOCK 回退等价性、注水收敛(3 book 两轮级联用例,外部评审点名)、确定性;10c CVaR 惩罚下最差尾情景权重下降的方向性用例 + 可行性重校验不回归;接线后**三条既有干跑策略数值回归对照**(不改变历史结论,新决策只对新 run 生效并带 detail 标记)。
- **DoD**:干跑 book 100% 过拥挤门(decision 落 `DryRunRun.detail`);riskparity 驱动 sleeve 权重来自 allocate_capital;优化器具尾部感知选项且被至少一条驱动路径使用。
- **工作量/依赖**:M;#8(白名单模式已立)。
- **诚实边界**:阈值未校准照实标;拥挤证据仅 `global_ls`(14 大币 OI 宇宙),carry 58 币宇宙覆盖不足时按 `min_coverage` fail-closed——不伪造覆盖。

#### Rank #11 — 冲击成本进干跑 + 参与率门同源 ADV + TCA-lite
- **为什么**:冲击校准(#6)刚建成但干跑还是每笔平 bps(`dry_run_pipeline.py:112`);容量结论(carry ~$2M / crowd ~$7M)从未到达干跑 PnL;参与率门被 `est_daily_volume=1e12` 占位废掉(`checks.py:191` vs 驱动器 `:105`)。
- **接缝与设计**:① `simulate_fills` 增可选 `impact=AlmgrenChrissImpact` + `adv_by_instrument=`,`commission = impact.total_cost_notional(fp*qty, adv)`(`impact.py:27` 纯函数,直接可当 commission);默认 None = 平 bps,**零回归**;② 驱动器把面板里现成的 `close*volume` ADV(`dry_run_carry_multi.py:58` 已载,`:83` 后别再丢)喂给 `simulate_fills` **和** `MarketContext.est_daily_volume`(`context.py:55`)——冲击成本与参与率门**同一个 ADV 真源**(与 `capacity_curve.participation_cap_aum` 口径一致);③ **trend/daily 库 fail-closed 回退平 bps**(volume 量纲 UNRELIABLE,#22 修完才解锁);④ provenance 落 `DryRunRun.detail`:`cost_model=IMPACT_AC_BAND|FLAT_BPS` + `cost_provenance=UNCALIBRATED_PRINCIPLED`(detail 原样透传 console,`console_api/main.py:5082`,无 API 改动);⑤ **TCA-lite**:对每 run 用 `impact_calibration.scenario_impact` 三情景重算成本区间,`detail` 记 `{assumed, band_lo, band_hi, in_band}`——L2 无真实成交,这是唯一站得住的「假设 vs 参照」。
- **对账恒等式**:复核确认**结构免疫**——commission 在 `reconcile` 两侧同进同出(`reconciliation.py:58,106`),逐笔变价成本不破残差 ≈0(既有测试钉 <1e-6);资金费现金流走恒等式**外**的独立通道(`dry_run_pipeline.py:235-237`),与冲击不双计。
- **tests-first**:恒等式残差在冲击成本下仍 <1e-6;flat 回退等价;ADV 同源(门与成本读同一 map);provenance 必现;TCA 区间含 assumed;trend fail-closed 用例。
- **DoD**:三条 funding 宇宙策略跑冲击带成本的新 run 并列入库;console 干跑页显示成本模型标记;参与率门在真 ADV 下有非平凡判定。
- **工作量/依赖**:M;#22 解锁 trend(可后置)。
- **诚实边界**:冲击系数仍是文献带非实测校准(标记如实);backtest 侧 `CostModel`(sqrt stub 默认关)是**独立路径,本项不碰**(避免双重口径混乱,收敛留后续)。

#### Rank #13 — 9 引擎风控门承重(诚实分级)
- **为什么**:风控覆盖不对称——pre-trade 门是活闸门,9 引擎研究门唯一调用方是 console 且喂**合成输入**(`console_api/main.py:131-134` 硬编码权重/编造收益/无 timestamps);「算了组合风险」与「组合风险能挡晋级」之间有缝。
- **接缝与设计**:① 流水线级 fail-closed 断言 `assert_research_risk_approved(...)`,**镜像 `assert_dry_run_adapter`**(`dry_run_pipeline.py:90` 先例),插在 `run_multi_period_dry_run` 曲线构建后:用**该接缝真实可得**的输入跑门——returns(`curve[].ret`)、equity(`curve[].equity`)、weights、**timestamps(periods,正好喂 #7 的真实情景重演)**;② **诚实分级(本项的灵魂)**:market/drawdown/concentration/stress(+可选 regime 用真标签器)四引擎真输入真跑;liquidity/capacity/correlation/model_alpha 在通用接缝**无真输入**——**fail-closed(passed=False)或显式 SKIPPED_NO_HONEST_INPUT,绝不喂编造数据跑出假放行**(引擎空输入已 fail-closed:`risk_modules.py:99,126,…`);每引擎结果带 provenance(stress 已有先例 `models.py:86-88`,推广到门级 report);③ 决策进 `paper_evidence` dict(`lifecycle.py:58` 已穿线),`guard_to_paper`(`guards.py:46-55`)增加 `research_risk_decision==RISK_APPROVED` 要件——晋级 PAPER_TRADING 前风控门是必要证据;④ console `_risk_decision` 顺手升真:接最新 DryRunRun 的真 returns/weights/timestamps(替换合成输入),门页 provenance 如实。
- **tests-first**:四真引擎拒绝时流水线 fail-closed;无真输入引擎不产生 PASS;timestamps 到达 stress(provenance=REAL_HISTORICAL_REPLAY);guard_to_paper 缺风控证据不放行;既有三策略回归(历史 run 不重写,新 run 带门 report)。
- **DoD**:任何新干跑 book 未过真引擎即拒;paper-track 晋级需要风控门证据;console 风控页吃真输入。
- **工作量/依赖**:M;#7(已落地)。
- **诚实边界**:liquidity/capacity/correlation/model_alpha 的真输入穿线(逐策略 ADV/容量/逐腿归因/AlphaEvaluation)是后续增量——本项**宁可少跑引擎也不伪造输入**,并把这条规则写成文档化的输入-provenance 契约(MRM 文档的一部分)。

#### Rank #22(新)— 数据质量强制 + volume 量纲修复回填
- **为什么**:质量检查「算而不 gate」(`file_ingest.py:66` 算 report 从不查 `.passed`);crypto_daily.db volume 跨币 17 个数量级(EOS $20k vs BTC $3.7Q)源头未修,压住了 trend 容量与 daily 宇宙一切 ADV 用途。
- **接缝与设计**:① `_ingest_records` 增 opt-in `strict=`:承重数据集 `quality.passed=False` → raise fail-closed(默认 False 零回归);② `adapters.normalize_records`/`market_provider` 加**每数据集量纲断言**(volume 必须以 vs_currency 计价、跨标的 log-spread 上限,接入即拒混合量纲);③ 用 `coingecko_volume_fetch` 的 REAL USD volume 路径(`:1-15`,已存在)回填重建 crypto_daily.db volume 列;④ 重建后跑 `scenario_tail_research` 复核 `data_quality` 从 `UNRELIABLE_VOLUME_UNITS` 转绿,补算 trend 容量(RE-1 遗留)。
- **tests-first**:strict 摄入对坏批次 raise;量纲断言正/负控制;回填后 spread 检查 <1e6;trend 容量快照带新 provenance。
- **DoD**:承重数据集质量强制;daily 宇宙 ADV 可用;trend 容量结论出炉(诚实标注)。
- **工作量/依赖**:M;网络仅在 tools/(回填一次性)。

### 第三批「溯源与持久」(全 M)

#### Rank #12 — 验证门端到端 provenance
- **为什么**:机构 MRM 的核心是「到达决策点的每个数字可溯源、可重算」;现状:`pbo` 裸浮点信任(`evaluator.py:82,160`)、pipeline 路径默认本地批计数(`pipeline.py:94`,catalog override 仅可选)、`reproducibility_hash` 只算不验(`registry.py:69`)、BHY/`effective_trials` 死代码(`multiple_testing.py:22-27,89-102` 无守卫引用)。
- **接缝与设计**:① 新 frozen `CatalogEvidence` 值对象(镜像 `SignedDecisionRef` 形状 + `leakage_evidence.py:25-49` 的「只有持有原始源的层能铸造裁决」纪律):`{trial_count, var_sr, observed_sr, catalog_snapshot_hash}`,**只能由** `catalog.evidence()` 铸造;evaluator/gate 改收 `CatalogEvidence`(替代裸 `deflated_sharpe`/`trial_count`),入门先重算内容哈希;`pbo` 同样走证据封套(由算 PBO 的层铸造,带输入哈希);② pipeline 路径**取消本地批默认**——catalog 源强制(与 `pipeline.py:76` 注释约定对齐,从约定变机器);③ `experiment_registry.verify_evaluation(run_id)`:重算 `reproducibility_hash`+`content_hash` 比对,fail-closed——write-once 存储补上缺的 verify 半边;`guard_to_validated` 前置调用;④ 守卫升级:`guard_to_validated` 走 **BHY**(任意依赖 FDR)+ `effective_trials` 相关性折减计数(替代 plain BH+原始计数)。
- **tests-first**:非 catalog 铸造的证据被拒(fail-closed 像 leakage None 路径);篡改已存评估任一字段 → verify_evaluation 抓到;pipeline 无 override 即拒(不再静默用本地批);BHY 守卫正/负控制;全链回归(现有晋级测试口径迁移)。
- **DoD**:每个晋级 alpha 的试验数/var_sr/DSR/PBO 可机器追溯到 catalog 且防篡改;守卫用因子动物园级校正。
- **工作量/依赖**:M;无。
- **诚实边界**:这仍是**进程内**溯源(受信代码可绕过)——出进程签名是 Phase 14;文档措辞与 `signing.py` 对齐。

#### Rank #15 — 特征注册表持久化 + PIT 特征物化
- **为什么**:泄漏检测器真承重,但 `FeatureRegistry` 是**孤儿内存 dict**(`registry.py:9`,唯一消费方 `feature_set.py:11`;console 自认「no persisted FeatureRegistry yet」渲染合成特征,`main.py:1480,1510`);滚动特征每次从原始 bar 重算(`calculation.py:21`;驱动器 `ax.evaluate` 每 run 重算全面板)。
- **接缝与设计**:① `SqliteFeatureRegistryStore` 镜像 `run_registry/store.py` 模式(lock、`CREATE TABLE IF NOT EXISTS`、`INSERT OR IGNORE` append-only 版本、店内无墙钟),facade API 不变;② 物化存储 `feature_values` **克隆 `fundamental_store` 双时点 schema**(`PRIMARY KEY(feature_id, feature_version, instrument_id, event_time, knowledge_time)` + as_of 索引;契约 `knowledge_time>=event_time` 已挡边界前视);写通道挂 `rolling_feature`/驱动器面板构建,读走 `latest_as_of`;③ **每特征版本带 `leakage_passed` 标志 + `code_version`**(镜像 run_registry `contract.py:41`)——缓存值可审计;④ console 特征/血缘页从合成升真(读新 store)。
- **tests-first**:注册表重启持久;物化读=直算逐位一致;PIT(as_of 截断不变);leakage 标志随版本;fail-closed(库损坏 raise 非空读)。
- **DoD**:特征层脱离「重启即失」;重复研究跑显著提速;console 特征页 provenance=REAL。
- **工作量/依赖**:M;无。

#### Rank #19 — 复现底座(证据指纹 + 离线重算 harness)
- **为什么**:11 个承重制品(~300× 容量高估、Hill 2.4–2.7、regime 互补、5 策略 VT 夏普榜…)全部只在 gitignored `data/cache/`,`validate.py` 对其零引用——**repo 无法自证自己引用的数字**;根库 57MB 不可入库。
- **接缝与设计**:① 提交 `evidence/*.json` 指纹:每个承重数字 = `{value, tolerance, input_db_content_hash, generator_path, code_version}`(复用 `versioning.content_hash_of`);② `tools/rebuild_snapshots.py`:按依赖序(DBs → funding 快照 → capacity/carry → dry_run.db → macro_regime → combo_book → scenario_tail → ic_panel → crypto_research → ml_stack → barra_lite)重算**全部离线叶子**(复核证实所有快照都是缓存库的确定性函数);③ 门测试 `tests/reproducibility/test_evidence_snapshots.py`:缓存缺席(CI)→ **诚实 skip**;在场 → 重算并断言 value±tol 且 input-hash 匹配;挂进 `validate.py`;④ 小制品直接入库解禁:快照 JSON(0.5–54K)、dry_run.db(276K)、paper_track.db(56K)体积安全,可选提交以让 CI 也有真数据面(`.gitignore` 精细化)。
- **tests-first**:指纹校验正/负控制(改一个数字→红);skip 路径诚实(报 SKIPPED 非 PASS);rebuild 幂等(两跑同 hash)。
- **DoD**:CLAUDE/文档引用的承重数字 100% 有指纹且可 `rebuild_snapshots.py` 一键离线复算;门禁能抓「数字漂移」。
- **工作量/依赖**:M;无。
- **诚实边界**:网络根库(fetch 层)不在指纹范围——指纹绑定「给定缓存输入下的确定性输出」,数据源本身的漂移由 provenance 与 as-of 记录。

#### Rank #24(新)— console_api 拆分 + 前端测试地板
- **为什么**:唯一运行服务是 5167 LOC 单文件(变更风险/评审不透明);前端 42 页仅 14 页冒烟、`ProvenanceRibbon` 零断言——「诚实标注」姿态缺前端侧机器保证。
- **接缝与设计**:① 按复核映射拆 8 路由组(alpha_research / backtest_risk / governance_synthetic / monitoring / data_experiments / research_runs / real_snapshots / shared payload 助手),`main.py` 只 `include_router`;**契约测试是安全网**(`test_console_api_contract.py:181-183` 断言路由集合恒等)——拆分全程它必须不动且常绿;② 前端:页注册表循环把 42 页全数冒烟;`assertConsoleSnapshot` 扩展成每 Payload 类型守卫;每页断言 ProvenanceRibbon 存在且 real/synthetic 来源正确。
- **tests-first**:先加「路由集合快照」与 42 页冒烟(红)再动刀。
- **DoD**:契约与两套既有测试零改动全绿;`npm test` 覆盖 42/42 页 + 徽标断言。
- **工作量/依赖**:M;无(与其他项并行安全)。

### 第四批「数据与天花板」(M/L)

#### Rank #14 — PIT regime 分类器
- **为什么**:平台最强分散故事(carry↔risk-on / crowd↔risk-off,corr≈−0.04)现在靠**全样本三分位**标签(`macro_regime.py:15-16` 排整段历史取阈值,自注释非 PIT)——叙事不可交易、不可测。
- **接缝与设计**:① `expanding_tercile_labels(values, min_history)`:阈值只用 `vals[:i+1]`(暖启 `<min_history` → "mid"/None);② **免估计规则优先**(最诚实):`sign_regime`(T10Y2Y 斜率符号,零估计,已 PIT)与 VIX 固定阈值 20/30;③ 信用维(HY-OAS 仅 785 点/2023-07+,复核实测)**诚实标 `NON_PIT_INSUFFICIENT`**,不硬上;④ 研究侧接 `_regime_labels`(`macro_regime_research.py:39-47`);可选执行侧:riskparity 驱动 `sc/sq` 乘 PIT regime 系数,**flag 默认关**(打开与否是独立研究决策 R-4);⑤ 周网格按现状(7 日间距)对齐,PIT 读走 `latest_as_of`(逐周 as-of 复核证实开销可忽略)。
- **tests-first**:**截断不变性**(`classifier(values[:k])[i]==classifier(values)[i]`,复刻 `test_features.py:32-36` 附加不变模式)、暖启、确定性、信用维拒绝用例。
- **DoD**:regime 桶结论有 PIT 版对照(全样本版保留作回顾性描述,两版并列诚实标注);分类器可被门/优化器合法消费。
- **工作量/依赖**:M;无。

#### Rank #23(新)— 企业行动总收益序列
- **为什么**:机构级价值/低波/股息类因子需要总收益价;现状仅拆股调整 + 现金分红计提原语(`corporate_actions.py:29-57`,自标 Phase-4),回测侧无人组装。
- **接缝与设计**:backtest_engine 内 `total_return_close(bar, actions, as_of)` = `adjusted_close`(split)⊕ `cash_dividend_total` PIT 累计(ex-date 参考价回调);纯组装,契约不动;与 #21 的原始价列配合,股票研究可选 raw/split-adj/total-return 三口径且 provenance 标注。
- **tests-first**:手算两段含分红样例;PIT(as_of 前分红不入);无行动=split 口径回归。
- **DoD**:三口径可选、可测试、标注清楚。
- **工作量/依赖**:S;#21。

#### Rank #25(新)— 跨 book 公司级汇总(read-only)
- **为什么**:5 本干跑书各自入库,无任何公司级 gross/net/因子暴露/重叠拥挤视图(外部评审点名的「book 各自不拥挤但重叠后公司级拥挤」正是历史危机模式);`capital_allocator` 本就是公司级原语但无消费方。
- **接缝与设计**:新只读工具 `tools/firm_book_report.py`:读 `dry_run.db` 各书持仓权重 + 收益流 → `allocate_capital(returns_by_book=…)` 建议 sleeve 权重 + 权重叠加后 `barra_lite_report` 总暴露 + 跨书重叠拥挤(同名净/毛权重合并后过 `evaluate_crowding_gate`);落 `firm_book_snapshot.json`(进 #19 指纹);console 一页透出。
- **tests-first**:两书重叠用例(各自 PASS 合并 BLOCK);快照确定性;无书 fail-closed。
- **DoD**:公司级视图一页可见,诊断-only 诚实标注。
- **工作量/依赖**:S–M;#10。

#### Rank #17 — 股票 + 基本面数据拓宽
- **为什么**:数据护城河是最低分维,广度是唯一杠杆;机器(双时点 store / universe_store / BYO-CSV / ALFRED 适配)已建,缺的是 vintage-correct 数据灌入。
- **接缝与设计**:① yahoo 宇宙扩容(治理收编后的 fetcher,#21),原始价列 + 幸存者标注;② **历史成分**灌 `universe_store.add_membership`(退市/纳入区间;来源:BYO-CSV 或公开历史成分表)→ 股票宇宙 survivorship-free;③ 经济发布类走 **ALFRED vintage**(restatement 安全)进 `fundamental_store`;④ 首轮研究:价值/动量/低波三族过既有关卡(IC 面板 → 证伪 → 如常)。
- **DoD**:股票研究面板 PIT + survivorship-free + 总收益口径可选;首轮因子结论(无论正负)诚实入档。
- **工作量/依赖**:L;#21、#22、#23;历史成分数据可能需用户供 CSV(NEEDS_EXTERNAL 半只脚)。

#### Rank #16 — ML 引导候选提议器(spec 先行)
- **为什么**:固定词表是信号生成天花板;但既有证据(GBM/Ridge stacker 无可交易 edge)要求诚实预期——定位为**扩大生成搜索空间**,验证严谨性不变。
- **设计要点**:提议器在门外(tools/ 或 research/ml 隔离),产物 = DSL 表达式候选;**必须过同一个 fail-closed 验证门 + #12 的 CatalogEvidence**;试验计数如实入 catalog(多重检验账本吃下提议器的全部尝试——这是机构级与「调参到过门」的分水岭)。先 spec + 对抗评审,再排实现。
- **工作量/依赖**:L;#12 先行。

### 持续轨道(P2,随迭代穿插)

#### Rank #26(新)— 观测性升真
`monitoring/health.py::build_snapshot`(真 KillSwitch/审计链/registry 计数)接 `system-health` 端点替换硬编码合成块(`main.py:587-612`);两个 FastAPI 服务加最小 JSON 请求日志(req-id/route/status/latency)。S。

#### Rank #27(新)— 事故账本 + kill-switch 演练
append-only sqlite 事故日志(镜像 run_registry 模式)+ console 事故页从 SYNTHETIC 升真(真行,可空);kill-switch 演练 runbook(docs/)+ 端到端演练测试(arm → 全拒 → 多方 grant 复位 → 审计链留痕,全链路一个测试跑通)。真 RBAC/评审身份/委员会签名投票 = **POST_L2**(需身份层与出进程签名),页面保持诚实蓝图标注。S。

#### Rank #28(新)— CI/环境机构化
lockfile(uv.lock 或 pip-compile)+ `.python-version` 钉 3.12(对齐 CI;现状 pyproject ≥3.11 / CI 3.12 / 本地 3.13 三版漂移,numpy/sklearn ABI 敏感);pytest-cov + `vitest --coverage` 阈值;`pip-audit`/`npm audit`;Playwright 冒烟与制品 SBOM/签名后置。M。Docker/Nix 全锁定 = 更后(旧 #10 的 L 部分)。

---

## 4. 排期(双周迭代 × 4 + 持续轨道)

| 迭代 | 主题 | 内容 | 里程碑 DoD |
|---|---|---|---|
| **S1 守卫与留痕** | 便宜、安全关键、全 S | #8 → #20 → #18 → #21 + 清理批 | 治理测试网收口:裸读/决策留痕/身份/新 fetcher 全机器强制;`validate.py` 常绿 |
| **S2 承重** | 本计划的心脏,全 M | #10(a→b→c)→ #11 → #13 → #22 | 干跑链路「门-成本-配置」全真:权重过拥挤门、成本带冲击 provenance、风控门 fail-closed 挡晋级、质量强制 |
| **S3 溯源与持久** | 证据链闭环 | #12 → #15 → #19 → #24 | 晋级证据可溯源防篡改;特征持久;承重数字可一键重算;console 拆分 + 前端 42/42 |
| **S4 数据与天花板** | 广度与下一层 | #14 → #23 → #25 → #17(数据到位程度定深度)→ #16 spec | PIT regime 落地;股票三口径;公司级视图;ML 提议器 spec 过对抗评审 |
| **持续** | 穿插 | #26、#27、#28;R-1/R-2 研究任务 | 观测性真源;演练测试常绿;CI 有 coverage/审计 |

**依赖链提醒**:#20→#18(审计沉淀先行);#21→#23→#17(股票数据递进);#22 解锁 #11 的 trend 分支与 RE-1 遗留 trend 容量;#12 先于 #16(提议器的试验计数纪律);#10 先于 #25(公司级复用门与配置器)。每迭代结束:更新两份路线图文档标 ✓ 附提交哈希 + 刷新 `PROJECT_STATE.md`(既定节奏)。

---

## 5. 可测量 KPI(机构级完成判据,全部可机器验证)

- **数据平台**:研究路径裸 `fetch_bars` = 0(治理测试);承重数据集 `strict` 摄入 100%;跨币 volume log-spread 断言过;股票库任意日期可还原未调价。
- **研究/MRM**:晋级 alpha 100% 携带 catalog 铸造的 `CatalogEvidence`;`verify_evaluation` 篡改检出率 100%(测试);晋级守卫用 BHY + effective_trials;每个物化特征值带 `leakage_passed`+`code_version`。
- **组合/风险**:新干跑 run 100% 带拥挤门 decision 与风控门 report(fail-closed);优化器尾部感知选项被 ≥1 条驱动路径消费;公司级汇总页对 5 本书可见。
- **执行现实**:干跑成本 100% 带 `cost_model` provenance;参与率门 `est_daily_volume` 占位符 = 0;每 run 有 TCA 区间记录。
- **治理运营**:kill-switch/部署决策 100% 落哈希链且可跨会话验证;POST /run 100% 带 operator;system-health 合成硬编码 = 0;演练测试在门禁内。
- **基建复现**:文档引用的承重数字 100% 有 `evidence/` 指纹且 `rebuild_snapshots.py` 离线复算一致;CI coverage 阈值生效;解释器/依赖锁定单一化。

---

## 6. 研究任务(非代码交付,与开发并行)

- **R-1 拥挤阈值实证校准**:用历史拥挤驱动型回撤窗口(LUNA/FTX 已在 scenario 库)校准 `threshold/warn_threshold`,出校准记录文档;校准前保持 `UNCALIBRATED` 标注。
- **R-2 quality_multiplier 来源**:用 DSR/var_sr/paper-track 证据系统化产出 book 质量分(候选:`AlphaEvaluation` 聚合),喂 `allocate_capital`;来源未定前该参数保持默认 1.0(不引入主观旋钮)。
- **R-3 crowd_contra 前向 paper-track 决策**(旧#12 收编):8 项证伪 + 干跑 VT 1.38 已备齐,是否送前向纸面跟踪是独立人工决策——材料已齐,等拍板。
- **R-4 regime 条件 sleeve 缩放开关**:#14 落地后,用 PIT 标签重跑 riskparity 对照(开/关两版),决定是否让 regime 进执行权重(默认关)。

---

## 7. 显式不做(POST_L2 / 需外部资源)

**安全边界本身(补 = 破边界,不该补)**:实盘/券商/下单/AI 直接交易;真实资金路径;SOR/场所/最优执行;毫秒级实时风控;真实成交 TCA 闭环;监管报送;做市;进程物理隔离实现(设计已备 `PHASE_14_PROCESS_ISOLATION.md`);**真 RBAC/身份鉴权、评审者实名工作流、委员会签名投票**(需身份层 + 出进程签名——页面保持诚实蓝图标注,#18 的 operator 是自述式标签不越界)。

**需外部资源**:厂商授权数据(CRSP/Compustat/TAQ/Bloomberg)、tick/L2 订单簿、真正量级另类数据、分布式/GPU、专职团队——同晨间审计 §5,不赘述。

---

## 8. 执行纪律与治理声明

每一项:tests-first → 最小安全改动 → `python scripts/validate.py` 绿 → 中文提交 → 大项对抗式评审(P0/P1 必修)→ 路线图/`PROJECT_STATE.md` 标 ✓ 附哈希。**全程不可让**:L2 五不变量、纯 stdlib services(numpy/sklearn 仅限已隔离的 ml/ 且非网络)、确定性(无 RNG/墙钟入核)、fail-closed、诚实标注(不知道 ≠ 安全;没跑 ≠ 通过;合成必须标合成)。本计划所有项均 `L2_SAFE_NOW`(#17 半只脚 NEEDS_EXTERNAL:历史成分/ALFRED 数据到位程度决定深度)。

---
*基于 2026-07-03 晨间 16 代理审计 + 同日 7 路专项复核(数据/组合/执行/验证风控/regime特征/治理运维/复现)。所有 file:line 证据来自真实代码核查,非文档复述。随开发推进更新。*
