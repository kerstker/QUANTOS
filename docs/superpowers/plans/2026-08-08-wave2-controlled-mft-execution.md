# QUANTOS Wave 2：受控中频 Testnet 执行实施计划

> 状态：实施计划。承接 Wave 1 实现提交 `bd08373` 与 L2 接受证据提交
> `1794977`。Wave 1 的 7 天 soak 仍由独立运维任务完成；本计划可以先完成
> L2/Testnet 工程闭环，但不得把开发期验证冒充 soak，也不得进入 Micro-Live。

## 目标

在不改变 `L2_DRY_RUN_ONLY` 和五个安全旗帜的前提下，把现有的 Binance
公开行情、组合意图、持久调度、testnet adapter、pre-trade gate、mandate、OMS、
对账、TCA、实时风险和恢复原语接成一个 1–4 小时节奏的持续 Testnet 服务闭环。

完成后的系统可以在 Binance Futures Testnet 上按预先限定的测试网 mandate
机械执行、恢复和对账，但仍然不具备真钱授权。Micro-Live 继续被外部 IdP、
mTLS、独立签名权威、进程权限隔离、前向证据、人工签署 mandate 和独立
Go/No-Go 结构性阻断。

## 已核实现状

以下能力已经存在，必须复用，禁止重写第二套：

- `services/data/binance_realtime_store.py`：哈希链实时快照存储；
- `tools/binance_realtime_stream.py`：Binance 公共 WebSocket 消费与快照刷新；
- `services/orchestration/`：持久命令、leader lease、scheduler、workflow 和恢复；
- `quantos/contracts/order.py` 与 `live/execution/oms_state.py`：canonical OMS 状态机；
- `live/exchange/binance_futures.py`：testnet adapter，mainnet 在 L2 构造即拒绝；
- `live/pipeline.py`：attestation→plan→gate→mandate→ledger→place→confirm→audit；
- `live/execution/{position_ledger,order_recovery,reconciliation,tca,risk_monitor,emergency}.py`；
- `live/governance/{validation_attestation,mandate,mandate_store,kill_switch_store}.py`；
- `tools/live_track_advance.py`、`tools/live_risk_sentinel.py` 和只读运维 API。

当前缺口不是算法原语，而是：

1. 公共行情没有统一的持久 cursor、序列缺口权威和 REST 补洞状态机；
2. 周期任务仍以独立脚本为主，没有一个单写者、可恢复的常驻编排根；
3. Wave 1 的验证/工作流回执尚未形成独立、过期即拒绝的执行命令封套；
4. OMS 以批次轮询为主，缺少持续订单事件 journal 与从零重放；
5. 风险、mandate 过期、数据陈旧和对账异常尚未在同一运行循环内统一停止新增风险；
6. Paper 与 Testnet 没有长期同决策逐笔对账和 soak 接受物；
7. 外部强身份、mTLS、出进程签名和生产权限域仍不存在，不能由本计划伪造。

## 不可改变的安全边界

- `LIVE_TRADING_ENABLED=false`
- `REAL_BROKER_ENABLED=false`
- `ALLOW_ORDER_SUBMISSION=false`
- `ALLOW_AI_DIRECT_TRADING=false`
- `REQUIRE_HUMAN_APPROVAL=true`
- readiness 恒为 `L2_DRY_RUN_ONLY`。
- 只允许 `execution_environment=BINANCE_FUTURES_TESTNET` 的自动提交；任何 mainnet、
  未知环境、真实资本或缺少明确 testnet 标记的命令都在构造或受理时拒绝。
- Testnet 提交许可必须使用独立字段表达，绝不复用或翻转真钱五旗。
- 研究、数据、前端进程不得读取凭证；凭证只由执行进程现有加载边界读取，且不得落盘、
  进入事件、日志、异常或测试 fixture。
- AI 不签 mandate、不选择风险数字、不复位 kill-switch、不扩大限额、不提交 mainnet 订单。
- 未知订单、陈旧行情、序列缺口、命令哈希不一致、mandate 到期、对账偏差、账本写失败、
  worker 身份不匹配或外部权威缺失一律停止新增风险。
- `live/` 继续只能单向依赖 `services/`/`quantos/`；`services/` 不得 import `live/`。
- Wave 3 前端不在本计划内；本计划只交付稳定的只读 snapshot/delta 契约。

## 运行架构

```text
Public Binance WS/REST
        │
        ▼
Ingest process ── append-only market store + cursor/gap journal
        │ verified watermark
        ▼
L2 scheduler leader ── feature/alpha/paper workers ── execution intent outbox
                                                   │ immutable TESTNET envelope
                                                   ▼
                                  isolated execution process / single writer
                                    gate → mandate → OMS → venue → ledger
                                                   │
                                      order events / fills / risk / TCA
                                                   ▼
                                  read-model projector + Paper/Testnet parity
```

时间由进程边界注入；纯核不读墙钟。所有持久身份由业务输入确定性派生，重启不得产生新
`command_id`、`trial_id` 或 `client_order_id`。

---

## Task 28：冻结 Testnet 执行命令与回执契约

**Files**

- Create: `quantos/contracts/mft_execution.py`
- Modify: `quantos/contracts/__init__.py`
- Test: `tests/contracts/test_mft_execution.py`
- Modify: relevant governance contract allowlists

**Interfaces**

- `TestnetExecutionIntent`：绑定 command/idempotency、portfolio、alpha IDs、决策时点、
  到期时点、目标仓位、数据 watermark、Wave 1 workflow receipt、validation artifact、
  policy/version 和全部输入摘要；
- `TestnetExecutionReceipt`：`ACCEPTED/BLOCKED/COMPLETED/FAILED/IN_FLIGHT_UNKNOWN`，
  绑定命令、OMS event head、ledger head、venue mode 和错误分类；
- `create_testnet_execution_intent(...)` 与 `verify_testnet_execution_intent(...)` 为纯函数；
- 环境只允许 `BINANCE_FUTURES_TESTNET`，并逐字段声明
  `real_capital=false`、`live_allowed=false`、`real_order_submission_allowed=false`。

**Steps**

1. 先写构造、稳定哈希、篡改、过期、未知 schema、mainnet/真钱字段对抗测试；
2. 复用 `stable_hash` 和现有 portfolio/order 值类型，不复制 `CommandEnvelope`；
3. 缺少 Wave 1 receipt、validation artifact 或数据 watermark 时返回 BLOCK，不接受自报 PASS；
4. 跑 contract + governance 回归并独立提交。

## Task 29：建立实时行情 cursor 与 gap authority

**Files**

- Modify: `services/data/binance_realtime_store.py`
- Create: `services/data/binance_stream_cursor.py`
- Modify: `tools/binance_realtime_stream.py`
- Modify: `tools/binance_rest_gap_fill.py`
- Test: `tests/data/test_binance_stream_cursor.py`
- Modify: `tests/tools/test_binance_realtime_stream.py`

**Interfaces**

- append-only `StreamCursorStore` 保存 channel/symbol/update range/watermark/gap state/hash chain；
- 状态：`CONTIGUOUS/GAP_DETECTED/RECOVERING/RECOVERED/BLOCKED`；
- 公开流只在 sequence 连续、REST 补洞覆盖且快照链有效时发布可交易 watermark；
- 无序、重复可幂等；时间倒退、覆盖不全或 REST 429/timeout 保持 BLOCKED。

**Steps**

1. 用最不方便的 fake 流覆盖重复、乱序、断流、跨连接 gap、补洞不完整和崩溃恢复；
2. 将 cursor/gap 核放在无网络的 `services/data`，网络仍只在 `tools/`；
3. 保留现有快照 schema 兼容读，新增字段全部加法；
4. 运行真实公共只读短时 smoke，不使用任何凭证或账户端点；
5. 独立提交。

## Task 30：实现单节点常驻 L2 编排根

**Files**

- Create: `tools/mft_l2_runtime.py`
- Create: `services/orchestration/mft_runtime.py`
- Modify: `tools/l2_schedule_entrypoint.py`
- Test: `tests/orchestration/test_mft_runtime.py`
- Test: `tests/tools/test_mft_l2_runtime.py`

**Interfaces**

- `MftRuntime.run_once(now_ms)` 只消费已登记任务，复用 leader lease、scheduler、command bus；
- 单次循环顺序：market health→materialize→hourly alpha→paper→intent projection→execution dispatch；
- 每个任务有持久 lease、预算、deadline、checkpoint、DLQ 和 deterministic retry key；
- 第二实例必须 `SKIPPED_NOT_LEADER`，stale fencing token 不能写任何 authority。

**Steps**

1. 先写双 leader、崩溃、超时、in-flight unknown、错过周期合并和重启测试；
2. 核心不启动线程、不读墙钟，常驻循环只在 `tools/`；
3. 失败只阻断下游，不把部分周期宣称 COMPLETED；
4. CLI 默认只跑 L2/Paper；执行 dispatch 在 Task 32 前保持显式 BLOCKED；
5. 独立提交。

## Task 31：从 Wave 1 产出组合意图 outbox

**Files**

- Create: `services/portfolio/mft_intent.py`
- Create: `services/portfolio/mft_intent_store.py`
- Modify: `tools/hourly_alpha_advance.py`
- Test: `tests/portfolio/test_mft_intent.py`
- Modify: `tests/tools/test_hourly_alpha_advance.py`

**Interfaces**

- 只接受 `PAPER` 且 validation/workflow/forward refs 完整一致的 sleeve；
- 复用 `RebalancePlan`、portfolio gate、容量/相关性/成本证据生成目标与当前仓位差；
- append-only outbox 先落库再可 dispatch，重复周期同一 intent hash；
- 0 个通过者合法地产生 `NO_ACTION` 回执，不产生空壳订单。

**Steps**

1. 先写 0 通过、ref mismatch、过期、陈旧 watermark、重复周期与账本失败测试；
2. 不读取 venue 账户，不 import `live/`，不持凭证；
3. Task 31 只生成 Testnet envelope，不能直接调用 adapter；
4. 独立提交。

## Task 32：实现独立 Testnet execution inbox 与单写者服务

**Files**

- Create: `live/execution/command_store.py`
- Create: `live/execution/service.py`
- Create: `tools/testnet_execution_service.py`
- Test: `tests/live/test_execution_command_store.py`
- Test: `tests/live/test_execution_service.py`
- Test: `tests/tools/test_testnet_execution_service.py`

**Interfaces**

- execution inbox 使用 append-only SQLite/CAS：`RECEIVED→VALIDATED→IN_FLIGHT→COMPLETED|BLOCKED|FAILED|UNKNOWN`；
- 服务唯一写者复验 envelope、数据 freshness、Wave 1 refs、kill-switch、mandate、venue mode；
- 只有明确 `BINANCE_FUTURES_TESTNET` 且 adapter 回报 `venue_mode=testnet` 才可调用现有 pipeline；
- ACK 不确定先记 UNKNOWN，再按 `client_order_id` 回查，绝不盲目重发；
- 启动时先恢复所有 UNKNOWN，未清零前禁止新风险。

**Steps**

1. 先写双 worker claim、重复 command、ACK 丢失、账本写失败、进程崩溃和 mainnet 对抗测试；
2. 复用现有 `submit_live_batch` 与 `SqliteOrderRecoveryStore`，不复制订单逻辑；
3. 工具只在显式 `--testnet-submit` 与有效 testnet mandate 下启用提交；默认 preview；
4. 任何凭证异常只输出安全分类，不输出值、路径内容或请求签名；
5. 独立提交。

## Task 33：把 OMS 升为持续订单事件 authority

**Files**

- Create: `live/execution/order_event_store.py`
- Create: `live/execution/oms.py`
- Modify: `live/execution/position_ledger.py`
- Modify: `live/execution/order_recovery.py`
- Test: `tests/live/test_order_event_store.py`
- Test: `tests/live/test_oms.py`

**Interfaces**

- append-only order event journal 保存 venue event id、source sequence、canonical state、payload hash、前链 hash；
- reducer 从零重放得到与 `LiveLedger` 相同的 order/fill/position 状态；
- 支持 REST poll 事件，预留 user-data stream adapter；两源重复幂等、冲突 fail-closed；
- 部分成交、撤单成交竞态、终态倒退、重复 fill、未知订单全部覆盖。

**Steps**

1. 先写事件排列组合和从零重放性质测试；
2. canonical 转换只走 `quantos/contracts/order.py` 与 `live/execution/oms_state.py`；
3. `LiveLedger` 保持兼容读，新增写先 journal 后 projection；
4. 账本和 event journal 不能原子提交时，未知状态必须进入 recovery queue；
5. 独立提交。

## Task 34：统一实时风险、mandate 到期与数据健康门

**Files**

- Create: `live/execution/risk_supervisor.py`
- Modify: `live/execution/risk_monitor.py`
- Modify: `live/governance/mandate_store.py`
- Modify: `tools/live_risk_sentinel.py`
- Test: `tests/live/test_risk_supervisor.py`

**Interfaces**

- 每轮同时检查 market watermark、mandate、账户权益、gross/net、margin、集中度、参与率、
  position/ledger reconciliation、UNKNOWN orders 和 scheduler freshness；
- verdict：`ALLOW_TESTNET_NEW_RISK/HALT_NEW_RISK/REDUCE_ONLY_REQUIRED/MANUAL_REVIEW`；
- 任一输入缺失或异常至少 `HALT_NEW_RISK`；严重对账/保证金/未知订单闩锁 kill-switch；
- mandate 到期后不新增风险，AI 无恢复路径。

**Steps**

1. 先写逐项缺失、边界值、同时故障、时间倒退和恢复不越权测试；
2. 只组合已有风险原语，阈值来自版本化 policy/mandate，不在 UI 或 supervisor 硬编码业务数字；
3. 自动动作只允许停止和减险；
4. 独立提交。

## Task 35：完成恢复与独立 reduce-only 应急闭环

**Files**

- Modify: `live/execution/emergency.py`
- Modify: `live/execution/order_recovery.py`
- Create: `live/execution/recovery_coordinator.py`
- Test: `tests/live/test_recovery_coordinator.py`
- Modify: `tests/live/test_emergency.py`

**Interfaces**

- 恢复顺序固定：未知订单→OMS replay→ledger/venue reconcile→risk state→新命令；
- kill-switch 后主路径恒拒绝；独立 emergency path 只接受可证明逐腿不增敞口的 MARKET reduce-only；
- 任何腿的当前 venue 仓位未知时不生成减仓单；部分失败保持 kill-switch 并转人工；
- kill-switch reset 仍为现有三方真人流程，本任务不加自动 reset。

**Steps**

1. 先写 crash-point matrix：place 前、ACK 后、journal 后、projection 前、fill 后、reconcile 前；
2. 验证每个 crash point 至多一次经济副作用且可审计；
3. emergency adapter 仍只限 Testnet；mainnet 构造拒绝测试必须保留；
4. 独立提交。

## Task 36：建立 Paper/Testnet 同决策对账与 TCA

**Files**

- Create: `services/monitoring/mft_parity.py`
- Create: `services/monitoring/mft_parity_store.py`
- Create: `tools/mft_parity_report.py`
- Test: `tests/monitoring/test_mft_parity.py`
- Test: `tests/tools/test_mft_parity_report.py`

**Interfaces**

- 按 intent/decision/client-order identity 对齐 Paper preview 与 Testnet order/fill；
- 报告覆盖目标差、请求差、成交率、滑点、延迟、费用/资金费未知、position/equity drift；
- Testnet 不是主网冲击证据，报告必须标 `TESTNET_MICROSTRUCTURE_NOT_PRODUCTION`；
- 差异阈值只作版本化 UNCALIBRATED 工程门，不能变成真钱 mandate 数字。

**Steps**

1. 先写未成交、部分成交、丢腿、错配、缺费用和不同决策时点测试；
2. 没有 Testnet 成交时返回 `INSUFFICIENT_EVIDENCE`，不把 0 误写成完美；
3. 报告 append-only，按 canonical run 绑定输入；
4. 独立提交。

## Task 37：发布 Wave 2 只读 snapshot/delta 契约

**Files**

- Create: `services/console/api/mft_execution_views.py`
- Modify: `services/console/main.py`
- Create: `services/monitoring/mft_read_model.py`
- Test: `tests/research/test_mft_execution_console_contract.py`

**Interfaces**

- GET snapshot 包含 schema/as_of/freshness/watermark/provenance、market gap、scheduler、
  intent funnel、OMS、fills、positions、risk、reconciliation、TCA、mandate expiry 和 safety；
- delta 是带 cursor 的 append-only控制事件；补洞失败明确要求重新取 snapshot；
- store 缺失、schema 未知、数据陈旧显示 `UNAVAILABLE/BLOCKED`，绝不回退 mock；
- console 仍只读，不接 submit/reset/approve。

**Steps**

1. 先写 snapshot + delta 重建等价、cursor gap、store 损坏和无路径泄漏测试；
2. 使用显式 read-only store 打开模式；
3. 为 Wave 3 冻结 v1 契约，之后只做兼容加法；
4. 独立提交。

## Task 38：Wave 2 确定性、故障恢复和全量门禁验收

**Files**

- Create: `tests/integration/test_mft_testnet_execution.py`
- Create: `docs/runbooks/mft-testnet-operations.md`
- Generate: `evidence/mft_wave2_acceptance.json`

**Acceptance**

- fake venue 完整走 market→intent→inbox→gate→mandate→OMS→fill→ledger→risk→parity；
- 注入断流、乱序、重复、部分成交、撤单竞态、429、timeout、ACK 丢失、账本失败和进程崩溃；
- 从零 replay 得到相同 read model；重复运行无重复订单/成交/事件；
- 任何无 grant/mainnet/过期/未知状态结构性零提交；
- `py -3.14 scripts/validate.py` 全绿，readiness 仍为 `L2_DRY_RUN_ONLY`；
- 实际 Testnet submit/soak 只在用户已配置的测试网凭证、有效 testnet mandate 和显式运维启动下进行；
  凭证缺失或用户未启动时证据必须标 `OPERATIONAL_SOAK_PENDING`。

## Task 39：独立 Paper/Testnet soak 与 Micro-Live 准备审计

这是运维任务，不是一次开发会话内可伪造的测试。

- 连续运行窗口、最小长度和告警值必须在启动前写入版本化 soak policy；
- 每周期保存 market continuity、scheduler lease、intent、OMS、fill、reconciliation、risk、TCA、
  parity 和恢复演练证据；
- 期间不得使用 mainnet，不得读取或创建真实主网凭证，不得签真钱 mandate；
- soak 完成也只证明 Testnet 工程闭环，不自动解锁 Micro-Live；
- Micro-Live 仍必须等待外部 IdP/mTLS、独立签名权威、进程权限隔离、足够前向证据、
  人工签署真钱 mandate 和独立 Go/No-Go。

---

## 每个任务的固定验收顺序

1. 单个规格审查：接口、时序、失败语义和安全边界；
2. 先写失败测试；
3. 最小实现；
4. 聚焦回归；
5. 单个质量审查：持久化、并发、恢复、日志/凭证泄漏和边界；
6. 治理扫描；
7. 实际运行验收（如该任务有进程/CLI）；
8. 独立提交。

timeout、429、空结果、异常退出、缺证据、未知状态和测试网连接失败都不得当作 PASS。

## Wave 2 退出条件

- Tasks 28–38 全部独立提交并通过 canonical gate；
- fake/回放故障矩阵全绿，零未解释重复副作用；
- 实际 Testnet soak 若尚未完成，状态必须保持 `IMPLEMENTATION_ACCEPTED_SOAK_PENDING`；
- 五个真钱安全旗帜不变，mainnet 构造仍结构性拒绝；
- Wave 3 只能消费 Task 37 的稳定只读契约，不得直接调用 adapter 或持久化库；
- 任何 Micro-Live 工作都不属于本计划，需新的外部基础设施与人工 Go/No-Go。
