from __future__ import annotations

from decimal import Decimal

import pytest

from services.research.market_resilience import (
    MarketResilienceError,
    build_market_resilience_snapshot,
)


_NOW = 1_800_000_000_000
_RAW_HASH = "a" * 64


def _event(
    *,
    event_id: str,
    channel: str,
    event_time_ms: int,
    received_at_ms: int,
    payload: dict,
) -> dict:
    return {
        "event_id": event_id,
        "venue": "bybit_linear",
        "channel": channel,
        "instrument_id": "BTCUSDT",
        "event_time_ms": event_time_ms,
        "received_at_ms": received_at_ms,
        "processed_at_ms": _NOW + 20,
        "source_sequence": event_id[:8],
        "payload": payload,
    }


def test_snapshot_measures_quote_depth_signed_flow_and_three_clocks():
    book = _event(
        event_id="1" * 64,
        channel="orderbook_l2",
        event_time_ms=_NOW,
        received_at_ms=_NOW + 5,
        payload={"update_count": 3, "delete_count": 2},
    )
    trades = _event(
        event_id="2" * 64,
        channel="public_trade",
        event_time_ms=_NOW + 2,
        received_at_ms=_NOW + 8,
        payload={
            "trades": [
                {
                    "trade_id": "buy-1",
                    "taker_side": "Buy",
                    "price": "100.5",
                    "quantity": "2",
                    "block_trade": True,
                    "rpi_trade": False,
                },
                {
                    "trade_id": "sell-1",
                    "taker_side": "Sell",
                    "price": "100",
                    "quantity": "1",
                    "block_trade": False,
                    "rpi_trade": True,
                },
            ]
        },
    )

    snapshot = build_market_resilience_snapshot(
        venue="bybit_linear",
        instrument_id="BTCUSDT",
        segment_id="b" * 64,
        bids={
            Decimal("100"): Decimal("2"),
            Decimal("99.9"): Decimal("3"),
            Decimal("99"): Decimal("100"),
        },
        asks={
            Decimal("101"): Decimal("4"),
            Decimal("101.1"): Decimal("5"),
            Decimal("102"): Decimal("100"),
        },
        book_update_id=12,
        book_cross_sequence=103,
        book_event_time_ms=_NOW,
        book_received_at_ms=_NOW + 5,
        source_events=(book, trades),
        processed_at_ms=_NOW + 20,
        raw_authority_head_seq=44,
        raw_authority_head_hash=_RAW_HASH,
    )

    assert snapshot["source_event_time_ms"] == _NOW + 2
    assert snapshot["received_at_ms"] == _NOW + 8
    assert snapshot["processed_at_ms"] == _NOW + 20
    assert snapshot["event_to_receive_ms"] == 6
    assert snapshot["receive_to_process_ms"] == 12
    assert snapshot["best_bid"] == "100"
    assert snapshot["best_ask"] == "101"
    assert snapshot["mid_price"] == "100.5"
    assert snapshot["spread_bps"] == "99.5024875621890547263681592"
    assert snapshot["bid_depth_usd_5bps"] == "200"
    assert snapshot["ask_depth_usd_5bps"] == "404"
    assert snapshot["bid_depth_usd_20bps"] == "499.7"
    assert snapshot["ask_depth_usd_20bps"] == "909.5"
    assert snapshot["buy_taker_notional"] == "201"
    assert snapshot["sell_taker_notional"] == "100"
    assert snapshot["signed_taker_notional"] == "101"
    assert snapshot["taker_imbalance"] == str(Decimal(101) / Decimal(301))
    assert snapshot["trade_count"] == 2
    assert snapshot["block_trade_count"] == 1
    assert snapshot["rpi_trade_count"] == 1
    assert snapshot["book_update_count"] == 3
    assert snapshot["level_delete_count"] == 2
    assert snapshot["cancel_rate_inferred"] is False
    assert snapshot["source_event_ids"] == ["1" * 64, "2" * 64]
    assert snapshot["raw_authority_head"] == {
        "head_seq": 44,
        "head_hash": _RAW_HASH,
    }
    serialized = str(snapshot).lower()
    assert "future_return" not in serialized
    assert "label" not in serialized
    assert "cancel_rate\":" not in serialized


def test_snapshot_requires_uncrossed_book_and_current_source_events():
    kwargs = {
        "venue": "bybit_linear",
        "instrument_id": "BTCUSDT",
        "segment_id": "b" * 64,
        "bids": {Decimal("100"): Decimal("1")},
        "asks": {Decimal("101"): Decimal("1")},
        "book_update_id": 12,
        "book_cross_sequence": 103,
        "book_event_time_ms": _NOW,
        "book_received_at_ms": _NOW + 5,
        "source_events": (),
        "processed_at_ms": _NOW + 20,
        "raw_authority_head_seq": 44,
        "raw_authority_head_hash": _RAW_HASH,
    }
    with pytest.raises(MarketResilienceError, match="source event"):
        build_market_resilience_snapshot(**kwargs)
    kwargs["source_events"] = (
        _event(
            event_id="1" * 64,
            channel="ticker",
            event_time_ms=_NOW,
            received_at_ms=_NOW + 5,
            payload={},
        ),
    )
    with pytest.raises(MarketResilienceError, match="book or trade"):
        build_market_resilience_snapshot(**kwargs)
    kwargs["source_events"] = (
        _event(
            event_id="1" * 64,
            channel="orderbook_l2",
            event_time_ms=_NOW,
            received_at_ms=_NOW + 5,
            payload={"update_count": 1, "delete_count": 0},
        ),
    )
    kwargs["asks"] = {Decimal("99"): Decimal("1")}
    with pytest.raises(MarketResilienceError, match="crossed"):
        build_market_resilience_snapshot(**kwargs)


def test_level_deletes_never_become_cancel_rate():
    snapshot = build_market_resilience_snapshot(
        venue="bybit_linear",
        instrument_id="BTCUSDT",
        segment_id="b" * 64,
        bids={Decimal("100"): Decimal("1")},
        asks={Decimal("101"): Decimal("1")},
        book_update_id=12,
        book_cross_sequence=103,
        book_event_time_ms=_NOW,
        book_received_at_ms=_NOW + 5,
        source_events=(
            _event(
                event_id="1" * 64,
                channel="orderbook_l2",
                event_time_ms=_NOW,
                received_at_ms=_NOW + 5,
                payload={"update_count": 0, "delete_count": 99},
            ),
        ),
        processed_at_ms=_NOW + 20,
        raw_authority_head_seq=44,
        raw_authority_head_hash=_RAW_HASH,
    )
    assert snapshot["level_delete_count"] == 99
    assert snapshot["cancel_rate_inferred"] is False
    assert "cancel_rate" not in {
        key for key in snapshot if key != "cancel_rate_inferred"
    }
