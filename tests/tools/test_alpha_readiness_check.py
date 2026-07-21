from tools.alpha_readiness_check import ALPHA_CATALOG, evaluate_alpha_readiness


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
    )


def test_research_can_pass_but_l2_still_blocks_small_capital_live():
    report = evaluate_alpha_readiness("crowd_contra", **_artifacts(forward_periods=8, admission="PASS", event_verdict="PASS"))
    assert report["research"]["verdict"] == "PASS_WITH_WARNINGS"
    assert report["deployment"]["research_packet_ready_for_paper"] is True
    assert report["deployment"]["small_capital_live_verdict"] == "BLOCKED_L2_DRY_RUN_ONLY"
    assert report["deployment"]["live_allowed"] is False
    assert report["deployment"]["order_submission_allowed"] is False


def test_missing_forward_and_event_evidence_blocks_fail_closed():
    report = evaluate_alpha_readiness("crowd_contra", **_artifacts())
    assert report["research"]["verdict"] == "PASS_WITH_WARNINGS"
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


def test_carry_uses_paper_cycle_research_evidence_and_its_book_ids():
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
            }
        }
    }
    report = evaluate_alpha_readiness("carry_oi_majors", **artifacts)
    assert report["research"]["verdict"] == "PASS_WITH_WARNINGS"
    assert "ALPHA_NOT_REGISTERED" not in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is True
    assert report["deployment"]["small_capital_live_verdict"] == "BLOCKED_L2_DRY_RUN_ONLY"


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
                "canonical_dry_run": {"research_risk_decision": "RISK_REJECTED"},
            }
        }
    }
    report = evaluate_alpha_readiness("top_contra", **artifacts)
    assert report["research"]["verdict"] == "PASS_WITH_WARNINGS"
    assert "PAPER_CYCLE_BLOCKED" in report["deployment"]["blockers"]
    assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" in report["deployment"]["blockers"]
    assert report["deployment"]["research_packet_ready_for_paper"] is False
