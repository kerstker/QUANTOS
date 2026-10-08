# 工厂F4：再研究反馈的成熟时间

现有research_trigger已由factory_campaign调度器和生命周期页面消费。原检查仅要求available_at_ms不晚于调用now，未核对标签时段、四小时成熟时刻及生成观察快照时是否已可用。

三个回归先实际失败：未来slot、提前成熟的标签、观察快照之后才可用的标签，原实现均可能触发RESEARCH_CHALLENGER。修复后要求整数非负时间、观察快照新鲜，并满足slot + 4小时 + 5分钟入场延迟 + 1分钟参考行情收盘 <= available_at_ms <= observed_at_ms <= now。成熟延迟对应现行ai_factory_forward的四小时协议；非通用多期限合同，新增期限必须显式版本化。缺失/不符返回LABEL_TIME_UNVERIFIED，不申请预算。

原鲜度测试夹具将所有标签时间设在1000ms、却包含未来slot；现改为真实成熟顺序，保留新鲜、过期、样本不足三种断言，没有删除旧测试。新增真实FactoryStore及调度器集成测试：未成熟时rounds为空、队列不提交；修正为成熟反馈后只保留一次研究预留并提交一次。队列使用记录桩，不启动真实模型、观察或交易。

lifecycle/campaign/forward/forward_worker四组23项通过，Ruff E9/F821通过。未部署、未完成新全量验收，未建立真实场所现金反馈。本增量只补强原标签触发路径；不自动提升策略、不改实盘参数，不使用锁定OOS调参。
