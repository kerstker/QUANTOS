import sqlite3

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from services.data.events import data_refresh_recorded_event
from services.monitoring.event_bus import DurableEventBus
from services.monitoring.event_health import (
    EventBusHealthPolicy,
    evaluate_event_bus_health,
)
from services.monitoring.domain_event_runtime import read_domain_event_bus_health
from services.monitoring.consumer_group import (
    PROCESS_DECLARED_WORKER_ASSURANCE,
)


def _event(index: int, at_ms: int):
    return data_refresh_recorded_event(
        run_id=f"refresh-{index}",
        outcome="PASS",
        source_count=1,
        failed_source_count=0,
        failed_instrument_count=0,
        duration_ms=10,
        status_hash=f"{index:064x}",
        history_entry_hash=f"{index + 1:064x}",
        at_ms=at_ms,
    )


def _group(bus: DurableEventBus, *, now_ms: int = 1_000):
    return bus.heartbeat_consumer_group(
        "projection",
        worker_id="worker-a",
        now_ms=now_ms,
        partition_count=2,
        session_timeout_ms=10_000,
    )


def test_operational_snapshot_counts_partition_lag_not_global_sequence_gap():
    bus = DurableEventBus(":memory:", schema_registry=CANONICAL_EVENT_SCHEMAS)
    for index in range(1, 6):
        bus.publish(_event(index, 1_000 + index), correlation_id=f"book-{index % 2}")
    assignment = _group(bus)
    partition = assignment.partitions[0]
    delivery = bus.claim_partition(assignment, partition, now_ms=1_001, lease_ms=100)
    assert delivery is not None
    bus.ack_delivery(delivery, at_ms=1_002)

    snapshot = bus.operational_snapshot("projection", now_ms=2_000)

    assert snapshot["integrity"] == {
        "event_chain_valid": True,
        "consumer_chain_valid": True,
        "coordination_chain_valid": True,
    }
    assert snapshot["totals"]["event_records"] == 5
    assert snapshot["totals"]["pending_events"] == sum(
        item["pending_events"] for item in snapshot["partitions"]
    )
    assert snapshot["totals"]["pending_events"] == 4
    assert all(item["pending_events"] >= 0 for item in snapshot["partitions"])
    assert snapshot["totals"]["dead_letters"] == 0
    assert snapshot["group"]["worker_identity_policy"] == (
        PROCESS_DECLARED_WORKER_ASSURANCE
    )
    assert snapshot["group"]["active_worker_identities"] == (
        {
            "assurance": PROCESS_DECLARED_WORKER_ASSURANCE,
            "worker_id": "worker-a",
        },
    )


def test_health_alerts_on_lag_age_dead_letter_and_retention_pressure():
    bus = DurableEventBus(":memory:", schema_registry=CANONICAL_EVENT_SCHEMAS)
    bus.publish(_event(1, 1_000), correlation_id="book-a")
    assignment = _group(bus, now_ms=1_100)
    claimed = [
        (item, bus.claim_partition(assignment, item, now_ms=1_101, lease_ms=1))
        for item in assignment.partitions
    ]
    partition, delivery = next(item for item in claimed if item[1] is not None)
    try:
        bus.fail_delivery(delivery, RuntimeError("secret"), at_ms=1_102, max_attempts=2)
    except Exception:
        pass
    retry = bus.claim_partition(assignment, partition, now_ms=1_103, lease_ms=1)
    assert retry is not None
    try:
        bus.fail_delivery(retry, RuntimeError("secret"), at_ms=1_104, max_attempts=2)
    except Exception:
        pass
    bus.publish(_event(2, 1_005), correlation_id="book-a")

    snapshot = bus.operational_snapshot("projection", now_ms=10_000)
    health = evaluate_event_bus_health(
        snapshot,
        EventBusHealthPolicy(
            max_pending_events=0,
            max_oldest_pending_age_ms=1,
            max_dead_letters=0,
            max_event_records=1,
            max_consumer_journal_records=1,
            max_coordination_journal_records=1,
            max_allocated_bytes=1,
            warning_fraction=0.8,
        ),
    )

    assert health["verdict"] == "BREACH"
    codes = {alert["code"] for alert in health["alerts"]}
    assert {
        "CONSUMER_LAG_EVENTS_EXCEEDED",
        "CONSUMER_LAG_AGE_EXCEEDED",
        "DEAD_LETTERS_PRESENT",
        "RETENTION_CONSUMER_JOURNAL_LIMIT_EXCEEDED",
        "RETENTION_COORDINATION_JOURNAL_LIMIT_EXCEEDED",
        "RETENTION_STORAGE_LIMIT_EXCEEDED",
    } <= codes
    assert health["retention_mode"] == "APPEND_ONLY_ALERT_ONLY"


def test_health_warns_before_retention_limit_without_mutating_history():
    snapshot = {
        "checked_at_ms": 100,
        "group": {
            "group_id": "projection",
            "active_workers": (),
            "partition_count": 1,
        },
        "integrity": {
            "event_chain_valid": True,
            "consumer_chain_valid": True,
            "coordination_chain_valid": True,
        },
        "partitions": ({"pending_events": 0, "oldest_pending_age_ms": None},),
        "totals": {
            "pending_events": 0,
            "dead_letters": 0,
            "event_records": 8,
            "consumer_journal_records": 0,
            "coordination_journal_records": 0,
            "allocated_bytes": 0,
        },
    }
    health = evaluate_event_bus_health(
        snapshot,
        EventBusHealthPolicy(
            max_event_records=10,
            max_consumer_journal_records=10,
            max_coordination_journal_records=10,
            max_allocated_bytes=10_000,
            warning_fraction=0.8,
        ),
    )

    assert health["verdict"] == "WARN"
    assert [alert["code"] for alert in health["alerts"]] == [
        "RETENTION_EVENT_ROWS_NEAR_LIMIT"
    ]


def test_read_health_does_not_create_a_missing_event_authority(tmp_path):
    path = tmp_path / "missing.db"

    health = read_domain_event_bus_health(now_ms=100, path=str(path))

    assert health["verdict"] == "UNAVAILABLE"
    assert health["alerts"][0]["code"] == "EVENT_AUTHORITY_UNAVAILABLE"
    assert not path.exists()


def test_read_health_does_not_initialize_an_existing_unrelated_sqlite_file(tmp_path):
    path = tmp_path / "unrelated.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker(value) VALUES ('preserve-me')")
    connection.commit()
    connection.close()
    before = path.read_bytes()

    health = read_domain_event_bus_health(now_ms=100, path=str(path))

    assert health["verdict"] == "BREACH"
    assert health["alerts"][0]["code"] == "EVENT_AUTHORITY_UNREADABLE"
    assert path.read_bytes() == before


def test_read_health_returns_verified_registered_group_snapshot(tmp_path):
    path = str(tmp_path / "events.db")
    bus = DurableEventBus(path, schema_registry=CANONICAL_EVENT_SCHEMAS)
    bus.publish(_event(1, 1_000))
    assignment = bus.heartbeat_consumer_group(
        "domain-event-projection-v1",
        worker_id="worker-a",
        now_ms=1_000,
        partition_count=1,
        session_timeout_ms=10_000,
    )
    delivery = bus.claim_partition(assignment, 0, now_ms=1_001, lease_ms=100)
    assert delivery is not None
    bus.ack_delivery(delivery, at_ms=1_002)
    bus.close()

    health = read_domain_event_bus_health(now_ms=1_100, path=path)

    assert health["verdict"] == "PASS"
    assert health["pending_events"] == 0
    assert health["event_records"] == 1
    assert health["integrity"]["event_chain_valid"] is True
    assert health["worker_identity_policy"] == (
        PROCESS_DECLARED_WORKER_ASSURANCE
    )
    assert health["active_worker_identities"] == [
        {
            "assurance": PROCESS_DECLARED_WORKER_ASSURANCE,
            "worker_id": "worker-a",
        }
    ]
