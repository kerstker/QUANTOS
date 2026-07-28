"""Executable contracts for the 11 closed structural verifier operations."""

from __future__ import annotations

import dataclasses
import inspect
import sqlite3
from collections.abc import Iterator

import pytest

from quantos.contracts.canonical import canonical_json_bytes, canonical_sha256
from services.research.experiment_registry import family_schema_spec as fs
from services.research.experiment_registry.family_schema import _SqlRenderer
from services.research.experiment_registry.family_schema_composition import (
    compose_family_schema_v3,
)
from services.research.experiment_registry.family_schema_semantic_functions import (
    quantos_canonical_json_sha256_v1,
)
from services.research.experiment_registry.family_schema_structural_verifiers import (
    FAMILY_SCHEMA_STRUCTURAL_VERIFIER_IMPLEMENTATIONS,
    StructuralDependencyKind,
    StructuralVerificationError,
    StructuralVerificationFailure,
    verify_structural_plan,
)
from services.research.experiment_registry.family_schema_verifier_registry import (
    FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS,
)


_COMPOSITION = compose_family_schema_v3()
_GENESIS_HASH = "a" * 64


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    opened = sqlite3.connect(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table(table_name: str):
    return next(
        table for table in _COMPOSITION.tables if table.table_name == table_name
    )


def _inherited_table(table_name: str):
    return next(
        table
        for table in _COMPOSITION.inherited_schema_catalog.tables
        if table.table_name == table_name
    )


def _plan(operation: fs.StructuralVerificationOperation, text: str):
    matches = tuple(
        plan
        for plan in FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS
        if plan.operation is operation and text in plan.rule_key
    )
    assert len(matches) == 1
    return matches[0]


def _create_relaxed_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> None:
    try:
        table = _table(table_name)
    except StopIteration:
        table = _inherited_table(table_name)
    columns = ", ".join(
        f"{_quote(column.name)} {column.sql_type.value}" for column in table.columns
    )
    connection.execute(f"CREATE TABLE {_quote(table_name)} ({columns})")


def _default_row(table_name: str, suffix: str = "one") -> dict[str, object]:
    table = _table(table_name)
    row: dict[str, object] = {}
    for column in table.columns:
        if column.sql_type is fs.SqlType.INTEGER:
            value: object = 1
        elif column.sql_type is fs.SqlType.REAL:
            value = 1.0
        elif column.sql_type is fs.SqlType.BLOB:
            value = suffix.encode("ascii")
        else:
            value = f"{column.name}-{suffix}"
        row[column.name] = value
    row["record_type"] = table.row_hash.version_literal
    if "authority_instance_id" in row:
        row["authority_instance_id"] = "authority-1"
    if "genesis_record_hash" in row:
        row["genesis_record_hash"] = _GENESIS_HASH
    if table.chain is not None:
        row[table.chain.previous_hash_column] = _GENESIS_HASH
    row[table.row_hash.record_hash_column] = canonical_sha256(
        {"table": table_name, "suffix": suffix}
    )
    return row


def _default_inherited_row(
    table_name: str,
    suffix: str = "one",
) -> dict[str, object]:
    table = _inherited_table(table_name)
    row: dict[str, object] = {}
    for column in table.columns:
        if column.sql_type is fs.SqlType.INTEGER:
            value: object = 1
        elif column.sql_type is fs.SqlType.REAL:
            value = 1.0
        elif column.sql_type is fs.SqlType.BLOB:
            value = suffix.encode("ascii")
        else:
            value = f"{column.name}-{suffix}"
        row[column.name] = value
    return row


def _insert(
    connection: sqlite3.Connection,
    table_name: str,
    row: dict[str, object],
) -> None:
    columns = tuple(row)
    connection.execute(
        f"INSERT INTO {_quote(table_name)} ("
        + ", ".join(_quote(column) for column in columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")",
        tuple(row[column] for column in columns),
    )


def _genesis_row() -> dict[str, object]:
    row = _default_row("family_authority_genesis")
    row["authority_instance_id"] = "authority-1"
    row["record_hash"] = _GENESIS_HASH
    return row


def test_structural_callable_registry_exactly_covers_11_operations() -> None:
    registry = FAMILY_SCHEMA_STRUCTURAL_VERIFIER_IMPLEMENTATIONS

    assert tuple(item.operation for item in registry) == tuple(
        fs.StructuralVerificationOperation
    )
    assert len(registry) == 11
    assert len({item.implementation_id for item in registry}) == 11
    assert all(inspect.isfunction(item.implementation) for item in registry)
    assert all(
        item.implementation_id
        == f"FAMILY_SCHEMA_STRUCTURAL_HANDLER::{item.operation.value}"
        for item in registry
    )


def test_every_canonical_structural_plan_resolves_to_a_real_handler(
    connection: sqlite3.Connection,
) -> None:
    for table in _COMPOSITION.inherited_schema_catalog.tables:
        _create_relaxed_table(connection, table.table_name)
    for table in _COMPOSITION.tables:
        _create_relaxed_table(connection, table.table_name)
    connection.commit()

    results = tuple(
        verify_structural_plan(
            connection,
            _COMPOSITION,
            plan,
            dependency_callback=lambda _kind, _key: True,
        )
        for plan in FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS
    )

    assert len(results) == len(FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS) == 228
    assert all(result.passed for result in results)
    assert {result.operation for result in results} == set(
        fs.StructuralVerificationOperation
    )


def test_row_hash_replay_accepts_exact_hash_and_rejects_one_field_tamper(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.RECOMPUTE_ROW_HASH,
        "research_questions_canonical_row_hash",
    )
    table = _table("research_questions")
    _create_relaxed_table(connection, table.table_name)
    row = _default_row(table.table_name)
    row[table.row_hash.record_hash_column] = canonical_sha256(
        {field: row[field] for field in table.row_hash.canonical_fields}
    )
    _insert(connection, table.table_name, row)
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(
        'UPDATE "research_questions" SET "question_hash"=?',
        ("tampered-question-hash",),
    )
    connection.commit()
    result = verify_structural_plan(connection, _COMPOSITION, plan)

    assert not result.passed
    assert result.failure is StructuralVerificationFailure.DATA_MISMATCH


@pytest.mark.parametrize(
    "operation, rule_text",
    (
        (
            fs.StructuralVerificationOperation.VERIFY_ALL_FOREIGN_KEYS,
            "research_authority_events_foreign_keys",
        ),
        (
            fs.StructuralVerificationOperation.VERIFY_GENESIS_FOREIGN_KEY,
            "research_authority_events_genesis_binding",
        ),
    ),
)
def test_foreign_key_replay_accepts_parent_and_rejects_one_field_tamper(
    connection: sqlite3.Connection,
    operation: fs.StructuralVerificationOperation,
    rule_text: str,
) -> None:
    plan = _plan(operation, rule_text)
    _create_relaxed_table(connection, "family_authority_genesis")
    _create_relaxed_table(connection, "research_authority_events")
    _insert(connection, "family_authority_genesis", _genesis_row())
    event = _default_row("research_authority_events")
    event["event_ordinal"] = 1
    _insert(connection, "research_authority_events", event)
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(
        'UPDATE "research_authority_events" SET "genesis_record_hash"=?',
        ("b" * 64,),
    )
    connection.commit()

    assert not verify_structural_plan(connection, _COMPOSITION, plan).passed


def test_exact_foreign_key_replay_rejects_one_field_tamper(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.VERIFY_EXACT_FOREIGN_KEY,
        "research_question_aliases_question_reference",
    )
    _create_relaxed_table(connection, "research_questions")
    _create_relaxed_table(connection, "research_question_aliases")
    question = _default_row("research_questions")
    question["research_question_id"] = "question-1"
    alias = _default_row("research_question_aliases")
    alias["research_question_id"] = "question-1"
    _insert(connection, "research_questions", question)
    _insert(connection, "research_question_aliases", alias)
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(
        'UPDATE "research_question_aliases" SET "research_question_id"=?',
        ("question-missing",),
    )
    connection.commit()

    assert not verify_structural_plan(connection, _COMPOSITION, plan).passed


@pytest.mark.parametrize(
    "operation, rule_text",
    (
        (
            fs.StructuralVerificationOperation.VERIFY_HASH_CHAIN,
            "research_authority_events_complete_chain",
        ),
        (
            fs.StructuralVerificationOperation.VERIFY_TERMINAL_HEAD,
            "research_authority_events_terminal_head",
        ),
    ),
)
def test_chain_handlers_accept_genesis_head_and_reject_previous_hash_tamper(
    connection: sqlite3.Connection,
    operation: fs.StructuralVerificationOperation,
    rule_text: str,
) -> None:
    plan = _plan(operation, rule_text)
    _create_relaxed_table(connection, "research_authority_events")
    event = _default_row("research_authority_events")
    event["event_ordinal"] = 1
    event["prev_record_hash"] = _GENESIS_HASH
    _insert(connection, "research_authority_events", event)
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(
        'UPDATE "research_authority_events" SET "prev_record_hash"=?',
        ("b" * 64,),
    )
    connection.commit()

    assert not verify_structural_plan(connection, _COMPOSITION, plan).passed


def test_primary_key_totality_rejects_one_null_field_tamper(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.VERIFY_PRIMARY_KEY_TOTALITY,
        "strategy_config_lineage_bindings_config_single_root",
    )
    _create_relaxed_table(connection, "strategy_config_lineage_bindings")
    _insert(
        connection,
        "strategy_config_lineage_bindings",
        _default_row("strategy_config_lineage_bindings"),
    )
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(
        'UPDATE "strategy_config_lineage_bindings" SET "config_hash"=NULL'
    )
    connection.commit()

    assert not verify_structural_plan(connection, _COMPOSITION, plan).passed


def test_unique_index_replay_rejects_one_field_tamper(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.VERIFY_UNIQUE_INDEX,
        "research_question_aliases_global_alias_immutability",
    )
    _create_relaxed_table(connection, "research_question_aliases")
    first = _default_row("research_question_aliases", "one")
    second = _default_row("research_question_aliases", "two")
    first["alias"] = "alpha"
    second["alias"] = "beta"
    _insert(connection, "research_question_aliases", first)
    _insert(connection, "research_question_aliases", second)
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(
        'UPDATE "research_question_aliases" SET "alias"=? WHERE "alias_hash"=?',
        ("alpha", second["alias_hash"]),
    )
    connection.commit()

    assert not verify_structural_plan(connection, _COMPOSITION, plan).passed


def test_check_constraint_replay_rejects_one_field_tamper(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.VERIFY_CHECK_CONSTRAINT,
        "research_authority_events_closed_action_enum",
    )
    _create_relaxed_table(connection, "research_authority_events")
    event = _default_row("research_authority_events")
    event["action"] = "QUESTION_ISSUED"
    _insert(connection, "research_authority_events", event)
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(
        'UPDATE "research_authority_events" SET "action"=?',
        ("NOT_AN_AUTHORITY_ACTION",),
    )
    connection.commit()

    assert not verify_structural_plan(connection, _COMPOSITION, plan).passed


def test_insert_guard_replay_catches_row_inserted_while_exact_guard_was_dropped(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.REPLAY_INSERT_GUARD,
        "research_questions_canonical_question_hash",
    )
    table = _table("research_questions")
    source = next(
        rule.structural_source
        for rule in table.verification_rules
        if rule.rule_id == "research_questions_canonical_question_hash"
    )
    assert source is not None
    trigger = next(item for item in table.triggers if item.name == source.source_id)
    exact_trigger_sql = _SqlRenderer(_COMPOSITION.renderer_grammar).trigger(
        table.table_name,
        trigger,
    )
    connection.create_function(
        "quantos_canonical_json_sha256_v1",
        2,
        quantos_canonical_json_sha256_v1,
        deterministic=True,
    )
    _create_relaxed_table(connection, table.table_name)
    connection.execute(exact_trigger_sql)
    payload = {"question": "does the edge survive?"}
    row = _default_row(table.table_name)
    row["question_payload_json"] = canonical_json_bytes(payload).decode("utf-8")
    row["question_hash"] = canonical_sha256(payload)
    _insert(connection, table.table_name, row)
    connection.commit()

    assert verify_structural_plan(connection, _COMPOSITION, plan).passed

    connection.execute(f"DROP TRIGGER {_quote(trigger.name)}")
    bad_row = _default_row(table.table_name, "two")
    bad_row["question_payload_json"] = canonical_json_bytes(payload).decode("utf-8")
    bad_row["question_hash"] = "b" * 64
    _insert(connection, table.table_name, bad_row)
    connection.execute(exact_trigger_sql)
    connection.commit()
    restored = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?",
        (trigger.name,),
    ).fetchone()

    assert restored == (exact_trigger_sql,)
    assert not verify_structural_plan(connection, _COMPOSITION, plan).passed


def test_delegated_extension_requires_every_map_or_callback_dependency(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.VERIFY_DELEGATED_EXTENSION,
        "global_oos_exposures_global_exposure_fingerprint",
    )
    _create_relaxed_table(connection, "global_oos_exposures")
    connection.commit()

    missing = verify_structural_plan(connection, _COMPOSITION, plan)
    mapped = verify_structural_plan(
        connection,
        _COMPOSITION,
        plan,
        verified_verifier_results={"VERIFY_EXPOSURE_FINGERPRINT": True},
    )
    tampered = verify_structural_plan(
        connection,
        _COMPOSITION,
        plan,
        verified_verifier_results={"VERIFY_EXPOSURE_FINGERPRINT": False},
    )
    callback = verify_structural_plan(
        connection,
        _COMPOSITION,
        plan,
        dependency_callback=lambda kind, key: (
            kind is StructuralDependencyKind.VERIFIER_CODE
            and key == "VERIFY_EXPOSURE_FINGERPRINT"
        ),
    )
    broken_callback = verify_structural_plan(
        connection,
        _COMPOSITION,
        plan,
        dependency_callback=lambda _kind, _key: (_ for _ in ()).throw(KeyError()),
    )

    assert not missing.passed
    assert missing.failure is StructuralVerificationFailure.MISSING_DEPENDENCY
    assert mapped.passed
    assert not tampered.passed
    assert callback.passed
    assert not broken_callback.passed


def test_verifier_is_read_only_and_rejects_plan_or_composition_drift(
    connection: sqlite3.Connection,
) -> None:
    plan = _plan(
        fs.StructuralVerificationOperation.RECOMPUTE_ROW_HASH,
        "research_questions_canonical_row_hash",
    )
    _create_relaxed_table(connection, "research_questions")
    connection.commit()
    total_changes = connection.total_changes
    traced: list[str] = []
    connection.execute("PRAGMA query_only=ON")
    connection.set_trace_callback(traced.append)
    try:
        result = verify_structural_plan(connection, _COMPOSITION, plan)
    finally:
        connection.set_trace_callback(None)

    assert result.passed
    assert connection.total_changes == total_changes
    assert not connection.in_transaction
    assert not any(
        statement.lstrip().upper().startswith(
            (
                "BEGIN",
                "COMMIT",
                "ROLLBACK",
                "CREATE",
                "DROP",
                "ALTER",
                "INSERT",
                "UPDATE",
                "DELETE",
            )
        )
        for statement in traced
    )

    with pytest.raises(StructuralVerificationError, match="not canonical"):
        verify_structural_plan(
            connection,
            _COMPOSITION,
            dataclasses.replace(
                plan,
                operation=fs.StructuralVerificationOperation.VERIFY_HASH_CHAIN,
            ),
        )
    with pytest.raises(StructuralVerificationError, match="not canonical"):
        verify_structural_plan(
            connection,
            dataclasses.replace(_COMPOSITION, schema_version=4),
            plan,
        )
