import pytest

from live.execution.gate_context import build_recent_order_gate_inputs
from services.risk.pre_trade.context import order_fingerprint


_NOW = 1_700_000_000_000
_HOUR_MS = 3_600_000
_UNIT_SCALE = 1_000_000


def _order(client_id, instrument, side, quantity, submitted_at, status, *,
           strategy="crowd_contra", limit_price=None):
    return {
        "client_order_id": client_id,
        "instrument_id": instrument,
        "side": side,
        "quantity": quantity,
        "submitted_at": submitted_at,
        "status": status,
        "strategy_id": strategy,
        "limit_price": limit_price,
    }


def test_recent_order_inputs_share_one_windowed_ledger_snapshot():
    orders = [
        _order("old", "XRP", "BUY", 100.0, _NOW - 25 * _HOUR_MS, "FILLED"),
        _order("market", "BTC", "BUY", 0.01, _NOW - 60_000, "FILLED"),
        _order("limit", "ETH", "SELL", 0.25, _NOW - 120_000, "CANCELED",
               limit_price=3000.0),
    ]

    recent = build_recent_order_gate_inputs(
        orders, now=_NOW, rate_window_ms=24 * _HOUR_MS,
        duplicate_window_ms=5 * 60_000, unit_scale=_UNIT_SCALE,
    )

    assert recent.timestamps == (_NOW - 60_000, _NOW - 120_000)
    assert recent.statuses == ("FILLED", "CANCELED")
    assert order_fingerprint(
        instrument_id="BTC", side="BUY", quantity=10_000,
        limit_price=None, strategy_id="crowd_contra",
    ) in recent.fingerprints
    # The gate screens a market intent before limit pricing. Retain both forms so
    # a persisted limit child still blocks the equivalent pre-pricing retry.
    assert order_fingerprint(
        instrument_id="ETH", side="SELL", quantity=250_000,
        limit_price=None, strategy_id="crowd_contra",
    ) in recent.fingerprints
    assert order_fingerprint(
        instrument_id="ETH", side="SELL", quantity=250_000,
        limit_price=3000.0, strategy_id="crowd_contra",
    ) in recent.fingerprints


def test_recent_order_inputs_reject_future_ledger_timestamps():
    orders = [_order("future", "BTC", "BUY", 0.01, _NOW + 1, "NEW")]
    with pytest.raises(ValueError, match="future submitted_at"):
        build_recent_order_gate_inputs(
            orders, now=_NOW, rate_window_ms=24 * _HOUR_MS,
            duplicate_window_ms=5 * 60_000, unit_scale=_UNIT_SCALE,
        )


def test_recent_order_inputs_reject_duplicate_window_larger_than_rate_window():
    with pytest.raises(ValueError, match="duplicate_window_ms"):
        build_recent_order_gate_inputs(
            [], now=_NOW, rate_window_ms=60_000,
            duplicate_window_ms=120_000, unit_scale=_UNIT_SCALE,
        )
