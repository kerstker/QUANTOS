# 交易日历子系统(A 类 #4)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/data/`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、确定性、失败关闭。

## 目标

补齐数据完整性的**交易日历**维度:持久化交易日历,并对数据做**双向对账**——交易日缺数据
(missing)与非交易日出现的"幽灵 bar"(ghost,节假日/坏数据)——加覆盖率与跨市场对齐。与
`universe_data_gaps`(universe 成员维度)互补。

## 关键约束

- 纯 Python stdlib(`csv`/`datetime`);确定性;失败关闭;离线、无网络。
- `session` 用 **YYYYMMDD 整数**(与 `market_csv` 的 `event_time` 同编码,直接可比)。
- 纯加法:不动现有 store/panel/universe。

## 非目标(YAGNI)

- 时区/半日市精细建模(session 为日粒度);自动节假日推断;盘中日历。

## 架构(新建 `services/data/calendar_store.py`)

### `SqliteCalendarStore`
durable sqlite,表 `sessions(calendar_id TEXT, session INTEGER, PK(calendar_id, session))`。
方法:`add_session(calendar_id, session) -> bool`(PK 去重)、`add_sessions`、
`sessions(calendar_id) -> list[int]`(有序)、`sessions_in(calendar_id, start, end) -> list[int]`、
`is_session(calendar_id, session) -> bool`;未初始化/损坏 → `DataStoreError`(复用 `persistent_store`)。

### `ingest_calendar_csv(path, store, calendar_id, *, date_col="date", date_format="%Y-%m-%d")`
BYO-CSV:一列日期 → `datetime.strptime` → YYYYMMDD 整数 session → `add_session`。
`date_format` 守卫:必须含 `%Y%m%d`、禁 `%y`(同 `market_csv`,防静默错序)。坏行(坏日期/缺列)
记入 `rejected (index, type_name, detail)` 不崩;去重计 `duplicates`。返回
`CalendarIngestionReport(rows_read, added, duplicates, rejected)`。

### `reconcile_calendar(store, calendar_store, instruments, calendar_id, *, start, end) -> dict`
`sessions = set(calendar_store.sessions_in(calendar_id, start, end))`;对每个 inst 用
`store.fetch_bars(inst, start, end)`(**原始 event_time 窗口**,审计数据覆盖,非 PIT)取
`ets = {b.event_time}`:
- `missing[inst] = len(sessions − ets)`(交易日缺 bar);
- `ghost[inst] = len(ets − sessions)`(非交易日的幽灵 bar);
- `coverage[inst] = len(sessions ∩ ets)/len(sessions)`(sessions 空 → 0.0)。
返回 `{"total_sessions": len(sessions), "missing": {...}, "ghost": {...}, "coverage": {...}}`。fail-soft。

### `align_calendars(calendar_store, cal_a, cal_b) -> dict`
`{"common": sorted(A∩B), "only_a": sorted(A−B), "only_b": sorted(B−A)}`(跨市场对齐)。

## 错误处理 / 失败关闭

- store 未初始化/损坏 → `DataStoreError`。
- `ingest`:坏日期/缺列 → rejected(不崩);非法 `date_format` → `ValueError`(构造期)。

## 确定性

无 RNG;`sorted` session;集合差转 sorted;同输入同输出。

## 测试矩阵(`tests/data/test_calendar_store.py`)

1. store:add/add_sessions→`sessions`/`sessions_in`/`is_session` 正确;PK 去重;未初始化 → `DataStoreError`。
2. `ingest_calendar_csv`:好行入库;坏日期/缺列进 rejected 不崩;`%y` 守卫 → `ValueError`;幂等去重。
3. `reconcile_calendar`:构造某 inst 缺一个交易日(missing=1)+ 某 inst 在非交易日有 bar(ghost=1);
   coverage 数值正确;无问题 inst → missing/ghost 0、coverage 1.0。
4. `align_calendars`:两日历的 common/only_a/only_b 三集正确。
5. 确定性:同输入两次同结果。

## 涉及文件

- 新建 `services/data/calendar_store.py` —— `SqliteCalendarStore`、`ingest_calendar_csv`、
  `reconcile_calendar`、`align_calendars`、`CalendarIngestionReport`。
- 新建 `tests/data/test_calendar_store.py`。
- 复用不动:`persistent_store.DataStoreError`、`SqlitePITStore.fetch_bars`。

## 安全审查

- 离线、纯 stdlib、无网络;只读历史日历/行情;不改契约/前端/后端合约门。
- 纯加法;与 universe 缺口检查互补,不影响既有路径。
