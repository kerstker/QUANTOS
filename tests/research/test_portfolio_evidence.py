"""Tests for services/research/portfolio_evidence.py (WP4 W4-1 pure core).

Every function is pure with injected series; the tests pin the happy paths, the
fail-closed None paths (insufficient overlap / singular / absent inputs), input
validation (ValueError on malformed), and the aggregator's missing-inventory —
no fabrication anywhere.
"""
from __future__ import annotations

import pytest

from services.research import portfolio_evidence as pe


N = 20  # >= MIN_COMMON_PERIODS with headroom for the k=10 factor OLS (n > k+1)


def _pattern(seed: int, n: int = N, scale: float = 0.01) -> list[float]:
    """Deterministic non-trivial return series (varying, zero-mean-ish)."""
    return [scale * (((i * (seed + 1) + seed) % 7) - 3) for i in range(n)]


def _incumbents() -> dict:
    return {"inc_a": _pattern(1), "inc_b": _pattern(2, scale=0.007)}


def _factors(k: int = 10) -> dict:
    return {f"f{j}": _pattern(j + 3, scale=0.004 + 0.001 * j) for j in range(k)}


# ---------------------------------------------------------------------------
# marginal_contribution
# ---------------------------------------------------------------------------


def test_marginal_contribution_happy_path_reports_ir_and_incremental_sharpe():
    candidate = [a + b for a, b in zip(_pattern(5, scale=0.012), _pattern(1))]
    out = pe.marginal_contribution(candidate, _incumbents())
    assert out["available"] is True
    assert out["n_common_periods"] == N
    assert out["marginal_ir"] is not None
    assert out["incremental_sharpe"] is not None
    assert out["sharpe_without_candidate"] is not None
    assert out["sharpe_with_candidate"] is not None
    assert out["reason"] is None


def test_marginal_contribution_redundant_candidate_collapses_ir_not_fabricated():
    incumbent = _pattern(1)
    out = pe.marginal_contribution(list(incumbent), {"inc_a": incumbent})
    # An exact repackaging is a near-perfect OLS fit: the appraisal ratio is
    # numerically undefined and must come back None, never an arbitrary O(1) value.
    assert out["marginal_ir"] is None
    assert out["available"] is False
    assert "MARGINAL_IR_UNDEFINED" in out["reason"]
    # The Sharpe delta is still honestly computable: a clone gets half the
    # inverse-vol weight and the combine is bit-identical to the incumbent alone.
    assert out["incremental_sharpe"] == pytest.approx(0.0, abs=1e-12)


def test_marginal_contribution_insufficient_overlap_is_all_none_with_reason():
    out = pe.marginal_contribution(_pattern(5)[:5], {k: v[:5] for k, v in _incumbents().items()})
    assert out == {
        "available": False,
        "marginal_ir": None,
        "incremental_sharpe": None,
        "n_common_periods": None,
        "reason": "INSUFFICIENT_COMMON_PERIODS",
    }


def test_marginal_contribution_no_incumbent_book():
    out = pe.marginal_contribution(_pattern(5), {})
    assert out["available"] is False
    assert out["reason"] == "NO_INCUMBENT_BOOK"
    assert out["marginal_ir"] is None


def test_marginal_contribution_rejects_malformed_inputs():
    with pytest.raises(ValueError, match="equal-length"):
        pe.marginal_contribution(_pattern(5), {"inc_a": _pattern(1)[:10]})
    with pytest.raises(ValueError, match="non-finite"):
        pe.marginal_contribution([0.01] * N, {"inc_a": [float("nan")] * N})


# ---------------------------------------------------------------------------
# common_exposure
# ---------------------------------------------------------------------------


def test_common_exposure_happy_path_reports_r2_beta_residual_corr():
    factors = _factors()
    candidate = [
        2.0 * factors["f1"][i] - factors["f4"][i] + _pattern(6)[i] for i in range(N)
    ]
    out = pe.common_exposure(candidate, factors, _incumbents())
    assert out["available"] is True
    assert out["r_squared"] is not None and 0.0 <= out["r_squared"] <= 1.0
    assert out["max_abs_beta"] is not None and out["max_abs_beta"] > 0.0
    assert out["max_residual_corr_vs_incumbent"] is not None
    assert out["n_factors"] == 10
    assert out["reason"] is None


def test_common_exposure_insufficient_sample_is_all_none_with_reason():
    factors = {k: v[:11] for k, v in _factors().items()}
    out = pe.common_exposure(
        _pattern(5)[:11], factors, {k: v[:11] for k, v in _incumbents().items()}
    )
    assert out["available"] is False
    assert out["reason"] == "INSUFFICIENT_COMMON_PERIODS"
    assert out["r_squared"] is None
    assert out["max_abs_beta"] is None
    assert out["max_residual_corr_vs_incumbent"] is None


def test_common_exposure_missing_inputs():
    out = pe.common_exposure(_pattern(5), {}, _incumbents())
    assert out["available"] is False and out["reason"] == "NO_FACTOR_LIBRARY"
    out = pe.common_exposure(_pattern(5), _factors(), {})
    assert out["available"] is False and out["reason"] == "NO_INCUMBENT_BOOK"


def test_common_exposure_rejects_grid_mismatch():
    with pytest.raises(ValueError, match="same grid"):
        pe.common_exposure(_pattern(5), {k: v[:15] for k, v in _factors().items()}, _incumbents())


# ---------------------------------------------------------------------------
# crowding_summary
# ---------------------------------------------------------------------------


def test_crowding_summary_counts_blocked_share_and_max_z():
    rows = (
        [{"blocked": True, "z": 3.4}, {"blocked": True, "z": 4.1}]
        + [{"blocked": False, "z": 1.0}] * 6
        + [{"blocked": False, "z": None}] * 2
    )
    out = pe.crowding_summary(rows)
    assert out == {
        "available": True,
        "n_weeks": 10,
        "n_blocked": 2,
        "blocked_share": 0.2,
        "max_z": 4.1,
        "reason": None,
    }


def test_crowding_summary_unpersisted_z_is_honest_none():
    rows = [{"blocked": True, "z": None}] * 3 + [{"blocked": False, "z": None}] * 7
    out = pe.crowding_summary(rows)
    assert out["max_z"] is None  # never guessed from sample-reason text
    assert out["n_blocked"] == 3
    assert out["blocked_share"] == pytest.approx(0.3)


def test_crowding_summary_empty_and_malformed():
    out = pe.crowding_summary([])
    assert out["available"] is False
    assert out["reason"] == "NO_GATE_DECISIONS"
    assert out["blocked_share"] is None
    with pytest.raises(ValueError, match="malformed"):
        pe.crowding_summary([{"blocked": "yes"}])
    with pytest.raises(ValueError, match="non-finite"):
        pe.crowding_summary([{"blocked": False, "z": float("inf")}])


# ---------------------------------------------------------------------------
# capacity_summary
# ---------------------------------------------------------------------------


def test_capacity_summary_passthrough_with_provenance():
    band = {"aum_low": 1_953_401.0, "aum_high": 19_534_010.0, "binding": "participation"}
    out = pe.capacity_summary(band, provenance="UNCALIBRATED_PRINCIPLED")
    assert out == {
        "available": True,
        "low": 1_953_401.0,
        "high": 19_534_010.0,
        "provenance": "UNCALIBRATED_PRINCIPLED",
        "binding": "participation",
        "reason": None,
    }


def test_capacity_summary_absent_bounds_are_honest_not_fabricated():
    out = pe.capacity_summary(
        {"aum_low": None, "aum_high": None}, provenance="UNCALIBRATED_PRINCIPLED"
    )
    assert out["available"] is False
    assert out["low"] is None and out["high"] is None
    assert out["reason"] == "BAND_BOUNDS_ABSENT"


def test_capacity_summary_fail_closed_on_invalid_band():
    with pytest.raises(ValueError, match="positive finite"):
        pe.capacity_summary({"aum_low": -5.0}, provenance="P")
    with pytest.raises(ValueError, match="not numeric"):
        pe.capacity_summary({"aum_low": "2M"}, provenance="P")
    with pytest.raises(ValueError, match="aum_low > aum_high"):
        pe.capacity_summary({"aum_low": 10.0, "aum_high": 5.0}, provenance="P")
    with pytest.raises(ValueError, match="provenance"):
        pe.capacity_summary({"aum_low": 1.0}, provenance="")


# ---------------------------------------------------------------------------
# tca_reference
# ---------------------------------------------------------------------------

_SOR_DOC = {
    "status": "PASS",
    "simulation_only": True,
    "route_tca_count": 2,
    "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
    "run_id": "run-1",
    "snapshot_id": "snap-1",
}
_TCA_RECORDS = [
    {"reference_price": "1.0", "filled_quantity": 100, "implementation_shortfall_bps": "2.0"},
    {"reference_price": "0.5", "filled_quantity": 200, "implementation_shortfall_bps": "5.0"},
]


def test_tca_reference_weighted_shortfall_from_records():
    out = pe.tca_reference(_SOR_DOC, _TCA_RECORDS)
    assert out["available"] is True
    # notional weights: 1.0*100=100 vs 0.5*200=100 → plain mean of 2.0 and 5.0
    assert out["weighted_shortfall_bps"] == pytest.approx(3.5)
    assert out["fee_calibration"] == "UNCALIBRATED_RESEARCH_ASSUMPTION"
    assert out["run_id"] == "run-1"
    assert out["route_tca_count"] == 2


def test_tca_reference_absent_or_non_simulation_is_unavailable():
    assert pe.tca_reference({})["available"] is False
    assert pe.tca_reference({})["reason"] == "SOR_STATUS_ABSENT"
    out = pe.tca_reference({**_SOR_DOC, "simulation_only": False})
    assert out["available"] is False
    assert out["reason"] == "SOR_STATUS_NOT_A_PASSING_SIMULATION"
    out = pe.tca_reference({**_SOR_DOC, "route_tca_count": 0})
    assert out["available"] is False


def test_tca_reference_without_records_keeps_shortfall_none():
    out = pe.tca_reference(_SOR_DOC)
    assert out["available"] is True
    assert out["weighted_shortfall_bps"] is None  # book-depth gap stays explicit


def test_tca_reference_invalid_record_fails_the_whole_reference():
    bad = [{"reference_price": "not-a-number", "filled_quantity": 1,
            "implementation_shortfall_bps": "1.0"}]
    out = pe.tca_reference(_SOR_DOC, bad)
    assert out["available"] is False
    assert out["reason"] == "INVALID_TCA_RECORD"


# ---------------------------------------------------------------------------
# build_candidate_portfolio_evidence (aggregator + missing inventory)
# ---------------------------------------------------------------------------


def _full_inputs() -> dict:
    factors = _factors()
    candidate = [factors["f2"][i] + _pattern(5)[i] for i in range(N)]
    return {
        "candidate_returns": candidate,
        "incumbent_returns_by_id": _incumbents(),
        "factor_returns_by_name": factors,
        "capacity_band": {"aum_low": 1.0e6, "aum_high": 2.0e7},
        "capacity_provenance": "UNCALIBRATED_PRINCIPLED",
        "crowding_rows": [{"blocked": False, "z": None}] * N,
        "sor_status_document": _SOR_DOC,
        "route_tca_records": _TCA_RECORDS,
    }


def test_aggregator_full_inputs_ok_row_no_missing():
    row = pe.build_candidate_portfolio_evidence("cand_x", **_full_inputs())
    assert row["candidate_id"] == "cand_x"
    assert row["status"] == "OK"
    assert row["missing"] == []
    for name in pe.SUB_EVIDENCE_NAMES:
        assert row[name]["available"] is True


def test_aggregator_missing_run_marks_all_five_missing():
    row = pe.build_candidate_portfolio_evidence("ghost")
    assert row["status"] == "MISSING_RUN"
    assert row["missing"] == sorted(pe.SUB_EVIDENCE_NAMES)
    for name in pe.SUB_EVIDENCE_NAMES:
        assert row[name]["available"] is False
        assert row[name]["reason"] == "INPUT_MISSING"


def test_aggregator_partial_inputs_incomplete_with_named_missing():
    inputs = _full_inputs()
    del inputs["capacity_band"]
    del inputs["capacity_provenance"]
    del inputs["sor_status_document"]
    row = pe.build_candidate_portfolio_evidence("cand_x", **inputs)
    assert row["status"] == "INCOMPLETE"
    assert row["missing"] == ["capacity", "tca"]
    assert row["capacity"]["available"] is False
    assert row["tca"]["available"] is False
    assert row["marginal_contribution"]["available"] is True


def test_aggregator_insufficient_overlap_counts_as_missing_not_fabrication():
    inputs = _full_inputs()
    inputs["candidate_returns"] = inputs["candidate_returns"][:4]
    inputs["incumbent_returns_by_id"] = {
        k: v[:4] for k, v in inputs["incumbent_returns_by_id"].items()
    }
    inputs["factor_returns_by_name"] = {
        k: v[:4] for k, v in inputs["factor_returns_by_name"].items()
    }
    row = pe.build_candidate_portfolio_evidence("cand_x", **inputs)
    assert row["status"] == "INCOMPLETE"
    assert "marginal_contribution" in row["missing"]
    assert "common_exposure" in row["missing"]
    assert row["marginal_contribution"]["marginal_ir"] is None
    assert row["common_exposure"]["r_squared"] is None


# ---------------------------------------------------------------------------
# W4-3: PortfolioGatePolicy + evaluate_portfolio_gate (merit, not completeness)
# ---------------------------------------------------------------------------

_REFERENCE_AUM = 1_000_000.0


def _gate_row() -> dict:
    """完备且全准则 PASS 的 portfolio evidence 行(形状镜像真 artifact)。"""
    return {
        "candidate_id": "cand_x",
        "status": "OK",
        "marginal_contribution": {
            "available": True,
            "marginal_ir": 0.13,
            "incremental_sharpe": 0.34,
            "n_common_periods": 192,
        },
        "common_exposure": {
            "available": True,
            "r_squared": 0.05,
            "max_abs_beta": 0.20,
            "max_residual_corr_vs_incumbent": 0.07,
        },
        "capacity": {"available": True, "low": 7_000_000.0, "high": 7_000_000.0},
        "crowding": {"available": True, "n_weeks": 235, "n_blocked": 21,
                     "blocked_share": 0.09, "max_z": None},
        "tca": {"available": True, "weighted_shortfall_bps": 3.3},
        "missing": [],
    }


def test_policy_defaults_match_frozen_preregistered_table():
    policy = pe.PortfolioGatePolicy()
    assert policy.policy_version == "portfolio_gate_policy.v1"
    assert policy.min_marginal_ir == 0.0
    assert policy.min_incremental_sharpe == 0.0
    assert policy.max_common_exposure_r2 == 0.5
    assert policy.max_residual_corr_vs_incumbent == 0.5
    assert policy.min_capacity_to_aum_ratio == 3.0
    assert policy.max_crowding_blocked_share == 0.25


def test_policy_validation_fails_closed():
    with pytest.raises(ValueError, match="min_marginal_ir"):
        pe.PortfolioGatePolicy(min_marginal_ir=float("nan"))
    with pytest.raises(ValueError, match="min_incremental_sharpe"):
        pe.PortfolioGatePolicy(min_incremental_sharpe=True)
    with pytest.raises(ValueError, match="max_common_exposure_r2"):
        pe.PortfolioGatePolicy(max_common_exposure_r2=0.0)
    with pytest.raises(ValueError, match="max_common_exposure_r2"):
        pe.PortfolioGatePolicy(max_common_exposure_r2=1.5)
    with pytest.raises(ValueError, match="max_residual_corr_vs_incumbent"):
        pe.PortfolioGatePolicy(max_residual_corr_vs_incumbent=-0.1)
    with pytest.raises(ValueError, match="max_crowding_blocked_share"):
        pe.PortfolioGatePolicy(max_crowding_blocked_share=float("inf"))
    with pytest.raises(ValueError, match="min_capacity_to_aum_ratio"):
        pe.PortfolioGatePolicy(min_capacity_to_aum_ratio=0.0)
    with pytest.raises(ValueError, match="policy_version"):
        pe.PortfolioGatePolicy(policy_version="  ")


def test_policy_content_hash_drifts_with_any_identity_field():
    base = pe.PortfolioGatePolicy()
    assert base.content_hash() == pe.PortfolioGatePolicy().content_hash()
    drifted = (
        pe.PortfolioGatePolicy(min_marginal_ir=0.1),
        pe.PortfolioGatePolicy(min_incremental_sharpe=-0.5),
        pe.PortfolioGatePolicy(max_common_exposure_r2=0.4),
        pe.PortfolioGatePolicy(max_residual_corr_vs_incumbent=0.6),
        pe.PortfolioGatePolicy(min_capacity_to_aum_ratio=2.0),
        pe.PortfolioGatePolicy(max_crowding_blocked_share=0.2),
        pe.PortfolioGatePolicy(policy_version="portfolio_gate_policy.v2"),
    )
    for other in drifted:
        assert other.content_hash() != base.content_hash()


def test_evaluate_pass_on_complete_row():
    out = pe.evaluate_portfolio_gate(
        _gate_row(), pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "PASS"
    assert out["failures"] == []
    assert out["missing"] == []
    assert out["policy_version"] == "portfolio_gate_policy.v1"
    assert out["policy_hash"] == pe.PortfolioGatePolicy().content_hash()
    assert out["calibration"] == "UNCALIBRATED"


def test_evaluate_each_merit_failure_code():
    cases = (
        (("marginal_contribution", "marginal_ir", -0.01), "MARGINAL_IR_BELOW_MIN"),
        (("marginal_contribution", "incremental_sharpe", -0.01),
         "INCREMENTAL_SHARPE_BELOW_MIN"),
        (("common_exposure", "r_squared", 0.9), "COMMON_EXPOSURE_R2_TOO_HIGH"),
        (("common_exposure", "max_residual_corr_vs_incumbent", 0.9),
         "RESIDUAL_CORR_TOO_HIGH"),
        (("crowding", "blocked_share", 0.5), "CROWDING_BLOCKED_SHARE_TOO_HIGH"),
    )
    for (block, key, value), code in cases:
        row = _gate_row()
        row[block][key] = value
        out = pe.evaluate_portfolio_gate(
            row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
        )
        assert out["status"] == "FAIL"
        assert out["failures"] == [code]


def test_evaluate_capacity_ratio_math_and_boundary():
    row = _gate_row()
    row["capacity"]["low"] = 2_999_999.0  # ratio 2.999999 < 3.0
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["failures"] == ["CAPACITY_RATIO_BELOW_MIN"]
    row["capacity"]["low"] = 3_000_000.0  # ratio exactly 3.0 → pass
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "PASS"


def test_evaluate_all_criteria_no_shortcircuit():
    row = _gate_row()
    row["marginal_contribution"]["marginal_ir"] = -1.0
    row["common_exposure"]["r_squared"] = 0.99
    row["crowding"]["blocked_share"] = 1.0
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "FAIL"
    assert out["failures"] == [
        "MARGINAL_IR_BELOW_MIN",
        "COMMON_EXPOSURE_R2_TOO_HIGH",
        "CROWDING_BLOCKED_SHARE_TOO_HIGH",
    ]


def test_evaluate_none_or_nonfinite_metric_on_available_block_is_invalid_fail():
    cases = (
        (("marginal_contribution", "marginal_ir"), "MARGINAL_IR_INVALID"),
        (("marginal_contribution", "incremental_sharpe"),
         "INCREMENTAL_SHARPE_INVALID"),
        (("common_exposure", "r_squared"), "COMMON_EXPOSURE_R2_INVALID"),
        (("common_exposure", "max_residual_corr_vs_incumbent"),
         "RESIDUAL_CORR_INVALID"),
        (("capacity", "low"), "CAPACITY_LOW_INVALID"),
        (("crowding", "blocked_share"), "CROWDING_BLOCKED_SHARE_INVALID"),
    )
    for (block, key), code in cases:
        for bad in (None, float("nan"), float("inf")):
            row = _gate_row()
            row[block][key] = bad
            out = pe.evaluate_portfolio_gate(
                row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
            )
            assert out["status"] == "FAIL"
            assert out["failures"] == [code]


def test_evaluate_missing_run_row_is_incomplete_not_merit_failure():
    row = pe.build_candidate_portfolio_evidence("ghost")
    assert row["status"] == "MISSING_RUN"
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "INCOMPLETE"
    assert out["failures"] == []
    assert out["missing"] == [
        "capacity", "common_exposure", "crowding", "marginal_contribution",
    ]
    assert "tca" not in out["missing"]  # 容忍缺失,不进 missing


def test_evaluate_declared_missing_and_unavailable_block_are_incomplete():
    row = _gate_row()
    row["status"] = "INCOMPLETE"
    row["missing"] = ["crowding"]
    row["crowding"] = {"available": False, "reason": "INPUT_MISSING"}
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "INCOMPLETE"
    assert out["missing"] == ["crowding"]
    assert out["failures"] == []

    # available=False 但 missing 清单未声明 → 同样 INCOMPLETE(纵深防御,绝不判 merit)
    row = _gate_row()
    row["capacity"] = {"available": False, "reason": "BAND_BOUNDS_ABSENT"}
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "INCOMPLETE"
    assert out["missing"] == ["capacity"]


def test_evaluate_tolerated_tca_missing_does_not_block_merit():
    row = _gate_row()
    row["tca"] = {"available": False, "reason": "INPUT_MISSING"}
    row["missing"] = ["tca"]
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "PASS"
    assert pe.PORTFOLIO_GATE_TOLERATED_MISSING == frozenset({"tca"})


def test_evaluate_incomplete_never_reports_merit_failures():
    # carry 实况形状:crowding 缺 + incremental_sharpe 为负 → INCOMPLETE,不评 FAIL
    row = _gate_row()
    row["status"] = "INCOMPLETE"
    row["missing"] = ["crowding"]
    row["crowding"] = {"available": False, "reason": "INPUT_MISSING"}
    row["marginal_contribution"]["incremental_sharpe"] = -0.053
    out = pe.evaluate_portfolio_gate(
        row, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "INCOMPLETE"
    assert out["failures"] == []
    assert out["missing"] == ["crowding"]


def test_evaluate_rejects_bad_reference_aum_and_policy_and_shape():
    policy = pe.PortfolioGatePolicy()
    for bad in (0.0, -1.0, float("nan"), float("inf"), True, "1e6"):
        with pytest.raises(ValueError, match="reference_aum"):
            pe.evaluate_portfolio_gate(_gate_row(), policy, reference_aum=bad)
    with pytest.raises(TypeError, match="PortfolioGatePolicy"):
        pe.evaluate_portfolio_gate(
            _gate_row(), object(), reference_aum=_REFERENCE_AUM
        )
    row = _gate_row()
    row["missing"] = "capacity"
    with pytest.raises(ValueError, match="malformed 'missing'"):
        pe.evaluate_portfolio_gate(row, policy, reference_aum=_REFERENCE_AUM)


def test_evaluate_real_artifact_rows_reproduce_expected_verdicts():
    """真 artifact 行(data/cache/candidate_portfolio_evidence.json)的期望语义:
    crowd_contra/top_contra PASS;carry 因 crowding 缺失 INCOMPLETE(其负
    incremental_sharpe 不构成 merit FAIL);challenger MISSING_RUN → INCOMPLETE。"""
    crowd = {
        "status": "OK",
        "marginal_contribution": {"available": True, "marginal_ir": 0.13018888691980002,
                                  "incremental_sharpe": 0.34355247064050154},
        "common_exposure": {"available": True, "r_squared": 0.04516655781604906,
                            "max_residual_corr_vs_incumbent": 0.07196978598288369},
        "capacity": {"available": True, "low": 7164102.963885001},
        "crowding": {"available": True, "blocked_share": 0.08936170212765958},
        "tca": {"available": True},
        "missing": [],
    }
    out = pe.evaluate_portfolio_gate(
        crowd, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "PASS"

    carry = {
        "status": "INCOMPLETE",
        "marginal_contribution": {"available": True, "marginal_ir": 0.0631818037038535,
                                  "incremental_sharpe": -0.053077517748044034},
        "common_exposure": {"available": True, "r_squared": 0.08955844344718478,
                            "max_residual_corr_vs_incumbent": 0.07196978598288369},
        "capacity": {"available": True, "low": 1953401.0952933603},
        "crowding": {"available": False, "reason": "INPUT_MISSING"},
        "tca": {"available": True},
        "missing": ["crowding"],
    }
    out = pe.evaluate_portfolio_gate(
        carry, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "INCOMPLETE"
    assert out["failures"] == []
    assert out["missing"] == ["crowding"]

    challenger = pe.build_candidate_portfolio_evidence("crowd_contra_14d")
    out = pe.evaluate_portfolio_gate(
        challenger, pe.PortfolioGatePolicy(), reference_aum=_REFERENCE_AUM
    )
    assert out["status"] == "INCOMPLETE"
    assert out["failures"] == []
