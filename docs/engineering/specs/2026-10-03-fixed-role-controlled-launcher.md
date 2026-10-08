# 固定执行、签发、控制角色的受控启动与退出

承接[三固定角色原生捕获](2026-10-03-fixed-role-native-capture.md)，复用
[研究启动器](2026-10-03-research-controlled-launcher.md)的内核权限、EOF与
wrapper回收检查。原研究请求继续只支持非零研究UID，新增角色入口不修改
ResearchRequest、原worker、归档格式或研究集合的精确类型约束。

## 原请求、固定命令与退出合同

[固定角色启动器](../../../tools/demo_gold_role_launcher.py)只接受原Root密封的
RoleRequest。unit由原请求摘要和原角色名称产生，完整摘要绑定Description；
来源、UID/GID、原根、源码附件和私有spool仍由原捕获清单与registry确定。
启动前核对受验发布源码、unit不存在、原cgroup已排空以及spool未使用。

固定systemd-run/env/setpriv命令限定受验Python，清除附加组、全部capability
含bounding set，并启用NoNewPrivileges后才运行原角色worker。原件及源码只读，
私有spool可写，其他三角色/全部研究根和已知凭据路径不可访问。源码附件落入
其他原角色范围拒绝，安全开关保持原默认值。实际调用launcher仍是操作员动作。

控制角色沿原UID0；UID匹配本身不能证明setpriv完成。启动检查在有限时间内
等待实际UID/GID、附加组与capability全部符合原条件，然后才发送密封描述符。
持续残留、原进程消失、manager归属变化或超时仍拒绝，不向未准入进程交接。

角色CLOSED之后仍须实际wrapper零退出、worker pidfd退出、原unit/cgroup排空、
socket EOF且无附加数据/FD，再复核原请求。取消沿原不可变签发和Root进程选择
自身unit，允许租约过期后的清理；外来unit不能停止。即使stop检查抛错，仍
以持有的wrapper pidfd回收本批子进程并关闭描述符，不按其他UID/PID批量发信号。

整组入口要求执行、签发、控制恰好三份原源码附件。全部原请求在启动前签发，
全部归档独立原生恢复并复核成功后才开始关闭，所有实际退出成功才返回结果。
可选原文件读租约只接受实际持有句柄；退出回执和整组结果继续明确保留
writer/whole-set/跨角色业务关联/经济权威/下单权限false。

## 验证与限制

最初缺失接口16项红；控制UID0在setpriv尚未清组时提前拒绝的回归先红后修，
持续组残留仍拒绝。普通检查另外以真实子进程及pidfd验证stop检查异常时回收。
第一轮Root容器5 FAIL：工作目录沿用了研究UID参数，被原固定角色验证器拒绝；
改为原角色名称后通过，原失败日志、冻结副本与摘要全部保留。

父d517e6a912ff16c2c465381ea46093bdd8109d02的自有Git对象副本加精确三个Python
overlay，最终四组联合82 PASS/20.86秒，禁网只读Root/角色容器5 PASS/149.79秒。
两组各自3021个Python文件前后相同，原driver/pytest均已终态回收exit0。
三个实际UID（含控制UID0）、原数据库/目录、原Git bundle、setpriv权限清除、
密封IPC、原worker、原生归档与Root独立恢复、复核/关闭及实际退出均执行；
异常退出、未排空、附加组残留和bounding capability残留分别拒绝。

容器只使用隔离状态根，禁网且源码/runtime只读；无宿主服务管理器、受保护
状态或真实凭据。manager及cgroup归属/事件、公件与accepted-release路径仍为
明确夹具，不能据此声称现场systemd sandbox/cgroup验收。原固定角色业务历史
为空，不能声称非空签发—执行因果关联或完整四角色恢复。

三个新Python文件完整Ruff、全仓canonical F821/E9、仓库卫生、文档链接、实际
pre-commit和服务图检查适用。本批输出为
gold-project-closure-20261003-3pr5mrly/root-role-launcher；首轮与最终冻结源码、
日志/XML、前后摘要、提交/patch和选择性整合记录保留。误在输出目录调用Ruff
的路径缺失结果未计PASS，随后在真实源码根完整检查通过。

## 完整目标与回退

执行/签发/控制与全部研究UID的同cut整组协调、非空签发—执行及retention镜像
关联、正式完整writer停写和维护接线仍待完成。统计/锁定OOS/基准/实际成本风险
受签消费与候选准入、最终同版ARM64/canonical完整门、获准现场安装/升级/恢复、
五秒链及实际24小时成交—现金—再研究仍需验收。最新完整门保持旧版
30 PASS/1 FAIL/1 TIMEOUT，专项不替代；机构异机设施延期，策略盈利研发单列。

本批只增加一个入口、两个测试及本记录，无现场部署或生产状态变更。回退须
先核对这四份文件仍为本批字节，再仅移除这些新增路径；保留验证与失败记录，
不覆盖其他用户修改。原研究启动器及固定角色原生捕获代码保持原样。
