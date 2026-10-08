# 完整原报告的独立评审请求运输

父提交：`845d8bbdb68efba65d6d16af21c3c780ac7a84f8`。
承接[完整项目原件](2026-10-05-project-chunked-originals.md)，
继续[既定单市场 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)。
本记录不将完整评审事务或 F1 标为完成。

## 完整原件交接

原 `registered_review_request` 队列仍只接原 invocation SHA。
明确的 Gold 分块前向计划可沿同一生产者、完整项目读者、原 cohort、
统计证据和非授权投影准备请求；源码身份、事前选择、成本风险和锁定 OOS 门保持。
项目/源码在同一读集合内核验，完整报告视图保留到请求发布结束。

含 V2 项目 cut 的请求使用 `QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V2`。
原六份 JSON 材料、各叶上限及整体 64M 元数据预算保持；
新 `results/<native invocation>/` 单独包含全项目所有 COMPLETE 分块报告的
原 manifest 和全部原 blob 字节。请求身份绑定完整 cut、六材料 SHA 及
全体完整报告文件绑定的摘要，不接受选中候选/账户摘要或运输方给定路径。
失败、未启动和发布中断试验仍保留在完整 cut 中；未完成报告没有完整视图，
不能借其部分块冒充已完成报告。

发布沿原私有目录和排他锁，先完整复制每份原字节，随后重验原项目和源码，
最后发布不可覆盖的 request.json。中断保留已复制原件且没有 ready marker；
重试逐字节核对已有文件，不分配新试验、不重算生产者，也不覆盖冲突。

## 受控读取及下载

`open_request` 在同一物理读集合下核验六份材料和全部原报告文件，
要求完整 run/blob roster、SHA/长度、原 manifest、输入/源码及完整 tagged 结果身份。
原全项目源/索引各 32GiB 和 100M 节点预算、各计划更小的冻结预算保持。
临时只读索引仅为派生工具，不成为结果、现金或试验记录。
完整报告视图可在上下文内重复用于原统计/签名复算，关闭后拒绝访问；
消费后再核文件身份，同字节改写、链接、别名、缺块和额外块均拒绝。

JSON 控制台响应保持原六角色形状，V2 request 明确包含报告材料绑定；
对应 TypeScript 使用版本联合合同。JSON 本身不含原报告块。
新的只读 GET 归档入口按请求 SHA 固定定位，输出六 JSON、全报告原件和最后的
request.json；不提取或写文件，不读取凭据。tar 分块流出，完整结尾前重验源文件，
缺失 404、无效 409、非法编号 422；响应标明 read-only=true、authoritative=false。
界面 V2 下载原完整 tar，沿浏览器下载避免将数 GiB 原件变成 JSON/内存 Blob。
原 V1 JSON 下载保持。

## 验证记录

初始两个实际生产者交接回归失败；先修测试原 SQLite 文件的私有权限，
随后复现旧请求拒绝新前向计划的 REVIEW_PRODUCER_SOURCE。
首批完整原件/统计视图两项通过，源码冻结保持。
完整归档入口缺失的两个红例保留；十四项原件运输、篡改/缺件/链接、
上下文改写、中断重试及完整归档用例通过（260.98 秒）。
新增 HTTP 完整下载、非选中报告损坏拒绝、发布前源改写拒绝和签名统计用例，
联合先为五通过/一失败：旧隔离签名 fixture 仅授权 BACKTEST，
新原前向 subject 要求 FORWARD；只修测试公钥的证据用途，不放松生产授权校验。
最终同版原件运输/签名统计复算/原项目读者/控制台合同/治理联合
51 passed / 1个既有Starlette提示 / 334.77秒，driver335.208554秒exit0；
3084 Python前后未变。原实际评审队列完整复验同版1 passed /
1个既有Starlette提示 / 951.27秒，driver951.707577秒exit0、3084 Python未变，
覆盖实际HTTP/JobQueue导出、原evaluation+audit写入、拒绝、恢复和重复消费。
两组范围分别保留，不相加为canonical全门或完整F1通过。

前端八项通过，TypeScript 初次检查发现新测试 this 缺类型注解，
补注解后检查及构建通过；保留既有大 bundle 提示。
服务图先 drift，经原生成器重生成后 20 域/52 边/零环；卫生、全仓 F821/E9、
修改 Python 的完整 Ruff 和 diff 检查通过。
本机输出保留在 gold-project-closure-20261003-3pr5mrly/project-chunked-review-y1_6rkvq。

## 尚未关闭

原生产 snapshot/read_retained、签名消费和原 evaluation+audit 事务尚未持有
新完整报告材料；缺材料继续拒绝。下一步必须接通写入、冷读、重复消费、
当前 cut 关系与最终时钟核验，不能使纯历史元数据等价于完整结果复算。
没有新增结果/现金账本、安装真实 trust、修改经济资格或执行权限。

完整目标还要求原长期输入/历史与真实 30 天协议、实际独立来源/结果/时间及
成本风险/OOS证据、正式整组停写/恢复、同版 ARM64/canonical 全门、
获准现场安装升级恢复、五秒链及实际 24 小时 Demo—现金—再研究。
盈利策略随后开发；其他市场单列，延期机构设施不记 PASS。
本批未部署、切服务、写保护状态、触碰凭据或下单。
