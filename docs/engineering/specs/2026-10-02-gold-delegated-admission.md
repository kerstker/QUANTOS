# 黄金有限委托的独立准入与原执行链集成

状态：本批实现与隔离联合验证完成，完整验收与现场交付未完成。父源码
`4673bc0a785a67b13d35402dd801e7984c69f363`；独立开发目录
`/home/kerstker/quantos-dev-output/gold-delegated-admission-20261002-654561bq/source`。
承接[原窗口留存和禁用登记](2026-10-02-gold-pilot-observation-retention.md)、
[有限委托设计](2026-10-02-gold-bounded-execution-delegation-design.md)与
[十环节交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。

## 本批处理的问题

原登记保持 `consumer_enabled=false`，精确 policy 只能批准已经固定的一个决策。
本批把独立启用、逐信号证明及原 controller/task/request/session/issuer/executor
串联起来。只接受已经登记的同一 pilot、同一 EXIT_BUDGET、同一共享预算组；
不借用旧 Scope 的确认，不改已安装 policy，不清空此前未签发的预留。
新 mandate、key、组或退出预留的迁移继续需要独立连续性工作，不能自动升级。

## 启用和权限

新用途为 `ENABLE_BOUNDED_FUTURE_SIGNALS`，沿用实际本机 sudo/TTY 操作员确认完整
候选摘要的入口。安装器先复核原策略、pilot、账户、登记、派发器和 issuer 历史，
在原维护租约和公共目录排他锁内不可覆盖写入、fsync/readback，最后发布完成记录。
部分失败保留原件并拒绝消费；独立的 REGISTER_DISABLED 不能被解释为 ENABLE。

公开文件固定在 `/etc/quantos/demo-gold-delegation`：目录0755、文件0444、root所有。
三个角色通过固定路径、实际UID、文件类型/权限/链接/目录身份和租约读取；
原 issuer/executor 私有目录的 `InaccessiblePaths` 不变。
纯 `validate` 只核声明内容，调用者提供的 JSON 不建立文件来源权限。

## 证明和原协议

`DEMO_GOLD_DELEGATED_DECISION_PROOF_V1` 包含原 executor 留存的 result、decision、
origin、claim、原窗口容量、pilot、执行历史和冻结行情前缀。证明限制12MB，沿原
16MB私有帧传输；不接受任意路径、代码或“已核验”布尔值代替实际复算。

独立准入重放原 bar/知识时间、策略定义和精确入退场草稿，派生原 policy 的精确
constraints。只有 policy_id 与 constraints 摘要变化，共享组绑定保持。
task 仍绑定已安装基础 policy，全 profile 与独立启用时的摘要相同；提案签名上下文
同时绑定 constraints 和完整证明摘要。issuer、executor 核对证明与提案的账户原件、
四份原历史、持仓 context 及 pilot 控制一致，不能拼接另一份合法快照。

原 issuer 事务内验证登记时与启用时两份历史锚，再沿原 reserve→复核→签名路径。
崩溃、撤销、过期、丢失回包均不返还预算；独立签名不能由 controller 派生 policy
代替。原恢复器能识别新提案，并在委托过期/撤销后继续查询原编号，不续权或重发。

## 本次联调暴露的缺陷

- 原 OMS 打开会执行迁移。两个原 store 增加显式 `read_only=True`，使用 SQLite
  `mode=ro` 和 `query_only`，拒绝新建、缺文件、内存及符号链接；旧 OMS schema
  明确拒绝、不会在读取中升级。原写入模式保留。
- 独立重放的开头时间不能代表结束时间。executor 在全部预检完成后重新读取时间，
  按原信号、委托、grant、key相关界限及 mandate 的最早期限拒绝；最后一次取凭据后
  的复核同样不能跨期发送。原预算和未知状态保留。
- 大证明生成的 task 超过64KiB。已有 delegated task 的复读改用原固定目录、摘要
  和16MB限制的 reader；普通配置的64KiB上限不扩大。claim 后仍只有 QUERY_ONLY。
- 原全量验收的9个 setup error 是夹具共享 Git 对象链过深。该夹具改为
  `git clone --no-local`，新目标有独立对象且 HEAD/tree 相同，不改变原对象链。
- request preparer 重新采账户会违反同一证明的原件绑定。delegated 分支现持原准备
  租约，从 executor 同 claim 复读账户、决策与结果，核对三个只读账本、pilot 和
  配置前后不变；公共市场风险输入仍走原四次 GET。最终结果落盘后、管道输出前
  再检查原截止时间及时间不倒退，重放耗时不能续期。
- 早期隔离夹具只构造四字段窗口，与真实 worker 的八字段原窗口不同。已以真实
  窗口生成器复现并修复；携带恢复观察时仍要求禁止新增订单。
- STOP 退出部分成交后的当前持仓不能替代最初触发上下文。原生回归覆盖 IOC
  成交 0.05/0.1、EXPIRED、三库只读重启及历史上下文独立重建，再用同一原预算组
  的剩余退出预留签发 0.05；替换历史上下文明确拒绝。
- 委托派发沿 executor 原生导出与完整证明复算，省去 controller 原先相邻的两次
  完整归档冷审；精确旧批准仍保留原检查。三种 bar runtime 均通过原生导出到原
  task 留存，配置、ENABLE、前缀、结果与期限变化均拒绝。留存结束再次读取时钟，
  到期返回 EXPIRED_QUERY_ONLY，回拨拒绝；已留存任务不删除、不续期，也不调用执行。

## 当前验证和边界

开发日志、原失败、各子包精确前像与导入摘要保留在上述输出目录及对应独立子目录。
目前已有原生策略证明、实际 SQLite、原风控/mandate、Ed25519、同组预算、会话和
最终发送检查的隔离联调；身份、操作员、密钥及场所响应是明确测试替身。
固定测试时钟不证明实际五秒链路；局部通过不替代同版 canonical ALL。
逐信号证明子包六组 78 项通过，准备器新旧联合 45 项通过；根导入最终证明后，
执行管道、任务复读和只读账本三组 19 项通过。这些范围存在重叠，不累计为总数。
新增完整私有准备管道到原 issuer/最终模拟发送的单例通过；派发末端时效修正后
新旧两组 25 项通过。隔离小 ENTRY 原生准备段三次约 201–203ms；单独的任务到
模拟发送测试体约 430ms。原生派发约 1.28–1.50s，其中导出约 1.14–1.34s。
这些测量来自不同用例，不能相加为全链实测；不含真实公网 GET、systemd/跨 UID、
最大历史或 EXIT 双前缀等开销。精确计时与测试源码摘要保留在输出目录的
latency-evidence-binding.json 和派发子包交接清单。

父4673全量验收已结束为 **ALL/FAIL**：32阶段31通过、1失败；执行侧7049通过、
2跳过、9个上述克隆初始化错误。原失败日志及报告保留，不能继续称在途或借父版本PASS。
最终相同 Python 源码的 38 组联合为 **508 项通过／913.64 秒**，没有失败或跳过；
3 条 JUnit 属性格式警告保留。37 个变更 Python 文件的 E9/F821/F811、仓库卫生、
20 域 52 边无环依赖图及 diff 检查通过。测试前后逐文件摘要一致，完整命令、源码
绑定、JUnit 和日志留存于输出目录。隔离 Git 对象已独立复制并通过连通性检查，
HEAD/tree/工作区状态保持，原 alternates 留存且未 prune。
本批自己的冻结提交、完整验收与交付结果须以其对应外部记录核对，不继承旧报告。

本批不产生实际 ENABLE、订单、账户调用、保护运行状态写入或服务切换。
原 short 开仓退出容量门保持；有限退出合同不能据此声称所有方向/所有部分成交/24小时
场景均已完成，须以各自实际回归与现场证据核对。完整系统仍缺实际批准后的持续运行、
成交和现金反馈再研究、独立经济来源、现场恢复/安装升级回滚及多资产专项验收；
盈利策略研发继续单列，机构异机设施仍延期。

## 下一交付接口

现有 pilot 安装器仅接受旧 BUDGET／Scope，Scope 到精确 policy 的原安装仍要求
当前短时决策；本批 ENABLE 则要求已安装 EXIT_BUDGET 和原 issuer 组历史。
因此还缺首次未来信号预批准、真实空组初始化及原消耗继承的人工安装接线。
不能伪造首次 RESERVED、填占位 constraints 摘要或复用旧 Scope 确认来跳过该缺口。
下一实现须明确非执行模板与独立新确认，在真实 proof 出现后才产生精确约束。

组绑定包含 source、registry、key、mandate、策略及退出预留；跨版本接续不能改
旧组或清空额度。新增委托历史也不能直接交给不认识该格式的旧二进制回退；查询
恢复必须继续使用兼容源码并保留原订单、预留与账本。安装／启用升级和恢复协议
仍需分别实现和验收，不能把通用 release 回滚当作执行状态回滚通过。
