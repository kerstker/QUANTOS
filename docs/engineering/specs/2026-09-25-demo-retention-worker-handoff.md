# 维护控制端到受限执行进程的交接

承接[执行侧归档适配器](2026-09-25-demo-executor-retention-worker.md)。
本次接通已授权描述符的启动与回收，不提供授权签发、现场部署或一键维护入口。

## 受限执行进程

`tools/demo_retention_worker.py`只接受PREPARE/APPLY/ROLLBACK动作，从stdin描述符0
读取内核封存授权，不从参数、环境或普通JSON文件接受替代授权。不提示输入。
输出固定schema的一行有界摘要；异常只输出固定原因枚举和INSPECTION_REQUIRED，
不能因为调用失败就宣称没有发生文件移动。没有服务恢复或自动重试行为。

`tools/demo_retention_worker_handoff.py`限定新的quantos-demo-retention-<32位摘要>
系统服务名。控制端必须是实际root，并核对描述符归属当前控制进程、公开注册表和
已验收源码；运行前要求该单元尚不存在且cgroup为空。
通过systemd-run --pipe将sealed memfd直接作为stdin传入，避免转写成可改的管道文本。

生成的单元使用既有执行UID/GID、空附加组/能力、NoNewPrivileges、独立禁网命名空间、
AF_UNIX限制和只读系统。仅四个历史记录目录与归档根可写；数据库根整体不开放写入，
requests输入子目录另外只读。/etc/quantos被只读临时文件系统遮蔽，仅重新绑定公开
authority.json；API凭证、签发与控制器私有状态不进入该进程。未修改交易安全开关。
子进程使用固定源码/环境及清空后的环境变量，最长600秒，KillMode=control-group。

## 退出与恢复边界

启动器有界读取结果，严格校验字段集合、动作、状态、数量类型/上限及False权限标志；
必须退出码匹配、输出无额外字节、单元及其全部cgroup子进程退出，才报告WORKER_COMPLETED。
超时或协议错误只尝试停止描述中绑定本次授权摘要的单元，不会停止同名的其他任务。
停止失败或残留子进程返回检查状态；杀掉systemd-run客户端不被当成执行进程已退出。

维护窗口新增run_worker：在已暂停、同boot和同目录身份的窗口锁内调用，绑定窗口
身份及归档操作摘要，启动前持久化派发意图，结束后再次核对原暂停状态。
若调用中断或结果未完成，restore拒绝恢复服务；不能仅凭worker_drained或一个成功
标志恢复，必须有通过严格校验的动作结果，并再次查询当前cgroup。
未解决的前次调用只允许在确认子进程退出后进入显式ROLLBACK恢复流程。

## 验证

新增15项交接测试及4项窗口测试，与授权、归档、历史读取、演练和其他维护组件
组合170项通过；Ruff通过。包含真实普通用户子进程正常退出/超时、固定动作协议、
状态伪装拒绝、残留cgroup、外部任务不误停，以及派发前日志和恢复拒绝。

另在DGX临时用户服务中实测systemd --pipe：原始字节、UID 1000、nlink=0、四项封印
值15均保留，写入被内核拒绝。该探针没有使用Demo账户、生产服务或root执行身份。
--pipe会传递调用者stderr，不能仅依赖StandardError=null；实际启动器明确把stderr
接到DEVNULL，按此方式的第二次探针同样通过。首轮捕获stderr的观察也保留。

生成单元的systemd-analyze verify退出0。其对系统中四个无读取权限的既有私有timer
产生的警告保留，不据此宣称已验证那些单元。没有实际启动新root沙箱或验证其挂载。
证据目录：`/home/kerstker/quantos-dev-output/maintenance-handoff-20260925`，包括
unit-parse.json、transport-probe.json及transport-probe-captured-stderr.json。

仍需root协调器实际运行新鲜引用审查、备份恢复演练和进程检查，再按明确操作授权
生成描述符并管理中断恢复；也需新源码冻结验收、沙箱实际预检和可审阅安装/维护包。
没有生产目录移动、服务切换或签发政策修改，不能把组件测试当成容量恢复。
