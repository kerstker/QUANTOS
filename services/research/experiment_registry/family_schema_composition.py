"""Pure, non-executable composition of the frozen C4-C9 schema components.

This module is the first Pass-M boundary.  It resolves the complete component
DAG and applies every required typed table extension, but it deliberately does
not render SQL, seal a manifest, open SQLite, migrate a store, or expose an
activation path.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import TypeVar

from .family_schema_c4 import C4_SCHEMA_COMPONENT
from .family_schema_c5_c6 import C5_SCHEMA_COMPONENT, C6_SCHEMA_COMPONENT
from .family_schema_c7 import C7_SCHEMA_COMPONENT
from .family_schema_c8 import C8_SCHEMA_COMPONENT
from .family_schema_c9 import C9_SCHEMA_COMPONENT
from .family_schema_v2_catalog import (
    EXACT_V2_INHERITED_SCHEMA_CATALOG,
    ExactV2InheritedSchemaCatalog,
)
from .family_schema_spec import (
    ArtifactGraphLocatorSourceKind,
    AuxiliarySemanticRecipeSpec,
    AuthorityTableExtensionSpec,
    AuthorityTableSpec,
    ChainPredecessorExpression,
    Collation,
    ColumnValue,
    ContentAddressedArtifactGraphDerivationSpec,
    ContractFieldCoverageSpec,
    ContractFieldDerivationSpec,
    CoverageKind,
    ExternalAuthorityClaimProfileSpec,
    ExternalAuthorityContinuitySpec,
    ExternalContinuityRowInputKind,
    ExternalProviderCapabilityRequirementSpec,
    FAMILY_AUTHORITY_GENESIS_TABLE_SPEC,
    FAMILY_SCHEMA_RENDERER_GRAMMAR,
    InsertionPosition,
    ImplementationEvidenceHashMethod,
    ImplementationEvidencePolicy,
    ImplementationEvidenceRequirementCatalog,
    ImplementationEvidenceRequirementSpec,
    ImplementationTargetKind,
    PrefixStreamExtensionExpression,
    QualifiedColumnValue,
    RelatedCanonicalJsonArrayContainsExpression,
    RelatedCanonicalJsonArrayLengthValueSpec,
    RelatedCanonicalJsonArrayValueSpec,
    RelatedValueLookupSpec,
    RelationAggregateExpression,
    RelationArgExtremumExpression,
    RelationChainPrefixExpression,
    RelationExistsExpression,
    RelationJoinAggregateExpression,
    RelationLatestPerDomainPolicyExpression,
    RelationPolicyAggregateExpression,
    RelationSetEqualityExpression,
    RequiredTableExtensionSpec,
    RendererGrammarContract,
    RowSource,
    SchemaComponentSpec,
    SortDirection,
    StructuralVerificationSourceKind,
    STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE,
    TriggerEvent,
    TriggerPolicyKind,
    VerificationRuleKind,
)


class FamilySchemaCompositionError(ValueError):
    """The frozen component set cannot form one exact schema candidate."""


FAMILY_SCHEMA_V3_COMPONENT_IDS = frozenset(
    {
        "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1",
        "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1",
        "C6_EXTERNAL_AUTHORITY_REFS_V1",
        "C7_ONE_SHOT_OOS_V1",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1",
    }
)

_CANONICAL_COMPONENTS = (
    C4_SCHEMA_COMPONENT,
    C5_SCHEMA_COMPONENT,
    C6_SCHEMA_COMPONENT,
    C7_SCHEMA_COMPONENT,
    C8_SCHEMA_COMPONENT,
    C9_SCHEMA_COMPONENT,
)
_CANONICAL_EXTENSION_CONTINUITY = {
    extension.extension_id: extension.external_continuity_rules
    for component in _CANONICAL_COMPONENTS
    for extension in component.extensions
}

_CONTINUITY_ROW_INPUT_CAPABILITIES = {
    "data_snooping_oos_access_attempts": "c7.oos_attempt.event_stream",
    "data_snooping_evaluation_batches": "c7.family_evaluation_batch.atomic",
    "data_snooping_family_closures": "c8.declaration_closure.core",
    "artifact_publication_receipts": "c9.publication_receipt.core",
}

EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY = (
    "EXACT_V3_VERIFIER_COMPOSITION"
)


def auxiliary_semantic_recipe_binding_key(
    component_id: str,
    recipe_id: str,
) -> str:
    return f"AUXILIARY_RECIPE::{component_id}::{recipe_id}"


def external_claim_profile_binding_key(
    provider_contract_type: str,
    provider_capability: str,
) -> str:
    return (
        "EXTERNAL_CLAIM_PROFILE::"
        f"{provider_contract_type}::{provider_capability}"
    )


@dataclass(frozen=True, slots=True)
class AppliedExtensionBinding:
    extension_id: str
    provider_component_id: str
    consumer_component_id: str
    required_capability: str
    target_table: str
    external_continuity_rules: tuple[ExternalAuthorityContinuitySpec, ...]


@dataclass(frozen=True, slots=True)
class ResolvedContractSurfaceBinding:
    consumer_component_id: str
    provider_component_id: str
    required_capability: str
    contract_type: str
    field_names: tuple[str, ...]
    source_extension_id: str | None


@dataclass(frozen=True, slots=True)
class FamilySchemaComposition:
    """One complete, deterministic, still-unrendered schema-v3 candidate."""

    schema_version: int
    source_schema_version: int
    renderer_grammar: RendererGrammarContract
    implementation_evidence_requirements: ImplementationEvidenceRequirementCatalog
    component_order: tuple[str, ...]
    components: tuple[SchemaComponentSpec, ...]
    inherited_schema_catalog: ExactV2InheritedSchemaCatalog
    tables: tuple[AuthorityTableSpec, ...]
    applied_extensions: tuple[AppliedExtensionBinding, ...]
    resolved_contract_surfaces: tuple[ResolvedContractSurfaceBinding, ...]
    coverage: tuple[ContractFieldCoverageSpec, ...]
    contract_derivations: tuple[ContractFieldDerivationSpec, ...]
    artifact_graph_derivations: tuple[
        ContentAddressedArtifactGraphDerivationSpec,
        ...,
    ]
    external_provider_requirements: tuple[
        ExternalProviderCapabilityRequirementSpec,
        ...,
    ]
    external_authority_claim_profiles: tuple[
        ExternalAuthorityClaimProfileSpec,
        ...,
    ]
    auxiliary_semantic_recipes: tuple[AuxiliarySemanticRecipeSpec, ...]

    def __post_init__(self) -> None:
        if type(self.implementation_evidence_requirements) is not (
            ImplementationEvidenceRequirementCatalog
        ):
            raise FamilySchemaCompositionError(
                "implementation evidence requirements must be exact typed metadata"
            )
        expected_profiles = tuple(
            profile
            for component in self.components
            for profile in component.external_authority_claim_profiles
        )
        expected_recipes = tuple(
            recipe
            for component in self.components
            for recipe in component.auxiliary_semantic_recipes
        )
        if self.external_authority_claim_profiles != expected_profiles:
            raise FamilySchemaCompositionError(
                "external authority claim profiles must exactly match components"
            )
        if self.auxiliary_semantic_recipes != expected_recipes:
            raise FamilySchemaCompositionError(
                "auxiliary semantic recipes must exactly match components"
            )
        expected = _implementation_evidence_requirements(
            self.components,
            self.external_provider_requirements,
            self.renderer_grammar,
        )
        if self.implementation_evidence_requirements != expected:
            raise FamilySchemaCompositionError(
                "implementation evidence requirements do not exactly cover the composition"
            )

    @property
    def applied_extension_ids(self) -> tuple[str, ...]:
        return tuple(item.extension_id for item in self.applied_extensions)

    @property
    def external_continuity_rules(self) -> tuple[ExternalAuthorityContinuitySpec, ...]:
        return tuple(
            rule
            for extension in self.applied_extensions
            for rule in extension.external_continuity_rules
        )


_T = TypeVar("_T")


def _implementation_binding_key(
    component_id: str,
    surface_kind: str,
    table_name: str,
    rule_id: str,
) -> str:
    return f"{component_id}:{surface_kind}:{table_name}:{rule_id}"


def _implementation_rule_rows(
    components: tuple[SchemaComponentSpec, ...],
) -> tuple[tuple[str, object], ...]:
    rows: list[tuple[str, object]] = [
        (
            _implementation_binding_key(
                "FAMILY_AUTHORITY_GENESIS_V1",
                "TABLE",
                FAMILY_AUTHORITY_GENESIS_TABLE_SPEC.table_name,
                rule.rule_id,
            ),
            rule,
        )
        for rule in FAMILY_AUTHORITY_GENESIS_TABLE_SPEC.verification_rules
    ]
    for component in components:
        for table in component.tables:
            rows.extend(
                (
                    _implementation_binding_key(
                        component.component_id,
                        "TABLE",
                        table.table_name,
                        rule.rule_id,
                    ),
                    rule,
                )
                for rule in table.verification_rules
            )
        for extension in component.extensions:
            rows.extend(
                (
                    _implementation_binding_key(
                        component.component_id,
                        "EXTENSION",
                        extension.target_table,
                        rule.rule_id,
                    ),
                    rule,
                )
                for rule in extension.verification_rules
            )
    return tuple(rows)


def _implementation_evidence_requirements(
    components: tuple[SchemaComponentSpec, ...],
    external_provider_requirements: tuple[ExternalProviderCapabilityRequirementSpec, ...],
    renderer_grammar: RendererGrammarContract,
) -> ImplementationEvidenceRequirementCatalog:
    hash_method = (
        ImplementationEvidenceHashMethod.PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1
    )
    hash_version = "QUANTOS_IMPLEMENTATION_EVIDENCE_HASH_V1"
    semantic_entries = tuple(
        ImplementationEvidenceRequirementSpec(
            target_kind=ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION,
            target_id=f"SQLITE_SEMANTIC::{abi.operation.value}",
            implementation_id=f"FAMILY_SCHEMA_SQLITE_SEMANTIC::{abi.sqlite_name}",
            evidence_policy=ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256,
            hash_method=hash_method,
            hash_method_version=hash_version,
            canonical_binding_keys=(
                "SEMANTIC_ABI::"
                f"{abi.operation.value}::{abi.sqlite_name}::{abi.semantic_contract_id}",
            ),
            semantic_operation=abi.operation,
            sqlite_name=abi.sqlite_name,
            semantic_contract_id=abi.semantic_contract_id,
        )
        for abi in renderer_grammar.semantic_function_abis
    )

    operation_by_source = dict(STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE)
    named_bindings: dict[str, list[str]] = {}
    structural_bindings: dict[object, list[str]] = {}

    def append_unique(bindings: dict[object, list[str]], owner: object, key: str) -> None:
        values = bindings.setdefault(owner, [])
        if key not in values:
            values.append(key)

    for binding_key, rule in _implementation_rule_rows(components):
        if rule.verifier_code is not None:
            append_unique(named_bindings, rule.verifier_code, binding_key)
            continue
        source = rule.structural_source
        if source is None or source.kind not in operation_by_source:
            raise FamilySchemaCompositionError(
                "structural implementation evidence requires one closed source operation"
            )
        operation = operation_by_source[source.kind]
        append_unique(structural_bindings, operation, binding_key)
    for component in components:
        for extension in component.extensions:
            for continuity in extension.external_continuity_rules:
                append_unique(
                    named_bindings,
                    continuity.verifier_code,
                    _implementation_binding_key(
                        component.component_id,
                        "CONTINUITY",
                        extension.target_table,
                        continuity.verifier_code,
                    ),
                )
        for graph in component.artifact_graph_derivations:
            append_unique(
                named_bindings,
                graph.verifier_code,
                _implementation_binding_key(
                    component.component_id,
                    "GRAPH",
                    graph.derivation_id,
                    graph.verifier_code,
                ),
            )

    profile_by_provider: dict[
        tuple[str, str],
        ExternalAuthorityClaimProfileSpec,
    ] = {}
    for component in components:
        tables = {table.table_name: table for table in component.tables}
        for recipe in component.auxiliary_semantic_recipes:
            recipe_key = auxiliary_semantic_recipe_binding_key(
                component.component_id,
                recipe.recipe_id,
            )
            rules = {
                rule.rule_id: rule
                for rule in tables[recipe.table_name].verification_rules
            }
            for rule_id in recipe.required_rule_ids:
                rule = rules[rule_id]
                if rule.verifier_code is not None:
                    append_unique(named_bindings, rule.verifier_code, recipe_key)
                    continue
                source = rule.structural_source
                if source is None or source.kind not in operation_by_source:
                    raise FamilySchemaCompositionError(
                        "recipe implementation evidence has no closed verifier target"
                    )
                append_unique(
                    structural_bindings,
                    operation_by_source[source.kind],
                    recipe_key,
                )
        for profile in component.external_authority_claim_profiles:
            provider_key = (
                profile.provider_contract_type,
                profile.provider_capability,
            )
            if provider_key in profile_by_provider:
                raise FamilySchemaCompositionError(
                    "claim-profile implementation bindings must be unique"
                )
            profile_by_provider[provider_key] = profile

    rule_rows = _implementation_rule_rows(components)
    for provider_key, profile in profile_by_provider.items():
        profile_key = external_claim_profile_binding_key(*provider_key)
        verifier_codes = {
            rule.verifier_code
            for _, rule in rule_rows
            if (
                rule.provider_contract_type,
                rule.provider_capability,
            )
            == provider_key
            and rule.verifier_code is not None
        }
        if not verifier_codes:
            raise FamilySchemaCompositionError(
                "claim profile has no named verifier implementation binding"
            )
        for verifier_code in sorted(verifier_codes):
            append_unique(named_bindings, verifier_code, profile_key)

    named_entries = tuple(
        ImplementationEvidenceRequirementSpec(
            target_kind=ImplementationTargetKind.NAMED_VERIFIER,
            target_id=f"NAMED_VERIFIER::{verifier_code}",
            implementation_id=f"FAMILY_SCHEMA_NAMED_VERIFIER::{verifier_code}",
            evidence_policy=ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256,
            hash_method=hash_method,
            hash_method_version=hash_version,
            canonical_binding_keys=(
                *named_bindings[verifier_code],
                EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY,
            ),
            verifier_code=verifier_code,
        )
        for verifier_code in sorted(named_bindings)
    )
    structural_entries = tuple(
        ImplementationEvidenceRequirementSpec(
            target_kind=ImplementationTargetKind.STRUCTURAL_HANDLER,
            target_id=f"STRUCTURAL_HANDLER::{operation.value}",
            implementation_id=f"FAMILY_SCHEMA_STRUCTURAL_HANDLER::{operation.value}",
            evidence_policy=ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256,
            hash_method=hash_method,
            hash_method_version=hash_version,
            canonical_binding_keys=(
                *structural_bindings[operation],
                EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY,
            ),
            structural_operation=operation,
        )
        for _, operation in STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE
        if operation in structural_bindings
    )
    external_entries = tuple(
        ImplementationEvidenceRequirementSpec(
            target_kind=ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT,
            target_id=(
                "EXTERNAL_PROVIDER::"
                f"{requirement.provider_contract_type}::{requirement.provider_capability}"
            ),
            implementation_id=None,
            evidence_policy=(
                ImplementationEvidencePolicy.EXCLUDED_DEPLOYMENT_TRUST_ROOT
            ),
            hash_method=ImplementationEvidenceHashMethod.NONE,
            hash_method_version="NONE",
            canonical_binding_keys=tuple(
                key
                for key in (
                    "EXTERNAL_PROVIDER_CONTRACT::"
                    f"{requirement.provider_contract_type}::{requirement.provider_capability}",
                    (
                        external_claim_profile_binding_key(
                            requirement.provider_contract_type,
                            requirement.provider_capability,
                        )
                        if (
                            requirement.provider_contract_type,
                            requirement.provider_capability,
                        )
                        in profile_by_provider
                        else None
                    ),
                )
                if key is not None
            ),
            provider_contract_type=requirement.provider_contract_type,
            provider_capability=requirement.provider_capability,
            requires_callable_closure=False,
        )
        for requirement in sorted(
            external_provider_requirements,
            key=lambda item: (
                item.provider_contract_type,
                item.provider_capability,
            ),
        )
    )
    return ImplementationEvidenceRequirementCatalog(
        entries=(
            *semantic_entries,
            *named_entries,
            *structural_entries,
            *external_entries,
        )
    )


def _ordered_insert(
    values: tuple[_T, ...],
    *,
    anchor: str,
    position: InsertionPosition,
    additions: tuple[_T, ...],
    names: tuple[str, ...],
) -> tuple[_T, ...]:
    try:
        anchor_index = names.index(anchor)
    except ValueError as exc:
        raise FamilySchemaCompositionError(
            f"extension anchor {anchor!r} is absent from its frozen base table"
        ) from exc
    insertion_index = (
        anchor_index
        if position is InsertionPosition.BEFORE
        else anchor_index + 1
    )
    return (*values[:insertion_index], *additions, *values[insertion_index:])


def _topological_components(
    components: tuple[SchemaComponentSpec, ...],
) -> tuple[SchemaComponentSpec, ...]:
    if any(type(component) is not SchemaComponentSpec for component in components):
        raise FamilySchemaCompositionError("component input must use exact SchemaComponentSpec values")
    component_ids = tuple(component.component_id for component in components)
    if len(set(component_ids)) != len(component_ids):
        raise FamilySchemaCompositionError("component IDs must be globally unique")
    actual_ids = set(component_ids)
    if actual_ids != FAMILY_SCHEMA_V3_COMPONENT_IDS:
        missing = sorted(FAMILY_SCHEMA_V3_COMPONENT_IDS - actual_ids)
        unknown = sorted(actual_ids - FAMILY_SCHEMA_V3_COMPONENT_IDS)
        raise FamilySchemaCompositionError(
            f"component set is not exact; missing={missing!r}, unknown={unknown!r}"
        )
    all_capabilities = tuple(
        capability
        for component in components
        for capability in component.capabilities
    )
    if len(set(all_capabilities)) != len(all_capabilities):
        raise FamilySchemaCompositionError(
            "component capability owners must be globally unique"
        )

    by_id = {component.component_id: component for component in components}
    unresolved_dependencies: dict[str, set[str]] = {}
    for component in components:
        dependencies: set[str] = set()
        for dependency in component.dependencies:
            provider = by_id.get(dependency.component_id)
            if provider is None:
                raise FamilySchemaCompositionError(
                    f"component dependency {dependency.component_id!r} is absent"
                )
            missing_capabilities = set(dependency.required_capabilities) - set(
                provider.capabilities
            )
            if missing_capabilities:
                raise FamilySchemaCompositionError(
                    "component dependency capability is absent: "
                    f"{sorted(missing_capabilities)[0]!r}"
                )
            dependencies.add(dependency.component_id)
        unresolved_dependencies[component.component_id] = dependencies

    ordered: list[SchemaComponentSpec] = []
    emitted: set[str] = set()
    while len(ordered) != len(components):
        ready = sorted(
            component_id
            for component_id, dependencies in unresolved_dependencies.items()
            if component_id not in emitted and dependencies <= emitted
        )
        if not ready:
            blocked = sorted(set(unresolved_dependencies) - emitted)
            raise FamilySchemaCompositionError(
                f"component dependency cycle blocks {blocked!r}"
            )
        for component_id in ready:
            emitted.add(component_id)
            ordered.append(by_id[component_id])
    return tuple(ordered)


def _validate_external_continuity_handler_inputs(
    components: tuple[SchemaComponentSpec, ...],
) -> None:
    """Close every typed continuity row read to its table owner and rules."""

    table_owner: dict[str, str] = {}
    table_columns: dict[str, set[str]] = {}
    table_rule_ids: dict[str, set[str]] = {}
    for component in components:
        for table in component.tables:
            table_owner[table.table_name] = component.component_id
            table_columns[table.table_name] = {
                column.name for column in table.columns
            }
            table_rule_ids[table.table_name] = {
                rule.rule_id for rule in table.verification_rules
            }
    for component in components:
        for extension in component.extensions:
            if extension.target_table not in table_owner:
                continue
            if extension.column_insertion is not None:
                table_columns[extension.target_table].update(
                    column.name for column in extension.column_insertion.columns
                )
            table_rule_ids[extension.target_table].update(
                rule.rule_id for rule in extension.verification_rules
            )

    for component in components:
        dependencies = {
            dependency.component_id: set(dependency.required_capabilities)
            for dependency in component.dependencies
        }
        for extension in component.extensions:
            for continuity in extension.external_continuity_rules:
                profile = continuity.semantic_profile
                if profile is None:
                    continue
                for branch in profile.chronology_branches:
                    for row_input in branch.row_inputs:
                        owner = table_owner.get(row_input.table)
                        capability = _CONTINUITY_ROW_INPUT_CAPABILITIES.get(
                            row_input.table
                        )
                        if owner is None or capability is None:
                            raise FamilySchemaCompositionError(
                                "external continuity row input lacks a frozen table owner "
                                f"or capability: {row_input.table!r}"
                            )
                        if owner != component.component_id and capability not in (
                            dependencies.get(owner) or set()
                        ):
                            raise FamilySchemaCompositionError(
                                "external continuity row input lacks its direct owner "
                                f"capability: {row_input.table!r} requires {capability!r}"
                            )
                        if row_input.source_kind is (
                            ExternalContinuityRowInputKind.SUBJECT_ROW
                        ) and row_input.table != extension.target_table:
                            raise FamilySchemaCompositionError(
                                "external continuity subject row must be the extension "
                                f"target table: {row_input.table!r}"
                            )
                        missing_fields = set(row_input.required_fields) - table_columns[
                            row_input.table
                        ]
                        missing_binding_columns = {
                            binding.related_column for binding in row_input.bindings
                        } - table_columns[row_input.table]
                        if missing_fields or missing_binding_columns:
                            raise FamilySchemaCompositionError(
                                "external continuity row input references absent fields "
                                f"on {row_input.table!r}"
                            )
                        missing_rules = set(
                            row_input.required_verification_rule_ids
                        ) - table_rule_ids[row_input.table]
                        if missing_rules:
                            raise FamilySchemaCompositionError(
                                "external continuity row input verification rule belongs "
                                f"to a different table: {sorted(missing_rules)[0]!r}"
                            )


def _resolve_extensions(
    components: tuple[SchemaComponentSpec, ...],
) -> tuple[
    dict[str, tuple[AuthorityTableExtensionSpec, ...]],
    tuple[AppliedExtensionBinding, ...],
]:
    table_owner: dict[str, str] = {}
    for component in components:
        for table in component.tables:
            if table.table_name in table_owner:
                raise FamilySchemaCompositionError(
                    f"table {table.table_name!r} has multiple component owners"
                )
            table_owner[table.table_name] = component.component_id

    provided: dict[str, tuple[SchemaComponentSpec, AuthorityTableExtensionSpec]] = {}
    for component in components:
        for extension in component.extensions:
            if extension.extension_id in provided:
                raise FamilySchemaCompositionError(
                    f"extension {extension.extension_id!r} is provided more than once"
                )
            provided[extension.extension_id] = (component, extension)

    required: dict[
        str,
        tuple[SchemaComponentSpec, RequiredTableExtensionSpec],
    ] = {}
    for component in components:
        for requirement in component.required_extensions:
            if requirement.extension_id in required:
                raise FamilySchemaCompositionError(
                    f"extension {requirement.extension_id!r} is required more than once"
                )
            required[requirement.extension_id] = (component, requirement)

    if set(provided) != set(required):
        missing = sorted(set(required) - set(provided))
        unrequired = sorted(set(provided) - set(required))
        raise FamilySchemaCompositionError(
            f"extension set is not exact; missing={missing!r}, unrequired={unrequired!r}"
        )

    by_target: dict[str, list[AuthorityTableExtensionSpec]] = {}
    component_dependencies = {
        component.component_id: {
            dependency.component_id for dependency in component.dependencies
        }
        for component in components
    }
    applied: list[AppliedExtensionBinding] = []
    for extension_id in provided:
        provider, extension = provided[extension_id]
        consumer, requirement = required[extension_id]
        if (
            requirement.provider_component_id != provider.component_id
            or requirement.target_table != extension.target_table
            or requirement.target_table not in table_owner
            or table_owner[requirement.target_table] != consumer.component_id
            or requirement.required_capability not in provider.capabilities
        ):
            raise FamilySchemaCompositionError(
                f"extension {extension_id!r} does not satisfy its exact requirement"
            )
        if consumer.component_id not in component_dependencies[provider.component_id]:
            raise FamilySchemaCompositionError(
                f"extension provider {provider.component_id!r} lacks its owner dependency"
            )
        if (
            extension.external_continuity_rules
            != _CANONICAL_EXTENSION_CONTINUITY[extension_id]
        ):
            raise FamilySchemaCompositionError(
                f"extension {extension_id!r} continuity differs from its frozen contract"
            )
        by_target.setdefault(extension.target_table, []).append(extension)
        applied.append(
            AppliedExtensionBinding(
                extension_id=extension.extension_id,
                provider_component_id=provider.component_id,
                consumer_component_id=consumer.component_id,
                required_capability=requirement.required_capability,
                target_table=extension.target_table,
                external_continuity_rules=extension.external_continuity_rules,
            )
        )

    return (
        {target: tuple(extensions) for target, extensions in by_target.items()},
        tuple(applied),
    )


def _apply_extensions(
    table: AuthorityTableSpec,
    extensions: tuple[AuthorityTableExtensionSpec, ...],
) -> AuthorityTableSpec:
    base_column_names = tuple(column.name for column in table.columns)
    base_hash_fields = table.row_hash.canonical_fields
    column_claims: set[tuple[InsertionPosition, str]] = set()
    hash_claims: set[tuple[InsertionPosition, str]] = set()
    for extension in extensions:
        if extension.column_insertion is not None:
            claim = (
                extension.column_insertion.position,
                extension.column_insertion.anchor_column,
            )
            if claim in column_claims:
                raise FamilySchemaCompositionError(
                    f"extension column anchor conflict on {table.table_name!r}"
                )
            column_claims.add(claim)
            if extension.column_insertion.anchor_column not in base_column_names:
                raise FamilySchemaCompositionError(
                    f"extension anchor {extension.column_insertion.anchor_column!r} "
                    f"is absent from {table.table_name!r}"
                )
        if extension.row_hash_field_insertion is not None:
            claim = (
                extension.row_hash_field_insertion.position,
                extension.row_hash_field_insertion.anchor_field,
            )
            if claim in hash_claims:
                raise FamilySchemaCompositionError(
                    f"extension row-hash anchor conflict on {table.table_name!r}"
                )
            hash_claims.add(claim)
            if extension.row_hash_field_insertion.anchor_field not in base_hash_fields:
                raise FamilySchemaCompositionError(
                    f"extension row-hash anchor "
                    f"{extension.row_hash_field_insertion.anchor_field!r} is absent "
                    f"from {table.table_name!r}"
                )

    columns = table.columns
    row_hash_fields = table.row_hash.canonical_fields
    foreign_keys = table.foreign_keys
    indexes = table.indexes
    checks = table.checks
    triggers = table.triggers
    verification_rules = table.verification_rules
    for extension in extensions:
        if extension.column_insertion is not None:
            insertion = extension.column_insertion
            columns = _ordered_insert(
                columns,
                anchor=insertion.anchor_column,
                position=insertion.position,
                additions=insertion.columns,
                names=tuple(column.name for column in columns),
            )
            hash_insertion = extension.row_hash_field_insertion
            assert hash_insertion is not None
            row_hash_fields = _ordered_insert(
                row_hash_fields,
                anchor=hash_insertion.anchor_field,
                position=hash_insertion.position,
                additions=hash_insertion.fields,
                names=row_hash_fields,
            )
        foreign_keys = (*foreign_keys, *extension.foreign_keys)
        indexes = (*indexes, *extension.indexes)
        checks = (*checks, *extension.checks)
        triggers = (*triggers, *extension.triggers)
        verification_rules = (
            *verification_rules,
            *extension.verification_rules,
        )

    return AuthorityTableSpec(
        table_name=table.table_name,
        columns=columns,
        primary_key_columns=table.primary_key_columns,
        row_hash=type(table.row_hash)(
            canonical_fields=row_hash_fields,
            record_hash_column=table.row_hash.record_hash_column,
            version_literal=table.row_hash.version_literal,
            algorithm=table.row_hash.algorithm,
            encoding=table.row_hash.encoding,
        ),
        triggers=triggers,
        foreign_keys=foreign_keys,
        indexes=indexes,
        checks=checks,
        chain=table.chain,
        verification_rules=verification_rules,
    )


def _validate_publication_receipt_final_row_hash(
    tables: tuple[AuthorityTableSpec, ...],
) -> None:
    publication = next(
        (
            table
            for table in tables
            if table.table_name == "artifact_publication_receipts"
        ),
        None,
    )
    if publication is None:
        raise FamilySchemaCompositionError(
            "artifact publication receipt table is absent from the composed schema"
        )
    rules = tuple(
        rule
        for rule in publication.verification_rules
        if rule.kind is VerificationRuleKind.ROW_HASH
        and rule.rule_id == "publication_receipt_row_hash"
    )
    if len(rules) != 1:
        raise FamilySchemaCompositionError(
            "artifact publication receipt requires exactly one final row-hash rule"
        )
    rule = rules[0]
    if (
        rule.columns != publication.row_hash.canonical_fields
        or rule.verifier_code != "c9.verify_publication_receipt_row_hash_v1"
    ):
        raise FamilySchemaCompositionError(
            "artifact publication receipt row-hash rule must cover the exact composed fields"
        )


_TABLE_REFERENCE_FIELDS = frozenset(
    {
        "anchor_table",
        "binding_table",
        "declaration_table",
        "left_table",
        "previous_table",
        "referenced_table",
        "right_table",
        "source_table",
        "table",
    }
)
_DIRECT_RELATED_COLUMN_FIELDS = frozenset(
    {
        "column",
        "domain_column",
        "latest_ordinal_column",
        "ordinal_column",
        "previous_hash_column",
        "previous_period_count_column",
        "previous_prefix_hash_column",
        "previous_replication_count_column",
        "record_hash_column",
    }
)


def _walk_dataclass_values(value: object) -> tuple[object, ...]:
    values: list[object] = []

    def visit(item: object) -> None:
        if is_dataclass(item) and not isinstance(item, type):
            values.append(item)
            for field in fields(item):
                visit(getattr(item, field.name))
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)

    visit(value)
    return tuple(values)


def _require_relation_columns(
    table_name: str,
    columns: tuple[str, ...],
    *,
    column_names: dict[str, set[str]],
) -> None:
    known = column_names.get(table_name)
    if known is None:
        raise FamilySchemaCompositionError(
            f"relation AST references unknown table {table_name!r}"
        )
    missing = next((column for column in columns if column not in known), None)
    if missing is not None:
        raise FamilySchemaCompositionError(
            f"relation AST references unknown column {missing!r} on {table_name!r}"
        )


def _validate_lookup_bindings(
    table_name: str,
    bindings: tuple[object, ...],
    *,
    column_names: dict[str, set[str]],
    parent_keys: dict[str, set[tuple[str, ...]]],
    require_unique_key: bool,
) -> None:
    related_columns = tuple(
        getattr(binding, "related_column") for binding in bindings
    )
    _require_relation_columns(
        table_name,
        related_columns,
        column_names=column_names,
    )
    if require_unique_key and not any(
        set(key) <= set(related_columns) for key in parent_keys[table_name]
    ):
        raise FamilySchemaCompositionError(
            f"relation AST on {table_name!r} lacks an exact unique lookup key"
        )


def _validate_related_predicate_columns(
    value: object | None,
    table_name: str,
    *,
    column_names: dict[str, set[str]],
) -> None:
    if value is None:
        return
    related_columns = tuple(
        node.column
        for node in _walk_dataclass_values(value)
        if type(node) is ColumnValue and node.source is RowSource.RELATED
    )
    _require_relation_columns(
        table_name,
        related_columns,
        column_names=column_names,
    )


def _validate_join_relation(
    value: RelationJoinAggregateExpression | RelationArgExtremumExpression,
    *,
    column_names: dict[str, set[str]],
) -> None:
    _validate_lookup_bindings(
        value.anchor_table,
        value.bindings,
        column_names=column_names,
        parent_keys={},
        require_unique_key=False,
    )
    alias_tables = {value.anchor_alias: value.anchor_table}
    for join in value.joins:
        if join.table not in column_names:
            raise FamilySchemaCompositionError(
                f"relation AST references unknown table {join.table!r}"
            )
        for equality in join.on:
            left_table = alias_tables.get(equality.left_alias)
            if left_table is None:
                raise FamilySchemaCompositionError(
                    f"relation AST references unknown alias {equality.left_alias!r}"
                )
            _require_relation_columns(
                left_table,
                (equality.left_column,),
                column_names=column_names,
            )
            _require_relation_columns(
                join.table,
                (equality.right_column,),
                column_names=column_names,
            )
        alias_tables[join.alias] = join.table
        for qualified in (
            node
            for node in _walk_dataclass_values(join.predicate)
            if type(node) is QualifiedColumnValue
        ):
            qualified_table = alias_tables.get(qualified.relation_alias)
            if qualified_table is None:
                raise FamilySchemaCompositionError(
                    f"relation AST references unknown alias {qualified.relation_alias!r}"
                )
            _require_relation_columns(
                qualified_table,
                (qualified.column,),
                column_names=column_names,
            )

    for qualified in (
        node
        for node in _walk_dataclass_values(value)
        if type(node) is QualifiedColumnValue
    ):
        qualified_table = alias_tables.get(qualified.relation_alias)
        if qualified_table is None:
            raise FamilySchemaCompositionError(
                f"relation AST references unknown alias {qualified.relation_alias!r}"
            )
        _require_relation_columns(
            qualified_table,
            (qualified.column,),
            column_names=column_names,
        )


def _validate_relation_ast(
    value: object,
    *,
    allowed_tables: set[str],
    column_names: dict[str, set[str]],
    parent_keys: dict[str, set[tuple[str, ...]]],
) -> None:
    for node in _walk_dataclass_values(value):
        node_fields = {field.name: getattr(node, field.name) for field in fields(node)}
        references = {
            referenced
            for name, referenced in node_fields.items()
            if name in _TABLE_REFERENCE_FIELDS and isinstance(referenced, str)
        }
        unknown = references - allowed_tables
        if unknown:
            raise FamilySchemaCompositionError(
                f"relation AST references unknown table {sorted(unknown)[0]!r}"
            )

        if type(node) in (
            RelatedValueLookupSpec,
            RelatedCanonicalJsonArrayValueSpec,
            RelatedCanonicalJsonArrayLengthValueSpec,
            RelatedCanonicalJsonArrayContainsExpression,
        ):
            _validate_lookup_bindings(
                node.table,
                node.bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=True,
            )
            remote_value = node.value if type(node) is RelatedValueLookupSpec else node.column
            _require_relation_columns(
                node.table,
                (remote_value,),
                column_names=column_names,
            )
        elif type(node) is RelationExistsExpression:
            _validate_lookup_bindings(
                node.table,
                node.bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=False,
            )
            _validate_related_predicate_columns(
                node.predicate,
                node.table,
                column_names=column_names,
            )
        elif type(node) is RelationAggregateExpression:
            _validate_lookup_bindings(
                node.table,
                node.bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=False,
            )
            aggregate_columns = (
                *((node.value,) if node.value is not None else ()),
                *(item.column for item in node.order),
            )
            _require_relation_columns(
                node.table,
                aggregate_columns,
                column_names=column_names,
            )
            _validate_related_predicate_columns(
                node.predicate,
                node.table,
                column_names=column_names,
            )
        elif type(node) in (
            RelationJoinAggregateExpression,
            RelationArgExtremumExpression,
        ):
            _validate_join_relation(node, column_names=column_names)
        elif type(node) in (
            RelationPolicyAggregateExpression,
            RelationLatestPerDomainPolicyExpression,
        ):
            _validate_lookup_bindings(
                node.table,
                node.bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=False,
            )
            policy_columns = (
                *node.inputs,
                *(item.column for item in node.order),
                node.domain_column,
                node.latest_ordinal_column,
            )
            _require_relation_columns(
                node.table,
                policy_columns,
                column_names=column_names,
            )
            _validate_related_predicate_columns(
                getattr(node, "predicate", None),
                node.table,
                column_names=column_names,
            )
        elif type(node) is PrefixStreamExtensionExpression:
            _validate_lookup_bindings(
                node.previous_table,
                node.previous_bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=False,
            )
            _require_relation_columns(
                node.previous_table,
                (
                    node.previous_prefix_hash_column,
                    node.previous_replication_count_column,
                    node.previous_period_count_column,
                ),
                column_names=column_names,
            )
            _validate_lookup_bindings(
                node.declaration_table,
                node.declaration_bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=True,
            )
            _require_relation_columns(
                node.declaration_table,
                node.algorithm_columns,
                column_names=column_names,
            )
        elif type(node) is ChainPredecessorExpression:
            _validate_lookup_bindings(
                node.table,
                node.partition_bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=False,
            )
            _require_relation_columns(
                node.table,
                (node.ordinal_column, node.record_hash_column),
                column_names=column_names,
            )
        elif type(node) is RelationChainPrefixExpression:
            _validate_lookup_bindings(
                node.table,
                node.partition_bindings,
                column_names=column_names,
                parent_keys=parent_keys,
                require_unique_key=False,
            )
            _require_relation_columns(
                node.table,
                (
                    node.ordinal_column,
                    node.record_hash_column,
                    node.previous_hash_column,
                ),
                column_names=column_names,
            )
        elif type(node) is RelationSetEqualityExpression:
            for table_name, bindings, value_column, predicate in (
                (
                    node.left_table,
                    node.left_bindings,
                    node.left_value,
                    node.left_predicate,
                ),
                (
                    node.right_table,
                    node.right_bindings,
                    node.right_value,
                    node.right_predicate,
                ),
            ):
                _validate_lookup_bindings(
                    table_name,
                    bindings,
                    column_names=column_names,
                    parent_keys=parent_keys,
                    require_unique_key=False,
                )
                _require_relation_columns(
                    table_name,
                    (value_column, *(item.column for item in node.order)),
                    column_names=column_names,
                )
                _validate_related_predicate_columns(
                    predicate,
                    table_name,
                    column_names=column_names,
                )


def _validate_global_catalog(
    tables: tuple[AuthorityTableSpec, ...],
    inherited_catalog: ExactV2InheritedSchemaCatalog,
) -> None:
    if (
        type(inherited_catalog) is not ExactV2InheritedSchemaCatalog
        or inherited_catalog.schema_version != 2
        or not inherited_catalog.catalog_id
    ):
        raise FamilySchemaCompositionError("exact-v2 inherited catalog is invalid")
    object_names: list[str] = []
    rule_ids: list[str] = []
    table_names = {table.table_name for table in tables}
    inherited_table_names = {table.table_name for table in inherited_catalog.tables}
    allowed_references = table_names | inherited_table_names
    for table in tables:
        object_names.append(table.table_name)
        object_names.extend(index.name for index in table.indexes)
        object_names.extend(trigger.name for trigger in table.triggers)
        for rule in table.verification_rules:
            rule_ids.append(rule.rule_id)
    if len(set(object_names)) != len(object_names):
        raise FamilySchemaCompositionError("owned SQLite object names must be globally unique")
    inherited_object_names = {item.name for item in inherited_catalog.objects}
    collisions = set(object_names).intersection(inherited_object_names)
    if collisions:
        raise FamilySchemaCompositionError(
            f"owned SQLite object name collides with exact-v2: {sorted(collisions)[0]!r}"
        )
    if len(set(rule_ids)) != len(rule_ids):
        raise FamilySchemaCompositionError("verification rule IDs must be globally unique")

    parent_keys: dict[str, set[tuple[str, ...]]] = {
        table.table_name: {table.primary_key_columns}
        for table in tables
    }
    for table in tables:
        parent_keys[table.table_name].update(
            tuple(column.column for column in index.columns)
            for index in table.indexes
            if index.unique
            and index.predicate is None
            and all(
                column.direction is SortDirection.ASC
                and column.collation is Collation.BINARY
                for column in index.columns
            )
        )
    parent_keys.update(
        {
            table.table_name: {
                key.columns
                for key in table.unique_keys
                if all(
                    direction is SortDirection.ASC
                    for direction in key.directions
                )
                and all(
                    collation is Collation.BINARY
                    for collation in key.collations
                )
            }
            for table in inherited_catalog.tables
        }
    )
    column_specs = {
        table.table_name: {column.name: column for column in table.columns}
        for table in tables
    }
    column_specs.update(
        {
            table.table_name: {column.name: column for column in table.columns}
            for table in inherited_catalog.tables
        }
    )
    column_names = {
        table_name: set(columns)
        for table_name, columns in column_specs.items()
    }
    owned_tables = {table.table_name: table for table in tables}
    for table in tables:
        for rule in table.verification_rules:
            source = rule.structural_source
            if source is None or source.source_table is None:
                continue
            source_table = owned_tables.get(source.source_table)
            if source_table is None:
                raise FamilySchemaCompositionError(
                    f"remote structural trigger table {source.source_table!r} is not owned"
                )
            matching_triggers = tuple(
                trigger
                for trigger in source_table.triggers
                if trigger.name == source.source_id
            )
            if len(matching_triggers) != 1:
                raise FamilySchemaCompositionError(
                    f"remote structural trigger {source.source_id!r} does not resolve exactly"
                )
            trigger = matching_triggers[0]
            if (
                trigger.kind is not TriggerPolicyKind.INSERT_GUARD
                or trigger.event is not TriggerEvent.INSERT
                or trigger.violation_when is None
            ):
                raise FamilySchemaCompositionError(
                    f"remote structural trigger {source.source_id!r} is not a closed insert guard"
                )
        for foreign_key in table.foreign_keys:
            keys = parent_keys.get(foreign_key.referenced_table)
            if keys is None:
                raise FamilySchemaCompositionError(
                    f"foreign key references unknown parent {foreign_key.referenced_table!r}"
                )
            if foreign_key.referenced_columns not in keys:
                raise FamilySchemaCompositionError(
                    f"foreign key from {table.table_name!r} lacks an exact unique parent key"
                )
            parent_columns = column_specs[foreign_key.referenced_table]
            if any(
                column not in parent_columns
                for column in foreign_key.referenced_columns
            ):
                raise FamilySchemaCompositionError(
                    f"foreign key from {table.table_name!r} references an unknown parent column"
                )
            local_columns = column_specs[table.table_name]
            if any(
                local_columns[local].sql_type
                is not parent_columns[parent].sql_type
                for local, parent in zip(
                    foreign_key.columns,
                    foreign_key.referenced_columns,
                )
            ):
                raise FamilySchemaCompositionError(
                    f"foreign key from {table.table_name!r} has a source/parent type mismatch"
                )
        for value in (
            table.checks,
            table.indexes,
            table.triggers,
            table.verification_rules,
        ):
            _validate_relation_ast(
                value,
                allowed_tables=allowed_references,
                column_names=column_names,
                parent_keys=parent_keys,
            )
    if inherited_catalog != EXACT_V2_INHERITED_SCHEMA_CATALOG:
        raise FamilySchemaCompositionError(
            "exact-v2 inherited catalog differs from the frozen canonical catalog"
        )


def _validate_delegated_structural_sources(
    tables: tuple[AuthorityTableSpec, ...],
    components: tuple[SchemaComponentSpec, ...],
) -> None:
    extensions = {
        extension.extension_id: extension
        for component in components
        for extension in component.extensions
    }
    final_rules = {
        rule.rule_id: (table.table_name, rule)
        for table in tables
        for rule in table.verification_rules
    }
    final_codes = {
        rule.verifier_code: (table.table_name, rule)
        for table in tables
        for rule in table.verification_rules
        if rule.verifier_code is not None
    }
    for table in tables:
        for rule in table.verification_rules:
            source = rule.structural_source
            if (
                source is None
                or source.kind
                is not StructuralVerificationSourceKind.DELEGATED_EXTENSION
            ):
                continue
            extension = extensions.get(source.source_id)
            if extension is None or extension.target_table != table.table_name:
                raise FamilySchemaCompositionError(
                    f"delegated extension {source.source_id!r} does not resolve on "
                    f"{table.table_name!r}"
                )
            extension_rule_ids = {
                item.rule_id for item in extension.verification_rules
            }
            extension_codes = {
                item.verifier_code
                for item in extension.verification_rules
                if item.verifier_code is not None
            }
            for required_rule_id in source.required_rule_ids:
                dependency = final_rules.get(required_rule_id)
                if dependency is None or dependency[0] != table.table_name:
                    raise FamilySchemaCompositionError(
                        f"delegated rule {required_rule_id!r} does not resolve on "
                        f"{table.table_name!r}"
                    )
                if required_rule_id == rule.rule_id:
                    raise FamilySchemaCompositionError(
                        f"delegated rule {rule.rule_id!r} cannot depend on itself"
                    )
            for required_code in source.required_verifier_codes:
                dependency = final_codes.get(required_code)
                if dependency is None or dependency[0] != table.table_name:
                    raise FamilySchemaCompositionError(
                        f"delegated verifier {required_code!r} does not resolve on "
                        f"{table.table_name!r}"
                    )
                if required_code == rule.verifier_code:
                    raise FamilySchemaCompositionError(
                        f"delegated verifier {required_code!r} cannot depend on itself"
                    )
            if not (
                extension_rule_ids.intersection(source.required_rule_ids)
                or extension_codes.intersection(source.required_verifier_codes)
            ):
                raise FamilySchemaCompositionError(
                    f"delegated extension {source.source_id!r} has no owned proof dependency"
                )


def _validate_contract_surfaces(
    components: tuple[SchemaComponentSpec, ...],
) -> tuple[ResolvedContractSurfaceBinding, ...]:
    by_id = {component.component_id: component for component in components}
    provided_extensions = {
        extension.extension_id: extension
        for component in components
        for extension in component.extensions
    }
    surface_by_component_contract: dict[tuple[str, str], tuple[str, ...]] = {}
    for component in components:
        ordered_surfaces = (
            *((item.contract_type, item.field_name) for item in component.coverage),
            *(
                (item.contract_type, item.field_name)
                for item in component.contract_derivations
            ),
            *(
                (surface.contract_type, field_name)
                for graph in component.artifact_graph_derivations
                for surface in graph.contract_surfaces
                for field_name in surface.field_names
            ),
        )
        contract_types = tuple(dict.fromkeys(item[0] for item in ordered_surfaces))
        for contract_type in contract_types:
            surface_by_component_contract[(
                component.component_id,
                contract_type,
            )] = tuple(
                field_name
                for item_contract_type, field_name in ordered_surfaces
                if item_contract_type == contract_type
            )

    resolved: list[ResolvedContractSurfaceBinding] = []
    for consumer in components:
        for requirement in consumer.required_contract_surfaces:
            provider = by_id.get(requirement.provider_component_id)
            if provider is None or requirement.required_capability not in provider.capabilities:
                raise FamilySchemaCompositionError(
                    f"required contract surface for {requirement.contract_type!r} is unresolved"
                )
            provided_fields = surface_by_component_contract.get(
                (provider.component_id, requirement.contract_type),
                (),
            )
            source_extension_id: str | None = None
            if provided_fields:
                if set(requirement.field_names) != set(provided_fields):
                    raise FamilySchemaCompositionError(
                        f"required contract surface for {requirement.contract_type!r} "
                        "does not exactly match its provider"
                    )
                resolved.append(
                    ResolvedContractSurfaceBinding(
                        consumer_component_id=consumer.component_id,
                        provider_component_id=provider.component_id,
                        required_capability=requirement.required_capability,
                        contract_type=requirement.contract_type,
                        field_names=requirement.field_names,
                        source_extension_id=None,
                    )
                )
                continue
            matching_extensions = tuple(
                (item, provided_extensions[item.extension_id])
                for item in consumer.required_extensions
                if item.provider_component_id == provider.component_id
                and item.required_capability == requirement.required_capability
            )
            if len(matching_extensions) != 1:
                raise FamilySchemaCompositionError(
                    f"required contract surface for {requirement.contract_type!r} is unresolved"
                )
            extension_requirement, extension = matching_extensions[0]
            insertion = extension.column_insertion
            inserted_fields = (
                ()
                if insertion is None
                else tuple(column.name for column in insertion.columns)
            )
            if set(requirement.field_names) != set(inserted_fields):
                raise FamilySchemaCompositionError(
                    f"required contract surface for {requirement.contract_type!r} "
                    "does not exactly match its extension"
                )
            source_extension_id = extension_requirement.extension_id
            resolved.append(
                ResolvedContractSurfaceBinding(
                    consumer_component_id=consumer.component_id,
                    provider_component_id=provider.component_id,
                    required_capability=requirement.required_capability,
                    contract_type=requirement.contract_type,
                    field_names=requirement.field_names,
                    source_extension_id=source_extension_id,
                )
            )
    return tuple(resolved)


def _validate_traceability(
    tables: tuple[AuthorityTableSpec, ...],
    coverage: tuple[ContractFieldCoverageSpec, ...],
    contract_derivations: tuple[ContractFieldDerivationSpec, ...],
    artifact_graph_derivations: tuple[
        ContentAddressedArtifactGraphDerivationSpec,
        ...,
    ],
) -> None:
    table_columns = {
        table.table_name: {column.name for column in table.columns}
        for table in tables
    }
    for item in coverage:
        if item.kind in (CoverageKind.COLUMN, CoverageKind.CANONICAL_PAYLOAD):
            if (
                item.table_name not in table_columns
                or item.target_field not in table_columns[item.table_name]
            ):
                raise FamilySchemaCompositionError(
                    f"contract coverage target for {item.contract_type}.{item.field_name} "
                    "is absent from the final table"
                )

    derivation_ids = tuple(item.derivation_id for item in contract_derivations)
    graph_ids = tuple(item.derivation_id for item in artifact_graph_derivations)
    if len(set(derivation_ids)) != len(derivation_ids):
        raise FamilySchemaCompositionError("row derivation IDs must be globally unique")
    if len(set(graph_ids)) != len(graph_ids):
        raise FamilySchemaCompositionError(
            "artifact graph derivation IDs must be globally unique"
        )
    if set(derivation_ids).intersection(graph_ids):
        raise FamilySchemaCompositionError(
            "row and artifact graph derivation IDs must be globally disjoint"
        )
    derivation_keys = tuple(
        (item.contract_type, item.field_name) for item in contract_derivations
    )
    graph_keys = tuple(
        (surface.contract_type, field_name)
        for graph in artifact_graph_derivations
        for surface in graph.contract_surfaces
        for field_name in surface.field_names
    )
    if len(set(derivation_keys)) != len(derivation_keys):
        raise FamilySchemaCompositionError("row derivation fields must be globally unique")
    if len(set(graph_keys)) != len(graph_keys):
        raise FamilySchemaCompositionError(
            "artifact graph derivation fields must be globally unique"
        )
    if set(derivation_keys).intersection(graph_keys):
        raise FamilySchemaCompositionError(
            "row and artifact graph derivation fields must be globally disjoint"
        )

    for item in contract_derivations:
        required_columns = {
            item.binding_table: {item.binding_field},
            item.source_table: {
                item.source_key_field,
                item.source_value_field,
                *item.order_by_fields,
            },
        }
        if any(
            table_name not in table_columns
            or not columns <= table_columns[table_name]
            for table_name, columns in required_columns.items()
        ):
            raise FamilySchemaCompositionError(
                f"row derivation {item.derivation_id!r} references an absent final column"
            )
    for graph in artifact_graph_derivations:
        locator = graph.locator
        if locator.kind is ArtifactGraphLocatorSourceKind.CONTRACT_INPUT:
            continue
        if (
            locator.binding_table not in table_columns
            or locator.hash_field not in table_columns[locator.binding_table]
            or locator.path_field not in table_columns[locator.binding_table]
        ):
            raise FamilySchemaCompositionError(
                f"artifact graph {graph.derivation_id!r} has an absent final locator"
            )


def _compose_family_schema_components(
    components: tuple[SchemaComponentSpec, ...],
    inherited_catalog: ExactV2InheritedSchemaCatalog = (
        EXACT_V2_INHERITED_SCHEMA_CATALOG
    ),
) -> FamilySchemaComposition:
    """Compose a validated, unrendered candidate; exposed privately for REDs."""

    if type(components) is not tuple:
        raise TypeError("components must be an exact tuple")
    ordered_components = _topological_components(components)
    _validate_external_continuity_handler_inputs(ordered_components)
    extensions_by_target, applied_extensions = _resolve_extensions(
        ordered_components
    )

    tables: list[AuthorityTableSpec] = [FAMILY_AUTHORITY_GENESIS_TABLE_SPEC]
    for component in ordered_components:
        tables.extend(
            _apply_extensions(
                table,
                extensions_by_target.get(table.table_name, ()),
            )
            for table in component.tables
        )
    composed_tables = tuple(tables)
    _validate_global_catalog(composed_tables, inherited_catalog)
    _validate_delegated_structural_sources(composed_tables, ordered_components)
    resolved_contract_surfaces = _validate_contract_surfaces(ordered_components)

    surface_providers: dict[tuple[str, str], set[str]] = {}
    for component in ordered_components:
        for contract_type, field_name in (
            *((item.contract_type, item.field_name) for item in component.coverage),
            *(
                (item.contract_type, item.field_name)
                for item in component.contract_derivations
            ),
            *(
                (surface.contract_type, field_name)
                for graph in component.artifact_graph_derivations
                for surface in graph.contract_surfaces
                for field_name in surface.field_names
            ),
        ):
            surface_providers.setdefault((contract_type, field_name), set()).add(
                component.component_id
            )
    for binding in resolved_contract_surfaces:
        for field_name in binding.field_names:
            surface_providers.setdefault(
                (binding.contract_type, field_name),
                set(),
            ).add(binding.provider_component_id)
    conflicts = tuple(
        key for key, providers in surface_providers.items() if len(providers) != 1
    )
    if conflicts:
        raise FamilySchemaCompositionError(
            f"contract surface {conflicts[0]!r} has multiple effective providers"
        )

    continuity_rules = tuple(
        rule
        for component in ordered_components
        for extension in component.extensions
        for rule in extension.external_continuity_rules
    )
    continuity_codes = tuple(rule.verifier_code for rule in continuity_rules)
    if len(set(continuity_codes)) != len(continuity_codes):
        raise FamilySchemaCompositionError(
            "external continuity verifier codes must be globally unique"
        )
    ordinary_codes = {
        rule.verifier_code
        for table in composed_tables
        for rule in table.verification_rules
        if rule.verifier_code is not None
    }
    if ordinary_codes.intersection(continuity_codes):
        raise FamilySchemaCompositionError(
            "external continuity and ordinary verifier codes must be disjoint"
        )

    coverage = tuple(
        item for component in ordered_components for item in component.coverage
    )
    coverage_keys = tuple((item.contract_type, item.field_name) for item in coverage)
    if len(set(coverage_keys)) != len(coverage_keys):
        raise FamilySchemaCompositionError("contract coverage must be globally unique")

    contract_derivations = tuple(
        item
        for component in ordered_components
        for item in component.contract_derivations
    )
    artifact_graph_derivations = tuple(
        item
        for component in ordered_components
        for item in component.artifact_graph_derivations
    )
    _validate_publication_receipt_final_row_hash(composed_tables)
    _validate_traceability(
        composed_tables,
        coverage,
        contract_derivations,
        artifact_graph_derivations,
    )
    external_provider_requirements = tuple(
        item
        for component in ordered_components
        for item in component.required_external_provider_capabilities
    )
    provider_keys = tuple(
        (item.provider_contract_type, item.provider_capability)
        for item in external_provider_requirements
    )
    if len(set(provider_keys)) != len(provider_keys):
        raise FamilySchemaCompositionError(
            "external provider requirements must be globally unique"
        )
    declared_provider_keys = {
        (rule.provider_contract_type, rule.provider_capability)
        for table in composed_tables
        for rule in table.verification_rules
        if rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE
        and rule.provider_component_id is None
    }
    if declared_provider_keys != set(provider_keys):
        raise FamilySchemaCompositionError(
            "external provider requirements do not close every provider rule"
        )
    external_authority_claim_profiles = tuple(
        profile
        for component in ordered_components
        for profile in component.external_authority_claim_profiles
    )
    claim_profile_keys = tuple(
        (profile.provider_contract_type, profile.provider_capability)
        for profile in external_authority_claim_profiles
    )
    if len(set(claim_profile_keys)) != len(claim_profile_keys):
        raise FamilySchemaCompositionError(
            "external authority claim profiles must be globally unique"
        )
    if not set(claim_profile_keys).issubset(provider_keys):
        raise FamilySchemaCompositionError(
            "external authority claim profiles require declared provider capabilities"
        )
    auxiliary_semantic_recipes = tuple(
        recipe
        for component in ordered_components
        for recipe in component.auxiliary_semantic_recipes
    )
    recipe_ids = tuple(recipe.recipe_id for recipe in auxiliary_semantic_recipes)
    if len(set(recipe_ids)) != len(recipe_ids):
        raise FamilySchemaCompositionError(
            "auxiliary semantic recipe IDs must be globally unique"
        )
    known_recipe_tables = {
        table.table_name for table in composed_tables
    } | {
        table.table_name for table in inherited_catalog.tables
    }
    for recipe in auxiliary_semantic_recipes:
        unknown_tables = set(recipe.referenced_tables) - known_recipe_tables
        if unknown_tables:
            raise FamilySchemaCompositionError(
                "auxiliary semantic recipe references an unknown composed table"
            )
    implementation_evidence_requirements = _implementation_evidence_requirements(
        ordered_components,
        external_provider_requirements,
        FAMILY_SCHEMA_RENDERER_GRAMMAR,
    )

    return FamilySchemaComposition(
        schema_version=3,
        source_schema_version=inherited_catalog.schema_version,
        renderer_grammar=FAMILY_SCHEMA_RENDERER_GRAMMAR,
        implementation_evidence_requirements=implementation_evidence_requirements,
        component_order=tuple(
            component.component_id for component in ordered_components
        ),
        components=ordered_components,
        inherited_schema_catalog=inherited_catalog,
        tables=composed_tables,
        applied_extensions=applied_extensions,
        resolved_contract_surfaces=resolved_contract_surfaces,
        coverage=coverage,
        contract_derivations=contract_derivations,
        artifact_graph_derivations=artifact_graph_derivations,
        external_provider_requirements=external_provider_requirements,
        external_authority_claim_profiles=external_authority_claim_profiles,
        auxiliary_semantic_recipes=auxiliary_semantic_recipes,
    )


def compose_family_schema_v3() -> FamilySchemaComposition:
    """Return the one canonical, complete, still-unrendered v3 candidate."""

    return _compose_family_schema_components(_CANONICAL_COMPONENTS)


__all__ = [
    "AppliedExtensionBinding",
    "EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY",
    "EXACT_V2_INHERITED_SCHEMA_CATALOG",
    "FAMILY_SCHEMA_V3_COMPONENT_IDS",
    "ExactV2InheritedSchemaCatalog",
    "FamilySchemaComposition",
    "FamilySchemaCompositionError",
    "ResolvedContractSurfaceBinding",
    "auxiliary_semantic_recipe_binding_key",
    "compose_family_schema_v3",
    "external_claim_profile_binding_key",
]
