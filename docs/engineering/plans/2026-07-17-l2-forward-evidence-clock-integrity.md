# L2 Forward Evidence Clock Integrity Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with the project-approved subagent-driven workflow (recommended) or inline TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an authority-bound, append-only and transactionally atomic L2 forward paper-evidence clock that never treats legacy or backfilled observations as attested forward evidence.

**Architecture:** Keep `paper_track.db` as the single-node authority and upgrade it additively. A verified `ForwardAdmissionRef` binds every new decision and realization to the append-only data-quality authority; `ForwardLedger.commit_realization` atomically appends the realization, paper period and local event while `paper_track` remains a rebuildable projection.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`/`dataclasses`/`hashlib`/`json`, pytest, FastAPI read-only console, React 18 + TypeScript, existing QUANTOS evidence and governance scanners.

## Global Constraints

- Readiness remains exactly `L2_DRY_RUN_ONLY`.
- `LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`, `ALLOW_ORDER_SUBMISSION=false`, `ALLOW_AI_DIRECT_TRADING=false`, `REQUIRE_HUMAN_APPROVAL=true`.
- No broker/CTP/FIX SDK, real credentials, secret reads, network import, RNG or implicit wall clock may enter `services/research/paper_trading`.
- Existing rows are never deleted, rewritten, back-signed or counted as attested evidence without a v2 authority binding.
- All shell commands in this repository are prefixed with `rtk`; the canonical gate is `rtk python scripts/validate.py`.
- Preserve all unrelated dirty-worktree changes. Execute this plan in an isolated worktree after explicit user consent, and port only the existing in-scope changes in `forward_store.py`, `store.py` and `test_forward_track.py`. The current `persistence.py` diff belongs to search provenance and must not be copied as part of this feature.
- Tests precede production changes; every RED must be observed for the intended missing behavior before GREEN implementation.

---

## File Map

- Create `services/research/paper_trading/forward_evidence.py`: immutable authority reference and authority loader.
- Modify `services/research/paper_trading/forward_store.py`: schema v2, decision binding, chain verification, atomic realization commit.
- Modify `services/research/paper_trading/forward.py`: consume authority references and atomic commit; remove boolean quality bypass.
- Modify `services/research/paper_trading/store.py`: retain current conflict protection and treat summary as a rebuildable projection.
- Modify `services/research/ports/persistence.py`: exact protocol signatures for authority-bound decision and realization operations.
- Modify `tools/data_quality_admission.py`: export the authority database path without changing admission semantics.
- Modify `tools/forward_track_advance.py`: reopen the authority, record and realize only attested decisions, report legacy separately.
- Create `tools/forward_evidence_status.py`: deterministic database verifier/evidence generator with read-only `--check`.
- Modify `tools/gonogo_report.py`: consume attested counts instead of the legacy cache count.
- Modify `services/console/api/artifact_snapshots.py`: expose honest attested/legacy/admission state.
- Modify `apps/research-console/src/lib/types/research.ts`: add the attested evidence payload types.
- Modify `apps/research-console/src/pages/LiveTrading.tsx`: relabel the surface as L2 evidence and render blocked/legacy states without enabling actions.
- Add/modify focused tests under `tests/research/`, `tests/tools/`, `tests/governance/`, and the existing frontend workflow test.
- Generate `evidence/forward_evidence_baseline.json` only from the authoritative database; never hand-edit it.

---

### Task 1: Verified Data-Admission Reference

**Files:**
- Create: `services/research/paper_trading/forward_evidence.py`
- Create: `tests/research/test_forward_evidence_admission.py`
- Modify: `tools/data_quality_admission.py:29-35`

**Interfaces:**
- Consumes: `DataQualityAdmissionStore.chain_valid()`, `.decisions()`, and `DataQualityAdmissionDecision`.
- Produces: `ForwardAdmissionRef` and `load_forward_admission_ref(store, *, admission_id, evidence_sha256, decision_hash, instruments)`.

- [ ] **Step 1: Write the authority-reference tests**

Create `tests/research/test_forward_evidence_admission.py` with a real temporary admission authority:

```python
import pytest

from services.data.admission_store import DataQualityAdmissionStore
from services.research.paper_trading.forward_evidence import (
    ForwardEvidenceError,
    load_forward_admission_ref,
)


def _evidence(*, verdict="PASS"):
    blocked = verdict == "BLOCK"
    return {
        "schema_version": 2,
        "generated_at_ms": 100,
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "FORWARD_EVIDENCE_DATA_ADMISSION",
        "instrument_scope": ["BTC", "ETH"],
        "price_basis": "PERP_MARK_PRICE",
        "quality_verdict": "BLOCK" if blocked else "PASS",
        "lineage_verdict": "BLOCK" if blocked else "PASS",
        "verdict": verdict,
        "block_reasons": ["QUALITY:funding:['BTC']"] if blocked else [],
        "sources": {},
        "refresh_observation": {"readable": True, "run_id": "refresh-1"},
    }


def _authority(tmp_path, *, verdict="PASS"):
    store = DataQualityAdmissionStore(str(tmp_path / "admission.db"), create=True)
    decision, _ = store.record_evidence(_evidence(verdict=verdict), recorded_at_ms=110)
    return store, decision


def test_loads_only_persisted_pass_admission_covering_instruments(tmp_path):
    store, decision = _authority(tmp_path)
    ref = load_forward_admission_ref(
        store,
        admission_id=decision.admission_id,
        evidence_sha256=decision.evidence_sha256,
        decision_hash=decision.decision_hash,
        instruments={"BTC"},
    )
    assert ref.admission_id == decision.admission_id
    assert ref.instrument_scope == ("BTC", "ETH")
    assert ref.price_basis == "PERP_MARK_PRICE"


@pytest.mark.parametrize("change", ["id", "evidence", "decision", "scope"])
def test_rejects_missing_mismatched_or_out_of_scope_authority(tmp_path, change):
    store, decision = _authority(tmp_path)
    values = {
        "admission_id": decision.admission_id,
        "evidence_sha256": decision.evidence_sha256,
        "decision_hash": decision.decision_hash,
        "instruments": {"BTC"},
    }
    if change == "id":
        values["admission_id"] = "missing"
    elif change == "evidence":
        values["evidence_sha256"] = "0" * 64
    elif change == "decision":
        values["decision_hash"] = "0" * 64
    else:
        values["instruments"] = {"SOL"}
    with pytest.raises(ForwardEvidenceError):
        load_forward_admission_ref(store, **values)


def test_rejects_block_admission(tmp_path):
    store, decision = _authority(tmp_path, verdict="BLOCK")
    with pytest.raises(ForwardEvidenceError, match="PASS"):
        load_forward_admission_ref(
            store,
            admission_id=decision.admission_id,
            evidence_sha256=decision.evidence_sha256,
            decision_hash=decision.decision_hash,
            instruments={"BTC"},
        )
```

- [ ] **Step 2: Run the focused test and observe RED**

Run:

```powershell
rtk python -m pytest tests/research/test_forward_evidence_admission.py -q
```

Expected: collection fails because `services.research.paper_trading.forward_evidence` does not exist.

- [ ] **Step 3: Implement the immutable reference and verified loader**

Create `services/research/paper_trading/forward_evidence.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection

from services.data.admission_store import DataQualityAdmissionStore


class ForwardEvidenceError(RuntimeError):
    """Forward evidence is missing, conflicting, blocked, or unverifiable."""


FORWARD_L2_DECISION_SOURCE = "FORWARD_L2_ATTESTED_DECISION"
FORWARD_L2_REALIZATION_SOURCE = "FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING"


@dataclass(frozen=True)
class ForwardAdmissionRef:
    admission_id: str
    evidence_sha256: str
    decision_hash: str
    recorded_at_ms: int
    price_basis: str
    instrument_scope: tuple[str, ...]


def load_forward_admission_ref(
    store: DataQualityAdmissionStore,
    *,
    admission_id: str,
    evidence_sha256: str,
    decision_hash: str,
    instruments: Collection[str],
) -> ForwardAdmissionRef:
    if not store.chain_valid():
        raise ForwardEvidenceError("data-admission authority chain is invalid")
    decision = next(
        (item for item in store.decisions() if item.admission_id == admission_id),
        None,
    )
    if decision is None:
        raise ForwardEvidenceError("data-admission decision is unavailable")
    if decision.evidence_sha256 != evidence_sha256 or decision.decision_hash != decision_hash:
        raise ForwardEvidenceError("data-admission digest binding is inconsistent")
    if (
        decision.readiness != "L2_DRY_RUN_ONLY"
        or decision.purpose != "FORWARD_EVIDENCE_DATA_ADMISSION"
        or decision.verdict != "PASS"
        or decision.quality_verdict != "PASS"
        or decision.lineage_verdict != "PASS"
        or decision.price_basis != "PERP_MARK_PRICE"
    ):
        raise ForwardEvidenceError("data-admission decision is not PASS for L2 forward evidence")
    required = {str(value) for value in instruments}
    if not required or not required.issubset(set(decision.instrument_scope)):
        raise ForwardEvidenceError("decision instruments are outside the admitted scope")
    return ForwardAdmissionRef(
        admission_id=decision.admission_id,
        evidence_sha256=decision.evidence_sha256,
        decision_hash=decision.decision_hash,
        recorded_at_ms=decision.recorded_at_ms,
        price_basis=decision.price_basis,
        instrument_scope=decision.instrument_scope,
    )
```

In `tools/data_quality_admission.py`, replace the private path constant with an exported constant while retaining the same value:

```python
DATA_QUALITY_ADMISSION_DB = os.path.join("data", "cache", "data_quality_admission.db")
_AUTHORITY = DATA_QUALITY_ADMISSION_DB
```

- [ ] **Step 4: Run GREEN and existing authority tests**

Run:

```powershell
rtk python -m pytest tests/research/test_forward_evidence_admission.py tests/data/test_data_quality_admission_store.py -q
```

Expected: both files pass.

- [ ] **Step 5: Commit Task 1**

```powershell
rtk git add services/research/paper_trading/forward_evidence.py tests/research/test_forward_evidence_admission.py tools/data_quality_admission.py
rtk git commit -m "feat(research): verify forward data-admission references"
```

---

### Task 2: Append-Only Decision Identity and Admission Binding

**Files:**
- Modify: `services/research/paper_trading/forward_store.py:10-108`
- Modify: `services/research/ports/persistence.py:195-220`
- Modify: `tests/research/test_forward_track.py:21-76`

**Interfaces:**
- Consumes: `ForwardAdmissionRef` from Task 1.
- Produces: authority-bound `ForwardLedger.record_decision(..., admission_ref=...)`, `attested_pending()`, `legacy_decisions()`, and `decision_chain_valid()`.

- [ ] **Step 1: Change the tests to express identity and legacy semantics**

Add a `_ref()` fixture helper and replace the current silent-conflict assertion:

```python
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef


def _ref(scope=("BTC", "ETH")):
    return ForwardAdmissionRef(
        admission_id="admission-1",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=_T0 - 1,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=tuple(sorted(scope)),
    )


def test_record_decision_is_idempotent_but_conflict_fails_closed(db):
    _, ledger = db
    weights = {"BTC": 0.5, "ETH": -0.5}
    kwargs = dict(recorded_at=_T0, weights=weights, admission_ref=_ref())
    assert ledger.record_decision("crowd_contra", _T0, **kwargs) is True
    assert ledger.record_decision("crowd_contra", _T0, **kwargs) is False
    with pytest.raises(Exception, match="identity reused"):
        ledger.record_decision(
            "crowd_contra",
            _T0,
            recorded_at=_T0,
            weights={"BTC": 1.0},
            admission_ref=_ref(),
        )
    assert ledger.decision_chain_valid()


def test_unbound_v1_decision_is_legacy_and_never_attested_pending(db):
    _, ledger = db
    ledger._conn.execute(
        "INSERT INTO forward_decisions"
        "(alpha_id,decided_ts,recorded_at,weights_json,signals_json,source,realized) "
        "VALUES (?,?,?,?,?,?,0)",
        ("legacy", _T0, _T0, '{"BTC":1.0}', None, "FORWARD_LIVE",),
    )
    ledger._conn.commit()
    assert [row["alpha_id"] for row in ledger.legacy_decisions()] == ["legacy"]
    assert ledger.attested_pending("legacy") == []
```

Use a dedicated migration test that creates only the v1 table before reopening with `create=True`; assert the original row remains byte-for-byte equivalent and is classified legacy.

- [ ] **Step 2: Run RED**

```powershell
rtk python -m pytest tests/research/test_forward_track.py -q
```

Expected: failures for the unknown `admission_ref`, `decision_chain_valid`, `legacy_decisions`, and `attested_pending` APIs.

- [ ] **Step 3: Add schema v2 and identity guards**

Extend `_SCHEMA` in `forward_store.py` with the additive table and triggers:

```sql
CREATE TABLE IF NOT EXISTS forward_decision_bindings (
  binding_seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id                 TEXT    NOT NULL,
  decided_ts               INTEGER NOT NULL,
  decision_payload_sha256  TEXT    NOT NULL,
  admission_id             TEXT    NOT NULL,
  admission_evidence_sha256 TEXT   NOT NULL,
  admission_decision_hash  TEXT    NOT NULL,
  admission_recorded_at_ms INTEGER NOT NULL,
  price_basis              TEXT    NOT NULL,
  instrument_scope_json    TEXT    NOT NULL,
  prev_hash                TEXT    NOT NULL,
  row_hash                 TEXT    NOT NULL UNIQUE,
  UNIQUE(alpha_id, decided_ts)
);
CREATE TRIGGER IF NOT EXISTS forward_decision_bindings_no_update
BEFORE UPDATE ON forward_decision_bindings
BEGIN SELECT RAISE(ABORT, 'forward_decision_bindings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_decision_bindings_no_delete
BEFORE DELETE ON forward_decision_bindings
BEGIN SELECT RAISE(ABORT, 'forward_decision_bindings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_decisions_identity_no_update
BEFORE UPDATE OF alpha_id,decided_ts,recorded_at,weights_json,signals_json,source
ON forward_decisions
BEGIN SELECT RAISE(ABORT, 'forward decision identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS forward_decisions_no_delete
BEFORE DELETE ON forward_decisions
BEGIN SELECT RAISE(ABORT, 'forward_decisions is append-only'); END;
```

Add canonical helpers using compact sorted JSON and SHA-256:

```python
def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
```

Update `record_decision` to require `admission_ref`, validate that all weight names are inside its scope, compare the complete retained decision and binding on replay, and insert both rows inside one `with self._conn:` transaction. Calculate the binding hash from all binding fields plus the previous binding hash. Raise:

```python
raise ForwardLedgerError(
    f"forward decision identity reused with different content: {alpha_id}@{int(decided_ts)}"
)
```

Implement `decision_chain_valid()` by recalculating every row in `binding_seq` order, `legacy_decisions()` with a left join where no binding exists, and `attested_pending(alpha_id)` with an inner join plus `realized=0`.

Update `ForwardLedgerPort.record_decision` with the exact keyword:

```python
admission_ref: ForwardAdmissionRef,
```

Use `FORWARD_L2_DECISION_SOURCE` as the `source` default. In `persistence.py`, import the type without creating a runtime adapter dependency:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
```

With `from __future__ import annotations` already active, add protocol methods for `attested_pending`, `legacy_decisions`, and `decision_chain_valid` using `ForwardAdmissionRef` in annotations.

- [ ] **Step 4: Run GREEN and trigger tamper tests**

```powershell
rtk python -m pytest tests/research/test_forward_track.py tests/research/test_persistence_ports.py -q
```

Expected: pass, including direct UPDATE/DELETE rejection and v1 migration preservation.

- [ ] **Step 5: Commit Task 2**

```powershell
rtk git add services/research/paper_trading/forward_store.py services/research/ports/persistence.py tests/research/test_forward_track.py tests/research/test_persistence_ports.py
rtk git commit -m "feat(research): bind forward decisions to data authority"
```

---

### Task 3: Atomic Realization, Paper Period, and Local Event

**Files:**
- Modify: `services/research/paper_trading/forward_store.py`
- Modify: `services/research/paper_trading/store.py:13-101`
- Modify: `services/research/ports/persistence.py`
- Modify: `tests/research/test_forward_track.py`

**Interfaces:**
- Consumes: attested decisions and `ForwardAdmissionRef`.
- Produces: `ForwardLedger.commit_realization(...) -> bool`, `realization_chain_valid()`, and `attested_realizations(alpha_id=None)`.

- [ ] **Step 1: Write atomicity, replay and tamper tests**

Add tests that call this wished-for API:

```python
def _commit(ledger, *, realized_return=0.01, inputs=None):
    return ledger.commit_realization(
        "a",
        _T0,
        as_of_ts=_T0 + _WEEK,
        realized_return=realized_return,
        realized_ic=0.5,
        source="FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING",
        inputs=inputs or {
            "BTC": {"weight": 1.0, "price_return": 0.011, "funding": 0.001},
        },
        admission_ref=_ref(scope=("BTC",)),
        committed_at_ms=_T0 + _WEEK + 1,
    )


def test_realization_and_period_commit_atomically_and_replay_idempotently(db):
    store, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )
    assert _commit(ledger) is True
    assert _commit(ledger) is False
    assert len(ledger.attested_realizations("a")) == 1
    assert len(store.periods("a")) == 1
    assert ledger.realization_chain_valid()


def test_realization_identity_conflict_rolls_back(db):
    store, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )
    _commit(ledger)
    with pytest.raises(Exception, match="realization identity reused"):
        _commit(ledger, realized_return=0.02)
    assert len(store.periods("a")) == 1
```

For rollback testing, install a temporary SQLite trigger that raises before each target insert, call `_commit`, and verify all authoritative counts remain zero:

```python
ledger._conn.execute(
    "CREATE TEMP TRIGGER fail_event BEFORE INSERT ON forward_local_events "
    "BEGIN SELECT RAISE(ABORT, 'injected'); END"
)
with pytest.raises(Exception, match="realization commit failed"):
    _commit(ledger)
assert ledger.attested_realizations("a") == []
assert store.periods("a") == []
assert len(ledger.attested_pending("a")) == 1
```

Repeat with triggers on `forward_realizations` and `paper_periods`. Add direct UPDATE/DELETE tests for realization, event and paper-period tables.

- [ ] **Step 2: Run RED**

```powershell
rtk python -m pytest tests/research/test_forward_track.py -q
```

Expected: failures because the atomic APIs and schema do not exist.

- [ ] **Step 3: Add realization and local-event schema**

Extend `_SCHEMA` with:

```sql
CREATE TABLE IF NOT EXISTS forward_realizations (
  realization_seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id                 TEXT    NOT NULL,
  decided_ts               INTEGER NOT NULL,
  as_of_ts                 INTEGER NOT NULL,
  decision_payload_sha256  TEXT    NOT NULL,
  realized_return          REAL    NOT NULL,
  realized_ic              REAL,
  source                   TEXT    NOT NULL,
  inputs_json              TEXT    NOT NULL,
  inputs_sha256            TEXT    NOT NULL,
  admission_id             TEXT    NOT NULL,
  admission_evidence_sha256 TEXT   NOT NULL,
  admission_decision_hash  TEXT    NOT NULL,
  committed_at_ms          INTEGER NOT NULL,
  prev_hash                TEXT    NOT NULL,
  row_hash                 TEXT    NOT NULL UNIQUE,
  UNIQUE(alpha_id, decided_ts),
  UNIQUE(alpha_id, as_of_ts)
);
CREATE TABLE IF NOT EXISTS forward_local_events (
  event_seq    INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type   TEXT    NOT NULL,
  alpha_id     TEXT    NOT NULL,
  decided_ts   INTEGER NOT NULL,
  payload_json TEXT    NOT NULL,
  prev_hash    TEXT    NOT NULL,
  row_hash     TEXT    NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS forward_realizations_no_update
BEFORE UPDATE ON forward_realizations
BEGIN SELECT RAISE(ABORT, 'forward_realizations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_realizations_no_delete
BEFORE DELETE ON forward_realizations
BEGIN SELECT RAISE(ABORT, 'forward_realizations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_local_events_no_update
BEFORE UPDATE ON forward_local_events
BEGIN SELECT RAISE(ABORT, 'forward_local_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_local_events_no_delete
BEFORE DELETE ON forward_local_events
BEGIN SELECT RAISE(ABORT, 'forward_local_events is append-only'); END;
```

Implement `commit_realization` with an explicit transaction. Before insert, verify the attested decision, decision payload hash, realization admission scope, existing period identity and exact replay content. Insert in this order inside the same transaction: realization, paper period, compatibility `realized=1`, local event.

```python
try:
    self._conn.execute("BEGIN IMMEDIATE")
    self._conn.execute(
        "INSERT INTO forward_realizations"
        "(alpha_id,decided_ts,as_of_ts,decision_payload_sha256,realized_return,"
        "realized_ic,source,inputs_json,inputs_sha256,admission_id,"
        "admission_evidence_sha256,admission_decision_hash,committed_at_ms,"
        "prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        realization_values,
    )
    self._conn.execute(
        "INSERT INTO paper_periods"
        "(alpha_id,as_of_ts,realized_return,realized_ic,source) VALUES (?,?,?,?,?)",
        (alpha_id, as_of_ts, realized_return, realized_ic, source),
    )
    updated = self._conn.execute(
        "UPDATE forward_decisions SET realized=1 "
        "WHERE alpha_id=? AND decided_ts=? AND realized=0",
        (alpha_id, decided_ts),
    )
    if updated.rowcount != 1:
        raise ForwardLedgerError("attested pending decision changed during commit")
    self._conn.execute(
        "INSERT INTO forward_local_events"
        "(event_type,alpha_id,decided_ts,payload_json,prev_hash,row_hash) "
        "VALUES (?,?,?,?,?,?)",
        event_values,
    )
    self._conn.commit()
except ForwardLedgerError:
    self._conn.rollback()
    raise
except sqlite3.DatabaseError as exc:
    self._conn.rollback()
    raise ForwardLedgerError(
        f"forward realization commit failed: {type(exc).__name__}"
    ) from exc
```

Install the `paper_periods_no_update` and `paper_periods_no_delete` triggers in `PaperTrackStore._SCHEMA`, where `paper_periods` is guaranteed to exist. `commit_realization` must check that `paper_periods` exists and fail closed if a caller created only a standalone decision ledger.

```sql
CREATE TRIGGER IF NOT EXISTS paper_periods_no_update
BEFORE UPDATE ON paper_periods
BEGIN SELECT RAISE(ABORT, 'paper_periods is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_periods_no_delete
BEFORE DELETE ON paper_periods
BEGIN SELECT RAISE(ABORT, 'paper_periods is append-only'); END;
```

The local event payload is exactly:

```python
{
    "event_type": "FORWARD_L2_REALIZATION_COMMITTED",
    "alpha_id": alpha_id,
    "decided_ts": int(decided_ts),
    "as_of_ts": int(as_of_ts),
    "realization_row_hash": realization_row_hash,
    "admission_id": admission_ref.admission_id,
    "source": source,
}
```

Add chain readers and validators using the same canonical/hash helpers as decision bindings. Preserve the current `PaperTrackStore.append_periods` content-conflict logic; do not return to `INSERT OR IGNORE`.

Add the exact port contract:

```python
def commit_realization(
    self,
    alpha_id: str,
    decided_ts: int,
    *,
    as_of_ts: int,
    realized_return: float,
    realized_ic: Optional[float],
    source: str,
    inputs: Mapping[str, object],
    admission_ref: ForwardAdmissionRef,
    committed_at_ms: int,
) -> bool: ...

def attested_realizations(self, alpha_id: str | None = None) -> list[dict]: ...
def realization_chain_valid(self) -> bool: ...
```

- [ ] **Step 4: Run GREEN and all paper-store tests**

```powershell
rtk python -m pytest tests/research/test_forward_track.py tests/research/test_paper_track_store.py tests/research/test_persistence_ports.py -q
```

Expected: pass with zero warnings.

- [ ] **Step 5: Commit Task 3**

```powershell
rtk git add services/research/paper_trading/forward_store.py services/research/paper_trading/store.py services/research/ports/persistence.py tests/research/test_forward_track.py tests/research/test_paper_track_store.py tests/research/test_persistence_ports.py
rtk git commit -m "feat(research): commit forward realizations atomically"
```

---

### Task 4: Domain Realization Uses the Atomic Authority

**Files:**
- Modify: `services/research/paper_trading/forward.py:45-123`
- Modify: `tests/research/test_forward_track.py`

**Interfaces:**
- Consumes: `ForwardLedger.commit_realization` and `ForwardAdmissionRef`.
- Produces: `realize_due(..., admission_ref, committed_at_ms)` with no boolean quality bypass.

- [ ] **Step 1: Rewrite tests around the desired API**

Replace `_realize_with_evidence` with:

```python
def _realize_attested(ledger, store, alpha_id, **kwargs):
    return realize_due(
        ledger,
        store,
        alpha_id,
        admission_ref=_ref(tuple(kwargs["prices"][min(kwargs["prices"])].keys())),
        committed_at_ms=max(kwargs["timestamps"]) + 1,
        **kwargs,
    )
```

Every decision used by a realization test must call `record_decision(..., admission_ref=_ref(scope))`. Add:

```python
def test_realize_due_has_no_boolean_quality_bypass(db):
    import inspect
    assert "quality_evidence_current" not in inspect.signature(realize_due).parameters


def test_realization_blocked_without_current_admission_keeps_pending(db):
    store, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )
    with pytest.raises(TypeError):
        realize_due(
            ledger,
            store,
            "a",
            timestamps=[_T0, _T0 + _WEEK],
            prices={_T0: {"BTC": 100.0}, _T0 + _WEEK: {"BTC": 101.0}},
            committed_at_ms=_T0 + _WEEK + 1,
        )
    assert len(ledger.attested_pending("a")) == 1
```

- [ ] **Step 2: Run RED**

```powershell
rtk python -m pytest tests/research/test_forward_track.py -q
```

Expected: the signature assertion fails and realization still calls the old two-commit path.

- [ ] **Step 3: Replace the boolean gate and two-commit sequence**

Change the signature to require:

```python
admission_ref: ForwardAdmissionRef,
committed_at_ms: int,
```

Iterate `ledger.attested_pending(alpha_id)`. Build reproducible inputs from only the names actually priced:

```python
inputs = {
    name: {
        "weight": float(weights[name]),
        "price_return": float(per_name[name]),
        "funding": 0.0 if funding_sum is None else float(funding_sum(name, t0, t1)),
    }
    for name in sorted(names)
}
```

Call only:

```python
inserted = ledger.commit_realization(
    alpha_id,
    t0,
    as_of_ts=t1,
    realized_return=ret,
    realized_ic=ic,
    source=source,
    inputs=inputs,
    admission_ref=admission_ref,
    committed_at_ms=committed_at_ms,
)
```

Delete `store.append_periods(...)` and `ledger.mark_realized(...)` from `realize_due`. Rebuild `paper_track` after committed rows; catch only `PaperTrackStoreError` and return `projection_status="STALE"` plus a redacted alert. Do not roll back or alter the authoritative realization.

Set the default source to the Task 1 constant:

```python
source: str = FORWARD_L2_REALIZATION_SOURCE,
```

- [ ] **Step 4: Run GREEN and no-lookahead regression tests**

```powershell
rtk python -m pytest tests/research/test_forward_track.py tests/research/test_forward_track_gate.py -q
```

Expected: pass; min-gap, coverage, funding and IC results remain unchanged.

- [ ] **Step 5: Commit Task 4**

```powershell
rtk git add services/research/paper_trading/forward.py tests/research/test_forward_track.py
rtk git commit -m "refactor(research): realize only attested forward decisions"
```

---

### Task 5: Official Driver Reopens the Authority and Separates Legacy State

**Files:**
- Modify: `tools/forward_track_advance.py:70-116,276-356`
- Modify: `tests/research/test_forward_track_gate.py`
- Create: `tests/tools/test_official_forward_evidence_wiring.py`

**Interfaces:**
- Consumes: exported `DATA_QUALITY_ADMISSION_DB`, `load_forward_admission_ref`, authority-bound ledger/domain APIs.
- Produces: `authoritative_admission_status(...) -> tuple[ForwardAdmissionRef | None, dict, list[str]]` and an honest cache snapshot.

- [ ] **Step 1: Write driver wiring and BLOCK-state tests**

Change the existing helper expectation so the first result is a reference, not a bool:

```python
ref, status, alerts = authoritative_admission_status(
    now_ms=_NOW,
    runner=lambda **kwargs: (evidence, decision, True),
    authority_path=str(authority_path),
)
assert ref is not None
assert status["verdict"] == "PASS"
```

For BLOCK or an exception, assert `ref is None` and no sensitive error text is returned. Add an AST/text boundary test:

```python
from pathlib import Path


def test_official_driver_uses_attested_api_and_no_boolean_bypass():
    source = Path("tools/forward_track_advance.py").read_text(encoding="utf-8")
    assert "load_forward_admission_ref" in source
    assert "admission_ref=" in source
    assert "quality_evidence_current=" not in source
    assert 'source="FORWARD_LIVE' not in source
```

- [ ] **Step 2: Run RED**

```powershell
rtk python -m pytest tests/research/test_forward_track_gate.py tests/tools/test_official_forward_evidence_wiring.py -q
```

Expected: failures because the helper returns a bool and the driver still passes `quality_evidence_current`.

- [ ] **Step 3: Reopen and verify the committed authority**

Add optional `authority_path=DATA_QUALITY_ADMISSION_DB` for test injection. After the runner commits, reopen:

```python
authority = DataQualityAdmissionStore(authority_path, create=False)
try:
    ref = load_forward_admission_ref(
        authority,
        admission_id=decision.admission_id,
        evidence_sha256=decision.evidence_sha256,
        decision_hash=decision.decision_hash,
        instruments=decision.instrument_scope,
    )
finally:
    authority.close()
```

Return `(ref, summary, alerts)` on PASS and `(None, summary, alerts)` on BLOCK/BREACH. In `main`, do not record or realize when `ref is None`. Pass the ref to both `record_decision` and `realize_due` and pass `committed_at_ms=now_ms`.

Build status counts from authority tables:

```python
"n_forward_periods": len(ledger.attested_realizations(alpha_id)),
"attested_pending_decisions": len(ledger.attested_pending(alpha_id)),
"legacy_unattested_decisions": len([
    row for row in ledger.legacy_decisions() if row["alpha_id"] == alpha_id
]),
"evidence_state": "DATA_BLOCKED" if admission_ref is None else "ATTESTED",
```

Do not mutate or delete legacy rows. New decisions use the L2 attested source from Task 4.

- [ ] **Step 4: Run GREEN**

```powershell
rtk python -m pytest tests/research/test_forward_track_gate.py tests/tools/test_official_forward_evidence_wiring.py -q
```

Expected: pass with redacted BLOCK/BREACH behavior.

- [ ] **Step 5: Commit Task 5**

```powershell
rtk git add tools/forward_track_advance.py tests/research/test_forward_track_gate.py tests/tools/test_official_forward_evidence_wiring.py
rtk git commit -m "feat(tools): make forward admission authority load-bearing"
```

---

### Task 6: Deterministic Forward Evidence Baseline

**Files:**
- Create: `tools/forward_evidence_status.py`
- Create: `tests/tools/test_forward_evidence_status.py`
- Generate: `evidence/forward_evidence_baseline.json`

**Interfaces:**
- Consumes: `ForwardLedger` read APIs and `DataQualityAdmissionStore`.
- Produces: `build_forward_evidence(repo_root, *, observed_at_ms) -> dict`, `verify_forward_evidence(document, repo_root) -> dict`, and CLI `--check`.

- [ ] **Step 1: Write deterministic generation and tamper tests**

The tests must build real temp SQLite stores, record one legacy decision, one attested pending decision and one attested realization, then assert:

```python
first = build_forward_evidence(tmp_path, observed_at_ms=500)
second = build_forward_evidence(tmp_path, observed_at_ms=500)
assert first == second
assert first["readiness"] == "L2_DRY_RUN_ONLY"
assert first["counts"] == {
    "attested_decisions": 2,
    "attested_pending": 1,
    "attested_realizations": 1,
    "legacy_unattested_decisions": 1,
}
assert first["evidence_gate"]["minimum_weeks"] == 8
assert first["evidence_gate"]["eligible"] is False
assert first["max_age_ms"] == 172_800_000
assert set(first["implementation"]["files"]) == {
    "services/research/paper_trading/forward.py",
    "services/research/paper_trading/forward_evidence.py",
    "services/research/paper_trading/forward_store.py",
    "tools/forward_track_advance.py",
}
```

Copy the test database, explicitly drop the append-only trigger in that disposable copy, mutate one binding row, and assert `verify_forward_evidence` raises `ForwardEvidenceError` because the trigger set or chain is invalid. Assert `main(["--check", ...])` never changes database bytes or mtimes.

- [ ] **Step 2: Run RED**

```powershell
rtk python -m pytest tests/tools/test_forward_evidence_status.py -q
```

Expected: module import failure.

- [ ] **Step 3: Implement generator and checker**

The document has this exact top-level structure:

```python
{
    "schema_version": 1,
    "observed_at_ms": int(observed_at_ms),
    "max_age_ms": 172_800_000,
    "readiness": "L2_DRY_RUN_ONLY",
    "source_database": "data/cache/paper_track.db",
    "price_basis": "PERP_MARK_PRICE",
    "decision_chain_valid": ledger.decision_chain_valid(),
    "realization_chain_valid": ledger.realization_chain_valid(),
    "counts": counts,
    "alphas": alpha_rows,
    "data_admission": admission_summary,
    "implementation": implementation_binding,
    "evidence_gate": {
        "minimum_weeks": 8,
        "eligible": all(row["attested_weeks"] >= 8 for row in alpha_rows) and bool(alpha_rows),
    },
    "honesty_note": "Only v2 authority-bound realizations count; legacy and backfilled rows never count.",
}
```

Build `implementation_binding` from the exact four paths asserted by the test. Each entry contains `sha256` and `size`; the parent binding contains a canonical aggregate SHA-256. `verify_forward_evidence` must rebuild the document using its `observed_at_ms`, compare the full canonical object, verify both chains and fail closed when the database, authority or bound implementation is missing or changed. CLI arguments are `--repo-root`, `--database`, `--admission-database`, `--output`, `--observed-at-ms`, and `--check`.

- [ ] **Step 4: Run GREEN and generate current honest evidence**

```powershell
rtk python -m pytest tests/tools/test_forward_evidence_status.py -q
rtk python tools/forward_evidence_status.py --observed-at-ms 1784296800000
rtk python tools/forward_evidence_status.py --check
```

Expected: tests pass; current evidence reports zero attested realized weeks if the admission is still BLOCK, and never promotes legacy rows.

- [ ] **Step 5: Commit Task 6**

```powershell
rtk git add tools/forward_evidence_status.py tests/tools/test_forward_evidence_status.py evidence/forward_evidence_baseline.json
rtk git commit -m "feat(evidence): publish attested forward clock baseline"
```

---

### Task 7: Go/No-Go and Console Consume the Same Honest State

**Files:**
- Modify: `tools/gonogo_report.py:90-115`
- Modify: `tests/tools/test_gonogo_report.py`
- Modify: `services/console/api/artifact_snapshots.py:270-290`
- Modify: `tests/research/test_console_api_contract.py`
- Modify: `apps/research-console/src/lib/types/research.ts:1580-1620`
- Modify: `apps/research-console/src/pages/LiveTrading.tsx:15-145,319-325`
- Modify: `apps/research-console/src/pages/__tests__/workflow-boundaries.test.tsx`

**Interfaces:**
- Consumes: `evidence/forward_evidence_baseline.json`.
- Produces: the same attested/legacy/admission status in reports, API and UI; no mutation or enabled action.

- [ ] **Step 1: Add backend and report contract tests**

Seed a baseline with `attested_realizations=0`, `legacy_unattested_decisions=2`, admission `BLOCK`; assert the Go/No-Go report contains “认证前向 0/8 周” and “历史未认证 2”，and never says the gate passed.

Update the console contract expectation:

```python
assert payload["forward_track"]["evidence_state"] == "DATA_BLOCKED"
assert payload["forward_track"]["attested_realizations"] == 0
assert payload["forward_track"]["legacy_unattested_decisions"] == 2
assert payload["forward_track"]["actions_allowed"] is False
```

- [ ] **Step 2: Run backend RED**

```powershell
rtk python -m pytest tests/tools/test_gonogo_report.py tests/research/test_console_api_contract.py -q
```

Expected: missing attested/legacy fields and legacy count still drives the report.

- [ ] **Step 3: Wire report and API to the controlled baseline**

Read `evidence/forward_evidence_baseline.json` through the existing safe JSON loader. Return:

```python
forward_track = {
    "present": True,
    "evidence_state": state,
    "attested_realizations": counts["attested_realizations"],
    "attested_pending": counts["attested_pending"],
    "legacy_unattested_decisions": counts["legacy_unattested_decisions"],
    "evidence_gate_weeks": evidence["evidence_gate"]["minimum_weeks"],
    "evidence_gate_eligible": evidence["evidence_gate"]["eligible"],
    "data_admission": evidence["data_admission"],
    "actions_allowed": False,
}
```

Missing/invalid evidence returns `present=False`, `evidence_state="UNAVAILABLE"`, `actions_allowed=False`. Evidence older than its exact `max_age_ms=172800000` returns `present=True`, `evidence_state="STALE"`, `actions_allowed=False`. Neither path may fall back to legacy cache counts.

- [ ] **Step 4: Add frontend RED test, then render honest labels**

In `workflow-boundaries.test.tsx`, render a BLOCKED payload and assert:

```typescript
expect(screen.getByText(/认证前向.*0.*8/)).toBeInTheDocument()
expect(screen.getByText(/历史未认证.*2/)).toBeInTheDocument()
expect(screen.queryByRole('button', { name: /启动|执行|交易/ })).not.toBeInTheDocument()
```

Run RED:

```powershell
rtk npm test -- --run src/pages/__tests__/workflow-boundaries.test.tsx
```

Update `LiveTradingForwardTrack` with the exact fields above. Change page copy from “实盘路线/Live Trading” to “L2 前向证据/L2 Forward Evidence” for this card, show `DATA_BLOCKED` and `LEGACY_UNATTESTED` as warnings, and keep all actions disabled.

- [ ] **Step 5: Run GREEN for backend and frontend**

```powershell
rtk python -m pytest tests/tools/test_gonogo_report.py tests/research/test_console_api_contract.py -q
rtk npm test -- --run src/pages/__tests__/workflow-boundaries.test.tsx
rtk npm run typecheck
```

Run npm commands from `apps/research-console`.

- [ ] **Step 6: Commit Task 7**

```powershell
rtk git add tools/gonogo_report.py tests/tools/test_gonogo_report.py services/console/api/artifact_snapshots.py tests/research/test_console_api_contract.py apps/research-console/src/lib/types/research.ts apps/research-console/src/pages/LiveTrading.tsx apps/research-console/src/pages/__tests__/workflow-boundaries.test.tsx
rtk git commit -m "feat(console): surface attested L2 forward evidence"
```

---

### Task 8: Governance Contract, Evidence Stabilization, and Full Verification

**Files:**
- Create: `tests/governance/test_forward_evidence_boundary.py`
- Modify only if required by deterministic generators: `evidence/model_risk_baseline.json`, `evidence/model_risk_history.json`, `evidence/reproducibility/container_build_latest.json`, `evidence/sbom.cdx.json`, `evidence/supply_chain_baseline.json`, `evidence/top_tier_assessment.json`, `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: machine-enforced L2 boundary, current evidence digests and the canonical gate result.

- [ ] **Step 1: Add governance boundary tests**

Create tests that parse/import the official files and assert:

```python
import ast
from pathlib import Path


def imported_roots(*paths: Path) -> set[str]:
    roots: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots.update(
            node.names[0].name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import) and node.names
        )
        roots.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
    return roots


def test_forward_services_are_offline_and_l2_only():
    forbidden = {"requests", "httpx", "urllib", "socket", "websockets"}
    imports = imported_roots(
        Path("services/research/paper_trading/forward.py"),
        Path("services/research/paper_trading/forward_store.py"),
        Path("services/research/paper_trading/forward_evidence.py"),
    )
    assert imports.isdisjoint(forbidden)


def test_official_forward_path_cannot_count_legacy_or_boolean_admission():
    driver = Path("tools/forward_track_advance.py").read_text(encoding="utf-8")
    status = Path("tools/forward_evidence_status.py").read_text(encoding="utf-8")
    assert "quality_evidence_current=" not in driver
    assert "attested_realizations" in driver
    assert "legacy_unattested_decisions" in status
    assert "LIVE_TRADING_ENABLED" not in driver
```

Use the repository’s existing AST import helper rather than executing imports through regex when available.

- [ ] **Step 2: Run RED, then add only the minimal boundary glue required**

```powershell
rtk python -m pytest tests/governance/test_forward_evidence_boundary.py -q
```

Expected RED if any forbidden import, boolean bypass or legacy counter remains. Fix only the identified boundary violation, then rerun until GREEN.

- [ ] **Step 3: Run all focused suites before evidence regeneration**

```powershell
rtk python -m pytest tests/research/test_forward_evidence_admission.py tests/research/test_forward_track.py tests/research/test_forward_track_gate.py tests/tools/test_official_forward_evidence_wiring.py tests/tools/test_forward_evidence_status.py tests/tools/test_gonogo_report.py tests/governance/test_forward_evidence_boundary.py -q
rtk python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

Expected: all focused tests pass and scanner reports zero violations.

- [ ] **Step 4: Exercise the real current authority without fabricating PASS**

```powershell
rtk python tools/data_quality_admission.py
rtk python tools/forward_evidence_status.py --check
```

Expected current honest state: if data admission remains BLOCK, no new attested decision or realization is written, attested weeks remain zero, and the command reports the concrete BLOCK reasons without exposing credentials.

- [ ] **Step 5: Stabilize generated evidence in dependency order**

Run only after code and tests stop changing:

```powershell
rtk python scripts/generate_model_risk_evidence.py
rtk python scripts/update_model_risk_history.py
rtk python scripts/generate_container_reproducibility_evidence.py
rtk python scripts/generate_supply_chain_evidence.py
rtk python scripts/assess_top_tier.py
```

Expected: each generator succeeds; the top-tier result may remain `NOT_TOP_TIER` because external identity, restore-history duration, data-vendor depth and natural forward time are honest open conditions.

- [ ] **Step 6: Run the canonical full gate**

```powershell
rtk python scripts/validate.py
```

Expected: zero unexpected failures. If an external/time-bound checker remains non-green by design, its exact fail-closed result must be reported and must not be waived or hand-edited.

- [ ] **Step 7: Inspect the final scope and commit verification artifacts**

```powershell
rtk git diff --check
rtk git status --short
rtk git diff --stat
```

Stage only files named by this plan and deterministic evidence files regenerated by the commands above:

```powershell
rtk git add tests/governance/test_forward_evidence_boundary.py evidence/model_risk_baseline.json evidence/model_risk_history.json evidence/reproducibility/container_build_latest.json evidence/sbom.cdx.json evidence/supply_chain_baseline.json evidence/top_tier_assessment.json docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md
rtk git commit -m "test(governance): enforce trustworthy forward evidence"
```

Do not stage unrelated dirty files. Record the exact full-gate counts and remaining machine red lines in the handoff.

---

## Self-Review Checklist

- Spec coverage: Tasks 1-8 cover authority binding, identity conflict, atomicity, append-only chains, legacy isolation, driver wiring, controlled evidence, console/report honesty, governance and full verification.
- Intentional deferral: cross-host event dispatch, external signing/WORM, real vendor data and the natural 8-week wait remain outside this L2 subproject and stay visibly blocked.
- Placeholder scan: every behavior-changing step names the exact API, error state, test and verification command; no step delegates unspecified work.
- Type consistency: `ForwardAdmissionRef`, `load_forward_admission_ref`, `record_decision(admission_ref=...)`, `commit_realization`, `attested_pending`, `attested_realizations`, `decision_chain_valid`, and `realization_chain_valid` use the same names in every task.
- Dirty-tree preservation: execution requires an isolated worktree and explicit porting of only the four existing in-scope dirty diffs; no bulk staging is allowed.
