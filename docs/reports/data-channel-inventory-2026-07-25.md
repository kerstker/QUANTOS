# QUANTOS 数据渠道覆盖清单

生成时间（epoch ms）：`1785328467396`

这是数据覆盖审计，不是盈利或实盘授权。缺失、近期窗口和诊断历史均保持原状态。

| 渠道 | 提供方/场地 | 频率 | 本地资产 | 状态 | 主要字段 |
|---|---|---|---|---|---|
| `binance_usdm_price` | Binance Data Vision / binance_usdm | daily | `data/cache/crypto_perp_mark_prices.json` | REAL_HISTORY | open、high、low、close、volume |
| `binance_usdm_funding` | Binance / binance_usdm | 8h->daily | `data/cache/crypto_funding_rates.json` | REAL_HISTORY | funding_rate、funding_daily_sum |
| `binance_usdm_oi_metrics` | Binance Data Vision / binance_usdm | 5m->daily | `data/cache/crypto_oi_metrics.json` | REAL_HISTORY | oi、oi_value、top_ls、global_ls、taker_ls |
| `binance_usdm_realtime_complete` | Binance / binance_usdm | 5m | `data/cache/binance_realtime_market.db` | REAL_RECENT | mark_price、index_price、basis_bps、funding_rate、mark_1m_ohlc、index_1m_ohlc、premium_1m_ohlc、funding_rate_cap、funding_rate_floor、funding_interval_hours、bid、bid_qty、ask、ask_qty、spread_bps、open、high、low、close、volume、contract_status、tick_size、step_size、min_qty、max_qty、min_notional、rolling_24h_quote_volume、rolling_24h_trade_count、oi、oi_value、global_ls、top_ls、taker_ls |
| `binance_usdm_websocket_stream` | Binance / binance_usdm | continuous->5s | `data/cache/binance_realtime_stream.db` | REAL_RECENT | best_bid_ask、depth20、mark_price、index_price、aggregate_trades、kline_1m、liquidation_orders、rpi_depth_updates、rpi_sequence_continuity、contract_status_updates、notional_bracket_updates |
| `binance_usdm_systemic_risk` | Binance / binance_usdm | 30m | `data/cache/binance_systemic_risk.db` | REAL_RECENT | adl_risk、basis、basis_rate、annualized_basis_rate、index_constituent_price、index_constituent_weight、index_dispersion_bps、insurance_asset、insurance_margin_balance |
| `binance_usdm_rpi_depth` | Binance / binance_usdm | 1h | `data/cache/binance_rpi_depth.db` | REAL_RECENT | rpi_bid_price、rpi_bid_quantity、rpi_ask_price、rpi_ask_quantity、spread_bps、bid_notional、ask_notional、notional_imbalance |
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
