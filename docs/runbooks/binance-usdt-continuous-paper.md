# Binance USDT-M 连续纸面重测

该入口只使用 Binance 公共行情，不读取 `key/`，不调用下单接口，也不会改变
`L2_DRY_RUN_ONLY`：

```powershell
rtk python -m tools.binance_usdt_continuous_paper `
  --days 730 --aum 1000000 --fee-bps 4 --participation 0.10 `
  --metrics-workers 24 `
  --ledger data/cache/binance_usdt_continuous_paper_24m.db `
  --output data/cache/binance_usdt_continuous_paper.json
```

执行口径固定为三条冻结 Alpha（`carry_oi_majors`、`crowd_contra`、
`top_contra`）及等权组合，使用 USDT-M 永续的当前 `exchangeInfo` 合约步长、
最小数量、最小名义价值、当前 `bookTicker` 半价差、资金费和
Almgren--Chriss 临时/永久冲击。4 bps 是用户给定的 Binance taker 基准；没有
账户凭证时不会冒充某个账户 VIP 费率。

每个完整历史日仍按标记价计价，信号在周度时间轴重调仓。每次调仓的目标权重、
十进制整数步长数量、跳过原因和成交摩擦写入追加式哈希链：
`data/cache/binance_usdt_continuous_paper_24m.db`。重复运行同一窗口是幂等的；同一
决策时间出现不同内容会失败而不是覆盖历史。

报告中的 `risk_gate` 强制请求完整九引擎输入。USDT 成交量不是经授权的 CNY
容量输入，且当前公共回放不携带不可变研究目录的 Alpha 元数据和 PIT regime
标签，因此缺失输入会保持 `RISK_REJECTED/PENDING`，不会用换算值或默认值伪造
通过。`deployment_gate` 同时要求真人批准和版本内容哈希锁定；缺任一项即
`PAPER_ONLY_PENDING_FULL_DEPLOYMENT_GATE`。账本中的记录在该状态下仅为诊断回放，
不是已获授权的自动纸面订单，更不是实盘订单。
