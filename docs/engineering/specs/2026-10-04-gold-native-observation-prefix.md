# 原连续观察前缀接原研究登记

承接[公共原件分段](2026-10-04-gold-segmented-originals.md)和
[十环节完整目标](../plans/2026-09-28-ai-factory-closed-loop.md)。本增量关闭原
分块 K 线／逐响应报价到原 native Gold 输入的一段接口；完整交付保持进行中。

## 原件与状态

复用 gold_bar_session、gold_quote_session 和 gold_session_prefix.snapshot。
tools.gold_native_prefix.arguments 只离线消费原五文件冻结前缀、原连续 K 线
plan 字节和显式后到 exchangeInfo 收据。保存原 summary、inputs、sources 字节，
warmup／receipt 保持原字节；原 raw 消息、收据时间和逐条原决策按原游标复核。
不读取 sources 中的任何路径，也不把其中路径作为载入器。

原六文件 journal 明确新增 GOLD_BAR_FROZEN_PREFIX_SUMMARY_V1：状态
FROZEN_PREFIX、原因 COMMON_WATERMARK_FROZEN。这是共享水位下的观察前缀，
不是 OBSERVED／DURATION_COMPLETE；原已完成及 extended journal 语义保持。
冷读同时验证五文件摘要、共享水位、原消息／计数／决策、原连续 plan 和
warmup／receipt 在 sources 中的确切字节绑定。改外层摘要不能省略这些校验。

GOLD_PUBLIC_SESSION_PREFIX_V1 只与这种明确的 prefix journal 成对进入原
三候选／可选第四共享组合；原公共报价、资金费、成本、容量与 Decimal
账本继续承重。bootstrap／samples／可选日容量必须与原前缀相同，capture
必须绑定原 summary SHA，不能替换报价、添加隐式 flat-gap 政策或移除缺口。
原分段生产者也执行同一 binding，原追加历史／单账户交接不放松。

## 原登记入口

write_request 一次写原 quotes.json、journal、可选 funding.json／segments.json，
最后写原 plan.json。默认 register_original_trials=true，沿原 gold_bar_research
及 registered_gold_collection，不新增 job、数据库、账户或第五试验。
完整 segments.json SHA 按原 segmented_input 接线。已存在目录不覆写，存在
路径别名拒绝；准备不会提交任务或消费试验，执行和恢复沿原队列。

调用者可传入已在窗口前形成的 forward_design；只保存原声明，检查源码与配置，
后续原 native plan 仍核实际窗口、原登记时间和声明。没有声明时保持
NOT_PREREGISTERED，不能事后生成旧窗口时间或凭正确签名获得 OOS／准入资格。

当前源码必须与两类原采集计划的 source 相同，准备前后原五文件和源码均复核。
这只是本机来源绑定，独立 source/result/time authority 未因此建立。所有经济、
晋级及下单权限继续 false；旧 V1 保持隔离。

## 范围与剩余交付

保持原 16MB native 输入、100000 节点和 journal 20000 消息预算；原前缀字节
嵌入后仍计入总预算，超限拒绝。原诊断 prefix 的更大预算不移植进 native
入口。本增量适用于有限冻结前缀，原 collectors 自身仍最多一天；没有增加
后台全天／多日切片调度、fresh rule/funding 轮转、长期有界原件／结果协议或
真实30天单账户运行。多段请求的原输入支持不等于原采集已自动产生跨日合格分段。

完整系统仍须长期连续采集／流式协议、实际持仓信息区间与统计规模、全局
搜索显著性、实际成本／独立风险／时间及源结果信任、锁定 OOS／候选准入；
全部 writer 同 cut 停写和跨角色业务现金恢复、最终同版 ARM64／canonical
完整验收、获准现场安装升级恢复、五秒链和真实24小时成交—费用—资金费—
现金—再研究。机构异机延期不记 PASS，其他市场单列，盈利策略开发单列。

## 验证记录

九项新回归首先因原连接不存在失败。首轮9失败／21通过，失败原因为新合成
open-bar 事件时钟过旧；只把合成事件时钟放到其实际合成收据前，不改变生产
可知性门。修后原队列登记／恢复及相关31项通过／61.34秒，3063 Python
前后字节相同。原 plan 改写反例随后真实先失败（未抛异常）；补原 sources
中的 plan／warmup／receipt 精确绑定，红日志保留。collector 联调首轮52通过／
1失败，为合成 history 目录与原夹具重名；只改新夹具目录名，失败原件保留。
最终同版采集／前缀／回放／原队列登记恢复／声明／统计／隔离及 DGX 入口
联合173通过／255.75秒，driver256.237秒 exit0、3063 Python 前后字节一致。
原 Starlette 提示保留。新两文件完整 Ruff、三修改文件完整 Ruff 与父同为
零诊断；全仓 F821／E9、卫生／diff／20域52边无环服务图通过。各组不相加
称 canonical 全门。实际 hook、精确克隆与主区整合以 PROJECT_STATE 当前
头部和外部本任务记录为准。

新联调实际调用原两种 collectors 和 chunk／receipt 持久化，但底层传输使用
合成资料；它不是自然一天、真实公共覆盖、性能或盈利证据。原队列测试包括
无声明和两根 bar 的事前声明、原三个 trial、唯一 producer 调用、原库与报告
冷恢复；恢复不能新增计算。HTTP／前端 payload 未改，本增量不报前端新成绩。
