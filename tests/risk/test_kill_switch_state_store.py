import sqlite3

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.events import (
    EVENT_KILL_SWITCH_ACTIVATED,
    EVENT_KILL_SWITCH_RESET,
    EventOutboxMessage,
)
from services.governance.approval import approve, consume, open_request, review
from services.monitoring.domain_event_runtime import (
    dispatch_kill_switch_event_outbox,
)
from services.monitoring.event_bus import DurableEventBus
from services.risk.pre_trade.events import (
    kill_switch_activated_event,
    kill_switch_reset_event,
)
from services.risk.pre_trade.kill_switch import KillSwitch
from services.risk.pre_trade.kill_switch_runtime import (
    arm_and_record,
    reset_with_grant_and_record,
)
from services.risk.pre_trade.kill_switch_store import (
    KillSwitchStateConflict,
    KillSwitchStateStore,
    KillSwitchStateStoreError,
)


SYSTEM = Actor(actor_id="risk-sentinel", kind=ActorKind.SYSTEM)
ALICE = Actor(actor_id="alice", kind=ActorKind.HUMAN)
BOB = Actor(actor_id="bob", kind=ActorKind.HUMAN)
CAROL = Actor(actor_id="carol", kind=ActorKind.HUMAN)


def _grant(subject: str):
    request = open_request("reset-request", subject, ALICE, at=10)
    request = review(request, BOB, at=11)
    request = approve(request, CAROL, at=12)
    return consume(request, subject, CAROL, at=13)


def test_arm_state_and_event_commit_in_one_append_only_transaction():
    store = KillSwitchStateStore(":memory:", create=True)

    state = arm_and_record(
        store,
        SYSTEM,
        "drawdown breach",
        at=1000,
        incident_id="incident-1",
    )

    assert state.tripped is True
    assert store.load_state() == state
    pending = store.pending_event_outbox()
    assert len(pending) == 1
    assert pending[0]["event"].event_type == EVENT_KILL_SWITCH_ACTIVATED
    assert pending[0]["event"].event_version == "1.1.0"
    assert pending[0]["event"].timestamp == 1_000_000
    assert pending[0]["event"].payload["incident_id"] == "incident-1"
    assert store.chain_valid()


def test_legacy_millisecond_arm_event_replays_under_current_store():
    store = KillSwitchStateStore(":memory:", create=True)
    previous = store.load_state()
    state = KillSwitch(previous).arm(
        SYSTEM,
        "legacy breach",
        at=1000,
        incident_id="legacy-incident",
    )
    event = kill_switch_activated_event(
        state,
        actor=SYSTEM,
        reason=state.reason,
        at_ms=1000,
        event_version="1.0.0",
    )

    store.append_transition(
        expected_state=previous,
        state=state,
        action="ARM",
        recorded_at=1000,
        event_outbox=EventOutboxMessage(
            event=event,
            correlation_id=state.incident_id,
        ),
    )

    assert event.timestamp == 1000
    assert store.load_state() == state
    assert store.chain_valid()


def test_arm_record_failure_leaves_default_state_and_no_orphan_event():
    store = KillSwitchStateStore(":memory:", create=True)
    store._conn.execute(
        "CREATE TRIGGER reject_arm BEFORE INSERT ON kill_switch_transitions "
        "WHEN NEW.action='ARM' "
        "BEGIN SELECT RAISE(ABORT, 'reject arm'); END"
    )

    with pytest.raises(sqlite3.DatabaseError, match="reject arm"):
        arm_and_record(
            store,
            SYSTEM,
            "drawdown breach",
            at=1000,
            incident_id="incident-1",
        )

    assert store.load_state().tripped is False
    assert store.pending_event_outbox() == ()


def test_multi_party_reset_state_and_event_commit_together():
    store = KillSwitchStateStore(":memory:", create=True)
    armed = arm_and_record(
        store,
        SYSTEM,
        "drawdown breach",
        at=1000,
        incident_id="incident-1",
    )

    reset = reset_with_grant_and_record(
        store,
        CAROL,
        _grant(armed.incident_id),
        at=2000,
    )

    assert reset.tripped is False
    assert store.load_state() == reset
    pending = store.pending_event_outbox()
    assert [row["event"].event_type for row in pending] == [
        EVENT_KILL_SWITCH_ACTIVATED,
        EVENT_KILL_SWITCH_RESET,
    ]
    assert pending[1]["causation_id"] == pending[0]["event"].event_id


def test_stale_cross_connection_transition_is_rejected_by_state_cas(tmp_path):
    path = str(tmp_path / "kill_switch.db")
    first = KillSwitchStateStore(path, create=True)
    second = KillSwitchStateStore(path, create=True)
    stale = first.load_state()

    arm_and_record(
        first,
        SYSTEM,
        "first breach",
        at=1000,
        incident_id="incident-1",
    )
    stale_machine = KillSwitch(stale)
    stale_next = stale_machine.arm(
        SYSTEM,
        "stale breach",
        at=1001,
        incident_id="incident-stale",
    )
    stale_event = kill_switch_activated_event(
        stale_next,
        actor=SYSTEM,
        reason="stale breach",
        at_ms=1001,
    )

    with pytest.raises(KillSwitchStateConflict, match="state changed"):
        second.append_transition(
            expected_state=stale,
            state=stale_next,
            action="ARM",
            recorded_at=1001,
            event_outbox=EventOutboxMessage(
                event=stale_event,
                correlation_id=stale_next.incident_id,
            ),
        )

    assert second.load_state().incident_id == "incident-1"


def test_store_rejects_rearm_that_would_rewrite_latched_incident():
    store = KillSwitchStateStore(":memory:", create=True)
    armed = arm_and_record(
        store,
        SYSTEM,
        "first breach",
        at=1000,
        incident_id="incident-1",
    )
    rewritten = KillSwitch(armed).arm(
        SYSTEM,
        "second breach",
        at=1001,
        incident_id="incident-2",
    )
    event = kill_switch_activated_event(
        rewritten,
        actor=SYSTEM,
        reason="second breach",
        at_ms=1001,
    )

    with pytest.raises(KillSwitchStateStoreError, match="existing latched incident"):
        store.append_transition(
            expected_state=armed,
            state=rewritten,
            action="ARM",
            recorded_at=1001,
            event_outbox=EventOutboxMessage(
                event=event,
                correlation_id=rewritten.incident_id,
            ),
        )

    assert store.load_state() == armed
    assert len(store.pending_event_outbox()) == 1


def test_store_rejects_arm_event_whose_redacted_reason_does_not_match_state():
    store = KillSwitchStateStore(":memory:", create=True)
    previous = store.load_state()
    state = KillSwitch(previous).arm(
        SYSTEM,
        "actual breach",
        at=1000,
        incident_id="incident-1",
    )
    forged_event = kill_switch_activated_event(
        state,
        actor=SYSTEM,
        reason="different breach",
        at_ms=1000,
    )

    with pytest.raises(KillSwitchStateStoreError, match="event does not match state"):
        store.append_transition(
            expected_state=previous,
            state=state,
            action="ARM",
            recorded_at=1000,
            event_outbox=EventOutboxMessage(
                event=forged_event,
                correlation_id=state.incident_id,
            ),
        )


def test_store_rejects_reset_with_wrong_activation_causation():
    store = KillSwitchStateStore(":memory:", create=True)
    armed = arm_and_record(
        store,
        SYSTEM,
        "drawdown breach",
        at=1000,
        incident_id="incident-1",
    )
    reset = KillSwitch(armed).reset_with_grant(
        CAROL,
        _grant(armed.incident_id),
        at=2000,
    )
    event = kill_switch_reset_event(
        armed,
        actor=CAROL,
        reset_via="multi_party",
        at_ms=2000,
    )

    with pytest.raises(KillSwitchStateStoreError, match="causally reference"):
        store.append_transition(
            expected_state=armed,
            state=reset,
            action="RESET",
            recorded_at=2000,
            event_outbox=EventOutboxMessage(
                event=event,
                correlation_id=armed.incident_id,
                causation_id="wrong-activation-event",
            ),
        )

    assert store.load_state() == armed


def test_dispatch_recovers_publish_before_ack_without_duplicate():
    store = KillSwitchStateStore(":memory:", create=True)
    arm_and_record(
        store,
        SYSTEM,
        "drawdown breach",
        at=1000,
        incident_id="incident-1",
    )
    pending = store.pending_event_outbox()[0]
    bus = DurableEventBus(":memory:")
    bus.publish_once(
        pending["event"],
        correlation_id=pending["correlation_id"],
        causation_id=pending["causation_id"],
    )

    summary = dispatch_kill_switch_event_outbox(store, bus, at_ms=1001)

    assert summary == {"dispatched": 1, "pending": 0}
    assert bus.count() == 1


def test_transition_tamper_breaks_state_and_outbox_reads():
    store = KillSwitchStateStore(":memory:", create=True)
    arm_and_record(
        store,
        SYSTEM,
        "drawdown breach",
        at=1000,
        incident_id="incident-1",
    )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM kill_switch_transitions")

    store._conn.execute("DROP TRIGGER kill_switch_transitions_no_update")
    store._conn.execute("UPDATE kill_switch_transitions SET state_hash='evil'")
    store._conn.commit()

    assert not store.chain_valid()
    with pytest.raises(KillSwitchStateStoreError):
        store.load_state()
    with pytest.raises(KillSwitchStateStoreError):
        store.pending_event_outbox()


def test_corrupt_store_constructor_releases_its_file_handle(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(KillSwitchStateStoreError):
        KillSwitchStateStore(str(path), create=False)

    path.unlink()
    assert not path.exists()


def test_read_only_snapshot_retains_exact_history_and_refreshes_on_new_open(tmp_path):
    from quantos.contracts.killswitch import KillSwitchState
    path=tmp_path/'protected.db'
    writer=KillSwitchStateStore(str(path),create=True)
    writer.bootstrap(KillSwitchState(),recorded_at=0)
    reader=KillSwitchStateStore(str(path),read_only=True)
    before=reader.verified_snapshot()
    assert before['transition_count']==1 and before['recorded_at']==0 and before['state']==KillSwitchState()
    arm_and_record(writer,SYSTEM,'isolated test stop',at=200,incident_id='readonly-test')
    assert reader.verified_snapshot()==before  # One pinned read transaction.
    with pytest.raises(sqlite3.OperationalError):reader._conn.execute('CREATE TABLE forbidden(x)')
    reader.close()
    refreshed=KillSwitchStateStore(str(path),read_only=True)
    after=refreshed.verified_snapshot();refreshed.close();writer.close()
    assert after['transition_count']==2 and after['recorded_at']==200 and after['state'].tripped
    assert after['record_hash']!=before['record_hash']


def test_read_only_snapshot_does_not_create_bootstrap_or_accept_empty_history(tmp_path):
    path=tmp_path/'missing.db'
    with pytest.raises(KillSwitchStateStoreError):KillSwitchStateStore(str(path),read_only=True)
    assert not path.exists()
    writer=KillSwitchStateStore(str(path),create=True);writer.close()
    reader=KillSwitchStateStore(str(path),read_only=True)
    with pytest.raises(KillSwitchStateStoreError,match='empty'):reader.verified_snapshot()
    reader.close()
    with pytest.raises(ValueError):KillSwitchStateStore(str(path),create=True,read_only=True)


def test_read_only_history_budget_rejects_before_parsing_unbounded_rows(tmp_path):
    path=tmp_path/'oversized-history.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE kill_switch_transitions (n INTEGER)')
        conn.execute('CREATE TABLE kill_switch_event_dispatch (n INTEGER)')
        conn.execute('WITH RECURSIVE count(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM count WHERE n<10001) INSERT INTO kill_switch_transitions SELECT n FROM count')
    with pytest.raises(KillSwitchStateStoreError,match='bounded replay'):
        KillSwitchStateStore(str(path),read_only=True)
