"""Pass-C closure for the future family-schema SQLite renderer runtime.

These tests freeze declarations only.  They do not render DDL, activate schema
v3, or make the future Pass-M renderer reachable.
"""

from __future__ import annotations

import dataclasses
import inspect
import sqlite3

import pytest

from services.research.experiment_registry import family_schema_spec as spec
from services.research.experiment_registry.family_schema_composition import (
    compose_family_schema_v3,
)


def test_renderer_runtime_profile_closes_sqlite_and_transaction_trust_boundary() -> None:
    grammar = spec.FAMILY_SCHEMA_RENDERER_GRAMMAR
    runtime = spec.FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE

    assert grammar.sqlite_runtime is runtime
    assert runtime.profile_version == "QUANTOS_SQLITE_RUNTIME_PROFILE_V1"
    assert runtime.minimum_sqlite_version == (3, 38, 0)
    assert runtime.required_capabilities == tuple(spec.SqliteRuntimeCapability)
    assert runtime.table_mode is spec.SqliteTableMode.STRICT
    assert runtime.affinity_policy is spec.SqliteAffinityPolicy.STRICT_LOSSLESS_COERCION
    assert runtime.trusted_schema_profile is (
        spec.SqliteTrustedSchemaProfile.VERIFY_SEALED_THEN_TRUST_TRANSACTION_V1
    )
    assert runtime.native_extension_policy is (
        spec.SqliteNativeExtensionPolicy.INNOCUOUS_EXTENSION_OFF
    )
    assert runtime.connection_cached_statements == 0
    assert runtime.trust_transition_steps == tuple(spec.SqliteTrustTransitionStep)
    assert runtime.authorizer_denied_operations == tuple(
        spec.SqliteAuthorizerDeniedOperation
    )
    assert runtime.transaction_authorizer_policy is (
        spec.SqliteAuthorizerTransactionPolicy.STORE_OWNED_TERMINAL_ONLY_V1
    )
    assert runtime.requires_fresh_connection is True
    assert runtime.requires_main_and_empty_temp_only is True
    assert runtime.requires_begin_immediate is True
    assert runtime.requires_owner_token_cas is True
    assert runtime.requires_exact_sealed_catalog is True
    assert runtime.requires_no_unknown_objects is True
    assert runtime.requires_schema_version_double_read is True
    assert runtime.authorizer_deny_by_default is True
    assert runtime.requires_manifest_bound_function_hashes is True
    assert runtime.allows_load_extension is False
    assert runtime.allows_connection_reuse is False
    assert runtime.unregisters_functions_before_close is True
    assert runtime.closes_connection_on_every_exit is True


def test_renderer_semantic_function_abi_is_closed_manifest_bound_and_pure() -> None:
    grammar = spec.FAMILY_SCHEMA_RENDERER_GRAMMAR
    abis = grammar.semantic_function_abis

    assert abis == spec.FAMILY_SCHEMA_RENDERER_SEMANTIC_FUNCTION_ABIS
    assert tuple(item.operation for item in abis) == tuple(
        spec.RendererSemanticOperation
    )
    assert len({item.sqlite_name for item in abis}) == len(abis)
    assert {item.operation: item.arity for item in abis} == {
        spec.RendererSemanticOperation.CANONICAL_JSON_FIELD_VALUE: 3,
        spec.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_VALUE: 5,
        spec.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_LENGTH: 3,
        spec.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_CONTAINS: 4,
        spec.RendererSemanticOperation.CANONICAL_JSON_PAYLOAD_HASH: 2,
        spec.RendererSemanticOperation.CANONICAL_ORDERED_VALUE_HASH: 1,
        spec.RendererSemanticOperation.MEAN_NET_RETURN_DIFFERENTIAL: 2,
        spec.RendererSemanticOperation.PREFIX_STREAM_EXTENSION: 14,
        spec.RendererSemanticOperation.LATEST_PER_DOMAIN_POLICY: 7,
    }
    assert all(item.deterministic for item in abis)
    assert all(item.pure_input_only for item in abis)
    assert all(item.manifest_implementation_hash_required for item in abis)
    assert all(
        item.failure_policy
        is spec.RendererFunctionFailurePolicy.RETURN_NULL_FOR_OUTER_FAIL_CLOSED
        for item in abis
    )
    assert {
        item.input_ordering
        for item in abis
        if item.kind is spec.RendererSemanticFunctionKind.AGGREGATE
    } == {spec.RendererAggregateInputOrdering.ORDERED_SUBQUERY_BINARY_ASC}


def test_renderer_null_lookup_and_state_machine_lowerings_are_exact_and_fail_closed() -> None:
    grammar = spec.FAMILY_SCHEMA_RENDERER_GRAMMAR

    assert grammar.unsupported_predicate_nodes == (
        "RelationPolicyAggregateExpression",
    )
    assert grammar.unsupported_predicate_nodes == (
        spec.FAMILY_SCHEMA_UNSUPPORTED_PREDICATE_NODES
    )

    assert grammar.null_lowering == spec.FAMILY_SCHEMA_RENDERER_NULL_LOWERING
    assert grammar.null_lowering.check_constraint is (
        spec.RendererNullLowering.CHECK_VALIDITY_NULL_IS_FALSE
    )
    assert grammar.null_lowering.insert_guard is (
        spec.RendererNullLowering.INSERT_VIOLATION_NULL_IS_TRUE
    )
    assert grammar.null_lowering.verification is (
        spec.RendererNullLowering.VERIFICATION_NON_TRUE_IS_FAILURE
    )
    assert grammar.null_lowering.function_failure is (
        spec.RendererNullLowering.FUNCTION_FAILURE_RETURNS_NULL
    )

    lookup = grammar.related_lookup_lowering
    assert lookup == spec.FAMILY_SCHEMA_RELATED_LOOKUP_LOWERING
    assert lookup.sql_lowering is (
        spec.RelatedLookupSqlLowering.EXACT_COUNT_THEN_SCALAR_SUBQUERY_V1
    )
    assert lookup.cardinality is spec.RelatedLookupCardinality.EXACTLY_ONE
    assert lookup.key_requirement is (
        spec.RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
    )
    assert lookup.rejects_zero_rows and lookup.rejects_multiple_rows
    assert lookup.requires_compile_time_key_proof

    machine = grammar.state_machine_lowering
    assert machine == spec.FAMILY_SCHEMA_INSERT_STATE_MACHINE_LOWERING
    assert machine.sql_lowering is (
        spec.InsertStateMachineSqlLowering.EXACT_PREDECESSOR_TRANSITION_V1
    )
    assert machine.predicate_composition is (
        spec.InsertStateMachinePredicateComposition.OR_WITH_DECLARED_VIOLATION
    )
    assert machine.initial_sequence_value == 0
    assert machine.transition_sequence_step == 1
    assert machine.requires_sequence_column
    assert machine.matches_all_identity_columns
    assert machine.rejects_missing_or_ambiguous_predecessor
    assert machine.rejects_outgoing_transition_from_terminal


def test_nullable_lineage_parent_is_explicit_under_fail_closed_check_lowering() -> None:
    table = next(
        table
        for table in compose_family_schema_v3().tables
        if table.table_name == "strategy_lineage_nodes"
    )
    check = next(
        check
        for check in table.checks
        if check.name == "strategy_lineage_nodes_parent_not_self"
    )

    assert check.expression == spec.LogicalExpression(
        spec.LogicalOperator.OR,
        (
            spec.NullExpression(
                spec.ColumnValue("parent_strategy_lineage_node_id"),
                spec.NullOperator.IS_NULL,
            ),
            spec.ComparisonExpression(
                spec.ColumnValue("strategy_lineage_node_id"),
                spec.ComparisonOperator.NE,
                spec.ColumnValue("parent_strategy_lineage_node_id"),
            ),
        ),
    )


def test_runtime_and_lowering_profile_tampering_is_rejected() -> None:
    runtime = spec.FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE
    grammar = spec.FAMILY_SCHEMA_RENDERER_GRAMMAR

    with pytest.raises(ValueError, match="minimum SQLite version"):
        dataclasses.replace(runtime, minimum_sqlite_version=(3, 37, 0))
    with pytest.raises(ValueError, match="cached statements"):
        dataclasses.replace(runtime, connection_cached_statements=1)
    with pytest.raises(ValueError, match="trust transition"):
        dataclasses.replace(runtime, trust_transition_steps=runtime.trust_transition_steps[:-1])
    with pytest.raises(ValueError, match="authorizer deny"):
        dataclasses.replace(
            runtime,
            authorizer_denied_operations=runtime.authorizer_denied_operations[:-1],
        )
    with pytest.raises(ValueError, match="load_extension"):
        dataclasses.replace(runtime, allows_load_extension=True)

    first_abi = grammar.semantic_function_abis[0]
    with pytest.raises(ValueError, match="deterministic"):
        dataclasses.replace(first_abi, deterministic=False)
    with pytest.raises(ValueError, match="manifest implementation hash"):
        dataclasses.replace(first_abi, manifest_implementation_hash_required=False)
    with pytest.raises(ValueError, match="semantic function ABI"):
        dataclasses.replace(
            grammar,
            semantic_function_abis=grammar.semantic_function_abis[:-1],
        )
    with pytest.raises(ValueError, match="zero rows"):
        dataclasses.replace(grammar.related_lookup_lowering, rejects_zero_rows=False)
    with pytest.raises(ValueError, match="sequence step"):
        dataclasses.replace(grammar.state_machine_lowering, transition_sequence_step=2)
    with pytest.raises(ValueError, match="unsupported predicate"):
        dataclasses.replace(grammar, unsupported_predicate_nodes=())


def test_stdlib_deterministic_udf_is_unsafe_in_persistent_schema_while_untrusted() -> None:
    """Machine evidence for why the L2 profile needs a scoped trust transition.

    Python's stdlib binding can mark a function deterministic, but cannot mark
    it SQLITE_INNOCUOUS.  SQLite therefore blocks the function when a persistent
    trigger invokes it while trusted_schema is OFF.
    """

    assert sqlite3.sqlite_version_info >= (3, 38, 0)
    parameters = inspect.signature(sqlite3.Connection.create_function).parameters
    assert "deterministic" in parameters
    assert "innocuous" not in parameters

    connection = sqlite3.connect(":memory:", cached_statements=0)
    try:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.create_function(
            "quantos_runtime_probe",
            1,
            lambda value: value,
            deterministic=True,
        )
        connection.execute('CREATE TABLE "probe_rows" ("value" TEXT) STRICT')
        connection.execute(
            'CREATE TRIGGER "probe_rows_guard" BEFORE INSERT ON "probe_rows" '
            'BEGIN SELECT "quantos_runtime_probe"(NEW."value"); END'
        )

        with pytest.raises(sqlite3.OperationalError, match="unsafe use"):
            connection.execute('INSERT INTO "probe_rows" ("value") VALUES (?)', ("x",))
    finally:
        connection.close()


@pytest.mark.parametrize("terminal", ("COMMIT", "ROLLBACK"))
def test_authorizer_rejects_nested_transaction_control_but_allows_one_store_terminal(
    terminal: str,
) -> None:
    connection = sqlite3.connect(":memory:", cached_statements=0)
    gate = {"armed": False, "consumed": False}
    seen: list[tuple[int, str | None]] = []

    def authorize(
        action: int,
        argument1: str | None,
        _argument2: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_TRANSACTION:
            seen.append((action, argument1))
            if (
                argument1 == terminal
                and gate["armed"]
                and not gate["consumed"]
            ):
                gate["consumed"] = True
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SAVEPOINT:
            seen.append((action, argument1))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        connection.execute('CREATE TABLE "owned_rows" ("value" INTEGER) STRICT')
        connection.execute("BEGIN IMMEDIATE")
        connection.set_authorizer(authorize)

        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("BEGIN")
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute('SAVEPOINT "caller_owned"')

        gate["armed"] = True
        if terminal == "COMMIT":
            connection.commit()
        else:
            connection.rollback()

        assert gate["consumed"] is True
        assert connection.in_transaction is False
        assert (sqlite3.SQLITE_TRANSACTION, terminal) in seen
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("BEGIN")
    finally:
        connection.set_authorizer(None)
        connection.close()


def test_composition_carries_the_exact_renderer_runtime_contract_without_execution() -> None:
    composition = compose_family_schema_v3()

    assert composition.renderer_grammar is spec.FAMILY_SCHEMA_RENDERER_GRAMMAR
    assert composition.renderer_grammar.sqlite_runtime is (
        spec.FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE
    )
    assert not hasattr(composition, "ddl")
    assert not hasattr(composition, "render")
    assert not hasattr(composition, "activate")
