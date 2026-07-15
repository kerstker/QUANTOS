# 设计 spec:ML 引导的候选提议器(路线图 Rank #16)

> 2026-07-03。状态:**spec 先行**(P1/L)——本文只定设计与边界,不含实现;按计划先过对抗式评审再排实现。
> 定位诚实前提:确定性 GBM+Ridge stacker(`services/research/ml`)已实证**无可交易 edge**(净@5bps,可交易宇宙),
> 结论瓶颈=**数据/信号类**非建模层。因此本提议器**不是**又一个事后组合器,而是**扩大生成搜索空间**的一步,
> 且**预期有真实的 null 结果风险**——它照样得过同一个 fail-closed 验证门,不会因为"用了 ML"就放松半分。

## 1. 问题与非目标

**问题**:当前因子生成锁死在**固定词表的标量表达式**(8 价格 + 4 量能算子的 GP 树组合)。可表达空间有限,
已实证在大盘币日线上挖不到跨 regime edge。**Rank #16 要打破的是"生成"这一环的天花板,不是"验证"环。**

**非目标(硬边界)**:
- 不进 `services/` 确定性核心:提议器是**门外**(`tools/` 或 `services/research/ml` 隔离层)。
- 不破容器化:LLM/ML 提议器 = **非人类 actor**,产物**永不自动晋级**(auto_approved=0 恒定)。
- 不放松验证:所有产物过**同一条** `ValidationGate`(DSR/PBO/purged-CV/no-peek OOS)+ Rank #12 的
  `CatalogEvidence` 溯源 + 多重检验账本(**提议器的每一次尝试都计入 catalog trial count**——这是与"调参到过门"
  的分水岭)。
- 不引入网络/在线学习到确定性核心;numpy/sklearn 仅限已隔离的 `ml/`(非网络,治理钉死)。

## 2. 设计:提议 → 编译 → 验证(同一条门)

```
[门外]  ML 提议器  ──产 DSL 候选──▶  硬化 AST 编译器(既有,拒 eval/exec/前视名)
                                          │(编译为确定性 ComputeFn)
[门内]  ◀── 每次尝试计入 trial count ──  搜索环(guided/evolutionary/gp/mcts,既有)
                                          │
                                       ValidationGate(DSR/PBO/CV/no-peek OOS,fail-closed)
                                          │ + CatalogEvidence(Rank #12)+ leakage 证据(既有)
                                       AlphaCatalog ──▶ 生命周期(非人到不了 APPROVED)
```

**核心接缝**:提议器只产出**DSL 表达式候选**(字符串/AST),喂给既有的 `alpha_expression` 编译器 + 搜索环。
它**不**碰权重、下单、凭证、组合构建。产物与人工/GP 提议的候选**走完全相同的下游**,只是**来源不同**。

## 3. 三种提议器变体(按风险/成本排序)

1. **ML 引导的算子/子树选择(最保守,首选实现)**:用学习型模型(GBM/小 MLP)在既有 DSL 算子空间里
   **引导搜索方向**(哪些算子组合的历史 IC 分布更有希望),替代 UCB/MCTS 的手调 reward。仍在固定词表内,
   但用数据学"往哪找"。**风险最低、最先做、最可能 null(词表就那么大)。**
2. **学习型特征生成喂给 DSL(中等)**:用 ML 从原始 bar/OI/宏观派生**新的中间特征**(如学习型 regime 嵌入、
   非线性变换),作为 DSL 的**新输入原语**(扩大词表本身)。过泄漏检测 + PIT 物化(Rank #15)。**这是真正扩大
   可发现 edge 类型的一步**,但需谨慎:学习型特征最容易偷偷引入前视,泄漏门必须承重。
3. **LLM 假设生成器(最高杠杆、最高风险,最后做,需单独 spec)**:门外 LLM 提议 DSL 假设/表达式(参考
   arXiv 2505.11122),人在回路审批**提议批次**(非单条),产物过同一门。LLM=非人 actor,永不 APPROVED。
   **本 spec 不细化 LLM 变体**——它需要独立的 prompt/成本/幻觉-防护 spec + 对抗评审。

## 4. 反自欺护栏(为什么这不会变成"过拟合工厂")

- **多重检验账本承重**:提议器可能一秒产 1000 个候选。**每个 distinct config 计入 `global_trial_count`**,
  DSR/BHY 随之收紧(Rank #12)。提议越多,过门越难——数量不换来虚假发现。
- **no-peek OOS 折叠**:提议器只见 IS;晋级看无窥视 OOS(既有)。
- **诚实预期**:基于 stacker 的 null 结果,**预设变体 1 大概率 null**(词表天花板),变体 2 是真正的杠杆但
  data/leakage 风险高。**成功的定义是"扩大了搜索空间且没放松验证",不是"一定挖到 alpha"。**
- **确定性/可复现**:提议器的 RNG/seed 钉死在门外边界;门内确定性核心不变。

## 5. 首个实现增量(变体 1)与验收

- `services/research/ml/operator_guidance.py`(隔离层):学习型算子-选择先验(numpy/sklearn,pinned、确定性),
  接进既有 `guided_search` 作为**可选** reward 先验(默认关,零回归)。
- 驱动器 `tools/ml_proposer_run.py`:在同一份加密/FX 面板上跑,产物过 `run_research_session` 的既有门。
- 验收:① 产物 100% 过同一 `ValidationGate` + 计入 trial count;② containment auto_approved=0;③ 确定性
  (同输入 bit-identical);④ 诚实报告结果(**包括 null**),不因"ML"美化。治理测试钉死无网络/无下单 surface。

## 6. 对抗评审要点(实现前必须回答)

- 变体 2 的学习型特征如何**机器证明**无前视(泄漏门够不够?物化的 PIT 键对不对?)。
- 提议器的 trial-count 计入是否**真的**收紧 DSR/BHY(而非被绕过)。
- 成本:变体 1 的算力可控;变体 3(LLM)的 token 成本 + 幻觉率 + 人工审批批次设计需单独定。
- null 结果的处理:如果变体 1/2 都 null,**是否升级到变体 3**还是判定"瓶颈确在数据、ML 生成无解"。

---
*本 spec 是 Rank #16 的 spec-先行交付。实现按变体 1 → 2 → (3 独立 spec)排期,每步先过对抗评审。诚实预期:
高潜力、真有 null 风险;价值在"扩大生成搜索空间 + 验证严谨不变",不在承诺 alpha。*
