# QUANTOS 数据湖文档(DATA_SOURCES)

> 状态:**L2_DRY_RUN_ONLY**(只做研究 + dry-run 模拟)。本文档描述的是 QUANTOS 唯一接触网络的部分——**只读公开行情抓取**。这里没有交易、没有下单、没有券商连接,也永远不会有。所有数字与字段口径均逐字取自 `tools/` 下抓取器源码与 `services/data/adapters.py`;研究结论数字取自 `docs/PROJECT_STATE.md` 与 `docs/crypto-research-conclusions.md`。

---

## 0. 一句话定位

数据湖 = 一组**门禁之外(out-of-gate)、只读、公开行情**抓取器,把外部行情拉进本地的时点(PIT)存储,供离线、确定性的研究核心使用。**网络只在 `tools/` 里发生;`services/` 整棵树一行联网 import 都没有**,这条由机器测试钉死(见 §5)。

---

## 1. 治理边界(钉死,不可松动)

数据湖的存在不削弱任何 L2 安全不变量。读公开价格触碰的是"数据"维度,而 L2 红线管的是"交易能力"(下单、凭证、券商连接)——两者正交。具体钉死如下:

- **无交易所/券商 SDK。** 抓取器只用 stdlib `urllib`。`tests/governance/test_crypto_data_boundary.py::test_tools_fetcher_only_stdlib_and_services_imports` 用 AST 断言每个抓取器的 import 根 ⊆ `{json, os, time, sys, urllib, services, tools, __future__}`,且与禁用 SDK 集合 `{ccxt, binance, coinbase, cbpro, krakenex, kraken, ib_insync, ibapi, alpaca, alpaca_trade_api, oandapyV20, fix, quickfix, ctp, vnpy}` 不相交。
  - 注:禁用集里的 `binance` 指的是**交易 SDK 包**;本仓的 Binance 抓取器用 stdlib `urllib` 打**公开数据**端点,不 import 任何 SDK。
- **无下单/转账调用。** `test_tools_fetcher_has_no_trade_or_transfer_calls` 断言源码中不含 `create_order, place_order, submit_order, cancel_order, create_market_order, create_limit_order, fapiPrivate, sapiPost, .withdraw(, create_withdrawal, transfer(` 中任意一个。
- **网络只在门外。** `test_services_tree_has_no_network_imports` 断言 `services/` 树内无任何网络 import 根(`urllib, socket, requests, httpx, aiohttp, websocket(s), ftplib, telnetlib, http`)——研究核心的离线确定性是**机器强制**的,不是约定。
- **凭证只读、绝不入库。** 凡是用到 key 的源,key 都是**只读 DATA key**,运行时从环境变量或 gitignore 掉的 `key/` 目录读取,绝不硬编码、绝不打印、绝不提交。抓回的数据落进本地 PIT 存储(`data/cache/*.db`),该目录被 gitignore,因此**行情数据从不进版本库**。
- **新增源必须登记进 `_FETCHERS`。** 任何新抓取器都要加进 `test_crypto_data_boundary.py` 顶部的 `_FETCHERS` 列表(当前 5 条),边界测试才会覆盖它(见 §4)。

---

## 2. 五个抓取器 / 四个数据提供方(逐源)

`_FETCHERS` 钉死**五个抓取器文件**,来自**四个数据提供方**(CoinGecko、CryptoCompare、Binance 现货、Binance 永续),全部**只读公开行情**。每条 bar 携带 `event_time`(bar 开盘时刻)与 `knowledge_time`(收盘可知时刻),做到时点正确;每个源都**丢弃正在形成的最后一根(live)bar**(其值未实现、重抓会变,采纳即为前视 + 不可复现)。

### 2.1 CoinGecko — OHLC(有 demo key)

| 项 | 值 |
|---|---|
| 文件 | `tools/coingecko_fetch.py` |
| 端点 | `GET /coins/{id}/ohlc`(`api.coingecko.com/api/v3`) |
| 鉴权 | 只读 demo data key,取自 `$COINGECKO_API_KEY` 或 gitignore 的 `key/coinkey.txt`;缺失则 `raise`(失败关闭) |
| 数据 | 日内 OHLC(**无成交量**:该端点不返回 volume,`Bar.volume` 取默认 0.0) |
| 时点 | `event_time = ts`(bar 开),`knowledge_time = ts + interval_ms`(收盘可知) |

补充:`tools/coingecko_volume_fetch.py` 是同一提供方的 **market_chart** 入口,`GET /coins/{id}/market_chart`,返回价格 + **真实 24h 滚动成交量**(`total_volumes`,一个真正的流动性维度)。它经声明式 adapter(`MC_SPEC`)落成带真实 volume 的 canonical bar,并把不同币的时间戳**对齐到 1 小时网格**(否则各币略有偏移的时间戳永远对不齐,每个截面都塌成单币)。语义诚实标注:market_chart 的 volume 是某时刻**观测到的 24h 滚动成交量(流动性水平)**,不是逐 bar 成交量;`open/high/low` 都被置为该观测价,不是真正的 OHLC range。

### 2.2 CryptoCompare — 多年日线(免费 key)

| 项 | 值 |
|---|---|
| 文件 | `tools/cryptocompare_fetch.py` |
| 端点 | `GET /data/v2/histoday`(日线 OHLCV)、`GET /data/top/mktcapfull`(按市值取 symbol 列表) |
| 鉴权 | **可选**只读 key,取自 `$CRYPTOCOMPARE_API_KEY` 或 `key/` 下若干常见文件名;**无 key 也能跑**(只是被限速) |
| 数据 | 日线 OHLCV,`limit` 最多约 **2000 天**——足以覆盖**出 regime 样本(含 2022 熊市)**,这正是 CoinGecko demo 365 天封顶够不到的 |
| 定位 | CryptoCompare 是**数据聚合器**,不是交易所;无下单/账户能力 |
| 清洗 | 跳过上市前的 0 值填充行(CryptoCompare 用 0 padding);丢弃未来 bar 与当天 live bar |

### 2.3 Binance 公开 klines(无 key)

| 项 | 值 |
|---|---|
| 文件 | `tools/binance_klines_fetch.py` |
| 端点 | 公开 `GET /api/v3/klines` + `/api/v3/exchangeInfo`(主机依次尝试 `data-api.binance.vision`、`api.binance.com`) |
| 鉴权 | **无 key、无账户**;公开 klines 端点不暴露任何下单能力 |
| 数据 | 数百个 USDT 现货对的**多年日线 OHLCV**;`exchangeInfo` 给出 `status=TRADING` 且可现货交易(`isSpotTradingAllowed`)的 USDT 对 base ticker 列表 |
| 广度 | 抓取器向后翻页最多 8×1000 天;研究侧据此把截面拓到 **437 个 USDT 对**(过滤后 336 币 × 多年日线,含 2022 熊市) |
| 清洗 | 按 UTC 日网格归桶,丢弃当天 live 日 |

### 2.4 Binance 永续资金费(无 key)

| 项 | 值 |
|---|---|
| 文件 | `tools/binance_funding_fetch.py` |
| 端点 | 公开 `GET /fapi/v1/fundingRate` + `/fapi/v1/exchangeInfo`(`fapi.binance.com`) |
| 鉴权 | **无 key、无账户**;公开 fundingRate 历史不暴露任何下单能力 |
| 数据 | **永续资金费率**——多空之间的周期性支付,即**杠杆/持仓失衡**的直接读数,一个与价/量正交的 alpha 维度。资金费每 8h 结算一次,这里**按 UTC 日聚合为日总和**(持仓一天的 carry) |
| 标的 | `exchangeInfo` 给出 `contractType=PERPETUAL`、`quoteAsset=USDT`、`status=TRADING` 的永续 base ticker |
| 时点 | 当日资金费当日实现(收盘可知),PIT 安全;丢弃当天 live 日 |

> **研究结论锚点(诚实标注)**:在所有这些源跑下来,大盘"价 + 量 + 日线"段**没有**经得起跨 regime 的真信号——瓶颈是数据,不是因子生成。唯一通过 OOS + 成本 + 跨 regime 的 lead 是**资金费 carry**(`tools/crypto_carry_strategy.py`:115 永续、净@5bps 夏普 **2.03**、7/7 年全正含 2022;`crypto_carry_stress.py` 压力测试把这个**乐观的 ~2.0** 折成更诚实的可部署数)。它带一串硬折扣:幸存者偏差(退市/极端资金费币缺失,偏高)、小盘滑点是建模非实测(5bps 是乐观值、20bps→1.36)、carry 尾部风险(暴动/爆仓未建模)、容量有限、近年随 carry 拥挤而衰减。**是 lead,不是已证实可规模化的 alpha;可部署夏普远低于 2.0。** 详见 `docs/PROJECT_STATE.md` §7 与 `docs/crypto-research-conclusions.md`。

---

## 3. 如何加一个新源(填 `SourceSpec`)

设计原则(`services/data/adapters.py` 模块 docstring):**加一个数据源应该是一次"声明",而不是写一个新解析器。** 更丰富的 feed(真实 volume、另一家交易所、股票 OHLCV、更高档的 crypto plan)只需一个 spec 即可接入,无需改动核心。

步骤:

1. **写一个 `SourceSpec`**(frozen dataclass),声明该源原始记录到 canonical bar 字段的映射:
   ```python
   from services.data.adapters import SourceSpec, normalize_records

   MY_SPEC = SourceSpec(
       name="my-source",            # 唯一名;重复注册会失败关闭
       asset_class="CRYPTO",        # 资产类别
       time_unit="ms",              # 'ms' | 'yyyymmdd',交给 ingest guard 做尺度检查
       field_map={                  # canonical_field -> 原始 key
           "instrument_id": "sym",  # 多标的源:给出 id 列
           "event_time": "ts",
           "open": "o", "high": "h", "low": "l", "close": "c",
           "volume": "v",           # 可选;缺省则 volume=0.0
       },
       knowledge_lag=0,             # 加到 event_time 得 knowledge_time(bar 收盘可知);必须 >= 0
   )
   ```
   - `field_map` 至少要覆盖 `_REQUIRED = (event_time, open, high, low, close)`,否则 `__post_init__` 直接 `raise`。
   - 单标的源(行里没有 id 列)用 `instrument_id="BTC"` 形式给一个固定 id;否则必须在 `field_map` 里给 `instrument_id`——二者缺一即报错。

2. **在抓取器里把原始记录喂给 `normalize_records(raw, MY_SPEC)`**,得到 canonical bar rows。该函数**失败关闭**:某条记录缺映射字段、或值非数值,会抛出清晰的 `ValueError`(spec/数据不匹配是真错误,不是悄悄丢行)。

3. **(可选)`register_source(MY_SPEC)`** 把源登记进注册表,平台即可 `list_sources()` 枚举;重复名字会失败关闭(不静默覆盖)。

4. **把行写进离线 ingest seam**:`services.data.market_provider.ingest_provider_rows(rows, store, dataset_id=..., now=now_ms, time_unit=...)`。传 `now=` 会武装第二道未来数据护栏,并做跨尺度共存检查(ms 与 YYYYMMDD 不可混)。

5. **治理登记(必做)**:新抓取器路径加入 `tests/governance/test_crypto_data_boundary.py` 的 `_FETCHERS` 列表,使 §1 的边界断言覆盖它。新抓取器必须只 import stdlib + `services`/`tools`,不得 import 任何交易所/券商 SDK,不得含下单/转账调用。

> `services/data/adapters.py` 这一层**纯且离线**:它从不碰网络或凭证(那永远留在门外的 `tools/`),只负责把**已抓回**的记录归一化。

---

## 4. 免费源的局限(诚实标注)

免费档位是真实约束,直接决定能做什么研究、不能做什么:

- **CoinGecko demo 365 天封顶。** demo plan 的 OHLC 历史够不到出 regime 段——它看不到 2022 熊市。这正是引入 CryptoCompare(~2000 天日线)与 Binance 公开 klines(多年)的原因:用更长历史做跨 regime 证伪。CoinGecko 段那批"样本内诱人"的趋势因子(如 `dist_from_high_14`)一旦出 regime 一测就死,已证伪、已归档。
- **OHLC 端点无成交量。** CoinGecko `/ohlc` 不带 volume(volume=0);要真实流动性维度得走 `market_chart`,而那里的 volume 是 **24h 滚动量(流动性水平)**,不是逐 bar 成交量。
- **OI / 多空比仅约 30 天。** 这类持仓型另类数据在免费档位只有约 30 天窗口,**不足以做跨 regime 研究**,因此尚未接入数据湖。要把资金费 carry 这个 lead 进一步坐实/规模化(并入 OI、多空比),需要更长的历史——这是待用户拍板成本/资源的"数据口子"。
- **幸存者偏差。** 公开端点通常只返回**活到今天**的标的(永续/现货);退市、极端资金费的币缺失,会让回测系统性偏高。这是资金费 carry lead 诚实折扣里的第一条。
- **滑点未在源里建模。** 数据湖只给行情;成本/滑点是研究侧的建模假设(carry 压力测试里 5bps 是流动档的乐观值),**不是实测**。
- **CryptoCompare / Binance 限速。** CryptoCompare 无 key 可跑但被限速;Binance 抓取器在翻页之间 `sleep` 以礼让公开端点。这些都是面向"慢速、离线、可复现"的研究节奏设计,不是面向实时交易。

---

## 5. 相关文件(绝对路径)

- 抓取器(门外只读,共 5 个文件 / `_FETCHERS` 钉死):
  - `H:\code\QUANTOS\tools\coingecko_fetch.py`(CoinGecko OHLC)
  - `H:\code\QUANTOS\tools\coingecko_volume_fetch.py`(CoinGecko market_chart,真实 volume)
  - `H:\code\QUANTOS\tools\cryptocompare_fetch.py`(多年日线 + 市值 symbol 列表)
  - `H:\code\QUANTOS\tools\binance_klines_fetch.py`(公开 klines,无 key)
  - `H:\code\QUANTOS\tools\binance_funding_fetch.py`(永续资金费,无 key)
- 离线声明式 adapter / 注册表:`H:\code\QUANTOS\services\data\adapters.py`
- 离线 ingest seam:`H:\code\QUANTOS\services\data\market_provider.py`、本地 PIT 存储 `H:\code\QUANTOS\services\data\persistent_store.py`(`data/cache/*.db`,gitignore)
- 治理边界测试(机器钉死):`H:\code\QUANTOS\tests\governance\test_crypto_data_boundary.py`
- 研究结论:`H:\code\QUANTOS\docs\crypto-research-conclusions.md`、`H:\code\QUANTOS\docs\PROJECT_STATE.md`
