# Gold 长期采集：显式留存迟到开放 Bar

父提交：`319c9bccb325f0f41f4bbd2254be380874b3405a`。
范围沿用[固定 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)。
这是长期原始采集的失败修复，不新增经济准入或交易能力。

## 原失败与新行为

原 2ac55474 月窗口在约7.2小时后失败：40548条消息、29个收盘及
21713443字节已留存。末条未收盘 Bar 实际延迟30756ms，超过原30000ms，
原 `GoldBarStream` 正确拒绝并进入 blocked 状态。旧窗口仍为 INCOMPLETE，
不得补写、自动升级格式或称为真实30天研究输入。

显式选择 `RETAIN_LATE_OPEN_DIAGNOSTICS_V1` 并提供原 collection budget
才产生 `GOLD_CONTINUOUS_BAR_SESSION_V3`。原始接收游标仅将满足原 stream、
事件类型、标的、周期、整数时钟及明确 `x=False` 的迟到消息标记为
`REJECTED_LATE_OPEN_BAR`，保留原 wire、真实接收时间、延迟及原拒绝原因。
这条消息消耗原消息/字节预算并推进原始接收水位，不进入决策游标、不改
已接受 Bar 或其可用时间、不解除 blocked。随后合格消息仍交给原决策读者。

未来时钟、错 stream/标的/周期、非布尔 finality、迟到收盘、重复 JSON
字段、接收时间回退及资源超限仍使采集失败，原件保留。30000ms边界仍
按原读者处理。旧 V1/V2 默认行为保持，旧计划携带新 policy 明确拒绝。

## 后续读者与身份

配对入口显式新 PLAN/SUMMARY/REVIEW V2，转交同一 policy 给 Bar worker，
并在冷读时绑定父计划和真实子计划/复算结果；重哈希后的父级降版仍拒绝。
Bar CLI 和 paired CLI 均提供显式 `--late-open-policy`，预算不扩大。

long prefix 与 delta journal 使用同一 Bar 计划校验及游标工厂。每条原始
消息包括被拒消息都进入原字节链和预算，只有原 `CLOSED_BAR` 进入决定
列表。原 delta schema V2继续描述分段链；所嵌原 Bar PLAN V3字节、摘要
和 policy 是身份的一部分，续段更换 policy 被原 ancestry 门拒绝。
native source、登记队列、恢复及项目冷读沿原路径消费，不另建现金公式。
legacy session prefix/replay 继续限定原 V1，不把 V3强行喂给旧格式读者。

## 验证

- 最初原拒绝/显式新入口回归1 FAIL / 1 PASS；Bar与预算专项57 PASS。
- 配对/native source/登记队列真实消费者先3 FAIL / 3 PASS。后继联合
  130 PASS / 1 FAIL，124.92秒（driver125.235957秒）；唯一失败为测试
  错把共同水位后的消息也计入已冻结前缀。修正预期为原 as_of 门，不改
  生产水位或输入覆盖规则。全部失败日志保留。
- 最终12组270 PASS / 1 SKIP，188.72秒（driver189.360392秒），3120
  Python前后保持。覆盖原数据流、资源预算、配对冷读、完整 native byte
  chain、注册/恢复/项目消费者、policy ancestry，以及治理与控制台合同。
  未改控制台载荷或前端合同，保留原跳过和依赖警告。
- 离线读取实际旧窗口全部40548条/11个chunk，摘要、决定、29个收盘和
  字节数均匹配；最后真实拒绝消息在新显式模式被留存，原决策 Bar和
  时钟保持。1.805929秒，原件摘要/inode/mtime保持；原失败窗口不重启。
- 新 schema的错误计数、布尔计数、降版、伪造 OPEN/CLOSED 及延迟在
  重算所有外部摘要后仍拒绝；原拒绝消息消耗预算，超限不称为完整采集。

证据目录：`gold-project-closure-20261003-3pr5mrly/`
`gold-late-open-collection-20261005`。冻结、质量、hook、DGX基线与主区
选择整合另留终态。该版本自身完整发布检查和新的长期真实采集尚未完成，
不能借319版的自身ARM64或其他专项结论证明本版整体可发布。

四组收尾条件仍为：同版完整发布门；真实长期因果输入/独立核验/实际
成本风险与锁定OOS；全部writer停写后的同cut现金恢复；获准现场
Demo→现金反馈→再研究及四类端到端验收。满足即结束本轮系统建设，
盈利策略、新市场和非关键优化单列后续。所有新结果仍无经济、来源或
交易权威；未部署、切常驻服务、写保护状态、装实际trust、读凭据或发单。
