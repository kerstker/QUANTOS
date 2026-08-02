# QUANTOS 股票数据库(data/stocks/)

A 股微盘量化研究的统一数据底座。2026-07-24 由 `data/cache/ashare_*` 散落文件整合迁移而来(迁移脚本 `tools/ashare_db_migrate.py`)。

## 目录结构

```
data/stocks/
├── stocks.db                  # ★ 统一股票数据库(唯一权威)
├── research.db                # stocks.db 的研究副本(驱动器只读它;刷新: cp stocks.db research.db)
├── universe.json / .csv       # 249 只微盘宇宙(Wind 筛选,2026-07-23)
├── snapshots/
│   ├── klines_snapshot.json   # K线落库快照(逐股行数/起止/来源)
│   ├── alpha_snapshot.json    # ALPHA 研究结果(tools/ashare_alpha_research.py 输出)
│   └── paper_track_snapshot.json
├── raw/wind/                  # Wind 原始 K线 CSV(逐股)
├── raw/ifind/                 # iFinD 原始 K线 CSV(3票/组)
├── wind_fetch_progress.json   # Wind 抓取断点
└── ifind_fetch_progress.json  # iFinD 抓取断点
```

## stocks.db 表结构

| 表 | 说明 |
|---|---|
| `bars(key, event_time, knowledge_time, payload_json)` | PIT 日线(与平台 PIT 契约同构,`SqlitePITStore` 直读;payload = OHLCV 前复权) |
| `universe(code, name, mcap_yi, adv_yi, source, n_bars, first_date, last_date)` | 宇宙成员 + 数据来源(wind/ifind)+ 覆盖区间 |
| `daily_prices(code, trade_date, open, high, low, close, volume)` | 扁平价格表,免 JSON 解析的直接 SQL 分析 |
| `valuation_snapshot(code, asof, name, close, mcap, pe_ttm, pe_lyr, pb, pb_lf, dividend_yield, raw_json)` | **估值/基本面层(2026-07-24 新增)**:当前截面 PE(TTM/LYR)、PB(/LF)、总市值(含限售)、股息率,Wind `get_stock_price_indicators` 结构化字段。**当前截面,非 PIT 历史**——只能按 asof 最新日期做截面筛选,不得当历史序列用。深度基本面(ROE/营收/利润增速)= 下一层(NL `get_stock_fundamentals`) |
| `meta(k, v)` | 迁移/来源 provenance |

## 数据口径(诚实注记)

- 宇宙 = 2026-07-23 截面:沪深 A 股总市值 20–26.5 亿、近 1 月日均成交额 >5000 万、非 ST,共 249 只;**幸存者截面,含幸存者偏差**
- 价格为前复权(Wind/iFinD 各自口径,跨来源价格水平不可比,个股内部自洽)
- payload 无 amt 字段;下游成交额 = volume × 典型价代理
- 覆盖 2021-01-04 → 2026-07-23,共 317,641 根日 K;晚 IPO 股票行数更少(≥375)

## 数据流水线(全部幂等、断点续跑)

| 步骤 | 工具 |
|---|---|
| 宇宙采集(Wind 分页) | `tools/ashare_universe_collect.py` |
| K线抓取(Wind) | `tools/ashare_kline_fetch.py --limit N` |
| K线抓取(iFinD 兜底) | `tools/ashare_ifind_fetch.py` |
| 派生表重建(daily_prices/universe/meta) | `tools/ashare_db_migrate.py`(SRC=DST 时原地重建) |
| 估值/基本面快照(Wind,断点续跑) | `tools/ashare_fundamentals_fetch.py`(`--refresh` 全量重抓) |
| ALPHA 研究 | `python -m tools.ashare_alpha_research`(读 research.db) |
| Paper-track | `python -m tools.ashare_paper_track` |
