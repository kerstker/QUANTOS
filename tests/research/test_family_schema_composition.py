"""Pass-M RED contracts for the exact C4-C9 schema composition boundary."""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
import importlib
from typing import Any

import pytest

from services.research.experiment_registry.family_schema_c4 import (
    C4_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c5_c6 import (
    C5_SCHEMA_COMPONENT,
    C6_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c7 import (
    C7_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c8 import (
    C8_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c9 import (
    C9_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_spec import (
    Collation,
    ColumnValue,
    ComparisonExpression,
    ComparisonOperator,
    ComponentDependencySpec,
    ContractFieldCoverageSpec,
    CoverageKind,
    DependencyKind,
    LiteralValue,
    PrefixStreamExtensionExpression,
    RelatedValueLookupSpec,
    RelationAggregateExpression,
    RelationJoinEqualitySpec,
    RowSource,
    SortDirection,
    SqlType,
)


MODULE = "services.research.experiment_registry.family_schema_composition"
COMPONENT_ORDER = (
    "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1",
    "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1",
    "C7_ONE_SHOT_OOS_V1",
    "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
    "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1",
    "C6_EXTERNAL_AUTHORITY_REFS_V1",
)
TABLES = {
    "family_authority_genesis",
    "research_questions",
    "research_question_aliases",
    "strategy_lineage_roots",
    "strategy_lineage_nodes",
    "strategy_config_lineage_bindings",
    "research_authority_events",
    "selection_scopes",
    "selection_scope_aliases",
    "selection_scope_publishable_decisions",
    "selection_scope_outcomes",
    "research_programs",
    "selection_decisions",
    "global_oos_exposures",
    "global_oos_exposure_consumptions",
    "data_snooping_family_searches",
    "data_snooping_proposal_registrations",
    "data_snooping_proposals",
    "data_snooping_hypotheses",
    "data_snooping_search_emission_seals",
    "data_snooping_oos_releases",
    "data_snooping_oos_access_attempts",
    "data_snooping_candidate_dispositions",
    "data_snooping_evaluation_batches",
    "data_snooping_family_oos_evaluations",
    "data_snooping_evaluation_receipts",
    "data_snooping_benchmark_returns",
    "data_snooping_declarations",
    "data_snooping_family_freezes",
    "data_snooping_family_closures",
    "data_snooping_grid_assessments",
    "data_snooping_assessments",
    "artifact_publication_receipts",
}


def _module() -> Any:
    return importlib.import_module(MODULE)


def _components() -> tuple[Any, ...]:
    return (
        C4_SCHEMA_COMPONENT,
        C5_SCHEMA_COMPONENT,
        C6_SCHEMA_COMPONENT,
        C7_SCHEMA_COMPONENT,
        C8_SCHEMA_COMPONENT,
        C9_SCHEMA_COMPONENT,
    )


def _compose(
    components: tuple[Any, ...],
    inherited_catalog: Any | None = None,
) -> Any:
    compose = getattr(_module(), "_compose_family_schema_components")
    if inherited_catalog is None:
        return compose(components)
    return compose(components, inherited_catalog)


def _replace_component(
    components: tuple[Any, ...],
    replacement: Any,
) -> tuple[Any, ...]:
    return tuple(
        replacement if item.component_id == replacement.component_id else item
        for item in components
    )


def _rewrite_first_nested(
    value: Any,
    target_type: type[Any],
    transform: Any,
) -> tuple[Any, bool]:
    if type(value) is target_type:
        rewritten = transform(value)
        if rewritten is not value:
            return rewritten, True
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            current = getattr(value, field.name)
            rewritten, changed = _rewrite_first_nested(
                current,
                target_type,
                transform,
            )
            if changed:
                return replace(value, **{field.name: rewritten}), True
        return value, False
    if type(value) is tuple:
        for index, current in enumerate(value):
            rewritten, changed = _rewrite_first_nested(
                current,
                target_type,
                transform,
            )
            if changed:
                return (*value[:index], rewritten, *value[index + 1 :]), True
    return value, False


def test_canonical_composition_is_exact_complete_and_non_executable() -> None:
    composition = getattr(_module(), "compose_family_schema_v3")()
    assert composition.schema_version == 3
    assert composition.source_schema_version == 2
    assert composition.component_order == COMPONENT_ORDER
    assert composition.inherited_schema_catalog.catalog_id == (
        "EXACT_V2_FAMILY_SCHEMA_INHERITED_CATALOG_V1"
    )
    assert composition.inherited_schema_catalog.schema_version == 2
    inherited_keys = {
        parent.table_name: parent.unique_parent_keys
        for parent in composition.inherited_schema_catalog.tables
        if parent.table_name in {"evaluations", "evaluation_audit_outbox"}
    }
    assert ("run_id",) in inherited_keys["evaluations"]
    assert ("record_hash",) in inherited_keys["evaluation_audit_outbox"]
    assert tuple(table.table_name for table in composition.tables)[0] == (
        "family_authority_genesis"
    )
    assert {table.table_name for table in composition.tables} == TABLES
    assert len(composition.tables) == 33
    assert len(composition.applied_extension_ids) == 18
    assert len(set(composition.applied_extension_ids)) == 18
    assert len(composition.coverage) == 619
    assert len(composition.contract_derivations) == 6
    assert len(composition.artifact_graph_derivations) == 2
    assert len(composition.external_provider_requirements) == 6
    assert len(composition.external_authority_claim_profiles) == 2
    assert len(composition.auxiliary_semantic_recipes) == 29
    assert {
        recipe.recipe_kind.value
        for recipe in composition.auxiliary_semantic_recipes
    } == {
        "ACTION_POLICY",
        "IDENTITY_DERIVATION",
        "CANONICAL_CONTENT_HASH",
    }
    assert len(
        {recipe.recipe_id for recipe in composition.auxiliary_semantic_recipes}
    ) == len(composition.auxiliary_semantic_recipes)
    assert {
        (
            profile.provider_contract_type,
            profile.provider_capability,
            profile.external_reference_kind.value,
        )
        for profile in composition.external_authority_claim_profiles
    } == {
        (
            "MultiPartyGrantEvidence",
            "multi_party_grant_evidence.verify",
            "MULTI_PARTY_GRANT",
        ),
        (
            "ActorProvenanceEvidence",
            "actor_provenance_evidence.verify",
            "ACTOR_PROVENANCE",
        ),
    }
    assert all(
        profile.fail_closed and not profile.default_pass
        for profile in composition.external_authority_claim_profiles
    )
    assert sum(len(table.verification_rules) for table in composition.tables) == 341
    assert not hasattr(composition, "ddl")
    assert not hasattr(composition, "manifest_hash")


def test_composed_publication_has_one_final_67_field_row_hash_rule() -> None:
    composition = getattr(_module(), "compose_family_schema_v3")()
    table = next(
        item
        for item in composition.tables
        if item.table_name == "artifact_publication_receipts"
    )
    row_hash_rules = tuple(
        rule
        for rule in table.verification_rules
        if rule.kind.value == "ROW_HASH"
        and rule.rule_id == "publication_receipt_row_hash"
    )
    assert len(row_hash_rules) == 1
    assert tuple(row_hash_rules[0].columns) == tuple(table.row_hash.canonical_fields)
    assert len(table.row_hash.canonical_fields) == 67

    c6 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c5_c6"
    )
    c9 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c9"
    )
    assert c6._C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS == (
        c9.C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS
    )


def test_composition_rejects_drift_in_the_final_publication_row_hash_rule() -> None:
    extension = next(
        item
        for item in C6_SCHEMA_COMPONENT.extensions
        if item.target_table == "artifact_publication_receipts"
    )
    row_hash_rule = next(
        rule
        for rule in extension.verification_rules
        if rule.rule_id == "publication_receipt_row_hash"
    )
    weakened_rule = replace(
        row_hash_rule,
        columns=row_hash_rule.columns[:-1],
    )
    weakened_extension = replace(
        extension,
        verification_rules=tuple(
            weakened_rule if rule is row_hash_rule else rule
            for rule in extension.verification_rules
        ),
    )
    weakened_c6 = replace(
        C6_SCHEMA_COMPONENT,
        extensions=tuple(
            weakened_extension if item is extension else item
            for item in C6_SCHEMA_COMPONENT.extensions
        ),
    )
    with pytest.raises(ValueError, match="exact composed fields"):
        _compose(_replace_component(_components(), weakened_c6))


def test_composition_is_deterministic_for_any_input_order() -> None:
    canonical = getattr(_module(), "compose_family_schema_v3")()
    assert _compose(tuple(reversed(_components()))) == canonical


@pytest.mark.parametrize(
    "components",
    (
        _components()[:-1],
        (*_components(), C9_SCHEMA_COMPONENT),
        (
            *_components()[:-1],
            replace(C9_SCHEMA_COMPONENT, component_id="UNKNOWN_COMPONENT_V1"),
        ),
    ),
)
def test_composition_rejects_missing_duplicate_or_unknown_components(
    components: tuple[Any, ...],
) -> None:
    with pytest.raises(ValueError, match="component"):
        _compose(components)


def test_composition_rejects_dependency_cycles_and_missing_capabilities() -> None:
    c4_cycle = replace(
        C4_SCHEMA_COMPONENT,
        dependencies=(
            ComponentDependencySpec(
                component_id=C6_SCHEMA_COMPONENT.component_id,
                kind=DependencyKind.CROSS_TABLE_REFERENCE,
                required_capabilities=(C6_SCHEMA_COMPONENT.capabilities[0],),
            ),
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        _compose(_replace_component(_components(), c4_cycle))

    original = C5_SCHEMA_COMPONENT.dependencies[0]
    missing_capability = replace(
        C5_SCHEMA_COMPONENT,
        dependencies=(
            replace(original, required_capabilities=("missing.capability",)),
        ),
    )
    with pytest.raises(ValueError, match="capability"):
        _compose(_replace_component(_components(), missing_capability))

    duplicate_capability_owner = replace(
        C5_SCHEMA_COMPONENT,
        capabilities=(
            *C5_SCHEMA_COMPONENT.capabilities,
            C4_SCHEMA_COMPONENT.capabilities[0],
        ),
    )
    with pytest.raises(ValueError, match="capability owners"):
        _compose(_replace_component(_components(), duplicate_capability_owner))


def test_composition_rejects_missing_duplicate_or_unrequired_extensions() -> None:
    missing = replace(
        C6_SCHEMA_COMPONENT,
        extensions=C6_SCHEMA_COMPONENT.extensions[:-1],
    )
    with pytest.raises(ValueError, match="extension"):
        _compose(_replace_component(_components(), missing))

    duplicate = replace(
        C7_SCHEMA_COMPONENT,
        extensions=(
            *C7_SCHEMA_COMPONENT.extensions,
            C8_SCHEMA_COMPONENT.extensions[2],
        ),
    )
    with pytest.raises(ValueError, match="extension"):
        _compose(_replace_component(_components(), duplicate))

    first = C6_SCHEMA_COMPONENT.extensions[0]
    unrequired = replace(
        C6_SCHEMA_COMPONENT,
        extensions=(
            replace(first, extension_id="UNREQUIRED_EXTENSION_V1"),
            *C6_SCHEMA_COMPONENT.extensions[1:],
        ),
    )
    with pytest.raises(ValueError, match="extension"):
        _compose(_replace_component(_components(), unrequired))


def test_composition_rejects_unresolved_extension_anchors_and_object_collisions() -> None:
    first = C6_SCHEMA_COMPONENT.extensions[0]
    broken_insertion = replace(
        first.column_insertion,
        anchor_column="missing_anchor",
    )
    broken_hash_insertion = replace(
        first.row_hash_field_insertion,
        anchor_field="missing_anchor",
    )
    broken_extension = replace(
        first,
        column_insertion=broken_insertion,
        row_hash_field_insertion=broken_hash_insertion,
    )
    broken_c6 = replace(
        C6_SCHEMA_COMPONENT,
        extensions=(broken_extension, *C6_SCHEMA_COMPONENT.extensions[1:]),
    )
    with pytest.raises(ValueError, match="anchor"):
        _compose(_replace_component(_components(), broken_c6))

    source_tables = C4_SCHEMA_COMPONENT.tables
    first_indexed = next(
        table for table in source_tables if table.table_name == "research_questions"
    )
    second_indexed = next(
        table
        for table in source_tables
        if table.table_name == "strategy_config_lineage_bindings"
    )
    duplicate_index = replace(
        second_indexed.indexes[0],
        name=first_indexed.indexes[0].name,
    )
    changed_second = replace(
        second_indexed,
        indexes=(duplicate_index, *second_indexed.indexes[1:]),
    )
    changed_c4 = replace(
        C4_SCHEMA_COMPONENT,
        tables=tuple(
            changed_second if table is second_indexed else table
            for table in source_tables
        ),
    )
    with pytest.raises(ValueError, match="object name"):
        _compose(_replace_component(_components(), changed_c4))

    exact_v2_collision = replace(
        first_indexed,
        indexes=(
            replace(first_indexed.indexes[0], name="search_candidates_by_search"),
            *first_indexed.indexes[1:],
        ),
    )
    collided_c4 = replace(
        C4_SCHEMA_COMPONENT,
        tables=tuple(
            exact_v2_collision if table is first_indexed else table
            for table in source_tables
        ),
    )
    with pytest.raises(ValueError, match="collides with exact-v2"):
        _compose(_replace_component(_components(), collided_c4))


def test_composition_resolves_delegated_structural_sources_to_the_exact_extension_rules() -> None:
    exposure_table = next(
        table
        for table in C4_SCHEMA_COMPONENT.tables
        if table.table_name == "global_oos_exposures"
    )
    delegated_rule = next(
        rule
        for rule in exposure_table.verification_rules
        if rule.rule_id == "global_oos_exposures_require_c6_embedded_refs"
    )
    assert delegated_rule.structural_source is not None

    def compose_with_source(source: Any) -> None:
        changed_rule = replace(delegated_rule, structural_source=source)
        changed_table = replace(
            exposure_table,
            verification_rules=tuple(
                changed_rule if rule is delegated_rule else rule
                for rule in exposure_table.verification_rules
            ),
        )
        changed_c4 = replace(
            C4_SCHEMA_COMPONENT,
            tables=tuple(
                changed_table if table is exposure_table else table
                for table in C4_SCHEMA_COMPONENT.tables
            ),
        )
        _compose(_replace_component(_components(), changed_c4))

    with pytest.raises(ValueError, match="delegated extension"):
        compose_with_source(
            replace(
                delegated_rule.structural_source,
                source_id="C6_MISSING_EXTENSION_V1",
            )
        )
    with pytest.raises(ValueError, match="delegated verifier"):
        compose_with_source(
            replace(
                delegated_rule.structural_source,
                required_verifier_codes=("VERIFY_MISSING_C6_BINDING",),
            )
        )


def test_composition_resolves_remote_trigger_sources_on_the_exact_owned_table() -> None:
    question_table = next(
        table
        for table in C4_SCHEMA_COMPONENT.tables
        if table.table_name == "research_questions"
    )
    hash_rule = next(
        rule
        for rule in question_table.verification_rules
        if rule.rule_id == "research_questions_canonical_question_hash"
    )
    assert hash_rule.structural_source is not None
    changed_rule = replace(
        hash_rule,
        structural_source=replace(
            hash_rule.structural_source,
            source_table="missing_remote_authority",
        ),
    )
    changed_table = replace(
        question_table,
        verification_rules=tuple(
            changed_rule if rule is hash_rule else rule
            for rule in question_table.verification_rules
        ),
    )
    changed_c4 = replace(
        C4_SCHEMA_COMPONENT,
        tables=tuple(
            changed_table if table is question_table else table
            for table in C4_SCHEMA_COMPONENT.tables
        ),
    )
    with pytest.raises(ValueError, match="remote structural trigger"):
        _compose(_replace_component(_components(), changed_c4))


def test_composition_rejects_foreign_keys_without_an_exact_unique_parent_key() -> None:
    assessment = next(
        table
        for table in C8_SCHEMA_COMPONENT.tables
        if table.table_name == "data_snooping_assessments"
    )
    weakened = replace(
        assessment,
        indexes=tuple(
            index
            for index in assessment.indexes
            if index.name != "unique_data_snooping_assessments_lineage"
        ),
    )
    weakened_c8 = replace(
        C8_SCHEMA_COMPONENT,
        tables=tuple(
            weakened if table is assessment else table
            for table in C8_SCHEMA_COMPONENT.tables
        ),
    )
    with pytest.raises(ValueError, match="unique parent key"):
        _compose(_replace_component(_components(), weakened_c8))

    lineage = next(
        index
        for index in assessment.indexes
        if index.name == "unique_data_snooping_assessments_lineage"
    )
    partial_predicate = ComparisonExpression(
        ColumnValue("authority_instance_id", RowSource.CURRENT),
        ComparisonOperator.EQ,
        LiteralValue("authority"),
    )
    for invalid_lineage in (
        replace(lineage, unique=False),
        replace(lineage, predicate=partial_predicate),
        replace(
            lineage,
            columns=(
                replace(lineage.columns[0], collation=Collation.NOCASE),
                *lineage.columns[1:],
            ),
        ),
    ):
        invalid_assessment = replace(
            assessment,
            indexes=tuple(
                invalid_lineage if index is lineage else index
                for index in assessment.indexes
            ),
        )
        invalid_c8 = replace(
            C8_SCHEMA_COMPONENT,
            tables=tuple(
                invalid_assessment if table is assessment else table
                for table in C8_SCHEMA_COMPONENT.tables
            ),
        )
        with pytest.raises(ValueError, match="unique parent key"):
            _compose(_replace_component(_components(), invalid_c8))


def test_composition_binds_foreign_keys_to_the_typed_exact_v2_catalog() -> None:
    catalog = getattr(_module(), "EXACT_V2_INHERITED_SCHEMA_CATALOG")
    outbox = next(
        table
        for table in catalog.tables
        if table.table_name == "evaluation_audit_outbox"
    )
    weakened_outbox = replace(
        outbox,
        unique_keys=tuple(
            key for key in outbox.unique_keys if key.columns != ("record_hash",)
        ),
    )
    weakened_catalog = replace(
        catalog,
        tables=tuple(
            weakened_outbox if table is outbox else table for table in catalog.tables
        ),
    )
    with pytest.raises(ValueError, match="unique parent key"):
        _compose(_components(), weakened_catalog)

    evaluations = next(
        table for table in catalog.tables if table.table_name == "evaluations"
    )
    mistyped_evaluations = replace(
        evaluations,
        columns=tuple(
            replace(column, sql_type=SqlType.INTEGER)
            if column.name == "run_id"
            else column
            for column in evaluations.columns
        ),
    )
    mistyped_catalog = replace(
        catalog,
        tables=tuple(
            mistyped_evaluations if table is evaluations else table
            for table in catalog.tables
        ),
    )
    with pytest.raises(ValueError, match="source/parent type mismatch"):
        _compose(_components(), mistyped_catalog)

    run_key = next(
        key for key in evaluations.unique_keys if key.columns == ("run_id",)
    )
    for invalid_key in (
        replace(run_key, directions=(SortDirection.DESC,)),
        replace(run_key, collations=(Collation.NOCASE,)),
    ):
        invalid_evaluations = replace(
            evaluations,
            unique_keys=tuple(
                invalid_key if key is run_key else key
                for key in evaluations.unique_keys
            ),
        )
        invalid_catalog = replace(
            catalog,
            tables=tuple(
                invalid_evaluations if table is evaluations else table
                for table in catalog.tables
            ),
        )
        with pytest.raises(ValueError, match="unique parent key"):
            _compose(_components(), invalid_catalog)


def test_composition_rejects_forged_exact_v2_catalog_extensions() -> None:
    catalog = getattr(_module(), "EXACT_V2_INHERITED_SCHEMA_CATALOG")
    evaluations = next(
        table for table in catalog.tables if table.table_name == "evaluations"
    )
    forged_evaluations = replace(
        evaluations,
        unique_keys=(
            *evaluations.unique_keys,
            replace(evaluations.unique_keys[0], columns=("content_hash",)),
        ),
    )
    forged_catalog = replace(
        catalog,
        tables=tuple(
            forged_evaluations if table is evaluations else table
            for table in catalog.tables
        ),
    )

    evaluation_table = next(
        table
        for table in C7_SCHEMA_COMPONENT.tables
        if table.table_name == "data_snooping_family_oos_evaluations"
    )
    evaluation_fk = next(
        foreign_key
        for foreign_key in evaluation_table.foreign_keys
        if foreign_key.referenced_table == "evaluations"
    )
    forged_fk = replace(evaluation_fk, referenced_columns=("content_hash",))
    evaluation_rule = next(
        rule
        for rule in evaluation_table.verification_rules
        if rule.rule_id == "family_oos_evaluation_existing_evaluation"
    )
    assert evaluation_rule.structural_source is not None
    forged_rule = replace(
        evaluation_rule,
        structural_source=replace(
            evaluation_rule.structural_source,
            referenced_columns=("content_hash",),
        ),
    )
    forged_table = replace(
        evaluation_table,
        foreign_keys=tuple(
            forged_fk if foreign_key is evaluation_fk else foreign_key
            for foreign_key in evaluation_table.foreign_keys
        ),
        verification_rules=tuple(
            forged_rule if rule is evaluation_rule else rule
            for rule in evaluation_table.verification_rules
        ),
    )
    forged_c7 = replace(
        C7_SCHEMA_COMPONENT,
        tables=tuple(
            forged_table if table is evaluation_table else table
            for table in C7_SCHEMA_COMPONENT.tables
        ),
    )
    with pytest.raises(ValueError, match="exact-v2 inherited catalog"):
        _compose(_replace_component(_components(), forged_c7), forged_catalog)

    missing_namespace = replace(catalog, objects=())
    with pytest.raises(ValueError, match="exact-v2 inherited catalog"):
        _compose(_components(), missing_namespace)


def test_composition_rejects_unknown_relation_tables_and_columns() -> None:
    evaluation_table = next(
        table
        for table in C7_SCHEMA_COMPONENT.tables
        if table.table_name == "data_snooping_family_oos_evaluations"
    )
    guard = next(
        trigger
        for trigger in evaluation_table.triggers
        if trigger.error_code == "FAMILY_OOS_NOT_SEARCH_EVALUATION"
    )
    relation = guard.violation_when
    assert relation is not None

    for invalid_relation, message in (
        (replace(relation, table="unknown_relation_table"), "unknown table"),
        (
            replace(
                relation,
                bindings=(
                    replace(relation.bindings[0], related_column="missing_column"),
                ),
            ),
            "unknown column",
        ),
    ):
        invalid_guard = replace(guard, violation_when=invalid_relation)
        invalid_table = replace(
            evaluation_table,
            triggers=tuple(
                invalid_guard if trigger is guard else trigger
                for trigger in evaluation_table.triggers
            ),
        )
        invalid_c7 = replace(
            C7_SCHEMA_COMPONENT,
            tables=tuple(
                invalid_table if table is evaluation_table else table
                for table in C7_SCHEMA_COMPONENT.tables
            ),
        )
        with pytest.raises(ValueError, match=message):
            _compose(_replace_component(_components(), invalid_c7))


@pytest.mark.parametrize(
    ("target_type", "transform"),
    (
        (
            RelatedValueLookupSpec,
            lambda node: replace(node, value="missing_remote_value"),
        ),
        (
            RelationAggregateExpression,
            lambda node: (
                node
                if node.value is None
                else replace(node, value="missing_aggregate_value")
            ),
        ),
        (
            RelationJoinEqualitySpec,
            lambda node: replace(node, right_column="missing_remote_column"),
        ),
        (
            PrefixStreamExtensionExpression,
            lambda node: replace(
                node,
                previous_prefix_hash_column="missing_prefix",
            ),
        ),
    ),
)
def test_composition_rejects_every_remote_relation_column_escape(
    target_type: type[Any],
    transform: Any,
) -> None:
    rewritten, changed = _rewrite_first_nested(
        _components(),
        target_type,
        transform,
    )
    assert changed
    with pytest.raises(ValueError, match="unknown column"):
        _compose(rewritten)


def test_composition_rejects_missing_final_coverage_targets() -> None:
    covered = next(
        item for item in C6_SCHEMA_COMPONENT.coverage if item.kind is CoverageKind.COLUMN
    )
    invalid_coverage = replace(covered, target_field="missing_column")
    invalid_c6 = replace(
        C6_SCHEMA_COMPONENT,
        coverage=tuple(
            invalid_coverage if item is covered else item
            for item in C6_SCHEMA_COMPONENT.coverage
        ),
    )
    with pytest.raises(ValueError, match="coverage target"):
        _compose(_replace_component(_components(), invalid_c6))


def test_composition_rejects_global_row_and_graph_derivation_id_collisions() -> None:
    c4_id = C4_SCHEMA_COMPONENT.contract_derivations[0].derivation_id
    c5_derivation = C5_SCHEMA_COMPONENT.contract_derivations[0]
    duplicate_row = replace(c5_derivation, derivation_id=c4_id)
    duplicate_row_c5 = replace(
        C5_SCHEMA_COMPONENT,
        contract_derivations=(duplicate_row,),
        coverage=tuple(
            replace(item, derivation_id=c4_id)
            if item.derivation_id == c5_derivation.derivation_id
            else item
            for item in C5_SCHEMA_COMPONENT.coverage
        ),
    )
    with pytest.raises(ValueError, match="row derivation IDs"):
        _compose(_replace_component(_components(), duplicate_row_c5))

    graph = C9_SCHEMA_COMPONENT.artifact_graph_derivations[0]
    colliding_graph = replace(graph, derivation_id=c4_id)
    colliding_graph_c9 = replace(
        C9_SCHEMA_COMPONENT,
        artifact_graph_derivations=(
            colliding_graph,
            *C9_SCHEMA_COMPONENT.artifact_graph_derivations[1:],
        ),
        coverage=tuple(
            replace(item, derivation_id=c4_id)
            if item.derivation_id == graph.derivation_id
            else item
            for item in C9_SCHEMA_COMPONENT.coverage
        ),
    )
    with pytest.raises(ValueError, match="globally disjoint"):
        _compose(_replace_component(_components(), colliding_graph_c9))


def test_composition_preserves_exact_extension_continuity_provenance() -> None:
    composition = getattr(_module(), "compose_family_schema_v3")()
    expected = tuple(
        (
            extension.extension_id,
            component.component_id,
            extension.target_table,
            extension.external_continuity_rules,
        )
        for component in composition.components
        for extension in component.extensions
    )
    actual = tuple(
        (
            binding.extension_id,
            binding.provider_component_id,
            binding.target_table,
            binding.external_continuity_rules,
        )
        for binding in composition.applied_extensions
    )
    assert actual == expected
    assert len(actual) == 18
    assert sum(len(item[3]) for item in actual) == 9
    assert len(composition.resolved_contract_surfaces) == 6
    assert all(
        binding.consumer_component_id
        and binding.provider_component_id
        and binding.required_capability
        and binding.field_names
        for binding in composition.resolved_contract_surfaces
    )


def test_composition_rejects_duplicate_effective_contract_providers() -> None:
    required = C9_SCHEMA_COMPONENT.required_contract_surfaces[0]
    duplicate = ContractFieldCoverageSpec(
        contract_type=required.contract_type,
        field_name=required.field_names[0],
        kind=CoverageKind.COLUMN,
        table_name="research_questions",
        target_field="research_question_id",
    )
    invalid_c4 = replace(
        C4_SCHEMA_COMPONENT,
        coverage=(*C4_SCHEMA_COMPONENT.coverage, duplicate),
    )
    with pytest.raises(ValueError, match="multiple effective providers"):
        _compose(_replace_component(_components(), invalid_c4))


def test_versioned_extension_cannot_drop_frozen_continuity() -> None:
    extension = next(
        item
        for item in C6_SCHEMA_COMPONENT.extensions
        if item.external_continuity_rules
    )
    weakened = replace(extension, external_continuity_rules=())
    weakened_c6 = replace(
        C6_SCHEMA_COMPONENT,
        extensions=tuple(
            weakened if item is extension else item
            for item in C6_SCHEMA_COMPONENT.extensions
        ),
    )
    with pytest.raises(ValueError, match="continuity"):
        _compose(_replace_component(_components(), weakened_c6))


def test_composition_carries_embedded_continuity_profiles_to_final_candidate() -> None:
    composition = _compose(_components())
    profiles = {
        rule.verifier_code: rule.semantic_profile
        for extension in composition.applied_extensions
        for rule in extension.external_continuity_rules
        if rule.semantic_profile is not None
    }
    assert set(profiles) == {
        "VERIFY_CLOSURE_START_TERMINAL_CONTINUITY",
        "VERIFY_CLOSURE_START_LEASE_CONTINUITY",
        "VERIFY_PUBLICATION_START_TERMINAL_CONTINUITY",
        "VERIFY_PUBLICATION_START_LEASE_CONTINUITY",
    }
    assert tuple(
        branch.selector.attempt_status_path
        for branch in profiles[
            "VERIFY_PUBLICATION_START_LEASE_CONTINUITY"
        ].chronology_branches
    ) == ("attempt.status", "attempt.status")


def test_composition_rejects_removed_embedded_continuity_profile() -> None:
    extension = next(
        item
        for item in C6_SCHEMA_COMPONENT.extensions
        if any(rule.semantic_profile is not None for rule in item.external_continuity_rules)
    )
    continuity = next(
        rule
        for rule in extension.external_continuity_rules
        if rule.semantic_profile is not None
    )
    weakened_rule = replace(continuity, semantic_profile=None)
    weakened_extension = replace(
        extension,
        external_continuity_rules=tuple(
            weakened_rule if rule is continuity else rule
            for rule in extension.external_continuity_rules
        ),
    )
    weakened_c6 = replace(
        C6_SCHEMA_COMPONENT,
        extensions=tuple(
            weakened_extension if item is extension else item
            for item in C6_SCHEMA_COMPONENT.extensions
        ),
    )
    with pytest.raises(ValueError, match="continuity"):
        _compose(_replace_component(_components(), weakened_c6))


@pytest.mark.parametrize(
    "capability",
    (
        "c7.oos_attempt.event_stream",
        "c7.family_evaluation_batch.atomic",
    ),
)
def test_composition_rejects_missing_direct_continuity_row_owner_capability(
    capability: str,
) -> None:
    dependency = next(
        item
        for item in C6_SCHEMA_COMPONENT.dependencies
        if item.component_id == "C7_ONE_SHOT_OOS_V1"
    )
    weakened_dependency = replace(
        dependency,
        required_capabilities=tuple(
            item for item in dependency.required_capabilities if item != capability
        ),
    )
    weakened_c6 = replace(
        C6_SCHEMA_COMPONENT,
        dependencies=tuple(
            weakened_dependency if item is dependency else item
            for item in C6_SCHEMA_COMPONENT.dependencies
        ),
    )
    with pytest.raises(ValueError, match=capability):
        _compose(_replace_component(_components(), weakened_c6))


def test_composition_rejects_mixed_table_continuity_rule_dependency() -> None:
    extension = next(
        item
        for item in C6_SCHEMA_COMPONENT.extensions
        if item.target_table == "data_snooping_family_closures"
    )
    continuity = next(
        rule
        for rule in extension.external_continuity_rules
        if rule.verifier_code == "VERIFY_CLOSURE_START_LEASE_CONTINUITY"
    )
    profile = continuity.semantic_profile
    assert profile is not None
    complete = profile.chronology_branches[0]
    attempt = next(row for row in complete.row_inputs if row.alias == "attempt")
    mixed_attempt = replace(
        attempt,
        required_verification_rule_ids=("family_closure_row_hash",),
    )
    mixed_complete = replace(
        complete,
        row_inputs=tuple(
            mixed_attempt if row is attempt else row for row in complete.row_inputs
        ),
    )
    mixed_profile = replace(
        profile,
        chronology_branches=(mixed_complete, profile.chronology_branches[1]),
    )
    mixed_rule = replace(continuity, semantic_profile=mixed_profile)
    mixed_extension = replace(
        extension,
        external_continuity_rules=tuple(
            mixed_rule if rule is continuity else rule
            for rule in extension.external_continuity_rules
        ),
    )
    mixed_c6 = replace(
        C6_SCHEMA_COMPONENT,
        extensions=tuple(
            mixed_extension if item is extension else item
            for item in C6_SCHEMA_COMPONENT.extensions
        ),
    )
    with pytest.raises(ValueError, match="different table"):
        _compose(_replace_component(_components(), mixed_c6))


def test_composition_rejects_unresolved_required_contract_surfaces() -> None:
    required_surface = C9_SCHEMA_COMPONENT.required_contract_surfaces[0]
    weakened_surface = replace(
        required_surface,
        field_names=(*required_surface.field_names, "missing_surface"),
    )
    weakened_c9 = replace(
        C9_SCHEMA_COMPONENT,
        required_contract_surfaces=(weakened_surface,),
    )
    with pytest.raises(ValueError, match="contract surface"):
        _compose(_replace_component(_components(), weakened_c9))

    canonical_surface = C9_SCHEMA_COMPONENT.required_contract_surfaces[0]
    silently_weakened = replace(
        canonical_surface,
        field_names=canonical_surface.field_names[1:],
    )
    silently_weakened_c9 = replace(
        C9_SCHEMA_COMPONENT,
        required_contract_surfaces=(silently_weakened,),
    )
    with pytest.raises(ValueError, match="contract surface"):
        _compose(_replace_component(_components(), silently_weakened_c9))
