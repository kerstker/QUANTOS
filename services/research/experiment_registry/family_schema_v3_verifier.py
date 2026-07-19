"""Complete, fail-closed, read-only verification for family schema v3.

The caller owns the supplied SQLite connection and any active transaction.
This module never executes DDL, DML, transaction control, a mutating PRAGMA, or
function registration against the caller's connection.  It verifies the
pre-trust catalog directly, serializes the current caller-visible snapshot to a
short-lived exact SQLite connection, installs the nine reviewed semantic
callables there, and closes that clone on every exit.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from types import FunctionType

from quantos.contracts.canonical import canonical_sha256

from . import family_schema_spec as fs
from .family_schema import (
    FamilySchemaManifest,
    render_family_schema_v3,
    seal_family_schema_v3,
)
from .family_schema_composition import (
    FamilySchemaComposition,
    compose_family_schema_v3,
)
from .family_schema_external_verification_c4 import C4ExternalProviderBundle
from .family_schema_external_verification_c6_c9 import (
    C6C9ExternalProviderBundle,
)
from .family_schema_named_verifiers_c4_c5 import (
    GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS,
)
from .family_schema_named_verifiers_c6_c9 import (
    C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS,
)
from .family_schema_named_verifiers_c7 import C7_NAMED_VERIFIER_IMPLEMENTATIONS
from .family_schema_named_verifiers_c8 import C8_NAMED_VERIFIER_IMPLEMENTATIONS
from .family_schema_semantic_functions import (
    FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS,
)
from .family_schema_structural_verifiers import (
    StructuralVerificationFailure,
    verify_structural_plan,
)
from .family_schema_verification_view import (
    FrozenFamilySchemaVerificationView,
    FrozenVerificationRow,
)
from .family_schema_verifier_registry import (
    FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS,
    FAMILY_SCHEMA_VERIFIER_BINDINGS,
    FAMILY_SCHEMA_VERIFIER_REGISTRY,
    StructuralVerificationOperation,
    VerifierRegistryKeyKind,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FamilyAuthorityVerificationError(ValueError):
    """The supplied source is not one complete canonical v3 authority."""


@dataclass(frozen=True, slots=True)
class FamilyAuthorityExternalProviders:
    """Deployment trust roots used only when matching authority rows exist."""

    c4: C4ExternalProviderBundle | None = None
    c6_c9: C6C9ExternalProviderBundle | None = None

    def __post_init__(self) -> None:
        if self.c4 is not None and type(self.c4) is not C4ExternalProviderBundle:
            raise TypeError("c4 providers must be C4ExternalProviderBundle")
        if self.c6_c9 is not None and type(self.c6_c9) is not C6C9ExternalProviderBundle:
            raise TypeError("c6_c9 providers must be C6C9ExternalProviderBundle")


@dataclass(frozen=True, slots=True)
class FamilyAuthorityVerificationEvidence:
    """Deterministic evidence returned only after every closed check passes."""

    schema_version: int
    authority_instance_id: str
    genesis_record_hash: str
    manifest_sha256: str
    family_table_row_counts: tuple[tuple[str, int], ...]
    inherited_table_row_counts: tuple[tuple[str, int], ...]
    named_binding_count: int
    named_owner_row_checks: int
    structural_rule_count: int
    structural_row_checks: int

    def __post_init__(self) -> None:
        if self.schema_version != 3:
            raise ValueError("family authority evidence requires schema version 3")
        if type(self.authority_instance_id) is not str or not self.authority_instance_id:
            raise ValueError("family authority evidence requires an authority identity")
        for value, label in (
            (self.genesis_record_hash, "genesis record hash"),
            (self.manifest_sha256, "manifest hash"),
        ):
            if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"family authority evidence {label} is invalid")
        for values, label in (
            (self.family_table_row_counts, "family row counts"),
            (self.inherited_table_row_counts, "inherited row counts"),
        ):
            if (
                type(values) is not tuple
                or any(
                    type(item) is not tuple
                    or len(item) != 2
                    or type(item[0]) is not str
                    or not item[0]
                    or type(item[1]) is not int
                    or item[1] < 0
                    for item in values
                )
                or len({item[0] for item in values}) != len(values)
            ):
                raise ValueError(f"family authority evidence {label} are invalid")
        for value, label in (
            (self.named_binding_count, "named binding count"),
            (self.named_owner_row_checks, "named owner-row count"),
            (self.structural_rule_count, "structural rule count"),
            (self.structural_row_checks, "structural row count"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"family authority evidence {label} is invalid")


@lru_cache(maxsize=1)
def _canonical_composition() -> FamilySchemaComposition:
    composition = compose_family_schema_v3()
    if type(composition) is not FamilySchemaComposition or composition.schema_version != 3:
        raise FamilyAuthorityVerificationError("canonical exact-v3 composition is unavailable")
    return composition


@lru_cache(maxsize=1)
def _canonical_manifest() -> FamilySchemaManifest:
    """Return the sole reviewed seal; cached to avoid re-hashing code per read."""

    manifest = seal_family_schema_v3()
    if type(manifest) is not FamilySchemaManifest or manifest.state != "SEALED":
        raise FamilyAuthorityVerificationError("canonical exact-v3 manifest is not sealed")
    return manifest


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _scalar(connection: sqlite3.Connection, statement: str) -> object:
    row = connection.execute(statement).fetchone()
    if row is None or len(row) != 1:
        raise FamilyAuthorityVerificationError(f"family authority scalar read returned an invalid shape: {statement}")
    return row[0]


def _register_semantic_functions(connection: sqlite3.Connection) -> None:
    composition = _canonical_composition()
    abi_by_operation = {item.operation: item for item in composition.renderer_grammar.semantic_function_abis}
    implementations = FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS
    if len(implementations) != len(fs.RendererSemanticOperation) or len(abi_by_operation) != len(implementations):
        raise FamilyAuthorityVerificationError("canonical semantic function registry is incomplete")
    for item in implementations:
        abi = abi_by_operation.get(item.operation)
        if abi is None or abi.sqlite_name != item.sqlite_name or abi.kind is not item.kind or not abi.deterministic:
            raise FamilyAuthorityVerificationError("canonical semantic function ABI does not match its implementation")
        if item.kind is fs.RendererSemanticFunctionKind.SCALAR:
            if type(item.implementation) is not FunctionType:
                raise FamilyAuthorityVerificationError("canonical scalar semantic implementation is invalid")
            connection.create_function(
                item.sqlite_name,
                abi.arity,
                item.implementation,
                deterministic=True,
            )
        else:
            if type(item.implementation) is not type:
                raise FamilyAuthorityVerificationError("canonical aggregate semantic implementation is invalid")
            connection.create_aggregate(
                item.sqlite_name,
                abi.arity,
                item.implementation,
            )
    _verify_registered_semantic_functions(connection)


def _expected_semantic_function_rows() -> tuple[tuple[object, ...], ...]:
    composition = _canonical_composition()
    abi_by_operation = {
        item.operation: item
        for item in composition.renderer_grammar.semantic_function_abis
    }
    implementations = FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS
    return tuple(
        sorted(
            (
                item.sqlite_name,
                0,
                ("s" if item.kind is fs.RendererSemanticFunctionKind.SCALAR else "a"),
                "utf8",
                abi_by_operation[item.operation].arity,
                (0x800 if item.kind is fs.RendererSemanticFunctionKind.SCALAR else 0),
            )
            for item in implementations
        )
    )


def _quantos_function_rows(
    connection: sqlite3.Connection,
) -> tuple[tuple[object, ...], ...]:
    actual_functions: list[tuple[object, ...]] = []
    for raw_row in connection.execute("PRAGMA function_list"):
        row = tuple(raw_row)
        if len(row) != 6:
            raise FamilyAuthorityVerificationError("SQLite function_list row shape does not match the sealed runtime")
        if type(row[0]) is str and row[0].casefold().startswith("quantos_"):
            actual_functions.append(
                (
                    row[0],
                    int(row[1]),
                    str(row[2]),
                    str(row[3]).lower(),
                    int(row[4]),
                    int(row[5]),
                )
            )
    return tuple(sorted(actual_functions))


def _verify_registered_semantic_functions(connection: sqlite3.Connection) -> None:
    if _quantos_function_rows(connection) != _expected_semantic_function_rows():
        raise FamilyAuthorityVerificationError(
            "canonical semantic functions were not installed with the exact ABI and flags"
        )


def _reject_preexisting_quantos_functions(connection: sqlite3.Connection) -> None:
    if _quantos_function_rows(connection):
        raise FamilyAuthorityVerificationError(
            "family authority verifier rejects pre-existing Quantos functions"
        )


def _family_primary_key_autoindex(table: fs.AuthorityTableSpec) -> str | None:
    if len(table.primary_key_columns) == 1:
        primary = next(column for column in table.columns if column.name == table.primary_key_columns[0])
        if primary.sql_type is fs.SqlType.INTEGER:
            return None
    return f"sqlite_autoindex_{table.table_name}_1"


def _expected_sqlite_schema(
    manifest: FamilySchemaManifest,
    composition: FamilySchemaComposition,
) -> tuple[tuple[object, ...], ...]:
    rendered = render_family_schema_v3(composition)
    if manifest.rendered_objects != rendered:
        raise FamilyAuthorityVerificationError("sealed manifest rendered catalog does not match canonical exact-v3")
    table_names = tuple(table.table_name for table in composition.tables)
    if len(table_names) != 33 or len(set(table_names)) != len(table_names):
        raise FamilyAuthorityVerificationError("canonical exact-v3 must contain exactly 33 family tables")
    rows: list[tuple[object, ...]] = [
        (entry.object_type, entry.name, entry.table_name, entry.sql)
        for entry in composition.inherited_schema_catalog.sqlite_schema_entries
    ]
    rows.extend(
        (
            item.object_type,
            item.object_name,
            item.table_name,
            item.sqlite_schema_sql,
        )
        for item in manifest.rendered_objects
    )
    rows.extend(
        ("index", index_name, table.table_name, None)
        for table in composition.tables
        if (index_name := _family_primary_key_autoindex(table)) is not None
    )
    identities = tuple((str(row[0]), str(row[1])) for row in rows)
    if len(set(identities)) != len(identities):
        raise FamilyAuthorityVerificationError("canonical exact-v3 sqlite_schema identities are not unique")
    return tuple(sorted(rows, key=lambda row: (str(row[0]), str(row[1]))))


def _verify_namespace(
    connection: sqlite3.Connection,
    manifest: FamilySchemaManifest,
    composition: FamilySchemaComposition,
) -> None:
    databases = tuple(str(row[1]) for row in connection.execute("PRAGMA database_list"))
    if databases not in (("main",), ("main", "temp")):
        raise FamilyAuthorityVerificationError(f"family authority has unexpected database namespaces: {databases}")
    if tuple(connection.execute("SELECT type,name,tbl_name,sql FROM temp.sqlite_schema ORDER BY type,name")):
        raise FamilyAuthorityVerificationError("family authority has unexpected temp sqlite_schema objects")
    if int(_scalar(connection, "PRAGMA user_version")) != 3:
        raise FamilyAuthorityVerificationError("family authority requires exact user_version 3")
    actual = tuple(
        tuple(row)
        for row in connection.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name")
    )
    expected = _expected_sqlite_schema(manifest, composition)
    if actual != expected:
        raise FamilyAuthorityVerificationError("family authority sqlite_schema namespace mismatch")


def _expected_family_indexes(
    table: fs.AuthorityTableSpec,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = [
        (
            table.table_name,
            item.name,
            item.unique,
            "c",
            item.predicate is not None,
            tuple(column.column for column in item.columns),
            tuple(column.direction is fs.SortDirection.DESC for column in item.columns),
            tuple(column.collation.value for column in item.columns),
        )
        for item in table.indexes
    ]
    autoindex = _family_primary_key_autoindex(table)
    if autoindex is not None:
        rows.append(
            (
                table.table_name,
                autoindex,
                True,
                "pk",
                False,
                table.primary_key_columns,
                (False,) * len(table.primary_key_columns),
                ("BINARY",) * len(table.primary_key_columns),
            )
        )
    return tuple(sorted(rows))


def _actual_indexes(
    connection: sqlite3.Connection,
    table_name: str,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for index_row in connection.execute(f"PRAGMA index_list({_quote(table_name)})"):
        index_name = str(index_row[1])
        key_rows = tuple(row for row in connection.execute(f"PRAGMA index_xinfo({_quote(index_name)})") if bool(row[5]))
        if any(row[2] is None for row in key_rows):
            raise FamilyAuthorityVerificationError(f"family authority index contains an expression: {index_name}")
        rows.append(
            (
                table_name,
                index_name,
                bool(index_row[2]),
                str(index_row[3]),
                bool(index_row[4]),
                tuple(str(row[2]) for row in key_rows),
                tuple(bool(row[3]) for row in key_rows),
                tuple(str(row[4] or "BINARY").upper() for row in key_rows),
            )
        )
    return tuple(sorted(rows))


def _normalized_foreign_keys(
    rows: tuple[tuple[object, ...], ...],
) -> tuple[tuple[object, ...], ...]:
    groups: dict[int, list[tuple[object, ...]]] = {}
    for row in rows:
        if len(row) != 8:
            raise FamilyAuthorityVerificationError("family authority foreign-key metadata shape drifted")
        groups.setdefault(int(row[0]), []).append(row)
    normalized: list[tuple[object, ...]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda row: int(row[1]))
        normalized.append(
            (
                tuple(str(row[3]) for row in ordered),
                str(ordered[0][2]),
                tuple(str(row[4]) for row in ordered),
                str(ordered[0][5]),
                str(ordered[0][6]),
                str(ordered[0][7]),
            )
        )
    return tuple(sorted(normalized))


def _expected_foreign_keys(
    table: fs.AuthorityTableSpec,
) -> tuple[tuple[object, ...], ...]:
    action = {
        fs.ForeignKeyAction.NO_ACTION: "NO ACTION",
        fs.ForeignKeyAction.RESTRICT: "RESTRICT",
        fs.ForeignKeyAction.CASCADE: "CASCADE",
        fs.ForeignKeyAction.SET_NULL: "SET NULL",
        fs.ForeignKeyAction.SET_DEFAULT: "SET DEFAULT",
    }
    return tuple(
        sorted(
            (
                item.columns,
                item.referenced_table,
                item.referenced_columns,
                action[item.on_update],
                action[item.on_delete],
                "NONE",
            )
            for item in table.foreign_keys
        )
    )


def _verify_table_metadata(
    connection: sqlite3.Connection,
    composition: FamilySchemaComposition,
) -> None:
    inherited_indexes = composition.inherited_schema_catalog.indexes
    for table in composition.inherited_schema_catalog.tables:
        actual_columns = tuple(
            (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                bool(row[3]),
                row[4],
                int(row[5]),
                int(row[6]),
            )
            for row in connection.execute(f"PRAGMA table_xinfo({_quote(table.table_name)})")
        )
        expected_columns = tuple(
            (
                ordinal,
                column.name,
                column.sql_type.value,
                column.not_null,
                None,
                column.primary_key_ordinal,
                0,
            )
            for ordinal, column in enumerate(table.columns)
        )
        if actual_columns != expected_columns:
            raise FamilyAuthorityVerificationError(
                f"family authority inherited table_xinfo mismatch: {table.table_name}"
            )
        expected_indexes = tuple(
            sorted(
                (
                    item.table_name,
                    item.name,
                    item.unique,
                    item.origin,
                    item.partial,
                    item.columns,
                    item.descending,
                    item.collations,
                )
                for item in inherited_indexes
                if item.table_name == table.table_name
            )
        )
        if _actual_indexes(connection, table.table_name) != expected_indexes:
            raise FamilyAuthorityVerificationError(
                f"family authority inherited index metadata mismatch: {table.table_name}"
            )
        if tuple(connection.execute(f"PRAGMA foreign_key_list({_quote(table.table_name)})")):
            raise FamilyAuthorityVerificationError(
                f"family authority inherited foreign key mismatch: {table.table_name}"
            )

    for table in composition.tables:
        primary_ordinals = {column: ordinal for ordinal, column in enumerate(table.primary_key_columns, start=1)}
        actual_columns = tuple(
            (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                bool(row[3]),
                row[4],
                int(row[5]),
                int(row[6]),
            )
            for row in connection.execute(f"PRAGMA table_xinfo({_quote(table.table_name)})")
        )
        expected_columns = tuple(
            (
                ordinal,
                column.name,
                column.sql_type.value,
                not column.nullable,
                None,
                primary_ordinals.get(column.name, 0),
                0,
            )
            for ordinal, column in enumerate(table.columns)
        )
        if actual_columns != expected_columns:
            raise FamilyAuthorityVerificationError(f"family authority table_xinfo mismatch: {table.table_name}")
        if _actual_indexes(connection, table.table_name) != _expected_family_indexes(table):
            raise FamilyAuthorityVerificationError(f"family authority index metadata mismatch: {table.table_name}")
        actual_foreign_keys = _normalized_foreign_keys(
            tuple(tuple(row) for row in connection.execute(f"PRAGMA foreign_key_list({_quote(table.table_name)})"))
        )
        if actual_foreign_keys != _expected_foreign_keys(table):
            raise FamilyAuthorityVerificationError(
                f"family authority foreign-key metadata mismatch: {table.table_name}"
            )


def _table_primary_key(table: object) -> tuple[str, ...]:
    if type(table) is fs.AuthorityTableSpec:
        return table.primary_key_columns
    columns = tuple(getattr(table, "columns"))
    return tuple(
        column.name
        for column in sorted(
            columns,
            key=lambda item: item.primary_key_ordinal or len(columns) + 1,
        )
        if column.primary_key_ordinal
    )


def _freeze_view(
    connection: sqlite3.Connection,
    composition: FamilySchemaComposition,
) -> tuple[
    FrozenFamilySchemaVerificationView,
    tuple[tuple[str, int], ...],
    tuple[tuple[str, int], ...],
]:
    family_names = {table.table_name for table in composition.tables}
    inputs: list[tuple[str, tuple[dict[str, object], ...]]] = []
    family_counts: list[tuple[str, int]] = []
    inherited_counts: list[tuple[str, int]] = []
    for table in (*composition.inherited_schema_catalog.tables, *composition.tables):
        columns = tuple(column.name for column in table.columns)
        primary_key = _table_primary_key(table)
        if not primary_key:
            raise FamilyAuthorityVerificationError(
                f"family authority snapshot table lacks a primary key: {table.table_name}"
            )
        cursor = connection.execute(
            "SELECT "
            + ", ".join(_quote(column) for column in columns)
            + f" FROM {_quote(table.table_name)} ORDER BY "
            + ", ".join(f"{_quote(column)} COLLATE {_quote('BINARY')} ASC" for column in primary_key)
        )
        if tuple(item[0] for item in cursor.description or ()) != columns:
            raise FamilyAuthorityVerificationError(f"family authority snapshot shape drifted: {table.table_name}")
        rows = tuple(dict(zip(columns, tuple(row), strict=True)) for row in cursor)
        inputs.append((table.table_name, rows))
        target = family_counts if table.table_name in family_names else inherited_counts
        target.append((table.table_name, len(rows)))
    return (
        FrozenFamilySchemaVerificationView.from_rows(tuple(inputs)),
        tuple(family_counts),
        tuple(inherited_counts),
    )


def _verify_genesis(
    view: FrozenFamilySchemaVerificationView,
    manifest: FamilySchemaManifest,
    composition: FamilySchemaComposition,
    expected_authority_instance_id: str,
) -> FrozenVerificationRow:
    rows = view.rows("family_authority_genesis")
    if len(rows) != 1 or type(rows[0]) is not FrozenVerificationRow:
        raise FamilyAuthorityVerificationError("family authority requires exactly one immutable genesis row")
    row = rows[0]
    genesis = composition.tables[0]
    if genesis.table_name != "family_authority_genesis":
        raise FamilyAuthorityVerificationError("canonical genesis table order drifted")
    if row["authority_instance_id"] != expected_authority_instance_id:
        raise FamilyAuthorityVerificationError("family authority instance does not match deployment identity")
    if row["sealed_v3_manifest_hash"] != manifest.manifest_sha256:
        raise FamilyAuthorityVerificationError("family authority genesis does not bind the sealed v3 manifest")
    if (
        row["singleton_key"] != 1
        or row["record_type"] != genesis.row_hash.version_literal
        or row["migration_id"] != "FAMILY_SCHEMA_V2_TO_V3_V1"
        or type(row["activated_at_us"]) is not int
        or row["activated_at_us"] <= 0
    ):
        raise FamilyAuthorityVerificationError("family authority genesis payload is invalid")
    for field in (
        "v2_origin_fingerprint",
        "sealed_v3_manifest_hash",
        "activation_code_manifest_evidence_hash",
        "record_hash",
    ):
        value = row[field]
        if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
            raise FamilyAuthorityVerificationError(f"family authority genesis {field} is invalid")
    expected_hash = canonical_sha256({field: row[field] for field in genesis.row_hash.canonical_fields})
    if row[genesis.row_hash.record_hash_column] != expected_hash:
        raise FamilyAuthorityVerificationError("family authority genesis record hash mismatch")
    return row


def _named_implementations() -> dict[str, object]:
    rows = (
        *GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS,
        *C7_NAMED_VERIFIER_IMPLEMENTATIONS,
        *C8_NAMED_VERIFIER_IMPLEMENTATIONS,
        *C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS,
    )
    result: dict[str, object] = {}
    for item in rows:
        code = item.verifier_code
        implementation = item.implementation
        if (
            code in result
            or type(implementation) is not FunctionType
            or getattr(item, "production_ready", True) is not True
        ):
            raise FamilyAuthorityVerificationError("canonical named verifier implementation registry is invalid")
        result[code] = implementation
    expected_codes = {
        item.key for item in FAMILY_SCHEMA_VERIFIER_REGISTRY if item.key_kind is VerifierRegistryKeyKind.VERIFIER_CODE
    }
    if set(result) != expected_codes:
        raise FamilyAuthorityVerificationError("canonical named verifier implementation coverage is incomplete")
    return result


def _graph_owner_table(
    composition: FamilySchemaComposition,
    component_id: str,
    derivation_id: str,
) -> str:
    graph = next(
        (item for item in composition.artifact_graph_derivations if item.derivation_id == derivation_id),
        None,
    )
    if graph is None:
        raise FamilyAuthorityVerificationError("named graph verifier binding has no canonical derivation")
    if graph.locator.binding_table is not None:
        return graph.locator.binding_table
    component = next(
        (item for item in composition.components if item.component_id == component_id),
        None,
    )
    if component is None or len(component.tables) != 1:
        raise FamilyAuthorityVerificationError("contract-input graph verifier has no unambiguous owner table")
    return component.tables[0].table_name


def _binding_owner_table(
    composition: FamilySchemaComposition,
    binding_key: str,
) -> str:
    parts = binding_key.split(":")
    if len(parts) != 4:
        raise FamilyAuthorityVerificationError("named verifier binding key is invalid")
    component_id, surface_kind, subject, _ = parts
    if surface_kind in ("TABLE", "EXTENSION", "CONTINUITY"):
        return subject
    if surface_kind == "GRAPH":
        return _graph_owner_table(composition, component_id, subject)
    raise FamilyAuthorityVerificationError("named verifier surface kind is unknown")


def _provider_for_implementation(
    implementation: FunctionType,
    providers: FamilyAuthorityExternalProviders | None,
) -> object:
    module = implementation.__module__
    if module.endswith("family_schema_named_verifiers_c4_c5"):
        return None if providers is None else providers.c4
    if module.endswith("family_schema_named_verifiers_c6_c9"):
        return None if providers is None else providers.c6_c9
    return None


def _verify_named_rules(
    view: FrozenFamilySchemaVerificationView,
    composition: FamilySchemaComposition,
    providers: FamilyAuthorityExternalProviders | None,
) -> tuple[dict[str, bool], int]:
    implementations = _named_implementations()
    verifier_results: dict[str, bool] = {code: True for code in implementations}
    checked_rows = 0
    binding_keys: set[str] = set()
    for binding in FAMILY_SCHEMA_VERIFIER_BINDINGS:
        if binding.binding_key in binding_keys:
            raise FamilyAuthorityVerificationError("canonical named verifier binding is duplicated")
        binding_keys.add(binding.binding_key)
        implementation = implementations.get(binding.verifier_code)
        if type(implementation) is not FunctionType:
            raise FamilyAuthorityVerificationError("canonical named verifier implementation is missing")
        owner_table = _binding_owner_table(
            composition,
            binding.binding_key,
        )
        try:
            owner_rows = view.rows(owner_table)
        except Exception as exc:
            raise FamilyAuthorityVerificationError(f"named verifier owner table is unavailable: {owner_table}") from exc
        provider = _provider_for_implementation(implementation, providers)
        binding_passed = True
        for owner_row in owner_rows:
            checked_rows += 1
            try:
                passed = implementation(owner_row, view, provider) is True
            except Exception:
                passed = False
            if not passed:
                binding_passed = False
                break
        verifier_results[binding.verifier_code] = verifier_results[binding.verifier_code] and binding_passed
        if not binding_passed:
            raise FamilyAuthorityVerificationError(
                f"named family verifier rejected authority data: {binding.binding_key}"
            )
    if len(binding_keys) != len(FAMILY_SCHEMA_VERIFIER_BINDINGS):
        raise FamilyAuthorityVerificationError("canonical named verifier binding coverage is incomplete")
    return verifier_results, checked_rows


def _verify_structural_rules(
    connection: sqlite3.Connection,
    composition: FamilySchemaComposition,
    verifier_results: dict[str, bool],
) -> int:
    local_rule_results: dict[str, dict[str, bool]] = {}
    completed_plan_keys: set[str] = set()
    checked_rows = 0
    delegated: list[object] = []
    for plan in FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS:
        if plan.operation is StructuralVerificationOperation.VERIFY_DELEGATED_EXTENSION:
            delegated.append(plan)
            continue
        result = verify_structural_plan(connection, composition, plan)
        checked_rows += result.checked_rows
        if not result.passed:
            raise FamilyAuthorityVerificationError(
                f"structural family verifier rejected {plan.rule_key}: {result.failure}"
            )
        parts = plan.rule_key.split(":")
        if len(parts) != 4 or plan.rule_key in completed_plan_keys:
            raise FamilyAuthorityVerificationError("canonical structural rule binding is invalid or duplicated")
        owner_table, rule_id = parts[2], parts[3]
        owner_results = local_rule_results.setdefault(owner_table, {})
        if rule_id in owner_results:
            raise FamilyAuthorityVerificationError(
                "canonical structural rule IDs are not unique within their owner table"
            )
        owner_results[rule_id] = True
        completed_plan_keys.add(plan.rule_key)
    for plan in delegated:
        parts = plan.rule_key.split(":")
        if len(parts) != 4:
            raise FamilyAuthorityVerificationError("canonical delegated structural rule binding is invalid")
        owner_table, rule_id = parts[2], parts[3]
        result = verify_structural_plan(
            connection,
            composition,
            plan,
            verified_rule_results=local_rule_results.get(owner_table, {}),
            verified_verifier_results=verifier_results,
        )
        checked_rows += result.checked_rows
        if not result.passed:
            failure = result.failure or StructuralVerificationFailure.DATA_MISMATCH
            raise FamilyAuthorityVerificationError(f"delegated structural verifier rejected {plan.rule_key}: {failure}")
        owner_results = local_rule_results.setdefault(owner_table, {})
        if rule_id in owner_results or plan.rule_key in completed_plan_keys:
            raise FamilyAuthorityVerificationError(
                "canonical delegated structural rule is duplicated within its owner table"
            )
        owner_results[rule_id] = True
        completed_plan_keys.add(plan.rule_key)
    if len(completed_plan_keys) != len(FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS):
        raise FamilyAuthorityVerificationError("canonical structural verifier coverage is incomplete")
    return checked_rows


def _verify_family_authority(
    connection: sqlite3.Connection,
    *,
    expected_authority_instance_id: str,
    external_providers: FamilyAuthorityExternalProviders | None,
) -> FamilyAuthorityVerificationEvidence:
    manifest = _canonical_manifest()
    composition = _canonical_composition()
    if (
        manifest.schema_version != 3
        or manifest.source_schema_version != 2
        or manifest.component_order != composition.component_order
    ):
        raise FamilyAuthorityVerificationError("sealed manifest identity does not match canonical exact-v3")
    _verify_registered_semantic_functions(connection)
    if int(_scalar(connection, "PRAGMA foreign_keys")) != 1:
        raise FamilyAuthorityVerificationError("family authority verification requires foreign_keys=ON")
    _verify_namespace(connection, manifest, composition)
    _verify_table_metadata(connection, composition)
    integrity = tuple(tuple(row) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != (("ok",),):
        raise FamilyAuthorityVerificationError("family authority integrity_check failed")
    if tuple(connection.execute("PRAGMA foreign_key_check")):
        raise FamilyAuthorityVerificationError("family authority foreign_key_check failed")
    view, family_counts, inherited_counts = _freeze_view(connection, composition)
    genesis = _verify_genesis(
        view,
        manifest,
        composition,
        expected_authority_instance_id,
    )
    verifier_results, named_checks = _verify_named_rules(
        view,
        composition,
        external_providers,
    )
    structural_checks = _verify_structural_rules(
        connection,
        composition,
        verifier_results,
    )
    return FamilyAuthorityVerificationEvidence(
        schema_version=3,
        authority_instance_id=expected_authority_instance_id,
        genesis_record_hash=str(genesis["record_hash"]),
        manifest_sha256=manifest.manifest_sha256,
        family_table_row_counts=family_counts,
        inherited_table_row_counts=inherited_counts,
        named_binding_count=len(FAMILY_SCHEMA_VERIFIER_BINDINGS),
        named_owner_row_checks=named_checks,
        structural_rule_count=len(FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS),
        structural_row_checks=structural_checks,
    )


def _verify_pretrust_family_authority(
    connection: sqlite3.Connection,
    *,
    expected_authority_instance_id: str,
) -> FrozenFamilySchemaVerificationView:
    """Verify exact catalog/genesis identity without application functions."""

    manifest = _canonical_manifest()
    composition = _canonical_composition()
    if (
        manifest.schema_version != 3
        or manifest.source_schema_version != 2
        or manifest.component_order != composition.component_order
    ):
        raise FamilyAuthorityVerificationError(
            "sealed manifest identity does not match canonical exact-v3"
        )
    if int(_scalar(connection, "PRAGMA foreign_keys")) != 1:
        raise FamilyAuthorityVerificationError(
            "family authority verification requires foreign_keys=ON"
        )
    _verify_namespace(connection, manifest, composition)
    _verify_table_metadata(connection, composition)
    integrity = tuple(tuple(row) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != (("ok",),):
        raise FamilyAuthorityVerificationError(
            "family authority integrity_check failed"
        )
    if tuple(connection.execute("PRAGMA foreign_key_check")):
        raise FamilyAuthorityVerificationError(
            "family authority foreign_key_check failed"
        )
    view, _, _ = _freeze_view(connection, composition)
    _verify_genesis(
        view,
        manifest,
        composition,
        expected_authority_instance_id,
    )
    return view


def _verify_registered_family_authority(
    connection: sqlite3.Connection,
    *,
    expected_authority_instance_id: str,
    external_providers: FamilyAuthorityExternalProviders | None,
) -> FamilyAuthorityVerificationEvidence:
    """Verify one exact-v3 connection whose nine functions are runtime-owned."""

    if type(connection) is not sqlite3.Connection:
        raise TypeError("connection must be an exact sqlite3.Connection")
    return _verify_family_authority(
        connection,
        expected_authority_instance_id=expected_authority_instance_id,
        external_providers=external_providers,
    )


def _clone_caller_visible_snapshot(
    connection: sqlite3.Connection,
    *,
    expected_authority_instance_id: str,
    source_view: FrozenFamilySchemaVerificationView,
) -> sqlite3.Connection:
    """Clone committed or caller-owned uncommitted state without touching it."""

    clone = _new_clone_connection()
    try:
        clone.deserialize(connection.serialize(name="main"), name="main")
        clone.execute("PRAGMA foreign_keys=ON")
        serialized_view = _verify_pretrust_family_authority(
            clone,
            expected_authority_instance_id=expected_authority_instance_id,
        )
        if serialized_view == source_view:
            return clone
    except Exception:
        pass
    clone.close()
    return _clone_caller_visible_snapshot_from_dump(connection)


def _new_clone_connection() -> sqlite3.Connection:
    clone = sqlite3.connect(":memory:", cached_statements=0)
    if type(clone) is not sqlite3.Connection:
        clone.close()
        raise FamilyAuthorityVerificationError(
            "verification clone must be an exact sqlite3.Connection"
        )
    return clone


def _clone_caller_visible_snapshot_from_dump(
    connection: sqlite3.Connection,
) -> sqlite3.Connection:
    """Fallback for builds where serialize omits deserialized dirty pages."""

    clone = _new_clone_connection()
    try:
        clone.executescript("\n".join(connection.iterdump()))
        user_version = int(_scalar(connection, "PRAGMA user_version"))
        clone.execute(f"PRAGMA user_version={user_version}")
        clone.execute("PRAGMA foreign_keys=ON")
        return clone
    except BaseException:
        clone.close()
        raise


def verify_family_authority(
    connection: sqlite3.Connection,
    *,
    expected_authority_instance_id: str,
    external_providers: FamilyAuthorityExternalProviders | None = None,
) -> FamilyAuthorityVerificationEvidence:
    """Verify one already-open exact-v3 source without taking transaction ownership."""

    if type(connection) is not sqlite3.Connection:
        raise TypeError("connection must be an exact sqlite3.Connection")
    fs.validate_authority_instance_id(expected_authority_instance_id)
    if external_providers is not None and type(external_providers) is not (FamilyAuthorityExternalProviders):
        raise TypeError("external_providers must be FamilyAuthorityExternalProviders or None")
    initial_transaction = connection.in_transaction
    initial_total_changes = connection.total_changes
    clone: sqlite3.Connection | None = None
    try:
        initial_data_version = int(_scalar(connection, "PRAGMA data_version"))
        initial_schema_version = int(_scalar(connection, "PRAGMA schema_version"))
        _reject_preexisting_quantos_functions(connection)
        source_view = _verify_pretrust_family_authority(
            connection,
            expected_authority_instance_id=expected_authority_instance_id,
        )
        clone = _clone_caller_visible_snapshot(
            connection,
            expected_authority_instance_id=expected_authority_instance_id,
            source_view=source_view,
        )
        _reject_preexisting_quantos_functions(clone)
        _register_semantic_functions(clone)
        evidence = _verify_registered_family_authority(
            clone,
            expected_authority_instance_id=expected_authority_instance_id,
            external_providers=external_providers,
        )
        if connection.in_transaction is not initial_transaction or connection.total_changes != initial_total_changes:
            raise FamilyAuthorityVerificationError("family authority verifier changed caller-owned transaction state")
        if (
            int(_scalar(connection, "PRAGMA data_version")) != initial_data_version
            or int(_scalar(connection, "PRAGMA schema_version")) != initial_schema_version
            or int(_scalar(connection, "PRAGMA user_version")) != 3
        ):
            raise FamilyAuthorityVerificationError("family authority source changed during read-only verification")
        return evidence
    except FamilyAuthorityVerificationError:
        raise
    except Exception as exc:
        raise FamilyAuthorityVerificationError("family authority verification failed closed") from exc
    finally:
        if clone is not None:
            clone.close()


__all__ = [
    "FamilyAuthorityExternalProviders",
    "FamilyAuthorityVerificationError",
    "FamilyAuthorityVerificationEvidence",
    "verify_family_authority",
]
