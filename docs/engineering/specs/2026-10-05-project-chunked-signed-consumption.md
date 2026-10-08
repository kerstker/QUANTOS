# 完整项目报告进入原签名评审事务与冷读

承接[完整评审请求运输](2026-10-05-project-chunked-review-request.md)，
父提交 `2247e743ea589f9865fbbc7db94b25e334fe62e5`。
独立开发分支 `codex/project-signed-consumption-20261005`。
继续[既定单市场 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)。
本增量不将 F1、经济准入或完整系统标为完成。

## 实现

原固定 snapshot 读者新增受控上下文：同时持有当前完整项目、原报告、
源码及独立 public trust 的读取身份，直到签名复算和记录构造结束。
运行时报告视图不序列化进 snapshot，不接受队列选择路径、预算、trust 或回调。
两个读取观察必须保持同一完整身份；关闭上下文仍重验全部物理原件。

纯 artifact 验证、原 evaluation/audit 写入、重试与历史冷读现在必须消费
历史 cut 中所有已完成的新报告，包括非选中候选所属运行。
当前材料视图可以包含后续运行，但历史验证只使用该签名 cut 的完整报告集合；
历史完整性不代表当前准入通过。缺原件或缺固定 provider 继续拒绝。

新固定报告读者只读 native project 的完整 manifest/blobs 与完整结果身份，
不打开 registry，避免原数据库验证递归。原登记 bootstrap 与项目读者的
私有数据库副本均固定配置此 provider；原 SQL lease 继续拥有唯一的
evaluation 与 audit 事务，不新增 search activity、trial 或现金账本。

新 V2 cut 的生产签名消费入口使用该上下文；原 V1 元数据接口保持。
原 schema、artifact/run identity、源与结果签名、全局试验、统计、成本风险、
锁定 OOS 和晋级/下单边界保持。最终交付时钟在完整材料与源码上下文关闭后
取样，发生过期或非选中原件改写时，已暂存的 evaluation 和 audit 一起回滚。

## 验证记录

首批缺 reports 参数、缺固定报告读者、缺 snapshot 上下文、缺 registry
上下文参数的失败日志均保留。纯 artifact 组23通过，原 store/provider 组27通过，
snapshot/原读者组7通过。原 registry 上下文/旧 artifact/store 联合47通过，
172.03秒，driver172.324227秒exit0、3088 Python前后相同；分别是各自冻结版本，
不累加为全门。

实际生产入口红例1失败/34.85秒，driver35.107629秒exit1、3089 Python未变：
旧入口只传元数据，在完整报告依赖处明确 MATERIAL_REQUIRED 拒绝。
修复只选择固定读者上下文，没有改签名、风险、统计或准入结论。
最终同版完整材料消费、snapshot/原报告读者、原 registry/store、统计冷恢复、
当前准入复验、纯 artifact/签名与家族 schema 边界联合84通过/1904.40秒，
driver1904.716321秒exit0、3089 Python前后相同。包含实际生产签名消费、
恢复、非选中报告损坏拒绝，以及已经暂存 evaluation/audit 后发生过期或
同字节改写的回滚；隔离签名有效仍为 INSUFFICIENT，经济/晋级/下单 false。
旧统计测试的四个独立执行源码子进程均成功终结，没有因等待而重启或跳过。

导入图与卫生另11通过/1.32秒，driver1.502223秒exit0、3089 Python未变。
服务图经原生成器刷新后20域52边零环；新读者/读者接线及四新测试完整 Ruff
通过，修改范围原6条诊断降至5条、无新增；全仓 F821/E9、卫生和 diff 通过。
这些定向结果不替代当前源码的 canonical 全门。
各版红绿日志/XML、冻结、实际进程身份与整合材料位于本机
gold-project-closure-20261003-3pr5mrly/project-signed-consumption-f0siz6ci。
独立提交、精确源码复核及主区整合状态继续单独记录，未通过即不记完成。

## 完整交付剩余条件

本增量的签名来自隔离测试夹具；实际独立来源/结果/时间及成本风险证据、
锁定 OOS 和候选准入尚未完成。长期输入/原生产者有界历史、真实30天协议、
完整全局试验统计、正式停止全部 writer 后同一 cut 的恢复、最终同版
ARM64/canonical 全门、获准现场安装升级恢复、五秒链和实际24小时
Demo—现金—再研究仍按既定 F1–F5 验收。
盈利策略随后研发，其他市场单列；延期机构设施不计 PASS。
本增量未部署、切服务、写保护运行状态、安装实际 trust、触碰凭据或下单。
