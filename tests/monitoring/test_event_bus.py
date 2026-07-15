"""P2-5 持久事件总线：因果回放、追加链、重启恢复和篡改失败关闭。"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS, EventSchemaError
from quantos.contracts.events import EVENT_DRY_RUN_STARTED
from quantos.contracts.events import Event
from services.monitoring.event_bus import (
    DurableEventBus,
    EventAuthorityCommitment,
    EventBusIntegrityError,
)


def _ev(eid: str, *, etype="research.alpha.promoted", ts=1000, payload=None) -> Event:
    return Event(event_id=eid, event_type=etype, timestamp=ts, source="test",
                 domain="research", payload=payload or {})


def test_publish_then_replay_returns_in_seq_order():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"))
    bus.publish(_ev("e2"))
    out = bus.replay()
    assert [e.event_id for e in out] == ["e1", "e2"]
    assert all(isinstance(e, Event) for e in out)


def test_replay_filters_by_correlation_group():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("a1"), correlation_id="C1")
    bus.publish(_ev("b1"), correlation_id="C2")
    bus.publish(_ev("a2"), correlation_id="C1")
    assert [e.event_id for e in bus.replay(correlation_id="C1")] == ["a1", "a2"]
    assert [e.event_id for e in bus.replay(correlation_id="C2")] == ["b1"]


def test_records_expose_correlation_and_causation_roundtrip():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"), correlation_id="C1", causation_id="")
    bus.publish(_ev("e2"), correlation_id="C1", causation_id="e1")
    recs = bus.records(correlation_id="C1")
    assert [r["event"].event_id for r in recs] == ["e1", "e2"]
    assert recs[1]["causation_id"] == "e1"      # 因果链:e2 由 e1 引发
    assert recs[0]["correlation_id"] == "C1"
    assert recs[0]["seq"] < recs[1]["seq"]


def test_duplicate_event_id_raises_anti_replay():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("dup"))
    with pytest.raises(ValueError):
        bus.publish(_ev("dup"))


def test_publish_once_is_idempotent_but_same_id_different_content_is_rejected():
    bus = DurableEventBus(":memory:")
    event = _ev("once", payload={"value": 1})
    bus.publish_once(event, correlation_id="C", causation_id="root")
    bus.publish_once(event, correlation_id="C", causation_id="root")
    assert bus.count() == 1

    with pytest.raises(ValueError, match="conflict"):
        bus.publish_once(_ev("once", payload={"value": 2}), correlation_id="C")


def test_non_event_rejected_fail_closed():
    bus = DurableEventBus(":memory:")
    with pytest.raises(TypeError):
        bus.publish({"event_id": "x"})            # dict 不是 Event


def test_schema_aware_bus_rejects_invalid_producer_before_persistence():
    bus = DurableEventBus(":memory:", schema_registry=CANONICAL_EVENT_SCHEMAS)
    invalid = Event(
        event_id="bad",
        event_type=EVENT_DRY_RUN_STARTED,
        timestamp=1,
        source="unregistered.source",
        domain="execution",
        payload={"mode": "single_period", "run_id": "r1", "strategy_id": "s1"},
    )

    with pytest.raises(EventSchemaError, match="source"):
        bus.publish(invalid)
    assert bus.count() == 0


def test_schema_aware_reopen_fails_closed_on_unregistered_retained_history(tmp_path):
    path = str(tmp_path / "events.db")
    permissive = DurableEventBus(path)
    permissive.publish(_ev("legacy-unregistered", etype="research.test.event"))
    permissive.close()

    with pytest.raises(EventBusIntegrityError, match="schema"):
        DurableEventBus(path, schema_registry=CANONICAL_EVENT_SCHEMAS)


def test_subscribe_handler_invoked_synchronously_on_publish():
    bus = DurableEventBus(":memory:")
    seen = []
    bus.subscribe(lambda e: seen.append(e.event_id))
    bus.publish(_ev("e1"))
    bus.publish(_ev("e2"))
    assert seen == ["e1", "e2"]                    # 同步、按发布顺序


def test_subscriber_sees_durable_event_persistence_first():
    # 订阅回调里能读到已落库的事件(persistence-first:先 commit 再 notify)
    bus = DurableEventBus(":memory:")
    counts = []
    bus.subscribe(lambda e: counts.append(bus.count()))
    bus.publish(_ev("e1"))
    assert counts == [1]                           # notify 时该事件已在库


def test_since_seq_filter_returns_only_newer():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"))
    bus.publish(_ev("e2"))
    all_recs = bus.records()
    first_seq = all_recs[0]["seq"]
    assert [e.event_id for e in bus.replay(since_seq=first_seq)] == ["e2"]


def test_deterministic_same_input_same_records():
    b1, b2 = DurableEventBus(":memory:"), DurableEventBus(":memory:")
    for b in (b1, b2):
        b.publish(_ev("e1", ts=5, payload={"k": 1}), correlation_id="C")
        b.publish(_ev("e2", ts=6, payload={"k": 2}), correlation_id="C", causation_id="e1")
    r1, r2 = b1.records(), b2.records()
    assert [(r["seq"], r["event"].event_id, r["event"].reproducibility_hash, r["causation_id"]) for r in r1] \
        == [(r["seq"], r["event"].event_id, r["event"].reproducibility_hash, r["causation_id"]) for r in r2]


def test_hash_chain_survives_close_and_reopen(tmp_path):
    path = str(tmp_path / "events.db")
    first = DurableEventBus(path)
    first.publish(_ev("e1"), correlation_id="C")
    first.publish(_ev("e2"), correlation_id="C", causation_id="e1")
    assert first.chain_valid()
    head = first.head_hash
    first.close()

    reopened = DurableEventBus(path)
    assert reopened.chain_valid()
    assert reopened.head_hash == head
    assert [event.event_id for event in reopened.replay()] == ["e1", "e2"]


def test_authority_commitment_covers_all_three_chains_and_survives_reopen(tmp_path):
    path = str(tmp_path / "events.db")
    bus = DurableEventBus(path)
    bus.publish(_ev("e1"), correlation_id="book-a")
    assignment = bus.heartbeat_consumer_group(
        "seal-test",
        worker_id="worker-a",
        now_ms=1,
        partition_count=1,
        session_timeout_ms=100,
    )
    delivery = bus.claim_partition(assignment, 0, now_ms=2, lease_ms=10)
    assert delivery is not None
    bus.ack_delivery(delivery, at_ms=3)
    bus.leave_consumer_group("seal-test", worker_id="worker-a", now_ms=4)

    commitment = bus.authority_commitment()
    assert isinstance(commitment, EventAuthorityCommitment)
    assert commitment.schema_version == 1
    assert commitment.event_records == 1
    assert commitment.consumer_journal_records == 2
    assert commitment.coordination_journal_records >= 2
    assert commitment.event_head_hash == bus.head_hash
    assert len(commitment.overall_hash) == 64
    bus.close()

    reopened = DurableEventBus(path, create=False)
    assert reopened.authority_commitment() == commitment
    assert EventAuthorityCommitment.from_dict(commitment.to_dict()) == commitment
    reopened.publish(_ev("e2"), correlation_id="book-a", causation_id="e1")
    assert reopened.authority_commitment().overall_hash != commitment.overall_hash
    assert reopened.extends_authority_commitment(commitment) is True
    reopened.close()

    tampered = commitment.to_dict()
    tampered["event_head_hash"] = "0" * 64
    with pytest.raises(ValueError, match="overall hash"):
        EventAuthorityCommitment.from_dict(tampered)

    unrelated = DurableEventBus(":memory:")
    unrelated.publish(_ev("other"), correlation_id="book-a")
    assert unrelated.extends_authority_commitment(commitment) is False


def test_two_connections_append_without_forking_the_chain(tmp_path):
    path = str(tmp_path / "events.db")
    first = DurableEventBus(path)
    second = DurableEventBus(path)
    first.publish(_ev("e1"))
    second.publish(_ev("e2"), causation_id="e1")
    first.publish(_ev("e3"), causation_id="e2")
    assert first.chain_valid() and second.chain_valid()
    assert [event.event_id for event in second.replay()] == ["e1", "e2", "e3"]


def test_legacy_database_is_migrated_to_a_verified_chain(tmp_path):
    path = str(tmp_path / "legacy.db")
    event = _ev("legacy", payload={"value": 1})
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE event_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        " event_id TEXT UNIQUE, event_type TEXT, event_version TEXT, timestamp INTEGER,"
        " source TEXT, domain TEXT, criticality TEXT, correlation_id TEXT,"
        " causation_id TEXT, payload_json TEXT, repro_hash TEXT)"
    )
    connection.execute(
        "INSERT INTO event_log (event_id,event_type,event_version,timestamp,source,domain,"
        " criticality,correlation_id,causation_id,payload_json,repro_hash)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            event.event_id,
            event.event_type,
            event.event_version,
            event.timestamp,
            event.source,
            event.domain,
            event.criticality,
            "legacy-correlation",
            "",
            '{"value": 1}',
            event.reproducibility_hash,
        ),
    )
    connection.commit()
    connection.close()

    migrated = DurableEventBus(path)
    assert migrated.chain_valid()
    assert migrated.replay(correlation_id="legacy-correlation") == (event,)
    columns = {
        row[1] for row in migrated._c.execute("PRAGMA table_info(event_log)").fetchall()
    }
    assert {"prev_record_hash", "record_hash"} <= columns


def test_legacy_migration_is_serialized_across_concurrent_openers(tmp_path):
    path = str(tmp_path / "legacy-concurrent.db")
    event = _ev("legacy", payload={"value": 1})
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE event_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        " event_id TEXT UNIQUE, event_type TEXT, event_version TEXT, timestamp INTEGER,"
        " source TEXT, domain TEXT, criticality TEXT, correlation_id TEXT,"
        " causation_id TEXT, payload_json TEXT, repro_hash TEXT)"
    )
    connection.execute(
        "INSERT INTO event_log (event_id,event_type,event_version,timestamp,source,domain,"
        " criticality,correlation_id,causation_id,payload_json,repro_hash)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            event.event_id,
            event.event_type,
            event.event_version,
            event.timestamp,
            event.source,
            event.domain,
            event.criticality,
            "legacy-correlation",
            "",
            '{"value": 1}',
            event.reproducibility_hash,
        ),
    )
    connection.commit()
    connection.close()
    barrier = Barrier(2)

    def open_and_verify():
        barrier.wait()
        bus = DurableEventBus(path)
        try:
            return bus.chain_valid(), bus.head_hash, bus.count()
        finally:
            bus.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: open_and_verify(), range(2)))

    assert results[0] == results[1]
    assert results[0][0] is True
    assert results[0][2] == 1


def test_append_only_triggers_block_update_and_delete():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        bus._c.execute("UPDATE event_log SET source='tampered' WHERE event_id='e1'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        bus._c.execute("DELETE FROM event_log WHERE event_id='e1'")


def test_bypassed_metadata_tamper_breaks_replay_and_future_publish():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"), correlation_id="C")
    bus.publish(_ev("e2"), correlation_id="C", causation_id="e1")
    bus._c.execute("DROP TRIGGER event_log_no_update")
    bus._c.execute("UPDATE event_log SET correlation_id='evil' WHERE event_id='e1'")
    bus._c.commit()

    assert not bus.chain_valid()
    with pytest.raises(EventBusIntegrityError):
        bus.records()
    with pytest.raises(EventBusIntegrityError):
        bus.publish(_ev("e3"))


def test_bypassed_deletion_breaks_chain():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"))
    bus.publish(_ev("e2"), causation_id="e1")
    bus._c.execute("DROP TRIGGER event_log_no_delete")
    bus._c.execute("DELETE FROM event_log WHERE event_id='e1'")
    bus._c.commit()
    assert not bus.chain_valid()
