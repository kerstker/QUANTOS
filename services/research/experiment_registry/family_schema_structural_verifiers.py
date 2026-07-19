"""Read-only implementations for the closed family-schema structural plans.

This module is executable implementation material for the canonical binder.  It
does not seal a schema, activate a database, accept caller SQL, or mutate the
supplied connection.  Every database statement is a read assembled from the
canonical composition and the existing closed predicate renderer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import FunctionType

from quantos.contracts.canonical import canonical_sha256

from . import family_schema_spec as fs
from .family_schema import FamilySchemaRenderError, _RenderContext, _SqlRenderer
from .family_schema_composition import (
    FamilySchemaComposition,
    compose_family_schema_v3,
)
from .family_schema_v2_catalog import InheritedTableSpec
from .family_schema_verifier_registry import (
    FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS,
    StructuralVerificationPlan,
)


class StructuralVerificationError(ValueError):
    """The requested verification is outside the closed canonical contract."""


class StructuralDependencyKind(StrEnum):
    RULE_ID = "RULE_ID"
    VERIFIER_CODE = "VERIFIER_CODE"


class StructuralVerificationFailure(StrEnum):
    DATA_MISMATCH = "DATA_MISMATCH"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    READ_FAILED = "READ_FAILED"
    SOURCE_CHANGED = "SOURCE_CHANGED"


DelegatedStructuralVerificationCallback = Callable[[StructuralDependencyKind, str], bool]


@dataclass(frozen=True, slots=True)
class StructuralVerificationResult:
    rule_key: str
    operation: fs.StructuralVerificationOperation
    passed: bool
    checked_rows: int
    failure: StructuralVerificationFailure | None = None

    def __post_init__(self) -> None:
        if type(self.rule_key) is not str or not self.rule_key:
            raise ValueError("structural result requires a rule key")
        if type(self.operation) is not fs.StructuralVerificationOperation:
            raise TypeError("structural result operation must be closed")
        if type(self.passed) is not bool:
            raise TypeError("structural result verdict must be bool")
        if type(self.checked_rows) is not int or self.checked_rows < 0:
            raise ValueError("structural result row count must be non-negative")
        if self.passed != (self.failure is None):
            raise ValueError("structural result failure must match its verdict")
        if self.failure is not None and type(self.failure) is not (StructuralVerificationFailure):
            raise TypeError("structural result failure must be closed")


@dataclass(frozen=True, slots=True)
class _HandlerOutcome:
    checked_rows: int
    failure: StructuralVerificationFailure | None = None


@dataclass(slots=True)
class _VerificationContext:
    connection: sqlite3.Connection
    composition: FamilySchemaComposition
    plan: StructuralVerificationPlan
    owner_table: fs.AuthorityTableSpec
    rule: fs.VerificationRuleSpec
    rule_results: dict[str, bool]
    verifier_results: dict[str, bool]
    dependency_callback: DelegatedStructuralVerificationCallback | None
    renderer: _SqlRenderer
    row_cache: dict[str, tuple[dict[str, object], ...]] = field(default_factory=dict)


StructuralHandler = Callable[[_VerificationContext], _HandlerOutcome]


@dataclass(frozen=True, slots=True)
class StructuralVerifierImplementation:
    """One typed callable ready for the canonical implementation binder."""

    operation: fs.StructuralVerificationOperation
    implementation_id: str
    implementation: StructuralHandler

    def __post_init__(self) -> None:
        if type(self.operation) is not fs.StructuralVerificationOperation:
            raise TypeError("structural implementation operation must be closed")
        expected = f"FAMILY_SCHEMA_STRUCTURAL_HANDLER::{self.operation.value}"
        if self.implementation_id != expected:
            raise ValueError("structural implementation ID does not match operation")
        if type(self.implementation) is not FunctionType:
            raise TypeError("structural implementation must be a Python function")


_CANONICAL_COMPOSITION = compose_family_schema_v3()
_CANONICAL_PLANS = {plan.rule_key: plan for plan in FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS}
_OPERATION_BY_SOURCE = dict(fs.STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_columns(
    table: fs.AuthorityTableSpec | InheritedTableSpec,
) -> tuple[str, ...]:
    return tuple(column.name for column in table.columns)


def _table_primary_key(
    table: fs.AuthorityTableSpec | InheritedTableSpec,
) -> tuple[str, ...]:
    if type(table) is fs.AuthorityTableSpec:
        return table.primary_key_columns
    return tuple(
        column.name
        for column in sorted(
            table.columns,
            key=lambda item: item.primary_key_ordinal or len(table.columns) + 1,
        )
        if column.primary_key_ordinal
    )


def _table_by_name(
    context: _VerificationContext,
    table_name: str,
) -> fs.AuthorityTableSpec | InheritedTableSpec:
    for table in context.composition.tables:
        if table.table_name == table_name:
            return table
    for table in context.composition.inherited_schema_catalog.tables:
        if table.table_name == table_name:
            return table
    raise StructuralVerificationError("canonical read plan names an unknown table")


def _read_rows(
    context: _VerificationContext,
    table_name: str,
) -> tuple[dict[str, object], ...]:
    if table_name not in context.plan.read_tables:
        raise StructuralVerificationError("handler attempted an undeclared table read")
    cached = context.row_cache.get(table_name)
    if cached is not None:
        return cached
    table = _table_by_name(context, table_name)
    columns = _table_columns(table)
    primary_key = _table_primary_key(table)
    if not primary_key:
        raise StructuralVerificationError("canonical structural table has no primary key")
    statement = (
        "SELECT "
        + ", ".join(_quote(column) for column in columns)
        + f" FROM {_quote(table_name)} ORDER BY "
        + ", ".join(f"{_quote(column)} COLLATE {_quote('BINARY')} ASC" for column in primary_key)
    )
    cursor = context.connection.execute(statement)
    if tuple(item[0] for item in cursor.description or ()) != columns:
        raise StructuralVerificationError("canonical structural read shape drifted")
    rows = tuple(dict(zip(columns, tuple(row), strict=True)) for row in cursor)
    context.row_cache[table_name] = rows
    return rows


def _tagged(value: object) -> tuple[str, object]:
    if value is None:
        return ("NULL", "")
    if type(value) in (int, float):
        # SQLite uniqueness/FK comparison treats equal INTEGER/REAL values as
        # the same numeric key after canonical column affinity is applied.
        return ("NUMERIC", value)
    if type(value) is str:
        return ("TEXT", value)
    if type(value) is bytes:
        return ("BLOB", value)
    raise StructuralVerificationError("SQLite returned an unsupported value type")


def _sqlite_equal(left: object, right: object) -> bool:
    if left is None or right is None:
        return False
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    return type(left) is type(right) and left == right


def _failed(
    checked_rows: int,
    failure: StructuralVerificationFailure = StructuralVerificationFailure.DATA_MISMATCH,
) -> _HandlerOutcome:
    return _HandlerOutcome(checked_rows=checked_rows, failure=failure)


def _verify_recompute_row_hash(
    context: _VerificationContext,
) -> _HandlerOutcome:
    table = context.owner_table
    rows = _read_rows(context, table.table_name)
    specification = table.row_hash
    for row in rows:
        try:
            expected = canonical_sha256({name: row[name] for name in specification.canonical_fields})
        except Exception:
            return _failed(len(rows))
        if row.get("record_type") != specification.version_literal or row[specification.record_hash_column] != expected:
            return _failed(len(rows))
    return _HandlerOutcome(len(rows))


def _verify_foreign_keys(
    context: _VerificationContext,
    foreign_keys: tuple[fs.ForeignKeySpec, ...],
) -> _HandlerOutcome:
    rows = _read_rows(context, context.owner_table.table_name)
    for foreign_key in foreign_keys:
        parent_rows = _read_rows(context, foreign_key.referenced_table)
        for row in rows:
            local = tuple(row[column] for column in foreign_key.columns)
            if any(value is None for value in local):
                continue
            matches = sum(
                all(
                    _sqlite_equal(parent[parent_column], value)
                    for parent_column, value in zip(
                        foreign_key.referenced_columns,
                        local,
                        strict=True,
                    )
                )
                for parent in parent_rows
            )
            if matches != 1:
                return _failed(len(rows))
    return _HandlerOutcome(len(rows))


def _verify_all_foreign_keys(
    context: _VerificationContext,
) -> _HandlerOutcome:
    return _verify_foreign_keys(context, context.owner_table.foreign_keys)


def _verify_genesis_foreign_key(
    context: _VerificationContext,
) -> _HandlerOutcome:
    expected = (
        ("authority_instance_id", "genesis_record_hash"),
        "family_authority_genesis",
        ("authority_instance_id", "record_hash"),
    )
    matches = tuple(
        item
        for item in context.owner_table.foreign_keys
        if (item.columns, item.referenced_table, item.referenced_columns) == expected
    )
    if len(matches) != 1:
        raise StructuralVerificationError("genesis foreign key did not resolve exactly")
    return _verify_foreign_keys(context, matches)


def _verify_exact_foreign_key(
    context: _VerificationContext,
) -> _HandlerOutcome:
    source = context.rule.structural_source
    assert source is not None
    expected = (source.columns, source.referenced_table, source.referenced_columns)
    matches = tuple(
        item
        for item in context.owner_table.foreign_keys
        if (item.columns, item.referenced_table, item.referenced_columns) == expected
    )
    if len(matches) != 1:
        raise StructuralVerificationError("exact foreign key did not resolve exactly")
    return _verify_foreign_keys(context, matches)


def _chain_start_ordinal(table: fs.AuthorityTableSpec) -> int:
    assert table.chain is not None
    for trigger in table.triggers:
        machine = trigger.state_machine
        if machine is not None and machine.sequence_column == table.chain.ordinal_column:
            return table_spec_runtime_initial_sequence()
    return 1


def table_spec_runtime_initial_sequence() -> int:
    """Return the one canonical state-machine initial sequence value."""

    return fs.FAMILY_SCHEMA_INSERT_STATE_MACHINE_LOWERING.initial_sequence_value


def _chain_failure(
    table: fs.AuthorityTableSpec,
    rows: tuple[dict[str, object], ...],
) -> StructuralVerificationFailure | None:
    chain = table.chain
    if chain is None:
        raise StructuralVerificationError("chain operation has no chain specification")
    groups: dict[tuple[tuple[str, object], ...], list[dict[str, object]]] = {}
    for row in rows:
        partition_values = tuple(row[column] for column in chain.partition_key_columns)
        if any(value is None for value in partition_values):
            return StructuralVerificationFailure.DATA_MISMATCH
        key = tuple(_tagged(value) for value in partition_values)
        groups.setdefault(key, []).append(row)
    start = _chain_start_ordinal(table)
    for group in groups.values():
        if any(type(row[chain.ordinal_column]) is not int for row in group):
            return StructuralVerificationFailure.DATA_MISMATCH
        ordered = sorted(group, key=lambda row: int(row[chain.ordinal_column]))
        for offset, row in enumerate(ordered):
            if row[chain.ordinal_column] != start + offset:
                return StructuralVerificationFailure.DATA_MISMATCH
            previous = row[chain.previous_hash_column]
            record_hash = row[chain.record_hash_column]
            if type(previous) is not str or type(record_hash) is not str:
                return StructuralVerificationFailure.DATA_MISMATCH
            if offset == 0:
                if previous != row.get("genesis_record_hash"):
                    return StructuralVerificationFailure.DATA_MISMATCH
            elif previous != ordered[offset - 1][chain.record_hash_column]:
                return StructuralVerificationFailure.DATA_MISMATCH
    return None


def _verify_hash_chain(context: _VerificationContext) -> _HandlerOutcome:
    rows = _read_rows(context, context.owner_table.table_name)
    failure = _chain_failure(context.owner_table, rows)
    return _HandlerOutcome(len(rows), failure)


def _verify_terminal_head(context: _VerificationContext) -> _HandlerOutcome:
    table = context.owner_table
    rows = _read_rows(context, table.table_name)
    failure = _chain_failure(table, rows)
    if failure is not None:
        return _HandlerOutcome(len(rows), failure)
    chain = table.chain
    assert chain is not None
    if chain.terminal_head_rule is fs.ChainHeadRule.LATEST_RECORD_HASH:
        return _HandlerOutcome(len(rows))
    terminal_states = {
        state
        for trigger in table.triggers
        if trigger.state_machine is not None
        for state in trigger.state_machine.terminal_states
    }
    if not terminal_states or "status" not in _table_columns(table):
        raise StructuralVerificationError("terminal chain has no closed terminal state")
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in rows:
        key = tuple(row[column] for column in chain.partition_key_columns)
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        ordered = sorted(group, key=lambda row: int(row[chain.ordinal_column]))
        terminals = [row for row in ordered if row["status"] in terminal_states]
        if terminals and (len(terminals) != 1 or terminals[0] is not ordered[-1]):
            return _failed(len(rows))
    return _HandlerOutcome(len(rows))


def _verify_primary_key_totality(
    context: _VerificationContext,
) -> _HandlerOutcome:
    table = context.owner_table
    rows = _read_rows(context, table.table_name)
    seen: set[tuple[tuple[str, object], ...]] = set()
    for row in rows:
        values = tuple(row[column] for column in table.primary_key_columns)
        if any(value is None for value in values):
            return _failed(len(rows))
        key = tuple(_tagged(value) for value in values)
        if key in seen:
            return _failed(len(rows))
        seen.add(key)
    return _HandlerOutcome(len(rows))


def _row_cte(
    table: fs.AuthorityTableSpec,
    row: Mapping[str, object],
) -> tuple[str, tuple[object, ...]]:
    columns = _table_columns(table)
    sql = (
        f"{_quote('NEW')} ("
        + ", ".join(_quote(column) for column in columns)
        + ") AS (VALUES ("
        + ", ".join("?" for _ in columns)
        + "))"
    )
    return sql, tuple(row[column] for column in columns)


def _evaluate_current_predicate(
    context: _VerificationContext,
    table: fs.AuthorityTableSpec,
    row: Mapping[str, object],
    expression: object,
) -> bool:
    predicate = context.renderer._predicate(expression, _RenderContext())
    new_cte, parameters = _row_cte(table, row)
    statement = f"WITH {new_cte} SELECT CASE WHEN COALESCE(({predicate}), 0) THEN 1 ELSE 0 END FROM {_quote('NEW')}"
    result = context.connection.execute(statement, parameters).fetchone()
    return result is not None and tuple(result) == (1,)


def _ascii_nocase(value: str) -> str:
    return "".join(chr(ord(character) + 32) if "A" <= character <= "Z" else character for character in value)


def _index_key(
    row: Mapping[str, object],
    index: fs.IndexSpec,
) -> tuple[tuple[str, object], ...]:
    result: list[tuple[str, object]] = []
    for item in index.columns:
        value = row[item.column]
        if item.collation is fs.Collation.NOCASE:
            if type(value) is not str:
                raise StructuralVerificationError("NOCASE index value is not TEXT")
            value = _ascii_nocase(value)
        elif item.collation is fs.Collation.RTRIM:
            if type(value) is not str:
                raise StructuralVerificationError("RTRIM index value is not TEXT")
            value = value.rstrip(" ")
        elif item.collation is not fs.Collation.BINARY:
            raise StructuralVerificationError("unknown canonical index collation")
        result.append(_tagged(value))
    return tuple(result)


def _verify_unique_index(context: _VerificationContext) -> _HandlerOutcome:
    source = context.rule.structural_source
    assert source is not None
    matches = tuple(item for item in context.owner_table.indexes if item.name == source.source_id and item.unique)
    if len(matches) != 1:
        raise StructuralVerificationError("unique index did not resolve exactly")
    index = matches[0]
    rows = _read_rows(context, context.owner_table.table_name)
    seen: set[tuple[tuple[str, object], ...]] = set()
    for row in rows:
        if index.predicate is not None and not _evaluate_current_predicate(
            context,
            context.owner_table,
            row,
            index.predicate,
        ):
            continue
        values = tuple(row[item.column] for item in index.columns)
        if any(value is None for value in values):
            continue
        key = _index_key(row, index)
        if key in seen:
            return _failed(len(rows))
        seen.add(key)
    return _HandlerOutcome(len(rows))


def _verify_check_constraint(context: _VerificationContext) -> _HandlerOutcome:
    source = context.rule.structural_source
    assert source is not None
    matches = tuple(item for item in context.owner_table.checks if item.name == source.source_id)
    if len(matches) != 1:
        raise StructuralVerificationError("check constraint did not resolve exactly")
    rows = _read_rows(context, context.owner_table.table_name)
    for row in rows:
        if not _evaluate_current_predicate(
            context,
            context.owner_table,
            row,
            matches[0].expression,
        ):
            return _failed(len(rows))
    return _HandlerOutcome(len(rows))


def _replay_order_columns(table: fs.AuthorityTableSpec) -> tuple[str, ...]:
    times = tuple(column for column in table.row_hash.canonical_fields if column.endswith("_at_us"))
    candidates = (
        *((times[-1],) if times else ()),
        *(table.chain.partition_key_columns if table.chain is not None else ()),
        *((table.chain.ordinal_column,) if table.chain is not None else ()),
        *table.primary_key_columns,
    )
    return tuple(dict.fromkeys(candidates))


def _read_replay_rows(
    context: _VerificationContext,
    table: fs.AuthorityTableSpec,
) -> tuple[dict[str, object], ...]:
    if table.table_name not in context.plan.read_tables:
        raise StructuralVerificationError("replay attempted an undeclared table read")
    columns = _table_columns(table)
    order = _replay_order_columns(table)
    statement = (
        "SELECT "
        + ", ".join(_quote(column) for column in columns)
        + f" FROM {_quote(table.table_name)} ORDER BY "
        + ", ".join(f"{_quote(column)} COLLATE {_quote('BINARY')} ASC" for column in order)
    )
    cursor = context.connection.execute(statement)
    if tuple(item[0] for item in cursor.description or ()) != columns:
        raise StructuralVerificationError("canonical replay read shape drifted")
    return tuple(dict(zip(columns, tuple(row), strict=True)) for row in cursor)


def _evaluate_insert_violation(
    context: _VerificationContext,
    table: fs.AuthorityTableSpec,
    trigger: fs.TriggerPolicySpec,
    row: Mapping[str, object],
    prefix_count: int,
) -> bool:
    assert trigger.violation_when is not None
    violations = ["COALESCE(" + context.renderer._predicate(trigger.violation_when, _RenderContext()) + ", 1)"]
    if trigger.state_machine is not None:
        violations.append(
            "COALESCE("
            + context.renderer._state_machine_violation(
                table.table_name,
                trigger.state_machine,
            )
            + ", 1)"
        )
    violation = context.renderer._or(tuple(violations))
    columns = _table_columns(table)
    order = _replay_order_columns(table)
    prefix_cte = (
        f"{_quote(table.table_name)} AS (SELECT "
        + ", ".join(_quote(column) for column in columns)
        + f" FROM {_quote('main')}.{_quote(table.table_name)} ORDER BY "
        + ", ".join(f"{_quote(column)} COLLATE {_quote('BINARY')} ASC" for column in order)
        + " LIMIT ?)"
    )
    new_cte, new_parameters = _row_cte(table, row)
    statement = f"WITH {prefix_cte}, {new_cte} SELECT CASE WHEN ({violation}) THEN 1 ELSE 0 END FROM {_quote('NEW')}"
    result = context.connection.execute(
        statement,
        (prefix_count, *new_parameters),
    ).fetchone()
    return result is None or tuple(result) != (0,)


def _verify_replay_insert_guard(
    context: _VerificationContext,
) -> _HandlerOutcome:
    source = context.rule.structural_source
    assert source is not None
    table = context.owner_table if source.source_table is None else _table_by_name(context, source.source_table)
    if type(table) is not fs.AuthorityTableSpec:
        raise StructuralVerificationError("insert guard source is not an authority table")
    matches = tuple(trigger for trigger in table.triggers if trigger.name == source.source_id)
    if len(matches) != 1:
        raise StructuralVerificationError("insert guard did not resolve exactly")
    trigger = matches[0]
    if (
        trigger.kind is not fs.TriggerPolicyKind.INSERT_GUARD
        or trigger.event is not fs.TriggerEvent.INSERT
        or trigger.violation_when is None
    ):
        raise StructuralVerificationError("structural trigger is not an insert guard")
    rows = _read_replay_rows(context, table)
    for index, row in enumerate(rows):
        if _evaluate_insert_violation(
            context,
            table,
            trigger,
            row,
            index,
        ):
            return _failed(len(rows))
    return _HandlerOutcome(len(rows))


def _dependency_result(
    context: _VerificationContext,
    kind: StructuralDependencyKind,
    key: str,
) -> bool:
    mapping = context.rule_results if kind is StructuralDependencyKind.RULE_ID else context.verifier_results
    if key in mapping:
        return mapping[key] is True
    callback = context.dependency_callback
    if callback is None:
        return False
    try:
        result = callback(kind, key)
    except Exception:
        return False
    return type(result) is bool and result


def _verify_delegated_extension(
    context: _VerificationContext,
) -> _HandlerOutcome:
    source = context.rule.structural_source
    assert source is not None
    rows = _read_rows(context, context.owner_table.table_name)
    for rule_id in source.required_rule_ids:
        if not _dependency_result(
            context,
            StructuralDependencyKind.RULE_ID,
            rule_id,
        ):
            return _failed(
                len(rows),
                StructuralVerificationFailure.MISSING_DEPENDENCY,
            )
    for verifier_code in source.required_verifier_codes:
        if not _dependency_result(
            context,
            StructuralDependencyKind.VERIFIER_CODE,
            verifier_code,
        ):
            return _failed(
                len(rows),
                StructuralVerificationFailure.MISSING_DEPENDENCY,
            )
    return _HandlerOutcome(len(rows))


FAMILY_SCHEMA_STRUCTURAL_VERIFIER_IMPLEMENTATIONS = (
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.RECOMPUTE_ROW_HASH,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::RECOMPUTE_ROW_HASH",
        _verify_recompute_row_hash,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_ALL_FOREIGN_KEYS,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_ALL_FOREIGN_KEYS",
        _verify_all_foreign_keys,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_GENESIS_FOREIGN_KEY,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_GENESIS_FOREIGN_KEY",
        _verify_genesis_foreign_key,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_HASH_CHAIN,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_HASH_CHAIN",
        _verify_hash_chain,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_TERMINAL_HEAD,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_TERMINAL_HEAD",
        _verify_terminal_head,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_PRIMARY_KEY_TOTALITY,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_PRIMARY_KEY_TOTALITY",
        _verify_primary_key_totality,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_UNIQUE_INDEX,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_UNIQUE_INDEX",
        _verify_unique_index,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_CHECK_CONSTRAINT,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_CHECK_CONSTRAINT",
        _verify_check_constraint,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.REPLAY_INSERT_GUARD,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::REPLAY_INSERT_GUARD",
        _verify_replay_insert_guard,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_EXACT_FOREIGN_KEY,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_EXACT_FOREIGN_KEY",
        _verify_exact_foreign_key,
    ),
    StructuralVerifierImplementation(
        fs.StructuralVerificationOperation.VERIFY_DELEGATED_EXTENSION,
        "FAMILY_SCHEMA_STRUCTURAL_HANDLER::VERIFY_DELEGATED_EXTENSION",
        _verify_delegated_extension,
    ),
)


_HANDLER_BY_OPERATION = {
    item.operation: item.implementation for item in FAMILY_SCHEMA_STRUCTURAL_VERIFIER_IMPLEMENTATIONS
}
if tuple(_HANDLER_BY_OPERATION) != tuple(fs.StructuralVerificationOperation):
    raise RuntimeError("structural verifier implementations must cover 11/11 operations")


def _normalize_results(
    values: Mapping[str, bool] | None,
    label: str,
) -> dict[str, bool]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise TypeError(f"{label} must be a mapping")
    normalized = dict(values.items())
    if any(type(key) is not str or not key for key in normalized):
        raise TypeError(f"{label} keys must be non-empty text")
    if any(type(value) is not bool for value in normalized.values()):
        raise TypeError(f"{label} values must be exact bool")
    return normalized


def _closed_context(
    connection: sqlite3.Connection,
    composition: FamilySchemaComposition,
    plan: StructuralVerificationPlan,
    *,
    verified_rule_results: Mapping[str, bool] | None,
    verified_verifier_results: Mapping[str, bool] | None,
    dependency_callback: DelegatedStructuralVerificationCallback | None,
) -> _VerificationContext:
    if type(connection) is not sqlite3.Connection:
        raise TypeError("structural verification requires an exact sqlite3.Connection")
    if type(composition) is not FamilySchemaComposition:
        raise TypeError("structural verification requires FamilySchemaComposition")
    if composition != _CANONICAL_COMPOSITION:
        raise StructuralVerificationError("composition is not canonical exact-v3")
    if type(plan) is not StructuralVerificationPlan:
        raise TypeError("structural verification requires an exact typed plan")
    canonical_plan = _CANONICAL_PLANS.get(plan.rule_key)
    if canonical_plan is None or plan != canonical_plan:
        raise StructuralVerificationError("structural plan is not canonical")
    parts = plan.rule_key.split(":")
    if len(parts) != 4 or parts[1] not in ("TABLE", "EXTENSION"):
        raise StructuralVerificationError("structural rule key is not canonical")
    table_name, rule_id = parts[2], parts[3]
    owner = next(
        (table for table in composition.tables if table.table_name == table_name),
        None,
    )
    if owner is None:
        raise StructuralVerificationError("structural owner table is absent")
    matches = tuple(rule for rule in owner.verification_rules if rule.rule_id == rule_id)
    if len(matches) != 1:
        raise StructuralVerificationError("structural rule did not resolve exactly")
    rule = matches[0]
    source = rule.structural_source
    if (
        type(source) is not fs.StructuralVerificationSourceSpec
        or _OPERATION_BY_SOURCE.get(source.kind) is not plan.operation
    ):
        raise StructuralVerificationError("structural source operation drifted")
    if dependency_callback is not None and not callable(dependency_callback):
        raise TypeError("dependency callback must be callable")
    return _VerificationContext(
        connection=connection,
        composition=composition,
        plan=plan,
        owner_table=owner,
        rule=rule,
        rule_results=_normalize_results(
            verified_rule_results,
            "verified rule results",
        ),
        verifier_results=_normalize_results(
            verified_verifier_results,
            "verified verifier results",
        ),
        dependency_callback=dependency_callback,
        renderer=_SqlRenderer(composition.renderer_grammar),
    )


def verify_structural_plan(
    connection: sqlite3.Connection,
    composition: FamilySchemaComposition,
    plan: StructuralVerificationPlan,
    *,
    verified_rule_results: Mapping[str, bool] | None = None,
    verified_verifier_results: Mapping[str, bool] | None = None,
    dependency_callback: DelegatedStructuralVerificationCallback | None = None,
) -> StructuralVerificationResult:
    """Execute one exact structural read plan without mutating ``connection``."""

    context = _closed_context(
        connection,
        composition,
        plan,
        verified_rule_results=verified_rule_results,
        verified_verifier_results=verified_verifier_results,
        dependency_callback=dependency_callback,
    )
    data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
    schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
    try:
        outcome = _HANDLER_BY_OPERATION[plan.operation](context)
    except (FamilySchemaRenderError, StructuralVerificationError, sqlite3.Error):
        outcome = _failed(0, StructuralVerificationFailure.READ_FAILED)
    if (
        int(connection.execute("PRAGMA data_version").fetchone()[0]) != data_version
        or int(connection.execute("PRAGMA schema_version").fetchone()[0]) != schema_version
    ):
        outcome = _failed(
            outcome.checked_rows,
            StructuralVerificationFailure.SOURCE_CHANGED,
        )
    return StructuralVerificationResult(
        rule_key=plan.rule_key,
        operation=plan.operation,
        passed=outcome.failure is None,
        checked_rows=outcome.checked_rows,
        failure=outcome.failure,
    )


__all__ = [
    "DelegatedStructuralVerificationCallback",
    "FAMILY_SCHEMA_STRUCTURAL_VERIFIER_IMPLEMENTATIONS",
    "StructuralDependencyKind",
    "StructuralVerificationError",
    "StructuralVerificationFailure",
    "StructuralVerificationResult",
    "StructuralVerifierImplementation",
    "verify_structural_plan",
]
