# First-Scope 档案与九引擎就绪测试材料修正

基于6c57f740556fbec656d349fb9d2c2e6d06cc7083，修改两个测试及本记录。
生产代码、身份算法、来源/语义一致性检查、九引擎守卫和交易授权均不变。

## 问题和修正

原6b同版完整门执行栈实际1 FAIL/9214 PASS/156 ERROR/52 SKIP，3381.00秒；
156个setup错误都在First-Scope档案初始化。fixture将PARENT锁在0d3f926d，
但归档/定义使用当前模块；当前Gold三个语义源码文件已改变，原工具正确
拒绝GOLD_BAR_DEFINITION_SEMANTICS_CHANGED，不能放宽该guard。

First-Scope是每批新生成的合成研究材料，fixture注释要求使用已审阅当前
Gold语义；现在明确锁到已冻结6c57f740，而非移动HEAD。该版运行源码与本轮
相同；旧BTC来源OLD_SOURCE=4673bc0a及其原签名/账本字段仍独立保留。
仍克隆具体提交、复读原档案并核对全部原字节/语义，不继承原错误档案结果。

另一FAIL为风险就绪正例仅提供stress provenance。九份原通过报告的守卫
已经要求完整集合及每份passed is True；原标签不是证据。合成接口正例补齐
报告，新反例覆盖缺报告、失败、整数伪装布尔、错误stress来源、额外引擎和
原labels_only缺陷。测试中隔离的上游authority mock不改变实际权威默认拒绝。

## 验证过程与边界

本轮当前版本原两案例实际1 FAIL/1 ERROR，2.37秒/driver2.555229793秒，
3138 Python摘要7fbc4a08前后保持；不是只从旧日志推定问题。修正后原第一
Scope来源/旧BTC链、七种风险材料及现有相同fingerprint仍拒绝语义改变的
反例共9 PASS，2.59秒/driver2.779704152秒，摘要4333b6db保持。

选取原13个失败模块，并补齐原First-Scope其他模块和Gold定义安全边界，
共18模块执行验证。两文件完整Ruff父/当前均65条原样式诊断、无新增；
全E9/F821通过。当前仅两个测试Python有差异，全部生产Python相同。

Main的原对象库不含6c57f740。复读旧固定对象时Git尝试promisor远端并拒绝；
未取得该对象。为使新的固定fixture在Main可复现，将只导入本轮已审阅的
本地codex开发分支历史，保留原HEAD、index和全部既有branch refs；不切换
工作树、合并或安装发行。导入和本机测试成绩在完成后另记，不先称通过。

Windows实际ACL修复/CheckOnly仍待回，实际trust未安装。本批是测试材料
资格修正，不能代替真实独立来源/结果/时间/OOS、现场现金恢复或Demo反馈。
完整目标ACTIVE，原四组、五秒链与实际24小时保持；原6b失败不追改。

18模块实际332 PASS，458.30秒/driver458.733983005秒，3138 Python摘要
4333b6db前后保持。完整第一Scope ancestry、exact continuity/policy、
compiled consumers、安装/CAS与中断恢复、原历史现金读者及Gold定义来源
安全边界均在该批选择范围内。原156 setup ERROR的13模块全部包含，保留
原失败；成绩是相关增量，不是完整发行门或真实签名/现金现场验收。

## 冻结与Main复现

冻结584145d59cb8cab3996ccb69286010f6ae783347；实际pre-commit运行，791文件
零违规，卫生通过。精确DGX基线8padu7ly通过，仅环境未--test。冻结版冷安装/
原BTC ancestry/七种风险材料/相同fingerprint语义改变拒绝10 PASS，7.39秒/
driver7.620020747秒，3138 Python摘要4333b6db前后保持。

为保证PARENT对象可达，本地fetch已审阅的codex/gold-first-scope-fixture-
coherence-20261006分支到Main对象库，tip584145d5；无tags/FETCH_HEAD更新，
全部原184 branch refs、MainHEAD913325cc及index95b9d9f1保持，没有checkout/
merge或提交Main。两个父文件精确比对/备份后，只整合两测试和本记录。
Main8005 Python摘要19ed8213，其他Python及现有权限保持，生产字节不变。

Main同一冷安装/ancestry/风险与语义边界实际10 PASS，8.23秒/driver
8.429599876秒，8005 Python摘要19ed8213前后保持。没有重复332批或把10项
称为整门；Source原332批及红例/原6b全门失败全部留存。

当前磁盘空闲417396666368，低于原完整验收启动余量442515849216字节；
不调小预留或验收预算。原canonical-tmp终态合成夹具逻辑字节15891406646，
后续先完整保存字节与元数据再整理终态夹具，实际公开输入、运行中的两个
collector、Source/原报告与保护状态保持。该存储准备不是现金现场恢复证明。
