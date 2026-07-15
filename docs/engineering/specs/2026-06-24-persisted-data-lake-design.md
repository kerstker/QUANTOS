# Persisted PIT Data Store + Read-Only Ingestion — Design (sub-project 1 of N)

Date: 2026-06-24 · Status: approved · Scope: single implementation plan
Audit driver: `docs/QUANTOS_AUDIT_2026-06.md` §6 P0 — "No persisted market-data
lake or real ingestion — everything runs on synthetic in-memory bars ... has the
PIT abstraction but no actual data and no path to ingest any."

## 0. Decomposition

A full data lake (persisted bars + fundamentals + universe + reconciliation +
vendor adapters) is several sub-projects. This spec builds **sub-project 1**: a
persisted, point-in-time bar store + a read-only local-file (CSV) ingestion
adapter. Later sub-projects: point-in-time fundamentals; persisted universe;
calendar reconciliation; additional file formats. The in-memory `PITStore` /
`InMemoryMarketDataSource` stay as test doubles (unchanged).

## 1. Goal

Give QUANTOS a **persisted, PIT-correct, deterministic** bar substrate that
survives process restarts, plus a **read-only path to ingest real bars from a
local CSV file** (normalize → reject-future → dedup → quality report → persist).
No vendor SDK, no network, no broker — ingestion reads a local file and writes to
the research store only.

Constraints: L2_DRY_RUN_ONLY; pure-Python stdlib (`sqlite3`, `csv`, `json`);
deterministic (no RNG; stable ordering); fail-closed; reuse the `Bar` /
`BitemporalRecord` contracts + `normalize_bar` / `reject_if_future` /
`quality.build_report` / `content_hash_of`; preserve the no-live-surface posture.

## 2. Architecture

New modules in `services/data/`:
```
persistent_store.py  SqlitePITStore — sqlite-backed PIT bar store; MarketDataSource-compatible
file_ingest.py       ingest_bars_csv(...) -> IngestionReport; IngestionReport dataclass
```
Reuse unchanged: `quantos.contracts.data.Bar`, `contracts.temporal.BitemporalRecord`,
`services.data.normalization.{normalize_bar, reject_if_future}`,
`services.data.quality.build_report`, `services.data.versioning.content_hash_of`.
The in-memory `PITStore` / `InMemoryMarketDataSource` are untouched.

`SqlitePITStore` mirrors `PITStore`'s public surface (`add`/`add_many`/`as_of`/
`latest_as_of`/`keys`) so research code is storage-agnostic, AND implements the
`MarketDataSource` protocol (`fetch_bars`). No new third-party deps; no live surface.

## 3. Components

### 3.1 `persistent_store.py` — SqlitePITStore
- Schema: `bars(key TEXT, event_time INT, knowledge_time INT, payload_json TEXT,
  PRIMARY KEY(key, event_time, knowledge_time))` — the PK gives natural dedup of
  identical (instrument, event, knowledge) rows; a restatement has a LATER
  knowledge_time and is stored separately. Index `(key, knowledge_time)` for `as_of`.
- `__init__(self, path: str, *, create: bool = False)` — opens `path`
  (`":memory:"` in tests). `create` runs schema init. Reading an
  uninitialized/corrupt store raises `DataStoreError` (fail-closed — never reads
  as empty, which would silently drop history).
- `add(self, key: str, bar: Bar) -> None` / `add_many(self, key, bars) -> None` —
  `INSERT OR IGNORE` (dedup by PK).
- `as_of(self, key, as_of_time, event_time_max=None) -> list[Bar]` — rows with
  `knowledge_time <= as_of_time` (and `event_time <= event_time_max` if given),
  `ORDER BY event_time, knowledge_time`; reconstruct via `Bar.model_validate`.
  Same lookahead-safe semantics as the in-memory `PITStore`.
- `latest_as_of(self, key, as_of_time, event_time_max=None) -> Optional[Bar]`.
- `fetch_bars(self, instrument_id, start, end) -> list[Bar]` — MarketDataSource
  protocol: bars with `start <= event_time <= end`, ordered (window fetch).
- `keys(self) -> list[str]`.

### 3.2 `file_ingest.py`
- `@dataclass(frozen=True) IngestionReport: rows_read; bars_ingested; duplicates;
  rejected: tuple[tuple[int,str],...] (row index, reason); content_hash: str;
  quality: DataQualityReport`.
- `ingest_bars_csv(path, store, *, knowledge_time_default=None, now=None,
  dataset_id="ingested", version="") -> IngestionReport`:
  reads the CSV with stdlib `csv.DictReader`; per row: build a Bar via
  `normalize_bar(row, knowledge_time)` where knowledge_time comes from the row's
  `knowledge_time` column or `knowledge_time_default` (raise if neither);
  `reject_if_future(bar, now)` when `now` is given; a row that fails validation or
  is future is COUNTED in `rejected` (index, reason) and skipped (robust +
  transparent, never a silent drop and never a crash on one bad row). Surviving
  bars are persisted via `store.add` (duplicates counted via pre/post `as_of`
  membership). Build the `quality` report from the ingested event_times and the
  `content_hash` from the ingested bars (`content_hash_of`). Read-only source.

## 4. Data flow

```
local CSV ──► csv.DictReader ──► normalize_bar / reject_if_future ──► dedup ──► SqlitePITStore (persisted)
                                                   │                                     │ as_of(key, t)
                                                   ▼                                     ▼
                                            IngestionReport                       research (PIT-correct bars)
```

## 5. Error handling — fail-closed

- `SqlitePITStore` reading an uninitialized/corrupt store raises `DataStoreError`
  (never reads as empty). `create=True` initializes an empty schema explicitly.
- `as_of` is lookahead-safe by construction (SQL filter `knowledge_time <= as_of`).
- Ingestion: a malformed/future row is recorded in `rejected` with a reason and
  skipped — robust and transparent (the report surfaces every drop); the store's
  PK prevents duplicate persistence. Missing knowledge_time with no default raises
  (cannot silently fabricate a knowledge time → that would risk lookahead).
- Determinism: stable `ORDER BY`; `content_hash_of` is order-independent; no RNG.
  Re-ingesting the same file yields the same store contents (PK dedup) and an
  identical content_hash.

## 6. Testing (tests-first)

- `tests/data/test_persistent_store.py`: add/as_of round-trips with PIT filtering
  (a future-knowledge bar is excluded at an earlier as_of, included later);
  restatement (same event_time, later knowledge_time) both retained, `latest_as_of`
  returns the newest; dedup (re-add identical bar → one row); persistence across a
  fresh store object on the same `:memory:`? (use a temp file to prove cross-
  connection persistence); `fetch_bars` window; uninitialized store raises;
  `Bar` round-trips through model_validate.
- `tests/data/test_file_ingest.py`: ingest a small CSV (tmp_path) → bars persisted,
  `as_of` returns them; a future row (with `now`) and a malformed-OHLC row are
  rejected with reasons, not crashing; re-ingesting the same file → duplicates
  counted, store unchanged, identical content_hash; quality report present;
  missing knowledge_time + no default raises.
- Wire: new tests under `tests/data` (already in `scripts/validate.py`); governance
  scanner auto-covers the new modules (no live surface).

## 7. Out of scope (later sub-projects / YAGNI)

Point-in-time fundamentals (new Fundamental contract); persisted survivorship-free
universe; calendar/expected-count reconciliation reports; parquet/other formats;
vendor network adapters (forbidden at L2 anyway); restatement conflict policies
beyond keep-all. Sub-project 1 delivers the persisted PIT bar store + CSV
ingestion only.
