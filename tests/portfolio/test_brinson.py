import math

import pytest

from services.portfolio.brinson import (
    BrinsonAttributionReport,
    brinson_fachler_attribution,
)


def _group(report: BrinsonAttributionReport, name: str):
    return {row.group: row for row in report.groups}[name]


def test_brinson_identity_on_hand_computable_two_sector_example():
    report = brinson_fachler_attribution(
        portfolio_weights={"A": 0.4, "B": 0.2, "C": 0.3, "D": 0.1},
        benchmark_weights={"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25},
        asset_returns={"A": 0.10, "B": 0.02, "C": 0.04, "D": -0.01},
        groups={"A": "growth", "B": "growth", "C": "value", "D": "value"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.reasons == ()

    growth = _group(report, "growth")
    value = _group(report, "value")

    assert growth.portfolio_weight == pytest.approx(0.6)
    assert growth.benchmark_weight == pytest.approx(0.5)
    assert growth.portfolio_return == pytest.approx((0.4 * 0.10 + 0.2 * 0.02) / 0.6)
    assert growth.benchmark_return == pytest.approx((0.25 * 0.10 + 0.25 * 0.02) / 0.5)
    assert value.portfolio_weight == pytest.approx(0.4)
    assert value.benchmark_weight == pytest.approx(0.5)

    assert report.totals["portfolio_return"] == pytest.approx(0.055)
    assert report.totals["benchmark_return"] == pytest.approx(0.0375)
    assert report.totals["active_return"] == pytest.approx(0.0175)
    assert (
        report.totals["allocation_effect"]
        + report.totals["selection_effect"]
        + report.totals["interaction_effect"]
    ) == pytest.approx(report.totals["active_return"])
    assert report.diagnostics["identity_residual"] == pytest.approx(0.0, abs=1e-12)


def test_portfolio_equal_to_benchmark_has_zero_active_effects():
    weights = {"A": 0.5, "B": 0.5}
    report = brinson_fachler_attribution(
        portfolio_weights=weights,
        benchmark_weights=weights,
        asset_returns={"A": 0.08, "B": -0.02},
        groups={"A": "x", "B": "y"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.totals["active_return"] == pytest.approx(0.0)
    assert report.totals["allocation_effect"] == pytest.approx(0.0)
    assert report.totals["selection_effect"] == pytest.approx(0.0)
    assert report.totals["interaction_effect"] == pytest.approx(0.0)


def test_negative_active_weights_remain_additive_for_long_short_research_books():
    report = brinson_fachler_attribution(
        portfolio_weights={"A": 1.2, "B": -0.2},
        benchmark_weights={"A": 0.8, "B": 0.2},
        asset_returns={"A": 0.03, "B": -0.04},
        groups={"A": "major", "B": "minor"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.totals["active_return"] == pytest.approx(
        (1.2 * 0.03 - 0.2 * -0.04) - (0.8 * 0.03 + 0.2 * -0.04)
    )
    assert (
        report.totals["allocation_effect"]
        + report.totals["selection_effect"]
        + report.totals["interaction_effect"]
    ) == pytest.approx(report.totals["active_return"])


@pytest.mark.parametrize(
    ("portfolio_weights", "benchmark_weights", "asset_returns", "groups", "reason"),
    [
        ({"A": 0.5}, {"A": 0.5}, {"A": 0.01}, {"A": "x"}, "portfolio_weights_sum_not_one"),
        ({"A": 1.0}, {"A": 1.0}, {}, {"A": "x"}, "missing_asset_return"),
        ({"A": 1.0}, {"A": 1.0}, {"A": 0.01}, {}, "missing_asset_group"),
        ({"A": math.inf}, {"A": 1.0}, {"A": 0.01}, {"A": "x"}, "non_finite_weight"),
        ({"A": 1.0}, {"A": 1.0}, {"A": math.nan}, {"A": "x"}, "non_finite_return"),
    ],
)
def test_invalid_inputs_block(portfolio_weights, benchmark_weights, asset_returns, groups, reason):
    report = brinson_fachler_attribution(
        portfolio_weights=portfolio_weights,
        benchmark_weights=benchmark_weights,
        asset_returns=asset_returns,
        groups=groups,
    )

    assert report.status == "BLOCKED"
    assert reason in report.reasons
    assert report.groups == ()


def test_zero_weight_assets_may_omit_return_and_group():
    report = brinson_fachler_attribution(
        portfolio_weights={"A": 1.0, "Z": 0.0},
        benchmark_weights={"A": 1.0},
        asset_returns={"A": 0.02},
        groups={"A": "core"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.totals["active_return"] == pytest.approx(0.0)


def test_output_is_deterministic():
    kwargs = dict(
        portfolio_weights={"B": 0.4, "A": 0.6},
        benchmark_weights={"A": 0.5, "B": 0.5},
        asset_returns={"B": -0.01, "A": 0.04},
        groups={"B": "z", "A": "a"},
    )

    assert brinson_fachler_attribution(**kwargs) == brinson_fachler_attribution(**kwargs)
