from __future__ import annotations

import pytest

from services.data.public_market_event_store import PublicMarketEventStore
from tools import bybit_realtime_stream as stream


_NOW = 1_800_000_000_000


def _book(
    *,
    message_type: str = "snapshot",
    update_id: int = 10,
    sequence: int = 100,
    bids=None,
    asks=None,
):
    return {
        "topic": "orderbook.200.BTCUSDT",
        "type": message_type,
        "ts": _NOW - 4,
        "cts": _NOW - 5,
        "data": {
            "s": "BTCUSDT",
            "b": [["100", "2"]] if bids is None else bids,
            "a": [["101", "3"]] if asks is None else asks,
            "u": update_id,
            "seq": sequence,
        },
    }


def _trade():
    return {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": _NOW - 2,
        "data": [
            {
                "T": _NOW - 3,
                "s": "BTCUSDT",
                "S": "Buy",
                "v": "0.5",
                "p": "100",
                "L": "PlusTick",
                "i": "trade-1",
                "BT": False,
                "RPI": True,
                "seq": 101,
            },
            {
                "T": _NOW - 2,
                "s": "BTCUSDT",
                "S": "Sell",
                "v": "0.25",
                "p": "104",
                "L": "MinusTick",
                "i": "trade-2",
                "BT": False,
                "seq": 102,
            },
        ],
    }


def _liquidation():
    return {
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": _NOW - 1,
        "data": [
            {
                "T": _NOW - 2,
                "s": "BTCUSDT",
                "S": "Buy",
                "v": "4",
                "p": "90",
            }
        ],
    }


def _ticker(*, message_type="snapshot", cs=200, data=None):
    return {
        "topic": "tickers.BTCUSDT",
        "type": message_type,
        "ts": _NOW - 1,
        "cs": cs,
        "data": {
            "symbol": "BTCUSDT",
            "lastPrice": "100",
            "markPrice": "99.9",
            "indexPrice": "99.8",
            "openInterest": "1000",
            "fundingRate": "0.0001",
            **(data or {}),
        },
    }


def test_subscription_is_public_linear_and_contains_four_mechanism_topics():
    topics = stream.subscription_topics(("BTCUSDT", "ETHUSDT"), depth=200)
    assert len(topics) == 8
    assert topics[:4] == (
        "orderbook.200.BTCUSDT",
        "publicTrade.BTCUSDT",
        "allLiquidation.BTCUSDT",
        "tickers.BTCUSDT",
    )
    request = stream.subscribe_request(("BTCUSDT",), depth=50)
    assert request["op"] == "subscribe"
    encoded = str(request).lower()
    assert "private" not in encoded
    assert "order." not in encoded
    assert stream.PUBLIC_LINEAR_URL == "wss://stream.bybit.com/v5/public/linear"


@pytest.mark.parametrize(
    "symbols,depth",
    [
        ((), 200),
        (("BTCUSDT", "BTCUSDT"), 200),
        (("BTCUSD",), 200),
        (("BTCUSDT",), 100),
    ],
)
def test_subscription_rejects_unreviewed_shapes(symbols, depth):
    with pytest.raises(ValueError):
        stream.subscription_topics(symbols, depth=depth)


def test_snapshot_and_delta_reconstruct_book_and_measure_deletes():
    state = stream.BybitLinearState(("BTCUSDT",))
    assert state.handle(_book(), received_at_ms=_NOW) is True
    delta = _book(
        message_type="delta",
        update_id=11,
        sequence=101,
        bids=[["100", "0"], ["99", "5"]],
        asks=[["101", "4"]],
    )
    assert state.handle(delta, received_at_ms=_NOW + 10) is True
    events = state.drain(processed_at_ms=_NOW + 20)
    assert len(events) == 2
    assert events[0]["channel"] == "orderbook_l2"
    assert events[0]["event_time_ms"] == _NOW - 5
    assert events[0]["received_at_ms"] == _NOW
    assert events[0]["processed_at_ms"] == _NOW + 20
    assert events[1]["payload"]["delete_count"] == 1
    assert events[1]["payload"]["update_count"] == 2
    latest = state.latest["BTCUSDT"]["book"]
    assert latest["best_bid"] == 99.0
    assert latest["best_ask"] == 101.0
    assert latest["update_id"] == 11


def test_new_snapshot_resets_book_and_update_one_is_allowed():
    state = stream.BybitLinearState(("BTCUSDT",))
    state.handle(_book(update_id=99, sequence=999), received_at_ms=_NOW)
    reset = _book(update_id=1, sequence=1, bids=[["98", "1"]], asks=[["102", "1"]])
    state.handle(reset, received_at_ms=_NOW + 1)
    assert state.latest["BTCUSDT"]["book"]["best_bid"] == 98.0


def test_delta_before_snapshot_and_nonincreasing_sequence_fail_closed():
    state = stream.BybitLinearState(("BTCUSDT",))
    with pytest.raises(stream.BybitRealtimeStreamError, match="before snapshot"):
        state.handle(_book(message_type="delta"), received_at_ms=_NOW)
    state.handle(_book(), received_at_ms=_NOW)
    with pytest.raises(stream.BybitRealtimeStreamError, match="not increasing"):
        state.handle(
            _book(message_type="delta", update_id=10, sequence=101),
            received_at_ms=_NOW + 1,
        )


def test_crossed_or_empty_book_is_rejected_without_pending_event():
    state = stream.BybitLinearState(("BTCUSDT",))
    with pytest.raises(stream.BybitRealtimeStreamError, match="crossed or empty"):
        state.handle(_book(asks=[["99", "1"]]), received_at_ms=_NOW)
    assert state.pending_count == 0


def test_trade_message_retains_each_print_and_taker_side():
    state = stream.BybitLinearState(("BTCUSDT",))
    state.handle(_trade(), received_at_ms=_NOW)
    event = state.drain(processed_at_ms=_NOW + 1)[0]
    assert event["channel"] == "public_trade"
    assert event["event_time_ms"] == _NOW - 2
    assert [row["trade_id"] for row in event["payload"]["trades"]] == [
        "trade-1",
        "trade-2",
    ]
    assert event["payload"]["buy_taker_notional"] == 50.0
    assert event["payload"]["sell_taker_notional"] == 26.0
    assert event["payload"]["trades"][0]["rpi_trade"] is True


def test_liquidation_message_names_liquidated_position_side():
    state = stream.BybitLinearState(("BTCUSDT",))
    state.handle(_liquidation(), received_at_ms=_NOW)
    event = state.drain(processed_at_ms=_NOW + 1)[0]
    assert event["channel"] == "all_liquidation"
    assert event["payload"]["liquidations"] == [
        {
            "event_time_ms": _NOW - 2,
            "liquidated_position_side": "Buy",
            "bankruptcy_price": "90",
            "quantity": "4",
        }
    ]


def test_ticker_delta_requires_snapshot_and_retains_financing_state():
    state = stream.BybitLinearState(("BTCUSDT",))
    with pytest.raises(stream.BybitRealtimeStreamError, match="before snapshot"):
        state.handle(
            _ticker(message_type="delta", data={"lastPrice": "101"}),
            received_at_ms=_NOW,
        )
    state.handle(_ticker(), received_at_ms=_NOW)
    state.handle(
        _ticker(message_type="delta", cs=201, data={"lastPrice": "101"}),
        received_at_ms=_NOW + 1,
    )
    assert state.latest["BTCUSDT"]["ticker"]["last_price"] == "101"
    assert state.latest["BTCUSDT"]["ticker"]["open_interest"] == "1000"


def test_future_frames_unsubscribed_topics_and_failed_control_are_rejected():
    state = stream.BybitLinearState(("BTCUSDT",))
    future = _trade()
    future["ts"] = _NOW + stream.MAX_FUTURE_SKEW_MS + 1
    with pytest.raises(stream.BybitRealtimeStreamError, match="future-dated"):
        state.handle(future, received_at_ms=_NOW)
    wrong = _trade()
    wrong["topic"] = "publicTrade.ETHUSDT"
    with pytest.raises(stream.BybitRealtimeStreamError, match="not subscribed"):
        state.handle(wrong, received_at_ms=_NOW)
    with pytest.raises(stream.BybitRealtimeStreamError, match="reports failure"):
        state.handle({"op": "subscribe", "success": False}, received_at_ms=_NOW)
    assert state.pending_count == 0


def test_drain_refuses_processing_clock_before_receive():
    state = stream.BybitLinearState(("BTCUSDT",))
    state.handle(_trade(), received_at_ms=_NOW)
    with pytest.raises(stream.BybitRealtimeStreamError, match="precedes"):
        state.drain(processed_at_ms=_NOW - 1)
    assert state.pending_count == 1


def test_status_is_honest_public_only_and_reports_three_clock_lag():
    state = stream.BybitLinearState(("BTCUSDT",))
    state.handle(_trade(), received_at_ms=_NOW)
    state.drain(processed_at_ms=_NOW + 1)
    status = state.status(
        generated_at_ms=_NOW + 100,
        connected=True,
        reconnects=2,
        store_head={"head_seq": 1, "head_hash": "a" * 64},
    )
    assert status["verdict"] == "PASS"
    assert status["lag_ms"] == 100
    assert status["event_counts"]["public_trade"] == 1
    assert status["safety"] == {
        "public_market_data_only": True,
        "credentials": False,
        "private_stream": False,
        "broker_connectivity": False,
        "order_submission": False,
        "live_trading": False,
    }
    stopped = state.status(
        generated_at_ms=_NOW + 101,
        connected=False,
        reconnects=2,
        store_head={"head_seq": 1, "head_hash": "a" * 64},
    )
    assert stopped["verdict"] == "COLLECTED"
    assert stopped["streaming"] is False


def test_chain_checkpoint_starts_full_then_verifies_only_suffix(tmp_path):
    store_path = tmp_path / "events.db"
    checkpoint_path = tmp_path / "chain.json"
    store = PublicMarketEventStore(str(store_path), create=True)
    state = stream.BybitLinearState(("BTCUSDT",))
    state.handle(_trade(), received_at_ms=_NOW)
    store.append_events(state.drain(processed_at_ms=_NOW + 1))

    first = stream._initial_chain_checkpoint(
        store,
        store_path=str(store_path),
        checkpoint_path=str(checkpoint_path),
        now_ms=_NOW + 2,
    )
    assert first["verification_mode"] == "FULL_FROM_GENESIS"
    assert first["verified_suffix_events"] == 1
    assert first["verified_seq"] == 1

    state.handle(_ticker(), received_at_ms=_NOW + 3)
    store.append_events(state.drain(processed_at_ms=_NOW + 4))
    second = stream._initial_chain_checkpoint(
        store,
        store_path=str(store_path),
        checkpoint_path=str(checkpoint_path),
        now_ms=_NOW + 5,
    )
    assert second["verification_mode"] == "INCREMENTAL_FROM_TRUSTED_LOCAL_CHECKPOINT"
    assert second["verified_suffix_events"] == 1
    assert second["verified_seq"] == 2
    assert second["full_verified_at_ms"] == _NOW + 2
    store.close()


def test_valid_but_wrong_chain_checkpoint_fails_closed(tmp_path):
    store_path = tmp_path / "events.db"
    checkpoint_path = tmp_path / "chain.json"
    store = PublicMarketEventStore(str(store_path), create=True)
    body = {
        "schema_version": 1,
        "store_path": str(store_path.resolve()),
        "full_verified_at_ms": _NOW,
        "verified_at_ms": _NOW,
        "verified_seq": 1,
        "verified_row_hash": "a" * 64,
    }
    checkpoint_path.write_text(
        stream._canonical({**body, "checkpoint_hash": stream._hash(body)}),
        encoding="utf-8",
    )
    with pytest.raises(stream.BybitRealtimeStreamError, match="verification failed"):
        stream._initial_chain_checkpoint(
            store,
            store_path=str(store_path),
            checkpoint_path=str(checkpoint_path),
            now_ms=_NOW + 1,
        )
    store.close()
