"""Structural contracts for linear-time warm domain-event catch-up."""

from __future__ import annotations

import inspect
import sqlite3

import pytest

from quantos.contracts.events import Event
from services.monitoring.event_bus import DurableEventBus, EventBusIntegrityError
from services.monitoring.event_projection import (
    DomainEventProjection,
    EventProjectionIntegrityError,
)


def _event(index: int) -> Event:
    return Event(
        event_id=f"event-{index:04d}",
        event_type="research.test.event",
        timestamp=index,
        source="test",
        domain="research",
        payload={"index": index},
    )


def _count_calls(monkeypatch, owner, name: str, counts: dict[str, int], key: str):
    original = getattr(owner, name)

    def counted(*args, **kwargs):
        counts[key] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, counted)


def test_warm_single_partition_catchup_does_not_replay_retained_histories(
    monkeypatch,
):
    bus = DurableEventBus(":memory:")
    projection = DomainEventProjection(":memory:")
    for index in range(1, 257):
        bus.publish(_event(index), correlation_id="ordered-stream")
    assignment = bus.heartbeat_consumer_group(
        "projection",
        worker_id="worker-a",
        now_ms=1,
        partition_count=1,
        session_timeout_ms=10_000,
    )

    counts = {"event": 0, "consumer": 0, "group": 0, "projection": 0}
    _count_calls(monkeypatch, bus, "_verify_rows", counts, "event")
    _count_calls(
        monkeypatch,
        bus,
        "_verify_consumer_rows_locked",
        counts,
        "consumer",
    )
    _count_calls(
        monkeypatch,
        bus._consumer_groups,
        "verify_locked",
        counts,
        "group",
    )
    _count_calls(monkeypatch, projection, "_verify_rows", counts, "projection")

    for index in range(1, 257):
        delivery = bus.claim_partition(
            assignment,
            0,
            now_ms=index + 1,
            lease_ms=1_000,
        )
        assert delivery is not None
        assert projection.apply(delivery) is True
        bus.ack_delivery(delivery, at_ms=index + 1)

    assert counts == {"event": 0, "consumer": 0, "group": 0, "projection": 0}
    assert projection.count() == 256
    assert bus.consumer_group_offsets("projection") == (256,)


def test_single_partition_lookup_is_one_indexed_limit_one_query():
    bus = DurableEventBus(":memory:")
    for index in range(1, 4):
        bus.publish(_event(index), correlation_id="ordered-stream")
    assignment = bus.heartbeat_consumer_group(
        "projection",
        worker_id="worker-a",
        now_ms=1,
        partition_count=1,
        session_timeout_ms=100,
    )
    statements: list[str] = []
    bus._c.set_trace_callback(statements.append)

    delivery = bus.claim_partition(assignment, 0, now_ms=2, lease_ms=10)

    assert delivery is not None and delivery.event.event_id == "event-0001"
    normalized = [" ".join(statement.upper().split()) for statement in statements]
    matching = [
        statement
        for statement in normalized
        if "FROM EVENT_LOG WHERE SEQ>" in statement
    ]
    assert len(matching) == 1
    assert "ORDER BY SEQ LIMIT 1" in matching[0]


def test_multi_partition_lookup_uses_bounded_fetchmany_not_fetchall():
    source = inspect.getsource(DurableEventBus._partition_event_row_locked)

    assert ".fetchall(" not in source
    assert ".fetchmany(" in source
    assert "_PARTITION_FETCH_CHUNK_SIZE" in source
    assert DurableEventBus._PARTITION_FETCH_CHUNK_SIZE <= 256


def test_external_valid_append_causes_exactly_one_complete_bus_refresh(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "events.db"
    first = DurableEventBus(str(path))
    second = DurableEventBus(str(path))
    counts = {"event": 0, "consumer": 0, "group": 0}
    _count_calls(monkeypatch, first, "_verify_rows", counts, "event")
    _count_calls(
        monkeypatch,
        first,
        "_verify_consumer_rows_locked",
        counts,
        "consumer",
    )
    _count_calls(
        monkeypatch,
        first._consumer_groups,
        "verify_locked",
        counts,
        "group",
    )

    second.publish(_event(1))
    first.publish(_event(2))
    first.publish(_event(3))

    assert counts == {"event": 1, "consumer": 1, "group": 1}


def test_external_self_consistent_truncation_cannot_rebase_bus_frontier(tmp_path):
    path = tmp_path / "events.db"
    bus = DurableEventBus(str(path))
    bus.publish(_event(1))
    bus.publish(_event(2))
    rogue = sqlite3.connect(path)
    trigger_sql = rogue.execute(
        "SELECT sql FROM sqlite_master WHERE name='event_log_no_delete'"
    ).fetchone()[0]
    rogue.execute("DROP TRIGGER event_log_no_delete")
    rogue.execute("DELETE FROM event_log WHERE seq=2")
    rogue.execute(trigger_sql)
    rogue.commit()

    with pytest.raises(EventBusIntegrityError, match="verified frontier"):
        bus.publish(_event(3))

    assert rogue.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 1


def test_failed_bus_transaction_invalidates_then_reverifies_once(
    monkeypatch,
):
    bus = DurableEventBus(":memory:")
    bus.publish(_event(1))
    counts = {"event": 0, "consumer": 0, "group": 0}
    _count_calls(monkeypatch, bus, "_verify_rows", counts, "event")
    _count_calls(
        monkeypatch,
        bus,
        "_verify_consumer_rows_locked",
        counts,
        "consumer",
    )
    _count_calls(
        monkeypatch,
        bus._consumer_groups,
        "verify_locked",
        counts,
        "group",
    )

    with pytest.raises(ValueError, match="duplicate event_id"):
        bus.publish(_event(1))
    bus.publish(_event(2))
    bus.publish(_event(3))

    assert counts == {"event": 1, "consumer": 1, "group": 1}


def test_explicit_bus_audit_detects_trigger_drift_with_warm_frontier():
    bus = DurableEventBus(":memory:")
    bus.publish(_event(1))
    bus._c.execute("DROP TRIGGER event_log_no_update")
    bus._c.commit()

    assert bus.chain_valid() is False


def test_explicit_projection_audit_detects_trigger_drift_with_warm_frontier():
    bus = DurableEventBus(":memory:")
    projection = DomainEventProjection(":memory:")
    bus.publish(_event(1))
    delivery = bus.claim_next("projection", worker_id="worker", now_ms=1, lease_ms=10)
    assert delivery is not None
    assert projection.apply(delivery) is True
    projection._c.execute("DROP TRIGGER projected_events_no_update")
    projection._c.commit()

    assert projection.chain_valid() is False


def test_projection_commit_uncertainty_permanently_fails_open_object_closed(
    monkeypatch,
):
    bus = DurableEventBus(":memory:")
    projection = DomainEventProjection(":memory:")
    bus.publish(_event(1))
    delivery = bus.claim_next("projection", worker_id="worker", now_ms=1, lease_ms=10)
    assert delivery is not None

    def uncertain_commit():
        raise sqlite3.OperationalError("injected commit uncertainty")

    monkeypatch.setattr(projection, "_commit_connection_locked", uncertain_commit)
    with pytest.raises(EventProjectionIntegrityError, match="outcome is uncertain"):
        projection.apply(delivery)
    with pytest.raises(EventProjectionIntegrityError, match="reopen is required"):
        projection.apply(delivery)
