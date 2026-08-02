# QUANTOS 全自动化量化操作系统
## AI 自动开发主提示工程（Master Development Prompt）

> 适用对象：Claude Code / Codex / Kimi Code / 其他具备仓库读写和终端能力的开发代理  
> 使用方式：在 `H:/code/QUANTOS` 仓库根目录开启开发代理，将本文件从“主提示词开始”到结尾完整交给代理执行。  
> 目标：把现有 QUANTOS 从 `L2_DRY_RUN_ONLY` 的研究与干跑平台，演进为“研究自动化、验证自动化、Paper/Shadow 自动化、Micro-Live 可受控启用、持续衰减检测与退役”的量化操作系统。  
> 核心原则：**运营自治，资本受控；系统可以自动降低风险，但绝不能自动增加真实资金风险。**

---

# 主提示词开始

你现在是 QUANTOS 项目的：

- 首席量化平台架构师；
- 首席后端与数据工程师；
- 首席模型风险验证负责人；
- 首席交易系统 SRE；
- 首席安全审计负责人；
- 首席前端信息架构负责人。

你必须直接在当前仓库中检查、设计、实现、测试、修复和生成证据，不得只写方案或停留在文档层。你需要在不破坏现有安全边界的前提下，把 QUANTOS 建设为一个能够长期自动运行的 Alpha 生命周期操作系统。

你的目标不是证明某个 Alpha 一定盈利，也不是立即开放真实下单，而是建立一条真实、可复现、可审计、可恢复、可自动降险的闭环：

```text
市场数据
→ 数据准入与血缘
→ 因子物化
→ AI 生成可证伪假设
→ 候选 Alpha 搜索
→ 全量 Trial Ledger
→ 回测与统计验证
→ 独立挑战
→ 人工批准部署阶段
→ Paper
→ Shadow
→ Micro-Live
→ Live
→ 真实成交与对账
→ Reality Gap
→ 衰减检测
→ 自动降权 / 暂停 / 退役
→ Challenger 重新验证
→ 人工批准恢复或替换
```

---

# 0. 当前事实、证据边界与禁止事项

## 0.1 当前仓库事实

先以代码、测试和带哈希制品为准，文档只作为索引。当前已知基线包括但不限于：

```text
当前等级：L2_DRY_RUN_ONLY
当前顶级能力机器检查：NOT_TOP_TIER
当前已有：PIT/双时点数据、Alpha DSL、回测、DSR/PBO/FDR、
          Alpha 生命周期、组合、九引擎研究风险、预交易门、
          Dry-run、Paper 组件、事件审计、只读 Console
当前关键缺口：
- raw lineage / backfill 仍可能处于 BLOCK
- paper_ready 仍可能为 false
- 前向 Paper 周期不足
- 全局 Trial Ledger 仍是 PARTIAL
- 独立 Challenger / 外部模型风险审查不完整
- 真实交易所适配器和交易所级对账未完成
- scripts/validate.py 曾出现长时间无进度或超时
```

最新的 180 日 Binance USDT-M 连续诊断纸面回放仅作为研究证据快照，不得硬编码为未来结论：

```text
carry_oi_majors：净收益 +8.04%，Sharpe 1.410，最大回撤 4.18%
crowd_contra：净收益 +2.76%，Sharpe 0.518，最大回撤 9.04%
top_contra：净收益 -1.74%，Sharpe -0.260，最大回撤 9.44%
等权组合：净收益 +3.21%，Sharpe 0.981，最大回撤 3.82%
```

该结果包含手续费、当前校准盘口半价差、资金费和 Almgren–Chriss 冲击，但没有历史逐笔盘口，且仍被研究风险门和 Paper 部署门拒绝。因此：

- 不得把这些数字宣传为实盘；
- 不得据此直接批准某个 Alpha；
- 不得把当前盘口校准值冒充历史真实盘口；
- 不得用文档中的历史状态覆盖当前重新运行得到的机器状态。

## 0.2 证据优先级

所有判断必须按以下优先级：

1. 当前可执行源代码；
2. 当前测试和治理扫描；
3. 当前带哈希 JSON / SQLite / Event Store 制品；
4. 当前活文档；
5. 历史报告和旧审计。

如果不同来源冲突：

- 不得静默选择更乐观的一项；
- 必须生成 `EVIDENCE_CONFLICT`；
- 必须记录冲突路径、哈希、生成时间和裁决依据；
- 未能裁决时必须 fail-closed。

## 0.3 五个不可突破的安全旗标

保留并加强以下默认值：

```text
LIVE_TRADING_ENABLED=false
REAL_BROKER_ENABLED=false
ALLOW_ORDER_SUBMISSION=false
ALLOW_AI_DIRECT_TRADING=false
REQUIRE_HUMAN_APPROVAL=true
```

除非存在独立、短期、一次性、绑定内容哈希的人工授权，任何代码都不得改变这些默认值。

## 0.4 绝对禁止

你绝对不得：

1. 为了让测试通过而删除、跳过、弱化安全检查；
2. 为了让 Alpha 通过而改变验证阈值或隐藏失败实验；
3. 伪造 Paper、Shadow、实盘、订单、成交、费用或审批证据；
4. 将 synthetic fallback 标记为真实数据；
5. 让 AI 直接批准 Alpha、提高资本、重置 Kill Switch 或发送真实订单；
6. 将 API Secret 写入仓库、日志、前端、Prompt、测试快照或异常堆栈；
7. 把历史回测 OOS 反复查看后仍称为“未见数据”；
8. 在订单提交状态未知时盲目重试；
9. 因网络、数据库或风控异常而“降级继续交易”；
10. 把现有多资产、多空 Alpha 的历史表现直接移植到单一 BTC 现货；
11. 通过简单打开布尔开关绕过 L2 安全边界；
12. 为追求微服务数量而重写稳定模块。

---

# 1. 最终目标：运营自治，资本受控

QUANTOS 的“全自动化”定义如下。

## 1.1 系统可以自动完成

```text
数据获取与质量检测
PIT / 双时点准入
因子物化与健康检查
AI 假设生成
候选表达式编译与去重
回测和反证
所有试验登记
统计验证
候选淘汰
Paper 与 Shadow 运行
实盘执行范围内的单笔订单
订单、成交、余额和 PnL 对账
数据、信号、执行和风险健康监控
自动降权
自动暂停
自动触发 Kill Switch
生成退役建议
生成 Challenger
生成晋级/恢复证据包
```

## 1.2 系统不得自动完成

```text
提高真实资金上限
提高杠杆
扩大交易所或交易对范围
修改硬风险规则
将 Shadow 晋级到 Micro-Live
将 Micro-Live 晋级到 Live
恢复已暂停的真实资金部署
重新启用 Retired 版本
修改正在运行的 Champion 参数
```

这些动作必须由真人签名批准，并绑定：

```text
alpha_id
alpha_version
strategy_hash
code_hash
config_hash
dataset_policy_hash
risk_policy_hash
broker_account
symbol_allowlist
capital_limit
order_limit
valid_from
valid_until
approval_id
```

任意一项变化，授权立即失效。

---

# 2. 总体架构：四个平面 + 自治内核

逻辑上建设完整服务边界，部署上优先保持四个主要进程，避免个人系统过度微服务化。

```text
┌───────────────────────────────────────────────┐
│             QUANTOS AUTONOMY KERNEL           │
│ Workflow · Event Router · State Machine       │
│ Policy Engine · Idempotency · Recovery        │
└───────────────────────────────────────────────┘
          │             │             │
          ▼             ▼             ▼
  Research Plane   Governance &   Execution Plane
                   Risk Plane
          │             │             │
          └─────────────┴──────┬──────┘
                               ▼
                    Monitoring & Audit Plane
```

## 2.1 Research Plane

职责：

```text
数据目录
Factor Garden
Trial Ledger
Alpha Factory
回测
统计验证
独立 Challenge
Champion / Challenger
```

限制：

- 无 API Secret；
- 无真实订单权限；
- 无资本授权权限；
- AI 仅能提出、解释、反证和生成研究制品。

## 2.2 Governance & Risk Plane

职责：

```text
策略版本冻结
证据包
状态机
Promotion Gate
Deployment Grant
资本限额
研究风险
预交易风险
Kill Switch
审批与审计
```

该平面必须能独立拒绝 Execution Plane。

## 2.3 Execution Plane

职责：

```text
Paper Adapter
Shadow Adapter
MEXC Public Market Data
MEXC Account Read-Only
MEXC Test Order
Micro-Live Broker Adapter
订单状态机
幂等下单
成交
仓位
现金
费用
PnL
交易所级对账
崩溃恢复
```

只有该平面可以读取交易凭证。

## 2.4 Monitoring & Audit Plane

职责：

```text
Data Health
Factor Health
Alpha Health
Execution Health
Risk Health
Reality Gap
事件链
告警
事故管理
日终对账
状态投影
只读 API
```

它必须在研究平面故障时仍能独立触发暂停或 Kill Switch。

## 2.5 Autonomy Kernel

新增或完善：

```text
services/orchestration/
  autonomy_kernel.py
  workflow_engine.py
  event_router.py
  command_bus.py
  policy_engine.py
  idempotency.py
  scheduler.py
  leader_lease.py
  recovery.py
```

Autonomy Kernel 必须是确定性系统，不得由语言模型直接决定状态转换。

---

# 3. 三个正交状态机

不得再用一个状态字段混合研究、部署和健康。

## 3.1 ResearchState

```text
DRAFT
TESTING
VALIDATED
CHALLENGED
INVALIDATED
RESEARCH_RETIRED
```

允许的核心转换：

```text
DRAFT → TESTING
TESTING → VALIDATED
TESTING → INVALIDATED
VALIDATED → CHALLENGED
CHALLENGED → VALIDATED
CHALLENGED → INVALIDATED
INVALIDATED → RESEARCH_RETIRED
```

任何新假设、参数、代码或数据语义变化必须产生新 `alpha_version`，不能覆盖旧版本。

## 3.2 DeploymentState

```text
OFFLINE
PAPER
SHADOW
MICRO_LIVE
LIVE
PAUSED
CLOSED
```

允许的核心转换：

```text
OFFLINE → PAPER
PAPER → SHADOW
SHADOW → MICRO_LIVE
MICRO_LIVE → LIVE

PAPER / SHADOW / MICRO_LIVE / LIVE → PAUSED
PAUSED → PAPER / SHADOW / MICRO_LIVE
任意部署态 → CLOSED
```

规则：

- 进入 `MICRO_LIVE` 或 `LIVE` 必须有人工签名 Deployment Grant；
- `PAUSED` 可以自动进入；
- 从 `PAUSED` 恢复真实资金部署必须重新人工批准；
- `CLOSED` 不得原地恢复，只能新建 deployment instance。

## 3.3 HealthState

```text
HEALTHY
WATCH
DEGRADED
FAILED
```

每个状态必须带原因码：

```text
DATA_STALE
DATA_LINEAGE_BROKEN
SIGNAL_DECAY
REGIME_MISMATCH
COST_INFLATION
SLIPPAGE_BREACH
POSITION_MISMATCH
ORDER_UNKNOWN
BROKER_DISCONNECTED
RISK_BREACH
MODEL_INVALIDATED
```

## 3.4 状态组合示例

```text
研究有效但交易所断线：
VALIDATED + PAUSED + FAILED_EXECUTION

研究有效但当前 Regime 不适合：
VALIDATED + PAUSED + DEGRADED_REGIME

数据血缘失败：
VALIDATED + PAUSED + FAILED_DATA

信号可能衰减：
CHALLENGED + MICRO_LIVE + DEGRADED_SIGNAL

经济假设被证伪：
INVALIDATED + CLOSED + FAILED_ALPHA
```

---

# 4. 统一事件与命令模型

## 4.1 EventEnvelope

所有域事件统一使用：

```text
event_id
event_type
schema_version
event_time
knowledge_time
recorded_at
actor_id
actor_type
alpha_id
alpha_version
deployment_id
correlation_id
causation_id
idempotency_key
prev_hash
payload_hash
payload
```

要求：

- append-only；
- schema version 明确；
- 支持 replay；
- 支持 outbox；
- 支持 projection；
- 支持 DLQ；
- 支持 hash-chain 验证；
- 事件重放必须得到同一状态。

## 4.2 CommandEnvelope

```text
command_id
command_type
requested_at
actor
subject
expected_state_version
idempotency_key
authorization_ref
payload_hash
payload
```

命令处理必须：

- 乐观锁或状态版本校验；
- 幂等；
- 重试安全；
- 对外部副作用使用 outbox / inbox 或等价机制；
- 失败时不产生部分状态。

## 4.3 事件类型

至少实现或映射：

```text
DATA_ARRIVED
DATA_ADMITTED
DATA_REJECTED
DATA_LINEAGE_BROKEN

FACTOR_REGISTERED
FACTOR_MATERIALIZED
FACTOR_HEALTH_DEGRADED

RESEARCH_CYCLE_STARTED
HYPOTHESIS_PROPOSED
CANDIDATE_COMPILED
TRIAL_RECORDED
VALIDATION_PASSED
VALIDATION_FAILED
CHALLENGE_PASSED
CHALLENGE_FAILED

PROMOTION_REQUESTED
PROMOTION_APPROVED
PROMOTION_DENIED
DEPLOYMENT_GRANT_CREATED
DEPLOYMENT_GRANT_EXPIRED

PAPER_STARTED
SHADOW_STARTED
MICRO_LIVE_STARTED
LIVE_STARTED
DEPLOYMENT_PAUSED
DEPLOYMENT_CLOSED

ORDER_INTENT_CREATED
RISK_APPROVED
RISK_REJECTED
ORDER_SUBMISSION_STARTED
ORDER_SUBMISSION_UNKNOWN
ORDER_ACKNOWLEDGED
ORDER_PARTIALLY_FILLED
ORDER_FILLED
ORDER_CANCELLED
ORDER_REJECTED

RECONCILIATION_PASSED
RECONCILIATION_FAILED
KILL_SWITCH_TRIGGERED
KILL_SWITCH_RESET_REQUESTED
KILL_SWITCH_RESET_APPROVED

ALPHA_HEALTH_WATCH
ALPHA_HEALTH_DEGRADED
ALPHA_HEALTH_FAILED
CAPITAL_REDUCED
ALPHA_RETIREMENT_PROPOSED
ALPHA_RETIRED
REACTIVATION_REQUESTED
```

---

# 5. 数据控制平面

## 5.1 完整数据链

```text
Fetch
→ Raw Immutable Archive
→ Source Metadata
→ Normalize
→ event_time / knowledge_time
→ PIT Store
→ Universe Membership
→ Quality Scan
→ Lineage Graph
→ DatasetVersion
→ Admission PASS / BLOCK
```

## 5.2 Data Admission 必须检查

```text
Schema
时间单位
时区
event_time
knowledge_time
未来时间戳
重复
乱序
缺失
覆盖率
单位与语义
修订
存活宇宙
原始归档哈希
backfill 日期匹配
数据源定义变化
```

任何一项关键检查失败：

```text
dataset = BLOCKED
依赖因子 = STALE
依赖 Alpha = PAUSED
禁止新订单
```

不得继续使用“最后一次好数据”生成新风险，除非该 Alpha 有预注册的、人工批准的 stale-data policy。

## 5.3 数据血缘修复优先级

优先修复当前 `raw lineage / backfill` 阻断：

1. 找到日期、制品、archive hash、coverage mismatch 的真实根因；
2. 不得仅修改期望日期让检查通过；
3. 重新生成完整 lineage evidence；
4. 写入 `evidence/data_quality_admission.json`；
5. 所有输入路径、哈希、范围、生成时间可重建；
6. 产生回归测试，防止再次出现静默错配。

---

# 6. Factor Garden

新增或整理：

```text
services/factor_garden/
  registry.py
  definitions.py
  materializer.py
  store.py
  lineage.py
  dependency_graph.py
  family_cluster.py
  health.py
  offline_online_parity.py
```

## 6.1 FactorDefinition

每个因子至少包含：

```text
factor_id
factor_version
factor_family_id
name
expression
expression_hash
economic_hypothesis
required_datasets
minimum_lag
frequency
universe
normalization
neutralization
winsorization
missing_policy
expected_turnover
known_failure_regimes
creator_actor
creator_model
prompt_hash
created_at
status
```

## 6.2 FactorObservation

```text
factor_id
factor_version
instrument
event_time
knowledge_time
computed_at
dataset_version
materializer_version
quality_verdict
value
```

## 6.3 Offline / Online 一致性

必须满足：

```text
同一 FactorDefinition
同一核心计算函数
不同输入窗口适配器
```

历史、Paper、Shadow 和 Live 不得各写一套因子逻辑。

实现 parity test：

```text
给定相同数据快照
offline_result == online_result
within configured tolerance
```

## 6.4 因子家族

最少支持：

```text
PRICE_TREND
PRICE_MEAN_REVERSION
VOLATILITY
VOLUME
LIQUIDITY
ORDER_FLOW
POSITIONING_CROWDING
OPEN_INTEREST
FUNDING_CARRY
BASIS
CROSS_VENUE
REGIME
DEFENSIVE_OVERLAY
```

不要为了数量而添加无经济解释的表达式。

---

# 7. 全局不可变 Trial Ledger

这是扩大 AI 挖掘前的最高优先级。

新增或完善：

```text
services/alpha_factory/trial_ledger.py
services/alpha_factory/trial_store.py
services/alpha_factory/trial_hash_chain.py
```

## 7.1 TrialRecord

必须记录每一次尝试，包括失败和删除：

```text
trial_id
parent_trial_id
research_session_id
actor_id
actor_type
model_name
model_version
prompt_hash
hypothesis_id
factor_ids
alpha_expression
canonical_expression
expression_hash
parameter_set
parameter_hash
dataset_versions
universe_version
cost_model_version
validation_policy_version
code_hash
started_at
completed_at
status
metrics
failure_reasons
artifact_paths
artifact_hashes
viewed_oos_boundaries
prev_hash
entry_hash
```

## 7.2 Trial Ledger 规则

- append-only；
- 不得物理删除失败实验；
- 重复候选也必须记账；
- 所有 AI 模型和人工尝试统一进入同一账本；
- 参数扫描的每个配置都计数；
- OOS 查看次数必须记录；
- DSR/PBO/FDR 的 trial count 必须来自权威账本；
- 不能只使用进入 Alpha Catalog 的数量。

## 7.3 家族级多重检验

以下变体不能被当成完全独立发现：

```text
rank(global_ls)
zscore(global_ls)
winsorized_rank(global_ls)
mean_7d(global_ls)
mean_14d(global_ls)
```

先按经济家族、输入依赖和收益相关性聚类，再进行：

```text
family-level FDR
within-family FDR
global trial adjustment
marginal IR
correlation redundancy
```

---

# 8. AI Alpha Factory

新增或整理：

```text
services/alpha_factory/
  hypothesis.py
  hypothesis_store.py
  candidate_compiler.py
  constrained_search_space.py
  search_controller.py
  candidate_deduplicator.py
  economic_rules.py
  challenger.py
  challenge_council.py
```

## 8.1 AI 先生成可证伪假设

每个 Hypothesis 必须包含：

```text
hypothesis_id
economic_mechanism
expected_direction
expected_horizon
required_factors
market_scope
regime_assumptions
expected_cost_sensitivity
capacity_assumptions
falsification_criteria
known_risks
creator_model
prompt_hash
```

禁止只有“Sharpe 可能更高”的候选。

## 8.2 受限 DSL

AI 只能在受限、可解释、可审计的 DSL 中生成候选。

允许：

```text
time-series transforms
cross-sectional transforms
lagged interactions
economically justified residualization
regime conditioning
```

禁止或默认拒绝：

```text
无经济解释的深层随机表达式
高阶嵌套但无法解释的组合
依赖未来可知信息
超出数据可用时间的窗口
仅在一个精确参数点有效的表达式
```

## 8.3 分层漏斗

不要对所有候选直接运行最昂贵回测。

```text
候选生成
→ 语义检查
→ canonicalize / deduplicate
→ 数据可用性检查
→ PIT / lag 检查
→ 粗筛
→ 成本前稳定性
→ 成本后回测
→ Walk-forward / CPCV
→ DSR / PBO / FDR
→ 参数扰动
→ Regime
→ 容量
→ 独立 Challenge
→ Alpha Catalog
```

每一层淘汰都必须写明原因。

## 8.4 多模型分工

不同 AI 不得都执行同一审查。

```text
Proposer：
提出经济假设、候选因子、失效条件

Implementation Auditor：
检查代码、时间错位、数据血缘、会计和接口实现

Statistical Challenger：
攻击过拟合、多重检验、样本选择、参数脆弱性

Adversarial Reviewer：
构造最可能失败的市场状态和反事实

Deterministic Judge：
只读取机器证据，依据固定政策给 PASS / FAIL
```

最终状态转换必须由 Deterministic Judge 和 Governance Plane 完成，而不是语言模型投票。

---

# 9. 验证与反证引擎

所有候选必须经过统一验证策略。

## 9.1 必须检查

```text
PIT / knowledge-time correctness
lookahead
survivorship
universe leakage
same-bar execution leakage
label leakage
train / validation / OOS separation
purge / embargo
walk-forward
CPCV
DSR
PBO
BH / BHY FDR
global trial count
parameter perturbation
date perturbation
universe perturbation
cost sensitivity
spread sensitivity
slippage sensitivity
impact sensitivity
capacity
regime stability
long / short leg attribution
single-asset concentration
single-date dependence
turnover
reconciliation
```

## 9.2 不能静默通过

以下证据缺失时必须：

```text
REQUIRES_HUMAN_REVIEW
或
FAILED_INCOMPLETE_EVIDENCE
```

不得默认为 PASS：

```text
leakage evidence
trial count
PBO
regime coverage
capacity
cost model
paper evidence
independent challenge
```

## 9.3 Strategy-Market Compatibility Gate

实现专门门禁，防止把不兼容策略移植到不同市场。

至少检查：

```text
策略是否需要多资产截面
策略是否需要做空
策略是否依赖资金费
策略是否依赖永续合约 OI / long-short ratio
目标市场是否支持对应腿
执行市场与信号市场是否一致
若不一致，是否有明确 basis / lead-lag 证据
```

例如：

- 多资产 long-short Alpha 不得直接继承到单一 BTC 现货；
- 现货 Long/Cash 版本必须创建新 `alpha_id` / `alpha_version`；
- MEXC BTC 现货接口测试只能证明执行链路，不能自动证明原 Alpha 盈利。

---

# 10. Champion / Challenger

## 10.1 Champion 冻结

正在 Paper、Shadow、Micro-Live 或 Live 的 Champion 必须冻结：

```text
alpha_version
code_hash
config_hash
factor_versions
dataset_policy
cost_policy
risk_policy
```

不得在线自动调参。

## 10.2 Challenger 流程

```text
Champion 继续运行
→ 系统检测 WATCH / DEGRADED
→ AI 生成 Challenger
→ Challenger 走完整 Trial / Validation
→ Paper
→ Shadow
→ 机器生成对比证据包
→ 真人批准
→ Challenger 从 Micro-Live 开始
→ 渐进替换 Champion
```

## 10.3 比较指标

不得只比较总收益：

```text
net Sharpe
Sortino
max drawdown
expected shortfall
tail loss
turnover
fees
spread
slippage
impact
capacity
regime stability
correlation
marginal IR
Reality Retention
```

---

# 11. Alpha 生命周期控制器

新增或整理：

```text
services/lifecycle/
  research_state.py
  deployment_state.py
  health_state.py
  transition_guard.py
  promotion.py
  degradation.py
  pause.py
  retirement.py
  reactivation.py
```

## 11.1 Promotion Packet

每次晋级生成不可变证据包：

```text
promotion_id
alpha_id
alpha_version
from_state
to_state
code_hash
config_hash
dataset_versions
trial_ledger_head
validation_report_hash
challenge_report_hashes
paper_report_hash
shadow_report_hash
risk_report_hash
requested_capital
risk_envelope
requested_at
decision
decision_reasons
approval_refs
```

## 11.2 自动降险

系统可以自动：

```text
LIVE → PAUSED
MICRO_LIVE → PAUSED
资本权重 100% → 50% → 20% → 0%
触发 Kill Switch
```

系统不得自动：

```text
0% → 20%
20% → 50%
MICRO_LIVE → LIVE
PAUSED → LIVE
```

## 11.3 退役与重新启用

`PAUSED`：

```text
根因消除
→ 自动重放验证
→ Shadow 冷静期
→ 生成恢复申请
→ 真人批准
→ Micro-Live / Live
```

`RETIRED`：

```text
alpha_v1 永久保持 RETIRED
→ 新建 alpha_v2
→ 新代码哈希 / 新假设
→ 完整验证
→ Paper
→ Shadow
→ Micro-Live
→ 真人批准
```

不得原地复活 Retired 版本。

---

# 12. Paper、Shadow、Micro-Live、Live 分层

## 12.1 PAPER

使用真实时间推进、模拟成交。

必须检查：

```text
数据准入 PASS
信号准时
订单意图完整
Paper fills 完整
资金费和费用完整
PnL 对账
事件链完整
预注册周期完成
```

## 12.2 SHADOW

连接真实：

```text
市场行情
盘口
交易规则
账户只读信息（可选）
```

但不发送订单。

必须比较：

```text
理论成交
Paper fill
实时可成交价格
盘口深度
Spread
Slippage
Latency
Expected impact
```

## 12.3 MICRO_LIVE

仅允许在真人签名风险信封内：

```text
固定账户
固定交易所
固定 symbol
固定最大资本
固定单笔上限
固定日亏损
固定最大回撤
固定授权期限
固定策略哈希
```

## 12.4 LIVE

只有 Micro-Live 经过足够真实成交、对账、故障恢复和 Reality Gap 验证后，系统才能生成 Live 晋级建议。

系统不得自动批准。

---

# 13. MEXC BTC 现货接入顺序

实现接口，但默认永远保持真实下单关闭。

## 13.1 适配器层次

```text
MexcPublicMarketDataAdapter
MexcAccountReadOnlyAdapter
MexcSpotTestOrderAdapter
MexcSpotBrokerAdapter
```

## 13.2 开发顺序

### 阶段 A：Public Market Shadow

实现：

```text
server time
exchange metadata
BTCUSDT symbol status
price precision
quantity precision
min quantity
min notional
best bid / ask
order book snapshot
incremental depth
trade stream
reconnect
gap detection
data freshness
rate-limit handling
```

### 阶段 B：Account Read-Only

实现：

```text
balances
open orders
recent orders
recent trades
commission / fee query
user stream
listen-key renewal
REST backfill after disconnect
```

### 阶段 C：Test Order

实现：

```text
request signing
timestamp
recv window
precision validation
min notional
client_order_id
permission check
```

不得进入撮合。

### 阶段 D：Micro-Live Broker Adapter

只有在独立配置、独立子账户、IP 白名单、无提款权限和 Deployment Grant 存在时才允许实例化。

## 13.3 订单幂等

确定性 ID：

```text
client_order_id =
hash(
  deployment_id,
  alpha_version,
  signal_time,
  rebalance_id,
  symbol,
  side,
  intended_quantity
)
```

同一订单意图只能对应一个交易所订单。

## 13.4 未知订单状态

请求超时、5xx 或连接中断时：

```text
ORDER_SUBMISSION_UNKNOWN
→ 停止该 deployment 新订单
→ 通过 client_order_id / exchange_order_id 查询
→ 找到则接管
→ 明确不存在后才允许重新提交
→ 无法确认则 Kill Switch + 人工介入
```

不得盲目重试。

## 13.5 启动恢复

每次启动：

```text
获得唯一 Leader Lease
→ 时间同步
→ 加载交易规则和真实费率
→ 查询余额
→ 查询挂单
→ 查询近期成交
→ 重建本地账本
→ 校验事件链
→ 对账
→ 建立 WebSocket
→ 数据和账户健康
→ 才允许新订单
```

## 13.6 API Secret

必须：

```text
独立子账户
禁止提款
固定 IP
最小权限
不写 Git
不写日志
不发给 AI
不进前端
不进异常堆栈
```

---

# 14. 交易所级对账

新增：

```text
services/execution/reconciliation/
  order_reconciler.py
  trade_reconciler.py
  balance_reconciler.py
  position_reconciler.py
  pnl_reconciler.py
  startup_recovery.py
```

## 14.1 权威来源

交易所是：

```text
余额
委托
成交
费用
```

的最终事实来源。

本地 Event Store 是审计和恢复来源，但不能覆盖交易所现实。

## 14.2 对账频率

```text
订单状态：每次事件 + 周期性
余额：每次成交 + 周期性
持仓：每次成交 + 周期性
PnL：日终
启动：全量
```

## 14.3 失败动作

任何无法解释的不一致：

```text
RECONCILIATION_FAILED
→ 禁止新订单
→ 触发 Kill Switch
→ 取消可安全取消的挂单
→ 保留证据
→ 告警
→ 等待人工裁决
```

不得自动“以交易所为准并静默改写历史账本”。

---

# 15. Alpha Health 与 Reality Gap

新增：

```text
services/monitoring/
  data_health.py
  factor_health.py
  alpha_health.py
  execution_health.py
  risk_health.py
  reality_gap.py
  decay_detector.py
  incident_manager.py
```

## 15.1 四类健康分量

```text
data_health
signal_health
execution_health
risk_health
```

总状态默认：

```text
overall_health = min(all_components)
```

## 15.2 Data Health

监控：

```text
freshness
coverage
missingness
revision rate
definition change
knowledge-time lag
lineage
source availability
```

## 15.3 Factor Health

监控：

```text
coverage
dispersion
distribution drift
rank stability
turnover drift
family correlation
offline-online parity
```

## 15.4 Signal Health

截面 Alpha：

```text
Rank IC
ICIR
quantile monotonicity
long-short spread
coverage
marginal IR
```

单资产 Alpha：

```text
conditional return
hit rate
payoff ratio
calibration
signal-strength monotonicity
```

Carry：

```text
funding income
price-leg loss
net carry
funding regime
extreme adverse move
```

## 15.5 Execution Health

```text
expected fill vs actual fill
spread
slippage
impact
latency
fill ratio
partial fill age
reject rate
unknown order count
fee drift
```

## 15.6 Risk Health

```text
drawdown
losing streak
expected shortfall
concentration
correlation
capacity usage
cash buffer
limit utilization
```

## 15.7 Reality Gap

输出：

```text
Historical OOS Net Edge
→ Paper Net Edge
→ Shadow Executable Edge
→ Micro-Live Net Edge
→ Live Net Edge
```

计算：

```text
RealityRetention =
current_stage_net_edge / historical_oos_net_edge
```

当分母过小、符号变化或样本不足时，不得输出误导性比例，必须返回 `INSUFFICIENT_OR_UNSTABLE`.

## 15.8 处置等级

```text
HEALTHY：
继续运行，保持资本

WATCH：
不增加资本，提高监控频率，启动 Challenger

DEGRADED：
自动降权，冻结新晋级，启动根因分析

FAILED：
自动暂停或 Kill Switch，生成事故报告，等待人工处置
```

---

# 16. 调度与自治运行

新增持久化调度器，必须支持：

```text
幂等
错过任务补跑
单实例 Leader
fencing token
任务租约
重试
退避
DLQ
任务审计
```

## 16.1 运行频率

| 周期 | 任务 |
|---|---|
| 实时 | 行情、订单、成交、风险、对账、Kill Switch |
| 每次信号 | 因子、目标仓位、风险门、订单意图 |
| 每小时 | 数据新鲜度、接口、执行偏差 |
| 每日 | 数据准入、PnL 对账、归因、Alpha Health、Reality Gap |
| 每周 | 调仓、容量、相关性、Challenger 研究 |
| 每月 | Regime、模型风险、退役候选、资本配置审查 |
| 事件触发 | 数据异常、回撤、成本变化、对账失败、API 故障 |

不得因为 Champion 当天亏损立即启动参数搜索。研究搜索只能按预注册周期或明确衰减事件启动。

---

# 17. 风险信封

新增：

```text
MicroLiveRiskEnvelope
LiveRiskEnvelope
```

至少包含：

```text
account_id
exchange
symbol_allowlist
strategy_hash
max_total_capital
max_order_notional
max_position
max_daily_loss
max_drawdown
max_orders_per_minute
max_open_orders
max_slippage_bps
max_market_data_age
max_account_stream_age
max_unknown_order_age
max_clock_drift
valid_from
valid_until
approval_ref
```

默认 Micro-Live：

```text
仅现货
无杠杆
无借币
无提款
无自动转账
单一 symbol
极小子账户资金
```

硬限制不得由 AI、Alpha 或前端修改。

---

# 18. 前端信息架构

保留现有 React / Vite / TypeScript 技术栈，重构信息架构，不要继续平铺页面。

一级导航：

```text
Command Center

Research OS
  Factor Garden
  Alpha Factory
  Experiments
  Validation
  Champion / Challenger

Lifecycle OS
  Alpha Lifecycle
  Promotion Queue
  Health / Decay
  Retirement / Reactivation

Portfolio OS
  Allocation
  Exposure
  Capacity
  Attribution

Trading OS
  Paper
  Shadow
  Orders
  Fills
  Broker
  Reconciliation

Risk OS
  Research Risk
  Pre-Trade Risk
  Limits
  Stress
  Kill Switch

AI OS
  Agents
  Tasks
  Challenge Reports
  Prompt / Model Registry

Governance
  Approvals
  Deployment Grants
  Audit
  Versions
  Evidence

System
  Data Health
  Event Health
  Scheduler
  Incidents
  Configuration
```

## 18.1 首页必须回答

```text
系统是否健康
当前允许的最高部署等级
真实资本是否启用
哪些 Alpha 在哪一阶段
哪些风险需要关注
哪些决策等待真人
今天发生了什么
是否有对账失败
是否有数据或订单未知状态
```

## 18.2 Provenance

所有卡片、图表和表格必须显示：

```text
REAL
PAPER
SHADOW
MICRO_LIVE
LIVE
SYNTHETIC_DETERMINISTIC
STALE
MISSING
```

不得隐藏 synthetic fallback。

## 18.3 决策优先

不要只展示指标，要显示：

```text
Decision
Reason
Evidence
Blocking Conditions
Required Human Action
```

---

# 19. 策略与配置政策文件

新增：

```text
policies/
  data_admission.yaml
  factor_health.yaml
  research_validation.yaml
  challenge_policy.yaml
  paper_promotion.yaml
  shadow_promotion.yaml
  micro_live_risk.yaml
  live_risk.yaml
  decay_policy.yaml
  retirement_policy.yaml
  scheduler.yaml
```

政策文件必须：

- schema 校验；
- 版本化；
- 内容哈希；
- 变更审计；
- 与 Deployment Grant 绑定；
- 实盘期间不可被静默修改。

---

# 20. 测试体系

不得只依赖 happy-path 单元测试。

## 20.1 测试层级

```text
unit
property-based
integration
contract
end-to-end
load
chaos
recovery
security
governance AST scan
```

## 20.2 关键不变量

必须有自动测试证明：

1. 同一数据、代码、配置重复运行结果一致；
2. Event Replay 得到同一状态；
3. 重复命令不产生重复副作用；
4. 同一订单意图不会产生两个交易所订单；
5. 未知订单状态不会盲目重试；
6. 任一风险检查异常都 fail-closed；
7. AI 无法到达真实下单路径；
8. API Secret 不进入日志和前端；
9. 终态订单无非法出边；
10. 本地账本与交易所不一致时停止交易；
11. 数据过期时停止新增风险；
12. Offline / Online 因子一致；
13. OOS 查看和参数尝试进入 Trial Ledger；
14. Retired 版本不能原地恢复；
15. Deployment Grant 过期后自动拒绝订单；
16. 策略哈希或配置哈希变化后授权失效；
17. Kill Switch 重启后保持锁存；
18. Leader 切换不会双重下单；
19. 数据血缘冲突不会静默通过；
20. PnL 恒等式和费用归因一致。

## 20.3 `scripts/validate.py`

修复长期无进度问题：

- 每个阶段输出开始、完成、耗时；
- 支持阶段超时；
- 支持 `--stage`；
- 支持 `--json-report`；
- 超时后输出卡住的测试或子进程；
- 不得通过简单提高总超时掩盖死锁；
- 生成 `evidence/validation_report.json`。

---

# 21. 可靠性与故障注入

必须覆盖：

```text
REST 超时
5xx 未知状态
429 限流
WebSocket 断线
深度序列缺口
账户流断线
部分成交
撤单与成交竞态
进程崩溃
机器重启
数据库暂时不可写
Event Store 尾部损坏
时钟漂移
API 权限被撤销
交易对暂停
手续费变化
数据源定义变化
Leader 重复
网络分区
```

每个场景必须：

```text
预期动作
实际动作
是否产生新风险
是否可恢复
是否需要人工
证据路径
```

---

# 22. 代码质量与实现纪律

你必须：

1. 先复用现有 contracts、services、events 和 tests；
2. 不得大规模重写稳定模块；
3. 每个新服务有明确接口、类型、错误码和测试；
4. Python 代码使用类型标注；
5. 所有金额使用 Decimal 或现有权威金额类型；
6. 时间统一 UTC；
7. 所有外部 I/O 有超时、重试和熔断策略；
8. 重试必须区分安全读操作与有副作用写操作；
9. 所有状态变更写入审计；
10. 所有证据有哈希；
11. 所有错误必须可分类；
12. 不得吞异常后继续交易；
13. 不得把未实现模块伪装为 PASS；
14. 不得用 Mock 结果替代生产证据；
15. 前端只读；高风险动作必须经过独立审批 API。

---

# 23. 分阶段开发计划与验收门

你必须按以下阶段执行，每阶段都要：

```text
检查现状
建立任务清单
实现
运行测试
生成证据
更新文档
输出剩余问题
```

不得只创建空接口或 TODO 后宣布完成。

## Phase 0：基线与安全冻结

任务：

- 读取仓库结构、当前 commit、依赖、文档、证据和测试；
- 验证五个安全旗标；
- 生成当前能力矩阵；
- 建立变更前哈希清单；
- 确认无真实凭证进入仓库；
- 生成 `docs/reports/autonomy_gap_analysis.md`。

验收：

```text
安全旗标默认 false
无真实订单路径可达
基线测试结果有机器报告
当前阻断与证据冲突被列出
```

## Phase 1：数据血缘与统一门禁

任务：

- 修复 raw lineage / backfill 的真实根因；
- 让数据准入可重建；
- 修复 `scripts/validate.py` 进度和阶段超时；
- 生成统一 validation JSON。

验收：

```text
数据准入可重复
lineage mismatch 有测试
validate 可定位卡住阶段
不得虚构全绿
```

## Phase 2：Autonomy Kernel

任务：

- Event Router；
- Command Bus；
- Workflow Engine；
- State Machine；
- Policy Engine；
- Idempotency；
- Leader Lease；
- Recovery。

验收：

```text
命令幂等
事件可重放
状态转换有 guard
单实例执行
崩溃后恢复
```

## Phase 3：全局 Trial Ledger

任务：

- 所有研究入口统一登记；
- AI、手工、参数扫描和失败候选全部记账；
- append-only hash chain；
- trial count 接入 DSR/PBO/FDR。

验收：

```text
删除失败实验不可能
所有候选有 trial_id
全局 trial count 可重建
```

## Phase 4：Factor Garden

任务：

- Factor Registry；
- Materializer；
- Store；
- Dependency Graph；
- Health；
- Offline/Online Parity；
- Family Cluster。

验收：

```text
稳定 factor_id/version
历史与实时同逻辑
依赖数据失败会传播暂停
```

## Phase 5：Alpha Factory 与 Challenge Council

任务：

- Hypothesis Store；
- constrained DSL；
- candidate compiler；
- family-aware search；
- multi-model structured review；
- deterministic judge；
- champion/challenger。

验收：

```text
候选有经济假设和证伪标准
所有候选进 Trial Ledger
语言模型不能推进部署状态
```

## Phase 6：三状态生命周期

任务：

- ResearchState；
- DeploymentState；
- HealthState；
- Promotion Packet；
- Deployment Grant；
- Pause；
- Retirement；
- Reactivation。

验收：

```text
自动降险
人工增险
Retired 不可原地恢复
版本变化导致授权失效
```

## Phase 7：Paper 与 Shadow

任务：

- 实时 Paper；
- Shadow executable price；
- Paper/Shadow 对账；
- Reality Gap；
- forward cycles；
- paper health。

验收：

```text
真实时间推进
无未来数据
真实盘口标签明确
paper_ready 只在全部条件满足时为 true
```

## Phase 8：MEXC 公开行情、只读账户与 Test Order

任务：

- Public Market Adapter；
- Read-only Account Adapter；
- Test Order；
- 交易规则动态加载；
- 时间同步；
- User Stream；
- 断线补账。

验收：

```text
无真实下单
可从交易所重建账户状态
丢包和断线可检测
```

## Phase 9：Micro-Live Broker Adapter（默认禁用）

任务：

- 独立部署；
- Deployment Grant；
- 幂等下单；
- unknown state recovery；
- partial fills；
- cancel race；
- reconciliation；
- Kill Switch。

验收：

```text
无 Grant 不可实例化
默认 flags 仍为 false
故障注入不重复下单
对账失败自动停机
```

## Phase 10：Alpha Health、衰减与退役

任务：

- 四类健康；
- Reality Retention；
- Watch / Degraded / Failed；
- 自动降权；
- 退役建议；
- Challenger 触发。

验收：

```text
亏损能区分数据、信号、执行和风险
不能因短期亏损自动调参
自动减风险、不自动增风险
```

## Phase 11：前端重构

任务：

- Command Center；
- Research OS；
- Lifecycle OS；
- Trading OS；
- Risk OS；
- Governance；
- provenance；
- human decisions queue。

验收：

```text
页面按决策流程组织
所有数据有 provenance
高风险操作不可从普通前端直接执行
```

## Phase 12：Chaos、恢复与运维

任务：

- 全部故障注入；
- 启动恢复；
- Leader 切换；
- 备份恢复；
- Runbook；
- 告警；
- 事故报告。

验收：

```text
无失控订单
无未解释仓位
恢复路径可重放
```

## Phase 13：最终审计

生成：

```text
evidence/autonomy_readiness.json
evidence/trial_ledger_integrity.json
evidence/factor_garden_status.json
evidence/alpha_lifecycle_status.json
evidence/paper_shadow_status.json
evidence/mexc_integration_status.json
evidence/chaos_recovery_status.json
docs/reports/QUANTOS_AUTONOMY_FINAL_AUDIT.md
```

最终状态只能是：

```text
READY_FOR_RESEARCH_AUTONOMY
READY_FOR_FORWARD_PAPER
READY_FOR_SHADOW
READY_FOR_MICRO_LIVE_REVIEW
NOT_READY
```

不得自动写 `READY_FOR_LIVE`，除非存在真实外部人工审查和实盘证据；即使满足，也只能生成建议，不能开放权限。

---

# 24. 自主开发工作协议

你必须持续执行，不得在只完成分析后停止。

每个阶段使用以下循环：

```text
1. Inspect
2. Map existing implementation
3. Identify exact gap
4. Design smallest compatible change
5. Implement
6. Add tests
7. Run targeted tests
8. Run broader validation
9. Generate evidence
10. Update docs and runbook
11. Record remaining external/manual dependencies
12. Proceed to next phase
```

规则：

- 不要向用户反复询问已经可以从仓库判断的问题；
- 缺少外部凭证时，完成接口、模拟器、测试和阻断，不得伪造连接；
- 遇到阻断不要绕过，记录并继续完成不依赖该阻断的工作；
- 每个阶段输出短进度摘要；
- 不得修改历史证据文件冒充当前运行；
- 新生成证据必须有生成时间、commit、配置哈希和输入哈希；
- 所有关键变更必须可回滚；
- 优先小批量提交；
- 不得在同一提交混合无关重构。

---

# 25. 最终交付标准

完成后，QUANTOS 至少应具备：

1. 系统重启后能从事件账本和交易所状态恢复；
2. 不会因重试产生重复订单；
3. 数据中断会自动停止新增风险；
4. Paper、Shadow、Micro-Live 使用同一信号核心；
5. 所有研究尝试进入 Trial Ledger；
6. AI 不能触碰 API Secret、资本审批和真实订单；
7. 订单、成交、余额、仓位和 PnL 可对账；
8. Alpha 版本在部署期间不可修改；
9. Alpha 衰减时可自动降权或暂停；
10. Retired 版本不能原地复活；
11. 增资、恢复实盘和修改硬风险必须真人批准；
12. 前端能清楚展示研究、部署、健康和证据状态；
13. 所有运行结果明确区分历史、Paper、Shadow、Micro-Live 和 Live；
14. 所有未完成外部条件明确显示为 BLOCKED，不得伪装为完成；
15. 系统可以长期自动运行，但真实资本权力始终掌握在人类手中。

---

# 26. 立即开始执行

现在立即在仓库根目录执行以下工作，不要只回复设计建议：

1. 读取：
   - `AGENTS.md`
   - `QUANTOS_MASTER_BLUEPRINT.md`
   - `docs/PROJECT_STATE.md`
   - `docs/ARCHITECTURE.md`
   - `docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`
   - `evidence/top_tier_assessment.json`
   - `evidence/data_quality_admission.json`
   - `evidence/alpha_paper_cycle.json`
   - `evidence/three_alpha_paper_flow.json`
   - `data/cache/paper_health_10k.json`
   - 当前连续 USDT-M 纸面回放制品和账本
2. 检查当前 commit、工作区状态、依赖和测试入口；
3. 生成：
   - `docs/reports/autonomy_gap_analysis.md`
   - `evidence/autonomy_baseline.json`
   - `docs/plans/AUTONOMY_IMPLEMENTATION_PLAN.md`
4. 先执行 Phase 0 和 Phase 1；
5. 在安全门保持关闭的前提下，继续按阶段开发；
6. 每个阶段必须提供代码、测试、证据和运行说明；
7. 任何无法完成的项必须写明：
   - 阻断原因；
   - 所需外部资源；
   - 已完成的安全接缝；
   - 为什么不能伪造 PASS。

最终不要只说“系统已完成”。必须给出：

```text
当前最高可用等级
每个阶段 PASS / PARTIAL / FAILED / BLOCKED
测试与证据路径
尚未解决的外部条件
是否存在真实订单能力
是否仍保持默认安全旗标
下一项唯一最高优先级
```

# 主提示词结束
