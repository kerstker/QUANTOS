# 并发候选登记的读取一致性修复

2026-09-13。承接[当前运行记录](2026-09-13-demo-operating-runtime.md)。

8535c1ac8 的完整检查出现一个失败；按已收集的测试顺序，对应六线程、每线程 200 个
候选的共享账本回归。完整检查尚未结束，最终失败栈仍待确认。单独重跑原测试通过，
不能据此抹去并发失败。

进一步使用真实 SQLite WAL 数据库和两个连接建立确定性复现：读取搜索事件后，让另一
连接提交有效的新搜索及候选，然后继续读取候选。原预检的多次读取没有保持同一快照，
将旧事件集合与新候选混用，误报 `search candidate 1 has invalid provenance`。
该回归在原实现上失败；没有修改实际研究数据库。

## 修复范围

`SqliteExperimentStore._ensure_search_schema` 的模式与来源预检现在保持一个尚未读完的
SQLite SELECT 游标，直至该组只读检查结束。SQLite 的隐式读事务在语句结束前保持
读取快照；具体机制见 [SQLite 事务文档](https://www.sqlite.org/lang_transaction.html)。
实际 Python 3.12/SQLite 环境中的交错提交回归验证了这一行为。

游标在原 `BEGIN IMMEDIATE` 之前关闭。真正写入仍在原事务内重新核对当前状态，
不沿用旧读取结果作为写入许可；未增加预检事务控制语句、写入租约或数据库等待时限。
并发迁移结束后的复核使用相同读取约束。模式、来源认证和回滚约束保持。

第一次实现使用显式 `BEGIN/ROLLBACK`，触发原事务契约的 17 项失败，已弃用。
原测试没有放宽、删除或改成接受新事务语义。

## 证据与限制

材料目录：`/home/kerstker/quantos-dev-output/demo-checkpoint-20260913`。

| 检查 | 结果 | 文件 |
|---|---|---|
| 原并发规模测试单独复跑 | 通过；不替代完整失败 | `concurrent-batch-reproduction.log` |
| 强制交错提交的新回归，修复前 | 失败，来源误报 | `concurrent-snapshot-red.log` |
| 首次显式事务方案 | 17 失败 / 32 通过，弃用 | `concurrent-snapshot-green.log` |
| 最终游标快照方案、原事务原语及 v3 回归 | 121 通过 | `concurrent-snapshot-cursor-green.log` |
| 持久化边界、依赖图、事件目录与 v2 来源激活 | 37 通过 | `concurrent-snapshot-boundaries.log` |

这些是增量验证。853 的完整运行不包含本修复，后续需针对最终源码重新完成完整检查。
当前研究入口仍为已验收的 6455980bc/aa2，原受保护执行侧未切换；没有授予研究或交易权威。
