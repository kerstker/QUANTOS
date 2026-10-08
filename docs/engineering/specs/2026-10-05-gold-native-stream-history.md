# 原Gold账户历史落盘与登记消费

承接[完整报告签名消费](2026-10-05-project-chunked-signed-consumption.md)，
父提交78ca783153dfb8efb773896841e91d8ba79823f1，
独立分支codex/gold-native-streaming-20261005。
继续[固定单市场F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)，
本增量不代表F1、S1或完整系统完成。

## 实现

原Gold生产者通过受信任离线history上下文复用SimulationHistory和
LinearPerpetualSimulationSession。现金基准、三原候选及可选共享组合
保留原账户、策略、风险和资金费逻辑；账户历史、策略观察/事件/trace及
增长的去重集合写入各账户私有SQLite。全部最多五账户共享明确磁盘预算。
完整原结果仍按原exact identity编码和原JSON语义消费，没有新增盈亏公式。
消费前后核历史数据库的物理身份、完整摘要及成员；关闭后历史视图拒绝使用。
初始数据库预算失败立即关闭连接，失败原件保留。

登记计划新增显式QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V2及对应FORWARD版本，
仅增加冻结history_storage；旧计划、16M输入、64段和元数据预算不变。
先原登记、PLANNED/RUNNING、唯一invocation，再从原execute thunk调用生产者一次。
存储上下文只负责bootstrap和关闭，持有全部账户至完整报告封存重验，
在成功outcome写入前关闭。生产失败保留原FAILED；发布或历史cut复验失败
保留中断/UNKNOWN。冷恢复完整验证已持久报告，不重算、不从scratch余额恢复。
原JobQueue只传invocation SHA，不接受路径、callback、trust或预算。
统计和签名请求识别新前向计划，继续消费全组原完整报告。

私有派生scratch由固定离线入口在/tmp/quantos-gold-history-*下创建，
成功和失败均保留。它不在正式run/cut内，不是来源、独立经济证据或恢复余额。
本辅助存储预算不能冒充整个工作空间的容量验收。

## 验证

缺GoldResearchHistory接口的两项红回归保留；数据库预算失败未关闭连接、
缺正式history_storage参数的红回归也保留。首批相关59通过/54.71秒，
driver54.995524秒；登记/归档首轮30通过/37.12秒，driver37.356756秒。
两组有重叠，不累加。完整原比较、全部五账户frames、资金费-.3、现金残差0、
关闭视图、整体磁盘预算和同字节改写/替换/成员异常均验证。

首次联合178通过/1520.41秒、driver1520.949195秒exit0、3092Python未变，
包含新V2实际原生产者/签名消费/冷读/非选中损坏及原HTTP/JobQueue签名流程。
它没有覆盖旧causal队列的execute观察钩子；随后该原回归1失败/5.13秒、
driver5.411277秒确认存储上下文绕过原execute thunk，保留失败日志。
修正只让上下文提供存储生命周期，原execute仍唯一调用生产者。

修正版最终相关48通过/523.98秒、driver524.246534秒exit0、3092Python未变。
包含新版实际签名消费/冷读/非选中损坏、原causal与五类segmented队列、
完整登记/归档及同invocation恢复；新版额外观察原execute也只调用一次。
隔离签名仍为INSUFFICIENT，经济/晋级/下单false。旧178结果与修正版48
分别记录，不称为同版全门。图/卫生另11通过/1.33秒、driver1.513744秒，
20域52边零环；全仓F821/E9及三个新Python文件完整Ruff通过。
修改范围原24条Ruff诊断无新增，不称为全部修改文件完整Ruff通过。
提交前实际hook、精确提交基线及主区整合结果按独立审计记录。

红绿日志/XML、冻结、原进程身份与整合记录位于本机
gold-project-closure-20261003-3pr5mrly/gold-native-streaming-ee5izx0g。
这些定向证据不替代最终源码的canonical全门。

## 剩余边界

本版策略Bar、完整input、prepared frames与部分段编排仍留在内存；
长期native输入仍受旧64段/16M编译限制。本批不是整个生产者内存上界，
不证明真实30天覆盖。策略Bar存储在后继独立目录继续，不借用该未完成版本。
实际独立来源/结果/时间及成本风险/OOS、正式整组停写恢复、最终同版
ARM64/canonical全门、获准现场安装升级恢复、五秒链与实际24小时
Demo—现金—再研究仍按既定F1–F5关闭。盈利策略随后研发，其他市场单列，
延期机构设施不记PASS。未部署、切服务、写保护状态、安装实际trust、
触碰凭据或下单。
