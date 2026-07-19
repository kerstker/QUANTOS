# QUANTOS L2 可信前向证据钟完整性设计

日期：2026-07-17
状态：待用户审核
范围：`L2_DRY_RUN_ONLY`，仅研究与 paper evidence；不连接券商、不提交订单、不提升 readiness

## 1. 背景与当前事实

当前前向框架已经具备“先记录权重、后到价格再兑现”的核心时间顺序，也已经在当前工作树中加入两项正确的失败关闭保护：

- `paper_periods` 的同身份、不同内容重放会拒绝；
- 重复或不存在的 `mark_realized` 会拒绝。

但当前实现仍不能构成机构级的可信前向证据链：

1. `forward_decisions` 对同一 `(alpha_id, decided_ts)` 使用 `INSERT OR IGNORE`，不同内容会被静默忽略，无法区分幂等重放与身份冲突。
2. 决策没有持久化绑定当时的数据质量准入记录；兑现只接收一个可由调用方传入的布尔值 `quality_evidence_current=True`。
3. `paper_periods` 写入和 `forward_decisions.realized=1` 由两个 SQLite 连接、两次事务提交；进程在两者之间退出时会产生部分提交。
4. `realized` 是可变状态，没有独立的追加式 realization event、输入摘要和哈希链。
5. 历史决策没有可证明的数据准入绑定，不能追溯补签为“真实前向”。
6. 当前权威数据准入为 `BLOCK`；前向报告为 0 个已兑现周。基础设施可以立即修复，但任何 8 周或更长期限都必须等待自然时间。
7. 旧来源名称包含 `FORWARD_LIVE`，容易被误读为真实交易；本设计明确改用 L2 paper 语义。

## 2. 目标

本子项目只解决“证据钟是否真实、原子、可重放、可审计”，不以提高 Sharpe 或关闭外部红线为目标。

完成后必须满足：

1. 官方前向决策只有在持久化的数据质量准入 authority 为 `PASS` 时才能写入。
2. 每个新决策都绑定准入 ID、evidence hash、decision hash、instrument scope、价格口径和记录时间。
3. 同一决策身份的完全相同重放幂等；任何字段不同都失败关闭。
4. 兑现时再次要求一个当前、已持久化且为 `PASS` 的准入记录。
5. realization event、paper period、兼容状态和本地事件记录在同一 SQLite 事务提交。
6. realization event 是追加式、哈希链接、输入可复算；相同重放幂等，不同结果冲突。
7. 旧决策和旧 period 原样保留，但没有准入绑定的记录明确归类为 `LEGACY_UNATTESTED`，不计入新的前向周数。
8. 状态与控制台只能显示 `ATTESTED_PENDING`、`ATTESTED_REALIZED`、`LEGACY_UNATTESTED`、`DATA_BLOCKED` 等诚实状态。
9. 所有新增服务代码保持离线、确定性、无网络、无 RNG、无隐式墙钟。

## 3. 非目标

- 不接入真实券商、CTP、FIX、OMS 或交易所下单 API。
- 不使用任何 KEY，不读取凭据，不改变五个安全开关。
- 不把历史数据回填伪装成决策后到达的数据。
- 不承诺或伪造 8 周前向业绩；本项目只能启动并保护时钟。
- 不在本子项目中解决 tick/L2 数据采购、外部 IdP、WORM、跨主机灾备或实盘 SOR。
- 不删除历史 paper track、历史决策或当前用户的未提交修改。

## 4. 方案比较

### 方案 A：仅加强现有两次提交

继续先调用 `PaperTrackStore.append_periods`，再调用 `ForwardLedger.mark_realized`，增加更多重试。

优点是改动最少；缺点是无法消除两次提交之间的崩溃窗口，也没有独立 realization event。拒绝采用。

### 方案 B：同库原子证据事务（采用）

保持现有 `paper_track.db` 和公开查询接口，通过加表、加触发器和一个原子 `commit_realization` 能力升级 `ForwardLedger`。`PaperTrackStore` 继续承担只读分析与可重建 summary；权威事实由 decision binding、realization event 和 paper period 共同组成。

优点：增量迁移、兼容现有数据、改动范围可控、能够真正消除部分提交。缺点：仍是单机 SQLite，不能替代外部不可篡改存储。

### 方案 C：重写为独立事件溯源服务

建立新服务、独立数据库和全局事件消费拓扑。长期可扩展性更好，但会与当前大量在途 event/catalog/outbox 改动重叠，迁移风险远高于本阶段收益。留作后续平台化工作。

## 5. 数据模型

采用只增加、不删除的 schema v2。

### 5.1 `forward_decision_bindings`

每个新决策恰好一条准入绑定：

- `alpha_id`, `decided_ts`：外键式身份；
- `decision_payload_sha256`：规范化权重、信号、source、recorded_at 的摘要；
- `admission_id`, `admission_evidence_sha256`, `admission_decision_hash`；
- `admission_recorded_at_ms`, `price_basis`, `instrument_scope_json`；
- `created_at_ms`, `prev_hash`, `row_hash`；
- 主键 `(alpha_id, decided_ts)`。

表禁止 UPDATE/DELETE。没有对应 binding 的旧决策属于 `LEGACY_UNATTESTED`。

### 5.2 `forward_realizations`

每条已认证决策最多一个权威兑现事件：

- 决策身份和 `decision_payload_sha256`；
- `as_of_ts`, `realized_return`, `realized_ic`, `source`；
- 兑现时准入 ID 与两个摘要；
- 规范化 `inputs_json` 和 `inputs_sha256`，至少保留逐名权重、起止价格收益、funding contribution、覆盖率和被使用的 instrument 集合；
- `committed_at_ms`, `prev_hash`, `row_hash`；
- 唯一键 `(alpha_id, decided_ts)`，并约束 `(alpha_id, as_of_ts)` 与 paper period 一致。

表禁止 UPDATE/DELETE。哈希链按单调本地序号连接，读取和生成 evidence 时全链验证。

### 5.3 现有表保护

- `forward_decisions`：禁止修改身份、时间、权重、信号和 source；仅保留 `realized` 作为兼容投影。
- `paper_periods`：增加禁止 UPDATE/DELETE 的触发器；写入仍执行“同内容幂等、异内容冲突”。
- `paper_track`：明确为可重建投影，可以重算，不作为权威前向事实。

### 5.4 本地事务事件

同一事务追加一个规范化的 `FORWARD_L2_REALIZATION_COMMITTED` 本地事件记录，绑定 realization row hash。第一阶段不伪装成跨主机消息平台；接入全局 event bus 时再按现有 owner-store outbox 模式增加 dispatch journal。

## 6. 核心 API 与控制流

### 6.1 准入引用

新增不可变 `ForwardAdmissionRef`，只能从 `DataQualityAdmissionStore` 中已经持久化的 `DataQualityAdmissionDecision` 构造。构造过程必须验证：

- admission history 可读且链有效；
- ID 对应记录存在；
- evidence hash、decision hash 与 authority 一致；
- readiness 为 `L2_DRY_RUN_ONLY`；
- purpose 为 `FORWARD_EVIDENCE_DATA_ADMISSION`；
- 总体、quality、lineage verdict 均为 `PASS`；
- price basis 为 `PERP_MARK_PRICE`；
- 新决策中的全部 instrument 都在 authority scope 内。

任何缺失、BLOCK、过期、摘要不符或读取异常均返回 `DATA_BLOCKED`，不得退化为布尔放行。

这里的“只能”指官方承重 API、类型接口、驱动器接线和治理测试共同强制的路径约束。与项目总威胁模型一致，同一受信 Python 进程内的恶意代码仍可能直接构造对象或改写调用路径；本地哈希链只能提供篡改证据，不能替代进程隔离、外部签名 authority 或独立封存。L2 因此继续失败关闭，绝不据此获得实盘权限。

### 6.2 记录决策

`record_decision` 增加必需的 `admission_ref`：

1. 规范化并验证权重、信号、时间和 L2 source；
2. 查询现有 decision 和 binding；
3. 完全相同则幂等返回 `False`；
4. 身份相同而任一内容不同则抛 `ForwardLedgerError`；
5. 在一个事务中插入 decision 和 binding；
6. source 使用 `FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING`。

旧的无绑定记录不会被自动补齐，也不会进入 attested pending 集合。

### 6.3 兑现

`realize_due` 不再接收 `quality_evidence_current: bool`，而接收经 authority 验证的 `ForwardAdmissionRef` 和显式 `committed_at_ms`。纯计算完成后调用：

`ledger.commit_realization(decision, realization, admission_ref)`

该方法在一个 `BEGIN IMMEDIATE` 事务中：

1. 复核 decision binding、pending 状态和两个 admission scope；
2. 处理完全相同的 realization 重放；
3. 检测任何身份或内容冲突；
4. 插入 `forward_realizations`；
5. 插入 `paper_periods`；
6. 更新兼容 `realized` 投影；
7. 追加本地事务事件；
8. 一次提交。

任何步骤失败全部回滚。`paper_track` summary 在提交后从权威 period 重建；summary 失败只报告 `PROJECTION_STALE`，不得撤销或改写已提交事实。

### 6.4 官方驱动器

`tools/forward_track_advance.py` 的顺序变为：

1. 运行现有数据质量准入并从 authority DB 重新读取已提交记录；
2. BLOCK/BREACH 时不记录新决策、不兑现，输出 `DATA_BLOCKED`；
3. PASS 时构造 `ForwardAdmissionRef`；
4. 只记录新的 attested 决策；
5. 只兑现 attested pending 决策；
6. 状态中的前向周数只统计 v2 attested realizations；
7. legacy 数量单列展示，绝不混入分母。

当前数据库中没有 binding 的两条决策保留原样，但不会被后续新数据兑现为认证证据。

## 7. 证据与状态

新增确定性生成器 `tools/forward_evidence_status.py`，输出受控 evidence：

- schema/version/code binding；
- decision、binding、realization 和 legacy 数量；
- 逐 alpha 的 attested weeks、pending、first/last timestamps；
- 两条哈希链和表触发器验证结果；
- 当前数据准入引用；
- 价格口径与 source；
- 是否满足现有 8 周人工复核最低门槛。

`--check` 只读且不得推进时钟、写数据库或重建历史。8 周字段只能由真实 attested realization 数量得出。

Go/No-Go 和 console 只读消费同一 evidence；缺失、过期、链损坏或 admission BLOCK 时显示 `BLOCKED/STALE/UNAVAILABLE`，不能回退成可操作状态。

## 8. 错误语义

- `DATA_BLOCKED`：没有可验证 PASS admission；不写决策、不兑现。
- `LEGACY_UNATTESTED`：历史记录存在，但缺 v2 binding；只读保留。
- `DECISION_IDENTITY_CONFLICT`：同一身份内容不同；事务回滚。
- `REALIZATION_IDENTITY_CONFLICT`：同一决策的兑现内容不同；事务回滚。
- `CHAIN_INVALID`：任何 binding/realization 哈希链损坏；官方路径失败关闭。
- `PROJECTION_STALE`：权威兑现已提交但 summary 重建失败；证据仍保留，控制台明确降级。

异常信息只包含 alpha、时间和错误类别，不写原始凭据、路径或 provider 响应。

## 9. 迁移与兼容

1. 迁移只创建新表、索引和触发器，不删除、不回写历史行。
2. 当前 `create=True` 路径执行幂等 schema v2 安装；`create=False` 的官方 attested 操作要求 v2 完整，否则失败关闭。
3. 旧查询接口继续可读；旧 `paper_track` 和 `paper_periods` 保留。
4. 迁移后先运行只读分类，确认 legacy/attested 数量；不自动生成 binding。
5. 如新代码回滚，新增表仍保留，旧读取不受影响；已经生成的 v2 evidence 不得删除。

## 10. 测试策略（TDD）

### 存储与事务

1. 决策完全相同重放幂等。
2. 同身份不同权重、信号、source、recorded_at 或 admission 失败关闭。
3. 没有 authority 记录、BLOCK、链损坏、scope 不包含 instrument 均拒绝。
4. legacy 决策保持可读但不进入 attested pending。
5. realization 完全相同重放幂等；任一输入或结果不同均冲突。
6. 在 realization、paper period、状态更新和事件插入的每个故障点注入异常，验证零部分提交。
7. UPDATE/DELETE binding、realization、paper period 被 SQLite trigger 拒绝。
8. 篡改行或链头后读取和 `--check` 失败。
9. 从现有 v1 数据库增量迁移，所有旧行逐字保留。

### 领域与驱动器

1. min-gap、coverage、funding、IC 数学保持原有行为。
2. admission BLOCK 时既不记录也不兑现。
3. 决策时 PASS、兑现时 BLOCK 时保持 attested pending。
4. 两个不同 PASS admission 正确分别绑定决策与兑现。
5. 状态只统计 attested realization，legacy 单列。
6. 官方驱动器不再传布尔质量开关，也不产生 `FORWARD_LIVE` 新 source。
7. services 层无网络、无环境凭据读取、无隐式墙钟。

### 全量验证

- focused：forward store/domain/driver/data-admission 测试；
- governance scanner；
- `python scripts/validate.py`；
- 当前 admission 与 forward evidence 的只读 `--check`；
- 最终稳定后统一刷新模型风险、容器复现和供应链 evidence，不能在代码仍变化时反复覆盖。

## 11. 实施顺序

1. 先写失败测试：身份冲突、authority 绑定、legacy 隔离、原子故障注入。
2. 实现 schema v2、`ForwardAdmissionRef` 和原子 `commit_realization`。
3. 更新 `realize_due` 与官方驱动器，删除布尔放行路径。
4. 增加 evidence/status 生成器和只读 console/Go-No-Go 消费。
5. 运行当前数据准入：若仍为 BLOCK，保持 0 新决策并报告原因；只在真实 PASS 后启动新时钟。
6. 完成全量验证后再更新供应链和模型风险历史，避免再次产生摘要漂移。

## 12. 与当前在途修改的协作边界

当前工作树已有用户未提交修改。本子项目实施时必须：

- 保留 `PaperTrackStore.append_periods` 的冲突检测；
- 保留 `mark_realized` 的 pending 单行检查，并将其纳入原子方法而非回退；
- 不覆盖 console snapshot、portfolio constraints、model-risk、external-evidence 等无关改动；
- 每次补丁前检查精确 diff，只暂存本子项目文件；
- 如同一文件出现无法安全合并的并发改动，停止该文件实施并先报告冲突。

## 13. 验收标准

本子项目完成的证据不是“出现正收益”，而是：

1. 当前全量门通过，或仅剩有明确外部原因且与本改动无关的失败；
2. v2 schema、哈希链和原子故障注入测试全部通过；
3. 官方驱动器无法用布尔值或 legacy 决策推进认证时钟；
4. 当前 BLOCK admission 不产生新 attested 记录；
5. 第一个未来 PASS admission 后的新决策具有完整绑定，且只有自然到达的后续周期才能兑现；
6. Go/No-Go 清楚显示真实 attested 周数，永不把历史回填或 legacy period 计入 8 周门槛；
7. 五个安全开关保持关闭，治理扫描零违规。
