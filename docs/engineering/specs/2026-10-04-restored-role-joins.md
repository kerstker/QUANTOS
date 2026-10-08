# 原生恢复后的签发—执行与 retention 关联

依据：[完整角色协调](2026-10-03-all-role-native-coordination.md)、[维护入口](2026-10-03-all-role-maintenance-window.md)及[完整交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。独立源码父提交 `ff9ef9c1f2855a91d4ec991dc5d36a20df19727c`；本记录不授予发布、经济准入或现场操作权限。

## 行为

完整协调入口在所有固定角色及研究 UID 的归档独立恢复成功后、任何 CLOSE 之前，调用新增的 `tools/demo_gold_restored_joins.py`。入口只消费原 Root 密封请求的三个实际固定角色 peer；不接受调用者指定恢复根、验证回调或成功布尔值。恢复位置必须是该 peer 的原操作工作区，manifest、公件、cut、源码、角色、原归档摘要及原生语义结果绑定。

原传输层 manifest 格式、原认证索引和全部恢复状态文件先核对，持有期间继续检查内容及 inode。签发—执行沿原 `demo_gold_issuer_executor_join.open_join` 读取私有 SQLite 副本及原提案证明，检查签名、预算预留、消费顺序、账户与部署绑定；存在撤单库时沿同一原撤单检查，不另造会计公式。

retention 沿原只读、全历史 `Retention` 及 `verify_retained_pair` 检查完整已绑定运行历史、closure/reobservation 和镜像。原生未投递签名、镜像待补齐及终端部分状态原样保留；不会 adopt、初始化或修复控制库。无 retention 明确返回 `ABSENCE_VERIFIED`，不称非空镜像完成；仅执行侧存在时返回 `CONTROLLER_RETENTION_NOT_INITIALIZED`，pair 未验证。两个已有库的矛盾、丢失原提案、未绑定运行切换或恢复原件替换均拒绝。

全部 peer 原生 RECHECK 后、关闭前再次核对关联；实际 worker 退出验证后及 join context 正常结束时再核对。返回的 `restored_role_joins` 绑定三份原归档摘要。维护入口必须消费该报告，核对同一 source/layout/public/cut/operation 及原归档；缺失、跨 cut、跨归档或声称经济权限的报告不能写 COMPLETE。原研究子集 schema 与历史维护记录不改标签。

## 验证

验证输出：`/home/kerstker/quantos-dev-output/gold-project-closure-20261003-3pr5mrly/root-restored-joins`。验证使用独立 Python 3.12.3/aarch64 环境、自有 Git 对象副本（无 alternates、无 hardlinks）、隔离输出。

修正后的精确冻结源码七组联合 **128 PASS/37.57 秒**；禁网、源码/runtime 只读的 Root/角色容器 **8 PASS**，原 driver 831.04 秒后回收 exit 0。两组各自 3030 个 Python 文件前后摘要一致，原 driver/pytest 终态已回收。四维护反例、两个完整协调案例及两个原恢复关联案例消费实际五 worker、原 UID/数据库/目录/Git bundle/研究试验、原件读租约、setpriv、密封 IPC、原生归档与异路径恢复；原归档绑定核验通过，同内容 inode 替换阻断所有 CLOSE，五 worker 实际回收。

七个修改/新增 Python 文件完整 Ruff、全仓 canonical F821/E9、文档链接、卫生及服务图 current 通过（20 域、52 边、无循环）。主工作区四个修改路径与父前像一致、三个新 Python 路径不存在，735 个递归依赖及 pytest 配置字节一致；核对包含嵌入的 Python 程序。实际提交 hook、干净提交基线及选择性整合结果单独留存。

保留先失败证据：八个缺实现反例；协调入口三项未消费关联结果；维护入口缺报告单项及四项外来报告反例。首轮 helper 验证为 25 通过/1 失败，暴露测试的替代 controller 从 ordinal 2 开始，尚未到达关联验证器；改用独立有效、连续但不同的首 claim，未放宽生产验证器。

第一容器运行在新增维护结果合同后主动结束，exit 137，日志已有 F，不计通过；原 Python 字节未变。下一精确冻结版在首个实际维护案例失败（97.59 秒），暴露 manifest 误用带换行的 retention 格式。仅改为原传输层 `_canonical`，原摘要与身份检查保留。所有失败、停止和后续验证分别留存，不覆盖旧结论。

## 限制与后续

本批证明原生签发消费及镜像一致性检查已接入关闭路径；非空 helper 数据是真实原生格式，公件、密钥与授权材料为测试夹具。实际 Root 容器的固定角色业务历史为空，manager/cgroup、公件与 accepted-release 为明确夹具。不能据此声称整组带非空业务历史、现场 sandbox、正式 writer 屏障或完整现金对账已验收。

`cross_role_business_join_verified`、`writer_barrier_verified`、`whole_set_verified`、`cash_reconciled`、`submission_enabled`、`economic_authority` 继续为 false。pair 原生审核通过仍可能含明确 `verified_partial`，不代表全部镜像已补齐。未操作宿主服务、生产状态、真实凭据、trust 或订单。

完整系统继续要求：非空整组原生关联与正式完整 writer 同 cut 停写；研究统计/锁定 OOS/基准/真实成本风险受签消费和候选准入；最终同版 ARM64/canonical 完整门；获准现场安装、升级与恢复、五秒链和实际 24 小时成交—现金—再研究。最新完整门仍是旧冻结版 30 PASS/1 FAIL/1 TIMEOUT，专项不能替代。其他市场逐项验收，机构异机设施延期，策略盈利研发单列。

回退本开发增量须恢复四个修改路径的已备份父版本，并撤回四个新增路径；本批没有发布或状态 schema 迁移。
