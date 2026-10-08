import sqlite3

import pytest

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from services.data.events import data_refresh_recorded_event
from services.monitoring.event_authority_consistency import (
    inspect_event_authority_consistency,
)
from services.monitoring.event_bus import DurableEventBus, EventDeadLetteredError
from services.monitoring.domain_event_runtime import DOMAIN_EVENT_PROJECTION_CONSUMER
from services.monitoring.event_projection import DomainEventProjection
from tools.domain_event_dead_letter_resolve import resolve_dead_letters


def _event(at_ms: int):
    return data_refresh_recorded_event(
        run_id="redrive-test",
        outcome="PASS",
        source_count=1,
        failed_source_count=0,
        failed_instrument_count=0,
        duration_ms=1,
        status_hash="a" * 64,
        history_entry_hash="b" * 64,
        at_ms=at_ms,
    )


def test_redrive_requires_exact_projection_and_preserves_history(tmp_path):
    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    bus = DurableEventBus(event_path, schema_registry=CANONICAL_EVENT_SCHEMAS)
    event = _event(1_000)
    bus.publish(event, correlation_id="redrive", causation_id="test")
    delivery = bus.claim_next(
        DOMAIN_EVENT_PROJECTION_CONSUMER,
        worker_id="poison-worker",
        now_ms=1_001,
        lease_ms=100,
    )
    assert delivery is not None
    projection = DomainEventProjection(
        projection_path, schema_registry=CANONICAL_EVENT_SCHEMAS
    )
    assert projection.apply(delivery) is True
    projection.close()
    with pytest.raises(EventDeadLetteredError):
        bus.fail_delivery(
            delivery, RuntimeError("projection was temporarily unavailable"),
            at_ms=1_002, max_attempts=1,
        )
    bus.close()

    report = resolve_dead_letters(
        event_path=event_path,
        projection_path=projection_path,
        consumer_id=DOMAIN_EVENT_PROJECTION_CONSUMER,
        now_ms=2_000,
    )

    assert report["verdict"] == "PASS"
    assert report["resolved_count"] == 1
    assert report["safety"]["historical_failures_deleted"] is False
    assert report["safety"]["projection_mutated"] is False

    reopened = DurableEventBus(event_path, schema_registry=CANONICAL_EVENT_SCHEMAS)
    assert reopened.dead_letters(consumer_id=DOMAIN_EVENT_PROJECTION_CONSUMER) == ()
    rows = reopened._c.execute(  # noqa: SLF001 - audit assertion
        "SELECT action FROM event_consumer_journal ORDER BY journal_seq"
    ).fetchall()
    assert [row[0] for row in rows] == ["CLAIMED", "DEAD_LETTERED", "CLAIMED", "ACKED"]
    reopened.close()

    consistency = inspect_event_authority_consistency(
        event_path=event_path, projection_path=projection_path
    )
    assert consistency["verdict"] == "PASS"
    assert consistency["authority"]["consumer"]["historical_failed_deliveries"] == 0


def test_redrive_is_idempotent_after_resolution(tmp_path):
    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    bus = DurableEventBus(event_path, schema_registry=CANONICAL_EVENT_SCHEMAS)
    event = _event(1_000)
    bus.publish(event)
    delivery = bus.claim_next(
        DOMAIN_EVENT_PROJECTION_CONSUMER,
        worker_id="poison-worker",
        now_ms=1_001,
        lease_ms=100,
    )
    assert delivery is not None
    projection = DomainEventProjection(
        projection_path, schema_registry=CANONICAL_EVENT_SCHEMAS
    )
    projection.apply(delivery)
    projection.close()
    with pytest.raises(EventDeadLetteredError):
        bus.fail_delivery(delivery, RuntimeError("temporary"), at_ms=1_002, max_attempts=1)
    bus.close()

    first = resolve_dead_letters(
        event_path=event_path,
        projection_path=projection_path,
        consumer_id=DOMAIN_EVENT_PROJECTION_CONSUMER,
        now_ms=2_000,
    )
    second = resolve_dead_letters(
        event_path=event_path,
        projection_path=projection_path,
        consumer_id=DOMAIN_EVENT_PROJECTION_CONSUMER,
        now_ms=2_001,
    )
    assert first["resolved_count"] == 1
    assert second["verdict"] == "PASS"
    assert second["resolved_count"] == 0

    connection = sqlite3.connect(event_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM event_consumer_journal"
        ).fetchone()[0] == 4
    finally:
        connection.close()
