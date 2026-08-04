import json
from datetime import datetime, timezone

from tools import paper_operational_evidence


def _iso(value_ms):
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc).isoformat()


def test_collector_binds_current_release_cycle_and_runs_isolated_drills(
    monkeypatch, tmp_path
):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    started = 1_000_000
    finished = 1_010_000
    receipt = {
        "module": "paper_shadow_realtime",
        "receipt_sha256": "c" * 64,
        "release_commit": "a" * 40,
        "release_verified": True,
        "status": "COMPLETED",
        "exit_code": 0,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
    }
    (receipt_root / "100-paper_shadow_realtime.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    shadow_path = tmp_path / "shadow.json"
    shadow_path.write_text(
        json.dumps(
            {
                "errors": {},
                "observed_at": started + 2_000,
                "upstream_cycle": {
                    "started_at_ms": started + 1_000,
                    "finished_at_ms": finished - 1_000,
                    "exit_code": 1,
                    "artifact_refreshed": True,
                },
                "verdict": {
                    "paper_mark_ready": True,
                    "backtest_reference_present_for_all_execution_candidates": False,
                },
                "safety": {
                    "paper_only": True,
                    "broker_connectivity": False,
                    "broker_order_submission": False,
                },
                "alphas": {
                    "alpha": {
                        "status": "PAPER_MARKED",
                        "reconciliation_residual": 0.0,
                    }
                },
                "persistence": {"chain_valid": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        paper_operational_evidence, "paper_candidate_ids", lambda: ("alpha",)
    )
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "b" * 40)
    report = paper_operational_evidence.collect(
        store_path=str(tmp_path / "operational.db"),
        output_path=str(tmp_path / "operational.json"),
        shadow_status_path=str(shadow_path),
        receipt_root=str(receipt_root),
        now_ms=finished + 1_000,
    )
    assert report["chain_valid"] is True
    assert report["window"]["accepted_cycle_count"] == 1
    assert all(report["window"]["drills"].values())
    assert report["window"]["production_window_ready"] is False
    assert report["verdict"]["paper_operation_continues"] is True
    assert report["safety"]["fault_injection_isolated"] is True
    assert report["safety"]["broker_order_submission"] is False


def test_fault_matrix_exercises_every_preregistered_drill():
    results = paper_operational_evidence.run_isolated_fault_matrix()
    assert set(results) == set(
        paper_operational_evidence.PRODUCTION_PAPER_POLICY_V1.required_drills
    )
    assert all(results.values())
