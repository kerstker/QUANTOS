import pytest

from quantos.contracts.events import Event, EVENT_RESEARCH_RISK_EVALUATED
from services.monitoring.event_bus import DurableEventBus
from services.monitoring.event_projection import DomainEventProjection
from services.execution.dry_run_store import DryRunOutboxEvent, DryRunRun, DryRunStore
from services.execution.events import dry_run_started_event
from tools.domain_event_bus import (
    DOMAIN_EVENT_PROJECTION_CONSUMER,
    dispatch_dry_run_outbox,
    project_pending_domain_events,
    save_and_project_dry_run,
)


@pytest.fixture(autouse=True)
def _exercise_event_mechanics_behind_admission(monkeypatch):
    """Authority rejection itself is covered by canonical-store tests."""
    monkeypatch.setattr(
        DryRunStore,
        "_require_authoritative_write",
        staticmethod(lambda _run, _authority: None),
    )


def _event(event_id: str, timestamp: int) -> Event:
    return Event(
        event_id=event_id,
        event_type="research.test.event",
        timestamp=timestamp,
        source="test",
        domain="research",
    )


def test_project_pending_domain_events_advances_durable_offset(tmp_path):
    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    bus = DurableEventBus(event_path)
    bus.publish(_event("e1", 1), correlation_id="C")
    bus.publish(_event("e2", 2), correlation_id="C", causation_id="e1")

    summary = project_pending_domain_events(
        bus,
        now_ms=100,
        projection_path=projection_path,
        worker_id="test-worker",
        schema_registry=None,
    )

    assert summary == {
        "assigned_partitions": [0],
        "consumer_id": DOMAIN_EVENT_PROJECTION_CONSUMER,
        "consumer_group_mode": "SINGLE_NODE_LEASED_PARTITIONS",
        "dead_letter_count": 0,
        "group_generation": 1,
        "idempotent_replays": 0,
        "offset": 2,
        "partition_count": 1,
        "projected": 2,
    }
    group = bus.consumer_group_status(
        DOMAIN_EVENT_PROJECTION_CONSUMER,
        now_ms=100,
    )
    assert group["active_workers"] == ()
    assert group["assignments"] == ("",)
    assert group["rebalance_required"] is False
    projection = DomainEventProjection(projection_path)
    assert [record["event"].event_id for record in projection.records()] == [
        "e1",
        "e2",
    ]

    repeated = project_pending_domain_events(
        bus,
        now_ms=200,
        projection_path=projection_path,
        worker_id="test-worker",
        schema_registry=None,
    )
    assert repeated["projected"] == 0
    assert repeated["offset"] == 2


def test_projection_leave_retries_when_another_worker_advances_group_clock(tmp_path):
    """Concurrent L2 projectors must not fail only because their exit clock is stale."""
    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    bus = DurableEventBus(event_path)
    bus.publish(_event("race-e1", 1), correlation_id="race")

    original_leave = bus.leave_consumer_group
    injected = False

    def leave_with_race(group_id, *, worker_id, now_ms):
        nonlocal injected
        if not injected:
            injected = True
            bus.heartbeat_consumer_group(
                group_id,
                worker_id="concurrent-worker",
                now_ms=200,
                partition_count=1,
                session_timeout_ms=120_000,
            )
        return original_leave(group_id, worker_id=worker_id, now_ms=now_ms)

    bus.leave_consumer_group = leave_with_race
    summary = project_pending_domain_events(
        bus,
        now_ms=100,
        projection_path=projection_path,
        worker_id="projection-worker",
        schema_registry=None,
    )

    assert summary["projected"] == 1
    assert injected is True
    assert bus.consumer_group_chain_valid()
    assert bus.consumer_group_status(
        DOMAIN_EVENT_PROJECTION_CONSUMER,
        now_ms=200,
    )["active_workers"] == ("concurrent-worker",)


def test_dispatch_outbox_recovers_publish_before_ack_without_duplicate(tmp_path):
    bus = DurableEventBus(str(tmp_path / "events.db"))
    store = DryRunStore(str(tmp_path / "dry_run.db"), create=True)
    event = _event("completed-r1", 1)
    message = DryRunOutboxEvent(
        run_id="r1",
        event=event,
        correlation_id="r1",
        causation_id="started-r1",
    )
    run = DryRunRun(
        run_id="r1",
        strategy_id="carry",
        timestamp=1,
        n_intents=1,
        n_approved=1,
        n_rejected=0,
        rejected_reasons=[],
        pnl={},
        audit_root="audit",
    )
    store.save_run_with_outbox(run, [message])

    bus.publish_once(event, correlation_id="r1", causation_id="started-r1")
    assert store.pending_outbox()  # simulated crash before dispatch ack

    summary = dispatch_dry_run_outbox(store, bus, at_ms=10)
    assert summary == {"dispatched": 1, "pending": 0}
    assert bus.count() == 1
    assert store.pending_outbox() == ()


def test_save_and_project_dry_run_is_load_bearing_end_to_end(tmp_path):
    bus = DurableEventBus(str(tmp_path / "events.db"))
    store = DryRunStore(str(tmp_path / "dry_run.db"), create=True)
    projection_path = str(tmp_path / "projection.db")
    run = DryRunRun(
        run_id="r1",
        strategy_id="carry",
        timestamp=10,
        n_intents=2,
        n_approved=2,
        n_rejected=0,
        rejected_reasons=[],
        pnl={},
        audit_root="audit",
        detail={"n_fills": 2},
    )
    started = dry_run_started_event(
        run_id="r1",
        strategy_id="carry",
        timestamp_ms=10,
        mode="single_period",
    )
    bus.publish_once(started, correlation_id="r1")

    summary = save_and_project_dry_run(
        store,
        run,
        bus,
        now_ms=10,
        projection_path=projection_path,
    )

    assert summary["dispatch"] == {"dispatched": 1, "pending": 0}
    assert store.get_run("r1")["detail"]["n_fills"] == 2
    assert store.pending_outbox() == ()
    records = bus.records(correlation_id="r1")
    assert [record["event"].event_type for record in records] == [
        "execution.dry_run.started",
        "execution.dry_run.completed",
    ]
    assert records[-1]["causation_id"] == started.event_id
    assert records[-1]["event"].event_version == "1.1.0"
    assert records[-1]["event"].payload["run_repro_hash"] == run.reproducibility_hash
    projection = DomainEventProjection(projection_path, create=False)
    assert [record["event"].event_id for record in projection.records()] == [
        started.event_id,
        records[-1]["event"].event_id,
    ]


def test_save_and_project_dry_run_can_defer_projection_for_verified_batch_rebuild(tmp_path):
    bus = DurableEventBus(str(tmp_path / "events.db"))
    store = DryRunStore(str(tmp_path / "dry_run.db"), create=True)
    projection_path = str(tmp_path / "projection.db")
    run = DryRunRun(
        run_id="deferred-r1",
        strategy_id="carry",
        timestamp=10,
        n_intents=1,
        n_approved=1,
        n_rejected=0,
        rejected_reasons=[],
        pnl={},
        audit_root="audit",
        detail={"n_fills": 1},
    )
    started = dry_run_started_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=run.timestamp,
        mode="multi_period",
    )
    bus.publish_once(started, correlation_id=run.run_id)

    summary = save_and_project_dry_run(
        store,
        run,
        bus,
        now_ms=run.timestamp,
        projection_path=projection_path,
        project=False,
    )

    assert summary["projection"]["skipped"] is True
    assert summary["dispatch"] == {"dispatched": 1, "pending": 0}
    assert not (tmp_path / "projection.db").exists()
    assert store.get_run(run.run_id)["strategy_id"] == "carry"
    bus.close()
    store.close()


def test_save_and_project_retains_risk_decision_with_exact_run_snapshot(tmp_path):
    bus = DurableEventBus(str(tmp_path / "events.db"))
    store = DryRunStore(str(tmp_path / "dry_run.db"), create=True)
    projection_path = str(tmp_path / "projection.db")
    run = DryRunRun(
        run_id="risk-run-1",
        strategy_id="crowd_contra",
        timestamp=20,
        n_intents=1,
        n_approved=1,
        n_rejected=0,
        rejected_reasons=[],
        pnl={},
        audit_root="audit",
        detail={
            "n_fills": 1,
            "research_risk": {
                "decision": "RISK_REJECTED",
                "gates_passed": 3,
                "gates_run": 4,
                "engines_run": {
                    "market": False,
                    "drawdown": True,
                    "concentration": True,
                    "stress": True,
                },
                "engines_skipped": {
                    "liquidity": "NO_HONEST_INPUT_AT_DRY_RUN_SEAM"
                },
                "reasons": ["market: not passed"],
                "provenance": "REAL_RESEARCH_RISK_SUBSET",
                "series": "vol_target",
                "config_provenance": "RISK_CONFIG_DEFAULTS_UNCALIBRATED_FOR_WEEKLY",
            },
        },
    )
    started = dry_run_started_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=run.timestamp,
        mode="single_period",
    )
    bus.publish_once(started, correlation_id=run.run_id)

    save_and_project_dry_run(
        store,
        run,
        bus,
        now_ms=20,
        projection_path=projection_path,
    )

    records = bus.records(correlation_id=run.run_id)
    assert [record["event"].event_type for record in records] == [
        "execution.dry_run.started",
        EVENT_RESEARCH_RISK_EVALUATED,
        "execution.dry_run.completed",
    ]
    risk_event = records[1]["event"]
    assert risk_event.payload["run_repro_hash"] == run.reproducibility_hash
    assert records[1]["causation_id"] == started.event_id
    assert records[2]["causation_id"] == risk_event.event_id
    assert store.outbox_chain_valid()
