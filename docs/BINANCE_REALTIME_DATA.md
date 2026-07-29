# Binance USD-M 公共实时数据契约

## 范围与安全边界

本数据面仅获取 Binance USDⓈ-M Futures **公开市场数据**，服务于研究、前向 Paper
和 L2 干跑。它不读取 API key，不连接 private/user-data stream，不导入 broker SDK，
不提交、撤销或查询订单，也不授权 live trading。

固定前向宇宙为 14 个 USDT 永续合约：
`BTC ETH SOL BNB XRP ADA DOGE AVAX BCH DOT LTC UNI LINK ATOM`。

## 官方协议依据

- 开发者总览：<https://developers.binance.com/zh-CN/docs/introduction>
- USD-M REST 市场数据目录：
  <https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data>
- USD-M WebSocket 连接与路由：
  <https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect>
- Public 高频流：
  <https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public>
- Market 常规流：
  <https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market>

实现遵循新路由拆分：

- `wss://fstream.binance.com/public`：`bookTicker`、`depth20@500ms`、
  `rpiDepth@500ms`
- `wss://fstream.binance.com/market`：`markPrice@1s`、`aggTrade`、
  `kline_1m`、`!forceOrder@arr`、`!contractInfo`

连接在 23 小时后主动退出，由调度器轮换，早于官方 24 小时强制断开；异常采用上限
30 秒的指数退避重连。收到带 `st` 的迁移后事件时，只接受 `st=1` 的 USD-M 数据。

## REST 完整快照

`python -m tools.binance_realtime_market`

每 5 分钟对 14 个合约形成同一观察时刻的完整快照：

- mark/index/estimated-settle/basis/current funding/next funding
- 最近已闭合 1 分钟 mark-price、index-price、premium-index OHLC，以及同分钟
  mark-index basis
- 资金费率调整策略：cap、floor、interval；官方 adjustment-only 响应未包含某标的时，
  明确保存 `adjusted=false` 和空默认值，不猜测交易所默认参数
- best bid/ask、数量、mid、spread bps
- 最近已闭合 1 分钟 OHLCV、成交笔数、taker buy volume
- open interest 与 open-interest value
- global long/short、top-trader position long/short
- taker buy/sell ratio 与分侧数量
- 最近已结算 funding rate/time/mark

任一币种缺字段或超过对应 freshness gate 都不算完整；14/14 完整才返回调度成功。

## 官方 API 覆盖契约

`config/binance_usdm_market_data_coverage.json` 逐项登记官方 USD-M REST
Market Data 目录的 34 个接口。`python -m tools.binance_api_coverage` 会先由
`services/data/binance_api_coverage.py` 严格校验数量、唯一性、安全类型、状态和承载实现，
再生成机器可读状态。Data Catalog 直接读取同一契约，当前显示：

- 34/34 已解释（真实承载、等价冗余、不适用或因凭证边界阻断）
- 23 个接口直接承载当前数据/纸面流程
- 0 个未解释缺口；Account、Trade、User Data Stream 仍明确不在公开市场数据范围

该覆盖率是“官方市场数据接口的工程账本”，不是 Alpha、实盘或生产成熟度评分。Account、
Trade、User Data Stream 等私有接口不在本契约范围，也不会被自动工作器读取。

## 官方 WebSocket 流覆盖契约

`config/binance_usdm_ws_stream_coverage.json` 逐项登记官方 USD-M Public 5 类与
Market 15 类 WebSocket 市场流。`python -m tools.binance_stream_coverage` 通过
`services/data/binance_stream_coverage.py` 对数量、唯一性、路由、状态和承载实现做
fail-closed 校验。当前结果：

- 20/20 官方公开市场流已逐项解释
- 8 类直接承载当前实时数据面
- 9 类因固定 14 币宇宙已有等价且更窄的数据流而明确冗余
- 3 类不适用于当前单一 USDT 抵押的普通加密永续宇宙
- 0 个未解释缺口

新增承载面包括 `rpiDepth@500ms` 与 `!contractInfo`。RPI 差分逐事件验证
`U/u/pu` 并统计连续性、更新/删除档位及最近增量；完整 1000 档恢复基线仍来自独立
REST 快照。RPI 报价只作为流动性诊断，明确 `paper_fill_eligible=false`，不假设可成交。
`!contractInfo` 保留上市、结算、合约状态和可选 notional bracket 变更；它是变更触发型流，
连接后没有事件不等于故障。私有账户流、WebSocket API 下单和用户数据流始终不在本契约。

## 低频系统风险与参考市场

`python -m tools.binance_systemic_risk`

独立 30 分钟 worker 为 14 币前向宇宙保存：

- 官方 symbol ADL risk；真实响应中的 `MIDDLE` 原值保留，并规范化为 `MEDIUM`
- 官方可用的完整 30 天、1 小时 perpetual basis 历史；按已闭合小时增量水位补齐
- 指数成分交易场所、价格、权重、加权价格和跨场所离散度
- 保险基金覆盖合约、资产余额与交易所更新时间

全部记录进入 `binance_systemic_risk.db` 的 append-only SHA-256 链。首轮真实运行
14/14 PASS，basis 回填 10,080 个小时观测（720 × 14），且不把这些诊断字段直接驱动
晋级、仓位或订单。

该 worker 还承载 Binance 官方 IP 限流合同。根据
[USD-M General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)，
每个响应的 `X-MBX-USED-WEIGHT-*` 是当前 IP 使用量，HTTP 429 必须退避，继续请求会触发
HTTP 418 自动封禁。当前每币调用的四个端点权重分别为 ADL 1、basis 0、指数成分 2、
保险基金 1，因此 14 币一轮计划权重为 56。worker 固定单并发、请求间隔 250ms；
收到 429/418 时读取 `Retry-After`（缺失则使用 120 秒）并在本地打开整轮冷却，禁止同轮
继续撞限流。状态文件与 Data Catalog 同时展示计划权重、实际
`X-MBX-USED-WEIGHT-*`、并发、节奏、最近状态码及冷却剩余时间。

真实运行曾在旧的 8 并发实现下收到 HTTP 418，并在 11/14 时 fail-closed。改为上述合同后，
任务恢复为 14/14 PASS、零失败、哈希链有效；最近轮次计划权重 56，观测到的
`X-MBX-USED-WEIGHT-1M` 随同机其他公共 worker 共享并如实报告，不能误解为本 worker
独占消耗。

`python -m tools.binance_rpi_depth`

RPI 接口只有固定 1000 档、单币 IP weight 20。系统将其隔离为小时 worker，完整保存
交易所实际返回的 bid/ask 档位和源时间，并派生 spread、两侧名义深度和不平衡；不补造
不足 1000 档的一侧。首轮真实运行 14/14 PASS、哈希链有效。

## WebSocket 高频流

`python -m tools.binance_realtime_stream`

高频帧在内存中按币种归并，每 5 秒写入一份可消费快照。快照包含 20 档盘口、最优
盘口、RPI 差分窗口与连续性、mark/funding、当前 1 分钟 K 线、该窗口成交聚合、
强平聚合，以及最近发生的合约状态/档位变更。状态文件公开：

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
- `data/cache/binance_systemic_risk.db`
- `data/cache/binance_systemic_risk_status.json`
- `data/cache/binance_rpi_depth.db`
- `data/cache/binance_rpi_depth_status.json`

生产式本机 Paper 运行使用仓库外状态根。Console API 启动时设置
`QUANTOS_STATE_ROOT`，数据目录会选择该目录与仓库目录中 `generated_at_ms` 最新的
有效状态文件。WebSocket 状态超过 30 秒或 REST 状态超过 10 分钟即显示 stale，
不会把历史 `streaming=true` 的烟测状态冒充为当前在线。

Windows 调度：

- `QUANTOS_binance_realtime_market`：每 5 分钟
- `QUANTOS_binance_systemic_risk`：每 30 分钟
- `QUANTOS_binance_rpi_depth`：每 1 小时
- `QUANTOS_binance_realtime_stream`：开机启动 + 每日轮换

统一注册由 `scripts/register_schedules.ps1` 完成；调度入口与 watchdog 均在 L2
allowlist 中，治理测试禁止这些抓取器导入凭据、broker、订单或 live 执行模块。
