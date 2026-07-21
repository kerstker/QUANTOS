# Rank #15：PIT 特征值缓存填充与只读接入

## 目标

把已经存在的特征定义、PIT 数据和 `FeatureValue` bitemporal 存储接成一个可重放的离线填充闭环。这个增量只解决“有真实缓存可读”这一缺口，不把诊断结果伪装成晋级或交易能力。

## 设计

- `tools/feature_cache_research.py` 从封存的 FX PIT bundle 重放现有的三个特征：`fx_distance_from_high_20`、`fx_momentum_20`、`fx_volatility_20`。
- 每个值写入 `SqliteFeatureValueStore`，注册信息写入 `SqliteFeatureRegistryStore`；版本固定为 `fx-pit-v1`，代码版本固定为 `feature_cache_v1`，泄漏检查结果写入审计字段。
- 来源 bundle 已满足 `knowledge_time <= event_time`；物化时使用事件边界作为 `knowledge_time`，并通过 `as_of()` 的边界回读验证没有前视。
- 临时 SQLite 文件完成验证后再原子替换 `data/cache/fx_feature_store.db`。缓存可删除并从 bundle 重新生成，不属于提交制品。
- `evidence/fx_feature_value_cache_baseline.json` 只保存摘要、内容哈希和诚实边界。默认驱动写缓存；`--check` 只做内存重放和证据校验。
- Console 只读探测缓存；缓存存在且非空时，`data-catalog` 返回 `REAL_PIT_FEATURE_CACHE` 和 `PERSISTED_PIT_FEATURE_CACHE`，否则保持原有 synthetic/offline 标记。

## 当前结果

基线为 3 个特征、18 个品种、7,036 个事件时点、377,237 个物化行，持久化重开后摘要和 SHA-256 内容指纹一致。该数据仍是历史研究缓存，`load_bearing=false`。

## 明确不包含

没有实时或纸面 feed、定时刷新、阈值校准、运营告警、晋级、仓位调整、干跑或订单路径；这些仍是后续独立工作，不因缓存存在而自动放行。
