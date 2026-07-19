"""Pass-M deterministic SQL rendering for the exact family schema v3."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import sqlite3
from typing import get_args

import pytest

from services.research.experiment_registry import family_schema_spec as fs
from services.research.experiment_registry.family_schema_composition import (
    compose_family_schema_v3,
)


def _renderer_module():
    from services.research.experiment_registry import family_schema

    return family_schema


def _walk_dataclass_tree(value: object):
    if isinstance(value, tuple):
        for item in value:
            yield from _walk_dataclass_tree(item)
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        yield value
        for field in dataclasses.fields(value):
            yield from _walk_dataclass_tree(getattr(value, field.name))


def test_renderer_is_pure_deterministic_and_covers_the_complete_object_catalog() -> None:
    module = _renderer_module()
    composition = compose_family_schema_v3()

    first = module.render_family_schema_v3(composition)
    second = module.render_family_schema_v3(composition)

    assert first == second
    assert len(first) == 401
    assert tuple(item.ordinal for item in first) == tuple(range(1, 402))
    assert sum(item.object_type == "table" for item in first) == 33
    assert sum(item.object_type == "index" for item in first) == 153
    assert sum(item.object_type == "trigger" for item in first) == 215
    assert len({(item.object_type, item.object_name) for item in first}) == len(first)
    assert tuple(item.table_name for item in first if item.object_type == "table") == tuple(
        table.table_name for table in composition.tables
    )
    assert tuple(
        (item.object_type, item.object_name, item.table_name) for item in first
    ) == tuple(
        entry
        for table in composition.tables
        for entry in (
            ("table", table.table_name, table.table_name),
            *(
                ("index", index.name, table.table_name)
                for index in table.indexes
            ),
            *(
                ("trigger", trigger.name, table.table_name)
                for trigger in table.triggers
            ),
        )
    )

    for item in first:
        assert type(item.statement_bytes) is bytes
        assert item.statement_bytes == item.statement_sql.encode("utf-8")
        assert item.statement_sql == item.sqlite_schema_sql + ";\n"
        assert item.statement_sha256 == hashlib.sha256(item.statement_bytes).hexdigest()
        assert "IF NOT EXISTS" not in item.statement_sql
        assert "\r" not in item.statement_sql
        assert not item.statement_sql.startswith(("BEGIN ", "COMMIT", "ROLLBACK"))

    source = inspect.getsource(module)
    assert "import sqlite3" not in source
    assert "executescript" not in source


def test_renderer_golden_genesis_table_index_and_trigger_bytes_are_exact() -> None:
    objects = _renderer_module().render_family_schema_v3()

    assert objects[0].statement_sql == (
        'CREATE TABLE "family_authority_genesis" ('
        '"singleton_key" INTEGER NOT NULL, '
        '"authority_instance_id" TEXT NOT NULL, '
        '"v2_origin_fingerprint" TEXT NOT NULL, '
        '"sealed_v3_manifest_hash" TEXT NOT NULL, '
        '"activation_code_manifest_evidence_hash" TEXT NOT NULL, '
        '"migration_id" TEXT NOT NULL, '
        '"activated_at_us" INTEGER NOT NULL, '
        '"record_type" TEXT NOT NULL, '
        '"record_hash" TEXT NOT NULL, '
        'PRIMARY KEY ("singleton_key"), '
        'CONSTRAINT "family_authority_genesis_singleton_check" '
        'CHECK (COALESCE(("singleton_key" = 1), 0)), '
        'CONSTRAINT "family_authority_genesis_record_type_check" '
        "CHECK (COALESCE((\"record_type\" = 'FAMILY_AUTHORITY_GENESIS_V1'), 0)), "
        'CONSTRAINT "family_authority_genesis_migration_id_check" '
        "CHECK (COALESCE((\"migration_id\" = 'FAMILY_SCHEMA_V2_TO_V3_V1'), 0)), "
        'CONSTRAINT "family_authority_genesis_activated_at_us_positive_check" '
        'CHECK (COALESCE(("activated_at_us" > 0), 0))'
        ') STRICT;\n'
    )
    assert objects[1].statement_sql == (
        'CREATE UNIQUE INDEX "family_authority_genesis_lineage_unique" '
        'ON "family_authority_genesis" '
        '("authority_instance_id" COLLATE "BINARY" ASC, '
        '"record_hash" COLLATE "BINARY" ASC);\n'
    )
    assert objects[2].statement_sql == (
        'CREATE TRIGGER "family_authority_genesis_no_update" '
        'BEFORE UPDATE ON "family_authority_genesis" '
        "BEGIN SELECT RAISE(ABORT, 'FAMILY_AUTHORITY_GENESIS_IMMUTABLE'); END;\n"
    )


def test_every_rendered_table_rejects_real_update_and_delete_on_a_nonempty_row() -> None:
    """Execute all 66 immutable triggers, not merely compare their SQL text."""

    composition = compose_family_schema_v3()
    rendered = {
        (item.object_type, item.object_name): item
        for item in _renderer_module().render_family_schema_v3(composition)
    }
    immutable_kinds = {
        fs.TriggerPolicyKind.NO_UPDATE,
        fs.TriggerPolicyKind.NO_DELETE,
    }
    connection = sqlite3.connect(":memory:")
    try:
        for table in composition.tables:
            immutable = {
                trigger.kind: rendered[("trigger", trigger.name)]
                for trigger in table.triggers
                if trigger.kind in immutable_kinds
            }
            assert set(immutable) == immutable_kinds, table.table_name

            quoted = '"' + table.table_name.replace('"', '""') + '"'
            connection.execute(f"CREATE TABLE {quoted} (probe INTEGER NOT NULL)")
            for trigger in immutable.values():
                connection.execute(trigger.statement_sql)
            connection.execute(f"INSERT INTO {quoted} (probe) VALUES (1)")

            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(f"UPDATE {quoted} SET probe=2")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(f"DELETE FROM {quoted}")

            assert connection.execute(
                f"SELECT probe FROM {quoted}"
            ).fetchall() == [(1,)]
    finally:
        connection.close()


def test_renderer_emits_every_manifest_bound_semantic_function_name() -> None:
    module = _renderer_module()
    sql = b"".join(
        item.statement_bytes for item in module.render_family_schema_v3()
    ).decode("utf-8")
    grammar = compose_family_schema_v3().renderer_grammar

    for abi in grammar.semantic_function_abis:
        assert f'"{abi.sqlite_name}"(' in sql


def test_renderer_dispatch_covers_every_ast_node_used_by_the_canonical_tables() -> None:
    module = _renderer_module()
    nodes = tuple(_walk_dataclass_tree(compose_family_schema_v3().tables))
    scalar_types = set(get_args(fs.ScalarExpression))
    predicate_types = set(get_args(fs.PredicateExpression))

    used_scalars = {type(item).__name__ for item in nodes if type(item) in scalar_types}
    used_predicates = {
        type(item).__name__ for item in nodes if type(item) in predicate_types
    }
    used_related = {
        type(item).__name__
        for item in nodes
        if type(item).__name__
        in {
            "LookupBinding",
            "RelatedValueLookupSpec",
            "RelationAggregateOrderSpec",
            "RelationJoinAggregateOrderSpec",
            "RelationJoinEqualitySpec",
            "RelationJoinSpec",
            "RelationMetricSpec",
        }
    }
    used_schema_dispatch = {
        type(item).__name__
        for item in nodes
        if type(item).__name__
        in {
            "AuthorityTableSpec",
            "CheckConstraintSpec",
            "ColumnSpec",
            "ForeignKeySpec",
            "IndexColumnSpec",
            "IndexSpec",
            "InsertStateMachineSpec",
            "InsertStateTransition",
            "TriggerPolicySpec",
        }
    }

    assert used_scalars == {
        "CanonicalJsonFieldValue",
        "ColumnValue",
        "LiteralValue",
        "QualifiedColumnValue",
        "RelatedCanonicalJsonArrayLengthValueSpec",
        "RelatedCanonicalJsonArrayValueSpec",
    }
    assert used_predicates == {
        "CanonicalJsonPayloadHashExpression",
        "ChainPredecessorExpression",
        "ComparisonExpression",
        "LogicalExpression",
        "MembershipExpression",
        "NotExpression",
        "NullExpression",
        "PrefixStreamExtensionExpression",
        "RelatedCanonicalJsonArrayContainsExpression",
        "RelationAggregateExpression",
        "RelationArgExtremumExpression",
        "RelationChainPrefixExpression",
        "RelationExistsExpression",
        "RelationJoinAggregateExpression",
        "RelationLatestPerDomainPolicyExpression",
    }
    assert used_scalars <= set(module.SUPPORTED_SCALAR_NODE_TYPES)
    assert used_predicates <= set(module.SUPPORTED_PREDICATE_NODE_TYPES)
    assert used_related == set(module.SUPPORTED_RELATED_NODE_TYPES)
    assert used_schema_dispatch == set(module.SUPPORTED_SCHEMA_NODE_TYPES)
    assert module.UNSUPPORTED_PREDICATE_NODE_TYPES == (
        compose_family_schema_v3().renderer_grammar.unsupported_predicate_nodes
    )


def test_renderer_golden_literals_null_collation_foreign_keys_and_state_machine() -> None:
    module = _renderer_module()
    escaped_literal = fs.ComparisonExpression(
        left=fs.ColumnValue("quoted_identifier", source=fs.RowSource.NEW),
        operator=fs.ComparisonOperator.EQ,
        right=fs.LiteralValue("O'Brien"),
    )
    null_literal = fs.ComparisonExpression(
        left=fs.ColumnValue("nullable_value"),
        operator=fs.ComparisonOperator.EQ,
        right=fs.LiteralValue(None),
    )
    null_test = fs.NullExpression(
        value=fs.ColumnValue("nullable_value"),
        operator=fs.NullOperator.IS_NULL,
    )

    assert module._render_predicate_for_test(escaped_literal) == (
        '(NEW."quoted_identifier" = \'O\'\'Brien\')'
    )
    assert module._render_predicate_for_test(null_literal) == (
        '("nullable_value" = NULL)'
    )
    assert module._render_predicate_for_test(null_test) == (
        '("nullable_value" IS NULL)'
    )

    objects = {item.object_name: item for item in module.render_family_schema_v3()}
    table = objects["selection_scope_outcomes"]
    partial_index = objects[
        "partial_unique_selection_scope_outcomes_winner_evidence"
    ]
    state_machine = objects[
        "data_snooping_oos_access_attempts_attempt_chain_append_next"
    ]

    assert table.statement_sha256 == (
        "dc99a858619026653faa2fda7caae1a5862dc394afeff031c9a71e93619b14fa"
    )
    assert (
        'FOREIGN KEY ("authority_instance_id", "genesis_record_hash") '
        'REFERENCES "family_authority_genesis" '
        '("authority_instance_id", "record_hash") '
        "ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE"
    ) in table.statement_sql
    assert partial_index.statement_sql == (
        'CREATE UNIQUE INDEX '
        '"partial_unique_selection_scope_outcomes_winner_evidence" '
        'ON "selection_scope_outcomes" '
        '("evidence_ref_hash" COLLATE "BINARY" ASC) '
        "WHERE (\"outcome\" = 'WINNER_PINNED');\n"
    )
    assert state_machine.statement_sha256 == (
        "bba9d1f361fe5321d96d12223ee5aa47a0a19898584d14d4f894fab9eb8e6b46"
    )
    assert 'NEW."sequence_no" = 0' in state_machine.statement_sql
    assert 'NEW."sequence_no" - 1' in state_machine.statement_sql
    assert "'ATTEMPT_CHAIN_APPEND_NEXT'" in state_machine.statement_sql


def test_unknown_expression_node_fails_closed() -> None:
    module = _renderer_module()

    with pytest.raises(module.FamilySchemaRenderError, match="unknown predicate node"):
        module._render_predicate_for_test(object())

    disabled = object.__new__(fs.RelationPolicyAggregateExpression)
    with pytest.raises(module.FamilySchemaRenderError, match="disabled by grammar"):
        module._render_predicate_for_test(disabled)


def test_unused_but_enabled_offset_and_set_equality_ast_lowerings_parse() -> None:
    module = _renderer_module()
    offset = fs.ComparisonExpression(
        left=fs.OrdinalOffsetValue(
            fs.ColumnValue("sequence_no", source=fs.RowSource.NEW),
            -1,
        ),
        operator=fs.ComparisonOperator.EQ,
        right=fs.LiteralValue(0),
    )
    assert module._render_predicate_for_test(offset) == (
        '((NEW."sequence_no" - 1) = 0)'
    )

    binding = fs.LookupBinding(
        "scope_id",
        fs.ColumnValue("scope_id", source=fs.RowSource.NEW),
    )
    expression = fs.RelationSetEqualityExpression(
        left_table="left_rows",
        left_bindings=(binding,),
        left_value="value",
        left_predicate=None,
        left_include_values=(
            fs.ColumnValue("value", source=fs.RowSource.NEW),
        ),
        right_table="right_rows",
        right_bindings=(binding,),
        right_value="value",
        right_predicate=None,
        order=(fs.RelationAggregateOrderSpec("value"),),
        negated=False,
    )
    predicate_sql = module._render_predicate_for_test(expression)
    assert 'SELECT DISTINCT "q3"."value" AS "value"' in predicate_sql
    assert 'ORDER BY "q3"."value" COLLATE "BINARY" ASC' in predicate_sql

    class _HashAggregate:
        def step(self, _value: object) -> None:
            pass

        def finalize(self) -> str:
            return "0" * 64

    connection = sqlite3.connect(":memory:")
    try:
        connection.create_aggregate(
            "quantos_canonical_ordered_value_sha256_v1",
            1,
            _HashAggregate,
        )
        connection.execute(
            'CREATE TABLE "left_rows" '
            '("scope_id" TEXT NOT NULL, "value" TEXT NOT NULL) STRICT'
        )
        connection.execute(
            'CREATE TABLE "right_rows" '
            '("scope_id" TEXT NOT NULL, "value" TEXT NOT NULL) STRICT'
        )
        connection.execute(
            'CREATE TABLE "current_rows" '
            '("scope_id" TEXT NOT NULL, "value" TEXT NOT NULL) STRICT'
        )
        connection.execute(
            'CREATE TRIGGER "set_equality_guard" BEFORE INSERT ON "current_rows" '
            f"WHEN COALESCE({predicate_sql}, 1) "
            "BEGIN SELECT RAISE(ABORT, 'SET_MISMATCH'); END"
        )
    finally:
        connection.close()


def test_renderer_rejects_noncanonical_composition_or_grammar() -> None:
    module = _renderer_module()
    composition = compose_family_schema_v3()

    with pytest.raises(TypeError, match="FamilySchemaComposition"):
        module.render_family_schema_v3(object())

    tampered_composition = dataclasses.replace(composition, schema_version=4)
    with pytest.raises(ValueError, match="unknown family schema renderer version"):
        dataclasses.replace(
            composition.renderer_grammar,
            renderer_version="QUANTOS_SQL_RENDERER_TAMPERED",
        )
    tampered_table = dataclasses.replace(
        composition.tables[0],
        table_name="family_authority_genesis_tampered",
    )

    for tampered in (
        tampered_composition,
        dataclasses.replace(
            composition,
            tables=(tampered_table, *composition.tables[1:]),
        ),
    ):
        with pytest.raises(
            module.FamilySchemaRenderError,
            match="canonical exact-v3",
        ):
            module.render_family_schema_v3(tampered)


def test_sqlite_machine_round_trip_matches_every_sealed_sqlite_schema_entry() -> None:
    module = _renderer_module()
    composition = compose_family_schema_v3()
    rendered = module.render_family_schema_v3(composition)
    connection = sqlite3.connect(":memory:")
    traced: list[str] = []

    class _NullAggregate:
        def step(self, *_args: object) -> None:
            pass

        def finalize(self) -> None:
            return None

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for abi in composition.renderer_grammar.semantic_function_abis:
            if abi.kind is fs.RendererSemanticFunctionKind.SCALAR:
                connection.create_function(
                    abi.sqlite_name,
                    abi.arity,
                    lambda *_args: None,
                    deterministic=True,
                )
            else:
                connection.create_aggregate(
                    abi.sqlite_name,
                    abi.arity,
                    _NullAggregate,
                )
        connection.set_trace_callback(traced.append)

        for item in rendered:
            connection.execute(item.statement_sql)

        for item in rendered:
            actual = connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE type = ? AND name = ?",
                (item.object_type, item.object_name),
            ).fetchone()
            assert actual == (
                item.object_type,
                item.object_name,
                item.table_name,
                item.sqlite_schema_sql,
            )

        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert not any(
            statement.lstrip().upper().startswith(
                ("BEGIN ", "BEGIN;", "COMMIT", "ROLLBACK")
            )
            for statement in traced
        )
    finally:
        connection.close()
