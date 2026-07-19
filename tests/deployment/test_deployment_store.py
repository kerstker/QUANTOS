"""Authoritative deployment evaluations with a transactional event outbox."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.governance import (
    ConfigVersion,
    Environment,
    StrategyVersion,
)
from services.deployment.runtime import (
    DEPLOYMENT_STATE_DB,
    evaluate_and_record_deployment,
    open_deployment_decision_store,
)
from services.deployment.store import (
    DeploymentDecisionStore,
    DeploymentEvaluation,
    DeploymentEvaluationState,
    DeploymentStoreError,
)
from services.governance.approval import approve, consume, open_request, review
from services.monitoring.domain_event_runtime import (
    dispatch_deployment_event_outbox,
)
from services.monitoring.event_bus import DurableEventBus


ALICE = Actor(actor_id="alice", kind=ActorKind.HUMAN)
BOB = Actor(actor_id="bob", kind=ActorKind.HUMAN)
CAROL = Actor(actor_id="carol", kind=ActorKind.HUMAN)


def _grant(subject: str):
    request = open_request(f"req-{subject}", subject, ALICE, at=1)
    reviewed = review(request, BOB, at=2)
    approved = approve(reviewed, CAROL, at=3)
    return consume(approved, subject, ALICE, at=4)


def _pinned():
    versions = (
        ConfigVersion(config_id="cfg", version="1", content_hash="a" * 16),
        StrategyVersion(
            strategy_id="strat",
            version="1",
            content_hash="b" * 16,
            approved=True,
        ),
    )
    return versions, {"cfg": "a" * 16, "strat": "b" * 16}


def _allowed(store, *, subject: str = "deploy-1", at: int = 100):
    versions, pins = _pinned()
    return evaluate_and_record_deployment(
        store,
        Environment.PAPER,
        subject=subject,
        grant=_grant(subject),
        versions=versions,
        approved_pins=pins,
        at=at,
    )


def test_paper_approval_is_recorded_as_eligible_not_applied_with_outbox():
    store = DeploymentDecisionStore(":memory:", create=True)
    decision = _allowed(store)

    assert decision.allowed is True
    record = store.latest_evaluation(subject="deploy-1")
    assert record is not None
    assert record.state is DeploymentEvaluationState.ELIGIBLE_NOT_APPLIED
    assert record.allowed is True
    assert record.environment is Environment.PAPER
    assert record.version_ids == ("cfg", "strat")
    assert record.causation_id == "req-deploy-1"
    pending = store.pending_event_outbox()
    assert len(pending) == 1
    assert pending[0]["event"].event_id == record.decision_id
    assert pending[0]["correlation_id"] == "deploy-1"
    assert pending[0]["causation_id"] == "req-deploy-1"


def test_live_at_l2_is_persisted_as_denied_never_deployed():
    store = DeploymentDecisionStore(":memory:", create=True)
    versions, pins = _pinned()
    decision = evaluate_and_record_deployment(
        store,
        Environment.LIVE,
        subject="live-1",
        grant=_grant("live-1"),
        versions=versions,
        approved_pins=pins,
        at=101,
    )

    assert decision.allowed is False
    assert {"READINESS_BELOW_LIVE", "LIVE_FLAGS_DISABLED"} <= set(
        decision.reasons
    )
    record = store.latest_evaluation(subject="live-1")
    assert record is not None
    assert record.state is DeploymentEvaluationState.DENIED
    assert record.allowed is False


def test_store_failure_turns_otherwise_allowed_decision_into_denial():
    class FailingStore:
        def record_evaluation(self, *args, **kwargs):
            raise OSError("state authority unavailable")

    decision = _allowed(FailingStore())
    assert decision.allowed is False
    assert decision.reasons == ("DEPLOYMENT_STATE_ERROR",)


def test_state_and_event_mismatch_rolls_back_atomically():
    source = DeploymentDecisionStore(":memory:", create=True)
    assert _allowed(source).allowed
    record = source.evaluations()[0]
    pending = source.pending_event_outbox()[0]
    message = EventOutboxMessage(
        event=pending["event"],
        correlation_id=pending["correlation_id"],
        causation_id=pending["causation_id"],
    )
    target = DeploymentDecisionStore(":memory:", create=True)

    with pytest.raises(DeploymentStoreError, match="does not match"):
        target.record_evaluation(
            replace(record, actor_id="forged-actor"),
            message,
        )
    assert target.evaluations() == ()
    assert target.pending_event_outbox() == ()


def test_exact_replay_is_idempotent_and_conflicting_replay_is_rejected():
    store = DeploymentDecisionStore(":memory:", create=True)
    assert _allowed(store).allowed
    assert _allowed(store).allowed
    assert len(store.evaluations()) == 1

    record = store.evaluations()[0]
    pending = store.pending_event_outbox()[0]
    message = EventOutboxMessage(
        event=pending["event"],
        correlation_id=pending["correlation_id"],
        causation_id=pending["causation_id"],
    )
    with pytest.raises(DeploymentStoreError, match="does not match"):
        store.record_evaluation(replace(record, subject="other"), message)


def test_distinct_decisions_in_the_same_millisecond_have_distinct_ids():
    store = DeploymentDecisionStore(":memory:", create=True)
    assert _allowed(store, subject="deploy-1", at=100).allowed
    denied = evaluate_and_record_deployment(
        store,
        Environment.PAPER,
        subject="deploy-1",
        grant=_grant("deploy-1"),
        at=100,
    )
    assert denied.allowed is False
    records = store.evaluations()
    assert len(records) == 2
    assert len({record.decision_id for record in records}) == 2


def test_subject_time_cannot_move_backwards():
    store = DeploymentDecisionStore(":memory:", create=True)
    assert _allowed(store, at=100).allowed
    stale = _allowed(store, at=99)
    assert stale.allowed is False
    assert stale.reasons == ("DEPLOYMENT_STATE_ERROR",)
    assert len(store.evaluations()) == 1


def test_only_denied_or_eligible_not_applied_states_exist():
    with pytest.raises(ValueError, match="state"):
        DeploymentEvaluation(
            decision_id="decision-1",
            environment=Environment.PAPER,
            subject="deploy",
            actor_id="alice",
            state="DEPLOYED",
            allowed=True,
            reasons=(),
            version_ids=("cfg",),
            evaluated_at_ms=1,
            causation_id="approval-1",
        )


def test_publish_before_ack_is_replayed_without_duplicate(tmp_path):
    path = str(tmp_path / "deployment.db")
    store = DeploymentDecisionStore(path, create=True)
    assert _allowed(store).allowed
    pending = store.pending_event_outbox()[0]
    bus = DurableEventBus(":memory:")
    bus.publish_once(
        pending["event"],
        correlation_id=pending["correlation_id"],
        causation_id=pending["causation_id"],
    )
    store.close()

    reopened = DeploymentDecisionStore(path)
    summary = dispatch_deployment_event_outbox(reopened, bus, at_ms=101)
    assert summary == {"dispatched": 1, "pending": 0}
    assert bus.count() == 1
    assert reopened.pending_event_outbox() == ()
    assert reopened.chain_valid()


def test_store_and_dispatch_chains_are_append_only_and_detect_tamper():
    store = DeploymentDecisionStore(":memory:", create=True)
    assert _allowed(store).allowed
    bus = DurableEventBus(":memory:")
    dispatch_deployment_event_outbox(store, bus, at_ms=101)

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute(
            "UPDATE deployment_evaluations SET actor_id='evil'"
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM deployment_event_dispatch")

    store._conn.execute("DROP TRIGGER deployment_evaluations_no_update")
    store._conn.execute("UPDATE deployment_evaluations SET actor_id='evil'")
    store._conn.commit()
    assert not store.chain_valid()
    with pytest.raises(DeploymentStoreError):
        store.evaluations()


def test_bypassed_dispatch_tamper_breaks_chain():
    store = DeploymentDecisionStore(":memory:", create=True)
    assert _allowed(store).allowed
    dispatch_deployment_event_outbox(store, DurableEventBus(":memory:"), at_ms=101)
    store._conn.execute("DROP TRIGGER deployment_event_dispatch_no_update")
    store._conn.execute(
        "UPDATE deployment_event_dispatch SET dispatched_at=999"
    )
    store._conn.commit()
    assert not store.chain_valid()


def test_uninitialized_store_fails_closed(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(DeploymentStoreError, match="unavailable"):
        DeploymentDecisionStore(str(path))
    assert not path.exists()


def test_composition_helper_uses_one_canonical_l2_path(tmp_path):
    assert DEPLOYMENT_STATE_DB.replace("\\", "/") == (
        "data/cache/deployment_state.db"
    )
    store = open_deployment_decision_store(
        str(tmp_path / "deployment_state.db")
    )
    assert store.chain_valid()


def test_concurrent_identical_writes_do_not_fork_history(tmp_path):
    path = str(tmp_path / "deployment.db")
    seed = DeploymentDecisionStore(":memory:", create=True)
    assert _allowed(seed).allowed
    record = seed.evaluations()[0]
    pending = seed.pending_event_outbox()[0]
    message = EventOutboxMessage(
        event=pending["event"],
        correlation_id=pending["correlation_id"],
        causation_id=pending["causation_id"],
    )
    first = DeploymentDecisionStore(path, create=True)
    second = DeploymentDecisionStore(path)
    barrier = Barrier(2)

    def write(store):
        barrier.wait()
        return store.record_evaluation(record, message)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            future.result()
            for future in (
                pool.submit(write, first),
                pool.submit(write, second),
            )
        )
    assert sorted(results) == [False, True]
    assert len(first.evaluations()) == 1
    assert first.chain_valid() and second.chain_valid()


def test_concurrent_first_openers_create_one_valid_schema(tmp_path):
    path = str(tmp_path / "deployment.db")
    barrier = Barrier(2)

    def open_store():
        barrier.wait()
        store = DeploymentDecisionStore(path, create=True)
        try:
            return store.chain_valid()
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = tuple(pool.submit(open_store) for _ in range(2))
        results = tuple(future.result() for future in futures)
    assert results == (True, True)
