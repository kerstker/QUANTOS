# PIT 基本面数据 + 价值因子(数据湖子项)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/data/` + `services/research/`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、确定性、失败关闭、防泄漏。

## 目标

引入**基本面数据域**(财报/估值),让研究方法论能构建**价值因子**(E/P 盈利收益率、
B/M 账面市值比等 = 基本面 ÷ 价格)——一个区别于价格/动量的真实 alpha 来源。全程 PIT
防泄漏:t 时刻只见 t 之前已公告的基本面。

## 关键约束

- 纯 Python stdlib;确定性;失败关闭;防泄漏(bitemporal `knowledge_time>=event_time`)。
- **复用 `FeatureValue(BitemporalRecord)` 契约**(`feature_id, instrument_id, value`)——不新增契约。
- 复用持久化模式(`SqlitePITStore`/`ExperimentStore`)与 universe 门控、退市守卫。
- 纯加法;不改前端/后端合约/`*Payload`。无网络/券商/凭证(BYO-CSV)。

## 非目标(YAGNI)

- 本子项不把基本面假设接入 `run_research_session`(其 HypothesisSpace 是价格算子的;
  基本面 space 属后续)。provider 可被 GuidedSearchLoop/独立调用。
- 不做复杂会计口径(TTM 滚动、合并报表调整);value 为调用方预先算好的指标值。
- 不做多币种。

## 架构

### 1. 基本面点 = `FeatureValue`(复用)

一个基本面观测 = bitemporal `(feature_id=指标名, instrument_id, value)`:
- `event_time` = 财报期末(YYYYMMDD 整数)。
- `knowledge_time` = 公告日(YYYYMMDD 整数)。`kt>=et` 天然成立(期末后才公告)。
- `value` = 该指标值(如 earnings_ttm、book_value),调用方预先算好。

### 2. `SqliteFundamentalStore`(新建 `services/data/fundamental_store.py`)

durable sqlite3,表:
```
fundamentals(feature_id TEXT, instrument_id TEXT, event_time INTEGER,
             knowledge_time INTEGER, value REAL,
             PRIMARY KEY(feature_id, instrument_id, event_time, knowledge_time))
```
- PK 含 `knowledge_time` → **重述天然保留**(同期末、不同公告日 = 不同行)。
- `add(fv: FeatureValue) -> bool`(PK 去重)。
- `as_of(feature_id, instrument_id, as_of_time) -> list[FeatureValue]`:`knowledge_time<=as_of` 的可知值,
  按 `(event_time, knowledge_time)` 有序(防泄漏)。
- `latest_as_of(feature_id, instrument_id, as_of_time) -> FeatureValue | None`:t 时刻最新可知值
  (列表最后一个)。
- fail-closed:未初始化/损坏 → `DataStoreError`(复用 `persistent_store.DataStoreError`)。

### 3. `ingest_fundamentals_csv(path, store)`(放 `fundamental_store.py`)

BYO-CSV。列 `instrument_id,metric,event_time,knowledge_time,value`(event/knowledge 为 YYYYMMDD 整数)。
逐行:`int(event_time)`、`int(knowledge_time)`、`float(value)`;构造 `FeatureValue(feature_id=metric, ...)`
(pydantic 校验 `kt>=et` 防泄漏);坏行(`kt<et`、value 非数、缺列、坏整数)记入
`rejected (index, type_name, detail)` 不崩。返回 `FundamentalIngestionReport(rows_read, added, duplicates, rejected)`。

### 4. 基本面因子构建(新建 `services/research/fundamental_factors.py`)

```
build_fundamental_periods(fund_store, price_store, instruments, metric, *,
                          event_times, fwd_horizon, mode="ratio_to_price", universe=None)
    -> list[PanelPeriod]
```
每周期 t(按位置取前向,同 `panel_builder`;复用 `_iso` 做 universe 门控):
- universe 门控(给定时):`is_member_as_of(inst, _iso(t))` 否则跳过(无幸存者偏差)。
- `fval = fund_store.latest_as_of(metric, inst, t)` —— t 时刻最新可知基本面(防泄漏)。
- `mode=="ratio_to_price"`:`pbar = price_store.latest_as_of(inst, t)`;需 `fval not None and pbar and pbar.close>0`
  → `signal = fval / pbar.close`(E/P、B/M);否则跳过。
- `mode=="raw"`:`signal = fval`(None 则跳过)。
- 前向收益取自 price_store:`now_bar=latest_as_of(inst,t)`、`fwd_bar=latest_as_of(inst,t_fwd)`;
  需 `fwd_bar.event_time > now_bar.event_time and now_bar.close>0`(退市守卫)→ `fwd = fwd_bar.close/now_bar.close-1`。
- ≥2 存活才 emit `PanelPeriod(instruments, signals, forward_returns)`。
- `mode` 非法 → `ValueError`。

```
make_fundamental_periods_provider(fund_store, price_store, instruments, *,
                                  event_times, fwd_horizon, universe=None) -> Callable
```
`provider(defn)`:`metric=defn.params["metric"]`、`mode=defn.params.get("mode","ratio_to_price")`
→ `build_fundamental_periods(...)`。供 GuidedSearchLoop 直接用。

## PIT 对齐 / 防泄漏

两 store 均 `latest_as_of(...t)`,只见 t 可知数据。基本面在 t 用"截至 t 最新已公告"(可能上季度,
正确);价格用 t 最新可知收盘。无 lookahead。

## 错误处理 / 失败关闭

- store 未初始化/损坏 → `DataStoreError`。
- ingest 坏行 → rejected(`FeatureValue` 的 `kt>=et` 校验拦截泄漏行)。
- `build_fundamental_periods` 非法 mode → `ValueError`。

## 确定性

无 RNG、无墙钟;`instruments` 排序遍历;store 有序查询;同输入同输出。

## 测试矩阵

`tests/data/test_fundamental_store.py`:
1. add→`as_of`(t 之前未公告的基本面**不可见**)→`latest_as_of`;重述保留(两公告日,latest_as_of 取最新可知);PK 去重。
2. 未初始化 store → `DataStoreError`。
3. `ingest_fundamentals_csv`:好行入库;坏行(`kt<et` 泄漏、value 非数、缺列)进 rejected 不崩。

`tests/research/test_fundamental_factors.py`:
4. raw 模式:signal=基本面值;PIT(t 时未公告 → 该 inst 当期丢弃)。
5. ratio_to_price:signal=fval/close;缺价(close≤0)或缺 fval → 丢弃;E/P 数值合理。
6. universe 门控生效(非成员被排除)。
7. `provider(defn)` 产出周期(guided-search 兼容);非法 mode 抛 `ValueError`。
8. 确定性:同输入两次同结果。

## 涉及文件

- 新建 `services/data/fundamental_store.py` —— `SqliteFundamentalStore`、`ingest_fundamentals_csv`、`FundamentalIngestionReport`。
- 新建 `services/research/fundamental_factors.py` —— `build_fundamental_periods`、`make_fundamental_periods_provider`。
- 新建 `tests/data/test_fundamental_store.py`、`tests/research/test_fundamental_factors.py`。
- 复用不动:`quantos/contracts/feature.FeatureValue`、`persistent_store.DataStoreError`、
  `panel_builder._iso`、`alpha/panel_backtest.PanelPeriod`、`data/universe.PITUniverse`。

## 安全审查

- 离线、纯 stdlib、无网络/SDK/凭证;只读历史基本面 CSV。
- 防泄漏:bitemporal + as_of 双重保证;价值因子用 PIT 对齐的基本面与价格。
- 纯加法;不改契约/前端/后端合约门。
