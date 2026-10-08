# Alpha Factory Phase 5 设计与 P 系列切片计划(727 §7 / §8)

- 日期:2026-07-27
- 依据:[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §7(TrialRecord / Trial
  Ledger 规则 / 家族级多重检验)、§8(AI Alpha Factory:§8.1 可证伪假设 /
  §8.2 受限 DSL / §8.3 分层漏斗 / §8.4 多模型分工)
- 状态:**P5-1..P5-3 全部落地,Phase 5 完成**——P5-1:Hypothesis 核心 +
  Hypothesis Store + TrialProvenance 接线进两个 canonical 搜索入口(27 测试);
  P5-2:candidate_compiler + constrained_search_space + candidate_deduplicator +
  search_controller(§8.2/§8.3 漏斗前四层,30 测试);P5-3(本提交):challenger +
  challenge_council(§8.4 Deterministic Judge)+ champion_challenger(§10 第一刀,
  26 测试,合计 83)。727 Phase 5 验收对照见 §6。
- readiness:L2_DRY_RUN_ONLY 不变。本方案不改五个安全旗标,不新增任何真实下单
  路径,不触碰凭证,`services/` 内零网络 import、零 LLM 客户端;注册一个假设
  或一条 TrialRecord 不改变任何评估门禁、mandate 或 live 路径(诊断非门禁)。

## 1. 问题:假设只是字符串,TrialRecord 字段齐但零接线

727 §7/§8 要求「每一次尝试都有完整 TrialRecord」且「AI 先生成可证伪假设」。
今天的现实:

1. **hypothesis 只有字符串**。W10 研究预算(`research_budget.py`)把
   `parent_hypothesis` 作为一段自由文本随 run artifact 落盘——无结构、无
   证伪条件、无经济机制校验。「禁止只有 Sharpe 可能更高的候选」(§8.1)
   今天没有任何机器承载。
2. **TrialProvenance 字段齐但零接线**。`services/research/trial_provenance.py`
   的 frozen dataclass + entry_hash 链早已实现 §7.1 全部字段,
   `experiment_registry` 的 `record_trial_provenance`(append-only 表 +
   哈希链 + `trial_provenance_chain_valid()`)也早已落地——**但没有任何
   驱动器调用它**。治理证据 `evidence/trial_ledger_integrity.json` 自我申报
   PARTIAL:「drivers still register bare config hashes」,生产账本 2533 条
   trial 全部 bare。
3. **§8.4 四个 LLM 角色全缺**。Proposer / Implementation Auditor /
   Statistical Challenger / Adversarial Reviewer 无任何代码面;
   Deterministic Judge 角色约九成已由既有机器承载(evaluator 机读证据 →
   固定政策 verdict、晋升门、BH-FDR ranker),缺的恰是「LLM 产出如何进入、
   谁能做最终状态转换」的边界治理。
4. **§8.3 分层漏斗只在文档里**。现有搜索入口(cs_genome 进化 /
   registered batch)有注册与评估,但语义检查、数据可用性、PIT/lag、
   逐层淘汰原因记录(ELIMINATED_* 漏斗阶段)未按漏斗组织。

## 2. 设计裁定

1. **新包 `services/alpha_factory/`,不塞进 `services/research` 平铺层**。
   727 §8 直接点名这个包路径(hypothesis.py / hypothesis_store.py /
   candidate_compiler.py / … 九个模块)。现有 `services/research/alpha_factory.py`
   是单文件搜索引擎(cs_genome 驱动),命名撞车是历史事实;新包承载 §8 的
   *假设-编译-搜索-挑战* 层,与 `services/factor_garden/`(§6)对称:每个
   727 大节一个 services 侧包,纯核 + 薄边界,平铺研究模块保持不动。
   依赖方向钉死:`services/alpha_factory` 不得 import `services/intelligence`
   (编排方向相反),不得含任何 LLM 客户端。
2. **Hypothesis 是不可变预注册声明**。`hypothesis_id` 由内容派生
   ("hyp_" + sha256(身份字段)[:16])——改任何内容字段 = 新 id,这正是
   预注册语义(试验跑完后编辑过的假设不是试验所测的假设)。`created_at_ms` /
   `prompt_hash` 是「写下假设」这一行为的 provenance、不是声明内容,不进身份
   哈希(与 factor garden 的 content-hash 决定同款);`creator_model` 进哈希
   ——同一声明由不同 proposer 写下是不同谱系。
3. **Store 只有 append**。`SqliteHypothesisStore` 与 maintenance_store /
   experiment_registry 同款开库语义(create=False + 缺失即错、WAL、
   busy_timeout、开库验证 append-only 触发器)、no-update/no-delete 触发器、
   record 级哈希链。register 幂等:同 id 同内容 → 返回既有行;同 id 不同
   内容 → `HypothesisConflictError` 大声抛出(内容派生 id 下这只能是哈希
   碰撞或伪造 id,绝不静默)。
4. **TrialProvenance 接线只做加法**。两个 canonical 入口
   (`run_alpha_factory` / `run_registered_candidate_batch`)只新增可选
   kwarg(`hypothesis_by_candidate` / `code_hash`),旧签名逐位兼容,全部
   既有测试零修改通过。未提供 hypothesis 的候选 `hypothesis_id=""` 诚实记录
   ——**绝不为 legacy 调用方编造假设**。状态只记 `RECORDED`:两个入口今天
   不追踪逐候选淘汰阶段(alpha_id 去重发生在账本注册之前),不把未知编造
   成 ELIMINATED_*;§8.3 漏斗阶段归 P5-2。
5. **code_hash 由调用方供给**。代码库唯一的 git sha 助手在
   `services/research_runner/runner.py::_git_sha`(被该模块测试大量
   monkeypatch 钉死),`services/research` 反向 import 它会颠倒依赖方向,
   且确定性核心不应自读 .git。故两个入口接受 `code_hash: str = ""` 可选
   参数,默认 "" = UNPINNED 诚实标记,driver(tools/)负责解析并传入。
6. **证据诚实翻转,不造假覆盖**。`trial_ledger_integrity.json` 的 verdict
   保持 PARTIAL——历史 2533 条 bare trial 永远无法回溯覆盖,bare_trials>0
   就不是 PASS(§22.13)。翻转的是**内容**:新增 `provenance_wiring` 代码级
   验证段(与 `_family_testing_available` 同款模式:签名检查两个入口的
   provenance kwarg + registry 记录 API),第一个 open_gap 状态从
   `MECHANISM_LANDED_CALLERS_PENDING` 翻为
   `CANONICAL_ENTRIES_WIRED_OTHER_DRIVERS_PENDING`,明确列出剩余缺口
   (15+ 其他驱动器 + 历史总体)。**不向生产账本注入合成 trial**——账本是
   DSR/PBO/FDR 分母,为让证据好看而写入的合成候选会污染泄气计数。

## 3. P 系列切片计划

| 切片 | 内容 | 状态 |
|---|---|---|
| **P5-1** | `services/alpha_factory/`:hypothesis.py(§8.1 全字段 + 内容派生 id + fail-closed builder)+ hypothesis_store.py(append-only 哈希链)+ economic_rules.py(可复用规则面);TrialProvenance 接线进两个 canonical 入口;trial-ledger 证据诚实翻转 | **已落地**(27 测试) |
| **P5-2** | candidate_compiler.py(Hypothesis → 受限 DSL 候选编译:canonicalize/serialize/alpha_id 三道 + 合成面板 evaluate 校验,只保形式合法 + 身份稳定不评优劣)+ constrained_search_space.py(§8.2 受限空间:五违规码,max_window 必填无默认=数据可用性上界,两层形态确定性枚举防爆炸)+ candidate_deduplicator.py(EXACT/NEAR/KEEP,首见保留与 run_alpha_factory._pending 同语义)+ search_controller.py(§8.3 漏斗前四层编排:假设违规拒开搜/语义淘汰 ELIMINATED_SEMANTIC+码/预算 BudgetExhausted/去重 ELIMINATED_DUPLICATE;**控制器不写库**,eliminations 供调用方经 P5-1 接线落账) | **本提交**(30 测试) |
| **P5-3** | challenger.py(ChallengeObjection 结构化异议 + 确定性 statistical_challenger 七条攻击线 + external_objection 唯一入境口)+ challenge_council.py(convene 六条固定政策=§8.4 Deterministic Judge;EXTERNAL BLOCK 只到 REQUIRES_HUMAN_REVIEW 绝不直接 FAIL)+ champion_challenger.py(§10 第一刀:ChampionSnapshot 冻结销 + assert_frozen 漂移码 + §10.3 多指标固定政策对比,绝不只比总收益) | **本提交**(26 测试) |

切分原则:每片独立可测、独立入库、不依赖后片;P5-1 不预留「将来一定这样」
的抽象(P5-2 的编译器可能反过来修正 Hypothesis 的字段选择,届时显式评审)。

## 4. P5-1 实施记录(2026-07-27)

### 4.1 `services/alpha_factory/`(新包)

- `hypothesis.py`:frozen `Hypothesis`(§8.1 全部 14 字段)+
  `HypothesisError` + `create_hypothesis`(内容派生 id、构造即过
  economic_rules、文本长度/空白/非负 int 全 fail-closed)+
  `hypothesis_content_hash`(stable_hash 覆盖身份字段)。
- `hypothesis_store.py`:`SqliteHypothesisStore`(register 幂等 / get /
  has / all 注册序确定 / count / chain_valid)+ `HypothesisConflictError` /
  `HypothesisNotFoundError`;表 `hypotheses` 六列 + 双触发器。
- `economic_rules.py`:`check_hypothesis_rules` 返回违规码元组(空=通过):
  HYP_MECHANISM_TOO_SHORT(≥20 字符)、HYP_NO_FALSIFICATION、
  HYP_NO_DIRECTION、HYP_NO_FACTORS、HYP_VAGUE_FALSIFICATION(<10 字符或
  无数字/比较符提示,**UNCALIBRATED 启发式**,文档钉死)、HYP_NO_PROVENANCE。
  builder 构造时复用;独立成模块是为了 P5-2 的 search_controller 能重检
  非本构造的假设(从 store 读回的、tools/ 传入的)。

### 4.2 TrialProvenance 接线(两个 canonical 入口)

- `run_alpha_factory`:新增可选 `hypothesis_by_candidate`(按 alpha_id 键)
  与 `code_hash`;`_run_alpha_factory_impl` 新增 `provenance_recorder`
  回调,与 `candidate_recorder` 同点(评分**之前**)触发——失败的评分器
  同样抹不掉已记账的尝试。每候选一条 TrialRecord:
  model_name="cs_genome-evolution"(真实生成器,非 LLM)、
  canonical_expression=serialize(canonicalize(expr))、
  parameter_hash=stable_hash(params)、dataset_versions=(dataset_version,)、
  trial_id=`<search_id>:<config_hash>`(内容派生,重放幂等——
  record_trial_provenance 同 id 返回 False)。
- `run_registered_candidate_batch`:同款接线。DSL 候选经 deserialize →
  canonicalize 派生 canonical_expression/expression_hash,**非 DSL 候选
  诚实记 ""**(不编造表达式身份);model_name="" 诚实(本入口只评估,
  生成器是外部 driver)。
- 状态纪律:两个入口今天只 attests `RECORDED`——它们不追踪逐候选淘汰
  阶段,不把未知编造成 ELIMINATED_*(§8.3 漏斗阶段归 P5-2)。
- `services/research/trial_provenance.py` 新增 `trial_id_for_attempt`
  纯函数,两个入口共享同一 id 约定;两个不同 session 尝试同一 config 仍
  产生两行(§7.2「重复候选也必须记账」,测试钉死)。

### 4.3 证据翻转(`evidence/trial_ledger_integrity.json`)

- before:`open_gaps[0].state = "MECHANISM_LANDED_CALLERS_PENDING"`,
  detail = "the research drivers still register bare config hashes"。
- after:`open_gaps[0].state = "CANONICAL_ENTRIES_WIRED_OTHER_DRIVERS_PENDING"`,
  新增 `provenance_wiring` 段(两入口 wired=true、registry_record_api=true、
  role=CANONICAL_ENTRIES_WIRED_NOT_LEDGER_COVERAGE)。
- verdict **保持 PARTIAL**:bare_trials=2533 不变(历史总体永久 bare +
  15+ 其他驱动器未接线,全部列入 open_gaps,不声称全覆盖)。
- 门禁测试强化而非放松:artifact 测试新增 wiring 段钉死(两入口
  wired==true、wired_entry_count==2),篡改链仍 BLOCKED、bare 总体仍
  PARTIAL、全覆盖仍可 PASS 的全部旧断言一字未动。

### 4.4 P5-2 实施记录(2026-07-27,本提交)

- `candidate_compiler.py`:`CompiledCandidate`(canonical expression +
  canonical_expression + expression_hash + hypothesis_id + params +
  compile_warnings);`compile_candidate` 三道(canonicalize → serialize →
  alpha_id)+ 合成面板 evaluate 校验——**编译不评优劣,只保形式合法 +
  身份稳定**。校验三层模式平行实现自 factor_garden
  `definitions.py::_validate_expression`(**刻意不抽共用**:那边
  FactorDefinitionError 被 tests/factor_garden 钉死,这边
  CandidateCompileError,合并要动已落地切片换不来行为收益;模式若变两处
  同步,注释钉死)。
- `compile_from_hypothesis` 绑定编译:required_factors 与表达式引用 fields
  **零交集 → compile_warnings 加 HYPOTHESIS_FACTOR_MISMATCH 不硬拒**——
  表达式可能经中间变换/派生字段触及假说机制,硬拒会误杀恰需要人工看的
  候选;警告随候选走,审计可见而非静默放行。
- `constrained_search_space.py`:`SearchSpaceConstraints`(fail-closed 构造;
  **max_window 必填无默认** = §8.2「超出数据可用时间的窗口」默认拒绝,
  数据可用性上界只有调用方知道;allowed_ops 默认 = cs_genome 可生成全集,
  signed_power 刻意排除——DSL 支持但 cs_genome 永不生成,注释钉死)。
  `check_expression` 五违规码固定检查序(root → depth → 前序遍历
  op/field/window),去重确定性;`_depth` 四行平行实现(不 import
  cs_genome 私有,**cs_genome 一字未改**)。`enumerate_space` 只枚举
  (ts_op field w) 与 (cs_op (ts_op field w)) 两层形态,几何窗口阶梯
  (min 起翻倍 + 恒含 max,刻意粗——细扫是 driver 预注册族的活),
  alpha_id 去重 + max_candidates 硬截断,产出全部过 check_expression。
- `candidate_deduplicator.py`:EXACT(hash 已见)/ NEAR(expression_similarity
  ≥ near_threshold,**0.85 默认 UNCALIBRATED 工程默认**,取最高相似度、
  平手取最早见)/ KEEP;`filter_batch` 首见保留、后者判重,与
  run_alpha_factory._pending 首见语义一致(两条路径去重结果相同)。
- `search_controller.py`:§8.3 漏斗前四层编排。§8.1 硬前置——
  check_hypothesis_rules 有违规 → SearchPlanError(**无合格假设不开搜**);
  compile 失败/space 违规 → ELIMINATED_SEMANTIC + 稳定码
  (COMPILE_INVALID_DSL 的 expression_hash 诚实为 "");预算 =
  run_registered_candidate_batch 同口径(trials_used=声明族大小,compute
  不可计量声明即拒)→ BudgetExhausted 在任何 dedup 之前;去重 →
  ELIMINATED_DUPLICATE(verdict + matched hash)。**控制器本身不写库**——
  返回 SearchPlan(survivors + 类型化 EliminationRecord 元组 +
  planned_at_ms),落账归调用方经 P5-1 接线(e2e 测试真消费:
  survivors 写 RECORDED、eliminations 写各自 ELIMINATED_* + reasons,
  重复候选与幸存者可同 config_hash 双行,链验证通过)。
  `evaluate_stopping` 薄封装 evaluate_stopping_rules + max_trials 硬顶
  (trial_count 独立参数:账本总体可多于有分 trial)。
- 数据可用性(store 层)/PIT-lag/粗筛及以后各层在 evaluator/gauntlet,
  刻意不在本切片。

### 4.5 P5-3 实施记录(2026-07-27,本提交)

- `challenger.py`:`ChallengeObjection`(role/code/severity/rationale/
  evidence_hash/origin/creator_model/prompt_hash;evidence_hash = stable_hash
  覆盖 role/code/severity/rationale/origin)。**两种来源一个形状**:
  DETERMINISTIC(statistical_challenger 七条固定攻击线:DSR≤0→BLOCK/
  PBO≥0.5→BLOCK/leakage→BLOCK/perturbation FAIL→BLOCK/regime<0.5→WARN
  (UNCALIBRATED)/cost FAIL→WARN/concentration FAIL→WARN;全 None → 单条
  INFO/INSUFFICIENT_EVIDENCE,**缺证据绝不静默通过**)与
  EXTERNAL_STRUCTURED(`external_objection` = LLM/人工异议**唯一入境口**,
  rationale≥20 字符 UNCALIBRATED、creator_model 必填、prompt_hash 可空
  如实;services/ 永远不知道模型存在)。`ChallengeInput` 构造即 fail-closed
  校验全部已知键(未知键/NaN/坏 verdict 直接拒)。
- `challenge_council.py`:`convene` 六条固定政策(见模块 docstring 逐条
  注释)——**EXTERNAL_STRUCTURED 的 BLOCK 只到 REQUIRES_HUMAN_REVIEW,
  绝不直接 FAIL(§8.4 钉死,测试钉死)**;空 objections ≠ PASS;PASS 只在
  全 INFO 非缺证据时可达。`decision_hash` 覆盖 verdict + 异议哈希序,
  evaluated_at_ms 是 provenance 不进哈希。`judge_candidate` =
  statistical_challenger + external_objections → convene,727 §8.4
  Deterministic Judge 的落地:只读机器证据 + 固定政策。
- `champion_challenger.py`(§10 第一刀):`ChampionSnapshot` 九字段冻结销
  (frozen_at_ms 是冻结行为 provenance,不进 snapshot hash)+
  `assert_frozen` → CHAMPION_CODE_DRIFT/CHAMPION_CONFIG_DRIFT(§10.1
  「不得在线自动调参」的机器检查;未钉的销 = 无意见如实,绝不静默 PASS)+
  `compare_champion_challenger`:§10.3 十键面板(缺键诚实 None)+
  marginal_ir(有 IR+相关才算,没有 None)+ 固定政策(net_sharpe ∧
  max_drawdown **双优**且无 guard 退化才 CHALLENGER_BETTER;缺关键指标/
  混合画面/guard 退化 → INCONCLUSIVE;双键不优 → CHAMPION_STANDS——
  **绝不只比总收益**,测试钉死单指标胜利 ≠ BETTER)。
- **诚实边界**:本切片是 §10.2 流程的「机器生成对比证据包」一半;
  Challenger 替换流程本身(真人批准、Micro-Live 渐进替换)在下游治理,
  刻意不在此。convene 的 verdict 是 Deterministic Judge 给 Governance
  Plane 的机读建议,不是生命周期状态转换本身。

## 5. LLM 边界治理原则(727 §8.4,P5-1 即钉死)

1. **`services/` 永不持有模型客户端**。无网络 import 由治理扫描机器钉死;
   LLM 调用只能发生在 tools/(gate 外),产出只能是**结构化数据**
   (Hypothesis 的 §8.1 字段、挑战意见的结构化对象)。
2. **最终状态转换永远归 Deterministic Judge + Governance Plane**。
   语言模型可以提议假设、提交挑战,但 PASS/FAIL、晋级、淘汰写账只能由
   读机器证据、按固定政策判定的确定性组件完成——LLM 投票不是状态机输入。
3. **provenance 强制**。LLM 产出的假设进入 store 时必须带
   `creator_model` + `prompt_hash`;人类假设用 `creator_model="human"`。
   TrialRecord 的 model_name/prompt_hash 同理——「这个候选是谁造的」永远
   可审计。

## 6. 727 Phase 5 验收对照表

| 验收项 | 状态 | 承载 |
|---|---|---|
| 候选有经济假设和证伪标准 | ✓ | P5-1:Hypothesis §8.1 全字段 + fail-closed builder + economic_rules;「Sharpe 可能更高」构造即拒 |
| 所有候选进 Trial Ledger | ✓(canonical 入口)/ PARTIAL(其余驱动器) | P5-1:两个 canonical 入口接线 + 证据诚实翻转(verdict 保持 PARTIAL,15+ 其他驱动器与历史 bare 总体全列 open_gaps) |
| 语言模型不能推进部署状态 | ✓ 结构性 | services/ 零 LLM 客户端(治理扫描钉死);P5-3:EXTERNAL 异议只能到 REQUIRES_HUMAN_REVIEW(convene 规则 2 机器钉死),FAIL/PASS 只由机器证据 + 固定政策给出;最终生命周期转换在 Governance Plane |
| 受限 DSL(§8.2) | ✓ | P5-2:SearchSpaceConstraints 五违规码 + 两层枚举 |
| 分层漏斗淘汰写原因(§8.3 前四层) | ✓ 前四层 | P5-2:ELIMINATED_SEMANTIC/DUPLICATE 类型化记录;后续层在 evaluator/gauntlet(既有) |
| Challenge 阶段(§8.3 独立 Challenge) | ✓ | P5-3:challenger + challenge_council(ELIMINATED_CHALLENGE 写账归调用方,与 P5-2 控制器同分工) |

