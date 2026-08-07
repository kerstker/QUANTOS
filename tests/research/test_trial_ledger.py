from __future__ import annotations

import sqlite3
import threading
from dataclasses import FrozenInstanceError

import pytest

from services.research.trial_ledger import (
    TrialLedger,
    TrialLedgerConflict,
    TrialLedgerError,
    TrialRecord,
    EffectiveTrialCount,
)


def h(n: int) -> str:
    return f"{n:064x}"


def rec(trial: int = 1, *, family: int = 10, exact: int | None = None,
        policy: int = 99, status: str = "PLANNED", index: int = 0, at: int = 1) -> TrialRecord:
    return TrialRecord(h(trial), h(trial + 1000), h(family), h(exact or trial), h(policy), status, index, at)


def test_empty_and_valid_transitions_persist(tmp_path):
    ledger = TrialLedger(str(tmp_path / "trials.db"))
    assert ledger.verify() is True
    ledger.append(rec())
    ledger.append(rec(status="RUNNING", index=1, at=2))
    ledger.append(rec(status="PASSED", index=2, at=3))
    loaded = ledger.load(h(1))
    assert loaded.status == "PASSED"
    assert [e.status for e in loaded.events] == ["PLANNED", "RUNNING", "PASSED"]
    assert TrialLedger(str(tmp_path / "trials.db")).load(h(1)) == loaded


@pytest.mark.parametrize("status,index", [("RUNNING", 0), ("PLANNED", 1), ("PASSED", 1), ("BOGUS", 0)])
def test_invalid_status_or_transition_rejected(tmp_path, status, index):
    ledger = TrialLedger(str(tmp_path / "trials.db"))
    ledger.append(rec())
    with pytest.raises(TrialLedgerError):
        ledger.append(rec(status=status, index=index, at=2))


def test_idempotency_and_conflict(tmp_path):
    ledger = TrialLedger(str(tmp_path / "trials.db"))
    first = ledger.append(rec())
    second = ledger.append(rec())
    assert second == first
    with pytest.raises(TrialLedgerConflict):
        ledger.append(rec(at=999))


def test_sqlite_triggers_reject_mutation(tmp_path):
    path = str(tmp_path / "trials.db")
    ledger = TrialLedger(path)
    ledger.append(rec())
    con = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE trial_events SET status='RUNNING'")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM trial_events")
    con.close()


@pytest.mark.parametrize("tamper", [
    "UPDATE trial_events SET status='RUNNING'",  # trigger itself
    "UPDATE trial_heads SET head_hash='x' WHERE trial_id=?",
    "UPDATE trial_heads SET event_count=99 WHERE trial_id=?",
])
def test_verify_detects_direct_tampering(tmp_path, tamper):
    path = str(tmp_path / "trials.db")
    ledger = TrialLedger(path)
    ledger.append(rec())
    con = sqlite3.connect(path)
    if "trial_events" in tamper:
        con.execute("DROP TRIGGER trial_events_no_update")
        con.execute("UPDATE trial_events SET status='RUNNING'")
    else:
        con.execute(tamper, (h(1),))
    con.commit(); con.close()
    with pytest.raises(TrialLedgerError):
        TrialLedger(path).verify()


def test_hole_reorder_and_tail_delete_detected(tmp_path):
    path = str(tmp_path / "trials.db")
    ledger = TrialLedger(path)
    ledger.append(rec()); ledger.append(rec(status="RUNNING", index=1, at=2))
    con = sqlite3.connect(path)
    con.execute("DROP TRIGGER trial_events_no_delete")
    con.execute("DELETE FROM trial_events WHERE event_index=1")
    con.commit(); con.close()
    with pytest.raises(TrialLedgerError):
        TrialLedger(path).verify()


def test_concurrent_same_content_single_row(tmp_path):
    path = str(tmp_path / "trials.db")
    a, b = TrialLedger(path), TrialLedger(path)
    results, errors = [], []
    barrier = threading.Barrier(2)
    def run(x):
        try:
            barrier.wait(); results.append(x.append(rec()))
        except Exception as exc:
            errors.append(exc)
    ts = [threading.Thread(target=run, args=(x,)) for x in (a, b)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(results) == 2 and not errors
    assert sqlite3.connect(path).execute("SELECT count(*) FROM trial_events").fetchone()[0] == 1


def test_concurrent_different_content_one_conflict(tmp_path):
    path = str(tmp_path / "trials.db")
    a, b = TrialLedger(path), TrialLedger(path)
    barrier = threading.Barrier(2); errors = []
    def run(x):
        try: barrier.wait(); x.append(rec(at=2 if x is b else 1))
        except Exception as exc: errors.append(exc)
    ts = [threading.Thread(target=run, args=(x,)) for x in (a, b)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(isinstance(e, TrialLedgerConflict) for e in errors) == 1


def test_effective_count_dedupes_exact_but_counts_attempts(tmp_path):
    ledger = TrialLedger(str(tmp_path / "trials.db"))
    for trial, exact, policy in [(1, 500, 9), (2, 500, 9), (3, 501, 9), (4, 500, 10)]:
        ledger.append(rec(trial, exact=exact, policy=policy))
        ledger.append(rec(trial, exact=exact, policy=policy, status="RUNNING", index=1, at=2))
        ledger.append(rec(trial, exact=exact, policy=policy, status="FAILED", index=2, at=3))
    result = ledger.effective_trial_count(h(10))
    assert isinstance(result, EffectiveTrialCount)
    assert result.count == 3
    assert result.trial_ids == (h(1), h(3), h(4))


def test_cancelled_counts_running_and_planned_does_not(tmp_path):
    ledger = TrialLedger(str(tmp_path / "trials.db"))
    ledger.append(rec(1, family=20))
    ledger.append(rec(2, family=20)); ledger.append(rec(2, family=20, status="RUNNING", index=1, at=2)); ledger.append(rec(2, family=20, status="CANCELLED", index=2, at=3))
    assert ledger.effective_trial_count(h(20)).trial_ids == (h(2),)


def test_loaded_aggregate_is_immutable(tmp_path):
    ledger = TrialLedger(str(tmp_path / "trials.db")); ledger.append(rec())
    loaded = ledger.load(h(1))
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        loaded.status = "FAILED"




@pytest.mark.parametrize("field", ["alpha_id", "family_id", "exact_definition_id", "validation_policy_digest"])
def test_identity_cannot_drift_after_first_event(tmp_path, field):
    ledger = TrialLedger(str(tmp_path / "trials.db")); ledger.append(rec())
    values = {"alpha_id": h(700), "family_id": h(701), "exact_definition_id": h(702), "validation_policy_digest": h(703)}
    args = {"alpha_id": h(1001), "family_id": h(10), "exact_definition_id": h(1), "validation_policy_digest": h(99)}
    args[field] = values[field]
    with pytest.raises(TrialLedgerConflict):
        ledger.append(TrialRecord(h(1), args["alpha_id"], args["family_id"], args["exact_definition_id"], args["validation_policy_digest"], "RUNNING", 1, 2))


def test_verify_replays_transition_even_if_hashes_recomputed(tmp_path):
    path = str(tmp_path / "trials.db"); ledger = TrialLedger(path); ledger.append(rec()); ledger.append(rec(status="RUNNING", index=1, at=2)); ledger.append(rec(status="PASSED", index=2, at=3))
    con = sqlite3.connect(path); con.execute("DROP TRIGGER trial_events_no_update")
    con.execute("UPDATE trial_events SET status='FAILED' WHERE trial_id=? AND event_index=2", (h(1),)); con.commit(); con.close()
    with pytest.raises(TrialLedgerError): TrialLedger(path).verify()


def test_effective_count_uses_first_event_identity(tmp_path):
    path = str(tmp_path / "trials.db"); ledger = TrialLedger(path); ledger.append(rec(1, family=30)); ledger.append(rec(1, family=30, status="RUNNING", index=1, at=2))
    con = sqlite3.connect(path); con.execute("DROP TRIGGER trial_events_no_update")
    con.execute("UPDATE trial_events SET family_id=? WHERE trial_id=? AND event_index=1", (h(31), h(1))); con.commit(); con.close()
    with pytest.raises(TrialLedgerError): ledger.effective_trial_count(h(31))


def test_per_trial_global_commitment_tamper_detected(tmp_path):
    path = str(tmp_path / "trials.db"); ledger = TrialLedger(path); ledger.append(rec())
    con = sqlite3.connect(path); con.execute("UPDATE trial_heads SET global_count=7 WHERE trial_id=?", (h(1),)); con.commit(); con.close()
    with pytest.raises(TrialLedgerError): TrialLedger(path).verify()


def test_create_false_missing_or_closed_database_is_ledger_error(tmp_path):
    with pytest.raises(TrialLedgerError): TrialLedger(str(tmp_path / "missing.db"), create=False)
    ledger = TrialLedger(":memory:"); ledger._c.close()
    with pytest.raises(TrialLedgerError): ledger.verify()
