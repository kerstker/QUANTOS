# Rank #13：只读监控告警适配规范

## 目标

把现有真实监控快照接到已有 append-only `alert_sink`，让漂移、数据新鲜度和审核 SLA 的
`WATCH`/`BREACH` 可被审计回放。该适配器不是风控门、晋级门或交易执行器。

## 诚实边界

- `SYNTHETIC_DETERMINISTIC`、`SYNTHETIC_FEATURE_VALUES`、`NO_HONEST_INPUT` 永不发出运营告警。
- 合成 Alpha 行只有在 payload 明确标记 `PARTIAL_ALPHA_DECAY_REAL` 时才可告警。
- 本地 `data/cache/alerts.jsonl` 仍是唯一权威记录；Telegram 只是可选通知，失败不改变记录。
- 本增量不提供实时 feed、阈值校准、独立外部监控或告警 SLA，因此不提升顶级成熟度分数。
