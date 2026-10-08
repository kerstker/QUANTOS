import math

import pytest

from services.portfolio.crowding_gate import (
    CrowdingGateDecision,
    evaluate_crowding_gate,
    project_symbol_crowding,
)


def test_high_crowding_score_blocks():
    decision = evaluate_crowding_gate(
        {"A": 0.6, "B": -0.4},
        {"A": 0.70, "B": 0.80},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert isinstance(decision, CrowdingGateDecision)
    assert decision.status == "BLOCK"
    assert decision.score == pytest.approx(0.74)
    assert decision.threshold == 0.60
    assert decision.exposures == {"A": 0.70, "B": 0.80}
    assert any("crowding_score" in reason for reason in decision.reasons)


def test_medium_crowding_score_warns():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.50, "B": 0.54},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "WARN"
    assert decision.score == pytest.approx(0.52)
    assert any("warn_threshold" in reason for reason in decision.reasons)


def test_crowding_score_equal_to_threshold_warns_instead_of_blocks():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.60, "B": 0.60},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "WARN"
    assert decision.score == pytest.approx(0.60)


def test_crowding_score_equal_to_warn_threshold_passes_instead_of_warns():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.45, "B": 0.45},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "PASS"
    assert decision.score == pytest.approx(0.45)


def test_low_crowding_score_passes():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.10, "B": 0.30},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "PASS"
    assert decision.score == pytest.approx(0.20)
    assert decision.reasons == ("crowding_score_ok",)


def test_missing_evidence_below_min_coverage_blocks():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.10},
        min_coverage=0.80,
    )

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert decision.exposures == {"A": 0.10}
    assert any("insufficient_coverage" in reason for reason in decision.reasons)


@pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf"), -float("inf")])
def test_invalid_crowding_evidence_blocks(bad_value):
    decision = evaluate_crowding_gate(
        {"A": 1.0},
        {"A": bad_value},
    )

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert any("invalid_crowding" in reason for reason in decision.reasons)


@pytest.mark.parametrize("weights", [{}, {"A": 0.0, "B": -0.0}])
def test_empty_or_zero_gross_books_block(weights):
    decision = evaluate_crowding_gate(weights, {"A": 0.1, "B": 0.2})

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert decision.reasons == ("empty_or_zero_gross_book",)


@pytest.mark.parametrize("bad_weight", [math.nan, math.inf, -math.inf])
def test_non_finite_weight_blocks(bad_weight):
    decision = evaluate_crowding_gate({"A": bad_weight}, {"A": 0.1})

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert decision.reasons == ("invalid_weight:A",)


def test_absolute_weight_drives_score_for_long_short_books():
    decision = evaluate_crowding_gate(
        {"HIGH": -0.80, "LOW": 0.20},
        {"HIGH": 0.80, "LOW": 0.00},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "BLOCK"
    assert decision.score == pytest.approx(0.64)


def _diffuse_high_avg_book():
    # 5 名各 0.2(无一 >0.25 集中阈)+ 全高拥挤 0.9 -> 平均 0.9>threshold,但集中度判据不触发,
    # 故可隔离"平均分路径"的 BLOCK/WARN 行为
    weights = {c: 0.2 for c in "ABCDE"}
    crowding = {c: 0.9 for c in "ABCDE"}
    return weights, crowding


def test_average_path_blocks_by_default():
    # 默认(average_warn_only=False)平均分 > threshold -> BLOCK(字节等价旧行为)
    w, c = _diffuse_high_avg_book()
    d = evaluate_crowding_gate(w, c, threshold=0.60, warn_threshold=0.45)
    assert d.status == "BLOCK"


def test_average_warn_only_caps_average_at_warn():
    # Rank average is low-information for this symmetric book; cap this diagnostic path at WARN.
    w, c = _diffuse_high_avg_book()
    d = evaluate_crowding_gate(w, c, threshold=0.60, warn_threshold=0.45, average_warn_only=True)
    assert d.status == "WARN"


def test_absolute_z_gate_blocks_shorted_crowded_name():
    # The uncalibrated conservative z safety rule blocks a crowded short name.
    d = evaluate_crowding_gate(
        {"A": -0.2, "B": 0.2}, {"A": 0.5, "B": 0.5},
        average_warn_only=True, crowding_z_by_symbol={"A": 3.5, "B": 0.1}, max_crowding_z=3.0)
    assert d.status == "BLOCK"
    assert any("absolute_crowding_z_block:A" in r for r in d.reasons)


def test_absolute_z_gate_ignores_long_names():
    # z 判据只评 short 腿(long 腿再高 z 也不由此 BLOCK)
    d = evaluate_crowding_gate(
        {"A": 0.2, "B": 0.2}, {"A": 0.5, "B": 0.5},
        average_warn_only=True, crowding_z_by_symbol={"A": 9.0}, max_crowding_z=3.0)
    assert d.status != "BLOCK"


def test_absolute_z_gate_below_threshold_no_block():
    d = evaluate_crowding_gate(
        {"A": -0.2}, {"A": 0.5},
        average_warn_only=True, crowding_z_by_symbol={"A": 2.9}, max_crowding_z=3.0)
    assert d.status != "BLOCK"


def test_absolute_z_missing_is_skipped_not_blocked():
    # z 需足够历史;缺 z 的名字不评 z 维(不 fail-open 成 BLOCK,其它判据仍在)
    d = evaluate_crowding_gate(
        {"A": -0.2}, {"A": 0.5},
        average_warn_only=True, crowding_z_by_symbol={}, max_crowding_z=3.0)
    assert d.status != "BLOCK"


def test_absolute_z_non_finite_fails_closed():
    d = evaluate_crowding_gate(
        {"A": -0.2}, {"A": 0.5},
        average_warn_only=True, crowding_z_by_symbol={"A": float("nan")}, max_crowding_z=3.0)
    assert d.status == "BLOCK"
    assert any("invalid_crowding_z:A" in r for r in d.reasons)


def test_new_params_default_none_false_byte_identical():
    # 不传新参 -> 与旧签名逐字节相同(默认关闭 z 门 + average 可 BLOCK)
    w, c = _diffuse_high_avg_book()
    assert evaluate_crowding_gate(w, c).status == "BLOCK"


def test_config_validation_rejects_invalid_thresholds():
    with pytest.raises(ValueError):
        evaluate_crowding_gate({"A": 1.0}, {"A": 0.1}, threshold=0.40, warn_threshold=0.50)

    with pytest.raises(ValueError):
        evaluate_crowding_gate({"A": 1.0}, {"A": 0.1}, min_coverage=1.01)


def test_project_symbol_crowding_extracts_named_factor_deterministically():
    projected = project_symbol_crowding(
        {"MSFT": 0.3, "MISSING": 0.5, "AAPL": -0.2},
        {
            "crowding": {"AAPL": 0.40, "MSFT": 0.20},
            "liquidity": {"AAPL": 1.0, "MSFT": 2.0, "MISSING": 3.0},
        },
        factor_name="liquidity",
    )

    assert projected == {"AAPL": 1.0, "MISSING": 3.0, "MSFT": 2.0}


def test_project_symbol_crowding_defaults_to_crowding_and_filters_weights():
    projected = project_symbol_crowding(
        {"MSFT": 0.3, "AAPL": -0.2},
        {
            "crowding": {"AAPL": 0.40, "MSFT": 0.20, "UNHELD": 0.90},
            "liquidity": {"AAPL": 1.0, "MSFT": 2.0},
        },
    )

    assert projected == {"AAPL": 0.40, "MSFT": 0.20}


def test_project_symbol_crowding_missing_factor_returns_empty_map():
    projected = project_symbol_crowding({"A": 1.0}, {"liquidity": {"A": 0.3}}, factor_name="crowding")

    assert projected == {}


# --- Conservative safety-limit drift lock; this is not calibration authority. ---

def test_uncalibrated_safety_limits_match_signature_and_are_explicitly_invalidated():
    import inspect
    from services.portfolio.crowding_gate import (
        CALIBRATED_DEFAULTS,
        CONSERVATIVE_SAFETY_DEFAULTS,
        evaluate_crowding_gate,
    )
    sig = inspect.signature(evaluate_crowding_gate)
    for k in ("threshold", "warn_threshold", "min_coverage", "max_name_weight", "max_exit_days"):
        assert sig.parameters[k].default == CALIBRATED_DEFAULTS[k], f"{k} drifted from CALIBRATED_DEFAULTS"
    assert CALIBRATED_DEFAULTS["provenance"] == \
        "LEGACY_GENERIC_DEFAULTS_SCANNED_NOT_PRODUCTION_CALIBRATED"
    assert CALIBRATED_DEFAULTS["threshold_status"] == \
        "UNCALIBRATED_CONSERVATIVE_SAFETY_LIMIT"
    assert CALIBRATED_DEFAULTS["calibration_authoritative"] is False
    assert CALIBRATED_DEFAULTS["invalidation_code"] == \
        "INVALIDATED_PERP_LEDGER_V1"
    assert CALIBRATED_DEFAULTS is CONSERVATIVE_SAFETY_DEFAULTS
