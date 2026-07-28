# Binance USD-M 公共实时数据契约

## 范围与安全边界

本数据面仅获取 Binance USDⓈ-M Futures **公开市场数据**，服务于研究、前向 Paper
和 L2 干跑。它不读取 API key，不连接 private/user-data stream，不导入 broker SDK，
不提交、撤销或查询订单，也不授权 live trading。

固定前向宇宙为 14 个 USDT 永续合约：
`BTC ETH SOL BNB XRP ADA DOGE AVAX BCH DOT LTC UNI LINK ATOM`。

## 官方协议依据

- 开发者总览：<https://developers.binance.com/zh-CN/docs/introduction>
- USD-M WebSocket 连接与路由：
  <https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect>
- Public 高频流：
  <https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public>
- Market 常规流：
  <https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market>

实现遵循新路由拆分：

- `wss://fstream.binance.com/public`：`bookTicker`、`depth20@500ms`
- `wss://fstream.binance.com/market`：`markPrice@1s`、`aggTrade`、
  `kline_1m`、`!forceOrder@arr`

连接在 23 小时后主动退出，由调度器轮换，早于官方 24 小时强制断开；异常采用上限
30 秒的指数退避重连。收到带 `st` 的迁移后事件时，只接受 `st=1` 的 USD-M 数据。

## REST 完整快照

`python -m tools.binance_realtime_market`

每 5 分钟对 14 个合约形成同一观察时刻的完整快照：

- mark/index/estimated-settle/basis/current funding/next funding
- best bid/ask、数量、mid、spread bps
- 最近已闭合 1 分钟 OHLCV、成交笔数、taker buy volume
- open interest 与 open-interest value
- global long/short、top-trader position long/short
- taker buy/sell ratio 与分侧数量
- 最近已结算 funding rate/time/mark

任一币种缺字段或超过对应 freshness gate 都不算完整；14/14 完整才返回调度成功。

## WebSocket 高频流

`python -m tools.binance_realtime_stream`

高频帧在内存中按币种归并，每 5 秒写入一份可消费快照。快照包含 20 档盘口、最优
盘口、mark/funding、当前 1 分钟 K 线、该窗口成交聚合和强平聚合。状态文件公开：

- 双连接状态与重连次数
- 最后接收时间和接收延迟
- 14 币核心字段覆盖率
- 分事件累计计数与拒绝帧
- SQLite 行数及哈希链验证
- 无凭据、无 private stream、无 broker、无下单的安全声明

## 制品与运行目录

默认仓库开发制品：

- `data/cache/binance_realtime_market.db`
- `data/cache/binance_realtime_market_status.json`
- `data/cache/binance_realtime_stream.db`
- `data/cache/binance_realtime_stream_status.json`

生产式本机 Paper 运行使用仓库外状态根。Console API 启动时设置
`QUANTOS_STATE_ROOT`，数据目录会选择该目录与仓库目录中 `generated_at_ms` 最新的
有效状态文件。WebSocket 状态超过 30 秒或 REST 状态超过 10 分钟即显示 stale，
不会把历史 `streaming=true` 的烟测状态冒充为当前在线。

Windows 调度：

- `QUANTOS_binance_realtime_market`：每 5 分钟
- `QUANTOS_binance_realtime_stream`：开机启动 + 每日轮换

统一注册由 `scripts/register_schedules.ps1` 完成；调度入口与 watchdog 均在 L2
allowlist 中，治理测试禁止这些抓取器导入凭据、broker、订单或 live 执行模块。
