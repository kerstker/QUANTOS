# 私有备份自动隔离恢复验证

维护归档需要验证备份确实可以恢复。此前capture_only生成私有ZIP后直接发布，
完整恢复验证位于后续加密流程；只执行capture并不能提供恢复验证结果。

本增量在tools/demo_namespace_checkpoint.py新增restore_private_capture，复用加密
恢复的清单、成员范围、大小限制、SQLite完整性、账本状态和latest指针校验。
输入必须属于当前用户、文件及直接父目录均无组/其他用户权限、单硬链接且所有
路径组件均非符号链接。打开文件后核对身份、长度及读前后元数据，复制到私有
临时文件并验证指定摘要，随后只解释这个固定副本。

capture_only现自动执行隔离恢复，确认恢复状态等于捕获状态之后才发布备份，
返回restore_verified=true。失败不发布目的文件。没有读取备份口令，也没有
改变加密备份流程；私有捕获仍明确encrypted=false，不能冒充加密或异机备份。

恢复目的地仍必须是全新隔离路径，禁止保护中的执行器、签发器、控制器和生产
状态目录，不激活服务、不发单、不改原始账本。SQLite快照可能重写物理文件头，
所以验证恢复文件与备份清单字节一致，并核对账本语义状态；不要求SQLite快照
物理字节与仍存有WAL状态的源数据库文件相同。

本增量不解决跨命名空间静止、引用扫描完整性、生产归档授权或长期留存；仍需
与维护流程集成，不能仅凭restore_verified启动生产移动。

## 验证证据

输出目录：/home/kerstker/quantos-dev-output/demo-maintenance-services-20260924。
私有恢复五项测试在入口缺失时失败；恢复失败禁止发布的测试在接线前失败。
首次整模块运行暴露测试错误地比较源SQLite物理字节，已改为与备份成员核对；
该失败保留在capture-after.log。checkpoint及cycle联合84项通过（97.19秒），见
capture-final.log。空间预留补充反例先失败后修复：周期捕获现为源副本、ZIP、
冻结ZIP及恢复树共四份最大体积预留空间，避免沿用旧三份预算。修改后cycle模块
再次通过，见capture-cycle-final.log。Ruff F821/E9、仓库卫生及治理扫描通过。
没有实际停服、归档生产文件或切换已部署版本；不继承3836或a99的发布证明。

## 只读源 WAL 捕获增量

只读目录中的冷 WAL 数据库已在隔离数据中复现 L2BackupError：直接在原路径
打开 SQLite 可能需要创建辅助文件。新增显式 read_only_source / --read-only-source
模式，先冻结主数据库和 WAL 至私有目录，校验复制前后身份、大小、时间及摘要，
只在副本上执行 SQLite 备份。源发生变化、符号链接、回滚日志或不合规权限均拒绝；
不复制 SHM、不修改源权限、不以 immutable 模式打开活动源。

新增六项回归覆盖冷/活动 WAL、并发变化、链接、回滚日志和完整只读执行器捕获。
已安装运行解释器下与演练模块联合14项通过；checkpoint/cycle回归85项通过；
Ruff E9/F821通过。日志位于 quantos-dev-output/maintenance-rehearsal-20260924
的 readonly-runtime-final.log 和 readonly-checkpoint-regression.log。

该增量尚未进入15c管理员包，未部署。真实演练CAPTURE失败的具体异常仍需读取
受保护诊断；离线复现不能作为现场同因的证明，亦不代表生产容量已恢复。

候选 worker 已接入 read_only_source=True，并使用已安装的运行解释器完成
只读源捕获→恢复→引用绑定归档演练→完整回滚的子进程测试。冷WAL原入口
先以L2BackupError/退出3失败，接线后通过；源逐文件字节和只读权限保持一致。
输出位于 quantos-dev-output/maintenance-readonly-worker-20260924：before-wal.log、
after.log、worker.py 和 test_readonly_worker.py。最初测试的权限断言写错，
其失败保留在before.log；修正后才执行上述冷WAL红绿验证。候选尚未打包，
测试从当前开发树加载模块，不是已固定管理员包或现场运行成功的证据。

后续已将该增量冻结为4ffc03e818a210b85cbfb52575a6f90240b43e27，独立工作树干净。
固定源码99项回归通过；新演练包10项检查通过，并从实际ZIP加载worker和模块
完成只读冷WAL端到端测试1项通过。包摘要为
d36f61c0a8ddb1070582f62b8f8a8f7bfdecc2ab1e36b1fefedd126205219d86；输出目录
同上，续作说明为CONTINUE.md。未执行新管理员包，现场诊断仍待操作员读取；
上述窄范围检查不构成新完整canonical验收或生产恢复证明。
