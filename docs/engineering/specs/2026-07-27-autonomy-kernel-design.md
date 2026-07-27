# 自治内核(Autonomy Kernel)设计与 K 系列切片计划(727 §2.5 / §4.2,Phase 2)

- 日期:2026-07-27
- 依据:[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §2.5(自治内核)、§4.2
  (CommandEnvelope)、§0.4.8(未知订单/在飞状态绝不盲目重试)
- 状态:**K1–K3 + K4a 已落地**(K1:command_envelope + idempotency 持久命令日志 +
  command_bus;K2:leader_lease 单实例 Leader + fencing token + 任务租约;
  K3:scheduler 持久化确定性调度器;K4a:policy_engine + event_router,
  `services/orchestration/`,84 测试)。K4b(workflow_engine / recovery /
  autonomy_kernel 门面)为后续切片,见 §3。
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
| K2 | `leader_lease`:单节点领导租约(多实例防双活;SQLite CAS,确定性时钟注入) | **已落地(2026-07-27)** |
| K3 | `scheduler`:持久化调度器,missed-run 补跑语义,与 leader_lease 联动 | **已落地(2026-07-27)** |
| K4 | `workflow_engine` / `policy_engine` / `event_router` / `recovery` / `autonomy_kernel` 门面 | **K4a 已落地**(policy_engine + event_router,2026-07-27);K4b(workflow/recovery/门面)未做 |

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

## 6. K2 实施记录(leader_lease,2026-07-27)

`services/orchestration/leader_lease.py` — `SqliteLeaderLeaseStore`,与
`SqliteCommandLog` 同款打开语义与重链哈希链(每个 resource 一行,获得/续期/
释放/易主重写该行并按 seq 级联重链;DELETE 触发器禁止;篡改库打开即拒,
测试钉死)。表 `leader_leases` 可与 command log 共库(不同表)。

语义裁定(逐条对应 727 §16「单实例 Leader + fencing token + 任务租约」):

1. **同 owner acquire 幂等、不延期**:持有中同 owner 再 acquire 返回当前
   lease,token 不变、expires 不动——延期只能走 `renew`,防止「隐式续命」
   掩盖「忘记 renew」的 bug。
2. **释放不删行、token 不归零**:released=1 保留行与 token;再获得(同/异
   owner、过期易主同理)token 一律 +1。token 归零会让「旧 token 又被发出」
   的 fencing 证明失效,这是 fencing 的数学根基,不可省。
3. **过期判定 `now_ms >= expires_at_ms`**:到期瞬间即过期(测试钉死边界);
   不引入 grace period——宽限期是部署策略,不属于原语。
4. **fencing 闸门 = `require_owner`**:无 lease/易主/token 旧/已过期/已释放
   一律 `LeaderLeaseFencedError`——stale leader 写不进。release 不卡过期
   (自己的租约自己收尾),但 token 失配仍 fence。
5. **单节点诚实边界**:防的是本机多实例/崩溃重启后的僵尸执行者,**不是**
   跨主机共识(无 quorum、无网络);墙钟跳变会让「过期」判定失真,本机
   单调性由部署环境保证;fencing token 是否写进下游 store 的 CAS 条件是
   各消费方的事(K3 起接入)。
6. 确定性核心:now_ms/lease_ms 全部调用方传入的非负/正整数,无墙钟、
   无随机、无后台线程(不自动续约——续约节奏属于 K3 调度器)。

## 7. 明确非目标(K1/K2 不做,单独评审)

- **跨主机共识/真分布式选主**:单节点诚实边界;跨主机是 post-L2(Phase 14)。
- **真调度器接生产任务**:K3 才建,且仍需人工评审其 missed-run 语义。
- **envelope v2 扩字段**(如 priority/deadline/retry_policy):破坏性契约决策,
  单独评审,不在本切片顺手加。
- **自动恢复/自动重试**:见 §5 规则 3;恢复层是 K4 的独立设计。
- **命令侧跨进程 outbox 派合**:事件发布复用既有 DurableEventBus(其自身
  at-least-once 语义不变),K1 不新增第六条 outbox。
- **租约自动续约/看门狗线程**:确定性核心不起线程;续约节奏是 K3 调度器的事。

## 8. L2 边界声明

- 五个安全旗标、live/order/凭证路径:**零触碰**。
- `services/orchestration/` 仅 import stdlib + `quantos.contracts` +
  `services.monitoring.event_bus`(TYPE_CHECKING);无网络、无券商 SDK,
  治理扫描覆盖。
- 确定性核心:无墙钟、无随机;所有时间戳为调用方传入的非负整数。
- CommandBus 是编排原语,**不授予**任何新权限:handler 能做什么仍由调用方
  既有边界决定(research_runner 白名单、live 人工 mandate 等,一字未动)。

## 9. K3 实施记录(scheduler,2026-07-27)

`services/orchestration/scheduler.py` — `SqliteSchedulerStore`(双链:
`scheduler_tasks` 重链链 + `scheduler_runs` 追加链,后者 UPDATE/DELETE 触发器
双禁)+ `Scheduler`(复用 K1 CommandBus 与 K2 LeaderLeaseStore,不起线程,
兑现节奏由调用方驱动 `run_once`)。三 store 可共库(不同表),崩溃重启恢复
已测(tmp_path 共库重开,补跑继续)。

语义裁定:

1. **只有 leader 兑现**:run_once 先 acquire 租约,异 owner 持有中 → 每个 due
   task 记 `SKIPPED_NOT_LEADER`(带持有者 fencing_token 与 `held_by:` 明细,
   跳过也进审计);每个 task 兑现前再过 `require_owner` fencing 闸门,轮中
   失约即记 SKIPPED 并中止本轮。
2. **按计划时刻推进,不漂移**:INTERVAL 完成后 `next_due = slot_ms +
   interval_ms`(slot 是计划时刻,不是 now)——调度延迟不累积成周期漂移,
   测试钉死精确值。
3. **错过补跑合并(coalesce)**:落后超过一个周期(`now - slot > interval`)
   → 被跳过的旧槽位各追加一条 COALESCED run(带 `coalesced into slot N`
   明细,错过哪些诚实留痕,绝不静默),只兑现最新完整周期槽位;ONE_SHOT
   不合并。落后 3 槽位 → 2 COALESCED + 1 COMPLETED,executor 恰跑 1 次。
4. **幂等补跑 = K1 重放**:command_id/idempotency_key =
   `sched:{task_id}:{slot}:{attempts}`。**显式偏差登记**:任务书规格写的是
   `{task_id}:{slot'}`,不含 attempts——但 K1 规定 FAILED 终态不换 key 不重跑,
   不含 attempts 的 key 会让失败退避重试永远撞 CommandStateError,重试机制
   名存实亡。编入 attempts 后每次重试是逻辑上的新命令,K1「每条命令至多
   执行一次」不变;同 (task, 槽位, attempts) 的崩溃重跑仍是零副作用重放
   (executor 计数恒 1,测试钉死)。
5. **确定性退避 + DLQ**:FAILED → attempts+1;耗尽(max_attempts)→ dead=1 +
   追加 DLQ run(`attempts exhausted (n>=m)` 明细),`due_tasks` 永不再含它,
   `dead_tasks()` 只上面、绝不自动重试;否则 `next_due = now + min(base *
   2**attempts, cap)`(attempts 为递增后的值——递增前/后是规格模糊点,取
   递增后,首次失败退避 2·base;base/cap 为 store 构造参数,默认
   60_000/3_600_000)。
6. **record_run 单事务原子**:run 追加与 task 迁移同事务提交——崩溃窗口只
   可能整段缺失,重放后恰好补回一条 run;不存在半截记录(这正是「重放后
   两条 COMPLETED run」的规格预期被修正为「恰一条」的原因,测试按修正后
   语义钉死)。
7. **未知结果大声抛出,已知失败正常记账**:CommandBusError(in-flight 冲突
   = 结果未知)从 run_once 原样抛出、不记 FAILED——命令留在 RECEIVED 由
   in_flight() 上面,人工裁决(镜像 §0.4.8);CommandStateError(FAILED 重放
   = 结果已知为失败,崩溃在 command-fail 与 record_run 之间的收敛路径)按
   任务失败记账,attempts 正常推进。
8. **单任务失败不拖垮整轮**:FAILED 记账后 continue;due 快照序
   (next_due_ms, task_id) 确定性。
9. **actor 调用方传入**:调度器不伪造身份(非人 actor 是常态,测试用
   SYSTEM);executor 与 CommandBus handler 同约定(result 或 (result, events),
   事件走 K1 publish_once 因果链)。

## 10. K4a 实施记录(policy_engine + event_router,2026-07-27)

**K4 为什么拆 a/b 两提交**:小批量原则——K4 原条目含五个组件,一次落地无法
保持每片独立可测/独立评审。K4a 是两个**纯核/纯编排**组件(无表、无迁移、
无状态机),先行;K4b(workflow_engine / recovery / autonomy_kernel 门面)
涉及持久化与恢复语义,单独评审。

### 10.1 policy_engine(纯核,无 IO)

727 §2.5「不得由语言模型直接决定状态转换」的落地形状:策略 = 注册的确定性
纯函数 `fn(context) -> (verdict, reasons)`,评估产出带证据哈希的不可变
`PolicyDecision`。语义裁定:

1. **不迁移任何现有判定器**:research_risk gate / pre_trade gate /
   promotion_checks 等各域硬编码 fail-closed 判定保持原样。本模块只统一
   注册/评估/记录形状;迁移是后续各域显式评审的事,绝不顺手搬。
2. **fail-closed 三规则**(727 §9.2):未注册 → PolicyNotFoundError(绝不
   默认 PASS);判定器抛异常或返回非法 verdict → verdict=FAIL +
   `POLICY_ERROR:<类型名>`,**不向外抛**(判定器异常 = 拒);缺证据必须
   FAIL 或 ABSTAIN——ABSTAIN 是「证据不足」,区别于 FAIL 的「明确拒绝」,
   两者都不是放行。
3. **engine 统一重封装**:fn 无论返回 PolicyDecision 还是 (verdict, reasons)
   简写,engine 只取 (verdict, reasons),用注册版本 + now_ms + 本次 context
   重算 evidence_hash——fn 无法伪造版本、时间或证据哈希(测试钉死:
   fn 返回伪造版本的 decision 被归一)。
4. **版本冲突显式**:同 policy_id 同 version 幂等(fn 身份按 id 不比——
   同版本换实现是部署层的事);不同 version → PolicyConflictError,绝不
   静默覆盖(与 trial ledger 诚实记账同源)。
5. **evidence_hash 只经 create 计算**:stable_hash(policy_id, version,
   verdict, reasons, stable_hash(context));context 本体不进决策记录
   (可能很大),其哈希进。同输入同哈希、键序无关、与 evaluated_at 无关
   (测试钉死)。

### 10.2 event_router(纯编排,无 IO)

DurableEventBus 已有 append/replay/subscribe/消费组;本模块补「类型→处理器
的声明式路由表」。语义裁定:

1. **多 handler 注册序**(显式偏差登记):任务书规格写「不同 handler →
   RouterConflictError」,但其测试清单同时要求「多 handler 注册序」且
   RouterDispatchError 的「已调用计数」只在多 handler 下有意义——故采用
   多 handler 设计(注册序 = 分发序),冲突收窄为「同名(__name__)不同
   身份」的静默替换防护;重复注册同一 handler(id 相同)幂等。
2. **at-least-once 边界**:handler 抛异常 → RouterDispatchError(第一个
   异常 + 已调用计数,含抛异常者),已调用的不回滚,dispatch/replay 立即
   中止;重放因此可能重复调用 handler——**幂等是 handler 自己的责任**
   (按 event_id 去重的惯用法有测试示例),路由层不替 handler 去重,正如
   bus 不替 consumer 去重。
3. **无匹配 = 0,非错误**:路由是声明式过滤,未订阅类型安静流过。
4. **event_view 原样传递**:handler 收到 bus `records()` 的 record dict
   ({seq, event, correlation_id, causation_id}),不重新包装;直接
   dispatch 单个 Event 时行级因果无从得知,以 None/"" 诚实缺省,绝不伪造。
5. **replay_from 对账**:经 `bus.records(since_seq=)`(bus 自带先验链)
   逐条 dispatch,返回 {dispatched, scanned, last_seq} 供恢复层增量对账;
   中途异常原样中止,恢复层按 last_seq 重跑。
