# 进化搜索循环(Agent 进化搜索 子项 2)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/intelligence/`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、确定性、失败关闭、agent 容器化不变。

## 目标

让 Agent 通过**遗传式进化**机器发现 alpha:从原子假设播种,逐代选择高奖励亲本,经**变异**
(扰动参数)与**交叉**(两亲本组成 2 腿交互,复用子项 1)产生新基因型,经**同一受控 PROPOSE
路径**评估,精英保留。这把搜索从"固定网格选臂"升级为"创造网格外的新复合因子"。

## 关键约束

- 纯 Python stdlib;**确定性**(无 RNG,变异/交叉/选择全枚举+排序);失败关闭。
- **容器化(神圣)**:所有评估经未改的 `ResearchAgentLoop`(PROPOSE);agent 永不可达 APPROVED。
- 复用 `evaluation_reward`、`interaction_definition`/`make_interaction_provider`(子项1)、
  价格/基本面 provider、`AlphaCatalog`(多重检验计数)。
- 纯加法;不改前端/后端合约;不改既有搜索。standalone,接入 session 留作后续。

## 非目标(YAGNI)

- 不做随机变异/锦标赛随机抽样(确定性枚举即可,且可复现)。
- 不做 3+ 腿、不做跨代基因型持久化(本子项不持久化进化态)。
- 不接入 `run_research_session`(后续)。

## 架构(新建 `services/intelligence/evolutionary_search.py`)

### 基因型(JSON 安全嵌套元组,可哈希用于去重)
- 原子价格 `("P", operator, window)`;原子基本面 `("F", metric, mode)`;
  交互 `("X", legA, legB, combine)`,`legA/legB` 为 `("price",op,win)` / `("fund",metric,mode)`。

### `genome_to_definition(genome) -> AlphaDefinition`
- `P` → `alpha_id=f"h_{op}_{window}"`, `factor_ids=("close",)`, `params={"operator","window"}`(价格 provider 读)。
- `F` → `alpha_id=f"hf_{metric}_{mode}"`, `factor_ids=(metric,)`, `params={"metric","mode","family":"fundamental"}`。
- `X` → `interaction_definition(legA, legB, combine)`(子项1)。

### 主 provider `make_evolution_provider(price_store, fund_store, instruments, *, event_times, fwd_horizon, universe)`
内建价格/基本面/交互三子 provider,`provider(defn)` 按 `defn.params` 派发:
`"family"=="interaction"`→交互;否则 `"metric" in params`→基本面;否则价格。

### 遗传算子(确定性)
- `mutate(genome, vocab) -> list`:
  - `P`:换 operator(`vocab.operators` 其余)∪ 换 window(`vocab.windows` 其余)。
  - `F`:换 mode ∪ 换 metric。
  - `X`:换 combine ∪ 变异 legA ∪ 变异 legB(每腿按其类型扰动一个分量)。
  - 返回去重、排序的邻域基因型。
- `crossover(g1, g2, vocab) -> list`:仅当 g1、g2 均原子 → 取各自原子腿 `("price"/"fund",…)`,
  对每个 `combine∈vocab.combines` 产 `("X", legA, legB, combine)`。
- `vocab = {operators, windows, metrics, modes, combines}`(调用方提供;默认取既有算子/窗口 + 基本面 metric/mode + sum/product)。

### `EvolutionarySearchLoop(provider, *, inner_loop=None, vocab)`
`run(seed_genomes, *, generations, population_size, elite_k, timestamp=0) -> dict`:
1. 评估种群(截断到 `population_size`):每基因型→`genome_to_definition`→`provider(defn)`,
   **per-genome provider 错误隔离**(异常→reward 0,记谱系,继续,同 guided_search);
   `inner_loop.run(candidates)` 一次;`evaluation_reward` 打分;`evaluated[genome]=reward`。
2. 每代(共 `generations`):`assert_action_allowed(actor, PROPOSE)`;选 `elite_k` 精英
   (`(reward 降序, genome 升序)`);繁殖 = `mutate(精英)+crossover(精英对)`,**去重剔已评估**,
   封顶 `population_size`;受控评估后代;新种群 = `(精英∪后代)` 取 reward 前 `population_size`。
3. 返回 `{best:[(genome,reward)…降序], history:逐代评估谱系, report:聚合, n_evaluated}`。

## 多重检验诚实(关键)

每个不同基因型 → 不同 `AlphaDefinition`(distinct `definition_hash`)→ `AlphaCatalog` 计为
**一次试验**。进化**放大试验数**,而 Deflated-Sharpe / var_sr / BH-FDR **随之加重惩罚**——机器
多搜了,统计门槛就更高。这是平台不被"进化过拟合"自欺的诚实保证(已有机制,本子项天然受益)。

## 错误处理 / 失败关闭

- provider/评估异常 → 该基因型 reward 0,继续(隔离)。
- 空 `seed_genomes` 或 `generations<0` 或 `population_size<1` 或 `elite_k<1` → `ValueError`。
- `genome_to_definition` 未知基因型 tag → `ValueError`。

## 确定性

无 RNG;`mutate`/`crossover` 枚举后排序去重;选择 `(−reward, genome)` 排序;同输入同输出。

## 测试矩阵(`tests/intelligence/test_evolutionary_search.py`)

1. `genome_to_definition` 三类 → 正确 defn(alpha_id/params/factor_ids)。
2. `mutate`:P/F/X 各产合法去重邻域;确定性。
3. `crossover`:两原子 → 交互基因型(各取一腿 × combines);非原子亲本不交叉。
4. 主 provider 按基因型派发到正确构建器(价格/基本面/交互各产周期)。
5. `run`:播种→跑数代→返回 best(降序);report 非空;`n_evaluated` 随代增长;**确定性**(两次同结果)。
6. **容器化**:跑后 catalog 无 APPROVED;PROPOSE guardrail 再测;非人类 actor。
7. **多重检验**:不同基因型各计一次试验,`catalog.global_trial_count` 随评估数增长。
8. provider 错误隔离:某基因型 provider 抛 → reward 0,循环继续、其余正常。

## 涉及文件

- 新建 `services/intelligence/evolutionary_search.py` —— 基因型映射、`mutate`/`crossover`、
  `make_evolution_provider`、`EvolutionarySearchLoop`。
- 新建 `tests/intelligence/test_evolutionary_search.py`。
- 复用不动:`interaction_factors`(子项1)、`panel_builder`/`fundamental_factors` provider、
  `ResearchAgentLoop`、`reward`、`guardrails`、`AlphaCatalog`。

## 安全审查

- 容器化:进化仅多产生 PROPOSE 候选,经同一受控评估;无新权限、无 APPROVED 路径、无凭证面。
- 多重检验诚实:试验放大被 DSR/var_sr/BH-FDR 正确惩罚。
- 纯加法、确定性;不改契约/前端/后端合约门。
