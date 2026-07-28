"""Private Pass-C schema semantics for family-authority slices C5 and C6.

The module freezes typed values only.  C5 owns five append-only business-table
specifications and the later disposition branch contract.  C6 owns no table;
it contributes five explicit embedded-reference surfaces and additive table
extensions to the components that own those rows.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum

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
    ExternalAuthorityContinuitySpec,
    ExternalAuthorityReferenceKind,
    ExternalContinuityBranchSelectorSpec,
    ExternalContinuityChronologyBranchSpec,
    ExternalContinuityProviderInputSourceKind,
    ExternalContinuityProviderInputSpec,
    ExternalContinuityReadBindingSpec,
    ExternalContinuityRowInputKind,
    ExternalContinuityRowInputSpec,
    ExternalContinuitySemanticKind,
    ExternalContinuitySemanticProfileSpec,
    ExternalContinuityStatusBranchKind,
    ExternalContinuityTimeComparisonSpec,
    ExternalFieldBindingSpec,
    ExternalProviderCapabilityRequirementSpec,
    ExternalProviderFieldEqualitySpec,
    ExternalProviderResolutionStage,
    ForeignKeyAction,
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


_TOKEN_RE = _re.compile(r"^[A-Z][A-Z0-9_]*$")
_IDENTIFIER_RE = _re.compile(r"^[a-z][a-z0-9_]*$")
_VERIFIER_CODE_RE = _re.compile(r"^[a-z][a-z0-9_.]*$")


def _column(
    name: str,
    sql_type: SqlType = SqlType.TEXT,
    *,
    nullable: bool = False,
) -> ColumnSpec:
    return ColumnSpec(name=name, sql_type=sql_type, nullable=nullable)


def _business_columns(*columns: ColumnSpec) -> tuple[ColumnSpec, ...]:
    return (
        _column("record_type"),
        _column("authority_instance_id"),
        _column("genesis_record_hash"),
        *columns,
        _column("record_hash"),
    )


def _comparison(
    column: str,
    operator: ComparisonOperator,
    literal: None | int | float | str | bytes,
    *,
    source: RowSource = RowSource.CURRENT,
) -> ComparisonExpression:
    return ComparisonExpression(ColumnValue(column, source), operator, LiteralValue(literal))


def _logical(
    operator: LogicalOperator,
    *operands: PredicateExpression,
) -> LogicalExpression:
    return LogicalExpression(operator, tuple(operands))


def _record_type_check(record_type: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        "record_type_literal",
        _comparison("record_type", ComparisonOperator.EQ, record_type),
    )


def _positive_check(column: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{column}_positive",
        _comparison(column, ComparisonOperator.GT, 0),
    )


def _nonnegative_check(column: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{column}_nonnegative",
        _comparison(column, ComparisonOperator.GE, 0),
    )


def _nullable_positive_check(column: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{column}_nullable_positive",
        _logical(
            LogicalOperator.OR,
            NullExpression(ColumnValue(column), NullOperator.IS_NULL),
            _comparison(column, ComparisonOperator.GT, 0),
        ),
    )


def _immutable_triggers(table_name: str) -> tuple[TriggerPolicySpec, ...]:
    return (
        TriggerPolicySpec(
            name=f"{table_name}_no_update",
            kind=TriggerPolicyKind.NO_UPDATE,
            event=TriggerEvent.UPDATE,
            error_code="AUTHORITY_ROW_IMMUTABLE",
        ),
        TriggerPolicySpec(
            name=f"{table_name}_no_delete",
            kind=TriggerPolicyKind.NO_DELETE,
            event=TriggerEvent.DELETE,
            error_code="AUTHORITY_ROW_IMMUTABLE",
        ),
    )


def _relation(
    table: str,
    local_to_related: tuple[tuple[str, str], ...],
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
            for local, related in local_to_related
        ),
        predicate=predicate,
        negated=negated,
    )


def _guard(
    table_name: str,
    error_code: str,
    violation_when: PredicateExpression,
) -> TriggerPolicySpec:
    return TriggerPolicySpec(
        name=f"{table_name}_{error_code.lower()}",
        kind=TriggerPolicyKind.INSERT_GUARD,
        event=TriggerEvent.INSERT,
        error_code=error_code,
        violation_when=violation_when,
    )


def _genesis_fk() -> ForeignKeySpec:
    return ForeignKeySpec(
        columns=("authority_instance_id", "genesis_record_hash"),
        referenced_table="family_authority_genesis",
        referenced_columns=("authority_instance_id", "record_hash"),
        on_update=ForeignKeyAction.RESTRICT,
        on_delete=ForeignKeyAction.RESTRICT,
    )


def _index(
    name: str,
    *columns: str,
    unique: bool = False,
) -> IndexSpec:
    return IndexSpec(
        name=name,
        columns=tuple(IndexColumnSpec(column) for column in columns),
        unique=unique,
    )


def _row_hash(columns: tuple[ColumnSpec, ...], record_type: str) -> RowHashSpec:
    return RowHashSpec(
        canonical_fields=tuple(column.name for column in columns if column.name != "record_hash"),
        version_literal=record_type,
        record_hash_column="record_hash",
    )


_SEARCH_RECORD_TYPE = "DATA_SNOOPING_FAMILY_SEARCH_V1"
_PROPOSAL_REGISTRATION_RECORD_TYPE = "DATA_SNOOPING_PROPOSAL_REGISTRATION_V1"
_PROPOSAL_RECORD_TYPE = "DATA_SNOOPING_PROPOSAL_V1"
_HYPOTHESIS_RECORD_TYPE = "DATA_SNOOPING_HYPOTHESIS_V1"
_EMISSION_SEAL_RECORD_TYPE = "DATA_SNOOPING_SEARCH_EMISSION_SEAL_V1"


_FAMILY_SEARCH_COLUMNS = _business_columns(
    _column("family_id"),
    _column("search_id"),
    _column("research_program_id"),
    _column("search_kind"),
    _column("generator_seed", SqlType.INTEGER),
    _column("proposal_budget", SqlType.INTEGER),
    _column("stopping_rule_hash"),
    _column("external_ingestion_deadline_us", SqlType.INTEGER),
    _column("search_authority_id"),
    _column("search_authority_head"),
    _column("linked_at_us", SqlType.INTEGER),
    _column("link_ordinal", SqlType.INTEGER),
    _column("prev_record_hash"),
)

_PROPOSAL_REGISTRATION_COLUMNS = _business_columns(
    _column("proposal_id"),
    _column("family_id"),
    _column("search_id"),
    _column("emission_ordinal", SqlType.INTEGER),
    _column("proposal_branch"),
    _column("identity_payload_json"),
    _column("raw_envelope_hash", nullable=True),
    _column("media_type"),
    _column("raw_byte_length", SqlType.INTEGER, nullable=True),
    _column("length_bucket"),
    _column("dlp_policy_id"),
    _column("dlp_policy_version"),
    _column("scanner_implementation"),
    _column("scanner_version"),
    _column("dlp_result_code"),
    _column("restricted_clean_blob_receipt_hash", nullable=True),
    _column("quarantine_record_id", nullable=True),
    _column("registered_at_us", SqlType.INTEGER),
    _column("prev_record_hash"),
)

_PROPOSAL_COLUMNS = _business_columns(
    _column("proposal_id"),
    _column("family_id"),
    _column("search_id"),
    _column("emission_ordinal", SqlType.INTEGER),
    _column("registration_record_hash"),
    _column("parse_status"),
    _column("canonical_proposal_json", nullable=True),
    _column("canonical_proposal_hash", nullable=True),
    _column("config_hash", nullable=True),
    _column("alpha_id", nullable=True),
    _column("parsed_at_us", SqlType.INTEGER),
)

_HYPOTHESIS_COLUMNS = _business_columns(
    _column("family_id"),
    _column("first_search_id"),
    _column("config_hash"),
    _column("alpha_id"),
    _column("canonical_hypothesis_hash"),
    _column("first_proposal_id"),
)

_EMISSION_SEAL_COLUMNS = _business_columns(
    _column("family_id"),
    _column("search_id"),
    _column("declared_proposal_budget", SqlType.INTEGER),
    _column("actual_raw_proposal_count", SqlType.INTEGER),
    _column("ordered_proposal_ids_hash"),
    _column("ordered_non_quarantined_raw_hashes_hash"),
    _column("ordered_canonical_proposal_hashes_hash"),
    _column("ordered_quarantine_record_ids_hash"),
    _column("accepted_config_set_hash"),
    _column("candidate_count", SqlType.INTEGER),
    _column("candidate_chain_head"),
    _column("terminal_reason"),
    _column("sealed_at_us", SqlType.INTEGER),
)


_PROPOSAL_CLEAN_BRANCH = CheckConstraintSpec(
    "clean_proposal_branch",
    _logical(
        LogicalOperator.OR,
        _comparison("proposal_branch", ComparisonOperator.NE, "CLEAN"),
        _logical(
            LogicalOperator.AND,
            _comparison("dlp_result_code", ComparisonOperator.EQ, "CLEAN"),
            NullExpression(ColumnValue("raw_envelope_hash"), NullOperator.IS_NOT_NULL),
            _comparison("raw_byte_length", ComparisonOperator.GT, 0),
            NullExpression(
                ColumnValue("restricted_clean_blob_receipt_hash"),
                NullOperator.IS_NOT_NULL,
            ),
            NullExpression(ColumnValue("quarantine_record_id"), NullOperator.IS_NULL),
        ),
    ),
)

_PROPOSAL_QUARANTINE_BRANCH = CheckConstraintSpec(
    "secret_quarantined_proposal_branch",
    _logical(
        LogicalOperator.OR,
        _comparison("proposal_branch", ComparisonOperator.NE, "SECRET_QUARANTINED"),
        _logical(
            LogicalOperator.AND,
            _comparison(
                "dlp_result_code",
                ComparisonOperator.EQ,
                "SECRET_QUARANTINED",
            ),
            NullExpression(ColumnValue("length_bucket"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("media_type"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("dlp_policy_id"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("dlp_policy_version"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("scanner_implementation"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("scanner_version"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("dlp_result_code"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("quarantine_record_id"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("raw_envelope_hash"), NullOperator.IS_NULL),
            NullExpression(ColumnValue("raw_byte_length"), NullOperator.IS_NULL),
            NullExpression(
                ColumnValue("restricted_clean_blob_receipt_hash"),
                NullOperator.IS_NULL,
            ),
        ),
    ),
)


_PROPOSAL_TERMINAL_PARSE_BRANCH = CheckConstraintSpec(
    "terminal_parse_branch",
    _logical(
        LogicalOperator.OR,
        _logical(
            LogicalOperator.AND,
            _comparison("parse_status", ComparisonOperator.EQ, "ACCEPTED"),
            NullExpression(
                ColumnValue("canonical_proposal_json"),
                NullOperator.IS_NOT_NULL,
            ),
            NullExpression(
                ColumnValue("canonical_proposal_hash"),
                NullOperator.IS_NOT_NULL,
            ),
            NullExpression(ColumnValue("config_hash"), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue("alpha_id"), NullOperator.IS_NOT_NULL),
        ),
        _logical(
            LogicalOperator.AND,
            _logical(
                LogicalOperator.OR,
                _comparison("parse_status", ComparisonOperator.EQ, "INVALID"),
                _comparison(
                    "parse_status",
                    ComparisonOperator.EQ,
                    "SECRET_QUARANTINED",
                ),
            ),
            NullExpression(
                ColumnValue("canonical_proposal_json"),
                NullOperator.IS_NULL,
            ),
            NullExpression(
                ColumnValue("canonical_proposal_hash"),
                NullOperator.IS_NULL,
            ),
            NullExpression(ColumnValue("config_hash"), NullOperator.IS_NULL),
            NullExpression(ColumnValue("alpha_id"), NullOperator.IS_NULL),
        ),
    ),
)


_TERMINAL_SEARCH_PREDICATE = MembershipExpression(
    ColumnValue("status", RowSource.RELATED),
    (LiteralValue("FAILED"), LiteralValue("COMPLETED")),
)


_FAMILY_SEARCH_TRIGGERS = (
    *_immutable_triggers("data_snooping_family_searches"),
    _guard(
        "data_snooping_family_searches",
        "SEARCH_NOT_STARTED",
        _relation(
            "search_attempt_events",
            (("search_id", "search_id"),),
        ),
    ),
)

_PROPOSAL_REGISTRATION_TRIGGERS = (
    *_immutable_triggers("data_snooping_proposal_registrations"),
    _guard(
        "data_snooping_proposal_registrations",
        "SEARCH_LINK_EXISTS",
        _relation(
            "data_snooping_family_searches",
            (("family_id", "family_id"), ("search_id", "search_id")),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_proposal_registrations",
        "SEARCH_NOT_TERMINAL",
        _relation(
            "search_attempt_events",
            (("search_id", "search_id"),),
            predicate=_TERMINAL_SEARCH_PREDICATE,
        ),
    ),
    _guard(
        "data_snooping_proposal_registrations",
        "REGISTRATION_WITHIN_DEADLINE",
        _relation(
            "data_snooping_family_searches",
            (("family_id", "family_id"), ("search_id", "search_id")),
            predicate=ComparisonExpression(
                ColumnValue("external_ingestion_deadline_us", RowSource.RELATED),
                ComparisonOperator.GE,
                ColumnValue("registered_at_us", RowSource.NEW),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_proposal_registrations",
        "EMISSION_WITHIN_PROPOSAL_BUDGET",
        _relation(
            "data_snooping_family_searches",
            (("family_id", "family_id"), ("search_id", "search_id")),
            predicate=ComparisonExpression(
                ColumnValue("proposal_budget", RowSource.RELATED),
                ComparisonOperator.GE,
                ColumnValue("emission_ordinal", RowSource.NEW),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_proposal_registrations",
        "SEARCH_EMISSION_NOT_SEALED",
        _relation(
            "data_snooping_search_emission_seals",
            (("family_id", "family_id"), ("search_id", "search_id")),
        ),
    ),
)

_PROPOSAL_TRIGGERS = (
    *_immutable_triggers("data_snooping_proposals"),
    _guard(
        "data_snooping_proposals",
        "SEARCH_NOT_TERMINAL",
        _relation(
            "search_attempt_events",
            (("search_id", "search_id"),),
            predicate=_TERMINAL_SEARCH_PREDICATE,
        ),
    ),
    _guard(
        "data_snooping_proposals",
        "SEARCH_EMISSION_NOT_SEALED",
        _relation(
            "data_snooping_search_emission_seals",
            (("family_id", "family_id"), ("search_id", "search_id")),
        ),
    ),
)

_HYPOTHESIS_TRIGGERS = (
    *_immutable_triggers("data_snooping_hypotheses"),
    _guard(
        "data_snooping_hypotheses",
        "PROPOSAL_CLEAN_AND_PARSED",
        _relation(
            "data_snooping_proposals",
            (
                ("family_id", "family_id"),
                ("first_search_id", "search_id"),
                ("first_proposal_id", "proposal_id"),
                ("canonical_hypothesis_hash", "canonical_proposal_hash"),
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
        "data_snooping_hypotheses",
        "SEARCH_EMISSION_NOT_SEALED",
        _relation(
            "data_snooping_search_emission_seals",
            (("family_id", "family_id"), ("first_search_id", "search_id")),
        ),
    ),
)

_EMISSION_SEAL_TRIGGERS = (
    *_immutable_triggers("data_snooping_search_emission_seals"),
    _guard(
        "data_snooping_search_emission_seals",
        "SEARCH_LINK_EXISTS",
        _relation(
            "data_snooping_family_searches",
            (("family_id", "family_id"), ("search_id", "search_id")),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_search_emission_seals",
        "SEARCH_TERMINAL",
        _relation(
            "search_attempt_events",
            (("search_id", "search_id"),),
            predicate=_TERMINAL_SEARCH_PREDICATE,
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_search_emission_seals",
        "DECLARED_BUDGET_MATCH",
        _relation(
            "data_snooping_family_searches",
            (("family_id", "family_id"), ("search_id", "search_id")),
            predicate=ComparisonExpression(
                ColumnValue("proposal_budget", RowSource.RELATED),
                ComparisonOperator.EQ,
                ColumnValue("declared_proposal_budget", RowSource.NEW),
            ),
            negated=True,
        ),
    ),
    _guard(
        "data_snooping_search_emission_seals",
        "EMISSION_COUNTS_MATCH",
        ComparisonExpression(
            ColumnValue("actual_raw_proposal_count", RowSource.NEW),
            ComparisonOperator.GT,
            ColumnValue("declared_proposal_budget", RowSource.NEW),
        ),
    ),
    _guard(
        "data_snooping_search_emission_seals",
        "PROPOSAL_CHAIN_HEAD_MATCHES",
        _logical(
            LogicalOperator.AND,
            _comparison(
                "candidate_count",
                ComparisonOperator.GT,
                0,
                source=RowSource.NEW,
            ),
            _relation(
                "search_candidates",
                (
                    ("search_id", "search_id"),
                    ("candidate_chain_head", "candidate_hash"),
                ),
                negated=True,
            ),
        ),
    ),
    _guard(
        "data_snooping_search_emission_seals",
        "NO_EXISTING_EMISSION_SEAL",
        _relation(
            "data_snooping_search_emission_seals",
            (("family_id", "family_id"), ("search_id", "search_id")),
        ),
    ),
)


_FAMILY_SEARCH_SPEC = AuthorityTableSpec(
    table_name="data_snooping_family_searches",
    columns=_FAMILY_SEARCH_COLUMNS,
    primary_key_columns=("search_id",),
    row_hash=_row_hash(_FAMILY_SEARCH_COLUMNS, _SEARCH_RECORD_TYPE),
    triggers=_FAMILY_SEARCH_TRIGGERS,
    foreign_keys=(
        _genesis_fk(),
        ForeignKeySpec(
            ("research_program_id",),
            "research_programs",
            ("research_program_id",),
            ForeignKeyAction.RESTRICT,
            ForeignKeyAction.RESTRICT,
        ),
    ),
    indexes=(
        _index(
            "data_snooping_family_searches_family_search",
            "family_id",
            "search_id",
            unique=True,
        ),
        _index(
            "data_snooping_family_searches_family_ordinal",
            "family_id",
            "link_ordinal",
            unique=True,
        ),
        _index(
            "data_snooping_family_searches_record_hash",
            "record_hash",
            unique=True,
        ),
    ),
    checks=(
        _record_type_check(_SEARCH_RECORD_TYPE),
        _nonnegative_check("generator_seed"),
        _positive_check("proposal_budget"),
        _positive_check("external_ingestion_deadline_us"),
        _positive_check("linked_at_us"),
        _positive_check("link_ordinal"),
    ),
    chain=ChainSpec(
        partition_key_columns=("family_id",),
        ordinal_column="link_ordinal",
        genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
        previous_hash_column="prev_record_hash",
        record_hash_column="record_hash",
        terminal_head_rule=ChainHeadRule.LATEST_RECORD_HASH,
    ),
    verification_rules=(
        VerificationRuleSpec(
            "family_search_row_hash",
            VerificationRuleKind.ROW_HASH,
            ("record_hash",),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.ROW_HASH_SPEC,
            ),
        ),
        VerificationRuleSpec(
            "family_search_chain",
            VerificationRuleKind.ORDERED_CHAIN,
            ("family_id", "link_ordinal", "prev_record_hash", "record_hash"),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.CHAIN_SPEC,
            ),
        ),
        VerificationRuleSpec(
            "family_search_registration_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            ("search_id",),
            "search_attempt_events",
            verifier_code="c5.verify_family_search_registration_totality_v1",
        ),
    ),
)


_PROPOSAL_REGISTRATION_SPEC = AuthorityTableSpec(
    table_name="data_snooping_proposal_registrations",
    columns=_PROPOSAL_REGISTRATION_COLUMNS,
    primary_key_columns=("proposal_id",),
    row_hash=_row_hash(
        _PROPOSAL_REGISTRATION_COLUMNS,
        _PROPOSAL_REGISTRATION_RECORD_TYPE,
    ),
    triggers=_PROPOSAL_REGISTRATION_TRIGGERS,
    foreign_keys=(
        _genesis_fk(),
        ForeignKeySpec(
            ("family_id", "search_id"),
            "data_snooping_family_searches",
            ("family_id", "search_id"),
            ForeignKeyAction.RESTRICT,
            ForeignKeyAction.RESTRICT,
        ),
    ),
    indexes=(
        _index(
            "data_snooping_proposal_registrations_search_ordinal",
            "family_id",
            "search_id",
            "emission_ordinal",
            unique=True,
        ),
        _index(
            "data_snooping_proposal_registrations_exact_result_origin",
            "authority_instance_id",
            "family_id",
            "search_id",
            "emission_ordinal",
            "proposal_id",
            "record_hash",
            unique=True,
        ),
        _index(
            "data_snooping_proposal_registrations_quarantine_record",
            "quarantine_record_id",
            unique=True,
        ),
        _index(
            "data_snooping_proposal_registrations_record_hash",
            "record_hash",
            unique=True,
        ),
    ),
    checks=(
        _record_type_check(_PROPOSAL_REGISTRATION_RECORD_TYPE),
        _positive_check("emission_ordinal"),
        _positive_check("registered_at_us"),
        _nullable_positive_check("raw_byte_length"),
        CheckConstraintSpec(
            "proposal_branch_domain",
            MembershipExpression(
                ColumnValue("proposal_branch"),
                (LiteralValue("CLEAN"), LiteralValue("SECRET_QUARANTINED")),
            ),
        ),
        CheckConstraintSpec(
            "dlp_result_code_domain",
            MembershipExpression(
                ColumnValue("dlp_result_code"),
                (LiteralValue("CLEAN"), LiteralValue("SECRET_QUARANTINED")),
            ),
        ),
        CheckConstraintSpec(
            "length_bucket_domain",
            MembershipExpression(
                ColumnValue("length_bucket"),
                tuple(
                    LiteralValue(bucket)
                    for bucket in (
                        "LE_1_KIB",
                        "GT_1_LE_4_KIB",
                        "GT_4_LE_16_KIB",
                        "GT_16_LE_64_KIB",
                        "GT_64_LE_256_KIB",
                        "GT_256_LE_1024_KIB",
                        "GT_1_MIB",
                    )
                ),
            ),
        ),
        _PROPOSAL_CLEAN_BRANCH,
        _PROPOSAL_QUARANTINE_BRANCH,
    ),
    chain=ChainSpec(
        partition_key_columns=("family_id", "search_id"),
        ordinal_column="emission_ordinal",
        genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
        previous_hash_column="prev_record_hash",
        record_hash_column="record_hash",
        terminal_head_rule=ChainHeadRule.LATEST_RECORD_HASH,
    ),
    verification_rules=(
        VerificationRuleSpec(
            "proposal_registration_row_hash",
            VerificationRuleKind.ROW_HASH,
            ("record_hash",),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.ROW_HASH_SPEC,
            ),
        ),
        VerificationRuleSpec(
            "proposal_registration_chain",
            VerificationRuleKind.ORDERED_CHAIN,
            (
                "family_id",
                "search_id",
                "emission_ordinal",
                "prev_record_hash",
                "record_hash",
            ),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.CHAIN_SPEC,
            ),
        ),
        VerificationRuleSpec(
            "proposal_registration_dlp_policy_scanner_binding",
            VerificationRuleKind.STATE_BRANCH,
            (
                "dlp_policy_id",
                "dlp_policy_version",
                "scanner_implementation",
                "scanner_version",
                "dlp_result_code",
            ),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.DELEGATED_EXTENSION,
                source_id="C8_PROPOSAL_DECLARATION_DLP_AND_LATE_GUARDS_V1",
                required_rule_ids=(
                    "proposal_declaration_dlp_match",
                ),
            ),
        ),
    ),
)


_PROPOSAL_SPEC = AuthorityTableSpec(
    table_name="data_snooping_proposals",
    columns=_PROPOSAL_COLUMNS,
    primary_key_columns=("proposal_id",),
    row_hash=_row_hash(_PROPOSAL_COLUMNS, _PROPOSAL_RECORD_TYPE),
    triggers=_PROPOSAL_TRIGGERS,
    foreign_keys=(
        _genesis_fk(),
        ForeignKeySpec(
            (
                "authority_instance_id",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
                "registration_record_hash",
            ),
            "data_snooping_proposal_registrations",
            (
                "authority_instance_id",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
                "record_hash",
            ),
            ForeignKeyAction.RESTRICT,
            ForeignKeyAction.RESTRICT,
        ),
    ),
    indexes=(
        _index(
            "data_snooping_proposals_search_ordinal",
            "family_id",
            "search_id",
            "emission_ordinal",
            unique=True,
        ),
        _index(
            "data_snooping_proposals_registration_record",
            "registration_record_hash",
            unique=True,
        ),
        _index(
            "data_snooping_proposals_exact_hypothesis_origin",
            "family_id",
            "search_id",
            "proposal_id",
            "canonical_proposal_hash",
            "config_hash",
            "alpha_id",
            unique=True,
        ),
        _index("data_snooping_proposals_record_hash", "record_hash", unique=True),
    ),
    checks=(
        _record_type_check(_PROPOSAL_RECORD_TYPE),
        _positive_check("emission_ordinal"),
        _positive_check("parsed_at_us"),
        CheckConstraintSpec(
            "parse_status_domain",
            MembershipExpression(
                ColumnValue("parse_status"),
                (
                    LiteralValue("ACCEPTED"),
                    LiteralValue("INVALID"),
                    LiteralValue("SECRET_QUARANTINED"),
                ),
            ),
        ),
        _PROPOSAL_TERMINAL_PARSE_BRANCH,
    ),
    verification_rules=(
        VerificationRuleSpec(
            "proposal_row_hash",
            VerificationRuleKind.ROW_HASH,
            ("record_hash",),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.ROW_HASH_SPEC,
            ),
        ),
        VerificationRuleSpec(
            "proposal_registration_exact",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "authority_instance_id",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
                "registration_record_hash",
            ),
            "data_snooping_proposal_registrations",
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
                columns=(
                    "authority_instance_id",
                    "family_id",
                    "search_id",
                    "emission_ordinal",
                    "proposal_id",
                    "registration_record_hash",
                ),
                referenced_table="data_snooping_proposal_registrations",
                referenced_columns=(
                    "authority_instance_id",
                    "family_id",
                    "search_id",
                    "emission_ordinal",
                    "proposal_id",
                    "record_hash",
                ),
            ),
        ),
        VerificationRuleSpec(
            "proposal_terminal_parse_branch",
            VerificationRuleKind.STATE_BRANCH,
            (
                "parse_status",
                "canonical_proposal_json",
                "canonical_proposal_hash",
                "config_hash",
                "alpha_id",
            ),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.CHECK_CONSTRAINT,
                source_id="terminal_parse_branch",
            ),
        ),
        VerificationRuleSpec(
            "proposal_branch_totality",
            VerificationRuleKind.STATE_BRANCH,
            (
                "registration_record_hash",
                "parse_status",
                "canonical_proposal_json",
                "canonical_proposal_hash",
                "config_hash",
                "alpha_id",
                "parsed_at_us",
            ),
            verifier_code="c5.verify_proposal_branch_totality_composite_v1",
        ),
        VerificationRuleSpec(
            "quarantine_public_id_safe_formula",
            VerificationRuleKind.STATE_BRANCH,
            (
                "registration_record_hash",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
            ),
            verifier_code="c5.verify_quarantine_public_id_safe_formula_v1",
        ),
        VerificationRuleSpec(
            "proposal_public_id_recomputed",
            VerificationRuleKind.ROW_HASH,
            (
                "registration_record_hash",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
            ),
            verifier_code="c5.verify_proposal_public_id_v1",
        ),
        VerificationRuleSpec(
            "proposal_identity_payload_matches_flattened_fields",
            VerificationRuleKind.STATE_BRANCH,
            (
                "registration_record_hash",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
            ),
            verifier_code="c5.verify_proposal_identity_payload_v1",
        ),
        VerificationRuleSpec(
            "proposal_canonical_identity_recomputed",
            VerificationRuleKind.STATE_BRANCH,
            (
                "parse_status",
                "canonical_proposal_json",
                "canonical_proposal_hash",
                "config_hash",
                "alpha_id",
            ),
            verifier_code=(
                "c5.verify_proposal_canonical_identity_from_canonical_bytes_v1"
            ),
        ),
    ),
)


_HYPOTHESIS_SPEC = AuthorityTableSpec(
    table_name="data_snooping_hypotheses",
    columns=_HYPOTHESIS_COLUMNS,
    primary_key_columns=("family_id", "config_hash"),
    row_hash=_row_hash(_HYPOTHESIS_COLUMNS, _HYPOTHESIS_RECORD_TYPE),
    triggers=_HYPOTHESIS_TRIGGERS,
    foreign_keys=(
        _genesis_fk(),
        ForeignKeySpec(
            (
                "family_id",
                "first_search_id",
                "first_proposal_id",
                "canonical_hypothesis_hash",
                "config_hash",
                "alpha_id",
            ),
            "data_snooping_proposals",
            (
                "family_id",
                "search_id",
                "proposal_id",
                "canonical_proposal_hash",
                "config_hash",
                "alpha_id",
            ),
            ForeignKeyAction.RESTRICT,
            ForeignKeyAction.RESTRICT,
        ),
        ForeignKeySpec(
            ("config_hash",),
            "strategy_config_lineage_bindings",
            ("config_hash",),
            ForeignKeyAction.RESTRICT,
            ForeignKeyAction.RESTRICT,
        ),
    ),
    indexes=(
        _index(
            "data_snooping_hypotheses_family_alpha",
            "family_id",
            "alpha_id",
            unique=True,
        ),
        _index(
            "data_snooping_hypotheses_first_proposal",
            "family_id",
            "first_search_id",
            "first_proposal_id",
        ),
        _index("data_snooping_hypotheses_record_hash", "record_hash", unique=True),
    ),
    checks=(_record_type_check(_HYPOTHESIS_RECORD_TYPE),),
    verification_rules=(
        VerificationRuleSpec(
            "hypothesis_row_hash",
            VerificationRuleKind.ROW_HASH,
            ("record_hash",),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.ROW_HASH_SPEC,
            ),
        ),
        VerificationRuleSpec(
            "hypothesis_identity_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "family_id",
                "first_search_id",
                "first_proposal_id",
                "canonical_hypothesis_hash",
                "config_hash",
                "alpha_id",
            ),
            "data_snooping_proposals",
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
                columns=(
                    "family_id",
                    "first_search_id",
                    "first_proposal_id",
                    "canonical_hypothesis_hash",
                    "config_hash",
                    "alpha_id",
                ),
                referenced_table="data_snooping_proposals",
                referenced_columns=(
                    "family_id",
                    "search_id",
                    "proposal_id",
                    "canonical_proposal_hash",
                    "config_hash",
                    "alpha_id",
                ),
            ),
        ),
    ),
)


_EMISSION_SEAL_SPEC = AuthorityTableSpec(
    table_name="data_snooping_search_emission_seals",
    columns=_EMISSION_SEAL_COLUMNS,
    primary_key_columns=("family_id", "search_id"),
    row_hash=_row_hash(_EMISSION_SEAL_COLUMNS, _EMISSION_SEAL_RECORD_TYPE),
    triggers=_EMISSION_SEAL_TRIGGERS,
    foreign_keys=(
        _genesis_fk(),
        ForeignKeySpec(
            ("family_id", "search_id"),
            "data_snooping_family_searches",
            ("family_id", "search_id"),
            ForeignKeyAction.RESTRICT,
            ForeignKeyAction.RESTRICT,
        ),
    ),
    indexes=(
        _index(
            "data_snooping_search_emission_seals_search",
            "search_id",
            unique=True,
        ),
        _index(
            "data_snooping_search_emission_seals_record_hash",
            "record_hash",
            unique=True,
        ),
    ),
    checks=(
        _record_type_check(_EMISSION_SEAL_RECORD_TYPE),
        _positive_check("declared_proposal_budget"),
        _nonnegative_check("actual_raw_proposal_count"),
        _nonnegative_check("candidate_count"),
        CheckConstraintSpec(
            "candidate_count_lte_raw_proposal_count",
            ComparisonExpression(
                ColumnValue("candidate_count"),
                ComparisonOperator.LE,
                ColumnValue("actual_raw_proposal_count"),
            ),
        ),
        _positive_check("sealed_at_us"),
    ),
    verification_rules=(
        VerificationRuleSpec(
            "emission_seal_row_hash",
            VerificationRuleKind.ROW_HASH,
            ("record_hash",),
            structural_source=StructuralVerificationSourceSpec(
                StructuralVerificationSourceKind.ROW_HASH_SPEC,
            ),
        ),
        *(
            VerificationRuleSpec(
                rule_id,
                VerificationRuleKind.CROSS_TABLE_TOTALITY,
                (
                    *(
                        ("authority_instance_id",)
                        if rule_id == "emission_seal_candidate_chain_head"
                        else ()
                    ),
                    "family_id",
                    "search_id",
                    *(column if type(column) is tuple else (column,)),
                ),
                referenced_table,
                verifier_code=verifier_code,
                structural_source=(
                    StructuralVerificationSourceSpec(
                        StructuralVerificationSourceKind.TRIGGER_POLICY,
                        source_id=(
                            "data_snooping_search_emission_seals_declared_budget_match"
                        ),
                    )
                    if rule_id == "emission_seal_declared_budget"
                    else None
                ),
            )
            for rule_id, column, referenced_table, verifier_code in (
                (
                    "emission_seal_declared_budget",
                    "declared_proposal_budget",
                    "data_snooping_family_searches",
                    None,
                ),
                (
                    "emission_seal_actual_proposal_count",
                    "actual_raw_proposal_count",
                    "data_snooping_proposal_registrations",
                    "c5.verify_emission_seal_actual_proposal_count_v1",
                ),
                (
                    "emission_seal_ordered_proposal_ids",
                    "ordered_proposal_ids_hash",
                    "data_snooping_proposal_registrations",
                    "c5.verify_emission_seal_ordered_proposal_ids_v1",
                ),
                (
                    "emission_seal_ordered_non_quarantined_raw_hashes",
                    "ordered_non_quarantined_raw_hashes_hash",
                    "data_snooping_proposal_registrations",
                    (
                        "c5.verify_emission_seal_"
                        "ordered_non_quarantined_raw_hashes_v1"
                    ),
                ),
                (
                    "emission_seal_ordered_canonical_proposal_hashes",
                    "ordered_canonical_proposal_hashes_hash",
                    "data_snooping_proposals",
                    (
                        "c5.verify_emission_seal_"
                        "ordered_canonical_proposal_hashes_v1"
                    ),
                ),
                (
                    "emission_seal_ordered_quarantine_record_ids",
                    "ordered_quarantine_record_ids_hash",
                    "data_snooping_proposal_registrations",
                    (
                        "c5.verify_emission_seal_"
                        "ordered_quarantine_record_ids_v1"
                    ),
                ),
                (
                    "emission_seal_accepted_config_set",
                    "accepted_config_set_hash",
                    "data_snooping_proposals",
                    "c5.verify_emission_seal_accepted_config_set_v1",
                ),
                (
                    "emission_seal_candidate_chain_head",
                    ("candidate_count", "candidate_chain_head"),
                    "search_candidates",
                    "c5.verify_emission_seal_candidate_chain_head_v1",
                ),
            )
        ),
        VerificationRuleSpec(
            "emission_seal_terminal_result_totality",
            VerificationRuleKind.CROSS_TABLE_TOTALITY,
            (
                "authority_instance_id",
                "family_id",
                "search_id",
                "actual_raw_proposal_count",
            ),
            "data_snooping_proposals",
            verifier_code=(
                "c5.verify_emission_seal_terminal_result_totality_v1"
            ),
        ),
        VerificationRuleSpec(
            "emission_seal_terminal_search",
            VerificationRuleKind.TERMINAL_HEAD,
            (
                "authority_instance_id",
                "family_id",
                "search_id",
                "candidate_count",
                "candidate_chain_head",
            ),
            "search_attempt_events",
            verifier_code=(
                "c5.verify_emission_seal_terminal_search_from_event_chain_v1"
            ),
        ),
    ),
)


C5_AUTHORITY_TABLE_SPECS = (
    _FAMILY_SEARCH_SPEC,
    _PROPOSAL_REGISTRATION_SPEC,
    _PROPOSAL_SPEC,
    _HYPOTHESIS_SPEC,
    _EMISSION_SEAL_SPEC,
)


class CandidateDisposition(_StrEnum):
    PERFORMANCE_EVALUATED = "PERFORMANCE_EVALUATED"
    PERFORMANCE_ATTEMPTED_INCOMPLETE = "PERFORMANCE_ATTEMPTED_INCOMPLETE"
    PRE_EVALUATION_INVALID = "PRE_EVALUATION_INVALID"
    SECRET_QUARANTINED = "SECRET_QUARANTINED"


class ProposalIdentityState(_StrEnum):
    CONFIG_ACCEPTED = "CONFIG_ACCEPTED"
    PRE_EVALUATION_INVALID = "PRE_EVALUATION_INVALID"
    SECRET_QUARANTINED = "SECRET_QUARANTINED"


class PerformanceAccess(_StrEnum):
    COMPLETED = "COMPLETED"
    ATTEMPTED = "ATTEMPTED"
    FORBIDDEN = "FORBIDDEN"


@_dataclass(frozen=True, slots=True)
class DeferredDispositionConstraintSpec:
    disposition: CandidateDisposition
    required_identity_state: ProposalIdentityState
    required_fields: tuple[str, ...]
    forbidden_fields: tuple[str, ...]
    performance_access: PerformanceAccess

    def __post_init__(self) -> None:
        if type(self.disposition) is not CandidateDisposition:
            raise TypeError("disposition must be CandidateDisposition")
        if type(self.required_identity_state) is not ProposalIdentityState:
            raise TypeError("required_identity_state must be ProposalIdentityState")
        if type(self.performance_access) is not PerformanceAccess:
            raise TypeError("performance_access must be PerformanceAccess")
        for label, values in (
            ("required_fields", self.required_fields),
            ("forbidden_fields", self.forbidden_fields),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} must be an immutable tuple")
            if len(set(values)) != len(values):
                raise ValueError(f"{label} must be unique")
            if any(type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None for value in values):
                raise ValueError(f"{label} must contain field identifiers")
        if set(self.required_fields) & set(self.forbidden_fields):
            raise ValueError("required and forbidden disposition fields must be disjoint")


C5_DEFERRED_DISPOSITION_CONSTRAINTS = (
    DeferredDispositionConstraintSpec(
        CandidateDisposition.PERFORMANCE_EVALUATED,
        ProposalIdentityState.CONFIG_ACCEPTED,
        ("config_hash", "evaluation_receipt_hash"),
        ("quarantine_record_id",),
        PerformanceAccess.COMPLETED,
    ),
    DeferredDispositionConstraintSpec(
        CandidateDisposition.PERFORMANCE_ATTEMPTED_INCOMPLETE,
        ProposalIdentityState.CONFIG_ACCEPTED,
        ("config_hash", "reason_code"),
        ("evaluation_receipt_hash", "quarantine_record_id"),
        PerformanceAccess.ATTEMPTED,
    ),
    DeferredDispositionConstraintSpec(
        CandidateDisposition.PRE_EVALUATION_INVALID,
        ProposalIdentityState.PRE_EVALUATION_INVALID,
        ("reason_code",),
        ("config_hash", "evaluation_receipt_hash", "quarantine_record_id"),
        PerformanceAccess.FORBIDDEN,
    ),
    DeferredDispositionConstraintSpec(
        CandidateDisposition.SECRET_QUARANTINED,
        ProposalIdentityState.SECRET_QUARANTINED,
        ("quarantine_record_id", "reason_code"),
        ("config_hash", "evaluation_receipt_hash"),
        PerformanceAccess.FORBIDDEN,
    ),
)


def _column_coverage(
    contract_type: str,
    field_name: str,
    table_name: str,
    target_field: str | None = None,
    *,
    kind: CoverageKind = CoverageKind.COLUMN,
) -> ContractFieldCoverageSpec:
    return ContractFieldCoverageSpec(
        contract_type=contract_type,
        field_name=field_name,
        kind=kind,
        table_name=table_name,
        target_field=target_field or field_name,
    )


_C5_COVERAGE = (
    *(
        _column_coverage("FamilySearchRecord", field_name, "data_snooping_family_searches")
        for field_name in (
            "family_id",
            "search_id",
            "research_program_id",
            "search_kind",
            "generator_seed",
            "proposal_budget",
            "stopping_rule_hash",
            "external_ingestion_deadline_us",
            "search_authority_id",
            "search_authority_head",
            "linked_at_us",
            "link_ordinal",
        )
    ),
    *(
        _column_coverage(
            "CandidateIdentity",
            field_name,
            "data_snooping_proposal_registrations",
        )
        for field_name in (
            "family_id",
            "search_id",
            "emission_ordinal",
            "proposal_id",
            "proposal_branch",
            "raw_envelope_hash",
        )
    ),
    _column_coverage(
        "CandidateProposalRegistrationRecord",
        "identity",
        "data_snooping_proposal_registrations",
        "identity_payload_json",
        kind=CoverageKind.CANONICAL_PAYLOAD,
    ),
    *(
        _column_coverage(
            "CandidateProposalRegistrationRecord",
            field_name,
            "data_snooping_proposal_registrations",
        )
        for field_name in (
            "media_type",
            "raw_byte_length",
            "length_bucket",
            "dlp_policy_id",
            "dlp_policy_version",
            "scanner_implementation",
            "scanner_version",
            "dlp_result_code",
            "restricted_clean_blob_receipt_hash",
            "quarantine_record_id",
            "registered_at_us",
        )
    ),
    *(
        _column_coverage(
            "CandidateProposalRecord",
            field_name,
            "data_snooping_proposals",
        )
        for field_name in (
            "proposal_id",
            "family_id",
            "search_id",
            "emission_ordinal",
            "registration_record_hash",
            "parse_status",
            "canonical_proposal_json",
            "canonical_proposal_hash",
            "config_hash",
            "alpha_id",
            "parsed_at_us",
        )
    ),
    *(
        _column_coverage(
            "CandidateHypothesisRecord",
            field_name,
            "data_snooping_hypotheses",
        )
        for field_name in (
            "family_id",
            "first_search_id",
            "config_hash",
            "alpha_id",
            "canonical_hypothesis_hash",
            "first_proposal_id",
        )
    ),
    *(
        _column_coverage(
            "CandidateEmissionSeal",
            field_name,
            "data_snooping_search_emission_seals",
        )
        for field_name in (
            "family_id",
            "search_id",
            "declared_proposal_budget",
            "actual_raw_proposal_count",
            "ordered_proposal_ids_hash",
            "ordered_non_quarantined_raw_hashes_hash",
            "ordered_canonical_proposal_hashes_hash",
            "ordered_quarantine_record_ids_hash",
            "accepted_config_set_hash",
            "candidate_count",
            "candidate_chain_head",
            "terminal_reason",
            "sealed_at_us",
        )
    ),
    *(
        _column_coverage(
            "RegisteredEnvelopeHandle",
            field_name,
            "data_snooping_proposal_registrations",
        )
        for field_name in (
            "proposal_id",
            "family_id",
            "search_id",
            "emission_ordinal",
            "restricted_clean_blob_receipt_hash",
        )
    ),
    _column_coverage(
        "RegisteredEnvelopeHandle",
        "registration_record_hash",
        "data_snooping_proposal_registrations",
        "record_hash",
    ),
    ContractFieldCoverageSpec(
        contract_type="RegisteredEnvelopeHandle",
        field_name="registration_authority_head",
        kind=CoverageKind.NON_PERSISTED_DERIVATION,
        derivation_id="c5.proposal_registration.chain_head",
    ),
)


_C5_REQUIRED_EXTENSIONS = (
    RequiredTableExtensionSpec(
        "C8_FAMILY_SEARCH_DECLARATION_AND_LATE_GUARDS_V1",
        "data_snooping_family_searches",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.family_search.declaration_and_late_guards",
    ),
    RequiredTableExtensionSpec(
        "C8_PROPOSAL_DECLARATION_DLP_AND_LATE_GUARDS_V1",
        "data_snooping_proposal_registrations",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.proposal.declaration_dlp_and_late_guards",
    ),
    RequiredTableExtensionSpec(
        "C8_PROPOSAL_TERMINAL_RESULT_LATE_GUARDS_V1",
        "data_snooping_proposals",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.proposal_terminal_result.late_guards",
    ),
    RequiredTableExtensionSpec(
        "C8_HYPOTHESIS_LATE_GUARDS_V1",
        "data_snooping_hypotheses",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.hypothesis.late_guards",
    ),
    RequiredTableExtensionSpec(
        "C8_EMISSION_SEAL_LATE_GUARDS_V1",
        "data_snooping_search_emission_seals",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.emission_seal.late_guards",
    ),
)


_C5_CONTRACT_DERIVATIONS = (
    ContractFieldDerivationSpec(
        derivation_id="c5.proposal_registration.chain_head",
        contract_type="RegisteredEnvelopeHandle",
        field_name="registration_authority_head",
        kind=ContractDerivationKind.IMMUTABLE_ROW_LOOKUP,
        binding_table="data_snooping_proposal_registrations",
        binding_field="proposal_id",
        source_table="data_snooping_proposal_registrations",
        source_key_field="proposal_id",
        source_value_field="record_hash",
    ),
)


_C5_REQUIRED_CONTRACT_SURFACES = (
    RequiredContractSurfaceSpec(
        contract_type="CandidateDisposition",
        field_names=(
            "family_id",
            "proposal_id",
            "disposition",
            "config_hash",
            "evaluation_receipt_hash",
            "quarantine_record_id",
            "reason_code",
            "performance_accessed",
        ),
        provider_component_id="C7_ONE_SHOT_OOS_V1",
        required_capability="c7.candidate_disposition.core",
    ),
)


C5_SCHEMA_COMPONENT = SchemaComponentSpec(
    component_id="C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1",
    capabilities=(
        "c5.proposals_hypotheses.dispositions",
        "c5.family_search.core",
        "c5.proposal_registration.core",
        "c5.proposal.core",
        "c5.hypothesis.core",
        "c5.emission_seal.core",
        "c5.deferred_disposition.policy",
    ),
    dependencies=(
        ComponentDependencySpec(
            "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1",
            DependencyKind.CONTRACT_LINEAGE,
            ("c4.research_program.core",),
        ),
    ),
    tables=C5_AUTHORITY_TABLE_SPECS,
    required_extensions=_C5_REQUIRED_EXTENSIONS,
    coverage=_C5_COVERAGE,
    contract_derivations=_C5_CONTRACT_DERIVATIONS,
    required_contract_surfaces=_C5_REQUIRED_CONTRACT_SURFACES,
)


ExternalReferenceKind = ExternalAuthorityReferenceKind
_C6_EXTERNAL_REFERENCE_KINDS = (
    ExternalReferenceKind.OBSERVED_PIT,
    ExternalReferenceKind.OOS_EXPOSURE,
    ExternalReferenceKind.CODE_MANIFEST,
    ExternalReferenceKind.EXECUTION_ATTESTATION,
    ExternalReferenceKind.IMMUTABLE_LEASE,
)


@_dataclass(frozen=True, slots=True)
class EmbeddedReferenceSurfaceSpec:
    table_name: str
    columns: tuple[ColumnSpec, ...]
    reference_kinds: tuple[ExternalReferenceKind, ...]
    verifier_ids: tuple[str, ...]
    row_hash_fields: tuple[str, ...]
    core_owned_fields: tuple[str, ...]
    additional_rule_columns: tuple[str, ...]
    extension: AuthorityTableExtensionSpec
    requirement: RequiredTableExtensionSpec

    def __post_init__(self) -> None:
        if type(self.table_name) is not str or _IDENTIFIER_RE.fullmatch(self.table_name) is None:
            raise ValueError("embedded-reference table_name must be an identifier")
        if type(self.columns) is not tuple or not self.columns:
            raise TypeError("embedded-reference columns must be a non-empty tuple")
        if any(type(column) is not ColumnSpec for column in self.columns):
            raise TypeError("embedded-reference columns must be ColumnSpec values")
        column_names = tuple(column.name for column in self.columns)
        if len(set(column_names)) != len(column_names):
            raise ValueError("embedded-reference columns must be unique")
        if type(self.reference_kinds) is not tuple or not self.reference_kinds:
            raise TypeError("reference_kinds must be a non-empty tuple")
        if any(type(kind) is not ExternalReferenceKind for kind in self.reference_kinds):
            raise TypeError("reference_kinds must be closed ExternalReferenceKind values")
        if len(set(self.reference_kinds)) != len(self.reference_kinds):
            raise ValueError("reference_kinds must be unique")
        if type(self.verifier_ids) is not tuple or not self.verifier_ids:
            raise TypeError("verifier_ids must be a non-empty tuple")
        if any(
            type(verifier_id) is not str
            or (
                _TOKEN_RE.fullmatch(verifier_id) is None
                and _IDENTIFIER_RE.fullmatch(verifier_id) is None
                and _VERIFIER_CODE_RE.fullmatch(verifier_id) is None
            )
            for verifier_id in self.verifier_ids
        ):
            raise ValueError("verifier_ids must be closed verifier tokens or rule identifiers")
        if len(set(self.verifier_ids)) != len(self.verifier_ids):
            raise ValueError("verifier_ids must be unique")
        if type(self.row_hash_fields) is not tuple:
            raise TypeError("row_hash_fields must be an immutable tuple")
        if not set(column_names).issubset(self.row_hash_fields):
            raise ValueError("every embedded-reference column must enter the row hash")
        if type(self.core_owned_fields) is not tuple:
            raise TypeError("core_owned_fields must be an immutable tuple")
        if not set(self.core_owned_fields).issubset(column_names):
            raise ValueError("core_owned_fields must belong to the complete surface")
        if type(self.additional_rule_columns) is not tuple:
            raise TypeError("additional_rule_columns must be an immutable tuple")
        if any(
            type(column) is not str or _IDENTIFIER_RE.fullmatch(column) is None
            for column in self.additional_rule_columns
        ):
            raise ValueError("additional_rule_columns must be identifiers")
        if len(set(self.additional_rule_columns)) != len(self.additional_rule_columns):
            raise ValueError("additional_rule_columns must be unique")
        if type(self.extension) is not AuthorityTableExtensionSpec:
            raise TypeError("extension must be AuthorityTableExtensionSpec")
        if type(self.requirement) is not RequiredTableExtensionSpec:
            raise TypeError("requirement must be RequiredTableExtensionSpec")
        if self.extension.target_table != self.table_name:
            raise ValueError("embedded extension must target its owning table")
        if self.requirement.target_table != self.table_name:
            raise ValueError("extension requirement must target its owning table")
        if self.extension.extension_id != self.requirement.extension_id:
            raise ValueError("provided and required extension IDs must match")
        added_columns = tuple(column.name for column in self.extension.column_insertion.columns)
        expected_additions = tuple(name for name in column_names if name not in self.core_owned_fields)
        if added_columns != expected_additions:
            raise ValueError("extension must add exactly the non-core surface columns")
        embedded_rules = tuple(self.extension.verification_rules)
        if tuple(rule.verifier_code or rule.rule_id for rule in embedded_rules) != self.verifier_ids:
            raise ValueError("verifier IDs must exactly match typed extension rules in order")
        known_rule_columns = set(column_names) | set(self.additional_rule_columns)
        for rule in embedded_rules:
            if not set(rule.columns).issubset(known_rule_columns):
                raise ValueError("embedded-reference verification uses an unknown surface column")
            if (
                rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE
                and rule.external_reference_kind not in self.reference_kinds
            ):
                raise ValueError("external verifier kind must be declared by the surface")


_C6_COMPONENT_ID = "C6_EXTERNAL_AUTHORITY_REFS_V1"
_C4_COMPONENT_ID = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"


def _external_rule(
    rule_id: str,
    kind: ExternalReferenceKind,
    provider_contract_type: str,
    provider_capability: str,
    bindings: tuple[tuple[str, str], ...],
    *,
    nullable: bool = False,
    provider_component_id: str | None = None,
) -> VerificationRuleSpec:
    typed_bindings = tuple(ExternalFieldBindingSpec(local, provider) for local, provider in bindings)
    return VerificationRuleSpec(
        rule_id=rule_id.lower(),
        kind=VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE,
        columns=tuple(binding.local_column for binding in typed_bindings),
        external_reference_kind=kind,
        provider_contract_type=provider_contract_type,
        provider_capability=provider_capability,
        provider_component_id=provider_component_id,
        external_bindings=typed_bindings,
        nullable_reference=nullable,
        reference_presence_column=(typed_bindings[0].local_column if nullable else None),
        verifier_code=rule_id,
    )


def _plain_rule(
    rule_id: str,
    kind: VerificationRuleKind,
    columns: tuple[str, ...],
    *,
    referenced_table: str | None = None,
    predicate: PredicateExpression | None = None,
) -> VerificationRuleSpec:
    return VerificationRuleSpec(
        rule_id=rule_id.lower(),
        kind=kind,
        columns=columns,
        referenced_table=referenced_table,
        predicate=predicate,
        verifier_code=rule_id,
    )


def _named_comparison_check(
    name: str,
    column: str,
    operator: ComparisonOperator,
    literal: int,
) -> CheckConstraintSpec:
    return CheckConstraintSpec(name, _comparison(column, operator, literal))


def _all_or_none_check(
    table_name: str,
    group_name: str,
    columns: tuple[str, ...],
) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{table_name}_{group_name}_all_or_none",
        _logical(
            LogicalOperator.OR,
            _logical(
                LogicalOperator.AND,
                *(NullExpression(ColumnValue(column), NullOperator.IS_NULL) for column in columns),
            ),
            _logical(
                LogicalOperator.AND,
                *(NullExpression(ColumnValue(column), NullOperator.IS_NOT_NULL) for column in columns),
            ),
        ),
    )


def _terminal_execution_check(table_name: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{table_name}_terminal_execution_requires_start",
        _logical(
            LogicalOperator.OR,
            NullExpression(
                ColumnValue("terminal_execution_attestation_hash"),
                NullOperator.IS_NULL,
            ),
            NullExpression(
                ColumnValue("start_execution_attestation_hash"),
                NullOperator.IS_NOT_NULL,
            ),
        ),
    )


_REFERENCE_BRANCH_PRESENCE_COLUMNS = (
    "oos_exposure_id",
    "start_execution_attestation_hash",
    "terminal_execution_attestation_hash",
    "immutable_artifact_lease_id",
)


def _external_reference_branch_predicate() -> PredicateExpression:
    oos, start, terminal, lease = _REFERENCE_BRANCH_PRESENCE_COLUMNS
    return _logical(
        LogicalOperator.OR,
        _logical(
            LogicalOperator.AND,
            *(NullExpression(ColumnValue(column), NullOperator.IS_NULL) for column in (oos, start, terminal, lease)),
        ),
        _logical(
            LogicalOperator.AND,
            NullExpression(ColumnValue(oos), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue(start), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue(terminal), NullOperator.IS_NULL),
            NullExpression(ColumnValue(lease), NullOperator.IS_NOT_NULL),
        ),
        _logical(
            LogicalOperator.AND,
            *(
                NullExpression(ColumnValue(column), NullOperator.IS_NOT_NULL)
                for column in (oos, start, terminal, lease)
            ),
        ),
    )


def _publication_attempt_reference_branch_predicate() -> PredicateExpression:
    oos, start, terminal, lease = _REFERENCE_BRANCH_PRESENCE_COLUMNS
    return _logical(
        LogicalOperator.OR,
        _logical(
            LogicalOperator.AND,
            _comparison("closure_status", ComparisonOperator.EQ, "COMPLETE"),
            _comparison("attempt_status", ComparisonOperator.EQ, "COMPLETED"),
            *(
                NullExpression(ColumnValue(column), NullOperator.IS_NOT_NULL)
                for column in (oos, start, terminal, lease)
            ),
        ),
        _logical(
            LogicalOperator.AND,
            _comparison("closure_status", ComparisonOperator.EQ, "NOT_ASSESSABLE"),
            _comparison("attempt_status", ComparisonOperator.EQ, "NOT_STARTED"),
            *(
                NullExpression(ColumnValue(column), NullOperator.IS_NULL)
                for column in (oos, start, terminal, lease)
            ),
        ),
        _logical(
            LogicalOperator.AND,
            _comparison("closure_status", ComparisonOperator.EQ, "NOT_ASSESSABLE"),
            _comparison("attempt_status", ComparisonOperator.EQ, "INCOMPLETE"),
            NullExpression(ColumnValue(oos), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue(start), NullOperator.IS_NOT_NULL),
            NullExpression(ColumnValue(terminal), NullOperator.IS_NULL),
            NullExpression(ColumnValue(lease), NullOperator.IS_NOT_NULL),
        ),
    )


_OBSERVED_DECLARATION_BINDINGS = (
    ("observed_pit_authority_id", "authority_id"),
    ("observed_pit_handle_id", "handle_id"),
    ("observed_pit_content_hash", "content_hash"),
    ("knowledge_time_policy_id", "knowledge_time_policy_id"),
    ("knowledge_time_policy_version", "knowledge_time_policy_version"),
    ("observed_pit_authority_head", "authority_head"),
)

_OBSERVED_EXPOSURE_BINDINGS = (
    ("observed_pit_authority_id", "authority_id"),
    ("observed_pit_handle_id", "handle_id"),
    ("observed_pit_content_hash", "content_hash"),
    ("observed_pit_authority_head", "authority_head"),
    ("dataset_id", "dataset_id"),
    ("dataset_version", "dataset_version"),
    ("universe_id", "universe_id"),
    ("universe_hash", "universe_hash"),
    ("ordered_period_calendar_hash", "ordered_period_calendar_hash"),
    ("knowledge_time_policy_id", "knowledge_time_policy_id"),
    ("knowledge_time_policy_version", "knowledge_time_policy_version"),
    ("label_horizon", "label_horizon"),
    ("purge_periods", "purge_periods"),
    ("embargo_periods", "embargo_periods"),
    ("cost_contract_hash", "cost_contract_hash"),
    ("benchmark_contract_hash", "benchmark_contract_hash"),
)

_CODE_MANIFEST_BINDINGS = (
    ("code_manifest_policy_id", "policy_ref.policy_id"),
    ("code_manifest_policy_version", "policy_ref.policy_version"),
    ("code_manifest_policy_hash", "policy_ref.policy_hash"),
    ("code_manifest_evidence_hash", "evidence_hash"),
    ("code_manifest_authority_id", "authority_id"),
    ("code_manifest_authority_head", "authority_head"),
)

_OOS_EXPOSURE_BINDINGS = (
    ("oos_exposure_id", "oos_exposure_id"),
    ("exposure_fingerprint", "exposure_fingerprint"),
    ("exposure_authority_id", "authority_instance_id"),
    ("exposure_authority_head", "record_hash"),
)

_START_ATTESTATION_BINDINGS = (
    ("start_execution_attestation_hash", "attestation_hash"),
    ("execution_attestation_authority_id", "authority_id"),
    ("execution_attestation_authority_head", "authority_head"),
)

_CLOSURE_PUBLICATION_START_ATTESTATION_BINDINGS = (
    ("start_execution_attestation_hash", "attestation_hash"),
    ("execution_attestation_authority_id", "authority_id"),
    ("start_execution_attestation_authority_head", "authority_head"),
)

_CLOSURE_PUBLICATION_TERMINAL_ATTESTATION_BINDINGS = (
    ("terminal_execution_attestation_hash", "attestation_hash"),
    ("execution_attestation_authority_id", "authority_id"),
    ("terminal_execution_attestation_authority_head", "authority_head"),
)

_LEASE_BINDINGS = (
    ("immutable_artifact_lease_id", "lease_id"),
    ("immutable_artifact_lease_hash", "lease_hash"),
    ("immutable_artifact_lease_authority_id", "authority_id"),
    ("immutable_artifact_lease_authority_head", "authority_head"),
)


_START_LEASE_CONTINUITY_FIELDS = (
    ("lease_id", "lease_id"),
    ("code_manifest_hash", "code_manifest_hash"),
    ("immutable_artifact_id", "artifact_id"),
    ("immutable_artifact_digest", "artifact_digest"),
    ("process_image_hash", "process_image_hash"),
)

_CODE_START_CONTINUITY_FIELDS = (
    ("manifest_hash", "code_manifest_hash"),
    ("source_commit", "source_commit"),
    ("runtime_identity", "runtime_identity"),
    ("dependency_lock_hash", "dependency_lock_hash"),
)

_START_TERMINAL_CONTINUITY_FIELDS = (
    ("code_manifest_hash", "code_manifest_hash"),
    ("source_commit", "source_commit"),
    ("runtime_identity", "runtime_identity"),
    ("dependency_lock_hash", "dependency_lock_hash"),
    ("immutable_artifact_id", "immutable_artifact_id"),
    ("immutable_artifact_digest", "immutable_artifact_digest"),
    ("entrypoint", "entrypoint"),
    ("process_image_hash", "process_image_hash"),
    ("lease_id", "lease_id"),
)

_OBSERVED_PIT_EXPOSURE_CONTINUITY_FIELDS = (
    ("authority_id", "observed_pit_authority_id"),
    ("handle_id", "observed_pit_handle_id"),
    ("content_hash", "observed_pit_content_hash"),
    ("knowledge_time_policy_id", "knowledge_time_policy_id"),
    ("knowledge_time_policy_version", "knowledge_time_policy_version"),
    ("authority_head", "observed_pit_authority_head"),
)


def _provider_equalities(
    left_verifier_code: str,
    right_verifier_code: str,
    field_pairs: tuple[tuple[str, str], ...],
) -> tuple[ExternalProviderFieldEqualitySpec, ...]:
    return tuple(
        ExternalProviderFieldEqualitySpec(
            left_verifier_code=left_verifier_code,
            left_provider_field_path=left_field,
            right_verifier_code=right_verifier_code,
            right_provider_field_path=right_field,
        )
        for left_field, right_field in field_pairs
    )


def _continuity_rule(
    verifier_code: str,
    left_verifier_code: str,
    right_verifier_code: str,
    field_pairs: tuple[tuple[str, str], ...],
    *,
    reference_presence_column: str | None,
    semantic_profile: ExternalContinuitySemanticProfileSpec | None = None,
) -> ExternalAuthorityContinuitySpec:
    return ExternalAuthorityContinuitySpec(
        verifier_code=verifier_code,
        required_external_verifier_codes=(
            left_verifier_code,
            right_verifier_code,
        ),
        provider_field_equalities=_provider_equalities(
            left_verifier_code,
            right_verifier_code,
            field_pairs,
        ),
        reference_presence_column=reference_presence_column,
        semantic_profile=semantic_profile,
    )


_CONTINUITY_SUBJECT_CONTEXT_FIELDS = (
    "family_id",
    "attempt_id",
    "evaluation_batch_id",
)
_CLOSURE_CHRONOLOGY_RULE_IDS = (
    "family_closure_row_hash",
    "family_closure_attempt_batch_branch",
)
_ATTEMPT_CHRONOLOGY_RULE_IDS = (
    "oos_attempt_row_hash",
    "oos_attempt_identity_stability",
    "oos_attempt_terminal_batch_branch",
)
_BATCH_CHRONOLOGY_RULE_IDS = (
    "evaluation_batch_row_hash",
    "evaluation_batch_started_attempt_identity",
    "evaluation_batch_attempt_terminal_binding",
)


def _chronology_subject_input(prefix: str) -> ExternalContinuityRowInputSpec:
    if prefix == "CLOSURE":
        return ExternalContinuityRowInputSpec(
            alias="closure",
            source_kind=ExternalContinuityRowInputKind.SUBJECT_ROW,
            table="data_snooping_family_closures",
            bindings=(),
            required_fields=(
                "authority_instance_id",
                "family_id",
                "attempt_id",
                "terminal_attempt_event_id",
                "evaluation_batch_id",
                "evaluation_batch_receipt_hash",
                "oos_access_attempt_terminal_hash",
                "closure_status",
                "closed_at_us",
            ),
            required_verification_rule_ids=_CLOSURE_CHRONOLOGY_RULE_IDS,
            cardinality=None,
            key_requirement=None,
        )
    if prefix != "PUBLICATION":
        raise ValueError("chronology subject prefix must be CLOSURE or PUBLICATION")
    return ExternalContinuityRowInputSpec(
        alias="publication",
        source_kind=ExternalContinuityRowInputKind.SUBJECT_ROW,
        table="artifact_publication_receipts",
        bindings=(),
        required_fields=(
            "authority_instance_id",
            "family_id",
            "closure_manifest_hash",
            "closure_status",
            "attempt_status",
        ),
        required_verification_rule_ids=(
            "publication_receipt_closure_exact",
            "publication_receipt_closure_external_summaries_exact",
            "verify_publication_external_reference_totality",
        ),
        cardinality=None,
        key_requirement=None,
    )


def _related_closure_input() -> ExternalContinuityRowInputSpec:
    return ExternalContinuityRowInputSpec(
        alias="related_closure",
        source_kind=ExternalContinuityRowInputKind.EXACT_RELATED_ROW,
        table="data_snooping_family_closures",
        bindings=(
            ExternalContinuityReadBindingSpec(
                "authority_instance_id",
                "publication.authority_instance_id",
            ),
            ExternalContinuityReadBindingSpec(
                "family_id",
                "publication.family_id",
            ),
            ExternalContinuityReadBindingSpec(
                "record_hash",
                "publication.closure_manifest_hash",
            ),
            ExternalContinuityReadBindingSpec(
                "closure_status",
                "publication.closure_status",
            ),
        ),
        required_fields=(
            "authority_instance_id",
            "family_id",
            "attempt_id",
            "terminal_attempt_event_id",
            "evaluation_batch_id",
            "evaluation_batch_receipt_hash",
            "oos_access_attempt_terminal_hash",
            "closure_status",
            "closed_at_us",
        ),
        required_verification_rule_ids=_CLOSURE_CHRONOLOGY_RULE_IDS,
    )


def _attempt_chronology_input(
    closure_alias: str,
) -> ExternalContinuityRowInputSpec:
    return ExternalContinuityRowInputSpec(
        alias="attempt",
        source_kind=ExternalContinuityRowInputKind.EXACT_RELATED_ROW,
        table="data_snooping_oos_access_attempts",
        bindings=(
            ExternalContinuityReadBindingSpec(
                "authority_instance_id",
                f"{closure_alias}.authority_instance_id",
            ),
            ExternalContinuityReadBindingSpec(
                "attempt_id",
                f"{closure_alias}.attempt_id",
            ),
            ExternalContinuityReadBindingSpec(
                "attempt_event_id",
                f"{closure_alias}.terminal_attempt_event_id",
            ),
            ExternalContinuityReadBindingSpec(
                "family_id",
                f"{closure_alias}.family_id",
            ),
            ExternalContinuityReadBindingSpec(
                "record_hash",
                f"{closure_alias}.oos_access_attempt_terminal_hash",
            ),
        ),
        required_fields=("status",),
        required_verification_rule_ids=_ATTEMPT_CHRONOLOGY_RULE_IDS,
    )


def _batch_chronology_input(
    closure_alias: str,
) -> ExternalContinuityRowInputSpec:
    return ExternalContinuityRowInputSpec(
        alias="evaluation_batch",
        source_kind=ExternalContinuityRowInputKind.EXACT_RELATED_ROW,
        table="data_snooping_evaluation_batches",
        bindings=(
            ExternalContinuityReadBindingSpec(
                "authority_instance_id",
                f"{closure_alias}.authority_instance_id",
            ),
            ExternalContinuityReadBindingSpec(
                "evaluation_batch_id",
                f"{closure_alias}.evaluation_batch_id",
            ),
            ExternalContinuityReadBindingSpec(
                "family_id",
                f"{closure_alias}.family_id",
            ),
            ExternalContinuityReadBindingSpec(
                "attempt_id",
                f"{closure_alias}.attempt_id",
            ),
            ExternalContinuityReadBindingSpec(
                "record_hash",
                f"{closure_alias}.evaluation_batch_receipt_hash",
            ),
            ExternalContinuityReadBindingSpec(
                "start_execution_attestation_hash",
                "start.attestation_hash",
            ),
            ExternalContinuityReadBindingSpec(
                "terminal_execution_attestation_hash",
                "terminal.attestation_hash",
            ),
            ExternalContinuityReadBindingSpec(
                "immutable_artifact_lease_hash",
                "lease.lease_hash",
            ),
        ),
        required_fields=("committed_at_us",),
        required_verification_rule_ids=_BATCH_CHRONOLOGY_RULE_IDS,
    )


def _chronology_comparisons(
    closure_alias: str,
    *,
    complete: bool,
) -> tuple[ExternalContinuityTimeComparisonSpec, ...]:
    closure_clock = f"{closure_alias}.closed_at_us"
    if complete:
        return (
            ExternalContinuityTimeComparisonSpec(
                "lease.acquired_at_us",
                ComparisonOperator.LE,
                "start.issued_at_us",
            ),
            ExternalContinuityTimeComparisonSpec(
                "start.issued_at_us",
                ComparisonOperator.LT,
                "terminal.issued_at_us",
            ),
            ExternalContinuityTimeComparisonSpec(
                "terminal.issued_at_us",
                ComparisonOperator.LE,
                "evaluation_batch.committed_at_us",
            ),
            ExternalContinuityTimeComparisonSpec(
                "evaluation_batch.committed_at_us",
                ComparisonOperator.LE,
                closure_clock,
            ),
            ExternalContinuityTimeComparisonSpec(
                closure_clock,
                ComparisonOperator.LE,
                "lease.expires_at_us",
            ),
        )
    return (
        ExternalContinuityTimeComparisonSpec(
            "lease.acquired_at_us",
            ComparisonOperator.LE,
            "start.issued_at_us",
        ),
        ExternalContinuityTimeComparisonSpec(
            "start.issued_at_us",
            ComparisonOperator.LE,
            closure_clock,
        ),
        ExternalContinuityTimeComparisonSpec(
            closure_clock,
            ComparisonOperator.LE,
            "lease.expires_at_us",
        ),
    )


def _start_terminal_semantic_profile(
    start_verifier_code: str,
    terminal_verifier_code: str,
) -> ExternalContinuitySemanticProfileSpec:
    return ExternalContinuitySemanticProfileSpec(
        semantic_kind=(
            ExternalContinuitySemanticKind.START_TERMINAL_STRICT_PROVIDER_PREFIX
        ),
        required_subject_context_fields=_CONTINUITY_SUBJECT_CONTEXT_FIELDS,
        required_subject_roles=(("left", "START"), ("right", "TERMINAL")),
        require_distinct_evidence_hashes=True,
        require_same_provider_authority=True,
        require_same_provider_genesis=True,
        require_right_projection_strict_extension=True,
        require_strict_issued_chronology=True,
        require_full_lease_time_window=False,
        strict_time_order_paths=("start.issued_at_us", "terminal.issued_at_us"),
        provider_inputs=(
            ExternalContinuityProviderInputSpec(
                "start",
                start_verifier_code,
                ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("attestation_hash", "issued_at_us"),
            ),
            ExternalContinuityProviderInputSpec(
                "terminal",
                terminal_verifier_code,
                ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("attestation_hash", "issued_at_us"),
            ),
        ),
        chronology_branches=(),
    )


def _start_lease_semantic_profile(
    prefix: str,
    start_verifier_code: str,
    lease_verifier_code: str,
    terminal_verifier_code: str,
) -> ExternalContinuitySemanticProfileSpec:
    subject = _chronology_subject_input(prefix)
    closure_alias = "closure" if prefix == "CLOSURE" else "related_closure"
    shared_inputs = (
        (subject,)
        if prefix == "CLOSURE"
        else (subject, _related_closure_input())
    )
    attempt = _attempt_chronology_input(closure_alias)
    complete = ExternalContinuityChronologyBranchSpec(
        branch_kind=ExternalContinuityStatusBranchKind.COMPLETE,
        selector=ExternalContinuityBranchSelectorSpec(
            closure_status_path=f"{closure_alias}.closure_status",
            closure_status="COMPLETE",
            attempt_status_path="attempt.status",
            attempt_status="COMPLETED",
        ),
        required_additional_external_verifier_codes=(terminal_verifier_code,),
        row_inputs=(
            *shared_inputs,
            attempt,
            _batch_chronology_input(closure_alias),
        ),
        comparisons=_chronology_comparisons(closure_alias, complete=True),
    )
    incomplete = ExternalContinuityChronologyBranchSpec(
        branch_kind=ExternalContinuityStatusBranchKind.INCOMPLETE,
        selector=ExternalContinuityBranchSelectorSpec(
            closure_status_path=f"{closure_alias}.closure_status",
            closure_status="NOT_ASSESSABLE",
            attempt_status_path="attempt.status",
            attempt_status="INCOMPLETE",
        ),
        required_additional_external_verifier_codes=(),
        row_inputs=(*shared_inputs, attempt),
        comparisons=_chronology_comparisons(closure_alias, complete=False),
    )
    return ExternalContinuitySemanticProfileSpec(
        semantic_kind=ExternalContinuitySemanticKind.START_LEASE_SUBJECT_TIME_WINDOW,
        required_subject_context_fields=_CONTINUITY_SUBJECT_CONTEXT_FIELDS,
        required_subject_roles=(("left", "START"),),
        require_distinct_evidence_hashes=False,
        require_same_provider_authority=False,
        require_same_provider_genesis=False,
        require_right_projection_strict_extension=False,
        require_strict_issued_chronology=False,
        require_full_lease_time_window=True,
        strict_time_order_paths=(),
        provider_inputs=(
            ExternalContinuityProviderInputSpec(
                "start",
                start_verifier_code,
                ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("attestation_hash", "issued_at_us"),
            ),
            ExternalContinuityProviderInputSpec(
                "lease",
                lease_verifier_code,
                ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("lease_hash", "acquired_at_us", "expires_at_us"),
            ),
            ExternalContinuityProviderInputSpec(
                "terminal",
                terminal_verifier_code,
                ExternalContinuityProviderInputSourceKind.BRANCH_ADDITIONAL,
                ("attestation_hash", "issued_at_us"),
            ),
        ),
        chronology_branches=(complete, incomplete),
    )


def _closure_or_publication_continuity_rules(
    prefix: str,
) -> tuple[ExternalAuthorityContinuitySpec, ...]:
    observed = f"VERIFY_{prefix}_OBSERVED_PIT_BINDING"
    exposure = f"VERIFY_{prefix}_OOS_EXPOSURE_BINDING"
    code = f"VERIFY_{prefix}_CODE_MANIFEST_BINDING"
    start = f"VERIFY_{prefix}_START_EXECUTION_ATTESTATION"
    terminal = f"VERIFY_{prefix}_TERMINAL_EXECUTION_ATTESTATION"
    lease = f"VERIFY_{prefix}_IMMUTABLE_ARTIFACT_LEASE"
    return (
        _continuity_rule(
            f"VERIFY_{prefix}_OBSERVED_PIT_EXPOSURE_CONTINUITY",
            observed,
            exposure,
            _OBSERVED_PIT_EXPOSURE_CONTINUITY_FIELDS,
            reference_presence_column="oos_exposure_id",
        ),
        _continuity_rule(
            f"VERIFY_{prefix}_CODE_START_CONTINUITY",
            code,
            start,
            _CODE_START_CONTINUITY_FIELDS,
            reference_presence_column="start_execution_attestation_hash",
        ),
        _continuity_rule(
            f"VERIFY_{prefix}_START_TERMINAL_CONTINUITY",
            start,
            terminal,
            _START_TERMINAL_CONTINUITY_FIELDS,
            reference_presence_column="terminal_execution_attestation_hash",
            semantic_profile=_start_terminal_semantic_profile(start, terminal),
        ),
        _continuity_rule(
            f"VERIFY_{prefix}_START_LEASE_CONTINUITY",
            start,
            lease,
            _START_LEASE_CONTINUITY_FIELDS,
            reference_presence_column="start_execution_attestation_hash",
            semantic_profile=_start_lease_semantic_profile(
                prefix,
                start,
                lease,
                terminal,
            ),
        ),
    )


def _nullable_reference_checks(table_name: str) -> tuple[CheckConstraintSpec, ...]:
    return (
        _all_or_none_check(
            table_name,
            "oos_reference",
            (
                "oos_exposure_id",
                "exposure_fingerprint",
                "exposure_authority_id",
                "exposure_authority_head",
            ),
        ),
        _all_or_none_check(
            table_name,
            "start_execution_reference",
            (
                "start_execution_attestation_hash",
                "execution_attestation_authority_id",
                "start_execution_attestation_authority_head",
            ),
        ),
        _all_or_none_check(
            table_name,
            "terminal_execution_reference",
            (
                "terminal_execution_attestation_hash",
                "terminal_execution_attestation_authority_head",
            ),
        ),
        _terminal_execution_check(table_name),
        _all_or_none_check(
            table_name,
            "lease_reference",
            (
                "immutable_artifact_lease_id",
                "immutable_artifact_lease_hash",
                "immutable_artifact_lease_authority_id",
                "immutable_artifact_lease_authority_head",
            ),
        ),
        CheckConstraintSpec(
            f"{table_name}_external_reference_branch",
            _external_reference_branch_predicate(),
        ),
    )


_C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS = (
    "record_type",
    "receipt_schema_version",
    "authority_instance_id",
    "genesis_record_hash",
    "authority_lineage_hash",
    "publication_receipt_id",
    "publication_ordinal",
    "previous_record_hash",
    "family_id",
    "selection_scope_id",
    "selection_decision_id",
    "winner_config_hash",
    "winner_evaluation_receipt_hash",
    "closure_manifest_hash",
    "closure_status",
    "attempt_status",
    "family_assessment_conclusion_hash",
    "ordered_grid_assessment_record_hashes_json",
    "ordered_grid_assessment_record_hashes_hash",
    "minimum_replication_count",
    "assessment_status",
    "diagnostic_verdict",
    "decision_class",
    "promotion_effect",
    "artifact_kind",
    "artifact_core_hash",
    "artifact_core_scan_receipt_hash",
    "published_artifact_envelope_hash",
    "target_content_addressed_path",
    "public_artifact_scan_policy_id",
    "dlp_policy_id",
    "dlp_policy_version",
    "dlp_policy_hash",
    "scanner_implementation",
    "scanner_version",
    "scan_status",
    "scan_result_hash",
    "envelope_scanned_at_us",
    "exporter_event_hash",
    "exporter_authority_head",
    "failure_reasons_json",
    "published_at_us",
)

_C6_PUBLICATION_RECEIPT_ADDED_FIELDS = (
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

_C9_PUBLICATION_RECEIPT_FINAL_ROW_HASH_FIELDS = (
    _C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS[:4]
    + _C6_PUBLICATION_RECEIPT_ADDED_FIELDS
    + _C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS[4:]
)

_C6_PUBLICATION_CLOSURE_EXACT_BINDINGS = (
    ("authority_instance_id", "authority_instance_id"),
    ("family_id", "family_id"),
    ("closure_manifest_hash", "record_hash"),
    ("closure_status", "closure_status"),
    *((field, field) for field in _C6_PUBLICATION_RECEIPT_ADDED_FIELDS),
)

_C6_PUBLICATION_CLOSURE_EXTERNAL_SUMMARIES_GUARD = _guard(
    "artifact_publication_receipts",
    "PUBLICATION_CLOSURE_EXTERNAL_SUMMARIES_EXACT",
    _relation(
        "data_snooping_family_closures",
        _C6_PUBLICATION_CLOSURE_EXACT_BINDINGS,
        negated=True,
    ),
)


def _closure_reference_rules() -> tuple[VerificationRuleSpec, ...]:
    optional_columns = tuple(
        local
        for bindings in (
            _OOS_EXPOSURE_BINDINGS,
            _CLOSURE_PUBLICATION_START_ATTESTATION_BINDINGS,
            _CLOSURE_PUBLICATION_TERMINAL_ATTESTATION_BINDINGS,
            _LEASE_BINDINGS,
        )
        for local, _ in bindings
    )
    return (
        _plain_rule(
            "VERIFY_CLOSURE_EXTERNAL_REFERENCE_BRANCH",
            VerificationRuleKind.STATE_BRANCH,
            tuple(dict.fromkeys(optional_columns)),
            predicate=_external_reference_branch_predicate(),
        ),
        _external_rule(
            "VERIFY_CLOSURE_OBSERVED_PIT_BINDING",
            ExternalReferenceKind.OBSERVED_PIT,
            "ObservedPitDatasetHandle",
            "observed_pit_dataset_handle.verify",
            _OBSERVED_DECLARATION_BINDINGS,
        ),
        _external_rule(
            "VERIFY_CLOSURE_OOS_EXPOSURE_BINDING",
            ExternalReferenceKind.OOS_EXPOSURE,
            "C4GlobalOosExposureAuthorityRow",
            "c4.global_oos_exposure.verify",
            _OOS_EXPOSURE_BINDINGS,
            nullable=True,
            provider_component_id=_C4_COMPONENT_ID,
        ),
        _external_rule(
            "VERIFY_CLOSURE_CODE_MANIFEST_BINDING",
            ExternalReferenceKind.CODE_MANIFEST,
            "CodeManifestEvidence",
            "code_manifest_evidence.verify",
            _CODE_MANIFEST_BINDINGS,
        ),
        _external_rule(
            "VERIFY_CLOSURE_START_EXECUTION_ATTESTATION",
            ExternalReferenceKind.EXECUTION_ATTESTATION,
            "ExecutionCodeAttestation",
            "execution_code_attestation.verify",
            _CLOSURE_PUBLICATION_START_ATTESTATION_BINDINGS,
            nullable=True,
        ),
        _external_rule(
            "VERIFY_CLOSURE_TERMINAL_EXECUTION_ATTESTATION",
            ExternalReferenceKind.EXECUTION_ATTESTATION,
            "ExecutionCodeAttestation",
            "execution_code_attestation.verify",
            _CLOSURE_PUBLICATION_TERMINAL_ATTESTATION_BINDINGS,
            nullable=True,
        ),
        _external_rule(
            "VERIFY_CLOSURE_IMMUTABLE_ARTIFACT_LEASE",
            ExternalReferenceKind.IMMUTABLE_LEASE,
            "ImmutableArtifactLease",
            "immutable_artifact_lease.verify",
            _LEASE_BINDINGS,
            nullable=True,
        ),
    )


def _publication_reference_rules() -> tuple[VerificationRuleSpec, ...]:
    external_columns = tuple(
        dict.fromkeys(
            local
            for bindings in (
                _OBSERVED_DECLARATION_BINDINGS,
                _OOS_EXPOSURE_BINDINGS,
                _CODE_MANIFEST_BINDINGS,
                _CLOSURE_PUBLICATION_START_ATTESTATION_BINDINGS,
                _CLOSURE_PUBLICATION_TERMINAL_ATTESTATION_BINDINGS,
                _LEASE_BINDINGS,
            )
            for local, _ in bindings
        )
    )
    return (
        _plain_rule(
            "VERIFY_PUBLICATION_CLOSURE_BINDING",
            VerificationRuleKind.CROSS_TABLE_REFERENCE,
            ("closure_manifest_hash",),
            referenced_table="data_snooping_family_closures",
        ),
        _plain_rule(
            "VERIFY_PUBLICATION_EXTERNAL_REFERENCE_TOTALITY",
            VerificationRuleKind.STATE_BRANCH,
            ("closure_status", "attempt_status", *external_columns),
            predicate=_publication_attempt_reference_branch_predicate(),
        ),
        VerificationRuleSpec(
            rule_id="publication_receipt_closure_external_summaries_exact",
            kind=VerificationRuleKind.CROSS_TABLE_REFERENCE,
            columns=tuple(
                dict.fromkeys(
                    local
                    for local, _ in _C6_PUBLICATION_CLOSURE_EXACT_BINDINGS
                )
            ),
            referenced_table="data_snooping_family_closures",
            structural_source=StructuralVerificationSourceSpec(
                kind=StructuralVerificationSourceKind.TRIGGER_POLICY,
                source_id=_C6_PUBLICATION_CLOSURE_EXTERNAL_SUMMARIES_GUARD.name,
            ),
        ),
        _external_rule(
            "VERIFY_PUBLICATION_OBSERVED_PIT_BINDING",
            ExternalReferenceKind.OBSERVED_PIT,
            "ObservedPitDatasetHandle",
            "observed_pit_dataset_handle.verify",
            _OBSERVED_DECLARATION_BINDINGS,
        ),
        _external_rule(
            "VERIFY_PUBLICATION_OOS_EXPOSURE_BINDING",
            ExternalReferenceKind.OOS_EXPOSURE,
            "C4GlobalOosExposureAuthorityRow",
            "c4.global_oos_exposure.verify",
            _OOS_EXPOSURE_BINDINGS,
            nullable=True,
            provider_component_id=_C4_COMPONENT_ID,
        ),
        _external_rule(
            "VERIFY_PUBLICATION_CODE_MANIFEST_BINDING",
            ExternalReferenceKind.CODE_MANIFEST,
            "CodeManifestEvidence",
            "code_manifest_evidence.verify",
            _CODE_MANIFEST_BINDINGS,
        ),
        _external_rule(
            "VERIFY_PUBLICATION_START_EXECUTION_ATTESTATION",
            ExternalReferenceKind.EXECUTION_ATTESTATION,
            "ExecutionCodeAttestation",
            "execution_code_attestation.verify",
            _CLOSURE_PUBLICATION_START_ATTESTATION_BINDINGS,
            nullable=True,
        ),
        _external_rule(
            "VERIFY_PUBLICATION_TERMINAL_EXECUTION_ATTESTATION",
            ExternalReferenceKind.EXECUTION_ATTESTATION,
            "ExecutionCodeAttestation",
            "execution_code_attestation.verify",
            _CLOSURE_PUBLICATION_TERMINAL_ATTESTATION_BINDINGS,
            nullable=True,
        ),
        _external_rule(
            "VERIFY_PUBLICATION_IMMUTABLE_ARTIFACT_LEASE",
            ExternalReferenceKind.IMMUTABLE_LEASE,
            "ImmutableArtifactLease",
            "immutable_artifact_lease.verify",
            _LEASE_BINDINGS,
            nullable=True,
        ),
        VerificationRuleSpec(
            rule_id="publication_receipt_row_hash",
            kind=VerificationRuleKind.ROW_HASH,
            columns=_C9_PUBLICATION_RECEIPT_FINAL_ROW_HASH_FIELDS,
            verifier_code="c9.verify_publication_receipt_row_hash_v1",
        ),
        VerificationRuleSpec(
            rule_id="publication_receipt_self_contained_bundle",
            kind=VerificationRuleKind.CONTENT_BINDING,
            columns=(*_C9_PUBLICATION_RECEIPT_FINAL_ROW_HASH_FIELDS, "receipt_record_hash"),
            verifier_code="c9.verify_self_contained_artifact_bundle_v1",
        ),
    )


_C6_SURFACE_DEFINITIONS = (
    (
        "data_snooping_declarations",
        "C6_DATA_SNOOPING_DECLARATION_EMBEDDED_REFS_V1",
        "c6.declaration.embedded_refs",
        (),
        (
            _column("observed_pit_authority_id"),
            _column("observed_pit_handle_id"),
            _column("observed_pit_content_hash"),
            _column("knowledge_time_policy_id"),
            _column("knowledge_time_policy_version"),
            _column("observed_pit_authority_head"),
            _column("code_manifest_policy_id"),
            _column("code_manifest_policy_version"),
            _column("code_manifest_policy_hash"),
            _column("code_manifest_evidence_hash"),
            _column("code_manifest_authority_id"),
            _column("code_manifest_authority_head"),
        ),
        (ExternalReferenceKind.OBSERVED_PIT, ExternalReferenceKind.CODE_MANIFEST),
        (
            _external_rule(
                "VERIFY_OBSERVED_PIT_DECLARATION_BINDING",
                ExternalReferenceKind.OBSERVED_PIT,
                "ObservedPitDatasetHandle",
                "observed_pit_dataset_handle.verify",
                _OBSERVED_DECLARATION_BINDINGS,
            ),
            _external_rule(
                "VERIFY_CODE_MANIFEST_DECLARATION_BINDING",
                ExternalReferenceKind.CODE_MANIFEST,
                "CodeManifestEvidence",
                "code_manifest_evidence.verify",
                _CODE_MANIFEST_BINDINGS,
            ),
        ),
        (),
        (),
    ),
    (
        "global_oos_exposures",
        "C6_GLOBAL_OOS_EXPOSURE_EMBEDDED_REFS_V1",
        "c6.global_oos_exposure.embedded_refs",
        ("selection_scope_id", "selection_decision_id", "exposure_fingerprint"),
        (
            _column("selection_scope_id"),
            _column("selection_decision_id"),
            _column("observed_pit_authority_id"),
            _column("observed_pit_handle_id"),
            _column("observed_pit_content_hash"),
            _column("observed_pit_authority_head"),
            _column("dataset_id"),
            _column("dataset_version"),
            _column("universe_id"),
            _column("universe_hash"),
            _column("ordered_period_calendar_hash"),
            _column("knowledge_time_policy_id"),
            _column("knowledge_time_policy_version"),
            _column("label_horizon"),
            _column("purge_periods", SqlType.INTEGER),
            _column("embargo_periods", SqlType.INTEGER),
            _column("cost_contract_hash"),
            _column("benchmark_contract_hash"),
            _column("exposure_fingerprint"),
            _column("exposure_version", SqlType.INTEGER),
        ),
        (ExternalReferenceKind.OBSERVED_PIT,),
        (
            _plain_rule(
                "VERIFY_GLOBAL_OOS_EXPOSURE_IDENTITY",
                VerificationRuleKind.EXPOSURE_CONSUMPTION,
                (
                    "selection_scope_id",
                    "selection_decision_id",
                    "exposure_fingerprint",
                    "exposure_version",
                ),
            ),
            _external_rule(
                "VERIFY_OBSERVED_PIT_EXPOSURE_BINDING",
                ExternalReferenceKind.OBSERVED_PIT,
                "ObservedPitDatasetHandle",
                "observed_pit_dataset_handle.verify",
                _OBSERVED_EXPOSURE_BINDINGS,
            ),
            _plain_rule(
                "VERIFY_EXPOSURE_FINGERPRINT",
                VerificationRuleKind.CONTENT_BINDING,
                (
                    "observed_pit_content_hash",
                    "dataset_version",
                    "universe_id",
                    "universe_hash",
                    "ordered_period_calendar_hash",
                    "label_horizon",
                    "purge_periods",
                    "embargo_periods",
                    "exposure_fingerprint",
                ),
            ),
        ),
        (
            _named_comparison_check(
                "global_oos_exposures_nonnegative_purge_periods",
                "purge_periods",
                ComparisonOperator.GE,
                0,
            ),
            _named_comparison_check(
                "global_oos_exposures_nonnegative_embargo_periods",
                "embargo_periods",
                ComparisonOperator.GE,
                0,
            ),
            _named_comparison_check(
                "global_oos_exposures_positive_exposure_version",
                "exposure_version",
                ComparisonOperator.GT,
                0,
            ),
        ),
        (),
    ),
    (
        "data_snooping_oos_releases",
        "C6_OOS_RELEASE_EMBEDDED_REFS_V1",
        "c6.oos_release.embedded_refs",
        (
            "authority_instance_id",
            "attempt_id",
            "started_attempt_event_id",
            "family_id",
            "oos_exposure_id",
        ),
        (
            _column("authority_instance_id"),
            _column("attempt_id"),
            _column("started_attempt_event_id"),
            _column("family_id"),
            _column("oos_exposure_id"),
            _column("exposure_fingerprint"),
            _column("exposure_authority_id"),
            _column("exposure_authority_head"),
            _column("start_execution_attestation_hash"),
            _column("execution_attestation_authority_id"),
            _column("execution_attestation_authority_head"),
            _column("immutable_artifact_lease_id"),
            _column("immutable_artifact_lease_hash"),
            _column("immutable_artifact_lease_authority_id"),
            _column("immutable_artifact_lease_authority_head"),
        ),
        (
            ExternalReferenceKind.OOS_EXPOSURE,
            ExternalReferenceKind.EXECUTION_ATTESTATION,
            ExternalReferenceKind.IMMUTABLE_LEASE,
        ),
        (
            _external_rule(
                "VERIFY_OOS_RELEASE_EXPOSURE_BINDING",
                ExternalReferenceKind.OOS_EXPOSURE,
                "C4GlobalOosExposureAuthorityRow",
                "c4.global_oos_exposure.verify",
                _OOS_EXPOSURE_BINDINGS,
                provider_component_id=_C4_COMPONENT_ID,
            ),
            _external_rule(
                "VERIFY_START_EXECUTION_ATTESTATION",
                ExternalReferenceKind.EXECUTION_ATTESTATION,
                "ExecutionCodeAttestation",
                "execution_code_attestation.verify",
                _START_ATTESTATION_BINDINGS,
            ),
            _external_rule(
                "VERIFY_IMMUTABLE_ARTIFACT_LEASE",
                ExternalReferenceKind.IMMUTABLE_LEASE,
                "ImmutableArtifactLease",
                "immutable_artifact_lease.verify",
                _LEASE_BINDINGS,
            ),
            _plain_rule(
                "VERIFY_OOS_RELEASE_STARTED_EXECUTION_IDENTITY",
                VerificationRuleKind.CROSS_TABLE_REFERENCE,
                (
                    "authority_instance_id",
                    "attempt_id",
                    "started_attempt_event_id",
                    "family_id",
                    "oos_exposure_id",
                    "start_execution_attestation_hash",
                    "immutable_artifact_lease_hash",
                ),
                referenced_table="data_snooping_oos_access_attempts",
            ),
        ),
        (),
        (
            _continuity_rule(
                "VERIFY_OOS_RELEASE_START_LEASE_CONTINUITY",
                "VERIFY_START_EXECUTION_ATTESTATION",
                "VERIFY_IMMUTABLE_ARTIFACT_LEASE",
                _START_LEASE_CONTINUITY_FIELDS,
                reference_presence_column=None,
            ),
        ),
    ),
    (
        "data_snooping_family_closures",
        "C6_FAMILY_CLOSURE_EMBEDDED_REFS_V1",
        "c6.family_closure.embedded_refs",
        (),
        (
            _column("observed_pit_authority_id"),
            _column("observed_pit_handle_id"),
            _column("observed_pit_content_hash"),
            _column("knowledge_time_policy_id"),
            _column("knowledge_time_policy_version"),
            _column("observed_pit_authority_head"),
            _column("oos_exposure_id", nullable=True),
            _column("exposure_fingerprint", nullable=True),
            _column("exposure_authority_id", nullable=True),
            _column("exposure_authority_head", nullable=True),
            _column("code_manifest_policy_id"),
            _column("code_manifest_policy_version"),
            _column("code_manifest_policy_hash"),
            _column("code_manifest_evidence_hash"),
            _column("code_manifest_authority_id"),
            _column("code_manifest_authority_head"),
            _column("start_execution_attestation_hash", nullable=True),
            _column("terminal_execution_attestation_hash", nullable=True),
            _column("execution_attestation_authority_id", nullable=True),
            _column("start_execution_attestation_authority_head", nullable=True),
            _column("terminal_execution_attestation_authority_head", nullable=True),
            _column("immutable_artifact_lease_id", nullable=True),
            _column("immutable_artifact_lease_hash", nullable=True),
            _column("immutable_artifact_lease_authority_id", nullable=True),
            _column("immutable_artifact_lease_authority_head", nullable=True),
        ),
        _C6_EXTERNAL_REFERENCE_KINDS,
        _closure_reference_rules(),
        _nullable_reference_checks("data_snooping_family_closures"),
        _closure_or_publication_continuity_rules("CLOSURE"),
    ),
    (
        "artifact_publication_receipts",
        "C6_PUBLICATION_RECEIPT_EMBEDDED_REFS_V1",
        "c6.publication_receipt.embedded_refs",
        ("closure_manifest_hash",),
        (
            _column("closure_manifest_hash"),
            _column("observed_pit_authority_id"),
            _column("observed_pit_handle_id"),
            _column("observed_pit_content_hash"),
            _column("knowledge_time_policy_id"),
            _column("knowledge_time_policy_version"),
            _column("observed_pit_authority_head"),
            _column("oos_exposure_id", nullable=True),
            _column("exposure_fingerprint", nullable=True),
            _column("exposure_authority_id", nullable=True),
            _column("exposure_authority_head", nullable=True),
            _column("code_manifest_policy_id"),
            _column("code_manifest_policy_version"),
            _column("code_manifest_policy_hash"),
            _column("code_manifest_evidence_hash"),
            _column("code_manifest_authority_id"),
            _column("code_manifest_authority_head"),
            _column("start_execution_attestation_hash", nullable=True),
            _column("terminal_execution_attestation_hash", nullable=True),
            _column("execution_attestation_authority_id", nullable=True),
            _column("start_execution_attestation_authority_head", nullable=True),
            _column("terminal_execution_attestation_authority_head", nullable=True),
            _column("immutable_artifact_lease_id", nullable=True),
            _column("immutable_artifact_lease_hash", nullable=True),
            _column("immutable_artifact_lease_authority_id", nullable=True),
            _column("immutable_artifact_lease_authority_head", nullable=True),
        ),
        _C6_EXTERNAL_REFERENCE_KINDS,
        _publication_reference_rules(),
        (
            *_nullable_reference_checks("artifact_publication_receipts"),
            CheckConstraintSpec(
                "artifact_publication_receipts_attempt_reference_branch",
                _publication_attempt_reference_branch_predicate(),
            ),
        ),
        _closure_or_publication_continuity_rules("PUBLICATION"),
    ),
)


_C6_RELEASE_STARTED_EXECUTION_GUARD = _guard(
    "data_snooping_oos_releases",
    "OOS_RELEASE_STARTED_EXECUTION_IDENTITY_EXACT",
    _relation(
        "data_snooping_oos_access_attempts",
        (
            ("authority_instance_id", "authority_instance_id"),
            ("attempt_id", "attempt_id"),
            ("started_attempt_event_id", "attempt_event_id"),
            ("family_id", "family_id"),
            ("oos_exposure_id", "oos_exposure_id"),
            ("start_execution_attestation_hash", "start_execution_attestation_hash"),
            ("immutable_artifact_lease_hash", "immutable_artifact_lease_hash"),
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


def _surface(
    table_name: str,
    extension_id: str,
    capability: str,
    core_owned_fields: tuple[str, ...],
    columns: tuple[ColumnSpec, ...],
    reference_kinds: tuple[ExternalReferenceKind, ...],
    verification_rules: tuple[VerificationRuleSpec, ...],
    checks: tuple[CheckConstraintSpec, ...],
    external_continuity_rules: tuple[ExternalAuthorityContinuitySpec, ...],
) -> EmbeddedReferenceSurfaceSpec:
    added_columns = tuple(column for column in columns if column.name not in core_owned_fields)
    added_names = tuple(column.name for column in added_columns)
    extension_triggers = {
        "data_snooping_oos_releases": (_C6_RELEASE_STARTED_EXECUTION_GUARD,),
        "artifact_publication_receipts": (
            _C6_PUBLICATION_CLOSURE_EXTERNAL_SUMMARIES_GUARD,
        ),
    }.get(table_name, ())
    extension = AuthorityTableExtensionSpec(
        extension_id=extension_id,
        target_table=table_name,
        column_insertion=OrderedColumnInsertionSpec(
            InsertionPosition.AFTER,
            "genesis_record_hash",
            added_columns,
        ),
        row_hash_field_insertion=OrderedFieldInsertionSpec(
            InsertionPosition.AFTER,
            "genesis_record_hash",
            added_names,
        ),
        checks=checks,
        triggers=extension_triggers,
        verification_rules=verification_rules,
        external_continuity_rules=external_continuity_rules,
    )
    requirement = RequiredTableExtensionSpec(
        extension_id=extension_id,
        target_table=table_name,
        provider_component_id=_C6_COMPONENT_ID,
        required_capability=capability,
    )
    return EmbeddedReferenceSurfaceSpec(
        table_name=table_name,
        columns=columns,
        reference_kinds=reference_kinds,
        verifier_ids=tuple(rule.verifier_code or rule.rule_id for rule in verification_rules),
        row_hash_fields=tuple(column.name for column in columns),
        core_owned_fields=core_owned_fields,
        additional_rule_columns=(
            (*_C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS, "receipt_record_hash")
            if table_name == "artifact_publication_receipts"
            else ()
        ),
        extension=extension,
        requirement=requirement,
    )


C6_EMBEDDED_REFERENCE_SPECS = tuple(_surface(*definition) for definition in _C6_SURFACE_DEFINITIONS)
C6_TABLE_EXTENSIONS = tuple(surface.extension for surface in C6_EMBEDDED_REFERENCE_SPECS)
C6_REQUIRED_EXTENSION_SPECS = tuple(surface.requirement for surface in C6_EMBEDDED_REFERENCE_SPECS)
C6_BUSINESS_TABLE_SPECS: tuple[AuthorityTableSpec, ...] = ()


_C6_CAPABILITIES = tuple(definition[2] for definition in _C6_SURFACE_DEFINITIONS)

_C6_DEPENDENCIES = (
    ComponentDependencySpec(
        _C4_COMPONENT_ID,
        DependencyKind.CONTRACT_LINEAGE,
        (
            "c4.global_oos_exposure.core",
            "c4.global_oos_exposure.verify",
        ),
    ),
    ComponentDependencySpec(
        "C7_ONE_SHOT_OOS_V1",
        DependencyKind.CONTRACT_LINEAGE,
        (
            "c7.oos_release.core",
            "c7.oos_attempt.event_stream",
            "c7.family_evaluation_batch.atomic",
        ),
    ),
    ComponentDependencySpec(
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        DependencyKind.CONTRACT_LINEAGE,
        ("c8.declaration_closure.core",),
    ),
    ComponentDependencySpec(
        "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1",
        DependencyKind.CONTRACT_LINEAGE,
        ("c9.publication_receipt.core",),
    ),
)

_C6_EXPOSURE_CONTRACT_FIELDS = (
    "observed_pit_authority_id",
    "observed_pit_handle_id",
    "observed_pit_content_hash",
    "dataset_id",
    "dataset_version",
    "universe_id",
    "universe_hash",
    "ordered_period_calendar_hash",
    "knowledge_time_policy_id",
    "knowledge_time_policy_version",
    "label_horizon",
    "purge_periods",
    "embargo_periods",
    "cost_contract_hash",
    "benchmark_contract_hash",
    "exposure_version",
    "observed_pit_authority_head",
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

_C6_COVERAGE = (
    *(
        ContractFieldCoverageSpec(
            contract_type="OosExposureRef",
            field_name=field_name,
            kind=CoverageKind.COLUMN,
            table_name="global_oos_exposures",
            target_field=field_name,
        )
        for field_name in _C6_EXPOSURE_CONTRACT_FIELDS
    ),
    *(
        ContractFieldCoverageSpec(
            contract_type="OosReleaseReceipt",
            field_name=field_name,
            kind=CoverageKind.COLUMN,
            table_name="data_snooping_oos_releases",
            target_field=field_name,
        )
        for field_name in _C6_RELEASE_CONTRACT_FIELDS
    ),
)

C6_REQUIRED_EXTERNAL_PROVIDER_CAPABILITIES = (
    ExternalProviderCapabilityRequirementSpec(
        provider_contract_type="ObservedPitDatasetHandle",
        provider_capability="observed_pit_dataset_handle.verify",
        resolution_stage=ExternalProviderResolutionStage.C8_PASS_M,
    ),
    ExternalProviderCapabilityRequirementSpec(
        provider_contract_type="CodeManifestEvidence",
        provider_capability="code_manifest_evidence.verify",
        resolution_stage=ExternalProviderResolutionStage.C8_PASS_M,
    ),
    ExternalProviderCapabilityRequirementSpec(
        provider_contract_type="ExecutionCodeAttestation",
        provider_capability="execution_code_attestation.verify",
        resolution_stage=ExternalProviderResolutionStage.C8_PASS_M,
    ),
    ExternalProviderCapabilityRequirementSpec(
        provider_contract_type="ImmutableArtifactLease",
        provider_capability="immutable_artifact_lease.verify",
        resolution_stage=ExternalProviderResolutionStage.C8_PASS_M,
    ),
)

C6_SCHEMA_COMPONENT = SchemaComponentSpec(
    component_id=_C6_COMPONENT_ID,
    capabilities=_C6_CAPABILITIES,
    dependencies=_C6_DEPENDENCIES,
    tables=C6_BUSINESS_TABLE_SPECS,
    extensions=C6_TABLE_EXTENSIONS,
    coverage=_C6_COVERAGE,
    required_external_provider_capabilities=C6_REQUIRED_EXTERNAL_PROVIDER_CAPABILITIES,
)


__all__ = [
    "AuthorityTableSpec",
    "C5_AUTHORITY_TABLE_SPECS",
    "C5_DEFERRED_DISPOSITION_CONSTRAINTS",
    "C5_SCHEMA_COMPONENT",
    "C6_BUSINESS_TABLE_SPECS",
    "C6_EMBEDDED_REFERENCE_SPECS",
    "C6_REQUIRED_EXTERNAL_PROVIDER_CAPABILITIES",
    "C6_REQUIRED_EXTENSION_SPECS",
    "C6_SCHEMA_COMPONENT",
    "C6_TABLE_EXTENSIONS",
    "CandidateDisposition",
    "DeferredDispositionConstraintSpec",
    "EmbeddedReferenceSurfaceSpec",
    "ExternalReferenceKind",
    "PerformanceAccess",
    "ProposalIdentityState",
]
