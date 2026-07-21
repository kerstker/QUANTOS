# Rank #13：PIT 特征缓存到漂移监控的只读接线

## 目标

让监控页使用 Rank #15 已经物化的 `FeatureValue` 缓存，而不是在缓存存在时继续生成 synthetic `FEATURE_DRIFT` 数值。该增量只打通读路径，保持诊断级别。

## 设计

- `SqliteFeatureValueStore.distribution_series()` 对每个事件时点取各品种最新已知版本，再计算截面均值；不重新计算因子，不访问网络。
- `DefaultDataConsolePort.enumerate_feature_distribution_series()` 只读取已通过泄漏审计的注册项，并在异常、缺表或缓存缺失时返回空结果。
- `monitoring_views` 复用已有 `evaluate_distribution_drift` 的 60/20 KS 内核。缓存存在时，`FEATURE_DRIFT` 的 metric、阈值、状态和 evidence ref 均来自真实缓存；无缓存时保留原 synthetic fallback。
- 结果标记 `PIT_FEATURE_TRAILING_KS_DRIFT_V1`，并附 `REAL_PIT_FEATURE_VALUE_CACHE_V1` 来源；`load_bearing=false`，不改晋级、仓位、干跑或订单。

## 边界

这不是实时监控、阈值校准或告警送达。历史 bundle 的最后 20 个事件只是可重放诊断窗口，不能证明当前市场状态或运营 SLO。
