from __future__ import annotations

import sqlite3

import pytest

from services.data.public_market_event_store import (
    PublicMarketEventStore,
    PublicMarketEventStoreError,
)


def _event(
    *,
    event_id: str = "a" * 64,
    event_time_ms: int = 1_000,
    received_at_ms: int = 1_100,
    processed_at_ms: int = 1_200,
) -> dict:
    return {
        "event_id": event_id,
        "venue": "bybit_linear",
        "channel": "public_trade",
        "instrument_id": "BTCUSDT",
        "event_time_ms": event_time_ms,
        "received_at_ms": received_at_ms,
        "processed_at_ms": processed_at_ms,
        "source_sequence": "trade-1",
        "payload": {"price": "100", "quantity": "2"},
    }


def test_append_batch_is_idempotent_and_hash_chained(tmp_path):
    path = tmp_path / "events.db"
    store = PublicMarketEventStore(str(path), create=True)
    first = store.append_events([_event()])
    assert first == {
        "inserted": 1,
        "duplicates": 0,
        "head_seq": 1,
        "head_hash": first["head_hash"],
    }
    assert len(first["head_hash"]) == 64

    duplicate = store.append_events([_event()])
    assert duplicate == {
        "inserted": 0,
        "duplicates": 1,
        "head_seq": 1,
        "head_hash": first["head_hash"],
    }
    second = store.append_events([_event(event_id="b" * 64, event_time_ms=2_000)])
    assert second["inserted"] == 1
    assert second["head_seq"] == 2
    assert store.verify_chain_from() == {
        "valid": True,
        "anchor_seq": 0,
        "verified_events": 2,
        "verified_seq": 2,
        "verified_row_hash": second["head_hash"],
    }
    rows = store.recent_events(2)
    assert [row["event_id"] for row in rows] == ["a" * 64, "b" * 64]
    assert rows[0]["event_time_ms"] == 1_000
    store.close()

    read_only = PublicMarketEventStore(str(path))
    assert read_only.head()["head_seq"] == 2
    read_only.close()


def test_event_id_collision_rolls_back_entire_batch(tmp_path):
    store = PublicMarketEventStore(str(tmp_path / "events.db"), create=True)
    store.append_events([_event()])
    changed = _event()
    changed["payload"] = {"price": "101"}
    with pytest.raises(PublicMarketEventStoreError, match="different content"):
        store.append_events([_event(event_id="b" * 64), changed])
    assert store.head()["head_seq"] == 1
    store.close()


@pytest.mark.parametrize(
    "change,match",
    [
        ({"event_time_ms": 40_001}, "clocks"),
        ({"received_at_ms": 1_300, "processed_at_ms": 1_200}, "clocks"),
        ({"received_at_ms": True}, "clocks"),
        ({"event_id": "not-a-hash"}, "event_id"),
        ({"venue": "BYBIT"}, "venue"),
        ({"channel": ""}, "channel"),
        ({"instrument_id": "btc/usdt"}, "instrument_id"),
        ({"payload": []}, "payload"),
    ],
)
def test_invalid_evidence_is_rejected_before_mutation(tmp_path, change, match):
    store = PublicMarketEventStore(str(tmp_path / "events.db"), create=True)
    event = _event()
    event.update(change)
    with pytest.raises(PublicMarketEventStoreError, match=match):
        store.append_events([event])
    assert store.head()["head_seq"] == 0
    store.close()


def test_append_only_triggers_and_tamper_detection(tmp_path):
    path = tmp_path / "events.db"
    store = PublicMarketEventStore(str(path), create=True)
    store.append_events([_event()])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM public_market_events")
    store._conn.rollback()
    store._conn.execute("DROP TRIGGER public_market_events_no_update")
    store._conn.execute(
        "UPDATE public_market_events SET payload_json='{}' WHERE seq=1"
    )
    store._conn.commit()
    assert store.verify_chain_from()["valid"] is False
    store.close()


def test_missing_store_fails_closed(tmp_path):
    with pytest.raises(PublicMarketEventStoreError, match="unreadable"):
        PublicMarketEventStore(str(tmp_path / "missing.db"))


def test_channel_reader_is_causal_filtered_and_payload_verified(tmp_path):
    store = PublicMarketEventStore(str(tmp_path / "events.db"), create=True)
    first = _event(
        event_id="a" * 64,
        event_time_ms=1_000,
        received_at_ms=1_100,
        processed_at_ms=1_200,
    )
    first["channel"] = "all_liquidation"
    second = _event(
        event_id="b" * 64,
        event_time_ms=2_000,
        received_at_ms=2_100,
        processed_at_ms=2_200,
    )
    second["channel"] = "all_liquidation"
    second["instrument_id"] = "ETHUSDT"
    store.append_events([second, first])

    assert [
        row["event_id"]
        for row in store.events_by_channel_after(
            "all_liquidation",
            after_processed_at_ms=1_200,
        )
    ] == ["b" * 64]
    assert [
        row["instrument_id"]
        for row in store.events_by_channel_after(
            "all_liquidation",
            after_processed_at_ms=0,
            instruments=("BTCUSDT",),
        )
    ] == ["BTCUSDT"]

    store._conn.execute("DROP TRIGGER public_market_events_no_update")
    store._conn.execute(
        "UPDATE public_market_events SET payload_json='{}' WHERE event_id=?",
        ("a" * 64,),
    )
    store._conn.commit()
    with pytest.raises(PublicMarketEventStoreError, match="payload hash"):
        store.events_by_channel_after(
            "all_liquidation", after_processed_at_ms=0
        )
    store.close()


@pytest.mark.parametrize(
    "channel,after,instruments",
    [
        ("BAD", 0, None),
        ("all_liquidation", True, None),
        ("all_liquidation", -1, None),
        ("all_liquidation", 0, ()),
        ("all_liquidation", 0, ("btc/usdt",)),
    ],
)
def test_channel_reader_rejects_unreviewed_query_shapes(
    tmp_path, channel, after, instruments
):
    store = PublicMarketEventStore(str(tmp_path / "events.db"), create=True)
    with pytest.raises(ValueError):
        store.events_by_channel_after(
            channel,
            after_processed_at_ms=after,
            instruments=instruments,
        )
    store.close()
