# Experiment Registry + Persistence Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the AlphaCatalog multiple-testing state (trial population + per-alpha Sharpe/pvalue) and every AlphaEvaluation durably so Deflated-Sharpe trial counting survives across processes and research is reproducible.

**Architecture:** A new isolated package `services/research/experiment_registry/` with a stdlib-`sqlite3` store (`store.py`) and a domain API (`registry.py`). `AlphaCatalog` gains a pure-in-memory `snapshot()`/`restore()` port; the registry maps that snapshot to/from rows. Catalog default behavior is unchanged when the registry is unused.

**Tech Stack:** Python 3 stdlib `sqlite3` + `json`; pydantic v2 contracts; `quantos/contracts/hashing.stable_hash`; `services/monitoring` `GlobalAuditLog`.

## Global Constraints

- Readiness L2_DRY_RUN_ONLY; pure-Python; no new third-party dependencies (stdlib `sqlite3`/`json` only).
- Deterministic: all timestamps are CALLER-SUPPLIED ints; no wall-clock enters any persisted hash. Content hashes use `stable_hash` (sorted keys, FLOAT_PRECISION=10).
- Fail-closed: `load_into` on a missing/corrupt/uninitialized store RAISES `ExperimentStoreError` — it must NEVER silently yield an empty trial population (that would un-deflate DSR).
- Immutable artifacts: evaluation rows are write-once; conflicting content under an existing `run_id` raises `ImmutableArtifactError`; no UPDATE/DELETE in the public API.
- Do not weaken existing tests or the gate. `AlphaCatalog` behaves identically when `snapshot/restore` are not called.
- Gate command between tasks: `python scripts/validate.py` (run from repo root; if `services`/`quantos` import errors appear in an isolated run, prefix `PYTHONPATH=<repo-root>`).
- Unique test-file basenames (pytest collects across dirs into one process): `test_experiment_store.py`, `test_catalog_snapshot.py`, `test_experiment_registry.py`.

**Reference signatures (already in the codebase):**
- `quantos.contracts.hashing.stable_hash(obj) -> str` (64-hex), `short_hash(obj, length=16) -> str`.
- `quantos.contracts.alpha.AlphaDefinition` — pydantic v2: `.model_dump()`, `AlphaDefinition.model_validate(dict)`, fields `alpha_id/name/factor_ids/universe/params`, property `.definition_hash`.
- `quantos.contracts.research.AlphaState` — str Enum (`AlphaState(value)` rebuilds).
- `quantos.contracts.alpha.AlphaEvaluation` — pydantic v2: `.model_dump()`, fields incl. `alpha_id`, `verdict` (an enum with `.value`), `reproducibility_hash`.
- `services.monitoring.audit_log.GlobalAuditLog.append(*, event_id, actor, action, timestamp, target="", metadata=None) -> AuditEvent`; `.chain_valid() -> bool`.
- `AlphaCatalog` private state: `_defs, _state, _returns, _sharpes, _pvalues, _seen_configs, _trial_count` (invariant: `_trial_count == len(_seen_configs)`; `_sharpes[a] = fmean(r)/pstdev(r)` only when `len(r)>1 and pstdev(r)>0`).

---

### Task 1: SqliteExperimentStore (schema + write-once rows + fail-closed open)

**Files:**
- Create: `services/research/experiment_registry/__init__.py` (empty)
- Create: `services/research/experiment_registry/store.py`
- Test: `tests/research/test_experiment_store.py`

**Interfaces:**
- Produces:
  - `class ExperimentStoreError(Exception)`
  - `class ImmutableArtifactError(Exception)`
  - `class SqliteExperimentStore:`
    - `__init__(self, path: str, *, create: bool = False)` — opens `path` (use `":memory:"` in tests). If `create`, runs schema init. If not `create` and the schema/tables are absent or the file is unreadable, the FIRST read/write raises `ExperimentStoreError`.
    - `add_trials(self, config_hashes: Iterable[str], *, first_seen_ts: int) -> None` — `INSERT OR IGNORE` each (dedup by PK).
    - `trial_count(self) -> int` — `SELECT COUNT(*) FROM trials`.
    - `all_trial_hashes(self) -> list[str]`
    - `upsert_alpha(self, alpha_id, definition_json, state, returns_json, sharpe, pvalue) -> None` — `INSERT ... ON CONFLICT(alpha_id) DO UPDATE`.
    - `all_alphas(self) -> list[dict]` — rows as dicts.
    - `insert_evaluation(self, *, run_id, alpha_id, dataset_version, recorded_ts, verdict, content_hash, evaluation_json) -> None` — write-once: if `run_id` exists with the same `content_hash`, no-op; with a different `content_hash`, raise `ImmutableArtifactError`.
    - `get_evaluation(self, run_id: str) -> dict | None`
    - `list_evaluations(self, alpha_id: str) -> list[dict]` — ordered by `recorded_ts, run_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_experiment_store.py
import sqlite3
import pytest
from services.research.experiment_registry.store import (
    SqliteExperimentStore, ExperimentStoreError, ImmutableArtifactError,
)


def _store():
    return SqliteExperimentStore(":memory:", create=True)


def test_trials_dedup_by_hash_and_count():
    s = _store()
    s.add_trials(["h1", "h2"], first_seen_ts=1)
    s.add_trials(["h2", "h3"], first_seen_ts=2)   # h2 already present
    assert s.trial_count() == 3
    assert sorted(s.all_trial_hashes()) == ["h1", "h2", "h3"]


def test_evaluation_is_write_once():
    s = _store()
    s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                        recorded_ts=1, verdict="PASS", content_hash="c1",
                        evaluation_json='{"x":1}')
    s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                        recorded_ts=1, verdict="PASS", content_hash="c1",
                        evaluation_json='{"x":1}')          # identical -> no-op
    assert s.get_evaluation("r1")["content_hash"] == "c1"
    with pytest.raises(ImmutableArtifactError):
        s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                            recorded_ts=2, verdict="FAIL", content_hash="c2",
                            evaluation_json='{"x":2}')       # conflicting -> raise


def test_uninitialized_store_fails_closed(tmp_path):
    # a path with no schema (create=False) must RAISE on read, never read as empty
    p = str(tmp_path / "empty.db")
    sqlite3.connect(p).close()                               # a file, but no tables
    s = SqliteExperimentStore(p, create=False)
    with pytest.raises(ExperimentStoreError):
        s.trial_count()


def test_created_empty_store_reads_zero_legitimately():
    s = _store()                                             # create=True -> schema exists
    assert s.trial_count() == 0 and s.all_alphas() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/research/test_experiment_store.py -v`
Expected: FAIL with `ModuleNotFoundError: services.research.experiment_registry.store`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/research/experiment_registry/__init__.py
# (empty package marker)
```

```python
# services/research/experiment_registry/store.py
"""Stdlib-sqlite3 store for the experiment registry (persistence layer).

Owns the schema and ALL SQL. No domain logic, no imports from services/alpha.
Append-only / write-once: trials dedup by PK, evaluations are immutable, alpha
state is upserted. Fail-closed: reading an uninitialized/corrupt store raises
ExperimentStoreError rather than reading as empty (an empty trial population
would un-deflate Deflated Sharpe)."""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional


class ExperimentStoreError(Exception):
    """The store is missing, corrupt, or uninitialized — fail closed."""


class ImmutableArtifactError(Exception):
    """Attempt to overwrite a recorded evaluation with different content."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    config_hash TEXT PRIMARY KEY,
    first_seen_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_state (
    alpha_id TEXT PRIMARY KEY,
    definition_json TEXT NOT NULL,
    state TEXT NOT NULL,
    returns_json TEXT NOT NULL,
    sharpe REAL,
    pvalue REAL
);
CREATE TABLE IF NOT EXISTS evaluations (
    run_id TEXT PRIMARY KEY,
    alpha_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    recorded_ts INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evaluation_json TEXT NOT NULL
);
"""

_TABLES = ("trials", "alpha_state", "evaluations")


class SqliteExperimentStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExperimentStoreError(f"store unreadable: {exc}") from exc
        present = {r["name"] for r in rows}
        missing = [t for t in _TABLES if t not in present]
        if missing:
            raise ExperimentStoreError(f"store uninitialized; missing tables: {missing}")

    def add_trials(self, config_hashes: Iterable[str], *, first_seen_ts: int) -> None:
        self._require_schema()
        self._conn.executemany(
            "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
            [(h, first_seen_ts) for h in config_hashes],
        )
        self._conn.commit()

    def trial_count(self) -> int:
        self._require_schema()
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM trials").fetchone()["n"])

    def all_trial_hashes(self) -> list[str]:
        self._require_schema()
        return [r["config_hash"] for r in self._conn.execute("SELECT config_hash FROM trials")]

    def upsert_alpha(self, alpha_id: str, definition_json: str, state: str,
                     returns_json: str, sharpe: Optional[float], pvalue: Optional[float]) -> None:
        self._require_schema()
        self._conn.execute(
            "INSERT INTO alpha_state(alpha_id, definition_json, state, returns_json, sharpe, pvalue) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(alpha_id) DO UPDATE SET definition_json=excluded.definition_json, "
            "state=excluded.state, returns_json=excluded.returns_json, sharpe=excluded.sharpe, "
            "pvalue=excluded.pvalue",
            (alpha_id, definition_json, state, returns_json, sharpe, pvalue),
        )
        self._conn.commit()

    def all_alphas(self) -> list[dict]:
        self._require_schema()
        return [dict(r) for r in self._conn.execute(
            "SELECT alpha_id, definition_json, state, returns_json, sharpe, pvalue "
            "FROM alpha_state ORDER BY alpha_id")]

    def insert_evaluation(self, *, run_id: str, alpha_id: str, dataset_version: str,
                          recorded_ts: int, verdict: str, content_hash: str,
                          evaluation_json: str) -> None:
        self._require_schema()
        existing = self.get_evaluation(run_id)
        if existing is not None:
            if existing["content_hash"] == content_hash:
                return                                   # idempotent re-record
            raise ImmutableArtifactError(
                f"run_id {run_id} already recorded with a different content_hash")
        self._conn.execute(
            "INSERT INTO evaluations(run_id, alpha_id, dataset_version, recorded_ts, "
            "verdict, content_hash, evaluation_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, alpha_id, dataset_version, recorded_ts, verdict, content_hash, evaluation_json),
        )
        self._conn.commit()

    def get_evaluation(self, run_id: str) -> Optional[dict]:
        self._require_schema()
        row = self._conn.execute(
            "SELECT * FROM evaluations WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_evaluations(self, alpha_id: str) -> list[dict]:
        self._require_schema()
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM evaluations WHERE alpha_id = ? ORDER BY recorded_ts, run_id", (alpha_id,))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/research/test_experiment_store.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/research/experiment_registry/__init__.py services/research/experiment_registry/store.py tests/research/test_experiment_store.py
git commit -m "feat(registry): sqlite experiment store — write-once rows, fail-closed open"
```

---

### Task 2: AlphaCatalog snapshot()/restore() port

**Files:**
- Modify: `services/alpha/catalog.py` (add `CatalogSnapshot`, `AlphaStateRow`, `snapshot()`, `restore()`)
- Test: `tests/alpha/test_catalog_snapshot.py`

**Interfaces:**
- Consumes: `AlphaCatalog` private state (Task 0 reference signatures).
- Produces (in `services/alpha/catalog.py`):
  - `@dataclass(frozen=True) class AlphaStateRow: alpha_id: str; definition: AlphaDefinition; state: AlphaState; returns: tuple[float, ...]; pvalue: Optional[float]`
  - `@dataclass(frozen=True) class CatalogSnapshot: trials: tuple[str, ...]; alphas: tuple[AlphaStateRow, ...]`
  - `AlphaCatalog.snapshot(self) -> CatalogSnapshot`
  - `AlphaCatalog.restore(self, snap: CatalogSnapshot) -> None` — replaces in-memory state; recomputes `_sharpes` from returns via the existing rule (sharpe is NOT trusted from outside).

- [ ] **Step 1: Write the failing test**

```python
# tests/alpha/test_catalog_snapshot.py
"""Audit roadmap — AlphaCatalog snapshot/restore round-trips the multiple-testing
state (trial pop, var_sr, BH-FDR, approved_set, per-alpha state) with no loss."""
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog


def _populated():
    c = AlphaCatalog()
    for i, rets in enumerate([(0.01, 0.02, -0.01, 0.03), (0.02, -0.01, 0.0, 0.01), (0.0, 0.01, 0.01, -0.02)]):
        aid = f"a{i}"
        c.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f{i}",)))
        c.set_returns(aid, rets)
        c.set_pvalue(aid, 0.01 * (i + 1))
    c._set_state("a0", AlphaState.APPROVED)
    c.register_trial("extra-config-hash")            # a trial with no alpha object
    return c


def test_snapshot_restore_preserves_multiple_testing_state():
    src = _populated()
    snap = src.snapshot()
    dst = AlphaCatalog()
    dst.restore(snap)
    assert dst.global_trial_count() == src.global_trial_count()
    assert dst.catalog_var_sr() == src.catalog_var_sr()
    assert dst.bh_fdr_survivors(0.10) == src.bh_fdr_survivors(0.10)
    assert dst.approved_set() == src.approved_set()
    assert {a: dst.state(a) for a in dst.all_ids()} == {a: src.state(a) for a in src.all_ids()}
    assert dst.returns("a1") == src.returns("a1")


def test_restore_is_independent_of_a_persisted_sharpe():
    # sharpe is recomputed from returns on restore, not trusted from the snapshot
    src = _populated()
    snap = src.snapshot()
    dst = AlphaCatalog()
    dst.restore(snap)
    # var_sr is derived from recomputed per-alpha sharpe -> identical to source
    assert dst.catalog_var_sr() == src.catalog_var_sr()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/alpha/test_catalog_snapshot.py -v`
Expected: FAIL with `AttributeError: 'AlphaCatalog' object has no attribute 'snapshot'`.

- [ ] **Step 3: Write minimal implementation**

Add near the top of `services/alpha/catalog.py` (after the existing imports add `from dataclasses import dataclass` and `from typing import Optional` if not present; `Optional` and `Sequence` are already imported):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class AlphaStateRow:
    alpha_id: str
    definition: AlphaDefinition
    state: AlphaState
    returns: tuple[float, ...]
    pvalue: Optional[float]


@dataclass(frozen=True)
class CatalogSnapshot:
    """A plain, serializable copy of AlphaCatalog state. `trials` is the FULL
    distinct-config population (a superset of the alphas' own definition hashes),
    so the trial count round-trips exactly."""
    trials: tuple[str, ...]
    alphas: tuple[AlphaStateRow, ...]
```

Add these two methods to `AlphaCatalog` (e.g. after `approved_set`):

```python
    def snapshot(self) -> CatalogSnapshot:
        rows = tuple(
            AlphaStateRow(
                alpha_id=aid,
                definition=self._defs[aid],
                state=self._state[aid],
                returns=self._returns.get(aid, ()),
                pvalue=self._pvalues.get(aid),
            )
            for aid in self.all_ids()
        )
        return CatalogSnapshot(trials=tuple(sorted(self._seen_configs)), alphas=rows)

    def restore(self, snap: CatalogSnapshot) -> None:
        """Replace in-memory state from a snapshot. Sharpe is RECOMPUTED from
        returns via set_returns (never trusted from outside), so catalog_var_sr is
        consistent with the persisted returns."""
        self._defs = {}
        self._state = {}
        self._returns = {}
        self._sharpes = {}
        self._pvalues = {}
        self._seen_configs = set(snap.trials)
        self._trial_count = len(self._seen_configs)
        for row in snap.alphas:
            self._defs[row.alpha_id] = row.definition
            self._state[row.alpha_id] = row.state
            if row.returns:
                self.set_returns(row.alpha_id, row.returns)   # recomputes _sharpes
            if row.pvalue is not None:
                self._pvalues[row.alpha_id] = float(row.pvalue)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/alpha/test_catalog_snapshot.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full alpha suite to confirm no regression**

Run: `PYTHONPATH=. python -m pytest tests/alpha -q`
Expected: PASS (all existing alpha tests still pass — default behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add services/alpha/catalog.py tests/alpha/test_catalog_snapshot.py
git commit -m "feat(alpha): AlphaCatalog snapshot()/restore() port (in-memory, recomputes sharpe)"
```

---

### Task 3: ExperimentRegistry (save/load_into/record_evaluation + audit)

**Files:**
- Create: `services/research/experiment_registry/registry.py`
- Test: `tests/research/test_experiment_registry.py`

**Interfaces:**
- Consumes: `SqliteExperimentStore`, `ExperimentStoreError`, `ImmutableArtifactError` (Task 1); `AlphaCatalog.snapshot()/restore()`, `CatalogSnapshot`, `AlphaStateRow` (Task 2); `stable_hash`/`short_hash`; `GlobalAuditLog.append`.
- Produces (in `services/research/experiment_registry/registry.py`):
  - `class ExperimentRegistry:`
    - `__init__(self, store: SqliteExperimentStore)`
    - `save(self, catalog, *, now: int = 0) -> None` — persists the catalog snapshot (trials with `first_seen_ts=now`, alpha rows upserted).
    - `load_into(self, catalog) -> None` — rebuilds a `CatalogSnapshot` from the store and calls `catalog.restore(...)`. Propagates `ExperimentStoreError` from the store (fail-closed).
    - `record_evaluation(self, evaluation, *, dataset_version: str, recorded_ts: int, audit_log=None) -> str` — derives `run_id`, computes `content_hash`, writes once, optionally appends an `EXPERIMENT_RECORDED` AuditEvent; returns `run_id`.
    - `get_evaluation(self, run_id: str) -> dict | None`
    - `list_evaluations(self, alpha_id: str) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_experiment_registry.py
"""Audit roadmap P0 — durable experiment registry: cross-session DSR trial
counting, reproducible immutable evaluation artifacts, fail-closed load."""
import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.monitoring.audit_log import GlobalAuditLog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import (
    SqliteExperimentStore, ExperimentStoreError,
)


def _populated():
    c = AlphaCatalog()
    for i, rets in enumerate([(0.01, 0.02, -0.01, 0.03), (0.02, -0.01, 0.0, 0.01)]):
        aid = f"a{i}"
        c.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f{i}",)))
        c.set_returns(aid, rets)
        c.set_pvalue(aid, 0.02 * (i + 1))
    c._set_state("a0", AlphaState.APPROVED)
    return c


def _reg(create=True):
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=create))


def test_save_then_load_round_trips_multiple_testing_state():
    src = _populated()
    reg = _reg()
    reg.save(src, now=1)
    dst = AlphaCatalog()
    reg.load_into(dst)
    assert dst.global_trial_count() == src.global_trial_count()
    assert dst.catalog_var_sr() == src.catalog_var_sr()
    assert dst.bh_fdr_survivors(0.10) == src.bh_fdr_survivors(0.10)
    assert dst.approved_set() == src.approved_set()


def test_trial_count_is_cumulative_and_dedup_across_save_load():
    reg = _reg()
    c = _populated()                 # 2 distinct definition trials
    reg.save(c, now=1)
    dst = AlphaCatalog()
    reg.load_into(dst)
    dst.register(AlphaDefinition(alpha_id="a0", name="a0", factor_ids=("f0",)))  # same config hash
    dst.register(AlphaDefinition(alpha_id="a9", name="a9", factor_ids=("f9",)))  # new config
    reg.save(dst, now=2)
    final = AlphaCatalog()
    reg.load_into(final)
    assert final.global_trial_count() == 3       # 2 + 1 new (the re-seen config did not inflate)


def test_load_into_uninitialized_store_fails_closed():
    reg = ExperimentRegistry(SqliteExperimentStore(":memory:", create=False))
    with pytest.raises(ExperimentStoreError):
        reg.load_into(AlphaCatalog())            # never silently empties the trial pop


def _evaluation():
    return AlphaEvaluation(alpha_id="a1", verdict=ValidationVerdict.PASS,
                           deflated_sharpe=0.8, pbo=0.1, catalog_trial_count=3,
                           reproducibility_hash="repro-1")


def test_record_evaluation_is_reproducible_and_immutable():
    reg = _reg()
    rid1 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    rid2 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    assert rid1 == rid2                                   # deterministic run_id
    stored = reg.get_evaluation(rid1)
    assert stored["verdict"] == "PASS" and stored["content_hash"]
    assert reg.list_evaluations("a1")[0]["run_id"] == rid1


def test_record_evaluation_emits_audit_event():
    reg = _reg()
    log = GlobalAuditLog()
    rid = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10, audit_log=log)
    assert log.chain_valid() and len(log.events) == 1
    ev = log.events[0]
    assert ev.action == "EXPERIMENT_RECORDED" and ev.metadata["run_id"] == rid


def test_content_hash_is_deterministic_across_registries():
    r1, r2 = _reg(), _reg()
    h1 = r1.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    h2 = r2.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    assert r1.get_evaluation(h1)["content_hash"] == r2.get_evaluation(h2)["content_hash"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/research/test_experiment_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: services.research.experiment_registry.registry`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/research/experiment_registry/registry.py
"""Experiment registry — durable catalog snapshot + immutable evaluation artifacts.

Makes the catalog-wide Deflated-Sharpe trial count and per-alpha Sharpe population
survive across processes (load_into at session start, save at end), and records
every AlphaEvaluation as a reproducible, write-once artifact. Optionally emits an
EXPERIMENT_RECORDED event into the global tamper-evident audit chain. Deterministic:
all timestamps are caller-supplied; content hashing uses stable_hash."""
from __future__ import annotations

import json
from typing import Optional

from quantos.contracts.hashing import short_hash, stable_hash
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog, AlphaStateRow, CatalogSnapshot

from .store import SqliteExperimentStore


class ExperimentRegistry:
    def __init__(self, store: SqliteExperimentStore) -> None:
        self._store = store

    # --- catalog snapshot persistence ---------------------------------------

    def save(self, catalog: AlphaCatalog, *, now: int = 0) -> None:
        snap = catalog.snapshot()
        self._store.add_trials(snap.trials, first_seen_ts=now)
        for row in snap.alphas:
            sharpe = catalog._sharpes.get(row.alpha_id)   # denormalized for queries
            self._store.upsert_alpha(
                alpha_id=row.alpha_id,
                definition_json=json.dumps(row.definition.model_dump(), sort_keys=True, default=str),
                state=row.state.value,
                returns_json=json.dumps(list(row.returns)),
                sharpe=sharpe,
                pvalue=row.pvalue,
            )

    def load_into(self, catalog: AlphaCatalog) -> None:
        trials = tuple(self._store.all_trial_hashes())     # raises ExperimentStoreError if uninit
        rows = []
        for r in self._store.all_alphas():
            rows.append(AlphaStateRow(
                alpha_id=r["alpha_id"],
                definition=AlphaDefinition.model_validate(json.loads(r["definition_json"])),
                state=AlphaState(r["state"]),
                returns=tuple(json.loads(r["returns_json"])),
                pvalue=r["pvalue"],
            ))
        catalog.restore(CatalogSnapshot(trials=trials, alphas=tuple(rows)))

    # --- immutable evaluation artifacts -------------------------------------

    def record_evaluation(self, evaluation, *, dataset_version: str, recorded_ts: int,
                          audit_log=None) -> str:
        payload = evaluation.model_dump()
        content_hash = stable_hash({"evaluation": payload, "dataset_version": dataset_version})
        run_id = short_hash({"alpha_id": evaluation.alpha_id,
                             "repro": evaluation.reproducibility_hash,
                             "dataset_version": dataset_version})
        self._store.insert_evaluation(
            run_id=run_id, alpha_id=evaluation.alpha_id, dataset_version=dataset_version,
            recorded_ts=recorded_ts, verdict=evaluation.verdict.value,
            content_hash=content_hash,
            evaluation_json=json.dumps(payload, sort_keys=True, default=str),
        )
        if audit_log is not None:
            audit_log.append(
                event_id=f"exp-{run_id}", actor="experiment-registry",
                action="EXPERIMENT_RECORDED", timestamp=recorded_ts,
                target=evaluation.alpha_id,
                metadata={"run_id": run_id, "content_hash": content_hash,
                          "verdict": evaluation.verdict.value},
            )
        return run_id

    def get_evaluation(self, run_id: str) -> Optional[dict]:
        return self._store.get_evaluation(run_id)

    def list_evaluations(self, alpha_id: str) -> list[dict]:
        return self._store.list_evaluations(alpha_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/research/test_experiment_registry.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add services/research/experiment_registry/registry.py tests/research/test_experiment_registry.py
git commit -m "feat(registry): ExperimentRegistry — durable catalog snapshot + immutable artifacts + audit"
```

---

### Task 4: Wire into the gate + document the opt-in pattern

**Files:**
- Modify: `scripts/validate.py` (add the two new research test files — they already live under `tests/research`/`tests/alpha`, both already-scanned dirs, but confirm collection)
- Modify: `CLAUDE.md` (document the load_into/save opt-in pattern + the cross-session DSR property)

**Interfaces:** none (wiring + docs).

- [ ] **Step 1: Confirm the new tests are collected by the gate**

`scripts/validate.py` already passes the dirs `tests/alpha` and `tests/research` to pytest, so `test_catalog_snapshot.py`, `test_experiment_store.py`, and `test_experiment_registry.py` are auto-collected. No CHECKS edit is required. Verify:

Run: `python scripts/validate.py`
Expected: tail shows `VALIDATE PASSED (fail-closed gate green).` with the new tests included (count increased by 12).

- [ ] **Step 2: Document the opt-in persistence pattern in CLAUDE.md**

Add under the "What actually exists" section (after the alpha factory bullet):

```markdown
- `services/research/experiment_registry/` — durable persistence (stdlib sqlite3):
  `ExperimentRegistry.load_into(catalog)` at session start + `.save(catalog)` at end
  make the catalog-wide Deflated-Sharpe trial count + var_sr survive across
  processes (no longer reset on exit); `.record_evaluation(...)` writes an
  immutable, reproducible artifact and optionally emits an EXPERIMENT_RECORDED
  event into the global audit chain. Opt-in: AlphaCatalog/AlphaFactory behave
  exactly as before when the registry is not used. Fail-closed: loading an
  uninitialized/corrupt store raises (never silently resets multiple-testing).
```

- [ ] **Step 3: Run the full gate**

Run: `python scripts/validate.py`
Expected: `VALIDATE PASSED (fail-closed gate green).`

- [ ] **Step 4: Commit**

```bash
git add scripts/validate.py CLAUDE.md
git commit -m "docs+gate: wire experiment registry tests + document opt-in persistence pattern"
```

---

## Self-Review

**1. Spec coverage:**
- §2 architecture (store/registry/catalog port) → Tasks 1, 2, 3. ✓
- §3 schema (trials/alpha_state/evaluations) → Task 1. ✓
- §4 data flow (load_into/record/save) → Task 3 + Task 4 docs. ✓
- §5 fail-closed load → Task 1 `test_uninitialized_store_fails_closed` + Task 3 `test_load_into_uninitialized_store_fails_closed`. ✓
- §5 immutability (write-once) → Task 1 `test_evaluation_is_write_once` + `ImmutableArtifactError`. ✓
- §5 determinism (caller ts, stable_hash) → Task 3 `test_content_hash_is_deterministic_across_registries`. ✓
- §5 audit emission → Task 3 `test_record_evaluation_emits_audit_event`. ✓
- §6 API → Tasks 1–3 produce-blocks. ✓
- §7 tests → Tasks 1–3 + Task 4 wiring. ✓
- §1 cross-session DSR fix → Task 3 `test_trial_count_is_cumulative_and_dedup_across_save_load`. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**3. Type consistency:** `CatalogSnapshot.trials: tuple[str, ...]` and `AlphaStateRow(alpha_id, definition, state, returns, pvalue)` are defined identically in Task 2 and consumed with the same fields in Task 3. `record_evaluation(evaluation, *, dataset_version, recorded_ts, audit_log=None) -> str` matches between §6, Task 3 produce-block, and the test calls. `SqliteExperimentStore(path, *, create=False)` consistent across tasks. `verdict.value` used (AlphaEvaluation.verdict is an enum). ✓

Notes for the implementer: `restore()` reaches `catalog._sharpes` indirectly via `set_returns`; `save()` reads `catalog._sharpes` for the denormalized `sharpe` column only (not used on load). The `register()` invariant `_trial_count == len(_seen_configs)` is what makes `restore` set `_trial_count = len(_seen_configs)` correct.
