"""Private, immutable verifier registry for the unrendered family schema.

This module closes the Pass-C rule inventory into deterministic read plans and
named implementation descriptors.  It remains deliberately non-executable:
the final ``family_schema`` module is the only future owner of SQL rendering,
SQLite verification, migration, and activation.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum

from .family_schema_c4 import C4_SCHEMA_COMPONENT as _C4
from .family_schema_c5_c6 import (
    C5_SCHEMA_COMPONENT as _C5,
    C6_SCHEMA_COMPONENT as _C6,
)
from .family_schema_c7 import C7_SCHEMA_COMPONENT as _C7
from .family_schema_c8 import C8_SCHEMA_COMPONENT as _C8
from .family_schema_c9 import C9_SCHEMA_COMPONENT as _C9
from .family_schema_composition import compose_family_schema_v3 as _compose
from .family_schema_spec import (
    FAMILY_AUTHORITY_GENESIS_TABLE_SPEC as _GENESIS,
    AuthorityTableSpec,
    ImplementationEvidencePolicy,
    STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE,
    StructuralVerificationOperation,
    StructuralVerificationSourceKind,
    VerificationRuleSpec,
)


class VerifierRegistryKeyKind(StrEnum):
    VERIFIER_CODE = "VERIFIER_CODE"
    STRUCTURAL_HANDLER = "STRUCTURAL_HANDLER"
    EXTERNAL_PROVIDER = "EXTERNAL_PROVIDER"


@dataclass(frozen=True, slots=True)
class StructuralVerificationPlan:
    rule_key: str
    read_tables: tuple[str, ...]
    read_columns: tuple[str, ...]
    operation: StructuralVerificationOperation
    fail_closed: bool = True
    default_pass: bool = False

    def __post_init__(self) -> None:
        if not self.rule_key:
            raise ValueError("structural verification plan requires a rule key")
        if not self.read_tables or not self.read_columns:
            raise ValueError("structural verification plans require a non-empty read set")
        if len(set(self.read_tables)) != len(self.read_tables):
            raise ValueError("structural verification read tables must be unique")
        if len(set(self.read_columns)) != len(self.read_columns):
            raise ValueError("structural verification read columns must be unique")
        if not self.fail_closed or self.default_pass:
            raise ValueError("structural verification plans must reject by default")


@dataclass(frozen=True, slots=True)
class VerifierRegistryEntry:
    key_kind: VerifierRegistryKeyKind
    key: str
    implementation_id: str | None
    implementation_requirement_id: str
    evidence_policy: ImplementationEvidencePolicy

    def __post_init__(self) -> None:
        if type(self.key_kind) is not VerifierRegistryKeyKind:
            raise TypeError("verifier registry key kind must be closed")
        if not self.key or not self.implementation_requirement_id:
            raise ValueError("verifier registry entries require closed identifiers")
        if type(self.evidence_policy) is not ImplementationEvidencePolicy:
            raise TypeError("verifier registry evidence policy must be closed")
        if self.key_kind is VerifierRegistryKeyKind.EXTERNAL_PROVIDER:
            if self.implementation_id is not None or self.evidence_policy is not (
                ImplementationEvidencePolicy.EXCLUDED_DEPLOYMENT_TRUST_ROOT
            ):
                raise ValueError(
                    "external providers are excluded trust roots, not local implementations"
                )
        elif not self.implementation_id or self.evidence_policy is not (
            ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
        ):
            raise ValueError("internal verifier entries require actual implementation evidence")


@dataclass(frozen=True, slots=True)
class VerifierBinding:
    binding_key: str
    verifier_code: str
    implementation_id: str
    implementation_requirement_id: str
    evidence_policy: ImplementationEvidencePolicy

    def __post_init__(self) -> None:
        if (
            not self.binding_key
            or not self.verifier_code
            or not self.implementation_id
            or not self.implementation_requirement_id
        ):
            raise ValueError("verifier bindings require closed identifiers")
        if self.evidence_policy is not ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256:
            raise ValueError("named verifier bindings require actual implementation evidence")


_COMPONENTS = (_C4, _C5, _C6, _C7, _C8, _C9)
_GENESIS_COMPONENT_ID = "FAMILY_AUTHORITY_GENESIS_V1"
_TABLE_REFERENCE_FIELDS = frozenset(
    {
        "anchor_table",
        "binding_table",
        "declaration_table",
        "event_table",
        "left_table",
        "previous_table",
        "referenced_table",
        "right_table",
        "source_table",
        "table",
    }
)
_OPERATION_BY_SOURCE = dict(STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE)


def _rule_key(
    component_id: str,
    surface_kind: str,
    table_name: str,
    rule_id: str,
) -> str:
    return f"{component_id}:{surface_kind}:{table_name}:{rule_id}"


def _rule_bindings() -> tuple[tuple[str, str, VerificationRuleSpec], ...]:
    bindings: list[tuple[str, str, VerificationRuleSpec]] = [
        (
            _rule_key(
                _GENESIS_COMPONENT_ID,
                "TABLE",
                _GENESIS.table_name,
                rule.rule_id,
            ),
            _GENESIS.table_name,
            rule,
        )
        for rule in _GENESIS.verification_rules
    ]
    for component in _COMPONENTS:
        for table in component.tables:
            bindings.extend(
                (
                    _rule_key(
                        component.component_id,
                        "TABLE",
                        table.table_name,
                        rule.rule_id,
                    ),
                    table.table_name,
                    rule,
                )
                for rule in table.verification_rules
            )
        for extension in component.extensions:
            bindings.extend(
                (
                    _rule_key(
                        component.component_id,
                        "EXTENSION",
                        extension.target_table,
                        rule.rule_id,
                    ),
                    extension.target_table,
                    rule,
                )
                for rule in extension.verification_rules
            )
    return tuple(bindings)


def _collect_table_references(value: object) -> set[str]:
    references: set[str] = set()

    def visit(node: object) -> None:
        if is_dataclass(node) and not isinstance(node, type):
            for field in fields(node):
                child = getattr(node, field.name)
                if field.name in _TABLE_REFERENCE_FIELDS and type(child) is str:
                    references.add(child)
                visit(child)
        elif type(node) is tuple:
            for child in node:
                visit(child)

    visit(value)
    return references


_COMPOSITION = _compose()
_TABLES = {table.table_name: table for table in _COMPOSITION.tables}
_INHERITED_TABLES = {
    table.table_name: table for table in _COMPOSITION.inherited_schema_catalog.tables
}
_TABLE_ORDER = {
    table_name: ordinal
    for ordinal, table_name in enumerate((*_TABLES, *_INHERITED_TABLES))
}


def _trigger_for_source(
    owner_table: AuthorityTableSpec,
    source_id: str,
    source_table: str | None,
) -> tuple[AuthorityTableSpec, object]:
    trigger_table = owner_table if source_table is None else _TABLES[source_table]
    matches = tuple(
        trigger for trigger in trigger_table.triggers if trigger.name == source_id
    )
    if len(matches) != 1:
        raise ValueError(f"trigger source {source_id!r} does not resolve exactly")
    return trigger_table, matches[0]


def _plan_read_tables(
    owner_table: AuthorityTableSpec,
    rule: VerificationRuleSpec,
) -> tuple[str, ...]:
    source = rule.structural_source
    if source is None:
        raise ValueError(f"structural rule {rule.rule_id!r} has no proof source")
    names = {owner_table.table_name}
    if source.kind in (
        StructuralVerificationSourceKind.ALL_FOREIGN_KEYS,
        StructuralVerificationSourceKind.GENESIS_FOREIGN_KEY,
    ):
        names.update(item.referenced_table for item in owner_table.foreign_keys)
    elif source.kind is StructuralVerificationSourceKind.EXACT_FOREIGN_KEY:
        assert source.referenced_table is not None
        names.add(source.referenced_table)
    elif source.kind is StructuralVerificationSourceKind.TRIGGER_POLICY:
        trigger_table, trigger = _trigger_for_source(
            owner_table,
            source.source_id or "",
            source.source_table,
        )
        names.add(trigger_table.table_name)
        names.update(_collect_table_references(trigger))
    unknown = names - (_TABLES.keys() | _INHERITED_TABLES.keys())
    if unknown:
        raise ValueError(f"structural read plan references unknown table {min(unknown)!r}")
    return tuple(sorted(names, key=_TABLE_ORDER.__getitem__))


def _all_table_columns(table_name: str) -> tuple[str, ...]:
    table = _TABLES.get(table_name)
    if table is not None:
        return tuple(column.name for column in table.columns)
    inherited = _INHERITED_TABLES[table_name]
    return tuple(column.name for column in inherited.columns)


def _structural_plan(
    binding_key: str,
    table_name: str,
    rule: VerificationRuleSpec,
) -> StructuralVerificationPlan:
    source = rule.structural_source
    if source is None:
        raise ValueError(f"structural rule {binding_key!r} has no reviewed source")
    read_tables = _plan_read_tables(_TABLES[table_name], rule)
    read_columns = tuple(
        f"{read_table}.{column}"
        for read_table in read_tables
        for column in _all_table_columns(read_table)
    )
    return StructuralVerificationPlan(
        rule_key=binding_key,
        read_tables=read_tables,
        read_columns=read_columns,
        operation=_OPERATION_BY_SOURCE[source.kind],
    )


_RULE_BINDINGS = _rule_bindings()
FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS = tuple(
    _structural_plan(binding_key, table_name, rule)
    for binding_key, table_name, rule in _RULE_BINDINGS
    if rule.verifier_code is None
)


def _named_binding_rows() -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = [
        (binding_key, rule.verifier_code)
        for binding_key, _, rule in _RULE_BINDINGS
        if rule.verifier_code is not None
    ]
    for component in _COMPONENTS:
        for extension in component.extensions:
            rows.extend(
                (
                    _rule_key(
                        component.component_id,
                        "CONTINUITY",
                        extension.target_table,
                        continuity.verifier_code,
                    ),
                    continuity.verifier_code,
                )
                for continuity in extension.external_continuity_rules
            )
        rows.extend(
            (
                _rule_key(
                    component.component_id,
                    "GRAPH",
                    graph.derivation_id,
                    graph.verifier_code,
                ),
                graph.verifier_code,
            )
            for graph in component.artifact_graph_derivations
        )
    return tuple(rows)


def _implementation_id(verifier_code: str) -> str:
    return f"FAMILY_SCHEMA_NAMED_VERIFIER::{verifier_code}"


_NAMED_BINDINGS = _named_binding_rows()
FAMILY_SCHEMA_VERIFIER_BINDINGS = tuple(
    VerifierBinding(
        binding_key=binding_key,
        verifier_code=verifier_code,
        implementation_id=_implementation_id(verifier_code),
        implementation_requirement_id=f"NAMED_VERIFIER::{verifier_code}",
        evidence_policy=ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256,
    )
    for binding_key, verifier_code in _NAMED_BINDINGS
)


_VERIFIER_CODES = tuple(sorted({code for _, code in _NAMED_BINDINGS}))
_PROVIDER_KEYS = tuple(
    sorted(
        {
            "|".join(
                (
                    requirement.provider_contract_type,
                    requirement.provider_capability,
                )
            )
            for component in _COMPONENTS
            for requirement in component.required_external_provider_capabilities
        }
    )
)
FAMILY_SCHEMA_VERIFIER_REGISTRY = (
    *(
        VerifierRegistryEntry(
            key_kind=VerifierRegistryKeyKind.VERIFIER_CODE,
            key=code,
            implementation_id=_implementation_id(code),
            implementation_requirement_id=f"NAMED_VERIFIER::{code}",
            evidence_policy=ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256,
        )
        for code in _VERIFIER_CODES
    ),
    *(
        VerifierRegistryEntry(
            key_kind=VerifierRegistryKeyKind.STRUCTURAL_HANDLER,
            key=operation.value,
            implementation_id=f"FAMILY_SCHEMA_STRUCTURAL_HANDLER::{operation.value}",
            implementation_requirement_id=f"STRUCTURAL_HANDLER::{operation.value}",
            evidence_policy=ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256,
        )
        for operation in StructuralVerificationOperation
    ),
    *(
        VerifierRegistryEntry(
            key_kind=VerifierRegistryKeyKind.EXTERNAL_PROVIDER,
            key=key,
            implementation_id=None,
            implementation_requirement_id=f"EXTERNAL_PROVIDER::{key.replace('|', '::')}",
            evidence_policy=(
                ImplementationEvidencePolicy.EXCLUDED_DEPLOYMENT_TRUST_ROOT
            ),
        )
        for key in _PROVIDER_KEYS
    ),
)


def _validate_implementation_requirement_bindings() -> None:
    requirements = {
        item.target_id: item
        for item in _COMPOSITION.implementation_evidence_requirements.entries
        if item.target_kind.value != "SQLITE_SEMANTIC_FUNCTION"
    }
    registry_ids = tuple(
        item.implementation_requirement_id for item in FAMILY_SCHEMA_VERIFIER_REGISTRY
    )
    if len(set(registry_ids)) != len(registry_ids) or set(registry_ids) != set(
        requirements
    ):
        raise ValueError(
            "verifier registry must exactly bind named, structural, and provider requirements"
        )
    for entry in FAMILY_SCHEMA_VERIFIER_REGISTRY:
        requirement = requirements[entry.implementation_requirement_id]
        if (
            entry.implementation_id != requirement.implementation_id
            or entry.evidence_policy is not requirement.evidence_policy
        ):
            raise ValueError(
                "verifier registry implementation metadata must match its requirement"
            )
    registry_by_code = {
        item.key: item
        for item in FAMILY_SCHEMA_VERIFIER_REGISTRY
        if item.key_kind is VerifierRegistryKeyKind.VERIFIER_CODE
    }
    for binding in FAMILY_SCHEMA_VERIFIER_BINDINGS:
        entry = registry_by_code[binding.verifier_code]
        if (
            binding.implementation_id != entry.implementation_id
            or binding.implementation_requirement_id
            != entry.implementation_requirement_id
            or binding.evidence_policy is not entry.evidence_policy
        ):
            raise ValueError(
                "named verifier binding must resolve its registry implementation requirement"
            )


_validate_implementation_requirement_bindings()


__all__ = [
    "FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS",
    "FAMILY_SCHEMA_VERIFIER_BINDINGS",
    "FAMILY_SCHEMA_VERIFIER_REGISTRY",
    "StructuralVerificationOperation",
    "StructuralVerificationPlan",
    "VerifierBinding",
    "VerifierRegistryEntry",
    "VerifierRegistryKeyKind",
]
