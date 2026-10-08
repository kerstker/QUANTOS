# 完整角色原生归档接入原维护窗口

承接[统一原生归档协调](2026-10-03-all-role-native-coordination.md)与
[原完整研究维护窗口](2026-10-03-gold-maintenance-research-window.md)。原研究
入口仍只归档研究范围；新增完整入口不能把旧研究记录改标签称为完整系统。

## 在途日志、退出与恢复

[完整维护入口](../../../tools/demo_gold_all_role_maintenance.py)沿原operator
独占锁、原Gold服务抑制/暂停、完整原UID检查和真实原件读租约，调用原完整
协调器归档三固定角色与全部研究UID。实际服务操作仍是Root操作员动作，本批
仅在隔离容器使用受控manager边界，不操作宿主服务或生产状态。

新增Root私有gold-all-role-capture.json，独立schema绑定原window/source/layout、
process scope、public/cut摘要。RUNNING必须持久化在dispatch之前；只有原完整
协调器返回四角色齐套、完整原研究UID、同cut绑定、实际退出及租约证据，并且
原租约上下文正常结束、再次新鲜检查全部UID为空后，才写COMPLETE和结果摘要。
来源、UID范围、cut或退出证据不符的成功声明不能写完成记录。

[原窗口恢复守卫](../../../tools/demo_maintenance_window.py)同时检查原研究
记录和新增完整记录。新增RUNNING、外来绑定、伪完成字段或未知记录均在任何
服务恢复动作前拒绝，不移除抑制、不启动原服务。失败只在新鲜完整UID检查
为空后写FAILED_CLEANED；任何残留或未知状态保留RUNNING，需显式原恢复路径
重新检查。FAILED_CLEANED不称归档成功，不携带结果摘要或原生齐套/退出成功标志。

复核发现原研究入口也在lease context最终清理之前写COMPLETE。本批最小修复
将该标记移到正常退出之后，并增加完整UID再检查；schema和原研究范围保持
不变。完整入口同样遵循此顺序，不把早先成功作为最终清理通过的替代。

## 回归与真实验证

新记录尚未接入守卫时，原恢复错误放行的五个反例先失败，接口缺失另九项报错，
原日志保留。接线后14项通过/5.71秒。lease最终清理抛错、释放时出现UID的
完整/研究两入口四反例均先失败；修后新18项加原窗口14项共32 PASS/10.75秒。
测试中worker、manager、Root custody为明确虚拟边界，不当作内核证明。

父5cdbbe1f62499d05766b26287ecafc636a647729的自有Git对象副本加精确四Python
overlay，七组联合129 PASS/25.37秒；禁网只读Root/角色容器4 PASS/366.63秒。
两组各自3027个Python文件前后相同，原driver/pytest终态回收exit0。容器使用
实际Root独占锁、原抑制文件、五实际worker、完整原件读租约、原数据库/目录/
Git bundle/研究试验、setpriv权限清除、密封IPC、原归档与Root异路径恢复，
随后实际退出，再恢复原活动服务的受控manager状态；记录COMPLETE有实际依据。

另外实际空闲研究UID存活阻断恢复，退出后才允许清理；最后原研究UID的原生
RECHECK后注入异常，五worker先回收才写FAILED_CLEANED；五worker全部成功退出
后，租约释放期间再启动实际研究UID，完成记录与恢复均被阻断，直到它真正退出。
失败证据与生成归档保留，不把中断清理当成成功、不删除历史使检查通过。

manager/cgroup、公件与accepted-release路径仍为明确夹具，固定角色历史为空。
未证明现场systemd sandbox/cgroup、非空签发—执行、retention镜像或完整正式
writer屏障；native四角色齐套不等于whole-set或跨namespace原子性。writer、
whole-set、跨角色业务关联、经济和下单权限仍false，未进行生产恢复或交易。

三个新Python文件及原窗口完整Ruff、全仓canonical F821/E9、文档链接、仓库
卫生、实际pre-commit与服务图检查适用；原窗口父版和本版完整Ruff均零诊断。
输出为gold-project-closure-20261003-3pr5mrly/root-system-maintenance，保留所有
红/绿日志、XML、冻结副本、前后摘要、提交/patch及选择性整合记录。

## 完整目标与回退

非空签发—执行及retention镜像原生关联、正式完整writer范围与同cut停写继续
开发。研究统计/锁定OOS/基准/真实成本风险受签消费及候选准入、最终同版
ARM64/canonical完整门、获准现场安装/升级/恢复、五秒链与实际24小时
成交—现金—再研究仍待验收。最新完整门仍旧版30 PASS/1 FAIL/1 TIMEOUT，
专项不替代。机构异机设施延期，其他市场逐项验收，策略盈利研发单列。

回退先核本批字节：仅将原窗口恢复为父版，并移除四新增路径；保留验证及
失败记录，不覆盖其他用户修改。开发未改变宿主服务、原账户/研究状态、trust、
真实凭据或授权边界，不需要以生产数据回滚作为本批开发回退步骤。
