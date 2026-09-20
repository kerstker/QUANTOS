import pytest

import tools.alpha_paper_cycle as alpha_paper_cycle
from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from tools.alpha_paper_cycle import build_report
from tools.forward_track_advance import _ALPHAS, TOP_CONTRA


@pytest.fixture(autouse=True)
def _authority_contract_double(monkeypatch):
    monkeypatch.setattr(
        alpha_paper_cycle,
        "is_authoritative_backtest_record",
        lambda record: bool(
            isinstance(record, dict)
            and isinstance(record.get("detail"), dict)
            and record["detail"].get("_test_manifest_valid") is True
        ),
    )


def _full_risk() -> dict:
    engines = {
        name: True
        for name in (
            "market", "liquidity", "concentration", "correlation", "drawdown",
            "regime", "stress", "capacity", "model_alpha",
        )
    }
    return {
        "decision": "RISK_APPROVED",
        "status": "APPROVED",
        "gates_passed": 9,
        "gates_run": 9,
        "engines_run": engines,
        "engines_skipped": {},
        "engines_pending": {},
        "provenance": "REAL_RESEARCH_RISK_FULL",
        # 2026-09-20 审计 P0-4-D:full_research_risk_approved 现在要求保留的 reports
        # 里带有 stress 引擎**自己**的真实历史回放 provenance。仅凭 REAL_RESEARCH_RISK_FULL
        # 这个字符串不再足以成为九引擎全量结论——手写 fixture 必须给出该证据。
        "reports": {"stress": {"provenance": "REAL_HISTORICAL_REPLAY"}},
    }


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


def test_cli_quarantines_legacy_profitability_before_read_or_write(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        pytest.fail("legacy artifact access must remain behind authority quarantine")

    monkeypatch.setattr(alpha_paper_cycle, "build_report", fail_if_called)
    monkeypatch.setattr(alpha_paper_cycle, "atomic_write_json", fail_if_called)

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        alpha_paper_cycle.main([])


def test_report_keeps_three_candidates_separate_and_fail_closed():
    report = build_report()
    assert set(report["alphas"]) == {"carry_oi_majors", "crowd_contra", "top_contra"}


def test_forward_driver_registers_the_same_three_candidates():
    assert {"carry_oi_majors", "crowd_contra", "top_contra"} <= set(_ALPHAS)
    assert _ALPHAS["top_contra"] == TOP_CONTRA


def test_forward_driver_registers_preregistered_variant_candidates():
    # 2026-07-24 pre-registered crowd_contra variant family
    # (evidence/crowd_contra_variants.json): exactly the 7 CANDIDATEs are registered
    # for forward paper evidence; the 9 REJECTED variants must NOT appear.
    from tools.crowd_contra_variants import build_variant_specs
    from tools.forward_track_advance import CROWD_TAKER_COMBO, _alpha_spec

    specs = {s["id"]: s for s in build_variant_specs()}
    expected_candidates = {"crowd_contra_q20", "crowd_taker_combo", "crowd_contra_14d",
                           "crowd_contra_q20_14d", "crowd_contra_3d", "crowd_contra_q25_3d",
                           "crowd_contra_q35_3d"}
    assert expected_candidates <= set(_ALPHAS)
    rejected = set(specs) - expected_candidates - {"crowd_contra"}
    assert not (rejected & set(_ALPHAS))
    for vid in expected_candidates:
        registered = _alpha_spec(_ALPHAS[vid])
        pre = specs[vid]
        assert registered["quantile"] == pre["quantile"], vid
        assert registered["cadence_days"] == pre["cadence_days"], vid
        if vid == "crowd_taker_combo":
            assert registered["expr"] == pre["expr"] == CROWD_TAKER_COMBO
        else:
            assert registered["expr"] == pre["expr"]


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
        "detail": {"_test_manifest_valid": True},
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
    assert top["canonical_dry_run"]["authoritative"] is not True
    assert "DRY_RUN_RESEARCH_RISK_NOT_FULL" in top["blockers"]


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
            "detail": {"_test_manifest_valid": True},
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
        assert row["canonical_dry_run"]["authoritative"] is not True
        assert "DRY_RUN_RESEARCH_RISK_NOT_FULL" in row["blockers"]


def test_risk_approved_canonical_dry_run_is_not_false_blocked(tmp_path):
    import json

    artifacts = _artifacts()
    artifacts["carry_dry_run_authoritative"] = {
        "authoritative": True,
        "evidence_class": "CANONICAL_L2_DRY_RUN",
        "run_id": "carry-approved-1",
        "strategy_id": "carry",
        "n_periods": 10,
        "pnl": {"sharpe": 0.4, "residual": 1e-9},
        "research_risk": _full_risk(),
        "detail": {
            "_test_manifest_valid": True,
            "research_risk": _full_risk(),
        },
    }
    paths = {}
    for name, payload in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    report = build_report(artifact_paths=paths)
    assert report["alphas"]["carry_oi_majors"]["canonical_dry_run"]["research_risk_decision"] == "RISK_APPROVED"
    assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" not in report["alphas"]["carry_oi_majors"]["blockers"]


def test_legacy_authoritative_flag_and_subset_approval_do_not_admit_backtest(tmp_path):
    import json

    artifacts = _artifacts()
    artifacts["carry_dry_run_authoritative"] = {
        "authoritative": True,
        "evidence_class": "CANONICAL_L2_DRY_RUN",
        "run_id": "legacy-carry",
        "pnl": {"sharpe": 99.0, "residual": 0.0},
        # This is the historical four-engine result: the decision label alone
        # is not an admission contract and there is no RunManifestV2.
        "research_risk": {
            "decision": "RISK_APPROVED",
            "status": "APPROVED",
            "gates_run": 4,
            "gates_passed": 4,
            "provenance": "REAL_RESEARCH_RISK_SUBSET",
        },
    }
    paths = {}
    for name, payload in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    row = build_report(artifact_paths=paths)["alphas"]["carry_oi_majors"]
    assert row["canonical_dry_run"]["authoritative"] is not True
    assert "DRY_RUN_AUTHORITY_INVALID" in row["blockers"]


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


def test_report_reads_attested_forward_status_fields(tmp_path):
    import json

    artifacts = _artifacts()
    artifacts["forward"] = {
        "refresh_evidence_available": True,
        "alphas": {
            alpha_id: {
                "n_forward_periods": 0,
                "attested_pending_decisions": 0,
                "legacy_unattested_decisions": 2 if alpha_id != "top_contra" else 1,
                "evidence_state": "ADMISSION_READY_PENDING_DECISION",
            }
            for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra")
        },
    }
    paths = {}
    for name, payload in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    report = build_report(artifact_paths=paths)
    forward = report["alphas"]["crowd_contra"]["forward"]
    assert forward["pending_decisions"] == 0
    assert forward["attested_pending_decisions"] == 0
    assert forward["legacy_unattested_decisions"] == 2
    assert forward["evidence_state"] == "ADMISSION_READY_PENDING_DECISION"
