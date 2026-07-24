"""Idempotent durable read projection for domain-event consumers."""

import sqlite3

import pytest

from quantos.contracts.events import Event
from services.monitoring.event_bus import DurableEventBus, EventDelivery
from services.monitoring.event_projection import (
    DomainEventProjection,
    EventProjectionIntegrityError,
)


def _event(event_id: str, timestamp: int, *, value: int = 1) -> Event:
    return Event(
        event_id=event_id,
        event_type="research.test.event",
        timestamp=timestamp,
        source="test",
        domain="research",
        payload={"value": value},
    )


def _delivery(event: Event, *, event_seq: int = 1, attempt: int = 1) -> EventDelivery:
    return EventDelivery(
        consumer_id="domain-event-projection-v1",
        worker_id="worker-a",
        event_seq=event_seq,
        event=event,
        correlation_id="correlation-1",
        causation_id="cause-1",
        attempt=attempt,
        claim_journal_seq=attempt,
        claimed_at_ms=100,
        lease_until_ms=200,
    )


def test_projection_persists_full_causal_record_and_is_idempotent(tmp_path):
    path = str(tmp_path / "projection.db")
    projection = DomainEventProjection(path)
    delivery = _delivery(_event("e1", 1))

    assert projection.apply(delivery) is True
    assert projection.apply(delivery) is False
    assert projection.count() == 1
    assert projection.chain_valid()
    record = projection.records()[0]
    assert record["event"] == delivery.event
    assert record["event_seq"] == 1
    assert record["correlation_id"] == "correlation-1"
    assert record["causation_id"] == "cause-1"


def test_projection_rejects_event_id_or_sequence_conflicts(tmp_path):
    projection = DomainEventProjection(str(tmp_path / "projection.db"))
    projection.apply(_delivery(_event("e1", 1), event_seq=1))

    with pytest.raises(EventProjectionIntegrityError, match="conflict"):
        projection.apply(_delivery(_event("e1", 1, value=2), event_seq=1))
    with pytest.raises(EventProjectionIntegrityError, match="conflict"):
        projection.apply(_delivery(_event("e2", 2), event_seq=1))


def test_crash_after_projection_before_ack_replays_idempotently(tmp_path):
    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    bus = DurableEventBus(event_path)
    projection = DomainEventProjection(projection_path)
    bus.publish(_event("e1", 1))

    first = bus.claim_next(
        "domain-event-projection-v1",
        worker_id="worker-a",
        now_ms=100,
        lease_ms=10,
    )
    assert first is not None
    assert projection.apply(first) is True
    bus.close()  # simulated crash before ack

    reopened = DurableEventBus(event_path)
    replayed = reopened.claim_next(
        "domain-event-projection-v1",
        worker_id="worker-b",
        now_ms=111,
        lease_ms=10,
    )
    assert replayed is not None and replayed.attempt == 2
    assert projection.apply(replayed) is False
    reopened.ack_delivery(replayed, at_ms=112)
    assert projection.count() == 1
    assert reopened.consumer_offset("domain-event-projection-v1") == replayed.event_seq


def test_projection_is_append_only_and_detects_bypassed_tamper(tmp_path):
    projection = DomainEventProjection(str(tmp_path / "projection.db"))
    projection.apply(_delivery(_event("e1", 1)))

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        projection._c.execute("UPDATE projected_events SET source='evil'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        projection._c.execute("DELETE FROM projected_events")

    projection._c.execute("DROP TRIGGER projected_events_no_update")
    projection._c.execute("UPDATE projected_events SET source='evil'")
    projection._c.commit()
    assert not projection.chain_valid()
    with pytest.raises(EventProjectionIntegrityError):
        projection.records()


def test_projection_rechecks_full_chain_after_external_connection_change(tmp_path):
    path = str(tmp_path / "projection.db")
    projection = DomainEventProjection(path)
    projection.apply(_delivery(_event("e1", 1), event_seq=1))

    other = sqlite3.connect(path)
    other.execute("DROP TRIGGER projected_events_no_update")
    other.execute("UPDATE projected_events SET source='evil'")
    other.commit()
    other.close()

    with pytest.raises(EventProjectionIntegrityError, match="payload hash|previous hash|record hash"):
        projection.apply(_delivery(_event("e2", 2), event_seq=2))


def test_projection_does_not_rescan_chain_for_each_same_connection_append(tmp_path, monkeypatch):
    projection = DomainEventProjection(str(tmp_path / "projection.db"))
    calls = 0
    original = projection._verify_rows

    def counted(rows):
        nonlocal calls
        calls += 1
        return original(rows)

    monkeypatch.setattr(projection, "_verify_rows", counted)
    for seq in range(1, 101):
        projection.apply(_delivery(_event(f"e{seq}", seq), event_seq=seq))

    assert calls == 0
    assert projection.chain_valid()
    assert calls == 1


def test_read_only_open_fails_closed_without_creating_missing_projection(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(EventProjectionIntegrityError, match="unavailable"):
        DomainEventProjection(str(path), create=False)
    assert not path.exists()
