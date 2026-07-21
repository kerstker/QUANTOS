from tools.three_alpha_paper_flow import run_flow
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
    )

    assert calls == ["carry_oi_majors", "crowd_contra", "top_contra"]
    assert report["verdict"]["flow_completed"] is True
    assert report["verdict"]["paper_packet_ready_for_all_three"] is True
    assert report["verdict"]["live_allowed"] is False
    assert report["verdict"]["order_submission_allowed"] is False
    assert report["safety"]["credential_access"] is False


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
    )

    assert report["verdict"]["flow_completed"] is False
    assert report["verdict"]["paper_packet_ready_for_all_three"] is False
    assert report["stages"]["authoritative_dry_runs"][0]["status"] == "ERROR"
    assert report["verdict"]["live_allowed"] is False


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
