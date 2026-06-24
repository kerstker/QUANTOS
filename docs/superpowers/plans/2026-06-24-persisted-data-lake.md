# Persisted PIT Data Store + CSV Ingestion (sub-project 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A persisted, PIT-correct, deterministic sqlite bar store that survives process restart, plus a read-only local-CSV ingestion adapter (normalize → reject-future → dedup → quality report → persist).

**Architecture:** Two new modules in `services/data/`: `persistent_store.py` (SqlitePITStore mirroring PITStore + MarketDataSource) and `file_ingest.py` (ingest_bars_csv → IngestionReport). Reuse Bar contract + normalize_bar/reject_if_future/quality.build_report/content_hash_of. In-memory PITStore untouched.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `csv`, `json`); pydantic `Bar`.

## Global Constraints

- L2_DRY_RUN_ONLY; pure-Python stdlib; deterministic (no RNG; stable ORDER BY); fail-closed; no live surface (no network/broker — ingestion reads a LOCAL file, writes to the research store only).
- `as_of` is lookahead-safe: SQL filter `knowledge_time <= as_of_time`.
- Reading an uninitialized/corrupt store raises `DataStoreError` (never reads as empty).
- Reuse: `quantos.contracts.data.Bar`; `services.data.normalization.{normalize_bar, reject_if_future, FutureDataError}`; `services.data.quality.build_report`; `services.data.versioning.content_hash_of`. Do NOT modify in-memory `PITStore`/`InMemoryMarketDataSource`.
- Gate: `python scripts/validate.py`. Isolated pytest: prefix `PYTHONPATH=H:/code/QUANTOS`.

**Reference signatures (existing):**
- `normalize_bar(raw: dict, knowledge_time: int) -> Bar` — `Bar(knowledge_time=kt, **raw)`; raises if `raw` contains `knowledge_time`.
- `reject_if_future(bar, now) -> Bar` — raises `FutureDataError` if `bar.event_time > now`.
- `quality.build_report(dataset_id, event_times, as_of=None, ..., version="") -> DataQualityReport`.
- `versioning.content_hash_of(records) -> str` (order-independent).
- `Bar` fields: `instrument_id, open, high, low, close, volume, event_time, knowledge_time`; `Bar.model_validate(dict)`, `Bar.model_dump(mode="json")`.

---

### Task 1: SqlitePITStore

**Files:**
- Create: `services/data/persistent_store.py`
- Test: `tests/data/test_persistent_store.py`

**Interfaces:**
- Produces: `class DataStoreError(Exception)`; `class SqlitePITStore:` `__init__(self, path, *, create=False)`; `add(self, key, bar) -> bool` (True if newly inserted); `add_many(self, key, bars) -> int` (count inserted); `as_of(self, key, as_of_time, event_time_max=None) -> list[Bar]`; `latest_as_of(self, key, as_of_time, event_time_max=None) -> Optional[Bar]`; `fetch_bars(self, instrument_id, start, end) -> list[Bar]`; `contains(self, key, event_time, knowledge_time) -> bool`; `keys(self) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/data/test_persistent_store.py
import sqlite3
import pytest
from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore, DataStoreError


def _bar(inst, et, kt, close):
    return Bar(instrument_id=inst, event_time=et, knowledge_time=kt,
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def _store():
    return SqlitePITStore(":memory:", create=True)


def test_as_of_excludes_future_knowledge_then_includes_it():
    s = _store()
    s.add("X", _bar("X", 10, 5, 100.0))      # knowable at kt=5
    s.add("X", _bar("X", 20, 50, 101.0))     # knowable only at kt=50
    early = s.as_of("X", as_of_time=30)
    assert [b.event_time for b in early] == [10]            # the kt=50 bar is hidden
    late = s.as_of("X", as_of_time=60)
    assert [b.event_time for b in late] == [10, 20]


def test_restatement_kept_and_latest_as_of_is_newest_knowledge():
    s = _store()
    s.add("X", _bar("X", 10, 5, 100.0))
    s.add("X", _bar("X", 10, 40, 105.0))     # restatement of the same event, later knowledge
    rows = s.as_of("X", as_of_time=60)
    assert len(rows) == 2                                    # both retained
    assert s.latest_as_of("X", as_of_time=60).close == 105.0


def test_dedup_identical_bar():
    s = _store()
    assert s.add("X", _bar("X", 10, 5, 100.0)) is True
    assert s.add("X", _bar("X", 10, 5, 100.0)) is False     # identical PK ignored
    assert len(s.as_of("X", as_of_time=60)) == 1


def test_persists_across_connections(tmp_path):
    p = str(tmp_path / "lake.db")
    s1 = SqlitePITStore(p, create=True)
    s1.add("X", _bar("X", 10, 5, 100.0))
    s2 = SqlitePITStore(p)                                   # reopen, no create
    assert [b.event_time for b in s2.as_of("X", as_of_time=60)] == [10]


def test_fetch_bars_window_and_keys():
    s = _store()
    s.add_many("X", [_bar("X", t, t, 100.0 + t) for t in (10, 20, 30)])
    assert [b.event_time for b in s.fetch_bars("X", 15, 25)] == [20]
    assert s.keys() == ["X"]


def test_uninitialized_store_fails_closed(tmp_path):
    p = str(tmp_path / "empty.db")
    sqlite3.connect(p).close()                               # file exists, no schema
    with pytest.raises(DataStoreError):
        SqlitePITStore(p, create=False).as_of("X", as_of_time=10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/data/test_persistent_store.py -v`
Expected: FAIL with `ModuleNotFoundError: services.data.persistent_store`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/data/persistent_store.py
"""Persisted point-in-time bar store (stdlib sqlite3).

Same lookahead-safe semantics as the in-memory PITStore (as_of filters
knowledge_time <= as_of_time), but durable across process restarts and indexed
for as_of queries. Also satisfies the MarketDataSource protocol (fetch_bars).
Fail-closed: reading an uninitialized/corrupt store raises rather than reading as
empty (which would silently drop history). Deterministic ordering."""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from quantos.contracts.data import Bar


class DataStoreError(Exception):
    """The store is missing, corrupt, or uninitialized — fail closed."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    key TEXT NOT NULL,
    event_time INTEGER NOT NULL,
    knowledge_time INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (key, event_time, knowledge_time)
);
CREATE INDEX IF NOT EXISTS ix_bars_key_kt ON bars (key, knowledge_time);
"""


class SqlitePITStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bars'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"store unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("store uninitialized; missing 'bars' table")

    def contains(self, key: str, event_time: int, knowledge_time: int) -> bool:
        self._require_schema()
        row = self._conn.execute(
            "SELECT 1 FROM bars WHERE key=? AND event_time=? AND knowledge_time=?",
            (key, event_time, knowledge_time)).fetchone()
        return row is not None

    def add(self, key: str, bar: Bar) -> bool:
        self._require_schema()
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO bars(key, event_time, knowledge_time, payload_json) VALUES (?,?,?,?)",
            (key, bar.event_time, bar.knowledge_time, json.dumps(bar.model_dump(mode="json"), sort_keys=True)))
        self._conn.commit()
        return cur.rowcount == 1

    def add_many(self, key: str, bars) -> int:
        return sum(1 for b in bars if self.add(key, b))

    def _query(self, key, as_of_time, event_time_max, *, limit_sql=""):
        self._require_schema()
        sql = "SELECT payload_json FROM bars WHERE key=? AND knowledge_time<=?"
        args = [key, as_of_time]
        if event_time_max is not None:
            sql += " AND event_time<=?"
            args.append(event_time_max)
        sql += " ORDER BY event_time, knowledge_time" + limit_sql
        return [Bar.model_validate(json.loads(r["payload_json"]))
                for r in self._conn.execute(sql, args)]

    def as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None) -> list:
        return self._query(key, as_of_time, event_time_max)

    def latest_as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None):
        rows = self._query(key, as_of_time, event_time_max)
        return rows[-1] if rows else None

    def fetch_bars(self, instrument_id: str, start: int, end: int) -> list:
        self._require_schema()
        rows = self._conn.execute(
            "SELECT payload_json FROM bars WHERE key=? AND event_time>=? AND event_time<=? "
            "ORDER BY event_time, knowledge_time", (instrument_id, start, end))
        return [Bar.model_validate(json.loads(r["payload_json"])) for r in rows]

    def keys(self) -> list:
        self._require_schema()
        return [r["key"] for r in self._conn.execute("SELECT DISTINCT key FROM bars ORDER BY key")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/data/test_persistent_store.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add services/data/persistent_store.py tests/data/test_persistent_store.py
git commit -m "feat(data): SqlitePITStore — persisted PIT bar store, fail-closed, MarketDataSource-compatible"
```

---

### Task 2: CSV ingestion adapter

**Files:**
- Create: `services/data/file_ingest.py`
- Test: `tests/data/test_file_ingest.py`

**Interfaces:**
- Consumes: `SqlitePITStore` (Task 1); `normalize_bar`/`reject_if_future`; `quality.build_report`; `content_hash_of`.
- Produces: `@dataclass(frozen=True) IngestionReport(rows_read, bars_ingested, duplicates, rejected: tuple, content_hash: str, quality)`; `ingest_bars_csv(path, store, *, knowledge_time_default=None, now=None, dataset_id="ingested", version="") -> IngestionReport`.

- [ ] **Step 1: Write the failing test**

```python
# tests/data/test_file_ingest.py
import csv
import pytest
from services.data.persistent_store import SqlitePITStore
from services.data.file_ingest import ingest_bars_csv


_HEADER = ["instrument_id", "event_time", "knowledge_time", "open", "high", "low", "close", "volume"]


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_HEADER)
        w.writerows(rows)


def test_ingests_valid_rows_and_persists(tmp_path):
    p = tmp_path / "bars.csv"
    _write_csv(p, [["X", 10, 5, 100, 101, 99, 100, 1000],
                   ["X", 20, 15, 101, 102, 100, 101, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_bars_csv(str(p), store)
    assert rep.rows_read == 2 and rep.bars_ingested == 2 and rep.duplicates == 0
    assert [b.event_time for b in store.as_of("X", as_of_time=60)] == [10, 20]
    assert rep.content_hash and rep.quality.passed


def test_future_and_malformed_rows_rejected_not_crash(tmp_path):
    p = tmp_path / "bars.csv"
    _write_csv(p, [["X", 10, 5, 100, 101, 99, 100, 1000],     # ok
                   ["X", 999, 5, 100, 101, 99, 100, 1000],    # future (now=100)
                   ["X", 30, 25, 100, 90, 99, 100, 1000]])    # high<low -> invalid OHLC
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_bars_csv(str(p), store, now=100)
    assert rep.bars_ingested == 1 and len(rep.rejected) == 2
    reasons = {r[1] for r in rep.rejected}
    assert "FutureDataError" in reasons


def test_reingest_is_idempotent_same_hash(tmp_path):
    p = tmp_path / "bars.csv"
    _write_csv(p, [["X", 10, 5, 100, 101, 99, 100, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    a = ingest_bars_csv(str(p), store)
    b = ingest_bars_csv(str(p), store)
    assert a.bars_ingested == 1 and b.bars_ingested == 0 and b.duplicates == 1
    assert a.content_hash == b.content_hash
    assert len(store.as_of("X", as_of_time=60)) == 1


def test_missing_knowledge_time_without_default_raises(tmp_path):
    p = tmp_path / "bars.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["instrument_id", "event_time", "open", "high", "low", "close", "volume"])
        w.writerow(["X", 10, 100, 101, 99, 100, 1000])
    store = SqlitePITStore(":memory:", create=True)
    with pytest.raises(ValueError):
        ingest_bars_csv(str(p), store)            # no knowledge_time column and no default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/data/test_file_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: services.data.file_ingest`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/data/file_ingest.py
"""Read-only local-CSV ingestion adapter (no network, no broker).

Reads a local CSV of bars, normalizes + rejects future/invalid rows (recorded,
not crashed), dedups via the persisted store, and returns an IngestionReport with
a quality report and a reproducible content hash. The ONLY data path into the
persisted lake at L2."""
from __future__ import annotations

import csv
from dataclasses import dataclass

from .normalization import normalize_bar, reject_if_future
from .quality import build_report
from .versioning import content_hash_of


@dataclass(frozen=True)
class IngestionReport:
    rows_read: int
    bars_ingested: int
    duplicates: int
    rejected: tuple
    content_hash: str
    quality: object


def ingest_bars_csv(path: str, store, *, knowledge_time_default=None, now=None,
                    dataset_id: str = "ingested", version: str = "") -> IngestionReport:
    rows_read = 0
    ingested_bars, event_times, rejected = [], [], []
    duplicates = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for i, raw in enumerate(csv.DictReader(fh)):
            rows_read += 1
            row = {k: v for k, v in raw.items() if v != ""}
            kt_raw = row.pop("knowledge_time", None)
            if kt_raw is None and knowledge_time_default is None:
                raise ValueError("row has no knowledge_time and no knowledge_time_default supplied")
            try:
                kt = int(kt_raw) if kt_raw is not None else int(knowledge_time_default)
                row = {k: v for k, v in row.items() if k in
                       ("instrument_id", "event_time", "open", "high", "low", "close", "volume")}
                bar = normalize_bar(row, kt)
                if now is not None:
                    reject_if_future(bar, now)
            except Exception as exc:
                rejected.append((i, type(exc).__name__))
                continue
            if store.contains(bar.instrument_id, bar.event_time, bar.knowledge_time):
                duplicates += 1
            elif store.add(bar.instrument_id, bar):
                ingested_bars.append(bar)
                event_times.append(bar.event_time)
            else:
                duplicates += 1
    return IngestionReport(
        rows_read=rows_read, bars_ingested=len(ingested_bars), duplicates=duplicates,
        rejected=tuple(rejected),
        content_hash=content_hash_of(ingested_bars) if ingested_bars else "",
        quality=build_report(dataset_id, sorted(event_times), as_of=now, version=version),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/data/test_file_ingest.py -v`
Expected: PASS (4 passed). If `content_hash_of` requires pydantic records (it calls `r.model_dump`), note `ingested_bars` are `Bar` instances — OK.

- [ ] **Step 5: Commit**

```bash
git add services/data/file_ingest.py tests/data/test_file_ingest.py
git commit -m "feat(data): read-only CSV bar ingestion adapter -> persisted PIT store + report"
```

---

### Task 3: Full gate + roadmap note

**Files:**
- Modify: `docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md` (one-line note)

- [ ] **Step 1: Full gate**

Run: `python scripts/validate.py`
Expected: `VALIDATE PASSED (fail-closed gate green).` (count up by ~10); scanner clean (new modules import only stdlib + contracts + services.data).

- [ ] **Step 2: Roadmap note**

Append to the roadmap/remaining section of `docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`:

```markdown
- Persisted data lake sub-project 1 DONE: services/data/{persistent_store,file_ingest}.py —
  SqlitePITStore (durable, fail-closed, MarketDataSource-compatible, lookahead-safe as_of) +
  read-only CSV ingestion (normalize/reject-future/dedup/quality/content-hash). Later:
  PIT fundamentals; persisted universe; calendar reconciliation; more formats.
```

- [ ] **Step 3: Commit**

```bash
git add docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md
git commit -m "docs: record persisted data lake sub-project 1 in the program plan"
```

---

## Self-Review

**1. Spec coverage:** §3.1 SqlitePITStore → Task 1; §3.2 file_ingest + IngestionReport → Task 2; §5 fail-closed (uninit raises, future/malformed rejected, missing-kt raises) → Tasks 1,2 tests; §5 determinism (idempotent re-ingest, stable hash) → Task 2 test; §6 testing → Tasks 1,2. ✓

**2. Placeholder scan:** every code step complete; no TBD/TODO. ✓

**3. Type consistency:** `SqlitePITStore(path, *, create=False)`, `.add->bool`, `.contains`, `.as_of`, `.fetch_bars`, `.keys` consistent Task 1↔2↔tests. `normalize_bar(row, kt)` called with a dict lacking `knowledge_time` (popped) — matches its contract. `content_hash_of(ingested_bars)` takes pydantic records (Bar) — matches. `build_report(dataset_id, event_times, as_of=, version=)` matches the existing signature. ✓

Implementer note: CSV values arrive as strings; pydantic `Bar` coerces `"100"`→float / `"10"`→int, and `normalize_bar` forbids a `knowledge_time` key in `raw` (popped before the call). Empty-string cells are dropped so optional columns don't break coercion. The store PK `(key, event_time, knowledge_time)` is the dedup key; a restatement (later knowledge_time) is a distinct row.
