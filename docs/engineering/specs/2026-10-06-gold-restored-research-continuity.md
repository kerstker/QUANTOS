# 原研究 UID 恢复副本的连续性

状态：独立增量，父提交 `dbc22390fda634a5be47a76bbf84db247ccad86f`。
依据：[固定交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)；
范围为 F5 中断恢复的原件边界，不增加交付目标。

## 原失败与修改

原全角色协调器对 executor、issuer、controller 和全部 research UID
执行原生归档恢复核验，但 `open_join` 只持有前三者的 Root 恢复副本。
研究 peer 后续检查原 UID 归档，遗漏 Root 已恢复副本的连续性。
在最后一个研究 UID 的 `input.json` 上以同字节替换 inode，原流程仍
能进入 CLOSE。真实 ARM64/Linux/五 UID 回归先失败：1 FAIL，
128.59 秒，原异常断言 `DID NOT RAISE ValueError` 留存。

协调器现在将原 peer 按完整 research UID roster 交给原 join。
每个研究恢复副本复用原 manifest/index/内容/UID/模式/目录与 inode
检查；原 native 状态、完整文件集合与请求/来源绑定均承重。
join 在进入、CLOSE 前、worker 退出后与上下文退出时，核对所有
原恢复副本。没有另建现金账本、研究档案或调用者指定的恢复树。

复核时第二个真实内核红例确认：完整 UID key 集合可把同一个
原 peer 放入两个槽位，原校验仍接受；1 FAIL，133.84 秒。
现在要求每个 peer 的实际 UID 等于对应槽位；错槽在 CLOSE 前
拒绝 `COMPLETE_RESEARCH_ROLE_SCOPE`。同字节换 inode 拒绝
`RESTORE_ORIGINAL_CHANGED`；失败均保留并核验 worker 清理。

## 版本与消费

全角色协调器输出的嵌套 join 升为 `GOLD_RESTORED_ROLE_JOINS_V3`：
`research_archives` 为完整 UID 对应的原 archive manifest/inventory
摘要，`research_restore_continuity_verified=true` 只表示这些恢复
副本连续性已核对。原固定三角色 `archives` 字段保持。

维护窗口消费者核验完整研究 UID 集合及每一份原 native capture
摘要绑定，拒绝缺失/多余 UID、错误摘要、无原 capture 或假连续性。
历史固定三角色调用仍为 V2；V1/V2 历史读取不升级为完整研究恢复。

## 验证与交付边界

最终定向验证通过；独立冻结提交与主区整合另有外部记录。
原正例及非空 executor/issuer/retention/cash 检查保留；仅更新
实际嵌套 schema 的预期，并增加完整 UID 摘要绑定断言。

内核测试使用无网络、只读源码和运行时的 ARM64 容器，Root、
五个 UID worker、原生归档与 inode 是实际内核对象；service
manager/cgroup/accepted-release 是明确夹具。它不证明现场安装、
writer 停写、业务引用或获准操作。

`cross_role_business_join_verified`、`writer_barrier_verified`、
`whole_set_verified`、`cash_reconciled`、`mutation_permitted`、
`submission_enabled` 和 `economic_authority` 继续为 false。
完整同版发布、长期因果输入/独立核验/真实成本风险与锁定 OOS、
现场同 cut 现金恢复和获准 Demo→现金反馈→再研究仍须验收。
策略盈利、新市场与模型后续单列；缺证据不算 PASS。

证据目录：`gold-project-closure-20261003-3pr5mrly/`
`gold-restored-research-continuity-20261006`，保留两轮红例、原命令、
日志、JUnit、进程身份、退出结果及完整 Python 摘要。
未部署、切宿主服务、写保护状态、安装实际 trust、读凭据或下单。

## 最终验证

- 四组定向测试：85 PASS、108.25 秒；driver 108.753116329 秒。
- 实际 ARM64 Root/五 UID 内核：6 PASS、848.20 秒；driver 848.971583843 秒，包括原非空正常恢复和拒绝案例。
- 两轮最终检查的 3121 份 Python 字节摘要前后逐项一致。
- 七个修改文件完整 Ruff、全仓 E9/F821、服务图、仓库卫生和 diff 检查通过。

原 dbc 同版完整门独立继续；后端阶段已按原 3600 秒预算 TIMEOUT
（3600124ms），不能记整门通过。原流程、失败与新候选分别留存；
这六项真实内核检查和 85 项定向检查不代替同版 ALL 或现场操作验收。
