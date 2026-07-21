# 特征级分布漂移证据（L2 诊断）

> **当前状态（2026-07-21）：** Rank #15 已把同一组三条特征物化到可重放的 bitemporal
> 缓存。本设计仍只定义诊断边界；实时/前向 feed、阈值校准和运营告警送达仍是开放项。

## 目的

把 Rank #13 从“只有数据集分布诊断”推进到一个可重放、可审计的特征级诊断，同时保持
L2 的安全边界：不接实时源、不改变晋级、仓位或订单决策。特征缓存由 Rank #15 的独立
离线填充计划负责；本设计不把历史缓存当作实时 feed。

## 数据与 PIT 约束

- 输入是已经封存并带 manifest 校验的 `evidence/datasets/fx_pit_tradable_v1.jsonl.gz`。
- `tools/fx_pit_bundle.py` 先验证 bundle、manifest、时间范围和 `knowledge_time <= event_time`，再构造 `FieldPanel`。
- 驱动器是离线只读的；没有网络、凭据、券商或下单路径。

## 特征与聚合

对每个截面计算三个只依赖历史 close 的因子：

1. `fx_momentum_20 = close / delay(close, 20) - 1`
2. `fx_volatility_20 = std(close / delay(close, 1) - 1, 20)`
3. `fx_distance_from_high_20 = close / rolling_max(close, 20) - 1`

每个事件时间只保留至少 3 个有效名称的截面中位数（`CROSS_SECTIONAL_MEDIAN`），缺失名称不
被填零或伪造。随后复用 `evaluate_feature_distribution_drift` 的不重叠 60/20 窗口 KS 内核。

## 证据与边界

`evidence/feature_distribution_drift_baseline.json` 记录 provenance、源 bundle/manifest 摘要、
面板范围、特征观察数、策略阈值和完整 assessment，并可用
`python -m tools.feature_distribution_drift_research --check` 确定性重放校验。

当前封存样本的诊断结果为 `BLOCK`（三个特征均超过当前 KS 阈值）。这是真实的研究警告，不是
交易阻断：制品明确 `load_bearing=false`，不会驱动晋级、组合、仓位、干跑或订单。实时/纸面
feed、定时 FeatureValue 缓存、阈值校准和运营告警仍是后续缺口。
