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
    attestation = paper_operational_evidence.release_run_attestation()
    assert attestation is not None and attestation["complete"] is True
    assert attestation["module"] == "paper_operational_evidence"
    assert attestation["release_commit"] == "b" * 40
    assert set(attestation["input_hashes"]) == {
        "candidate_universe",
        "fault_matrix_results",
        "operational_evidence_contract",
        "operational_store_authority_before",
        "upstream_cycle_view",
    }
    assert set(attestation["output_hashes"]) == {
        "operational_evidence_report_file",
        "operational_store_authority_after",
    }
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }

    replay = paper_operational_evidence.collect(
        store_path=str(tmp_path / "operational.db"),
        output_path=str(tmp_path / "operational.json"),
        shadow_status_path=str(shadow_path),
        receipt_root=str(receipt_root),
        now_ms=finished + 2_000,
    )
    assert replay["new_events"] == 0
    assert replay["window"]["accepted_cycle_count"] == 1
    assert all(replay["window"]["drills"].values())

    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "d" * 40)
    after_release_change = paper_operational_evidence.collect(
        store_path=str(tmp_path / "operational.db"),
        output_path=str(tmp_path / "operational.json"),
        shadow_status_path=str(shadow_path),
        receipt_root=str(receipt_root),
        now_ms=finished + 3_000,
    )
    assert after_release_change["new_events"] == len(
        paper_operational_evidence.PRODUCTION_PAPER_POLICY_V1.required_drills
    )
    assert after_release_change["window"]["accepted_cycle_count"] == 1
    changed_attestation = paper_operational_evidence.release_run_attestation()
    assert changed_attestation is not None
    assert changed_attestation["complete"] is True
    assert changed_attestation["release_commit"] == "d" * 40


def test_fault_matrix_exercises_every_preregistered_drill():
    results = paper_operational_evidence.run_isolated_fault_matrix()
    assert set(results) == set(
        paper_operational_evidence.PRODUCTION_PAPER_POLICY_V1.required_drills
    )
    assert all(results.values())


def test_cycle_release_replay_ignores_only_collector_commit() -> None:
    original = {
        "collector_release_commit": "a" * 40,
        "collector_release_verified": True,
        "artifact_sha256": "b" * 64,
        "accepted": True,
        "reasons": [],
    }
    new_collector = {**original, "collector_release_commit": "c" * 40}
    changed_artifact = {**new_collector, "artifact_sha256": "d" * 64}

    assert paper_operational_evidence._cycle_replay_view(
        original
    ) == paper_operational_evidence._cycle_replay_view(new_collector)
    assert paper_operational_evidence._cycle_replay_view(
        original
    ) != paper_operational_evidence._cycle_replay_view(changed_artifact)


def test_main_failure_clears_prior_release_attestation(monkeypatch) -> None:
    paper_operational_evidence._LAST_RELEASE_RUN_ATTESTATION = {"complete": True}

    def fail(**_kwargs):
        raise RuntimeError("broken operational evidence")

    monkeypatch.setattr(paper_operational_evidence, "collect", fail)

    assert paper_operational_evidence.main([]) == 1
    assert paper_operational_evidence.release_run_attestation() is None


def test_collector_marks_attestation_incomplete_when_upstream_changes(
    monkeypatch, tmp_path
) -> None:
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    receipt = {
        "module": "paper_shadow_realtime",
        "receipt_sha256": "c" * 64,
        "release_commit": "a" * 40,
        "release_verified": True,
        "status": "FAILED",
        "exit_code": 2,
        "started_at": _iso(1_000_000),
        "finished_at": _iso(1_010_000),
    }
    (receipt_root / "100-paper_shadow_realtime.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    shadow_path = tmp_path / "shadow.json"
    shadow_path.write_text(json.dumps({"status": "SAFE_BLOCKED"}), encoding="utf-8")
    monkeypatch.setattr(
        paper_operational_evidence, "paper_candidate_ids", lambda: ("alpha",)
    )
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "b" * 40)
    original = paper_operational_evidence._upstream_cycle_view
    calls = 0

    def changing_view(**kwargs):
        nonlocal calls
        calls += 1
        view, latest, shadow = original(**kwargs)
        if calls == 2:
            view = {**view, "status": "CHANGED_AFTER_CONSUMPTION"}
        return view, latest, shadow

    monkeypatch.setattr(
        paper_operational_evidence, "_upstream_cycle_view", changing_view
    )

    paper_operational_evidence.collect(
        store_path=str(tmp_path / "operational.db"),
        output_path=str(tmp_path / "operational.json"),
        shadow_status_path=str(shadow_path),
        receipt_root=str(receipt_root),
        now_ms=1_011_000,
    )

    attestation = paper_operational_evidence.release_run_attestation()
    assert attestation is not None
    assert attestation["complete"] is False
