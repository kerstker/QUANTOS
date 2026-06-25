# 真实市场数据接入 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 建一个离线公开-CSV 适配器,把真实历史日线 CSV(Date,OHLCV)安全、确定性、防泄漏地映射进 PIT 数据湖,使整条研究方法论能在真实信号上运行。

**架构:** 把 `file_ingest` 的核心失败关闭循环抽成共享 `_ingest_records`;新增 `market_csv.ingest_market_csv` 复用它,只多一层"真实 CSV 列/日期 → 规范行"的转换。纯加法、零回归、无网络。

**技术栈:** 纯 Python stdlib(`csv` + `datetime.strptime`),确定性,无第三方库、无网络、无 SDK。

## 全局约束

- 纯 Python stdlib;无网络/券商/CTP/FIX SDK/凭证;真实数据由用户离线自带 CSV。
- 确定性:无 RNG、无墙钟(`strptime` 是纯解析,非 `now()`)。
- 失败关闭:坏行进 `rejected (index, type_name, detail)`,不崩;配置级错误硬 raise。
- 复用现有 `normalize_bar`/`Bar` 校验/store 去重/`content_hash`/`quality`,不削弱护栏。
- 零回归:既有 `tests/data/test_file_ingest.py` 在重构后必须全绿(含缺 knowledge_time 的硬 raise)。
- `event_time = YYYYMMDD` 整数(单调;panel_builder 按位置取前向,间距无关);`knowledge_time = event_time`(保持 bitemporal `kt>=event_time`)。
- 文档/注释中文,代码标识符英文。提交脚注 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 单测隔离需 `PYTHONPATH=.`;全量门禁 `python scripts/validate.py`。

## 文件结构

- 修改 `services/data/file_ingest.py` —— 抽取 `_ingest_records` + `FatalIngestionError`;`ingest_bars_csv` 改为构造 records 并委托。
- 新建 `services/data/market_csv.py` —— `ingest_market_csv`、`DEFAULT_COLUMN_MAP`、日期→event_time 转换。
- 新建 `tests/data/test_market_csv.py` —— 适配器单测 + 端到端。
- (回归)`tests/data/test_file_ingest.py` 不改、保持全绿。

---

### Task 1: 抽取共享 `_ingest_records`(重构,既有测试守护)

**Files:**
- Modify: `services/data/file_ingest.py`
- Test(回归): `tests/data/test_file_ingest.py`(不改)

**Interfaces:**
- Produces:
  - `class FatalIngestionError(ValueError)` —— 配置级错误,不被吞为 rejected 行。
  - `_ingest_records(records, parse, store, *, now, dataset_id, version) -> IngestionReport` —— `records` 产出 `(index, raw)`;`parse(raw) -> (canonical_row_dict, knowledge_time)` 可抛(→ rejected);抛 `FatalIngestionError` 则向上传播。
  - `ingest_bars_csv(...)` 签名与行为不变(委托实现)。

- [ ] **Step 1: 用整个新版替换 `file_ingest.py`**

```python
"""Read-only local-CSV ingestion adapter (no network, no broker).

Reads a local CSV of bars, normalizes + rejects future/invalid rows (recorded,
not crashed), dedups via the persisted store, and returns an IngestionReport with
a quality report and a reproducible content hash. The shared `_ingest_records`
core is reused by the real-market-data adapter (market_csv.py)."""
from __future__ import annotations

import csv
from dataclasses import dataclass

from .normalization import normalize_bar, reject_if_future
from .quality import build_report
from .versioning import content_hash_of

_BAR_COLS = ("instrument_id", "event_time", "open", "high", "low", "close", "volume")


class FatalIngestionError(ValueError):
    """配置级接入错误,必须向上抛,不能被吞成 rejected 行
    (例如某行缺 knowledge_time 且未提供默认值)。"""


@dataclass(frozen=True)
class IngestionReport:
    rows_read: int
    bars_ingested: int
    duplicates: int
    rejected: tuple
    content_hash: str
    quality: object


def _ingest_records(records, parse, store, *, now, dataset_id, version) -> IngestionReport:
    """共享失败关闭接入核心。`records` 产出 (index, raw);`parse(raw)` 返回
    (canonical_row_dict, knowledge_time),可抛(→ rejected)。parse 抛
    FatalIngestionError 则向上传播(配置错误,非坏行)。canonical_row_dict 含
    instrument_id/event_time/OHLCV,不含 knowledge_time。"""
    rows_read = 0
    valid_bars, rejected = [], []
    bars_ingested = 0
    duplicates = 0
    for i, raw in records:
        rows_read += 1
        try:
            canonical, kt = parse(raw)
            bar = normalize_bar(canonical, kt)
            if now is not None:
                reject_if_future(bar, now)
        except FatalIngestionError:
            raise
        except Exception as exc:
            rejected.append((i, type(exc).__name__, str(exc)))   # index, class, detail
            continue
        valid_bars.append(bar)
        if store.contains(bar.instrument_id, bar.event_time, bar.knowledge_time):
            duplicates += 1
        elif store.add(bar.instrument_id, bar):
            bars_ingested += 1
        else:
            duplicates += 1
    return IngestionReport(
        rows_read=rows_read, bars_ingested=bars_ingested, duplicates=duplicates,
        rejected=tuple(rejected),
        content_hash=content_hash_of(valid_bars) if valid_bars else "",
        quality=build_report(dataset_id, sorted(b.event_time for b in valid_bars), as_of=now, version=version),
    )


def ingest_bars_csv(path: str, store, *, knowledge_time_default=None, now=None,
                    dataset_id: str = "ingested", version: str = "") -> IngestionReport:
    def parse(raw):
        row = {k: v for k, v in raw.items() if v not in ("", None)}
        kt_raw = row.pop("knowledge_time", None)
        if kt_raw is None and knowledge_time_default is None:
            raise FatalIngestionError("row has no knowledge_time and no knowledge_time_default supplied")
        kt = int(kt_raw) if kt_raw is not None else int(knowledge_time_default)
        canonical = {k: v for k, v in row.items() if k in _BAR_COLS}
        return canonical, kt

    with open(path, newline="", encoding="utf-8") as fh:
        records = list(enumerate(csv.DictReader(fh)))
    return _ingest_records(records, parse, store, now=now, dataset_id=dataset_id, version=version)
```

- [ ] **Step 2: 跑既有接入测试确认零回归**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/data/test_file_ingest.py -q`
Expected: PASS(5 passed —— 含 `test_missing_knowledge_time_without_default_raises` 仍硬 raise)

- [ ] **Step 3: 提交**

```bash
git add services/data/file_ingest.py
git commit -m "refactor(data): 抽取共享 _ingest_records 接入核心(零回归)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `ingest_market_csv` 真实 CSV 适配器

**Files:**
- Create: `services/data/market_csv.py`
- Test: `tests/data/test_market_csv.py`

**Interfaces:**
- Consumes: `_ingest_records`、`IngestionReport`(Task 1);`SqlitePITStore`。
- Produces: `DEFAULT_COLUMN_MAP: dict`;`ingest_market_csv(path, store, *, instrument_id, column_map=None, date_format="%Y-%m-%d", now=None, dataset_id="market", version="") -> IngestionReport`。

- [ ] **Step 1: 写失败测试**

```python
import csv
import pytest
from services.data.persistent_store import SqlitePITStore
from services.data.market_csv import ingest_market_csv, DEFAULT_COLUMN_MAP


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


_STD_HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]


def test_maps_real_format_into_store(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000000],
        ["2024-03-18", 100.5, 102.0, 100.0, 101.5, 1200000],   # 周末跳空(16/17)
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert rep.rows_read == 2 and rep.bars_ingested == 2 and rep.rejected == ()
    ets = [b.event_time for b in store.as_of("AAA", as_of_time=99999999)]
    assert ets == [20240315, 20240318]                          # event_time == YYYYMMDD
    assert rep.content_hash and rep.quality.passed


def test_custom_column_map_and_date_format(tmp_path):
    p = tmp_path / "bbb.csv"
    _write(p, ["d", "o", "h", "l", "c", "v"], [
        ["20240102", 10.0, 10.5, 9.8, 10.2, 5000],
    ])
    store = SqlitePITStore(":memory:", create=True)
    cmap = {"date": "d", "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"}
    rep = ingest_market_csv(str(p), store, instrument_id="BBB",
                            column_map=cmap, date_format="%Y%m%d")
    assert rep.bars_ingested == 1
    assert [b.event_time for b in store.as_of("BBB", as_of_time=99999999)] == [20240102]


def test_bad_rows_rejected_good_rows_kept(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000],    # ok
        ["not-a-date", 100.0, 101.0, 99.0, 100.5, 1000],    # 坏日期
        ["2024-03-18", 100.0, 90.0, 99.0, 100.5, 1000],     # high<low → OHLC 矛盾
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert rep.bars_ingested == 1 and len(rep.rejected) == 2     # 失败关闭,不崩
    classes = {r[1] for r in rep.rejected}
    assert "ValueError" in classes


def test_missing_mapped_column_rejected(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, ["Date", "Open", "High", "Low", "Close"], [      # 无 Volume 列
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5],
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert rep.bars_ingested == 0 and len(rep.rejected) == 1     # 缺列 → 该行进 rejected


def test_reingest_idempotent_same_hash(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    a = ingest_market_csv(str(p), store, instrument_id="AAA")
    b = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert a.bars_ingested == 1 and b.bars_ingested == 0 and b.duplicates == 1
    assert a.content_hash == b.content_hash


def test_now_rejects_future_dates(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000],
        ["2099-01-01", 100.0, 101.0, 99.0, 100.5, 1000],     # 未来
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA", now=20240601)
    assert rep.bars_ingested == 1 and len(rep.rejected) == 1
    assert rep.rejected[0][1] == "FutureDataError"


def test_bad_column_map_raises():
    store = SqlitePITStore(":memory:", create=True)
    with pytest.raises(ValueError):
        ingest_market_csv("nonexistent.csv", store, instrument_id="X",
                          column_map={"date": "Date"})           # 缺 OHLCV 键
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/data/test_market_csv.py -q`
Expected: FAIL —— `ModuleNotFoundError: services.data.market_csv`

- [ ] **Step 3: 写实现**

```python
"""离线公开-CSV 适配器:把真实历史日线 CSV(Date,OHLCV)映射进 PIT 数据湖。

无网络、无券商、无凭证 —— 仅读用户离线自带的本地 CSV(BYO-CSV)。复用共享的失败关闭
接入核心(normalize/reject-future/dedup/hash/quality)。一个文件 = 一个标的
(instrument_id 由调用方注入,因真实 CSV 通常无此列)。

event_time = YYYYMMDD 整数(单调可排序;panel_builder 按位置取前向,故非均匀间距安全)。
knowledge_time = event_time(EOD 当日收盘即可知;as_of 过滤无 lookahead)。
"""
from __future__ import annotations

import csv
from datetime import datetime

from .file_ingest import IngestionReport, _ingest_records

DEFAULT_COLUMN_MAP = {"date": "Date", "open": "Open", "high": "High",
                      "low": "Low", "close": "Close", "volume": "Volume"}

_REQUIRED_KEYS = ("date", "open", "high", "low", "close", "volume")


def ingest_market_csv(path: str, store, *, instrument_id: str, column_map=None,
                      date_format: str = "%Y-%m-%d", now=None,
                      dataset_id: str = "market", version: str = "") -> IngestionReport:
    cmap = dict(DEFAULT_COLUMN_MAP if column_map is None else column_map)
    missing = [k for k in _REQUIRED_KEYS if k not in cmap]
    if missing:
        raise ValueError(f"column_map missing keys: {missing}")

    def parse(raw):
        dt = datetime.strptime(raw[cmap["date"]], date_format)
        event_time = dt.year * 10000 + dt.month * 100 + dt.day
        canonical = {
            "instrument_id": instrument_id,
            "event_time": event_time,
            "open": float(raw[cmap["open"]]),
            "high": float(raw[cmap["high"]]),
            "low": float(raw[cmap["low"]]),
            "close": float(raw[cmap["close"]]),
            "volume": float(raw[cmap["volume"]]),
        }
        return canonical, event_time     # knowledge_time = event_time(EOD)

    with open(path, newline="", encoding="utf-8") as fh:
        records = list(enumerate(csv.DictReader(fh)))
    return _ingest_records(records, parse, store, now=now, dataset_id=dataset_id, version=version)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/data/test_market_csv.py -q`
Expected: PASS(7 passed)

- [ ] **Step 5: 提交**

```bash
git add services/data/market_csv.py tests/data/test_market_csv.py
git commit -m "feat(data): 真实市场数据离线 CSV 适配器 ingest_market_csv

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 端到端 —— 真实格式数据跑通整条方法论

**Files:**
- Test: `tests/data/test_market_csv.py`(追加端到端用例)

**Interfaces:**
- Consumes: `ingest_market_csv`(Task 2);`run_research_session`、`HypothesisSpace`、`AlphaCatalog`、`ExperimentRegistry`/`SqliteExperimentStore`、`AlphaState`。

- [ ] **Step 1: 写失败测试**(追加到 `tests/data/test_market_csv.py`)

```python
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.intelligence.search_space import HypothesisSpace
from services.research.session import run_research_session


def _ingest_universe(tmp_path):
    # 3 个标的、同一段交易日(含周末跳空),真实格式;价量随标的/日期确定性变化。
    store = SqlitePITStore(":memory:", create=True)
    days = ["2024-03-%02d" % d for d in range(4, 23) if d % 7 not in (0, 6)]  # 跳过周末
    for k, inst in enumerate(("AAA", "BBB", "CCC")):
        p = tmp_path / f"{inst}.csv"
        rows = []
        for j, d in enumerate(days):
            base = 10.0 + k + 0.1 * ((j * (k + 1)) % 5)       # 确定性的真实型价
            rows.append([d, base, base + 1.0, base - 0.5, base + 0.2 * ((j + k) % 3), 1000 + j])
        _write(p, _STD_HEADER, rows)
        rep = ingest_market_csv(str(p), store, instrument_id=inst)
        assert rep.bars_ingested == len(days) and rep.rejected == ()
    return store


def test_end_to_end_real_format_runs_methodology(tmp_path):
    store = _ingest_universe(tmp_path)
    instruments = store.keys()
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, 0, 99999999)})
    reg = ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))
    space = HypothesisSpace(factors=("AAA",), operators=("momentum", "zscore"), windows=(3, 5))
    out = run_research_session(store, instruments, event_times=event_times, fwd_horizon=1,
                               space=space, rounds=4, batch_size=2, timestamp=20240601, registry=reg)
    assert out["dataset_version"]                       # 真实数据内容派生版本
    assert out["n_evaluated"] >= 1                      # 方法论在真实格式数据上跑出评估
    cat = AlphaCatalog()
    reg.load_into(cat)
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())   # contained


def test_end_to_end_is_deterministic(tmp_path):
    a_store = _ingest_universe(tmp_path / "a")
    b_store = _ingest_universe(tmp_path / "b")
    kw = dict(fwd_horizon=1, space=HypothesisSpace(factors=("AAA",),
              operators=("momentum", "zscore"), windows=(3, 5)),
              rounds=4, batch_size=2, timestamp=20240601)
    a = run_research_session(a_store, a_store.keys(),
                             event_times=sorted({b.event_time for inst in a_store.keys()
                                                 for b in a_store.fetch_bars(inst, 0, 99999999)}), **kw)
    b = run_research_session(b_store, b_store.keys(),
                             event_times=sorted({b.event_time for inst in b_store.keys()
                                                 for b in b_store.fetch_bars(inst, 0, 99999999)}), **kw)
    assert a["dataset_version"] == b["dataset_version"] and a["arm_means"] == b["arm_means"]
```

注:`tmp_path / "a"` 与 `tmp_path / "b"` 子目录需先建,`_ingest_universe` 内 `tmp_path` 形参直接用;Step 1 写测试时若子目录不存在,`_write` 的 `open(...,"w")` 会因父目录缺失失败 —— 在 `_ingest_universe` 首行加 `tmp_path.mkdir(parents=True, exist_ok=True)`。

- [ ] **Step 2: 跑测试确认失败 → 修子目录 → 通过**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/data/test_market_csv.py -q -k end_to_end`
Expected: 先失败(若子目录问题)→ 加 `tmp_path.mkdir(parents=True, exist_ok=True)` 于 `_ingest_universe` 首行 → PASS(2 passed)

- [ ] **Step 3: 跑整文件**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/data/test_market_csv.py -q`
Expected: PASS(9 passed)

- [ ] **Step 4: 提交**

```bash
git add tests/data/test_market_csv.py
git commit -m "test(data): 端到端真实格式数据 → run_research_session(contained+确定性)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 全量门禁 + 收口

**Files:** 无新增(验证 + 记忆)。

- [ ] **Step 1: 全量门禁**

Run: `cd H:/code/QUANTOS && python scripts/validate.py`
Expected: 治理扫描 0 违规 + 全部 pytest 绿。

- [ ] **Step 2: 同步 main**

```bash
git branch -f main master
```

- [ ] **Step 3: 更新进度记忆**

更新 `program-state-complete.md`:真实市场数据接入 DONE(market_csv 适配器 + 端到端);说明这是把方法论从合成演示变为可跑真实信号的关键钥匙;REMAINING 缩减。

## 自检

- **Spec 覆盖:** §架构(_ingest_records 重构)→Task 1;§每行转换(列映射/日期→event_time/kt=event_time)→Task 2;§数据流端到端→Task 3;§失败关闭(坏行/缺列/未来)→Task 2;§确定性→Task 2+3;§测试矩阵 1–7→Task 1(7)+Task 2(1–5)+Task 3(6)。无遗漏。
- **占位符:** 无 TBD/TODO;每个代码步骤含完整代码。
- **类型一致:** `_ingest_records(records, parse, store, *, now, dataset_id, version)`、`parse(raw)->(dict,int)`、`ingest_market_csv` 签名、`DEFAULT_COLUMN_MAP` 键集在 Task 1–3 间一致;`FatalIngestionError(ValueError)` 子类保证既有 `pytest.raises(ValueError)` 仍通过;`event_time=YYYYMMDD`、`Bar.volume: float` 已核对契约。
