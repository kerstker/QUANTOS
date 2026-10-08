# 分区准备的原生停机历史校验

接续[跨日安装恢复](2026-10-01-demo-market-partition-crossday.md)，父源码
`ec1b86da46bbef8aa06cf026675ce925b9a8c6af`。原 worker 对保护目录下的
quiescent stop image 做字节摘要与前后比较，但没有读取其中的状态和追加历史。
一致的摘要不能证明 SQLite 内容有效，也不能证明停机未触发或没有未来记录。

## 实现

从原操作员 assembler 提取 `verified_stop_snapshot`，继续在私有临时副本中用
原 `KillSwitchStateStore(read_only=True).verified_snapshot()` 核验非空历史、
状态、transition/event/dispatch 链及原摘要；不创建历史、不 bootstrap、
不执行 arm/reset、不写原数据库。检查原未触发与时间边界，并限制输入类型和
原64MiB预算。assembler 继续消费同一 snapshot 的原 kill-switch 状态。

分区 worker 在原 preparation lease 内、候选生成和旧候选复核前后两次调用。
固定源路径继续由原角色属主/权限检查、descriptor reader 和 quiescent sidecar
检查约束；读取前后均检查路径，最后比较原字节与原生 snapshot。历史、状态或
时间校验失败返回 `PARTITION_STOP_SOURCE_INVALID`，不生成成功材料。
原 root/source/cycle 管道与所有执行准入保持，报告合同和 authority 标记未改。

## 验证与限制

新增初装/旧切分点复核的14项反例，覆盖损坏/截断 SQLite、空历史、record/state
摘要篡改、原生有效的未来状态和 SYSTEM 触发停机。仅在隔离测试数据库构造或
篡改这些情况；检查失败后原 ledger/event/image 字节均不变。

首次夹具有两项 bootstrap 时间不一致、SQLite 写连接未显式关闭；首次函数
抽取漏接 assembler snapshot 返回值。相应 before/after/fixed 日志和静态失败
均保留，已修正夹具和返回值。正确夹具又在原 ec1 源码独立复现拒绝缺口，
本版验证覆盖原操作员、安装材料、worker 及实际本地子进程管道。
正确夹具在原 ec1 源码 **14项失败，19.47秒**；本版五组最终
**83项通过，46.40秒**，包括这些反例与原正常路径。先前日志未覆盖。

原生链校验只是技术完整性检查，不能对抗可信代码重写整份合法历史，也不能
授予订单权限或独立经济资格。属主/角色/已接受源码仍为隔离替身；没有实际
保护路径、真实账户、凭据或订单操作。`source_verified`、
`kill_switch_verified` 与经济 authority 的原 false 标记保持。

最终源码自身仍需完整 canonical 与发布验收；前版结果不能覆盖本增量。
首次黄金独立登记/shared-budget批准、未知账户事件、部分旧格式/跨版本迁移、
现场安装/升级/回滚、真实24小时资金反馈闭环与多资产验收仍待完成。
开发回退仅使用该增量的原字节备份，先逐文件确认没有后续修改；未部署无需
回退服务，不回退或删除实验、停机、订单及资金历史。
