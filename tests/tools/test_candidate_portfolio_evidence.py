"""Driver tests for tools/candidate_portfolio_evidence.py (WP4 W4-1).

All stores live in tmp_path.  Historical rows are seeded directly into the
legacy SQLite schema because the production writer now correctly rejects every
unsigned aggregate; the consumer authority verifier is the injected test
double.  The population and factor builder are injected fakes (no network, no
real market data). Pins:
MISSING_RUN rows for runless challengers, idempotent re-publish, safety flags,
artifact schema, missing inventory, and fail-closed behavior on invalid inputs.
"""
from __future__ import annotations

import json

import pytest
from tests.risk.full_risk_fixture import complete_engine_reports

from services.execution.dry_run_store import DryRunRun, DryRunStore
from services.research import factor_decomp as fd
from services.research import portfolio_evidence as pe
import tools.candidate_portfolio_evidence as candidate_portfolio_evidence
from tools.candidate_portfolio_evidence import (
    FLOW,
    POLICY_VERSION,
    build_portfolio_evidence,
)

DAY = 86_400_000
BASE_TS = 1_578_268_800_000  # 2020-01-06 (Monday) → 7d spacing = distinct ISO weeks
N = 20
LEGACY = ("carry_oi_majors", "crowd_contra", "top_contra")
CHALLENGER = "crowd_contra_3d"
POPULATION = LEGACY + (CHALLENGER,)
_DB_SID = {"carry_oi_majors": "carry", "crowd_contra": "crowd_contra", "top_contra": "top_contra"}


@pytest.fixture(autouse=True)
def _backtest_authority_contract_double(monkeypatch):
    monkeypatch.setattr(
        candidate_portfolio_evidence,
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
        # Compute all nine reports over explicit synthetic history.
        "reports": complete_engine_reports(),
    }


def _curve(seed: int) -> list[dict]:
    return [
        {
            "t": BASE_TS + i * 7 * DAY,
            "equity": 1_000_000.0 * (1.0 + 0.001 * i),
            "ret": 0.005 * (((i * (seed + 1) + seed) % 7) - 3),
        }
        for i in range(N)
    ]


def _fake_run(
    candidate: str, seed: int, *, crowding: bool, authoritative: bool = True
) -> DryRunRun:
    curve = _curve(seed)
    detail = {
        "mode": "multi_period",
        "n_periods": N,
        "equity_curve": curve,
        "equity_curve_vt": curve,
        "research_risk": _full_risk(),
    }
    if authoritative:
        detail["_test_manifest_valid"] = True
    if crowding:
        detail["crowding_gate"] = {"n_periods": N, "n_pass": 0, "n_warn": N - 2, "n_block": 2}
    return DryRunRun(
        run_id=f"run-{candidate}",
        strategy_id=_DB_SID.get(candidate, candidate),
        timestamp=BASE_TS + N * 7 * DAY,
        n_intents=N,
        n_approved=N,
        n_rejected=0,
        rejected_reasons=[],
        pnl={"sharpe": 1.0, "residual": 0.0},
        audit_root="audit",
        detail=detail,
    )


def _write_store(path, candidates=LEGACY, *, authoritative: bool = True) -> None:
    store = DryRunStore(str(path), create=True)
    try:
        for seed, candidate in enumerate(candidates):
            run = _fake_run(
                candidate,
                seed,
                crowding=candidate != "carry_oi_majors",
                authoritative=authoritative,
            )
            # Simulate an on-disk row written by the pre-authority V1 store.
            # Calling save_run() here would test (and require weakening) the
            # production admission boundary rather than the legacy reader.
            store._conn.execute(  # noqa: SLF001 - explicit legacy fixture
                "INSERT OR IGNORE INTO dry_run(run_id, strategy_id, timestamp, n_intents, "
                "n_approved, n_rejected, rejected_reasons, pnl, audit_root, detail) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.strategy_id,
                    run.timestamp,
                    run.n_intents,
                    run.n_approved,
                    run.n_rejected,
                    json.dumps(run.rejected_reasons),
                    json.dumps(run.pnl),
                    run.audit_root,
                    json.dumps(run.detail),
                ),
            )
        store._conn.commit()  # noqa: SLF001 - explicit legacy fixture
    finally:
        store.close()


def _weeks() -> list[str]:
    return sorted({fd.iso_week(BASE_TS + i * 7 * DAY) for i in range(N)})


def _factor_maps() -> dict:
    weeks = _weeks()
    return {
        f"f{j}": {w: 0.004 * (((k * (j + 2) + j) % 5) - 2) for k, w in enumerate(weeks)}
        for j in range(10)
    }


def _write_json(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _capacity_doc() -> dict:
    return {
        "provenance": "UNCALIBRATED_PRINCIPLED",
        "books": {
            key: {"aum_low": 1.0e6, "aum_high": 2.0e7, "binding": "participation"}
            for key in ("carry", "crowd_contra", "top_contra")
        },
    }


def _sor_doc() -> dict:
    return {
        "status": "PASS",
        "simulation_only": True,
        "route_tca_count": 4,
        "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
        "run_id": "no-such-run-in-this-store",
        "snapshot_id": "snap",
    }


def _kwargs(tmp_path, **overrides):
    authoritative_records = overrides.pop("_authoritative_records", True)
    _write_store(tmp_path / "dry_run.db", authoritative=authoritative_records)
    _write_json(tmp_path / "capacity.json", _capacity_doc())
    _write_json(tmp_path / "sor.json", _sor_doc())
    kwargs = {
        "db_path": str(tmp_path / "dry_run.db"),
        "output_path": str(tmp_path / "out.json"),
        "capacity_path": str(tmp_path / "capacity.json"),
        "sor_status_path": str(tmp_path / "sor.json"),
        "now_ms": 1_700_000_000_000,
        "population_fn": lambda: POPULATION,
        "factor_builder": _factor_maps,
    }
    kwargs.update(overrides)
    return kwargs


def test_runless_challenger_gets_honest_missing_run_row(tmp_path):
    payload = build_portfolio_evidence(**_kwargs(tmp_path))
    row = payload["alphas"][CHALLENGER]
    assert row["status"] == "MISSING_RUN"
    assert row["missing"] == sorted(pe.SUB_EVIDENCE_NAMES)
    assert payload["missing_inventory"][CHALLENGER] == row["missing"]
    # The challenger is absent from the incumbent book (no canonical run).
    assert CHALLENGER not in payload["incumbent_book"]["members_with_runs"]


def test_legacy_rows_carry_real_sub_evidence(tmp_path):
    payload = build_portfolio_evidence(**_kwargs(tmp_path))
    crowd = payload["alphas"]["crowd_contra"]
    assert crowd["dry_run"]["db_strategy_id"] == "crowd_contra"
    assert crowd["dry_run"]["n_periods"] == N
    assert crowd["marginal_contribution"]["n_common_periods"] == N
    assert crowd["common_exposure"]["n_common_periods"] == N
    assert crowd["capacity"] == {
        "available": True,
        "low": 1.0e6,
        "high": 2.0e7,
        "provenance": "UNCALIBRATED_PRINCIPLED",
        "binding": "participation",
        "reason": None,
    }
    assert crowd["crowding"]["n_blocked"] == 2
    assert crowd["crowding"]["blocked_share"] == pytest.approx(2 / N)
    assert crowd["crowding"]["max_z"] is None  # per-period z never persisted
    assert crowd["tca"]["available"] is True
    assert crowd["tca"]["weighted_shortfall_bps"] is None  # run not in store
    assert "crowding" not in crowd["missing"]
    # carry ran without a crowding gate → its crowding block is honestly MISSING.
    carry = payload["alphas"]["carry_oi_majors"]
    assert carry["dry_run"]["db_strategy_id"] == "carry"  # legacy id mapping
    assert carry["crowding"]["available"] is False
    assert "crowding" in carry["missing"]
    assert carry["status"] == "INCOMPLETE"


def test_artifact_schema_safety_flags_and_idempotent_republish(tmp_path):
    kwargs = _kwargs(tmp_path)
    first = build_portfolio_evidence(**kwargs)
    second = build_portfolio_evidence(**kwargs)
    assert first == second  # deterministic: fixed now_ms, no side effects
    on_disk = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert on_disk == first
    assert first["schema_version"] == 1
    assert first["flow"] == FLOW == "CANDIDATE_PORTFOLIO_EVIDENCE"
    assert first["policy_version"] == POLICY_VERSION == "portfolio_evidence.v1"
    assert first["readiness"] == "L2_DRY_RUN_ONLY"
    assert first["population"]["candidate_ids"] == list(POPULATION)
    assert first["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credential_access": False,
        "broker_order_submission": False,
    }
    assert first["factor_library"]["available"] is True
    assert len(first["factor_library"]["built"]) == 10


def test_missing_factor_data_marks_common_exposure_missing_everywhere(tmp_path):
    payload = build_portfolio_evidence(**_kwargs(tmp_path, factor_builder=lambda: None))
    assert payload["factor_library"]["available"] is False
    for candidate in LEGACY:
        row = payload["alphas"][candidate]
        assert row["common_exposure"]["available"] is False
        assert "common_exposure" in row["missing"]


def test_challenger_without_capacity_band_is_missing_not_fabricated(tmp_path):
    # Only the legacy three carry bands; a challenger WITH a run gets capacity MISSING.
    kwargs = _kwargs(tmp_path)
    _write_store(tmp_path / "dry_run.db", candidates=LEGACY + (CHALLENGER,))
    payload = build_portfolio_evidence(**kwargs)
    row = payload["alphas"][CHALLENGER]
    assert row["status"] != "MISSING_RUN"
    assert row["capacity"]["available"] is False
    assert "capacity" in row["missing"]


def test_missing_sor_document_marks_tca_missing(tmp_path):
    kwargs = _kwargs(tmp_path)
    (tmp_path / "sor.json").unlink()
    payload = build_portfolio_evidence(**kwargs)
    for candidate in LEGACY:
        assert "tca" in payload["alphas"][candidate]["missing"]


def test_invalid_capacity_band_fails_closed(tmp_path):
    kwargs = _kwargs(tmp_path)
    doc = _capacity_doc()
    doc["books"]["carry"] = {"aum_low": 3.0e7, "aum_high": 1.0e6}  # low > high
    _write_json(tmp_path / "capacity.json", doc)
    with pytest.raises(ValueError, match="aum_low > aum_high"):
        build_portfolio_evidence(**kwargs)


def test_missing_dry_run_store_fails_closed(tmp_path):
    kwargs = _kwargs(tmp_path)
    (tmp_path / "dry_run.db").unlink()
    with pytest.raises(RuntimeError, match="dry-run store missing"):
        build_portfolio_evidence(**kwargs)


def test_legacy_runs_without_manifest_v2_are_excluded_from_portfolio(tmp_path):
    """A legacy DB row is not a portfolio/search/promotion input.

    The old records have internally consistent curves, but no RunManifestV2
    binding.  They must therefore remain visible only as missing diagnostic
    evidence and may not become incumbent-book members.
    """
    payload = build_portfolio_evidence(
        **_kwargs(tmp_path, _authoritative_records=False)
    )
    assert payload["incumbent_book"]["members_with_runs"] == []
    for candidate in LEGACY:
        assert payload["alphas"][candidate]["status"] == "MISSING_RUN"
