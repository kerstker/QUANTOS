# QUANTOS 当前系统全逻辑与架构：第三方审查包

> 版本：2026-07-23  
> 审查对象：`H:/code/QUANTOS` 量化交易研究平台  
> 代码锚点（生成本文时）：`7ec324cd9b29c2ce74df67404c0fc2c298695b6b`  
> 当前就绪等级：`L2_DRY_RUN_ONLY`  
> 本文用途：把仓库中的真实逻辑、边界、证据、缺口和复现方法交给独立第三方审查。

本文不是收益承诺，也不是实盘授权书。审查者应以代码、测试和带哈希的证据制品为准；文档中的历史数字只能作为索引，不能替代重新运行。

## 1. 执行摘要

### 1.1 一句话结论

QUANTOS 已经是一个可以运行研究、回测、组合构建、风险评估、端到端干跑和部分前向纸面流程的安全关键量化研究平台；它还不是“已证明能够稳定为个人赚钱的实盘系统”。

当前最准确的结论是：

~~~text
研究与干跑能力：已实现，可以复现，可以审计
历史回测中的候选 Alpha：存在条件性正结果，但受数据、成本、状态和样本期限制
前向纸面证据：未达到门槛，当前被数据血缘/事件/新鲜度门禁阻断
真实券商、真实凭证、真实下单：当前不存在，并且被 L2 安全边界结构性禁止
顶级机构级系统：机器结论 NOT_TOP_TIER
个人稳定盈利系统：尚未证明
~~~

### 1.2 当前机器证据快照

| 项目 | 当前证据 | 审查解释 |
|---|---|---|
| 顶级能力检查 | `python scripts/assess_top_tier.py --check` 返回 `NOT_TOP_TIER - 14/22 pass` | 不是代码崩溃；表示仍有部分、失败和外部证据项 |
| 顶级要求状态 | 14 `PASS`、1 `PARTIAL`、5 `FAILED`、2 `MANUAL_EVIDENCE_REQUIRED` | 详见 `evidence/top_tier_assessment.json` |
| 当前安全模式 | `L2_DRY_RUN_ONLY` | 研究/干跑/纸面，不允许真实订单 |
| 当前纸面健康 | `data/cache/paper_health_10k.json` 的 `paper_ready=false` | 前向质量证据不新鲜，原始血缘回填为 `BLOCK` |
| 操作员烟测 | `l2_paper_operator_10k.json` 的三 Alpha 权威 dry-run 阶段为 `PASS` | 这只证明干跑/预览编排阶段，不等于前向纸面就绪 |
| 前向周期 | 三个 Alpha 都是 `0/8` 个有效前向周期 | `8` 是当前系统的最低证据门，不是盈利保证 |
| 统一门禁 | 本次审查运行 `python scripts/validate.py` 超过 304 秒未返回，已回收其子进程 | 不能把这次运行写成“全绿”或“全红”；第三方应在自己的环境重新运行 |
| 网络/券商 | `services/` 未发现券商 SDK 或下单路径；网络抓取只允许在 `tools/` | 当前没有私有 Binance、券商、真实凭证或真实订单通道 |

### 1.3 当前三条主 Alpha 的权威干跑摘要

下面的数字来自 `evidence/alpha_paper_cycle.json` 和 `evidence/three_alpha_paper_flow.json`，是历史/干跑证据，不是前向业绩。

| Alpha | 定义 | 干跑周期 | 原始 Sharpe | VT Sharpe | 原始最大回撤 | 干跑风险门 | 前向状态 |
|---|---|---:|---:|---:|---:|---|---|
| `carry_oi_majors` | 负资金费的组合 carry | 316 周 | 0.4771 | 0.7458 | 71.07%（VT 22.08%） | `RISK_REJECTED` | 0/8，`BLOCKED` |
| `crowd_contra` | `cs_rank(neg(global_ls))`，反向拥挤散户多空 | 235 周 | 1.0697 | 1.0950 | 8.85%（VT 9.71%） | `RISK_APPROVED` | 0/8，`BLOCKED` |
| `top_contra` | `cs_zscore(neg(top_ls))`，大币持仓反向 | 192 周 | 1.5246 | 1.3013 | 20.15% | `RISK_APPROVED` | 0/8，`BLOCKED` |

三者都有 `reconciliation_residual` 约 `1e-9` 至 `4e-9`，说明干跑 PnL 恒等式在这些制品中对账成立；这不能证明信号未来有效，也不能证明成本、流动性和数据可用性已经在真实环境成立。

### 1.4 之前运行过的组合 Alpha / Alpha Book 摘要

组合不是第四条独立预测因子，而是把已有 Alpha 的收益流或持仓做资本配置；它们必须和三条主 Alpha 分开审查：

| 组合版本 | 关键历史结果 | 真实口径 |
|---|---|---|
| carry + crowd 收益流分散 | 231 个共同周，corr=-0.038；逆波动组合 Sharpe 1.977、回撤 7.99% | gross、不同宇宙/网格，仅用于相对分散 |
| 三 Alpha optimizer book | carry/crowd/top 权重 20.98%/35.11%/43.91%；economic Sharpe 1.787、回撤 8.7%；10bps fee Sharpe 1.210 | 研究层组合，未进入 forward paper |
| carry_crowd_combo | naive 50/50，同一 dry-run pipeline；历史 VT Sharpe 1.20、回撤 15% | 有整手、门禁、成本、资金费和 vol-target，但当前 db 需重跑确认 |
| carry_crowd_risk_parity | PIT trailing 逆波动/资本配置；历史 VT Sharpe 约 1.14 | 执行现实版本，仍低于 crowd_contra 单条约 1.38 |

最重要的结论是：gross 收益流层面的 1.95–1.98 Sharpe 没有在真实统一执行后保持；成本、整手、宇宙不对称和 carry 的大回撤把组合压到约 1.14–1.21 VT Sharpe。因此“组合有效”只能表述为历史分散诊断，不能表述为已证明的实盘盈利组合。

## 2. 审查边界、证据优先级与已知状态差异

### 2.1 证据优先级

第三方应按以下顺序判断事实：

1. **可执行源代码**：`quantos/contracts/`、`services/`、正式 `tools` 驱动器。
2. **测试与治理扫描**：`tests/`、`scripts/validate.py`、治理 AST 扫描器。
3. **带路径和哈希的 JSON/SQLite 制品**：`evidence/` 和 `data/cache/`。
4. **活文档**：`docs/PROJECT_STATE.md`、`docs/ARCHITECTURE.md`、本报告。
5. **历史文档/旧审计**：只能作为背景，不能覆盖当前源代码和当前制品。

### 2.2 纸面状态为何看起来有差异

`l2_paper_operator_10k.json` 的“流程 PASS”表示：三条权威干跑可以读到、运行、持久化并返回摘要；它的 `public_forward` 在本次运行中是 `SKIPPED`，且下游预览需要当前质量证据。

同一时间的 `paper_health_10k.json` 和 `paper_realtime_10k_status.json` 显示：

- `paper_preview_ready=false`；
- `PAPER_MARK_NOT_RECORDED`、`PAPER_FILLS_MISSING` 等健康阻断；
- `data_quality_admission.lineage_verdict=BLOCK`；
- 原始血缘回填存在日期/制品/归档不匹配；
- 实时纸面链没有可用新快照。

因此，正确解释是“干跑编排烟测通过，但最终纸面健康门没有通过”，而不是“系统已经完成前向验证”。这种差异本身应作为第三方审查项目，确认状态机是否需要更清晰地命名和展示。

### 2.3 本次审查的验证限制

- 本报告生成时没有修改运行旗标、没有读取或提交任何私钥、没有连接私有账户。
- 统一门禁运行超过 304 秒未返回，进程已确认属于本次 `validate.py`/`pytest` 并回收；不能据此推断测试失败原因。
- 顶级检查已独立运行并返回 `NOT_TOP_TIER - 14/22 pass`。
- 第三方应在干净环境中重新运行完整门禁，不要把仓库内的历史测试计数当成当前计数。

## 3. 系统定义与安全边界

QUANTOS 的权威蓝图是 [`QUANTOS_MASTER_BLUEPRINT.md`](../../QUANTOS_MASTER_BLUEPRINT.md)，活状态是 [`docs/PROJECT_STATE.md`](../PROJECT_STATE.md)，架构说明是 [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)。系统的设计目标是：在不允许 AI 或程序直接触碰真实资金的前提下，完成可复现的 Alpha 研究、验证、组合、风险、纸面和审计闭环。

### 3.1 五个不可谈判的安全旗标

`quantos/contracts/config.py::SafetyConfig` 的默认值为：

~~~text
LIVE_TRADING_ENABLED=false
REAL_BROKER_ENABLED=false
ALLOW_ORDER_SUBMISSION=false
ALLOW_AI_DIRECT_TRADING=false
REQUIRE_HUMAN_APPROVAL=true
~~~

`live_trading_armed` 只有在前三个允许项都显式打开、且 AI 直交易仍关闭时才可能为真；研究/纸面代码构造 `SafetyConfig` 时会调用 `assert_research_safe()`，任何不安全组合直接抛出 `SafetyViolation`。

### 3.2 两层威胁模型

运行时控制能阻止非人类 Actor 自行到达 `APPROVED`、重置 kill switch、读取凭证或下真实订单；但单个进程内的“受信代码”理论上仍可以直接构造 `HUMAN` Actor、直接调用 manager/catalog 私有方法或重算守卫。仓库已把这一点明确写入威胁模型：

- 运行时：`ResearchAgentLoop`、`guardrails`、Alpha 生命周期和预交易门。
- 构建/部署时：代码审查、治理扫描、进程隔离、出进程签名权威。

`services/governance/signing.py` 在 L2 只有 `NullSigningAuthority`，没有密钥，`sign()` 会拒绝，`verify_signed_ref()` 在没有真正出进程权威时恒为 `False`。这保证了“设计接缝存在”，但不冒充已经完成的生产签名部署。

### 3.3 允许与禁止的边界

| 允许 | 禁止 |
|---|---|
| `tools/` 使用公开数据抓取器、生成研究快照 | `services/` 直接访问网络或券商 |
| 本地 SQLite/JSON 研究、回测、干跑、纸面账本 | 私有 Binance、CTP/FIX/CCXT SDK、真实下单 |
| 人工启动研究 runner 的白名单任务 | AI 直接交易、自动批准、自动翻转 LIVE 旗标 |
| 公开 mark price 的纸面估值 | 将纸面成交/回测结果称为实盘成绩 |

## 4. 总体架构与数据流

~~~mermaid
flowchart LR
    A[公开数据与本地文件 tools/] --> B[规范化与质量准入]
    B --> C[PIT/双时点数据湖 DatasetVersion/Universe]
    C --> D[特征与 Alpha DSL 价格/成交量/截面算子]
    D --> E[研究搜索 GP/Evolutionary/MCTS/Guided]
    E --> F[验证关卡 IC/DSR/PBO/WF/Leakage/Regime]
    F --> G[Alpha Catalog + Lifecycle]
    G --> H[组合与资本配置 Optimizer/Factor Risk/CVaR]
    H --> I[研究风控 9-engine post-run evidence]
    H --> J[目标手数/订单意图]
    J --> K[PreTrade Gate + KillSwitch]
    K --> L[DryRun Adapter 只模拟成交]
    L --> M[PnL 对账/资金费/冲击]
    M --> N[Paper Track + Health + Go/No-Go]
    M --> O[Audit/Event Store 哈希链/投影/DLQ]
    N --> P[只读 Console]
    O --> P
    Q[ResearchAgentLoop 非人类 Actor] --> E
    Q -.永不可到达 APPROVED/真实订单.-> G
    R[Research Runner 唯一可写研究入口] --> E
~~~

核心原则是：策略只产生“意图/目标权重”，风险决定是否有权限，执行适配器决定如何模拟成交；研究、纸面、实时纸面和未来实盘不能共用同一状态或账本。

## 5. 代码分层与每层运行原理

### 5.1 合同层：`quantos/contracts/`

这是跨域的单一数据契约，不应该由业务模块各自重新定义。

| 合同 | 作用 |
|---|---|
| `config.py` | 安全旗标、研究安全断言 |
| `data.py` | `Instrument`、`Bar`、`Tick`、`BitemporalRecord`、数据质量、量纲语义 |
| `factor.py`、`feature.py` | 因子/特征定义与版本 |
| `research.py`、`alpha.py` | Experiment、Validation、Alpha 状态和哈希 |
| `portfolio.py` | Signal、TargetPosition、RebalancePlan、归因 |
| `risk.py`、`rejection.py` | 风险决定及统一拒绝原因枚举 |
| `order.py`、`trading.py`、`paper.py` | 订单状态机、交易意图、纸面成交 |
| `event_schemas.py`、`events.py` | 严格版本化事件和兼容性规则 |
| `hashing.py`、`identity.py`、`governance.py` | 可复现哈希、身份和治理引用 |

订单状态机允许 `CREATED → VALIDATING → RISK_APPROVED → SUBMITTED → FILLED` 或对应拒绝/取消/失败终态；终态没有出边。L2 的交易适配器只允许 dry-run 状态，不会进入真实 `SUBMITTED` 路径。

### 5.2 数据层：PIT、版本、宇宙与质量

主要实现：

- `services/data/pit_store.py`：按 `event_time` 和 `knowledge_time` 保存双时点记录，`as_of()`/`latest_as_of()` 只返回当时已知数据。
- `services/data/universe.py`、`universe_store.py`：按有效区间维护成员资格，避免生存者偏差。
- `services/data/versioning.py`：对规范化记录计算内容哈希，生成 `DatasetVersion`。
- `services/data/adapters.py`：`SourceSpec` 将外部原始记录映射为统一 OHLCV；源必须声明时间单位、knowledge lag、成交量单位和语义。
- `services/data/quality.py`、`ingestion.py`、`admission_store.py`：检测重复、乱序、缺失、未来时间戳、量纲异常，并把质量决定写入追加式权威存储。

关键时间规则：

~~~text
knowledge_time = event_time + knowledge_lag
forward_return(t, h) = price[t+h] / price[t] - 1
~~~

数据抓取在 `tools/`，核心 `services/` 只接收已获取的记录。当前前向纸面主要使用 Binance Data Vision USD-M 永续 mark-price、funding history 和 futures metrics 的公开制品；它们的当前文件和哈希见 `evidence/data_quality_admission.json`。该制品中价格、资金费和 OI 检查可通过，但 raw-lineage backfill 当前为 `BLOCK`。

### 5.3 特征与 Alpha 表达式层

实现集中在：

- `services/research/signals.py`、`volume_signals.py`：价格、动量、波动、成交量算子。
- `services/research/alpha_expression.py`：对 `T × N` 面板执行时序窗口、截面 rank/demean/z-score/scale/winsorize、逐元素加减乘除、相关/协方差/beta、残差化等操作。
- `services/research/leakage_evidence.py`、`expression_leakage.py`：将原始面板和计算函数交给泄漏探测器，证据为 `None` 时不默认为通过。

表达式会被 canonicalize；交换律等价表达式共享同一 `alpha_id`，序列化结果参与内容哈希。这让搜索去重、实验追踪和审计可以针对具体表达式，而不是只记录一个显示名称。

### 5.4 Alpha 搜索层

固定入口是 `services/intelligence/research_session.py::run_research_session`。它组合：

- `ResearchAgentLoop`：持有非人类 Actor，只能提出和评估，不能审批或交易。
- `guided_search.py`：基于 UCB/引导的候选选择。
- `evolutionary_search.py`、`services/research/gp_tree.py`、`gp_genome.py`：进化/遗传表达式搜索。
- `mcts_search.py`：MCTS/UCT 探索候选表达式。
- `search_space.py`、`interaction_factors.py`、`fundamental_factors.py`：搜索空间与交互项。
- `services/research/session.py`：确定性核心；智能层固定注入 runtime，避免研究域反向依赖智能域。
- `services/research/experiment_registry/`、`run_registry/`：持久化搜索、候选、配置哈希、结果和运行日志。

搜索的输出是候选，不是自动晋级。搜索身份、来源、数据集版本、候选配置哈希必须持久化；当前机器评估将“跨会话、版本化、追加式全局 trial ledger”标为 `PARTIAL`，因此第三方要重点检查多重检验的 trial 数是否完整。

### 5.5 回测层：单资产与事件驱动

实现：`services/research/backtest_engine/`。

- `engine.py`：在 bar 收盘形成目标，在下一根 bar 的 close 执行；不使用同一根 bar 的未来信息。
- `event_engine.py`：组合级事件循环；在 bar `i` close 形成目标，在 bar `i+1` open 成交；支持整数手、可插拔 fill model、限价挂单、融资和公司行动。
- `book.py`：有符号平均成本账本，支持开仓、部分平仓和方向翻转。
- `fill_model.py`：`NextOpenFill`、参与率 fill；滑点进入成交价，费用和冲击单独记录，避免双计成本。
- `costs.py` / `quantos/contracts/cost.py`：fee、slippage、可选参与率冲击。

回测必须满足：

~~~text
equity_end - initial_capital
  = realized_pnl + final_unrealized + dividend_income - total_cost
reconciliation_residual ≈ 0
~~~

该恒等式只证明账本计算一致，不证明输入价格、成交量、冲击参数或数据源在真实市场中正确。

### 5.6 验证层：不自欺关卡

主要实现：

- `services/research/validation/cpcv.py`：组合纯化交叉验证，减少时间和组合泄漏。
- `deflated_sharpe.py`：按观察数、试验数和 Sharpe 方差做 Deflated Sharpe。
- `walk_forward.py`、`oos_validation.py`：按时间滚动、purged/embargo 的选择 OOS；候选选择只能使用训练段。
- `pipeline.py`：把 walk-forward、PBO、observed Sharpe、DSR、trial count、泄漏、regime 接入同一关卡。
- `services/alpha/evaluator.py`：IC 序列、ICIR/t-stat/p 值、净成本面板回测、容量、边际 IR、和已批准集合的相关性。
- `services/alpha/multiple_testing.py`、`catalog.py`：跨试验的 DSR、BH/BHY FDR、全局试验计数。

以下任一证据为 `None` 时，候选不会静默通过，而是 `REQUIRES_HUMAN_REVIEW`：

~~~text
leakage_ok
pbo
regime_profitable_frac
DSR 所需的 catalog/trial variance
与已批准集合的边际 IR / 相关性
~~~

注意：验证门可以证明“在给定数据和假设下没有被已知检验击穿”，不能证明未来盈利。样本外也可能因为 regime 变化、交易成本、容量和反身性失效。

### 5.7 Alpha 生命周期与晋级

`quantos/contracts/research.py::AlphaState`：

~~~text
DRAFT → TESTING → VALIDATED → PAPER_TRADING → SHADOW → APPROVED
                                  ↘ DEGRADED/PAUSED/RETIRED
任何阶段也可能 REJECTED
~~~

`services/alpha/lifecycle.py::AlphaLifecycleManager.advance()` 是状态变更 chokepoint：

1. 先检查合法状态转移和 Actor 类型；非人类不能到 `APPROVED`。
2. 检查目标状态的评价、PIT 数据、trial count、FDR、paper evidence、审批或多方 grant。
3. 更新 catalog，并追加带 `prev_hash` 的 AuditEvent 和 AlphaTransition。

`APPROVED` 是唯一具有“未来 live authority 语义”的状态，但当前状态机故意不包含 `small_live`/`production` 状态，L2 不会通过它获得真实资金权限。`maintenance.py`、`degradation_monitor.py` 和 `retirement.py` 负责衰退、暂停、退役建议；非人类 maintenance 不能授予 live 权限。

### 5.8 组合、容量与归因层

实现集中在 `services/portfolio/`：

- `constrained_optimizer.py`、`optimizer.py`：最小方差/带 alpha 目标的约束优化，Dykstra 投影处理 box、gross/net、L1、行业等约束；可选 Rockafellar-Uryasev CVaR 尾部惩罚。
- `risk_model.py`、`factor_risk.py`、`barra_lite.py`：因子暴露与结构化风险诊断。
- `capital_allocator.py`、`sleeve_sizing.py`：按滚动收益、协方差、质量和容量做 sleeve 配置；观测不足时回退到受限逆波动，并 fail-closed。
- `crowding_gate.py`、`crowding_calibration.py`：拥挤度/单名集中/参与率门；未知或输入无效时 `BLOCK`。
- `construction.py`、`signal_adapter.py`：把信号变成目标权重，再交给执行层。
- `brinson.py`：allocation/selection/interaction 的 benchmark-relative 归因，当前是诊断原语。
- `book_combine.py`：多策略组合与风险平衡；历史结果显示 gross 分散不一定在净成本执行后优于单一 crowd book。

“容量”目前主要是原则性、敏感性区间，冲击系数来源标记为 `UNCALIBRATED_PRINCIPLED`。第三方不能把报告里的单点 AUM 当成经交易所成交数据校准过的容量。

### 5.9 研究风控层：9 引擎聚合

`services/risk/research_risk/domain/gate.py::RiskApprovalGate` 运行并收集：

1. market
2. liquidity
3. concentration
4. correlation
5. drawdown
6. regime
7. stress
8. capacity
9. model_alpha

任何引擎异常都视为该引擎失败，不会被当成通过。决定优先级为 market、stress、liquidity、concentration、correlation、drawdown、regime、capacity、model/alpha；全部通过才是 `RISK_APPROVED`，达到配置的有限通过数才可能是 `RISK_APPROVED_WITH_LIMITS`，数据不足为 `NEEDS_MORE_DATA`。

当前干跑的某些驱动器只能提供四个诚实输入（market/drawdown/concentration/stress），其余引擎标成 `SKIPPED_NO_HONEST_INPUT`，而不是用假数据伪造通过。这个边界必须保留。

### 5.10 预交易风控与 kill switch

`services/risk/pre_trade/gate.py::PreTradeRiskGate` 是每个订单意图的运行时 chokepoint：

- 构造时先断言 research-safe 安全配置。
- 将权威 `SafetyConfig` 和持久化 kill-switch 状态覆盖进 `RiskContext`，拒绝调用者伪造的上下文。
- 顺序运行完整 `CHECK_REGISTRY`，每个检查异常都变成 `UNKNOWN_RISK_ERROR`，收集并去重所有拒绝原因。
- 只允许 `APPROVED` 或 `REJECTED` 离开，绝不产生模糊的“半批准”。
- 审计写入失败时拒绝；外层异常也只返回拒绝。
- 只有 `APPROVED` 才会产生 intent-bound `risk_ref`；空 ref 会在执行边界再次失败关闭。

kill switch 是 latched 的；一旦触发，所有意图都拒绝。权威状态和事件 outbox 绑定在同一事务，重启后仍然可验证。单进程内受信代码绕过仍属于部署隔离问题，不能把运行时门误认为完整安全边界。

### 5.11 执行边界与干跑适配器

执行主路径 `services/execution/dry_run_pipeline.py`：

~~~text
目标权重
 → w × AUM ÷ price
 → 整数手/step/min-notional 量化
 → build_intents
 → 每个 intent 过 PreTradeRiskGate + kill switch
 → DryRunAdapter
 → PaperFill
 → 平均成本 PnL 对账
 → GlobalAuditLog + EventBus
 → DryRunRun(SQLite)
~~~

`services/trading/broker_adapter.py` 定义 `BrokerAdapter` 接口：

- `DryRunBrokerAdapter` 是现有模拟引擎的透明包装，可作为流水线 drop-in。
- `LiveBrokerAdapter` 在 L2 构造时即拒绝，且不导入券商 SDK；其方法也不会发送订单。
- `assert_dry_run_adapter()` 在 `simulate_fills()` 首行执行，防止把未知适配器误接入。

成交成本：若有 ADV，使用 `AlmgrenChrissImpact` 的参与率驱动临时平方根 + 永久线性项；缺 ADV 时逐笔回退平 bps。成本进入 PnL 对账两侧，不能凭“残差为零”证明冲击参数真实。

### 5.12 纸面交易、前向证据与健康门

实现集中在 `services/research/paper_trading/` 和 `tools/`：

- `forward.py`：先记录在决定时点可知的决定，再在边界之后兑现，不改写已决定的记录；覆盖不足留 `pending`。
- `forward_store.py`、`forward_evidence.py`、`attested_store.py`：持久化 held-out OOS 回报、IC、数据和事件证明。
- `paper_realtime.py`：读取纸面订单账本，用公开 mark price 做实时估值，可选公共 funding history；不读取私有账户。
- `paper_health.py`：同时检查新鲜 mark、三 Alpha 纸面 fills、对账、三条权威回测引用、资金费完整性、链有效性和所有 live/order 旗标。
- `three_alpha_paper_flow.py`：顺序执行三条权威 dry-run，生成 paper cycle 和 alpha readiness 报告；`--advance-forward` 只调用公共前向驱动器。
- `l2_schedule_entrypoint.py`：Windows 调度任务的白名单入口，不能调任意模块。

当前纸面 Go/No-Go 必须同时满足：

~~~text
data quality admission = PASS
raw lineage = PASS
event projection/authority = healthy
三 Alpha 各至少 8 个已兑现前向周期
paper order preview 可用且 fills 与 intents 数量相等
reconciliation residual 在阈值内
live_allowed=false 且 order_submission_allowed=false
~~~

当前第一项和第二项未满足，因此不应使用纸面健康报告来暗示可实盘。

### 5.13 事件、审计、投影和恢复

`services/monitoring/` 提供单节点、持久化、可验证的事件基础设施：

- `DurableEventBus`：事件 log、发布幂等、完整哈希链、consumer journal、lease、offset、retry、DLQ。
- `DomainEventProjection`：逐条校验 schema、幂等 apply、投影链完整性。
- `consumer_group.py`：单节点分区协调、heartbeat、generation fencing、确定性 rebalance；不是分布式共识系统。
- `event_schemas.py`：当前制品记录 12 种事件类型、17 个 schema 定义和显式 timestamp unit/compatibility。
- `domain_event_runtime.py`、`event_health.py`：把 dry-run、风控、kill switch、数据准入、部署记录接入权威存储。

审计日志采用追加式 `prev_hash → entry_hash`；正常追加允许，截断、重排、替换、旧头恢复会失败。当前没有独立外部 WORM、跨主机 HA 或外部 heartbeat SLA；它们是顶级评估的未完成部分。

### 5.14 治理、身份和部署

- `services/governance/approval.py`：`REQUESTED → REVIEWED → APPROVED → CONSUMED`；提出、复核、批准必须是三个不同真人；subject 绑定，一次性消费，步骤带哈希链。
- `identity_authorization.py`、`strong_identity.py`：支持 OIDC/mTLS/RBAC 的契约和本地基线，但外部 IdP、证书轮换、撤销、访问复核尚未由本仓库证明。
- `artifact_admission.py`、`artifact_receipt_auth.py`：制品准入、策略哈希、receipt 和撤销检查；L2 没有真实外部 authority 时保持 `BLOCK`。
- `deployment/`：版本内容哈希、环境和批准绑定；L2 只能 `DENIED` 或 `ELIGIBLE_NOT_APPLIED`，不会写 `DEPLOYED`。
- `docs/PHASE_14_PROCESS_ISOLATION.md`：描述未来的出进程签名/隔离目标；当前只落拒绝式接缝。

### 5.15 Console 和 Research Runner

- `services/console/api/`：FastAPI 只读 GET 路由，研究运行、数据目录、Alpha、回测、组合、风险、dry-run、事件和系统完整度均有视图；每个 payload 带来源/状态标记。
- `apps/research-console/`：React + Vite + TypeScript，路由注册表是页面唯一真源；最近证据为 30/30 页冒烟通过和类型/构建检查。
- `services/research_runner/main.py`：唯一允许变更状态的研究 API；有 token、operator 记录和白名单 `run_kind`，不接订单/券商/凭证路径。
- Console 在缺少真实制品时可以使用 `SYNTHETIC_DETERMINISTIC` fallback；这是明确标记的演示数据，不应被当成真实研究结果。`services/console/api/main.py` 中 `_factor_values`/`_forward_returns` 等 fallback 是第三方必须审查的重点。

## 6. 当前三条主 Alpha 与组合 Alpha Book 的研究和部署解释

### 6.1 `carry_oi_majors`

- 经济来源：资金费现金流，不是单纯价格方向预测。
- 研究制品：`data/cache/crypto_carry_strategy.json`。
- 研究摘要：`net_sharpe_5bps=2.01`，walk-forward 标记为 `DESIGN_ROBUST`、5 blocks；这不是前向证明。
- 权威干跑：原始 Sharpe 0.4771，VT Sharpe 0.7458，原始回撤 71.07%，研究风控 `RISK_REJECTED`。
- 容量：约 1.95M AUM 参与率上限，来源 `UNCALIBRATED_PRINCIPLED`。
- 当前结论：不能进入纸面健康/实盘路径。

### 6.2 `crowd_contra`

- 定义：`cs_rank(neg(global_ls))`，对 global long/short 拥挤度做截面反向排序。
- 研究摘要：10bps 净 Sharpe 0.687；一日执行滞后 Sharpe 1.312；embargoed OOS 平均 Sharpe 1.1269；反证电池 5/5 通过。
- 警告：生存者偏差和近期衰退风险仍开放；IC panel 的某些当前快照统计显著性弱，需按原始面板复核。
- 权威干跑：原始 Sharpe 1.0697，VT Sharpe 1.095，回撤约 8.85%，研究风控 `RISK_APPROVED`；拥挤门历史上阻断 21 周。
- 容量：约 7.16M AUM 参与率上限，但冲击系数未实盘校准。
- 当前结论：是目前最像可继续前向观察的候选之一，但仍是 `0/8` 前向，未获任何 live authority。

### 6.3 `top_contra`

- 定义：`cs_zscore(neg(top_ls))`，在大币持仓数据上做反向 z-score。
- 研究摘要：price Sharpe 0.727、economic Sharpe 0.879、IC 0.0216、相对 carry 的增量 IR 0.606；专用反证 4/5 通过。
- 权威干跑：原始 Sharpe 1.5246，VT Sharpe 1.3013，回撤 20.15%，研究风控 `RISK_APPROVED`；拥挤门历史上阻断 11 周。
- 警告：容量仍只是诊断，不是晋级门；专用反证尚未完成 5/5。
- 当前结论：研究候选，不是纸面/实盘批准。

### 6.4 Alpha 不是一个数字：从候选到可交易 book 的完整路径

第三方审查时必须把下面四种对象分开，否则很容易把一条漂亮的组合净值误认为一个已经证明的 Alpha：

| 对象 | 含义 | 是否是独立 Alpha |
|---|---|---|
| Alpha 表达式 | 例如 cs_rank(neg(global_ls))，在每个时点对资产排序 | 是，产生信号边 |
| 单 Alpha book | 将表达式变成多空腿、持仓、资金费和成本后的收益流 | 是信号的可交易实现 |
| 组合 book | 把多条已有 Alpha 的收益流或持仓合并 | 不是新信号；是组合/资本配置层 |
| dry-run execution book | 目标权重经过整手、门禁、纸面成交和对账后的结果 | 是执行现实验证，不是新的 Alpha |

QUANTOS 的 Alpha 生命周期是：

~~~text
原始数据/PIT
  → Alpha DSL 表达式
  → 截面/时序评估
  → 多空腿构造
  → 价格收益 + 资金费 - 成本
  → IC/ICIR/Sharpe/DSR/PBO/FDR
  → Purged/Embargo Walk-Forward + OOS
  → 容量/相关/边际 IR/拥挤度/压力
  → AlphaCatalog 与 Lifecycle
  → Paper evidence
  →（L2 在此停止，不进入真实订单）
~~~

每期信号的基本持仓规则是：

~~~text
有效资产按 signal_t 排序
多头 = 最高 q 分位，空头 = 最低 q 分位
多头总权重 = +0.5，空头总权重 = -0.5
forward PnL 使用 t+1 价格/资金费
net PnL = price PnL + funding cash flow - fee - slippage - impact
~~~

carry 的 q=0.20，OI 定位类的 q=0.30。三条 Alpha 的表面相似之处是多空截面结构，真正不同的是输入变量和经济来源：carry 使用资金费历史平滑，crowd 使用散户多空拥挤度，top 使用大户多空持仓。

### 6.5 三条主 Alpha 的信号、经济假设和失败模式

| Alpha | 信号定义/变量 | 经济假设 | 研究证据 | 已知失败模式 |
|---|---|---|---|---|
| carry_oi_majors | 负资金费的 decay_linear(3/7/14) 与 ts_mean(7) 的截面 z-score 平均 | 做多负资金费资产、做空正资金费资产，赚资金费和可能的价差 | 5bps net Sharpe 2.01；5 个 walk-forward block 标 DESIGN_ROBUST；干跑 VT Sharpe 0.7458 | 价差回撤大；资金费不是稳定现金；成本/整手/容量压缩明显；研究风险曾 RISK_REJECTED |
| crowd_contra | cs_rank(neg(global_ls)) | 反向交易散户多空拥挤，极端拥挤的多头/空头可能被未来价格反转 | 10bps net Sharpe 0.687；一日滞后 Sharpe 1.312；embargo OOS 均值 1.1269；反证 5/5 | 主要暴露是 crowding 因子；OI 历史覆盖和生存者偏差；拥挤门曾阻断 21 周 |
| top_contra | cs_zscore(neg(top_ls)) | 反向交易大户持仓极端值 | price Sharpe 0.727、economic Sharpe 0.879、IC 0.0216、增量 IR 0.606；专用反证 4/5 | 深层压力测试下稳定性不足，不能与 crowd/carry 同等级视为已确认边 |

#### carry_oi_majors 的实际计算

carry 不是简单“买高资金费”。代码先对 funding 做负号，再对多个过去窗口做平滑和截面 z-score：

~~~text
carry_signal
= [zscore(-decay_linear(funding, 3))
 + zscore(-decay_linear(funding, 7))
 + zscore(-decay_linear(funding, 14))
 + zscore(-mean(funding, 7))] / 4
~~~

每个持有日的经济收益是：

~~~text
price_LS_return
+ funding_collected = Σ(-position_weight_i × realized_funding_i)
- turnover_i × fee_bps / 10000
~~~

因此审查者必须同时看价格腿、资金费腿和扣成本腿；只看价格方向会低估 carry 的经济来源，只看资金费又会忽略价格价差回撤。

#### crowd_contra 与 top_contra 的实际计算

global_ls 和 top_ls 是定位数据而不是价格本身。crowd_contra 对 global_ls 做负号后截面排序，top_contra 对 top_ls 做负号后截面 z-score。两者均使用 PIT 面板；信号使用时点 t 的已知定位数据，收益使用 t+1 的价格和资金费。

这两条定位 Alpha 不等价：

- global_ls 更接近广泛散户拥挤；
- top_ls 更接近大户/顶级交易者持仓；
- 组合收益低相关不代表风险因子低相关；Barra-lite 诊断显示 crowd_contra 的 crowding 因子贡献曾占总风险约 54%，必须单独监控。

### 6.6 组合 Alpha / Alpha Book：之前实际跑过的四种版本

之前确实运行过组合，但它们不是同一个东西。第三方必须分别复核，不能把四种结果混成一个 Sharpe：

#### A. 收益流分散组合：combo_book_snapshot.json

驱动器为 tools/combo_book_research.py，纯函数核心为 services/research/book_combine.py。

1. 先分别生成 carry 和 crowd_contra 的逐周 gross 收益。
2. 按 ISO 年-周对齐，只保留两边都存在的 231 个共同周。
3. 计算收益相关性。
4. 同时计算 50/50 等权和历史波动率逆波动权重。
5. 再按 VIX 与 2s10s 曲线分桶，检查分散是否只在单一 regime 中成立。

当前制品 data/cache/combo_book_snapshot.json 的关键值：

| 版本 | 权重 | 年化 Sharpe | 最大回撤 | 总收益 | 口径 |
|---|---:|---:|---:|---:|---|
| carry 单独 | — | 1.444 | 16.42% | 161.21% | gross、共同周 |
| crowd 单独 | — | 1.297 | 14.80% | 192.13% | gross、共同周 |
| 50/50 combo | 0.50/0.50 | 1.949 | 7.55% | 186.92% | gross、共同周 |
| inverse-vol combo | 0.56/0.44 | 1.977 | 7.99% | 184.83% | gross、共同周 |

组合在高 VIX/压力 regime 的 Sharpe 约 2.44，在低 VIX regime 约 1.65；2s10s 正常/倒挂分别约 2.16/1.74。这支持“regime 互补”的历史描述，但不构成择时证据，因为 regime 标签是回顾性诊断。

重要限制：这不是同一交易账本的净收益，carry 使用约 58 币网格、crowd 使用约 14 个大币网格，宇宙、规模和调仓细节不同；结果为 gross、无执行成本，只能用于相对分散比较。

#### B. 三 Alpha 资本配置组合：crypto_combo_book.json

驱动器为 tools/crypto_combo_book.py，调用真实 services/alpha/population_optimizer.py：

~~~text
输入：carry、crowd_contra、top_contra 各自的 economic return stream
→ 增量 IR / 相关性去冗余 / 最多 3 条
→ inverse-vol + CVaR 感知的资本配置
→ 每条 book 先独立扣 turnover fee，再按组合权重合成
~~~

当前制品是 24 个液态大币、2206 个日度观测：

| book | economic Sharpe | 最大回撤 |
|---|---:|---:|
| carry | 1.201 | 27.1% |
| crowd_contra | 1.277 | 16.8% |
| top_contra | 1.132 | 15.1% |
| 三 Alpha combo | 1.787 | 8.7% |

优化器选择三条，权重为：

~~~text
carry         20.98%
crowd_contra  35.11%
top_contra    43.91%
~~~

成本敏感性（按每条 book turnover 费率扫描）：

| 费率 | 组合 Sharpe |
|---:|---:|
| 2 bps | 1.671 |
| 5 bps | 1.498 |
| 10 bps | 1.210 |
| 20 bps | 0.634 |

该组合相对最佳单条 economic Sharpe 1.277 的 lift 为 +0.510；但这是研究层组合，不是 forward paper，也不是 dry-run 的完整成交成本结果。containment 明确为 REQUIRES_HUMAN_REVIEW、auto_approved=0、无 live/flags。

#### C. 统一执行的 naive combo：carry_crowd_combo

驱动器为 tools/dry_run_combo_multi.py。它把 carry 面板补上可用 OI 的 global_ls，每周构造：

~~~text
combined_weight
= 0.5 × carry_legs
 + 0.5 × crowd_contra_legs
~~~

然后进入同一条 run_multi_period_dry_run：目标权重→整数数量→预交易门→DryRunAdapter→纸面成交→价格 MTM/资金费→PnL 对账→审计事件。成本设为约 9bps，之后再做 vol-target 曲线。

历史运行结论（见 docs/PROJECT_STATE.md 与 docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md）：

| book | RAW Sharpe / 回撤 | VT Sharpe / 回撤 |
|---|---:|---:|
| carry | 0.45 / 77% | 0.65 / 22% |
| crowd_contra | 1.21 / 12% | 1.38 / 11% |
| naive combo | 0.58 / 56% | 1.20 / 15% |

它确实比 carry 单独更好，但仍差于 crowd_contra 单独。原因不是“组合算法失效”，而是固定 50/50 的名义腿在 58 币 carry 宇宙和 14 币 crowd 宇宙上被 carry 的结构主导；gross 相关性带来的分散，在整手、成本和 vol-target 后没有完全兑现。

#### D. PIT 风险平衡执行组合：carry_crowd_risk_parity

驱动器为 tools/dry_run_combo_riskparity_multi.py，是对 C 版本的修正：

1. 每周先得到 carry sleeve 与 crowd sleeve 的各自多空腿。
2. 用仅过去的 trailing 周收益，lookback 8 周、最少 4 个观测，计算逆波动权重。
3. 达到至少 20 个历史观测时，优先调用 services/portfolio/capital_allocator.py，带 CVaR 惩罚；若配置器 BLOCKED/数据不足，则 fail-closed 回退到 PIT inverse-vol。
4. 用 sleeve 权重合成实际 instrument weights。
5. 再走同一个 dry-run pipeline、9bps 成本、资金费和 vol-target。

历史结论是三种执行组合（naive merge、risk-parity merge、portfolio-of-books）VT Sharpe 约 1.20、1.14、1.21，均低于 crowd_contra 单条 VT Sharpe 约 1.38。也就是说，风险平衡版本修复了权重方法的前视风险和 carry 主导问题，但没有凭空创造新的 edge。

当前数据库状态须单独核对：本次审查时 data/cache/dry_run.db 直读只看到 carry、crowd_contra、top_contra 的重复运行行，未看到 carry_crowd_combo 或 carry_crowd_risk_parity 行。因此上面 C/D 的数字属于仓库历史文档/驱动器记录，第三方若要求“当前可重放制品”，必须重新运行对应驱动器并保存新 DryRunRun、event outbox 和 hash；不能把缺失的当前数据库行当成存在。

### 6.7 已搜索但没有成为生产候选的 Alpha

Alpha 工厂和 OI 因子研究不是“找到多少都算成功”，而是必须把假阳性写出来：

| 候选/类别 | 结果 | 为什么不能当作已确认边 |
|---|---|---|
| crowd_follow、top_follow、taker/OI 变化类 | 进入因子研究/比较池 | 没有同时满足增量 IR、OOS、成本和反证门 |
| top_contra | 历史上有正结果，保留为研究候选 | 深压测和 survivor-free 视角下脆弱，证伪 4/5 |
| cs_rank(add(cs_scale(volume),ts_rank(close,14))) | 机器搜索得到漂亮 IS/OOS 片段 | 选择膨胀后更脆；更严格 lag+cost/截面 IC 电池最终 REFUTED |
| 宽宇宙动量 momentum_7_wide | IC 约 -0.063 | 宽宇宙并未产生稳定价格边 |
| 4h/小时级价格、GP、量能因子 | null 或 OOS 负 | 样本内好看但无窥视 OOS 证伪 |
| 其他自动搜索候选 | 留在研究/实验制品 | 非人搜索只产生候选，不得自动晋级 |

当前最诚实的 Alpha 结论是：真正经过较完整历史压力和执行现实检查的主边主要是 carry 与 crowd_contra；top_contra 是弱 lead/研究候选；组合 book 是资本配置层，不是第四条独立预测边。

## 7. 当前顶级能力评估与缺口

当前 `evidence/top_tier_assessment.json` 的关键结果：

| 维度 | 当前成熟度/状态 | 主要事实 |
|---|---|---|
| AI Agent 边界 | M1；非人类边界 PASS，模型风险 FAILED | Agent 不能批准/交易；外部模型注册、提示词/红队证据不足 |
| Alpha Research | M3；多资产研究 PASS，trial ledger PARTIAL | 因子/搜索/验证真实存在；跨会话全局试验历史仍需补齐 |
| Data Moat | M1；PIT 合同 PASS，供应商深度 MANUAL | PIT/宇宙/版本有代码；缺许可 tick/L2/历史成员与 SLA |
| Execution Microstructure | M1；dry-run reconciliation PASS，SOR MANUAL | bar/next-open/冲击有模型；无 tick/LOB/多场所 SOR |
| Frontend Console | M3；真实只读面和注册表 PASS | UI 可视化多为真实制品；缺失子系统明确显示为 missing |
| Governance/Security | M1；强身份 FAILED | 多方批准/哈希链有代码；外部 IdP、mTLS、密钥托管和撤销未证明 |
| Platform Engineering | M2；CI PASS，Supply Chain FAILED | 依赖固定和扫描存在；外部签名 receipt、跨主机复现、独立 authority 未完成 |
| Portfolio Construction | M3；load-bearing 约束 PASS | 优化、风险、容量、归因原语真实；部分仍为诊断/未校准 |
| Reliability/Operations | M3；恢复演练 FAILED | 本地事件/备份/恢复代码存在；缺 30 天带外证据、WORM、外部心跳 |
| Risk Management | M4；fail-closed gates PASS | 研究风险、预交易门、kill switch、对账链真实；仍是 L2 研究环境 |
| Validation/Model Risk | M1；抗过拟合 PASS，challenger FAILED | DSR/PBO/Purged-WF/泄漏等在代码；独立 challenger 和外部模型审查不足 |

### 7.1 未完成项的分类

**L2 内可继续做：**

- 修正并重建 raw lineage backfill，使数据准入从 `BLOCK` 变为 `PASS`。
- 持续每日/每周前向纸面，累计至少 8 个完整周期；实际独立审查建议 6–12 个月而不是只满足 8 周。
- 完成所有三 Alpha 的 paper fills、mark、funding 和 preview 链。
- 完善全局 trial ledger、独立 challenger、模型/提示词注册和数据漂移报警。
- 用真实逐标的 ADV、费用、资金费、拒单和延迟资料替换原则性成本区间。

**需要外部资源：**

- 许可 tick/L2/历史成分/另类数据和供应商 SLA。
- 外部 OIDC、mTLS、密钥轮换、撤销和访问复核。
- 独立 WORM/对象版本、外部 Ed25519 checkpoint、跨主机 scheduler/heartbeat。
- 生产可观测性、on-call、独立恢复演练。

**明确属于 post-L2，不能在本仓库自行“补开”：**

- 真实券商/CTP/FIX/交易所 API 适配器。
- 真实订单提交、资金、清算、保证金、借贷、SOR 和生产微结构。
- 将 readiness 从 `L2_DRY_RUN_ONLY` 提升到允许真实资金的等级。

## 8. “一个人的量化盈利系统”判断标准

第三方应把“能运行”与“能盈利”拆开审查：

| 问题 | 当前答案 |
|---|---|
| 一个人能否在本机运行研究、回测和干跑？ | 可以，前提是依赖、缓存和环境可复现 |
| 一个人能否看到明确的候选 Alpha 和风控/成本结果？ | 可以，三条主 Alpha 有权威干跑制品；组合 Alpha/Book 有历史研究和部分统一执行制品 |
| 是否已经有足够的真实前向证据证明候选可持续？ | 否，当前三条均 0/8，且质量血缘门阻断 |
| 是否能在真实交易所下单？ | 否，当前没有券商、私有凭证或订单提交路径 |
| 历史结果是否足以保证个人盈利？ | 否；历史结果依赖数据范围、成本、资金费、容量、regime 和实施一致性 |
| 是否接近顶级机构的全栈能力？ | 研究过程和安全工程较完整，但数据、执行、外部治理、模型风险和运营证据仍明显不足 |

真正可以支持“个人小资金验证”的最低证据组合应至少包括：

1. 独立第三方从原始数据重新生成信号、成交和 PnL，并核对每个时间戳没有前视。
2. 训练/选择期、严格 OOS 期和真实前向纸面期完全分离；所有试验数进入 DSR/FDR。
3. 前向纸面在多个市场状态中达到足够长度，且成本、资金费、拒单、延迟和 mark 缺失都被记录。
4. 真实执行环境先经过 testnet/demo 与独立 reconciliation；生产权限由外部多方人工审批和出进程签名控制。
5. 有明确的最大损失、最大回撤、单名/总敞口、资金/借贷和 kill-switch 操作规程。

在上述证据完成前，任何“理论上能赚钱”的表述都只能是条件性研究假设：

~~~text
真实期望收益
= 信号边际
 - 手续费
 - 滑点
 - 盘口冲击
 - 延迟/排队损失
 - 资金费与借贷成本
 - 数据缺失/停牌/清算损失
 - 模型衰退与参数重估损失
~~~

## 9. 第三方复现与审查流程

第三方审查应先固定版本和制品，再运行系统。不要只看网页截图、单次净值或仓库 README；所有结论都应能回到代码、输入数据、事件日志和可重建的 JSON 制品。

### 9.1 固定审查对象

1. 在审查工作站克隆仓库，记录 Git commit、Python/Node 版本、操作系统、时区和依赖锁定文件。
2. 核对本报告第 10 节的 SHA-256；若源码或证据制品哈希不一致，先停止结论发布。
3. 将 `data/`、`evidence/` 和 `.runtime/` 中的输入快照视为审查对象的一部分，不能用当前网络数据悄悄替换历史输入。
4. 将所有时间转换为 UTC 进行核对；展示层可使用 Asia/Shanghai，但不能改变交易日切分。

### 9.2 建议的最小命令序列

~~~powershell
cd H:\code\QUANTOS
python --version
pip install -r requirements-dev.txt
python scripts/assess_top_tier.py --check
python scripts/validate.py
~~~

随后重新生成三 Alpha 的研究/干跑制品：

~~~powershell
python -m tools.three_alpha_paper_flow
python -m tools.l2_paper_operator --no-forward
python -m tools.paper_health
~~~

如果要推进真实前向纸面阶段，必须在确认原始数据血缘、时间覆盖和事件制品完整后，显式运行：

~~~powershell
python -m tools.three_alpha_paper_flow --advance-forward
~~~

`--advance-forward` 不是绕过门禁的开关；它只能请求推进，数据质量、血缘、周期覆盖和风险门仍必须通过。第三方应保存命令输出、退出码、生成时间和所有新文件哈希。

本次审查环境中 `python scripts/validate.py` 在 304 秒内没有完成输出，相关测试进程已被回收。因此本报告不把本次统一门禁标记为 PASS；第三方应在干净环境重新完成该命令，并把完整日志作为独立附件。

### 9.3 逐层复核

1. **数据层**：从原始行情、资金费率和 OI 快照重建 canonical bars；核对 `event_time`、`knowledge_time`、source version、缺失值和存活宇宙。
2. **信号层**：解析 Alpha DSL，确认每个窗口只读取当前及过去观测；对表达式 canonical serialization 和 `alpha_id` 做哈希复核。
3. **回测层**：检查 close-to-next-close 或 close-to-next-open 的成交约定、整手取整、部分成交、资金费、费用和冲击；逐笔核对 PnL reconciliation residual。
4. **验证层**：重新计算 IC/ICIR、Sharpe、Deflated Sharpe、PBO、BH-FDR、purged/embargoed CV、walk-forward、OOS、边际 IR、相关性和容量。
5. **生命周期层**：检查 evaluator 输出是否通过 evidence guard；核对 AlphaCatalog 状态、试验次数、审计哈希链和维护/退役转换。
6. **组合与风险层**：以同一份目标权重重跑组合构建、容量、集中度、相关性、回撤、压力和 regime 检查；确认任一异常都 fail-closed。
7. **执行层**：从目标权重到 order intent、整数数量、预交易门、kill-switch、纸面成交、对账和审计事件逐步追踪。
8. **运营层**：重启事件总线和 consumer journal，检查 offset、lease、DLQ、projection hash chain、备份恢复和告警。
9. **前端层**：调用只读 API，核对 `snapshot_kind`/`provenance`，确保页面展示的是 REAL artifact 而不是合成 fallback。

### 9.4 当前制品的关键差异

当前存在一个必须向第三方明确说明的状态差异：

- `evidence/three_alpha_paper_flow.json` 的操作员干跑阶段为 PASS，说明流程可以产生干跑制品。
- `data/cache/paper_health_10k.json` 当前为 `paper_ready=false`，`paper_mark_ready=false`，且三条 Alpha 的前向填充均为空。
- `evidence/data_quality_admission.json` 的 raw lineage backfill 为 BLOCK，原因包含 `LINEAGE:BACKFILL_DAYS_MISMATCH` 以及价格/资金费/OI/archive artifact mismatch。

因此，“操作流程跑通”不能被解释为“纸面前向已经准备好”，更不能解释为“可以实盘”。

## 10. 代码、制品与证据索引

### 10.1 权威文档

| 内容 | 文件 |
|---|---|
| 总体蓝图、11 层和阶段边界 | [QUANTOS_MASTER_BLUEPRINT.md](../../QUANTOS_MASTER_BLUEPRINT.md) |
| 当前架构和数据流 | [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) |
| 当前项目状态 | [docs/PROJECT_STATE.md](../../docs/PROJECT_STATE.md) |
| 开发计划和阶段门 | [docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md](../../docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md) |
| Agent/治理硬约束 | [AGENTS.md](../../AGENTS.md) |

### 10.2 承重实现入口

| 子系统 | 主要文件 | 审查重点 |
|---|---|---|
| 安全配置 | [quantos/contracts/config.py](../../quantos/contracts/config.py) | L2 flags、live_trading_armed、研究安全断言 |
| Alpha DSL | [services/research/alpha_expression.py](../../services/research/alpha_expression.py) | 算子、窗口、序列化、PIT |
| 回测引擎 | [services/research/backtest_engine/engine.py](../../services/research/backtest_engine/engine.py) | 下一根成交、无前视、PnL 恒等式 |
| 事件驱动回测 | [services/research/backtest_engine/event_engine.py](../../services/research/backtest_engine/event_engine.py) | next-open、成交、资金费、公司行为 |
| Alpha evaluator | [services/alpha/evaluator.py](../../services/alpha/evaluator.py) | IC、成本、DSR、CV、evidence guard |
| 研究会话 | [services/intelligence/research_session.py](../../services/intelligence/research_session.py) | 非人研究代理、搜索策略、持久化 |
| 研究风险门 | [services/risk/research_risk/domain/gate.py](../../services/risk/research_risk/domain/gate.py) | 9 个风险引擎、collect-all、fail-closed |
| 组合纯核 | [services/research/book_combine.py](../../services/research/book_combine.py) | ISO 年-周对齐、等权/逆波动、无前视纯函数 |
| 组合研究驱动器 | [tools/combo_book_research.py](../../tools/combo_book_research.py) | gross 分散、宏观 regime 分桶、非晋级诊断 |
| 三 Alpha 组合优化 | [tools/crypto_combo_book.py](../../tools/crypto_combo_book.py) | 增量 IR、相关去冗余、inverse-vol/CVaR 资本配置 |
| 统一组合干跑 | [tools/dry_run_combo_multi.py](../../tools/dry_run_combo_multi.py) | naive 50/50 组合经过真实干跑执行骨架 |
| 风险平衡组合干跑 | [tools/dry_run_combo_riskparity_multi.py](../../tools/dry_run_combo_riskparity_multi.py) | PIT trailing 权重、资本配置器、fail-closed 回退 |
| 预交易门 | [services/risk/pre_trade/gate.py](../../services/risk/pre_trade/gate.py) | 安全配置、kill-switch、意图绑定 |
| 干跑流水线 | [services/execution/dry_run_pipeline.py](../../services/execution/dry_run_pipeline.py) | 权重→意图→门→纸面成交→对账→审计 |
| 纸面 PnL | [services/research/paper_trading/domain/pnl.py](../../services/research/paper_trading/domain/pnl.py) | realized/unrealized/costs 对账 |
| 券商接缝 | [services/trading/broker_adapter.py](../../services/trading/broker_adapter.py) | L2 下 live adapter 结构性拒绝 |
| 多方批准 | [services/governance/approval.py](../../services/governance/approval.py) | 三个不同真人、subject 绑定、一次性消费 |
| 出进程签名接缝 | [services/governance/signing.py](../../services/governance/signing.py) | L2 默认无签名权威、缺失即拒绝 |
| 事件存储/投影 | [services/monitoring/event_bus.py](../../services/monitoring/event_bus.py) | 单节点持久化、重试、DLQ、projection |
| 只读控制台 | [services/console/api/main.py](../../services/console/api/main.py) | REAL/SYNTHETIC provenance、契约输出 |
| 研究操作员 | [services/research_runner/main.py](../../services/research_runner/main.py) | 研究白名单、无订单/券商/凭证路径 |

### 10.3 当前证据制品

| 制品 | 作用 | 当前结论 |
|---|---|---|
| [evidence/top_tier_assessment.json](../../evidence/top_tier_assessment.json) | 22 项顶级能力机器评估 | `NOT_TOP_TIER`，14 PASS、5 FAILED、1 PARTIAL、2 MANUAL |
| [evidence/alpha_paper_cycle.json](../../evidence/alpha_paper_cycle.json) | 三条 Alpha 研究与纸面生命周期 | `BLOCKED_BY_DATA_AND_EVENT_GATES`，前向 0/8 |
| [evidence/three_alpha_paper_flow.json](../../evidence/three_alpha_paper_flow.json) | 三 Alpha 操作员流程 | 干跑 PASS；public forward SKIPPED |
| [evidence/data_quality_admission.json](../../evidence/data_quality_admission.json) | 数据质量和血缘准入 | raw lineage `BLOCK` |
| [data/cache/paper_health_10k.json](../../data/cache/paper_health_10k.json) | 10k paper operator 健康快照 | `paper_ready=false` |
| [data/cache/l2_paper_operator_10k.json](../../data/cache/l2_paper_operator_10k.json) | L2 纸面操作流水 | 下游 paper health 未就绪 |
| [data/cache/dry_run.db](../../data/cache/dry_run.db) | 干跑运行、成交、对账和净值 | 当前直读到三条主 Alpha 重复运行；组合行需重跑驱动器确认 |
| [evidence/dry_run_books.json](../../evidence/dry_run_books.json) | dry-run 书籍摘要和对账容差 | 当前制品只列三条主 Alpha |
| [data/cache/combo_book_snapshot.json](../../data/cache/combo_book_snapshot.json) | carry/crowd 共同周收益流组合 | REAL_COMBINED_BOOK；gross 相对分散诊断 |
| [data/cache/crypto_combo_book.json](../../data/cache/crypto_combo_book.json) | 三 Alpha optimizer 组合 | 研究输出；明确 REQUIRES_HUMAN_REVIEW |

### 10.4 本次审查记录的 SHA-256

以下哈希用于第三方确认审查对象没有被静默替换：

| 文件 | SHA-256 |
|---|---|
| `QUANTOS_MASTER_BLUEPRINT.md` | `ba34b79c2d5c2a7aaa8d777d62f80e4ca9cfd26b415f794adf7c783390de5d92` |
| `docs/ARCHITECTURE.md` | `a6c140a23c255c4d152dcae185e6352d0ba459e570a7219d6dbb80815010fde4` |
| `docs/PROJECT_STATE.md` | `266636da46cc6bd2d92839d4fb44e1b0686ae84632546155e25d75230ced2107` |
| `quantos/contracts/config.py` | `b59e44d7cc6f2afe03ea4e4a37e775cd9759d0ee2744d71279b3387b59b516a0` |
| `services/intelligence/research_session.py` | `0230589bad83c68312c2f1c4061d89b9209ebf33c6fa137fb59ad08d1207c87c` |
| `services/research/validation/pipeline.py` | `27278f4157d77c181b8607614bbd2ca108032379f3a06b64955c30a3a76d5f5f` |
| `services/research/book_combine.py` | `739e1f9fd4befbcd248d2ee07beed188cace8142e487d9f9a3ee412f7bbed314` |
| `services/execution/dry_run_pipeline.py` | `72e9d73c148377f2dbe5e9e1d4d51568dc6e92e3b662b43e7d6012d64b3881c2` |
| `services/risk/pre_trade/gate.py` | `08b80ae89fce524ee05ae8f8207fcda105e18c6770952b389facb9d0d26a916d` |
| `services/risk/research_risk/domain/gate.py` | `d364a1489330afa9eb27f86dadca7bdb823f7e604315bf95359cd41eaa68300e` |
| `services/governance/approval.py` | `b3d34ada338f52fd13faf6e2e1b879b6b4d0cc479432cf45d3eede95c6726b4f` |
| `services/governance/signing.py` | `82cdf0a33ba46bb00c0fb1c2e2abb35c1b799dc2588b03e0b77004ad3c35c8e2` |
| `tools/combo_book_research.py` | `bf27a3724e758273b85f2bafdaa657676b483716c0be7e484f0ffedaee926597` |
| `tools/crypto_combo_book.py` | `9b6232600036299871d2a5008bf502e182581485840d2cfe955522d90d566d58` |
| `tools/dry_run_combo_multi.py` | `03b620d7a2a1c3a363c16a6cdc0342e20a5fabe079ca5e4c91be42c95be036ed` |
| `tools/dry_run_combo_riskparity_multi.py` | `5f9be79226c6fd6321420dce393882de695c27cf603b88e8bbf60513d801982c` |
| `evidence/top_tier_assessment.json` | `69c4686725f54d19bedc195c42b85f139f068607d9777fe2e697c50c0237eefb` |
| `evidence/alpha_paper_cycle.json` | `9252319c7114290d42ff3bd573a05d137954116a95a1638335226dec2e250fa3` |
| `evidence/three_alpha_paper_flow.json` | `f2e725e12cfde034424f160cb7e8e13deb00ff75c6e90a563aa7eb5a78211b78` |
| `data/cache/paper_health_10k.json` | `6c97df127706ce2c1486f22c82331f1b324608da82208a065f8ce5fe1ec7d1b0` |
| `data/cache/l2_paper_operator_10k.json` | `cf4423be0e435a4835ca81b8d5526f6db2bfebc4bbe6cee4b4c640e3d39f1ffa` |
| `data/cache/combo_book_snapshot.json` | `cf31fb673b0ed40383771476d1d9f86bfe7e045d1da3c64356f62ca303a58b35` |
| `data/cache/crypto_combo_book.json` | `d40301e343298054f499917d941b488d53fd986965b0024bfffbf59dd0c2d5f9` |
| `evidence/dry_run_books.json` | `cb315c69faa89dc8062f3b7551db54dadea1975520b5cd5001a76a1108044d0a` |

> 注意：上表中的运行生成制品（尤其是 `three_alpha_paper_flow.json`、`paper_health_10k.json`、`l2_paper_operator_10k.json`、`combo_book_snapshot.json` 和 `crypto_combo_book.json`）可能随一次操作员运行而变化。第三方必须对当前审查目录重新执行 `Get-FileHash`；若不一致，应重新固定整套证据，而不是只更新某一行。

## 11. 第三方审查清单

### 数据与时间

- [ ] 原始数据供应商、授权范围、时区、延迟、修订规则和停牌/下架规则已记录。
- [ ] 每条观测有 `event_time` 与 `knowledge_time`，并能证明训练时只读取当时已知信息。
- [ ] 存活宇宙、退市/下架资产和样本选择没有 survivorship bias。
- [ ] 价格、资金费、OI、成交量的单位和语义已由 schema 强制，而非靠人工约定。
- [ ] 数据缺口、backfill、归档和版本冲突会阻断准入，而不是静默填补。

### Alpha 与统计验证

- [ ] Alpha 表达式可序列化、可复现、带稳定 `alpha_id`，所有滚动窗口逐算子检查。
- [ ] 信号和收益严格错位；回测成交至少在下一根 bar，不允许 same-bar lookahead。
- [ ] 训练、验证、OOS、forward paper 的日期边界和 embargo 明确，且没有根据 OOS 结果反复调参。
- [ ] DSR/PBO/多重检验/FDR 的 trial ledger 是全局、不可篡改且覆盖所有搜索尝试。
- [ ] 不能仅凭 IC、单段 Sharpe 或单一 regime 宣称可交易。
- [ ] 有独立 challenger/model-risk 评审，能解释失败模型和停止规则。

### 组合、成本与执行

- [ ] 权重、整数数量、最小名义、集中度、相关性、杠杆和容量约束可重算。
- [ ] 手续费、滑点、冲击、资金费、延迟、排队、部分成交和拒单均进入净 PnL。
- [ ] ADV/volume 的单位没有重复换算；参与率上限和容量区间有校准区间。
- [ ] 纸面成交使用与回测一致的订单语义，且 PnL reconciliation residual 接近零。
- [ ] kill-switch 具备锁存、持久化、重启恢复和独立复核证据。
- [ ] 真实券商适配器、凭证和订单提交在 L2 代码路径中不存在或结构性拒绝。

### 治理与运营

- [ ] 非人 AI 只能生成研究产物，不能直接触发批准、重置 kill-switch 或下单。
- [ ] 晋级需要三名不同真人、subject 绑定、一次性消费和完整哈希链。
- [ ] 出进程签名、密钥托管、证书轮换、撤销和身份生命周期已由外部系统提供。
- [ ] 事件日志、consumer journal、projection、DLQ、备份和恢复演练已完成。
- [ ] 供应链、CI、依赖、镜像、SBOM 和审计日志具有独立签名或可信存档。
- [ ] 控制台页面标明数据 provenance，任何 synthetic fallback 都显式可见。

### “个人可盈利”结论门

- [ ] 至少一个未参与开发的审查者从原始数据重建结果。
- [ ] 三条 Alpha 在前向纸面期而非已知历史期中完成目标周期（当前要求 8 个周期）。
- [ ] 前向期跨越多个 regime，净成本、资金费、容量和拒单都真实记录。
- [ ] 纸面结果达到预先注册的收益/回撤/换手/容量阈值，且不是靠事后选样本。
- [ ] 真实资金只在单独 testnet/demo 验证后，经过人工批准和最小限额，才可讨论生产部署。

## 12. Lance-1m 外部 Alpha 附录

本附录用于审查用户提供的外部策略包，不把它误认为 QUANTOS 三条已注册 Alpha 的前向证据。

- 输入包：`I:/qqFILE/Lance-1m-技术包.zip`
- 输入包 SHA-256：`8FC4306A8A59494D8EB428C2D049FA5A25847AC1112601CECA0E7447F8D4BD42`
- 复测实现：[tools/lance_1m_package_backtest.py](../../tools/lance_1m_package_backtest.py)
- 复测制品：[data/cache/lance_1m_package_backtest.json](../../data/cache/lance_1m_package_backtest.json)
- 详细报告：[docs/reports/lance-1m-technical-package-retest-2026-07-23.md](../../docs/reports/lance-1m-technical-package-retest-2026-07-23.md)
- Go 原实现未在本机编译（缺少 Go 编译器）；当前是逐逻辑移植的 Python 复测，第三方应优先用原始 Go 工程复编译核对。

复测结果（净收益口径含进出场佣金；没有把它当作完整生产成本模型）：

| 窗口 | 1 bp | 2 bp | 3 bp |
|---|---:|---:|---:|
| 精确窗口 | +47.996% | +9.236% | -19.373% |
| 全部样本 | +85.540% | +15.687% | -27.869% |

解释：

1. 结果对成本极其敏感；1 bp 有正收益不等于扣除完整滑点、冲击、延迟、盘口排队后仍有边。
2. 技术包中的报告数字（约 +40.68%、+1.1%）与移植复测数字存在 source/report drift，必须由第三方基于原始代码、原始数据和固定 commission 规则重新裁决。
3. 当前分类只能是 `CONDITIONAL_POSITIVE_HISTORICAL_ALPHA`：说明在某些历史/成本假设下存在可检验的正向结果，不代表稳健、可扩容或可实盘。
4. 该 1 分钟策略尚未通过 QUANTOS 的完整 PIT 数据准入、walk-forward、forward paper、容量/延迟和独立重建门，因此不能与三 Alpha 的干跑结果直接合并为“系统盈利证明”。

## 13. 最终结论

QUANTOS 当前已经是一个相当完整的研究、验证、风险和纸面执行骨架：它能把数据、Alpha 表达式、回测、统计验证、组合约束、研究风险门、预交易门、纸面成交、PnL 对账、审计事件和只读控制台串成一条可检查的链路；三条主 Alpha 有历史干跑制品，carry/crowd 的收益流组合、三 Alpha optimizer book 以及统一执行组合也曾经运行过。

但第三方必须给出清晰的否定边界：

- 当前运行级别是 `L2_DRY_RUN_ONLY`，不是实盘系统。
- 当前顶级能力评估是 `NOT_TOP_TIER`（14/22 通过，仍有失败、部分和人工证据项）。
- 三条主 Alpha 的前向纸面周期是 0/8；组合 Alpha 也没有独立的 forward paper 证据，当前数据血缘准入为 BLOCK，`paper_ready=false`。
- 历史回测/干跑的正收益只能证明“在给定样本、成本和实现下可复现”，不能证明未来收益或个人可持续盈利。
- 要成为“一个人的量化盈利系统”，最短路径不是继续堆叠页面，而是先修复数据血缘与事件准入，完成严格 forward paper，再由独立审查者重建净收益、容量、回撤和失败模式；之后才是 testnet/demo、外部身份/签名、恢复演练和最小权限的生产部署。

因此，本报告支持的专业判断是：**QUANTOS 具备继续验证 Alpha 和运行纸面交易的工程基础，但目前没有足够证据宣称能够做到一个人的量化盈利系统，更没有条件宣称可以安全实盘。**
