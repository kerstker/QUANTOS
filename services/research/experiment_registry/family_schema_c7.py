"""Private Pass-C schema AST for the C7 one-shot OOS evidence slice.

The values in this module are declarations only.  They do not render SQL,
open a database, activate schema v3, read OOS data, or implement evaluation
services.  Pass M remains responsible for composing and sealing all slices.
"""

from __future__ import annotations

from .family_schema_spec import (
    AuthorityTableExtensionSpec,
    AuthorityTableSpec,
    ChainGenesisRule,
    ChainHeadRule,
    ChainSpec,
    CheckConstraintSpec,
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
    InsertStateMachineSpec,
    InsertStateTransition,
    LiteralValue,
    LogicalExpression,
    LogicalOperator,
    LookupBinding,
    MembershipExpression,
    NullExpression,
    NullOperator,
    PredicateExpression,
    RelatedValueLookupSpec,
    RelationAggregateExpression,
    RelationAggregateKind,
    RelationAggregateOrderSpec,
    RelationExistsExpression,
    RequiredContractSurfaceSpec,
    RequiredTableExtensionSpec,
    RowHashSpec,
    RowSource,
    SchemaComponentSpec,
    SqlType,
    StructuralVerificationSourceKind,
    StructuralVerificationSourceSpec,
    TriggerEvent,
    TriggerPolicyKind,
    TriggerPolicySpec,
    VerificationRuleKind,
    VerificationRuleSpec,
)


_COMPONENT_ID = "C7_ONE_SHOT_OOS_V1"
_C4_COMPONENT = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
_C5_COMPONENT = "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1"
_C6_COMPONENT = "C6_EXTERNAL_AUTHORITY_REFS_V1"
_C8_COMPONENT = "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1"

_CAPABILITIES = (
    "c7.one_shot_oos.core",
    "c7.oos_release.core",
    "c7.oos_attempt.event_stream",
    "c7.candidate_disposition.core",
    "c7.family_evaluation_batch.atomic",
    "c7.series.exact_reconciliation",
    "c7.global_oos_consumption.attempt_binding",
)

_TABLE_ORDER = (
    "data_snooping_oos_releases",
    "data_snooping_oos_access_attempts",
    "data_snooping_candidate_dispositions",
    "data_snooping_evaluation_batches",
    "data_snooping_family_oos_evaluations",
    "data_snooping_evaluation_receipts",
    "data_snooping_benchmark_returns",
)

_RECORD_TYPES = {
    "data_snooping_oos_releases": "DATA_SNOOPING_OOS_RELEASE_V1",
    "data_snooping_oos_access_attempts": ("DATA_SNOOPING_OOS_ACCESS_ATTEMPT_EVENT_V1"),
    "data_snooping_candidate_dispositions": ("DATA_SNOOPING_CANDIDATE_DISPOSITION_V1"),
    "data_snooping_evaluation_batches": "DATA_SNOOPING_EVALUATION_BATCH_V1",
    "data_snooping_family_oos_evaluations": ("DATA_SNOOPING_FAMILY_OOS_EVALUATION_V1"),
    "data_snooping_evaluation_receipts": ("DATA_SNOOPING_EVALUATION_RECEIPT_V1"),
    "data_snooping_benchmark_returns": "DATA_SNOOPING_BENCHMARK_RETURN_V1",
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
    "data_snooping_oos_releases": _columns(
        _column("oos_release_id"),
        _column("family_id"),
        _column("attempt_id"),
        _column("started_attempt_event_id"),
        _column("family_freeze_record_hash"),
        _column("exposure_consumption_record_hash"),
        _column("oos_exposure_id"),
        _column("released_at_us", SqlType.INTEGER),
    ),
    "data_snooping_oos_access_attempts": _columns(
        _column("attempt_event_id"),
        _column("attempt_id"),
        _column("family_id"),
        _column("selection_decision_id"),
        _column("oos_exposure_id"),
        _column("evaluation_batch_id"),
        _column("sequence_no", SqlType.INTEGER),
        _column("status"),
        _column("frozen_config_set_hash"),
        _column("benchmark_contract_hash"),
        _column("ordered_period_calendar_hash"),
        _column("cost_contract_hash"),
        _column("start_execution_attestation_hash"),
        _column("terminal_execution_attestation_hash", nullable=True),
        _column("immutable_artifact_lease_hash"),
        _column("evaluation_batch_receipt_hash", nullable=True),
        _column("reason_code", nullable=True),
        _column("event_at_us", SqlType.INTEGER),
        _column("prev_record_hash"),
    ),
    "data_snooping_candidate_dispositions": _columns(
        _column("family_id"),
        _column("proposal_id"),
        _column("disposition"),
        _column("config_hash", nullable=True),
        _column("evaluation_receipt_hash", nullable=True),
        _column("quarantine_record_id", nullable=True),
        _column("reason_code", nullable=True),
        _column("performance_accessed", SqlType.INTEGER),
        _column("recorded_at_us", SqlType.INTEGER),
    ),
    "data_snooping_evaluation_batches": _columns(
        _column("evaluation_batch_id"),
        _column("family_id"),
        _column("attempt_id"),
        _column("frozen_config_set_hash"),
        _column("candidate_count", SqlType.INTEGER),
        _column("ordered_candidate_config_hashes_hash"),
        _column("ordered_candidate_receipt_hashes_hash"),
        _column("benchmark_receipt_hash"),
        _column("ordered_period_keys_hash"),
        _column("cost_contract_hash"),
        _column("start_execution_attestation_hash"),
        _column("terminal_execution_attestation_hash"),
        _column("immutable_artifact_lease_hash"),
        _column("committed_at_us", SqlType.INTEGER),
    ),
    "data_snooping_family_oos_evaluations": _columns(
        _column("family_id"),
        _column("evaluation_batch_id"),
        _column("config_hash"),
        _column("alpha_id"),
        _column("source_search_id"),
        _column("source_proposal_id"),
        _column("evaluation_run_id"),
        _column("evaluation_content_hash"),
        _column("evaluation_audit_intent_hash"),
        _column("evaluation_receipt_hash"),
        _column("linked_at_us", SqlType.INTEGER),
    ),
    "data_snooping_evaluation_receipts": _columns(
        _column("evaluation_receipt_hash"),
        _column("family_id"),
        _column("evaluation_batch_id"),
        _column("config_hash"),
        _column("series_payload_json"),
        _column("ordered_period_keys_hash"),
        _column("gross_returns_hash"),
        _column("costs_hash"),
        _column("net_returns_hash"),
        _column("turnovers_hash"),
        _column("cost_contract_hash"),
        _column("cost_model_input_hash"),
        _column("series_hash"),
        _column("start_execution_attestation_hash"),
        _column("terminal_execution_attestation_hash"),
        _column("immutable_artifact_lease_hash"),
        _column("committed_at_us", SqlType.INTEGER),
    ),
    "data_snooping_benchmark_returns": _columns(
        _column("benchmark_receipt_hash"),
        _column("family_id"),
        _column("evaluation_batch_id"),
        _column("benchmark_id"),
        _column("benchmark_contract_hash"),
        _column("series_payload_json"),
        _column("ordered_period_keys_hash"),
        _column("gross_returns_hash"),
        _column("costs_hash"),
        _column("net_returns_hash"),
        _column("turnovers_hash"),
        _column("cost_contract_hash"),
        _column("cost_model_input_hash"),
        _column("series_hash"),
        _column("start_execution_attestation_hash"),
        _column("terminal_execution_attestation_hash"),
        _column("immutable_artifact_lease_hash"),
        _column("committed_at_us", SqlType.INTEGER),
    ),
}

_PRIMARY_KEYS = {
    "data_snooping_oos_releases": ("oos_release_id",),
    "data_snooping_oos_access_attempts": ("attempt_event_id",),
    "data_snooping_candidate_dispositions": ("family_id", "proposal_id"),
    "data_snooping_evaluation_batches": ("evaluation_batch_id",),
    "data_snooping_family_oos_evaluations": ("family_id", "config_hash"),
    "data_snooping_evaluation_receipts": ("evaluation_receipt_hash",),
    "data_snooping_benchmark_returns": ("benchmark_receipt_hash",),
}


def _foreign_key(
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
    *,
    deferred: bool = False,
) -> ForeignKeySpec:
    return ForeignKeySpec(
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
        on_update=ForeignKeyAction.RESTRICT,
        on_delete=ForeignKeyAction.RESTRICT,
        deferrability=(
            ForeignKeyDeferrability.DEFERRABLE_INITIALLY_DEFERRED
            if deferred
            else ForeignKeyDeferrability.NOT_DEFERRABLE
        ),
    )


def _genesis_foreign_key() -> ForeignKeySpec:
    return _foreign_key(
        ("authority_instance_id", "genesis_record_hash"),
        "family_authority_genesis",
        ("authority_instance_id", "record_hash"),
    )


_FOREIGN_KEYS = {
    "data_snooping_oos_releases": (
        _genesis_foreign_key(),
        _foreign_key(
            ("oos_exposure_id",),
            "global_oos_exposures",
            ("oos_exposure_id",),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "attempt_id",
                "started_attempt_event_id",
                "family_id",
                "oos_exposure_id",
            ),
            "data_snooping_oos_access_attempts",
            (
                "authority_instance_id",
                "attempt_id",
                "attempt_event_id",
                "family_id",
                "oos_exposure_id",
            ),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "oos_exposure_id",
                "family_id",
                "attempt_id",
                "exposure_consumption_record_hash",
            ),
            "global_oos_exposure_consumptions",
            (
                "authority_instance_id",
                "oos_exposure_id",
                "family_id",
                "access_attempt_id",
                "record_hash",
            ),
        ),
    ),
    "data_snooping_oos_access_attempts": (
        _genesis_foreign_key(),
        _foreign_key(
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
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
            deferred=True,
        ),
    ),
    "data_snooping_candidate_dispositions": (
        _genesis_foreign_key(),
        _foreign_key(
            ("proposal_id",),
            "data_snooping_proposal_registrations",
            ("proposal_id",),
        ),
        _foreign_key(
            ("family_id", "config_hash"),
            "data_snooping_hypotheses",
            ("family_id", "config_hash"),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "family_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
            "data_snooping_evaluation_receipts",
            (
                "authority_instance_id",
                "family_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
        ),
    ),
    "data_snooping_evaluation_batches": (
        _genesis_foreign_key(),
        _foreign_key(
            (
                "authority_instance_id",
                "attempt_id",
                "evaluation_batch_id",
                "family_id",
                "record_hash",
            ),
            "data_snooping_oos_access_attempts",
            (
                "authority_instance_id",
                "attempt_id",
                "evaluation_batch_id",
                "family_id",
                "evaluation_batch_receipt_hash",
            ),
            deferred=True,
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
            deferred=True,
        ),
    ),
    "data_snooping_family_oos_evaluations": (
        _genesis_foreign_key(),
        _foreign_key(
            ("family_id", "config_hash"),
            "data_snooping_hypotheses",
            ("family_id", "config_hash"),
        ),
        _foreign_key(
            ("family_id", "source_search_id"),
            "data_snooping_family_searches",
            ("family_id", "search_id"),
        ),
        _foreign_key(
            ("source_proposal_id",),
            "data_snooping_proposals",
            ("proposal_id",),
        ),
        _foreign_key(("evaluation_run_id",), "evaluations", ("run_id",)),
        _foreign_key(
            ("evaluation_audit_intent_hash",),
            "evaluation_audit_outbox",
            ("record_hash",),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
            "data_snooping_evaluation_receipts",
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
            deferred=True,
        ),
    ),
    "data_snooping_evaluation_receipts": (
        _genesis_foreign_key(),
        _foreign_key(
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            "data_snooping_evaluation_batches",
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            deferred=True,
        ),
        _foreign_key(
            ("family_id", "config_hash"),
            "data_snooping_hypotheses",
            ("family_id", "config_hash"),
        ),
        _foreign_key(
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
            "data_snooping_family_oos_evaluations",
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
            deferred=True,
        ),
    ),
    "data_snooping_benchmark_returns": (
        _genesis_foreign_key(),
        _foreign_key(
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            "data_snooping_evaluation_batches",
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            deferred=True,
        ),
    ),
}


def _comparison(
    column: str,
    operator: ComparisonOperator,
    value: None | int | float | str | bytes,
    *,
    source: RowSource = RowSource.CURRENT,
) -> ComparisonExpression:
    return ComparisonExpression(ColumnValue(column, source), operator, LiteralValue(value))


def _column_comparison(
    left: str,
    operator: ComparisonOperator,
    right: str,
    *,
    left_source: RowSource = RowSource.NEW,
    right_source: RowSource = RowSource.RELATED,
) -> ComparisonExpression:
    return ComparisonExpression(
        ColumnValue(left, left_source),
        operator,
        ColumnValue(right, right_source),
    )


def _logical(
    operator: LogicalOperator,
    *operands: PredicateExpression,
) -> LogicalExpression:
    return LogicalExpression(operator, operands)


def _aggregate_expected(
    value: str,
    bindings: tuple[tuple[str, str], ...],
) -> RelatedValueLookupSpec:
    return RelatedValueLookupSpec(
        table="data_snooping_evaluation_batches",
        bindings=tuple(LookupBinding(related, ColumnValue(local, RowSource.NEW)) for local, related in bindings),
        value=value,
    )


def _aggregate(
    table: str,
    bindings: tuple[tuple[str, str], ...],
    aggregate: RelationAggregateKind,
    *,
    value: str | None,
    expected: LiteralValue | RelatedValueLookupSpec,
    order: tuple[str, ...] = (),
) -> RelationAggregateExpression:
    return RelationAggregateExpression(
        table=table,
        bindings=tuple(LookupBinding(related, ColumnValue(local, RowSource.NEW)) for local, related in bindings),
        aggregate=aggregate,
        source=RowSource.RELATED,
        value=value,
        order=tuple(RelationAggregateOrderSpec(column) for column in order),
        operator=ComparisonOperator.NE,
        expected=expected,
    )


def _null(
    column: str,
    operator: NullOperator,
    *,
    source: RowSource = RowSource.CURRENT,
) -> NullExpression:
    return NullExpression(ColumnValue(column, source), operator)


def _membership(column: str, *values: int | str) -> MembershipExpression:
    return MembershipExpression(ColumnValue(column), tuple(LiteralValue(value) for value in values))


def _index(
    table: str,
    suffix: str,
    kind: str,
    columns: tuple[str, ...],
    *,
    predicate: tuple[str, int] | None = None,
) -> IndexSpec:
    return IndexSpec(
        name=f"{kind.lower()}_{table}_{suffix}",
        columns=tuple(IndexColumnSpec(column) for column in columns),
        unique=kind in {"UNIQUE", "PARTIAL_UNIQUE"},
        predicate=(_comparison(predicate[0], ComparisonOperator.EQ, predicate[1]) if predicate is not None else None),
    )


_INDEXES = {
    "data_snooping_oos_releases": (
        _index("data_snooping_oos_releases", "record_hash", "UNIQUE", ("record_hash",)),
        _index("data_snooping_oos_releases", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _index("data_snooping_oos_releases", "attempt", "UNIQUE", ("authority_instance_id", "attempt_id")),
        _index("data_snooping_oos_releases", "exposure", "UNIQUE", ("authority_instance_id", "oos_exposure_id")),
        _index(
            "data_snooping_oos_releases",
            "exact_binding",
            "UNIQUE",
            ("authority_instance_id", "family_id", "attempt_id", "oos_exposure_id"),
        ),
    ),
    "data_snooping_oos_access_attempts": (
        _index("data_snooping_oos_access_attempts", "record_hash", "UNIQUE", ("record_hash",)),
        _index(
            "data_snooping_oos_access_attempts",
            "attempt_sequence",
            "UNIQUE",
            ("authority_instance_id", "attempt_id", "sequence_no"),
        ),
        _index(
            "data_snooping_oos_access_attempts",
            "family_start",
            "PARTIAL_UNIQUE",
            ("authority_instance_id", "family_id"),
            predicate=("sequence_no", 0),
        ),
        _index(
            "data_snooping_oos_access_attempts",
            "terminal",
            "PARTIAL_UNIQUE",
            ("authority_instance_id", "attempt_id"),
            predicate=("sequence_no", 1),
        ),
        _index(
            "data_snooping_oos_access_attempts",
            "started_event_tuple",
            "UNIQUE",
            ("authority_instance_id", "attempt_id", "attempt_event_id", "family_id", "oos_exposure_id"),
        ),
        _index(
            "data_snooping_oos_access_attempts",
            "terminal_batch_tuple",
            "UNIQUE",
            (
                "authority_instance_id",
                "attempt_id",
                "evaluation_batch_id",
                "family_id",
                "evaluation_batch_receipt_hash",
            ),
        ),
        _index(
            "data_snooping_oos_access_attempts",
            "exposure_tuple",
            "ORDINARY",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
    ),
    "data_snooping_candidate_dispositions": (
        _index("data_snooping_candidate_dispositions", "record_hash", "UNIQUE", ("record_hash",)),
        _index("data_snooping_candidate_dispositions", "proposal", "ORDINARY", ("family_id", "proposal_id")),
        _index("data_snooping_candidate_dispositions", "hypothesis", "ORDINARY", ("family_id", "config_hash")),
        _index(
            "data_snooping_candidate_dispositions",
            "receipt",
            "ORDINARY",
            ("authority_instance_id", "family_id", "config_hash", "evaluation_receipt_hash"),
        ),
    ),
    "data_snooping_evaluation_batches": (
        _index("data_snooping_evaluation_batches", "record_hash", "UNIQUE", ("record_hash",)),
        _index("data_snooping_evaluation_batches", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _index("data_snooping_evaluation_batches", "attempt", "UNIQUE", ("authority_instance_id", "attempt_id")),
        _index(
            "data_snooping_evaluation_batches",
            "attempt_receipt_tuple",
            "UNIQUE",
            ("authority_instance_id", "evaluation_batch_id", "family_id", "attempt_id", "record_hash"),
        ),
        _index(
            "data_snooping_evaluation_batches",
            "batch_family",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
        ),
        _index(
            "data_snooping_evaluation_batches",
            "benchmark",
            "ORDINARY",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "benchmark_receipt_hash"),
        ),
    ),
    "data_snooping_family_oos_evaluations": (
        _index("data_snooping_family_oos_evaluations", "record_hash", "UNIQUE", ("record_hash",)),
        _index(
            "data_snooping_family_oos_evaluations",
            "exact_receipt_link",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "config_hash", "evaluation_receipt_hash"),
        ),
        _index("data_snooping_family_oos_evaluations", "family_run", "UNIQUE", ("family_id", "evaluation_run_id")),
        _index(
            "data_snooping_family_oos_evaluations",
            "source",
            "ORDINARY",
            ("family_id", "source_search_id", "source_proposal_id"),
        ),
    ),
    "data_snooping_evaluation_receipts": (
        _index("data_snooping_evaluation_receipts", "record_hash", "UNIQUE", ("record_hash",)),
        _index("data_snooping_evaluation_receipts", "family_config", "UNIQUE", ("family_id", "config_hash")),
        _index(
            "data_snooping_evaluation_receipts",
            "batch_config",
            "UNIQUE",
            ("authority_instance_id", "evaluation_batch_id", "config_hash"),
        ),
        _index(
            "data_snooping_evaluation_receipts",
            "exact_receipt_link",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "config_hash", "evaluation_receipt_hash"),
        ),
        _index(
            "data_snooping_evaluation_receipts",
            "disposition_receipt",
            "UNIQUE",
            ("authority_instance_id", "family_id", "config_hash", "evaluation_receipt_hash"),
        ),
    ),
    "data_snooping_benchmark_returns": (
        _index("data_snooping_benchmark_returns", "record_hash", "UNIQUE", ("record_hash",)),
        _index("data_snooping_benchmark_returns", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _index("data_snooping_benchmark_returns", "batch", "UNIQUE", ("authority_instance_id", "evaluation_batch_id")),
        _index(
            "data_snooping_benchmark_returns",
            "exact_batch_benchmark",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "benchmark_receipt_hash"),
        ),
    ),
}


def _record_type_check(table: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{table}_record_type",
        _comparison("record_type", ComparisonOperator.EQ, _RECORD_TYPES[table]),
    )


def _positive_time_check(table: str, column: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{table}_positive_time",
        _comparison(column, ComparisonOperator.GT, 0),
    )


_ATTEMPT_BRANCH_CHECK = _logical(
    LogicalOperator.OR,
    _logical(
        LogicalOperator.AND,
        _comparison("status", ComparisonOperator.EQ, "STARTED"),
        _comparison("sequence_no", ComparisonOperator.EQ, 0),
        _null("terminal_execution_attestation_hash", NullOperator.IS_NULL),
        _null("evaluation_batch_receipt_hash", NullOperator.IS_NULL),
        _null("reason_code", NullOperator.IS_NULL),
        _column_comparison(
            "prev_record_hash",
            ComparisonOperator.EQ,
            "genesis_record_hash",
            left_source=RowSource.CURRENT,
            right_source=RowSource.CURRENT,
        ),
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("status", ComparisonOperator.EQ, "COMPLETED"),
        _comparison("sequence_no", ComparisonOperator.EQ, 1),
        _null("terminal_execution_attestation_hash", NullOperator.IS_NOT_NULL),
        _null("evaluation_batch_receipt_hash", NullOperator.IS_NOT_NULL),
        _null("reason_code", NullOperator.IS_NULL),
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("status", ComparisonOperator.EQ, "INCOMPLETE"),
        _comparison("sequence_no", ComparisonOperator.EQ, 1),
        _null("evaluation_batch_receipt_hash", NullOperator.IS_NULL),
        _null("reason_code", NullOperator.IS_NOT_NULL),
    ),
)


_DISPOSITION_BRANCH_CHECK = _logical(
    LogicalOperator.OR,
    _logical(
        LogicalOperator.AND,
        _comparison("disposition", ComparisonOperator.EQ, "PERFORMANCE_EVALUATED"),
        _null("config_hash", NullOperator.IS_NOT_NULL),
        _null("evaluation_receipt_hash", NullOperator.IS_NOT_NULL),
        _null("quarantine_record_id", NullOperator.IS_NULL),
        _null("reason_code", NullOperator.IS_NULL),
        _comparison("performance_accessed", ComparisonOperator.EQ, 1),
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("disposition", ComparisonOperator.EQ, "PERFORMANCE_ATTEMPTED_INCOMPLETE"),
        _null("config_hash", NullOperator.IS_NOT_NULL),
        _null("evaluation_receipt_hash", NullOperator.IS_NULL),
        _null("quarantine_record_id", NullOperator.IS_NULL),
        _null("reason_code", NullOperator.IS_NOT_NULL),
        _comparison("performance_accessed", ComparisonOperator.EQ, 1),
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("disposition", ComparisonOperator.EQ, "PRE_EVALUATION_INVALID"),
        _null("config_hash", NullOperator.IS_NULL),
        _null("evaluation_receipt_hash", NullOperator.IS_NULL),
        _null("quarantine_record_id", NullOperator.IS_NULL),
        _null("reason_code", NullOperator.IS_NOT_NULL),
        _comparison("performance_accessed", ComparisonOperator.EQ, 0),
    ),
    _logical(
        LogicalOperator.AND,
        _comparison("disposition", ComparisonOperator.EQ, "SECRET_QUARANTINED"),
        _null("config_hash", NullOperator.IS_NULL),
        _null("evaluation_receipt_hash", NullOperator.IS_NULL),
        _null("quarantine_record_id", NullOperator.IS_NOT_NULL),
        _null("reason_code", NullOperator.IS_NOT_NULL),
        _comparison("performance_accessed", ComparisonOperator.EQ, 0),
    ),
)


_CHECKS = {
    "data_snooping_oos_releases": (
        _record_type_check("data_snooping_oos_releases"),
        _positive_time_check("data_snooping_oos_releases", "released_at_us"),
    ),
    "data_snooping_oos_access_attempts": (
        _record_type_check("data_snooping_oos_access_attempts"),
        _positive_time_check("data_snooping_oos_access_attempts", "event_at_us"),
        CheckConstraintSpec(
            "data_snooping_oos_access_attempts_sequence_domain",
            _membership("sequence_no", 0, 1),
        ),
        CheckConstraintSpec(
            "data_snooping_oos_access_attempts_status_domain",
            _membership("status", "STARTED", "COMPLETED", "INCOMPLETE"),
        ),
        CheckConstraintSpec(
            "data_snooping_oos_access_attempts_status_sequence_branch",
            _ATTEMPT_BRANCH_CHECK,
        ),
    ),
    "data_snooping_candidate_dispositions": (
        _record_type_check("data_snooping_candidate_dispositions"),
        _positive_time_check("data_snooping_candidate_dispositions", "recorded_at_us"),
        CheckConstraintSpec(
            "data_snooping_candidate_dispositions_performance_accessed_domain",
            _membership("performance_accessed", 0, 1),
        ),
        CheckConstraintSpec(
            "data_snooping_candidate_dispositions_disposition_domain",
            _membership(
                "disposition",
                "PERFORMANCE_EVALUATED",
                "PERFORMANCE_ATTEMPTED_INCOMPLETE",
                "PRE_EVALUATION_INVALID",
                "SECRET_QUARANTINED",
            ),
        ),
        CheckConstraintSpec(
            "data_snooping_candidate_dispositions_exact_branch",
            _DISPOSITION_BRANCH_CHECK,
        ),
        CheckConstraintSpec(
            "data_snooping_candidate_dispositions_incomplete_reason_domain",
            _logical(
                LogicalOperator.OR,
                _comparison(
                    "disposition",
                    ComparisonOperator.NE,
                    "PERFORMANCE_ATTEMPTED_INCOMPLETE",
                ),
                _membership(
                    "reason_code",
                    "EVALUATION_EXCEPTION",
                    "EVALUATION_TIMEOUT",
                    "ATOMIC_COMMIT_FAILED",
                    "ARTIFACT_LEASE_INVALID",
                    "EXECUTION_ATTESTATION_MISMATCH",
                    "RECOVERY_INCOMPLETE",
                ),
            ),
        ),
        CheckConstraintSpec(
            "data_snooping_candidate_dispositions_pre_evaluation_reason_domain",
            _logical(
                LogicalOperator.OR,
                _comparison(
                    "disposition",
                    ComparisonOperator.NE,
                    "PRE_EVALUATION_INVALID",
                ),
                _membership(
                    "reason_code",
                    "INVALID_UTF8",
                    "MALFORMED_JSON",
                    "DUPLICATE_JSON_KEY",
                    "NON_NFC_TEXT",
                    "SCHEMA_INVALID",
                    "TYPE_INVALID",
                    "GUARDRAIL_REJECTED",
                ),
            ),
        ),
        CheckConstraintSpec(
            "data_snooping_candidate_dispositions_quarantine_reason_domain",
            _logical(
                LogicalOperator.OR,
                _comparison(
                    "disposition",
                    ComparisonOperator.NE,
                    "SECRET_QUARANTINED",
                ),
                _membership(
                    "reason_code",
                    "DLP_SECRET_DETECTED",
                    "DLP_SCANNER_UNAVAILABLE",
                    "DLP_SCANNER_ERROR",
                ),
            ),
        ),
    ),
    "data_snooping_evaluation_batches": (
        _record_type_check("data_snooping_evaluation_batches"),
        _positive_time_check("data_snooping_evaluation_batches", "committed_at_us"),
        CheckConstraintSpec(
            "data_snooping_evaluation_batches_positive_candidate_count",
            _comparison("candidate_count", ComparisonOperator.GT, 0),
        ),
    ),
    "data_snooping_family_oos_evaluations": (
        _record_type_check("data_snooping_family_oos_evaluations"),
        _positive_time_check("data_snooping_family_oos_evaluations", "linked_at_us"),
    ),
    "data_snooping_evaluation_receipts": (
        _record_type_check("data_snooping_evaluation_receipts"),
        _positive_time_check("data_snooping_evaluation_receipts", "committed_at_us"),
    ),
    "data_snooping_benchmark_returns": (
        _record_type_check("data_snooping_benchmark_returns"),
        _positive_time_check("data_snooping_benchmark_returns", "committed_at_us"),
    ),
}


def _relation(
    table: str,
    bindings: tuple[tuple[str, str], ...],
    *,
    predicate: PredicateExpression | None = None,
    negated: bool = False,
) -> RelationExistsExpression:
    return RelationExistsExpression(
        table=table,
        bindings=tuple(
            LookupBinding(
                related_column=related,
                value=ColumnValue(local, RowSource.NEW),
            )
            for local, related in bindings
        ),
        predicate=predicate,
        negated=negated,
    )


def _related_comparison(
    related_column: str,
    operator: ComparisonOperator,
    new_column: str,
) -> ComparisonExpression:
    return ComparisonExpression(
        ColumnValue(related_column, RowSource.RELATED),
        operator,
        ColumnValue(new_column, RowSource.NEW),
    )


def _immutable_triggers(table: str) -> tuple[TriggerPolicySpec, ...]:
    return (
        TriggerPolicySpec(
            name=f"{table}_no_update",
            kind=TriggerPolicyKind.NO_UPDATE,
            event=TriggerEvent.UPDATE,
            error_code="AUTHORITY_ROW_IMMUTABLE",
        ),
        TriggerPolicySpec(
            name=f"{table}_no_delete",
            kind=TriggerPolicyKind.NO_DELETE,
            event=TriggerEvent.DELETE,
            error_code="AUTHORITY_ROW_IMMUTABLE",
        ),
    )


def _guard(
    table: str,
    error_code: str,
    violation_when: PredicateExpression,
    *,
    state_machine: InsertStateMachineSpec | None = None,
) -> TriggerPolicySpec:
    return TriggerPolicySpec(
        name=f"{table}_{error_code.lower()}",
        kind=TriggerPolicyKind.INSERT_GUARD,
        event=TriggerEvent.INSERT,
        error_code=error_code,
        violation_when=violation_when,
        state_machine=state_machine,
    )


_ATTEMPT_IDENTITY_COLUMNS = (
    "authority_instance_id",
    "genesis_record_hash",
    "attempt_id",
    "family_id",
    "selection_decision_id",
    "oos_exposure_id",
    "evaluation_batch_id",
    "frozen_config_set_hash",
    "benchmark_contract_hash",
    "ordered_period_calendar_hash",
    "cost_contract_hash",
    "start_execution_attestation_hash",
    "immutable_artifact_lease_hash",
)

_ATTEMPT_STATE_MACHINE = InsertStateMachineSpec(
    state_column="status",
    initial_states=("STARTED",),
    transitions=(
        InsertStateTransition("STARTED", "COMPLETED"),
        InsertStateTransition("STARTED", "INCOMPLETE"),
    ),
    terminal_states=("COMPLETED", "INCOMPLETE"),
    sequence_column="sequence_no",
    identity_columns=_ATTEMPT_IDENTITY_COLUMNS,
)


_IS_TERMINAL_NEW = MembershipExpression(
    ColumnValue("status", RowSource.NEW),
    (LiteralValue("COMPLETED"), LiteralValue("INCOMPLETE")),
)
_IS_STARTED_NEW = ComparisonExpression(
    ColumnValue("status", RowSource.NEW),
    ComparisonOperator.EQ,
    LiteralValue("STARTED"),
)
_IS_COMPLETED_NEW = ComparisonExpression(
    ColumnValue("status", RowSource.NEW),
    ComparisonOperator.EQ,
    LiteralValue("COMPLETED"),
)
_IS_INCOMPLETE_NEW = ComparisonExpression(
    ColumnValue("status", RowSource.NEW),
    ComparisonOperator.EQ,
    LiteralValue("INCOMPLETE"),
)


_RELEASE_GUARDS = (
    _guard(
        "data_snooping_oos_releases",
        "OOS_RELEASE_EXACT_STARTED_ATTEMPT",
        _relation(
            "data_snooping_oos_access_attempts",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("attempt_id", "attempt_id"),
                ("started_attempt_event_id", "attempt_event_id"),
                ("family_id", "family_id"),
                ("oos_exposure_id", "oos_exposure_id"),
            ),
            predicate=_logical(
                LogicalOperator.AND,
                _comparison(
                    "status",
                    ComparisonOperator.EQ,
                    "STARTED",
                    source=RowSource.RELATED,
                ),
                _comparison(
                    "sequence_no",
                    ComparisonOperator.EQ,
                    0,
                    source=RowSource.RELATED,
                ),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_oos_releases",
        "OOS_RELEASE_EXACT_CONSUMPTION",
        _relation(
            "global_oos_exposure_consumptions",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("oos_exposure_id", "oos_exposure_id"),
                ("family_id", "family_id"),
                ("attempt_id", "access_attempt_id"),
                ("exposure_consumption_record_hash", "record_hash"),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_oos_releases",
        "OOS_RELEASE_AFTER_START_AND_CONSUMPTION",
        _logical(
            LogicalOperator.OR,
            _relation(
                "data_snooping_oos_access_attempts",
                (
                    ("authority_instance_id", "authority_instance_id"),
                    ("started_attempt_event_id", "attempt_event_id"),
                ),
                predicate=_related_comparison("event_at_us", ComparisonOperator.GE, "released_at_us"),
            ),
            _relation(
                "global_oos_exposure_consumptions",
                (
                    ("authority_instance_id", "authority_instance_id"),
                    ("exposure_consumption_record_hash", "record_hash"),
                ),
                predicate=_related_comparison("consumed_at_us", ComparisonOperator.GE, "released_at_us"),
            ),
        ),
    ),
)


_ATTEMPT_PREVIOUS_BINDINGS = (
    ("authority_instance_id", "authority_instance_id"),
    ("attempt_id", "attempt_id"),
    ("prev_record_hash", "record_hash"),
)
_ATTEMPT_FULL_IDENTITY_BINDINGS = tuple((column, column) for column in _ATTEMPT_IDENTITY_COLUMNS)

_COMPLETED_CHILD_BINDINGS = (
    ("authority_instance_id", "authority_instance_id"),
    ("family_id", "family_id"),
    ("evaluation_batch_id", "evaluation_batch_id"),
)

_COMPLETED_BATCH_EXACT_BINDINGS = (
    ("authority_instance_id", "authority_instance_id"),
    ("attempt_id", "attempt_id"),
    ("evaluation_batch_id", "evaluation_batch_id"),
    ("family_id", "family_id"),
    ("evaluation_batch_receipt_hash", "record_hash"),
    ("frozen_config_set_hash", "frozen_config_set_hash"),
    ("ordered_period_calendar_hash", "ordered_period_keys_hash"),
    ("cost_contract_hash", "cost_contract_hash"),
    ("start_execution_attestation_hash", "start_execution_attestation_hash"),
    ("terminal_execution_attestation_hash", "terminal_execution_attestation_hash"),
    ("immutable_artifact_lease_hash", "immutable_artifact_lease_hash"),
)


def _batch_expected(value: str) -> RelatedValueLookupSpec:
    return _aggregate_expected(value, _COMPLETED_BATCH_EXACT_BINDINGS)


_ATTEMPT_GUARDS = (
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_CHAIN_APPEND_NEXT",
        _logical(
            LogicalOperator.AND,
            _IS_TERMINAL_NEW,
            _relation(
                "data_snooping_oos_access_attempts",
                _ATTEMPT_PREVIOUS_BINDINGS,
                predicate=_comparison(
                    "sequence_no",
                    ComparisonOperator.EQ,
                    0,
                    source=RowSource.RELATED,
                ),
                negated=True,
            ),
        ),
        state_machine=_ATTEMPT_STATE_MACHINE,
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_SEQUENCE_GAP",
        _logical(
            LogicalOperator.AND,
            _IS_TERMINAL_NEW,
            _relation(
                "data_snooping_oos_access_attempts",
                (
                    ("authority_instance_id", "authority_instance_id"),
                    ("attempt_id", "attempt_id"),
                ),
                predicate=_comparison(
                    "sequence_no",
                    ComparisonOperator.EQ,
                    0,
                    source=RowSource.RELATED,
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_IDENTITY_DRIFT",
        _logical(
            LogicalOperator.AND,
            _IS_TERMINAL_NEW,
            _relation(
                "data_snooping_oos_access_attempts",
                _ATTEMPT_FULL_IDENTITY_BINDINGS,
                predicate=_comparison(
                    "sequence_no",
                    ComparisonOperator.EQ,
                    0,
                    source=RowSource.RELATED,
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_SECOND_START",
        _logical(
            LogicalOperator.AND,
            _IS_STARTED_NEW,
            _relation(
                "data_snooping_oos_access_attempts",
                (
                    ("authority_instance_id", "authority_instance_id"),
                    ("family_id", "family_id"),
                ),
                predicate=_comparison(
                    "sequence_no",
                    ComparisonOperator.EQ,
                    0,
                    source=RowSource.RELATED,
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_AFTER_TERMINAL",
        _relation(
            "data_snooping_oos_access_attempts",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("attempt_id", "attempt_id"),
            ),
            predicate=MembershipExpression(
                ColumnValue("status", RowSource.RELATED),
                (LiteralValue("COMPLETED"), LiteralValue("INCOMPLETE")),
            ),
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_TERMINAL_TIME_ADVANCES",
        _logical(
            LogicalOperator.AND,
            _IS_TERMINAL_NEW,
            _relation(
                "data_snooping_oos_access_attempts",
                (
                    ("authority_instance_id", "authority_instance_id"),
                    ("attempt_id", "attempt_id"),
                ),
                predicate=_logical(
                    LogicalOperator.AND,
                    _comparison(
                        "sequence_no",
                        ComparisonOperator.EQ,
                        0,
                        source=RowSource.RELATED,
                    ),
                    _related_comparison("event_at_us", ComparisonOperator.GE, "event_at_us"),
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_TERMINAL_PREV_HEAD_MATCHES",
        _logical(
            LogicalOperator.AND,
            _IS_TERMINAL_NEW,
            _relation(
                "data_snooping_oos_access_attempts",
                _ATTEMPT_PREVIOUS_BINDINGS,
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "ATTEMPT_EXPOSURE_TUPLE_MISMATCH",
        _relation(
            "global_oos_exposures",
            (
                ("oos_exposure_id", "oos_exposure_id"),
                ("selection_decision_id", "selection_decision_id"),
                ("family_id", "family_id"),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "COMPLETED_REQUIRES_COMPLETE_BATCH",
        _logical(
            LogicalOperator.AND,
            _IS_COMPLETED_NEW,
            _logical(
                LogicalOperator.OR,
                _relation(
                    "data_snooping_evaluation_batches",
                    _COMPLETED_BATCH_EXACT_BINDINGS,
                    negated=True,
                ),
                _relation(
                    "data_snooping_family_oos_evaluations",
                    (
                        ("authority_instance_id", "authority_instance_id"),
                        ("family_id", "family_id"),
                        ("evaluation_batch_id", "evaluation_batch_id"),
                    ),
                    negated=True,
                ),
                _relation(
                    "data_snooping_evaluation_receipts",
                    (
                        ("authority_instance_id", "authority_instance_id"),
                        ("family_id", "family_id"),
                        ("evaluation_batch_id", "evaluation_batch_id"),
                        ("ordered_period_calendar_hash", "ordered_period_keys_hash"),
                        ("cost_contract_hash", "cost_contract_hash"),
                        (
                            "start_execution_attestation_hash",
                            "start_execution_attestation_hash",
                        ),
                        (
                            "terminal_execution_attestation_hash",
                            "terminal_execution_attestation_hash",
                        ),
                        (
                            "immutable_artifact_lease_hash",
                            "immutable_artifact_lease_hash",
                        ),
                    ),
                    negated=True,
                ),
                _relation(
                    "data_snooping_benchmark_returns",
                    (
                        ("authority_instance_id", "authority_instance_id"),
                        ("family_id", "family_id"),
                        ("evaluation_batch_id", "evaluation_batch_id"),
                        ("benchmark_contract_hash", "benchmark_contract_hash"),
                        ("ordered_period_calendar_hash", "ordered_period_keys_hash"),
                        ("cost_contract_hash", "cost_contract_hash"),
                        (
                            "start_execution_attestation_hash",
                            "start_execution_attestation_hash",
                        ),
                        (
                            "terminal_execution_attestation_hash",
                            "terminal_execution_attestation_hash",
                        ),
                        (
                            "immutable_artifact_lease_hash",
                            "immutable_artifact_lease_hash",
                        ),
                    ),
                    negated=True,
                ),
                _aggregate(
                    "data_snooping_family_oos_evaluations",
                    _COMPLETED_CHILD_BINDINGS,
                    RelationAggregateKind.COUNT_ROWS,
                    value=None,
                    expected=_batch_expected("candidate_count"),
                ),
                _aggregate(
                    "data_snooping_family_oos_evaluations",
                    _COMPLETED_CHILD_BINDINGS,
                    RelationAggregateKind.COUNT_DISTINCT,
                    value="config_hash",
                    expected=_batch_expected("candidate_count"),
                ),
                _aggregate(
                    "data_snooping_evaluation_receipts",
                    _COMPLETED_CHILD_BINDINGS,
                    RelationAggregateKind.COUNT_ROWS,
                    value=None,
                    expected=_batch_expected("candidate_count"),
                ),
                _aggregate(
                    "data_snooping_evaluation_receipts",
                    _COMPLETED_CHILD_BINDINGS,
                    RelationAggregateKind.COUNT_DISTINCT,
                    value="config_hash",
                    expected=_batch_expected("candidate_count"),
                ),
                _aggregate(
                    "data_snooping_family_oos_evaluations",
                    _COMPLETED_CHILD_BINDINGS,
                    RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                    value="config_hash",
                    order=("config_hash",),
                    expected=_batch_expected("ordered_candidate_config_hashes_hash"),
                ),
                _aggregate(
                    "data_snooping_evaluation_receipts",
                    _COMPLETED_CHILD_BINDINGS,
                    RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
                    value="evaluation_receipt_hash",
                    order=("config_hash", "evaluation_receipt_hash"),
                    expected=_batch_expected("ordered_candidate_receipt_hashes_hash"),
                ),
                _aggregate(
                    "data_snooping_benchmark_returns",
                    _COMPLETED_CHILD_BINDINGS,
                    RelationAggregateKind.COUNT_ROWS,
                    value=None,
                    expected=LiteralValue(1),
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_oos_access_attempts",
        "INCOMPLETE_FORBIDS_BATCH_SERIES",
        _logical(
            LogicalOperator.AND,
            _IS_INCOMPLETE_NEW,
            _logical(
                LogicalOperator.OR,
                *(
                    _relation(
                        table,
                        (
                            ("authority_instance_id", "authority_instance_id"),
                            ("family_id", "family_id"),
                            ("evaluation_batch_id", "evaluation_batch_id"),
                        ),
                    )
                    for table in (
                        "data_snooping_evaluation_batches",
                        "data_snooping_family_oos_evaluations",
                        "data_snooping_evaluation_receipts",
                        "data_snooping_benchmark_returns",
                    )
                ),
            ),
        ),
    ),
)


def _disposition_is(value: str) -> ComparisonExpression:
    return ComparisonExpression(
        ColumnValue("disposition", RowSource.NEW),
        ComparisonOperator.EQ,
        LiteralValue(value),
    )


_DISPOSITION_GUARDS = (
    _guard(
        "data_snooping_candidate_dispositions",
        "DISPOSITION_PROPOSAL_EXISTS",
        _relation(
            "data_snooping_proposal_registrations",
            (("family_id", "family_id"), ("proposal_id", "proposal_id")),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_candidate_dispositions",
        "DISPOSITION_C5_BRANCH_MATCH",
        _logical(
            LogicalOperator.OR,
            _logical(
                LogicalOperator.AND,
                _disposition_is("PERFORMANCE_EVALUATED"),
                _logical(
                    LogicalOperator.OR,
                    _relation(
                        "data_snooping_proposal_registrations",
                        (("family_id", "family_id"), ("proposal_id", "proposal_id")),
                        predicate=_comparison(
                            "proposal_branch",
                            ComparisonOperator.EQ,
                            "CLEAN",
                            source=RowSource.RELATED,
                        ),
                        negated=True,
                    ),
                    _relation(
                        "data_snooping_proposals",
                        (
                            ("family_id", "family_id"),
                            ("proposal_id", "proposal_id"),
                            ("config_hash", "config_hash"),
                        ),
                        predicate=_comparison(
                            "parse_status",
                            ComparisonOperator.EQ,
                            "ACCEPTED",
                            source=RowSource.RELATED,
                        ),
                        negated=True,
                    ),
                ),
            ),
            _logical(
                LogicalOperator.AND,
                _disposition_is("PERFORMANCE_ATTEMPTED_INCOMPLETE"),
                _logical(
                    LogicalOperator.OR,
                    _relation(
                        "data_snooping_proposal_registrations",
                        (("family_id", "family_id"), ("proposal_id", "proposal_id")),
                        predicate=_comparison(
                            "proposal_branch",
                            ComparisonOperator.EQ,
                            "CLEAN",
                            source=RowSource.RELATED,
                        ),
                        negated=True,
                    ),
                    _relation(
                        "data_snooping_proposals",
                        (
                            ("family_id", "family_id"),
                            ("proposal_id", "proposal_id"),
                            ("config_hash", "config_hash"),
                        ),
                        predicate=_comparison(
                            "parse_status",
                            ComparisonOperator.EQ,
                            "ACCEPTED",
                            source=RowSource.RELATED,
                        ),
                        negated=True,
                    ),
                ),
            ),
            _logical(
                LogicalOperator.AND,
                _disposition_is("SECRET_QUARANTINED"),
                _logical(
                    LogicalOperator.OR,
                    _relation(
                        "data_snooping_proposal_registrations",
                        (
                            ("family_id", "family_id"),
                            ("proposal_id", "proposal_id"),
                            ("quarantine_record_id", "quarantine_record_id"),
                        ),
                        predicate=_comparison(
                            "proposal_branch",
                            ComparisonOperator.EQ,
                            "SECRET_QUARANTINED",
                            source=RowSource.RELATED,
                        ),
                        negated=True,
                    ),
                    _relation(
                        "data_snooping_proposals",
                        (("family_id", "family_id"), ("proposal_id", "proposal_id")),
                        predicate=_comparison(
                            "parse_status",
                            ComparisonOperator.EQ,
                            "SECRET_QUARANTINED",
                            source=RowSource.RELATED,
                        ),
                        negated=True,
                    ),
                ),
            ),
            _logical(
                LogicalOperator.AND,
                _disposition_is("PRE_EVALUATION_INVALID"),
                _logical(
                    LogicalOperator.OR,
                    _relation(
                        "data_snooping_proposal_registrations",
                        (("family_id", "family_id"), ("proposal_id", "proposal_id")),
                        predicate=_comparison(
                            "proposal_branch",
                            ComparisonOperator.EQ,
                            "CLEAN",
                            source=RowSource.RELATED,
                        ),
                        negated=True,
                    ),
                    _relation(
                        "data_snooping_proposals",
                        (("family_id", "family_id"), ("proposal_id", "proposal_id")),
                        predicate=_logical(
                            LogicalOperator.AND,
                            _comparison(
                                "parse_status",
                                ComparisonOperator.EQ,
                                "INVALID",
                                source=RowSource.RELATED,
                            ),
                            _null(
                                "canonical_proposal_json",
                                NullOperator.IS_NULL,
                                source=RowSource.RELATED,
                            ),
                            _null(
                                "canonical_proposal_hash",
                                NullOperator.IS_NULL,
                                source=RowSource.RELATED,
                            ),
                            _null(
                                "config_hash",
                                NullOperator.IS_NULL,
                                source=RowSource.RELATED,
                            ),
                            _null(
                                "alpha_id",
                                NullOperator.IS_NULL,
                                source=RowSource.RELATED,
                            ),
                        ),
                        negated=True,
                    ),
                ),
            ),
        ),
    ),
    _guard(
        "data_snooping_candidate_dispositions",
        "DISPOSITION_EVALUATED_RECEIPT_EXACT",
        _logical(
            LogicalOperator.AND,
            _disposition_is("PERFORMANCE_EVALUATED"),
            _relation(
                "data_snooping_evaluation_receipts",
                (
                    ("authority_instance_id", "authority_instance_id"),
                    ("family_id", "family_id"),
                    ("config_hash", "config_hash"),
                    ("evaluation_receipt_hash", "evaluation_receipt_hash"),
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_candidate_dispositions",
        "DISPOSITION_INCOMPLETE_ATTEMPT_EXISTS",
        _logical(
            LogicalOperator.AND,
            _disposition_is("PERFORMANCE_ATTEMPTED_INCOMPLETE"),
            _relation(
                "data_snooping_oos_access_attempts",
                (("authority_instance_id", "authority_instance_id"), ("family_id", "family_id")),
                predicate=_comparison(
                    "status",
                    ComparisonOperator.EQ,
                    "INCOMPLETE",
                    source=RowSource.RELATED,
                ),
                negated=True,
            ),
        ),
    ),
)


_BATCH_GUARDS = (
    _guard(
        "data_snooping_evaluation_batches",
        "EVALUATION_BATCH_STARTED_ATTEMPT_EXACT",
        _relation(
            "data_snooping_oos_access_attempts",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("attempt_id", "attempt_id"),
                ("evaluation_batch_id", "evaluation_batch_id"),
                ("family_id", "family_id"),
                ("frozen_config_set_hash", "frozen_config_set_hash"),
                ("ordered_period_keys_hash", "ordered_period_calendar_hash"),
                ("cost_contract_hash", "cost_contract_hash"),
                (
                    "start_execution_attestation_hash",
                    "start_execution_attestation_hash",
                ),
                ("immutable_artifact_lease_hash", "immutable_artifact_lease_hash"),
            ),
            predicate=_comparison(
                "status",
                ComparisonOperator.EQ,
                "STARTED",
                source=RowSource.RELATED,
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_evaluation_batches",
        "EVALUATION_BATCH_NO_INCOMPLETE_TERMINAL",
        _relation(
            "data_snooping_oos_access_attempts",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("attempt_id", "attempt_id"),
            ),
            predicate=_comparison(
                "status",
                ComparisonOperator.EQ,
                "INCOMPLETE",
                source=RowSource.RELATED,
            ),
        ),
    ),
)


_FAMILY_EVALUATION_GUARDS = (
    _guard(
        "data_snooping_family_oos_evaluations",
        "FAMILY_OOS_HYPOTHESIS_EXACT",
        _relation(
            "data_snooping_hypotheses",
            (("family_id", "family_id"), ("config_hash", "config_hash"), ("alpha_id", "alpha_id")),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_family_oos_evaluations",
        "FAMILY_OOS_CLEAN_PROPOSAL_ORIGIN_EXACT",
        _relation(
            "data_snooping_proposals",
            (
                ("family_id", "family_id"),
                ("source_search_id", "search_id"),
                ("source_proposal_id", "proposal_id"),
                ("config_hash", "config_hash"),
                ("alpha_id", "alpha_id"),
            ),
            predicate=_comparison(
                "parse_status",
                ComparisonOperator.EQ,
                "ACCEPTED",
                source=RowSource.RELATED,
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_family_oos_evaluations",
        "FAMILY_OOS_EVALUATION_EXACT",
        _relation(
            "evaluations",
            (("evaluation_run_id", "run_id"), ("evaluation_content_hash", "content_hash")),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_family_oos_evaluations",
        "FAMILY_OOS_RECEIPT_EXACT",
        _relation(
            "data_snooping_evaluation_receipts",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("family_id", "family_id"),
                ("evaluation_batch_id", "evaluation_batch_id"),
                ("config_hash", "config_hash"),
                ("evaluation_receipt_hash", "evaluation_receipt_hash"),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_family_oos_evaluations",
        "FAMILY_OOS_NOT_SEARCH_EVALUATION",
        _relation(
            "search_evaluations",
            (("evaluation_run_id", "run_id"),),
        ),
    ),
)


_RECEIPT_GUARDS = (
    _guard(
        "data_snooping_evaluation_receipts",
        "EVALUATION_RECEIPT_BATCH_ATTEMPT_EXACT",
        _relation(
            "data_snooping_evaluation_batches",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("family_id", "family_id"),
                ("evaluation_batch_id", "evaluation_batch_id"),
                ("ordered_period_keys_hash", "ordered_period_keys_hash"),
                ("cost_contract_hash", "cost_contract_hash"),
                (
                    "start_execution_attestation_hash",
                    "start_execution_attestation_hash",
                ),
                (
                    "terminal_execution_attestation_hash",
                    "terminal_execution_attestation_hash",
                ),
                ("immutable_artifact_lease_hash", "immutable_artifact_lease_hash"),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_evaluation_receipts",
        "EVALUATION_RECEIPT_HYPOTHESIS_EXACT",
        _relation(
            "data_snooping_hypotheses",
            (("family_id", "family_id"), ("config_hash", "config_hash")),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_evaluation_receipts",
        "EVALUATION_RECEIPT_CANONICAL_SERIES",
        ComparisonExpression(
            ColumnValue("series_payload_json", RowSource.NEW),
            ComparisonOperator.EQ,
            LiteralValue(""),
        ),
    ),
)


_BENCHMARK_GUARDS = (
    _guard(
        "data_snooping_benchmark_returns",
        "BENCHMARK_BATCH_ATTEMPT_EXACT",
        _relation(
            "data_snooping_evaluation_batches",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("family_id", "family_id"),
                ("evaluation_batch_id", "evaluation_batch_id"),
                ("benchmark_receipt_hash", "benchmark_receipt_hash"),
                ("ordered_period_keys_hash", "ordered_period_keys_hash"),
                ("cost_contract_hash", "cost_contract_hash"),
                (
                    "start_execution_attestation_hash",
                    "start_execution_attestation_hash",
                ),
                (
                    "terminal_execution_attestation_hash",
                    "terminal_execution_attestation_hash",
                ),
                ("immutable_artifact_lease_hash", "immutable_artifact_lease_hash"),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_benchmark_returns",
        "BENCHMARK_CANONICAL_SERIES",
        ComparisonExpression(
            ColumnValue("series_payload_json", RowSource.NEW),
            ComparisonOperator.EQ,
            LiteralValue(""),
        ),
    ),
    _guard(
        "data_snooping_benchmark_returns",
        "BENCHMARK_CALENDAR_COST_MATCH",
        _relation(
            "data_snooping_evaluation_receipts",
            (
                ("authority_instance_id", "authority_instance_id"),
                ("family_id", "family_id"),
                ("evaluation_batch_id", "evaluation_batch_id"),
            ),
            predicate=_logical(
                LogicalOperator.OR,
                _related_comparison(
                    "ordered_period_keys_hash",
                    ComparisonOperator.NE,
                    "ordered_period_keys_hash",
                ),
                _related_comparison(
                    "cost_contract_hash",
                    ComparisonOperator.NE,
                    "cost_contract_hash",
                ),
            ),
        ),
    ),
)

_TABLE_GUARDS = {
    "data_snooping_oos_releases": _RELEASE_GUARDS,
    "data_snooping_oos_access_attempts": _ATTEMPT_GUARDS,
    "data_snooping_candidate_dispositions": _DISPOSITION_GUARDS,
    "data_snooping_evaluation_batches": _BATCH_GUARDS,
    "data_snooping_family_oos_evaluations": _FAMILY_EVALUATION_GUARDS,
    "data_snooping_evaluation_receipts": _RECEIPT_GUARDS,
    "data_snooping_benchmark_returns": _BENCHMARK_GUARDS,
}


def _structural_source(
    kind: StructuralVerificationSourceKind,
    *,
    source_id: str | None = None,
    source_table: str | None = None,
    columns: tuple[str, ...] = (),
    referenced_table: str | None = None,
    referenced_columns: tuple[str, ...] = (),
    required_verifier_codes: tuple[str, ...] = (),
) -> StructuralVerificationSourceSpec:
    return StructuralVerificationSourceSpec(
        kind=kind,
        source_id=source_id,
        source_table=source_table,
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
        required_verifier_codes=required_verifier_codes,
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


_STRUCTURAL_RULE_SOURCES: dict[str, StructuralVerificationSourceSpec] = {}
for _prefix in (
    "oos_release",
    "oos_attempt",
    "candidate_disposition",
    "evaluation_batch",
    "family_oos_evaluation",
    "evaluation_receipt",
    "benchmark_return",
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


_COMPLETE_BATCH_TRIGGER = (
    "data_snooping_oos_access_attempts_completed_requires_complete_batch"
)
_GLOBAL_CONSUMPTION_TRIGGER = (
    "global_oos_exposure_consumptions_"
    "global_oos_consumption_requires_exact_started_attempt"
)
_STRUCTURAL_RULE_SOURCES.update(
    {
        "oos_release_started_attempt": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_oos_releases_oos_release_exact_started_attempt",
        ),
        "oos_release_exact_consumption_tuple": _exact_foreign_key_source(
            (
                "authority_instance_id",
                "oos_exposure_id",
                "family_id",
                "attempt_id",
                "exposure_consumption_record_hash",
            ),
            "global_oos_exposure_consumptions",
            (
                "authority_instance_id",
                "oos_exposure_id",
                "family_id",
                "access_attempt_id",
                "record_hash",
            ),
        ),
        "oos_release_c6_reference_totality": _structural_source(
            StructuralVerificationSourceKind.DELEGATED_EXTENSION,
            source_id="C6_OOS_RELEASE_EMBEDDED_REFS_V1",
            required_verifier_codes=(
                "VERIFY_OOS_RELEASE_EXPOSURE_BINDING",
                "VERIFY_START_EXECUTION_ATTESTATION",
                "VERIFY_IMMUTABLE_ARTIFACT_LEASE",
                "VERIFY_OOS_RELEASE_STARTED_EXECUTION_IDENTITY",
            ),
        ),
        "oos_attempt_chain": _structural_source(
            StructuralVerificationSourceKind.CHAIN_SPEC
        ),
        "oos_attempt_truth_table": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_oos_access_attempts_status_sequence_branch",
        ),
        "oos_attempt_authority_instance_stability": _structural_source(
            StructuralVerificationSourceKind.GENESIS_FOREIGN_KEY,
            columns=("authority_instance_id", "genesis_record_hash"),
        ),
        "oos_attempt_identity_stability": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="data_snooping_oos_access_attempts_attempt_identity_drift",
        ),
        "oos_attempt_terminal_time_monotonic": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_oos_access_attempts_"
                "attempt_terminal_time_advances"
            ),
        ),
        "oos_attempt_terminal_previous_head": _structural_source(
            StructuralVerificationSourceKind.TERMINAL_HEAD
        ),
        "oos_attempt_unique_family_start": _structural_source(
            StructuralVerificationSourceKind.UNIQUE_INDEX,
            source_id=(
                "partial_unique_data_snooping_oos_access_attempts_family_start"
            ),
            columns=("authority_instance_id", "family_id"),
        ),
        "oos_attempt_exact_exposure_tuple": _exact_foreign_key_source(
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
        "oos_attempt_terminal_batch_branch": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_oos_access_attempts_status_sequence_branch",
        ),
        "candidate_disposition_c5_exact_branch": _structural_source(
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="data_snooping_candidate_dispositions_exact_branch",
        ),
        "candidate_disposition_proposal_totality": _exact_foreign_key_source(
            ("proposal_id",),
            "data_snooping_proposal_registrations",
            ("proposal_id",),
        ),
        "candidate_disposition_evaluated_receipt_exact": (
            _exact_foreign_key_source(
                (
                    "authority_instance_id",
                    "family_id",
                    "config_hash",
                    "evaluation_receipt_hash",
                ),
                "data_snooping_evaluation_receipts",
                (
                    "authority_instance_id",
                    "family_id",
                    "config_hash",
                    "evaluation_receipt_hash",
                ),
            )
        ),
        "candidate_disposition_incomplete_attempt_totality": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_candidate_dispositions_"
                "disposition_incomplete_attempt_exists"
            ),
        ),
        "candidate_disposition_quarantine_origin": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_candidate_dispositions_disposition_c5_branch_match"
            ),
        ),
        "candidate_disposition_pre_proposal_identity_exact": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_candidate_dispositions_disposition_c5_branch_match"
            ),
        ),
        "evaluation_batch_exact_candidate_set": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=_COMPLETE_BATCH_TRIGGER,
            source_table="data_snooping_oos_access_attempts",
        ),
        "evaluation_batch_candidate_receipt_totality": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=_COMPLETE_BATCH_TRIGGER,
            source_table="data_snooping_oos_access_attempts",
        ),
        "evaluation_batch_exactly_one_benchmark": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=_COMPLETE_BATCH_TRIGGER,
            source_table="data_snooping_oos_access_attempts",
        ),
        "evaluation_batch_common_calendar_cost_contract": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=_COMPLETE_BATCH_TRIGGER,
            source_table="data_snooping_oos_access_attempts",
        ),
        "evaluation_batch_started_attempt_identity": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_evaluation_batches_"
                "evaluation_batch_started_attempt_exact"
            ),
        ),
        "evaluation_batch_attempt_terminal_binding": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=_COMPLETE_BATCH_TRIGGER,
            source_table="data_snooping_oos_access_attempts",
        ),
        "family_oos_evaluation_hypothesis_identity": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_family_oos_evaluations_"
                "family_oos_hypothesis_exact"
            ),
        ),
        "family_oos_evaluation_clean_proposal_origin": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_family_oos_evaluations_"
                "family_oos_clean_proposal_origin_exact"
            ),
        ),
        "family_oos_evaluation_existing_evaluation": _exact_foreign_key_source(
            ("evaluation_run_id",),
            "evaluations",
            ("run_id",),
        ),
        "family_oos_evaluation_content_hash": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_family_oos_evaluations_"
                "family_oos_evaluation_exact"
            ),
        ),
        "family_oos_evaluation_receipt_exact": _exact_foreign_key_source(
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
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
        "family_oos_evaluation_never_search_evaluation": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_family_oos_evaluations_"
                "family_oos_not_search_evaluation"
            ),
        ),
        "evaluation_receipt_batch_execution_identity": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_evaluation_receipts_"
                "evaluation_receipt_batch_attempt_exact"
            ),
        ),
        "evaluation_receipt_exact_link": _exact_foreign_key_source(
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
            "data_snooping_family_oos_evaluations",
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "config_hash",
                "evaluation_receipt_hash",
            ),
        ),
        "benchmark_return_candidate_calendar_cost_match": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_benchmark_returns_benchmark_calendar_cost_match"
            ),
        ),
        "benchmark_return_batch_execution_identity": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=(
                "data_snooping_benchmark_returns_benchmark_batch_attempt_exact"
            ),
        ),
        "benchmark_return_completed_attempt_contract_identity": (
            _structural_source(
                StructuralVerificationSourceKind.TRIGGER_POLICY,
                source_id=_COMPLETE_BATCH_TRIGGER,
                source_table="data_snooping_oos_access_attempts",
            )
        ),
        "global_oos_consumption_started_attempt_exact_tuple": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=_GLOBAL_CONSUMPTION_TRIGGER,
        ),
        "global_oos_consumption_attempt_authority_binding": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=_GLOBAL_CONSUMPTION_TRIGGER,
        ),
    }
)


_NAMED_RULE_VERIFIER_CODES = {
    rule_id: f"c7.verify_{rule_id}_v1"
    for rule_id in (
        "oos_release_exact_exposure_tuple",
        "oos_release_after_freeze_start_consumption",
        "oos_attempt_performance_access_count",
        "candidate_disposition_duplicate_receipt_identity",
        "family_oos_evaluation_audit_intent",
        "evaluation_receipt_canonical_series_payload",
        "evaluation_receipt_finite_float64_positive_zero",
        "evaluation_receipt_nonnegative_costs_turnovers",
        "evaluation_receipt_strict_period_order",
        "evaluation_receipt_common_frequency_horizon",
        "evaluation_receipt_exact_cost_reconciliation",
        "evaluation_receipt_component_hashes",
        "benchmark_return_canonical_series_payload",
        "benchmark_return_finite_float64_positive_zero",
        "benchmark_return_nonnegative_costs_turnovers",
        "benchmark_return_strict_period_order",
        "benchmark_return_common_frequency_horizon",
        "benchmark_return_exact_cost_reconciliation",
    )
}


def _source_with_rule_columns(
    source: StructuralVerificationSourceSpec,
    columns: tuple[str, ...],
) -> StructuralVerificationSourceSpec:
    if source.columns or source.source_table is not None or not columns:
        return source
    return StructuralVerificationSourceSpec(
        kind=source.kind,
        source_id=source.source_id,
        source_table=source.source_table,
        columns=columns,
        referenced_table=source.referenced_table,
        referenced_columns=source.referenced_columns,
        required_rule_ids=source.required_rule_ids,
        required_verifier_codes=source.required_verifier_codes,
    )


def _rule(
    rule_id: str,
    kind: VerificationRuleKind,
    columns: tuple[str, ...] = (),
    *,
    referenced_table: str | None = None,
) -> VerificationRuleSpec:
    verifier_code = _NAMED_RULE_VERIFIER_CODES.get(rule_id)
    source = _STRUCTURAL_RULE_SOURCES.get(rule_id)
    if (verifier_code is None) == (source is None):
        raise ValueError(
            f"C7 verification rule requires exactly one proof binding: {rule_id}"
        )
    return VerificationRuleSpec(
        rule_id=rule_id,
        kind=kind,
        columns=columns,
        referenced_table=referenced_table,
        verifier_code=verifier_code,
        structural_source=(
            None if source is None else _source_with_rule_columns(source, columns)
        ),
    )


_VERIFICATION_RULES = {
    "data_snooping_oos_releases": (
        _rule(
            "oos_release_row_hash",
            VerificationRuleKind.ROW_HASH,
            tuple(
                column.name for column in _TABLE_COLUMNS["data_snooping_oos_releases"] if column.name != "record_hash"
            ),
        ),
        _rule(
            "oos_release_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("oos_release_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "oos_release_started_attempt",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "authority_instance_id",
                "attempt_id",
                "started_attempt_event_id",
                "family_id",
                "oos_exposure_id",
            ),
            referenced_table="data_snooping_oos_access_attempts",
        ),
        _rule(
            "oos_release_exact_consumption_tuple",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "authority_instance_id",
                "oos_exposure_id",
                "family_id",
                "attempt_id",
                "exposure_consumption_record_hash",
            ),
            referenced_table="global_oos_exposure_consumptions",
        ),
        _rule(
            "oos_release_exact_exposure_tuple",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("oos_exposure_id", "family_id"),
            referenced_table="global_oos_exposures",
        ),
        _rule(
            "oos_release_after_freeze_start_consumption",
            VerificationRuleKind.CONTENT_BINDING,
            ("family_freeze_record_hash", "released_at_us"),
        ),
        _rule(
            "oos_release_c6_reference_totality",
            VerificationRuleKind.CONTENT_BINDING,
        ),
    ),
    "data_snooping_oos_access_attempts": (
        _rule(
            "oos_attempt_row_hash",
            VerificationRuleKind.ROW_HASH,
            tuple(
                column.name
                for column in _TABLE_COLUMNS["data_snooping_oos_access_attempts"]
                if column.name != "record_hash"
            ),
        ),
        _rule(
            "oos_attempt_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("oos_attempt_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "oos_attempt_chain",
            VerificationRuleKind.ORDERED_CHAIN,
            ("authority_instance_id", "attempt_id", "sequence_no", "prev_record_hash", "record_hash"),
        ),
        _rule(
            "oos_attempt_truth_table",
            VerificationRuleKind.STATE_BRANCH,
            (
                "status",
                "sequence_no",
                "terminal_execution_attestation_hash",
                "evaluation_batch_receipt_hash",
                "reason_code",
            ),
        ),
        _rule(
            "oos_attempt_authority_instance_stability",
            VerificationRuleKind.CONTENT_BINDING,
            ("authority_instance_id", "attempt_id"),
        ),
        _rule(
            "oos_attempt_identity_stability",
            VerificationRuleKind.CONTENT_BINDING,
            _ATTEMPT_IDENTITY_COLUMNS,
        ),
        _rule(
            "oos_attempt_terminal_time_monotonic",
            VerificationRuleKind.CONTENT_BINDING,
            ("attempt_id", "event_at_us"),
        ),
        _rule(
            "oos_attempt_terminal_previous_head",
            VerificationRuleKind.TERMINAL_HEAD,
            ("attempt_id", "prev_record_hash"),
        ),
        _rule(
            "oos_attempt_unique_family_start",
            VerificationRuleKind.CONTENT_BINDING,
            ("authority_instance_id", "family_id", "sequence_no"),
        ),
        _rule(
            "oos_attempt_exact_exposure_tuple",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            referenced_table="global_oos_exposures",
        ),
        _rule(
            "oos_attempt_terminal_batch_branch",
            VerificationRuleKind.STATE_BRANCH,
            ("status", "evaluation_batch_receipt_hash"),
        ),
        _rule(
            "oos_attempt_performance_access_count",
            VerificationRuleKind.CONTENT_BINDING,
            ("attempt_id", "sequence_no"),
        ),
    ),
    "data_snooping_candidate_dispositions": (
        _rule(
            "candidate_disposition_row_hash",
            VerificationRuleKind.ROW_HASH,
            tuple(
                column.name
                for column in _TABLE_COLUMNS["data_snooping_candidate_dispositions"]
                if column.name != "record_hash"
            ),
        ),
        _rule(
            "candidate_disposition_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("candidate_disposition_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "candidate_disposition_c5_exact_branch",
            VerificationRuleKind.STATE_BRANCH,
            (
                "disposition",
                "config_hash",
                "evaluation_receipt_hash",
                "quarantine_record_id",
                "reason_code",
                "performance_accessed",
            ),
        ),
        _rule(
            "candidate_disposition_proposal_totality",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("proposal_id",),
            referenced_table="data_snooping_proposal_registrations",
        ),
        _rule(
            "candidate_disposition_evaluated_receipt_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "config_hash", "evaluation_receipt_hash"),
            referenced_table="data_snooping_evaluation_receipts",
        ),
        _rule(
            "candidate_disposition_incomplete_attempt_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "config_hash", "reason_code"),
            referenced_table="data_snooping_oos_access_attempts",
        ),
        _rule(
            "candidate_disposition_quarantine_origin",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("family_id", "proposal_id", "quarantine_record_id"),
            referenced_table="data_snooping_proposal_registrations",
        ),
        _rule(
            "candidate_disposition_pre_proposal_identity_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("family_id", "proposal_id", "disposition", "config_hash"),
            referenced_table="data_snooping_proposals",
        ),
        _rule(
            "candidate_disposition_duplicate_receipt_identity",
            VerificationRuleKind.CONTENT_BINDING,
            ("family_id", "proposal_id", "evaluation_receipt_hash"),
        ),
    ),
    "data_snooping_evaluation_batches": (
        _rule(
            "evaluation_batch_row_hash",
            VerificationRuleKind.ROW_HASH,
            tuple(
                column.name
                for column in _TABLE_COLUMNS["data_snooping_evaluation_batches"]
                if column.name != "record_hash"
            ),
        ),
        _rule(
            "evaluation_batch_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("evaluation_batch_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "evaluation_batch_exact_candidate_set",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "evaluation_batch_id",
                "candidate_count",
                "frozen_config_set_hash",
                "ordered_candidate_config_hashes_hash",
            ),
            referenced_table="data_snooping_family_oos_evaluations",
        ),
        _rule(
            "evaluation_batch_candidate_receipt_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "evaluation_batch_id", "candidate_count", "ordered_candidate_receipt_hashes_hash"),
            referenced_table="data_snooping_evaluation_receipts",
        ),
        _rule(
            "evaluation_batch_exactly_one_benchmark",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("family_id", "evaluation_batch_id", "benchmark_receipt_hash"),
            referenced_table="data_snooping_benchmark_returns",
        ),
        _rule(
            "evaluation_batch_common_calendar_cost_contract",
            VerificationRuleKind.CONTENT_BINDING,
            ("ordered_period_keys_hash", "cost_contract_hash"),
        ),
        _rule(
            "evaluation_batch_started_attempt_identity",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "authority_instance_id",
                "attempt_id",
                "evaluation_batch_id",
                "family_id",
                "frozen_config_set_hash",
                "ordered_period_keys_hash",
                "cost_contract_hash",
                "start_execution_attestation_hash",
                "immutable_artifact_lease_hash",
            ),
            referenced_table="data_snooping_oos_access_attempts",
        ),
        _rule(
            "evaluation_batch_attempt_terminal_binding",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "authority_instance_id",
                "attempt_id",
                "evaluation_batch_id",
                "family_id",
                "frozen_config_set_hash",
                "ordered_period_keys_hash",
                "cost_contract_hash",
                "start_execution_attestation_hash",
                "terminal_execution_attestation_hash",
                "immutable_artifact_lease_hash",
                "record_hash",
            ),
            referenced_table="data_snooping_oos_access_attempts",
        ),
    ),
    "data_snooping_family_oos_evaluations": (
        _rule(
            "family_oos_evaluation_row_hash",
            VerificationRuleKind.ROW_HASH,
            tuple(
                column.name
                for column in _TABLE_COLUMNS["data_snooping_family_oos_evaluations"]
                if column.name != "record_hash"
            ),
        ),
        _rule(
            "family_oos_evaluation_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("family_oos_evaluation_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "family_oos_evaluation_hypothesis_identity",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("family_id", "config_hash", "alpha_id"),
            referenced_table="data_snooping_hypotheses",
        ),
        _rule(
            "family_oos_evaluation_clean_proposal_origin",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("family_id", "source_search_id", "source_proposal_id", "config_hash", "alpha_id"),
            referenced_table="data_snooping_proposals",
        ),
        _rule(
            "family_oos_evaluation_existing_evaluation",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("evaluation_run_id",),
            referenced_table="evaluations",
        ),
        _rule(
            "family_oos_evaluation_content_hash",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("evaluation_run_id", "evaluation_content_hash"),
            referenced_table="evaluations",
        ),
        _rule(
            "family_oos_evaluation_audit_intent",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("evaluation_run_id", "evaluation_content_hash", "evaluation_audit_intent_hash"),
            referenced_table="evaluation_audit_outbox",
        ),
        _rule(
            "family_oos_evaluation_receipt_exact",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "evaluation_batch_id", "config_hash", "evaluation_receipt_hash"),
            referenced_table="data_snooping_evaluation_receipts",
        ),
        _rule(
            "family_oos_evaluation_never_search_evaluation",
            VerificationRuleKind.CONTENT_BINDING,
            ("evaluation_run_id", "source_search_id"),
        ),
    ),
    "data_snooping_evaluation_receipts": (
        _rule(
            "evaluation_receipt_row_hash",
            VerificationRuleKind.ROW_HASH,
            tuple(
                column.name
                for column in _TABLE_COLUMNS["data_snooping_evaluation_receipts"]
                if column.name != "record_hash"
            ),
        ),
        _rule(
            "evaluation_receipt_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("evaluation_receipt_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "evaluation_receipt_canonical_series_payload",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json",),
        ),
        _rule(
            "evaluation_receipt_finite_float64_positive_zero",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json",),
        ),
        _rule(
            "evaluation_receipt_nonnegative_costs_turnovers",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json",),
        ),
        _rule("evaluation_receipt_strict_period_order", VerificationRuleKind.CONTENT_BINDING, ("series_payload_json",)),
        _rule(
            "evaluation_receipt_common_frequency_horizon",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json",),
        ),
        _rule(
            "evaluation_receipt_exact_cost_reconciliation",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json", "cost_contract_hash"),
        ),
        _rule(
            "evaluation_receipt_component_hashes",
            VerificationRuleKind.CONTENT_BINDING,
            (
                "ordered_period_keys_hash",
                "gross_returns_hash",
                "costs_hash",
                "net_returns_hash",
                "turnovers_hash",
                "series_hash",
            ),
        ),
        _rule(
            "evaluation_receipt_batch_execution_identity",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "ordered_period_keys_hash",
                "cost_contract_hash",
                "start_execution_attestation_hash",
                "terminal_execution_attestation_hash",
                "immutable_artifact_lease_hash",
            ),
            referenced_table="data_snooping_evaluation_batches",
        ),
        _rule(
            "evaluation_receipt_exact_link",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("authority_instance_id", "family_id", "evaluation_batch_id", "config_hash", "evaluation_receipt_hash"),
            referenced_table="data_snooping_family_oos_evaluations",
        ),
    ),
    "data_snooping_benchmark_returns": (
        _rule(
            "benchmark_return_row_hash",
            VerificationRuleKind.ROW_HASH,
            tuple(
                column.name
                for column in _TABLE_COLUMNS["data_snooping_benchmark_returns"]
                if column.name != "record_hash"
            ),
        ),
        _rule(
            "benchmark_return_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            ("authority_instance_id", "genesis_record_hash"),
        ),
        _rule("benchmark_return_foreign_keys", VerificationRuleKind.FOREIGN_KEY),
        _rule(
            "benchmark_return_canonical_series_payload", VerificationRuleKind.CONTENT_BINDING, ("series_payload_json",)
        ),
        _rule(
            "benchmark_return_finite_float64_positive_zero",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json",),
        ),
        _rule(
            "benchmark_return_nonnegative_costs_turnovers",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json",),
        ),
        _rule("benchmark_return_strict_period_order", VerificationRuleKind.CONTENT_BINDING, ("series_payload_json",)),
        _rule(
            "benchmark_return_common_frequency_horizon", VerificationRuleKind.CONTENT_BINDING, ("series_payload_json",)
        ),
        _rule(
            "benchmark_return_exact_cost_reconciliation",
            VerificationRuleKind.CONTENT_BINDING,
            ("series_payload_json", "cost_contract_hash"),
        ),
        _rule(
            "benchmark_return_candidate_calendar_cost_match",
            VerificationRuleKind.CONTENT_BINDING,
            ("ordered_period_keys_hash", "cost_contract_hash"),
        ),
        _rule(
            "benchmark_return_batch_execution_identity",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "benchmark_receipt_hash",
                "ordered_period_keys_hash",
                "cost_contract_hash",
                "start_execution_attestation_hash",
                "terminal_execution_attestation_hash",
                "immutable_artifact_lease_hash",
            ),
            referenced_table="data_snooping_evaluation_batches",
        ),
        _rule(
            "benchmark_return_completed_attempt_contract_identity",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            (
                "authority_instance_id",
                "family_id",
                "evaluation_batch_id",
                "benchmark_contract_hash",
                "ordered_period_keys_hash",
                "cost_contract_hash",
                "start_execution_attestation_hash",
                "terminal_execution_attestation_hash",
                "immutable_artifact_lease_hash",
            ),
            referenced_table="data_snooping_oos_access_attempts",
        ),
    ),
}


_ATTEMPT_CHAIN = ChainSpec(
    partition_key_columns=("authority_instance_id", "attempt_id"),
    ordinal_column="sequence_no",
    genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
    previous_hash_column="prev_record_hash",
    record_hash_column="record_hash",
    terminal_head_rule=ChainHeadRule.LATEST_TERMINAL_RECORD_HASH,
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
        chain=(_ATTEMPT_CHAIN if table == "data_snooping_oos_access_attempts" else None),
        verification_rules=_VERIFICATION_RULES[table],
    )


C7_AUTHORITY_TABLE_SPECS = tuple(_table_spec(table) for table in _TABLE_ORDER)


_C4_CONSUMPTION_EXTENSION_INDEX = _index(
    "global_oos_exposure_consumptions",
    "c7_release_binding",
    "UNIQUE",
    (
        "authority_instance_id",
        "oos_exposure_id",
        "family_id",
        "access_attempt_id",
        "record_hash",
    ),
)

_C4_CONSUMPTION_STARTED_GUARD = _guard(
    "global_oos_exposure_consumptions",
    "GLOBAL_OOS_CONSUMPTION_REQUIRES_EXACT_STARTED_ATTEMPT",
    _relation(
        "data_snooping_oos_access_attempts",
        (
            ("authority_instance_id", "authority_instance_id"),
            ("access_attempt_id", "attempt_id"),
            ("family_id", "family_id"),
            ("selection_decision_id", "selection_decision_id"),
            ("oos_exposure_id", "oos_exposure_id"),
        ),
        predicate=_logical(
            LogicalOperator.AND,
            _comparison(
                "status",
                ComparisonOperator.EQ,
                "STARTED",
                source=RowSource.RELATED,
            ),
            _comparison(
                "sequence_no",
                ComparisonOperator.EQ,
                0,
                source=RowSource.RELATED,
            ),
        ),
        negated=True,
    ),
)

C7_TABLE_EXTENSIONS = (
    AuthorityTableExtensionSpec(
        extension_id="C7_GLOBAL_OOS_EXPOSURE_CONSUMPTION_ATTEMPT_BINDING_V1",
        target_table="global_oos_exposure_consumptions",
        indexes=(_C4_CONSUMPTION_EXTENSION_INDEX,),
        triggers=(_C4_CONSUMPTION_STARTED_GUARD,),
        verification_rules=(
            _rule(
                "global_oos_consumption_started_attempt_exact_tuple",
                VerificationRuleKind.CROSS_TABLE_REFERENCE,
                (
                    "authority_instance_id",
                    "access_attempt_id",
                    "family_id",
                    "selection_decision_id",
                    "oos_exposure_id",
                ),
                referenced_table="data_snooping_oos_access_attempts",
            ),
            _rule(
                "global_oos_consumption_attempt_authority_binding",
                VerificationRuleKind.GENESIS_BINDING,
                ("authority_instance_id", "access_attempt_id"),
            ),
        ),
    ),
)


C7_REQUIRED_EXTENSION_SPECS = (
    RequiredTableExtensionSpec(
        extension_id="C6_OOS_RELEASE_EMBEDDED_REFS_V1",
        target_table="data_snooping_oos_releases",
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.oos_release.embedded_refs",
    ),
    *(
        RequiredTableExtensionSpec(
            extension_id=f"C8_{table.upper()}_LATE_INSERT_GUARDS_V1",
            target_table=table,
            provider_component_id=_C8_COMPONENT,
            required_capability=f"c8.{table}.late_insert_guards",
        )
        for table in _TABLE_ORDER
    ),
)


_C6_RELEASE_CONTRACT_FIELDS = (
    "exposure_fingerprint",
    "exposure_authority_id",
    "exposure_authority_head",
    "start_execution_attestation_hash",
    "execution_attestation_authority_id",
    "execution_attestation_authority_head",
    "immutable_artifact_lease_id",
    "immutable_artifact_lease_hash",
    "immutable_artifact_lease_authority_id",
    "immutable_artifact_lease_authority_head",
)

C7_REQUIRED_CONTRACT_SURFACES = (
    RequiredContractSurfaceSpec(
        contract_type="OosReleaseReceipt",
        field_names=_C6_RELEASE_CONTRACT_FIELDS,
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.oos_release.embedded_refs",
    ),
)


def _column_coverage(
    contract_type: str,
    field_name: str,
    table_name: str,
    target_field: str | None = None,
) -> ContractFieldCoverageSpec:
    return ContractFieldCoverageSpec(
        contract_type=contract_type,
        field_name=field_name,
        kind=CoverageKind.COLUMN,
        table_name=table_name,
        target_field=target_field or field_name,
    )


def _payload_coverage(
    contract_type: str,
    field_name: str,
    table_name: str,
) -> ContractFieldCoverageSpec:
    return ContractFieldCoverageSpec(
        contract_type=contract_type,
        field_name=field_name,
        kind=CoverageKind.CANONICAL_PAYLOAD,
        table_name=table_name,
        target_field="series_payload_json",
    )


def _derived_coverage(
    contract_type: str,
    field_name: str,
    derivation_id: str,
) -> ContractFieldCoverageSpec:
    return ContractFieldCoverageSpec(
        contract_type=contract_type,
        field_name=field_name,
        kind=CoverageKind.NON_PERSISTED_DERIVATION,
        derivation_id=derivation_id,
    )


_SERIES_PAYLOAD_FIELDS = (
    "ordered_period_keys",
    "gross_returns",
    "costs",
    "net_returns",
    "turnovers",
)
_SERIES_DIGEST_FIELDS = (
    "ordered_period_keys_hash",
    "gross_returns_hash",
    "costs_hash",
    "net_returns_hash",
    "turnovers_hash",
    "cost_contract_hash",
    "cost_model_input_hash",
    "series_hash",
)

C7_CONTRACT_COVERAGE = (
    *(
        _payload_coverage(
            "EvaluationSeriesReceipt",
            field,
            "data_snooping_evaluation_receipts",
        )
        for field in _SERIES_PAYLOAD_FIELDS
    ),
    *(
        _column_coverage(
            "EvaluationSeriesReceipt",
            field,
            "data_snooping_evaluation_receipts",
        )
        for field in _SERIES_DIGEST_FIELDS
    ),
    *(
        _column_coverage(
            "CandidateReturnSeries",
            field,
            "data_snooping_family_oos_evaluations",
        )
        for field in (
            "family_id",
            "evaluation_batch_id",
            "config_hash",
            "alpha_id",
            "evaluation_run_id",
            "evaluation_content_hash",
            "evaluation_audit_intent_hash",
            "source_search_id",
            "source_proposal_id",
        )
    ),
    _payload_coverage(
        "CandidateReturnSeries",
        "series",
        "data_snooping_evaluation_receipts",
    ),
    _column_coverage(
        "CandidateReturnSeries",
        "evaluation_receipt_hash",
        "data_snooping_family_oos_evaluations",
    ),
    *(
        _column_coverage(
            "BenchmarkReturnSeries",
            field,
            "data_snooping_benchmark_returns",
        )
        for field in (
            "family_id",
            "evaluation_batch_id",
            "benchmark_id",
            "benchmark_contract_hash",
        )
    ),
    _payload_coverage(
        "BenchmarkReturnSeries",
        "series",
        "data_snooping_benchmark_returns",
    ),
    _column_coverage(
        "BenchmarkReturnSeries",
        "benchmark_receipt_hash",
        "data_snooping_benchmark_returns",
    ),
    *(
        _column_coverage(
            "FamilyEvaluationBatchReceipt",
            field,
            "data_snooping_evaluation_batches",
            "record_hash" if field == "batch_receipt_hash" else None,
        )
        for field in (
            "record_type",
            "authority_instance_id",
            "genesis_record_hash",
            "family_id",
            "attempt_id",
            "evaluation_batch_id",
            "frozen_config_set_hash",
            "candidate_count",
            "benchmark_receipt_hash",
            "ordered_period_keys_hash",
            "cost_contract_hash",
            "start_execution_attestation_hash",
            "terminal_execution_attestation_hash",
            "immutable_artifact_lease_hash",
            "committed_at_us",
            "batch_receipt_hash",
        )
    ),
    _derived_coverage(
        "FamilyEvaluationBatchReceipt",
        "candidate_order",
        "c7.batch_candidate_order_from_receipts",
    ),
    _derived_coverage(
        "FamilyEvaluationBatchReceipt",
        "candidate_receipt_hashes",
        "c7.batch_candidate_receipts_from_rows",
    ),
    *(
        _column_coverage(
            "OosAccessAttempt",
            field,
            "data_snooping_oos_access_attempts",
            {
                "previous_event_hash": "prev_record_hash",
                "event_hash": "record_hash",
            }.get(field),
        )
        for field in (
            "record_type",
            "authority_instance_id",
            "genesis_record_hash",
            "attempt_event_id",
            "attempt_id",
            "family_id",
            "selection_decision_id",
            "oos_exposure_id",
            "evaluation_batch_id",
            "sequence_no",
            "status",
            "frozen_config_set_hash",
            "benchmark_contract_hash",
            "ordered_period_calendar_hash",
            "cost_contract_hash",
            "start_execution_attestation_hash",
            "terminal_execution_attestation_hash",
            "immutable_artifact_lease_hash",
            "evaluation_batch_receipt_hash",
            "reason_code",
            "event_at_us",
            "previous_event_hash",
            "event_hash",
        )
    ),
    *(
        _column_coverage(
            "OosReleaseReceipt",
            field,
            "data_snooping_oos_releases",
            "record_hash" if field == "release_record_hash" else None,
        )
        for field in (
            "record_type",
            "authority_instance_id",
            "genesis_record_hash",
            "oos_release_id",
            "family_id",
            "attempt_id",
            "started_attempt_event_id",
            "family_freeze_record_hash",
            "exposure_consumption_record_hash",
            "oos_exposure_id",
            "released_at_us",
            "release_record_hash",
        )
    ),
    _derived_coverage(
        "OosReleaseReceipt",
        "authority_head",
        "c7.oos_release_latest_record_hash",
    ),
    *(
        _column_coverage(
            "CandidateDisposition",
            field,
            "data_snooping_candidate_dispositions",
        )
        for field in (
            "family_id",
            "proposal_id",
            "disposition",
            "config_hash",
            "evaluation_receipt_hash",
            "quarantine_record_id",
            "reason_code",
            "performance_accessed",
        )
    ),
)


C7_CONTRACT_DERIVATIONS = (
    ContractFieldDerivationSpec(
        derivation_id="c7.batch_candidate_order_from_receipts",
        contract_type="FamilyEvaluationBatchReceipt",
        field_name="candidate_order",
        kind=ContractDerivationKind.ORDERED_ROW_AGGREGATION,
        binding_table="data_snooping_evaluation_batches",
        binding_field="evaluation_batch_id",
        source_table="data_snooping_family_oos_evaluations",
        source_key_field="evaluation_batch_id",
        source_value_field="config_hash",
        order_by_fields=("config_hash",),
    ),
    ContractFieldDerivationSpec(
        derivation_id="c7.batch_candidate_receipts_from_rows",
        contract_type="FamilyEvaluationBatchReceipt",
        field_name="candidate_receipt_hashes",
        kind=ContractDerivationKind.ORDERED_ROW_AGGREGATION,
        binding_table="data_snooping_evaluation_batches",
        binding_field="evaluation_batch_id",
        source_table="data_snooping_evaluation_receipts",
        source_key_field="evaluation_batch_id",
        source_value_field="evaluation_receipt_hash",
        order_by_fields=("config_hash",),
    ),
    ContractFieldDerivationSpec(
        derivation_id="c7.oos_release_latest_record_hash",
        contract_type="OosReleaseReceipt",
        field_name="authority_head",
        kind=ContractDerivationKind.IMMUTABLE_ROW_LOOKUP,
        binding_table="data_snooping_oos_releases",
        binding_field="oos_release_id",
        source_table="data_snooping_oos_releases",
        source_key_field="oos_release_id",
        source_value_field="record_hash",
    ),
)


C7_SCHEMA_COMPONENT = SchemaComponentSpec(
    component_id=_COMPONENT_ID,
    capabilities=_CAPABILITIES,
    dependencies=(
        ComponentDependencySpec(
            component_id=_C4_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=(
                "c4.questions_lineage_scope_decisions_exposures.core",
                "c4.global_oos_exposure.core",
            ),
        ),
        ComponentDependencySpec(
            component_id=_C5_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=("c5.proposals_hypotheses.dispositions",),
        ),
    ),
    tables=C7_AUTHORITY_TABLE_SPECS,
    extensions=C7_TABLE_EXTENSIONS,
    required_extensions=C7_REQUIRED_EXTENSION_SPECS,
    coverage=C7_CONTRACT_COVERAGE,
    contract_derivations=C7_CONTRACT_DERIVATIONS,
    required_contract_surfaces=C7_REQUIRED_CONTRACT_SURFACES,
)
