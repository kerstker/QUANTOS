import sqlite3

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.events import alpha_promoted_event
from services.monitoring.domain_event_runtime import (
    dispatch_experiment_event_outbox,
)
from services.monitoring.event_bus import DurableEventBus
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore


def _catalog(state: AlphaState) -> AlphaCatalog:
    catalog = AlphaCatalog()
    catalog.register(
        AlphaDefinition(alpha_id="a1", name="alpha-1", factor_ids=("f1",))
    )
    catalog._set_state("a1", state)
    return catalog


def _message() -> EventOutboxMessage:
    return EventOutboxMessage(
        event=alpha_promoted_event(
            request_id="request-1",
            alpha_id="a1",
            src="PAPER_TRADING",
            dst="APPROVED",
            actor_id="issuer|carol",
            grant_head_hash="a" * 64,
            eval_run_id="eval-1",
            evidence_notes=(),
            at_ms=20,
        ),
        correlation_id="request-1",
        causation_id="a" * 64,
    )


def test_catalog_snapshot_and_promotion_outbox_commit_atomically():
    registry = ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))
    message = _message()

    registry.save(
        _catalog(AlphaState.APPROVED),
        now=20,
        event_outbox=(message,),
    )

    restored = AlphaCatalog()
    registry.load_into(restored)
    assert restored.state("a1") == AlphaState.APPROVED
    pending = registry.pending_event_outbox()
    assert len(pending) == 1
    assert pending[0]["event"] == message.event
    assert pending[0]["event"].event_version == "1.1.0"
    assert pending[0]["event"].timestamp == 20_000
    assert registry.event_outbox_chain_valid()


def test_outbox_failure_rolls_back_alpha_state_in_same_transaction(monkeypatch):
    store = SqliteExperimentStore(":memory:", create=True)
    registry = ExperimentRegistry(store)
    registry.save(_catalog(AlphaState.PAPER_TRADING), now=10)
    def reject_experiment_outbox(stage: str) -> None:
        if stage == "after_outbox":
            raise sqlite3.DatabaseError("reject experiment outbox")

    monkeypatch.setattr(store, "_event_outbox_write_checkpoint", reject_experiment_outbox)

    with pytest.raises(sqlite3.DatabaseError, match="reject experiment outbox"):
        registry.save(
            _catalog(AlphaState.APPROVED),
            now=20,
            event_outbox=(_message(),),
        )

    restored = AlphaCatalog()
    registry.load_into(restored)
    assert restored.state("a1") == AlphaState.PAPER_TRADING
    assert registry.pending_event_outbox() == ()


def test_experiment_outbox_is_append_only_and_tamper_evident():
    store = SqliteExperimentStore(":memory:", create=True)
    registry = ExperimentRegistry(store)
    registry.save(
        _catalog(AlphaState.APPROVED),
        now=20,
        event_outbox=(_message(),),
    )

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM experiment_event_outbox")

    store._conn.execute("DROP TRIGGER experiment_event_outbox_no_update")
    store._conn.execute(
        "UPDATE experiment_event_outbox SET correlation_id='evil'"
    )
    store._conn.commit()
    assert not registry.event_outbox_chain_valid()


def test_dispatch_recovers_publish_before_ack_without_duplicate():
    registry = ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))
    message = _message()
    registry.save(
        _catalog(AlphaState.APPROVED),
        now=20,
        event_outbox=(message,),
    )
    bus = DurableEventBus(":memory:")

    bus.publish_once(
        message.event,
        correlation_id=message.correlation_id,
        causation_id=message.causation_id,
    )
    assert registry.pending_event_outbox()

    summary = dispatch_experiment_event_outbox(registry, bus, at_ms=21)

    assert summary == {"dispatched": 1, "pending": 0}
    assert bus.count() == 1
    assert registry.pending_event_outbox() == ()
