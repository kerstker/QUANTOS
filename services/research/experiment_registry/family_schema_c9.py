"""Exact Pass-C/C9 publication-authority schema metadata.

This module freezes one append-only publication receipt table.  It is a schema
contract only: no DDL renderer, migration, exporter, verifier implementation,
network client, or trading/lifecycle authority lives here.

The C8 family conclusion is an independent parent row.  The ordered grid
history is bound separately through the conclusion's canonical JSON payload;
there is deliberately no "effective grid row" carrier.  Pass M must execute
the fixed C8 semantic verifier and the C9 self-contained bundle verifier before
accepting a publication receipt.

The artifact is not its own trust root.  The A9 verifier must receive the
deployment-specific provider/scanner membership and the expected activation
anchors listed in ``C9_A9_EXTERNAL_INPUT_REQUIREMENTS`` out of band.  Those
values must never be embedded in the canonical deployment-neutral v3 schema
manifest.
"""

from __future__ import annotations

from .family_schema_spec import (
    ArtifactGraphContractSpec,
    ArtifactGraphLocatorSourceKind,
    ArtifactGraphLocatorSpec,
    AuthorityTableSpec,
    CanonicalEncoding,
    CanonicalJsonPayloadHashExpression,
    ChainGenesisRule,
    ChainHeadRule,
    ChainPredecessorExpression,
    ChainSpec,
    CheckConstraintSpec,
    Collation,
    ColumnSpec,
    ColumnValue,
    ComparisonExpression,
    ComparisonOperator,
    ComponentDependencySpec,
    ContentAddressedArtifactGraphDerivationSpec,
    ContractFieldCoverageSpec,
    CoverageKind,
    DependencyKind,
    ForeignKeyAction,
    ForeignKeyDeferrability,
    ForeignKeySpec,
    IndexColumnSpec,
    IndexSpec,
    LiteralValue,
    LogicalExpression,
    LogicalOperator,
    LookupBinding,
    MembershipExpression,
    NullExpression,
    NullOperator,
    RelationExistsExpression,
    RequiredContractSurfaceSpec,
    RequiredTableExtensionSpec,
    RowHashAlgorithm,
    RowHashSpec,
    RowSource,
    SchemaComponentSpec,
    SortDirection,
    SqlType,
    TriggerEffect,
    TriggerEvent,
    TriggerPolicyKind,
    TriggerPolicySpec,
    TriggerTiming,
    VerificationRuleKind,
    VerificationRuleSpec,
)


_COMPONENT_ID = "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1"
_C4_COMPONENT = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
_C5_COMPONENT = "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1"
_C6_COMPONENT = "C6_EXTERNAL_AUTHORITY_REFS_V1"
_C7_COMPONENT = "C7_ONE_SHOT_OOS_V1"
_C8_COMPONENT = "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1"
_TABLE = "artifact_publication_receipts"
_RECORD_TYPE = "ARTIFACT_PUBLICATION_RECEIPT_V1"
_RECEIPT_SCHEMA_VERSION = "ARTIFACT_PUBLICATION_RECEIPT_V1"
_ARTIFACT_KIND = "DATA_SNOOPING_FAMILY_ARTIFACT_V1"

_CAPABILITIES = (
    "c9.publication_receipt.core",
    "c9.publication_receipt.chain",
    "c9.publication_receipt.exact_parents",
    "c9.publication_receipt.final_scan",
    "c9.publication_receipt.self_contained_bundle",
    "c9.phase_a.no_promotion",
)

C9_A9_EXTERNAL_INPUT_REQUIREMENTS = (
    "expected_external_provider_authority_genesis_registry",
    "trusted_scanner_implementation_registry",
    "expected_family_schema_v3_manifest_hash",
    "expected_authority_activation_code_manifest_evidence_hash",
)


def _column(
    name: str,
    sql_type: SqlType = SqlType.TEXT,
    *,
    nullable: bool = False,
) -> ColumnSpec:
    return ColumnSpec(name=name, sql_type=sql_type, nullable=nullable)


_COLUMNS = (
    _column("record_type"),
    _column("receipt_schema_version"),
    _column("authority_instance_id"),
    _column("genesis_record_hash"),
    _column("authority_lineage_hash"),
    _column("publication_receipt_id"),
    _column("publication_ordinal", SqlType.INTEGER),
    _column("previous_record_hash"),
    _column("family_id"),
    _column("selection_scope_id"),
    _column("selection_decision_id"),
    _column("winner_config_hash", nullable=True),
    _column("winner_evaluation_receipt_hash", nullable=True),
    _column("closure_manifest_hash"),
    _column("closure_status"),
    _column("attempt_status"),
    _column("family_assessment_conclusion_hash"),
    _column("ordered_grid_assessment_record_hashes_json"),
    _column("ordered_grid_assessment_record_hashes_hash"),
    _column("minimum_replication_count", SqlType.INTEGER, nullable=True),
    _column("assessment_status"),
    _column("diagnostic_verdict"),
    _column("decision_class"),
    _column("promotion_effect"),
    _column("artifact_kind"),
    _column("artifact_core_hash"),
    _column("artifact_core_scan_receipt_hash"),
    _column("published_artifact_envelope_hash"),
    _column("target_content_addressed_path"),
    _column("public_artifact_scan_policy_id"),
    _column("dlp_policy_id"),
    _column("dlp_policy_version"),
    _column("dlp_policy_hash"),
    _column("scanner_implementation"),
    _column("scanner_version"),
    _column("scan_status"),
    _column("scan_result_hash"),
    _column("envelope_scanned_at_us", SqlType.INTEGER),
    _column("exporter_event_hash"),
    _column("exporter_authority_head"),
    _column("failure_reasons_json"),
    _column("published_at_us", SqlType.INTEGER),
    _column("receipt_record_hash"),
)

_ROW_HASH_FIELDS = tuple(
    column.name for column in _COLUMNS if column.name != "receipt_record_hash"
)
C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS = _ROW_HASH_FIELDS


def _value(column: str, source: RowSource = RowSource.CURRENT) -> ColumnValue:
    return ColumnValue(column=column, source=source)


def _literal(value: object) -> LiteralValue:
    return LiteralValue(value=value)


def _compare_values(
    left: ColumnValue,
    operator: ComparisonOperator,
    right: ColumnValue | LiteralValue,
) -> ComparisonExpression:
    return ComparisonExpression(left=left, operator=operator, right=right)


def _comparison(
    column: str,
    operator: ComparisonOperator,
    expected: object,
    *,
    source: RowSource = RowSource.CURRENT,
) -> ComparisonExpression:
    return _compare_values(_value(column, source), operator, _literal(expected))


def _logical(
    operator: LogicalOperator,
    *operands: ComparisonExpression
    | MembershipExpression
    | NullExpression
    | LogicalExpression,
) -> LogicalExpression:
    return LogicalExpression(operator=operator, operands=operands)


def _membership(column: str, values: tuple[str, ...]) -> MembershipExpression:
    return MembershipExpression(
        value=_value(column),
        values=tuple(_literal(value) for value in values),
    )


def _null(column: str, operator: NullOperator) -> NullExpression:
    return NullExpression(value=_value(column), operator=operator)


_RESOLVED_VERDICTS = (
    "SUPPORTS_SUPERIORITY",
    "DOES_NOT_SUPPORT_SUPERIORITY",
    "METHOD_DISAGREEMENT",
    "MONTE_CARLO_UNRESOLVED",
)

_ASSESSED_BRANCH = _logical(
    LogicalOperator.AND,
    _comparison("closure_status", ComparisonOperator.EQ, "COMPLETE"),
    _comparison("attempt_status", ComparisonOperator.EQ, "COMPLETED"),
    _comparison("assessment_status", ComparisonOperator.EQ, "ASSESSED"),
    _membership("diagnostic_verdict", _RESOLVED_VERDICTS),
    _comparison("minimum_replication_count", ComparisonOperator.GE, 10_000),
    _comparison(
        "ordered_grid_assessment_record_hashes_json",
        ComparisonOperator.NE,
        "[]",
    ),
    _null("winner_config_hash", NullOperator.IS_NOT_NULL),
    _null("winner_evaluation_receipt_hash", NullOperator.IS_NOT_NULL),
    _comparison("failure_reasons_json", ComparisonOperator.EQ, "[]"),
)

_COMPLETE_NOT_ASSESSABLE_BRANCH = _logical(
    LogicalOperator.AND,
    _comparison("closure_status", ComparisonOperator.EQ, "COMPLETE"),
    _comparison("attempt_status", ComparisonOperator.EQ, "COMPLETED"),
    _comparison("assessment_status", ComparisonOperator.EQ, "NOT_ASSESSABLE"),
    _comparison("diagnostic_verdict", ComparisonOperator.EQ, "NOT_APPLICABLE"),
    _null("minimum_replication_count", NullOperator.IS_NULL),
    _null("winner_config_hash", NullOperator.IS_NOT_NULL),
    _null("winner_evaluation_receipt_hash", NullOperator.IS_NOT_NULL),
    _comparison("failure_reasons_json", ComparisonOperator.NE, "[]"),
)

_CLOSURE_NOT_ASSESSABLE_BRANCH = _logical(
    LogicalOperator.AND,
    _comparison("closure_status", ComparisonOperator.EQ, "NOT_ASSESSABLE"),
    _membership("attempt_status", ("NOT_STARTED", "INCOMPLETE")),
    _comparison("assessment_status", ComparisonOperator.EQ, "NOT_ASSESSABLE"),
    _comparison("diagnostic_verdict", ComparisonOperator.EQ, "NOT_APPLICABLE"),
    _null("minimum_replication_count", NullOperator.IS_NULL),
    _null("winner_config_hash", NullOperator.IS_NULL),
    _null("winner_evaluation_receipt_hash", NullOperator.IS_NULL),
    _comparison(
        "ordered_grid_assessment_record_hashes_json",
        ComparisonOperator.EQ,
        "[]",
    ),
    _comparison("failure_reasons_json", ComparisonOperator.NE, "[]"),
)

_LOCAL_BRANCH = _logical(
    LogicalOperator.AND,
    _comparison("promotion_effect", ComparisonOperator.EQ, "NONE"),
    _logical(
        LogicalOperator.OR,
        _ASSESSED_BRANCH,
        _COMPLETE_NOT_ASSESSABLE_BRANCH,
        _CLOSURE_NOT_ASSESSABLE_BRANCH,
    ),
)

_CHECKS = (
    CheckConstraintSpec(
        "artifact_publication_receipts_record_type",
        _comparison("record_type", ComparisonOperator.EQ, _RECORD_TYPE),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_schema_version",
        _comparison(
            "receipt_schema_version",
            ComparisonOperator.EQ,
            _RECEIPT_SCHEMA_VERSION,
        ),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_positive_ordinal",
        _comparison("publication_ordinal", ComparisonOperator.GT, 0),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_positive_time",
        _comparison("published_at_us", ComparisonOperator.GT, 0),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_positive_envelope_scan_time",
        _comparison("envelope_scanned_at_us", ComparisonOperator.GT, 0),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_scan_precedes_publication",
        _compare_values(
            _value("envelope_scanned_at_us"),
            ComparisonOperator.LE,
            _value("published_at_us"),
        ),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_artifact_kind",
        _comparison("artifact_kind", ComparisonOperator.EQ, _ARTIFACT_KIND),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_attempt_status_domain",
        _membership("attempt_status", ("COMPLETED", "NOT_STARTED", "INCOMPLETE")),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_decision_class_domain",
        _membership("decision_class", ("PUBLISHABLE", "DIAGNOSTIC_ONLY")),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_scan_allow_only",
        _comparison("scan_status", ComparisonOperator.EQ, "ALLOW"),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_promotion_none",
        _comparison("promotion_effect", ComparisonOperator.EQ, "NONE"),
    ),
    CheckConstraintSpec(
        "artifact_publication_receipts_exact_branch",
        _LOCAL_BRANCH,
    ),
)


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


_FOREIGN_KEYS = (
    _foreign_key(
        ("authority_instance_id", "genesis_record_hash"),
        "family_authority_genesis",
        ("authority_instance_id", "record_hash"),
    ),
    _foreign_key(
        ("authority_instance_id", "family_id", "closure_manifest_hash"),
        "data_snooping_family_closures",
        ("authority_instance_id", "family_id", "record_hash"),
    ),
    _foreign_key(
        (
            "authority_instance_id",
            "family_id",
            "family_assessment_conclusion_hash",
        ),
        "data_snooping_assessments",
        ("authority_instance_id", "family_id", "record_hash"),
    ),
    _foreign_key(
        ("exporter_event_hash",),
        "research_authority_events",
        ("record_hash",),
    ),
)


def _index(name: str, columns: tuple[str, ...], *, unique: bool) -> IndexSpec:
    return IndexSpec(
        name=name,
        unique=unique,
        columns=tuple(
            IndexColumnSpec(
                column=column,
                direction=SortDirection.ASC,
                collation=Collation.BINARY,
            )
            for column in columns
        ),
    )


_INDEXES = (
    _index(
        "unique_artifact_publication_receipts_record_hash",
        ("receipt_record_hash",),
        unique=True,
    ),
    _index(
        "unique_artifact_publication_receipts_authority_ordinal",
        ("authority_instance_id", "publication_ordinal"),
        unique=True,
    ),
    _index(
        "unique_artifact_publication_receipts_family",
        ("authority_instance_id", "family_id"),
        unique=True,
    ),
    _index(
        "unique_artifact_publication_receipts_envelope",
        ("authority_instance_id", "published_artifact_envelope_hash"),
        unique=True,
    ),
    _index(
        "unique_artifact_publication_receipts_path",
        ("target_content_addressed_path",),
        unique=True,
    ),
    _index(
        "unique_artifact_publication_receipts_exporter_event",
        ("exporter_event_hash",),
        unique=True,
    ),
    _index(
        "ordinary_artifact_publication_receipts_conclusion",
        ("family_assessment_conclusion_hash",),
        unique=False,
    ),
)


def _bindings(*pairs: str | tuple[str, str]) -> tuple[LookupBinding, ...]:
    normalized: list[LookupBinding] = []
    for item in pairs:
        related, local = (item, item) if isinstance(item, str) else item
        normalized.append(
            LookupBinding(
                related_column=related,
                value=_value(local, RowSource.NEW),
            )
        )
    return tuple(normalized)


_CHAIN_GUARD = ChainPredecessorExpression(
    table=_TABLE,
    partition_bindings=_bindings("authority_instance_id"),
    ordinal_column="publication_ordinal",
    current_ordinal=_value("publication_ordinal", RowSource.NEW),
    record_hash_column="receipt_record_hash",
    current_previous_hash=_value("previous_record_hash", RowSource.NEW),
    genesis_hash=_value("genesis_record_hash", RowSource.NEW),
    negated=True,
)

_CLOSURE_GUARD = RelationExistsExpression(
    table="data_snooping_family_closures",
    bindings=_bindings(
        "authority_instance_id",
        "family_id",
        ("record_hash", "closure_manifest_hash"),
        "closure_status",
        "selection_scope_id",
        "selection_decision_id",
        "winner_config_hash",
        "winner_evaluation_receipt_hash",
    ),
    predicate=_compare_values(
        _value("closed_at_us", RowSource.RELATED),
        ComparisonOperator.LE,
        _value("published_at_us", RowSource.NEW),
    ),
    negated=True,
)

_CONCLUSION_GUARD = RelationExistsExpression(
    table="data_snooping_assessments",
    bindings=_bindings(
        "authority_instance_id",
        "family_id",
        ("record_hash", "family_assessment_conclusion_hash"),
        "closure_manifest_hash",
        (
            "ordered_latest_grid_assessment_hashes_json",
            "ordered_grid_assessment_record_hashes_json",
        ),
        (
            "ordered_latest_grid_assessment_hashes_hash",
            "ordered_grid_assessment_record_hashes_hash",
        ),
        "minimum_replication_count",
        "assessment_status",
        "diagnostic_verdict",
        "promotion_effect",
    ),
    predicate=_compare_values(
        _value("concluded_at_us", RowSource.RELATED),
        ComparisonOperator.LE,
        _value("published_at_us", RowSource.NEW),
    ),
    negated=True,
)

_GRID_HISTORY_HASH_GUARD = CanonicalJsonPayloadHashExpression(
    document=_value(
        "ordered_grid_assessment_record_hashes_json",
        RowSource.NEW,
    ),
    excluded_paths=(),
    encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
    algorithm=RowHashAlgorithm.SHA256,
    operator=ComparisonOperator.NE,
    expected_hash=_value(
        "ordered_grid_assessment_record_hashes_hash",
        RowSource.NEW,
    ),
)

_EXPORTER_GUARD = RelationExistsExpression(
    table="research_authority_events",
    bindings=_bindings(
        "authority_instance_id",
        ("record_hash", "exporter_event_hash"),
        ("subject_id", "publication_receipt_id"),
        ("subject_record_hash", "published_artifact_envelope_hash"),
    ),
    predicate=_logical(
        LogicalOperator.AND,
        _comparison(
            "action",
            ComparisonOperator.EQ,
            "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1",
            source=RowSource.RELATED,
        ),
        _comparison(
            "subject_record_type",
            ComparisonOperator.EQ,
            "PUBLISHED_ARTIFACT_ENVELOPE_V1",
            source=RowSource.RELATED,
        ),
        _compare_values(
            _value("event_at_us", RowSource.RELATED),
            ComparisonOperator.LE,
            _value("published_at_us", RowSource.NEW),
        ),
        _compare_values(
            _value("record_hash", RowSource.RELATED),
            ComparisonOperator.EQ,
            _value("exporter_authority_head", RowSource.NEW),
        ),
    ),
    negated=True,
)


def _no_mutation(
    name: str,
    kind: TriggerPolicyKind,
    event: TriggerEvent,
    error_code: str,
) -> TriggerPolicySpec:
    return TriggerPolicySpec(
        name=name,
        kind=kind,
        event=event,
        error_code=error_code,
        timing=TriggerTiming.BEFORE,
        effect=TriggerEffect.ABORT,
    )


def _insert_guard(
    name: str,
    error_code: str,
    violation_when: ChainPredecessorExpression
    | RelationExistsExpression
    | CanonicalJsonPayloadHashExpression,
) -> TriggerPolicySpec:
    return TriggerPolicySpec(
        name=name,
        kind=TriggerPolicyKind.INSERT_GUARD,
        event=TriggerEvent.INSERT,
        error_code=error_code,
        violation_when=violation_when,
        timing=TriggerTiming.BEFORE,
        effect=TriggerEffect.ABORT,
    )


_TRIGGERS = (
    _no_mutation(
        "trg_no_update_artifact_publication_receipts",
        TriggerPolicyKind.NO_UPDATE,
        TriggerEvent.UPDATE,
        "ARTIFACT_PUBLICATION_RECEIPTS_APPEND_ONLY_UPDATE",
    ),
    _no_mutation(
        "trg_no_delete_artifact_publication_receipts",
        TriggerPolicyKind.NO_DELETE,
        TriggerEvent.DELETE,
        "ARTIFACT_PUBLICATION_RECEIPTS_APPEND_ONLY_DELETE",
    ),
    _insert_guard(
        "trg_guard_artifact_publication_receipts_chain",
        "PUBLICATION_CHAIN_PREDECESSOR_EXACT",
        _CHAIN_GUARD,
    ),
    _insert_guard(
        "trg_guard_artifact_publication_receipts_closure",
        "PUBLICATION_CLOSURE_EXACT",
        _CLOSURE_GUARD,
    ),
    _insert_guard(
        "trg_guard_artifact_publication_receipts_conclusion",
        "PUBLICATION_FAMILY_CONCLUSION_EXACT",
        _CONCLUSION_GUARD,
    ),
    _insert_guard(
        "trg_guard_artifact_publication_receipts_grid_hash",
        "PUBLICATION_GRID_HISTORY_HASH_EXACT",
        _GRID_HISTORY_HASH_GUARD,
    ),
    _insert_guard(
        "trg_guard_artifact_publication_receipts_exporter",
        "PUBLICATION_EXPORTER_EVENT_EXACT",
        _EXPORTER_GUARD,
    ),
)


def _rule(
    rule_id: str,
    kind: VerificationRuleKind,
    columns: tuple[str, ...] = (),
    *,
    referenced_table: str | None = None,
    predicate: LogicalExpression | None = None,
    verifier_code: str,
) -> VerificationRuleSpec:
    return VerificationRuleSpec(
        rule_id=rule_id,
        kind=kind,
        columns=columns,
        referenced_table=referenced_table,
        predicate=predicate,
        verifier_code=verifier_code,
    )


_RULES = (
    _rule(
        "publication_receipt_genesis_binding",
        VerificationRuleKind.GENESIS_BINDING,
        ("authority_instance_id", "genesis_record_hash"),
        verifier_code="c9.verify_publication_genesis_binding_v1",
    ),
    _rule(
        "publication_receipt_ordered_chain",
        VerificationRuleKind.ORDERED_CHAIN,
        (
            "authority_instance_id",
            "genesis_record_hash",
            "publication_ordinal",
            "previous_record_hash",
            "receipt_record_hash",
        ),
        verifier_code="c9.verify_complete_publication_chain_v1",
    ),
    _rule(
        "publication_receipt_closure_exact",
        VerificationRuleKind.CROSS_TABLE_REFERENCE,
        (
            "authority_instance_id",
            "family_id",
            "closure_manifest_hash",
            "closure_status",
            "selection_scope_id",
            "selection_decision_id",
            "winner_config_hash",
            "winner_evaluation_receipt_hash",
        ),
        referenced_table="data_snooping_family_closures",
        verifier_code="c9.verify_publication_closure_v1",
    ),
    _rule(
        "publication_receipt_family_conclusion_exact",
        VerificationRuleKind.CROSS_TABLE_REFERENCE,
        (
            "authority_instance_id",
            "family_id",
            "closure_manifest_hash",
            "family_assessment_conclusion_hash",
            "ordered_grid_assessment_record_hashes_json",
            "ordered_grid_assessment_record_hashes_hash",
            "minimum_replication_count",
            "assessment_status",
            "diagnostic_verdict",
            "promotion_effect",
        ),
        referenced_table="data_snooping_assessments",
        verifier_code="c9.verify_publication_family_conclusion_v1",
    ),
    _rule(
        "publication_receipt_c8_semantic_recomputation",
        VerificationRuleKind.CONTENT_BINDING,
        (
            "closure_manifest_hash",
            "family_assessment_conclusion_hash",
            "ordered_grid_assessment_record_hashes_json",
            "ordered_grid_assessment_record_hashes_hash",
            "minimum_replication_count",
            "assessment_status",
            "diagnostic_verdict",
        ),
        referenced_table="data_snooping_assessments",
        verifier_code="c8.verify_family_assessment_v1",
    ),
    _rule(
        "publication_receipt_final_scan_allow",
        VerificationRuleKind.CONTENT_BINDING,
        (
            "published_artifact_envelope_hash",
            "public_artifact_scan_policy_id",
            "dlp_policy_id",
            "dlp_policy_version",
            "dlp_policy_hash",
            "scanner_implementation",
            "scanner_version",
            "scan_status",
            "scan_result_hash",
            "envelope_scanned_at_us",
        ),
        verifier_code="c9.verify_exact_envelope_scan_allow_v1",
    ),
    _rule(
        "publication_receipt_exporter_event_exact",
        VerificationRuleKind.CROSS_TABLE_REFERENCE,
        (
            "authority_instance_id",
            "publication_receipt_id",
            "published_artifact_envelope_hash",
            "exporter_event_hash",
            "exporter_authority_head",
            "published_at_us",
        ),
        referenced_table="research_authority_events",
        verifier_code="c9.verify_exporter_event_v1",
    ),
    _rule(
        "publication_receipt_branch",
        VerificationRuleKind.STATE_BRANCH,
        (
            "closure_status",
            "attempt_status",
            "assessment_status",
            "diagnostic_verdict",
            "minimum_replication_count",
            "ordered_grid_assessment_record_hashes_json",
            "winner_config_hash",
            "winner_evaluation_receipt_hash",
            "decision_class",
            "promotion_effect",
            "failure_reasons_json",
        ),
        predicate=_LOCAL_BRANCH,
        verifier_code="c9.verify_publication_branch_v1",
    ),
    _rule(
        "publication_receipt_projection_totality",
        VerificationRuleKind.PUBLICATION_TOTALITY,
        (
            "authority_instance_id",
            "genesis_record_hash",
            "family_id",
            "decision_class",
            "assessment_status",
            "publication_receipt_id",
            "publication_ordinal",
            "previous_record_hash",
            "receipt_record_hash",
            "published_artifact_envelope_hash",
            "target_content_addressed_path",
        ),
        verifier_code="c9.verify_complete_acyclic_projection_v1",
    ),
)

_CHAIN = ChainSpec(
    partition_key_columns=("authority_instance_id",),
    ordinal_column="publication_ordinal",
    genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
    previous_hash_column="previous_record_hash",
    record_hash_column="receipt_record_hash",
    terminal_head_rule=ChainHeadRule.LATEST_RECORD_HASH,
)

_TABLE_SPEC = AuthorityTableSpec(
    table_name=_TABLE,
    columns=_COLUMNS,
    primary_key_columns=("publication_receipt_id",),
    row_hash=RowHashSpec(
        canonical_fields=_ROW_HASH_FIELDS,
        version_literal=_RECORD_TYPE,
        record_hash_column="receipt_record_hash",
        algorithm=RowHashAlgorithm.SHA256,
        encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
    ),
    foreign_keys=_FOREIGN_KEYS,
    indexes=_INDEXES,
    checks=_CHECKS,
    triggers=_TRIGGERS,
    chain=_CHAIN,
    verification_rules=_RULES,
)


_C6_PUBLICATION_CONTRACT_FIELDS = (
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

_RECEIPT_FIELDS = tuple(column.name for column in _COLUMNS)

_ARTIFACT_CORE_FIELDS = (
    "record_type",
    "artifact_kind",
    "artifact_schema_version",
    "canonical_json_version",
    "authority_instance_id",
    "genesis_record_hash",
    "authority_lineage_hash",
    "family_id",
    "selection_scope_id",
    "selection_decision_id",
    "winner_config_hash",
    "winner_evaluation_receipt_hash",
    "decision_class",
    "declaration_record_hash",
    "freeze_record_hash",
    "closure_manifest_hash",
    "closure_status",
    "assessment_status",
    "diagnostic_verdict",
    "promotion_effect",
    "ordered_grid_assessment_record_hashes",
    "family_assessment_conclusion_hash",
    "authority_projection",
    "authority_projection_hash",
    "pre_publication_research_authority_head",
    "selection_scope_evidence",
    "selection_decision_evidence",
    "evaluation_batch_evidence",
    "ordered_candidate_receipt_evidence",
    "benchmark_receipt_evidence",
    "trial_ledger_proof_evidence",
    "family_schema_v3_manifest_sidecar",
    "trial_ledger_history_sidecar",
    "trial_ledger_snapshot_sidecar",
    "trial_ledger_seal_sidecar",
    "observed_pit_evidence",
    "activation_code_manifest_evidence",
    "code_manifest_evidence",
    "start_execution_attestation_evidence",
    "terminal_execution_attestation_evidence",
    "immutable_artifact_lease_evidence",
    "ordered_candidate_disposition_evidence",
    "public_proposals",
    "candidate_disposition_record_hashes",
    "ordered_period_keys",
    "ordered_candidate_config_hashes",
    "candidate_gross_returns",
    "candidate_costs",
    "candidate_net_returns",
    "candidate_turnovers",
    "benchmark_gross_returns",
    "benchmark_costs",
    "benchmark_net_returns",
    "benchmark_turnovers",
    "evaluation_batch_receipt_hash",
    "benchmark_receipt_hash",
    "ordered_candidate_receipt_hashes",
    "trial_ledger_proof_hash",
    "failure_reasons",
)
_ARTIFACT_CORE_SCAN_RECEIPT_FIELDS = (
    "record_type",
    "scan_receipt_schema_version",
    "scan_target",
    "artifact_core_hash",
    "artifact_core_byte_length",
    "public_artifact_scan_policy_id",
    "dlp_policy_id",
    "dlp_policy_version",
    "dlp_policy_hash",
    "scanner_implementation",
    "scanner_version",
    "scan_status",
    "scan_result_hash",
    "scanned_at_us",
    "scan_receipt_hash",
)
_PUBLISHED_ARTIFACT_ENVELOPE_FIELDS = (
    "record_type",
    "envelope_schema_version",
    "artifact_core",
    "artifact_core_scan_receipt",
)
_PUBLICATION_PROJECTION_FIELDS = (
    "record_type",
    "projection_schema_version",
    "publication_receipt_schema_version",
    "authority_instance_id",
    "genesis_record_hash",
    "authority_lineage_hash",
    "authority_genesis",
    "publication_receipt",
    "publication_chain_prefix",
    "pre_publication_research_authority_head",
    "pre_publication_research_authority_row_count",
    "exporter_authority_event_suffix",
    "exporter_authority_head",
    "terminal_publication_receipt_id",
    "terminal_publication_record_hash",
    "terminal_chain_head",
)

_ENVELOPE_GRAPH_DERIVATION_ID = "c9.artifact_envelope.exact_byte_graph"
_PROJECTION_GRAPH_DERIVATION_ID = "c9.publication_projection.exact_byte_graph"

C9_ARTIFACT_GRAPH_DERIVATIONS = (
    ContentAddressedArtifactGraphDerivationSpec(
        derivation_id=_ENVELOPE_GRAPH_DERIVATION_ID,
        locator=ArtifactGraphLocatorSpec(
            kind=ArtifactGraphLocatorSourceKind.PERSISTED_ROW,
            binding_table=_TABLE,
            hash_field="published_artifact_envelope_hash",
            path_field="target_content_addressed_path",
        ),
        contract_surfaces=(
            ArtifactGraphContractSpec(
                contract_type="ArtifactCore",
                field_names=_ARTIFACT_CORE_FIELDS,
            ),
            ArtifactGraphContractSpec(
                contract_type="ArtifactCoreScanReceipt",
                field_names=_ARTIFACT_CORE_SCAN_RECEIPT_FIELDS,
            ),
            ArtifactGraphContractSpec(
                contract_type="PublishedArtifactEnvelope",
                field_names=_PUBLISHED_ARTIFACT_ENVELOPE_FIELDS,
            ),
        ),
        verifier_code="c9.verify_exact_envelope_graph_v1",
    ),
    ContentAddressedArtifactGraphDerivationSpec(
        derivation_id=_PROJECTION_GRAPH_DERIVATION_ID,
        locator=ArtifactGraphLocatorSpec(
            kind=ArtifactGraphLocatorSourceKind.CONTRACT_INPUT,
            contract_type="ArtifactPublicationProjectionRef",
            hash_field="publication_projection_hash",
            path_field="publication_projection_path",
        ),
        contract_surfaces=(
            ArtifactGraphContractSpec(
                contract_type="ArtifactPublicationReceiptProjection",
                field_names=_PUBLICATION_PROJECTION_FIELDS,
            ),
            ArtifactGraphContractSpec(
                contract_type="ArtifactPublicationProjectionRef",
                field_names=("record_type", "projection_schema_version"),
            ),
        ),
        verifier_code="c9.verify_complete_acyclic_projection_v1",
    ),
)

_EVIDENCE_REF_ALIASES = {
    "artifact_kind": "artifact_kind",
    "authority_instance_id": "authority_instance_id",
    "genesis_record_hash": "genesis_record_hash",
    "authority_lineage_hash": "authority_lineage_hash",
    "family_id": "family_id",
    "selection_scope_id": "selection_scope_id",
    "selection_decision_id": "selection_decision_id",
    "decision_class": "decision_class",
    "winner_config_hash": "winner_config_hash",
    "closure_manifest_hash": "closure_manifest_hash",
    "family_assessment_conclusion_hash": "family_assessment_conclusion_hash",
    "assessment_status": "assessment_status",
    "diagnostic_verdict": "diagnostic_verdict",
    "promotion_effect": "promotion_effect",
    "published_artifact_envelope_hash": "published_artifact_envelope_hash",
    "published_artifact_path": "target_content_addressed_path",
    "artifact_publication_receipt_id": "publication_receipt_id",
    "artifact_publication_receipt_hash": "receipt_record_hash",
    "publication_chain_head": "receipt_record_hash",
}

_PUBLICATION_PROJECTION_REF_ALIASES = {
    "authority_instance_id": "authority_instance_id",
    "genesis_record_hash": "genesis_record_hash",
    "authority_lineage_hash": "authority_lineage_hash",
    "family_id": "family_id",
    "decision_class": "decision_class",
    "assessment_status": "assessment_status",
    "artifact_publication_receipt_id": "publication_receipt_id",
    "artifact_publication_receipt_hash": "receipt_record_hash",
    "publication_chain_head": "receipt_record_hash",
}

C9_CONTRACT_COVERAGE = (
    *(
        ContractFieldCoverageSpec(
            contract_type="ArtifactPublicationReceipt",
            field_name=field,
            kind=CoverageKind.COLUMN,
            table_name=_TABLE,
            target_field=field,
        )
        for field in _RECEIPT_FIELDS
    ),
    *(
        ContractFieldCoverageSpec(
            contract_type="DataSnoopingEvidenceRef",
            field_name=field,
            kind=CoverageKind.COLUMN,
            table_name=_TABLE,
            target_field=target,
        )
        for field, target in _EVIDENCE_REF_ALIASES.items()
    ),
    *(
        ContractFieldCoverageSpec(
            contract_type="ArtifactPublicationProjectionRef",
            field_name=field,
            kind=CoverageKind.COLUMN,
            table_name=_TABLE,
            target_field=target,
        )
        for field, target in _PUBLICATION_PROJECTION_REF_ALIASES.items()
    ),
    *(
        ContractFieldCoverageSpec(
            contract_type=surface.contract_type,
            field_name=field,
            kind=CoverageKind.NON_PERSISTED_DERIVATION,
            derivation_id=graph.derivation_id,
        )
        for graph in C9_ARTIFACT_GRAPH_DERIVATIONS
        for surface in graph.contract_surfaces
        for field in surface.field_names
    ),
)

C9_REQUIRED_EXTENSION_SPECS = (
    RequiredTableExtensionSpec(
        extension_id="C6_PUBLICATION_RECEIPT_EMBEDDED_REFS_V1",
        target_table=_TABLE,
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.publication_receipt.embedded_refs",
    ),
)

_REQUIRED_CONTRACT_SURFACES = (
    RequiredContractSurfaceSpec(
        contract_type="ArtifactPublicationReceipt",
        field_names=_C6_PUBLICATION_CONTRACT_FIELDS,
        provider_component_id=_C6_COMPONENT,
        required_capability="c6.publication_receipt.embedded_refs",
    ),
)

C9_AUTHORITY_TABLE_SPECS = (_TABLE_SPEC,)

C9_SCHEMA_COMPONENT = SchemaComponentSpec(
    component_id=_COMPONENT_ID,
    capabilities=_CAPABILITIES,
    dependencies=(
        ComponentDependencySpec(
            component_id=_C4_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=(
                "c4.questions_lineage_scope_decisions_exposures.core",
            ),
        ),
        ComponentDependencySpec(
            component_id=_C5_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=(
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
                "c7.global_oos_consumption.attempt_binding",
            ),
        ),
        ComponentDependencySpec(
            component_id=_C8_COMPONENT,
            kind=DependencyKind.CROSS_TABLE_REFERENCE,
            required_capabilities=(
                "c8.family_closure.core",
                "c8.grid_assessment.core",
                "c8.assessment.family_conclusion",
            ),
        ),
    ),
    tables=C9_AUTHORITY_TABLE_SPECS,
    required_extensions=C9_REQUIRED_EXTENSION_SPECS,
    coverage=C9_CONTRACT_COVERAGE,
    artifact_graph_derivations=C9_ARTIFACT_GRAPH_DERIVATIONS,
    required_contract_surfaces=_REQUIRED_CONTRACT_SURFACES,
)


__all__ = [
    "C9_A9_EXTERNAL_INPUT_REQUIREMENTS",
    "C9_AUTHORITY_TABLE_SPECS",
    "C9_ARTIFACT_GRAPH_DERIVATIONS",
    "C9_CONTRACT_COVERAGE",
    "C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS",
    "C9_REQUIRED_EXTENSION_SPECS",
    "C9_SCHEMA_COMPONENT",
]
