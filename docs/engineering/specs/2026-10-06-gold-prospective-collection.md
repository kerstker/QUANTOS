# 黄金预声明长期采集与原研究队列交接

状态：DEVELOPMENT_VALIDATION_ONLY，未发布或部署。固定交付范围沿用
[F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)；策略盈利随后单列。
本批继承 [长期声明容量](2026-10-06-gold-long-forward-design.md)，补原长期
采集编排，不增加市场、统计阈值或现场验收条件。

## 行为及原件

原月度公共观察没有绑定新的未来窗口声明，不能补写为前向预登记。
新 `tools/gold_forward_collection.py` 在未来窗口前保存固定源码、
V4 声明、候选选择、提案摘要及原采集/输入/准备/结果/历史预算。
选择必须在采集前冻结；全部候选试验仍沿原生产者留存。

`prepare --output DIR --request FILE` 独占创建声明目录；FILE 为本机
私有、受原读者保护的请求。`run --output DIR` 等待声明的 opening，
取得新鲜公共 history 后调用原配对 Bar/报价采集器。原始缺口、
网络错误与终态保留；迟到、回拨、源码改变、磁盘或原预算不足均拒绝。
已有 collection 不重启、不覆盖。`collect` 可仅采集，`finish` 冷读
完整原日志后进入同一研究流程；INCOMPLETE 不提交研究。

原 `gold_chunked_source.from_session` 流式冻结整个原报价/Bar输入，
仅首段加入可选原组合计划。旧默认输入保持原字节与账户结果。
随后复用原 registration、RunRegistry、JobQueue、trial ledger、
完整统计重算和 unsigned independent review request。新增计划文件
仅绑定这些原入口，不建立另一份账户、试验或状态数据库。

`recover --output DIR` 复核同一 raw 及原不可变 native plan，调用
原冷恢复；不重新准备带新时间的计划，不重建缺失原件，不重放成功
账户。原研究失败、拒绝或独立复核失败仍保存在原队列和原归档中。
协调结果保留原 run/request 标识，economic_authority 和
submission_enabled 恒为 false。

## 资源与验证边界

声明至少早于 opening 三分钟，采样十秒、原120样本段。
原32天/字节/消息/收据/段/环境预算不扩大，保留 Bar 收尾时间；
输入需显式原有预算版本，磁盘同时预留 raw、Source、Prepared、
结果、历史和索引。计划仍受原65KiB限制。

测试首先实际暴露入口缺失、原 Source 组合参数断点、独立复核要求
PREDECLARED_SELECTION、重新准备恢复计划的 EXISTING_BYTES 拒绝。
配对日志测试另暴露公共采集 JSON 与登记包格式不一致；协调器改用
公共采集原读者读取原字节，登记包读者和原 custody 限制保持。
所有失败原日志留存，不改成成功。

原两套 collector 的日志落盘与冷读测试使用明确合成网络、时钟、
持续时间和取消等待；并非真实长期网络实证。完整 NativeSource→
原队列→原账户→unsigned review→冷恢复测试使用新冻结的合成原件，
采集/编译交接是明确夹具。两组分别验证原读者和原研究消费者，
不能合并宣称真实长期端到端通过。

四组联合39 PASS、1项超大1001段测试未选，40.85秒；driver41.181283340秒，
3127 Python文件保持。未选案例仍保留在原全门，并非本批通过证据。

本批不证明真实三十天完整因果输入、历史 warmup PIT、独立时间/
信任、实际费率/风险校准或锁定 OOS。未切服务、写保护状态、
安装实际 trust、读取凭据或下单。现有原月观察继续保持其原源码、
进程和诊断标签，现场 [停写及恢复前置](2026-10-05-gold-post-pause-layout.md)
仍需实际关闭。
