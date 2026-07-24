from __future__ import annotations

from tools.paper_health import ALPHA_IDS, DEFAULT_MAX_AGE_MS, evaluate_paper_health


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
            alpha: {"provenance": "REAL_AUTHORITATIVE_L2_DRY_RUN"}
            for alpha in ALPHA_IDS
        },
    }


def _preview() -> dict:
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
            }
            for alpha in ALPHA_IDS
        },
    }


def _forward_track(*, periods: int = 0) -> dict:
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


def test_paper_health_blocks_missing_error_or_safety_fields():
    realtime = _realtime()
    realtime.pop("errors")
    realtime["safety"].pop("broker_connectivity")
    report = evaluate_paper_health(realtime, _preview(), now_ms=2_000)
    assert report["verdict"]["paper_ready"] is False
    assert "PAPER_REALTIME_REPORTED_ERRORS" in report["blockers"]
    assert "REALTIME_BROKER_CONNECTIVITY_ENABLED" in report["blockers"]
