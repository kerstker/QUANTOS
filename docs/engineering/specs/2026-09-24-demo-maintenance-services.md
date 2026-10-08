# Demo维护服务状态保全

承接[固定源码联合验证](2026-09-24-maintenance-integration-validation.md)。本增量新增
服务暂停/恢复库，供后续显式操作员维护入口组合；没有新增CLI、sudo、定时器或
后台进程，也没有运行任何stop/start操作。

## 实现边界

`tools/demo_maintenance_services.py`固定列出42个Demo查询、展示发布及checkpoint
服务/定时器，不包含黄金账户、钱包、RF3或下单/签发控制入口。SystemdBackend
只允许该清单内的start/stop，执行变更要求现有root身份，不提供提权。

prepare先观察全部单元并保存私有持久日志，记录原active/inactive/failed状态及
unit定义和drop-in的摘要。读取配置文件不跟随EnvironmentFile或凭证目标。
正在转换状态、未加载、待daemon-reload、无法读取或所有权不符合要求即拒绝。

pause先持久化PAUSING，再停定时器、后停范围内运行服务；范围内都观察为inactive
或failed后记录PAUSED。restore先复核全部配置未变，持久化RESTORING，再启动
原先active的服务、最后启动其定时器。原来inactive/failed的单元不盲目启动；
原本失败的origin/observe恢复需要后续独立只读验收步骤，不能冒充状态回退。

单次命令失败保留中间阶段供恢复；持久日志有独占锁。服务启动失败时不会继续
启动后续定时器。RESTORED后重放为无操作，避免撤销操作员稍后的手动停服。
PAUSED后若观察到外部重启，报告PAUSE_LOST而不重新停它。配置漂移时拒绝启动，
不重置failed、kill-switch、定时器enabled状态或交易权限。

准备和暂停之间也保留操作员动作：PREPARED阶段首次pause发现任何运行状态与
准备快照不同即拒绝，不停止新启动的任务；尚未尝试pause就restore则只结束日志，
返回NOT_PAUSED_NO_CHANGES，不启动已被人工停止的服务。该检查不宣称跨42单元
的原子快照，准备后发生变化时应重新准备，而非重放旧快照。

## 实际只读核查

当前扫描42个清单单元，34个可读取，其中16个active；8个issuer/controller
checkpoint服务和定时器定义因PermissionError无法读取。这8项仍为UNRESOLVED，
不是缺失或已暂停。安全的元数据/摘要保存在输出根actual-unit-inventory.json。
没有借助sudo读取这些定义，也没有追加用户手工命令。

暂停这些单元不证明其他人工调用、用户服务或所有命名空间已经静止；所有结果
保持quiescence_proven=false。本库不得单独作为归档授权或全局停止写入的证明。

后续停写检查补充：SystemdBackend.idle重新读取单元状态和ControlGroup；对仍
存在的cgroup读取cgroup.events的populated值，覆盖后代子组。只有inactive/failed、
无残留进程且前后单元状态一致才返回空闲；路径越界、重复/缺少populated、服务
缺少ControlGroup属性或读取失败均拒绝。pause首次完成及PAUSED重查都调用此
检查，残留子进程时不写入PAUSED，也不擅自kill它们。

真实只读检查42项：22项当时空闲、stream服务和19个timer非空闲、0项读取失败，
见cgroup-runtime.json。该检查不读取unit定义，所以不消除上文8项配置读取权限
问题。另核对到控制器目录属root，不能用“执行器UID无进程”代表控制器静止；
单元清单之外的人工调用、root控制器及新进程仍需维护窗口约束。本检查仍不证明
全局静止，没有停服或接入生产归档。

## 验证

输出根`/home/kerstker/quantos-dev-output/demo-maintenance-services-20260924`：
实现缺失时先失败；六项基础回归通过，故障/范围/锁回归补充后，与十九项留存
事务联合28项通过。恢复重放和丢失暂停两项先失败再修复，最终联合30项通过。
服务变更使用注入的测试后端；真实SystemdBackend仅调用inspect，未变更单元。
Ruff专项通过，治理扫描695文件、0违规，失败日志保留。

后续PREPARED阶段三项反例先失败，再修复；服务与留存联合33项通过，见
prepared-before.log和prepared-after.log。所有变更测试仍使用模拟服务后端。

残留进程/cgroup五项反例先失败后实现，再补四项异常元数据测试，服务与留存
联合42项通过（cgroup-final.log），Ruff通过；真实后端仅运行只读idle观察。

该增量晚于3836提交，不受其5052项执行栈通过声明覆盖。尚未部署，也没有完整
发布验收。真实备份、静止窗口及引用复核、生产归档组合入口和长期留存仍需完成。
