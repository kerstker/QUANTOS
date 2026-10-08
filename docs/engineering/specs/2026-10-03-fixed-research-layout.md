# 固定研究清单与实际项目成员绑定

状态：已实现并通过专项验证的开发增量；提交与整合状态见
[本批记录](2026-10-03-project-statistics-and-research-recovery.md)。本模块不安装清单。
承接[风险附件与原件恢复](2026-10-03-research-risk-and-cross-role-recovery.md)。
完整研究角色、四角色同一切面及真实停写屏障继续开发；本记录不是完整交付验收。

## 固定原件与身份

[清单读者](../../../tools/demo_gold_research_layout.py)只读取固定
`/etc/quantos/research-checkpoint/layout.json`。原件必须由root持有、单链接、
模式0644，所有父目录经原描述符读者核验。它绑定实际源码、账户和原registry摘要，
仅在已验证的fixed-public句柄及同一operation、四角色cut下捕获。
原JSON或一个自报摘要不能替代该句柄；模块不创建安装目录或配置。

新`GOLD_RESEARCH_INSTALLED_LAYOUT_V1`保留全部原`research_uids`，每个UID必须
有且仅有一个明确root、原`GOLD_RESEARCH_CHECKPOINT_LAYOUT_V1`及project containers。
不任选首UID，也不把同一原根赋给两个角色。根和组件不得重叠或别名；身份、原路径、
有限种类及项目对应原registry继续使用研究原生合同。

每个`project_collections`实际枚举全部直接成员，并严格与清单中PROJECT条目比较。
遗漏项目、删原目录、插入新项目、目录或配置同字节换inode、权限变化、source/account/
registry变动均拒绝。成员数与节点数在完整materialization之前限制；原registry只读核
文件边界，实际SQL人口与账本内容仍交原研究读者验证。

## 消费与剩余边界

`capture(verified_public, public_bundle, cut_request, guard)`返回操作内句柄，提供
`snapshot()`、`layout_for_uid(uid)`、`root_for_uid(uid)`和`recheck()`。
快照为独立副本；UID必须明确匹配，布尔别名与未知UID拒绝。
复查再次核固定公件、实际配置、全部目录及项目成员，不能使用缓存资格。

root协调器须在降权前取得原布局，并把原root与实际捕获记录交给受控role协议。
异路径恢复只能保留原根作历史引用比对，不改写原签名或读取旧绝对目录。
该协议及归档实际索引绑定尚待接线；本阶段不从归档JSON生成新捕获句柄。

`configured_project_roster_verified`仅说明配置与实际项目容器已绑定。
writer barrier、native research、transport、whole set及economic authority均为false。
`guard`回调不是停写证明；清单没有证明所有直接CLI、runner与反馈worker都已排空。
研究全原件语义、固定writer roster、实际锁/进程屏障、四角色receipt和完整transport
仍须在整套消费者中结合验证。当前机器上是否安装该新清单未验证；本批未读取现场配置。

## 实际验证

[测试](../../../tests/tools/test_demo_gold_research_layout.py)初次因缺新模块出现22项
setup errors，原日志保留；实现后22通过，补多UID和真实原项目组合后24通过/4.98秒。
权限及accepted-release接缝在临时目录隔离；实际原公件/partition算法、描述符、目录成员、
inode、原registered producer、SQLite/ledger及人口校验执行，不读取真实凭据或发送订单。
部分测试库边界只检查元数据，不将那些样本声明为已核原SQL语义。

最终在独立Git对象副本验证：基准`27437aa4edd85ecb128399ac14f309f24541be54`，
明确8文件overlay（本模块/测试及当时6个研究关系依赖）；不是该HEAD自身已包含新代码。
三组联合120通过/32.30秒，2986个Python文件前后摘要相同；本批完整Ruff、仓库卫生及
治理扫描通过（756文件、0违规）。审阅副本/输入摘要/原日志/结果在
`gold-project-closure-20261003-3pr5mrly/root-research-layout`。

独立只读审阅未发现该范围可行动新阻断，仍明确不证明writer barrier或whole-role恢复。
原5ae完整门30 PASS/1 FAIL/1 TIMEOUT保持；本开发增量未运行完整发布验收，未部署。
