import sqlite3

import pytest

from quantos.contracts.events import EventOutboxMessage
from services.alpha.maintenance_events import maintenance_transition_events
from services.alpha.maintenance_store import MaintenanceStore, MaintenanceStoreError
from services.monitoring.domain_event_runtime import dispatch_maintenance_event_outbox
from services.monitoring.event_bus import DurableEventBus


def _snapshot(timestamp: int = 1000) -> dict:
    return {
        "schema_version": 1,
        "generated_at": timestamp,
        "actor_kind": "AGENT",
        "data_provenance": "REAL_MAINTENANCE_SWEEP",
        "strategies": {"a1": {"state": "DEGRADED"}},
        "n_actions": 1,
        "honesty_note": "test",
    }


def _messages(timestamp: int = 1000) -> tuple[EventOutboxMessage, ...]:
    events = maintenance_transition_events(
        {
            "a1": {
                "action": "DEGRADED",
                "reasons": [{"metric": "ic_retention"}],
            }
        },
        actor_id="alpha_maintenance_sweep",
        timestamp_ms=timestamp,
    )
    return tuple(
        EventOutboxMessage(
            event=event,
            correlation_id=f"alpha-maintenance:{timestamp}",
        )
        for event in events
    )


def test_snapshot_and_transition_events_commit_as_one_append_only_record():
    store = MaintenanceStore(":memory:", create=True)

    assert store.save_snapshot(_snapshot(), _messages()) is True
    assert store.save_snapshot(_snapshot(), _messages()) is False

    assert store.latest_snapshot() == _snapshot()
    pending = store.pending_event_outbox()
    assert len(pending) == 1
    assert pending[0]["event"].payload["action"] == "DEGRADED"
    assert store.chain_valid()


def test_snapshot_insert_failure_cannot_leave_orphan_events():
    store = MaintenanceStore(":memory:", create=True)
    store._conn.execute(
        "CREATE TRIGGER reject_maintenance_record "
        "BEFORE INSERT ON maintenance_records "
        "BEGIN SELECT RAISE(ABORT, 'reject maintenance record'); END"
    )

    with pytest.raises(sqlite3.DatabaseError, match="reject maintenance record"):
        store.save_snapshot(_snapshot(), _messages())

    assert store.latest_snapshot() is None
    assert store.pending_event_outbox() == ()


def test_publish_before_dispatch_ack_replays_without_duplicate():
    store = MaintenanceStore(":memory:", create=True)
    store.save_snapshot(_snapshot(), _messages())
    pending = store.pending_event_outbox()[0]
    bus = DurableEventBus(":memory:")
    bus.publish_once(
        pending["event"],
        correlation_id=pending["correlation_id"],
        causation_id=pending["causation_id"],
    )

    summary = dispatch_maintenance_event_outbox(store, bus, at_ms=1001)

    assert summary == {"dispatched": 1, "pending": 0}
    assert bus.count() == 1


def test_snapshot_tamper_breaks_record_and_outbox_reads():
    store = MaintenanceStore(":memory:", create=True)
    store.save_snapshot(_snapshot(), _messages())
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM maintenance_records")

    store._conn.execute("DROP TRIGGER maintenance_records_no_update")
    store._conn.execute(
        "UPDATE maintenance_records SET snapshot_json='{\"generated_at\":1000}'"
    )
    store._conn.commit()

    assert not store.chain_valid()
    with pytest.raises(MaintenanceStoreError):
        store.pending_event_outbox()

