"""Private Pass-C schema AST for declaration, freeze, closure, grid evidence, and conclusion.

This module contains immutable schema declarations only.  It neither renders
SQL nor opens an authority store.  Composition, executable verification, and
schema sealing remain Pass-M responsibilities.
"""

from __future__ import annotations

from quantos.contracts import research as _research_contracts

from .family_schema_spec import (
    AuthorityTableExtensionSpec,
    AuthorityTableSpec,
    ChainGenesisRule,
    ChainHeadRule,
    ChainPredecessorExpression,
    ChainSpec,
    CanonicalEncoding,
    CanonicalJsonArrayProjection,
    CanonicalJsonFieldValue,
    CanonicalJsonPayloadHashExpression,
    CanonicalJsonScalarType,
    CheckConstraintSpec,
    Collation,
    ColumnSpec,
    ColumnValue,
    ComparisonExpression,
    ComparisonOperator,
    ComponentDependencySpec,
    ContractDerivationKind,
    ContractFieldCoverageSpec,
    ContractFieldDerivationSpec,
    CoverageKind,
    DependencyKind,
    ForeignKeyAction,
    ForeignKeyDeferrability,
    ForeignKeySpec,
    IndexColumnSpec,
    IndexSpec,
    InsertionPosition,
    LiteralValue,
    LogicalExpression,
    LogicalOperator,
    LookupBinding,
    MembershipExpression,
    NullExpression,
    NullOperator,
    OrderedColumnInsertionSpec,
    OrderedFieldInsertionSpec,
    PredicateExpression,
    PrefixStreamExtensionExpression,
    QualifiedColumnValue,
    RelatedValueLookupSpec,
    RelatedCanonicalJsonArrayContainsExpression,
    RelatedCanonicalJsonArrayLengthValueSpec,
    RelatedCanonicalJsonArrayValueSpec,
    RelationAggregateExpression,
    RelationAggregateKind,
    RelationAggregateOrderSpec,
    RelationExistsExpression,
    RelationArgExtremumExpression,
    RelationChainPrefixExpression,
    RelationChainPrefixProof,
    RelationExtremum,
    RelationJoinAggregateExpression,
    RelationJoinAggregateOrderSpec,
    RelationJoinEqualitySpec,
    RelationJoinSpec,
    RelationLatestPerDomainCardinality,
    RelationLatestPerDomainPolicyExpression,
    RelationMetricSpec,
    RequiredContractSurfaceSpec,
    RequiredTableExtensionSpec,
    RowHashSpec,
    RowHashAlgorithm,
    RowSource,
    SchemaComponentSpec,
    SortDirection,
    SqlType,
    StructuralVerificationSourceKind,
    StructuralVerificationSourceSpec,
    TriggerEvent,
    TriggerPolicyKind,
    TriggerPolicySpec,
    VerificationRuleKind,
    VerificationRuleSpec,
)


_COMPONENT_ID = "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1"
_C4_COMPONENT = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
_C5_COMPONENT = "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1"
_C6_COMPONENT = "C6_EXTERNAL_AUTHORITY_REFS_V1"
_C7_COMPONENT = "C7_ONE_SHOT_OOS_V1"
_ASSESSMENT_ALGORITHM_VERSION = "WHITE_RC_HANSEN_SPA_FAMILY_V1"

_CAPABILITIES = (
    "c8.declaration_closure.core",
    "c8.declaration.core",
    "c8.family_freeze.core",
    "c8.family_closure.core",
    "c8.grid_assessment.core",
    "c8.assessment.family_conclusion",
    "c8.family_search.declaration_and_late_guards",
    "c8.proposal.declaration_dlp_and_late_guards",
    "c8.proposal_terminal_result.late_guards",
    "c8.hypothesis.late_guards",
    "c8.emission_seal.late_guards",
    "c8.data_snooping_oos_releases.late_insert_guards",
    "c8.data_snooping_oos_access_attempts.late_insert_guards",
    "c8.data_snooping_candidate_dispositions.late_insert_guards",
    "c8.data_snooping_evaluation_batches.late_insert_guards",
    "c8.data_snooping_family_oos_evaluations.late_insert_guards",
    "c8.data_snooping_evaluation_receipts.late_insert_guards",
    "c8.data_snooping_benchmark_returns.late_insert_guards",
)

_TABLE_ORDER = (
    "data_snooping_declarations",
    "data_snooping_family_freezes",
    "data_snooping_family_closures",
    "data_snooping_grid_assessments",
    "data_snooping_assessments",
)

_RECORD_TYPES = {
    "data_snooping_declarations": "DATA_SNOOPING_DECLARATION_V1",
    "data_snooping_family_freezes": "DATA_SNOOPING_FAMILY_FREEZE_V1",
    "data_snooping_family_closures": "DATA_SNOOPING_FAMILY_CLOSURE_V1",
    "data_snooping_grid_assessments": "DATA_SNOOPING_GRID_ASSESSMENT_V1",
    "data_snooping_assessments": "DATA_SNOOPING_ASSESSMENT_V1",
}


def _column(
    name: str,
    sql_type: SqlType = SqlType.TEXT,
    *,
    nullable: bool = False,
) -> ColumnSpec:
    return ColumnSpec(name=name, sql_type=sql_type, nullable=nullable)


def _columns(*specific: ColumnSpec) -> tuple[ColumnSpec, ...]:
    return (
        _column("record_type"),
        _column("authority_instance_id"),
        _column("genesis_record_hash"),
        *specific,
        _column("record_hash"),
    )


_TABLE_COLUMNS = {
    "data_snooping_declarations": _columns(
        _column("declaration_id"),
        _column("family_id"),
        _column("selection_scope_id"),
        _column("deployment_mandate_hash"),
        _column("selection_decision_id"),
        _column("selection_scope_predecessor_head"),
        _column("decision_class"),
        _column("ordered_research_program_ids_json"),
        _column("ordered_research_program_ids_hash"),
        _column("ordered_research_question_ids_json"),
        _column("ordered_research_question_ids_hash"),
        _column("ordered_strategy_lineage_root_ids_json"),
        _column("ordered_strategy_lineage_root_ids_hash"),
        _column("objective_id"),
        _column("winner_selection_rule_id"),
        _column("search_data_contract_hash"),
        _column("oos_exposure_id"),
        _column("oos_release_policy_id"),
        _column("oos_access_policy_id"),
        _column("non_overlap_proof_hash"),
        _column("universe_id"),
        _column("universe_hash"),
        _column("ordered_period_calendar_hash"),
        _column("label_horizon"),
        _column("purge_periods", SqlType.INTEGER),
        _column("embargo_periods", SqlType.INTEGER),
        _column("return_currency"),
        _column("return_scale"),
        _column("compounding_convention"),
        _column("cost_model_id"),
        _column("cost_contract_hash"),
        _column("cost_parameters_hash"),
        _column("benchmark_id"),
        _column("benchmark_contract_hash"),
        _column("alpha_level"),
        _column("canonical_json_version"),
        _column("reality_check_algorithm_version"),
        _column("spa_algorithm_version"),
        _column("assessment_algorithm_version"),
        _column("bootstrap_algorithm_version"),
        _column("prng_algorithm_version"),
        _column("block_length_policy_id"),
        _column("block_length_grid_json"),
        _column("replication_schedule_json"),
        _column("bootstrap_seed", SqlType.INTEGER),
        _column("dlp_policy_id"),
        _column("dlp_policy_version"),
        _column("dlp_policy_hash"),
        _column("scanner_implementation"),
        _column("scanner_version"),
        _column("restricted_envelope_authority_id"),
        _column("quarantine_policy_id"),
        _column("public_artifact_scan_policy_id"),
        _column("allowed_search_kinds_json"),
        _column("generator_seeds_json"),
        _column("search_proposal_budgets_json"),
        _column("required_search_count", SqlType.INTEGER),
        _column("maximum_search_count", SqlType.INTEGER),
        _column("stopping_rule_hash"),
        _column("external_ingestion_deadline_us", SqlType.INTEGER),
        _column("search_attachment_rule_id"),
        _column("trial_ledger_authority_id"),
        _column("raw_proposal_count_definition"),
        _column("unique_performance_hypothesis_count_definition"),
        _column("global_unique_config_count_definition"),
        _column("performance_access_attempt_count_definition"),
        _column("declared_at_us", SqlType.INTEGER),
    ),
    "data_snooping_family_freezes": _columns(
        _column("freeze_id"),
        _column("family_id"),
        _column("declaration_id"),
        _column("declaration_record_hash"),
        _column("search_count", SqlType.INTEGER),
        _column("ordered_search_ids_json"),
        _column("ordered_search_ids_hash"),
        _column("ordered_search_terminal_hashes_json"),
        _column("ordered_search_terminal_hashes_hash"),
        _column("ordered_emission_seal_hashes_json"),
        _column("ordered_emission_seal_hashes_hash"),
        _column("ordered_candidate_chain_heads_json"),
        _column("ordered_candidate_chain_heads_hash"),
        _column("raw_proposal_count", SqlType.INTEGER),
        _column("unique_performance_hypothesis_count", SqlType.INTEGER),
        _column("frozen_config_set_hash"),
        _column("observed_pit_content_hash"),
        _column("code_manifest_evidence_hash"),
        _column("dlp_policy_hash"),
        _column("stopping_rule_hash"),
        _column("frozen_at_us", SqlType.INTEGER),
    ),
    "data_snooping_family_closures": _columns(
        _column("closure_id"),
        _column("family_id"),
        _column("declaration_id"),
        _column("declaration_record_hash"),
        _column("freeze_id"),
        _column("freeze_record_hash"),
        _column("closure_status"),
        _column("selection_scope_id"),
        _column("selection_decision_id"),
        _column("selection_scope_history_head"),
        _column("selection_scope_outcome_record_hash", nullable=True),
        _column("ordered_search_ids_hash"),
        _column("ordered_search_terminal_hashes_hash"),
        _column("ordered_emission_seal_hashes_hash"),
        _column("ordered_proposal_ids_hash"),
        _column("ordered_disposition_record_hashes_hash"),
        _column("ordered_config_hashes_json"),
        _column("ordered_evaluation_receipt_hashes_json"),
        _column("attempt_id", nullable=True),
        _column("terminal_attempt_event_id", nullable=True),
        _column("evaluation_batch_id", nullable=True),
        _column("evaluation_batch_receipt_hash", nullable=True),
        _column("oos_release_id", nullable=True),
        _column("oos_access_attempt_terminal_hash", nullable=True),
        _column("oos_release_record_hash", nullable=True),
        _column("raw_proposal_count", SqlType.INTEGER),
        _column("unique_performance_hypothesis_count", SqlType.INTEGER),
        _column("global_unique_config_count", SqlType.INTEGER),
        _column("performance_access_attempt_count", SqlType.INTEGER),
        _column("ordered_period_keys_hash", nullable=True),
        _column("candidate_series_set_hash", nullable=True),
        _column("benchmark_receipt_hash", nullable=True),
        _column("cost_model_input_set_hash", nullable=True),
        _column("winner_config_hash", nullable=True),
        _column("winner_evaluation_receipt_hash", nullable=True),
        _column("trial_ledger_proof_json"),
        _column("trial_ledger_proof_hash"),
        _column("failure_reasons_json"),
        _column("closed_at_us", SqlType.INTEGER),
    ),
    "data_snooping_grid_assessments": _columns(
        _column("grid_assessment_id"),
        _column("family_id"),
        _column("closure_manifest_hash"),
        _column("algorithm_version"),
        _column("period_count", SqlType.INTEGER),
        _column("block_length", SqlType.INTEGER),
        _column("replication_count", SqlType.INTEGER),
        _column("schedule_ordinal", SqlType.INTEGER),
        _column("prefix_index_stream_hash"),
        _column("prev_record_hash"),
        _column("reality_check_payload_json", nullable=True),
        _column("reality_check_result_hash", nullable=True),
        _column("spa_payload_json", nullable=True),
        _column("spa_result_hash", nullable=True),
        _column("grid_point_status"),
        _column("grid_point_verdict"),
        _column("promotion_effect"),
        _column("failure_reasons_json"),
        _column("assessed_at_us", SqlType.INTEGER),
    ),
    "data_snooping_assessments": _columns(
        _column("assessment_id"),
        _column("family_id"),
        _column("closure_manifest_hash"),
        _column("algorithm_version"),
        _column("ordered_latest_grid_assessment_hashes_json"),
        _column("ordered_latest_grid_assessment_hashes_hash"),
        _column("candidate_order_hash", nullable=True),
        _column("period_count", SqlType.INTEGER, nullable=True),
        _column("minimum_replication_count", SqlType.INTEGER, nullable=True),
        _column("assessment_status"),
        _column("diagnostic_verdict"),
        _column("promotion_effect"),
        _column("failure_reasons_json"),
        _column("concluded_at_us", SqlType.INTEGER),
    ),
}

_PRIMARY_KEYS = {
    "data_snooping_declarations": ("declaration_id",),
    "data_snooping_family_freezes": ("freeze_id",),
    "data_snooping_family_closures": ("closure_id",),
    "data_snooping_grid_assessments": ("grid_assessment_id",),
    "data_snooping_assessments": ("assessment_id",),
}


def _foreign_key(
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
) -> ForeignKeySpec:
    return ForeignKeySpec(
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
        on_update=ForeignKeyAction.RESTRICT,
        on_delete=ForeignKeyAction.RESTRICT,
        deferrability=ForeignKeyDeferrability.NOT_DEFERRABLE,
    )


def _genesis_foreign_key() -> ForeignKeySpec:
    return _foreign_key(
        ("authority_instance_id", "genesis_record_hash"),
        "family_authority_genesis",
        ("authority_instance_id", "record_hash"),
    )


_FOREIGN_KEYS = {
    "data_snooping_declarations": (
        _genesis_foreign_key(),
        _foreign_key(
            ("selection_scope_id", "selection_decision_id", "family_id"),
            "selection_decisions",
            ("selection_scope_id", "selection_decision_id", "family_id"),
        ),
        _foreign_key(
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
    ),
    "data_snooping_family_freezes": (
        _genesis_foreign_key(),
        _foreign_key(
            ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
    ),
    "data_snooping_family_closures": (
        _genesis_foreign_key(),
        _foreign_key(
            ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
        _foreign_key(
            ("authority_instance_id", "family_id", "freeze_id", "freeze_record_hash"),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "freeze_id", "record_hash"),
        ),
        _foreign_key(
            ("selection_scope_history_head",),
            "research_authority_events",
            ("record_hash",),
        ),
        _foreign_key(
            ("selection_scope_outcome_record_hash",),
            "selection_scope_outcomes",
            ("record_hash",),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "evaluation_batch_id",
                "family_id",
                "attempt_id",
                "evaluation_batch_receipt_hash",
            ),
            "data_snooping_evaluation_batches",
            (
                "authority_instance_id",
                "evaluation_batch_id",
                "family_id",
                "attempt_id",
                "record_hash",
            ),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "attempt_id",
                "terminal_attempt_event_id",
                "family_id",
                "oos_access_attempt_terminal_hash",
            ),
            "data_snooping_oos_access_attempts",
            (
                "authority_instance_id",
                "attempt_id",
                "attempt_event_id",
                "family_id",
                "record_hash",
            ),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "family_id",
                "attempt_id",
                "oos_release_id",
                "oos_release_record_hash",
            ),
            "data_snooping_oos_releases",
            (
                "authority_instance_id",
                "family_id",
                "attempt_id",
                "oos_release_id",
                "record_hash",
            ),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "winner_config_hash",
                "winner_evaluation_receipt_hash",
            ),
            "data_snooping_evaluation_receipts",
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "benchmark_receipt_hash",
            ),
            "data_snooping_benchmark_returns",
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "benchmark_receipt_hash",
            ),
        ),
    ),
    "data_snooping_grid_assessments": (
        _genesis_foreign_key(),
        _foreign_key(
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            "data_snooping_family_closures",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        _foreign_key(
            ("authority_instance_id", "family_id", "algorithm_version"),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "assessment_algorithm_version"),
        ),
    ),
    "data_snooping_assessments": (
        _genesis_foreign_key(),
        _foreign_key(
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            "data_snooping_family_closures",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        _foreign_key(
            ("authority_instance_id", "family_id", "algorithm_version"),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "assessment_algorithm_version"),
        ),
    ),
}


def _index(
    table: str,
    suffix: str,
    columns: tuple[str, ...],
    *,
    unique: bool = False,
    predicate: PredicateExpression | None = None,
) -> IndexSpec:
    prefix = "partial_unique" if unique and predicate is not None else "unique" if unique else "ordinary"
    return IndexSpec(
        name=f"{prefix}_{table}_{suffix}",
        columns=tuple(IndexColumnSpec(column=column) for column in columns),
        unique=unique,
        predicate=predicate,
    )


def _literal(value: None | int | float | str | bytes) -> LiteralValue:
    return LiteralValue(value)


def _value(column: str, source: RowSource = RowSource.CURRENT) -> ColumnValue:
    return ColumnValue(column=column, source=source)


def _compare_values(
    left: ColumnValue | LiteralValue,
    operator: ComparisonOperator,
    right: ColumnValue | LiteralValue,
) -> ComparisonExpression:
    return ComparisonExpression(left=left, operator=operator, right=right)


def _comparison(
    column: str,
    operator: ComparisonOperator,
    other: None | int | float | str | bytes | ColumnValue,
    *,
    source: RowSource = RowSource.CURRENT,
) -> ComparisonExpression:
    return _compare_values(
        _value(column, source),
        operator,
        other if isinstance(other, ColumnValue) else _literal(other),
    )


def _null(
    column: str,
    operator: NullOperator,
    *,
    source: RowSource = RowSource.CURRENT,
) -> NullExpression:
    return NullExpression(value=_value(column, source), operator=operator)


def _membership(
    column: str,
    values: tuple[None | int | float | str | bytes, ...],
    *,
    source: RowSource = RowSource.CURRENT,
) -> MembershipExpression:
    return MembershipExpression(
        value=_value(column, source),
        values=tuple(_literal(value) for value in values),
    )


def _logical(operator: LogicalOperator, *operands: PredicateExpression) -> LogicalExpression:
    return LogicalExpression(operator=operator, operands=operands)


def _bindings(
    *columns: str,
    source: RowSource = RowSource.NEW,
) -> tuple[LookupBinding, ...]:
    return tuple(LookupBinding(related_column=column, value=_value(column, source)) for column in columns)


def _mapped_bindings(
    *pairs: tuple[str, str],
    source: RowSource = RowSource.NEW,
) -> tuple[LookupBinding, ...]:
    return tuple(LookupBinding(related_column=related, value=_value(local, source)) for related, local in pairs)


def _relation(
    table: str,
    bindings: tuple[LookupBinding, ...],
    *,
    predicate: PredicateExpression | None = None,
    negated: bool = False,
) -> RelationExistsExpression:
    return RelationExistsExpression(
        table=table,
        bindings=bindings,
        predicate=predicate,
        negated=negated,
    )


def _aggregate(
    table: str,
    bindings: tuple[LookupBinding, ...],
    aggregate: RelationAggregateKind,
    *,
    value: str | None = None,
    order: tuple[str, ...] = (),
    operator: ComparisonOperator = ComparisonOperator.EQ,
    expected: ColumnValue | LiteralValue | RelatedValueLookupSpec = LiteralValue(0),
    predicate: PredicateExpression | None = None,
) -> RelationAggregateExpression:
    return RelationAggregateExpression(
        table=table,
        bindings=bindings,
        aggregate=aggregate,
        source=RowSource.RELATED,
        value=value,
        order=tuple(
            RelationAggregateOrderSpec(
                column=column,
                direction=SortDirection.ASC,
                collation=Collation.BINARY,
            )
            for column in order
        ),
        operator=operator,
        expected=expected,
        predicate=predicate,
    )


def _related_value(
    table: str,
    bindings: tuple[LookupBinding, ...],
    value: str,
) -> RelatedValueLookupSpec:
    return RelatedValueLookupSpec(table=table, bindings=bindings, value=value)


def _qualified(alias: str, column: str) -> QualifiedColumnValue:
    return QualifiedColumnValue(relation_alias=alias, column=column)


def _join(
    table: str,
    alias: str,
    on: tuple[tuple[str, str, str, str], ...],
    *,
    predicate: PredicateExpression | None = None,
) -> RelationJoinSpec:
    return RelationJoinSpec(
        table=table,
        alias=alias,
        on=tuple(
            RelationJoinEqualitySpec(
                left_alias=left_alias,
                left_column=left_column,
                right_alias=right_alias,
                right_column=right_column,
            )
            for left_alias, left_column, right_alias, right_column in on
        ),
        predicate=predicate,
    )


def _join_aggregate(
    anchor_table: str,
    anchor_alias: str,
    bindings: tuple[LookupBinding, ...],
    joins: tuple[RelationJoinSpec, ...],
    aggregate: RelationAggregateKind,
    *,
    value: tuple[str, str] | None = None,
    order: tuple[tuple[str, str], ...] = (),
    operator: ComparisonOperator = ComparisonOperator.EQ,
    expected: ColumnValue | LiteralValue | RelatedValueLookupSpec = LiteralValue(0),
) -> RelationJoinAggregateExpression:
    return RelationJoinAggregateExpression(
        anchor_table=anchor_table,
        anchor_alias=anchor_alias,
        bindings=bindings,
        joins=joins,
        aggregate=aggregate,
        source=RowSource.RELATED,
        value=None if value is None else _qualified(*value),
        order=tuple(
            RelationJoinAggregateOrderSpec(
                relation_alias=alias,
                column=column,
                direction=SortDirection.ASC,
                collation=Collation.BINARY,
            )
            for alias, column in order
        ),
        operator=operator,
        expected=expected,
    )


def _check(name: str, expression: PredicateExpression) -> CheckConstraintSpec:
    return CheckConstraintSpec(name=name, expression=expression)


def _guard(table: str, error_code: str, violation_when: PredicateExpression) -> TriggerPolicySpec:
    return TriggerPolicySpec(
        name=f"{table}_{error_code.lower()}",
        kind=TriggerPolicyKind.INSERT_GUARD,
        event=TriggerEvent.INSERT,
        error_code=error_code,
        violation_when=violation_when,
    )


def _immutable_triggers(table: str) -> tuple[TriggerPolicySpec, TriggerPolicySpec]:
    return (
        TriggerPolicySpec(
            name=f"{table}_no_update",
            kind=TriggerPolicyKind.NO_UPDATE,
            event=TriggerEvent.UPDATE,
            error_code=f"{table.upper()}_IMMUTABLE",
        ),
        TriggerPolicySpec(
            name=f"{table}_no_delete",
            kind=TriggerPolicyKind.NO_DELETE,
            event=TriggerEvent.DELETE,
            error_code=f"{table.upper()}_APPEND_ONLY",
        ),
    )


def _structural_source(
    kind: StructuralVerificationSourceKind,
    *,
    source_id: str | None = None,
    columns: tuple[str, ...] = (),
    referenced_table: str | None = None,
    referenced_columns: tuple[str, ...] = (),
) -> StructuralVerificationSourceSpec:
    return StructuralVerificationSourceSpec(
        kind=kind,
        source_id=source_id,
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
    )


_STRUCTURAL_RULE_SOURCES: dict[str, StructuralVerificationSourceSpec] = {}
for _prefix in (
    "declaration",
    "family_freeze",
    "family_closure",
    "grid_assessment",
    "assessment",
):
    _STRUCTURAL_RULE_SOURCES[f"{_prefix}_row_hash"] = _structural_source(
        StructuralVerificationSourceKind.ROW_HASH_SPEC
    )
    _STRUCTURAL_RULE_SOURCES[f"{_prefix}_genesis_binding"] = _structural_source(
        StructuralVerificationSourceKind.GENESIS_FOREIGN_KEY
    )
    _STRUCTURAL_RULE_SOURCES[f"{_prefix}_foreign_keys"] = _structural_source(
        StructuralVerificationSourceKind.ALL_FOREIGN_KEYS
    )


def _exact_foreign_key_source(
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
) -> StructuralVerificationSourceSpec:
    return _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
    )


_DECLARATION_EXACT_FOREIGN_KEY_SOURCE = _exact_foreign_key_source(
    (
        "authority_instance_id",
        "family_id",
        "declaration_id",
        "declaration_record_hash",
    ),
    "data_snooping_declarations",
    ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
)
_STRUCTURAL_RULE_SOURCES.update(
    {
        "declaration_scope_decision_exact": _exact_foreign_key_source(
            ("selection_scope_id", "selection_decision_id", "family_id"),
            "selection_decisions",
            ("selection_scope_id", "selection_decision_id", "family_id"),
        ),
        "declaration_oos_exposure_exact": _exact_foreign_key_source(
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
        "declaration_four_counter_definitions": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_declarations_counter_definitions",
        ),
        "family_freeze_declaration_exact": _DECLARATION_EXACT_FOREIGN_KEY_SOURCE,
        "family_freeze_declared_search_totality": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_freezes_freeze_all_declared_searches_present",
        ),
        "family_freeze_emission_seal_totality": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_freezes_freeze_emission_seals_complete",
        ),
        "family_freeze_candidate_chain_heads": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_freezes_freeze_emission_seals_complete",
        ),
        "family_freeze_independent_counts": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_freezes_freeze_counts_match",
        ),
        "family_freeze_config_set": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_freezes_freeze_counts_match",
        ),
        "family_freeze_declaration_content_stability": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_freezes_freeze_data_code_dlp_stable",
        ),
        "family_closure_freeze_exact": _exact_foreign_key_source(
            (
                "authority_instance_id",
                "family_id",
                "freeze_id",
                "freeze_record_hash",
            ),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "freeze_id", "record_hash"),
        ),
        "family_closure_search_manifest": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_closures_closure_freeze_exact",
        ),
        "family_closure_proposal_disposition_totality": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_closures_closure_proposal_dispositions_total",
        ),
        "family_closure_attempt_batch_branch": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_closures_closure_attempt_batch_exact_branch",
        ),
        "family_closure_series_calendar_cost_totality": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_closures_closure_series_calendar_cost_total",
        ),
        "family_closure_four_counts": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_closures_closure_four_counts_recomputed",
        ),
        "family_closure_trial_ledger_proof": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_closures_closure_trial_proof_exact",
        ),
        "family_closure_deterministic_winner": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_closures_closure_winner_rule_exact",
        ),
        "family_closure_branch_totality": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_family_closures_exact_branch",
        ),
        "grid_assessment_chain": _structural_source(
            StructuralVerificationSourceKind.CHAIN_SPEC
        ),
        "grid_assessment_closure_complete_exact": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_grid_assessments_grid_closure_complete_exact",
        ),
        "grid_assessment_declared_point": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_grid_assessments_grid_declared_point_exact",
        ),
        "grid_assessment_schedule_order": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_grid_assessments_grid_schedule_next",
        ),
        "grid_assessment_prefix_stream_extension": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_grid_assessments_grid_prefix_proof",
        ),
        "grid_assessment_pending_predecessor": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_grid_assessments_grid_previous_must_be_pending",
        ),
        "grid_assessment_branch_totality": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_grid_assessments_exact_branch",
        ),
        "grid_assessment_family_conclusion_cutoff": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_grid_assessments_grid_after_family_assessment_forbidden",
        ),
        "grid_assessment_promotion_none": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_grid_assessments_promotion_none",
        ),
        "assessment_closure_exact": _exact_foreign_key_source(
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            "data_snooping_family_closures",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        "assessment_latest_grid_policy": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_assessments_assessment_latest_grid_policy_exact",
        ),
        "assessment_branch_totality": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_assessments_exact_branch",
        ),
        "assessment_promotion_none": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_assessments_promotion_none",
        ),
        "family_search_declaration_exact": _DECLARATION_EXACT_FOREIGN_KEY_SOURCE,
        "family_search_declared_plan_match": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_family_searches_search_matches_declared_plan",
        ),
        "proposal_declaration_exact": _DECLARATION_EXACT_FOREIGN_KEY_SOURCE,
        "proposal_declaration_dlp_match": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_proposal_registrations_"
                "proposal_declaration_dlp_match"
            ),
        ),
        "oos_release_family_freeze_exact": _exact_foreign_key_source(
            ("authority_instance_id", "family_id", "family_freeze_record_hash"),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        "oos_release_scope_outcome_policy": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_oos_releases_oos_release_scope_outcome_policy",
        ),
        "oos_release_closure_cutoff": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_oos_releases_oos_release_before_closure",
        ),
        "oos_attempt_family_freeze_totality": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_oos_access_attempts_oos_attempt_requires_freeze",
        ),
        "oos_attempt_scope_outcome_policy": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_oos_access_attempts_oos_attempt_scope_outcome_policy",
        ),
        "oos_attempt_closure_cutoff": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_oos_access_attempts_oos_attempt_before_closure",
        ),
    }
)


_NAMED_RULE_VERIFIER_CODES = {
    rule_id: f"c8.verify_{rule_id}_v1"
    for rule_id in (
        "declaration_scope_history_predecessor",
        "declaration_program_question_root_totality",
        "declaration_search_oos_non_overlap",
        "declaration_ordered_sets_hashes",
        "declaration_grid_schedule",
        "declaration_code_data_dlp_binding",
        "declaration_search_plan",
        "declaration_precedes_family_activity",
        "family_freeze_terminal_search_totality",
        "family_freeze_activity_cutoff",
        "family_closure_scope_history",
        "grid_assessment_result_payload_hashes",
        "family_search_freeze_closure_cutoff",
        "proposal_freeze_closure_cutoff",
        "proposal_terminal_result_freeze_closure_cutoff",
        "hypothesis_freeze_closure_cutoff",
        "emission_seal_freeze_closure_cutoff",
        "candidate_disposition_freeze_closure_cutoff",
        "evaluation_batch_freeze_closure_cutoff",
        "family_oos_evaluation_freeze_closure_cutoff",
        "evaluation_receipt_freeze_closure_cutoff",
        "benchmark_return_freeze_closure_cutoff",
    )
}


def _rule(
    rule_id: str,
    kind: VerificationRuleKind,
    columns: tuple[str, ...] = (),
    *,
    referenced_table: str | None = None,
    verifier_code: str | None = None,
) -> VerificationRuleSpec:
    resolved_verifier_code = verifier_code or _NAMED_RULE_VERIFIER_CODES.get(rule_id)
    return VerificationRuleSpec(
        rule_id=rule_id,
        kind=kind,
        columns=columns,
        referenced_table=referenced_table,
        verifier_code=resolved_verifier_code,
        structural_source=_STRUCTURAL_RULE_SOURCES.get(rule_id),
    )


_INDEXES = {
    "data_snooping_declarations": (
        _index("data_snooping_declarations", "record_hash", ("record_hash",), unique=True),
        _index("data_snooping_declarations", "family", ("authority_instance_id", "family_id"), unique=True),
        _index("data_snooping_declarations", "decision", ("selection_decision_id",), unique=True),
        _index("data_snooping_declarations", "exposure", ("oos_exposure_id",), unique=True),
        _index(
            "data_snooping_declarations",
            "assessment_algorithm",
            ("authority_instance_id", "family_id", "assessment_algorithm_version"),
            unique=True,
        ),
        _index(
            "data_snooping_declarations",
            "exact_binding",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
            unique=True,
        ),
    ),
    "data_snooping_family_freezes": (
        _index("data_snooping_family_freezes", "record_hash", ("record_hash",), unique=True),
        _index("data_snooping_family_freezes", "family", ("authority_instance_id", "family_id"), unique=True),
        _index("data_snooping_family_freezes", "declaration", ("declaration_id",), unique=True),
        _index(
            "data_snooping_family_freezes",
            "exact_binding",
            ("authority_instance_id", "family_id", "freeze_id", "record_hash"),
            unique=True,
        ),
        _index(
            "data_snooping_family_freezes",
            "family_record",
            ("authority_instance_id", "family_id", "record_hash"),
            unique=True,
        ),
    ),
    "data_snooping_family_closures": (
        _index("data_snooping_family_closures", "record_hash", ("record_hash",), unique=True),
        _index("data_snooping_family_closures", "family", ("authority_instance_id", "family_id"), unique=True),
        _index("data_snooping_family_closures", "declaration", ("declaration_id",), unique=True),
        _index("data_snooping_family_closures", "freeze", ("freeze_id",), unique=True),
        _index(
            "data_snooping_family_closures",
            "family_record",
            ("authority_instance_id", "family_id", "record_hash"),
            unique=True,
        ),
        _index(
            "data_snooping_family_closures",
            "winner",
            ("family_id", "winner_config_hash", "winner_evaluation_receipt_hash"),
        ),
    ),
    "data_snooping_grid_assessments": (
        _index(
            "data_snooping_grid_assessments",
            "record_hash",
            ("record_hash",),
            unique=True,
        ),
        _index(
            "data_snooping_grid_assessments",
            "grid",
            ("authority_instance_id", "family_id", "block_length", "replication_count", "algorithm_version"),
            unique=True,
        ),
        _index(
            "data_snooping_grid_assessments",
            "schedule",
            ("authority_instance_id", "family_id", "block_length", "schedule_ordinal"),
            unique=True,
        ),
        _index(
            "data_snooping_grid_assessments",
            "closure",
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
        ),
    ),
    "data_snooping_assessments": (
        _index("data_snooping_assessments", "record_hash", ("record_hash",), unique=True),
        _index(
            "data_snooping_assessments",
            "lineage",
            ("authority_instance_id", "family_id", "record_hash"),
            unique=True,
        ),
        _index(
            "data_snooping_assessments",
            "family",
            ("authority_instance_id", "family_id"),
            unique=True,
        ),
        _index(
            "data_snooping_assessments",
            "closure",
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            unique=True,
        ),
    ),
}


def _base_checks(table: str, time_column: str) -> tuple[CheckConstraintSpec, ...]:
    return (
        _check(
            f"{table}_record_type",
            _comparison("record_type", ComparisonOperator.EQ, _RECORD_TYPES[table]),
        ),
        _check(
            f"{table}_positive_time",
            _comparison(time_column, ComparisonOperator.GT, 0),
        ),
    )


_CLOSURE_REFERENCE_COLUMNS = (
    "attempt_id",
    "terminal_attempt_event_id",
    "evaluation_batch_id",
    "evaluation_batch_receipt_hash",
    "oos_release_id",
    "oos_access_attempt_terminal_hash",
    "oos_release_record_hash",
)
_CLOSURE_RESULT_COLUMNS = (
    "ordered_period_keys_hash",
    "candidate_series_set_hash",
    "benchmark_receipt_hash",
    "cost_model_input_set_hash",
    "winner_config_hash",
    "winner_evaluation_receipt_hash",
)
_CLOSURE_NOT_ASSESSABLE_COMMON = (
    *(
        _null(column, NullOperator.IS_NULL)
        for column in (
            "evaluation_batch_id",
            "evaluation_batch_receipt_hash",
            "oos_release_id",
            "oos_release_record_hash",
            *_CLOSURE_RESULT_COLUMNS,
        )
    ),
    _comparison("ordered_config_hashes_json", ComparisonOperator.EQ, "[]"),
    _comparison("ordered_evaluation_receipt_hashes_json", ComparisonOperator.EQ, "[]"),
    _comparison("failure_reasons_json", ComparisonOperator.NE, "[]"),
)

_CLOSURE_BRANCH_CHECK = _logical(
    LogicalOperator.OR,
    _logical(
        LogicalOperator.AND,
        _comparison("closure_status", ComparisonOperator.EQ, "COMPLETE"),
        *(_null(column, NullOperator.IS_NOT_NULL) for column in _CLOSURE_REFERENCE_COLUMNS),
        _comparison("performance_access_attempt_count", ComparisonOperator.EQ, 1),
        *(_null(column, NullOperator.IS_NOT_NULL) for column in _CLOSURE_RESULT_COLUMNS),
        _comparison("ordered_config_hashes_json", ComparisonOperator.NE, "[]"),
        _comparison("ordered_evaluation_receipt_hashes_json", ComparisonOperator.NE, "[]"),
        _comparison("failure_reasons_json", ComparisonOperator.EQ, "[]"),
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("closure_status", ComparisonOperator.EQ, "NOT_ASSESSABLE"),
        *(
            _null(column, NullOperator.IS_NULL)
            for column in (
                "attempt_id",
                "terminal_attempt_event_id",
                "oos_access_attempt_terminal_hash",
            )
        ),
        _comparison("performance_access_attempt_count", ComparisonOperator.EQ, 0),
        *_CLOSURE_NOT_ASSESSABLE_COMMON,
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("closure_status", ComparisonOperator.EQ, "NOT_ASSESSABLE"),
        *(
            _null(column, NullOperator.IS_NOT_NULL)
            for column in (
                "attempt_id",
                "terminal_attempt_event_id",
                "oos_access_attempt_terminal_hash",
            )
        ),
        _comparison("performance_access_attempt_count", ComparisonOperator.EQ, 1),
        *_CLOSURE_NOT_ASSESSABLE_COMMON,
    ),
)

_GRID_ASSESSMENT_KERNEL_COLUMNS = (
    "reality_check_payload_json",
    "reality_check_result_hash",
    "spa_payload_json",
    "spa_result_hash",
)
_VALID_GRID_EVIDENCE = (
    *(
        _null(column, NullOperator.IS_NOT_NULL)
        for column in _GRID_ASSESSMENT_KERNEL_COLUMNS
    ),
    _comparison("failure_reasons_json", ComparisonOperator.EQ, "[]"),
)
_GRID_ASSESSMENT_BRANCH_CHECK = _logical(
    LogicalOperator.OR,
    _logical(
        LogicalOperator.AND,
        _comparison("grid_point_status", ComparisonOperator.EQ, "RESOLVED"),
        _membership(
            "grid_point_verdict",
            (
                "SUPPORTS_SUPERIORITY",
                "DOES_NOT_SUPPORT_SUPERIORITY",
                "METHOD_DISAGREEMENT",
            ),
        ),
        *_VALID_GRID_EVIDENCE,
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("grid_point_status", ComparisonOperator.EQ, "RESOLVED"),
        _comparison(
            "grid_point_verdict",
            ComparisonOperator.EQ,
            "PENDING_MORE_REPLICATIONS",
        ),
        *_VALID_GRID_EVIDENCE,
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("grid_point_status", ComparisonOperator.EQ, "AMBIGUOUS"),
        _comparison(
            "grid_point_verdict",
            ComparisonOperator.EQ,
            "PENDING_MORE_REPLICATIONS",
        ),
        *_VALID_GRID_EVIDENCE,
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("grid_point_status", ComparisonOperator.EQ, "AMBIGUOUS"),
        _comparison(
            "grid_point_verdict",
            ComparisonOperator.EQ,
            "MONTE_CARLO_UNRESOLVED",
        ),
        *_VALID_GRID_EVIDENCE,
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("grid_point_status", ComparisonOperator.EQ, "INVALID"),
        _comparison(
            "grid_point_verdict",
            ComparisonOperator.EQ,
            "NOT_APPLICABLE",
        ),
        *(
            _null(column, NullOperator.IS_NULL)
            for column in _GRID_ASSESSMENT_KERNEL_COLUMNS
        ),
        _comparison("failure_reasons_json", ComparisonOperator.NE, "[]"),
    ),
)

_FAMILY_ASSESSMENT_DERIVED_COLUMNS = (
    "candidate_order_hash",
    "period_count",
    "minimum_replication_count",
)
_FAMILY_ASSESSMENT_BRANCH_CHECK = _logical(
    LogicalOperator.OR,
    _logical(
        LogicalOperator.AND,
        _comparison("assessment_status", ComparisonOperator.EQ, "ASSESSED"),
        _membership(
            "diagnostic_verdict",
            (
                "SUPPORTS_SUPERIORITY",
                "DOES_NOT_SUPPORT_SUPERIORITY",
                "METHOD_DISAGREEMENT",
                "MONTE_CARLO_UNRESOLVED",
            ),
        ),
        _comparison(
            "ordered_latest_grid_assessment_hashes_json",
            ComparisonOperator.NE,
            "[]",
        ),
        *(
            _null(column, NullOperator.IS_NOT_NULL)
            for column in _FAMILY_ASSESSMENT_DERIVED_COLUMNS
        ),
        _comparison("period_count", ComparisonOperator.GE, 20),
        _comparison("minimum_replication_count", ComparisonOperator.GE, 10_000),
        _comparison("failure_reasons_json", ComparisonOperator.EQ, "[]"),
    ),
    _logical(
        LogicalOperator.AND,
        _comparison(
            "assessment_status",
            ComparisonOperator.EQ,
            "NOT_ASSESSABLE",
        ),
        _comparison(
            "diagnostic_verdict",
            ComparisonOperator.EQ,
            "NOT_APPLICABLE",
        ),
        *(
            _null(column, NullOperator.IS_NULL)
            for column in _FAMILY_ASSESSMENT_DERIVED_COLUMNS
        ),
        _comparison("failure_reasons_json", ComparisonOperator.NE, "[]"),
    ),
)

_CHECKS = {
    "data_snooping_declarations": (
        *_base_checks("data_snooping_declarations", "declared_at_us"),
        _check(
            "data_snooping_declarations_positive_deadline",
            _comparison("external_ingestion_deadline_us", ComparisonOperator.GT, 0),
        ),
        _check(
            "data_snooping_declarations_deadline_after_declaration",
            _compare_values(
                _value("external_ingestion_deadline_us"),
                ComparisonOperator.GT,
                _value("declared_at_us"),
            ),
        ),
        _check(
            "data_snooping_declarations_decision_class_domain",
            _membership("decision_class", ("PUBLISHABLE", "DIAGNOSTIC_ONLY")),
        ),
        _check(
            "data_snooping_declarations_nonnegative_purge_embargo",
            _logical(
                LogicalOperator.AND,
                _comparison("purge_periods", ComparisonOperator.GE, 0),
                _comparison("embargo_periods", ComparisonOperator.GE, 0),
            ),
        ),
        _check(
            "data_snooping_declarations_positive_search_bounds",
            _logical(
                LogicalOperator.AND,
                _comparison("required_search_count", ComparisonOperator.GT, 0),
                _comparison("maximum_search_count", ComparisonOperator.GT, 0),
            ),
        ),
        _check(
            "data_snooping_declarations_search_bounds_order",
            _compare_values(
                _value("required_search_count"),
                ComparisonOperator.LE,
                _value("maximum_search_count"),
            ),
        ),
        _check(
            "data_snooping_declarations_algorithm_versions",
            _logical(
                LogicalOperator.AND,
                _comparison("canonical_json_version", ComparisonOperator.EQ, "QUANTOS_CANONICAL_JSON_V1"),
                _comparison("reality_check_algorithm_version", ComparisonOperator.EQ, "WHITE_REALITY_CHECK_V1"),
                _comparison("spa_algorithm_version", ComparisonOperator.EQ, "HANSEN_SPA_V1"),
                _comparison("assessment_algorithm_version", ComparisonOperator.EQ, _ASSESSMENT_ALGORITHM_VERSION),
                _comparison(
                    "bootstrap_algorithm_version",
                    ComparisonOperator.EQ,
                    "QUANTOS_STATIONARY_BOOTSTRAP_V1",
                ),
                _comparison("prng_algorithm_version", ComparisonOperator.EQ, "SPLITMIX64_V1"),
                _comparison("block_length_policy_id", ComparisonOperator.EQ, "QUANTOS_BLOCK_LENGTH_GRID_V1"),
            ),
        ),
        _check(
            "data_snooping_declarations_counter_definitions",
            _logical(
                LogicalOperator.AND,
                _comparison(
                    "raw_proposal_count_definition",
                    ComparisonOperator.EQ,
                    "EVERY_REGISTERED_OPAQUE_ENVELOPE_V1",
                ),
                _comparison(
                    "unique_performance_hypothesis_count_definition",
                    ComparisonOperator.EQ,
                    "DISTINCT_ACCEPTED_CONFIG_HASH_V1",
                ),
                _comparison(
                    "global_unique_config_count_definition",
                    ComparisonOperator.EQ,
                    "TRIAL_LEDGER_DISTINCT_CONFIG_AT_HISTORY_HEAD_V1",
                ),
                _comparison(
                    "performance_access_attempt_count_definition",
                    ComparisonOperator.EQ,
                    "DISTINCT_COMMITTED_STARTED_ATTEMPT_V1",
                ),
            ),
        ),
    ),
    "data_snooping_family_freezes": (
        *_base_checks("data_snooping_family_freezes", "frozen_at_us"),
        _check(
            "data_snooping_family_freezes_positive_search_count",
            _comparison("search_count", ComparisonOperator.GT, 0),
        ),
        _check(
            "data_snooping_family_freezes_nonnegative_counts",
            _logical(
                LogicalOperator.AND,
                _comparison("raw_proposal_count", ComparisonOperator.GE, 0),
                _comparison("unique_performance_hypothesis_count", ComparisonOperator.GE, 0),
            ),
        ),
        _check(
            "data_snooping_family_freezes_unique_not_above_raw",
            _compare_values(
                _value("unique_performance_hypothesis_count"),
                ComparisonOperator.LE,
                _value("raw_proposal_count"),
            ),
        ),
    ),
    "data_snooping_family_closures": (
        *_base_checks("data_snooping_family_closures", "closed_at_us"),
        _check(
            "data_snooping_family_closures_status_domain",
            _membership("closure_status", ("COMPLETE", "NOT_ASSESSABLE")),
        ),
        _check(
            "data_snooping_family_closures_nonnegative_counts",
            _logical(
                LogicalOperator.AND,
                _comparison("raw_proposal_count", ComparisonOperator.GE, 0),
                _comparison("unique_performance_hypothesis_count", ComparisonOperator.GE, 0),
                _comparison("global_unique_config_count", ComparisonOperator.GE, 0),
                _comparison("performance_access_attempt_count", ComparisonOperator.GE, 0),
            ),
        ),
        _check(
            "data_snooping_family_closures_unique_not_above_raw",
            _compare_values(
                _value("unique_performance_hypothesis_count"),
                ComparisonOperator.LE,
                _value("raw_proposal_count"),
            ),
        ),
        _check(
            "data_snooping_family_closures_global_count_covers_family_subset",
            _compare_values(
                _value("global_unique_config_count"),
                ComparisonOperator.GE,
                _value("unique_performance_hypothesis_count"),
            ),
        ),
        _check(
            "data_snooping_family_closures_access_count_domain",
            _membership("performance_access_attempt_count", (0, 1)),
        ),
        _check("data_snooping_family_closures_exact_branch", _CLOSURE_BRANCH_CHECK),
    ),
    "data_snooping_grid_assessments": (
        *_base_checks("data_snooping_grid_assessments", "assessed_at_us"),
        _check(
            "data_snooping_grid_assessments_positive_grid",
            _logical(
                LogicalOperator.AND,
                _comparison("period_count", ComparisonOperator.GE, 20),
                _comparison("block_length", ComparisonOperator.GT, 0),
                _comparison("replication_count", ComparisonOperator.GT, 0),
                _comparison("schedule_ordinal", ComparisonOperator.GT, 0),
            ),
        ),
        _check(
            "data_snooping_grid_assessments_status_domain",
            _membership("grid_point_status", ("RESOLVED", "AMBIGUOUS", "INVALID")),
        ),
        _check(
            "data_snooping_grid_assessments_verdict_domain",
            _membership(
                "grid_point_verdict",
                (
                    "SUPPORTS_SUPERIORITY",
                    "DOES_NOT_SUPPORT_SUPERIORITY",
                    "METHOD_DISAGREEMENT",
                    "PENDING_MORE_REPLICATIONS",
                    "MONTE_CARLO_UNRESOLVED",
                    "NOT_APPLICABLE",
                ),
            ),
        ),
        _check(
            "data_snooping_grid_assessments_promotion_none",
            _comparison("promotion_effect", ComparisonOperator.EQ, "NONE"),
        ),
        _check(
            "data_snooping_grid_assessments_algorithm_version",
            _comparison("algorithm_version", ComparisonOperator.EQ, _ASSESSMENT_ALGORITHM_VERSION),
        ),
        _check(
            "data_snooping_grid_assessments_exact_branch",
            _GRID_ASSESSMENT_BRANCH_CHECK,
        ),
    ),
    "data_snooping_assessments": (
        *_base_checks("data_snooping_assessments", "concluded_at_us"),
        _check(
            "data_snooping_assessments_status_domain",
            _membership("assessment_status", ("ASSESSED", "NOT_ASSESSABLE")),
        ),
        _check(
            "data_snooping_assessments_verdict_domain",
            _membership(
                "diagnostic_verdict",
                (
                    "SUPPORTS_SUPERIORITY",
                    "DOES_NOT_SUPPORT_SUPERIORITY",
                    "METHOD_DISAGREEMENT",
                    "MONTE_CARLO_UNRESOLVED",
                    "NOT_APPLICABLE",
                ),
            ),
        ),
        _check(
            "data_snooping_assessments_promotion_none",
            _comparison("promotion_effect", ComparisonOperator.EQ, "NONE"),
        ),
        _check(
            "data_snooping_assessments_algorithm_version",
            _comparison(
                "algorithm_version",
                ComparisonOperator.EQ,
                _ASSESSMENT_ALGORITHM_VERSION,
            ),
        ),
        _check(
            "data_snooping_assessments_exact_branch",
            _FAMILY_ASSESSMENT_BRANCH_CHECK,
        ),
    ),
}


_FAMILY_BINDINGS = _bindings("authority_instance_id", "family_id")

_DECLARATION_GUARDS = (
    _guard(
        "data_snooping_declarations",
        "DECLARATION_PRECEDES_LINKED_SEARCH",
        _relation(
            "data_snooping_family_searches",
            _FAMILY_BINDINGS,
        ),
    ),
    _guard(
        "data_snooping_declarations",
        "DECLARATION_PRECEDES_FAMILY_PROPOSAL",
        _relation("data_snooping_proposal_registrations", _FAMILY_BINDINGS),
    ),
    _guard(
        "data_snooping_declarations",
        "DECLARATION_PRECEDES_OOS_ACCESS",
        _relation("data_snooping_oos_access_attempts", _FAMILY_BINDINGS),
    ),
)

_FREEZE_DECLARATION_BINDINGS = (
    *_FAMILY_BINDINGS,
    LookupBinding("declaration_id", _value("declaration_id", RowSource.NEW)),
    LookupBinding("record_hash", _value("declaration_record_hash", RowSource.NEW)),
)
_SEARCH_ORDER = ("link_ordinal", "search_id")
_SEAL_ORDER = ("search_id",)
_CONFIG_ORDER = ("config_hash",)

_FREEZE_GUARDS = (
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_DECLARATION_EXACT",
        _relation(
            "data_snooping_declarations",
            _FREEZE_DECLARATION_BINDINGS,
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_ALL_DECLARED_SEARCHES_PRESENT",
        _logical(
            LogicalOperator.OR,
            _relation(
                "data_snooping_declarations",
                _FREEZE_DECLARATION_BINDINGS,
                predicate=_logical(
                    LogicalOperator.AND,
                    _compare_values(
                        _value("required_search_count", RowSource.RELATED),
                        ComparisonOperator.EQ,
                        _value("search_count", RowSource.NEW),
                    ),
                    _compare_values(
                        _value("maximum_search_count", RowSource.RELATED),
                        ComparisonOperator.EQ,
                        _value("search_count", RowSource.NEW),
                    ),
                ),
                negated=True,
            ),
            _aggregate(
                "data_snooping_family_searches",
                _FAMILY_BINDINGS,
                RelationAggregateKind.COUNT_ROWS,
                operator=ComparisonOperator.NE,
                expected=_value("search_count", RowSource.NEW),
            ),
            _aggregate(
                "data_snooping_family_searches",
                _FAMILY_BINDINGS,
                RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                value="search_id",
                order=_SEARCH_ORDER,
                operator=ComparisonOperator.NE,
                expected=_value("ordered_search_ids_hash", RowSource.NEW),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_SEARCHES_TERMINAL",
        _join_aggregate(
            "data_snooping_family_searches",
            "family_search",
            _FAMILY_BINDINGS,
            (
                _join(
                    "search_attempt_events",
                    "terminal_event",
                    (("family_search", "search_id", "terminal_event", "search_id"),),
                    predicate=MembershipExpression(
                        value=_qualified("terminal_event", "status"),
                        values=(_literal("FAILED"), _literal("COMPLETED")),
                    ),
                ),
            ),
            RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
            value=("terminal_event", "event_hash"),
            order=(
                ("family_search", "link_ordinal"),
                ("family_search", "search_id"),
            ),
            operator=ComparisonOperator.NE,
            expected=_value("ordered_search_terminal_hashes_hash", RowSource.NEW),
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_EMISSION_SEALS_COMPLETE",
        _logical(
            LogicalOperator.OR,
            _aggregate(
                "data_snooping_search_emission_seals",
                _FAMILY_BINDINGS,
                RelationAggregateKind.COUNT_ROWS,
                operator=ComparisonOperator.NE,
                expected=_value("search_count", RowSource.NEW),
            ),
            _aggregate(
                "data_snooping_search_emission_seals",
                _FAMILY_BINDINGS,
                RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                value="record_hash",
                order=_SEAL_ORDER,
                operator=ComparisonOperator.NE,
                expected=_value("ordered_emission_seal_hashes_hash", RowSource.NEW),
            ),
            _aggregate(
                "data_snooping_search_emission_seals",
                _FAMILY_BINDINGS,
                RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                value="candidate_chain_head",
                order=_SEAL_ORDER,
                operator=ComparisonOperator.NE,
                expected=_value("ordered_candidate_chain_heads_hash", RowSource.NEW),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_SEARCH_BUDGETS_MATCH",
        _logical(
            LogicalOperator.OR,
            _relation(
                "data_snooping_declarations",
                _FREEZE_DECLARATION_BINDINGS,
                negated=True,
            ),
            _relation(
                "data_snooping_family_searches",
                _FAMILY_BINDINGS,
                predicate=_comparison(
                    "proposal_budget",
                    ComparisonOperator.LE,
                    0,
                    source=RowSource.RELATED,
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_COUNTS_MATCH",
        _logical(
            LogicalOperator.OR,
            _aggregate(
                "data_snooping_proposal_registrations",
                _FAMILY_BINDINGS,
                RelationAggregateKind.COUNT_ROWS,
                operator=ComparisonOperator.NE,
                expected=_value("raw_proposal_count", RowSource.NEW),
            ),
            _aggregate(
                "data_snooping_hypotheses",
                _FAMILY_BINDINGS,
                RelationAggregateKind.COUNT_DISTINCT,
                value="config_hash",
                operator=ComparisonOperator.NE,
                expected=_value("unique_performance_hypothesis_count", RowSource.NEW),
            ),
            _aggregate(
                "data_snooping_hypotheses",
                _FAMILY_BINDINGS,
                RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                value="config_hash",
                order=_CONFIG_ORDER,
                operator=ComparisonOperator.NE,
                expected=_value("frozen_config_set_hash", RowSource.NEW),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_DATA_CODE_DLP_STABLE",
        _relation(
            "data_snooping_declarations",
            _FREEZE_DECLARATION_BINDINGS,
            predicate=_logical(
                LogicalOperator.AND,
                *(
                    _compare_values(
                        _value(column, RowSource.RELATED),
                        ComparisonOperator.EQ,
                        _value(column, RowSource.NEW),
                    )
                    for column in (
                        "observed_pit_content_hash",
                        "code_manifest_evidence_hash",
                        "dlp_policy_hash",
                        "stopping_rule_hash",
                    )
                ),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_STOPPING_RULE_SATISFIED",
        _relation(
            "data_snooping_family_searches",
            _FAMILY_BINDINGS,
            predicate=_compare_values(
                _value("stopping_rule_hash", RowSource.RELATED),
                ComparisonOperator.NE,
                _value("stopping_rule_hash", RowSource.NEW),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_freezes",
        "FREEZE_PRECEDES_OOS_ACCESS",
        _relation("data_snooping_oos_access_attempts", _FAMILY_BINDINGS),
    ),
)

_CLOSURE_FREEZE_BINDINGS = (
    *_FAMILY_BINDINGS,
    LookupBinding("freeze_id", _value("freeze_id", RowSource.NEW)),
    LookupBinding("record_hash", _value("freeze_record_hash", RowSource.NEW)),
)
_CLOSURE_ATTEMPT_BINDINGS = (
    LookupBinding("authority_instance_id", _value("authority_instance_id", RowSource.NEW)),
    LookupBinding("attempt_id", _value("attempt_id", RowSource.NEW)),
    LookupBinding("attempt_event_id", _value("terminal_attempt_event_id", RowSource.NEW)),
    LookupBinding("family_id", _value("family_id", RowSource.NEW)),
    LookupBinding("record_hash", _value("oos_access_attempt_terminal_hash", RowSource.NEW)),
)
_CLOSURE_BATCH_BINDINGS = (
    LookupBinding("authority_instance_id", _value("authority_instance_id", RowSource.NEW)),
    LookupBinding("evaluation_batch_id", _value("evaluation_batch_id", RowSource.NEW)),
    LookupBinding("family_id", _value("family_id", RowSource.NEW)),
    LookupBinding("attempt_id", _value("attempt_id", RowSource.NEW)),
    LookupBinding("record_hash", _value("evaluation_batch_receipt_hash", RowSource.NEW)),
    LookupBinding(
        "ordered_period_keys_hash",
        _value("ordered_period_keys_hash", RowSource.NEW),
    ),
    LookupBinding(
        "benchmark_receipt_hash",
        _value("benchmark_receipt_hash", RowSource.NEW),
    ),
)
_CLOSURE_RELEASE_BINDINGS = (
    LookupBinding("authority_instance_id", _value("authority_instance_id", RowSource.NEW)),
    LookupBinding("family_id", _value("family_id", RowSource.NEW)),
    LookupBinding("attempt_id", _value("attempt_id", RowSource.NEW)),
    LookupBinding("oos_release_id", _value("oos_release_id", RowSource.NEW)),
    LookupBinding("record_hash", _value("oos_release_record_hash", RowSource.NEW)),
)

_CLOSURE_GUARDS = (
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_FREEZE_EXACT",
        _relation(
            "data_snooping_family_freezes",
            _CLOSURE_FREEZE_BINDINGS,
            predicate=_logical(
                LogicalOperator.AND,
                *(
                    _compare_values(
                        _value(column, RowSource.RELATED),
                        ComparisonOperator.EQ,
                        _value(column, RowSource.NEW),
                    )
                    for column in (
                        "ordered_search_ids_hash",
                        "ordered_search_terminal_hashes_hash",
                        "ordered_emission_seal_hashes_hash",
                    )
                ),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_NO_ORPHAN_STARTED_ATTEMPT",
        _aggregate(
            "data_snooping_oos_access_attempts",
            _FAMILY_BINDINGS,
            RelationAggregateKind.COUNT_DISTINCT,
            value="attempt_id",
            operator=ComparisonOperator.GT,
            expected=_literal(1),
            predicate=_logical(
                LogicalOperator.AND,
                _comparison("sequence_no", ComparisonOperator.EQ, 0, source=RowSource.RELATED),
                _comparison("status", ComparisonOperator.EQ, "STARTED", source=RowSource.RELATED),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_PROPOSAL_DISPOSITIONS_TOTAL",
        _logical(
            LogicalOperator.OR,
            _join_aggregate(
                "data_snooping_proposal_registrations",
                "proposal",
                _FAMILY_BINDINGS,
                (
                    _join(
                        "data_snooping_candidate_dispositions",
                        "disposition",
                        (
                            (
                                "proposal",
                                "authority_instance_id",
                                "disposition",
                                "authority_instance_id",
                            ),
                            ("proposal", "family_id", "disposition", "family_id"),
                            ("proposal", "proposal_id", "disposition", "proposal_id"),
                        ),
                    ),
                ),
                RelationAggregateKind.COUNT_ROWS,
                operator=ComparisonOperator.NE,
                expected=_value("raw_proposal_count", RowSource.NEW),
            ),
            _join_aggregate(
                "data_snooping_proposal_registrations",
                "proposal",
                _FAMILY_BINDINGS,
                (
                    _join(
                        "data_snooping_candidate_dispositions",
                        "disposition",
                        (
                            (
                                "proposal",
                                "authority_instance_id",
                                "disposition",
                                "authority_instance_id",
                            ),
                            ("proposal", "family_id", "disposition", "family_id"),
                            ("proposal", "proposal_id", "disposition", "proposal_id"),
                        ),
                    ),
                ),
                RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                value=("proposal", "proposal_id"),
                order=(
                    ("proposal", "search_id"),
                    ("proposal", "emission_ordinal"),
                    ("proposal", "proposal_id"),
                ),
                operator=ComparisonOperator.NE,
                expected=_value("ordered_proposal_ids_hash", RowSource.NEW),
            ),
            _join_aggregate(
                "data_snooping_proposal_registrations",
                "proposal",
                _FAMILY_BINDINGS,
                (
                    _join(
                        "data_snooping_candidate_dispositions",
                        "disposition",
                        (
                            (
                                "proposal",
                                "authority_instance_id",
                                "disposition",
                                "authority_instance_id",
                            ),
                            ("proposal", "family_id", "disposition", "family_id"),
                            ("proposal", "proposal_id", "disposition", "proposal_id"),
                        ),
                    ),
                ),
                RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                value=("disposition", "record_hash"),
                order=(
                    ("proposal", "search_id"),
                    ("proposal", "emission_ordinal"),
                    ("proposal", "proposal_id"),
                ),
                operator=ComparisonOperator.NE,
                expected=_value("ordered_disposition_record_hashes_hash", RowSource.NEW),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_ATTEMPT_BATCH_EXACT_BRANCH",
        _logical(
            LogicalOperator.OR,
            _logical(
                LogicalOperator.AND,
                _comparison("closure_status", ComparisonOperator.EQ, "COMPLETE", source=RowSource.NEW),
                _logical(
                    LogicalOperator.OR,
                    _relation(
                        "data_snooping_oos_access_attempts",
                        _CLOSURE_ATTEMPT_BINDINGS,
                        predicate=_comparison("status", ComparisonOperator.EQ, "COMPLETED", source=RowSource.RELATED),
                        negated=True,
                    ),
                    _relation(
                        "data_snooping_evaluation_batches",
                        _CLOSURE_BATCH_BINDINGS,
                        negated=True,
                    ),
                    _relation(
                        "data_snooping_oos_releases",
                        _CLOSURE_RELEASE_BINDINGS,
                        negated=True,
                    ),
                ),
            ),
            _logical(
                LogicalOperator.AND,
                _comparison("closure_status", ComparisonOperator.EQ, "NOT_ASSESSABLE", source=RowSource.NEW),
                _comparison("performance_access_attempt_count", ComparisonOperator.EQ, 1, source=RowSource.NEW),
                _relation(
                    "data_snooping_oos_access_attempts",
                    _CLOSURE_ATTEMPT_BINDINGS,
                    predicate=_comparison("status", ComparisonOperator.EQ, "INCOMPLETE", source=RowSource.RELATED),
                    negated=True,
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_SERIES_CALENDAR_COST_TOTAL",
        _logical(
            LogicalOperator.AND,
            _comparison(
                "closure_status",
                ComparisonOperator.EQ,
                "COMPLETE",
                source=RowSource.NEW,
            ),
            _logical(
                LogicalOperator.OR,
                CanonicalJsonPayloadHashExpression(
                    document=_value("ordered_config_hashes_json", RowSource.NEW),
                    excluded_paths=(),
                    encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
                    algorithm=RowHashAlgorithm.SHA256,
                    operator=ComparisonOperator.NE,
                    expected_hash=_related_value(
                        "data_snooping_evaluation_batches",
                        _CLOSURE_BATCH_BINDINGS,
                        "ordered_candidate_config_hashes_hash",
                    ),
                ),
                CanonicalJsonPayloadHashExpression(
                    document=_value(
                        "ordered_evaluation_receipt_hashes_json",
                        RowSource.NEW,
                    ),
                    excluded_paths=(),
                    encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
                    algorithm=RowHashAlgorithm.SHA256,
                    operator=ComparisonOperator.NE,
                    expected_hash=_related_value(
                        "data_snooping_evaluation_batches",
                        _CLOSURE_BATCH_BINDINGS,
                        "ordered_candidate_receipt_hashes_hash",
                    ),
                ),
                _join_aggregate(
                    "data_snooping_evaluation_batches",
                    "batch",
                    _CLOSURE_BATCH_BINDINGS,
                    (
                        _join(
                            "data_snooping_declarations",
                            "declaration",
                            (
                                (
                                    "batch",
                                    "authority_instance_id",
                                    "declaration",
                                    "authority_instance_id",
                                ),
                                ("batch", "family_id", "declaration", "family_id"),
                            ),
                            predicate=_logical(
                                LogicalOperator.AND,
                                _compare_values(
                                    _qualified("declaration", "record_hash"),
                                    ComparisonOperator.EQ,
                                    _value("declaration_record_hash", RowSource.NEW),
                                ),
                                _compare_values(
                                    _qualified("batch", "ordered_period_keys_hash"),
                                    ComparisonOperator.EQ,
                                    _qualified(
                                        "declaration",
                                        "ordered_period_calendar_hash",
                                    ),
                                ),
                                _compare_values(
                                    _qualified("batch", "cost_contract_hash"),
                                    ComparisonOperator.EQ,
                                    _qualified("declaration", "cost_contract_hash"),
                                ),
                            ),
                        ),
                    ),
                    RelationAggregateKind.COUNT_ROWS,
                    operator=ComparisonOperator.NE,
                    expected=_literal(1),
                ),
                _aggregate(
                    "data_snooping_evaluation_receipts",
                    _bindings(
                        "authority_instance_id",
                        "family_id",
                        "evaluation_batch_id",
                    ),
                    RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                    value="series_hash",
                    order=("config_hash",),
                    operator=ComparisonOperator.NE,
                    expected=_value("candidate_series_set_hash", RowSource.NEW),
                ),
                _aggregate(
                    "data_snooping_evaluation_receipts",
                    _bindings(
                        "authority_instance_id",
                        "family_id",
                        "evaluation_batch_id",
                    ),
                    RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                    value="cost_model_input_hash",
                    order=("config_hash",),
                    operator=ComparisonOperator.NE,
                    expected=_value("cost_model_input_set_hash", RowSource.NEW),
                ),
                _relation(
                    "data_snooping_benchmark_returns",
                    (
                        *_bindings(
                            "authority_instance_id",
                            "family_id",
                            "evaluation_batch_id",
                        ),
                        LookupBinding(
                            "benchmark_receipt_hash",
                            _value("benchmark_receipt_hash", RowSource.NEW),
                        ),
                    ),
                    negated=True,
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_TRIAL_PROOF_EXACT",
        _logical(
            LogicalOperator.OR,
            CanonicalJsonPayloadHashExpression(
                document=_value("trial_ledger_proof_json", RowSource.NEW),
                excluded_paths=(("proof_hash",),),
                encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
                algorithm=RowHashAlgorithm.SHA256,
                operator=ComparisonOperator.NE,
                expected_hash=_value("trial_ledger_proof_hash", RowSource.NEW),
            ),
            _compare_values(
                _value("global_unique_config_count", RowSource.NEW),
                ComparisonOperator.NE,
                CanonicalJsonFieldValue(
                    document=_value("trial_ledger_proof_json", RowSource.NEW),
                    path=("global_unique_config_count",),
                    scalar_type=CanonicalJsonScalarType.INTEGER,
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_SCOPE_HISTORY_PREFIX",
        _logical(
            LogicalOperator.OR,
            _relation(
                "data_snooping_declarations",
                (
                    *_FAMILY_BINDINGS,
                    LookupBinding(
                        "declaration_id",
                        _value("declaration_id", RowSource.NEW),
                    ),
                    LookupBinding(
                        "record_hash",
                        _value("declaration_record_hash", RowSource.NEW),
                    ),
                ),
                predicate=_logical(
                    LogicalOperator.AND,
                    _compare_values(
                        _value("selection_scope_id", RowSource.RELATED),
                        ComparisonOperator.EQ,
                        _value("selection_scope_id", RowSource.NEW),
                    ),
                    _compare_values(
                        _value("selection_decision_id", RowSource.RELATED),
                        ComparisonOperator.EQ,
                        _value("selection_decision_id", RowSource.NEW),
                    ),
                ),
                negated=True,
            ),
            RelationChainPrefixExpression(
                table="research_authority_events",
                partition_bindings=_bindings("authority_instance_id"),
                ordinal_column="event_ordinal",
                record_hash_column="record_hash",
                previous_hash_column="prev_record_hash",
                ancestor_hash=_related_value(
                    "data_snooping_declarations",
                    (
                        *_FAMILY_BINDINGS,
                        LookupBinding(
                            "declaration_id",
                            _value("declaration_id", RowSource.NEW),
                        ),
                        LookupBinding(
                            "record_hash",
                            _value("declaration_record_hash", RowSource.NEW),
                        ),
                    ),
                    "selection_scope_predecessor_head",
                ),
                head_hash=_value("selection_scope_history_head", RowSource.NEW),
                source=RowSource.RELATED,
                proof=RelationChainPrefixProof.COMPLETE_ANCESTOR_TO_CURRENT_TIP,
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_SCOPE_OUTCOME_BINDING",
        _logical(
            LogicalOperator.AND,
            _null(
                "selection_scope_outcome_record_hash",
                NullOperator.IS_NOT_NULL,
                source=RowSource.NEW,
            ),
            _relation(
                "selection_scope_outcomes",
                (
                    *_bindings(
                        "authority_instance_id",
                        "selection_scope_id",
                        "selection_decision_id",
                        "family_id",
                    ),
                    LookupBinding(
                        "record_hash",
                        _value(
                            "selection_scope_outcome_record_hash",
                            RowSource.NEW,
                        ),
                    ),
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_WINNER_RULE_EXACT",
        _logical(
            LogicalOperator.AND,
            _comparison(
                "closure_status",
                ComparisonOperator.EQ,
                "COMPLETE",
                source=RowSource.NEW,
            ),
            _logical(
                LogicalOperator.OR,
                _relation(
                    "data_snooping_declarations",
                    (
                        *_FAMILY_BINDINGS,
                        LookupBinding(
                            "declaration_id",
                            _value("declaration_id", RowSource.NEW),
                        ),
                        LookupBinding(
                            "record_hash",
                            _value("declaration_record_hash", RowSource.NEW),
                        ),
                    ),
                    predicate=_logical(
                        LogicalOperator.AND,
                        _comparison(
                            "objective_id",
                            ComparisonOperator.EQ,
                            "MEAN_NET_RETURN_DIFFERENTIAL",
                            source=RowSource.RELATED,
                        ),
                        _comparison(
                            "winner_selection_rule_id",
                            ComparisonOperator.EQ,
                            "MAX_MEAN_NET_RETURN_DIFFERENTIAL_THEN_CONFIG_HASH_V1",
                            source=RowSource.RELATED,
                        ),
                    ),
                    negated=True,
                ),
                RelationArgExtremumExpression(
                    anchor_table="data_snooping_evaluation_receipts",
                    anchor_alias="candidate",
                    bindings=_bindings(
                        "authority_instance_id",
                        "family_id",
                        "evaluation_batch_id",
                    ),
                    joins=(
                        _join(
                            "data_snooping_benchmark_returns",
                            "benchmark",
                            (
                                (
                                    "candidate",
                                    "authority_instance_id",
                                    "benchmark",
                                    "authority_instance_id",
                                ),
                                ("candidate", "family_id", "benchmark", "family_id"),
                                (
                                    "candidate",
                                    "evaluation_batch_id",
                                    "benchmark",
                                    "evaluation_batch_id",
                                ),
                                (
                                    "candidate",
                                    "ordered_period_keys_hash",
                                    "benchmark",
                                    "ordered_period_keys_hash",
                                ),
                                (
                                    "candidate",
                                    "cost_contract_hash",
                                    "benchmark",
                                    "cost_contract_hash",
                                ),
                            ),
                            predicate=_compare_values(
                                _qualified("benchmark", "benchmark_receipt_hash"),
                                ComparisonOperator.EQ,
                                _value("benchmark_receipt_hash", RowSource.NEW),
                            ),
                        ),
                    ),
                    metric=RelationMetricSpec(
                        metric_id="MEAN_NET_RETURN_DIFFERENTIAL_V1",
                        inputs=(
                            _qualified("candidate", "series_payload_json"),
                            _qualified("benchmark", "series_payload_json"),
                        ),
                    ),
                    extremum=RelationExtremum.MAX,
                    tie_break=(
                        RelationJoinAggregateOrderSpec(
                            relation_alias="candidate",
                            column="config_hash",
                        ),
                    ),
                    result=(
                        _qualified("candidate", "config_hash"),
                        _qualified("candidate", "evaluation_receipt_hash"),
                    ),
                    operator=ComparisonOperator.NE,
                    expected=(
                        _value("winner_config_hash", RowSource.NEW),
                        _value("winner_evaluation_receipt_hash", RowSource.NEW),
                    ),
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_family_closures",
        "CLOSURE_FOUR_COUNTS_RECOMPUTED",
        _logical(
            LogicalOperator.OR,
            _aggregate(
                "data_snooping_proposal_registrations",
                _FAMILY_BINDINGS,
                RelationAggregateKind.COUNT_ROWS,
                operator=ComparisonOperator.NE,
                expected=_value("raw_proposal_count", RowSource.NEW),
            ),
            _aggregate(
                "data_snooping_hypotheses",
                _FAMILY_BINDINGS,
                RelationAggregateKind.COUNT_DISTINCT,
                value="config_hash",
                operator=ComparisonOperator.NE,
                expected=_value("unique_performance_hypothesis_count", RowSource.NEW),
            ),
            _aggregate(
                "data_snooping_oos_access_attempts",
                _FAMILY_BINDINGS,
                RelationAggregateKind.COUNT_DISTINCT,
                value="attempt_id",
                operator=ComparisonOperator.NE,
                expected=_value("performance_access_attempt_count", RowSource.NEW),
                predicate=_logical(
                    LogicalOperator.AND,
                    _comparison("sequence_no", ComparisonOperator.EQ, 0, source=RowSource.RELATED),
                    _comparison("status", ComparisonOperator.EQ, "STARTED", source=RowSource.RELATED),
                ),
            ),
            _compare_values(
                _value("global_unique_config_count", RowSource.NEW),
                ComparisonOperator.NE,
                CanonicalJsonFieldValue(
                    document=_value("trial_ledger_proof_json", RowSource.NEW),
                    path=("global_unique_config_count",),
                    scalar_type=CanonicalJsonScalarType.INTEGER,
                ),
            ),
        ),
    ),
)

_GRID_CLOSURE_BINDINGS = (
    *_FAMILY_BINDINGS,
    LookupBinding("record_hash", _value("closure_manifest_hash", RowSource.NEW)),
)
_DECLARED_REPLICATION = RelatedCanonicalJsonArrayValueSpec(
    table="data_snooping_declarations",
    bindings=_FAMILY_BINDINGS,
    column="replication_schedule_json",
    element_type=CanonicalJsonScalarType.INTEGER,
    projection=CanonicalJsonArrayProjection.ELEMENT_AT_ORDINAL,
    ordinal=_value("schedule_ordinal", RowSource.NEW),
    ordinal_offset=-1,
)
_REPLICATION_MISMATCH = _compare_values(
    _value("replication_count", RowSource.NEW),
    ComparisonOperator.NE,
    _DECLARED_REPLICATION,
)
_DECLARED_SCHEDULE_LENGTH = RelatedCanonicalJsonArrayLengthValueSpec(
    table="data_snooping_declarations",
    bindings=_FAMILY_BINDINGS,
    column="replication_schedule_json",
    element_type=CanonicalJsonScalarType.INTEGER,
)

_GRID_ASSESSMENT_GUARDS = (
    _guard(
        "data_snooping_grid_assessments",
        "GRID_CLOSURE_COMPLETE_EXACT",
        _relation(
            "data_snooping_family_closures",
            _GRID_CLOSURE_BINDINGS,
            predicate=_comparison(
                "closure_status",
                ComparisonOperator.EQ,
                "COMPLETE",
                source=RowSource.RELATED,
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_DECLARED_POINT_EXACT",
        _logical(
            LogicalOperator.OR,
            RelatedCanonicalJsonArrayContainsExpression(
                table="data_snooping_declarations",
                bindings=_FAMILY_BINDINGS,
                column="block_length_grid_json",
                element_type=CanonicalJsonScalarType.INTEGER,
                element=_value("block_length", RowSource.NEW),
                negated=True,
            ),
            _REPLICATION_MISMATCH,
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_SCHEDULE_NEXT",
        _logical(
            LogicalOperator.OR,
            ChainPredecessorExpression(
                table="data_snooping_grid_assessments",
                partition_bindings=_bindings("authority_instance_id", "family_id", "block_length"),
                ordinal_column="schedule_ordinal",
                current_ordinal=_value("schedule_ordinal", RowSource.NEW),
                record_hash_column="record_hash",
                current_previous_hash=_value("prev_record_hash", RowSource.NEW),
                genesis_hash=_value("genesis_record_hash", RowSource.NEW),
                negated=True,
            ),
            _REPLICATION_MISMATCH,
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_PREFIX_PROOF",
        PrefixStreamExtensionExpression(
            previous_table="data_snooping_grid_assessments",
            previous_bindings=(
                *_bindings("authority_instance_id", "family_id", "block_length"),
                LookupBinding("record_hash", _value("prev_record_hash", RowSource.NEW)),
            ),
            previous_prefix_hash_column="prefix_index_stream_hash",
            previous_replication_count_column="replication_count",
            previous_period_count_column="period_count",
            declaration_table="data_snooping_declarations",
            declaration_bindings=_FAMILY_BINDINGS,
            algorithm_columns=(
                "bootstrap_seed",
                "bootstrap_algorithm_version",
                "prng_algorithm_version",
                "canonical_json_version",
                "block_length_policy_id",
            ),
            current_prefix_hash=_value("prefix_index_stream_hash", RowSource.NEW),
            current_replication_count=_value("replication_count", RowSource.NEW),
            current_period_count=_value("period_count", RowSource.NEW),
            block_length=_value("block_length", RowSource.NEW),
            current_ordinal=_value("schedule_ordinal", RowSource.NEW),
            genesis_hash=_value("genesis_record_hash", RowSource.NEW),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_PREVIOUS_MUST_BE_PENDING",
        _logical(
            LogicalOperator.AND,
            _comparison("schedule_ordinal", ComparisonOperator.GT, 1, source=RowSource.NEW),
            _relation(
                "data_snooping_grid_assessments",
                (
                    *_bindings(
                        "authority_instance_id",
                        "family_id",
                        "algorithm_version",
                        "block_length",
                    ),
                    LookupBinding(
                        "record_hash",
                        _value("prev_record_hash", RowSource.NEW),
                    ),
                ),
                predicate=_comparison(
                    "grid_point_verdict",
                    ComparisonOperator.EQ,
                    "PENDING_MORE_REPLICATIONS",
                    source=RowSource.RELATED,
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_VERDICT_SCHEDULE_EXACT",
        _logical(
            LogicalOperator.OR,
            _logical(
                LogicalOperator.AND,
                _comparison(
                    "grid_point_verdict",
                    ComparisonOperator.EQ,
                    "PENDING_MORE_REPLICATIONS",
                    source=RowSource.NEW,
                ),
                ComparisonExpression(
                    left=_value("schedule_ordinal", RowSource.NEW),
                    operator=ComparisonOperator.GE,
                    right=_DECLARED_SCHEDULE_LENGTH,
                ),
            ),
            _logical(
                LogicalOperator.AND,
                _comparison(
                    "grid_point_verdict",
                    ComparisonOperator.EQ,
                    "MONTE_CARLO_UNRESOLVED",
                    source=RowSource.NEW,
                ),
                ComparisonExpression(
                    left=_value("schedule_ordinal", RowSource.NEW),
                    operator=ComparisonOperator.NE,
                    right=_DECLARED_SCHEDULE_LENGTH,
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_REALITY_CHECK_PAYLOAD_HASH_EXACT",
        _logical(
            LogicalOperator.AND,
            _null(
                "reality_check_payload_json",
                NullOperator.IS_NOT_NULL,
                source=RowSource.NEW,
            ),
            CanonicalJsonPayloadHashExpression(
                document=_value("reality_check_payload_json", RowSource.NEW),
                excluded_paths=(),
                encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
                algorithm=RowHashAlgorithm.SHA256,
                operator=ComparisonOperator.NE,
                expected_hash=_value("reality_check_result_hash", RowSource.NEW),
            ),
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_SPA_PAYLOAD_HASH_EXACT",
        _logical(
            LogicalOperator.AND,
            _null(
                "spa_payload_json",
                NullOperator.IS_NOT_NULL,
                source=RowSource.NEW,
            ),
            CanonicalJsonPayloadHashExpression(
                document=_value("spa_payload_json", RowSource.NEW),
                excluded_paths=(),
                encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
                algorithm=RowHashAlgorithm.SHA256,
                operator=ComparisonOperator.NE,
                expected_hash=_value("spa_result_hash", RowSource.NEW),
            ),
        ),
    ),
    _guard(
        "data_snooping_grid_assessments",
        "GRID_AFTER_FAMILY_ASSESSMENT_FORBIDDEN",
        _relation("data_snooping_assessments", _FAMILY_BINDINGS),
    ),
)

_ASSESSMENT_CLOSURE_BINDINGS = (
    *_FAMILY_BINDINGS,
    LookupBinding("record_hash", _value("closure_manifest_hash", RowSource.NEW)),
)

_ASSESSMENT_GUARDS = (
    _guard(
        "data_snooping_assessments",
        "ASSESSMENT_CLOSURE_EXACT",
        _relation(
            "data_snooping_family_closures",
            _ASSESSMENT_CLOSURE_BINDINGS,
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_assessments",
        "ASSESSMENT_COMPLETE_CLOSURE_REQUIRED",
        _logical(
            LogicalOperator.AND,
            _comparison(
                "assessment_status",
                ComparisonOperator.EQ,
                "ASSESSED",
                source=RowSource.NEW,
            ),
            _relation(
                "data_snooping_family_closures",
                _ASSESSMENT_CLOSURE_BINDINGS,
                predicate=_comparison(
                    "closure_status",
                    ComparisonOperator.EQ,
                    "COMPLETE",
                    source=RowSource.RELATED,
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_assessments",
        "ASSESSMENT_LATEST_GRID_POLICY_EXACT",
        _logical(
            LogicalOperator.AND,
            _comparison(
                "assessment_status",
                ComparisonOperator.EQ,
                "ASSESSED",
                source=RowSource.NEW,
            ),
            RelationLatestPerDomainPolicyExpression(
                table="data_snooping_grid_assessments",
                bindings=_bindings(
                    "authority_instance_id",
                    "family_id",
                    "algorithm_version",
                ),
                policy_id="ANY_GRID_DISAGREEMENT_MAX_P_V1",
                source=RowSource.RELATED,
                inputs=(
                    "block_length",
                    "replication_count",
                    "reality_check_result_hash",
                    "spa_result_hash",
                    "grid_point_status",
                    "grid_point_verdict",
                    "record_hash",
                ),
                order=(RelationAggregateOrderSpec(column="block_length"),),
                expected_verdict=_value("diagnostic_verdict", RowSource.NEW),
                expected_ordered_record_hashes_hash=_value(
                    "ordered_latest_grid_assessment_hashes_hash",
                    RowSource.NEW,
                ),
                declared_domain=RelatedCanonicalJsonArrayValueSpec(
                    table="data_snooping_declarations",
                    bindings=_FAMILY_BINDINGS,
                    column="block_length_grid_json",
                    element_type=CanonicalJsonScalarType.INTEGER,
                    projection=CanonicalJsonArrayProjection.CANONICAL_ARRAY,
                ),
                domain_column="block_length",
                latest_ordinal_column="schedule_ordinal",
                latest_cardinality=(
                    RelationLatestPerDomainCardinality.EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_assessments",
        "ASSESSMENT_LATEST_GRID_HASHES_EXACT",
        CanonicalJsonPayloadHashExpression(
            document=_value(
                "ordered_latest_grid_assessment_hashes_json",
                RowSource.NEW,
            ),
            excluded_paths=(),
            encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
            algorithm=RowHashAlgorithm.SHA256,
            operator=ComparisonOperator.NE,
            expected_hash=_value(
                "ordered_latest_grid_assessment_hashes_hash",
                RowSource.NEW,
            ),
        ),
    ),
    _guard(
        "data_snooping_assessments",
        "ASSESSMENT_NA_CLOSURE_FORBIDS_GRID_EVIDENCE",
        _logical(
            LogicalOperator.AND,
            _comparison(
                "assessment_status",
                ComparisonOperator.EQ,
                "NOT_ASSESSABLE",
                source=RowSource.NEW,
            ),
            _compare_values(
                _value(
                    "ordered_latest_grid_assessment_hashes_json",
                    RowSource.NEW,
                ),
                ComparisonOperator.NE,
                _literal("[]"),
            ),
            _relation(
                "data_snooping_family_closures",
                _ASSESSMENT_CLOSURE_BINDINGS,
                predicate=_comparison(
                    "closure_status",
                    ComparisonOperator.EQ,
                    "NOT_ASSESSABLE",
                    source=RowSource.RELATED,
                ),
            ),
        ),
    ),
)

_TABLE_GUARDS = {
    "data_snooping_declarations": _DECLARATION_GUARDS,
    "data_snooping_family_freezes": _FREEZE_GUARDS,
    "data_snooping_family_closures": _CLOSURE_GUARDS,
    "data_snooping_grid_assessments": _GRID_ASSESSMENT_GUARDS,
    "data_snooping_assessments": _ASSESSMENT_GUARDS,
}


def _row_hash_rule(table: str, rule_id: str) -> VerificationRuleSpec:
    return _rule(
        rule_id,
        VerificationRuleKind.ROW_HASH,
        tuple(column.name for column in _TABLE_COLUMNS[table] if column.name != "record_hash"),
    )


_VERIFICATION_RULES = {
    "data_snooping_declarations": (
        _row_hash_rule("data_snooping_declarations", "declaration_row_hash"),
        _rule(
            "declaration_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("declaration_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "declaration_scope_decision_exact",
            VerificationRuleKind.SELECTION_SCOPE,
            ("selection_scope_id", "selection_decision_id", "family_id"),
        ),
        _rule(
            "declaration_scope_history_predecessor",
            VerificationRuleKind.TERMINAL_HEAD,
            ("selection_scope_predecessor_head",),
        ),
        _rule(
            "declaration_program_question_root_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "ordered_research_program_ids_hash",
                "ordered_research_question_ids_hash",
                "ordered_strategy_lineage_root_ids_hash",
            ),
            referenced_table="research_programs",
        ),
        _rule(
            "declaration_oos_exposure_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            referenced_table="global_oos_exposures",
        ),
        _rule(
            "declaration_search_oos_non_overlap",
            VerificationRuleKind.EXPOSURE_CONSUMPTION,
            ("search_data_contract_hash", "oos_exposure_id", "non_overlap_proof_hash"),
        ),
        _rule(
            "declaration_ordered_sets_hashes",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "ordered_research_program_ids_json",
                "ordered_research_program_ids_hash",
                "ordered_research_question_ids_json",
                "ordered_research_question_ids_hash",
                "ordered_strategy_lineage_root_ids_json",
                "ordered_strategy_lineage_root_ids_hash",
            ),
        ),
        _rule(
            "declaration_grid_schedule",
            VerificationRuleKind.CONTENT_BINDING,
            ("block_length_grid_json", "replication_schedule_json", "bootstrap_seed"),
        ),
        _rule(
            "declaration_code_data_dlp_binding",
            VerificationRuleKind.CONTENT_BINDING,
            ("search_data_contract_hash", "dlp_policy_hash", "scanner_implementation", "scanner_version"),
        ),
        _rule(
            "declaration_search_plan",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "ordered_research_program_ids_json",
                "allowed_search_kinds_json",
                "generator_seeds_json",
                "search_proposal_budgets_json",
                "required_search_count",
                "maximum_search_count",
                "stopping_rule_hash",
                "search_attachment_rule_id",
            ),
        ),
        _rule(
            "declaration_four_counter_definitions",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "raw_proposal_count_definition",
                "unique_performance_hypothesis_count_definition",
                "global_unique_config_count_definition",
                "performance_access_attempt_count_definition",
            ),
        ),
        _rule(
            "declaration_precedes_family_activity",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "declaration_id", "declared_at_us"),
            referenced_table="data_snooping_family_searches",
        ),
    ),
    "data_snooping_family_freezes": (
        _row_hash_rule("data_snooping_family_freezes", "family_freeze_row_hash"),
        _rule(
            "family_freeze_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("family_freeze_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "family_freeze_declaration_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
            referenced_table="data_snooping_declarations",
        ),
        _rule(
            "family_freeze_declared_search_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "search_count", "ordered_search_ids_hash"),
            referenced_table="data_snooping_family_searches",
        ),
        _rule(
            "family_freeze_terminal_search_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "ordered_search_terminal_hashes_json",
                "ordered_search_terminal_hashes_hash",
            ),
            referenced_table="search_attempt_events",
        ),
        _rule(
            "family_freeze_emission_seal_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "ordered_emission_seal_hashes_hash"),
            referenced_table="data_snooping_search_emission_seals",
        ),
        _rule(
            "family_freeze_candidate_chain_heads",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "ordered_candidate_chain_heads_hash"),
            referenced_table="data_snooping_search_emission_seals",
        ),
        _rule(
            "family_freeze_independent_counts",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "raw_proposal_count", "unique_performance_hypothesis_count"),
            referenced_table="data_snooping_proposal_registrations",
        ),
        _rule(
            "family_freeze_config_set",
            VerificationRuleKind.CONTENT_BINDING,
            ("family_id", "frozen_config_set_hash"),
        ),
        _rule(
            "family_freeze_declaration_content_stability",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "observed_pit_content_hash",
                "code_manifest_evidence_hash",
                "dlp_policy_hash",
                "stopping_rule_hash",
            ),
        ),
        _rule(
            "family_freeze_activity_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "frozen_at_us"),
        ),
    ),
    "data_snooping_family_closures": (
        _row_hash_rule("data_snooping_family_closures", "family_closure_row_hash"),
        _rule(
            "family_closure_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("family_closure_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "family_closure_freeze_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "freeze_id", "freeze_record_hash"),
            referenced_table="data_snooping_family_freezes",
        ),
        _rule(
            "family_closure_scope_history",
            VerificationRuleKind.SELECTION_SCOPE,
            (
                "selection_scope_id",
                "selection_decision_id",
                "selection_scope_history_head",
                "selection_scope_outcome_record_hash",
            ),
        ),
        _rule(
            "family_closure_search_manifest",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "ordered_search_ids_hash",
                "ordered_search_terminal_hashes_hash",
                "ordered_emission_seal_hashes_hash",
            ),
            referenced_table="data_snooping_family_searches",
        ),
        _rule(
            "family_closure_proposal_disposition_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "ordered_proposal_ids_hash", "ordered_disposition_record_hashes_hash"),
            referenced_table="data_snooping_candidate_dispositions",
        ),
        _rule(
            "family_closure_attempt_batch_branch",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "attempt_id",
                "terminal_attempt_event_id",
                "oos_access_attempt_terminal_hash",
                "evaluation_batch_id",
                "evaluation_batch_receipt_hash",
                "closure_status",
            ),
            referenced_table="data_snooping_oos_access_attempts",
        ),
        _rule(
            "family_closure_series_calendar_cost_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "evaluation_batch_id",
                "ordered_period_keys_hash",
                "candidate_series_set_hash",
                "benchmark_receipt_hash",
                "cost_model_input_set_hash",
            ),
            referenced_table="data_snooping_evaluation_receipts",
        ),
        _rule(
            "family_closure_four_counts",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "raw_proposal_count",
                "unique_performance_hypothesis_count",
                "global_unique_config_count",
                "performance_access_attempt_count",
            ),
            referenced_table="data_snooping_proposal_registrations",
        ),
        _rule(
            "family_closure_trial_ledger_proof",
            VerificationRuleKind.CONTENT_BINDING,
            ("trial_ledger_proof_json", "trial_ledger_proof_hash", "global_unique_config_count"),
        ),
        _rule(
            "family_closure_deterministic_winner",
            VerificationRuleKind.CONTENT_BINDING,
            ("winner_config_hash", "winner_evaluation_receipt_hash", "benchmark_receipt_hash"),
        ),
        _rule(
            "family_closure_branch_totality",
            VerificationRuleKind.STATE_BRANCH,
            ("closure_status", "performance_access_attempt_count"),
        ),
    ),
    "data_snooping_grid_assessments": (
        _row_hash_rule(
            "data_snooping_grid_assessments",
            "grid_assessment_row_hash",
        ),
        _rule(
            "grid_assessment_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("grid_assessment_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "grid_assessment_chain",
            VerificationRuleKind.ORDERED_CHAIN,
            ("authority_instance_id", "family_id", "block_length", "schedule_ordinal", "prev_record_hash"),
        ),
        _rule(
            "grid_assessment_closure_complete_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            referenced_table="data_snooping_family_closures",
        ),
        _rule(
            "grid_assessment_declared_point",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "family_id",
                "period_count",
                "block_length",
                "replication_count",
                "algorithm_version",
            ),
            referenced_table="data_snooping_declarations",
        ),
        _rule(
            "grid_assessment_schedule_order",
            VerificationRuleKind.CONTENT_BINDING,
            ("block_length", "replication_count", "schedule_ordinal"),
        ),
        _rule(
            "grid_assessment_prefix_stream_extension",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "period_count",
                "prefix_index_stream_hash",
                "prev_record_hash",
            ),
        ),
        _rule(
            "grid_assessment_pending_predecessor",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "block_length",
                "replication_count",
                "schedule_ordinal",
                "grid_point_verdict",
            ),
            referenced_table="data_snooping_grid_assessments",
        ),
        _rule(
            "grid_assessment_result_payload_hashes",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "reality_check_payload_json",
                "reality_check_result_hash",
                "spa_payload_json",
                "spa_result_hash",
            ),
        ),
        _rule(
            "grid_assessment_branch_totality",
            VerificationRuleKind.STATE_BRANCH,
            (
                "grid_point_status",
                "grid_point_verdict",
                "failure_reasons_json",
            ),
        ),
        _rule(
            "grid_assessment_family_conclusion_cutoff",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "assessed_at_us"),
            referenced_table="data_snooping_assessments",
        ),
        _rule(
            "grid_assessment_promotion_none",
            VerificationRuleKind.CONTENT_BINDING,
            ("promotion_effect",),
        ),
    ),
    "data_snooping_assessments": (
        _row_hash_rule("data_snooping_assessments", "assessment_row_hash"),
        _rule(
            "assessment_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("assessment_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "assessment_closure_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            referenced_table="data_snooping_family_closures",
        ),
        _rule(
            "assessment_latest_grid_policy",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "ordered_latest_grid_assessment_hashes_json",
                "ordered_latest_grid_assessment_hashes_hash",
                "candidate_order_hash",
                "period_count",
                "minimum_replication_count",
                "diagnostic_verdict",
            ),
            referenced_table="data_snooping_grid_assessments",
        ),
        _rule(
            "assessment_branch_totality",
            VerificationRuleKind.STATE_BRANCH,
            (
                "assessment_status",
                "diagnostic_verdict",
                "candidate_order_hash",
                "period_count",
                "minimum_replication_count",
                "failure_reasons_json",
            ),
        ),
        _rule(
            "assessment_semantic_recomputation",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "authority_instance_id",
                "family_id",
                "closure_manifest_hash",
                "algorithm_version",
                "ordered_latest_grid_assessment_hashes_json",
                "ordered_latest_grid_assessment_hashes_hash",
                "candidate_order_hash",
                "period_count",
                "minimum_replication_count",
                "assessment_status",
                "diagnostic_verdict",
                "failure_reasons_json",
                "record_hash",
            ),
            verifier_code="c8.verify_family_assessment_v1",
        ),
        _rule(
            "assessment_promotion_none",
            VerificationRuleKind.CONTENT_BINDING,
            ("promotion_effect",),
        ),
    ),
}


_GRID_ASSESSMENT_CHAIN = ChainSpec(
    partition_key_columns=("authority_instance_id", "family_id", "block_length"),
    ordinal_column="schedule_ordinal",
    genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
    previous_hash_column="prev_record_hash",
    record_hash_column="record_hash",
    terminal_head_rule=ChainHeadRule.LATEST_RECORD_HASH,
)


def _table_spec(table: str) -> AuthorityTableSpec:
    columns = _TABLE_COLUMNS[table]
    return AuthorityTableSpec(
        table_name=table,
        columns=columns,
        primary_key_columns=_PRIMARY_KEYS[table],
        row_hash=RowHashSpec(
            canonical_fields=tuple(column.name for column in columns if column.name != "record_hash"),
            version_literal=_RECORD_TYPES[table],
            record_hash_column="record_hash",
        ),
        triggers=(*_immutable_triggers(table), *_TABLE_GUARDS[table]),
        foreign_keys=_FOREIGN_KEYS[table],
        indexes=_INDEXES[table],
        checks=_CHECKS[table],
        chain=(
            _GRID_ASSESSMENT_CHAIN
            if table == "data_snooping_grid_assessments"
            else None
        ),
        verification_rules=_VERIFICATION_RULES[table],
    )


C8_AUTHORITY_TABLE_SPECS = tuple(_table_spec(table) for table in _TABLE_ORDER)


_C5_EXTENSION_ORDER = (
    "C8_FAMILY_SEARCH_DECLARATION_AND_LATE_GUARDS_V1",
    "C8_PROPOSAL_DECLARATION_DLP_AND_LATE_GUARDS_V1",
    "C8_PROPOSAL_TERMINAL_RESULT_LATE_GUARDS_V1",
    "C8_HYPOTHESIS_LATE_GUARDS_V1",
    "C8_EMISSION_SEAL_LATE_GUARDS_V1",
)
_C7_TABLE_ORDER = (
    "data_snooping_oos_releases",
    "data_snooping_oos_access_attempts",
    "data_snooping_candidate_dispositions",
    "data_snooping_evaluation_batches",
    "data_snooping_family_oos_evaluations",
    "data_snooping_evaluation_receipts",
    "data_snooping_benchmark_returns",
)
_C7_EXTENSION_ORDER = tuple(f"C8_{table.upper()}_LATE_INSERT_GUARDS_V1" for table in _C7_TABLE_ORDER)

_DECLARATION_COLUMNS = (
    _column("declaration_id"),
    _column("declaration_record_hash"),
)
_DECLARATION_COLUMN_INSERTION = OrderedColumnInsertionSpec(
    position=InsertionPosition.AFTER,
    anchor_column="genesis_record_hash",
    columns=_DECLARATION_COLUMNS,
)
_DECLARATION_HASH_INSERTION = OrderedFieldInsertionSpec(
    position=InsertionPosition.AFTER,
    anchor_field="genesis_record_hash",
    fields=("declaration_id", "declaration_record_hash"),
)
_DECLARATION_FOREIGN_KEY = _foreign_key(
    ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
    "data_snooping_declarations",
    ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
)
_EXACT_DECLARATION = _relation(
    "data_snooping_declarations",
    (
        *_FAMILY_BINDINGS,
        LookupBinding("declaration_id", _value("declaration_id", RowSource.NEW)),
        LookupBinding("record_hash", _value("declaration_record_hash", RowSource.NEW)),
    ),
    negated=True,
)


def _freeze_cutoff(table: str, error_code: str) -> TriggerPolicySpec:
    return _guard(
        table,
        error_code,
        _relation("data_snooping_family_freezes", _FAMILY_BINDINGS),
    )


def _closure_cutoff(table: str, error_code: str) -> TriggerPolicySpec:
    return _guard(
        table,
        error_code,
        _relation("data_snooping_family_closures", _FAMILY_BINDINGS),
    )


_SEARCH_EXTENSION = AuthorityTableExtensionSpec(
    extension_id=_C5_EXTENSION_ORDER[0],
    target_table="data_snooping_family_searches",
    column_insertion=_DECLARATION_COLUMN_INSERTION,
    row_hash_field_insertion=_DECLARATION_HASH_INSERTION,
    foreign_keys=(_DECLARATION_FOREIGN_KEY,),
    indexes=(
        _index(
            "data_snooping_family_searches",
            "declaration_binding",
            ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
        ),
    ),
    triggers=(
        _guard("data_snooping_family_searches", "SEARCH_REQUIRES_EXACT_DECLARATION", _EXACT_DECLARATION),
        _guard(
            "data_snooping_family_searches",
            "SEARCH_MATCHES_DECLARED_PLAN",
            _logical(
                LogicalOperator.OR,
                RelatedCanonicalJsonArrayContainsExpression(
                    table="data_snooping_declarations",
                    bindings=(
                        *_FAMILY_BINDINGS,
                        LookupBinding("declaration_id", _value("declaration_id", RowSource.NEW)),
                        LookupBinding("record_hash", _value("declaration_record_hash", RowSource.NEW)),
                    ),
                    column="ordered_research_program_ids_json",
                    element_type=CanonicalJsonScalarType.TEXT,
                    element=_value("research_program_id", RowSource.NEW),
                    negated=True,
                ),
                *(
                    _compare_values(
                        _value(local_column, RowSource.NEW),
                        ComparisonOperator.NE,
                        RelatedCanonicalJsonArrayValueSpec(
                            table="data_snooping_declarations",
                            bindings=(
                                *_FAMILY_BINDINGS,
                                LookupBinding(
                                    "declaration_id",
                                    _value("declaration_id", RowSource.NEW),
                                ),
                                LookupBinding(
                                    "record_hash",
                                    _value("declaration_record_hash", RowSource.NEW),
                                ),
                            ),
                            column=json_column,
                            element_type=element_type,
                            projection=CanonicalJsonArrayProjection.ELEMENT_AT_ORDINAL,
                            ordinal=_value("link_ordinal", RowSource.NEW),
                            ordinal_offset=-1,
                        ),
                    )
                    for local_column, json_column, element_type in (
                        (
                            "search_kind",
                            "allowed_search_kinds_json",
                            CanonicalJsonScalarType.TEXT,
                        ),
                        (
                            "generator_seed",
                            "generator_seeds_json",
                            CanonicalJsonScalarType.INTEGER,
                        ),
                        (
                            "proposal_budget",
                            "search_proposal_budgets_json",
                            CanonicalJsonScalarType.INTEGER,
                        ),
                    )
                ),
                _relation(
                    "data_snooping_declarations",
                    (
                        *_FAMILY_BINDINGS,
                        LookupBinding(
                            "declaration_id",
                            _value("declaration_id", RowSource.NEW),
                        ),
                        LookupBinding(
                            "record_hash",
                            _value("declaration_record_hash", RowSource.NEW),
                        ),
                    ),
                    predicate=_logical(
                        LogicalOperator.AND,
                        _compare_values(
                            _value("stopping_rule_hash", RowSource.RELATED),
                            ComparisonOperator.EQ,
                            _value("stopping_rule_hash", RowSource.NEW),
                        ),
                        _compare_values(
                            _value("external_ingestion_deadline_us", RowSource.RELATED),
                            ComparisonOperator.EQ,
                            _value("external_ingestion_deadline_us", RowSource.NEW),
                        ),
                    ),
                    negated=True,
                ),
            ),
        ),
        _freeze_cutoff("data_snooping_family_searches", "SEARCH_LINK_BEFORE_FREEZE"),
        _closure_cutoff("data_snooping_family_searches", "SEARCH_LINK_BEFORE_CLOSURE"),
    ),
    verification_rules=(
        _rule(
            "family_search_declaration_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
            referenced_table="data_snooping_declarations",
        ),
        _rule(
            "family_search_declared_plan_match",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "family_id",
                "research_program_id",
                "search_kind",
                "generator_seed",
                "proposal_budget",
                "stopping_rule_hash",
                "external_ingestion_deadline_us",
                "link_ordinal",
            ),
            referenced_table="data_snooping_declarations",
        ),
        _rule(
            "family_search_freeze_closure_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "linked_at_us"),
        ),
    ),
)

_PROPOSAL_REGISTRATION_EXTENSION = AuthorityTableExtensionSpec(
    extension_id=_C5_EXTENSION_ORDER[1],
    target_table="data_snooping_proposal_registrations",
    column_insertion=_DECLARATION_COLUMN_INSERTION,
    row_hash_field_insertion=_DECLARATION_HASH_INSERTION,
    foreign_keys=(_DECLARATION_FOREIGN_KEY,),
    indexes=(
        _index(
            "data_snooping_proposal_registrations",
            "declaration_binding",
            ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
        ),
    ),
    triggers=(
        _guard(
            "data_snooping_proposal_registrations",
            "PROPOSAL_REQUIRES_EXACT_DECLARATION",
            _EXACT_DECLARATION,
        ),
        _guard(
            "data_snooping_proposal_registrations",
            "PROPOSAL_DECLARATION_DLP_MATCH",
            _relation(
                "data_snooping_declarations",
                (
                    *_FAMILY_BINDINGS,
                    LookupBinding("declaration_id", _value("declaration_id", RowSource.NEW)),
                    LookupBinding("record_hash", _value("declaration_record_hash", RowSource.NEW)),
                ),
                predicate=_logical(
                    LogicalOperator.AND,
                    *(
                        _compare_values(
                            _value(column, RowSource.RELATED),
                            ComparisonOperator.EQ,
                            _value(column, RowSource.NEW),
                        )
                        for column in (
                            "dlp_policy_id",
                            "dlp_policy_version",
                            "scanner_implementation",
                            "scanner_version",
                        )
                    ),
                ),
                negated=True,
            ),
        ),
        _guard(
            "data_snooping_proposal_registrations",
            "PROPOSAL_WITHIN_DECLARED_EXTERNAL_DEADLINE",
            _relation(
                "data_snooping_declarations",
                (
                    *_FAMILY_BINDINGS,
                    LookupBinding("declaration_id", _value("declaration_id", RowSource.NEW)),
                    LookupBinding("record_hash", _value("declaration_record_hash", RowSource.NEW)),
                ),
                predicate=_compare_values(
                    _value("external_ingestion_deadline_us", RowSource.RELATED),
                    ComparisonOperator.GE,
                    _value("registered_at_us", RowSource.NEW),
                ),
                negated=True,
            ),
        ),
        _freeze_cutoff(
            "data_snooping_proposal_registrations",
            "PROPOSAL_BEFORE_FREEZE",
        ),
        _closure_cutoff(
            "data_snooping_proposal_registrations",
            "PROPOSAL_BEFORE_CLOSURE",
        ),
    ),
    verification_rules=(
        _rule(
            "proposal_declaration_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "declaration_id", "declaration_record_hash"),
            referenced_table="data_snooping_declarations",
        ),
        _rule(
            "proposal_declaration_dlp_match",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("family_id", "dlp_policy_id", "dlp_policy_version", "scanner_implementation", "scanner_version"),
            referenced_table="data_snooping_declarations",
        ),
        _rule(
            "proposal_freeze_closure_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "registered_at_us"),
        ),
    ),
)


_PROPOSAL_TERMINAL_RESULT_EXTENSION = AuthorityTableExtensionSpec(
    extension_id=_C5_EXTENSION_ORDER[2],
    target_table="data_snooping_proposals",
    triggers=(
        _freeze_cutoff(
            "data_snooping_proposals",
            "PROPOSAL_TERMINAL_RESULT_BEFORE_FREEZE",
        ),
        _closure_cutoff(
            "data_snooping_proposals",
            "PROPOSAL_TERMINAL_RESULT_BEFORE_CLOSURE",
        ),
    ),
    verification_rules=(
        _rule(
            "proposal_terminal_result_freeze_closure_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "parsed_at_us"),
        ),
    ),
)


_HYPOTHESIS_EXTENSION = AuthorityTableExtensionSpec(
    extension_id=_C5_EXTENSION_ORDER[3],
    target_table="data_snooping_hypotheses",
    triggers=(
        _freeze_cutoff("data_snooping_hypotheses", "HYPOTHESIS_BEFORE_FREEZE"),
        _closure_cutoff("data_snooping_hypotheses", "HYPOTHESIS_BEFORE_CLOSURE"),
    ),
    verification_rules=(
        _rule(
            "hypothesis_freeze_closure_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "first_search_id", "first_proposal_id"),
        ),
    ),
)

_SEAL_EXTENSION = AuthorityTableExtensionSpec(
    extension_id=_C5_EXTENSION_ORDER[4],
    target_table="data_snooping_search_emission_seals",
    triggers=(
        _freeze_cutoff("data_snooping_search_emission_seals", "EMISSION_SEAL_BEFORE_FREEZE"),
        _closure_cutoff("data_snooping_search_emission_seals", "EMISSION_SEAL_BEFORE_CLOSURE"),
    ),
    verification_rules=(
        _rule(
            "emission_seal_freeze_closure_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "search_id", "sealed_at_us"),
        ),
    ),
)

_RELEASE_FREEZE_BINDINGS = (
    *_FAMILY_BINDINGS,
    LookupBinding("record_hash", _value("family_freeze_record_hash", RowSource.NEW)),
)


def _release_scope_path(
    decision_class: str,
    *,
    require_terminal_outcome: bool,
) -> RelationJoinAggregateExpression:
    joins = [
        _join(
            "data_snooping_family_freezes",
            "freeze",
            (
                (
                    "attempt",
                    "authority_instance_id",
                    "freeze",
                    "authority_instance_id",
                ),
                ("attempt", "family_id", "freeze", "family_id"),
            ),
            predicate=_compare_values(
                _qualified("freeze", "record_hash"),
                ComparisonOperator.EQ,
                _value("family_freeze_record_hash", RowSource.NEW),
            ),
        ),
        _join(
            "data_snooping_declarations",
            "declaration",
            (
                (
                    "freeze",
                    "authority_instance_id",
                    "declaration",
                    "authority_instance_id",
                ),
                ("freeze", "family_id", "declaration", "family_id"),
                ("freeze", "declaration_id", "declaration", "declaration_id"),
                (
                    "freeze",
                    "declaration_record_hash",
                    "declaration",
                    "record_hash",
                ),
            ),
            predicate=_logical(
                LogicalOperator.AND,
                _compare_values(
                    _qualified("attempt", "status"),
                    ComparisonOperator.EQ,
                    _literal("STARTED"),
                ),
                _compare_values(
                    _qualified("attempt", "frozen_config_set_hash"),
                    ComparisonOperator.EQ,
                    _qualified("freeze", "frozen_config_set_hash"),
                ),
                _compare_values(
                    _qualified("declaration", "decision_class"),
                    ComparisonOperator.EQ,
                    _literal(decision_class),
                ),
                *(
                    _compare_values(
                        _qualified("attempt", attempt_column),
                        ComparisonOperator.EQ,
                        _qualified("declaration", declaration_column),
                    )
                    for attempt_column, declaration_column in (
                        ("selection_decision_id", "selection_decision_id"),
                        ("oos_exposure_id", "oos_exposure_id"),
                        ("benchmark_contract_hash", "benchmark_contract_hash"),
                        (
                            "ordered_period_calendar_hash",
                            "ordered_period_calendar_hash",
                        ),
                        ("cost_contract_hash", "cost_contract_hash"),
                    )
                ),
            ),
        ),
    ]
    if require_terminal_outcome:
        joins.append(
            _join(
                "selection_scope_outcomes",
                "outcome",
                (
                    (
                        "declaration",
                        "selection_scope_id",
                        "outcome",
                        "selection_scope_id",
                    ),
                ),
                predicate=MembershipExpression(
                    value=_qualified("outcome", "outcome"),
                    values=(
                        _literal("WINNER_PINNED"),
                        _literal("CLOSED_WITHOUT_PROMOTION"),
                    ),
                ),
            )
        )
    return _join_aggregate(
        "data_snooping_oos_access_attempts",
        "attempt",
        (
            *_bindings("authority_instance_id", "family_id", "attempt_id"),
            LookupBinding(
                "attempt_event_id",
                _value("started_attempt_event_id", RowSource.NEW),
            ),
            LookupBinding(
                "oos_exposure_id",
                _value("oos_exposure_id", RowSource.NEW),
            ),
        ),
        tuple(joins),
        RelationAggregateKind.COUNT_ROWS,
        operator=ComparisonOperator.NE,
        expected=_literal(1),
    )


_RELEASE_EXTENSION = AuthorityTableExtensionSpec(
    extension_id=_C7_EXTENSION_ORDER[0],
    target_table="data_snooping_oos_releases",
    foreign_keys=(
        _foreign_key(
            ("authority_instance_id", "family_id", "family_freeze_record_hash"),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
    ),
    indexes=(
        _index(
            "data_snooping_oos_releases",
            "family_freeze",
            ("authority_instance_id", "family_id", "family_freeze_record_hash"),
        ),
        _index(
            "data_snooping_oos_releases",
            "closure_exact_binding",
            ("authority_instance_id", "family_id", "attempt_id", "oos_release_id", "record_hash"),
            unique=True,
        ),
    ),
    triggers=(
        _guard(
            "data_snooping_oos_releases",
            "OOS_RELEASE_REQUIRES_EXACT_FREEZE",
            _relation("data_snooping_family_freezes", _RELEASE_FREEZE_BINDINGS, negated=True),
        ),
        _guard(
            "data_snooping_oos_releases",
            "OOS_RELEASE_SCOPE_OUTCOME_POLICY",
            _logical(
                LogicalOperator.AND,
                _release_scope_path(
                    "PUBLISHABLE",
                    require_terminal_outcome=False,
                ),
                _release_scope_path(
                    "DIAGNOSTIC_ONLY",
                    require_terminal_outcome=True,
                ),
            ),
        ),
        _closure_cutoff("data_snooping_oos_releases", "OOS_RELEASE_BEFORE_CLOSURE"),
    ),
    verification_rules=(
        _rule(
            "oos_release_family_freeze_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "family_freeze_record_hash"),
            referenced_table="data_snooping_family_freezes",
        ),
        _rule(
            "oos_release_scope_outcome_policy",
            VerificationRuleKind.SELECTION_SCOPE,
            (
                "family_id",
                "attempt_id",
                "started_attempt_event_id",
                "family_freeze_record_hash",
                "oos_exposure_id",
            ),
            referenced_table="data_snooping_declarations",
        ),
        _rule(
            "oos_release_closure_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "released_at_us"),
        ),
    ),
)


def _c7_standard_extension(
    extension_id: str,
    table: str,
    time_column: str,
    freeze_error_code: str,
    cutoff_error_code: str,
    rule_id: str,
) -> AuthorityTableExtensionSpec:
    rules = [
        _rule(
            rule_id,
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", time_column),
        )
    ]
    guards = [
        _guard(
            table,
            freeze_error_code,
            _relation("data_snooping_family_freezes", _FAMILY_BINDINGS, negated=True),
        ),
        _closure_cutoff(table, cutoff_error_code),
    ]
    return AuthorityTableExtensionSpec(
        extension_id=extension_id,
        target_table=table,
        triggers=tuple(guards),
        verification_rules=tuple(rules),
    )


_ATTEMPT_EXTENSION = AuthorityTableExtensionSpec(
    extension_id=_C7_EXTENSION_ORDER[1],
    target_table="data_snooping_oos_access_attempts",
    indexes=(
        _index(
            "data_snooping_oos_access_attempts",
            "closure_terminal_exact_binding",
            ("authority_instance_id", "attempt_id", "attempt_event_id", "family_id", "record_hash"),
            unique=True,
        ),
    ),
    triggers=(
        _guard(
            "data_snooping_oos_access_attempts",
            "OOS_ATTEMPT_REQUIRES_FREEZE",
            _logical(
                LogicalOperator.AND,
                _comparison(
                    "status",
                    ComparisonOperator.EQ,
                    "STARTED",
                    source=RowSource.NEW,
                ),
                _join_aggregate(
                    "data_snooping_family_freezes",
                    "freeze",
                    _FAMILY_BINDINGS,
                    (
                        _join(
                            "data_snooping_declarations",
                            "declaration",
                            (
                                (
                                    "freeze",
                                    "authority_instance_id",
                                    "declaration",
                                    "authority_instance_id",
                                ),
                                ("freeze", "family_id", "declaration", "family_id"),
                                (
                                    "freeze",
                                    "declaration_id",
                                    "declaration",
                                    "declaration_id",
                                ),
                                (
                                    "freeze",
                                    "declaration_record_hash",
                                    "declaration",
                                    "record_hash",
                                ),
                            ),
                            predicate=_logical(
                                LogicalOperator.AND,
                                _compare_values(
                                    _qualified("freeze", "frozen_config_set_hash"),
                                    ComparisonOperator.EQ,
                                    _value("frozen_config_set_hash", RowSource.NEW),
                                ),
                                *(
                                    _compare_values(
                                        _qualified("declaration", related_column),
                                        ComparisonOperator.EQ,
                                        _value(local_column, RowSource.NEW),
                                    )
                                    for related_column, local_column in (
                                        ("selection_decision_id", "selection_decision_id"),
                                        ("oos_exposure_id", "oos_exposure_id"),
                                        ("benchmark_contract_hash", "benchmark_contract_hash"),
                                        (
                                            "ordered_period_calendar_hash",
                                            "ordered_period_calendar_hash",
                                        ),
                                        ("cost_contract_hash", "cost_contract_hash"),
                                    )
                                ),
                            ),
                        ),
                    ),
                    RelationAggregateKind.COUNT_ROWS,
                    operator=ComparisonOperator.NE,
                    expected=_literal(1),
                ),
            ),
        ),
        _guard(
            "data_snooping_oos_access_attempts",
            "OOS_ATTEMPT_SCOPE_OUTCOME_POLICY",
            _logical(
                LogicalOperator.AND,
                _comparison(
                    "status",
                    ComparisonOperator.EQ,
                    "STARTED",
                    source=RowSource.NEW,
                ),
                _relation(
                    "data_snooping_declarations",
                    _FAMILY_BINDINGS,
                    predicate=_logical(
                        LogicalOperator.AND,
                        _comparison(
                            "decision_class",
                            ComparisonOperator.EQ,
                            "PUBLISHABLE",
                            source=RowSource.RELATED,
                        ),
                        _compare_values(
                            _value("selection_decision_id", RowSource.RELATED),
                            ComparisonOperator.EQ,
                            _value("selection_decision_id", RowSource.NEW),
                        ),
                        _compare_values(
                            _value("oos_exposure_id", RowSource.RELATED),
                            ComparisonOperator.EQ,
                            _value("oos_exposure_id", RowSource.NEW),
                        ),
                    ),
                    negated=True,
                ),
                _join_aggregate(
                    "data_snooping_declarations",
                    "declaration",
                    _FAMILY_BINDINGS,
                    (
                        _join(
                            "selection_scope_outcomes",
                            "outcome",
                            (
                                (
                                    "declaration",
                                    "selection_scope_id",
                                    "outcome",
                                    "selection_scope_id",
                                ),
                            ),
                            predicate=_logical(
                                LogicalOperator.AND,
                                _compare_values(
                                    _qualified("declaration", "decision_class"),
                                    ComparisonOperator.EQ,
                                    _literal("DIAGNOSTIC_ONLY"),
                                ),
                                _compare_values(
                                    _qualified(
                                        "declaration",
                                        "selection_decision_id",
                                    ),
                                    ComparisonOperator.EQ,
                                    _value("selection_decision_id", RowSource.NEW),
                                ),
                                _compare_values(
                                    _qualified("declaration", "oos_exposure_id"),
                                    ComparisonOperator.EQ,
                                    _value("oos_exposure_id", RowSource.NEW),
                                ),
                                MembershipExpression(
                                    value=_qualified("outcome", "outcome"),
                                    values=(
                                        _literal("WINNER_PINNED"),
                                        _literal("CLOSED_WITHOUT_PROMOTION"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                    RelationAggregateKind.COUNT_ROWS,
                    operator=ComparisonOperator.NE,
                    expected=_literal(1),
                ),
            ),
        ),
        _closure_cutoff("data_snooping_oos_access_attempts", "OOS_ATTEMPT_BEFORE_CLOSURE"),
    ),
    verification_rules=(
        _rule(
            "oos_attempt_family_freeze_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "authority_instance_id",
                "family_id",
                "status",
                "frozen_config_set_hash",
                "selection_decision_id",
                "oos_exposure_id",
                "benchmark_contract_hash",
                "ordered_period_calendar_hash",
                "cost_contract_hash",
                "event_at_us",
            ),
            referenced_table="data_snooping_family_freezes",
        ),
        _rule(
            "oos_attempt_scope_outcome_policy",
            VerificationRuleKind.SELECTION_SCOPE,
            ("family_id", "selection_decision_id", "oos_exposure_id"),
            referenced_table="selection_scope_outcomes",
        ),
        _rule(
            "oos_attempt_closure_cutoff",
            VerificationRuleKind.FREEZE_CLOSURE_ASSESSMENT,
            ("family_id", "event_at_us"),
        ),
    ),
)

_C7_SIMPLE_EXTENSIONS = (
    _c7_standard_extension(
        _C7_EXTENSION_ORDER[2],
        "data_snooping_candidate_dispositions",
        "recorded_at_us",
        "CANDIDATE_DISPOSITION_REQUIRES_FREEZE",
        "CANDIDATE_DISPOSITION_BEFORE_CLOSURE",
        "candidate_disposition_freeze_closure_cutoff",
    ),
    _c7_standard_extension(
        _C7_EXTENSION_ORDER[3],
        "data_snooping_evaluation_batches",
        "committed_at_us",
        "EVALUATION_BATCH_REQUIRES_FREEZE",
        "EVALUATION_BATCH_BEFORE_CLOSURE",
        "evaluation_batch_freeze_closure_cutoff",
    ),
    _c7_standard_extension(
        _C7_EXTENSION_ORDER[4],
        "data_snooping_family_oos_evaluations",
        "linked_at_us",
        "FAMILY_OOS_EVALUATION_REQUIRES_FREEZE",
        "FAMILY_OOS_EVALUATION_BEFORE_CLOSURE",
        "family_oos_evaluation_freeze_closure_cutoff",
    ),
    _c7_standard_extension(
        _C7_EXTENSION_ORDER[5],
        "data_snooping_evaluation_receipts",
        "committed_at_us",
        "EVALUATION_RECEIPT_REQUIRES_FREEZE",
        "EVALUATION_RECEIPT_BEFORE_CLOSURE",
        "evaluation_receipt_freeze_closure_cutoff",
    ),
    _c7_standard_extension(
        _C7_EXTENSION_ORDER[6],
        "data_snooping_benchmark_returns",
        "committed_at_us",
        "BENCHMARK_RETURN_REQUIRES_FREEZE",
        "BENCHMARK_RETURN_BEFORE_CLOSURE",
        "benchmark_return_freeze_closure_cutoff",
    ),
)

C8_TABLE_EXTENSIONS = (
    _SEARCH_EXTENSION,
    _PROPOSAL_REGISTRATION_EXTENSION,
    _PROPOSAL_TERMINAL_RESULT_EXTENSION,
    _HYPOTHESIS_EXTENSION,
    _SEAL_EXTENSION,
    _RELEASE_EXTENSION,
    _ATTEMPT_EXTENSION,
    *_C7_SIMPLE_EXTENSIONS,
)


C8_REQUIRED_EXTENSION_SPECS = (
    RequiredTableExtensionSpec(
        extension_id="C6_DATA_SNOOPING_DECLARATION_EMBEDDED_REFS_V1",
        target_table="data_snooping_declarations",
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.declaration.embedded_refs",
    ),
    RequiredTableExtensionSpec(
        extension_id="C6_FAMILY_CLOSURE_EMBEDDED_REFS_V1",
        target_table="data_snooping_family_closures",
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.family_closure.embedded_refs",
    ),
)

_C6_DECLARATION_CONTRACT_FIELDS = (
    "observed_pit_authority_id",
    "observed_pit_handle_id",
    "observed_pit_content_hash",
    "knowledge_time_policy_id",
    "knowledge_time_policy_version",
    "observed_pit_authority_head",
    "code_manifest_policy_id",
    "code_manifest_policy_version",
    "code_manifest_policy_hash",
    "code_manifest_evidence_hash",
    "code_manifest_authority_id",
    "code_manifest_authority_head",
)
_C6_CLOSURE_CONTRACT_FIELDS = (
    "observed_pit_authority_id",
    "observed_pit_handle_id",
    "observed_pit_content_hash",
    "knowledge_time_policy_id",
    "knowledge_time_policy_version",
    "observed_pit_authority_head",
    "oos_exposure_id",
    "exposure_fingerprint",
    "exposure_authority_id",
    "exposure_authority_head",
    "code_manifest_policy_id",
    "code_manifest_policy_version",
    "code_manifest_policy_hash",
    "code_manifest_evidence_hash",
    "code_manifest_authority_id",
    "code_manifest_authority_head",
    "start_execution_attestation_hash",
    "terminal_execution_attestation_hash",
    "execution_attestation_authority_id",
    "start_execution_attestation_authority_head",
    "terminal_execution_attestation_authority_head",
    "immutable_artifact_lease_id",
    "immutable_artifact_lease_hash",
    "immutable_artifact_lease_authority_id",
    "immutable_artifact_lease_authority_head",
)

C8_REQUIRED_CONTRACT_SURFACES = (
    RequiredContractSurfaceSpec(
        contract_type="DataSnoopingDeclaration",
        field_names=_C6_DECLARATION_CONTRACT_FIELDS,
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.declaration.embedded_refs",
    ),
    RequiredContractSurfaceSpec(
        contract_type="DataSnoopingFamilyManifest",
        field_names=_C6_CLOSURE_CONTRACT_FIELDS,
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.family_closure.embedded_refs",
    ),
)

_CONTRACT_TABLES = {
    "DataSnoopingDeclaration": "data_snooping_declarations",
    "FamilyFreezeReceipt": "data_snooping_family_freezes",
    "TrialLedgerProof": "data_snooping_family_closures",
    "DataSnoopingFamilyManifest": "data_snooping_family_closures",
    "DataSnoopingGridAssessment": "data_snooping_grid_assessments",
    "DataSnoopingAssessment": "data_snooping_assessments",
}
_CANONICAL_PAYLOAD_FIELDS = {
    "DataSnoopingDeclaration": {
        "ordered_research_program_ids",
        "ordered_research_question_ids",
        "ordered_strategy_lineage_root_ids",
        "block_length_grid",
        "replication_schedule",
        "allowed_search_kinds",
        "generator_seeds",
        "search_proposal_budgets",
    },
    "FamilyFreezeReceipt": {
        "ordered_search_ids",
        "ordered_search_terminal_hashes",
        "ordered_emission_seal_hashes",
        "ordered_candidate_chain_heads",
    },
    "TrialLedgerProof": {
        "trial_ledger_authority_id",
        "history_artifact_id",
        "history_artifact_sha256",
        "history_head",
        "snapshot_artifact_id",
        "snapshot_artifact_sha256",
        "seal_artifact_id",
        "seal_artifact_sha256",
        "global_unique_config_count",
        "captured_at_us",
    },
    "DataSnoopingFamilyManifest": {
        "ordered_config_hashes",
        "ordered_evaluation_receipt_hashes",
        "trial_ledger_proof",
        "failure_reasons",
    },
    "DataSnoopingGridAssessment": {
        "reality_check_result",
        "spa_result",
        "failure_reasons",
    },
    "DataSnoopingAssessment": {
        "ordered_latest_grid_assessment_hashes",
        "failure_reasons",
    },
}
_TARGET_ALIASES = {
    ("DataSnoopingDeclaration", "ordered_research_program_ids"): "ordered_research_program_ids_json",
    ("DataSnoopingDeclaration", "ordered_research_question_ids"): "ordered_research_question_ids_json",
    (
        "DataSnoopingDeclaration",
        "ordered_strategy_lineage_root_ids",
    ): "ordered_strategy_lineage_root_ids_json",
    ("DataSnoopingDeclaration", "block_length_grid"): "block_length_grid_json",
    ("DataSnoopingDeclaration", "replication_schedule"): "replication_schedule_json",
    ("DataSnoopingDeclaration", "allowed_search_kinds"): "allowed_search_kinds_json",
    ("DataSnoopingDeclaration", "generator_seeds"): "generator_seeds_json",
    ("DataSnoopingDeclaration", "search_proposal_budgets"): "search_proposal_budgets_json",
    ("DataSnoopingDeclaration", "declaration_record_hash"): "record_hash",
    ("FamilyFreezeReceipt", "ordered_search_ids"): "ordered_search_ids_json",
    ("FamilyFreezeReceipt", "ordered_search_terminal_hashes"): "ordered_search_terminal_hashes_json",
    ("FamilyFreezeReceipt", "ordered_emission_seal_hashes"): "ordered_emission_seal_hashes_json",
    ("FamilyFreezeReceipt", "ordered_candidate_chain_heads"): "ordered_candidate_chain_heads_json",
    ("FamilyFreezeReceipt", "freeze_record_hash"): "record_hash",
    ("TrialLedgerProof", "proof_hash"): "trial_ledger_proof_hash",
    ("DataSnoopingFamilyManifest", "ordered_config_hashes"): "ordered_config_hashes_json",
    (
        "DataSnoopingFamilyManifest",
        "ordered_evaluation_receipt_hashes",
    ): "ordered_evaluation_receipt_hashes_json",
    ("DataSnoopingFamilyManifest", "trial_ledger_proof"): "trial_ledger_proof_json",
    ("DataSnoopingFamilyManifest", "failure_reasons"): "failure_reasons_json",
    ("DataSnoopingFamilyManifest", "closure_manifest_hash"): "record_hash",
    ("DataSnoopingGridAssessment", "previous_grid_assessment_hash"): "prev_record_hash",
    ("DataSnoopingGridAssessment", "reality_check_result"): "reality_check_payload_json",
    ("DataSnoopingGridAssessment", "spa_result"): "spa_payload_json",
    ("DataSnoopingGridAssessment", "failure_reasons"): "failure_reasons_json",
    ("DataSnoopingGridAssessment", "grid_assessment_record_hash"): "record_hash",
    (
        "DataSnoopingAssessment",
        "ordered_latest_grid_assessment_hashes",
    ): "ordered_latest_grid_assessment_hashes_json",
    ("DataSnoopingAssessment", "failure_reasons"): "failure_reasons_json",
    ("DataSnoopingAssessment", "assessment_record_hash"): "record_hash",
}
_REQUIRED_FIELDS_BY_CONTRACT = {
    "DataSnoopingDeclaration": set(_C6_DECLARATION_CONTRACT_FIELDS),
    "DataSnoopingFamilyManifest": set(_C6_CLOSURE_CONTRACT_FIELDS),
}


def _coverage_for(contract_type: str) -> tuple[ContractFieldCoverageSpec, ...]:
    table = _CONTRACT_TABLES[contract_type]
    contract = getattr(_research_contracts, contract_type)
    result: list[ContractFieldCoverageSpec] = []
    for field_name in contract.model_fields:
        if field_name in _REQUIRED_FIELDS_BY_CONTRACT.get(contract_type, set()):
            continue
        if contract_type == "DataSnoopingFamilyManifest" and field_name == "authority_head":
            result.append(
                ContractFieldCoverageSpec(
                    contract_type=contract_type,
                    field_name=field_name,
                    kind=CoverageKind.NON_PERSISTED_DERIVATION,
                    derivation_id="c8.closure.authority_head",
                )
            )
            continue
        target = _TARGET_ALIASES.get((contract_type, field_name), field_name)
        if contract_type == "TrialLedgerProof" and field_name != "proof_hash":
            target = "trial_ledger_proof_json"
        result.append(
            ContractFieldCoverageSpec(
                contract_type=contract_type,
                field_name=field_name,
                kind=(
                    CoverageKind.CANONICAL_PAYLOAD
                    if field_name in _CANONICAL_PAYLOAD_FIELDS.get(contract_type, set())
                    else CoverageKind.COLUMN
                ),
                table_name=table,
                target_field=target,
            )
        )
    return tuple(result)


C8_CONTRACT_COVERAGE = tuple(item for contract_type in _CONTRACT_TABLES for item in _coverage_for(contract_type))

C8_CONTRACT_DERIVATIONS = (
    ContractFieldDerivationSpec(
        derivation_id="c8.closure.authority_head",
        contract_type="DataSnoopingFamilyManifest",
        field_name="authority_head",
        kind=ContractDerivationKind.IMMUTABLE_ROW_LOOKUP,
        binding_table="data_snooping_family_closures",
        binding_field="record_hash",
        source_table="data_snooping_family_closures",
        source_key_field="record_hash",
        source_value_field="record_hash",
    ),
)

C8_SCHEMA_COMPONENT = SchemaComponentSpec(
    component_id=_COMPONENT_ID,
    capabilities=_CAPABILITIES,
    dependencies=(
        ComponentDependencySpec(
            component_id=_C4_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=(
                "c4.questions_lineage_scope_decisions_exposures.core",
                "c4.research_program.core",
                "c4.global_oos_exposure.core",
            ),
        ),
        ComponentDependencySpec(
            component_id=_C5_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=(
                "c5.proposals_hypotheses.dispositions",
                "c5.family_search.core",
                "c5.proposal_registration.core",
                "c5.proposal.core",
                "c5.hypothesis.core",
                "c5.emission_seal.core",
            ),
        ),
        ComponentDependencySpec(
            component_id=_C7_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=(
                "c7.one_shot_oos.core",
                "c7.oos_release.core",
                "c7.oos_attempt.event_stream",
                "c7.candidate_disposition.core",
                "c7.family_evaluation_batch.atomic",
                "c7.series.exact_reconciliation",
            ),
        ),
    ),
    tables=C8_AUTHORITY_TABLE_SPECS,
    extensions=C8_TABLE_EXTENSIONS,
    required_extensions=C8_REQUIRED_EXTENSION_SPECS,
    coverage=C8_CONTRACT_COVERAGE,
    contract_derivations=C8_CONTRACT_DERIVATIONS,
    required_contract_surfaces=C8_REQUIRED_CONTRACT_SURFACES,
)
