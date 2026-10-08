# 原登记运行接入实际研究队列与控制台

父源码为 `70866eebbc8f517f4e6e3e76d9ee27287b7760f6`。本批推进
[完整工厂路线 F1](../plans/2026-09-28-ai-factory-closed-loop.md)，
承接[项目统计受签消费](2026-10-04-signed-project-statistics.md)。
完整交付目标继续进行；本批不是经济准入、现场发布或完整工厂验收。

## 原缺口与本批交接

原生登记、评估计划及风险复算已经存在，但实际 runner 白名单没有消费者。
既有黄金三策略任务仍走独立的诊断归档，不能当成原项目的完整试验集合。

新 `registered_research` 和 `registered_research_recover` 进入原
`POST /run`、认证、类型白名单、JobQueue、RunRegistry 和幂等协议。
请求必须且只能含 `native_invocation_id`，为 64 位小写十六进制摘要。
请求不能指定路径、源码模块、回调、trust、评审时钟或交易权限。

原离线输入 producer 可通过 `tools.registered_research_job.prepare_run`
准备请求；参数为原 native arguments、search_id、dataset_version、
registered_at_ms、invocation_id、evaluation_core 和 risk_review_policy。
准备边界固定自动研究 actor 与 producer，核当前源码及真实准备时钟，
调用原 `prepare_technical`，不向旧运行补登记或改写已有计划。
这仍需可信原输入 producer 交接；AI/采集主流程自动准备以及独立评审任务
尚未接入，不能把手工准备能力当作整个研究产品闭环完成。

固定原件位置相对于受控 `QUANTOS_STATE_ROOT`：

- `data/cache/registered_research/project/runs/<native_invocation_id>`：原计划、
  输入、登记、invocation、outcome、原生账本、结果和风险复算。
- `data/cache/registered_research/registry.db`：原 SqliteExperimentStore。
  共用原登记/追加历史语义，不新增结果状态机或现金账本；它只覆盖这个
  受管项目，不能宣称包括其他研究历史或所有市场。

准备时只初始化新原登记库；已有损坏或不完整数据库不自动修复。
私有目录、单链接常规文件、固定解析路径与数据库物理身份检查保留。
原登记库共享 writer 锁串行所有运行目录；该锁不是正式部署停写屏障。

队列先核 plan/input 摘要、当前源码和 V3 预登记评估/风险政策，再通过
既有有限类型静态解码器还原原参数并核完整 transport。随后只调用原
`run_technical`，沿原注册、原生账本、invocation witness 和风险复算执行。
恢复要求已有原 invocation。已留存原结果沿原恢复逻辑发布；缺结果的
在途调用保持 UNKNOWN，不重复执行，不新建试验，不把失败任务改为成功。
初次运行包括原 producer 和预登记风险诊断复算；恢复不新增其中任一种调用。

## 浏览器与证据边界

新建研究表单按 runner 实际健康清单展示两种入口，只有合法冻结运行编号
才能提交。只读 technical-replay 增加对应 TypeScript/HTTP 合同；原控制台
展示独立的原登记报告，不复用失效 V1 的收益字段。

结果展示原账本十二项精确成本/权益/对账值、冻结计划与原试验身份。
OOS、统计评审、原风险复算、独立评审和候选准入各自显示；本批统计状态为
NOT_REVIEWED，独立评审等待、候选未准入，OOS 沿原 core_status 的 UNKNOWN
或 INSUFFICIENT，风险沿原 REJECTED 或 INSUFFICIENT，计算完成不提升任何状态。
transport 报告不能自称 promotion/economic/submission 为 true；即使重新计算
外层展示镜像摘要，这类报告仍在只读消费边界拒绝。
快照 envelope 同时保留结果摘要与数据版本，因此存在两个身份时单一
snapshot_id 为空；没有把多身份误压成一个结果身份。

## 验证记录

红例先因实际入口缺失失败。原登记白名单遗漏、原结果状态字段位于 detail、
初始风险复算调用计数和多身份快照断言的失败全部留存；按原合同补齐适配
或修正夹具断言，没有放宽资格、风险或原恢复规则。
首次入口/只读 HTTP 组合 19 项通过 / 11.96 秒。兼容组 230 项通过、1 项失败 /
80.95 秒：健康接口固定清单未包含两个新类型。该清单只增加这两个研究类型，
其余类型、隔离集合和权限政策保持原约束。最终受影响组 33 项通过 / 12.64 秒，
3037 个原仓库 Python 文件运行前后相同；原 driver 终态退出 0 / 13.00 秒。
不将前一版兼容计数合成最终版整套通过。
服务图首次摘要漂移保留，按原生成器重生成仍为 20 域、52 边、无循环。

前端新入口与既有 Studio 两文件共 9 项通过，typecheck 和生产 build 通过；
依赖按既有 lock 离线安装，未升级。新增 Python 全 Ruff 和 canonical F821/E9
通过。原生行情、源身份、成本/风险材料以及 HTTP token 均为隔离夹具；
原 producer、风险复算、登记事务、实际队列和实际 HTTP 路由被执行。
日志/XML、源码前后摘要、提交与整合记录位于独立任务输出目录
`gold-project-closure-20261003-3pr5mrly/registered-research-entry-28klwn6e`。

独立受签评审的产品任务、受控锁定 OOS、真实成本风险证据及候选准入仍缺。
正式完整 writer 停写、完整业务/现金对账、最终同版 ARM64/canonical 全门、
获准现场安装/升级/恢复、五秒链和实际 24 小时成交—现金—再研究继续验收。
本批未操作宿主服务、受保护运行状态、真实 trust/凭据或订单；旧 V1 继续隔离。
其他市场逐项验收，异机机构设施延期不记 PASS，盈利策略研发单列。
