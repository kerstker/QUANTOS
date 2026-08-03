from tools.three_alpha_paper_flow import run_flow
import tools.three_alpha_paper_flow as paper_flow
from services.execution.dry_run_store import DryRunRun, DryRunStore
from tools.dry_run_crowd_contra_multi import _existing_crowd_run


def test_flow_executes_all_three_drivers_and_keeps_live_disabled(tmp_path):
    calls = []

    def runner(alpha_id):
        def _run():
            calls.append(alpha_id)

        return _run

    snapshots = {
        alpha_id: {
            "run_id": f"run-{alpha_id}",
            "n_periods": 10,
            "pnl": {"residual": 1e-9},
            "research_risk_decision": "PASS",
        }
        for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra")
    }
    cycle = {"cycle": {"state": "PENDING_FORWARD_EVIDENCE"}, "alphas": {}}

    def readiness(alpha_id):
        return {
            "deployment": {
                "research_packet_ready_for_paper": True,
                "small_capital_live_verdict": "BLOCKED_L2_DRY_RUN_ONLY",
                "blockers": [],
            }
        }

    report = run_flow(
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        driver_runners={alpha_id: runner(alpha_id) for alpha_id in snapshots},
        snapshot_loader=lambda alpha_id, _path: snapshots[alpha_id],
        cycle_builder=lambda: cycle,
        readiness_builder=readiness,
        candidate_runner=lambda: None,
        candidate_ids_loader=lambda: (),
    )

    assert calls == ["carry_oi_majors", "crowd_contra", "top_contra"]
    assert report["stages"]["candidate_authoritative_dry_runs"] == []
    assert report["verdict"]["flow_completed"] is True
    assert report["verdict"]["paper_packet_ready_for_all_three"] is True
    assert report["verdict"]["live_allowed"] is False
    assert report["verdict"]["order_submission_allowed"] is False
    assert report["safety"]["credential_access"] is False


def test_flow_does_not_rewrite_evidence_when_only_runtime_changes(tmp_path, monkeypatch):
    output = tmp_path / "flow.json"
    cycle_output = tmp_path / "cycle.json"
    snapshots = {
        alpha_id: {
            "run_id": f"run-{alpha_id}",
            "n_periods": 10,
            "pnl": {"residual": 0.0},
            "research_risk_decision": "PASS",
        }
        for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra")
    }

    def readiness(_alpha_id):
        return {
            "deployment": {
                "research_packet_ready_for_paper": True,
                "small_capital_live_verdict": "BLOCKED_L2_DRY_RUN_ONLY",
                "blockers": [],
            }
        }

    clock = iter((1.0, 2.0, 3.0, 4.0))
    monkeypatch.setattr(paper_flow.time, "time", lambda: next(clock))
    kwargs = {
        "output_path": str(output),
        "cycle_output_path": str(cycle_output),
        "driver_runners": {alpha_id: (lambda: None) for alpha_id in snapshots},
        "snapshot_loader": lambda alpha_id, _path: snapshots[alpha_id],
        "cycle_builder": lambda: {"cycle": {}},
        "readiness_builder": readiness,
        "candidate_runner": lambda: None,
        "candidate_ids_loader": lambda: (),
    }

    first = run_flow(**kwargs)
    first_bytes = output.read_bytes()
    second = run_flow(**kwargs)

    assert output.read_bytes() == first_bytes
    assert second["started_at"] == first["started_at"]
    assert second["finished_at"] == first["finished_at"]


def test_flow_records_driver_failure_without_claiming_paper_ready(tmp_path):
    def failing():
        raise RuntimeError("paper driver failed")

    def readiness(_alpha_id):
        return {
            "deployment": {
                "research_packet_ready_for_paper": True,
                "small_capital_live_verdict": "BLOCKED_L2_DRY_RUN_ONLY",
                "blockers": [],
            }
        }

    report = run_flow(
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        driver_runners={
            "carry_oi_majors": failing,
            "crowd_contra": lambda: None,
            "top_contra": lambda: None,
        },
        snapshot_loader=lambda _alpha_id, _path: {"run_id": "paper", "n_periods": 1},
        cycle_builder=lambda: {"cycle": {}},
        readiness_builder=readiness,
        candidate_runner=lambda: None,
        candidate_ids_loader=lambda: (),
    )

    assert report["verdict"]["flow_completed"] is False
    assert report["verdict"]["paper_packet_ready_for_all_three"] is False
    assert report["stages"]["authoritative_dry_runs"][0]["status"] == "ERROR"
    assert report["verdict"]["live_allowed"] is False


def test_flow_records_forward_failure_without_claiming_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(
        paper_flow,
        "_run_public_forward_driver",
        lambda: {
            "status": "ERROR",
            "driver": "tools.forward_track_advance",
            "error_type": "RuntimeError",
            "error": "public refresh failed",
            "private_binance": False,
        },
    )

    def readiness(_alpha_id):
        return {
            "deployment": {
                "research_packet_ready_for_paper": True,
                "small_capital_live_verdict": "BLOCKED_L2_DRY_RUN_ONLY",
                "blockers": [],
            }
        }

    report = run_flow(
        run_drivers=False,
        advance_forward=True,
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        cycle_builder=lambda: {"cycle": {}},
        readiness_builder=readiness,
        candidate_runner=lambda: None,
        candidate_ids_loader=lambda: (),
    )

    assert report["stages"]["public_forward"]["status"] == "ERROR"
    assert report["verdict"]["flow_completed"] is False
    assert report["verdict"]["paper_packet_ready_for_all_three"] is False


def test_crowd_driver_reuses_existing_append_only_run(tmp_path):
    path = tmp_path / "dry_run.db"
    store = DryRunStore(str(path), create=True)
    store.save_run(
        DryRunRun(
            run_id="crowd-run",
            strategy_id="crowd_contra",
            timestamp=1,
            n_intents=1,
            n_approved=1,
            n_rejected=0,
            rejected_reasons=[],
            pnl={"sharpe": 1.0, "residual": 0.0},
            audit_root="audit",
        )
    )
    store.close()

    existing = _existing_crowd_run(str(path))
    assert existing is not None
    assert existing["run_id"] == "crowd-run"
    assert _existing_crowd_run(str(tmp_path / "missing.db")) is None


def _ready_readiness(_alpha_id):
    return {
        "deployment": {
            "research_packet_ready_for_paper": True,
            "small_capital_live_verdict": "BLOCKED_L2_DRY_RUN_ONLY",
            "blockers": [],
        }
    }


def _legacy_snapshots():
    return {
        alpha_id: {"run_id": f"run-{alpha_id}", "n_periods": 10, "pnl": {"residual": 0.0}}
        for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra")
    }


def test_candidate_stage_rows_pass_with_verified_snapshots(tmp_path):
    snapshots = _legacy_snapshots()
    snapshots["crowd_contra_3d"] = {
        "run_id": "run-crowd_contra_3d",
        "n_periods": 81,
        "pnl": {"residual": 1e-9, "sharpe": 0.5},
        "research_risk_decision": "RISK_APPROVED",
        "source": "dry_run_store",
    }
    report = run_flow(
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        driver_runners={alpha_id: (lambda: None) for alpha_id in _legacy_snapshots()},
        snapshot_loader=lambda alpha_id, _path: snapshots.get(alpha_id, {}),
        cycle_builder=lambda: {"cycle": {}},
        readiness_builder=_ready_readiness,
        candidate_runner=lambda: None,
        candidate_ids_loader=lambda: ("crowd_contra_3d",),
    )
    rows = report["stages"]["candidate_authoritative_dry_runs"]
    assert rows == [
        {
            "alpha_id": "crowd_contra_3d",
            "module": "tools.dry_run_candidate_multi",
            "status": "PASS",
            "paper_order_submission": False,
            "snapshot": snapshots["crowd_contra_3d"],
        }
    ]
    # Additive evidence only: the legacy verdicts are untouched by this stage.
    assert report["verdict"]["flow_completed"] is True
    assert report["verdict"]["paper_packet_ready_for_all_three"] is True
    assert report["verdict"]["live_allowed"] is False


def test_candidate_stage_marks_missing_snapshot_without_gating_legacy_verdict(tmp_path):
    snapshots = _legacy_snapshots()
    report = run_flow(
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        driver_runners={alpha_id: (lambda: None) for alpha_id in snapshots},
        snapshot_loader=lambda alpha_id, _path: snapshots.get(alpha_id, {}),
        cycle_builder=lambda: {"cycle": {}},
        readiness_builder=_ready_readiness,
        candidate_runner=lambda: None,
        candidate_ids_loader=lambda: ("crowd_contra_3d",),
    )
    rows = report["stages"]["candidate_authoritative_dry_runs"]
    assert rows[0]["status"] == "ERROR"
    assert rows[0]["reason"] == "DRY_RUN_ARTIFACT_MISSING_AFTER_DRIVER"
    assert rows[0]["paper_order_submission"] is False
    assert "snapshot" not in rows[0]
    assert report["verdict"]["flow_completed"] is True
    assert report["verdict"]["paper_packet_ready_for_all_three"] is True


def test_candidate_stage_records_driver_failure_fail_closed(tmp_path):
    def failing():
        raise RuntimeError("candidate driver failed")

    report = run_flow(
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        driver_runners={alpha_id: (lambda: None) for alpha_id in _legacy_snapshots()},
        snapshot_loader=lambda alpha_id, _path: _legacy_snapshots().get(alpha_id, {}),
        cycle_builder=lambda: {"cycle": {}},
        readiness_builder=_ready_readiness,
        candidate_runner=failing,
        candidate_ids_loader=lambda: ("crowd_contra_3d", "crowd_contra_14d"),
    )
    rows = report["stages"]["candidate_authoritative_dry_runs"]
    assert [row["status"] for row in rows] == ["ERROR", "ERROR"]
    assert all(row["error_type"] == "RuntimeError" for row in rows)
    assert [row["alpha_id"] for row in rows] == ["crowd_contra_3d", "crowd_contra_14d"]
    assert report["verdict"]["flow_completed"] is True


def test_candidate_stage_fails_closed_on_unresolved_population(tmp_path):
    def broken_ids():
        raise RuntimeError("alpha production workflow authority is not verified")

    report = run_flow(
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        driver_runners={alpha_id: (lambda: None) for alpha_id in _legacy_snapshots()},
        snapshot_loader=lambda alpha_id, _path: _legacy_snapshots().get(alpha_id, {}),
        cycle_builder=lambda: {"cycle": {}},
        readiness_builder=_ready_readiness,
        candidate_runner=lambda: None,
        candidate_ids_loader=broken_ids,
    )
    rows = report["stages"]["candidate_authoritative_dry_runs"]
    assert len(rows) == 1
    assert rows[0]["status"] == "ERROR"
    assert rows[0]["reason"] == "CANDIDATE_POPULATION_UNRESOLVED"
    assert rows[0]["alpha_id"] is None
    assert report["verdict"]["flow_completed"] is True


def test_candidate_stage_skipped_when_not_requested(tmp_path):
    called = []

    report = run_flow(
        run_candidates=False,
        output_path=str(tmp_path / "flow.json"),
        cycle_output_path=str(tmp_path / "cycle.json"),
        driver_runners={alpha_id: (lambda: None) for alpha_id in _legacy_snapshots()},
        snapshot_loader=lambda alpha_id, _path: _legacy_snapshots().get(alpha_id, {}),
        cycle_builder=lambda: {"cycle": {}},
        readiness_builder=_ready_readiness,
        candidate_runner=lambda: called.append(True),
    )
    assert report["stages"]["candidate_authoritative_dry_runs"] == []
    assert called == []
