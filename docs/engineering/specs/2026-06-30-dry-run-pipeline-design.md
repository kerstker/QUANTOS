# 端到端「干跑流水线」—— 设计 spec

> 状态:设计已与用户确认(2026-06-30)。下一步:writing-plans 出实现计划。
> 约定:本文中文叙述,代码标识符英文。安全边界见 §6,不可让。
> 由来:设计意图 [`2026-06-30-dry-run-pipeline-design-intent.md`](2026-06-30-dry-run-pipeline-design-intent.md);
> 用户战略判断「系统要能用、最终目标实盘」;差距报告 #3 P0(预交易门是"未接运行时的死代码")。

## 1. 目标(一句话)

把现有的真零件(信号/组合/执行意图/预交易门/干跑适配器/纸面对账/审计)串成**一条连续的、不碰真钱的
干跑流水线**:一个策略能从信号一路跑到成交对账+监控,作为一个**系统**运行(而非孤立研究脚本),并落库成
一次**可复现的 DryRunRun**。这也是通往实盘的**安全脚手架**——实盘 = 同接口背后把干跑适配器换成真券商适配器
+ 人工批准(显式非目标)。

## 2. 已确认的关键决策

- **首刀范围**:核心编排器 + 驱动器,端到端跑通 carry;后端为主,**不接 console、不接实盘适配器**(后续增量)。
- **编排器独立**:新模块,**绝不放进 research_runner**(它被 `tests/governance/test_research_runner_boundary.py`
  钉死"无下单路径")。干跑纸面、无券商 SDK,L2 合规。
- **方案 A1**:一个薄确定性编排器,按序组合现有真零件,只补"接线"+ DryRunRun 记录 + append-only 落库。
- **首个策略**:carry(本会话唯一验证过的边)+ vol-target 权重。

## 3. 现有真零件(复用,接口已探明)

- `services/execution/intent_builder.py::build_intents(targets: Dict[str,int], current: Dict[str,int], *,
  strategy_id="") -> list[OrderIntent]` —— 目标/当前**整数手数** → 订单意图。
- `services/risk/pre_trade/gate.py::PreTradeRiskGate.evaluate(intent, ctx: RiskContext) -> RiskDecision`
  (+ `issue_ref`/`verify_ref`);`RiskContext`(account/market/strategy/kill_switch/safety_config/actor/now/
  max_staleness/recent_order_fingerprints/recent_timestamps/plan_turnover);`order_bridge.apply_decision(order,
  decision)`。**这是运行时 chokepoint。**
- `services/trading/dry_run_adapters/domain/engine.py::DryRunAdapterEngine`(validate→map→normalize,
  `TestNoExecution` 钉死**绝不真发**)+ models(DryRunCommand/NormalizedReport/DryRunDecision/RejectionReason)。
  **这是券商接缝。**
- `services/research/paper_trading/domain/`:`models`(PaperAccount/PaperPosition/PaperOrder/PaperFill/
  OrderSide/OrderStatus/FillMethod/PaperRun)、`reconciliation.reconcile(...)`、`pnl.PnLAttributor`。
- `services/monitoring/audit_log.py::GlobalAuditLog`(哈希链)。

## 4. 组件与接口(每个单元一个清晰职责)

### 4.1 `services/execution/dry_run_pipeline.py::run_dry_run_pipeline`
- `run_dry_run_pipeline(*, weights: Dict[str,float], prices: Dict[str,float], current_positions: Dict[str,int],
  aum: float, gate: PreTradeRiskGate, ctx_factory, adapter: DryRunAdapterEngine, account: PaperAccount,
  audit: GlobalAuditLog, strategy_id: str, timestamp: int) -> DryRunRun`。
- 步骤(纯编排,确定性):
  1. 权重→目标整数手:`target_qty_i = round(weights_i * aum / prices_i)`(价≤0/缺价跳过,fail-soft)。
  2. `build_intents(targets, current_positions, strategy_id=...)` → 订单意图。
  3. **逐意图,运行时 chokepoint**:先 `ctx = ctx_factory(intent)`(注入 kill_switch/safety_config/account/...);
     `decision = gate.evaluate(intent, ctx)`;`order = order_bridge.apply_decision(intent, decision)`。
     **fail-closed:仅 `decision` 批准的意图进入下一步;被拒/异常→记录原因、不成交。**
  4. 批准意图 → `adapter` 受理(DryRunCommand→NormalizedReport;**不真发**)。
  5. 受理通过 → 纸面成交(`PaperFill`, FillMethod;真实成本)→ 更新 `account`(PaperPosition)。
  6. `reconcile(...)` + `PnLAttributor` → 该次运行的 PnL/对账。
  7. 每步进 `audit`(哈希链)。
  8. 组装并返回 `DryRunRun`(意图、门决策+ref、受理报告、成交、持仓、PnL、审计承诺根)。
- 无 IO 以外副作用;无 RNG/无墙钟(墙钟在驱动器边界注入 timestamp)。

### 4.2 `services/execution/dry_run_store.py::DryRunStore`
- append-only sqlite `data/cache/dry_run.db`,`save_run(DryRunRun)` / `get_run(run_id)` / `all_runs()`;
  schema 校验、`create=` 守卫、fail-closed(沿用 paper_track/experiment_registry 成熟模式)。

### 4.3 `tools/dry_run_carry.py`(驱动器,墙钟只在此边界)
- 载入 carry 信号(COMPOSITE)+ vol-target 权重 + 近端价格(自 `crypto_funding.db`/价格库)→ 构造 gate/
  ctx_factory/adapter/account/audit → `run_dry_run_pipeline(...)` → 打印一次运行摘要(意图数/批准数/拒绝原因/
  成交/PnL/审计根)→ `DryRunStore.save_run`。确定性、可复现、可重跑。

## 5. 数据流

信号/权重 → 目标手数 → `build_intents` → 【逐意图:kill_switch + `gate.evaluate`,fail-closed】→ 干跑适配器受理
(不发)→ 纸面成交 → `reconcile`/PnL → 哈希链审计 → `DryRunRun` 落库。

## 6. 容器化 / 安全边界(机器强制,不可让)

- 五个 LIVE 标志(`LIVE_TRADING_ENABLED` 等)在 ctx 的 `safety_config` 里**断言为 false**;门的
  `check_live_trading_disabled`/`check_ai_direct_trading`/`check_kill_switch`/`check_strategy_approved` 正常生效。
- **kill_switch 先查**:latched → 全意图被拒(不成交);门 **fail-closed**:任一检查不过 / 异常 → 该意图不成交。
- 干跑适配器**无券商 SDK、不真发**(已有 `TestNoExecution` 钉死);`PaperAccount` = 模拟资本,**无真凭证**。
- **编排器绝不加进 research_runner**(治理边界);新模块不得引入网络/券商 import(`services/` 无网络 import
  仍机器强制)。
- 确定性:核心无 RNG/无墙钟;`actor` 为非人或受控,**晋级实盘人工专属**;实盘适配器 = 显式非目标。

## 7. 测试(TDD,门绿)

- `tests/execution/test_dry_run_pipeline.py`:
  - 端到端良性:权重→门批准→纸面成交→PnL 对账一致;DryRunRun 字段完整。
  - **门拒绝路径**:一个超限/重复意图 → `decision` 拒绝 → 不成交、原因入 run(fail-closed)。
  - **kill_switch latched** → 全意图被拒、零成交。
  - 确定性:同输入 → 同 DryRunRun(逐字段)。
- `tests/execution/test_dry_run_store.py`:save/get/all 往返;无库 fail-closed。
- 治理:新模块无网络/券商 import;`test_research_runner_boundary` 仍绿(编排器没进 research_runner)。
- `scripts/validate.py` 全绿。

## 8. 非目标(YAGNI)

- 不接实盘/真券商/真单/真钱(L2 边界);实盘 = 换适配器 + 人工批准,后续。
- 不接 console 页(首刀后端为主;"干跑运行"页后续增量)。
- 单策略(carry)、单次批量干跑;不做流式/调度/多策略编排。
- 不动晋级审批逻辑、不让非人因干跑结果自动晋级。
