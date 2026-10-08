# 长期预算接通原正式登记、统计与评审

承接[原单账户长期执行预算](2026-10-05-gold-long-replay-budget.md)，继续固定
[F1–F5完整交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。原登记路径
已接通显式长期预算；真实30天输入与完整现场交付仍须验收。

## 冻结与执行

新 `QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V2` 与 `QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2`
必须冻结相同的replay_budget。正式计划使用 `QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V4`
或对应FORWARD版本。预算、输入、原manifest、试验定义及计划身份关联；旧计划不能
换成新storage，预算不足在创建计划/试验前拒绝。旧V1输入、V1 storage和V3计划保留。

原NativeSource从完整原件重建原输入引用，再绑定显式新预算；复制的原件及manifest
不改写。原登记、RUNNING、唯一生产者、原trial与现金账本沿用；新dataset V3、
模拟V4保留预算。冷恢复消费原完整报告，不重算交易、不新建账户或试验。

项目切面、完整结果读者、资金费统计和独立评审核验输入/storage/报告内各原账户的
同一预算及版本，缺失、错配或降级拒绝。评审请求及HTTP归档外层仍为原V3；控制台
的project_cut保持原Record合同，实际预算和原件随该切面传递，不新增外层字段。

## 验证与资源声明

三项新登记回归先失败；修正后新/旧原件登记与恢复9通过/38.21秒。
明确合成1001段正式队列执行四原账户，完整报告、项目切面及冷恢复通过；旧默认
1000环境预算没有扩大。该样本时间跨度约4.17小时，不是自然30天观察。

首次大样本因4096个4096字节分块发布失败，保留INCOMPLETE、16781334字节与
全部4096分块及失败试验。随后仅调整该夹具显式chunk_bytes为65536；结果预算
256000000、索引/history/准备各512000000，原件预算64000000，未改全局分块上限。
修正后1通过/381.11秒（driver381.442496秒、3105 Python前后保持）。

新FORWARD预算的Console实际载荷/完整归档/损坏409，以及原独立签名生产消费、
恢复、冷读、非选中结果及输入损坏联合2通过/435.82秒（driver436.283585秒、3105
Python保持）。该检查发生在大样本夹具分块参数修正前；之后没有改生产行为代码。
签名为隔离测试临时来源，authentication=VERIFIED_SOURCE_AND_RESULT、qualification=
INSUFFICIENT，经济/晋级/下单false；没有安装实际独立authority。

九文件兼容批119通过/394.29秒（driver394.793120秒、3105 Python保持），覆盖原登记、
完整报告、原件评审、资金费/统计、结果投影、声明、图与卫生。各批有重叠，不累加
为canonical。新合同/测试完整Ruff及全仓F821/E9通过，十四修改Python原94诊断不变。
Starlette提示保留。审计与红绿日志位于gold-project-closure-20261003-3pr5mrly/
gold-long-native-20261005。

有界盘点现存公共历史摘要：8份，最长15.625天，historical_pit_proven均非true。
盘点不涵盖全局前向数据，历史区间不能替代实时报价/规则/资金费的可知时间证明。

## 继续交付

下一项是原持续采集到完整30天输入的实际覆盖、预算与跨日/资金费边界验收。
独立源/结果/时间、实际成本风险、锁定OOS与候选准入；正式全writer同cut恢复、
最终同版ARM64/canonical全门、获准现场安装/恢复、五秒链及实际24小时Demo—现金—
再研究仍须完成。最新完整门仍为旧5ae版30 PASS/1 FAIL/1 TIMEOUT。完整目标ACTIVE，
盈利策略随后单独开发，其他市场另列，机构异机延期不计PASS。
