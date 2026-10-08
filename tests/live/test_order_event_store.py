from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from live.execution.order_event_store import (
    OrderEventConflict,
    OrderEventError,
    OrderEventSource,
    SqliteOrderEventStore,
    create_order_event,
)
from quantos.contracts.trading import OrderSide


def _event(
    *,
    venue_event_id: str = "command-1",
    source_sequence: int = 0,
    venue_status: str = "PENDING_NEW",
    cumulative_filled_qty: float = 0.0,
    avg_fill_price: float = 0.0,
):
    return create_order_event(
        source=OrderEventSource.COMMAND,
        source_sequence=source_sequence,
        venue_event_id=venue_event_id,
        client_order_id="cid-1",
        venue_order_id="",
        instrument_id="BTC",
        side=OrderSide.BUY,
        order_quantity=1.0,
        strategy_id="hourly-paper",
        order_type="MARKET",
        limit_price=None,
        venue_status=venue_status,
        cumulative_filled_qty=cumulative_filled_qty,
        avg_fill_price=avg_fill_price,
        observed_at_ms=1_000,
        venue_payload_hash="1" * 64,
    )


def test_append_is_idempotent_and_same_source_event_conflict_is_loud(tmp_path) -> None:
    store = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)
    event = _event()

    first = store.append(event)
    replay = store.append(event)

    assert first == replay
    assert store.row_count() == 1
    conflicting = _event(venue_status="NEW")
    with pytest.raises(OrderEventConflict, match="venue event"):
        store.append(conflicting)
    assert store.row_count() == 1
    store.close()


def test_source_sequence_must_advance_for_each_order_and_source(tmp_path) -> None:
    store = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)
    store.append(_event(venue_event_id="command-2", source_sequence=2))

    with pytest.raises(OrderEventConflict, match="source sequence"):
        store.append(_event(venue_event_id="command-1", source_sequence=1))

    assert store.row_count() == 1
    store.close()


def test_unknown_venue_status_and_nonfinite_fill_never_enter_journal(tmp_path) -> None:
    store = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)

    with pytest.raises(ValueError, match="unknown venue order status"):
        _event(venue_status="NEW_AND_UNREVIEWED")
    with pytest.raises(ValueError, match="cumulative_filled_qty"):
        _event(cumulative_filled_qty=float("nan"))

    assert store.row_count() == 0
    store.close()


def test_direct_dataclass_tampering_cannot_bypass_event_factory(tmp_path) -> None:
    store = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)
    tampered = replace(_event(), content_hash="f" * 64)

    with pytest.raises(OrderEventError, match="round-trip"):
        store.append(tampered)

    assert store.row_count() == 0
    store.close()


def test_store_is_append_only_hash_chained_and_detects_tampering(tmp_path) -> None:
    path = tmp_path / "events.db"
    store = SqliteOrderEventStore(str(path), create=True)
    store.append(_event())
    assert store.chain_valid()
    store.close()

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE live_order_events SET venue_status='FILLED' WHERE seq=1"
        )
    connection.rollback()
    connection.execute("DROP TRIGGER live_order_events_no_update")
    connection.execute(
        "UPDATE live_order_events SET event_hash=? WHERE seq=1", ("0" * 64,)
    )
    connection.commit()
    connection.close()

    with pytest.raises(OrderEventError, match="event hash"):
        SqliteOrderEventStore(str(path))
