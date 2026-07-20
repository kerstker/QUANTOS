from __future__ import annotations

import sqlite3
import struct
from collections.abc import Callable
from dataclasses import replace

import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.events import alpha_promoted_event
from services.research.experiment_registry.family_schema_v2_activation import (
    ExactV2ActivationSourceError,
    _verify_exact_v2_activation_source,
    _typed_bytes,
    verify_exact_v2_activation_source,
)
from services.research.experiment_registry.family_schema_v2_catalog import (
    EXACT_V2_INHERITED_SCHEMA_CATALOG,
)
from services.research.experiment_registry.family_schema_composition import (
    compose_family_schema_v3,
)
from services.research.experiment_registry.registry import (
    ExperimentRegistry,
    prepare_evaluation_artifact,
)
from services.research.experiment_registry.store import SqliteExperimentStore


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


class _EmptyBandit:
    @staticmethod
    def snapshot() -> dict[str, object]:
        return {
            "version": 1,
            "pulls": [],
            "reward_sum": [],
            "n_evaluated": 0,
            "proposed": [],
            "regret_history": [],
        }


class _ConnectionSubclass(sqlite3.Connection):
    pass


def _activation_store() -> SqliteExperimentStore:
    store = SqliteExperimentStore(":memory:", create=True)
    store.begin_search_attempt(
        search_id="stale-1",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    store.heartbeat_search_attempt("stale-1", recorded_at=20)
    store.recover_stale_search_attempts(recorded_at=100, timeout_ms=50)

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
    registry = ExperimentRegistry(store)
    registry.save(
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
    registry.save_bandit(_EmptyBandit(), dataset_version="dataset-v1")
    return store


def _exact_trigger_sql(connection: sqlite3.Connection, name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?",
        (name,),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _tamper_append_only_row(
    connection: sqlite3.Connection,
    *,
    trigger_name: str,
    statement: str,
) -> None:
    trigger_sql = _exact_trigger_sql(connection, trigger_name)
    connection.execute(f'DROP TRIGGER "{trigger_name}"')
    connection.execute(statement)
    connection.execute(trigger_sql)
    connection.commit()


def test_exact_v2_activation_source_is_read_only_and_deterministic() -> None:
    first = _activation_store()
    second = _activation_store()
    statements: list[str] = []
    total_changes = first._conn.total_changes
    first._conn.execute("PRAGMA query_only=ON")
    first._conn.set_trace_callback(statements.append)
    try:
        evidence = verify_exact_v2_activation_source(first._conn)
    finally:
        first._conn.set_trace_callback(None)
    twin = verify_exact_v2_activation_source(second._conn)

    assert evidence == twin
    assert evidence.catalog_id == "EXACT_V2_FAMILY_SCHEMA_INHERITED_CATALOG_V1"
    assert evidence.schema_version == 2
    assert len(evidence.origin_fingerprint) == 64
    assert evidence.origin_fingerprint == (
        "cf50cf4cbf9bcb248c39c7f0db6e0fc5b2b77712b31fe98fe8acb98338302036"
    )
    assert dict(evidence.row_counts) == {
        "alpha_state": 1,
        "bandit_state": 1,
        "evaluation_audit_dispatch": 1,
        "evaluation_audit_outbox": 1,
        "evaluations": 1,
        "experiment_event_dispatch": 1,
        "experiment_event_outbox": 1,
        "search_attempt_events": 4,
        "search_attempt_heartbeats": 2,
        "search_attempt_recoveries": 1,
        "search_candidates": 1,
        "search_evaluations": 1,
        "trials": 2,
    }
    assert all(value is not None for _, value in evidence.owned_sequences)
    assert first._conn.total_changes == total_changes
    assert not first._conn.in_transaction
    assert first._conn.execute("PRAGMA query_only").fetchone()[0] == 1
    assert first._conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert not any(
        statement.lstrip().upper().startswith(
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
        or statement.upper().replace(" ", "").startswith("PRAGMAUSER_VERSION=")
        for statement in statements
    )


def test_exact_v2_activation_accepts_an_empty_official_v2_store() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    evidence = verify_exact_v2_activation_source(store._conn)

    assert all(count == 0 for _, count in evidence.row_counts)
    assert all(head == "" for _, head in evidence.chain_heads)
    assert all(value is None for _, value in evidence.owned_sequences)


def test_exact_v2_activation_rejects_sqlite_connection_subclasses() -> None:
    connection = sqlite3.connect(":memory:", factory=_ConnectionSubclass)
    try:
        with pytest.raises(TypeError, match="exact sqlite3.Connection"):
            verify_exact_v2_activation_source(connection)
    finally:
        connection.close()


def test_origin_fingerprint_uses_explicit_sqlite_type_tags() -> None:
    assert _typed_bytes(None) == b"N"
    assert _typed_bytes(7) == b"I" + struct.pack(">q", 7)
    assert _typed_bytes(-0.0) == b"R" + struct.pack(">d", -0.0)
    assert _typed_bytes("7") == b"T7"
    assert _typed_bytes(b"7") == b"B7"
    assert len({_typed_bytes(7), _typed_bytes(7.0), _typed_bytes("7"), _typed_bytes(b"7")}) == 4


def test_origin_projection_change_changes_fingerprint_and_manifest_material() -> None:
    from services.research.experiment_registry.family_schema import (
        _canonical_manifest_value,
    )

    store = _activation_store()
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    changed_projection = replace(
        catalog.origin_projection,
        domain_separator=b"QUANTOS_EXACT_V2_LOGICAL_ORIGIN_V1_TEST_VARIANT",
    )

    baseline = _verify_exact_v2_activation_source(store._conn)
    changed = _verify_exact_v2_activation_source(
        store._conn,
        changed_projection,
    )
    composition = compose_family_schema_v3()
    changed_composition = replace(
        composition,
        inherited_schema_catalog=replace(
            catalog,
            origin_projection=changed_projection,
        ),
    )

    assert changed.origin_fingerprint != baseline.origin_fingerprint
    assert _canonical_manifest_value(changed_composition) != (
        _canonical_manifest_value(composition)
    )


def test_unknown_origin_projection_abi_fails_closed_before_reads() -> None:
    store = _activation_store()
    unknown_projection = replace(
        EXACT_V2_INHERITED_SCHEMA_CATALOG.origin_projection,
        projection_version=2,
    )
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ExactV2ActivationSourceError, match="unknown.*ABI"):
            _verify_exact_v2_activation_source(store._conn, unknown_projection)
    finally:
        store._conn.set_trace_callback(None)

    assert statements == []


def test_origin_fingerprint_is_logical_and_stable_across_vacuum() -> None:
    store = _activation_store()
    before = verify_exact_v2_activation_source(store._conn).origin_fingerprint
    store._conn.execute("VACUUM")
    after = verify_exact_v2_activation_source(store._conn).origin_fingerprint

    assert after == before


@pytest.mark.parametrize(
    "tamper",
    (
        lambda connection: connection.execute("CREATE TABLE unexpected(value TEXT)"),
        lambda connection: connection.execute(
            "CREATE TEMP TABLE unexpected_temp(value TEXT)"
        ),
        lambda connection: connection.execute(
            "DROP INDEX search_candidates_by_search"
        ),
        lambda connection: connection.execute(
            "DROP TRIGGER search_candidates_no_update"
        ),
        lambda connection: connection.execute("PRAGMA user_version=3"),
    ),
    ids=("extra-table", "temp-table", "missing-index", "missing-trigger", "version"),
)
def test_exact_v2_activation_rejects_schema_namespace_drift(
    tamper: Callable[[sqlite3.Connection], object],
) -> None:
    store = _activation_store()
    tamper(store._conn)
    store._conn.commit()

    with pytest.raises(ExactV2ActivationSourceError):
        verify_exact_v2_activation_source(store._conn)


@pytest.mark.parametrize(
    "tamper",
    (
        lambda connection: connection.execute(
            "UPDATE sqlite_sequence SET seq=999 WHERE name='search_candidates'"
        ),
        lambda connection: connection.execute(
            "INSERT INTO sqlite_sequence(name,seq) VALUES('unexpected',1)"
        ),
        lambda connection: connection.execute(
            "UPDATE alpha_state SET state='NOT_A_STATE'"
        ),
        lambda connection: connection.execute(
            "UPDATE alpha_state SET sharpe='not-real'"
        ),
        lambda connection: connection.execute(
            "UPDATE bandit_state SET snapshot_json='not-json' WHERE id=1"
        ),
        lambda connection: connection.execute(
            "UPDATE bandit_state SET snapshot_json='{}' WHERE id=1"
        ),
        lambda connection: _tamper_append_only_row(
            connection,
            trigger_name="trials_no_update",
            statement="UPDATE trials SET first_seen_ts=-1",
        ),
        lambda connection: _tamper_append_only_row(
            connection,
            trigger_name="trials_no_delete",
            statement="DELETE FROM trials WHERE config_hash<>'config-1'",
        ),
        lambda connection: _tamper_append_only_row(
            connection,
            trigger_name="search_attempt_events_no_update",
            statement="UPDATE search_attempt_events SET event_hash='evil' WHERE event_seq=1",
        ),
        lambda connection: _tamper_append_only_row(
            connection,
            trigger_name="evaluations_no_update",
            statement="UPDATE evaluations SET content_hash='evil'",
        ),
    ),
    ids=(
        "sequence",
        "unknown-sequence",
        "alpha-state",
        "storage-class",
        "bandit-json",
        "bandit-semantics",
        "trial-time",
        "alpha-trial-reference",
        "chain-hash",
        "evaluation-artifact",
    ),
)
def test_exact_v2_activation_rejects_semantic_tamper(
    tamper: Callable[[sqlite3.Connection], object],
) -> None:
    store = _activation_store()
    tamper(store._conn)
    store._conn.commit()

    with pytest.raises(ExactV2ActivationSourceError):
        verify_exact_v2_activation_source(store._conn)
