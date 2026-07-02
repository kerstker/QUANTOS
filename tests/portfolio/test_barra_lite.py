import math

import pytest

from quantos.contracts.factor import FactorRiskModel
from services.portfolio.barra_lite import (
    barra_lite_report,
    build_barra_lite_model,
    corr_exposure,
    winsorize,
    zscore,
)


def _sample_stdev(values):
    mean = sum(values) / len(values)
    return math.sqrt(sum((x - mean) ** 2 for x in values) / (len(values) - 1))


def _risk_model():
    return FactorRiskModel(
        factor_ids=("beta", "size"),
        loadings={
            "AAPL": (1.1, -0.2),
            "MSFT": (0.8, 0.6),
            "TSLA": (1.5, -1.0),
        },
        factor_cov=((0.04, 0.005), (0.005, 0.03)),
        specific_var={"AAPL": 0.02, "MSFT": 0.03, "TSLA": 0.05},
    )


def test_zscore_centers_to_zero_and_uses_sample_stdev():
    scored = zscore({"a": 1.0, "b": 2.0, "c": 4.0})
    values = list(scored.values())

    assert set(scored) == {"a", "b", "c"}
    assert sum(values) / len(values) == pytest.approx(0.0, abs=1e-12)
    assert _sample_stdev(values) == pytest.approx(1.0, abs=1e-12)
    assert scored["a"] < scored["b"] < scored["c"]


def test_zscore_returns_zero_for_empty_and_constant_cross_sections():
    assert zscore({}) == {}
    assert zscore({"a": 7.0, "b": 7.0, "c": 7.0}) == {
        "a": 0.0,
        "b": 0.0,
        "c": 0.0,
    }


def test_winsorize_clips_to_cross_section_order_statistics_and_keeps_keys():
    clipped = winsorize(
        {"low": -100.0, "mid1": -1.0, "mid2": 0.0, "mid3": 2.0, "high": 99.0},
        limit=0.2,
    )

    assert set(clipped) == {"low", "mid1", "mid2", "mid3", "high"}
    assert clipped == {
        "low": -1.0,
        "mid1": -1.0,
        "mid2": 0.0,
        "mid3": 2.0,
        "high": 2.0,
    }


@pytest.mark.parametrize("limit", [-0.01, 0.5, 1.0])
def test_winsorize_rejects_invalid_limit(limit):
    with pytest.raises(ValueError):
        winsorize({"a": 1.0, "b": 2.0}, limit=limit)


def test_corr_exposure_uses_only_aligned_symbols_for_pearson_correlation():
    exposure = corr_exposure(
        {"a": 1.0, "b": 2.0, "c": 3.0, "ignored": 100.0},
        {"a": 10.0, "b": 20.0, "c": 30.0, "extra": -999.0},
    )
    inverse = corr_exposure(
        {"a": 1.0, "b": 2.0, "c": 3.0},
        {"a": 30.0, "b": 20.0, "c": 10.0},
    )

    assert exposure == pytest.approx(1.0, abs=1e-12)
    assert inverse == pytest.approx(-1.0, abs=1e-12)


def test_corr_exposure_matches_nonperfect_pearson_value():
    exposure = corr_exposure(
        {"a": 1.0, "b": 2.0, "c": 4.0, "d": 8.0},
        {"a": 8.0, "b": 4.0, "c": 2.0, "d": 3.0},
    )

    assert exposure == pytest.approx(-0.6448409113439546, abs=1e-12)


@pytest.mark.parametrize(
    ("series_by_symbol", "factor_by_symbol"),
    [
        ({"a": 1.0, "b": 2.0}, {"c": 3.0, "d": 4.0}),
        ({"a": 1.0}, {"a": 2.0}),
        ({"a": 1.0, "b": 1.0, "c": 1.0}, {"a": 2.0, "b": 3.0, "c": 4.0}),
        ({"a": 1.0, "b": 2.0, "c": 3.0}, {"a": 5.0, "b": 5.0, "c": 5.0}),
    ],
)
def test_corr_exposure_returns_zero_for_thin_or_constant_samples(
    series_by_symbol, factor_by_symbol
):
    assert corr_exposure(series_by_symbol, factor_by_symbol) == 0.0


def test_build_barra_lite_model_returns_factor_model_with_stable_ordering():
    symbol_returns = {
        "MSFT": (0.01, 0.02, -0.01, 0.03),
        "TSLA": (0.03, -0.02, 0.04, 0.01),
        "AAPL": (0.02, 0.01, 0.00, -0.01),
    }
    factor_exposures = {
        "size": {"AAPL": -0.2, "MSFT": 0.5},
        "beta": {"AAPL": 1.1, "MSFT": 0.8, "TSLA": 1.4},
    }

    model = build_barra_lite_model(
        symbol_returns,
        factor_exposures,
        factor_cov=((0.04, 0.005), (0.005, 0.03)),
    )

    assert isinstance(model, FactorRiskModel)
    assert model.factor_ids == ("beta", "size")
    assert tuple(model.loadings) == ("AAPL", "MSFT", "TSLA")
    assert model.loadings["AAPL"] == pytest.approx((1.1, -0.2))
    assert model.loadings["MSFT"] == pytest.approx((0.8, 0.5))
    assert model.loadings["TSLA"] == pytest.approx((1.4, 0.0))
    assert set(model.specific_var) == {"AAPL", "MSFT", "TSLA"}
    assert all(value > 0.0 for value in model.specific_var.values())


def test_build_barra_lite_model_default_covariance_is_stable_and_positive():
    model = build_barra_lite_model(
        {"MSFT": (0.01, 0.02, -0.01), "AAPL": (0.02, 0.01, 0.00)},
        {"size": {"AAPL": -0.2}, "beta": {"AAPL": 1.1, "MSFT": 0.8}},
    )

    assert model.factor_ids == ("beta", "size")
    assert model.loadings["MSFT"] == pytest.approx((0.8, 0.0))
    assert len(model.factor_cov) == 2
    assert all(model.factor_cov[i][i] > 0.0 for i in range(2))
    assert all(value > 0.0 for value in model.specific_var.values())


def test_build_barra_lite_model_rejects_invalid_covariance():
    with pytest.raises(ValueError):
        build_barra_lite_model(
            {"AAPL": (0.01, 0.02), "MSFT": (0.02, 0.01)},
            {"beta": {"AAPL": 1.0, "MSFT": 0.9}, "size": {"AAPL": -0.2}},
            factor_cov=((0.04,),),
        )


def test_barra_lite_report_has_finite_additive_risk_decomposition():
    report = barra_lite_report({"AAPL": 0.5, "MSFT": -0.25, "TSLA": 0.1}, _risk_model())

    assert {
        "total_risk",
        "factor_risk",
        "specific_risk",
        "factor_contributions",
        "top_exposures",
    } <= set(report)
    assert math.isfinite(report["total_risk"])
    assert math.isfinite(report["factor_risk"])
    assert math.isfinite(report["specific_risk"])
    assert report["total_risk"] == pytest.approx(
        report["factor_risk"] + report["specific_risk"], abs=1e-12
    )
    assert sum(report["factor_contributions"].values()) == pytest.approx(
        report["factor_risk"], abs=1e-12
    )
    assert report["top_exposures"][0]["factor"] == "beta"
    assert report["top_exposures"][0]["exposure"] == pytest.approx(0.5)


def test_barra_lite_outputs_are_deterministic_for_identical_inputs():
    symbol_returns = {
        "MSFT": (0.01, 0.02, -0.01, 0.03),
        "TSLA": (0.03, -0.02, 0.04, 0.01),
        "AAPL": (0.02, 0.01, 0.00, -0.01),
    }
    factor_exposures = {
        "size": {"AAPL": -0.2, "MSFT": 0.5},
        "beta": {"AAPL": 1.1, "MSFT": 0.8, "TSLA": 1.4},
    }
    weights = {"AAPL": 0.5, "MSFT": -0.25, "TSLA": 0.1}

    first = build_barra_lite_model(symbol_returns, factor_exposures)
    second = build_barra_lite_model(symbol_returns, factor_exposures)

    assert first.model_dump() == second.model_dump()
    assert barra_lite_report(weights, first) == barra_lite_report(weights, second)
