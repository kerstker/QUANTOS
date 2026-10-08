# 固定执行、签发、控制角色的内核交接与原生归档

承接[完整研究维护窗口](2026-10-03-gold-maintenance-research-window.md)。原生
归档器已经支持三种固定角色，但原受控请求、worker 和 Root 消费者只接受
研究角色。不能通过把研究请求的 namespace 或 UID 改标签取得控制角色权限。

## 固定角色请求与权限

[新交接协议](../../../tools/demo_gold_role_handoff.py)从实际
VerifiedResearchLayout、原 VerifiedPublic 和固定 ROLE_ROOTS 选择角色。调用者
不能指定 UID、原根路径、输出路径或运行代码。执行/签发 UID 沿原 registry，
控制角色沿原登记 UID 0；研究请求类型继续只支持原非零研究 UID。

Root 原进程、boot、PID 起点、有限单调时钟租期、原清单和公共 cut、角色根
inode/属主/私有权限/祖先、源码声明均绑定到 Root 所有的密封 memfd。内核
SEQPACKET 的逐消息凭证与实际 PID/UID/GID匹配后才交接。每次工作检查要求
原 UID/GID、清除附加组、全部 capability 含 bounding set 为零、NoNewPrivileges。
这尤其适用于原 UID 0 的控制 worker，不提供凭据或服务操作接口。

原件目录和输出不能重叠，检查包含全部原研究根。输出落入研究根的反例先红
后修；签发前及每次原请求复核都从原清单重新核对，不能只排除三固定角色。
同字节换 inode、原清单漂移、错角色、过期、附加组/capability 残留均拒绝。

## 原生读取、独立恢复和关闭

[角色 worker](../../../tools/demo_gold_role_capture_worker.py)只接受原 admitted
RoleLease，沿原 CaptureInventory 与多卷 transport 验证源码、原始文件、
数据库/WAL和原生语义。原清单持有至 PREPARED→RECHECKED→CLOSED，Root 消失、
socket 断开、原件变化或控制顺序不符则拒绝，不修改原角色状态。

[Root peer](../../../tools/demo_gold_role_capture_peer.py)独立复制角色私有归档，
绑定原请求/manifest/receipt，运行原异路径恢复与语义验证；状态包不是成功
证明。恢复验证前不能要求 RECHECK/CLOSE。复核及关闭前后重查原归档，实际
角色根、源码、cut、receipt 与 worker 身份必须连续。原研究 peer/set 的精确
类型约束保留，不把新角色 peer 纳入旧研究集合。

实际角色捕获另外复现了原 transport 的目录空载缺陷：合法 controller 只有
六个原必需目录、无普通文件时，原恢复只有在遇到第一个 FILE item 才开启
源码/预算/索引阶段，最终错误拒绝。明文与加密反例均先红。[原 transport](
../../../tools/demo_gold_checkpoint_transport.py)现在先确认辅助序列完整，再
沿同一原验证器检查源码、预算和完整索引、恢复原目录；有任何未到齐的预期
文件仍拒绝。没有添加另一种归档格式或放宽普通文件完整性条件。

## 验证与当前边界

父 499cba3c 的自有 Git 对象副本加精确八个 Python overlay，最终九组联合
185 PASS/417.31 秒；禁网只读 Root/角色容器两项 2 PASS/86.07 秒。两组各自
3018 个 Python 文件前后相同，原 driver/pytest 终态回收 exit0。实际三角色
原数据库/目录、源码 bundle、降权、请求、归档和 Root 独立恢复均执行；原件
改变后拒绝复核，不能把此前成功作为关闭许可。最初请求接口缺失24 ERROR、
输出/研究根重叠1 FAIL、目录空载明文/加密2 FAIL，以及两版容器失败均保留。
第一版容器 source fixture 的 Git 属主不符按原验证器拒绝；后续由各实际角色
自行克隆源码，没有放宽 source verifier 或原权限。

七个新 Python 文件完整 Ruff、全仓 canonical F821/E9、仓库卫生、文档链接、
原实际 pre-commit 和服务图适用检查。transport 另行完整风格诊断有57条既有
E701/E702，父版和本版诊断集合相同，原日志保留；本次仅改13行，不借此格式化
整个原模块。上述结果不替代完整 canonical 门。

本批输出目录为 gold-project-closure-20261003-3pr5mrly/root-fixed-role-capture，
红/失败日志、XML、每次冻结副本和前后摘要均保留。整合前原 transport 字节
核对、八新路径无前像检查、688个递归依赖含嵌入 Python 导入核对已通过。

真实 Linux Root/角色容器只绑定只读源码/依赖和任务输出，禁网且不绑定宿主
服务、生产状态、凭据。原公共/accepted-release 是明确夹具；原角色数据库/
目录、Git bundle、UID/GID/capability 降级、密封 FD、私有多卷和异路径恢复
使用原实现。执行/签发历史为空、控制目录为空，不能据此证明非空签发—执行
因果关联或历史 retention 镜像完整。

生产固定角色 launcher、实际退出/cgroup 排空、三角色与全部研究 UID 的整组
协调、跨角色签发与镜像关联、同 cut 的完整 writer 屏障及维护接线仍待闭合。
本批不称现场 sandbox、全局停写、整组原子恢复、安装或全量发布验收。
writer_barrier、whole_set、经济与下单权限均 false。当前完整系统仍须完成
独立统计/OOS/成本风险证据消费、最终同版 ARM64/canonical 完整门、获准现场
安装/升级/恢复、五秒链与实际 24 小时成交—现金—再研究；其他市场分别验收，
机构异机设施延期，策略盈利研发单列。
