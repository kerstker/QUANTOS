from __future__ import annotations

import pytest
from tests.risk.full_risk_fixture import complete_engine_reports

import tools.paper_health as paper_health
from tools.paper_health import ALPHA_IDS, DEFAULT_MAX_AGE_MS, evaluate_paper_health


@pytest.fixture(autouse=True)
def _authority_contract_double(monkeypatch):
    monkeypatch.setattr(
        paper_health,
        "is_authoritative_backtest_record",
        lambda record: bool(
            isinstance(record, dict)
            and isinstance(record.get("detail"), dict)
            and record["detail"].get("_test_manifest_valid") is True
        ),
    )


def _realtime(*, observed_at: int = 1_000) -> dict:
    return {
        "observed_at": observed_at,
        "verdict": {
            "cycle_completed": True,
            "paper_mark_ready": True,
            "backtest_reference_present_for_all_three": True,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "persistence": {"chain_valid": True},
        "errors": {},
        "safety": {
            "paper_only": True,
            "public_market_data_only": True,
            "broker_connectivity": False,
            "broker_order_submission": False,
            "credentials_accessed": False,
            "private_binance_access": False,
        },
        "alphas": {
            alpha: {
                "status": "PAPER_MARKED",
                "n_fills": 2,
                "equity": 1000.0,
                "net_pnl": 0.0,
                "reconciliation_residual": 0.0,
            }
            for alpha in ALPHA_IDS
        },
        "backtests": {
            alpha: {
                "provenance": "REAL_AUTHORITATIVE_L2_DRY_RUN",
                "research_risk": _full_research_risk(),
                "detail": {
                    "_test_manifest_valid": True,
                    "research_risk": _full_research_risk(),
                },
            }
            for alpha in ALPHA_IDS
        },
    }


def _full_research_risk() -> dict:
    return {
        "decision": "RISK_APPROVED",
        "status": "APPROVED",
        "gates_passed": 9,
        "gates_run": 9,
        "engines_run": {
            name: True
            for name in (
                "market", "liquidity", "concentration", "correlation",
                "drawdown", "regime", "stress", "capacity", "model_alpha",
            )
        },
        "engines_skipped": {},
        "engines_pending": {},
        "provenance": "REAL_RESEARCH_RISK_FULL",
        # Compute all nine reports over explicit synthetic history.
        "reports": complete_engine_reports(),
    }


def _preview() -> dict:
    full_research_risk = _full_research_risk()
    return {
        "verdict": {
            "paper_preview_ready": True,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "errors": {},
        "safety": {
            "paper_only": True,
            "broker_connectivity": False,
            "broker_order_submission": False,
            "credentials_accessed": False,
            "private_binance_access": False,
        },
        "alphas": {
            alpha: {
                "paper_execution": "PAPER_FILLED",
                "n_intents": 2,
                "n_approved": 2,
                "n_paper_fills": 2,
                "n_rejected": 0,
                "reconciliation_residual": 0.0,
                "research_risk": full_research_risk,
            }
            for alpha in ALPHA_IDS
        },
    }


def _forward_track(*, periods: int = 0, last_decision_ts: int | None = None) -> dict:
    return {
        "ran_at": 1_500,
        "evidence_gate_weeks": 8,
        "quality_evidence_current": True,
        "data_quality_admission": {
            "verdict": "PASS",
            "quality_verdict": "PASS",
            "lineage_verdict": "PASS",
        },
        "alphas": {
            alpha: {
                "n_forward_periods": periods,
                "pending_decisions": 1,
                "attested_pending_decisions": 1,
                "evidence_state": "ATTESTED_PENDING_REALIZATION",
                "paper_sharpe": None,
                "last_decision_ts": last_decision_ts,
            }
            for alpha in ALPHA_IDS
        },
        "alerts": [],
    }


def test_paper_health_passes_complete_fresh_three_alpha_artifacts():
    report = evaluate_paper_health(
        _realtime(), _preview(), now_ms=2_000, forward_track=_forward_track()
    )
    assert report["verdict"]["paper_ready"] is True
    assert report["age_ms"] == 1_000
    assert report["blockers"] == []
    assert report["verdict"]["live_allowed"] is False
    assert report["forward_evidence"]["complete_for_all_three"] is False
    assert report["forward_evidence"]["alphas"]["crowd_contra"]["periods_to_gate"] == 8
    assert report["readiness_class"] == "PAPER_READY"


def test_paper_health_distinguishes_mark_only_replay_from_order_ready():
    realtime = _realtime()
    realtime["operator_mode"] = "EXISTING_FILLED_PAPER_LEDGER_REPLAY"
    preview = _preview()
    # A mark-only replay can be healthy as an observation while the preview is
    # still blocked by the missing full-risk admission artifact.
    preview["verdict"]["paper_preview_ready"] = False
    preview["alphas"]["crowd_contra"]["research_risk"] = {
        "decision": "RISK_PENDING",
        "status": "PENDING",
        "gates_run": 4,
        "gates_passed": 4,
        "engines_run": {"market": True, "drawdown": True, "concentration": True, "stress": True},
        "engines_skipped": {"liquidity": "missing"},
        "engines_pending": {"liquidity": "missing"},
        "provenance": "REAL_RESEARCH_RISK_SUBSET",
    }
    report = evaluate_paper_health(
        realtime, preview, now_ms=2_000, forward_track=_forward_track()
    )
    assert report["verdict"]["paper_ready"] is False
    assert report["readiness_class"] == "PAPER_MARKS_ONLY_REPLAY"
    assert report["next_action"] == "resolve_research_risk_and_create_new_decision"


def test_paper_health_reports_attested_forward_clock_as_maturing_not_broken():
    realtime = _realtime()
    realtime["operator_mode"] = "EXISTING_FILLED_PAPER_LEDGER_REPLAY"
    preview = _preview()
    preview["verdict"]["paper_preview_ready"] = False
    for row in preview["alphas"].values():
        row["research_risk_bundle"] = {
            "status": "READY",
            "provenance": "REAL_RESEARCH_RISK_INPUT_BUNDLE",
        }
        row["research_risk"] = {
            "decision": "RISK_REJECTED",
            "status": "REJECTED",
            "gates_passed": 8,
            "gates_run": 9,
            "engines_run": {"model_alpha": True},
            "engines_skipped": {},
            "engines_pending": {},
            "provenance": "REAL_RESEARCH_RISK_FULL",
            "reasons": ["Model/Alpha: paper_conf=0.125"],
        }
        row["paper_execution"] = "PAPER_BLOCKED"
        row["n_paper_fills"] = 0
    report = evaluate_paper_health(
        realtime,
        preview,
        now_ms=2_000,
        forward_track=_forward_track(periods=1, last_decision_ts=1_000),
    )

    forward = report["forward_evidence"]
    assert report["readiness_class"] == "PAPER_FORWARD_EVIDENCE_MATURING"
    assert report["next_action"] == (
        "continue_scheduled_forward_evidence_and_review_remaining_risk_gates"
    )
    assert forward["maturity_state"] == "FORWARD_EVIDENCE_MATURING"
    assert forward["evidence_maturing"] is True
    assert forward["max_periods_to_gate"] == 7
    assert forward["next_realization_eligible_at"] == 1_000 + paper_health.DEFAULT_MIN_GAP_MS
    assert forward["wait_ms"] == paper_health.DEFAULT_MIN_GAP_MS - 1_000
    assert forward["alphas"]["top_contra"]["last_decision_ts"] == 1_000


def test_forward_clock_fails_closed_when_quality_or_pending_attestation_is_missing():
    forward = _forward_track(periods=1, last_decision_ts=1_000)
    forward["quality_evidence_current"] = False
    report = evaluate_paper_health(
        _realtime(), _preview(), now_ms=2_000, forward_track=forward
    )
    assert report["forward_evidence"]["evidence_maturing"] is False
    assert report["forward_evidence"]["maturity_state"] == "FORWARD_EVIDENCE_BLOCKED"


def test_paper_health_marks_forward_evidence_complete_only_after_all_periods():
    report = evaluate_paper_health(
        _realtime(), _preview(), now_ms=2_000, forward_track=_forward_track(periods=8)
    )
    assert report["verdict"]["paper_ready"] is True
    assert report["forward_evidence"]["complete_for_all_three"] is True
    assert report["forward_evidence"]["alphas"]["top_contra"]["periods_to_gate"] == 0


def test_paper_health_blocks_stale_marks():
    report = evaluate_paper_health(
        _realtime(observed_at=1),
        _preview(),
        now_ms=DEFAULT_MAX_AGE_MS + 2,
    )
    assert report["verdict"]["paper_ready"] is False
    assert "PAPER_MARKS_STALE" in report["blockers"]


def test_paper_health_blocks_missing_alpha_and_bad_reconciliation():
    realtime = _realtime()
    realtime["alphas"].pop("top_contra")
    realtime["alphas"]["crowd_contra"]["reconciliation_residual"] = 0.01
    report = evaluate_paper_health(realtime, _preview(), now_ms=2_000)
    assert report["verdict"]["paper_ready"] is False
    assert "PAPER_REALTIME_ALPHA_SET_INCOMPLETE" in report["blockers"]
    assert "crowd_contra:PAPER_RECONCILIATION_FAILED" in report["blockers"]


def test_paper_health_never_accepts_live_flags():
    preview = _preview()
    preview["verdict"]["live_allowed"] = True
    report = evaluate_paper_health(_realtime(), preview, now_ms=2_000)
    assert report["verdict"]["paper_ready"] is False
    assert "PREVIEW_LIVE_ALLOWED_NOT_FALSE" in report["blockers"]


def test_paper_health_blocks_four_engine_subset_even_when_fills_reconcile():
    preview = _preview()
    preview["alphas"]["crowd_contra"]["research_risk"] = {
        "decision": "RISK_APPROVED",
        "status": "APPROVED",
        "gates_passed": 4,
        "gates_run": 4,
        "engines_run": {
            "market": True,
            "drawdown": True,
            "concentration": True,
            "stress": True,
        },
        "engines_skipped": {"liquidity": "NO_HONEST_INPUT_AT_DRY_RUN_SEAM"},
        "engines_pending": {"liquidity": "NO_HONEST_INPUT_AT_DRY_RUN_SEAM"},
        "provenance": "REAL_RESEARCH_RISK_SUBSET",
    }
    report = evaluate_paper_health(_realtime(), preview, now_ms=2_000)
    assert report["verdict"]["paper_ready"] is False
    assert report["verdict"]["paper_preview_ready"] is False
    assert report["verdict"]["paper_research_risk_complete"] is False
    assert "crowd_contra:PAPER_RESEARCH_RISK_NOT_FULL" in report["blockers"]


def test_paper_health_does_not_trust_backtest_provenance_string_alone():
    realtime = _realtime()
    for row in realtime["backtests"].values():
        row.pop("detail")
    # Historical realtime summaries only self-labelled provenance; they did
    # not retain a RunManifestV2 or the complete nine-engine risk packet.
    report = evaluate_paper_health(realtime, _preview(), now_ms=2_000)
    assert report["verdict"]["paper_ready"] is False
    assert "crowd_contra:BACKTEST_AUTHORITY_INVALID" in report["blockers"]


def test_paper_health_surfaces_missing_research_risk_bundle_without_inventing_a_blocker():
    preview = _preview()
    preview["alphas"]["crowd_contra"]["research_risk_bundle"] = {
        "status": "PENDING",
        "missing": ["bundle_file"],
        "reasons": ["missing:bundle_file"],
    }
    report = evaluate_paper_health(_realtime(), preview, now_ms=2_000)
    assert report["research_risk_bundle_complete"] is False
    assert report["verdict"]["paper_research_risk_bundle_complete"] is False
    assert report["research_risk_bundles"]["crowd_contra"]["missing"] == ["bundle_file"]


def test_paper_health_blocks_missing_error_or_safety_fields():
    realtime = _realtime()
    realtime.pop("errors")
    realtime["safety"].pop("broker_connectivity")
    report = evaluate_paper_health(realtime, _preview(), now_ms=2_000)
    assert report["verdict"]["paper_ready"] is False
    assert "PAPER_REALTIME_REPORTED_ERRORS" in report["blockers"]
    assert "REALTIME_BROKER_CONNECTIVITY_ENABLED" in report["blockers"]


def test_required_unique_alpha_workflow_is_fail_closed() -> None:
    invalid = evaluate_paper_health(
        _realtime(),
        _preview(),
        now_ms=2_000,
        alpha_production_workflow={},
    )
    assert "UNIQUE_ALPHA_PRODUCTION_WORKFLOW_NOT_VERIFIED" in invalid["blockers"]
    assert invalid["verdict"]["unique_alpha_production_workflow_verified"] is False

    verified = evaluate_paper_health(
        _realtime(),
        _preview(),
        now_ms=2_000,
        alpha_production_workflow={
            "schema_version": 1,
            "authority": "ALPHA_PRODUCTION_WORKFLOW",
            "status": "VERIFIED",
            "active_candidate_ids": list(ALPHA_IDS),
            "population_count": len(ALPHA_IDS),
            "event_count": len(ALPHA_IDS),
            "execution_paper_eligible_ids": [],
            "safety": {
                "live_allowed": False,
                "order_submission_allowed": False,
            },
        },
    )
    assert "UNIQUE_ALPHA_PRODUCTION_WORKFLOW_NOT_VERIFIED" not in verified["blockers"]
    assert verified["verdict"]["unique_alpha_production_workflow_verified"] is True


def test_research_challengers_do_not_pollute_execution_paper_health() -> None:
    realtime = _realtime()
    realtime["alphas"]["research_challenger"] = {
        "status": "PENDING_HISTORICAL_GATE",
        "n_fills": 0,
        "reconciliation_residual": None,
    }
    realtime["backtests"]["research_challenger"] = {
        "provenance": "PENDING_AUTHORITATIVE_DRY_RUN"
    }
    workflow = {
        "schema_version": 1,
        "authority": "ALPHA_PRODUCTION_WORKFLOW",
        "status": "VERIFIED",
        "active_candidate_ids": [*ALPHA_IDS, "research_challenger"],
        "population_count": len(ALPHA_IDS) + 1,
        "event_count": len(ALPHA_IDS) + 1,
        "execution_paper_eligible_ids": [],
        "safety": {"live_allowed": False, "order_submission_allowed": False},
    }

    report = evaluate_paper_health(
        realtime,
        _preview(),
        now_ms=2_000,
        alpha_production_workflow=workflow,
    )

    assert report["verdict"]["paper_ready"] is True
    assert report["alphas"]["research_challenger"][
        "required_for_operational_health"
    ] is False
    assert not any(
        blocker.startswith("research_challenger:") for blocker in report["blockers"]
    )

    workflow["execution_paper_eligible_ids"] = ["research_challenger"]
    eligible = evaluate_paper_health(
        realtime,
        _preview(),
        now_ms=2_000,
        alpha_production_workflow=workflow,
    )
    assert eligible["verdict"]["paper_ready"] is False
    assert eligible["alphas"]["research_challenger"][
        "required_for_operational_health"
    ] is True
    assert any(
        blocker.startswith("research_challenger:") for blocker in eligible["blockers"]
    )


def test_main_distinguishes_safe_not_ready_from_evaluation_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        paper_health,
        "build_report",
        lambda **_kwargs: {
            "verdict": {"paper_ready": False},
            "age_ms": {},
            "blockers": ["FORWARD_EVIDENCE_INSUFFICIENT"],
            "forward_evidence": {"complete_for_all_three": False},
        },
    )
    assert (
        paper_health.main(["--output", str(tmp_path / "health.json")])
        == 2
    )

    def fail(**_kwargs):
        raise ValueError("corrupt input")

    monkeypatch.setattr(paper_health, "build_report", fail)
    assert (
        paper_health.main(["--output", str(tmp_path / "health.json")])
        == 1
    )
