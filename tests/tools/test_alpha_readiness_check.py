import pytest
from tests.risk.full_risk_fixture import complete_engine_reports

import tools.alpha_readiness_check as alpha_readiness_check
from tools.alpha_readiness_check import ALPHA_CATALOG, evaluate_alpha_readiness


@pytest.fixture(autouse=True)
def _authority_contract_double(monkeypatch):
    monkeypatch.setattr(
        alpha_readiness_check,
        "is_authoritative_backtest_record",
        lambda record: bool(
            isinstance(record, dict)
            and isinstance(record.get("detail"), dict)
            and record["detail"].get("_test_manifest_valid") is True
        ),
    )


def _full_risk(*, approved: bool = True) -> dict:
    engines = {
        name: True
        for name in (
            "market", "liquidity", "concentration", "correlation", "drawdown",
            "regime", "stress", "capacity", "model_alpha",
        )
    }
    if not approved:
        engines["capacity"] = False
    return {
        "decision": "RISK_APPROVED" if approved else "RISK_REJECTED",
        "status": "APPROVED" if approved else "REJECTED",
        "gates_passed": 9 if approved else 8,
        "gates_run": 9,
        "engines_run": engines,
        "engines_skipped": {},
        "engines_pending": {},
        "provenance": "REAL_RESEARCH_RISK_FULL",
        # Compute all nine reports over explicit synthetic history.
        "reports": complete_engine_reports(),
    }


def _canonical(*, approved: bool = True) -> dict:
    risk = _full_risk(approved=approved)
    return {
        "authoritative": True,
        "run_id": "manifest-bound-run",
        "reconciliation_residual": 1e-9,
        "research_risk_decision": risk["decision"],
        "research_risk": risk,
        "detail": {
            "_test_manifest_valid": True,
            "research_risk": risk,
        },
    }


def _artifacts(*, forward_periods=0, admission="BLOCK", event_verdict="BREACH"):
    return dict(
        refutation={
            "verdict": "SURVIVES 5/5",
            "refutation_checks": {"a": True, "b": True, "c": True, "d": True, "e": True},
            "net_sharpe_by_fee_bps": {10: 0.68},
            "price_sharpe_by_lag": {1: 1.31},
            "embargoed_oos": {"n_folds": 10, "mean_oos": 1.12},
        },
        dry_run={
            "books": {"crowd_contra": {"sharpe": 1.0}},
            "residual_max": 1e-9,
            "tolerances": {"residual": 1e-6},
        },
        capacity={"numbers": {"crowd_contra.participation_cap_aum": {"value": 7_000_000}}},
        forward={
            "quality_evidence_current": forward_periods >= 8,
            "alphas": {"crowd_contra": {"n_forward_periods": forward_periods, "paper_sharpe": 1.0}},
        },
        data_admission={"verdict": admission},
        event_health={"verdict": event_verdict, "pending_events": 0, "thresholds": {"max_pending_events": 1000}},
        gonogo={"forward_evidence": {"present": forward_periods >= 8}},
        ic_panel={"rows": [{"name": "crowd_contra", "t_stat": 0.8}]},
        safety={
            "live_trading_enabled": False,
            "real_broker_enabled": False,
            "allow_order_submission": False,
            "allow_ai_direct_trading": False,
            "require_human_approval": True,
        },
        paper_cycle={
            "alphas": {
                "crowd_contra": {
                    "research": {"verdict": "PASS_WITH_WARNINGS"},
                    "paper_verdict": "PENDING_REVIEW",
                    "blockers": [],
                    "canonical_dry_run": _canonical(),
                }
            }
        },
    )


def test_research_can_pass_but_l2_still_blocks_small_capital_live():
    report = evaluate_alpha_readiness("crowd_contra", **_artifacts(forward_periods=8, admission="PASS", event_verdict="PASS"))
    assert report["research"]["verdict"] == "PASS"
    assert report["deployment"]["research_packet_ready_for_paper"] is False
    assert "FORWARD_OBSERVATION_DIAGNOSTIC_ONLY" in report["deployment"]["blockers"]
    assert report["deployment"]["small_capital_live_verdict"] == "BLOCKED_L2_DRY_RUN_ONLY"
    assert report["deployment"]["live_allowed"] is False
    assert report["deployment"]["order_submission_allowed"] is False


def test_missing_forward_and_event_evidence_blocks_fail_closed():
    report = evaluate_alpha_readiness("crowd_contra", **_artifacts())
    assert report["research"]["verdict"] == "PASS"
    assert report["deployment"]["research_packet_ready_for_paper"] is False
    assert "DATA_ADMISSION_BLOCK" in report["deployment"]["blockers"]
    assert "FORWARD_EVIDENCE_INSUFFICIENT" in report["deployment"]["blockers"]
    assert "EVENT_BUS_NOT_HEALTHY" in report["deployment"]["blockers"]
    assert report["deployment"]["live_allowed"] is False


def test_unknown_alpha_and_unsafe_config_cannot_pass():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["safety"] = {
        "live_trading_enabled": True,
        "real_broker_enabled": True,
        "allow_order_submission": True,
        "allow_ai_direct_trading": False,
        "require_human_approval": True,
    }
    report = evaluate_alpha_readiness("not_registered", **artifacts)
    assert report["research"]["verdict"] == "BLOCKED"
    assert "ALPHA_NOT_REGISTERED" in report["deployment"]["blockers"]
    assert "SAFETY_CONFIG_NOT_LOCKED" in report["deployment"]["blockers"]
    assert report["deployment"]["live_allowed"] is False


def test_event_authority_projection_mismatch_is_a_separate_blocker():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["event_consistency"] = {
        "verdict": "BREACH",
        "reasons": ["PROJECTION_AHEAD_OF_AUTHORITY"],
    }
    report = evaluate_alpha_readiness("crowd_contra", **artifacts)
    assert "EVENT_AUTHORITY_PROJECTION_INCONSISTENT" in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is False


def test_current_forward_schema_uses_refresh_evidence_and_admission():
    artifacts = _artifacts(forward_periods=0, admission="PASS", event_verdict="PASS")
    artifacts["forward"] = {
        "refresh_evidence_available": True,
        "alphas": {
            "crowd_contra": {"n_forward_periods": 0, "paper_sharpe": 0.0}
        },
    }
    report = evaluate_alpha_readiness("crowd_contra", **artifacts)
    assert report["forward_gate"]["quality_evidence_current"] is True
    assert "FORWARD_QUALITY_EVIDENCE_UNAVAILABLE" not in report["deployment"]["blockers"]
    assert "FORWARD_EVIDENCE_INSUFFICIENT" in report["deployment"]["blockers"]
    assert "Continue the official forward driver" in report["deployment"]["next_action"]


def test_catalog_contains_all_three_alpha_candidates():
    assert {"crowd_contra", "carry_oi_majors", "top_contra"} <= set(ALPHA_CATALOG)


def test_carry_uses_only_independently_admitted_canonical_research():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["dry_run"] = {
        "books": {"carry": {"sharpe": 1.0}},
        "residual_max": 1e-9,
        "tolerances": {"residual": 1e-6},
    }
    artifacts["capacity"] = {
        "numbers": {"carry.participation_cap_aum": {"value": 7_000_000}}
    }
    artifacts["forward"] = {
        "quality_evidence_current": True,
        "alphas": {"carry_oi_majors": {"n_forward_periods": 8, "paper_sharpe": 1.0}},
    }
    artifacts["gonogo"] = {"forward_evidence": {"present": True}}
    artifacts["paper_cycle"] = {
        "alphas": {
            "carry_oi_majors": {
                "research": {
                    "verdict": "PASS_WITH_WARNINGS",
                    "warnings": ["forward evidence pending"],
                },
                "paper_verdict": "PENDING_REVIEW",
                "blockers": [],
                "canonical_dry_run": _canonical(),
            }
        }
    }
    report = evaluate_alpha_readiness("carry_oi_majors", **artifacts)
    assert report["research"]["verdict"] == "PASS"
    assert "ALPHA_NOT_REGISTERED" not in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is False
    assert "forward evidence pending" not in report["research"]["warnings"]
    assert report["deployment"]["small_capital_live_verdict"] == "BLOCKED_L2_DRY_RUN_ONLY"


def test_paper_cycle_accepts_canonical_risk_approved_decision():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["paper_cycle"] = {
        "alphas": {
            "crowd_contra": {
                "research": {"verdict": "PASS_WITH_WARNINGS"},
                "paper_verdict": "PENDING_REVIEW",
                "blockers": [],
                "canonical_dry_run": _canonical(),
            }
        }
    }
    report = evaluate_alpha_readiness("crowd_contra", **artifacts)
    assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" not in report["deployment"]["blockers"]
    assert report["research"]["verdict"] == "PASS"
    assert report["deployment"]["research_packet_ready_for_paper"] is False


def test_readiness_rejects_scalar_risk_approval_without_manifest_v2():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["paper_cycle"] = {
        "alphas": {
            "crowd_contra": {
                "research": {"verdict": "PASS_WITH_WARNINGS"},
                "paper_verdict": "PENDING_REVIEW",
                "blockers": [],
                "canonical_dry_run": {
                    "authoritative": True,
                    "research_risk_decision": "RISK_APPROVED",
                },
            }
        }
    }
    report = evaluate_alpha_readiness("crowd_contra", **artifacts)
    assert "DRY_RUN_AUTHORITY_INVALID" in report["deployment"]["blockers"]
    assert "INVALIDATED_PERP_LEDGER_V1" in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is False


def test_non_crowd_alpha_without_paper_cycle_evidence_fails_closed():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["forward"] = {
        "quality_evidence_current": True,
        "alphas": {"carry_oi_majors": {"n_forward_periods": 8, "paper_sharpe": 1.0}},
    }
    artifacts["gonogo"] = {"forward_evidence": {"present": True}}
    report = evaluate_alpha_readiness("carry_oi_majors", **artifacts)
    assert report["research"]["checks"][
        "independently_admitted_authoritative_backtest_record"
    ] is False
    assert "INDEPENDENT_AUTHORITATIVE_BACKTEST_RECORD_MISSING" in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is False


def test_top_canonical_risk_rejection_remains_a_readiness_blocker():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["dry_run"] = {
        "books": {"top_contra": {"sharpe": 1.0}},
        "residual_max": 1e-9,
        "tolerances": {"residual": 1e-6},
    }
    artifacts["capacity"] = {
        "numbers": {"top_contra.participation_cap_aum": {"value": 7_000_000}}
    }
    artifacts["forward"] = {
        "quality_evidence_current": True,
        "alphas": {"top_contra": {"n_forward_periods": 8, "paper_sharpe": 1.0}},
    }
    artifacts["gonogo"] = {"forward_evidence": {"present": True}}
    artifacts["paper_cycle"] = {
        "alphas": {
            "top_contra": {
                "research": {
                    "verdict": "PASS_WITH_WARNINGS",
                    "warnings": ["capacity remains a diagnostic"],
                },
                "paper_verdict": "BLOCKED",
                "blockers": ["DRY_RUN_RESEARCH_RISK_NOT_PASS"],
                "canonical_dry_run": _canonical(approved=False),
            }
        }
    }
    report = evaluate_alpha_readiness("top_contra", **artifacts)
    assert report["research"]["verdict"] == "BLOCKED"
    assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is False


def test_crowd_canonical_risk_rejection_remains_a_readiness_blocker():
    artifacts = _artifacts(forward_periods=8, admission="PASS", event_verdict="PASS")
    artifacts["paper_cycle"] = {
        "alphas": {
            "crowd_contra": {
                "research": {"verdict": "PASS_WITH_WARNINGS"},
                "paper_verdict": "BLOCKED",
                "blockers": [],
                "canonical_dry_run": _canonical(approved=False),
            }
        }
    }
    report = evaluate_alpha_readiness("crowd_contra", **artifacts)
    assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is False


def test_all_green_legacy_numbers_cannot_be_repackaged_as_readiness():
    artifacts = _artifacts(
        forward_periods=8,
        admission="PASS",
        event_verdict="PASS",
    )
    # Preserve every old green profitability/capacity claim while removing the
    # independent-authority test marker from the purported canonical record.
    artifacts["paper_cycle"]["alphas"]["crowd_contra"]["canonical_dry_run"][
        "detail"
    ].pop("_test_manifest_valid")

    report = evaluate_alpha_readiness("crowd_contra", **artifacts)

    assert report["decision_class"] == "DIAGNOSTIC_ONLY"
    assert report["evidence_status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert report["research"]["verdict"] == "BLOCKED"
    assert report["research"]["authoritative_backtest_record_admitted"] is False
    assert "INVALIDATED_PERP_LEDGER_V1" in report["deployment"]["blockers"]
    assert "INDEPENDENT_AUTHORITATIVE_BACKTEST_RECORD_MISSING" in report[
        "deployment"
    ]["blockers"]
    assert report["research"]["refutation_pass_count"] is None
    assert report["research"]["net_sharpe_10bps"] is None
    assert report["research"]["one_day_lag_sharpe"] is None
    assert report["research"]["embargoed_oos_mean_sharpe"] is None
    assert report["research"]["capacity_participation_cap_aum"] is None
    assert report["forward_gate"]["paper_sharpe"] is None
    assert report["invalidated_history"]["load_bearing"] is False
