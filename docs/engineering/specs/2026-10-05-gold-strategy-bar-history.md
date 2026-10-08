# 原Gold策略K线历史写入原会话存储

承接[原账户历史落盘与登记消费](2026-10-05-gold-native-stream-history.md)，
父提交206cfd3d898879cc85c0ccffebd97e13e8640b55。
独立分支codex/gold-bar-history-20261005，继续[固定F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)。
本增量不代表完整S1、F1或系统完成。

## 实现

原SimulationHistory固定typed记录集合增加Bar，只编码已知类型和有限Decimal，
不反序列化可执行对象。原GoldResearchHistory在首次回调前将三候选和共享组合
的完整Bar历史写入各自原会话SQLite。策略仍使用原因果指标状态、原订单/风险/账户；
扩展视图先验证收到的后缀，再按原信号状态推进后保留Bar记录，不在每次追加前
复制全部旧tuple。封存状态在原指标变化前拒绝追加，关闭后历史视图不可用。

旧内存路径和原非单调receipt完整历史fallback保持。fallback仍材料化完整tuple，
不能宣称任意receipt输入都具有相同内存上界。没有截断EMA、Bar历史或完整输入摘要。
源bar_journal、prepared输入和组合dataset_identity仍按原路径加载；
策略端存储不代表整个生产者已具有内存上界。

等待前批联合验证时，先复制其明确十五路径到独立无alternates副本，
前批受检源码保持冻结。前批提交确认后，仅对本任务自有副本的Git index
对齐206cfd3d父版，4475个工作文件字节保持；恢复唯一此前尚未生成的父版文档。
本版最终只有四个原Python文件、一个新回归和本文的增量，不复制主区改动。

## 验证

Bar历史仍为tuple的红回归保留。完整原比较、全部四个策略历史、资金费、
原结果identity及关闭边界验证。封存追加原先改变CausalGoldBarEvaluator再拒绝
的红回归保留；修后在原状态变化前拒绝。重复Bar拒绝后原SQL前缀和信号状态保持。

增长负载为明确合成2800根Bar、2401次无quote回调。完整原指标与输入摘要一致，
Python heap峰值小于4M，pending归零、指标窗口最多41。
这是回调/存储负载，不是真实30天行情、成交、费用或资金费证据。

首批相关53通过/52.31秒，driver52.613104秒。增长组2通过/1失败是错误码
预期与原BAR_RECEIPT_ALREADY_PROCESSED不匹配，按原拒绝语义纠正，日志保留。
扩大组63通过/1失败定位前批新增native上下文绕过原execute thunk；前批和本副本
均修正为上下文只负责bootstrap/关闭，原生产者仍从原execute唯一调用。
最终相关70通过/98.19秒，driver98.456281秒exit0、3093Python未变。

该版实际新V2原生产者→完整签名消费→恢复/冷读→非选中原件损坏拒绝
单独1通过/418.46秒，driver418.742405秒exit0、3093Python未变。
这是对齐父提交之前的同字节源码测试，独立fixture签名仍INSUFFICIENT，
经济/晋级/下单false；不称为正式新提交的canonical全门。
各版结果有重叠，不累加。导入图仍20域52边零环；全仓F821/E9、
新回归与存储助手完整Ruff、卫生/diff通过。提交hook、精确提交基线和
主区整合结果按独立审计记录。

证据位于本机gold-project-closure-20261003-3pr5mrly/gold-bar-history-a96pc240，
包括逐版红绿日志/XML、父版对齐的全工作字节核对及实际消费结果。

## 剩余

whole input、prepared frames、段编排及组合dataset_identity仍会累积或材料化；
长期native输入仍受旧64段/16M编译限制。真实30天协议、独立源/结果/时间/
成本风险和OOS、正式整组停写恢复、同版ARM64/canonical全门、获准现场
安装升级恢复、五秒链及实际24小时Demo—现金—再研究仍按F1–F5关闭。
盈利策略随后研发，其他市场单列，延期机构设施不记PASS。
未部署、切服务、写保护状态、安装实际trust、触碰凭据或下单。
