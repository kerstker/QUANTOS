# L2 纸面订单预览操作缝

`tools.paper_order_preview` 是当前版本最短的可用执行路径。它从
`paper_track.db` 读取最新一条经数据准入认证的待实现决策，用本地
`crypto_perp_mark_prices.json` 的决策时点永续标记价格构造目标手数，然后
依次经过 `PreTradeRiskGate`、`DryRunBrokerAdapter`、纸面成交和 PnL 对账。

运行：

```powershell
rtk python -m tools.paper_order_preview
```

可用参数：`--alpha crowd_contra|carry_oi_majors|top_contra|all`、`--aum`、
`--cost-bps`、`--output`。默认只处理 14 个大盘币，默认纸面名义资金为
`1,000,000`，不代表真实资金。

输出 `data/cache/paper_order_preview.json` 是原子写入的只读操作证据，包含
每条 Alpha 的目标订单、批准/拒绝数、纸面成交数、对账残差和运行哈希。命令
不会读取 KEY、访问 Binance 私有接口、连接券商、写入 forward realization，
也不会改变任何 live 开关；`live_allowed` 与
`order_submission_allowed` 永远为 `false`。多个未实现的认证决策会直接阻断，
避免跳过待处理调仓。成功的纸面调仓还会按 Alpha/决策时间幂等写入
`data/cache/paper_order_ledger.db` 的哈希链；下一条新决策从该 Alpha 的纸面持仓
开始，重复运行同一决策只重放已有快照，不重复制造成交。
