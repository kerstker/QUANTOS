# Rank #13 最小实施计划：真实缓存漂移读路径

1. 为 bitemporal 特征值存储增加只读截面时间序列聚合，修订值只取最新 `knowledge_time`。
2. 在数据 Console port 暴露缓存序列，异常或缺失时 fail-closed，不让 synthetic 数据冒充真实输入。
3. 在监控页复用既有 60/20 KS 内核，真实缓存可用时替换 synthetic `FEATURE_DRIFT` 行，并保留明确 provenance。
4. 补持久层、Console、监控契约和无缓存 fallback 测试；不增加网络、调度、告警发送或交易权限。
