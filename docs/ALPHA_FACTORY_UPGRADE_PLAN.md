# Alpha Factory 机构级补全升级 · Phase 0 审计 + Gap Report + Implementation Plan

> 本文档是「Alpha 因子工厂 → Alpha 资产生命周期系统」升级的 **Phase 0 交付物**:仓库审计、能力核验、
> Gap Report、分阶段实施计划。**审计由 6 个只读智能体逐子系统对真实文件核验**(全部有文件路径+符号证据)。
> 叙述中文;代码标识符英文。**结论:67 项能力 → 22 已有 / 35 部分 / 10 缺失。**
>
> **遵守原则**:先适配后扩展(规则 #9);纯 stdlib/确定性/失败关闭/容器化不变;所有交易能力默认 MOCK/PAPER;
> 不删现有功能;每个 Phase 可测试/可回滚/可审计;每 Phase 完成出验收报告;新增前列计划、改后跑门禁。

---

## 0. 与 prompt 的架构冲突(按规则 #9 适配)

| prompt 假设 | 现实(审计实证) | 适配决定 |
|---|---|---|
| 类式 AST(FeatureNode/ConstantNode/…/NeutralizationNode) | 两套**元组** DSL:`alpha_expression`(截面面板)+ `gp_tree`(单标的标量) | **扩展元组 DSL**,新增 group/neutralization 节点为新 head;**不重写成类** |
| pandas/numpy/polars 向量化执行 | 纯 Python list-of-lists 解释器(刻意,为确定性 + 安全约束) | **保持纯 stdlib**;加节点级 memo/CSE 提速。numpy=破确定性的**口子**,需用户授权 |
| 9 态含 small_live/production/live | 7 态,终态 `APPROVED`(L2 不碰实盘) | 可加 **shadow/degraded/paused**(研究态);**live 类状态 = L3 治理门 + 人工专属,不建** |
| industry/size/style 中性化 | 仅 market-demean;**无行业/板块/风格成员数据** | 需 **数据口子**(行业映射);先做 market/size(若有市值)中性,行业留接口 |
| LLM Research Agent 生成假设/DSL | 无 LLM;确定性 UCB/GP/MCTS 搜索 | LLM=**口子**(门外生成+门内裁判);Research Memory/Brief 可**不依赖 LLM** 先建 |

---

## 1. 模块清单(现有真实代码)

| 类别 | 文件 |
|---|---|
| 因子表达式(截面 DSL) | `services/research/alpha_expression.py`(元组树 + ts/cs/ew 算子 + evaluate/serialize/canonicalize/alpha_id) |
| 因子表达式(单标的 GP) | `services/research/gp_tree.py` + `gp_genome.py`(eval/serialize/parse/canonicalize/codec/确定性变异) |
| 量产基因组(截面) | `services/research/cs_genome.py`(确定性 mutate/crossover/seed,深度≤6,cs 根不变量) |
| 算子库 | `signals.py`(8 价格)、`volume_signals.py`(4 量能)、`alpha_expression` 内置 ts/cs/ew |
| 生成器/搜索 | `services/intelligence/{evolutionary_search,mcts_search,guided_search,search_space}.py`、`agent_loop.py`、`guardrails.py` |
| 回测 | `services/alpha/panel_backtest.py`、`services/research/backtest_engine/{engine,costs,metrics,...}` |
| 验证 | `services/research/validation/{walk_forward,deflated_sharpe,gate,pipeline}.py`、`services/alpha/{multiple_testing,walk_forward}.py`、`services/features/leakage.py` |
| Catalog/生命周期 | `services/alpha/{catalog,factory,lifecycle,config}.py`、`quantos/contracts/alpha.py`(AlphaDefinition)、`services/research/experiment_registry/` |
| 排序/去冗余 | `services/alpha/ranker.py`、`services/research/factor_set.py`、`subtree_stats.py`、相关/聚类/正交模块 |
| 组合/风险 | `services/portfolio/{construction,risk_model,optimizer,attribution}.py`、`services/risk/research_risk/`(9 引擎)、`capacity`、`crowding` |
| Paper Trading | `services/research/paper_trading/`(模型真实,PnL 占位) |
| 前端 | `apps/research-console/src/`(26 页,mock-first) |
| API | `services/research/console_api/main.py`(只读快照) |
| 测试 | `tests/`(104 文件 / 741 test_ 函数,按子系统);门禁 `scripts/validate.py`;CI `.github/workflows/validate.yml` |

---

## 2. 能力核验清单(逐项,带证据)

✅=已有 · 🟡=部分 · ❌=缺失

| 能力 | 状态 | 证据 / 缺口 |
|---|---|---|
| Expression Tree(节点类型) | 🟡 | 元组树非类;**缺 GroupOp/NeutralizationNode 节点** |
| Alpha DSL | 🟡 | 两套不相交元组 DSL(截面 + 单标的) |
| Operator Registry | 🟡 | ts 10/10 ✅;cs **3/7**(缺 winsorize/neutralize/group/industry);pairwise **1/4**(仅 corr,且为时序);math **4/7**(缺 sqrt/clip/where) |
| Type Checker | ❌ | 只有 arity/grammar 校验,无操作数类型检查 |
| Dimension Checker | ❌ | 无形状/量纲检查,错配运行时 IndexError |
| Complexity Scorer | 🟡 | 仅 gp_tree 的 size/depth parsimony;无算子加权评分 |
| Vectorized Executor + 缓存 | 🟡 | **非向量化**(纯 Python);"缓存"是数据层 bar 缓存,**无节点级 CSE/memo** |
| Walk-forward | ✅ | `walk_forward.py` 两实现,门禁硬 FAIL |
| Purged/Embargo CV | 🟡 | embargo 为**整数索引带**,**未从 label horizon 推导**,无 pct/日历 |
| CPCV(组合purged,路径分布) | ❌ | 仅 `pbo_cscv`(给 PBO 标量),无 OOS 路径分布 |
| Deflated Sharpe | ✅ | 完整 LdP DSR(N 试验 + 偏度峰度修正),var_sr 强制 |
| PBO / CSCV | ✅ | logit-rank,门禁硬 FAIL(max_pbo=0.5) |
| Multiple Testing | 🟡 | **BH-FDR 已强制** ✅;**缺 Bonferroni、Harvey-Liu-Zhu t 阈** |
| Leakage 检测 | 🟡 | 检测器强,但**未接进门禁**(gate 把 leakage_ok 当可信入参) ⚠️ |
| Validation Report | 🟡 | raw/corrected/reasons/4 路裁决有;**缺逐 fold OOS 分布** |
| Alpha Correlation Graph | 🟡 | 隐式 pairwise dict + clusters,**无显式 nodes/edges 图对象** |
| Alpha Dedup | ✅ | canonical hash + cluster 代表去重 |
| Alpha Population Optimizer | 🟡 | 贪心增量 IR;**无集合级联合优化/进化集合选择** |
| Signal-to-Portfolio | 🟡 | 归一化/构建有;**无行业/风格中性化** |
| Cost Model | ✅ | 佣金/滑点/sqrt 冲击/换手;spread 并入滑点、impact 默认关 |
| Capacity Model | ✅ | ADV 参与/流动性/冲击缩放/max-capital |
| Lifecycle Monitor | 🟡 | 7 态 + 审计日志强;**缺 live/shadow 衰减监控** |
| Paper/Shadow/Live Feedback | 🟡 | 仅 paper→decay;**无 shadow/live 轨** |
| Research Memory | 🟡 | 实验账本(result/verdict/dataset_version/repro hash);**缺假设/代码版本/血缘/Brief** |
| 容器化(非人类永不 APPROVED) | ✅ | 运行时分层强制(状态机 actor + 能力策略 + 无 approve 路径) |
| LLM Research Agent | ❌ | 无 LLM;确定性搜索 |
| Rich Alpha Asset(~50 字段) | ❌ | 仅瘦 AlphaDefinition(id/name/factor_ids/universe/params) |
| Alpha Lineage(父子/变异/性能 delta) | ❌ | 搜索内有父子变异,但**未写入注册表** |
| 前端:DSL Explorer / Alpha Graph / Optimizer / Signal-to-Portfolio 页 | 🟡 | 部分缺失或只读表格;无 DAG/可编辑 |
| 前端测试 | 🟡 | **近零 UI 测试**(vite.config 无 vitest test 块) |

---

## 3. Gap Report

### 3.1 已完成(可直接依赖的地基,22 项)
Walk-forward、Deflated Sharpe、PBO/CSCV、canonical-hash 去重、生命周期状态机 + 防篡改审计日志、容器化、
收益相关相似度、多样性(MMR+聚类)、容量模型、成本模型(核心)、组合构建(核心)、BH-FDR 强制、
ts 算子全覆盖、门禁/CI/契约测试/typed client。

### 3.2 部分完成(35 项,需"补齐而非新建")
算子缺口(cs/pairwise/math)、purged-CV 未绑 label horizon、多重检验缺 Bonferroni/HLZ、
**leakage 未接门禁**、validation report 缺 fold 分布、瘦注册表、无图对象、优化器仅贪心、
中性化仅 market、cost 把 spread 并入滑点、生命周期缺 live 态、衰减监控靠手喂、experiment memory 缺字段、
前端 4 页缺/弱、前端 UI 测试近零。

### 3.3 缺失(10 项)
CPCV、Type Checker、Dimension Checker、holdings/exposure 相似度、Alpha Lineage、Rich Alpha Asset、
industry/sector/style 中性化(+ 无板块数据)、进化式种群优化器、LLM Agent + 自动 Research Brief、
live/shadow 衰减监控。

### 3.4 风险点(机构级视角)
- ⚠️ **leakage 检测器未接进门禁** —— 真实可修的安全缺口(门禁信任调用方传入的 leakage_ok)。**优先修。**
- ⚠️ **embargo 与 label horizon 解耦** —— 三重门 max_hold 没自动喂给 purge/embargo,重叠泄漏靠人工设参。
- ⚠️ **市场冲击默认关**(impact_coef=0)—— 多数回测静默低估成本;spread 并入滑点无半价差模型。
- ⚠️ **两套 DSL/hash 并存**(ax_/hgp_)—— 试验计数/去重有漂移风险。
- ⚠️ **前端 UI 测试近零** —— jsdom/testing-library 已装但 vite.config 无 test 块。
- ⚠️ **加密无行业/板块数据** —— industry/style 中性化缺数据基。
- **口子(需用户拍板,我不擅自开)**:numpy 向量化(破确定性)、LLM Agent(破纯确定性)、live/small_live 状态(破 L2 实盘红线)、行业数据(成本/来源)。

### 3.5 推荐补齐顺序(适配现有架构 + 你的 Phase 序 + 现实优先级)
**先插队一个 P0 安全修复**(把 leakage 接进门禁 + embargo 绑 label horizon),再按你的 Phase 1→8。
理由:这两条是"机构级防伪"地基,且是真实风险,成本极小、价值最高。

---

## 4. Implementation Plan(分 Phase · 文件级 · 测试 · 验收)

> 通则:每 Phase = domain model + service + evaluator + tests + spec doc(+ API 契约/前端 mock 按需);
> tests-first;改后 `python scripts/validate.py` 绿 + 前端 typecheck/build;完成出验收报告。

### P0(插队 · 安全地基 · ~小)
- **目标**:把已有 leakage 检测接进 gate/evaluator(不再信任入参);让 purge/embargo 从 labeling 的 max_hold 自动推导。
- **文件**:`services/research/validation/gate.py`、`services/alpha/evaluator.py`、`services/research/labeling.py`(导出 horizon)、`services/alpha/walk_forward.py`。
- **测试**:label-overlap 泄漏场景(已有 leakage 测试扩展)、embargo=horizon-1 自动绑定回归测试。
- **验收**:泄漏因子被门禁拒;embargo 不再需手设。

### Phase 1 — Alpha DSL + Operator Registry(扩展元组 DSL)
- **适配**:扩展 `alpha_expression` 元组语法,**不引入类**。统一/桥接两套 DSL 的 canonical/hash(降漂移)。
- **文件**:`alpha_expression.py`(+ 算子:winsorize/sqrt/clip/where/cov/beta/residualize;+ group/neutralize 节点 head)、
  新 `services/research/dsl_checker.py`(Type Checker + Dimension Checker:校验时序/截面/分组算子合法性、防 log(负)/corr(类别,价))、
  新 `services/research/complexity.py`(算子加权复杂度评分)、`alpha_expression.evaluate`(加节点 memo/CSE,纯 stdlib)。
- **口子标注**:industry_neutralize/group 需板块数据 → 先留节点 + 接口,数据到位再启用。numpy 向量化=口子,不做。
- **测试**:合法/非法/边界表达式;type/dimension 拒绝;canonical 去重;memo 输出与无 memo 逐位一致。
- **验收**:所有表达式可 parse/validate/execute;非法明确拒绝;同义可 canonicalize;重复可 hash 检测;不破现有引擎。
- **文档**:`docs/ALPHA_DSL_SPEC.md`。

### Phase 2 — Scientific Validation Gate
- **适配**:在现有 walk_forward/DSR/PBO/BH-FDR 上**补**,不替换。
- **文件**:新 `services/research/validation/cpcv.py`(组合 purged CV → OOS 路径分布 + Sharpe/IC/IR 分布)、
  `multiple_testing.py`(+ Bonferroni + Harvey-Liu-Zhu t 阈)、`validation/gate.py` + `pipeline.py`(整合 + 逐 fold 分布写入报告)、
  `quantos/contracts/`(ValidationReport + fold 分布字段)。
- **测试**:label-overlap 泄漏单测、多重检验膨胀单测(搜索越多门槛越严)、CPCV 路径分布、普通 WF vs purged 并列。
- **验收**:每个 Alpha 验证报告可追溯;明显过拟合被拒;自动生成 Alpha 必须过 Gate 才入 Zoo。
- **文档**:`docs/VALIDATION_GATE_SPEC.md`。

### Phase 3 — Alpha Zoo / Registry / Graph
- **适配**:在 AlphaDefinition + experiment_registry + catalog 上**包一层 Alpha Asset**,不动现有契约语义。
- **文件**:新 `quantos/contracts/alpha_asset.py`(Rich Alpha Asset,~50 字段,frozen)、
  新 `services/alpha/registry.py`(version/lineage/owner/generator/status/audit)、
  新 `services/alpha/zoo.py`(search/filter by universe/generator/cluster/status)、
  新 `services/alpha/similarity.py`(AST tree-edit + holdings overlap + exposure)、
  新 `services/alpha/alpha_graph.py`(显式 nodes/edges/clusters/near-dup);搜索循环写 lineage(parent/child/mutation/perf-delta)。
- **测试**:资产档案完整、重复可检、高相关可聚类、新 Alpha 入库前 similarity check、状态流转有审计。
- **验收**:每 Alpha 有完整档案 + 血缘;入库前 similarity check;状态流转审计。
- **文档**:`docs/ALPHA_ZOO_SPEC.md`。

### Phase 4 — Alpha Population Optimizer
- **适配**:把 `factor_set` 贪心升级为**集合级联合优化**(保留贪心为 baseline)。
- **文件**:新 `services/alpha/population_optimizer.py`(set 评估 + 边际贡献含 drawdown + 多样性目标 + greedy/corr-constrained/MV/risk-budget/可选进化)、目标函数(post-cost IR + 边际 + regime 稳定 + 容量 + 多样性 − 相关 − 换手 − 回撤 − 复杂度 − crowding − 算力)。
- **测试**:高 IC 高重复被降权/排除;低 IC 低相关有边际贡献被选中;选择可复现;组合报告完整。
- **验收**:不再只按 IC 选;输出 Alpha Portfolio(selected/weights/excluded/reasons/cluster/expected IC/turnover/capacity)。
- **文档**:`docs/POPULATION_OPTIMIZER_SPEC.md`。

### Phase 5 — Signal-to-Portfolio Adapter
- **适配**:复用 `portfolio/construction` + `costs` + `capacity`;补归一化/中性化/混合。
- **文件**:新 `services/portfolio/signal_adapter.py`(rank/zscore/**winsorize**/missing/stale → neutralize(market/size,industry 留口子)→ blend(static/dynamic/regime/recent-perf)→ 风险模型 → 成本(+ 拆分 spread)→ 容量 → 构建(含约束)→ 报告)。
- **测试**:score→weights 稳定;约束违规有原因;成本后收益入评;容量不足不许高权重;可回放复验。
- **验收**:目标权重/持仓/换手/成本/容量/约束违规/被拒信号/构建报告齐全。
- **文档**:`docs/SIGNAL_TO_PORTFOLIO_SPEC.md`。

### Phase 6 — Lifecycle Monitor / Feedback
- **适配**:扩状态机加 **shadow/degraded/paused**(研究/dry-run 安全态);**live/small_live/production 不建(L3 口子)**。
- **文件**:`services/alpha/lifecycle.py`(+ 状态 + 晋升门)、新 `services/alpha/degradation_monitor.py`(live-vs-backtest IC/return/slippage/turnover 比对、drawdown/capacity 告警、变异/退役建议)、对接 paper_trading 遥测。
- **测试**:Alpha 不许无状态裸跑;状态变更有原因;偏差可记录;衰减可识别;周度健康报告。
- **验收**:同上 + 每周 Alpha 健康报告(MOCK/PAPER 数据)。
- **文档**:`docs/LIFECYCLE_SPEC.md`。

### Phase 7 — Research Memory(+ LLM 口子)
- **适配**:Research Memory **不依赖 LLM** 先建;LLM 接法=门外生成 + 门内裁判,**口子,需用户授权**。
- **文件**:`services/research/experiment_registry/`(+ hypothesis/code-version(git sha)/lineage refs/regime/next-action)、新 `services/research/research_brief.py`(从注册表确定性合成 brief:tested/worked/failed/why/next/redundant/mutation 建议)。
- **测试**:Agent 输出可追溯;生成 Alpha 过同门禁;Research Memory 可检索;失败实验入库不丢。
- **验收**:同上;Research Brief 自动生成(确定性,无 LLM 也可)。

### Phase 8 — Frontend Alpha Factory Console
- **适配**:在现有 26 页 + 契约两侧守护上**加页**,不破现有 UI;先修 vitest 配置补 UI 测试。
- **文件**:`apps/research-console/src/pages/`(+ DSL Explorer / Validation Gate 详情 / Alpha Zoo 可筛 / Alpha Graph(DAG)/ Population Optimizer / Signal-to-Portfolio / Lifecycle Monitor)、`lib/types` + `lib/api`(+ 类型 + client)、`console_api/main.py`(+ 只读端点)、`test_console_api_contract.py`(+ 契约键)、`vite.config.ts`(+ vitest test 块 + 组件测试)。
- **风格**:机构级深色、高密度、类 Bloomberg/Grafana、语义状态色、表格优先、图服务决策。
- **测试**:MOCK_MODE 可跑;每页 mock + loading/error/empty;typed client;npm typecheck/build 过。
- **验收**:同上。

---

## 5. 全局验收命令(每 Phase 后)
```bash
python scripts/validate.py                       # 后端门禁(治理扫描 + 全 pytest)
cd apps/research-console && npm run typecheck && npm run build && npm test   # 前端(改前端时)
```

## 6. 不变量(全程不破)
纯 stdlib / 确定性 / 失败关闭 / 容器化(非人类永不 APPROVED)/ 网络只在 tools/ / 无券商 SDK / 无下单 /
默认 MOCK·PAPER / 不删现有功能 / 每 Phase 可测试·可回滚·可审计 / 契约改动两侧同提交。
**实盘 / live 状态 / numpy / LLM / 行业数据 = 口子,人工授权专属,我不擅自开。**
