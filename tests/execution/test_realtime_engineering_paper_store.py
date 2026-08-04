import copy

import pytest

from services.execution.realtime_engineering_paper_store import (
    RealtimeEngineeringPaperStore,
    RealtimeEngineeringPaperStoreError,
)


def _preview(candidate_id: str = "candidate-1", decision_ts: int = 200) -> dict:
    return {
        "alpha_id": candidate_id,
        "decision_ts": decision_ts,
        "run_id": "run-1",
        "paper_positions": {"BTC": 1},
        "orders": [{"instrument": "BTC"}],
        "n_paper_fill_slices": 2,
        "paper_execution": "PAPER_FILLED",
        "official_paper_execution": "PAPER_BLOCKED",
        "execution_class": "AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER",
        "reconciliation_residual": 0.0,
        "execution_tca": {
            "route_tca_available": True,
            "execution_cost": 0.25,
        },
        "promotion_eligible": False,
        "forward_evidence_eligible": False,
        "deployment_eligible": False,
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "broker_order_submission": False,
        "credentials_accessed": False,
    }


def test_admission_execution_restart_and_exact_replay(tmp_path) -> None:
    path = tmp_path / "engineering.db"
    with RealtimeEngineeringPaperStore(str(path), create=True) as store:
        assert store.admit(
            "candidate-1",
            {"source": "shadow", "class": "engineering"},
            baseline_decision_time_ms=100,
            admitted_at_ms=150,
        )
        assert not store.admit(
            "candidate-1",
            {"source": "shadow", "class": "engineering"},
            baseline_decision_time_ms=100,
            admitted_at_ms=150,
        )
        assert store.record_execution("candidate-1", _preview(), recorded_at_ms=250)
        assert not store.record_execution("candidate-1", _preview(), recorded_at_ms=250)
        assert store.latest_positions("candidate-1") == {"BTC": 1}
        assert store.summary("candidate-1")["n_fill_slices"] == 2
        assert store.chain_valid()

    with RealtimeEngineeringPaperStore(str(path), create=False) as reopened:
        assert reopened.admitted_candidate_ids() == ("candidate-1",)
        assert reopened.get_by_decision("candidate-1", 200) is not None
        assert reopened.chain_valid()


def test_conflict_and_authority_widening_fail_closed(tmp_path) -> None:
    path = tmp_path / "engineering.db"
    with RealtimeEngineeringPaperStore(str(path), create=True) as store:
        store.admit(
            "candidate-1",
            {"source": "shadow"},
            baseline_decision_time_ms=100,
            admitted_at_ms=150,
        )
        unsafe = _preview()
        unsafe["live_allowed"] = True
        with pytest.raises(RealtimeEngineeringPaperStoreError, match="boundary"):
            store.record_execution("candidate-1", unsafe, recorded_at_ms=250)

        store.record_execution("candidate-1", _preview(), recorded_at_ms=250)
        conflict = copy.deepcopy(_preview())
        conflict["paper_positions"] = {"BTC": 2}
        with pytest.raises(RealtimeEngineeringPaperStoreError, match="different content"):
            store.record_execution("candidate-1", conflict, recorded_at_ms=250)


def test_tamper_is_detected(tmp_path) -> None:
    path = tmp_path / "engineering.db"
    with RealtimeEngineeringPaperStore(str(path), create=True) as store:
        store.admit(
            "candidate-1",
            {"source": "shadow"},
            baseline_decision_time_ms=100,
            admitted_at_ms=150,
        )
        assert store.chain_valid()
        store._conn.execute("DROP TRIGGER realtime_engineering_paper_no_update")
        store._conn.execute(
            "UPDATE realtime_engineering_paper_events SET row_hash='bad' WHERE event_seq=1"
        )
        store._conn.commit()
        assert not store.chain_valid()


def test_missed_execution_window_is_append_only_and_resolves_decision(tmp_path) -> None:
    path = tmp_path / "engineering.db"
    payload = {
        "candidate_id": "candidate-1",
        "resolution": "MISSED_EXECUTION_WINDOW",
        "reason_code": "POST_DECISION_BOOK_DELAY_EXCEEDED",
        "promotion_eligible": False,
        "forward_evidence_eligible": False,
        "deployment_eligible": False,
        "live_allowed": False,
        "order_submission_allowed": False,
    }
    with RealtimeEngineeringPaperStore(str(path), create=True) as store:
        store.admit(
            "candidate-1",
            {"source": "shadow"},
            baseline_decision_time_ms=100,
            admitted_at_ms=150,
        )
        assert store.record_missed_decision(
            "candidate-1", payload, decision_time_ms=200, recorded_at_ms=300
        )
        assert not store.record_missed_decision(
            "candidate-1", payload, decision_time_ms=200, recorded_at_ms=300
        )
        assert store.resolved_decision_times("candidate-1") == {200}
        assert store.summary("candidate-1")["n_missed_execution_windows"] == 1
        assert store.event_count() == 2
        assert store.chain_valid()

        store._conn.execute(
            "DROP TRIGGER realtime_engineering_paper_missed_no_update"
        )
        store._conn.execute(
            "UPDATE realtime_engineering_paper_missed SET row_hash='bad' WHERE event_seq=1"
        )
        store._conn.commit()
        assert not store.chain_valid()
