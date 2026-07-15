# 真实市场数据接入(离线公开-CSV 适配器)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 转 writing-plans
**所在层:** `services/data/`(持久化 PIT 数据湖)
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、无网络、无券商、无凭证。

## 目标

把整条已建成的研究方法论(IC/ICIR、PBO/CSCV、样本外 walk-forward、Deflated Sharpe、
引导搜索)从"合成数据演示"变成"能在**真实历史信号**上运行",从而**真正验证 edge 是否存在**。

这是 L2 安全边界内逼近机构级系统的最大单步杠杆:方法论已正确,只缺真实内容。

## 关键约束(安全边界,不可破)

- **无网络、无券商/CTP/FIX SDK、无凭证**:绝不联网抓数。真实数据由用户离线导出为本地 CSV
  自带(BYO-CSV);本适配器只负责把本地 CSV 安全、确定性、防泄漏地喂进数据湖。
- 纯 Python stdlib(`csv` + `datetime`);确定性;失败关闭。
- 复用现有 `normalize_bar`/`Bar` 校验/store 去重/`content_hash`/`quality`,不削弱任何护栏。
- 纯加法:不改 `Bar` 契约、不改 store、不改 `run_research_session`。

## 非目标(YAGNI)

- 不做联网取数(即便公开端点);如需,属仓库外一次性辅助脚本,绝不进门禁路径。
- 不做盘中/逐笔;仅日线 EOD(免费真实数据的常见粒度)。
- 不做基本面/另类数据(独立子项)。
- v1 不做结算滞后(`knowledge_time = event_time`,EOD 当日收盘即可知);如需可后续加。

## 架构

新增 `services/data/market_csv.py`,入口:

```
ingest_market_csv(path, store, *, instrument_id: str,
                  column_map: dict = DEFAULT_COLUMN_MAP,
                  date_format: str = "%Y-%m-%d",
                  now: int | None = None,
                  dataset_id: str = "market", version: str = "") -> IngestionReport
```

返回与 `ingest_bars_csv` 相同的 `IngestionReport`。

**DRY 重构**:把 `file_ingest.ingest_bars_csv` 的核心循环(normalize → reject_if_future →
store 去重 → 累积 `content_hash`/`quality`)抽成共享函数:

```
_ingest_records(records, store, *, now, dataset_id, version) -> IngestionReport
```

`records` 是一个产出 `(canonical_row: dict, knowledge_time: int)` 的可迭代对象,其中
`canonical_row` 含 `instrument_id,event_time,open,high,low,close,volume`(不含 knowledge_time)。
`ingest_bars_csv` 与 `ingest_market_csv` 都改为构造各自的 `records` 再委托给它 —— 既消重,
又保证两条入口共享完全相同的失败关闭语义。既有 `file_ingest` 测试在重构后必须全绿(零回归)。

## 每行转换(`ingest_market_csv`)

`DEFAULT_COLUMN_MAP = {"date":"Date","open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"}`,
可整体覆盖以适配任意来源(Stooq 小写表头、`%Y%m%d` 日期、交易所导出等)。

对每行:

1. `date_str = row[column_map["date"]]` → `datetime.strptime(date_str, date_format)` →
   `event_time = year*10000 + month*100 + day`(即 `YYYYMMDD` 整数)。
   - `event_time` 仅作单调排序键;`panel_builder` 按**位置**取前向期(`j=i+fwd_horizon`),
     不做 event_time 算术,故非均匀间距(月/年边界、周末跳空)完全安全。
2. `open/high/low/close = float(row[column_map[k]])`;`volume = float(row[column_map["volume"]])`。
3. `instrument_id = 入参`;`knowledge_time = event_time`(EOD 当日收盘即可知;`store.as_of(t)`
   过滤 `knowledge_time<=t` 含当日 bar → 信号用到收盘 t、前向 t→t+h,**无 lookahead**)。
4. 组装 `canonical_row` → 经 `_ingest_records` 走 `normalize_bar`(OHLC 一致性/正价/防泄漏)→
   `reject_if_future`(若给 `now`)→ store 去重 → hash/quality。

**失败关闭**:坏日期(strptime 失败)、缺列(KeyError)、OHLC 矛盾、非正价、未来日期 →
记入 `rejected (index, type_name, detail)`,**不崩**,与 `ingest_bars_csv` 语义一致。

## 数据流(端到端)

```
真实日线 CSV(每文件一标的)
   └─ ingest_market_csv(instrument_id=代码) ─┐
   └─ ingest_market_csv(...) ───────────────┤→ SqlitePITStore(持久化 PIT 数据湖)
                                             ┘
   event_times = sorted(各标的 date 并集)
   └─ run_research_session(store, instruments=store.keys(), event_times=..., ...)
        → 引导搜索在真实持久化数据期上跑 → 评估写入登记 → contained(无 APPROVED)、确定性
```

## 错误处理 / 失败关闭

- 单行解析/校验失败 → 进 `rejected`,继续(不崩)。
- 缺 `date`/OHLCV 映射列 → 该行进 `rejected`。
- `now` 给定时未来日期行被 `reject_if_future` 拒。
- store 未初始化/损坏 → 现有 `DataStoreError` 失败关闭(不变)。

## 确定性

无 RNG、无墙钟;`datetime.strptime` 纯解析(非 `now()`);行按文件顺序处理;`content_hash`
顺序无关(现有 `content_hash_of`)。同输入 → 同 `IngestionReport`。

## 测试矩阵(`tests/data/test_market_csv.py` + 现有 `test_file_ingest.py` 回归)

1. 真实格式 CSV(`Date,Open,High,Low,Close,Volume` + 周末跳空真实日历)→ 正确映射进 store;
   `bars_ingested` 正确、`rejected` 为空;`event_time == YYYYMMDD`。
2. 自定义 `column_map` + `date_format`(Stooq 小写 + `%Y%m%d`)同样工作。
3. 坏行(OHLC 矛盾 / 坏日期 / 缺列)进 `rejected`,好行照常入库(失败关闭、不崩)。
4. 幂等:重复接入同文件 → 全 `duplicates`、`content_hash` 不变。
5. `now` 防未来:未来日期行被拒。
6. **端到端**:多标的真实格式数据接入 → 由 `store.keys()` 并集导出 `event_times` →
   `run_research_session` 跑出非空评估、**contained**(所有 alpha != APPROVED)、**确定性**(两次同结果)。
7. `_ingest_records` 抽取后,既有 `tests/data/test_file_ingest.py` 全绿(零回归)。

## 涉及文件

- 新建 `services/data/market_csv.py` —— `ingest_market_csv`、`DEFAULT_COLUMN_MAP`、
  日期→`event_time` 转换。
- 修改 `services/data/file_ingest.py` —— 抽取共享 `_ingest_records`,`ingest_bars_csv` 改为委托。
- 新建 `tests/data/test_market_csv.py` —— 上述 1–6。
- (回归)`tests/data/test_file_ingest.py` 不变、保持全绿。

## 安全审查

- 离线、纯 stdlib、无网络/SDK/凭证;读本地历史 CSV,非实盘、非取数。
- 复用现有失败关闭校验/防泄漏/去重;不改契约、store、会话编排。
- 纯加法、opt-in;`event_time=YYYYMMDD` 单调,前向按位置 → 无 lookahead;`knowledge_time=event_time`
  保持 bitemporal 不变量(knowledge_time>=event_time)。
