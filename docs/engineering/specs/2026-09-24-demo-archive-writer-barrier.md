# Demo归档写入锁边界

真实备份及离线归档演练完成，不代表可以直接移动生产文件。正式归档前必须
排除相关写入者及新增引用者；服务暂停不等于任意人工/root任务均已静止。

新增tools/demo_archive_barrier.py的executor_barrier，打开执行器现存的
observation.lock、origin-capture.lock、resident-recovery/cycle.lock，按固定顺序
持有独占非阻塞flock。只接受当前身份所有、私有、单链接、零长度普通文件；
检查目录及锁inode，拒绝缺失/链接/权限错误，不创建替代锁、不修改原文件。
后续锁忙或操作异常释放已持有锁；成功退出前再次检查inode，发生替换则报错。

它不是生产操作入口，不取得归档授权，不读取凭证，不停止服务，不宣称全局静止。
尤其旧resident版本在cycle锁外发布展示；旧controller也不遵守新controller_lease。
读取实际/opt/quantos/releases/c34f31b701c9d6b90456bfbf37f13e63b9b20ef7源码，
未发现新的controller_lease或publish回调接线。未经该部署边界解决，不能单凭
executor_barrier放行生产移动。现行root/人工调用及引用扫描仍需单独约束。

测试先因入口缺失失败；八项真实flock/文件身份测试与五项controller lease测试
联合13通过。验证实际争用、部分获取失败后的释放、缺失/软硬链接/权限拒绝、
锁替换和操作异常释放。Ruff E9/F821通过。证据输出：
/home/kerstker/quantos-dev-output/maintenance-window-20260924/barrier-before.log、
barrier-after.log。未在生产目录获取这些锁，未改变服务或文件，未部署。
