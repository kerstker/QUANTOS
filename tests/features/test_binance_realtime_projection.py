import pytest

from services.features.binance_realtime_projection import (
    BinanceRealtimeProjectionError,
    FEATURE_IDS,
    project_snapshot,
)


def _snapshot() -> dict:
    return {
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "venue": "binance_usdm",
        "instrument_id": "BTCUSDT",
        "event_time_ms": 999,
        "observed_at_ms": 1_000,
        "quality": {"complete_core": True},
        "book": {"mid": 100.0, "spread_bps": 2.0},
        "depth20": {
            "bids": [[str(99 - index), str(index + 1)] for index in range(5)],
            "asks": [[str(101 + index), str(index + 2)] for index in range(5)],
        },
        "mark": {
            "mark_price": 101.0,
            "index_price": 100.0,
            "funding_rate": 0.0001,
        },
        "trade_window": {
            "buy_taker_notional": 75.0,
            "sell_taker_notional": 25.0,
        },
        "liquidation_window": {
            "long_liquidation_notional": 10.0,
            "short_liquidation_notional": 30.0,
        },
    }


def test_projects_complete_public_snapshot_with_pit_times():
    values = project_snapshot(_snapshot())
    assert tuple(value.feature_id for value in values) == FEATURE_IDS
    assert all(value.instrument_id == "BTCUSDT" for value in values)
    assert all(value.event_time == 999 for value in values)
    assert all(value.knowledge_time == 1_000 for value in values)
    by_id = {value.feature_id: value.value for value in values}
    assert by_id["rt_mid_price"] == 100.0
    assert by_id["rt_mark_basis_bps"] == pytest.approx(100.0)
    assert by_id["rt_taker_imbalance_5s"] == pytest.approx(0.5)
    assert by_id["rt_liquidation_imbalance_5s"] == pytest.approx(-0.5)
    bid_notional = sum((99 - index) * (index + 1) for index in range(5))
    ask_notional = sum((101 + index) * (index + 2) for index in range(5))
    assert by_id["rt_executable_depth_5_usd"] == pytest.approx(
        min(bid_notional, ask_notional)
    )


def test_projection_rejects_incomplete_or_future_source():
    snapshot = _snapshot()
    snapshot["quality"]["complete_core"] = False
    with pytest.raises(BinanceRealtimeProjectionError, match="incomplete"):
        project_snapshot(snapshot)

    snapshot = _snapshot()
    snapshot["event_time_ms"] = 1_001
    with pytest.raises(BinanceRealtimeProjectionError, match="PIT"):
        project_snapshot(snapshot)


def test_projection_uses_conservative_knowledge_time_for_bounded_clock_skew():
    snapshot = _snapshot()
    snapshot.update(
        {
            "event_time_ms": 1_010,
            "observed_at_ms": 1_000,
            "knowledge_time_ms": 1_010,
        }
    )
    snapshot["quality"].update(
        {
            "exchange_clock_skew_ms": 10,
            "future_skew_bound_ms": 30_000,
        }
    )

    values = project_snapshot(snapshot)

    assert all(value.event_time == 1_010 for value in values)
    assert all(value.knowledge_time == 1_010 for value in values)


def test_projection_rejects_unattested_or_oversized_clock_skew():
    snapshot = _snapshot()
    snapshot.update(
        {
            "event_time_ms": 1_010,
            "observed_at_ms": 1_000,
            "knowledge_time_ms": 1_010,
        }
    )
    with pytest.raises(BinanceRealtimeProjectionError, match="clock skew"):
        project_snapshot(snapshot)

    snapshot["quality"].update(
        {
            "exchange_clock_skew_ms": 10,
            "future_skew_bound_ms": 30_000,
        }
    )
    snapshot["event_time_ms"] = 31_001
    snapshot["knowledge_time_ms"] = 31_001
    snapshot["quality"]["exchange_clock_skew_ms"] = 30_001
    with pytest.raises(BinanceRealtimeProjectionError, match="clock skew"):
        project_snapshot(snapshot)
