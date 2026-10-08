# Demo维护准备：恢复展示写入纳入周期锁

承接[现场容量方案](2026-09-24-demo-maintenance-capacity-proposal.md)。核对实际服务
触发关系和写入代码后发现，RecoveryLoop.tick持有cycle.lock，但两个调用者在tick
返回、释放锁之后才保存resident-recovery/views。这意味着维护者取得该锁时，
旧循环仍可能写入目录，不能用持锁直接推断展示目录已停止变化。

## 实现

RecoveryLoop.tick增加可选publish回调，在同一周期锁内先生成报告、再发布展示，
完成后释放锁。锁获取失败时返回RESIDENT_BUSY且不调用publish；只将锁获取冲突
转换为BUSY，内部或发布期间的BlockingIOError不伪装成锁忙，错误传播并释放锁。

tools/demo_resident_recovery的views/outcomes保存和tools/demo_functional_worker的
settle展示写入均移入回调。原15秒/状态变化发布条件保留。单次resident运行遇到
RESIDENT_BUSY返回2，不能因没有执行工作而报告退出成功。持续运行保留原重试节奏。
没有新增查询、订单、撤单、签发或停止运行服务。

## 验证

输出根：`/home/kerstker/quantos-dev-output/demo-maintenance-lock-20260924`。

- before.log：三个新锁边界回归先失败。
- after.log：修复后恢复循环、resident入口和functional worker的25项通过。
- busy-before.log：单次锁忙退出码回归先失败。
- final.log：修复退出码后，连同日检查点和reported recovery共34项通过。
- Ruff专项通过；治理扫描695文件、0违规；diff格式检查通过。

回归在发布回调中竞争同一文件锁，要求不能获取；锁忙时不执行周期和发布；
发布失败传播后能够重新取得锁。CLI替身按回调合同执行，没有放宽账户所有权边界。

## 运维限制

这只是已核对的resident展示写入边界，不是全命名空间静止证明。另需考虑origin、
observation、stream数据库、签发/控制入口及其他人工调用。只读进程快照观察到
执行UID1001的stream服务，不能证明未来没有短时调用或其他UID写入。

生产仍运行原冻结源码；本改动未部署，不能据此认定当前服务已具备新锁语义。
生产维护入口、真实备份/复核、服务状态恢复及长期留存接线仍未完成。专项验证
不替代本批的完整发布验收，也不授权移动生产记录。
