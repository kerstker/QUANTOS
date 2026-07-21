from tools.alpha_paper_cycle import build_report
from tools.forward_track_advance import _ALPHAS, TOP_CONTRA


def _artifacts():
    return {
        "carry_strategy": {"sharpe_net_5bps": 2.0},
        "carry_walkforward": {"verdict": "DESIGN_ROBUST", "n_blocks": 5},
        "oi_research": {
            "verdict": "candidate needs adversarial verification",
            "factors": [{
                "name": "top_contra",
                "price_sharpe": 0.727,
                "econ_sharpe": 0.879,
                "ic": 0.0216,
                "incremental_ir_vs_carry": 0.606,
            }],
        },
        "oi_verify": {
            "verdict": "SURVIVES 5/5",
            "refutation_checks": {"a": True, "b": True, "c": True, "d": True},
            "net_sharpe_by_fee_bps": {"10": 0.68},
            "price_sharpe_by_lag": {"1": 1.3},
            "embargoed_oos": {"mean_oos": 1.1},
        },
        "top_verify": {},
        "carry_dry_run_authoritative": {},
        "crowd_dry_run_authoritative": {},
        "top_dry_run_authoritative": {},
        "dry_run": {"books": {
            "carry": {"sharpe": 0.4},
            "crowd_contra": {"sharpe": 1.0},
        }},
        "capacity": {"numbers": {
            "carry.participation_cap_aum": {"value": 1_000_000},
            "crowd_contra.participation_cap_aum": {"value": 2_000_000},
        }},
        "forward": {"alphas": {
            "carry_oi_majors": {"n_forward_periods": 0},
            "crowd_contra": {"n_forward_periods": 0},
        }},
        "admission": {"verdict": "BLOCK"},
        "event_health": {"verdict": "BREACH"},
        "event_consistency": {"verdict": "BREACH"},
        "gonogo": {"forward_evidence": {"present": False}},
    }


def test_report_keeps_three_candidates_separate_and_fail_closed():
    report = build_report()
    assert set(report["alphas"]) == {"carry_oi_majors", "crowd_contra", "top_contra"}


def test_forward_driver_registers_the_same_three_candidates():
    assert set(_ALPHAS) == {"carry_oi_majors", "crowd_contra", "top_contra"}
    assert _ALPHAS["top_contra"] == TOP_CONTRA


def test_report_marks_top_contra_unconfirmed(tmp_path):
    import json

    artifacts = _artifacts()
    paths = {}
    for name, payload in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    report = build_report(artifact_paths=paths)
    assert report["alphas"]["top_contra"]["research"]["verdict"] == "UNCONFIRMED"
    assert "DRY_RUN_EVIDENCE_MISSING" in report["alphas"]["top_contra"]["blockers"]
    assert report["cycle"]["live_allowed"] is False


def test_report_surfaces_authoritative_top_contra_dry_run(tmp_path):
    import json

    artifacts = _artifacts()
    artifacts["top_dry_run_authoritative"] = {
        "authoritative": True,
        "evidence_class": "CANONICAL_L2_DRY_RUN",
        "run_id": "top-run-1",
        "n_periods": 192,
        "pnl": {
            "sharpe": 1.5246,
            "sharpe_vt": 1.3013,
            "max_drawdown": 0.2015,
            "total_return": 1.650056,
            "residual": 1e-9,
        },
        "research_risk": {"decision": "RISK_REJECTED"},
        "gate": {"n_block": 11},
    }
    paths = {}
    for name, payload in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    report = build_report(artifact_paths=paths)
    top = report["alphas"]["top_contra"]
    assert top["dry_run"]["authoritative"] is True
    assert top["dry_run"]["sharpe"] == 1.5246
    assert top["canonical_dry_run"]["n_periods"] == 192
    assert top["canonical_dry_run"]["research_risk_decision"] == "RISK_REJECTED"
    assert top["canonical_dry_run"]["crowding_gate_blocks"] == 11
    assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" in top["blockers"]


def test_report_surfaces_authoritative_carry_and_crowd_dry_runs(tmp_path):
    import json

    artifacts = _artifacts()
    for name, alpha_id in (
        ("carry_dry_run_authoritative", "carry_oi_majors"),
        ("crowd_dry_run_authoritative", "crowd_contra"),
    ):
        artifacts[name] = {
            "authoritative": True,
            "evidence_class": "CANONICAL_L2_DRY_RUN",
            "run_id": f"{alpha_id}-run-1",
            "n_periods": 200,
            "pnl": {
                "sharpe": 0.7,
                "sharpe_vt": 0.9,
                "max_drawdown": 0.2,
                "total_return": 1.1,
                "residual": 1e-9,
            },
            "research_risk": {"decision": "RISK_REJECTED"},
            "gate": {"n_block": 2},
        }
    paths = {}
    for name, payload in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    report = build_report(artifact_paths=paths)
    for alpha_id in ("carry_oi_majors", "crowd_contra"):
        row = report["alphas"][alpha_id]
        assert row["canonical_dry_run"]["authoritative"] is True
        assert row["canonical_dry_run"]["run_id"] == f"{alpha_id}-run-1"
        assert row["canonical_dry_run"]["reconciliation_residual"] == 1e-9
        assert row["canonical_dry_run"]["research_risk_decision"] == "RISK_REJECTED"
        assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" in row["blockers"]


def test_report_describes_daily_forward_polling(tmp_path):
    import json

    artifacts = _artifacts()
    paths = {}
    for name, payload in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    report = build_report(artifact_paths=paths)
    assert any("daily poll" in action for action in report["next_actions"])
    semantics = report["cycle"]["forward_return_semantics"]
    assert semantics["includes_trading_costs"] is False
    assert semantics["net_cost_evidence"] == "data/cache/dry_run.db"
