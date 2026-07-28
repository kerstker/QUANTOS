from __future__ import annotations

import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.events import alpha_promoted_event
from services.research.experiment_registry.registry import (
    ExperimentRegistry,
    prepare_evaluation_artifact,
)
from services.research.experiment_registry.family_schema_v2_semantics import (
    verify_evaluation_audit_state,
    verify_event_outbox_state,
    verify_search_state,
)
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import ExperimentStoreError


def _message() -> EventOutboxMessage:
    return EventOutboxMessage(
        event=alpha_promoted_event(
            request_id="request-1",
            alpha_id="alpha-1",
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


def _catalog() -> AlphaCatalog:
    catalog = AlphaCatalog()
    catalog.register(
        AlphaDefinition(
            alpha_id="alpha-1",
            name="alpha-1",
            factor_ids=("factor-1",),
        )
    )
    catalog._set_state("alpha-1", AlphaState.APPROVED)
    return catalog


def _populated_store() -> tuple[SqliteExperimentStore, EventOutboxMessage]:
    store = SqliteExperimentStore(":memory:", create=True)

    store.begin_search_attempt(
        search_id="stale-1",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    store.heartbeat_search_attempt("stale-1", recorded_at=20)
    assert store.recover_stale_search_attempts(
        recorded_at=100,
        timeout_ms=50,
    ) == ("stale-1",)

    store.begin_search_attempt(
        search_id="search-1",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=110,
    )
    store.register_search_candidates(
        "search-1",
        ("config-1",),
        registered_at=111,
    )
    store.heartbeat_search_attempt("search-1", recorded_at=112)
    write = prepare_evaluation_artifact(
        AlphaEvaluation(
            alpha_id="alpha-1",
            verdict=ValidationVerdict.PASS,
            reproducibility_hash="repro-1",
        ),
        "dataset-v1",
        113,
    )
    store.insert_evaluation(
        **write.__dict__,
        search_id="search-1",
        config_hash="config-1",
    )
    store.finish_search_attempt(
        "search-1",
        status="COMPLETED",
        recorded_at=114,
    )

    message = _message()
    ExperimentRegistry(store).save(
        _catalog(),
        now=120,
        event_outbox=(message,),
    )
    store.mark_event_dispatched(
        message.outbox_id,
        dispatched_at=121,
        event_repro_hash=message.event.reproducibility_hash,
    )
    store.mark_evaluation_audit_dispatched(
        f"exp-{write.run_id}",
        dispatched_at=122,
        audit_entry_hash="b" * 64,
    )
    store.save_bandit('{"arms":{"guided":1}}')
    return store, message


def test_existing_private_v2_verifiers_characterization() -> None:
    store, message = _populated_store()

    event_head, candidate_head, attempts = store._verified_search_state()
    assert len(event_head) == len(candidate_head) == 64
    assert attempts == {
        "stale-1": {
            "metadata": ("TEST", "tests.research", "dataset-v1"),
            "status": "FAILED",
            "started_at": 10,
            "last_activity_at": 20,
            "terminal_at": 100,
            "terminal_event_hash": attempts["stale-1"]["terminal_event_hash"],
        },
        "search-1": {
            "metadata": ("TEST", "tests.research", "dataset-v1"),
            "status": "COMPLETED",
            "started_at": 110,
            "last_activity_at": 113,
            "terminal_at": 114,
            "terminal_event_hash": attempts["search-1"]["terminal_event_hash"],
        },
    }
    assert all(
        isinstance(attempt["terminal_event_hash"], str)
        and len(attempt["terminal_event_hash"]) == 64
        for attempt in attempts.values()
    )

    outbox_head, messages, event_dispatch_head = store._verified_outbox_state()
    assert len(outbox_head) == len(event_dispatch_head) == 64
    assert messages == {message.outbox_id: message}

    audit_head, intents, audit_dispatch_head = (
        store._verified_evaluation_audit_state()
    )
    assert len(audit_head) == len(audit_dispatch_head) == 64
    audit_event_id = next(iter(intents))
    assert audit_event_id.startswith("exp-")
    assert intents[audit_event_id][0] == next(iter(store.list_evaluations("alpha-1")))[
        "run_id"
    ]
    assert intents[audit_event_id][1].metadata == {
        "content_hash": next(iter(store.list_evaluations("alpha-1")))[
            "content_hash"
        ],
        "run_id": next(iter(store.list_evaluations("alpha-1")))["run_id"],
        "verdict": "PASS",
    }


def test_shared_v2_semantics_match_existing_store_private_contract() -> None:
    store, _ = _populated_store()

    search = verify_search_state(store._conn)
    assert (search.event_head, search.candidate_head, search.attempts) == (
        store._verified_search_state()
    )
    assert all(
        len(head) == 64
        for head in (
            search.event_head,
            search.candidate_head,
            search.heartbeat_head,
            search.recovery_head,
            search.evaluation_link_head,
        )
    )

    outbox = verify_event_outbox_state(store._conn)
    assert (outbox.outbox_head, outbox.messages, outbox.dispatch_head) == (
        store._verified_outbox_state()
    )

    audit = verify_evaluation_audit_state(store._conn)
    assert (audit.audit_head, audit.intents, audit.dispatch_head) == (
        store._verified_evaluation_audit_state()
    )


@pytest.mark.parametrize(
    ("trigger", "statement", "verify", "error"),
    (
        (
            "search_attempt_events_no_update",
            "UPDATE search_attempt_events SET event_hash='evil' WHERE event_seq=1",
            "search",
            "search event 1 hash mismatch",
        ),
        (
            "experiment_event_outbox_no_update",
            "UPDATE experiment_event_outbox SET record_hash='evil' WHERE outbox_seq=1",
            "outbox",
            "experiment outbox row 1 record hash mismatch",
        ),
        (
            "evaluation_audit_outbox_no_update",
            "UPDATE evaluation_audit_outbox SET record_hash='evil' WHERE audit_seq=1",
            "audit",
            "evaluation audit row 1 hash mismatch",
        ),
    ),
)
def test_existing_private_v2_verifiers_error_characterization(
    trigger: str,
    statement: str,
    verify: str,
    error: str,
) -> None:
    store, _ = _populated_store()
    trigger_sql = store._conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?",
        (trigger,),
    ).fetchone()[0]
    store._conn.execute(f'DROP TRIGGER "{trigger}"')
    store._conn.execute(statement)
    store._conn.execute(trigger_sql)
    store._conn.commit()

    verifiers = {
        "search": store._verified_search_state,
        "outbox": store._verified_outbox_state,
        "audit": store._verified_evaluation_audit_state,
    }
    with pytest.raises(ExperimentStoreError, match=error):
        verifiers[verify]()


def test_existing_private_v2_verifiers_are_read_only() -> None:
    store, _ = _populated_store()
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        store._verified_search_state()
        store._verified_outbox_state()
        store._verified_evaluation_audit_state()
    finally:
        store._conn.set_trace_callback(None)

    control_or_writes = tuple(
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(
            (
                "BEGIN",
                "COMMIT",
                "ROLLBACK",
                "CREATE",
                "ALTER",
                "DROP",
                "INSERT",
                "UPDATE",
                "DELETE",
            )
        )
    )
    assert control_or_writes == ()
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 2
