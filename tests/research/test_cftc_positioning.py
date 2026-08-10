import pytest

from services.research import cftc_positioning as cp


DAY_MS = 86_400_000


def test_trailing_zscore_never_reads_future_observations():
    values = [float(i) for i in range(60)]
    original = cp.trailing_zscores(values, window=10)
    mutated = cp.trailing_zscores(values + [1_000_000.0], window=10)

    assert original == mutated[:-1]
    assert original[:9] == [None] * 9
    assert original[9] is not None


def test_align_forward_returns_enters_strictly_after_knowledge_time():
    prices = [
        (1 * DAY_MS, 100.0),
        (2 * DAY_MS, 999.0),
        (3 * DAY_MS, 110.0),
        (10 * DAY_MS, 121.0),
    ]
    observations = [(2 * DAY_MS, 1.0)]

    aligned = cp.align_forward_returns(observations, prices, horizon_days=7)

    assert aligned[0]["entry_time_ms"] == 3 * DAY_MS
    assert aligned[0]["asset_return"] == pytest.approx(0.10)


def test_relative_forward_returns_use_eth_over_btc_ratio():
    observations = [(2 * DAY_MS, -1.0)]
    eth = [(3 * DAY_MS, 100.0), (10 * DAY_MS, 120.0)]
    btc = [(3 * DAY_MS, 200.0), (10 * DAY_MS, 220.0)]

    aligned = cp.align_forward_returns(
        observations, eth, benchmark_prices=btc, horizon_days=7,
    )

    assert aligned[0]["asset_return"] == pytest.approx(1.2 / 1.1 - 1.0)


def test_evaluate_positions_charges_turnover_and_reports_drawdown():
    aligned = [
        {"entry_time_ms": (i + 1) * DAY_MS,
         "signal": signal, "asset_return": ret}
        for i, (signal, ret) in enumerate(((1.0, 0.02), (-1.0, -0.01), (1.0, -0.03)))
    ]

    result = cp.evaluate_positions(aligned, cost_bps=10.0, periods_per_year=52)

    assert result["n_periods"] == 3
    assert result["turnover"] == pytest.approx(5.0)
    assert result["total_cost"] == pytest.approx(0.005)
    assert result["max_drawdown"] > 0
    assert result["net_returns"] == pytest.approx([0.019, 0.008, -0.032])

