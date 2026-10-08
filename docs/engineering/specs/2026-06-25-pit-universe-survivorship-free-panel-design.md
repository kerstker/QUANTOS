# 持久化 PIT Universe + 无幸存者偏差面板构建(数据湖子项)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 转 writing-plans
**所在层:** `services/data/` + `services/research/`(数据湖 → PIT 面板接缝)
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、确定性、失败关闭。

## 目标

让真实数据上的研究回测**无幸存者偏差**:在每个决策时刻 t 的横截面里,只纳入"t 时刻确属
universe 成员"的标的(包含后来退市/退出指数者),从而消除回测最大的谎言来源。顺带提供
**日历缺口**诚实度信号(成员在交易日缺数据)。这是顶级机构最较真的数据正确性维度。

## 关键约束

- 纯 Python stdlib;确定性;失败关闭。
- 复用现有内存 `PITUniverse`(`services/data/universe.py`,区间成员逻辑)——不重写。
- 复用持久化模式(`SqlitePITStore`/`ExperimentStore`:sqlite3、PK 去重、未初始化/损坏即抛)。
- **纯加法、opt-in、零回归**:`universe=None` 时 `panel_builder`/`session` 行为逐字节同现状。
- 无网络、无券商、无凭证;universe 数据由用户离线自带 CSV(BYO-CSV)。

## 非目标(YAGNI)

- 不做独立的完整交易日历子系统;日历仅作轻量缺口检查函数。
- 不改 `*Payload` 类型/前端/后端合约。
- 不做多币种。

## 架构

### 1. `SqliteUniverseStore`(新建 `services/data/universe_store.py`)

durable sqlite3,表:
```
membership(instrument_id TEXT, start_date TEXT, end_date TEXT NULL,
           PRIMARY KEY(instrument_id, start_date))
```
- `start_date`/`end_date` 为 ISO `YYYY-MM-DD`;`end_date` 为 NULL = 仍是成员。
- 方法:`add_membership(iid, start, end=None)`(校验 ISO、end≥start)、`save(universe: PITUniverse)`
  (把内存 universe 全量区间写入,PK 去重)、`load_into(universe: PITUniverse)`(从库填充内存 universe)、
  `members_as_of(date)`(便捷直查)。
- 失败关闭:未初始化/损坏 → 抛 `DataStoreError`(复用 `persistent_store.DataStoreError`)。

### 2. `ingest_universe_csv(path, store)`(新建,轻量,放 `universe_store.py` 或 `file_ingest`)

BYO-CSV 接入(真实指数成员资格常以此形式给出)。列:`instrument_id,start_date,end_date`
(end 可空)。逐行:校验 ISO + end≥start → `store.add_membership(...)`;坏行记入
`rejected (index, type_name, detail)` 不崩。返回 `UniverseIngestionReport(rows_read, added, rejected)`。

### 3. 日期桥接 `_iso(event_time:int)->str`(放 `panel_builder`)

`panel_builder` 的 `event_time` 是整数 `YYYYMMDD`;universe 用 ISO 串。
`_iso(20240315) -> "2024-03-15"`:`f"{et//10000:04d}-{(et//100)%100:02d}-{et%100:02d}"`,纯格式化、确定性。
成员比较是字典序,ISO 零填充下正确。非法 et(非 8 位 YYYYMMDD)→ 抛 `ValueError`。

### 4. 无幸存者偏差过滤(扩展 `panel_builder.build_panel_periods`)

签名加 `universe: Optional[PITUniverse] = None`。给定时,在周期 t 的逐标的循环开头:
```
if universe is not None and not universe.is_member_as_of(inst, _iso(t)):
    continue                      # t 时刻非成员 → 排除出该横截面
```
- **成员资格只在决策时刻 t 判定**;前向收益 [t, t+fwd_horizon] 照常实现(持有期内退出指数/
  退市是应计入的真实风险)。教科书级 PIT 正确性。
- 现有"前向需真实后续观测"的退市守卫保留。
- `universe=None` → 该过滤完全不执行,逐字节同现状(零回归)。
- `make_data_periods_provider(..., universe=None)` 透传。

### 5. 日历缺口检查(新函数 `universe_data_gaps`,放 `panel_builder`)

```
universe_data_gaps(store, universe, event_times) ->
    {"total_gaps": int, "by_date": {iso_date: missing_member_count}}
```
对每个 t:`members = universe.members_as_of(_iso(t))`;统计其中**当日 event_time==t 无 bar** 的成员数
(`store.as_of(inst, t)` 中无 event_time==t 的记录,或更简单 `not store.contains(inst, t, t)` 等价判断 —
实现用 `store.as_of` 取该 inst 截至 t 的最新观测并检查其 event_time 是否 == t)。独立函数,不进
`build_panel_periods`,保持其单一职责。

### 6. 接入 session(扩展 `run_research_session`)

签名加 `universe: Optional[PITUniverse] = None`。透传给 provider→panel_builder;若给定 universe,
额外计算 `universe_data_gaps` 摘要进返回 dict 的 `data_gaps` 字段。默认 None → 现状不变。

## 错误处理 / 失败关闭

- store 未初始化/损坏 → `DataStoreError`。
- `add_membership`/ingest:非 ISO 日期、end<start → 拒(ingest 记 rejected;直接调用抛 `ValueError`)。
- `_iso`:非法整数 → `ValueError`。

## 确定性

无 RNG、无墙钟;成员遍历按现有 `PITUniverse` 确定性集合;`_iso` 纯格式化。同输入同输出。

## 测试矩阵(`tests/data/test_universe_store.py` + `tests/research/test_panel_universe.py`)

1. `SqliteUniverseStore` 往返:add→save→load_into→`members_as_of`/`is_member_as_of` 正确;含
   退市-后-重纳入的多区间;未初始化 store → `DataStoreError`(fail-closed)。
2. `ingest_universe_csv`:好行入库计数正确;坏行(坏 ISO、end<start、缺列)进 rejected 不崩。
3. `_iso(20240315)=="2024-03-15"`;非法 et 抛 `ValueError`。
4. **无幸存者偏差**(核心):构造 universe,某 inst 在 t 之后才入会 → 被排除于 t 的横截面;
   某 inst 在 t 是成员、t 之后退市 → **仍纳入** t(前向收益照常用其真实后续观测);
   `universe=None` → 与不传 universe 逐字节相同的 periods(零回归)。
5. `universe_data_gaps`:成员在某交易日无 bar → 计入 `total_gaps`/`by_date`。
6. `run_research_session(..., universe=...)`:跑通、contained(无 APPROVED)、确定性、`data_gaps` 摘要存在。

## 涉及文件

- 新建 `services/data/universe_store.py` —— `SqliteUniverseStore`、`ingest_universe_csv`、`UniverseIngestionReport`。
- 修改 `services/research/panel_builder.py` —— `_iso`、`build_panel_periods(universe=...)`、
  `make_data_periods_provider(universe=...)`、`universe_data_gaps`。
- 修改 `services/research/session.py` —— `run_research_session(universe=...)` + `data_gaps` 摘要。
- 新建 `tests/data/test_universe_store.py`、`tests/research/test_panel_universe.py`。
- 复用不动:`services/data/universe.py`(PITUniverse)、`persistent_store.DataStoreError`。

## 安全审查

- 离线、纯 stdlib、无网络/SDK/凭证;只读历史成员 CSV。
- 纯加法、opt-in;默认路径零回归;无 lookahead(成员资格按 as-of 判定,与 PIT 一致)。
- 不改契约/前端/后端合约门。
