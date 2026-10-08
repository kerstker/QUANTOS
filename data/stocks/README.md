# QUANTOS A股数据层

当前 A 股数据分为两层，禁止再把旧的 249 只微盘样本称为全市场唯一权威。

## 当前主数据：通达信全市场技术研究库

稳定路径：

```text
data/stocks/pit/tdx-windows-20260830/ashare_tdx_pit.sqlite
data/stocks/pit/tdx-windows-20260830/ashare_tdx_pit.adv20000000.panel.npz
```

2026-08-30 捕获的已验证状态：

- 5,549 只沪深北股票；
- 6,267,665 条日线，1,231 个交易日；
- 2021-08-02 至 2026-08-28；
- 原始未复权 OHLC、成交量、成交额；
- 5,549 条当前证券名称/ST、当前基本面、当前行业和主营快照；
- SQLite `quick_check=ok`，schema v2；
- 原始源保存在隔离 intake，SHA-256 清单完整验证。

核心表：

| 表 | 语义 |
|---|---|
| `daily_prices` | 原始未复权日线；每条 bar 的日期级 `knowledge_time=trade_date` |
| `instruments` | 跨捕获累计的证券价格覆盖汇总 |
| `source_captures` | 不可变捕获日、源清单哈希和报告 |
| `universe_snapshots` | 从首次捕获日起的当前股票池快照，不能回填更早历史 |
| `security_master_snapshots` | 捕获日证券名称和当前 ST 状态 |
| `fundamental_snapshots` | `base.dbf` 捕获日原始快照，非历史 PIT 财报 |
| `industry_snapshots` | 捕获日通达信行业代码和名称，非历史行业有效期 |
| `current_business_snapshots` | 捕获日 `specgpext.txt` 原始主营字段 |
| `opaque_source_snapshots` | 无法可靠解码的数据源审计；当前 `gbbq` 公司行为容器在此登记 |

该库是当前 A 股价格/技术 Alpha 搜索的主源，但仍是
`TECHNICAL_VALIDATION_ONLY`。它没有补齐历史复权因子、退市股票池、历史 ST、停复牌、
涨跌停、带公告可知时间的历史财报、历史行业、融资融券、北向或逐单资金流。

## 旧数据：249只微盘诊断样本

```text
data/stocks/stocks.db
data/stocks/research.db
data/stocks/universe.json
data/stocks/snapshots/
```

旧库是 2026-07-23 的 Wind/iFinD 截面，249 只、317,641 条前复权日线，覆盖
2021-01-04 至 2026-07-23。它包含当前截面筛选造成的幸存者偏差，仅用于复现旧诊断，
不得作为全市场回测、历史股票池或新候选晋级依据。

旧库主要表：

| 表 | 说明 |
|---|---|
| `bars` | 旧样本 PIT 形状的 JSON 日线 |
| `daily_prices` | 旧样本扁平前复权价格 |
| `universe` | 2026-07-23 当前截面成员 |
| `valuation_snapshot` | 当前估值截面，非历史 PIT |
| `meta` | 迁移与来源信息 |

## 更新流程

通达信接入、Windows 快照、发现、完整哈希、增量冲突规则及原子替换命令见
[`docs/runbooks/ashare-tdx-local-intake.md`](../../docs/runbooks/ashare-tdx-local-intake.md)。

关键规则：

1. incoming 原始文件永不修改；
2. 同一捕获重复运行幂等；
3. 既有历史 bar 发生变化时拒绝，不静默覆盖；
4. 新捕获的名称/ST/行业/基本面只从捕获日起可知；
5. 数据库在同目录临时副本完成全量校验后原子切换；
6. 新数据先进入研究/前向观察，不能自动触发实盘下单。
