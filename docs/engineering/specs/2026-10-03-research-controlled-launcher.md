# 研究受控启动、权限降级与退出排空

承接[研究归档协调端](2026-10-03-research-capture-coordinator.md)。原协调协议收到
CLOSED 时，worker 仍可能在最后租约核对或退出阶段失败；只凭状态包不能证明
实际进程成功退出，也不能排除仍持有控制通道的子进程。

[受控入口](../../../tools/demo_gold_research_launcher.py)现在只接受原 Root 签发的
ResearchRequest，从原完整研究清单取得 UID、源码和私有 spool。实际启动仍是
操作员动作：本批只做隔离开发，没有在宿主调用 systemd-run。

## 启动与退出合同

每个请求派生固定 transient unit，完整请求摘要绑定 Description。启动前核对
已安装受验源码，拒绝已有 unit、残存 cgroup 或已有角色 workspace。生产命令
使用固定 systemd-run/env/setpriv 和受验 Python 路径，不接受调用方命令或 unit。

systemd 的空 SupplementaryGroups 会清空额外配置，但不会移除用户数据库中的
默认附加组。固定 env/setpriv 启动程序先有 SETUID、SETGID、SETPCAP 三项能力，
再在同一 PID 清空附加组、继承/有效/许可/环境及 bounding capabilities，降为
原研究 UID/GID，并启用 NoNewPrivs。Root 读取实际 /proc 身份与 capability，
确认降级和所属 cgroup 后才发送原密封描述符。

服务严格限制文件系统及网络；固定原研究根、受验源码和原源码附件只读，角色
spool 单独可写，临时目录隔离，执行/签发/控制私有根与凭据目录不可访问。
交易开关保持原五项安全默认值。原 worker 和原生归档/恢复算法不另建一套实现。

完整研究集合经原清单、密封交接、归档恢复、RECHECK/CLOSE 后，还须 wrapper
退出码为零、持有的 worker pidfd 已退出、原 unit 的 cgroup 排空、控制 socket
EOF 无附加数据/描述符，才能产生研究 worker 退出回执。缺少任一项均拒绝。
退出回执仍不证明全局 writer 停写、完整四角色恢复或经济/下单权限。

取消允许在原租约过期或研究输入变化后执行，但仍核原不可变签发、Root 控制
进程及完整 unit 摘要；不授予新的捕获权限。服务身份不符时拒绝停止。即使该
检查失败，finally 仍以持有的 pidfd 回收本 launcher 的 wrapper，并关闭描述符；
不会按其他服务的数值 PID 或 UID 批量发信号。

## 实际验证与限制

两条清理反例先 2 FAIL/12 PASS，修后通过：租约过期阻断清理，以及停止检查
抛错后真实子进程与 pidfd 泄漏。原命令断言把 PYTHONPATH 误当独立 argv 元素的
一次失败保留，并纠正测试断言，没有改变生产命令迎合它。

真实内核测试首轮 1 FAIL/4 PASS：正常 EOF 返回了接收调用的 MSG_CMSG_CLOEXEC
标志，旧检查误拒绝。普通 UID 的真实 SEQPACKET 反例先 1 FAIL/1 PASS；修后
仅容许该标志，截断标志、数据和控制信息仍拒绝，意外收到的 FD 也会回收。
本批 launcher 单组最终16 PASS/5.48秒。

独立自有 Git 对象副本为95407b08基线加精确三个 Python 文件 overlay。五组
联合69 PASS/39.46秒，3006个 Python 文件前后摘要相同。真实 Root/角色容器
五项全部通过/100.90秒：两个 UID 的原生产者、完整试验、原 Git bundle、原
研究归档/恢复、原密封 IPC、实际 setpriv 权限降级、原 worker 和退出码确实执行；
异常退出、未排空、附加组残留、bounding capability 残留分别拒绝。

容器禁网、只读，使用已核验 ARM64 镜像和 Python3.12.3。启动能力只增加用于
实际 setpriv 的 SETPCAP；没有 SYS_ADMIN/KILL，也没有宿主状态、凭据或服务
管理器。systemd manager、unit cgroup 归属及 fixed-public/accepted-release
路径明确为夹具边界；不据此宣称实际 systemd sandbox 或现场权限已经验收。
原失败日志/XML、每版冻结源码、全部 Python 前后摘要及终态位于
gold-project-closure-20261003-3pr5mrly/root-research-launcher。

## 完整目标剩余条件

执行/签发/控制与全部研究 UID 同一切面、固定完整 writer 范围及真实停写、完整
原生归档/恢复/维护入口继续开发。锁定 OOS、基准、多重试验和实际风险的受签
消费与最终候选准入、最终同版 ARM64/canonical 完整门、获准现场安装/升级/
恢复、五秒链和实际24小时成交—现金—再研究仍未完成。最新完整门仍为旧冻结
版30 PASS/1 FAIL/1 TIMEOUT。机构异机设施延期，策略盈利研发单列。
