# 非空业务历史的整组原生捕获与恢复

延续[恢复关联](2026-10-04-restored-role-joins.md)与
[完整交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。父提交
`25d4508cd035a9cb6437b502e0915df69f096916`。本批是开发验证，不授予发布、
经济准入或现场操作权限。

## 行为与缺陷

新增隔离内核案例由实际 executor UID 2001 调用原生产者，在签发与不可变写入
之前选择原源码、账户和角色身份。原生材料包含五条签发记录（含未签 RESERVED）、
两笔入场/退出预留与成交、原现金反馈及完整 retention 历史。issuer UID 2002、
controller UID 0 和 research UID 10001/10002 使用各自目录与源码附件。
测试配置、公件、签发密钥、场所响应、manager/cgroup 与 accepted-release 为夹具；
不会使用真实凭据、网络或宿主服务。

当前和历史源码附件均随原业务引用提供，各角色通过自己的实际 UID 克隆原 bundle。
生产者使用自己拥有的私有源码副本，保留真实 Git 属主检查；不修改只读 `/code`，
不加入全局 safe.directory 例外，不改写已签或已留存的身份。

实际五个 worker 的整组原生恢复揭示 `verify_retained_pair` 的已结算分支调用
`_feedback_view` 时遗漏 `rotation_sha256`。原 `_feedback_view` 随后 KeyError，
空历史和 HOLDING 镜像此前未覆盖该分支。新增三个短回归分别覆盖反馈未留存、
已留存待投递、已投递，均先在原代码失败。

修复仅为该逐帧 EXACT 审核上下文显式提供 `rotation_sha256=None`；完整运行历史
仍由原调用方及原验证器审核。每帧以其原 runtime 为 current，不制造轮换签名，
不放宽结果、窗口、原始 decision、镜像、反馈或投递身份检查，不修复控制库。

## 验证

输出在 `/home/kerstker/quantos-dev-output/gold-project-closure-20261003-3pr5mrly/root-nonempty-native`。
每版冻结到自有 Git 对象副本（无 alternates/hardlinks），原日志/XML 与失败分别保留。
第一版实际空历史不能满足非空断言；随后夹具的共享目录权限、生产者源码属主、
目录命名冲突及研究历史源码附件缺失分别失败。没有放宽生产身份边界。

第六版五 worker 原生恢复后的 KeyError 红例保留：1 FAIL/1 SKIP，192.26 秒，
原 driver 192.98 秒 exit 1，3031 个 Python 文件前后一致。三个短回归同样因
`rotation_sha256` 缺失失败（1.18 秒）。第八版专项联合 **99 PASS/76.09 秒**，
原 driver 76.65 秒 exit 0，3031 个 Python 文件前后一致。

第七、八版禁网只读 Root 容器均完成正向非空整组恢复：五个原 worker 真正退出 0，
签发记录 5、执行预留 2、关联 2、未签 RESERVED 保留；原生只读 retention 镜像
帧数一致且无镜像待补齐；原件 inode/内容不变。两版各有后续反例初始化失败，
总结果均为 1 PASS/1 FAIL/1 SKIP，不能称整组测试通过。原 driver 分别 267.96、
267.55 秒 exit 1，3031 个 Python 文件前后一致。反例先后暴露测试 manager
拦截独立数据库生产者，以及隔离 Python 未显式导入项目目录；仅修测试设置。

第九版只复跑尚未通过的空签发库反例：**1 PASS/192.59 秒**，原 driver 193.30 秒
exit 0，3031 个 Python 文件前后一致。五份归档各自原生恢复成功后，空签发库
不能对应非空执行记录，原 SIGNED_ANCESTOR 检查拒绝；没有发出任何 CLOSE，
五个实际 worker 全部回收。原五条签发历史保留，反例使用独立有效的新空库。
其 production、全部短回归及正向/夹具 AST 与第八版相同，唯一 Python 变化为
反例函数内的初始化设置。各自原 driver 已终态回收，容器已退出；不把不同
冻结版本相加为完整同版门禁结果。

新增/修改测试完整 Ruff、全仓 canonical F821/E9 通过。生产 retention 文件完整
风格检查的十条既有诊断与父版本一致，原结果保留，未称该项全绿。
提交 hook、干净提交基线与选择性整合结果在本批输出独立记录；这些不替代
canonical 完整门或现场验收。

## 交付边界

本批目标是非空原生签发消费与留存镜像的一致性证据；完整正式 writer 同 cut
停写、现场 sandbox/cgroup、完整现金对账及实际 24 小时链仍需独立验收。
`cross_role_business_join_verified`、`writer_barrier_verified`、`whole_set_verified`、
`cash_reconciled`、`submission_enabled`、`economic_authority` 仍为 false。

研究统计、锁定 OOS、基准、真实成本风险的受签消费与候选准入，以及最终同版
ARM64/canonical 完整门仍待关闭。最新完整门仍为旧冻结版 30 PASS/1 FAIL/1 TIMEOUT，
本批专项不替代。其他市场逐项验收，机构异机设施延期，策略盈利研发单列。
未部署、操作受保护状态或真实订单；完整交付目标保持进行中。
