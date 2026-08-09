from __future__ import annotations

import json
from decimal import Decimal

import pytest

import tools.paper_order_preview as paper_preview
from services.execution.simulated_sor import (
    BookLevel,
    SORPolicy,
    VenueBook,
    route_simulated_intent,
)
from services.risk.research_risk.dry_run_guard import DryRunResearchRiskInputs
from services.risk.research_risk.domain.models import RiskConfig
from tools.paper_order_preview import build_alpha_preview, run_preview


DECISION_TS = 1_700_000_000_000
STATUS = {
    "quality_evidence_current": True,
    "data_quality_admission": {"verdict": "PASS"},
    "alerts": [],
    "ran_at": DECISION_TS + 1_000,
    "staleness_days": {"prices": {"worst_days": 1}},
}


def _pending(weights):
    return {
        "decided_ts": DECISION_TS,
        "recorded_at": DECISION_TS,
        "weights_json": json.dumps(weights),
        "source": "FORWARD_L2_ATTESTED_DECISION",
    }


def _workflow_admission(*authorized):
    authorized_ids = frozenset(authorized)
    return {
        "authority": "ALPHA_PRODUCTION_WORKFLOW",
        "status": "VERIFIED",
        "source_path": "workflow.json",
        "source_hash": "a" * 64,
        "eligible_ids": authorized_ids,
        "execution_ids": frozenset(),
        "authorized_ids": authorized_ids,
    }


def test_build_preview_accepts_explicit_isolated_paper_authority() -> None:
    with pytest.raises(ValueError, match="aum must be"):
        build_alpha_preview(
            "rt-candidate",
            {},
            mark_by_ticker={},
            status={},
            aum=0,
            supported_alpha_ids=("rt-candidate",),
        )

    with pytest.raises(ValueError, match="supported alpha authority"):
        build_alpha_preview(
            "rt-candidate",
            {},
            mark_by_ticker={},
            status={},
            supported_alpha_ids=("rt-candidate", "rt-candidate"),
        )


def test_public_exports_are_resolvable():
    assert all(hasattr(paper_preview, name) for name in paper_preview.__all__)


def test_workflow_admission_is_fail_closed_and_identity_bound(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(
        json.dumps(
            {
                "authority": "ALPHA_PRODUCTION_WORKFLOW",
                "status": "VERIFIED",
                "active_candidate_ids": ["candidate"],
                "execution_paper_eligible_ids": ["candidate"],
                "execution_paper_ids": [],
            }
        ),
        encoding="utf-8",
    )
    admission = paper_preview._workflow_admission(str(path))
    assert admission["authorized_ids"] == frozenset({"candidate"})
    assert len(admission["source_hash"]) == 64
    paper_preview._revalidate_workflow_admission(
        str(path), alpha_id="candidate", expected_hash=admission["source_hash"]
    )

    path.write_text(
        json.dumps(
            {
                "authority": "ALPHA_PRODUCTION_WORKFLOW",
                "status": "VERIFIED",
                "active_candidate_ids": [],
                "execution_paper_eligible_ids": ["candidate"],
                "execution_paper_ids": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="exceeds active population"):
        paper_preview._workflow_admission(str(path))


def test_workflow_admission_revalidation_detects_toctou(tmp_path):
    path = tmp_path / "workflow.json"
    first = {
        "authority": "ALPHA_PRODUCTION_WORKFLOW",
        "status": "VERIFIED",
        "active_candidate_ids": ["candidate"],
        "execution_paper_eligible_ids": ["candidate"],
        "execution_paper_ids": [],
    }
    path.write_text(json.dumps(first), encoding="utf-8")
    admission = paper_preview._workflow_admission(str(path))
    first["execution_paper_eligible_ids"] = []
    path.write_text(json.dumps(first), encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed during paper admission"):
        paper_preview._revalidate_workflow_admission(
            str(path), alpha_id="candidate", expected_hash=admission["source_hash"]
        )


def test_build_alpha_preview_requires_complete_research_risk():
    result = build_alpha_preview(
        "crowd_contra",
        _pending({"BTC": 0.125, "ETH": -0.125}),
        mark_by_ticker={
            "BTC": {DECISION_TS: 50_000.0},
            "ETH": {DECISION_TS: 2_000.0},
        },
        status=STATUS,
        aum=1_000_000.0,
        generated_at_ms=DECISION_TS + 2_000,
    )

    assert result["paper_execution"] == "PAPER_BLOCKED"
    assert result["n_intents"] == result["n_approved"]
    assert result["n_paper_fills"] == 0
    assert "FULL_RESEARCH_RISK_NOT_APPROVED" in result["rejected_reasons"]
    assert result["research_risk"]["provenance"] == "REAL_RESEARCH_RISK_FULL_PENDING"
    assert result["reconciliation_residual"] == 0.0
    assert result["paper_only"] is True
    assert result["live_allowed"] is False
    assert {row["instrument"] for row in result["orders"]} == {"BTC", "ETH"}


def test_build_alpha_preview_uses_aligned_pit_history_for_full_risk():
    periods = 24
    week_ms = 7 * 24 * 60 * 60 * 1_000
    timestamps = [
        DECISION_TS - (periods - index) * week_ms for index in range(periods)
    ]
    inputs = DryRunResearchRiskInputs(
        capacity_cny={"BTC": 1.0e12, "ETH": 1.0e12},
        daily_volume_cny=1.0e12,
        total_aum=1.0e6,
        alpha_returns={
            "crowd_contra": [
                0.001 if index % 2 else -0.0002 for index in range(periods)
            ],
            "peer_alpha": [
                0.0003 if index % 3 else -0.0001 for index in range(periods)
            ],
        },
        regime_labels=["normal"] * periods,
        alpha_metadata=[
            {
                "alpha_id": "crowd_contra",
                "age_days": 1.0,
                "validation_confidence": 0.9,
                "paper_confidence": 0.9,
                "decay_flag": False,
                "overfit_flag": False,
                "leakage_flag": False,
            },
            {
                "alpha_id": "peer_alpha",
                "age_days": 1.0,
                "validation_confidence": 0.9,
                "paper_confidence": 0.9,
                "decay_flag": False,
                "overfit_flag": False,
                "leakage_flag": False,
            },
        ],
        timestamps=timestamps,
    )

    result = build_alpha_preview(
        "crowd_contra",
        _pending({"BTC": 0.125, "ETH": -0.125}),
        mark_by_ticker={
            "BTC": {DECISION_TS: 50_000.0},
            "ETH": {DECISION_TS: 2_000.0},
        },
        status=STATUS,
        aum=1_000_000.0,
        generated_at_ms=DECISION_TS + 2_000,
        research_risk_inputs=inputs,
    )

    risk = result["research_risk"]
    assert risk["series"] == "aligned_pit_history"
    assert risk["provenance"] == "REAL_RESEARCH_RISK_FULL"
    assert risk["gates_run"] == 9
    assert not risk["engines_pending"]
    assert risk["config_provenance"] == RiskConfig(
        periods_per_year=paper_preview.OFFICIAL_PAPER_PERIODS_PER_YEAR
    ).compute_hash()
    assert risk["reports"]["stress"]["provenance"] == "REAL_HISTORICAL_REPLAY"
    assert risk["scope"] == "PRE_FILL_RESEARCH_EVIDENCE"


def test_build_alpha_preview_rejects_non_major_or_missing_price():
    with pytest.raises(RuntimeError, match="major-universe"):
        build_alpha_preview(
            "crowd_contra",
            _pending({"SHIB": 0.125}),
            mark_by_ticker={"SHIB": {DECISION_TS: 0.001}},
            status=STATUS,
        )


def test_build_alpha_preview_does_not_fill_rebalance_without_full_research_risk():
    result = build_alpha_preview(
        "crowd_contra",
        _pending({"ETH": 0.125}),
        mark_by_ticker={
            "BTC": {DECISION_TS: 50_000.0},
            "ETH": {DECISION_TS: 2_000.0},
        },
        status=STATUS,
        current_positions={"BTC": 1},
        aum=1_000_000.0,
        generated_at_ms=DECISION_TS + 2_000,
    )

    orders = {row["instrument"]: row for row in result["orders"]}
    assert orders["BTC"]["side"] == "SELL"
    assert orders["BTC"]["paper_position_before"] == 1
    assert orders["BTC"]["paper_position_after_fill"] == 1
    assert orders["ETH"]["side"] == "BUY"
    assert result["paper_positions"] == {"BTC": 1}
    assert result["n_intents"] == result["n_approved"] == 2
    assert result["n_paper_fills"] == 0
    assert result["paper_execution"] == "PAPER_BLOCKED"
    with pytest.raises(RuntimeError, match="exact decision-time price"):
        build_alpha_preview(
            "crowd_contra",
            _pending({"BTC": 0.125}),
            mark_by_ticker={"BTC": {DECISION_TS + 1: 50_000.0}},
            status=STATUS,
        )


def test_exchange_filters_require_fresh_complete_public_snapshot(tmp_path):
    path = tmp_path / "realtime.json"
    path.write_text(
        json.dumps(
            {
                "verdict": "PASS",
                "complete": True,
                "generated_at_ms": DECISION_TS,
                "latest": {
                    "BTC": {
                        "contract": {
                            "status": "TRADING",
                            "contract_type": "PERPETUAL",
                            "step_size": 0.001,
                            "min_qty": 0.001,
                            "min_notional": 5.0,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert paper_preview._load_exchange_filters(
        str(path), {"BTC"}, now_ms=DECISION_TS + 1_000
    ) == {"BTC": (0.001, 0.001, 5.0)}
    with pytest.raises(RuntimeError, match="stale"):
        paper_preview._load_exchange_filters(
            str(path),
            {"BTC"},
            now_ms=DECISION_TS + paper_preview.MAX_EXCHANGE_FILTER_AGE_MS + 1,
        )


def test_build_preview_applies_exchange_step_size_to_targets():
    result = build_alpha_preview(
        "crowd_contra",
        _pending({"BTC": 0.125}),
        mark_by_ticker={"BTC": {DECISION_TS: 50_000.0}},
        status=STATUS,
        aum=1_000_000.0,
        generated_at_ms=DECISION_TS + 2_000,
        exchange_filters={"BTC": (2.0, 1.0, 50.0)},
    )

    assert result["orders"][0]["quantity"] == 2


def test_engineering_preview_exposes_reconciled_simulation_tca():
    result = build_alpha_preview(
        "crowd_contra",
        _pending({"BTC": 0.125, "ETH": -0.125}),
        mark_by_ticker={
            "BTC": {DECISION_TS: 50_000.0},
            "ETH": {DECISION_TS: 2_000.0},
        },
        status=STATUS,
        aum=1_000_000.0,
        cost_bps=10.0,
        generated_at_ms=DECISION_TS + 2_000,
        require_full_research_risk=False,
    )

    tca = result["execution_tca"]
    assert result["n_paper_fills"] == result["n_approved"] == 2
    assert tca["status"] == "COMPLETE"
    assert tca["simulation_only"] is True
    assert tca["implementation_shortfall_bps_including_cost"] == pytest.approx(10.0)
    assert tca["route_tca_available"] is False
    assert "NO_ORDER_BOOK_ROUTE_MODEL" in tca["limitations"]


def test_engineering_preview_routes_new_fill_on_bound_public_book_snapshot():
    execution_ts = DECISION_TS + 2_000
    books = (
        VenueBook(
            venue_id="binance_usdm",
            observed_at_ms=execution_ts,
            taker_fee_bps=Decimal("5"),
            latency_ms=20,
            bids=(BookLevel(Decimal("49999"), 10),),
            asks=(BookLevel(Decimal("50001"), 10),),
        ),
        VenueBook(
            venue_id="bybit_linear",
            observed_at_ms=execution_ts,
            taker_fee_bps=Decimal("5.5"),
            latency_ms=30,
            bids=(BookLevel(Decimal("49998"), 10),),
            asks=(BookLevel(Decimal("50002"), 10),),
        ),
    )
    policy = SORPolicy(
        max_quote_age_ms=60_000,
        min_fill_fraction=Decimal("1"),
        max_venue_fraction=Decimal("1"),
        latency_penalty_bps_per_ms=Decimal("0"),
    )

    def planner(intent):
        return route_simulated_intent(
            intent,
            books,
            as_of_ms=execution_ts,
            policy=policy,
        )

    result = build_alpha_preview(
        "crowd_contra",
        _pending({"BTC": 0.125}),
        mark_by_ticker={"BTC": {DECISION_TS: 50_000.0}},
        status=STATUS,
        aum=1_000_000.0,
        cost_bps=0.0,
        generated_at_ms=execution_ts,
        execution_prices={"BTC": 50_000.0},
        execution_at_ms=execution_ts,
        route_planner=planner,
        routing_snapshot_id="a" * 64,
        require_full_research_risk=False,
    )

    tca = result["execution_tca"]
    assert result["paper_execution"] == "PAPER_BLOCKED"
    assert "FULL_RESEARCH_RISK_NOT_APPROVED" in result["rejected_reasons"]
    assert result["execution_ts"] == execution_ts
    assert result["n_paper_fills"] == result["n_approved"] == 1
    assert result["n_paper_fill_slices"] == 1
    assert tca["route_tca_available"] is True
    assert tca["fill_model"] == "REAL_PUBLIC_BOOK_SIMULATED_SOR"
    assert tca["routing_snapshot_id"] == "a" * 64
    assert tca["route_tca"][0]["venues"][0]["venue_id"] == "binance_usdm"
    assert result["reconciliation_residual"] == 0.0


def test_run_preview_publishes_fail_closed_report_for_missing_attested_ledger(
    tmp_path, monkeypatch
):
    release_commit = "b" * 40
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", release_commit)
    output = tmp_path / "preview.json"
    report = run_preview(
        alpha_ids=("crowd_contra",),
        forward_db=str(tmp_path / "missing.db"),
        mark_cache=str(tmp_path / "missing-mark.json"),
        status_path=str(tmp_path / "missing-status.json"),
        output_path=str(output),
        paper_store_path=str(tmp_path / "paper.db"),
        research_risk_bundle_path=str(tmp_path / "missing-risk.json"),
        realtime_market_status_path=str(tmp_path / "missing-market.json"),
        workflow_path=str(tmp_path / "missing-workflow.json"),
        generated_at_ms=DECISION_TS,
    )

    assert report["verdict"]["paper_preview_ready"] is False
    assert report["verdict"]["live_allowed"] is False
    assert report["errors"]["crowd_contra"]["paper_execution"] == "PAPER_BLOCKED"
    assert json.loads(output.read_text(encoding="utf-8"))["safety"]["paper_only"] is True
    attestation = paper_preview.release_run_attestation()
    assert attestation is not None and attestation["complete"] is True
    assert attestation["module"] == "paper_order_preview"
    assert attestation["release_commit"] == release_commit
    assert set(attestation["input_hashes"]) == {
        "candidate_universe",
        "exchange_filter_views",
        "forward_decision_views",
        "mark_price_view",
        "paper_preview_contract",
        "paper_store_authority_before",
        "research_risk_views",
        "supporting_artifact_documents",
        "workflow_admission_view",
    }
    assert set(attestation["output_hashes"]) == {
        "paper_order_preview_report_file",
        "paper_store_authority_after",
    }
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }


def test_run_preview_failure_clears_prior_release_attestation(tmp_path):
    paper_preview._LAST_RELEASE_RUN_ATTESTATION = {"complete": True}

    with pytest.raises(ValueError, match="non-empty subset"):
        run_preview(
            alpha_ids=(),
            output_path=str(tmp_path / "preview.json"),
            paper_store_path=str(tmp_path / "paper.db"),
        )

    assert paper_preview.release_run_attestation() is None


def test_run_preview_reports_missing_full_research_risk(monkeypatch, tmp_path):
    pending = _pending({"BTC": 0.125})
    monkeypatch.setattr(paper_preview, "_load_json", lambda _path: STATUS)
    monkeypatch.setattr(
        paper_preview,
        "load_mark_prices",
        lambda _path: {"BTC": {DECISION_TS: 50_000.0}},
    )
    monkeypatch.setattr(
        paper_preview,
        "_latest_attested_pending",
        lambda _path, _alpha_id: (pending, 1),
    )
    monkeypatch.setattr(
        paper_preview,
        "_workflow_admission",
        lambda _path: _workflow_admission("crowd_contra"),
    )
    report = run_preview(
        alpha_ids=("crowd_contra",),
        output_path=str(tmp_path / "first.json"),
        paper_store_path=str(tmp_path / "paper.db"),
        aum=1_000_000.0,
        cost_bps=10.0,
        generated_at_ms=DECISION_TS,
    )
    row = report["alphas"]["crowd_contra"]
    assert report["verdict"]["paper_preview_ready"] is False
    assert row["paper_execution"] == "PAPER_BLOCKED"
    assert report["errors"]["crowd_contra"]["reason"] == (
        "FULL_RESEARCH_RISK_NOT_APPROVED"
    )


def test_run_preview_requires_workflow_authority_before_new_fill(monkeypatch, tmp_path):
    pending = _pending({"BTC": 0.125})
    monkeypatch.setattr(paper_preview, "_load_json", lambda _path: STATUS)
    monkeypatch.setattr(
        paper_preview,
        "load_mark_prices",
        lambda _path: {"BTC": {DECISION_TS: 50_000.0}},
    )
    monkeypatch.setattr(
        paper_preview,
        "_latest_attested_pending",
        lambda _path, _alpha_id: (pending, 1),
    )
    monkeypatch.setattr(
        paper_preview,
        "_workflow_admission",
        lambda _path: _workflow_admission(),
    )
    monkeypatch.setattr(
        paper_preview,
        "build_alpha_preview",
        lambda *_args, **_kwargs: pytest.fail("workflow gate must run before preview"),
    )

    report = run_preview(
        alpha_ids=("crowd_contra",),
        output_path=str(tmp_path / "blocked.json"),
        paper_store_path=str(tmp_path / "paper.db"),
        generated_at_ms=DECISION_TS,
    )

    row = report["alphas"]["crowd_contra"]
    assert row["paper_execution"] == "PAPER_BLOCKED"
    assert row["workflow_admission"]["candidate_authorized"] is False
    assert report["errors"]["crowd_contra"]["reason"] == (
        "ALPHA_PRODUCTION_WORKFLOW_NOT_EXECUTION_PAPER_ELIGIBLE"
    )
    assert report["verdict"]["workflow_admission_valid"] is True
    assert report["verdict"]["workflow_authorized_ids"] == []


def test_main_returns_safe_blocked_code_for_completed_not_ready_evaluation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        paper_preview,
        "run_preview",
        lambda **_kwargs: {
            "verdict": {"paper_preview_ready": False},
            "safety": {
                "paper_only": True,
                "broker_order_submission": False,
            },
        },
    )

    assert (
        paper_preview.main(["--output", str(tmp_path / "preview.json")])
        == 2
    )
