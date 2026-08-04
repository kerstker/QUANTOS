import sqlite3

import pytest

from services.monitoring.paper_operational_evidence import (
    PRODUCTION_PAPER_POLICY_V1,
    PaperOperationalEvidenceError,
    PaperOperationalEvidenceStore,
    assess_current_shadow_cycle,
    assess_shadow_artifact,
    evaluate_operational_window,
)


def _event(event_type, event_id, occurred_at_ms, payload):
    return {
        "event_type": event_type,
        "event_id": event_id,
        "occurred_at_ms": occurred_at_ms,
        "payload": payload,
    }


def test_operational_store_is_idempotent_conflict_safe_and_tamper_evident(tmp_path):
    path = tmp_path / "operational.db"
    store = PaperOperationalEvidenceStore(str(path), create=True)
    payload = {"started_at_ms": 1, "finished_at_ms": 2, "accepted": True}
    assert store.append(event_type="CYCLE", event_id="cycle-1", occurred_at_ms=2, payload=payload)
    assert not store.append(event_type="CYCLE", event_id="cycle-1", occurred_at_ms=2, payload=payload)
    with pytest.raises(PaperOperationalEvidenceError):
        store.append(
            event_type="CYCLE",
            event_id="cycle-1",
            occurred_at_ms=2,
            payload={**payload, "accepted": False},
        )
    assert store.chain_valid()
    store.close()

    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER paper_operational_events_no_update")
    conn.execute("UPDATE paper_operational_events SET payload_json='{}' WHERE seq=1")
    conn.commit()
    conn.close()
    reopened = PaperOperationalEvidenceStore(str(path), create=False)
    assert not reopened.chain_valid()
    reopened.close()


def test_shadow_artifact_gate_separates_nonpromotable_operation_from_safety():
    report = {
        "errors": {},
        "verdict": {
            "paper_mark_ready": True,
            "backtest_reference_present_for_all_execution_candidates": False,
        },
        "safety": {
            "paper_only": True,
            "broker_connectivity": False,
            "broker_order_submission": False,
        },
        "alphas": {"alpha": {"status": "PAPER_MARKED"}},
    }
    accepted, reasons = assess_shadow_artifact(
        report, expected_candidates=("alpha",), upstream_exit_code=1
    )
    assert accepted and reasons == ()
    stale, stale_reasons = assess_current_shadow_cycle(
        report,
        expected_candidates=("alpha",),
        upstream_exit_code=1,
        artifact_refreshed=False,
    )
    assert not stale
    assert "CURRENT_ARTIFACT_NOT_REFRESHED" in stale_reasons
    report["safety"]["broker_connectivity"] = True
    unsafe, unsafe_reasons = assess_shadow_artifact(
        report, expected_candidates=("alpha",), upstream_exit_code=1
    )
    assert not unsafe
    assert "PAPER_SAFETY_BOUNDARY_INVALID" in unsafe_reasons


def test_window_requires_real_span_drills_cadence_and_release_restart():
    policy = PRODUCTION_PAPER_POLICY_V1
    start = 1_000_000
    events = []
    events.append(
        _event(
            "CYCLE",
            "predecessor-cycle",
            start - policy.cadence_ms + 1_000,
            {
                "started_at_ms": start - policy.cadence_ms,
                "finished_at_ms": start - policy.cadence_ms + 1_000,
                "release_commit": "a" * 40,
                "accepted": True,
            },
        )
    )
    for index in range(301):
        cycle_start = start + index * policy.cadence_ms
        events.append(
            _event(
                "CYCLE",
                f"cycle-{index}",
                cycle_start + 1_000,
                {
                    "started_at_ms": cycle_start,
                    "finished_at_ms": cycle_start + 1_000,
                    "release_commit": "b" * 40,
                    "accepted": True,
                },
            )
        )
    for name in policy.required_drills:
        events.append(_event("DRILL", name, start, {
            "name": name,
            "passed": True,
            "isolated": True,
            "collector_release_commit": "c" * 40,
        }))
    result = evaluate_operational_window(
        events,
        now_ms=start + 300 * policy.cadence_ms + 2_000,
        chain_valid=True,
    )
    assert result["engineering_smoke_ready"] is True
    assert result["production_window_ready"] is True
    assert result["status"] == "PASS"
    assert result["success_ratio"] == 1.0
    assert result["active_release_commit"] == "b" * 40
    assert result["cycle_count"] == 301
    assert result["retained_cycle_count"] == 302
    assert result["excluded_pre_active_release_cycle_count"] == 1
    assert result["planned_restart_recoveries"][0]["within_policy"] is True


def test_predecessor_failures_are_retained_but_do_not_poison_active_cohort():
    policy = PRODUCTION_PAPER_POLICY_V1
    events = [
        _event(
            "CYCLE",
            "old-failure",
            1_000,
            {
                "started_at_ms": 0,
                "finished_at_ms": 1_000,
                "release_commit": "a" * 40,
                "accepted": False,
            },
        )
    ]
    for index in range(7):
        start = 10_000 + index * policy.cadence_ms
        events.append(
            _event(
                "CYCLE",
                f"current-{index}",
                start + 1_000,
                {
                    "started_at_ms": start,
                    "finished_at_ms": start + 1_000,
                    "release_commit": "b" * 40,
                    "accepted": True,
                },
            )
        )
    result = evaluate_operational_window(
        events,
        now_ms=10_000 + 7 * policy.cadence_ms,
        chain_valid=True,
    )
    assert result["active_release_commit"] == "b" * 40
    assert result["cycle_count"] == 7
    assert result["accepted_cycle_count"] == 7
    assert result["success_ratio"] == 1.0
    assert result["retained_cycle_count"] == 8
    assert result["retained_success_ratio"] == 0.875
    assert result["excluded_pre_active_release_cycle_count"] == 1
    assert result["production_window_ready"] is False


def test_short_window_is_honestly_maturing():
    policy = PRODUCTION_PAPER_POLICY_V1
    events = [
        _event(
            "CYCLE",
            "cycle-1",
            2_000,
            {
                "started_at_ms": 1_000,
                "finished_at_ms": 2_000,
                "release_commit": "a" * 40,
                "accepted": True,
            },
        )
    ]
    for name in policy.required_drills:
        events.append(_event("DRILL", name, 2_000, {
            "name": name,
            "passed": True,
            "isolated": True,
            "collector_release_commit": "c" * 40,
        }))
    result = evaluate_operational_window(events, now_ms=3_000, chain_valid=True)
    assert result["status"] == "MATURING"
    assert result["engineering_smoke_ready"] is False
    assert result["production_window_ready"] is False
    assert "CONTINUOUS_CADENCE_NOT_PROVEN" in result["blockers"]
