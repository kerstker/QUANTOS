# Rank #13 最小实施计划：真实监控告警适配

1. 复用现有 `monitoring-drift` 只读快照和 `tools/alert_sink.py`，不新增数据源或交易入口。
2. 只对真实 provenance 的 `WATCH`/`BREACH` 行生成稳定文本；`SYNTHETIC_*`、`NO_HONEST_INPUT` 和合成 Alpha 行直接跳过。
3. 保留本地 append-only 台账作为权威记录；Telegram 仍是可选、失败软化的通知通道。
4. 用纯函数测试过滤边界，用注入 emitter 测试驱动器，不启动后台进程、不写入版本化证据。

## 验收

- 告警适配器不导入券商、订单或 live 权限。
- `live_authorization_allowed` 恒为 `false`。
- 50 项针对性测试通过，治理与供应链基线保持 current。
