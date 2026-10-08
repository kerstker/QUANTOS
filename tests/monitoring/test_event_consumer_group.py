"""Single-node consumer-group liveness, partitioning, and fencing."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from quantos.contracts.events import Event
from services.monitoring.consumer_group import (
    EventConsumerGroupConfigurationError,
    EventConsumerGroupFencedError,
    EventConsumerGroupIntegrityError,
    LEGACY_UNLABELED_WORKER_ASSURANCE,
    PROCESS_DECLARED_WORKER_ASSURANCE,
)
from services.monitoring.event_bus import (
    DurableEventBus,
    EventDeadLetteredError,
    EventDeliveryError,
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


def _heartbeat(
    bus: DurableEventBus,
    worker_id: str,
    *,
    now_ms: int,
    partition_count: int = 4,
    session_timeout_ms: int = 100,
):
    return bus.heartbeat_consumer_group(
        "projection",
        worker_id=worker_id,
        now_ms=now_ms,
        partition_count=partition_count,
        session_timeout_ms=session_timeout_ms,
    )


def test_membership_heartbeat_assigns_partitions_deterministically_without_churn():
    bus = DurableEventBus(":memory:")

    first = _heartbeat(bus, "worker-b", now_ms=1)
    assert first.generation == 1
    assert first.partitions == (0, 1, 2, 3)
    assert first.expires_at_ms == 101
    assert first.declared_worker_id == "worker-b"
    assert first.identity_assurance == PROCESS_DECLARED_WORKER_ASSURANCE
    assert first.worker_id != first.declared_worker_id

    second = _heartbeat(bus, "worker-a", now_ms=2)
    refreshed = _heartbeat(bus, "worker-b", now_ms=3)
    assert second.generation == refreshed.generation == 2
    assert second.partitions == (0, 2)
    assert refreshed.partitions == (1, 3)

    unchanged = _heartbeat(bus, "worker-a", now_ms=4)
    assert unchanged.generation == 2
    assert unchanged.partitions == (0, 2)
    status = bus.consumer_group_status("projection", now_ms=4)
    assert status["active_workers"] == ("worker-a", "worker-b")
    assert status["assignments"] == (
        "worker-a",
        "worker-b",
        "worker-a",
        "worker-b",
    )
    assert status["rebalance_required"] is False
    assert status["worker_identity_policy"] == (
        PROCESS_DECLARED_WORKER_ASSURANCE
    )
    assert status["active_worker_identities"] == (
        {
            "assurance": PROCESS_DECLARED_WORKER_ASSURANCE,
            "worker_id": "worker-a",
        },
        {
            "assurance": PROCESS_DECLARED_WORKER_ASSURANCE,
            "worker_id": "worker-b",
        },
    )


def test_rebalance_fences_old_assignment_and_inflight_delivery():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1), correlation_id="book-a")
    old = _heartbeat(bus, "worker-b", now_ms=1, partition_count=1)
    delivery = bus.claim_partition(old, 0, now_ms=2, lease_ms=100)
    assert delivery is not None

    current = _heartbeat(bus, "worker-a", now_ms=3, partition_count=1)
    assert current.generation == 2
    with pytest.raises(EventConsumerGroupFencedError):
        bus.claim_partition(old, 0, now_ms=4, lease_ms=100)
    with pytest.raises(EventConsumerGroupFencedError):
        bus.ack_delivery(delivery, at_ms=4)


def test_expired_member_is_rebalanced_and_partition_offset_survives_owner_change():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1), correlation_id="book-a")
    bus.publish(_event("e2", 2), correlation_id="book-a")
    worker_a = _heartbeat(
        bus,
        "worker-a",
        now_ms=0,
        partition_count=1,
        session_timeout_ms=10,
    )
    first = bus.claim_partition(worker_a, 0, now_ms=1, lease_ms=5)
    assert first is not None and first.event.event_id == "e1"
    bus.ack_delivery(first, at_ms=2)

    worker_b = _heartbeat(
        bus,
        "worker-b",
        now_ms=11,
        partition_count=1,
        session_timeout_ms=10,
    )
    assert worker_b.generation == 2
    assert worker_b.partitions == (0,)
    second = bus.claim_partition(worker_b, 0, now_ms=11, lease_ms=5)
    assert second is not None and second.event.event_id == "e2"
    assert bus.consumer_group_offsets("projection") == (first.event_seq,)


def test_partitioned_consumption_is_disjoint_and_preserves_correlation_order():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1), correlation_id="book-a")
    bus.publish(_event("e2", 2), correlation_id="book-a")
    bus.publish(_event("e3", 3), correlation_id="book-b")
    bus.publish(_event("e4", 4))
    worker_b = _heartbeat(bus, "worker-b", now_ms=1)
    worker_a = _heartbeat(bus, "worker-a", now_ms=2)
    worker_b = _heartbeat(bus, "worker-b", now_ms=3)
    assert worker_a.generation == worker_b.generation == 2

    seen: dict[str, int] = {}
    for assignment in (worker_a, worker_b):
        for partition_id in assignment.partitions:
            while True:
                delivery = bus.claim_partition(
                    assignment,
                    partition_id,
                    now_ms=4,
                    lease_ms=10,
                )
                if delivery is None:
                    break
                assert delivery.event.event_id not in seen
                seen[delivery.event.event_id] = partition_id
                bus.ack_delivery(delivery, at_ms=4)

    assert set(seen) == {"e1", "e2", "e3", "e4"}
    assert seen["e1"] == seen["e2"]


def test_group_configuration_and_time_are_immutable():
    bus = DurableEventBus(":memory:")
    _heartbeat(bus, "worker-a", now_ms=10, partition_count=2)

    with pytest.raises(EventConsumerGroupConfigurationError):
        _heartbeat(bus, "worker-a", now_ms=11, partition_count=3)
    with pytest.raises(EventConsumerGroupConfigurationError):
        _heartbeat(
            bus,
            "worker-a",
            now_ms=11,
            partition_count=2,
            session_timeout_ms=101,
        )
    with pytest.raises(ValueError, match="monotonic"):
        _heartbeat(bus, "worker-a", now_ms=9, partition_count=2)


def test_claim_rejects_clock_rollback_and_reserved_internal_group_id():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    assignment = _heartbeat(bus, "worker-a", now_ms=100, partition_count=1)

    with pytest.raises(EventConsumerGroupFencedError, match="time moved backwards"):
        bus.claim_partition(assignment, 0, now_ms=99, lease_ms=5)
    with pytest.raises(ValueError, match="reserved"):
        bus.heartbeat_consumer_group(
            "__event_group_partition__:forged",
            worker_id="worker-a",
            now_ms=101,
            partition_count=1,
            session_timeout_ms=100,
        )
    with pytest.raises(ValueError, match="reserved worker identity"):
        _heartbeat(
            bus,
            "__worker_identity__:process_declared:forged",
            now_ms=101,
            partition_count=1,
        )


def test_partition_consumer_ids_cannot_collide_with_other_group_names():
    bus = DurableEventBus(":memory:")
    multi = bus.heartbeat_consumer_group(
        "group-a",
        worker_id="worker-a",
        now_ms=1,
        partition_count=2,
        session_timeout_ms=100,
    )
    single = bus.heartbeat_consumer_group(
        "group-a::partition:0/2",
        worker_id="worker-b",
        now_ms=1,
        partition_count=1,
        session_timeout_ms=100,
    )
    bus.publish(_event("e1", 1))

    multi_delivery = next(
        delivery
        for partition in multi.partitions
        if (
            delivery := bus.claim_partition(
                multi,
                partition,
                now_ms=2,
                lease_ms=5,
            )
        )
        is not None
    )
    single_delivery = bus.claim_partition(single, 0, now_ms=2, lease_ms=5)
    assert single_delivery is not None
    assert multi_delivery.consumer_id != single_delivery.consumer_id


def test_expiration_requires_rebalance_before_more_claims():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    assignment = _heartbeat(
        bus,
        "worker-a",
        now_ms=1,
        partition_count=1,
        session_timeout_ms=10,
    )

    status = bus.consumer_group_status("projection", now_ms=12)
    assert status["active_workers"] == ()
    assert status["rebalance_required"] is True
    with pytest.raises(EventConsumerGroupFencedError):
        bus.claim_partition(assignment, 0, now_ms=12, lease_ms=5)


def test_leave_rebalances_immediately():
    bus = DurableEventBus(":memory:")
    _heartbeat(bus, "worker-a", now_ms=1, partition_count=2)
    worker_b = _heartbeat(bus, "worker-b", now_ms=2, partition_count=2)
    status = bus.leave_consumer_group(
        "projection",
        worker_id="worker-b",
        now_ms=3,
    )

    assert status["generation"] == worker_b.generation + 1
    assert status["assignments"] == ("worker-a", "worker-a")
    assert status["active_workers"] == ("worker-a",)


def test_group_assignment_and_partition_offset_survive_restart(tmp_path):
    path = str(tmp_path / "events.db")
    bus = DurableEventBus(path)
    bus.publish(_event("e1", 1))
    bus.publish(_event("e2", 2))
    assignment = _heartbeat(bus, "worker-a", now_ms=1, partition_count=1)
    first = bus.claim_partition(assignment, 0, now_ms=2, lease_ms=5)
    assert first is not None
    bus.ack_delivery(first, at_ms=3)
    bus.close()

    reopened = DurableEventBus(path)
    resumed = _heartbeat(reopened, "worker-a", now_ms=4, partition_count=1)
    assert resumed.generation == assignment.generation
    second = reopened.claim_partition(resumed, 0, now_ms=4, lease_ms=5)
    assert second is not None and second.event.event_id == "e2"


def test_coordination_and_delivery_journals_bind_declared_assurance():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("e1", 1))
    assignment = _heartbeat(bus, "worker-a", now_ms=1, partition_count=1)
    delivery = bus.claim_partition(assignment, 0, now_ms=2, lease_ms=5)

    heartbeat_worker = bus._c.execute(
        "SELECT worker_id FROM event_consumer_group_journal "
        "WHERE action='HEARTBEAT' ORDER BY coordination_seq DESC LIMIT 1"
    ).fetchone()[0]
    claim_worker = bus._c.execute(
        "SELECT worker_id FROM event_consumer_journal "
        "WHERE action='CLAIMED' ORDER BY journal_seq DESC LIMIT 1"
    ).fetchone()[0]
    assert heartbeat_worker == assignment.worker_id
    assert claim_worker == assignment.worker_id
    assert delivery is not None and delivery.worker_id == assignment.worker_id
    assert "process_declared" in assignment.worker_id
    assert bus.consumer_group_chain_valid()
    assert bus.consumer_chain_valid()


def test_legacy_unlabeled_active_member_is_retired_before_assured_heartbeat():
    bus = DurableEventBus(":memory:")
    coordinator = bus._consumer_groups
    with bus._lock:
        bus._c.execute("BEGIN IMMEDIATE")
        coordinator._append_locked(
            group_id="projection",
            action="GROUP_CREATED",
            worker_id="",
            at_ms=0,
            partition_count=1,
            session_timeout_ms=100,
            generation=0,
            assignments=("",),
        )
        coordinator.verify_locked()
        coordinator._append_locked(
            group_id="projection",
            action="HEARTBEAT",
            worker_id="worker-a",
            at_ms=1,
            partition_count=1,
            session_timeout_ms=100,
            generation=0,
            assignments=("",),
        )
        coordinator.verify_locked()
        coordinator._append_locked(
            group_id="projection",
            action="REBALANCED",
            worker_id="worker-a",
            at_ms=1,
            partition_count=1,
            session_timeout_ms=100,
            generation=1,
            assignments=("worker-a",),
        )
        bus._c.commit()

    legacy = bus.consumer_group_status("projection", now_ms=1)
    assert legacy["active_worker_identities"] == (
        {
            "assurance": LEGACY_UNLABELED_WORKER_ASSURANCE,
            "worker_id": "worker-a",
        },
    )

    assignment = _heartbeat(
        bus,
        "worker-a",
        now_ms=2,
        partition_count=1,
    )

    assert assignment.declared_worker_id == "worker-a"
    assert assignment.partitions == (0,)
    actions = bus._c.execute(
        "SELECT action,worker_id FROM event_consumer_group_journal "
        "ORDER BY coordination_seq"
    ).fetchall()
    assert ("LEFT", "worker-a") in actions
    assert actions[-2][0] == "HEARTBEAT"
    assert actions[-2][1] == assignment.worker_id
    assert actions[-1] == ("REBALANCED", assignment.worker_id)
    assert bus.consumer_group_chain_valid()


def test_concurrent_heartbeats_serialize_to_one_deterministic_generation(tmp_path):
    path = str(tmp_path / "events.db")
    first = DurableEventBus(path)
    second = DurableEventBus(path)
    barrier = Barrier(2)

    def heartbeat(bus, worker_id):
        barrier.wait()
        return _heartbeat(bus, worker_id, now_ms=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(heartbeat, first, "worker-a"),
            pool.submit(heartbeat, second, "worker-b"),
        )
        initial = tuple(future.result() for future in futures)

    assert sorted(item.generation for item in initial) == [1, 2]
    worker_a = _heartbeat(first, "worker-a", now_ms=11)
    worker_b = _heartbeat(second, "worker-b", now_ms=11)
    assert worker_a.generation == worker_b.generation == 2
    assert worker_a.partitions == (0, 2)
    assert worker_b.partitions == (1, 3)
    assert first.consumer_group_chain_valid()
    assert second.consumer_group_chain_valid()


def test_legacy_event_database_without_group_table_is_migrated(tmp_path):
    path = str(tmp_path / "legacy.db")
    bus = DurableEventBus(path)
    bus._c.execute("DROP TABLE event_consumer_group_journal")
    bus._c.commit()
    bus.close()

    migrated = DurableEventBus(path)
    assignment = _heartbeat(migrated, "worker-a", now_ms=1)
    assert assignment.generation == 1
    assert migrated.consumer_group_chain_valid()


def test_partition_delivery_reuses_retry_and_dead_letter_semantics():
    bus = DurableEventBus(":memory:")
    bus.publish(_event("poison", 1), correlation_id="book-a")
    assignment = _heartbeat(bus, "worker-a", now_ms=1, partition_count=2)
    partition_id = next(
        partition
        for partition in assignment.partitions
        if bus.claim_partition(assignment, partition, now_ms=2, lease_ms=5)
        is not None
    )
    first = bus.claim_partition(assignment, partition_id, now_ms=8, lease_ms=5)
    assert first is not None and first.attempt == 2
    with pytest.raises(EventDeliveryError):
        bus.fail_delivery(
            first,
            RuntimeError("redacted-secret"),
            at_ms=9,
            max_attempts=3,
        )
    second = bus.claim_partition(assignment, partition_id, now_ms=10, lease_ms=5)
    assert second is not None and second.attempt == 3
    with pytest.raises(EventDeadLetteredError):
        bus.fail_delivery(
            second,
            RuntimeError("redacted-secret"),
            at_ms=11,
            max_attempts=3,
        )
    assert bus.consumer_group_offsets("projection")[partition_id] == second.event_seq


def test_group_journal_is_append_only_and_tamper_fails_closed():
    bus = DurableEventBus(":memory:")
    _heartbeat(bus, "worker-a", now_ms=1)

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        bus._c.execute(
            "UPDATE event_consumer_group_journal SET worker_id='evil'"
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        bus._c.execute("DELETE FROM event_consumer_group_journal")

    bus._c.execute("DROP TRIGGER event_consumer_group_journal_no_update")
    bus._c.execute(
        "UPDATE event_consumer_group_journal SET worker_id='evil' "
        "WHERE action='HEARTBEAT'"
    )
    bus._c.commit()
    assert not bus.consumer_group_chain_valid()
    with pytest.raises(EventConsumerGroupIntegrityError):
        _heartbeat(bus, "worker-b", now_ms=2)
