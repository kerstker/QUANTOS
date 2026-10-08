"""Private Pass-C schema AST for the C4 family-authority slice.

This module owns declarations only.  It intentionally has no renderer,
activation entry point, database connection, composition step, or seal.  C6
and C7 must extend two owned tables before Pass M can derive a complete family
schema manifest.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum

from .family_schema_spec import (
    ActionPolicySemanticRecipeSpec,
    AuthorityTableSpec,
    ChainGenesisRule,
    ChainHeadRule,
    ChainPredecessorExpression,
    ChainSpec,
    CanonicalEncoding,
    CanonicalContentHashGoldenVectorSpec,
    CanonicalContentHashSemanticRecipeSpec,
    CanonicalContentSourceKind,
    CanonicalJsonPayloadHashExpression,
    CheckConstraintSpec,
    ColumnSpec,
    ColumnValue,
    ComparisonExpression,
    ComparisonOperator,
    ContractDerivationKind,
    ContractFieldCoverageSpec,
    ContractFieldDerivationSpec,
    CoverageKind,
    ExternalAuthorityClaimProfileSpec,
    ExternalAuthorityReferenceKind,
    ExternalFieldBindingSpec,
    ExternalProviderCapabilityRequirementSpec,
    ExternalProviderClaimOperator,
    ExternalProviderResolutionStage,
    ExternalProviderSemanticClaimSpec,
    ForeignKeyAction,
    ForeignKeyDeferrability,
    ForeignKeySpec,
    IndexColumnSpec,
    IndexSpec,
    IdentityDerivationGoldenVectorSpec,
    IdentityDerivationPayloadShape,
    IdentityDerivationSemanticRecipeSpec,
    IdentityDigestRepresentation,
    IdentityIssuance,
    IdentityPredecessorSourceKind,
    IdentityPredecessorSourceSpec,
    LiteralValue,
    LogicalExpression,
    LogicalOperator,
    LookupBinding,
    MembershipExpression,
    NotExpression,
    NullExpression,
    NullOperator,
    PredicateExpression,
    QualifiedColumnValue,
    RelationAggregateKind,
    RelationExistsExpression,
    RelationJoinAggregateExpression,
    RelationJoinEqualitySpec,
    RelationJoinSpec,
    RequiredTableExtensionSpec,
    RequiredContractSurfaceSpec,
    RowHashSpec,
    RowHashAlgorithm,
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


_COMPONENT_ID = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"


class C4AuthorityAction(_StrEnum):
    QUESTION_ISSUED = "QUESTION_ISSUED"
    QUESTION_ALIAS_REGISTERED = "QUESTION_ALIAS_REGISTERED"
    LINEAGE_ROOT_REGISTERED = "LINEAGE_ROOT_REGISTERED"
    LINEAGE_NODE_REGISTERED = "LINEAGE_NODE_REGISTERED"
    CONFIG_LINEAGE_BOUND = "CONFIG_LINEAGE_BOUND"
    SELECTION_SCOPE_ISSUED = "SELECTION_SCOPE_ISSUED"
    SELECTION_SCOPE_ALIAS_REGISTERED = "SELECTION_SCOPE_ALIAS_REGISTERED"
    RESEARCH_PROGRAM_ISSUED = "RESEARCH_PROGRAM_ISSUED"
    SELECTION_DECISION_ISSUED = "SELECTION_DECISION_ISSUED"
    PUBLISHABLE_DECISION_RESERVED = "PUBLISHABLE_DECISION_RESERVED"
    SELECTION_SCOPE_OUTCOME_RECORDED = "SELECTION_SCOPE_OUTCOME_RECORDED"
    OOS_EXPOSURE_ISSUED = "OOS_EXPOSURE_ISSUED"
    OOS_EXPOSURE_CONSUMED = "OOS_EXPOSURE_CONSUMED"
    PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1 = (
        "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1"
    )


C4ActionPolicy = ActionPolicySemanticRecipeSpec


C4_ACTION_POLICIES = (
    C4ActionPolicy(
        C4AuthorityAction.QUESTION_ISSUED.value,
        "research_questions",
        True,
    ),
    C4ActionPolicy(
        C4AuthorityAction.QUESTION_ALIAS_REGISTERED.value,
        "research_question_aliases",
        True,
    ),
    C4ActionPolicy(
        C4AuthorityAction.LINEAGE_ROOT_REGISTERED.value,
        "strategy_lineage_roots",
        True,
    ),
    C4ActionPolicy(
        C4AuthorityAction.LINEAGE_NODE_REGISTERED.value,
        "strategy_lineage_nodes",
        True,
    ),
    C4ActionPolicy(
        C4AuthorityAction.CONFIG_LINEAGE_BOUND.value,
        "strategy_config_lineage_bindings",
        True,
    ),
    C4ActionPolicy(
        C4AuthorityAction.SELECTION_SCOPE_ISSUED.value,
        "selection_scopes",
        True,
    ),
    C4ActionPolicy(
        C4AuthorityAction.SELECTION_SCOPE_ALIAS_REGISTERED.value,
        "selection_scope_aliases",
        True,
    ),
    C4ActionPolicy(
        C4AuthorityAction.RESEARCH_PROGRAM_ISSUED.value,
        "research_programs",
        False,
    ),
    C4ActionPolicy(
        C4AuthorityAction.SELECTION_DECISION_ISSUED.value,
        "selection_decisions",
        False,
    ),
    C4ActionPolicy(
        C4AuthorityAction.PUBLISHABLE_DECISION_RESERVED.value,
        "selection_scope_publishable_decisions",
        False,
    ),
    C4ActionPolicy(
        C4AuthorityAction.SELECTION_SCOPE_OUTCOME_RECORDED.value,
        "selection_scope_outcomes",
        False,
    ),
    C4ActionPolicy(
        C4AuthorityAction.OOS_EXPOSURE_ISSUED.value,
        "global_oos_exposures",
        False,
    ),
    C4ActionPolicy(
        C4AuthorityAction.OOS_EXPOSURE_CONSUMED.value,
        "global_oos_exposure_consumptions",
        False,
    ),
    C4ActionPolicy(
        C4AuthorityAction.PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1.value,
        "artifact_publication_receipts",
        False,
    ),
)

_GRANT_REFERENCE_COLUMNS = (
    "grant_id",
    "grant_record_hash",
    "grant_policy_id",
    "grant_policy_version",
    "grant_policy_hash",
    "grant_authority_id",
    "grant_authority_head",
)


IdentityIssuanceSpec = IdentityDerivationSemanticRecipeSpec
CanonicalContentHashSpec = CanonicalContentHashSemanticRecipeSpec


def _identity_issuance(
    table_name: str,
    field_name: str,
    verification_rule_id: str,
    domain_separator: str,
    source_fields: tuple[str, ...],
    golden_source_values: tuple[str | int | None, ...],
    golden_sha256: str,
) -> IdentityIssuanceSpec:
    predecessor_source = (
        IdentityPredecessorSourceSpec(
            IdentityPredecessorSourceKind.LOCAL_COLUMN,
            local_column="prev_record_hash",
        )
        if table_name == "research_authority_events"
        else IdentityPredecessorSourceSpec(
            IdentityPredecessorSourceKind.EXACT_AUTHORITY_EVENT_LOOKUP,
            binding_field="authority_event_id",
            source_table="research_authority_events",
            source_key_field="event_id",
            source_value_field="prev_record_hash",
        )
    )
    return IdentityIssuanceSpec(
        table_name=table_name,
        field_name=field_name,
        issuance=IdentityIssuance.AUTHORITY_DERIVED,
        verification_rule_id=verification_rule_id,
        domain_separator=domain_separator,
        source_fields=source_fields,
        predecessor_source=predecessor_source,
        golden_vector=IdentityDerivationGoldenVectorSpec(
            predecessor_record_hash="0" * 64,
            source_values=golden_source_values,
            expected_sha256=golden_sha256,
        ),
    )


C4_IDENTITY_ISSUANCE = (
    _identity_issuance(
        "research_questions",
        "research_question_id",
        "research_questions_question_id_issuance",
        "QUANTOS_RESEARCH_QUESTION_ID_V1",
        ("authority_instance_id", "question_hash"),
        ("authority:g1", "1" * 64),
        "4700118ebfcefbc24bda6dc42762f39f67be448335a202d5f09e9e3b193be519",
    ),
    _identity_issuance(
        "strategy_lineage_roots",
        "strategy_lineage_root_id",
        "strategy_lineage_roots_root_id_issuance",
        "QUANTOS_STRATEGY_LINEAGE_ROOT_ID_V1",
        ("authority_instance_id", "source_manifest_hash"),
        ("authority:g1", "2" * 64),
        "9c53bde4fd81ac49ad4642230fc6a1e10c40696b209c205e0d472597e373ed9c",
    ),
    _identity_issuance(
        "strategy_lineage_nodes",
        "strategy_lineage_node_id",
        "strategy_lineage_nodes_node_id_issuance",
        "QUANTOS_STRATEGY_LINEAGE_NODE_ID_V1",
        (
            "authority_instance_id",
            "strategy_lineage_root_id",
            "parent_strategy_lineage_node_id",
            "node_manifest_hash",
        ),
        ("authority:g1", "3" * 64, None, "4" * 64),
        "b6a6e7b90e7a906103a9bed7884a35f23bc78957ae7fd127793f08332871d871",
    ),
    _identity_issuance(
        "research_authority_events",
        "event_id",
        "research_authority_events_event_id_issuance",
        "QUANTOS_RESEARCH_AUTHORITY_EVENT_ID_V1",
        ("authority_instance_id", "event_ordinal"),
        ("authority:g1", 1),
        "f69e0288782fcc3efa38847bbc2b112d524d285e6655eb789894d3069e84f492",
    ),
    _identity_issuance(
        "selection_scopes",
        "selection_scope_id",
        "selection_scopes_scope_id_issuance",
        "QUANTOS_SELECTION_SCOPE_ID_V1",
        ("authority_instance_id", "deployment_mandate_hash"),
        ("authority:g1", "5" * 64),
        "66df0045ca2e5a33f60ba275f4cdd74276a14c5a1d2994c321aedd21aea21f67",
    ),
    _identity_issuance(
        "research_programs",
        "research_program_id",
        "research_programs_program_id_issuance",
        "QUANTOS_RESEARCH_PROGRAM_ID_V1",
        (
            "authority_instance_id",
            "selection_scope_id",
            "research_question_id",
            "strategy_lineage_root_id",
        ),
        ("authority:g1", "6" * 64, "7" * 64, "8" * 64),
        "70c2ec931de72eea8c0196024037aba00c272e7e5c3440f0db864e139602fed6",
    ),
    _identity_issuance(
        "selection_decisions",
        "selection_decision_id",
        "selection_decisions_decision_and_family_id_issuance",
        "QUANTOS_SELECTION_DECISION_ID_V1",
        ("authority_instance_id", "selection_scope_id", "decision_ordinal"),
        ("authority:g1", "6" * 64, 1),
        "e68640179178a5c2ad0eff007990bfbe8b4eae9c0ea3d6262691c455be58b373",
    ),
    _identity_issuance(
        "selection_decisions",
        "family_id",
        "selection_decisions_decision_and_family_id_issuance",
        "QUANTOS_DATA_SNOOPING_FAMILY_ID_V1",
        (
            "authority_instance_id",
            "selection_scope_id",
            "decision_ordinal",
            "decision_class",
        ),
        ("authority:g1", "6" * 64, 1, "PUBLISHABLE"),
        "7d10f4932d2c72203ed5a86357df9e7e185d8f10ff0ca359a2f58875826db6db",
    ),
    _identity_issuance(
        "global_oos_exposures",
        "oos_exposure_id",
        "global_oos_exposures_global_exposure_id",
        "QUANTOS_GLOBAL_OOS_EXPOSURE_ID_V1",
        (
            "authority_instance_id",
            "selection_scope_id",
            "selection_decision_id",
            "family_id",
            "exposure_ordinal",
            "exposure_fingerprint",
        ),
        (
            "authority:g1",
            "6" * 64,
            "9" * 64,
            "a" * 64,
            1,
            "b" * 64,
        ),
        "4735da442cdc53da4ddec25ce3a8ba56010e64e7569748e8b49783267570c775",
    ),
)


def _content_hash(
    table_name: str,
    source_field: str,
    hash_field: str,
    source_kind: CanonicalContentSourceKind,
    verification_rule_id: str,
    *,
    object_field_name: str | None = None,
    golden_vector: CanonicalContentHashGoldenVectorSpec | None = None,
) -> CanonicalContentHashSpec:
    return CanonicalContentHashSpec(
        table_name=table_name,
        source_field=source_field,
        hash_field=hash_field,
        source_kind=source_kind,
        verification_rule_id=verification_rule_id,
        object_field_name=object_field_name,
        golden_vector=golden_vector,
    )


C4_CANONICAL_CONTENT_HASHES = (
    _content_hash(
        "research_questions",
        "question_payload_json",
        "question_hash",
        CanonicalContentSourceKind.CANONICAL_JSON_DOCUMENT,
        "research_questions_canonical_question_hash",
    ),
    _content_hash(
        "research_question_aliases",
        "alias",
        "alias_hash",
        CanonicalContentSourceKind.CANONICAL_JSON_SINGLE_FIELD_OBJECT,
        "research_question_aliases_canonical_alias_hash",
        object_field_name="alias",
        golden_vector=CanonicalContentHashGoldenVectorSpec(
            source_value="golden-alias",
            expected_sha256=(
                "348dc09b60d9a07d59a94cec80e1ab18e2d8651490cb90577aed14b62a07a668"
            ),
        ),
    ),
    _content_hash(
        "strategy_lineage_roots",
        "source_manifest_payload_json",
        "source_manifest_hash",
        CanonicalContentSourceKind.CANONICAL_JSON_DOCUMENT,
        "strategy_lineage_roots_canonical_source_manifest_hash",
    ),
    _content_hash(
        "strategy_lineage_nodes",
        "node_manifest_payload_json",
        "node_manifest_hash",
        CanonicalContentSourceKind.CANONICAL_JSON_DOCUMENT,
        "strategy_lineage_nodes_node_manifest_hash",
    ),
    _content_hash(
        "selection_scopes",
        "deployment_mandate_payload_json",
        "deployment_mandate_hash",
        CanonicalContentSourceKind.CANONICAL_JSON_DOCUMENT,
        "selection_scopes_canonical_mandate_hash",
    ),
    _content_hash(
        "selection_scope_aliases",
        "alias",
        "alias_hash",
        CanonicalContentSourceKind.CANONICAL_JSON_SINGLE_FIELD_OBJECT,
        "selection_scope_aliases_canonical_alias_hash",
        object_field_name="alias",
        golden_vector=CanonicalContentHashGoldenVectorSpec(
            source_value="golden-alias",
            expected_sha256=(
                "348dc09b60d9a07d59a94cec80e1ab18e2d8651490cb90577aed14b62a07a668"
            ),
        ),
    ),
)


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


_TABLE_ORDER = (
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
)


_RECORD_TYPES = {
    "research_questions": "RESEARCH_QUESTION_V1",
    "research_question_aliases": "RESEARCH_QUESTION_ALIAS_V1",
    "strategy_lineage_roots": "STRATEGY_LINEAGE_ROOT_V1",
    "strategy_lineage_nodes": "STRATEGY_LINEAGE_NODE_V1",
    "strategy_config_lineage_bindings": "STRATEGY_CONFIG_LINEAGE_BINDING_V1",
    "research_authority_events": "RESEARCH_AUTHORITY_EVENT_V1",
    "selection_scopes": "SELECTION_SCOPE_V1",
    "selection_scope_aliases": "SELECTION_SCOPE_ALIAS_V1",
    "selection_scope_publishable_decisions": ("SELECTION_SCOPE_PUBLISHABLE_DECISION_V1"),
    "selection_scope_outcomes": "SELECTION_SCOPE_OUTCOME_V1",
    "research_programs": "RESEARCH_PROGRAM_V1",
    "selection_decisions": "SELECTION_DECISION_V1",
    "global_oos_exposures": "GLOBAL_OOS_EXPOSURE_V1",
    "global_oos_exposure_consumptions": "GLOBAL_OOS_EXPOSURE_CONSUMPTION_V1",
}


_TABLE_COLUMNS = {
    "research_questions": _columns(
        _column("research_question_id"),
        _column("question_hash"),
        _column("question_payload_json"),
        _column("authority_event_id"),
        _column("issued_at_us", SqlType.INTEGER),
    ),
    "research_question_aliases": _columns(
        _column("alias_hash"),
        _column("alias"),
        _column("research_question_id"),
        _column("authority_event_id"),
        _column("registered_at_us", SqlType.INTEGER),
    ),
    "strategy_lineage_roots": _columns(
        _column("strategy_lineage_root_id"),
        _column("source_manifest_hash"),
        _column("source_manifest_payload_json"),
        _column("authority_event_id"),
        _column("registered_at_us", SqlType.INTEGER),
    ),
    "strategy_lineage_nodes": _columns(
        _column("strategy_lineage_node_id"),
        _column("strategy_lineage_root_id"),
        _column("parent_strategy_lineage_node_id", nullable=True),
        _column("node_manifest_hash"),
        _column("node_manifest_payload_json"),
        _column("authority_event_id"),
        _column("registered_at_us", SqlType.INTEGER),
    ),
    "strategy_config_lineage_bindings": _columns(
        _column("config_hash"),
        _column("strategy_lineage_root_id"),
        _column("strategy_lineage_node_id"),
        _column("authority_event_id"),
        _column("bound_at_us", SqlType.INTEGER),
    ),
    "research_authority_events": _columns(
        _column("event_id"),
        _column("event_ordinal", SqlType.INTEGER),
        _column("action"),
        _column("subject_record_type"),
        _column("subject_id"),
        _column("subject_record_hash"),
        _column("grant_id", nullable=True),
        _column("grant_record_hash", nullable=True),
        _column("grant_policy_id", nullable=True),
        _column("grant_policy_version", nullable=True),
        _column("grant_policy_hash", nullable=True),
        _column("grant_authority_id", nullable=True),
        _column("grant_authority_head", nullable=True),
        _column("audit_actor_id"),
        _column("audit_actor_kind"),
        _column("audit_actor_evidence_hash"),
        _column("audit_actor_policy_id"),
        _column("audit_actor_policy_version"),
        _column("audit_actor_policy_hash"),
        _column("audit_actor_authority_id"),
        _column("audit_actor_authority_head"),
        _column("event_at_us", SqlType.INTEGER),
        _column("prev_record_hash"),
    ),
    "selection_scopes": _columns(
        _column("selection_scope_id"),
        _column("deployment_mandate_hash"),
        _column("deployment_mandate_payload_json"),
        _column("authority_event_id"),
        _column("issued_at_us", SqlType.INTEGER),
    ),
    "selection_scope_aliases": _columns(
        _column("alias_hash"),
        _column("alias"),
        _column("selection_scope_id"),
        _column("authority_event_id"),
        _column("registered_at_us", SqlType.INTEGER),
    ),
    "selection_scope_publishable_decisions": _columns(
        _column("selection_scope_id"),
        _column("selection_decision_id"),
        _column("authority_event_id"),
        _column("reserved_at_us", SqlType.INTEGER),
    ),
    "selection_scope_outcomes": _columns(
        _column("selection_scope_id"),
        _column("outcome"),
        _column("selection_decision_id", nullable=True),
        _column("family_id", nullable=True),
        _column("winner_alpha_id", nullable=True),
        _column("evidence_ref_hash", nullable=True),
        _column("authority_event_id"),
        _column("recorded_at_us", SqlType.INTEGER),
    ),
    "research_programs": _columns(
        _column("research_program_id"),
        _column("selection_scope_id"),
        _column("research_question_id"),
        _column("question_hash"),
        _column("strategy_lineage_root_id"),
        _column("source_manifest_hash"),
        _column("authority_event_id"),
        _column("issued_at_us", SqlType.INTEGER),
    ),
    "selection_decisions": _columns(
        _column("selection_decision_id"),
        _column("selection_scope_id"),
        _column("decision_ordinal", SqlType.INTEGER),
        _column("family_id"),
        _column("decision_class"),
        _column("authority_event_id"),
        _column("issued_at_us", SqlType.INTEGER),
        _column("prev_record_hash"),
    ),
    "global_oos_exposures": _columns(
        _column("oos_exposure_id"),
        _column("selection_scope_id"),
        _column("selection_decision_id"),
        _column("family_id"),
        _column("exposure_ordinal", SqlType.INTEGER),
        _column("exposure_fingerprint"),
        _column("period_start_us", SqlType.INTEGER),
        _column("period_end_us", SqlType.INTEGER),
        _column("authority_event_id"),
        _column("issued_at_us", SqlType.INTEGER),
        _column("prev_record_hash"),
    ),
    "global_oos_exposure_consumptions": _columns(
        _column("oos_exposure_id"),
        _column("selection_decision_id"),
        _column("family_id"),
        _column("access_attempt_id"),
        _column("expected_exposure_version", SqlType.INTEGER),
        _column("authority_event_id"),
        _column("consumed_at_us", SqlType.INTEGER),
    ),
}


_PRIMARY_KEYS = {
    "research_questions": ("research_question_id",),
    "research_question_aliases": ("alias_hash",),
    "strategy_lineage_roots": ("strategy_lineage_root_id",),
    "strategy_lineage_nodes": ("strategy_lineage_node_id",),
    "strategy_config_lineage_bindings": ("config_hash",),
    "research_authority_events": ("event_id",),
    "selection_scopes": ("selection_scope_id",),
    "selection_scope_aliases": ("alias_hash",),
    "selection_scope_publishable_decisions": ("selection_scope_id",),
    "selection_scope_outcomes": ("selection_scope_id",),
    "research_programs": ("research_program_id",),
    "selection_decisions": ("selection_decision_id",),
    "global_oos_exposures": ("oos_exposure_id",),
    "global_oos_exposure_consumptions": ("oos_exposure_id",),
}


def _fk(
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


def _base_foreign_keys(
    table_name: str,
    *,
    include_authority_event: bool = True,
) -> tuple[ForeignKeySpec, ...]:
    foreign_keys = (
        _fk(
            ("authority_instance_id", "genesis_record_hash"),
            "family_authority_genesis",
            ("authority_instance_id", "record_hash"),
        ),
    )
    if include_authority_event:
        foreign_keys += (
            _fk(
                ("authority_event_id",),
                "research_authority_events",
                ("event_id",),
            ),
        )
    return foreign_keys


_FOREIGN_KEYS = {
    table_name: _base_foreign_keys(
        table_name,
        include_authority_event=table_name != "research_authority_events",
    )
    for table_name in _TABLE_ORDER
}
_FOREIGN_KEYS.update(
    {
        "research_question_aliases": (
            *_base_foreign_keys("research_question_aliases"),
            _fk(
                ("research_question_id",),
                "research_questions",
                ("research_question_id",),
            ),
        ),
        "strategy_lineage_nodes": (
            *_base_foreign_keys("strategy_lineage_nodes"),
            _fk(
                ("strategy_lineage_root_id",),
                "strategy_lineage_roots",
                ("strategy_lineage_root_id",),
            ),
            _fk(
                ("parent_strategy_lineage_node_id", "strategy_lineage_root_id"),
                "strategy_lineage_nodes",
                ("strategy_lineage_node_id", "strategy_lineage_root_id"),
            ),
        ),
        "strategy_config_lineage_bindings": (
            *_base_foreign_keys("strategy_config_lineage_bindings"),
            _fk(
                ("strategy_lineage_root_id",),
                "strategy_lineage_roots",
                ("strategy_lineage_root_id",),
            ),
            _fk(
                ("strategy_lineage_node_id", "strategy_lineage_root_id"),
                "strategy_lineage_nodes",
                ("strategy_lineage_node_id", "strategy_lineage_root_id"),
            ),
        ),
        "selection_scope_aliases": (
            *_base_foreign_keys("selection_scope_aliases"),
            _fk(
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
        ),
        "selection_scope_publishable_decisions": (
            *_base_foreign_keys("selection_scope_publishable_decisions"),
            _fk(
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
            _fk(
                ("selection_scope_id", "selection_decision_id"),
                "selection_decisions",
                ("selection_scope_id", "selection_decision_id"),
            ),
        ),
        "selection_scope_outcomes": (
            *_base_foreign_keys("selection_scope_outcomes"),
            _fk(
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
            _fk(
                ("selection_scope_id", "selection_decision_id", "family_id"),
                "selection_decisions",
                ("selection_scope_id", "selection_decision_id", "family_id"),
            ),
        ),
        "research_programs": (
            *_base_foreign_keys("research_programs"),
            _fk(
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
            _fk(
                ("research_question_id", "question_hash"),
                "research_questions",
                ("research_question_id", "question_hash"),
            ),
            _fk(
                ("strategy_lineage_root_id", "source_manifest_hash"),
                "strategy_lineage_roots",
                ("strategy_lineage_root_id", "source_manifest_hash"),
            ),
        ),
        "selection_decisions": (
            *_base_foreign_keys("selection_decisions"),
            _fk(
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
        ),
        "global_oos_exposures": (
            *_base_foreign_keys("global_oos_exposures"),
            _fk(
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
            _fk(
                ("selection_scope_id", "selection_decision_id", "family_id"),
                "selection_decisions",
                ("selection_scope_id", "selection_decision_id", "family_id"),
            ),
        ),
        "global_oos_exposure_consumptions": (
            *_base_foreign_keys("global_oos_exposure_consumptions"),
            _fk(
                ("oos_exposure_id", "selection_decision_id", "family_id"),
                "global_oos_exposures",
                ("oos_exposure_id", "selection_decision_id", "family_id"),
            ),
        ),
    }
)


def _index(
    table_name: str,
    suffix: str,
    columns: tuple[str, ...],
    *,
    unique: bool = False,
    partial_literal: tuple[str, str] | None = None,
) -> IndexSpec:
    predicate = None
    prefix = "unique" if unique else "ordinary"
    if partial_literal is not None:
        prefix = "partial_unique"
        column, value = partial_literal
        predicate = ComparisonExpression(
            ColumnValue(column),
            ComparisonOperator.EQ,
            LiteralValue(value),
        )
    return IndexSpec(
        name=f"{prefix}_{table_name}_{suffix}",
        columns=tuple(IndexColumnSpec(column) for column in columns),
        unique=unique or partial_literal is not None,
        predicate=predicate,
    )


_INDEXES = {
    "research_questions": (
        _index("research_questions", "question_hash", ("question_hash",), unique=True),
        _index(
            "research_questions",
            "id_hash",
            ("research_question_id", "question_hash"),
            unique=True,
        ),
        _index(
            "research_questions",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("research_questions", "record_hash", ("record_hash",), unique=True),
    ),
    "research_question_aliases": (
        _index("research_question_aliases", "alias", ("alias",), unique=True),
        _index(
            "research_question_aliases",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index(
            "research_question_aliases",
            "record_hash",
            ("record_hash",),
            unique=True,
        ),
        _index("research_question_aliases", "question", ("research_question_id",)),
    ),
    "strategy_lineage_roots": (
        _index(
            "strategy_lineage_roots",
            "manifest",
            ("source_manifest_hash",),
            unique=True,
        ),
        _index(
            "strategy_lineage_roots",
            "id_manifest",
            ("strategy_lineage_root_id", "source_manifest_hash"),
            unique=True,
        ),
        _index(
            "strategy_lineage_roots",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("strategy_lineage_roots", "record_hash", ("record_hash",), unique=True),
    ),
    "strategy_lineage_nodes": (
        _index(
            "strategy_lineage_nodes",
            "root_manifest",
            ("strategy_lineage_root_id", "node_manifest_hash"),
            unique=True,
        ),
        _index(
            "strategy_lineage_nodes",
            "id_root",
            ("strategy_lineage_node_id", "strategy_lineage_root_id"),
            unique=True,
        ),
        _index(
            "strategy_lineage_nodes",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("strategy_lineage_nodes", "record_hash", ("record_hash",), unique=True),
        _index(
            "strategy_lineage_nodes",
            "parent",
            ("parent_strategy_lineage_node_id",),
        ),
    ),
    "strategy_config_lineage_bindings": (
        _index(
            "strategy_config_lineage_bindings",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index(
            "strategy_config_lineage_bindings",
            "record_hash",
            ("record_hash",),
            unique=True,
        ),
        _index(
            "strategy_config_lineage_bindings",
            "node_root",
            ("strategy_lineage_node_id", "strategy_lineage_root_id"),
        ),
    ),
    "research_authority_events": (
        _index(
            "research_authority_events",
            "authority_ordinal",
            ("authority_instance_id", "event_ordinal"),
            unique=True,
        ),
        _index(
            "research_authority_events",
            "subject_action",
            ("action", "subject_record_type", "subject_id", "subject_record_hash"),
            unique=True,
        ),
        _index(
            "research_authority_events",
            "grant",
            ("grant_id",),
            unique=True,
        ),
        _index(
            "research_authority_events",
            "record_hash",
            ("record_hash",),
            unique=True,
        ),
        _index(
            "research_authority_events",
            "subject",
            ("subject_record_type", "subject_id"),
        ),
    ),
    "selection_scopes": (
        _index(
            "selection_scopes",
            "mandate",
            ("deployment_mandate_hash",),
            unique=True,
        ),
        _index(
            "selection_scopes",
            "id_mandate",
            ("selection_scope_id", "deployment_mandate_hash"),
            unique=True,
        ),
        _index(
            "selection_scopes",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("selection_scopes", "record_hash", ("record_hash",), unique=True),
    ),
    "selection_scope_aliases": (
        _index("selection_scope_aliases", "alias", ("alias",), unique=True),
        _index(
            "selection_scope_aliases",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("selection_scope_aliases", "record_hash", ("record_hash",), unique=True),
        _index("selection_scope_aliases", "scope", ("selection_scope_id",)),
    ),
    "selection_scope_publishable_decisions": (
        _index(
            "selection_scope_publishable_decisions",
            "decision",
            ("selection_decision_id",),
            unique=True,
        ),
        _index(
            "selection_scope_publishable_decisions",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index(
            "selection_scope_publishable_decisions",
            "record_hash",
            ("record_hash",),
            unique=True,
        ),
    ),
    "selection_scope_outcomes": (
        _index(
            "selection_scope_outcomes",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("selection_scope_outcomes", "record_hash", ("record_hash",), unique=True),
        _index(
            "selection_scope_outcomes",
            "winner_evidence",
            ("evidence_ref_hash",),
            partial_literal=("outcome", "WINNER_PINNED"),
        ),
        _index(
            "selection_scope_outcomes",
            "decision_family",
            ("selection_decision_id", "family_id"),
        ),
    ),
    "research_programs": (
        _index(
            "research_programs",
            "scope_question_root",
            (
                "selection_scope_id",
                "research_question_id",
                "strategy_lineage_root_id",
            ),
            unique=True,
        ),
        _index(
            "research_programs",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("research_programs", "record_hash", ("record_hash",), unique=True),
        _index(
            "research_programs",
            "question_provenance",
            ("research_question_id", "question_hash"),
        ),
        _index(
            "research_programs",
            "root_provenance",
            ("strategy_lineage_root_id", "source_manifest_hash"),
        ),
    ),
    "selection_decisions": (
        _index(
            "selection_decisions",
            "scope_ordinal",
            ("selection_scope_id", "decision_ordinal"),
            unique=True,
        ),
        _index(
            "selection_decisions",
            "scope_id",
            ("selection_scope_id", "selection_decision_id"),
            unique=True,
        ),
        _index(
            "selection_decisions",
            "scope_id_family",
            ("selection_scope_id", "selection_decision_id", "family_id"),
            unique=True,
        ),
        _index("selection_decisions", "family", ("family_id",), unique=True),
        _index(
            "selection_decisions",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("selection_decisions", "record_hash", ("record_hash",), unique=True),
    ),
    "global_oos_exposures": (
        _index(
            "global_oos_exposures",
            "scope_ordinal",
            ("selection_scope_id", "exposure_ordinal"),
            unique=True,
        ),
        _index(
            "global_oos_exposures",
            "fingerprint",
            ("exposure_fingerprint",),
            unique=True,
        ),
        _index(
            "global_oos_exposures",
            "decision",
            ("selection_decision_id",),
            unique=True,
        ),
        _index("global_oos_exposures", "family", ("family_id",), unique=True),
        _index(
            "global_oos_exposures",
            "exact_tuple",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            unique=True,
        ),
        _index(
            "global_oos_exposures",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index("global_oos_exposures", "record_hash", ("record_hash",), unique=True),
        _index(
            "global_oos_exposures",
            "scope_interval",
            ("selection_scope_id", "period_start_us", "period_end_us"),
        ),
    ),
    "global_oos_exposure_consumptions": (
        _index(
            "global_oos_exposure_consumptions",
            "access_attempt",
            ("access_attempt_id",),
            unique=True,
        ),
        _index(
            "global_oos_exposure_consumptions",
            "authority_event",
            ("authority_event_id",),
            unique=True,
        ),
        _index(
            "global_oos_exposure_consumptions",
            "record_hash",
            ("record_hash",),
            unique=True,
        ),
        _index(
            "global_oos_exposure_consumptions",
            "decision_family",
            ("selection_decision_id", "family_id"),
        ),
    ),
}


def _comparison(
    column: str,
    operator: ComparisonOperator,
    literal: None | int | float | str | bytes,
) -> ComparisonExpression:
    return ComparisonExpression(
        ColumnValue(column),
        operator,
        LiteralValue(literal),
    )


def _record_type_check(table_name: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{table_name}_record_type",
        _comparison(
            "record_type",
            ComparisonOperator.EQ,
            _RECORD_TYPES[table_name],
        ),
    )


def _positive_check(table_name: str, column: str, suffix: str) -> CheckConstraintSpec:
    return CheckConstraintSpec(
        f"{table_name}_{suffix}",
        _comparison(column, ComparisonOperator.GT, 0),
    )


def _time_checks(table_name: str, column: str) -> tuple[CheckConstraintSpec, ...]:
    return (
        _record_type_check(table_name),
        _positive_check(table_name, column, "positive_time"),
    )


_CHECKS = {
    "research_questions": _time_checks("research_questions", "issued_at_us"),
    "research_question_aliases": _time_checks("research_question_aliases", "registered_at_us"),
    "strategy_lineage_roots": _time_checks("strategy_lineage_roots", "registered_at_us"),
    "strategy_lineage_nodes": (
        *_time_checks("strategy_lineage_nodes", "registered_at_us"),
        CheckConstraintSpec(
            "strategy_lineage_nodes_parent_not_self",
            LogicalExpression(
                LogicalOperator.OR,
                (
                    NullExpression(
                        ColumnValue("parent_strategy_lineage_node_id"),
                        NullOperator.IS_NULL,
                    ),
                    ComparisonExpression(
                        ColumnValue("strategy_lineage_node_id"),
                        ComparisonOperator.NE,
                        ColumnValue("parent_strategy_lineage_node_id"),
                    ),
                ),
            ),
        ),
    ),
    "strategy_config_lineage_bindings": _time_checks("strategy_config_lineage_bindings", "bound_at_us"),
    "research_authority_events": (
        *_time_checks("research_authority_events", "event_at_us"),
        _positive_check("research_authority_events", "event_ordinal", "positive_ordinal"),
        CheckConstraintSpec(
            "research_authority_events_action_domain",
            MembershipExpression(
                ColumnValue("action"),
                tuple(LiteralValue(policy.action) for policy in C4_ACTION_POLICIES),
            ),
        ),
        CheckConstraintSpec(
            "research_authority_events_actor_kind_domain",
            MembershipExpression(
                ColumnValue("audit_actor_kind"),
                tuple(LiteralValue(actor_kind) for actor_kind in ("HUMAN", "AGENT", "SYSTEM")),
            ),
        ),
        CheckConstraintSpec(
            "research_authority_events_grant_reference_tuple",
            LogicalExpression(
                LogicalOperator.OR,
                (
                    LogicalExpression(
                        LogicalOperator.AND,
                        tuple(
                            NullExpression(ColumnValue(column), NullOperator.IS_NULL)
                            for column in _GRANT_REFERENCE_COLUMNS
                        ),
                    ),
                    LogicalExpression(
                        LogicalOperator.AND,
                        tuple(
                            NullExpression(ColumnValue(column), NullOperator.IS_NOT_NULL)
                            for column in _GRANT_REFERENCE_COLUMNS
                        ),
                    ),
                ),
            ),
        ),
    ),
    "selection_scopes": _time_checks("selection_scopes", "issued_at_us"),
    "selection_scope_aliases": _time_checks("selection_scope_aliases", "registered_at_us"),
    "selection_scope_publishable_decisions": _time_checks("selection_scope_publishable_decisions", "reserved_at_us"),
    "selection_scope_outcomes": (
        *_time_checks("selection_scope_outcomes", "recorded_at_us"),
        CheckConstraintSpec(
            "selection_scope_outcomes_domain",
            MembershipExpression(
                ColumnValue("outcome"),
                (
                    LiteralValue("WINNER_PINNED"),
                    LiteralValue("CLOSED_WITHOUT_PROMOTION"),
                ),
            ),
        ),
        CheckConstraintSpec(
            "selection_scope_outcomes_branch",
            LogicalExpression(
                LogicalOperator.OR,
                (
                    LogicalExpression(
                        LogicalOperator.AND,
                        (
                            _comparison(
                                "outcome",
                                ComparisonOperator.EQ,
                                "WINNER_PINNED",
                            ),
                            NullExpression(
                                ColumnValue("selection_decision_id"),
                                NullOperator.IS_NOT_NULL,
                            ),
                            NullExpression(ColumnValue("family_id"), NullOperator.IS_NOT_NULL),
                            NullExpression(
                                ColumnValue("winner_alpha_id"),
                                NullOperator.IS_NOT_NULL,
                            ),
                            NullExpression(
                                ColumnValue("evidence_ref_hash"),
                                NullOperator.IS_NOT_NULL,
                            ),
                        ),
                    ),
                    LogicalExpression(
                        LogicalOperator.AND,
                        (
                            _comparison(
                                "outcome",
                                ComparisonOperator.EQ,
                                "CLOSED_WITHOUT_PROMOTION",
                            ),
                            NullExpression(
                                ColumnValue("selection_decision_id"),
                                NullOperator.IS_NULL,
                            ),
                            NullExpression(ColumnValue("family_id"), NullOperator.IS_NULL),
                            NullExpression(ColumnValue("winner_alpha_id"), NullOperator.IS_NULL),
                            NullExpression(
                                ColumnValue("evidence_ref_hash"),
                                NullOperator.IS_NULL,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
    "research_programs": _time_checks("research_programs", "issued_at_us"),
    "selection_decisions": (
        *_time_checks("selection_decisions", "issued_at_us"),
        _positive_check("selection_decisions", "decision_ordinal", "positive_ordinal"),
        CheckConstraintSpec(
            "selection_decisions_class_domain",
            MembershipExpression(
                ColumnValue("decision_class"),
                (
                    LiteralValue("PUBLISHABLE"),
                    LiteralValue("DIAGNOSTIC_ONLY"),
                ),
            ),
        ),
    ),
    "global_oos_exposures": (
        *_time_checks("global_oos_exposures", "issued_at_us"),
        _positive_check("global_oos_exposures", "exposure_ordinal", "positive_ordinal"),
        CheckConstraintSpec(
            "global_oos_exposures_strict_interval",
            LogicalExpression(
                LogicalOperator.AND,
                (
                    _comparison("period_start_us", ComparisonOperator.GT, 0),
                    ComparisonExpression(
                        ColumnValue("period_end_us"),
                        ComparisonOperator.GT,
                        ColumnValue("period_start_us"),
                    ),
                ),
            ),
        ),
    ),
    "global_oos_exposure_consumptions": (
        *_time_checks("global_oos_exposure_consumptions", "consumed_at_us"),
        _positive_check(
            "global_oos_exposure_consumptions",
            "expected_exposure_version",
            "positive_expected_version",
        ),
    ),
}


_CHAINS = {
    "research_authority_events": ChainSpec(
        partition_key_columns=("authority_instance_id",),
        ordinal_column="event_ordinal",
        genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
        previous_hash_column="prev_record_hash",
        record_hash_column="record_hash",
        terminal_head_rule=ChainHeadRule.LATEST_RECORD_HASH,
    ),
    "selection_decisions": ChainSpec(
        partition_key_columns=("authority_instance_id", "selection_scope_id"),
        ordinal_column="decision_ordinal",
        genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
        previous_hash_column="prev_record_hash",
        record_hash_column="record_hash",
        terminal_head_rule=ChainHeadRule.LATEST_RECORD_HASH,
    ),
    "global_oos_exposures": ChainSpec(
        partition_key_columns=("authority_instance_id", "selection_scope_id"),
        ordinal_column="exposure_ordinal",
        genesis_rule=ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
        previous_hash_column="prev_record_hash",
        record_hash_column="record_hash",
        terminal_head_rule=ChainHeadRule.LATEST_RECORD_HASH,
    ),
}


_GUARD_CODES = {
    "research_questions": (
        "AUTHORITY_EVENT_MATCH",
        "CANONICAL_PAYLOAD_HASH_MATCH",
    ),
    "research_question_aliases": ("AUTHORITY_EVENT_MATCH",),
    "strategy_lineage_roots": (
        "AUTHORITY_EVENT_MATCH",
        "CANONICAL_PAYLOAD_HASH_MATCH",
    ),
    "strategy_lineage_nodes": (
        "AUTHORITY_EVENT_MATCH",
        "CANONICAL_PAYLOAD_HASH_MATCH",
    ),
    "strategy_config_lineage_bindings": ("AUTHORITY_EVENT_MATCH",),
    "research_authority_events": (
        "CHAIN_APPEND_NEXT",
        "CLOSED_ACTION_SUBJECT_MATCH",
        "GRANT_PRESENCE_POLICY_MATCH",
    ),
    "selection_scopes": (
        "AUTHORITY_EVENT_MATCH",
        "CANONICAL_PAYLOAD_HASH_MATCH",
    ),
    "selection_scope_aliases": ("AUTHORITY_EVENT_MATCH",),
    "selection_scope_publishable_decisions": (
        "AUTHORITY_EVENT_MATCH",
        "PUBLISHABLE_DECISION_ONLY",
        "SCOPE_OUTCOME_ABSENT",
    ),
    "selection_scope_outcomes": (
        "AUTHORITY_EVENT_MATCH",
        "WINNER_MATCHES_PUBLISHABLE_SLOT",
    ),
    "research_programs": ("AUTHORITY_EVENT_MATCH",),
    "selection_decisions": (
        "AUTHORITY_EVENT_MATCH",
        "CHAIN_APPEND_NEXT",
    ),
    "global_oos_exposures": (
        "AUTHORITY_EVENT_MATCH",
        "CHAIN_APPEND_NEXT",
        "NO_OVERLAPPING_SCOPE_INTERVAL",
    ),
    "global_oos_exposure_consumptions": (
        "AUTHORITY_EVENT_MATCH",
        "EXPECTED_EXPOSURE_VERSION_MATCH",
        "DIAGNOSTIC_REQUIRES_SCOPE_OUTCOME",
    ),
}


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


_SUBJECT_ID_COLUMNS = {
    "research_questions": "research_question_id",
    "research_question_aliases": "alias_hash",
    "strategy_lineage_roots": "strategy_lineage_root_id",
    "strategy_lineage_nodes": "strategy_lineage_node_id",
    "strategy_config_lineage_bindings": "config_hash",
    "selection_scopes": "selection_scope_id",
    "selection_scope_aliases": "alias_hash",
    "selection_scope_publishable_decisions": "selection_scope_id",
    "selection_scope_outcomes": "selection_scope_id",
    "research_programs": "research_program_id",
    "selection_decisions": "selection_decision_id",
    "global_oos_exposures": "oos_exposure_id",
    "global_oos_exposure_consumptions": "oos_exposure_id",
}


_PAYLOAD_HASH_COLUMNS = {
    "research_questions": ("question_payload_json", "question_hash"),
    "strategy_lineage_roots": (
        "source_manifest_payload_json",
        "source_manifest_hash",
    ),
    "strategy_lineage_nodes": ("node_manifest_payload_json", "node_manifest_hash"),
    "selection_scopes": (
        "deployment_mandate_payload_json",
        "deployment_mandate_hash",
    ),
}


def _binding(related_column: str, value: str | LiteralValue) -> LookupBinding:
    expression = value if type(value) is LiteralValue else ColumnValue(value, RowSource.NEW)
    return LookupBinding(related_column=related_column, value=expression)


def _authority_event_match(table_name: str) -> PredicateExpression:
    action = next(
        policy.action
        for policy in C4_ACTION_POLICIES
        if policy.subject_table == table_name
    )
    return RelationExistsExpression(
        table="research_authority_events",
        bindings=(
            _binding("event_id", "authority_event_id"),
            _binding("authority_instance_id", "authority_instance_id"),
            _binding("genesis_record_hash", "genesis_record_hash"),
            _binding("action", LiteralValue(action)),
            _binding("subject_record_type", "record_type"),
            _binding("subject_id", _SUBJECT_ID_COLUMNS[table_name]),
            _binding("subject_record_hash", "record_hash"),
        ),
        negated=True,
    )


def _canonical_payload_hash(table_name: str) -> PredicateExpression:
    payload_column, hash_column = _PAYLOAD_HASH_COLUMNS[table_name]
    return CanonicalJsonPayloadHashExpression(
        document=ColumnValue(payload_column, RowSource.NEW),
        excluded_paths=(),
        encoding=CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
        algorithm=RowHashAlgorithm.SHA256,
        operator=ComparisonOperator.NE,
        expected_hash=ColumnValue(hash_column, RowSource.NEW),
    )


def _chain_append_guard(table_name: str) -> PredicateExpression:
    chain = _CHAINS[table_name]
    return ChainPredecessorExpression(
        table=table_name,
        partition_bindings=tuple(
            _binding(column, column) for column in chain.partition_key_columns
        ),
        ordinal_column=chain.ordinal_column,
        current_ordinal=ColumnValue(chain.ordinal_column, RowSource.NEW),
        record_hash_column=chain.record_hash_column,
        current_previous_hash=ColumnValue(chain.previous_hash_column, RowSource.NEW),
        genesis_hash=ColumnValue("genesis_record_hash", RowSource.NEW),
        negated=True,
    )


def _action_subject_guard() -> PredicateExpression:
    valid_branches = tuple(
        LogicalExpression(
            LogicalOperator.AND,
            (
                ComparisonExpression(
                    ColumnValue("action", RowSource.NEW),
                    ComparisonOperator.EQ,
                    LiteralValue(policy.action),
                ),
                ComparisonExpression(
                    ColumnValue("subject_record_type", RowSource.NEW),
                    ComparisonOperator.EQ,
                    LiteralValue(
                        _RECORD_TYPES.get(
                            policy.subject_table,
                            "PUBLISHED_ARTIFACT_ENVELOPE_V1",
                        )
                    ),
                ),
            ),
        )
        for policy in C4_ACTION_POLICIES
    )
    return NotExpression(LogicalExpression(LogicalOperator.OR, valid_branches))


def _grant_presence_guard() -> PredicateExpression:
    valid_branches = []
    for policy in C4_ACTION_POLICIES:
        null_operator = (
            NullOperator.IS_NOT_NULL if policy.grant_required else NullOperator.IS_NULL
        )
        valid_branches.append(
            LogicalExpression(
                LogicalOperator.AND,
                (
                    ComparisonExpression(
                        ColumnValue("action", RowSource.NEW),
                        ComparisonOperator.EQ,
                        LiteralValue(policy.action),
                    ),
                    *(
                        NullExpression(
                            ColumnValue(column, RowSource.NEW),
                            null_operator,
                        )
                        for column in _GRANT_REFERENCE_COLUMNS
                    ),
                ),
            )
        )
    return NotExpression(
        LogicalExpression(LogicalOperator.OR, tuple(valid_branches))
    )


def _publishable_decision_guard() -> PredicateExpression:
    return RelationExistsExpression(
        table="selection_decisions",
        bindings=(
            _binding("selection_scope_id", "selection_scope_id"),
            _binding("selection_decision_id", "selection_decision_id"),
        ),
        predicate=ComparisonExpression(
            ColumnValue("decision_class", RowSource.RELATED),
            ComparisonOperator.EQ,
            LiteralValue("PUBLISHABLE"),
        ),
        negated=True,
    )


def _scope_outcome_absent_guard() -> PredicateExpression:
    return RelationExistsExpression(
        table="selection_scope_outcomes",
        bindings=(_binding("selection_scope_id", "selection_scope_id"),),
    )


def _winner_matches_slot_guard() -> PredicateExpression:
    missing_slot = RelationExistsExpression(
        table="selection_scope_publishable_decisions",
        bindings=(
            _binding("selection_scope_id", "selection_scope_id"),
            _binding("selection_decision_id", "selection_decision_id"),
        ),
        negated=True,
    )
    return LogicalExpression(
        LogicalOperator.AND,
        (
            ComparisonExpression(
                ColumnValue("outcome", RowSource.NEW),
                ComparisonOperator.EQ,
                LiteralValue("WINNER_PINNED"),
            ),
            missing_slot,
        ),
    )


def _overlapping_scope_interval_guard() -> PredicateExpression:
    return RelationExistsExpression(
        table="global_oos_exposures",
        bindings=(_binding("selection_scope_id", "selection_scope_id"),),
        predicate=LogicalExpression(
            LogicalOperator.AND,
            (
                ComparisonExpression(
                    ColumnValue("period_start_us", RowSource.RELATED),
                    ComparisonOperator.LT,
                    ColumnValue("period_end_us", RowSource.NEW),
                ),
                ComparisonExpression(
                    ColumnValue("period_end_us", RowSource.RELATED),
                    ComparisonOperator.GT,
                    ColumnValue("period_start_us", RowSource.NEW),
                ),
            ),
        ),
    )


def _expected_exposure_version_guard() -> PredicateExpression:
    return RelationExistsExpression(
        table="global_oos_exposures",
        bindings=(
            _binding("oos_exposure_id", "oos_exposure_id"),
            _binding("selection_decision_id", "selection_decision_id"),
            _binding("family_id", "family_id"),
        ),
        predicate=ComparisonExpression(
            ColumnValue("exposure_version", RowSource.RELATED),
            ComparisonOperator.EQ,
            ColumnValue("expected_exposure_version", RowSource.NEW),
        ),
        negated=True,
    )


def _decision_join(decision_class: str) -> RelationJoinSpec:
    return RelationJoinSpec(
        table="selection_decisions",
        alias="decision",
        on=tuple(
            RelationJoinEqualitySpec(
                "exposure",
                column,
                "decision",
                column,
            )
            for column in (
                "selection_scope_id",
                "selection_decision_id",
                "family_id",
            )
        ),
        predicate=ComparisonExpression(
            QualifiedColumnValue("decision", "decision_class"),
            ComparisonOperator.EQ,
            LiteralValue(decision_class),
        ),
    )


def _diagnostic_scope_outcome_guard() -> PredicateExpression:
    anchor_bindings = (
        _binding("oos_exposure_id", "oos_exposure_id"),
        _binding("selection_decision_id", "selection_decision_id"),
        _binding("family_id", "family_id"),
    )
    publishable_path_missing = RelationJoinAggregateExpression(
        anchor_table="global_oos_exposures",
        anchor_alias="exposure",
        bindings=anchor_bindings,
        joins=(_decision_join("PUBLISHABLE"),),
        aggregate=RelationAggregateKind.COUNT_ROWS,
        source=RowSource.RELATED,
        value=None,
        order=(),
        operator=ComparisonOperator.NE,
        expected=LiteralValue(1),
    )
    diagnostic_path_missing = RelationJoinAggregateExpression(
        anchor_table="global_oos_exposures",
        anchor_alias="exposure",
        bindings=anchor_bindings,
        joins=(
            _decision_join("DIAGNOSTIC_ONLY"),
            RelationJoinSpec(
                table="selection_scope_outcomes",
                alias="outcome",
                on=(
                    RelationJoinEqualitySpec(
                        "decision",
                        "selection_scope_id",
                        "outcome",
                        "selection_scope_id",
                    ),
                ),
            ),
        ),
        aggregate=RelationAggregateKind.COUNT_ROWS,
        source=RowSource.RELATED,
        value=None,
        order=(),
        operator=ComparisonOperator.NE,
        expected=LiteralValue(1),
    )
    return LogicalExpression(
        LogicalOperator.AND,
        (publishable_path_missing, diagnostic_path_missing),
    )


def _guard_predicate(table_name: str, error_code: str) -> PredicateExpression:
    if error_code == "AUTHORITY_EVENT_MATCH":
        return _authority_event_match(table_name)
    if error_code == "CANONICAL_PAYLOAD_HASH_MATCH":
        return _canonical_payload_hash(table_name)
    if error_code == "CHAIN_APPEND_NEXT":
        return _chain_append_guard(table_name)
    if error_code == "CLOSED_ACTION_SUBJECT_MATCH":
        return _action_subject_guard()
    if error_code == "GRANT_PRESENCE_POLICY_MATCH":
        return _grant_presence_guard()
    if error_code == "PUBLISHABLE_DECISION_ONLY":
        return _publishable_decision_guard()
    if error_code == "SCOPE_OUTCOME_ABSENT":
        return _scope_outcome_absent_guard()
    if error_code == "WINNER_MATCHES_PUBLISHABLE_SLOT":
        return _winner_matches_slot_guard()
    if error_code == "NO_OVERLAPPING_SCOPE_INTERVAL":
        return _overlapping_scope_interval_guard()
    if error_code == "EXPECTED_EXPOSURE_VERSION_MATCH":
        return _expected_exposure_version_guard()
    if error_code == "DIAGNOSTIC_REQUIRES_SCOPE_OUTCOME":
        return _diagnostic_scope_outcome_guard()
    raise ValueError(f"unreviewed C4 insert guard {table_name}.{error_code}")


def _triggers(table_name: str) -> tuple[TriggerPolicySpec, ...]:
    return (
        *_immutable_triggers(table_name),
        *(
            TriggerPolicySpec(
                name=f"{table_name}_{error_code.lower()}",
                kind=TriggerPolicyKind.INSERT_GUARD,
                event=TriggerEvent.INSERT,
                error_code=error_code,
                violation_when=_guard_predicate(table_name, error_code),
            )
            for error_code in _GUARD_CODES[table_name]
        ),
    )


_VERIFIER_OBLIGATIONS = {
    "research_questions": (
        "CANONICAL_QUESTION_HASH",
        "AUTHORITY_EVENT_BINDING",
        "QUESTION_ID_ISSUANCE",
        "GRANT_REQUIRED",
    ),
    "research_question_aliases": (
        "CANONICAL_ALIAS_HASH",
        "GLOBAL_ALIAS_IMMUTABILITY",
        "QUESTION_REFERENCE",
        "AUTHORITY_EVENT_BINDING",
        "GRANT_REQUIRED",
    ),
    "strategy_lineage_roots": (
        "CANONICAL_SOURCE_MANIFEST_HASH",
        "SOURCE_MANIFEST_SINGLE_ROOT",
        "ROOT_ID_ISSUANCE",
        "AUTHORITY_EVENT_BINDING",
        "GRANT_REQUIRED",
    ),
    "strategy_lineage_nodes": (
        "PARENT_ROOT_INHERITANCE",
        "NODE_MANIFEST_HASH",
        "NODE_ID_ISSUANCE",
        "AUTHORITY_EVENT_BINDING",
        "GRANT_REQUIRED",
    ),
    "strategy_config_lineage_bindings": (
        "CONFIG_SINGLE_ROOT",
        "NODE_ROOT_MATCH",
        "AUTHORITY_EVENT_BINDING",
        "GRANT_REQUIRED",
    ),
    "research_authority_events": (
        "CLOSED_ACTION_ENUM",
        "ACTION_SUBJECT_MATCH",
        "GRANT_POLICY",
        "AUDIT_ACTOR_PROVENANCE",
        "COMPLETE_CHAIN",
        "EVENT_ID_ISSUANCE",
    ),
    "selection_scopes": (
        "CANONICAL_MANDATE_HASH",
        "MANDATE_SINGLE_SCOPE",
        "SCOPE_ID_ISSUANCE",
        "AUTHORITY_EVENT_BINDING",
        "GRANT_REQUIRED",
    ),
    "selection_scope_aliases": (
        "CANONICAL_ALIAS_HASH",
        "GLOBAL_ALIAS_IMMUTABILITY",
        "SCOPE_REFERENCE",
        "AUTHORITY_EVENT_BINDING",
        "GRANT_REQUIRED",
    ),
    "selection_scope_publishable_decisions": (
        "ONE_SLOT_PER_SCOPE",
        "ONE_SCOPE_PER_SLOT_DECISION",
        "PUBLISHABLE_DECISION_MATCH",
        "OUTCOME_NOT_ALREADY_TERMINAL",
        "AUTHORITY_EVENT_BINDING",
    ),
    "selection_scope_outcomes": (
        "ONE_TERMINAL_OUTCOME_PER_SCOPE",
        "OUTCOME_BRANCH",
        "WINNER_SLOT_DECISION_FAMILY_MATCH",
        "AUTHORITY_EVENT_BINDING",
    ),
    "research_programs": (
        "ONE_SCOPE_QUESTION_ROOT_TUPLE",
        "QUESTION_HASH_PROVENANCE",
        "ROOT_MANIFEST_PROVENANCE",
        "PROGRAM_ID_ISSUANCE",
        "AUTHORITY_EVENT_BINDING",
    ),
    "selection_decisions": (
        "SCOPE_MONOTONE_DECISION_CHAIN",
        "DECISION_CLASS_SLOT_POLICY",
        "UNIQUE_FAMILY_ID",
        "DECISION_AND_FAMILY_ID_ISSUANCE",
        "AUTHORITY_EVENT_BINDING",
    ),
    "global_oos_exposures": (
        "SCOPE_MONOTONE_EXPOSURE_CHAIN",
        "GLOBAL_EXPOSURE_ID",
        "GLOBAL_EXPOSURE_FINGERPRINT",
        "DECISION_SCOPE_FAMILY_MATCH",
        "NO_OVERLAPPING_SCOPE_INTERVAL",
        "REQUIRE_C6_EMBEDDED_REFS",
        "AUTHORITY_EVENT_BINDING",
    ),
    "global_oos_exposure_consumptions": (
        "ONE_SHOT_CONSUMPTION",
        "EXACT_EXPOSURE_DECISION_FAMILY_TUPLE",
        "EXPECTED_EXPOSURE_VERSION_MATCH",
        "UNIQUE_ACCESS_ATTEMPT",
        "DIAGNOSTIC_SCOPE_OUTCOME",
        "REQUIRE_C7_ATTEMPT_BINDING",
        "AUTHORITY_EVENT_BINDING",
    ),
}


def _specific_rule_kind(obligation: str) -> VerificationRuleKind:
    if "ATTEMPT_BINDING" in obligation:
        return VerificationRuleKind.ATTEMPT_BINDING
    if "CHAIN" in obligation:
        return VerificationRuleKind.ORDERED_CHAIN
    if "HASH" in obligation:
        return VerificationRuleKind.ROW_HASH
    if "EXPOSURE" in obligation or "CONSUMPTION" in obligation:
        return VerificationRuleKind.EXPOSURE_CONSUMPTION
    if "OUTCOME" in obligation or "POLICY" in obligation:
        return VerificationRuleKind.STATE_BRANCH
    return VerificationRuleKind.SELECTION_SCOPE


def _structural_source(
    kind: StructuralVerificationSourceKind,
    *,
    source_id: str | None = None,
    columns: tuple[str, ...] = (),
    referenced_table: str | None = None,
    referenced_columns: tuple[str, ...] = (),
    required_rule_ids: tuple[str, ...] = (),
    required_verifier_codes: tuple[str, ...] = (),
) -> StructuralVerificationSourceSpec:
    return StructuralVerificationSourceSpec(
        kind=kind,
        source_id=source_id,
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
        required_rule_ids=required_rule_ids,
        required_verifier_codes=required_verifier_codes,
    )


_SPECIFIC_STRUCTURAL_SOURCES = {
    "research_question_aliases_global_alias_immutability": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_research_question_aliases_alias",
    ),
    "research_question_aliases_question_reference": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("research_question_id",),
        referenced_table="research_questions",
        referenced_columns=("research_question_id",),
    ),
    "strategy_lineage_roots_source_manifest_single_root": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_strategy_lineage_roots_manifest",
    ),
    "strategy_lineage_nodes_parent_root_inheritance": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("parent_strategy_lineage_node_id", "strategy_lineage_root_id"),
        referenced_table="strategy_lineage_nodes",
        referenced_columns=("strategy_lineage_node_id", "strategy_lineage_root_id"),
    ),
    "strategy_config_lineage_bindings_config_single_root": _structural_source(
        StructuralVerificationSourceKind.PRIMARY_KEY,
    ),
    "strategy_config_lineage_bindings_node_root_match": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("strategy_lineage_node_id", "strategy_lineage_root_id"),
        referenced_table="strategy_lineage_nodes",
        referenced_columns=("strategy_lineage_node_id", "strategy_lineage_root_id"),
    ),
    "research_authority_events_closed_action_enum": _structural_source(
        StructuralVerificationSourceKind.CHECK_CONSTRAINT,
        source_id="research_authority_events_action_domain",
    ),
    "research_authority_events_complete_chain": _structural_source(
        StructuralVerificationSourceKind.CHAIN_SPEC,
    ),
    "selection_scopes_mandate_single_scope": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_selection_scopes_mandate",
    ),
    "selection_scope_aliases_global_alias_immutability": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_selection_scope_aliases_alias",
    ),
    "selection_scope_aliases_scope_reference": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("selection_scope_id",),
        referenced_table="selection_scopes",
        referenced_columns=("selection_scope_id",),
    ),
    "selection_scope_publishable_decisions_one_slot_per_scope": _structural_source(
        StructuralVerificationSourceKind.PRIMARY_KEY,
    ),
    "selection_scope_publishable_decisions_one_scope_per_slot_decision": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_selection_scope_publishable_decisions_decision",
    ),
    "selection_scope_outcomes_one_terminal_outcome_per_scope": _structural_source(
        StructuralVerificationSourceKind.PRIMARY_KEY,
    ),
    "selection_scope_outcomes_outcome_branch": _structural_source(
        StructuralVerificationSourceKind.CHECK_CONSTRAINT,
        source_id="selection_scope_outcomes_branch",
    ),
    "research_programs_one_scope_question_root_tuple": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_research_programs_scope_question_root",
    ),
    "research_programs_question_hash_provenance": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("research_question_id", "question_hash"),
        referenced_table="research_questions",
        referenced_columns=("research_question_id", "question_hash"),
    ),
    "research_programs_root_manifest_provenance": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("strategy_lineage_root_id", "source_manifest_hash"),
        referenced_table="strategy_lineage_roots",
        referenced_columns=("strategy_lineage_root_id", "source_manifest_hash"),
    ),
    "selection_decisions_scope_monotone_decision_chain": _structural_source(
        StructuralVerificationSourceKind.CHAIN_SPEC,
    ),
    "selection_decisions_unique_family_id": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_selection_decisions_family",
    ),
    "global_oos_exposures_scope_monotone_exposure_chain": _structural_source(
        StructuralVerificationSourceKind.CHAIN_SPEC,
    ),
    "global_oos_exposures_decision_scope_family_match": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("selection_scope_id", "selection_decision_id", "family_id"),
        referenced_table="selection_decisions",
        referenced_columns=("selection_scope_id", "selection_decision_id", "family_id"),
    ),
    "global_oos_exposure_consumptions_one_shot_consumption": _structural_source(
        StructuralVerificationSourceKind.PRIMARY_KEY,
    ),
    "global_oos_exposure_consumptions_exact_exposure_decision_family_tuple": _structural_source(
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        columns=("oos_exposure_id", "selection_decision_id", "family_id"),
        referenced_table="global_oos_exposures",
        referenced_columns=("oos_exposure_id", "selection_decision_id", "family_id"),
    ),
    "global_oos_exposure_consumptions_unique_access_attempt": _structural_source(
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        source_id="unique_global_oos_exposure_consumptions_access_attempt",
    ),
    "research_questions_canonical_question_hash": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="research_questions_canonical_payload_hash_match",
    ),
    "strategy_lineage_roots_canonical_source_manifest_hash": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="strategy_lineage_roots_canonical_payload_hash_match",
    ),
    "strategy_lineage_nodes_node_manifest_hash": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="strategy_lineage_nodes_canonical_payload_hash_match",
    ),
    "selection_scopes_canonical_mandate_hash": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="selection_scopes_canonical_payload_hash_match",
    ),
    "research_authority_events_action_subject_match": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="research_authority_events_closed_action_subject_match",
    ),
    "selection_scope_publishable_decisions_publishable_decision_match": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="selection_scope_publishable_decisions_publishable_decision_only",
    ),
    "selection_scope_publishable_decisions_outcome_not_already_terminal": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="selection_scope_publishable_decisions_scope_outcome_absent",
    ),
    "selection_scope_outcomes_winner_slot_decision_family_match": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="selection_scope_outcomes_winner_matches_publishable_slot",
    ),
    "global_oos_exposures_no_overlapping_scope_interval": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="global_oos_exposures_no_overlapping_scope_interval",
    ),
    "global_oos_exposure_consumptions_expected_exposure_version_match": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="global_oos_exposure_consumptions_expected_exposure_version_match",
    ),
    "global_oos_exposure_consumptions_diagnostic_scope_outcome": _structural_source(
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        source_id="global_oos_exposure_consumptions_diagnostic_requires_scope_outcome",
    ),
    **{
        f"{table_name}_authority_event_binding": _structural_source(
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id=f"{table_name}_authority_event_match",
        )
        for table_name in _SUBJECT_ID_COLUMNS
    },
    "global_oos_exposures_global_exposure_fingerprint": _structural_source(
        StructuralVerificationSourceKind.DELEGATED_EXTENSION,
        source_id="C6_GLOBAL_OOS_EXPOSURE_EMBEDDED_REFS_V1",
        required_verifier_codes=("VERIFY_EXPOSURE_FINGERPRINT",),
    ),
    "global_oos_exposures_require_c6_embedded_refs": _structural_source(
        StructuralVerificationSourceKind.DELEGATED_EXTENSION,
        source_id="C6_GLOBAL_OOS_EXPOSURE_EMBEDDED_REFS_V1",
        required_verifier_codes=(
            "VERIFY_GLOBAL_OOS_EXPOSURE_IDENTITY",
            "VERIFY_OBSERVED_PIT_EXPOSURE_BINDING",
            "VERIFY_EXPOSURE_FINGERPRINT",
        ),
    ),
    "global_oos_exposure_consumptions_require_c7_attempt_binding": _structural_source(
        StructuralVerificationSourceKind.DELEGATED_EXTENSION,
        source_id="C7_GLOBAL_OOS_EXPOSURE_CONSUMPTION_ATTEMPT_BINDING_V1",
        required_rule_ids=(
            "global_oos_consumption_started_attempt_exact_tuple",
            "global_oos_consumption_attempt_authority_binding",
        ),
    ),
}


_NAMED_VERIFIER_RULE_IDS = (
    "research_questions_question_id_issuance",
    "research_questions_grant_required",
    "research_question_aliases_canonical_alias_hash",
    "research_question_aliases_grant_required",
    "strategy_lineage_roots_root_id_issuance",
    "strategy_lineage_roots_grant_required",
    "strategy_lineage_nodes_node_id_issuance",
    "strategy_lineage_nodes_grant_required",
    "strategy_config_lineage_bindings_grant_required",
    "research_authority_events_grant_policy",
    "research_authority_events_audit_actor_provenance",
    "research_authority_events_event_id_issuance",
    "selection_scopes_scope_id_issuance",
    "selection_scopes_grant_required",
    "selection_scope_aliases_canonical_alias_hash",
    "selection_scope_aliases_grant_required",
    "research_programs_program_id_issuance",
    "selection_decisions_decision_class_slot_policy",
    "selection_decisions_decision_and_family_id_issuance",
    "global_oos_exposures_global_exposure_id",
)


def _external_bindings(
    *pairs: tuple[str, str],
) -> tuple[ExternalFieldBindingSpec, ...]:
    return tuple(
        ExternalFieldBindingSpec(local_column, provider_field_path)
        for local_column, provider_field_path in pairs
    )


_GRANT_PROVIDER_BINDINGS = _external_bindings(
    ("grant_id", "grant_id"),
    ("grant_record_hash", "grant_record_hash"),
    ("grant_policy_id", "policy_id"),
    ("grant_policy_version", "policy_version"),
    ("grant_policy_hash", "policy_hash"),
    ("grant_authority_id", "authority_id"),
    ("grant_authority_head", "authority_head"),
    ("action", "action"),
    ("subject_record_type", "subject_record_type"),
    ("subject_id", "subject_id"),
    ("subject_record_hash", "subject_record_hash"),
    ("audit_actor_id", "consumed_by_actor_id"),
    ("event_at_us", "consumed_at_us"),
)

_ACTOR_PROVIDER_BINDINGS = _external_bindings(
    ("audit_actor_id", "actor_id"),
    ("audit_actor_kind", "actor_kind"),
    ("audit_actor_evidence_hash", "evidence_hash"),
    ("audit_actor_policy_id", "policy_id"),
    ("audit_actor_policy_version", "policy_version"),
    ("audit_actor_policy_hash", "policy_hash"),
    ("audit_actor_authority_id", "authority_id"),
    ("audit_actor_authority_head", "authority_head"),
    ("action", "action"),
    ("subject_record_type", "subject_record_type"),
    ("subject_id", "subject_id"),
    ("subject_record_hash", "subject_record_hash"),
    ("event_at_us", "event_at_us"),
)

_EXTERNAL_AUTHORITY_RULES = {
    "research_authority_events_grant_policy": (
        ExternalAuthorityReferenceKind.MULTI_PARTY_GRANT,
        "MultiPartyGrantEvidence",
        "multi_party_grant_evidence.verify",
        _GRANT_PROVIDER_BINDINGS,
        True,
    ),
    "research_authority_events_audit_actor_provenance": (
        ExternalAuthorityReferenceKind.ACTOR_PROVENANCE,
        "ActorProvenanceEvidence",
        "actor_provenance_evidence.verify",
        _ACTOR_PROVIDER_BINDINGS,
        False,
    ),
}


@_dataclass(frozen=True, slots=True)
class _NamedVerifierReadSurfaceSpec:
    columns: tuple[str, ...]
    referenced_table: str | None = None
    verifier_code: str | None = None
    required_rule_ids: tuple[str, ...] = ()
    required_verifier_codes: tuple[str, ...] = ()
    kind: VerificationRuleKind | None = None


_NAMED_RULE_READ_SURFACES = {
    "research_questions_question_id_issuance": _NamedVerifierReadSurfaceSpec(
        (
            "research_question_id",
            "authority_instance_id",
            "question_hash",
            "authority_event_id",
        ),
        referenced_table="research_authority_events",
        required_rule_ids=("research_questions_authority_event_binding",),
    ),
    "strategy_lineage_roots_root_id_issuance": _NamedVerifierReadSurfaceSpec(
        (
            "strategy_lineage_root_id",
            "authority_instance_id",
            "source_manifest_hash",
            "authority_event_id",
        ),
        referenced_table="research_authority_events",
        required_rule_ids=("strategy_lineage_roots_authority_event_binding",),
    ),
    "strategy_lineage_nodes_node_id_issuance": _NamedVerifierReadSurfaceSpec(
        (
            "strategy_lineage_node_id",
            "authority_instance_id",
            "strategy_lineage_root_id",
            "parent_strategy_lineage_node_id",
            "node_manifest_hash",
            "authority_event_id",
        ),
        referenced_table="research_authority_events",
        required_rule_ids=("strategy_lineage_nodes_authority_event_binding",),
    ),
    "research_authority_events_event_id_issuance": _NamedVerifierReadSurfaceSpec(
        ("event_id", "authority_instance_id", "event_ordinal", "prev_record_hash"),
    ),
    "selection_scopes_scope_id_issuance": _NamedVerifierReadSurfaceSpec(
        (
            "selection_scope_id",
            "authority_instance_id",
            "deployment_mandate_hash",
            "authority_event_id",
        ),
        referenced_table="research_authority_events",
        required_rule_ids=("selection_scopes_authority_event_binding",),
    ),
    "research_programs_program_id_issuance": _NamedVerifierReadSurfaceSpec(
        (
            "research_program_id",
            "authority_instance_id",
            "selection_scope_id",
            "research_question_id",
            "strategy_lineage_root_id",
            "authority_event_id",
        ),
        referenced_table="research_authority_events",
        required_rule_ids=("research_programs_authority_event_binding",),
    ),
    "selection_decisions_decision_and_family_id_issuance": _NamedVerifierReadSurfaceSpec(
        (
            "selection_decision_id",
            "family_id",
            "authority_instance_id",
            "selection_scope_id",
            "decision_ordinal",
            "decision_class",
            "authority_event_id",
        ),
        referenced_table="research_authority_events",
        required_rule_ids=("selection_decisions_authority_event_binding",),
    ),
    "global_oos_exposures_global_exposure_id": _NamedVerifierReadSurfaceSpec(
        (
            "oos_exposure_id",
            "authority_instance_id",
            "selection_scope_id",
            "selection_decision_id",
            "family_id",
            "exposure_ordinal",
            "exposure_fingerprint",
            "authority_event_id",
        ),
        referenced_table="research_authority_events",
        required_rule_ids=(
            "global_oos_exposures_authority_event_binding",
            "global_oos_exposures_global_exposure_fingerprint",
        ),
    ),
    "research_question_aliases_canonical_alias_hash": _NamedVerifierReadSurfaceSpec(
        ("alias", "alias_hash"),
    ),
    "selection_scope_aliases_canonical_alias_hash": _NamedVerifierReadSurfaceSpec(
        ("alias", "alias_hash"),
    ),
    "selection_decisions_decision_class_slot_policy": _NamedVerifierReadSurfaceSpec(
        (
            "selection_scope_id",
            "selection_decision_id",
            "decision_ordinal",
            "decision_class",
            "prev_record_hash",
        ),
        referenced_table="selection_scope_publishable_decisions",
        required_rule_ids=(
            "selection_decisions_scope_monotone_decision_chain",
            "selection_scope_publishable_decisions_one_slot_per_scope",
            "selection_scope_publishable_decisions_one_scope_per_slot_decision",
            "selection_scope_publishable_decisions_publishable_decision_match",
        ),
    ),
    **{
        f"{table_name}_grant_required": _NamedVerifierReadSurfaceSpec(
            (
                "authority_instance_id",
                "genesis_record_hash",
                "authority_event_id",
                "record_type",
                _SUBJECT_ID_COLUMNS[table_name],
                "record_hash",
            ),
            referenced_table="research_authority_events",
            verifier_code="c4.verify_subject_grant_required_v1",
            required_rule_ids=(
                f"{table_name}_authority_event_binding",
                "research_authority_events_grant_policy",
            ),
            required_verifier_codes=(
                "c4.verify_research_authority_events_grant_policy_v1",
            ),
            kind=VerificationRuleKind.CROSS_TABLE_REFERENCE,
        )
        for table_name in (
            "research_questions",
            "research_question_aliases",
            "strategy_lineage_roots",
            "strategy_lineage_nodes",
            "strategy_config_lineage_bindings",
            "selection_scopes",
            "selection_scope_aliases",
        )
    },
}

if set(_NAMED_VERIFIER_RULE_IDS) != (
    set(_EXTERNAL_AUTHORITY_RULES) | set(_NAMED_RULE_READ_SURFACES)
):
    raise ValueError("every named C4 verifier requires one exact read surface")


def _semantic_claim(
    claim_id: str,
    provider_field_path: str,
    operator: ExternalProviderClaimOperator,
    expected_value: bool | int | str,
) -> ExternalProviderSemanticClaimSpec:
    return ExternalProviderSemanticClaimSpec(
        claim_id=claim_id,
        provider_field_path=provider_field_path,
        operator=operator,
        expected_value=expected_value,
    )


C4_EXTERNAL_AUTHORITY_CLAIM_PROFILES = (
    ExternalAuthorityClaimProfileSpec(
        provider_contract_type="MultiPartyGrantEvidence",
        provider_capability="multi_party_grant_evidence.verify",
        external_reference_kind=ExternalAuthorityReferenceKind.MULTI_PARTY_GRANT,
        required_binding_columns=tuple(
            binding.local_column for binding in _GRANT_PROVIDER_BINDINGS
        ),
        required_claims=(
            _semantic_claim(
                "grant_consumed",
                "claims.state",
                ExternalProviderClaimOperator.EQUALS,
                "CONSUMED",
            ),
            _semantic_claim(
                "three_verified_humans",
                "claims.verified_human_actor_count",
                ExternalProviderClaimOperator.AT_LEAST,
                3,
            ),
            _semantic_claim(
                "distinct_human_actors",
                "claims.distinct_human_actors",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            ),
            _semantic_claim(
                "complete_hash_chain",
                "claims.complete_hash_chain",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            ),
            _semantic_claim(
                "grant_not_reused",
                "claims.grant_not_reused",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            ),
            _semantic_claim(
                "subject_action_bound",
                "claims.subject_action_bound",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            ),
            _semantic_claim(
                "consumption_time_bound",
                "claims.consumption_time_bound",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            ),
            _semantic_claim(
                "policy_verified",
                "claims.policy_verified",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            ),
            _semantic_claim(
                "authority_head_verified",
                "claims.authority_head_verified",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            ),
        ),
    ),
    ExternalAuthorityClaimProfileSpec(
        provider_contract_type="ActorProvenanceEvidence",
        provider_capability="actor_provenance_evidence.verify",
        external_reference_kind=ExternalAuthorityReferenceKind.ACTOR_PROVENANCE,
        required_binding_columns=tuple(
            binding.local_column for binding in _ACTOR_PROVIDER_BINDINGS
        ),
        required_claims=tuple(
            _semantic_claim(
                claim_id,
                f"claims.{claim_id}",
                ExternalProviderClaimOperator.IS_TRUE,
                True,
            )
            for claim_id in (
                "provenance_verified",
                "actor_kind_verified",
                "action_context_bound",
                "subject_context_bound",
                "event_time_bound",
                "evidence_hash_verified",
                "policy_verified",
                "authority_head_verified",
            )
        ),
    ),
)


C4_REQUIRED_EXTERNAL_PROVIDER_CAPABILITIES = tuple(
    ExternalProviderCapabilityRequirementSpec(
        provider_contract_type=profile.provider_contract_type,
        provider_capability=profile.provider_capability,
        resolution_stage=ExternalProviderResolutionStage.C8_PASS_M,
    )
    for profile in C4_EXTERNAL_AUTHORITY_CLAIM_PROFILES
)


def _bound_verification_rule(
    rule_id: str,
    kind: VerificationRuleKind,
    *,
    structural_source: StructuralVerificationSourceSpec | None = None,
) -> VerificationRuleSpec:
    if structural_source is not None:
        if rule_id in _NAMED_VERIFIER_RULE_IDS:
            raise ValueError(f"named C4 verifier rule {rule_id!r} cannot use a structural source")
        return VerificationRuleSpec(
            rule_id,
            kind,
            structural_source=structural_source,
        )
    external = _EXTERNAL_AUTHORITY_RULES.get(rule_id)
    if external is not None:
        (
            reference_kind,
            provider_contract_type,
            provider_capability,
            bindings,
            nullable_reference,
        ) = external
        return VerificationRuleSpec(
            rule_id=rule_id,
            kind=VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE,
            columns=tuple(binding.local_column for binding in bindings),
            external_reference_kind=reference_kind,
            provider_contract_type=provider_contract_type,
            provider_capability=provider_capability,
            external_bindings=bindings,
            nullable_reference=nullable_reference,
            reference_presence_column=("grant_id" if nullable_reference else None),
            verifier_code=f"c4.verify_{rule_id}_v1",
        )
    if rule_id not in _NAMED_VERIFIER_RULE_IDS:
        raise ValueError(f"C4 verifier rule {rule_id!r} has no reviewed proof source")
    read_surface = _NAMED_RULE_READ_SURFACES[rule_id]
    return VerificationRuleSpec(
        rule_id=rule_id,
        kind=read_surface.kind or kind,
        columns=read_surface.columns,
        referenced_table=read_surface.referenced_table,
        verifier_code=(
            read_surface.verifier_code or f"c4.verify_{rule_id}_v1"
        ),
        required_rule_ids=read_surface.required_rule_ids,
        required_verifier_codes=read_surface.required_verifier_codes,
    )


def _verification_rules(table_name: str) -> tuple[VerificationRuleSpec, ...]:
    rules = [
        _bound_verification_rule(
            f"{table_name}_genesis_binding",
            VerificationRuleKind.GENESIS_BINDING,
            structural_source=_structural_source(
                StructuralVerificationSourceKind.GENESIS_FOREIGN_KEY,
            ),
        ),
        _bound_verification_rule(
            f"{table_name}_canonical_row_hash",
            VerificationRuleKind.ROW_HASH,
            structural_source=_structural_source(
                StructuralVerificationSourceKind.ROW_HASH_SPEC,
            ),
        ),
        _bound_verification_rule(
            f"{table_name}_foreign_keys",
            VerificationRuleKind.FOREIGN_KEY,
            structural_source=_structural_source(
                StructuralVerificationSourceKind.ALL_FOREIGN_KEYS,
            ),
        ),
    ]
    for obligation in _VERIFIER_OBLIGATIONS[table_name]:
        rule_id = f"{table_name}_{obligation.lower()}"
        rules.append(
            _bound_verification_rule(
                rule_id,
                _specific_rule_kind(obligation),
                structural_source=_SPECIFIC_STRUCTURAL_SOURCES.get(rule_id),
            )
        )
    if table_name in _CHAINS:
        rules.extend(
            (
                _bound_verification_rule(
                    f"{table_name}_ordered_chain",
                    VerificationRuleKind.ORDERED_CHAIN,
                    structural_source=_structural_source(
                        StructuralVerificationSourceKind.CHAIN_SPEC,
                    ),
                ),
                _bound_verification_rule(
                    f"{table_name}_terminal_head",
                    VerificationRuleKind.TERMINAL_HEAD,
                    structural_source=_structural_source(
                        StructuralVerificationSourceKind.TERMINAL_HEAD,
                    ),
                ),
            )
        )
    return tuple(rules)


def _table(table_name: str) -> AuthorityTableSpec:
    columns = _TABLE_COLUMNS[table_name]
    return AuthorityTableSpec(
        table_name=table_name,
        columns=columns,
        primary_key_columns=_PRIMARY_KEYS[table_name],
        row_hash=RowHashSpec(
            canonical_fields=tuple(column.name for column in columns if column.name != "record_hash"),
            version_literal=_RECORD_TYPES[table_name],
            record_hash_column="record_hash",
        ),
        triggers=_triggers(table_name),
        foreign_keys=_FOREIGN_KEYS[table_name],
        indexes=_INDEXES[table_name],
        checks=_CHECKS[table_name],
        chain=_CHAINS.get(table_name),
        verification_rules=_verification_rules(table_name),
    )


C4_AUTHORITY_TABLE_SPECS = tuple(_table(table_name) for table_name in _TABLE_ORDER)


_REQUIRED_EXTENSIONS = (
    RequiredTableExtensionSpec(
        extension_id="C6_GLOBAL_OOS_EXPOSURE_EMBEDDED_REFS_V1",
        target_table="global_oos_exposures",
        provider_component_id="C6_EXTERNAL_AUTHORITY_REFS_V1",
        required_capability="c6.global_oos_exposure.embedded_refs",
    ),
    RequiredTableExtensionSpec(
        extension_id="C7_GLOBAL_OOS_EXPOSURE_CONSUMPTION_ATTEMPT_BINDING_V1",
        target_table="global_oos_exposure_consumptions",
        provider_component_id="C7_ONE_SHOT_OOS_V1",
        required_capability="c7.global_oos_consumption.attempt_binding",
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
    target_field: str,
) -> ContractFieldCoverageSpec:
    return ContractFieldCoverageSpec(
        contract_type=contract_type,
        field_name=field_name,
        kind=CoverageKind.CANONICAL_PAYLOAD,
        table_name=table_name,
        target_field=target_field,
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


C4_CONTRACT_COVERAGE = (
    *(
        _payload_coverage(
            "StructuredResearchQuestion",
            field_name,
            "research_questions",
            "question_payload_json",
        )
        for field_name in (
            "question_schema_version",
            "market_domain",
            "universe_domain",
            "signal_class",
            "label_definition",
            "evaluation_horizon",
        )
    ),
    _column_coverage("ResearchQuestionRef", "research_question_id", "research_questions"),
    _column_coverage("ResearchQuestionRef", "question_hash", "research_questions"),
    _payload_coverage(
        "ResearchQuestionRef",
        "question",
        "research_questions",
        "question_payload_json",
    ),
    *(
        _column_coverage("ResearchQuestionAlias", field_name, "research_question_aliases")
        for field_name in ("alias", "alias_hash", "research_question_id")
    ),
    *(
        _payload_coverage(
            "SourceManifest",
            field_name,
            "strategy_lineage_roots",
            "source_manifest_payload_json",
        )
        for field_name in (
            "source_manifest_schema_version",
            "generator_kind",
            "strategy_class",
            "generator_entrypoint",
            "source_tree_hash",
        )
    ),
    _column_coverage(
        "StrategyLineageRootRef",
        "strategy_lineage_root_id",
        "strategy_lineage_roots",
    ),
    _column_coverage(
        "StrategyLineageRootRef",
        "source_manifest_hash",
        "strategy_lineage_roots",
    ),
    _payload_coverage(
        "StrategyLineageRootRef",
        "source_manifest",
        "strategy_lineage_roots",
        "source_manifest_payload_json",
    ),
    *(
        _column_coverage("StrategyLineageNodeRef", field_name, "strategy_lineage_nodes")
        for field_name in (
            "strategy_lineage_node_id",
            "strategy_lineage_root_id",
            "parent_strategy_lineage_node_id",
            "node_manifest_hash",
        )
    ),
    *(
        _payload_coverage(
            "DeploymentMandate",
            field_name,
            "selection_scopes",
            "deployment_mandate_payload_json",
        )
        for field_name in (
            "mandate_schema_version",
            "portfolio_sleeve",
            "capital_authority",
            "risk_budget_authority",
            "market_domain",
            "universe_domain",
            "label_definition",
            "evaluation_horizon",
            "benchmark_id",
            "cost_policy_class",
        )
    ),
    _column_coverage("SelectionScopeRef", "selection_scope_id", "selection_scopes"),
    _column_coverage("SelectionScopeRef", "deployment_mandate_hash", "selection_scopes"),
    _payload_coverage(
        "SelectionScopeRef",
        "deployment_mandate",
        "selection_scopes",
        "deployment_mandate_payload_json",
    ),
    *(
        _column_coverage("SelectionScopeAlias", field_name, "selection_scope_aliases")
        for field_name in ("alias", "alias_hash", "selection_scope_id")
    ),
    *(
        _column_coverage("SelectionScopeOutcome", field_name, "selection_scope_outcomes")
        for field_name in (
            "selection_scope_id",
            "outcome",
            "selection_decision_id",
            "family_id",
            "winner_alpha_id",
            "evidence_ref_hash",
        )
    ),
    *(
        _column_coverage("ResearchProgramRef", field_name, "research_programs")
        for field_name in (
            "research_program_id",
            "selection_scope_id",
            "research_question_id",
            "question_hash",
            "strategy_lineage_root_id",
            "source_manifest_hash",
        )
    ),
    *(
        _column_coverage("SelectionDecisionRef", field_name, "selection_decisions")
        for field_name in (
            "selection_decision_id",
            "selection_scope_id",
            "decision_ordinal",
            "family_id",
            "decision_class",
        )
    ),
    *(
        _column_coverage("OosExposureRef", field_name, "global_oos_exposures")
        for field_name in (
            "oos_exposure_id",
            "selection_scope_id",
            "selection_decision_id",
            "family_id",
            "exposure_ordinal",
            "period_start_us",
            "period_end_us",
            "exposure_fingerprint",
        )
    ),
    *(
        _column_coverage(
            "OosExposureConsumption",
            field_name,
            "global_oos_exposure_consumptions",
        )
        for field_name in (
            "oos_exposure_id",
            "selection_decision_id",
            "family_id",
            "access_attempt_id",
            "expected_exposure_version",
            "consumed_at_us",
        )
    ),
    _column_coverage(
        "OosExposureConsumption",
        "consumption_authority_id",
        "global_oos_exposure_consumptions",
        "authority_instance_id",
    ),
    _column_coverage(
        "OosExposureConsumption",
        "consumption_record_hash",
        "global_oos_exposure_consumptions",
        "record_hash",
    ),
    _derived_coverage(
        "OosExposureConsumption",
        "consumption_authority_head",
        "C4_CONSUMPTION_AUTHORITY_HEAD_FROM_AUTHORITY_EVENT_RECORD_HASH_V1",
    ),
)


C4_CONTRACT_DERIVATIONS = (
    ContractFieldDerivationSpec(
        derivation_id=("C4_CONSUMPTION_AUTHORITY_HEAD_FROM_AUTHORITY_EVENT_RECORD_HASH_V1"),
        contract_type="OosExposureConsumption",
        field_name="consumption_authority_head",
        kind=ContractDerivationKind.IMMUTABLE_ROW_LOOKUP,
        binding_table="global_oos_exposure_consumptions",
        binding_field="authority_event_id",
        source_table="research_authority_events",
        source_key_field="event_id",
        source_value_field="record_hash",
    ),
)


C4_REQUIRED_CONTRACT_SURFACES = (
    RequiredContractSurfaceSpec(
        contract_type="OosExposureRef",
        field_names=(
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
        ),
        provider_component_id="C6_EXTERNAL_AUTHORITY_REFS_V1",
        required_capability="c6.global_oos_exposure.embedded_refs",
    ),
)


C4_SCHEMA_COMPONENT = SchemaComponentSpec(
    component_id=_COMPONENT_ID,
    capabilities=(
        "c4.questions_lineage_scope_decisions_exposures.core",
        "c4.global_oos_exposure.core",
        "c4.global_oos_exposure.verify",
        "c4.research_program.core",
    ),
    tables=C4_AUTHORITY_TABLE_SPECS,
    required_extensions=_REQUIRED_EXTENSIONS,
    coverage=C4_CONTRACT_COVERAGE,
    contract_derivations=C4_CONTRACT_DERIVATIONS,
    required_contract_surfaces=C4_REQUIRED_CONTRACT_SURFACES,
    required_external_provider_capabilities=(
        C4_REQUIRED_EXTERNAL_PROVIDER_CAPABILITIES
    ),
    external_authority_claim_profiles=C4_EXTERNAL_AUTHORITY_CLAIM_PROFILES,
    auxiliary_semantic_recipes=(
        *C4_ACTION_POLICIES,
        *C4_IDENTITY_ISSUANCE,
        *C4_CANONICAL_CONTENT_HASHES,
    ),
)


__all__ = [
    "AuthorityTableSpec",
    "C4_ACTION_POLICIES",
    "C4_AUTHORITY_TABLE_SPECS",
    "C4_CANONICAL_CONTENT_HASHES",
    "C4_CONTRACT_COVERAGE",
    "C4_CONTRACT_DERIVATIONS",
    "C4_EXTERNAL_AUTHORITY_CLAIM_PROFILES",
    "C4_IDENTITY_ISSUANCE",
    "C4_REQUIRED_EXTERNAL_PROVIDER_CAPABILITIES",
    "C4_REQUIRED_CONTRACT_SURFACES",
    "C4_SCHEMA_COMPONENT",
    "C4ActionPolicy",
    "C4AuthorityAction",
    "CanonicalContentHashSpec",
    "CanonicalContentHashGoldenVectorSpec",
    "CanonicalContentSourceKind",
    "IdentityDerivationGoldenVectorSpec",
    "IdentityDerivationPayloadShape",
    "IdentityDigestRepresentation",
    "IdentityIssuance",
    "IdentityIssuanceSpec",
    "IdentityPredecessorSourceKind",
    "IdentityPredecessorSourceSpec",
]
