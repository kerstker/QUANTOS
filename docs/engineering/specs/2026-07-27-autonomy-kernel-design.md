# 自治内核(Autonomy Kernel)设计与 K 系列切片计划(727 §2.5 / §4.2,Phase 2)

- 日期:2026-07-27
- 依据:[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §2.5(自治内核)、§4.2
  (CommandEnvelope)、§0.4.8(未知订单/在飞状态绝不盲目重试)
- 状态:**K1 已落地**(command_envelope + idempotency 持久命令日志 + command_bus,
  `services/orchestration/`,21 测试)。K2–K4 为后续切片,见 §3。
- readiness:L2_DRY_RUN_ONLY 不变。本方案不改五个安全旗标,不新增任何真实下单
  路径,不触碰凭证,`services/` 内零网络 import。

## 1. 问题:命令侧幂等是分散的即兴品

事件侧(event bus / consumer journal / 六条 transactional outbox)已经系统化,
但**命令侧**——「谁、在什么时候、对哪个 subject、请求做什么、做完没有」——
今天在三个地方各写各的:

| 现状 | 位置 | 缺什么 |
|---|---|---|
| JobQueue `invocation_id` 幂等 | `services/research_runner/runner.py` | 进程内存/单店语义,无统一哈希链,无命令→事件因果 |
| LiveLedger 预记账 | `live/execution/` | live 专用,研究编排复用不了 |
| 各驱动器自拼重试 | `tools/*` | 无幂等保证,崩溃即悬挂 |

具体损失(不是洁癖):

1. **无统一命令日志**:一条研究命令从受理到完成的生命周期没有落盘权威;
   进程崩溃后「这条命令到底跑没跑完」只能靠猜。
2. **无命令→事件因果链**:事件有 `correlation_id/causation_id` 字段,但事件侧
   没有一个「命令」实体能作为 causation 的源头——因果链断在命令边界。
3. **JobQueue 崩溃悬挂**:invocation 受理后进程死亡,重启后该 invocation 处于
   不上不下的状态,没有 in-flight 上面机制,更没有恢复语义。

## 2. 现状盘点结论:事件侧 ~80% 已有,kernel 做薄编排

727 §2.5 列的内核组件中,**事件基础设施已经存在且承重**
(`services/monitoring/event_bus.py` 的 DurableEventBus:持久事件日志、
`publish`/`publish_once`、correlation/causation、hash-chain、consumer group
协调器)。因此内核**不重造事件层**,只做三件事:

1. 定义命令输入契约(CommandEnvelope,727 §4.2);
2. 提供持久、防篡改、幂等的命令日志(SqliteCommandLog);
3. 提供把「幂等受理 → handler → 事件因果发布 → 结果落账」串起来的薄编排
   (CommandBus)。

凡事件侧已有能力(publish_once 重试安全、链校验、DLQ)一律复用,不在内核里
复制第二份。

## 3. K 系列切片计划

| 切片 | 内容 | 状态 |
|---|---|---|
| **K1** | `command_envelope` + `idempotency`(SqliteCommandLog)+ `command_bus` | **本提交** |
| K2 | `leader_lease`:单节点领导租约(多实例防双活;SQLite CAS,确定性时钟注入) | 未做 |
| K3 | `scheduler`:持久化调度器,missed-run 补跑语义,与 leader_lease 联动 | 未做 |
| K4 | `workflow_engine` / `policy_engine` / `event_router` / `recovery` / `autonomy_kernel` 门面 | 未做 |

切分原则:每片独立可测、独立入库、不依赖后片;K1 不预留「将来一定这样」的
抽象(K3 的调度语义可能反过来修正 K1 的 API,届时显式评审)。

## 4. 727 §4.2 字段映射表

| 727 §4.2 | K1 落点 | 说明 |
|---|---|---|
| `command_id` | `CommandEnvelope.command_id` / `command_log.command_id`(PK) | 全局唯一 |
| `command_type` | 同名;`UNIQUE(command_type, idempotency_key)` 的一半 | 路由 handler 的类型串 |
| `requested_at` | 同名(int,**Unix 毫秒**,调用方供给) | 确定性核心无墙钟;事件时间戳仍用 µs(schema 现行约定),两个单位不混用 |
| `actor` | `quantos.contracts.alpha.Actor`(落库拆 `actor_id`/`actor_kind`) | 非人 actor 语义沿用 alpha 契约 |
| `subject` | 同名 | 命令作用对象(如 `alpha:crowd_contra`) |
| `expected_state_version` | 同名(Optional[int]);CommandBus 乐观锁,**写日志前**校验 | None = 不校验 |
| `idempotency_key` | 同名;幂等去重键 | 重试安全的核心 |
| `authorization_ref` | 同名,`""` = 无授权引用 | 引用多方批准等外部授权,内核不解释 |
| `payload_hash` | `create()` 由 `stable_hash(payload)` 计算;构造时 fail-closed 校验 | 调用方无法填错 |
| `payload` | 存为不可变 `MappingProxyType`,落库 canonical JSON | 构造后不可改写 |

### 4.1 与 §4.2 列清单的两点显式偏差

1. **`command_log` 多一列 `seq`(单调插入序号)**:§4.2 列清单没有任何可排序
   字段(`requested_at` 是调用方供给,不保证单调),哈希链必须有稳定链序。
2. **`recorded_at` 语义 = 本行当前版本的记录时间**:begin 时为受理时间,
   complete/fail 时更新为迁移时间。§4.2 列清单没有 `completed_at`/`failed_at`,
   而迁移时间必须落库才能进链、可验证。`fail` 的 `error_class` 同理,以
   canonical JSON `{"error_class": ...}` 落入 `result_json`。

### 4.2 UPDATE 为什么被允许(与纯追加表的差异)

命令行承载的是「这条命令当前处在生命周期哪一步」的**可变性状态**(CAS 状态机:
`RECEIVED → COMPLETED|FAILED`,终态不可变),不是事件历史。防篡改由哈希链
承担:每次状态迁移**重链**(重算该行 record_hash 并按 seq 级联重算其后所有行),
DELETE 由触发器结构性禁止。级联重链是 O(n) 的刻意开销——命令量远低于事件量;
若未来成为瓶颈,正确解法是迁到「追加式迁移表 + 状态视图」(kill_switch_store
模式),而不是放弃重链。

## 5. 崩溃语义:at-least-once,in-flight 只上面不重试

CommandBus 执行链上的崩溃窗口与裁决:

| 崩溃点 | 落库状态 | 恢复语义 |
|---|---|---|
| 版本检查前 | 无痕迹 | 直接重试 |
| begin 后、handler 前/中 | RECEIVED | **in-flight 上面**,绝不自动重跑(镜像 §0.4.8) |
| 事件已 publish、complete 前 | RECEIVED(事件已在总线) | 同上;事件已 `publish_once` 幂等,恢复层重放同一确定性事件无重复,但「命令是否算完成」由人工/恢复层裁决 |
| complete 后 | COMPLETED | 重放返回存证结果,handler 不再运行 |

三条硬规则:

1. **重复命令不产生重复副作用**:同 (command_type, idempotency_key) + 同
   payload 的已完成命令,重放直接返回 `json.loads(result_json)`,handler 调用
   计数恒为 1(测试钉死)。
2. **失败命令不自动重跑**:FAILED 是终态,同 key 重提 CommandStateError;
   显式换 key 重提。
3. **在飞命令不自动重跑**:同 key 且状态 RECEIVED(他处崩溃/并发在飞)→
   CommandBusError。恢复是 K 系列后续切片(recovery)+ 人工决策,不在 K1。

结果保真度边界:重放路径返回 `json.loads` 后的值,结果必须 JSON 可往返
(tuple 会变成 list)——调用方责任,已在 docstring 声明。

## 6. 明确非目标(K1 不做,单独评审)

- **跨主机共识/真分布式选主**:单节点诚实边界;跨主机是 post-L2(Phase 14)。
- **真调度器接生产任务**:K3 才建,且仍需人工评审其 missed-run 语义。
- **envelope v2 扩字段**(如 priority/deadline/retry_policy):破坏性契约决策,
  单独评审,不在本切片顺手加。
- **自动恢复/自动重试**:见 §5 规则 3;恢复层是 K4 的独立设计。
- **命令侧跨进程 outbox 派合**:事件发布复用既有 DurableEventBus(其自身
  at-least-once 语义不变),K1 不新增第六条 outbox。

## 7. L2 边界声明

- 五个安全旗标、live/order/凭证路径:**零触碰**。
- `services/orchestration/` 仅 import stdlib + `quantos.contracts` +
  `services.monitoring.event_bus`(TYPE_CHECKING);无网络、无券商 SDK,
  治理扫描覆盖。
- 确定性核心:无墙钟、无随机;所有时间戳为调用方传入的非负整数。
- CommandBus 是编排原语,**不授予**任何新权限:handler 能做什么仍由调用方
  既有边界决定(research_runner 白名单、live 人工 mandate 等,一字未动)。
