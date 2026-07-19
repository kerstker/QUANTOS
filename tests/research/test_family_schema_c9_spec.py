"""Strict Pass-C/C9 publication-authority schema contract.

The publication row binds a unique C8 family conclusion independently from
the ordered C8 grid history.  It supports an assessed publication plus the two
honest diagnostic-only NOT_ASSESSABLE branches, but only the assessed branch
is eligible for ``DataSnoopingEvidenceRef``.  This is metadata only: no DDL,
exporter, verifier implementation, renderer, network, or lifecycle authority
is part of Pass C.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
import importlib
import inspect
from typing import Any

import pytest


MODULE = "services.research.experiment_registry.family_schema_c9"
COMPONENT_ID = "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1"
C4_COMPONENT = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
C6_COMPONENT = "C6_EXTERNAL_AUTHORITY_REFS_V1"
C8_COMPONENT = "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1"
TABLE = "artifact_publication_receipts"
RECORD_TYPE = "ARTIFACT_PUBLICATION_RECEIPT_V1"
RECEIPT_SCHEMA_VERSION = "ARTIFACT_PUBLICATION_RECEIPT_V1"
ARTIFACT_KIND = "DATA_SNOOPING_FAMILY_ARTIFACT_V1"
C8_SEMANTIC_VERIFIER = "c8.verify_family_assessment_v1"
C9_BUNDLE_VERIFIER = "c9.verify_self_contained_artifact_bundle_v1"
C5_COMPONENT = "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1"
C7_COMPONENT = "C7_ONE_SHOT_OOS_V1"

A9_EXTERNAL_INPUT_REQUIREMENTS = (
    "expected_external_provider_authority_genesis_registry",
    "trusted_scanner_implementation_registry",
    "expected_family_schema_v3_manifest_hash",
    "expected_authority_activation_code_manifest_evidence_hash",
)

CAPABILITIES = (
    "c9.publication_receipt.core",
    "c9.publication_receipt.chain",
    "c9.publication_receipt.exact_parents",
    "c9.publication_receipt.final_scan",
    "c9.publication_receipt.self_contained_bundle",
    "c9.phase_a.no_promotion",
)


def _c(name: str, sql_type: str = "TEXT", nullable: bool = False) -> tuple[str, str, bool]:
    return (name, sql_type, nullable)


COLUMNS = (
    _c("record_type"),
    _c("receipt_schema_version"),
    _c("authority_instance_id"),
    _c("genesis_record_hash"),
    _c("authority_lineage_hash"),
    _c("publication_receipt_id"),
    _c("publication_ordinal", "INTEGER"),
    _c("previous_record_hash"),
    _c("family_id"),
    _c("selection_scope_id"),
    _c("selection_decision_id"),
    _c("winner_config_hash", nullable=True),
    _c("winner_evaluation_receipt_hash", nullable=True),
    _c("closure_manifest_hash"),
    _c("closure_status"),
    _c("attempt_status"),
    _c("family_assessment_conclusion_hash"),
    _c("ordered_grid_assessment_record_hashes_json"),
    _c("ordered_grid_assessment_record_hashes_hash"),
    _c("minimum_replication_count", "INTEGER", nullable=True),
    _c("assessment_status"),
    _c("diagnostic_verdict"),
    _c("decision_class"),
    _c("promotion_effect"),
    _c("artifact_kind"),
    _c("artifact_core_hash"),
    _c("artifact_core_scan_receipt_hash"),
    _c("published_artifact_envelope_hash"),
    _c("target_content_addressed_path"),
    _c("public_artifact_scan_policy_id"),
    _c("dlp_policy_id"),
    _c("dlp_policy_version"),
    _c("dlp_policy_hash"),
    _c("scanner_implementation"),
    _c("scanner_version"),
    _c("scan_status"),
    _c("scan_result_hash"),
    _c("envelope_scanned_at_us", "INTEGER"),
    _c("exporter_event_hash"),
    _c("exporter_authority_head"),
    _c("failure_reasons_json"),
    _c("published_at_us", "INTEGER"),
    _c("receipt_record_hash"),
)
ROW_HASH_FIELDS = tuple(name for name, _, _ in COLUMNS if name != "receipt_record_hash")

C6_PUBLICATION_CONTRACT_FIELDS = (
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
REQUIRED_C6_EXTENSION = (
    "C6_PUBLICATION_RECEIPT_EMBEDDED_REFS_V1",
    TABLE,
    C6_COMPONENT,
    "c6.publication_receipt.embedded_refs",
)

DEPENDENCIES = {
    C4_COMPONENT: (
        "CROSS_TABLE_REFERENCE",
        ("c4.questions_lineage_scope_decisions_exposures.core",),
    ),
    C5_COMPONENT: (
        "CROSS_TABLE_REFERENCE",
        (
            "c5.family_search.core",
            "c5.proposal_registration.core",
            "c5.proposal.core",
            "c5.hypothesis.core",
            "c5.emission_seal.core",
        ),
    ),
    C7_COMPONENT: (
        "CROSS_TABLE_REFERENCE",
        (
            "c7.one_shot_oos.core",
            "c7.oos_release.core",
            "c7.oos_attempt.event_stream",
            "c7.candidate_disposition.core",
            "c7.family_evaluation_batch.atomic",
            "c7.series.exact_reconciliation",
            "c7.global_oos_consumption.attempt_binding",
        ),
    ),
    C8_COMPONENT: (
        "CROSS_TABLE_REFERENCE",
        (
            "c8.family_closure.core",
            "c8.grid_assessment.core",
            "c8.assessment.family_conclusion",
        ),
    ),
}

FOREIGN_KEYS = (
    (
        ("authority_instance_id", "genesis_record_hash"),
        "family_authority_genesis",
        ("authority_instance_id", "record_hash"),
        "RESTRICT",
        "RESTRICT",
        "NOT_DEFERRABLE",
    ),
    (
        ("authority_instance_id", "family_id", "closure_manifest_hash"),
        "data_snooping_family_closures",
        ("authority_instance_id", "family_id", "record_hash"),
        "RESTRICT",
        "RESTRICT",
        "NOT_DEFERRABLE",
    ),
    (
        ("authority_instance_id", "family_id", "family_assessment_conclusion_hash"),
        "data_snooping_assessments",
        ("authority_instance_id", "family_id", "record_hash"),
        "RESTRICT",
        "RESTRICT",
        "NOT_DEFERRABLE",
    ),
    (
        ("exporter_event_hash",),
        "research_authority_events",
        ("record_hash",),
        "RESTRICT",
        "RESTRICT",
        "NOT_DEFERRABLE",
    ),
)

INDEXES = (
    ("unique_artifact_publication_receipts_record_hash", True, ("receipt_record_hash",)),
    (
        "unique_artifact_publication_receipts_authority_ordinal",
        True,
        ("authority_instance_id", "publication_ordinal"),
    ),
    (
        "unique_artifact_publication_receipts_family",
        True,
        ("authority_instance_id", "family_id"),
    ),
    (
        "unique_artifact_publication_receipts_envelope",
        True,
        ("authority_instance_id", "published_artifact_envelope_hash"),
    ),
    (
        "unique_artifact_publication_receipts_path",
        True,
        ("target_content_addressed_path",),
    ),
    (
        "unique_artifact_publication_receipts_exporter_event",
        True,
        ("exporter_event_hash",),
    ),
    (
        "ordinary_artifact_publication_receipts_conclusion",
        False,
        ("family_assessment_conclusion_hash",),
    ),
)


def _col(column: str, source: str = "CURRENT") -> tuple[Any, ...]:
    return ("COLUMN", column, source)


def _lit(value: object) -> tuple[Any, ...]:
    return ("LITERAL", value)


def _cmp(operator: str, left: tuple[Any, ...], right: tuple[Any, ...]) -> tuple[Any, ...]:
    return ("COMPARISON", operator, left, right)


def _eq(column: str, value: object, source: str = "CURRENT") -> tuple[Any, ...]:
    return _cmp("EQ", _col(column, source), _lit(value))


def _logical(operator: str, *operands: tuple[Any, ...]) -> tuple[Any, ...]:
    return ("LOGICAL", operator, operands)


def _member(column: str, values: tuple[object, ...]) -> tuple[Any, ...]:
    return ("MEMBERSHIP", _col(column), tuple(_lit(value) for value in values), False)


def _null(column: str, operator: str) -> tuple[Any, ...]:
    return ("NULL", operator, _col(column))


RESOLVED_VERDICTS = (
    "SUPPORTS_SUPERIORITY",
    "DOES_NOT_SUPPORT_SUPERIORITY",
    "METHOD_DISAGREEMENT",
    "MONTE_CARLO_UNRESOLVED",
)

ASSESSED_BRANCH = _logical(
    "AND",
    _eq("closure_status", "COMPLETE"),
    _eq("attempt_status", "COMPLETED"),
    _eq("assessment_status", "ASSESSED"),
    _member("diagnostic_verdict", RESOLVED_VERDICTS),
    _cmp("GE", _col("minimum_replication_count"), _lit(10_000)),
    _cmp("NE", _col("ordered_grid_assessment_record_hashes_json"), _lit("[]")),
    _null("winner_config_hash", "IS_NOT_NULL"),
    _null("winner_evaluation_receipt_hash", "IS_NOT_NULL"),
    _eq("failure_reasons_json", "[]"),
)
COMPLETE_NOT_ASSESSABLE_BRANCH = _logical(
    "AND",
    _eq("closure_status", "COMPLETE"),
    _eq("attempt_status", "COMPLETED"),
    _eq("assessment_status", "NOT_ASSESSABLE"),
    _eq("diagnostic_verdict", "NOT_APPLICABLE"),
    _null("minimum_replication_count", "IS_NULL"),
    _null("winner_config_hash", "IS_NOT_NULL"),
    _null("winner_evaluation_receipt_hash", "IS_NOT_NULL"),
    _cmp("NE", _col("failure_reasons_json"), _lit("[]")),
)
CLOSURE_NOT_ASSESSABLE_BRANCH = _logical(
    "AND",
    _eq("closure_status", "NOT_ASSESSABLE"),
    _member("attempt_status", ("NOT_STARTED", "INCOMPLETE")),
    _eq("assessment_status", "NOT_ASSESSABLE"),
    _eq("diagnostic_verdict", "NOT_APPLICABLE"),
    _null("minimum_replication_count", "IS_NULL"),
    _null("winner_config_hash", "IS_NULL"),
    _null("winner_evaluation_receipt_hash", "IS_NULL"),
    _eq("ordered_grid_assessment_record_hashes_json", "[]"),
    _cmp("NE", _col("failure_reasons_json"), _lit("[]")),
)
LOCAL_BRANCH = _logical(
    "AND",
    _eq("promotion_effect", "NONE"),
    _logical(
        "OR",
        ASSESSED_BRANCH,
        COMPLETE_NOT_ASSESSABLE_BRANCH,
        CLOSURE_NOT_ASSESSABLE_BRANCH,
    ),
)

CHECKS = (
    ("artifact_publication_receipts_record_type", _eq("record_type", RECORD_TYPE)),
    (
        "artifact_publication_receipts_schema_version",
        _eq("receipt_schema_version", RECEIPT_SCHEMA_VERSION),
    ),
    (
        "artifact_publication_receipts_positive_ordinal",
        _cmp("GT", _col("publication_ordinal"), _lit(0)),
    ),
    (
        "artifact_publication_receipts_positive_time",
        _cmp("GT", _col("published_at_us"), _lit(0)),
    ),
    (
        "artifact_publication_receipts_positive_envelope_scan_time",
        _cmp("GT", _col("envelope_scanned_at_us"), _lit(0)),
    ),
    (
        "artifact_publication_receipts_scan_precedes_publication",
        _cmp(
            "LE",
            _col("envelope_scanned_at_us"),
            _col("published_at_us"),
        ),
    ),
    ("artifact_publication_receipts_artifact_kind", _eq("artifact_kind", ARTIFACT_KIND)),
    (
        "artifact_publication_receipts_attempt_status_domain",
        _member("attempt_status", ("COMPLETED", "NOT_STARTED", "INCOMPLETE")),
    ),
    (
        "artifact_publication_receipts_decision_class_domain",
        _member("decision_class", ("PUBLISHABLE", "DIAGNOSTIC_ONLY")),
    ),
    ("artifact_publication_receipts_scan_allow_only", _eq("scan_status", "ALLOW")),
    ("artifact_publication_receipts_promotion_none", _eq("promotion_effect", "NONE")),
    ("artifact_publication_receipts_exact_branch", LOCAL_BRANCH),
)


def _new_bindings(*pairs: str | tuple[str, str]) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    return tuple(
        (related, _col(local, "NEW"))
        for related, local in ((item, item) if isinstance(item, str) else item for item in pairs)
    )


CHAIN_GUARD = (
    "CHAIN_PREDECESSOR",
    TABLE,
    _new_bindings("authority_instance_id"),
    "publication_ordinal",
    _col("publication_ordinal", "NEW"),
    "receipt_record_hash",
    _col("previous_record_hash", "NEW"),
    _col("genesis_record_hash", "NEW"),
    True,
)

CLOSURE_GUARD = (
    "RELATION_EXISTS",
    "data_snooping_family_closures",
    _new_bindings(
        "authority_instance_id",
        "family_id",
        ("record_hash", "closure_manifest_hash"),
        "closure_status",
        "selection_scope_id",
        "selection_decision_id",
        "winner_config_hash",
        "winner_evaluation_receipt_hash",
    ),
    _cmp("LE", _col("closed_at_us", "RELATED"), _col("published_at_us", "NEW")),
    True,
)

CONCLUSION_GUARD = (
    "RELATION_EXISTS",
    "data_snooping_assessments",
    _new_bindings(
        "authority_instance_id",
        "family_id",
        ("record_hash", "family_assessment_conclusion_hash"),
        "closure_manifest_hash",
        ("ordered_latest_grid_assessment_hashes_json", "ordered_grid_assessment_record_hashes_json"),
        ("ordered_latest_grid_assessment_hashes_hash", "ordered_grid_assessment_record_hashes_hash"),
        "minimum_replication_count",
        "assessment_status",
        "diagnostic_verdict",
        "promotion_effect",
    ),
    _cmp("LE", _col("concluded_at_us", "RELATED"), _col("published_at_us", "NEW")),
    True,
)

GRID_HISTORY_HASH_GUARD = (
    "CANONICAL_JSON_PAYLOAD_HASH",
    _col("ordered_grid_assessment_record_hashes_json", "NEW"),
    (),
    "QUANTOS_CANONICAL_JSON_V1",
    "SHA256",
    "NE",
    _col("ordered_grid_assessment_record_hashes_hash", "NEW"),
    True,
    "REJECT",
)

EXPORTER_GUARD = (
    "RELATION_EXISTS",
    "research_authority_events",
    _new_bindings(
        "authority_instance_id",
        ("record_hash", "exporter_event_hash"),
        ("subject_id", "publication_receipt_id"),
        ("subject_record_hash", "published_artifact_envelope_hash"),
    ),
    _logical(
        "AND",
        _eq("action", "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1", "RELATED"),
        _eq("subject_record_type", "PUBLISHED_ARTIFACT_ENVELOPE_V1", "RELATED"),
        _cmp("LE", _col("event_at_us", "RELATED"), _col("published_at_us", "NEW")),
        _cmp("EQ", _col("record_hash", "RELATED"), _col("exporter_authority_head", "NEW")),
    ),
    True,
)

TRIGGERS = (
    (
        "trg_no_update_artifact_publication_receipts",
        "NO_UPDATE",
        "UPDATE",
        "ARTIFACT_PUBLICATION_RECEIPTS_APPEND_ONLY_UPDATE",
        None,
    ),
    (
        "trg_no_delete_artifact_publication_receipts",
        "NO_DELETE",
        "DELETE",
        "ARTIFACT_PUBLICATION_RECEIPTS_APPEND_ONLY_DELETE",
        None,
    ),
    (
        "trg_guard_artifact_publication_receipts_chain",
        "INSERT_GUARD",
        "INSERT",
        "PUBLICATION_CHAIN_PREDECESSOR_EXACT",
        CHAIN_GUARD,
    ),
    (
        "trg_guard_artifact_publication_receipts_closure",
        "INSERT_GUARD",
        "INSERT",
        "PUBLICATION_CLOSURE_EXACT",
        CLOSURE_GUARD,
    ),
    (
        "trg_guard_artifact_publication_receipts_conclusion",
        "INSERT_GUARD",
        "INSERT",
        "PUBLICATION_FAMILY_CONCLUSION_EXACT",
        CONCLUSION_GUARD,
    ),
    (
        "trg_guard_artifact_publication_receipts_grid_hash",
        "INSERT_GUARD",
        "INSERT",
        "PUBLICATION_GRID_HISTORY_HASH_EXACT",
        GRID_HISTORY_HASH_GUARD,
    ),
    (
        "trg_guard_artifact_publication_receipts_exporter",
        "INSERT_GUARD",
        "INSERT",
        "PUBLICATION_EXPORTER_EVENT_EXACT",
        EXPORTER_GUARD,
    ),
)

RULES = (
    (
        "publication_receipt_genesis_binding",
        "GENESIS_BINDING",
        ("authority_instance_id", "genesis_record_hash"),
        None,
        "c9.verify_publication_genesis_binding_v1",
    ),
    (
        "publication_receipt_ordered_chain",
        "ORDERED_CHAIN",
        (
            "authority_instance_id",
            "genesis_record_hash",
            "publication_ordinal",
            "previous_record_hash",
            "receipt_record_hash",
        ),
        None,
        "c9.verify_complete_publication_chain_v1",
    ),
    (
        "publication_receipt_closure_exact",
        "CROSS_TABLE_REFERENCE",
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
        "data_snooping_family_closures",
        "c9.verify_publication_closure_v1",
    ),
    (
        "publication_receipt_family_conclusion_exact",
        "CROSS_TABLE_REFERENCE",
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
        "data_snooping_assessments",
        "c9.verify_publication_family_conclusion_v1",
    ),
    (
        "publication_receipt_c8_semantic_recomputation",
        "CONTENT_BINDING",
        (
            "closure_manifest_hash",
            "family_assessment_conclusion_hash",
            "ordered_grid_assessment_record_hashes_json",
            "ordered_grid_assessment_record_hashes_hash",
            "minimum_replication_count",
            "assessment_status",
            "diagnostic_verdict",
        ),
        "data_snooping_assessments",
        C8_SEMANTIC_VERIFIER,
    ),
    (
        "publication_receipt_final_scan_allow",
        "CONTENT_BINDING",
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
        None,
        "c9.verify_exact_envelope_scan_allow_v1",
    ),
    (
        "publication_receipt_exporter_event_exact",
        "CROSS_TABLE_REFERENCE",
        (
            "authority_instance_id",
            "publication_receipt_id",
            "published_artifact_envelope_hash",
            "exporter_event_hash",
            "exporter_authority_head",
            "published_at_us",
        ),
        "research_authority_events",
        "c9.verify_exporter_event_v1",
    ),
    (
        "publication_receipt_branch",
        "STATE_BRANCH",
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
        None,
        "c9.verify_publication_branch_v1",
    ),
    (
        "publication_receipt_projection_totality",
        "PUBLICATION_TOTALITY",
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
        None,
        "c9.verify_complete_acyclic_projection_v1",
    ),
)

CHAIN = (
    ("authority_instance_id",),
    "publication_ordinal",
    "FAMILY_AUTHORITY_GENESIS_RECORD_HASH",
    "previous_record_hash",
    "receipt_record_hash",
    "LATEST_RECORD_HASH",
)

RECEIPT_OWNED_COVERAGE = {
    field: ("COLUMN", field) for field, _, _ in COLUMNS
}

EVIDENCE_REF_COVERAGE = {
    "artifact_kind": ("COLUMN", "artifact_kind"),
    "authority_instance_id": ("COLUMN", "authority_instance_id"),
    "genesis_record_hash": ("COLUMN", "genesis_record_hash"),
    "authority_lineage_hash": ("COLUMN", "authority_lineage_hash"),
    "family_id": ("COLUMN", "family_id"),
    "selection_scope_id": ("COLUMN", "selection_scope_id"),
    "selection_decision_id": ("COLUMN", "selection_decision_id"),
    "decision_class": ("COLUMN", "decision_class"),
    "winner_config_hash": ("COLUMN", "winner_config_hash"),
    "closure_manifest_hash": ("COLUMN", "closure_manifest_hash"),
    "family_assessment_conclusion_hash": (
        "COLUMN",
        "family_assessment_conclusion_hash",
    ),
    "assessment_status": ("COLUMN", "assessment_status"),
    "diagnostic_verdict": ("COLUMN", "diagnostic_verdict"),
    "promotion_effect": ("COLUMN", "promotion_effect"),
    "published_artifact_envelope_hash": (
        "COLUMN",
        "published_artifact_envelope_hash",
    ),
    "published_artifact_path": ("COLUMN", "target_content_addressed_path"),
    "artifact_publication_receipt_id": ("COLUMN", "publication_receipt_id"),
    "artifact_publication_receipt_hash": ("COLUMN", "receipt_record_hash"),
    "publication_chain_head": ("COLUMN", "receipt_record_hash"),
}

PROJECTION_REF_COVERAGE = {
    "authority_instance_id": ("COLUMN", "authority_instance_id"),
    "genesis_record_hash": ("COLUMN", "genesis_record_hash"),
    "authority_lineage_hash": ("COLUMN", "authority_lineage_hash"),
    "family_id": ("COLUMN", "family_id"),
    "decision_class": ("COLUMN", "decision_class"),
    "assessment_status": ("COLUMN", "assessment_status"),
    "artifact_publication_receipt_id": ("COLUMN", "publication_receipt_id"),
    "artifact_publication_receipt_hash": ("COLUMN", "receipt_record_hash"),
    "publication_chain_head": ("COLUMN", "receipt_record_hash"),
}

ARTIFACT_CORE_COVERAGE = {
    "authority_instance_id": ("COLUMN", "authority_instance_id"),
    "genesis_record_hash": ("COLUMN", "genesis_record_hash"),
    "authority_lineage_hash": ("COLUMN", "authority_lineage_hash"),
    "family_id": ("COLUMN", "family_id"),
    "selection_scope_id": ("COLUMN", "selection_scope_id"),
    "selection_decision_id": ("COLUMN", "selection_decision_id"),
    "winner_config_hash": ("COLUMN", "winner_config_hash"),
    "winner_evaluation_receipt_hash": (
        "COLUMN",
        "winner_evaluation_receipt_hash",
    ),
    "closure_manifest_hash": ("COLUMN", "closure_manifest_hash"),
    "closure_status": ("COLUMN", "closure_status"),
    "assessment_status": ("COLUMN", "assessment_status"),
    "diagnostic_verdict": ("COLUMN", "diagnostic_verdict"),
    "promotion_effect": ("COLUMN", "promotion_effect"),
    "ordered_grid_assessment_record_hashes": (
        "CANONICAL_PAYLOAD",
        "ordered_grid_assessment_record_hashes_json",
    ),
    "family_assessment_conclusion_hash": (
        "COLUMN",
        "family_assessment_conclusion_hash",
    ),
    "failure_reasons": ("CANONICAL_PAYLOAD", "failure_reasons_json"),
}


def _schema_module():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == MODULE:
            pytest.fail(f"missing C9 schema module: {MODULE}")
        raise


def _required(name: str) -> Any:
    value = getattr(_schema_module(), name, None)
    assert value is not None, f"missing C9 schema symbol: {name}"
    return value


def _enum(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _shape(expression: Any) -> tuple[Any, ...]:
    node = type(expression).__name__
    if node == "ColumnValue":
        return ("COLUMN", expression.column, _enum(expression.source))
    if node == "LiteralValue":
        return ("LITERAL", _enum(expression.value))
    if node == "ComparisonExpression":
        return (
            "COMPARISON",
            _enum(expression.operator),
            _shape(expression.left),
            _shape(expression.right),
        )
    if node == "MembershipExpression":
        return (
            "MEMBERSHIP",
            _shape(expression.value),
            tuple(_shape(value) for value in expression.values),
            expression.negated,
        )
    if node == "NullExpression":
        return ("NULL", _enum(expression.operator), _shape(expression.value))
    if node == "LogicalExpression":
        return (
            "LOGICAL",
            _enum(expression.operator),
            tuple(_shape(value) for value in expression.operands),
        )
    if node == "RelationExistsExpression":
        return (
            "RELATION_EXISTS",
            expression.table,
            tuple((binding.related_column, _shape(binding.value)) for binding in expression.bindings),
            None if expression.predicate is None else _shape(expression.predicate),
            expression.negated,
        )
    if node == "CanonicalJsonPayloadHashExpression":
        return (
            "CANONICAL_JSON_PAYLOAD_HASH",
            _shape(expression.document),
            tuple(expression.excluded_paths),
            _enum(expression.encoding),
            _enum(expression.algorithm),
            _enum(expression.operator),
            _shape(expression.expected_hash),
            expression.require_canonical_json,
            _enum(expression.failure_policy),
        )
    if node == "ChainPredecessorExpression":
        return (
            "CHAIN_PREDECESSOR",
            expression.table,
            tuple(
                (binding.related_column, _shape(binding.value))
                for binding in expression.partition_bindings
            ),
            expression.ordinal_column,
            _shape(expression.current_ordinal),
            expression.record_hash_column,
            _shape(expression.current_previous_hash),
            _shape(expression.genesis_hash),
            expression.negated,
        )
    raise AssertionError(f"unknown C9 AST node: {node}")


def _table() -> Any:
    component = _required("C9_SCHEMA_COMPONENT")
    assert tuple(table.table_name for table in component.tables) == (TABLE,)
    return component.tables[0]


def _column_shape(column: Any) -> tuple[Any, ...]:
    assert column.default is None
    return (column.name, _enum(column.sql_type), column.nullable)


def _fk_shape(item: Any) -> tuple[Any, ...]:
    return (
        tuple(item.columns),
        item.referenced_table,
        tuple(item.referenced_columns),
        _enum(item.on_update),
        _enum(item.on_delete),
        _enum(item.deferrability),
    )


def _index_shape(item: Any) -> tuple[Any, ...]:
    assert all(_enum(column.direction) == "ASC" for column in item.columns)
    assert all(_enum(column.collation) == "BINARY" for column in item.columns)
    assert item.predicate is None
    return (item.name, item.unique, tuple(column.column for column in item.columns))


def _trigger_shape(item: Any) -> tuple[Any, ...]:
    assert item.state_machine is None
    assert _enum(item.timing) == "BEFORE" and _enum(item.effect) == "ABORT"
    return (
        item.name,
        _enum(item.kind),
        _enum(item.event),
        item.error_code,
        None if item.violation_when is None else _shape(item.violation_when),
    )


def _rule_shape(item: Any) -> tuple[Any, ...]:
    assert item.external_reference_kind is None
    assert item.provider_contract_type is None
    assert item.provider_capability is None
    assert item.provider_component_id is None
    assert tuple(item.external_bindings) == ()
    assert item.nullable_reference is False
    assert item.reference_presence_column is None
    return (
        item.rule_id,
        _enum(item.kind),
        tuple(item.columns),
        item.referenced_table,
        item.verifier_code,
    )


def test_component_and_single_table_are_exact() -> None:
    component = _required("C9_SCHEMA_COMPONENT")
    assert component.component_id == COMPONENT_ID
    assert tuple(component.capabilities) == CAPABILITIES
    assert tuple(component.extensions) == ()
    assert tuple(component.tables) == tuple(_required("C9_AUTHORITY_TABLE_SPECS"))
    assert tuple(component.required_extensions) == tuple(_required("C9_REQUIRED_EXTENSION_SPECS"))
    assert tuple(component.coverage) == tuple(_required("C9_CONTRACT_COVERAGE"))
    assert tuple(component.contract_derivations) == ()
    assert tuple(component.artifact_graph_derivations) == tuple(
        _required("C9_ARTIFACT_GRAPH_DERIVATIONS")
    )
    assert tuple(component.required_external_provider_capabilities) == ()


def test_a9_external_trust_inputs_are_explicit_and_out_of_band() -> None:
    assert tuple(_required("C9_A9_EXTERNAL_INPUT_REQUIREMENTS")) == (
        A9_EXTERNAL_INPUT_REQUIREMENTS
    )
    assert all(
        "deployment_id" not in requirement
        and "authority_instance_id" not in requirement
        for requirement in A9_EXTERNAL_INPUT_REQUIREMENTS
    )


def test_publication_table_shape_is_exact() -> None:
    table = _table()
    assert tuple(_column_shape(column) for column in table.columns) == COLUMNS
    assert tuple(table.primary_key_columns) == ("publication_receipt_id",)
    assert tuple(table.row_hash.canonical_fields) == ROW_HASH_FIELDS
    assert table.row_hash.version_literal == "ARTIFACT_PUBLICATION_RECEIPT_V1"
    assert table.row_hash.record_hash_column == "receipt_record_hash"
    assert _enum(table.row_hash.algorithm) == "SHA256"
    assert _enum(table.row_hash.encoding) == "QUANTOS_CANONICAL_JSON_V1"
    assert tuple(_fk_shape(item) for item in table.foreign_keys) == FOREIGN_KEYS
    assert tuple(_index_shape(item) for item in table.indexes) == INDEXES
    assert tuple((item.name, _shape(item.expression)) for item in table.checks) == CHECKS
    assert tuple(_trigger_shape(item) for item in table.triggers) == TRIGGERS
    assert tuple(_rule_shape(item) for item in table.verification_rules) == RULES
    assert (
        tuple(table.chain.partition_key_columns),
        table.chain.ordinal_column,
        _enum(table.chain.genesis_rule),
        table.chain.previous_hash_column,
        table.chain.record_hash_column,
        _enum(table.chain.terminal_head_rule),
    ) == CHAIN


def test_publication_receipt_contract_dump_matches_composed_c9_c6_row_hash_fields() -> None:
    from quantos.contracts.research import ArtifactPublicationReceipt

    c6_module = importlib.import_module(
        "services.research.experiment_registry.family_schema_c5_c6"
    )
    extension = next(
        extension
        for extension in c6_module.C6_TABLE_EXTENSIONS
        if extension.target_table == TABLE
    )
    base_fields = tuple(_table().row_hash.canonical_fields)
    insertion = extension.row_hash_field_insertion
    assert insertion is not None
    anchor_index = base_fields.index(insertion.anchor_field)
    composed_fields = (
        *base_fields[: anchor_index + 1],
        *insertion.fields,
        *base_fields[anchor_index + 1 :],
    )

    constructed = ArtifactPublicationReceipt.model_construct(
        **{field_name: None for field_name in ArtifactPublicationReceipt.model_fields}
    )
    dumped = constructed.model_dump(exclude={"receipt_record_hash"}, warnings=False)
    assert tuple(dumped) == composed_fields
    assert tuple(ArtifactPublicationReceipt.model_fields) == (
        *composed_fields,
        "receipt_record_hash",
    )
    assert (
        c6_module._C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS
        == _required("C9_PUBLICATION_RECEIPT_BASE_ROW_HASH_FIELDS")
        == base_fields
    )
    row_hash_rules = tuple(
        rule
        for rule in (*_table().verification_rules, *extension.verification_rules)
        if _enum(rule.kind) == "ROW_HASH"
        and rule.rule_id == "publication_receipt_row_hash"
    )
    assert len(row_hash_rules) == 1
    assert tuple(row_hash_rules[0].columns) == composed_fields
    assert row_hash_rules[0].verifier_code == (
        "c9.verify_publication_receipt_row_hash_v1"
    )
    assert len(composed_fields) == 67


def test_leaf_static_projection_registry_matches_all_c4_c8_tables_and_18_extensions() -> None:
    from quantos.contracts.data_snooping_projection_layouts import (
        AUTHORITY_PROJECTION_EXTENSION_IDS,
        AUTHORITY_ROW_LAYOUTS,
    )

    c4 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c4"
    )
    c5_c6 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c5_c6"
    )
    c7 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c7"
    )
    c8 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c8"
    )
    tables = (
        *c4.C4_AUTHORITY_TABLE_SPECS,
        *c5_c6.C5_AUTHORITY_TABLE_SPECS,
        *c7.C7_AUTHORITY_TABLE_SPECS,
        *c8.C8_AUTHORITY_TABLE_SPECS,
    )
    extensions = (
        *c5_c6.C6_TABLE_EXTENSIONS,
        *c7.C7_TABLE_EXTENSIONS,
        *c8.C8_TABLE_EXTENSIONS,
    )
    assert len(extensions) == 18
    assert tuple(extension.extension_id for extension in extensions) == (
        AUTHORITY_PROJECTION_EXTENSION_IDS
    )

    live_layouts: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}
    for table in tables:
        columns = list(table.columns)
        fields = list(table.row_hash.canonical_fields)
        for extension in extensions:
            if extension.target_table != table.table_name:
                continue
            column_insertion = extension.column_insertion
            field_insertion = extension.row_hash_field_insertion
            assert (column_insertion is None) == (field_insertion is None)
            if column_insertion is None:
                continue
            column_index = next(
                index
                for index, column in enumerate(columns)
                if column.name == column_insertion.anchor_column
            )
            field_index = fields.index(field_insertion.anchor_field)
            if _enum(column_insertion.position) == "AFTER":
                column_index += 1
            if _enum(field_insertion.position) == "AFTER":
                field_index += 1
            columns[column_index:column_index] = list(column_insertion.columns)
            fields[field_index:field_index] = list(field_insertion.fields)

        projected_columns = tuple(
            column
            for column in columns
            if column.name != table.row_hash.record_hash_column
        )
        assert tuple(column.name for column in projected_columns) == tuple(fields)
        assert {
            _enum(column.sql_type) for column in projected_columns
        }.issubset({"TEXT", "INTEGER"})
        live_layouts[table.table_name] = (
            table.row_hash.version_literal,
            tuple(fields),
            tuple(
                column.name
                for column in projected_columns
                if _enum(column.sql_type) == "INTEGER"
            ),
            tuple(column.name for column in projected_columns if column.nullable),
        )

    assert live_layouts == AUTHORITY_ROW_LAYOUTS


def test_family_conclusion_and_grid_history_are_independent_exact_bindings() -> None:
    columns = {column.name for column in _table().columns}
    assert {
        "family_assessment_conclusion_hash",
        "ordered_grid_assessment_record_hashes_json",
        "ordered_grid_assessment_record_hashes_hash",
    }.issubset(columns)
    assert "effective_assessment_record_hash" not in columns
    assert "ordered_assessment_record_hashes_json" not in columns
    assert _shape(_table().triggers[4].violation_when) == CONCLUSION_GUARD
    bindings = dict(CONCLUSION_GUARD[2])
    assert bindings["record_hash"] == _col("family_assessment_conclusion_hash", "NEW")
    assert bindings["ordered_latest_grid_assessment_hashes_json"] == _col(
        "ordered_grid_assessment_record_hashes_json",
        "NEW",
    )
    assert _shape(_table().triggers[5].violation_when) == GRID_HISTORY_HASH_GUARD


def test_parent_guards_enforce_complete_assessed_minimum_and_fixed_c8_semantics() -> None:
    assert _cmp("GE", _col("minimum_replication_count"), _lit(10_000)) in ASSESSED_BRANCH[-1]
    assert _member("diagnostic_verdict", RESOLVED_VERDICTS) in ASSESSED_BRANCH[-1]
    assert _eq("closure_status", "COMPLETE") in ASSESSED_BRANCH[-1]
    assert _eq("assessment_status", "ASSESSED") in ASSESSED_BRANCH[-1]
    rules = {rule.rule_id: rule for rule in _table().verification_rules}
    semantic = rules["publication_receipt_c8_semantic_recomputation"]
    assert semantic.verifier_code == C8_SEMANTIC_VERIFIER
    assert semantic.referenced_table == "data_snooping_assessments"
    assert "family_assessment_conclusion_hash" in semantic.columns
    assert "ordered_grid_assessment_record_hashes_json" in semantic.columns


def test_not_assessable_branches_do_not_conflate_missing_closure_with_kernel_failure() -> None:
    checks = {item.name: _shape(item.expression) for item in _table().checks}
    branch = checks["artifact_publication_receipts_exact_branch"]
    assert COMPLETE_NOT_ASSESSABLE_BRANCH in branch[-1][-1][-1]
    assert CLOSURE_NOT_ASSESSABLE_BRANCH in branch[-1][-1][-1]
    assert _null("winner_config_hash", "IS_NOT_NULL") in COMPLETE_NOT_ASSESSABLE_BRANCH[-1]
    assert _null("winner_config_hash", "IS_NULL") in CLOSURE_NOT_ASSESSABLE_BRANCH[-1]
    assert _eq("ordered_grid_assessment_record_hashes_json", "[]") in (
        CLOSURE_NOT_ASSESSABLE_BRANCH[-1]
    )


def test_self_contained_bundle_winner_and_exact_bytes_are_load_bearing() -> None:
    c6 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c5_c6"
    )
    extension = next(
        item for item in c6.C6_TABLE_EXTENSIONS if item.target_table == TABLE
    )
    rule = next(
        item
        for item in extension.verification_rules
        if item.rule_id == "publication_receipt_self_contained_bundle"
    )
    assert rule.verifier_code == C9_BUNDLE_VERIFIER
    assert {
        "artifact_core_hash",
        "artifact_core_scan_receipt_hash",
        "published_artifact_envelope_hash",
        "closure_manifest_hash",
        "family_assessment_conclusion_hash",
        "winner_config_hash",
        "winner_evaluation_receipt_hash",
        "receipt_record_hash",
    }.issubset(rule.columns)
    assert set(C6_PUBLICATION_CONTRACT_FIELDS).issubset(rule.columns)
    assert "VERIFY_PUBLICATION_BYTE_HASH_PROVENANCE" not in {
        item.verifier_code for item in extension.verification_rules
    }


def test_receipt_contract_coverage_and_c6_delegation_are_exact() -> None:
    component = _required("C9_SCHEMA_COMPONENT")
    persisted: dict[str, dict[str, tuple[str, str]]] = {}
    derived: dict[tuple[str, str], str] = {}
    for item in component.coverage:
        if _enum(item.kind) == "NON_PERSISTED_DERIVATION":
            assert item.table_name is None
            assert item.target_field is None
            assert item.derivation_id is not None
            derived[(item.contract_type, item.field_name)] = item.derivation_id
        else:
            assert item.table_name == TABLE
            assert item.derivation_id is None
            persisted.setdefault(item.contract_type, {})[item.field_name] = (
                _enum(item.kind),
                item.target_field,
            )
    assert persisted == {
        "ArtifactPublicationReceipt": RECEIPT_OWNED_COVERAGE,
        "ArtifactPublicationProjectionRef": PROJECTION_REF_COVERAGE,
        "DataSnoopingEvidenceRef": EVIDENCE_REF_COVERAGE,
    }
    expected_derived = {
        (surface.contract_type, field_name): graph.derivation_id
        for graph in component.artifact_graph_derivations
        for surface in graph.contract_surfaces
        for field_name in surface.field_names
    }
    assert derived == expected_derived

    receipt = importlib.import_module("quantos.contracts.research").ArtifactPublicationReceipt
    assert set(receipt.model_fields) == set(RECEIPT_OWNED_COVERAGE) | set(
        C6_PUBLICATION_CONTRACT_FIELDS
    )
    assert tuple(
        (
            item.extension_id,
            item.target_table,
            item.provider_component_id,
            item.required_capability,
        )
        for item in component.required_extensions
    ) == (REQUIRED_C6_EXTENSION,)
    assert tuple(
        (
            item.contract_type,
            tuple(item.field_names),
            item.provider_component_id,
            item.required_capability,
        )
        for item in component.required_contract_surfaces
    ) == (
        (
            "ArtifactPublicationReceipt",
            C6_PUBLICATION_CONTRACT_FIELDS,
            C6_COMPONENT,
            "c6.publication_receipt.embedded_refs",
        ),
    )


def test_targeted_c9_contract_fields_are_covered_with_new_names_only() -> None:
    contracts = importlib.import_module("quantos.contracts.research")
    assert set(EVIDENCE_REF_COVERAGE).issubset(contracts.DataSnoopingEvidenceRef.model_fields)
    assert set(PROJECTION_REF_COVERAGE).issubset(
        contracts.ArtifactPublicationProjectionRef.model_fields
    )
    envelope_graph, projection_graph = _required("C9_ARTIFACT_GRAPH_DERIVATIONS")
    surfaces = {
        surface.contract_type: tuple(surface.field_names)
        for surface in envelope_graph.contract_surfaces
    }
    assert set(surfaces["ArtifactCore"]) == set(contracts.ArtifactCore.model_fields)
    assert set(surfaces["ArtifactCoreScanReceipt"]) == set(
        contracts.ArtifactCoreScanReceipt.model_fields
    )
    assert set(surfaces["PublishedArtifactEnvelope"]) == set(
        contracts.PublishedArtifactEnvelope.model_fields
    )
    assert "DataSnoopingEvidenceRef" not in surfaces
    projection_surfaces = {
        surface.contract_type: tuple(surface.field_names)
        for surface in projection_graph.contract_surfaces
    }
    assert projection_surfaces["ArtifactPublicationProjectionRef"] == (
        "record_type",
        "projection_schema_version",
    )
    assert "family_assessment_conclusion_hash" in contracts.ArtifactCore.model_fields
    assert "ordered_grid_assessment_record_hashes" in contracts.ArtifactCore.model_fields
    assert "effective_assessment_record_hash" not in contracts.ArtifactCore.model_fields
    assert "ordered_assessment_record_hashes" not in contracts.ArtifactCore.model_fields


def test_dependencies_resolve_live_c4_c5_c7_and_c8_capabilities() -> None:
    component = _required("C9_SCHEMA_COMPONENT")
    actual = {
        item.component_id: (_enum(item.kind), tuple(item.required_capabilities))
        for item in component.dependencies
    }
    assert actual == DEPENDENCIES
    live = {
        C4_COMPONENT: importlib.import_module(
            "services.research.experiment_registry.family_schema_c4"
        ).C4_SCHEMA_COMPONENT,
        C5_COMPONENT: importlib.import_module(
            "services.research.experiment_registry.family_schema_c5_c6"
        ).C5_SCHEMA_COMPONENT,
        C7_COMPONENT: importlib.import_module(
            "services.research.experiment_registry.family_schema_c7"
        ).C7_SCHEMA_COMPONENT,
        C8_COMPONENT: importlib.import_module(
            "services.research.experiment_registry.family_schema_c8"
        ).C8_SCHEMA_COMPONENT,
    }
    for component_id, (_, capabilities) in actual.items():
        assert set(capabilities).issubset(live[component_id].capabilities)


def test_c4_action_domain_and_c9_exporter_guard_form_one_reachable_closed_loop() -> None:
    c4 = importlib.import_module(
        "services.research.experiment_registry.family_schema_c4"
    )
    policies = {
        str(getattr(item.action, "value", item.action)): (
            item.subject_table,
            item.grant_required,
        )
        for item in c4.C4_ACTION_POLICIES
    }
    action = "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1"
    assert policies[action] == (TABLE, False)
    assert "UNKNOWN_EXPORT_ACTION" not in policies
    action_check = next(
        check
        for check in next(
            table
            for table in c4.C4_AUTHORITY_TABLE_SPECS
            if table.table_name == "research_authority_events"
        ).checks
        if check.name == "research_authority_events_action_domain"
    )
    assert {item.value for item in action_check.expression.values} == set(policies)
    assert _shape(_table().triggers[6].violation_when) == EXPORTER_GUARD
    assert _eq("action", action, "RELATED") in EXPORTER_GUARD[3][2]
    assert any(
        foreign_key.referenced_table == "research_authority_events"
        and tuple(foreign_key.columns) == ("exporter_event_hash",)
        and tuple(foreign_key.referenced_columns) == ("record_hash",)
        for foreign_key in _table().foreign_keys
    )


def test_publication_projection_address_is_not_persisted_or_self_referential() -> None:
    columns = {column.name for column in _table().columns}
    forbidden = {
        "publication_projection_hash",
        "publication_projection_path",
        "publication_projection_sha256",
        "sidecar_sha256",
        "sidecar_path",
    }
    assert columns.isdisjoint(forbidden)
    assert set(_table().row_hash.canonical_fields).isdisjoint(forbidden)
    projection_rule = next(
        item
        for item in _table().verification_rules
        if item.rule_id == "publication_receipt_projection_totality"
    )
    assert projection_rule.verifier_code == "c9.verify_complete_acyclic_projection_v1"
    envelope_graph, projection_graph = _required("C9_ARTIFACT_GRAPH_DERIVATIONS")
    assert _enum(envelope_graph.locator.kind) == "PERSISTED_ROW"
    assert (
        envelope_graph.locator.binding_table,
        envelope_graph.locator.hash_field,
        envelope_graph.locator.path_field,
    ) == (TABLE, "published_artifact_envelope_hash", "target_content_addressed_path")
    assert _enum(projection_graph.locator.kind) == "CONTRACT_INPUT"
    assert (
        projection_graph.locator.contract_type,
        projection_graph.locator.hash_field,
        projection_graph.locator.path_field,
        projection_graph.locator.binding_table,
    ) == (
        "ArtifactPublicationProjectionRef",
        "publication_projection_hash",
        "publication_projection_path",
        None,
    )
    projection_surface = projection_graph.contract_surfaces[0]
    contracts = importlib.import_module("quantos.contracts.research")
    assert projection_surface.contract_type == "ArtifactPublicationReceiptProjection"
    assert set(projection_surface.field_names) == set(
        contracts.ArtifactPublicationReceiptProjection.model_fields
    )
    assert {
        (item.contract_type, item.field_name)
        for item in _required("C9_SCHEMA_COMPONENT").coverage
        if _enum(item.kind) == "NON_PERSISTED_DERIVATION"
    }.isdisjoint(
        {
            ("ArtifactPublicationProjectionRef", "publication_projection_hash"),
            ("ArtifactPublicationProjectionRef", "publication_projection_path"),
        }
    )


def test_pass_c_exports_no_runtime_ddl_exporter_verifier_or_network_service() -> None:
    module = _schema_module()
    source = inspect.getsource(module).upper()
    for forbidden in (
        "CREATE TABLE",
        "CREATE TRIGGER",
        "PRAGMA USER_VERSION",
        "SQLITE3",
        "REQUESTS",
        "HTTPX",
        "EXPORT_PUBLISHED_ARTIFACT",
        "VERIFY_PUBLISHED_ARTIFACT",
        "PUBLISH_ARTIFACT",
        "APPEND_PUBLICATION_RECEIPT",
    ):
        assert forbidden not in source


def test_closed_ast_has_no_raw_sql_or_summary_boolean_escape() -> None:
    for trigger in _table().triggers:
        if trigger.violation_when is None:
            continue
        flattened = repr(_shape(trigger.violation_when)).upper()
        assert "RAW_SQL" not in flattened
        assert "SUMMARY_BOOLEAN" not in flattened
        assert "TRUSTED" not in flattened

    def walk(value: Any):
        if is_dataclass(value):
            yield from walk(asdict(value))
        elif isinstance(value, Mapping):
            yield value
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                yield from walk(item)

    assert tuple(walk(_table()))
