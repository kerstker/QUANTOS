# 原项目独立评审的任务、材料导出与结果交接

承接[原登记研究产品入口](2026-10-04-registered-research-product-entry.md)和
[项目统计受签消费](2026-10-04-signed-project-statistics.md)，推进
[完整工厂路线 F1](../plans/2026-09-28-ai-factory-closed-loop.md)。
本批为开发集成，专项已通过；不计为完整工厂验收、发布或经济准入。

## 产品交接

三个类型进入原认证 `POST /run`、白名单、JobQueue 和 RunRegistry：

- `registered_review_request`：唯一参数 `native_invocation_id`。
- `registered_review`：唯一参数 `review_request_id`。
- `registered_review_recover`：唯一参数 `review_request_id`。

编号必须为 64 位小写十六进制摘要。请求不能携带路径、trust、公钥、
时钟或权限。原运行结果链接到材料准备，材料结果链接到回复消费，
已记录结果链接到恢复；表单从这些链接带入同一编号。

准备读取受管项目的完整原 cut，包括失败、未完成和未启动尝试。
统计选择和固定基准使用运行前保留的政策及原试验身份；不接受用户曲线。
原输入、账本、登记和统计材料保持原身份，不重跑试验或补写旧计划。
完整资料包含 subject、project_cut、source_manifest、statistical_evidence、
projection，以及全部 services/quantos/tools Python 源码和 ARM64 依赖锁的
逐文件 base64 原字节。源码清单、整体源码摘要和锁摘要均核对。

材料固定写在 `data/cache/registered_research/review_requests/<id>/`。
各文件不可变，request.json 最后发布。只读
`GET /api/v1/console/registered-review-request?review_request_id=<id>`
仅从这个固定目录导出；缺失或损坏明确报告 MISSING/INVALID，不初始化状态。
目录、文件数、逐文件及整个 JSON 有界，路径不由请求选择。
导出明确为未签评审请求，不含签名、信任安装或批准。

## 原回复消费与信任边界

独立回复须由受控交接放入
`data/cache/registered_research/signed_reviews/<id>/`，只含原五个文件：
subject.json、source_manifest.json、statistical_evidence.json、
source_envelope.json、result_envelope.json。前三件必须与原请求字节一致。
本批不实现签名器或安装器，不从队列请求接收 trust。

执行进程的受控配置提供 `QUANTOS_PROJECT_REVIEW_TRUST_FILE`、
`QUANTOS_PROJECT_REVIEW_TRUST_SHA256`、`QUANTOS_PROJECT_REVIEW_MIN_GENERATION`。
公件采用原读者的所有权、不可写、无链接及路径约束；每次消费仍从原读集合
复核固定源码、独立公钥 pin/代际、原项目、原风险复算和统计结果。
缺配置即拒绝。现场独立 trust 的安装与运营方交接尚未完成。

沿原 ExperimentRegistry 的 evaluations 和唯一 audit outbox 事务记录。
恢复沿同一事务核对当前 cut 与旧记录，不新建收据数据库或现金账本。
原项目新增尝试时不能复用旧 cut；有效签名也不能覆盖原风险/统计拒绝。

新库先通过原迁移器建立 provenance 能力，再以人口摘要 CAS 升级为评审 schema 5。
既有库不在任务中自动迁移；旧版本消费明确要求维护升级。统计政策必须在
原计划发布前保留，既有缺政策的运行不能事后补齐。

## 展示与完整目标

控制台分别展示未签/已核验身份、统计、风险、OOS、评审资格、完整尝试数，
并提供完整材料下载。镜像同时绑定请求参数、原结果摘要和阶段；重算镜像
外层摘要不能让自称批准或错编号的报告进入展示。

统计 PASS 或签名有效不授予候选、经济或下单资格；本批三个权限仍 false。
独立锁定 OOS、AI/采集主流程自动准备、真实成本风险和最终候选准入仍须闭合。
完整 writer 同 cut 停写、跨角色业务/现金对账、最终同版 ARM64/canonical 全门、
获准现场安装/升级/恢复、五秒链及实际 24 小时成交—现金—再研究仍需验收。
其他市场单独验收，机构异机设施延期不记 PASS，盈利策略研发单列。

## 验证记录

非法参数十五项先通过。控制台三种报告缺失和导出路由缺失先红；原库直接
升级至 schema 5 后缺 provenance 能力的两个实际反例保留，先建立原能力再升级。
源码 group-write 拒绝保留，仅收紧自有开发目录权限，未放宽读者或改变主区权限。
测试误用 store context manager、前端误用 ErrorNote 的失败也分别保留。

真实原生产者生成三次原生研究，实际认证 HTTP→队列→完整材料→合成独立
回复→原 evaluations/audit 事务→恢复，共 22 项通过 / 967.063 秒；driver
967.525 秒 exit 0，3041 个 Python 文件前后相同。缺 trust 在入库前拒绝且
原数据库不变；有效签名和统计 PASS 仍因原风险为 REJECTED，权限全部 false。
完整下载逐字节对应原材料；损坏材料被实际只读 HTTP 拒绝。恢复沿同一原
评审编号，队列重复请求不重复提交。公件采用测试专用 0444 文件。

最终仅补总控制台合同清单，生产 Python 和上述实际案例函数/夹具保持不变。
最终控制台及原登记、参数/新旧库联合 140 通过、1 跳过 / 21.766 秒；跳过为
既有 IC panel 快照缺失。driver 22.281 秒 exit 0，3041 Python 前后相同。
另一次边界兼容 80 项通过 / 13.92 秒，不把重叠或跨冻结计数合成完整门通过。

前端三个文件共 16 项通过 / 1.31 秒，包括表单、身份带入、拒绝展示和完整
下载/缺失拒绝；typecheck/build 通过。新增 Python 全 Ruff、全仓 canonical
F821/E9 通过；修改文件 75 条既有诊断与父版相同，无新增诊断。服务图漂移
先失败，原生成器更新后 20 域52边、无循环且 current；卫生通过。
日志/XML、源码摘要、提交与选择性整合记录留存在独立输出
`gold-project-closure-20261003-3pr5mrly/registered-review-entry-z3iw5r35`。

行情、成本风险、HTTP token、签名与 trust 均为隔离测试材料。未部署或切换
服务、安装真实 trust、触碰真实凭据/受保护状态或下单。最终完整门仍是旧
5ae 版 30 PASS、1 FAIL、1 TIMEOUT，本批不改写其结果。
