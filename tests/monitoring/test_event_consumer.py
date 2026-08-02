"""Durable at-least-once consumer offsets, leases, retries, and dead letters."""

import sqlite3

import pytest

from quantos.contracts.events import Event
from services.monitoring.event_bus import (
    DurableEventBus,
    EventBusIntegrityError,
    EventConsumerBusyError,
    EventDeadLetteredError,
    EventDeliveryError,
    EventDeliveryLostError,
)


def _event(event_id: str, timestamp: int) -> Event:
    return Event(
        event_id=event_id,
        event_type="research.test.event",
        timestamp=timestamp,
        source="test",
        domain="research",
        payload={"event_id": event_id},
    )


def test_acknowledged_offset_survives_restart(tmp_path):
    path = str(tmp_path / "events.db")
    bus = DurableEventBus(path)
    bus.publish(_event("e1", 1))
    bus.publish(_event("e2", 2))

    first = bus.claim_next(
        "projection",
        worker_id="worker-a",
        now_ms=100,
        lease_ms=1_000,
    )
    assert first is not None
    assert first.event.event_id == "e1"
    assert first.attempt == 1
    bus.ack_delivery(first, at_ms=101)
    assert bus.consumer_offset("projection") == first.event_seq
    bus.close()

    reopened = DurableEventBus(path)
    second = reopened.claim_next(
        "projection",
        worker_id="worker-b",
        now_ms=200,
        lease_ms=1_000,
    )
    assert second is not None
    assert second.event.event_id == "e2"
    assert second.attempt == 1


def test_active_lease_blocks_second_connection_and_expired_lease_can_be_reclaimed(tmp_path):
    path = str(tmp_path / "events.db")
    first_bus = DurableEventBus(path)
    second_bus = DurableEventBus(path)
    first_bus.publish(_event("e1", 1))

    first = first_bus.claim_next(
        "projection",
        worker_id="worker-a",
        now_ms=100,
        lease_ms=50,
    )
    assert first is not None
    with pytest.raises(EventConsumerBusyError):
        second_bus.claim_next(
            "projection",
            worker_id="worker-b",
            now_ms=149,
            lease_ms=50,
        )

    reclaimed = second_bus.claim_next(
        "projection",
        worker_id="worker-b",
        now_ms=151,
        lease_ms=50,
    )
    assert reclaimed is not None
    assert reclaimed.event.event_id == "e1"
    assert reclaimed.attempt == 2
    with pytest.raises(EventDeliveryLostError):
        first_bus.ack_delivery(first, at_ms=152)
    second_bus.ack_delivery(reclaimed, at_ms=153)


def test_failures_retry_then_dead_letter_and_advance_past_poison_event():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("poison", 1))
    bus.publish(_event("healthy", 2))

    for attempt in (1, 2):
        delivery = bus.claim_next(
            "projection",
            worker_id=f"worker-{attempt}",
            now_ms=attempt * 10,
            lease_ms=5,
        )
        assert delivery is not None and delivery.attempt == attempt
        with pytest.raises(EventDeliveryError) as raised:
            bus.fail_delivery(
                delivery,
                RuntimeError("secret-token-must-not-be-retained"),
                at_ms=attempt * 10 + 1,
                max_attempts=3,
            )
        assert not isinstance(raised.value, EventDeadLetteredError)
        assert bus.consumer_offset("projection") == 0

    final = bus.claim_next(
        "projection",
        worker_id="worker-3",
        now_ms=30,
        lease_ms=5,
    )
    assert final is not None and final.attempt == 3
    with pytest.raises(EventDeadLetteredError):
        bus.fail_delivery(
            final,
            RuntimeError("secret-token-must-not-be-retained"),
            at_ms=31,
            max_attempts=3,
        )

    letters = bus.dead_letters(consumer_id="projection")
    assert len(letters) == 1
    assert letters[0]["event"].event_id == "poison"
    assert letters[0]["attempt"] == 3
    assert letters[0]["error_type"] == "RuntimeError"
    assert len(letters[0]["error_fingerprint"]) == 64
    assert "secret-token" not in repr(letters)

    next_delivery = bus.claim_next(
        "projection",
        worker_id="worker-4",
        now_ms=40,
        lease_ms=5,
    )
    assert next_delivery is not None
    assert next_delivery.event.event_id == "healthy"


def test_delivery_cannot_be_acked_after_its_lease_expires_without_reclaim():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    delivery = bus.claim_next(
        "projection",
        worker_id="worker-a",
        now_ms=100,
        lease_ms=10,
    )
    assert delivery is not None
    with pytest.raises(EventDeliveryLostError):
        bus.ack_delivery(delivery, at_ms=111)
    assert bus.consumer_offset("projection") == 0


def test_delivery_cannot_be_acked_before_it_was_claimed():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    delivery = bus.claim_next(
        "projection",
        worker_id="worker-a",
        now_ms=100,
        lease_ms=10,
    )
    assert delivery is not None
    with pytest.raises(EventDeliveryLostError):
        bus.ack_delivery(delivery, at_ms=99)


@pytest.mark.parametrize("consumer_id", ["", " ", None, 7])
def test_invalid_consumer_identity_is_rejected(consumer_id):
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    with pytest.raises((TypeError, ValueError)):
        bus.claim_next(
            consumer_id,
            worker_id="worker-a",
            now_ms=1,
            lease_ms=1,
        )


def test_consumer_journal_is_append_only_and_tamper_breaks_future_claims():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    delivery = bus.claim_next(
        "projection",
        worker_id="worker-a",
        now_ms=1,
        lease_ms=10,
    )
    assert delivery is not None
    bus.ack_delivery(delivery, at_ms=2)

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        bus._c.execute("UPDATE event_consumer_journal SET worker_id='evil'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        bus._c.execute("DELETE FROM event_consumer_journal")

    bus._c.execute("DROP TRIGGER event_consumer_journal_no_update")
    bus._c.execute("UPDATE event_consumer_journal SET worker_id='evil' WHERE journal_seq=1")
    bus._c.commit()
    assert not bus.consumer_chain_valid()
    with pytest.raises(EventBusIntegrityError):
        bus.claim_next(
            "projection",
            worker_id="worker-b",
            now_ms=20,
            lease_ms=10,
        )


def test_consumer_chain_is_not_rescanned_for_each_delivery(tmp_path, monkeypatch):
    bus = DurableEventBus(str(tmp_path / "events.db"))
    for seq in range(1, 101):
        bus.publish(_event(f"e{seq}", seq))

    calls = 0
    original = bus._verify_consumer_rows_locked

    def counted():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(bus, "_verify_consumer_rows_locked", counted)
    for now in range(1, 101):
        delivery = bus.claim_next(
            "projection",
            worker_id="worker-a",
            now_ms=now * 2,
            lease_ms=10,
        )
        assert delivery is not None
        bus.ack_delivery(delivery, at_ms=now * 2 + 1)

    assert calls == 0
    assert bus.consumer_chain_valid()
    assert calls == 1


def test_event_history_tamper_blocks_consumer_claim():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    bus._c.execute("DROP TRIGGER event_log_no_update")
    bus._c.execute("UPDATE event_log SET source='evil' WHERE event_id='e1'")
    bus._c.commit()

    with pytest.raises(EventBusIntegrityError):
        bus.claim_next(
            "projection",
            worker_id="worker-a",
            now_ms=1,
            lease_ms=10,
        )
