# QUANTOS 数据渠道覆盖清单

生成时间（epoch ms）：`1784927654137`

这是数据覆盖审计，不是盈利或实盘授权。缺失、近期窗口和诊断历史均保持原状态。

| 渠道 | 提供方/场地 | 频率 | 本地资产 | 状态 | 主要字段 |
|---|---|---|---|---|---|
| `binance_usdm_price` | Binance Data Vision / binance_usdm | daily | `data/cache/crypto_perp_mark_prices.json` | REAL_HISTORY | open、high、low、close、volume |
| `binance_usdm_funding` | Binance / binance_usdm | 8h->daily | `data/cache/crypto_funding_rates.json` | REAL_HISTORY | funding_rate、funding_daily_sum |
| `binance_usdm_oi_metrics` | Binance Data Vision / binance_usdm | 5m->daily | `data/cache/crypto_oi_metrics.json` | REAL_HISTORY | oi、oi_value、top_ls、global_ls、taker_ls |
| `binance_spot_klines` | Binance / binance_spot | daily | `data/cache/crypto_binance.db` | REAL_HISTORY | open、high、low、close、volume |
| `binance_liquidations` | Binance / binance_usdm | event->daily | `data/cache/crypto_liquidations.json` | PUBLIC_STREAM_REQUIRED | liquidation_notional、long_liquidation_notional、short_liquidation_notional、order_count、largest_notional |
| `cross_exchange_book` | Binance + Bybit / multi_venue | on-demand | `data/cache/cross_exchange_snapshot.json` | REAL_SNAPSHOT | bid、ask、mid、spread_bps、last_price、funding_rate、open_interest |
| `deribit_term_structure` | Deribit / deribit | daily | `data/cache/deribit_vol.db` | REAL_DIAGNOSTIC | close、basis、term_structure |
| `deribit_options_vol` | Deribit / deribit | snapshot->daily | `data/cache/deribit_options_skew.json` | REAL_DIAGNOSTIC | mark_iv、delta、skew、open_interest、volume |
| `fred_market_regime` | FRED / macro | daily | `data/cache/macro_fred.db` | REAL_HISTORY | VIX、DGS10、T10Y2Y、HY_OAS、DXY、DFII10 |
| `alfred_vintage` | ALFRED / macro | release/vintage | `data/cache/macro_alfred.db` | BLOCKED_MISSING | CPI、UNRATE、GDP、realtime_start |
| `mexc_spot_btc` | MEXC / mexc_spot | daily | `data/cache/crypto_mexc.db` | REAL_HISTORY | open、high、low、close、volume |

## 解释

- 代码获取器存在不等于本地有连续历史数据
- REAL_RECENT/REAL_SNAPSHOT/REAL_DIAGNOSTIC 不可直接当作 paper-ready PIT 截面
- 缺失输入不会用零值、上一值或跨 venue 价格静默补齐
