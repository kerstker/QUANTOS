from dataclasses import FrozenInstanceError

import pytest

from services.data.hourly_market import (
    HourlyBar,
    HourlyFunding,
    HourlyMarketPanel,
    HourlyOpenInterest,
    aggregate_bars,
    validate_panel,
)


def test_aggregate_bars_uses_event_time_and_latest_knowledge_time():
    rows = (
        HourlyBar("BTCUSDT", 0, 10, 10, 12, 9, 11, 100),
        HourlyBar("BTCUSDT", 300, 310, 11, 13, 10, 12, 120),
    )
    result = aggregate_bars(rows, interval_seconds=3600)
    assert result == (HourlyBar("BTCUSDT", 0, 310, 10, 13, 9, 12, 220),)


def test_aggregate_bars_rejects_future_knowledge():
    with pytest.raises(ValueError, match="knowledge_time"):
        HourlyBar("BTCUSDT", 3600, 3599, 10, 11, 9, 10, 1)


def test_aggregate_bars_separates_instruments_and_buckets_deterministically():
    rows = (
        HourlyBar("ETH", 3601, 3610, 2, 4, 1, 3, 5),
        HourlyBar("BTC", 3600, 3602, 10, 12, 9, 11, 7),
        HourlyBar("ETH", 7200, 7210, 3, 5, 2, 4, 6),
        HourlyBar("BTC", 7201, 7209, 11, 13, 10, 12, 8),
    )
    assert aggregate_bars(rows, interval_seconds=3600) == (
        HourlyBar("BTC", 3600, 3602, 10, 12, 9, 11, 7),
        HourlyBar("BTC", 7200, 7209, 11, 13, 10, 12, 8),
        HourlyBar("ETH", 3600, 3610, 2, 4, 1, 3, 5),
        HourlyBar("ETH", 7200, 7210, 3, 5, 2, 4, 6),
    )


def test_funding_oi_panel_and_validation():
    bars = (HourlyBar("BTC", 0, 1, 10, 11, 9, 10, 2),)
    funding = (HourlyFunding("BTC", 0, 1, -0.001),)
    oi = (HourlyOpenInterest("BTC", 0, 1, 100.0),)
    panel = HourlyMarketPanel("hourly-market-v1", 10, bars, funding, oi, "digest")
    validate_panel(panel)
    assert panel.bars is bars
    with pytest.raises(FrozenInstanceError):
        panel.as_of = 11


@pytest.mark.parametrize("factory", [
    lambda: HourlyBar("B", 0, 0, float("nan"), 2, 1, 1.5, 1),
    lambda: HourlyBar("B", 0, 0, 2, 1, 1, 1.5, 1),
    lambda: HourlyFunding("B", 0, 0, float("inf")),
    lambda: HourlyOpenInterest("B", 0, 0, -1),
])
def test_invalid_numeric_rejected(factory):
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize("factory", [
    lambda: HourlyBar("", 0, 0, 1, 1, 1, 1, 0),
    lambda: HourlyBar("B", True, 0, 1, 1, 1, 1, 0),
    lambda: HourlyBar("B", -1, 0, 1, 1, 1, 1, 0),
    lambda: HourlyFunding("B", 1, 0, 0),
])
def test_invalid_identity_and_times_rejected(factory):
    with pytest.raises(ValueError):
        factory()


def test_invalid_order_and_interval_rejected():
    rows = (HourlyBar("B", 10, 10, 1, 1, 1, 1, 1), HourlyBar("B", 10, 10, 1, 1, 1, 1, 1))
    with pytest.raises(ValueError, match="strictly"):
        aggregate_bars(rows, interval_seconds=1)
    with pytest.raises(ValueError, match="interval_seconds"):
        aggregate_bars((rows[0],), interval_seconds=True)


def test_panel_direct_construction_rejects_invalid_bar_element():
    with pytest.raises(ValueError, match="bars must contain HourlyBar records"):
        HourlyMarketPanel("hourly-market-v1", 0, (object(),), (), (), "digest")


def test_panel_direct_construction_rejects_invalid_funding_element():
    with pytest.raises(ValueError, match="funding must contain HourlyFunding records"):
        HourlyMarketPanel("hourly-market-v1", 0, (), (object(),), (), "digest")


def test_panel_direct_construction_rejects_invalid_open_interest_element():
    with pytest.raises(ValueError, match="open_interest must contain HourlyOpenInterest records"):
        HourlyMarketPanel("hourly-market-v1", 0, (), (), (object(),), "digest")


def test_panel_direct_construction_enforces_core_invariants():
    bar = HourlyBar("B", 10, 10, 1, 1, 1, 1, 1)
    with pytest.raises(ValueError, match="schema_version"):
        HourlyMarketPanel("wrong", 10, (bar,), (), (), "digest")
    with pytest.raises(ValueError, match="as_of"):
        HourlyMarketPanel("hourly-market-v1", 9, (bar,), (), (), "digest")
    duplicate = (bar, bar)
    with pytest.raises(ValueError, match="strictly"):
        HourlyMarketPanel("hourly-market-v1", 10, duplicate, (), (), "digest")


def test_panel_allows_sparse_auxiliary_series():
    bars = (
        HourlyBar("BTC", 0, 0, 10, 11, 9, 10, 1),
        HourlyBar("BTC", 3600, 3600, 10, 11, 9, 10, 1),
        HourlyBar("ETH", 0, 0, 2, 3, 1, 2, 1),
    )
    funding = (HourlyFunding("BTC", 3600, 3600, 0.001),)
    open_interest = (HourlyOpenInterest("ETH", 7200, 7200, 100),)
    panel = HourlyMarketPanel("hourly-market-v1", 7200, bars, funding, open_interest, "digest")
    validate_panel(panel)


def test_panel_rejects_future_records():
    bar = HourlyBar("B", 0, 1, 1, 1, 1, 1, 1)
    with pytest.raises(ValueError, match="as_of"):
        HourlyMarketPanel("hourly-market-v1", 0, (bar,), (), (), "d")


def test_panel_schema_and_digest_bound():
    with pytest.raises(ValueError):
        HourlyMarketPanel("other", 1, (), (), (), "digest")


def test_blank_instrument_and_digest_rejected_directly():
    with pytest.raises(ValueError):
        HourlyBar("   ", 0, 0, 1, 1, 1, 1, 0)
    with pytest.raises(ValueError):
        HourlyMarketPanel("hourly-market-v1", 0, (), (), (), "   ")
