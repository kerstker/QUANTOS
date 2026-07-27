# MEXC Phase 8 设计与 M 系列切片计划(727 §13 阶段 B)

- 日期:2026-07-27
- 依据:[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §13(MEXC BTC 现货接入顺序:
  §13.1 适配器层次、§13.2 阶段 B Account Read-Only、§13.3 订单幂等、§13.4 未知
  订单状态、§13.5 启动恢复)、Phase 8(「MEXC 公开行情、只读账户与 Test Order」,
  验收:无真实下单 / 可从交易所重建账户状态 / 丢包和断线可检测)、§18.2(provenance
  标签)、[`docs/MEXC_INTEGRATION.md`](../../MEXC_INTEGRATION.md)、
  [`2026-07-27-shadow-phase7-design.md`](2026-07-27-shadow-phase7-design.md)
  (Phase 7 SHADOW 侧;MEXC 现货 shadow 化是 §13 阶段 A 的单列任务)。
- 状态:**M1 本提交落地**(只读账户端点三件套 + commission 解析 + 治理登记 +
  文档漂移修正);M2(Test Order)/ M3(unknown 持久化 + 启动对账)为后续切片。
- readiness:L2_DRY_RUN_ONLY 不变。M1 不新增任何下单路径(三个端点全是签名
  **GET** 只读),mainnet 构造闸门逐字节不动(未 arm 的 mainnet 构造即
  `SafetyViolation`),不触碰凭证;网络 import 仍只在
  `live/exchange/{mexc_spot,binance_futures,bybit_futures}.py`(mexc 纯 urllib,
  AST 治理钉死);`services/` 不 import `live/`。

## 1. 现状:单适配器 vs §13.1 的四层愿景

§13.1 的适配器层次是四个类:

```text
MexcPublicMarketDataAdapter
MexcAccountReadOnlyAdapter
MexcSpotTestOrderAdapter
MexcSpotBrokerAdapter
```

今天的现实是**一个** `MexcSpotAdapter`(live/exchange/mexc_spot.py)同时承载公开
行情(exchange_info/exchange_filters/book_ticker/depth/sync_clock)、签名只读
(account/positions)、签名写(place_order/get_order/cancel_order)三块能力。

**设计裁定:本阶段不切类。** 四层是**部署期**的能力拆分——用独立的构造面让
「只有只读权限的部署形态」在类型层面就拿不到写方法,与 `LiveBrokerAdapter` 的
接缝拒绝对称。在 L2 今天:mainnet 构造闸门(`live_trading_armed`)已经结构性
锁死一切实例化,写方法的存在不构成可达风险;把一个 391 行的已测适配器拆成四
个类是纯 churn,不增加任何机器强制的安全性质(治理钉的是网络 import 位置、
withdraw 字面量缺席、构造闸门——三者都与类数量无关)。**四层拆分留作阶段 D
(Micro-Live Broker Adapter,Phase 9)的部署期任务**,届时切分才有真实的承重
语义(独立配置/独立子账户/IP 白名单/Deployment Grant 只对 broker 层生效)。
本设计文档把 §13.2 阶段 B 的能力清单按 M 系列切片落到**现有单适配器**上,逐片
标注它与未来四层中哪一层对应,切类时按标注平移即可。

## 2. 设计裁定

1. **fail-loud 读、绝不静默跳过——与 book_ticker/depth 的 fail-soft 相反,刻意
   分层。** 行情读(book_ticker/depth)拿不到就回退 None,缺一个盘口快照不打断
   流程;但 `open_orders`/`all_orders`/`my_trades` 喂的是**对账**——静默跳过
   一行等于把交易所侧的真实挂单/成交从对账视野里抹掉(账本说无单、交易所说有
   单,漂移不报错,正是 §14「交易所是权威来源」要抓的情形)。所以:非 2xx →
   RuntimeError(带 status,镜像既有 `account()` 风格);响应非 list / 任一行
   必填字段缺失或类型不符 → RuntimeError(`malformed exchange row/trade row`)。
   由调用方(M3 的对账器)显式处理异常,不在适配器层吞。
2. **三端点不进 VenueAdapter Protocol——沿用 depth()/premium_index 的 hasattr
   可选能力惯例。** Protocol 是「一个完整 venue 必须有什么」的最小集合
   (tests/live/test_venue_base.py 钉死);只读对账输入按 venue 各自提供,调用方
   `hasattr(venue, "open_orders")` 探测、缺了干净跳过。理由与 Phase 7 设计文档
   §2.2 逐字同理。
3. **MEXC 窗口诚实注记进 docstring,不做超出文档口径的承诺。** `allOrders` 只
   覆盖**最近 7 天**、`myTrades` 约**最近 1 个月**(公开 v3 文档口径)——更早的
   历史靠本地账本/成交回填,适配器不承诺全量历史;`openOrders` 是当前快照无窗口
   问题。这三个窗口共同决定 §13.5「断连恢复用 REST 回填」的可行域(见 §4)。
4. **ExchangeFilters 加法扩展 commission,其他 venue 诚实 None。** MEXC
   exchangeInfo 按 symbol 携带 `makerCommission`/`takerCommission`(零手续费现货
   对核心策略是真实成本项,§13.5「加载交易规则和真实费率」的费率输入)。
   `ExchangeFilters` 是三 venue 共享契约 → 新增 `maker_commission` /
   `taker_commission` 两个 `Optional[float] = None` 尾随字段(默认 None → 既有
   全部构造点/测试零漂移);MEXC 解析填充,Binance/Bybit 不解析、留 None——
   **缺席 ≠ 免费**,绝不把字段缺席硬凑成 0(`_parse_optional_float`:缺失/不可
   解析 → None)。
5. **规范化是最小化、非建模。** 返回 `list[dict]`(不新建 dataclass——M3 的
   对账器才是消费方,届时按对账需要建模,适配器层只做字段重命名 + 类型落地):
   order 行 {order_id, client_order_id, symbol, side, type, status, price,
   orig_qty, executed_qty, transact_time_ms};trade 行 {trade_id, order_id,
   client_order_id(缺席诚实 ""), symbol, side(从 isBuyer 推导), price, qty,
   quote_qty, commission, commission_asset, time_ms, is_maker}。
6. **确定性核心、测试零真网络。** timestamp 仍全部来自注入 clock + offset;
   测试复用既有 `_FakeTransport` path→(status, json) 表 + `_ARMED` 显式全开
   SafetyConfig fixture + `_verify_signature` 重算 HMAC——三个端点的每个测试都
   经假 transport,零真网络。

## 3. M 系列切片计划

| 切片 | 内容 | 对应 §13.1 层 | 状态 |
|---|---|---|---|
| **M1** | `open_orders(instrument_id=None)`(GET /api/v3/openOrders,签名,可选 symbol)+ `all_orders(instrument_id, *, limit=500)`(GET /api/v3/allOrders,symbol 必填,limit 钳 [1,1000],**7 天窗口**)+ `my_trades(instrument_id, *, limit=500)`(GET /api/v3/myTrades,limit 钳 [1,1000],**~1 月窗口**)——全 fail-loud;`exchange_filters()` 附 maker/taker commission 解析(ExchangeFilters 加法扩展,缺席 None);治理补登记 `tools/mexc_readonly_smoke.py` + `tools/mexc_klines_fetch.py` 入 `_FETCHERS`;`docs/MEXC_INTEGRATION.md` §3 方法面漂移修正(原列的 `get_open_orders`/`get_account`/`get_klines` 不存在) | MexcAccountReadOnlyAdapter(+ PublicMarketData 的费率部分) | **本提交**(适配器 +14 测试,45→59) |
| **M2** | Test Order(§13.2 阶段 C):签名/时间戳/recvWindow/精度校验/minNotional/client_order_id/permission check 全链演练但**不得进入撮合**(MEXC 无 Binance 式 `/order/test` 端点——用 minNotional 之下的必拒单或独立子账户极小单 + 立即撤单实现,具体形态在 M2 切片内评审);**治理钉死 test-order 绝不在下单链上**(`submit_live_batch`/ops API argv 不可达,与 ops API「argv 绝不 `--submit`」同款结构性封锁) | MexcSpotTestOrderAdapter | 未做 |
| **M3** | §13.4 未知订单状态持久化(ORDER_SUBMISSION_UNKNOWN → deployment 级停新单 → client_order_id 查询接管 → 确认不存在才允许重提 → 无法确认则 Kill Switch + 人工;**不得盲目重试**)+ §13.5 启动对账(时间同步 → 交易规则+真实费率 → 余额 → **openOrders** → **myTrades** 近期成交 → 重建本地账本 → 校验事件链 → 对账 → 才允许新订单——M1 三端点是它的交易所侧输入) | 四层共用 + Broker 层消费 | 未做 |

切分原则:每片独立可测、不依赖后片——M1 是纯只读能力(无消费方也成立,
因为它同时是文档漂移修正与治理缺口的载体),M2 复用 M1 的签名/查询面做
round-trip 验证,M3 是唯一消费 M1 三端点的切片(对账器)。

## 4. 裁定:User Stream / WebSocket 推迟(立据)

§13.2 阶段 B 清单含 `user stream` / `listen-key renewal` / `REST backfill after
disconnect`;Phase 8 任务清单同样列了 User Stream 与断线补账。**本方案裁定:
user stream/listenKey 推迟到 M3 之后的独立切片,REST 回填先行。** 依据:

1. **与既有全仓裁定一致。** `docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md`
   已把「无 websocket」定性为**明确设计选择**——「weekly strategy 不需要」,
   诚实地说不是差距。MEXC 现货承载的 `btc_spot_trend` 同样是日/周频策略,决策
   周期以天计,user stream 的毫秒级推送对该策略没有任何承重语义。
2. **REST 三件套等价覆盖断连恢复。** §13.5 启动恢复需要的交易所侧事实——
   当前挂单(openOrders,无窗口)、近期订单(allOrders,7 天)、近期成交
   (myTrades,~1 月)——REST 全能拉到;策略节奏(日/周)远小于最短的 7 天
   窗口,断连后用 REST 回填在数学上覆盖任何可达的缺口。user stream 在此只是
   延迟优化,不是正确性前提。
3. **listenKey 是一套独立的运维状态机**(POST 创建 / PUT 续期 30min / DELETE
   关闭,ws 连接 + 重连 + gap detection),把它和 M1 的只读 REST 混在一个切片
   会把「已验证的签名 REST 面」和「未验证的长连接运维面」耦合进同一次评审。
   listenKey REST 三端点 + ws 消费留作未来切片(若届时策略频率或 §13.5 的
   「建立 WebSocket」步骤被重新评审为承重,再单独立项)。

## 5. §13.3 client_order_id 公式 vs 现有实现——字段映射差异诚实表

§13.3 规定:`client_order_id = hash(deployment_id, alpha_version, signal_time,
rebalance_id, symbol, side, intended_quantity)`。现有实现
`live/execution/filters.py::client_order_id(strategy_id, decided_ts,
instrument_id, side, quantity, limit_price=None, prior_qty=None)` =
`decision_prefix(strategy_id, decided_ts, instrument_id) + sha256(side|quantity
[|P prior][|L limit])[:16]`。**不声称逐字段等价**,差异如实记录:

| §13.3 字段 | 现有实现对应 | 差异 |
|---|---|---|
| deployment_id | strategy_id(近似) | deployment(部署实例)≠ strategy(策略)——同一策略多 deployment 时现有键不区分;MEXC 现货单策略单 venue 下语义重合,差异潜伏但不引爆 |
| alpha_version | **不在身份里** | 版本变更不改变 id(刻意——幂等键跨版本稳定,版本漂移归治理/账本审计) |
| signal_time | decided_ts | 语义等价(决策时间戳) |
| rebalance_id | **不在身份里** | 由 (decided_ts + prior_qty) 近似承载——同 ts 同数量不同前仓的增量单靠 prior_qty 区分(2026-07-06 审计 P1-3 修复) |
| symbol | instrument_id | 等价 |
| side | side | 等价(2026-07-05 双所真实调仓暴露撞 id bug 后加入) |
| intended_quantity | quantity | 等价(取整后真实浮点) |
| — | prior_qty / limit_price(额外) | 现有实现**多于** §13.3:前仓位防同额增量撞 id、限价使改价重发成为新订单 |

结论:现有实现是 §13.3 公式的**超集 + 两处身份缺席**(alpha_version /
rebalance_id)。这不是漂移——幂等语义(「同一笔订单的重试去重」)完全一致,
身份粒度按真实事故(2026-07-05/07-06 两次撞 id)校准过;但 M3 做 §13.4
查询接管时,若未来引入多 deployment 同策略,deployment_id 维度必须显式补上,
本表即评审依据。

## 6. M1 实施记录(2026-07-27,本提交)

- `live/contracts.py::ExchangeFilters`:加法扩展 `maker_commission` /
  `taker_commission`(`Optional[float] = None`,尾随字段,既有构造零漂移;
  注释钉死「缺席 ≠ 免费,其他 venue 诚实 None」)。
- `live/exchange/mexc_spot.py`:
  - 模块级辅助 `_parse_optional_float`(可解析才填,否则 None)、`_require_float`
    (fail-loud 必填数值)、`_normalize_order_row` / `_normalize_trade_row`
    (fail-loud 行规范化,非 dict/必填字段缺失/类型不符 → RuntimeError)。
  - `open_orders(instrument_id=None)` / `all_orders(instrument_id, *, limit=500)` /
    `my_trades(instrument_id, *, limit=500)`——签名 GET,limit 钳 [1,1000],
    docstring 载明 7 天 / ~1 月窗口与 fail-loud 裁定;**不进** VenueAdapter
    Protocol。
  - `exchange_filters()` 解析 makerCommission/takerCommission(可解析才填)。
  - 模块 docstring 的「字段名依文档假设、未对真 mainnet 验证」诚实注记补入新
    端点字段。
- `tests/live/test_mexc_spot.py`:45 → 59(+14)。每端点:happy-path 逐字段
  精确断言 / 签名形状(X-MEXC-APIKEY 头 + `_verify_signature` 重算 +
  timestamp/recvWindow)/ limit 钳档 / 非 2xx 抛 / 畸形载荷 fail-loud 抛 /
  全经 `_FakeTransport` 零真网络;commission:在场解析 0.0、缺席 None、旧响应
  形状(无 commission 字段)兼容 None。
- `tests/governance/test_crypto_data_boundary.py`:`_FETCHERS` 补登记
  `tools/mexc_klines_fetch.py`(`_EXTRA_ALLOWED_BY_FILE: {"argparse"}`)与
  `tools/mexc_readonly_smoke.py`(`{"typing"}`)——binance_readonly_smoke 在册
  而这两个 MEXC 对称工具不在册,是既有覆盖缺口,本切片顺手补上(调 allowance
  而非改工具,与既有先例一致)。
- `docs/MEXC_INTEGRATION.md` §3:方法面漂移修正——原列 `get_open_orders` /
  `get_account` / `get_klines` 均不存在,改为以适配器实际实现为准的完整方法面
  (含 M1 三端点与 commission 注记)。
- 证据再生:`scripts/generate_service_import_graph.py` +
  `scripts/generate_supply_chain_evidence.py`。

## 7. Phase 8 验收对照(727 §13.2 阶段 B + Phase 8 验收条)

| 要求 | M1 后状态 |
|---|---|
| balances | ✅ 既有 `account()`/`positions()` |
| open orders | ✅ M1 `open_orders()` |
| recent orders | ✅ M1 `all_orders()`(7 天窗口诚实注记) |
| recent trades | ✅ M1 `my_trades()`(~1 月窗口诚实注记) |
| commission / fee query | ✅ M1 exchangeInfo maker/taker commission 解析 |
| user stream / listen-key renewal | ⏸ **推迟**(§4 立据;REST 回填等价覆盖) |
| REST backfill after disconnect | 🟡 能力面已备(M1 三端点),消费方对账器在 M3 |
| 无真实下单 | ✅ M1 纯签名 GET,下单链零改动,mainnet 构造闸门不变 |
| 可从交易所重建账户状态 | 🟡 交易所侧输入齐了(M1),重建器在 M3 |
| 丢包和断线可检测 | ⏸ 随 user stream 推迟;REST 轮询侧的 freshness 判定复用既有 price_check/funding_check 纪律 |
