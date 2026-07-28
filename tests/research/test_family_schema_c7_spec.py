"""Exact Pass-C/C7 schema-AST semantics (intentionally RED).

This file freezes a private, non-executable component specification for the
one-shot whole-family OOS authority.  It must not create a database, render
SQL, run a migration, or implement any A7 service.  C6 owns the external
reference columns embedded in the release row, while C8 will later provide the
freeze/closure late-insert guards required by every C7-owned table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
import importlib
import inspect
from types import ModuleType
from typing import Any

import pytest


MODULE = "services.research.experiment_registry.family_schema_c7"
COMPONENT_ID = "C7_ONE_SHOT_OOS_V1"
C4_COMPONENT = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
C5_COMPONENT = "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1"
C6_COMPONENT = "C6_EXTERNAL_AUTHORITY_REFS_V1"
C8_COMPONENT = "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1"

C7_CAPABILITIES = (
    "c7.one_shot_oos.core",
    "c7.oos_release.core",
    "c7.oos_attempt.event_stream",
    "c7.candidate_disposition.core",
    "c7.family_evaluation_batch.atomic",
    "c7.series.exact_reconciliation",
    "c7.global_oos_consumption.attempt_binding",
)

C4_CONSUMPTION_EXTENSION = "C7_GLOBAL_OOS_EXPOSURE_CONSUMPTION_ATTEMPT_BINDING_V1"
C6_RELEASE_EXTENSION = "C6_OOS_RELEASE_EMBEDDED_REFS_V1"
C6_RELEASE_CONTRACT_FIELDS = (
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

TABLE_ORDER = (
    "data_snooping_oos_releases",
    "data_snooping_oos_access_attempts",
    "data_snooping_candidate_dispositions",
    "data_snooping_evaluation_batches",
    "data_snooping_family_oos_evaluations",
    "data_snooping_evaluation_receipts",
    "data_snooping_benchmark_returns",
)

RECORD_TYPES = {
    "data_snooping_oos_releases": "DATA_SNOOPING_OOS_RELEASE_V1",
    "data_snooping_oos_access_attempts": "DATA_SNOOPING_OOS_ACCESS_ATTEMPT_EVENT_V1",
    "data_snooping_candidate_dispositions": "DATA_SNOOPING_CANDIDATE_DISPOSITION_V1",
    "data_snooping_evaluation_batches": "DATA_SNOOPING_EVALUATION_BATCH_V1",
    "data_snooping_family_oos_evaluations": "DATA_SNOOPING_FAMILY_OOS_EVALUATION_V1",
    "data_snooping_evaluation_receipts": "DATA_SNOOPING_EVALUATION_RECEIPT_V1",
    "data_snooping_benchmark_returns": "DATA_SNOOPING_BENCHMARK_RETURN_V1",
}


def _c(name: str, sql_type: str = "TEXT", nullable: bool = False) -> tuple[str, str, bool]:
    return (name, sql_type, nullable)


def _columns(*specific: tuple[str, str, bool]) -> tuple[tuple[str, str, bool], ...]:
    return (
        _c("record_type"),
        _c("authority_instance_id"),
        _c("genesis_record_hash"),
        *specific,
        _c("record_hash"),
    )


COLUMNS = {
    "data_snooping_oos_releases": _columns(
        _c("oos_release_id"),
        _c("family_id"),
        _c("attempt_id"),
        _c("started_attempt_event_id"),
        _c("family_freeze_record_hash"),
        _c("exposure_consumption_record_hash"),
        _c("oos_exposure_id"),
        _c("released_at_us", "INTEGER"),
    ),
    "data_snooping_oos_access_attempts": _columns(
        _c("attempt_event_id"),
        _c("attempt_id"),
        _c("family_id"),
        _c("selection_decision_id"),
        _c("oos_exposure_id"),
        _c("evaluation_batch_id"),
        _c("sequence_no", "INTEGER"),
        _c("status"),
        _c("frozen_config_set_hash"),
        _c("benchmark_contract_hash"),
        _c("ordered_period_calendar_hash"),
        _c("cost_contract_hash"),
        _c("start_execution_attestation_hash"),
        _c("terminal_execution_attestation_hash", nullable=True),
        _c("immutable_artifact_lease_hash"),
        _c("evaluation_batch_receipt_hash", nullable=True),
        _c("reason_code", nullable=True),
        _c("event_at_us", "INTEGER"),
        _c("prev_record_hash"),
    ),
    "data_snooping_candidate_dispositions": _columns(
        _c("family_id"),
        _c("proposal_id"),
        _c("disposition"),
        _c("config_hash", nullable=True),
        _c("evaluation_receipt_hash", nullable=True),
        _c("quarantine_record_id", nullable=True),
        _c("reason_code", nullable=True),
        _c("performance_accessed", "INTEGER"),
        _c("recorded_at_us", "INTEGER"),
    ),
    "data_snooping_evaluation_batches": _columns(
        _c("evaluation_batch_id"),
        _c("family_id"),
        _c("attempt_id"),
        _c("frozen_config_set_hash"),
        _c("candidate_count", "INTEGER"),
        _c("ordered_candidate_config_hashes_hash"),
        _c("ordered_candidate_receipt_hashes_hash"),
        _c("benchmark_receipt_hash"),
        _c("ordered_period_keys_hash"),
        _c("cost_contract_hash"),
        _c("start_execution_attestation_hash"),
        _c("terminal_execution_attestation_hash"),
        _c("immutable_artifact_lease_hash"),
        _c("committed_at_us", "INTEGER"),
    ),
    "data_snooping_family_oos_evaluations": _columns(
        _c("family_id"),
        _c("evaluation_batch_id"),
        _c("config_hash"),
        _c("alpha_id"),
        _c("source_search_id"),
        _c("source_proposal_id"),
        _c("evaluation_run_id"),
        _c("evaluation_content_hash"),
        _c("evaluation_audit_intent_hash"),
        _c("evaluation_receipt_hash"),
        _c("linked_at_us", "INTEGER"),
    ),
    "data_snooping_evaluation_receipts": _columns(
        _c("evaluation_receipt_hash"),
        _c("family_id"),
        _c("evaluation_batch_id"),
        _c("config_hash"),
        _c("series_payload_json"),
        _c("ordered_period_keys_hash"),
        _c("gross_returns_hash"),
        _c("costs_hash"),
        _c("net_returns_hash"),
        _c("turnovers_hash"),
        _c("cost_contract_hash"),
        _c("cost_model_input_hash"),
        _c("series_hash"),
        _c("start_execution_attestation_hash"),
        _c("terminal_execution_attestation_hash"),
        _c("immutable_artifact_lease_hash"),
        _c("committed_at_us", "INTEGER"),
    ),
    "data_snooping_benchmark_returns": _columns(
        _c("benchmark_receipt_hash"),
        _c("family_id"),
        _c("evaluation_batch_id"),
        _c("benchmark_id"),
        _c("benchmark_contract_hash"),
        _c("series_payload_json"),
        _c("ordered_period_keys_hash"),
        _c("gross_returns_hash"),
        _c("costs_hash"),
        _c("net_returns_hash"),
        _c("turnovers_hash"),
        _c("cost_contract_hash"),
        _c("cost_model_input_hash"),
        _c("series_hash"),
        _c("start_execution_attestation_hash"),
        _c("terminal_execution_attestation_hash"),
        _c("immutable_artifact_lease_hash"),
        _c("committed_at_us", "INTEGER"),
    ),
}

PRIMARY_KEYS = {
    "data_snooping_oos_releases": ("oos_release_id",),
    "data_snooping_oos_access_attempts": ("attempt_event_id",),
    "data_snooping_candidate_dispositions": ("family_id", "proposal_id"),
    "data_snooping_evaluation_batches": ("evaluation_batch_id",),
    "data_snooping_family_oos_evaluations": ("family_id", "config_hash"),
    "data_snooping_evaluation_receipts": ("evaluation_receipt_hash",),
    "data_snooping_benchmark_returns": ("benchmark_receipt_hash",),
}


def _fk(
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
    *,
    deferred: bool = False,
) -> tuple[object, ...]:
    return (
        columns,
        referenced_table,
        referenced_columns,
        "RESTRICT",
        "RESTRICT",
        ("DEFERRABLE_INITIALLY_DEFERRED" if deferred else "NOT_DEFERRABLE"),
    )


def _genesis_fk() -> tuple[object, ...]:
    return _fk(
        ("authority_instance_id", "genesis_record_hash"),
        "family_authority_genesis",
        ("authority_instance_id", "record_hash"),
    )


FOREIGN_KEYS = {
    "data_snooping_oos_releases": (
        _genesis_fk(),
        _fk(
            ("oos_exposure_id",),
            "global_oos_exposures",
            ("oos_exposure_id",),
        ),
        _fk(
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
        _fk(
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
        _genesis_fk(),
        _fk(
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
        _fk(
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
        _genesis_fk(),
        _fk(
            ("proposal_id",),
            "data_snooping_proposal_registrations",
            ("proposal_id",),
        ),
        _fk(
            ("family_id", "config_hash"),
            "data_snooping_hypotheses",
            ("family_id", "config_hash"),
        ),
        _fk(
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
        _genesis_fk(),
        _fk(
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
        _fk(
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
        _genesis_fk(),
        _fk(
            ("family_id", "config_hash"),
            "data_snooping_hypotheses",
            ("family_id", "config_hash"),
        ),
        _fk(
            ("family_id", "source_search_id"),
            "data_snooping_family_searches",
            ("family_id", "search_id"),
        ),
        _fk(
            ("source_proposal_id",),
            "data_snooping_proposals",
            ("proposal_id",),
        ),
        _fk(("evaluation_run_id",), "evaluations", ("run_id",)),
        _fk(
            ("evaluation_audit_intent_hash",),
            "evaluation_audit_outbox",
            ("record_hash",),
        ),
        _fk(
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
        _genesis_fk(),
        _fk(
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            "data_snooping_evaluation_batches",
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            deferred=True,
        ),
        _fk(
            ("family_id", "config_hash"),
            "data_snooping_hypotheses",
            ("family_id", "config_hash"),
        ),
        _fk(
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
        _genesis_fk(),
        _fk(
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            "data_snooping_evaluation_batches",
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
            deferred=True,
        ),
    ),
}


def _ix(
    table: str,
    suffix: str,
    kind: str,
    columns: tuple[str, ...],
    predicate: tuple[str, str, int] | None = None,
) -> tuple[object, ...]:
    return (f"{kind.lower()}_{table}_{suffix}", kind, columns, predicate)


INDEXES = {
    "data_snooping_oos_releases": (
        _ix("data_snooping_oos_releases", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_oos_releases", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _ix("data_snooping_oos_releases", "attempt", "UNIQUE", ("authority_instance_id", "attempt_id")),
        _ix("data_snooping_oos_releases", "exposure", "UNIQUE", ("authority_instance_id", "oos_exposure_id")),
        _ix(
            "data_snooping_oos_releases",
            "exact_binding",
            "UNIQUE",
            ("authority_instance_id", "family_id", "attempt_id", "oos_exposure_id"),
        ),
    ),
    "data_snooping_oos_access_attempts": (
        _ix("data_snooping_oos_access_attempts", "record_hash", "UNIQUE", ("record_hash",)),
        _ix(
            "data_snooping_oos_access_attempts",
            "attempt_sequence",
            "UNIQUE",
            ("authority_instance_id", "attempt_id", "sequence_no"),
        ),
        _ix(
            "data_snooping_oos_access_attempts",
            "family_start",
            "PARTIAL_UNIQUE",
            ("authority_instance_id", "family_id"),
            ("EQ_LITERAL", "sequence_no", 0),
        ),
        _ix(
            "data_snooping_oos_access_attempts",
            "terminal",
            "PARTIAL_UNIQUE",
            ("authority_instance_id", "attempt_id"),
            ("EQ_LITERAL", "sequence_no", 1),
        ),
        _ix(
            "data_snooping_oos_access_attempts",
            "started_event_tuple",
            "UNIQUE",
            (
                "authority_instance_id",
                "attempt_id",
                "attempt_event_id",
                "family_id",
                "oos_exposure_id",
            ),
        ),
        _ix(
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
        _ix(
            "data_snooping_oos_access_attempts",
            "exposure_tuple",
            "ORDINARY",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
    ),
    "data_snooping_candidate_dispositions": (
        _ix("data_snooping_candidate_dispositions", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_candidate_dispositions", "proposal", "ORDINARY", ("family_id", "proposal_id")),
        _ix("data_snooping_candidate_dispositions", "hypothesis", "ORDINARY", ("family_id", "config_hash")),
        _ix(
            "data_snooping_candidate_dispositions",
            "receipt",
            "ORDINARY",
            ("authority_instance_id", "family_id", "config_hash", "evaluation_receipt_hash"),
        ),
    ),
    "data_snooping_evaluation_batches": (
        _ix("data_snooping_evaluation_batches", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_evaluation_batches", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _ix("data_snooping_evaluation_batches", "attempt", "UNIQUE", ("authority_instance_id", "attempt_id")),
        _ix(
            "data_snooping_evaluation_batches",
            "attempt_receipt_tuple",
            "UNIQUE",
            ("authority_instance_id", "evaluation_batch_id", "family_id", "attempt_id", "record_hash"),
        ),
        _ix(
            "data_snooping_evaluation_batches",
            "batch_family",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id"),
        ),
        _ix(
            "data_snooping_evaluation_batches",
            "benchmark",
            "ORDINARY",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "benchmark_receipt_hash"),
        ),
    ),
    "data_snooping_family_oos_evaluations": (
        _ix("data_snooping_family_oos_evaluations", "record_hash", "UNIQUE", ("record_hash",)),
        _ix(
            "data_snooping_family_oos_evaluations",
            "exact_receipt_link",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "config_hash", "evaluation_receipt_hash"),
        ),
        _ix("data_snooping_family_oos_evaluations", "family_run", "UNIQUE", ("family_id", "evaluation_run_id")),
        _ix(
            "data_snooping_family_oos_evaluations",
            "source",
            "ORDINARY",
            ("family_id", "source_search_id", "source_proposal_id"),
        ),
    ),
    "data_snooping_evaluation_receipts": (
        _ix("data_snooping_evaluation_receipts", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_evaluation_receipts", "family_config", "UNIQUE", ("family_id", "config_hash")),
        _ix(
            "data_snooping_evaluation_receipts",
            "batch_config",
            "UNIQUE",
            ("authority_instance_id", "evaluation_batch_id", "config_hash"),
        ),
        _ix(
            "data_snooping_evaluation_receipts",
            "exact_receipt_link",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "config_hash", "evaluation_receipt_hash"),
        ),
        _ix(
            "data_snooping_evaluation_receipts",
            "disposition_receipt",
            "UNIQUE",
            ("authority_instance_id", "family_id", "config_hash", "evaluation_receipt_hash"),
        ),
    ),
    "data_snooping_benchmark_returns": (
        _ix("data_snooping_benchmark_returns", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_benchmark_returns", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _ix("data_snooping_benchmark_returns", "batch", "UNIQUE", ("authority_instance_id", "evaluation_batch_id")),
        _ix(
            "data_snooping_benchmark_returns",
            "exact_batch_benchmark",
            "UNIQUE",
            ("authority_instance_id", "family_id", "evaluation_batch_id", "benchmark_receipt_hash"),
        ),
    ),
}

C4_CONSUMPTION_EXTENSION_INDEXES = (
    _ix(
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
    ),
)

TIMESTAMP_COLUMNS = {
    "data_snooping_oos_releases": {"released_at_us"},
    "data_snooping_oos_access_attempts": {"event_at_us"},
    "data_snooping_candidate_dispositions": {"recorded_at_us"},
    "data_snooping_evaluation_batches": {"committed_at_us"},
    "data_snooping_family_oos_evaluations": {"linked_at_us"},
    "data_snooping_evaluation_receipts": {"committed_at_us"},
    "data_snooping_benchmark_returns": {"committed_at_us"},
}

ATTEMPT_IDENTITY_COLUMNS = (
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

C8_LATE_GUARD_REQUIREMENTS = tuple(
    (
        f"C8_{table.upper()}_LATE_INSERT_GUARDS_V1",
        table,
        C8_COMPONENT,
        f"c8.{table}.late_insert_guards",
    )
    for table in TABLE_ORDER
)


def _base_checks(
    table: str,
    timestamp_column: str,
) -> dict[str, tuple[set[str], set[object]]]:
    return {
        f"{table}_record_type": (
            {"record_type"},
            {RECORD_TYPES[table]},
        ),
        f"{table}_positive_time": ({timestamp_column}, {0}),
    }


CHECK_REQUIREMENTS = {
    "data_snooping_oos_releases": _base_checks("data_snooping_oos_releases", "released_at_us"),
    "data_snooping_oos_access_attempts": {
        **_base_checks("data_snooping_oos_access_attempts", "event_at_us"),
        "data_snooping_oos_access_attempts_sequence_domain": (
            {"sequence_no"},
            {0, 1},
        ),
        "data_snooping_oos_access_attempts_status_domain": (
            {"status"},
            {"STARTED", "COMPLETED", "INCOMPLETE"},
        ),
        "data_snooping_oos_access_attempts_status_sequence_branch": (
            {
                "status",
                "sequence_no",
                "terminal_execution_attestation_hash",
                "evaluation_batch_receipt_hash",
                "reason_code",
                "prev_record_hash",
                "genesis_record_hash",
            },
            {"STARTED", "COMPLETED", "INCOMPLETE", 0, 1},
        ),
    },
    "data_snooping_candidate_dispositions": {
        **_base_checks("data_snooping_candidate_dispositions", "recorded_at_us"),
        "data_snooping_candidate_dispositions_performance_accessed_domain": (
            {"performance_accessed"},
            {0, 1},
        ),
        "data_snooping_candidate_dispositions_disposition_domain": (
            {"disposition"},
            {
                "PERFORMANCE_EVALUATED",
                "PERFORMANCE_ATTEMPTED_INCOMPLETE",
                "PRE_EVALUATION_INVALID",
                "SECRET_QUARANTINED",
            },
        ),
        "data_snooping_candidate_dispositions_exact_branch": (
            {
                "disposition",
                "config_hash",
                "evaluation_receipt_hash",
                "quarantine_record_id",
                "reason_code",
                "performance_accessed",
            },
            {
                "PERFORMANCE_EVALUATED",
                "PERFORMANCE_ATTEMPTED_INCOMPLETE",
                "PRE_EVALUATION_INVALID",
                "SECRET_QUARANTINED",
                0,
                1,
            },
        ),
        "data_snooping_candidate_dispositions_incomplete_reason_domain": (
            {"disposition", "reason_code"},
            {
                "PERFORMANCE_ATTEMPTED_INCOMPLETE",
                "EVALUATION_EXCEPTION",
                "EVALUATION_TIMEOUT",
                "ATOMIC_COMMIT_FAILED",
                "ARTIFACT_LEASE_INVALID",
                "EXECUTION_ATTESTATION_MISMATCH",
                "RECOVERY_INCOMPLETE",
            },
        ),
        "data_snooping_candidate_dispositions_pre_evaluation_reason_domain": (
            {"disposition", "reason_code"},
            {
                "PRE_EVALUATION_INVALID",
                "INVALID_UTF8",
                "MALFORMED_JSON",
                "DUPLICATE_JSON_KEY",
                "NON_NFC_TEXT",
                "SCHEMA_INVALID",
                "TYPE_INVALID",
                "GUARDRAIL_REJECTED",
            },
        ),
        "data_snooping_candidate_dispositions_quarantine_reason_domain": (
            {"disposition", "reason_code"},
            {
                "SECRET_QUARANTINED",
                "DLP_SECRET_DETECTED",
                "DLP_SCANNER_UNAVAILABLE",
                "DLP_SCANNER_ERROR",
            },
        ),
    },
    "data_snooping_evaluation_batches": {
        **_base_checks("data_snooping_evaluation_batches", "committed_at_us"),
        "data_snooping_evaluation_batches_positive_candidate_count": (
            {"candidate_count"},
            {0},
        ),
    },
    "data_snooping_family_oos_evaluations": _base_checks("data_snooping_family_oos_evaluations", "linked_at_us"),
    "data_snooping_evaluation_receipts": _base_checks("data_snooping_evaluation_receipts", "committed_at_us"),
    "data_snooping_benchmark_returns": _base_checks("data_snooping_benchmark_returns", "committed_at_us"),
}


GUARD_ERROR_CODES = {
    "data_snooping_oos_releases": (
        "OOS_RELEASE_EXACT_STARTED_ATTEMPT",
        "OOS_RELEASE_EXACT_CONSUMPTION",
        "OOS_RELEASE_AFTER_START_AND_CONSUMPTION",
    ),
    "data_snooping_oos_access_attempts": (
        "ATTEMPT_CHAIN_APPEND_NEXT",
        "ATTEMPT_SEQUENCE_GAP",
        "ATTEMPT_IDENTITY_DRIFT",
        "ATTEMPT_SECOND_START",
        "ATTEMPT_AFTER_TERMINAL",
        "ATTEMPT_TERMINAL_TIME_ADVANCES",
        "ATTEMPT_TERMINAL_PREV_HEAD_MATCHES",
        "ATTEMPT_EXPOSURE_TUPLE_MISMATCH",
        "COMPLETED_REQUIRES_COMPLETE_BATCH",
        "INCOMPLETE_FORBIDS_BATCH_SERIES",
    ),
    "data_snooping_candidate_dispositions": (
        "DISPOSITION_PROPOSAL_EXISTS",
        "DISPOSITION_C5_BRANCH_MATCH",
        "DISPOSITION_EVALUATED_RECEIPT_EXACT",
        "DISPOSITION_INCOMPLETE_ATTEMPT_EXISTS",
    ),
    "data_snooping_evaluation_batches": (
        "EVALUATION_BATCH_STARTED_ATTEMPT_EXACT",
        "EVALUATION_BATCH_NO_INCOMPLETE_TERMINAL",
    ),
    "data_snooping_family_oos_evaluations": (
        "FAMILY_OOS_HYPOTHESIS_EXACT",
        "FAMILY_OOS_CLEAN_PROPOSAL_ORIGIN_EXACT",
        "FAMILY_OOS_EVALUATION_EXACT",
        "FAMILY_OOS_RECEIPT_EXACT",
        "FAMILY_OOS_NOT_SEARCH_EVALUATION",
    ),
    "data_snooping_evaluation_receipts": (
        "EVALUATION_RECEIPT_BATCH_ATTEMPT_EXACT",
        "EVALUATION_RECEIPT_HYPOTHESIS_EXACT",
        "EVALUATION_RECEIPT_CANONICAL_SERIES",
    ),
    "data_snooping_benchmark_returns": (
        "BENCHMARK_BATCH_ATTEMPT_EXACT",
        "BENCHMARK_CANONICAL_SERIES",
        "BENCHMARK_CALENDAR_COST_MATCH",
    ),
}


REQUIRED_VERIFICATION_RULES = {
    "data_snooping_oos_releases": {
        "oos_release_row_hash",
        "oos_release_genesis_binding",
        "oos_release_foreign_keys",
        "oos_release_started_attempt",
        "oos_release_exact_consumption_tuple",
        "oos_release_exact_exposure_tuple",
        "oos_release_after_freeze_start_consumption",
        "oos_release_c6_reference_totality",
    },
    "data_snooping_oos_access_attempts": {
        "oos_attempt_row_hash",
        "oos_attempt_genesis_binding",
        "oos_attempt_foreign_keys",
        "oos_attempt_chain",
        "oos_attempt_truth_table",
        "oos_attempt_authority_instance_stability",
        "oos_attempt_identity_stability",
        "oos_attempt_terminal_time_monotonic",
        "oos_attempt_terminal_previous_head",
        "oos_attempt_unique_family_start",
        "oos_attempt_exact_exposure_tuple",
        "oos_attempt_terminal_batch_branch",
        "oos_attempt_performance_access_count",
    },
    "data_snooping_candidate_dispositions": {
        "candidate_disposition_row_hash",
        "candidate_disposition_genesis_binding",
        "candidate_disposition_foreign_keys",
        "candidate_disposition_c5_exact_branch",
        "candidate_disposition_proposal_totality",
        "candidate_disposition_evaluated_receipt_exact",
        "candidate_disposition_incomplete_attempt_totality",
        "candidate_disposition_quarantine_origin",
        "candidate_disposition_pre_proposal_identity_exact",
        "candidate_disposition_duplicate_receipt_identity",
    },
    "data_snooping_evaluation_batches": {
        "evaluation_batch_row_hash",
        "evaluation_batch_genesis_binding",
        "evaluation_batch_foreign_keys",
        "evaluation_batch_exact_candidate_set",
        "evaluation_batch_candidate_receipt_totality",
        "evaluation_batch_exactly_one_benchmark",
        "evaluation_batch_common_calendar_cost_contract",
        "evaluation_batch_started_attempt_identity",
        "evaluation_batch_attempt_terminal_binding",
    },
    "data_snooping_family_oos_evaluations": {
        "family_oos_evaluation_row_hash",
        "family_oos_evaluation_genesis_binding",
        "family_oos_evaluation_foreign_keys",
        "family_oos_evaluation_hypothesis_identity",
        "family_oos_evaluation_clean_proposal_origin",
        "family_oos_evaluation_existing_evaluation",
        "family_oos_evaluation_content_hash",
        "family_oos_evaluation_audit_intent",
        "family_oos_evaluation_receipt_exact",
        "family_oos_evaluation_never_search_evaluation",
    },
    "data_snooping_evaluation_receipts": {
        "evaluation_receipt_row_hash",
        "evaluation_receipt_genesis_binding",
        "evaluation_receipt_foreign_keys",
        "evaluation_receipt_canonical_series_payload",
        "evaluation_receipt_finite_float64_positive_zero",
        "evaluation_receipt_nonnegative_costs_turnovers",
        "evaluation_receipt_strict_period_order",
        "evaluation_receipt_common_frequency_horizon",
        "evaluation_receipt_exact_cost_reconciliation",
        "evaluation_receipt_component_hashes",
        "evaluation_receipt_batch_execution_identity",
        "evaluation_receipt_exact_link",
    },
    "data_snooping_benchmark_returns": {
        "benchmark_return_row_hash",
        "benchmark_return_genesis_binding",
        "benchmark_return_foreign_keys",
        "benchmark_return_canonical_series_payload",
        "benchmark_return_finite_float64_positive_zero",
        "benchmark_return_nonnegative_costs_turnovers",
        "benchmark_return_strict_period_order",
        "benchmark_return_common_frequency_horizon",
        "benchmark_return_exact_cost_reconciliation",
        "benchmark_return_candidate_calendar_cost_match",
        "benchmark_return_batch_execution_identity",
        "benchmark_return_completed_attempt_contract_identity",
    },
}


def _source_expectation(
    kind: str,
    source_id: str | None = None,
    referenced_table: str | None = None,
    referenced_columns: tuple[str, ...] = (),
    required_verifier_codes: tuple[str, ...] = (),
    source_table: str | None = None,
) -> tuple[object, ...]:
    return (
        kind,
        source_id,
        source_table,
        referenced_table,
        referenced_columns,
        required_verifier_codes,
    )


_STANDARD_RULE_TABLES = {
    "oos_release": "data_snooping_oos_releases",
    "oos_attempt": "data_snooping_oos_access_attempts",
    "candidate_disposition": "data_snooping_candidate_dispositions",
    "evaluation_batch": "data_snooping_evaluation_batches",
    "family_oos_evaluation": "data_snooping_family_oos_evaluations",
    "evaluation_receipt": "data_snooping_evaluation_receipts",
    "benchmark_return": "data_snooping_benchmark_returns",
}


C7_STRUCTURAL_SOURCE_EXPECTATIONS = {
    **{
        f"{prefix}_row_hash": _source_expectation("ROW_HASH_SPEC")
        for prefix in _STANDARD_RULE_TABLES
    },
    **{
        f"{prefix}_genesis_binding": _source_expectation("GENESIS_FOREIGN_KEY")
        for prefix in _STANDARD_RULE_TABLES
    },
    **{
        f"{prefix}_foreign_keys": _source_expectation("ALL_FOREIGN_KEYS")
        for prefix in _STANDARD_RULE_TABLES
    },
    "oos_release_started_attempt": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_oos_releases_oos_release_exact_started_attempt",
    ),
    "oos_release_exact_consumption_tuple": _source_expectation(
        "EXACT_FOREIGN_KEY",
        referenced_table="global_oos_exposure_consumptions",
        referenced_columns=(
            "authority_instance_id",
            "oos_exposure_id",
            "family_id",
            "access_attempt_id",
            "record_hash",
        ),
    ),
    "oos_release_c6_reference_totality": _source_expectation(
        "DELEGATED_EXTENSION",
        "C6_OOS_RELEASE_EMBEDDED_REFS_V1",
        required_verifier_codes=(
            "VERIFY_OOS_RELEASE_EXPOSURE_BINDING",
            "VERIFY_START_EXECUTION_ATTESTATION",
            "VERIFY_IMMUTABLE_ARTIFACT_LEASE",
            "VERIFY_OOS_RELEASE_STARTED_EXECUTION_IDENTITY",
        ),
    ),
    "oos_attempt_chain": _source_expectation("CHAIN_SPEC"),
    "oos_attempt_truth_table": _source_expectation(
        "CHECK_CONSTRAINT",
        "data_snooping_oos_access_attempts_status_sequence_branch",
    ),
    "oos_attempt_authority_instance_stability": _source_expectation(
        "GENESIS_FOREIGN_KEY"
    ),
    "oos_attempt_identity_stability": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_oos_access_attempts_attempt_identity_drift",
    ),
    "oos_attempt_terminal_time_monotonic": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_oos_access_attempts_attempt_terminal_time_advances",
    ),
    "oos_attempt_terminal_previous_head": _source_expectation("TERMINAL_HEAD"),
    "oos_attempt_unique_family_start": _source_expectation(
        "UNIQUE_INDEX",
        "partial_unique_data_snooping_oos_access_attempts_family_start",
    ),
    "oos_attempt_exact_exposure_tuple": _source_expectation(
        "EXACT_FOREIGN_KEY",
        referenced_table="global_oos_exposures",
        referenced_columns=("oos_exposure_id", "selection_decision_id", "family_id"),
    ),
    "oos_attempt_terminal_batch_branch": _source_expectation(
        "CHECK_CONSTRAINT",
        "data_snooping_oos_access_attempts_status_sequence_branch",
    ),
    "candidate_disposition_c5_exact_branch": _source_expectation(
        "CHECK_CONSTRAINT",
        "data_snooping_candidate_dispositions_exact_branch",
    ),
    "candidate_disposition_proposal_totality": _source_expectation(
        "EXACT_FOREIGN_KEY",
        referenced_table="data_snooping_proposal_registrations",
        referenced_columns=("proposal_id",),
    ),
    "candidate_disposition_evaluated_receipt_exact": _source_expectation(
        "EXACT_FOREIGN_KEY",
        referenced_table="data_snooping_evaluation_receipts",
        referenced_columns=(
            "authority_instance_id",
            "family_id",
            "config_hash",
            "evaluation_receipt_hash",
        ),
    ),
    "candidate_disposition_incomplete_attempt_totality": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_candidate_dispositions_disposition_incomplete_attempt_exists",
    ),
    "candidate_disposition_quarantine_origin": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_candidate_dispositions_disposition_c5_branch_match",
    ),
    "candidate_disposition_pre_proposal_identity_exact": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_candidate_dispositions_disposition_c5_branch_match",
    ),
    **{
        rule_id: _source_expectation(
            "TRIGGER_POLICY",
            "data_snooping_oos_access_attempts_completed_requires_complete_batch",
            source_table="data_snooping_oos_access_attempts",
        )
        for rule_id in (
            "evaluation_batch_exact_candidate_set",
            "evaluation_batch_candidate_receipt_totality",
            "evaluation_batch_exactly_one_benchmark",
            "evaluation_batch_common_calendar_cost_contract",
            "evaluation_batch_attempt_terminal_binding",
        )
    },
    "evaluation_batch_started_attempt_identity": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_evaluation_batches_evaluation_batch_started_attempt_exact",
    ),
    "family_oos_evaluation_hypothesis_identity": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_family_oos_evaluations_family_oos_hypothesis_exact",
    ),
    "family_oos_evaluation_clean_proposal_origin": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_family_oos_evaluations_family_oos_clean_proposal_origin_exact",
    ),
    "family_oos_evaluation_existing_evaluation": _source_expectation(
        "EXACT_FOREIGN_KEY",
        referenced_table="evaluations",
        referenced_columns=("run_id",),
    ),
    "family_oos_evaluation_content_hash": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_family_oos_evaluations_family_oos_evaluation_exact",
    ),
    "family_oos_evaluation_receipt_exact": _source_expectation(
        "EXACT_FOREIGN_KEY",
        referenced_table="data_snooping_evaluation_receipts",
        referenced_columns=(
            "authority_instance_id",
            "family_id",
            "evaluation_batch_id",
            "config_hash",
            "evaluation_receipt_hash",
        ),
    ),
    "family_oos_evaluation_never_search_evaluation": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_family_oos_evaluations_family_oos_not_search_evaluation",
    ),
    "evaluation_receipt_batch_execution_identity": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_evaluation_receipts_evaluation_receipt_batch_attempt_exact",
    ),
    "evaluation_receipt_exact_link": _source_expectation(
        "EXACT_FOREIGN_KEY",
        referenced_table="data_snooping_family_oos_evaluations",
        referenced_columns=(
            "authority_instance_id",
            "family_id",
            "evaluation_batch_id",
            "config_hash",
            "evaluation_receipt_hash",
        ),
    ),
    "benchmark_return_candidate_calendar_cost_match": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_benchmark_returns_benchmark_calendar_cost_match",
    ),
    "benchmark_return_batch_execution_identity": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_benchmark_returns_benchmark_batch_attempt_exact",
    ),
    "benchmark_return_completed_attempt_contract_identity": _source_expectation(
        "TRIGGER_POLICY",
        "data_snooping_oos_access_attempts_completed_requires_complete_batch",
        source_table="data_snooping_oos_access_attempts",
    ),
    "global_oos_consumption_started_attempt_exact_tuple": _source_expectation(
        "TRIGGER_POLICY",
        "global_oos_exposure_consumptions_global_oos_consumption_requires_exact_started_attempt",
    ),
    "global_oos_consumption_attempt_authority_binding": _source_expectation(
        "TRIGGER_POLICY",
        "global_oos_exposure_consumptions_global_oos_consumption_requires_exact_started_attempt",
    ),
}


C7_NAMED_VERIFIER_RULE_IDS = {
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
}


_STRUCTURAL_COLUMN_OVERRIDES = {
    "oos_attempt_authority_instance_stability": (
        "authority_instance_id",
        "genesis_record_hash",
    ),
    "oos_attempt_unique_family_start": ("authority_instance_id", "family_id"),
}


# These references cannot be represented by an ordinary SQLite FK without
# weakening semantics (variable-length digest sets or a branch lacking an
# attempt ID).  Their exact typed verifier metadata is therefore load-bearing.
NON_FK_TYPED_REFERENCES = {
    "data_snooping_candidate_dispositions": {
        "candidate_disposition_incomplete_attempt_totality": (
            "CROSS_TABLE_TOTALITY",
            ("family_id", "config_hash", "reason_code"),
            "data_snooping_oos_access_attempts",
        ),
        "candidate_disposition_quarantine_origin": (
            "CROSS_TABLE_REFERENCE",
            ("family_id", "proposal_id", "quarantine_record_id"),
            "data_snooping_proposal_registrations",
        ),
        "candidate_disposition_pre_proposal_identity_exact": (
            "CROSS_TABLE_REFERENCE",
            ("family_id", "proposal_id", "disposition", "config_hash"),
            "data_snooping_proposals",
        ),
    },
    "data_snooping_evaluation_batches": {
        "evaluation_batch_exact_candidate_set": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "evaluation_batch_id",
                "candidate_count",
                "frozen_config_set_hash",
                "ordered_candidate_config_hashes_hash",
            ),
            "data_snooping_family_oos_evaluations",
        ),
        "evaluation_batch_candidate_receipt_totality": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "evaluation_batch_id",
                "candidate_count",
                "ordered_candidate_receipt_hashes_hash",
            ),
            "data_snooping_evaluation_receipts",
        ),
        "evaluation_batch_started_attempt_identity": (
            "CROSS_TABLE_REFERENCE",
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
            "data_snooping_oos_access_attempts",
        ),
    },
    "data_snooping_family_oos_evaluations": {
        "family_oos_evaluation_clean_proposal_origin": (
            "CROSS_TABLE_REFERENCE",
            (
                "family_id",
                "source_search_id",
                "source_proposal_id",
                "config_hash",
                "alpha_id",
            ),
            "data_snooping_proposals",
        ),
        "family_oos_evaluation_content_hash": (
            "CROSS_TABLE_REFERENCE",
            ("evaluation_run_id", "evaluation_content_hash"),
            "evaluations",
        ),
        "family_oos_evaluation_audit_intent": (
            "CROSS_TABLE_REFERENCE",
            (
                "evaluation_run_id",
                "evaluation_content_hash",
                "evaluation_audit_intent_hash",
            ),
            "evaluation_audit_outbox",
        ),
    },
    "data_snooping_evaluation_receipts": {
        "evaluation_receipt_batch_execution_identity": (
            "CROSS_TABLE_REFERENCE",
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
            "data_snooping_evaluation_batches",
        ),
    },
    "data_snooping_benchmark_returns": {
        "benchmark_return_batch_execution_identity": (
            "CROSS_TABLE_REFERENCE",
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
            "data_snooping_evaluation_batches",
        ),
        "benchmark_return_completed_attempt_contract_identity": (
            "CROSS_TABLE_REFERENCE",
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
            "data_snooping_oos_access_attempts",
        ),
    },
}


def _schema_module() -> ModuleType:
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == MODULE:
            pytest.fail(f"missing C7 schema-spec module: {MODULE}")
        raise
    return module


def _required(name: str) -> Any:
    value = getattr(_schema_module(), name, None)
    assert value is not None, f"Pass C C7 schema symbol is missing: {name}"
    return value


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise AssertionError(f"schema AST value is not inspectable: {type(value).__name__}")


def _walk(value: Any):
    if is_dataclass(value):
        yield from _walk(asdict(value))
    elif isinstance(value, Mapping):
        payload = dict(value)
        yield payload
        for item in payload.values():
            yield from _walk(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _walk(item)


def _table_map() -> dict[str, Any]:
    component = _required("C7_SCHEMA_COMPONENT")
    return {table.table_name: table for table in component.tables}


def _column_shape(column: Any) -> tuple[object, object, object]:
    assert column.default is None
    return (column.name, _enum_value(column.sql_type), column.nullable)


def _fk_shape(foreign_key: Any) -> tuple[object, ...]:
    return (
        tuple(foreign_key.columns),
        foreign_key.referenced_table,
        tuple(foreign_key.referenced_columns),
        _enum_value(foreign_key.on_update),
        _enum_value(foreign_key.on_delete),
        _enum_value(foreign_key.deferrability),
    )


def _index_shape(index: Any) -> tuple[object, ...]:
    columns = tuple(item.column for item in index.columns)
    assert all(_enum_value(item.direction) == "ASC" for item in index.columns)
    assert all(_enum_value(item.collation) == "BINARY" for item in index.columns)
    predicate = None
    if index.predicate is not None:
        node = _payload(index.predicate)
        left = _payload(node["left"])
        right = _payload(node["right"])
        assert _enum_value(node["operator"]) == "EQ"
        predicate = ("EQ_LITERAL", left["column"], right["value"])
    kind = "PARTIAL_UNIQUE" if index.unique and predicate else "UNIQUE" if index.unique else "ORDINARY"
    return (index.name, kind, columns, predicate)


def test_c7_component_has_exact_table_order_and_private_ast_only() -> None:
    module = _schema_module()
    component = _required("C7_SCHEMA_COMPONENT")
    assert component.component_id == COMPONENT_ID
    assert tuple(component.capabilities) == C7_CAPABILITIES
    assert component.tables is module.C7_AUTHORITY_TABLE_SPECS
    assert tuple(table.table_name for table in component.tables) == TABLE_ORDER
    assert isinstance(module.C7_AUTHORITY_TABLE_SPECS, tuple)
    assert not hasattr(component, "sealed"), "only Pass M may derive a SEALED manifest"


def test_columns_primary_keys_foreign_keys_indexes_and_row_hashes_are_exact() -> None:
    module = _schema_module()
    tables = _table_map()
    for table_name in TABLE_ORDER:
        table = tables[table_name]
        assert isinstance(table, module.AuthorityTableSpec), table_name
        assert tuple(_column_shape(column) for column in table.columns) == COLUMNS[table_name]
        assert tuple(table.primary_key_columns) == PRIMARY_KEYS[table_name]
        assert tuple(_fk_shape(item) for item in table.foreign_keys) == FOREIGN_KEYS[table_name]
        assert tuple(_index_shape(item) for item in table.indexes) == INDEXES[table_name]
        expected_fields = tuple(column[0] for column in COLUMNS[table_name] if column[0] != "record_hash")
        assert tuple(table.row_hash.canonical_fields) == expected_fields
        assert _enum_value(table.row_hash.encoding) == "QUANTOS_CANONICAL_JSON_V1"
        assert _enum_value(table.row_hash.algorithm) == "SHA256"
        assert table.row_hash.version_literal == RECORD_TYPES[table_name]
        assert table.row_hash.record_hash_column == "record_hash"


def test_every_c7_table_is_genesis_bound_append_only_and_timestamped_in_us() -> None:
    for table_name, table in _table_map().items():
        genesis = [
            foreign_key
            for foreign_key in table.foreign_keys
            if tuple(foreign_key.columns) == ("authority_instance_id", "genesis_record_hash")
        ]
        assert len(genesis) == 1, table_name
        assert genesis[0].referenced_table == "family_authority_genesis"
        assert tuple(genesis[0].referenced_columns) == (
            "authority_instance_id",
            "record_hash",
        )
        immutable = {
            _enum_value(trigger.kind): trigger
            for trigger in table.triggers
            if _enum_value(trigger.kind) in {"NO_UPDATE", "NO_DELETE"}
        }
        assert set(immutable) == {"NO_UPDATE", "NO_DELETE"}, table_name
        assert immutable["NO_UPDATE"].name == f"{table_name}_no_update"
        assert immutable["NO_DELETE"].name == f"{table_name}_no_delete"
        assert all(_enum_value(trigger.effect) == "ABORT" for trigger in immutable.values())

        columns = {column.name: column for column in table.columns}
        assert all(columns[name].nullable is False for name in TIMESTAMP_COLUMNS[table_name])
        check_columns = {
            node["column"] for check in table.checks for node in _walk(_payload(check.expression)) if "column" in node
        }
        assert TIMESTAMP_COLUMNS[table_name].issubset(check_columns), table_name


def test_attempt_is_the_only_c7_chain_and_is_exactly_two_events() -> None:
    tables = _table_map()
    assert tuple(name for name in TABLE_ORDER if tables[name].chain is not None) == (
        "data_snooping_oos_access_attempts",
    )
    attempt = tables["data_snooping_oos_access_attempts"]
    chain = attempt.chain
    assert tuple(chain.partition_key_columns) == ("authority_instance_id", "attempt_id")
    assert chain.ordinal_column == "sequence_no"
    assert _enum_value(chain.genesis_rule) == "FAMILY_AUTHORITY_GENESIS_RECORD_HASH"
    assert chain.previous_hash_column == "prev_record_hash"
    assert chain.record_hash_column == "record_hash"
    assert _enum_value(chain.terminal_head_rule) == "LATEST_TERMINAL_RECORD_HASH"

    state_guards = [trigger for trigger in attempt.triggers if trigger.state_machine is not None]
    assert len(state_guards) == 1
    machine = state_guards[0].state_machine
    assert machine.state_column == "status"
    assert tuple(machine.initial_states) == ("STARTED",)
    assert {(transition.previous_state, transition.next_state) for transition in machine.transitions} == {
        ("STARTED", "COMPLETED"),
        ("STARTED", "INCOMPLETE"),
    }
    assert tuple(machine.terminal_states) == ("COMPLETED", "INCOMPLETE")
    assert machine.sequence_column == "sequence_no"
    assert tuple(machine.identity_columns) == ATTEMPT_IDENTITY_COLUMNS


def test_attempt_indexes_are_authority_partitioned_and_freeze_exact_reference_tuples() -> None:
    attempt = _table_map()["data_snooping_oos_access_attempts"]
    shapes = tuple(_index_shape(index) for index in attempt.indexes)
    assert shapes == INDEXES["data_snooping_oos_access_attempts"]
    assert all(
        columns == ("record_hash",) or columns[0] == "authority_instance_id" or kind == "ORDINARY"
        for _, kind, columns, _ in shapes
    )
    assert any(
        kind == "UNIQUE"
        and columns
        == (
            "authority_instance_id",
            "attempt_id",
            "evaluation_batch_id",
            "family_id",
            "evaluation_batch_receipt_hash",
        )
        for _, kind, columns, _ in shapes
    )


def test_release_batch_receipt_and_benchmark_cardinalities_are_exactly_unique() -> None:
    tables = _table_map()
    for table_name in (
        "data_snooping_oos_releases",
        "data_snooping_evaluation_batches",
        "data_snooping_evaluation_receipts",
        "data_snooping_benchmark_returns",
    ):
        assert tuple(_index_shape(item) for item in tables[table_name].indexes) == (INDEXES[table_name])


def test_checks_are_a_complete_exact_catalog_with_closed_branch_literals() -> None:
    for table_name, expected in CHECK_REQUIREMENTS.items():
        actual = {check.name: check for check in _table_map()[table_name].checks}
        assert set(actual) == set(expected), table_name
        for check_name, (expected_columns, expected_literals) in expected.items():
            nodes = tuple(_walk(_payload(actual[check_name].expression)))
            columns = {str(node["column"]) for node in nodes if "column" in node}
            literals = {_enum_value(node["value"]) for node in nodes if set(node) == {"value"}}
            assert columns == expected_columns, check_name
            assert literals == expected_literals, check_name


def test_candidate_disposition_mirrors_c5_fields_and_four_branch_truth_table() -> None:
    disposition = _table_map()["data_snooping_candidate_dispositions"]
    business = tuple(
        column.name
        for column in disposition.columns
        if column.name
        not in {"record_type", "authority_instance_id", "genesis_record_hash", "recorded_at_us", "record_hash"}
    )
    assert business == (
        "family_id",
        "proposal_id",
        "disposition",
        "config_hash",
        "evaluation_receipt_hash",
        "quarantine_record_id",
        "reason_code",
        "performance_accessed",
    )
    branch_rules = {rule.rule_id: _enum_value(rule.kind) for rule in disposition.verification_rules}
    assert branch_rules["candidate_disposition_c5_exact_branch"] == "STATE_BRANCH"
    exact_branch = next(
        check for check in disposition.checks if check.name == "data_snooping_candidate_dispositions_exact_branch"
    )
    literals = {node["value"] for node in _walk(_payload(exact_branch.expression)) if set(node) == {"value"}}
    assert literals == {
        "PERFORMANCE_EVALUATED",
        "PERFORMANCE_ATTEMPTED_INCOMPLETE",
        "PRE_EVALUATION_INVALID",
        "SECRET_QUARANTINED",
        0,
        1,
    }


def test_all_tables_freeze_required_cross_row_verifier_obligations() -> None:
    for table_name, required in REQUIRED_VERIFICATION_RULES.items():
        rules = {rule.rule_id for rule in _table_map()[table_name].verification_rules}
        assert rules == required, table_name


def _all_c7_verification_rules() -> dict[str, Any]:
    rules = {
        rule.rule_id: rule
        for table in _required("C7_SCHEMA_COMPONENT").tables
        for rule in table.verification_rules
    }
    for extension in _required("C7_TABLE_EXTENSIONS"):
        for rule in extension.verification_rules:
            assert rule.rule_id not in rules, rule.rule_id
            rules[rule.rule_id] = rule
    return rules


def test_every_c7_rule_has_one_exact_structural_or_named_verifier_binding() -> None:
    rules = _all_c7_verification_rules()
    structural_ids = set(C7_STRUCTURAL_SOURCE_EXPECTATIONS)
    named_ids = set(C7_NAMED_VERIFIER_RULE_IDS)

    assert len(structural_ids) == 58
    assert len(named_ids) == 18
    assert structural_ids.isdisjoint(named_ids)
    assert set(rules) == structural_ids | named_ids

    for rule_id in sorted(named_ids):
        rule = rules[rule_id]
        assert rule.verifier_code == f"c7.verify_{rule_id}_v1"
        assert rule.structural_source is None

    for rule_id, expected in C7_STRUCTURAL_SOURCE_EXPECTATIONS.items():
        rule = rules[rule_id]
        source = rule.structural_source
        assert rule.verifier_code is None, rule_id
        assert source is not None, rule_id
        assert (
            _enum_value(source.kind),
            source.source_id,
            source.source_table,
            source.referenced_table,
            tuple(source.referenced_columns),
            tuple(source.required_verifier_codes),
        ) == expected, rule_id
        assert tuple(source.required_rule_ids) == (), rule_id
        expected_columns = _STRUCTURAL_COLUMN_OVERRIDES.get(
            rule_id,
            () if source.source_table is not None else tuple(rule.columns),
        )
        assert tuple(source.columns) == expected_columns, rule_id


def test_partial_unique_structural_source_freezes_its_exact_predicate() -> None:
    rule = _all_c7_verification_rules()["oos_attempt_unique_family_start"]
    source = rule.structural_source
    assert source is not None
    assert _enum_value(source.kind) == "UNIQUE_INDEX"
    assert source.source_id == "partial_unique_data_snooping_oos_access_attempts_family_start"
    assert tuple(source.columns) == ("authority_instance_id", "family_id")

    attempt = _table_map()["data_snooping_oos_access_attempts"]
    index = next(item for item in attempt.indexes if item.name == source.source_id)
    assert _index_shape(index) == (
        "partial_unique_data_snooping_oos_access_attempts_family_start",
        "PARTIAL_UNIQUE",
        ("authority_instance_id", "family_id"),
        ("EQ_LITERAL", "sequence_no", 0),
    )


@pytest.mark.parametrize(
    ("table_name", "error_code", "rule_prefix"),
    (
        (
            "data_snooping_evaluation_receipts",
            "EVALUATION_RECEIPT_CANONICAL_SERIES",
            "evaluation_receipt_",
        ),
        (
            "data_snooping_benchmark_returns",
            "BENCHMARK_CANONICAL_SERIES",
            "benchmark_return_",
        ),
    ),
)
def test_nonempty_series_guard_is_not_misrepresented_as_content_verification(
    table_name: str,
    error_code: str,
    rule_prefix: str,
) -> None:
    rules = _all_c7_verification_rules()
    series_rule_ids = {
        rule_id
        for rule_id in C7_NAMED_VERIFIER_RULE_IDS
        if rule_id.startswith(rule_prefix)
    }
    assert series_rule_ids
    assert all(rules[rule_id].structural_source is None for rule_id in series_rule_ids)

    guard = _insert_guard(table_name, error_code)
    structural_source_ids = {
        source.source_id
        for rule in rules.values()
        if (source := rule.structural_source) is not None
        and source.source_id is not None
    }
    assert guard.name not in structural_source_ids

    expression = _payload(guard.violation_when)
    left = _payload(expression["left"])
    right = _payload(expression["right"])
    assert _enum_value(expression["operator"]) == "EQ"
    assert left["column"] == "series_payload_json"
    assert _enum_value(left["source"]) == "NEW"
    assert right == {"value": ""}


def test_every_non_fk_same_database_reference_has_an_exact_typed_reason() -> None:
    for table_name, expected in NON_FK_TYPED_REFERENCES.items():
        rules = {rule.rule_id: rule for rule in _table_map()[table_name].verification_rules}
        for rule_id, (kind, columns, referenced_table) in expected.items():
            rule = rules[rule_id]
            assert _enum_value(rule.kind) == kind, rule_id
            assert tuple(rule.columns) == columns, rule_id
            assert rule.referenced_table == referenced_table, rule_id


def test_all_insert_guards_are_complete_exact_and_fail_closed() -> None:
    for table_name, expected_codes in GUARD_ERROR_CODES.items():
        guards = tuple(
            trigger for trigger in _table_map()[table_name].triggers if _enum_value(trigger.kind) == "INSERT_GUARD"
        )
        assert tuple(guard.error_code for guard in guards) == expected_codes, table_name
        assert all(guard.violation_when is not None for guard in guards), table_name
        assert all(_enum_value(guard.event) == "INSERT" for guard in guards), table_name
        assert all(_enum_value(guard.timing) == "BEFORE" for guard in guards), table_name
        assert all(_enum_value(guard.effect) == "ABORT" for guard in guards), table_name

    attempt = _table_map()["data_snooping_oos_access_attempts"]

    incomplete = next(
        trigger for trigger in attempt.triggers if trigger.error_code == "INCOMPLETE_FORBIDS_BATCH_SERIES"
    )
    relation_tables = {node["table"] for node in _walk(_payload(incomplete.violation_when)) if "table" in node}
    assert {
        "data_snooping_evaluation_batches",
        "data_snooping_family_oos_evaluations",
        "data_snooping_evaluation_receipts",
        "data_snooping_benchmark_returns",
    } == relation_tables


def test_series_are_canonical_payloads_not_shadow_period_business_tables() -> None:
    table_names = set(_table_map())
    assert not any(
        fragment in table_name
        for table_name in table_names
        for fragment in ("period_rows", "return_rows", "series_rows", "turnover_rows")
    )
    for table_name in (
        "data_snooping_evaluation_receipts",
        "data_snooping_benchmark_returns",
    ):
        columns = {column.name for column in _table_map()[table_name].columns}
        assert {
            "series_payload_json",
            "ordered_period_keys_hash",
            "gross_returns_hash",
            "costs_hash",
            "net_returns_hash",
            "turnovers_hash",
            "cost_contract_hash",
            "cost_model_input_hash",
            "series_hash",
        }.issubset(columns)


def test_c4_consumption_extension_is_guarded_totality_not_an_invalid_fk() -> None:
    extensions = tuple(_required("C7_TABLE_EXTENSIONS"))
    extension = next(item for item in extensions if item.extension_id == C4_CONSUMPTION_EXTENSION)
    assert extension.target_table == "global_oos_exposure_consumptions"
    assert tuple(extension.foreign_keys) == (), "attempt_id has STARTED and terminal rows and is not a unique FK target"
    assert extension.column_insertion is None
    assert extension.row_hash_field_insertion is None
    assert tuple(_index_shape(index) for index in extension.indexes) == (C4_CONSUMPTION_EXTENSION_INDEXES)
    assert tuple(extension.checks) == ()
    assert tuple(trigger.error_code for trigger in extension.triggers) == (
        "GLOBAL_OOS_CONSUMPTION_REQUIRES_EXACT_STARTED_ATTEMPT",
    )
    rules = {rule.rule_id: rule for rule in extension.verification_rules}
    assert set(rules) == {
        "global_oos_consumption_started_attempt_exact_tuple",
        "global_oos_consumption_attempt_authority_binding",
    }
    exact = rules["global_oos_consumption_started_attempt_exact_tuple"]
    assert _enum_value(exact.kind) == "CROSS_TABLE_REFERENCE"
    assert exact.referenced_table == "data_snooping_oos_access_attempts"
    assert tuple(exact.columns) == (
        "authority_instance_id",
        "access_attempt_id",
        "family_id",
        "selection_decision_id",
        "oos_exposure_id",
    )
    assert tuple(item.column for item in extension.indexes[0].columns) == (
        "authority_instance_id",
        "oos_exposure_id",
        "family_id",
        "access_attempt_id",
        "record_hash",
    )


def test_c7_requires_c6_release_refs_and_all_pending_c8_late_guards_without_dependencies() -> None:
    component = _required("C7_SCHEMA_COMPONENT")
    requirements = tuple(
        (
            item.extension_id,
            item.target_table,
            item.provider_component_id,
            item.required_capability,
        )
        for item in component.required_extensions
    )
    assert requirements == (
        (
            C6_RELEASE_EXTENSION,
            "data_snooping_oos_releases",
            C6_COMPONENT,
            "c6.oos_release.embedded_refs",
        ),
        *C8_LATE_GUARD_REQUIREMENTS,
    )
    assert tuple(_required("C7_REQUIRED_EXTENSION_SPECS")) == tuple(component.required_extensions)

    dependencies = {
        dependency.component_id: (
            _enum_value(dependency.kind),
            tuple(dependency.required_capabilities),
        )
        for dependency in component.dependencies
    }
    assert dependencies == {
        C4_COMPONENT: (
            "CROSS_TABLE_REFERENCE",
            (
                "c4.questions_lineage_scope_decisions_exposures.core",
                "c4.global_oos_exposure.core",
            ),
        ),
        C5_COMPONENT: (
            "CROSS_TABLE_REFERENCE",
            ("c5.proposals_hypotheses.dispositions",),
        ),
    }
    assert C6_COMPONENT not in dependencies
    assert C8_COMPONENT not in dependencies

    c5_component = importlib.import_module(
        "services.research.experiment_registry.family_schema_c5_c6"
    ).C5_SCHEMA_COMPONENT
    assert C5_COMPONENT == c5_component.component_id
    assert set(dependencies[C5_COMPONENT][1]).issubset(c5_component.capabilities)


def test_c6_release_contract_fields_are_delegated_once_not_double_owned() -> None:
    component = _required("C7_SCHEMA_COMPONENT")
    required = tuple(
        (
            item.contract_type,
            tuple(item.field_names),
            item.provider_component_id,
            item.required_capability,
        )
        for item in component.required_contract_surfaces
    )
    assert required == (
        (
            "OosReleaseReceipt",
            C6_RELEASE_CONTRACT_FIELDS,
            C6_COMPONENT,
            "c6.oos_release.embedded_refs",
        ),
    )
    assert tuple(_required("C7_REQUIRED_CONTRACT_SURFACES")) == tuple(component.required_contract_surfaces)

    release_owned = {item.field_name for item in component.coverage if item.contract_type == "OosReleaseReceipt"}
    release_required = set(C6_RELEASE_CONTRACT_FIELDS)
    release_contract = getattr(
        importlib.import_module("quantos.contracts.research"),
        "OosReleaseReceipt",
    )
    assert release_owned.isdisjoint(release_required)
    assert release_owned | release_required == set(release_contract.model_fields)


def test_c7_contract_coverage_is_total_typed_and_pass_m_ready() -> None:
    expected = {
        "EvaluationSeriesReceipt": {
            "ordered_period_keys",
            "gross_returns",
            "costs",
            "net_returns",
            "turnovers",
            "ordered_period_keys_hash",
            "gross_returns_hash",
            "costs_hash",
            "net_returns_hash",
            "turnovers_hash",
            "cost_contract_hash",
            "cost_model_input_hash",
            "series_hash",
        },
        "CandidateReturnSeries": {
            "family_id",
            "evaluation_batch_id",
            "config_hash",
            "alpha_id",
            "evaluation_run_id",
            "evaluation_content_hash",
            "evaluation_audit_intent_hash",
            "source_search_id",
            "source_proposal_id",
            "series",
            "evaluation_receipt_hash",
        },
        "BenchmarkReturnSeries": {
            "family_id",
            "evaluation_batch_id",
            "benchmark_id",
            "benchmark_contract_hash",
            "series",
            "benchmark_receipt_hash",
        },
        "FamilyEvaluationBatchReceipt": {
            "record_type",
            "authority_instance_id",
            "genesis_record_hash",
            "family_id",
            "attempt_id",
            "evaluation_batch_id",
            "frozen_config_set_hash",
            "candidate_count",
            "candidate_order",
            "candidate_receipt_hashes",
            "benchmark_receipt_hash",
            "ordered_period_keys_hash",
            "cost_contract_hash",
            "start_execution_attestation_hash",
            "terminal_execution_attestation_hash",
            "immutable_artifact_lease_hash",
            "committed_at_us",
            "batch_receipt_hash",
        },
        "OosAccessAttempt": {
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
        },
        "OosReleaseReceipt": {
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
            "authority_head",
        },
        "CandidateDisposition": {
            "family_id",
            "proposal_id",
            "disposition",
            "config_hash",
            "evaluation_receipt_hash",
            "quarantine_record_id",
            "reason_code",
            "performance_accessed",
        },
    }
    coverage: dict[str, dict[str, Any]] = {}
    for item in _required("C7_SCHEMA_COMPONENT").coverage:
        coverage.setdefault(item.contract_type, {})[item.field_name] = item
    assert coverage, "Pass M cannot seal a C7 component with empty coverage"
    assert set(coverage) == set(expected)
    assert {contract_type: set(coverage[contract_type]) for contract_type in expected} == expected

    canonical_payload = {
        ("EvaluationSeriesReceipt", field)
        for field in (
            "ordered_period_keys",
            "gross_returns",
            "costs",
            "net_returns",
            "turnovers",
        )
    } | {
        ("CandidateReturnSeries", "series"),
        ("BenchmarkReturnSeries", "series"),
    }
    explicit_derivations = {
        ("FamilyEvaluationBatchReceipt", "candidate_order"): "c7.batch_candidate_order_from_receipts",
        ("FamilyEvaluationBatchReceipt", "candidate_receipt_hashes"): "c7.batch_candidate_receipts_from_rows",
        ("OosReleaseReceipt", "authority_head"): "c7.oos_release_latest_record_hash",
    }
    table_for_contract = {
        "EvaluationSeriesReceipt": "data_snooping_evaluation_receipts",
        "CandidateReturnSeries": "data_snooping_family_oos_evaluations",
        "BenchmarkReturnSeries": "data_snooping_benchmark_returns",
        "FamilyEvaluationBatchReceipt": "data_snooping_evaluation_batches",
        "OosAccessAttempt": "data_snooping_oos_access_attempts",
        "OosReleaseReceipt": "data_snooping_oos_releases",
        "CandidateDisposition": "data_snooping_candidate_dispositions",
    }
    canonical_payload_target = {
        ("CandidateReturnSeries", "series"): (
            "data_snooping_evaluation_receipts",
            "series_payload_json",
        ),
    }
    target_alias = {
        ("CandidateReturnSeries", "evaluation_run_id"): "evaluation_run_id",
        ("CandidateReturnSeries", "evaluation_receipt_hash"): "evaluation_receipt_hash",
        ("FamilyEvaluationBatchReceipt", "batch_receipt_hash"): "record_hash",
        ("OosAccessAttempt", "previous_event_hash"): "prev_record_hash",
        ("OosAccessAttempt", "event_hash"): "record_hash",
        ("OosReleaseReceipt", "release_record_hash"): "record_hash",
    }

    for contract_type, fields in expected.items():
        for field_name in fields:
            item = coverage[contract_type][field_name]
            key = (contract_type, field_name)
            kind = _enum_value(item.kind)
            if key in canonical_payload:
                assert kind == "CANONICAL_PAYLOAD", key
                expected_table, expected_field = canonical_payload_target.get(
                    key,
                    (table_for_contract[contract_type], "series_payload_json"),
                )
                assert item.table_name == expected_table
                assert item.target_field == expected_field
                assert item.derivation_id is None
            elif key in explicit_derivations:
                assert kind == "NON_PERSISTED_DERIVATION", key
                assert item.table_name is None and item.target_field is None
                assert item.derivation_id == explicit_derivations[key]
            else:
                assert kind == "COLUMN", key
                assert item.table_name == table_for_contract[contract_type]
                assert item.target_field == target_alias.get(key, field_name)
                assert item.derivation_id is None


def _insert_guard(table_name: str, error_code: str) -> Any:
    matches = tuple(
        trigger
        for trigger in _table_map()[table_name].triggers
        if _enum_value(trigger.kind) == "INSERT_GUARD" and trigger.error_code == error_code
    )
    assert len(matches) == 1, f"missing unique {table_name}.{error_code} guard"
    return matches[0]


def _relation_nodes(expression: Any, related_table: str | None = None) -> tuple[dict[str, Any], ...]:
    payload = dict(expression) if isinstance(expression, Mapping) else _payload(expression)
    relations = tuple(node for node in _walk(payload) if "table" in node and "bindings" in node and "negated" in node)
    if related_table is None:
        return relations
    return tuple(node for node in relations if node["table"] == related_table)


def _aggregate_nodes(expression: Any, related_table: str | None = None) -> tuple[dict[str, Any], ...]:
    payload = dict(expression) if isinstance(expression, Mapping) else _payload(expression)
    aggregates = tuple(
        node for node in _walk(payload) if "table" in node and "bindings" in node and "aggregate" in node
    )
    if related_table is None:
        return aggregates
    return tuple(node for node in aggregates if node["table"] == related_table)


def _relation_binding_columns(relation: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_binding in relation["bindings"]:
        binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else _payload(raw_binding)
        raw_value = binding["value"]
        value = dict(raw_value) if isinstance(raw_value, Mapping) else _payload(raw_value)
        assert _enum_value(value["source"]) == "NEW"
        result[str(binding["related_column"])] = str(value["column"])
    return result


def _expression_literals(expression: Any) -> set[object]:
    return {_enum_value(node["value"]) for node in _walk(expression) if set(node) == {"value"}}


def _expression_columns(expression: Any) -> set[tuple[str, object]]:
    return {
        (str(node["column"]), _enum_value(node["source"]))
        for node in _walk(expression)
        if "column" in node and "source" in node
    }


def _expression_null_tests(expression: Any) -> set[tuple[str, object, object]]:
    tests: set[tuple[str, object, object]] = set()
    for node in _walk(expression):
        if _enum_value(node.get("operator")) not in {"IS_NULL", "IS_NOT_NULL"}:
            continue
        raw_value = node.get("value")
        if not isinstance(raw_value, Mapping) or "column" not in raw_value:
            continue
        tests.add(
            (
                str(raw_value["column"]),
                _enum_value(raw_value["source"]),
                _enum_value(node["operator"]),
            )
        )
    return tests


def test_atomic_batch_guards_have_one_satisfiable_insert_topology() -> None:
    child_tables = {
        "data_snooping_family_oos_evaluations",
        "data_snooping_evaluation_receipts",
        "data_snooping_benchmark_returns",
    }
    batch = _table_map()["data_snooping_evaluation_batches"]
    batch_guards = tuple(trigger for trigger in batch.triggers if _enum_value(trigger.kind) == "INSERT_GUARD")
    batch_dependencies = {
        relation["table"] for guard in batch_guards for relation in _relation_nodes(guard.violation_when)
    }
    assert batch_dependencies.isdisjoint(child_tables), (
        "a BEFORE INSERT batch header must not require rows that can only be inserted after the batch"
    )

    assert {
        relation["table"]
        for relation in _relation_nodes(
            _insert_guard(
                "data_snooping_evaluation_receipts",
                "EVALUATION_RECEIPT_BATCH_ATTEMPT_EXACT",
            ).violation_when
        )
    } >= {"data_snooping_evaluation_batches"}
    assert {
        relation["table"]
        for relation in _relation_nodes(
            _insert_guard(
                "data_snooping_family_oos_evaluations",
                "FAMILY_OOS_RECEIPT_EXACT",
            ).violation_when
        )
    } >= {"data_snooping_evaluation_receipts"}
    assert {
        relation["table"]
        for relation in _relation_nodes(
            _insert_guard(
                "data_snooping_benchmark_returns",
                "BENCHMARK_BATCH_ATTEMPT_EXACT",
            ).violation_when
        )
    } >= {"data_snooping_evaluation_batches"}

    completed = _insert_guard(
        "data_snooping_oos_access_attempts",
        "COMPLETED_REQUIRES_COMPLETE_BATCH",
    )
    completed_dependencies = {relation["table"] for relation in _relation_nodes(completed.violation_when)}
    assert {
        "data_snooping_evaluation_batches",
        *child_tables,
    }.issubset(completed_dependencies), "COMPLETED is the only write-time totality seal"

    batch_rules = {rule.rule_id: rule for rule in batch.verification_rules}
    for rule_id in (
        "evaluation_batch_exact_candidate_set",
        "evaluation_batch_candidate_receipt_totality",
        "evaluation_batch_exactly_one_benchmark",
    ):
        assert _enum_value(batch_rules[rule_id].kind) == "CROSS_TABLE_TOTALITY"


def test_completed_guard_is_an_exact_renderable_batch_totality_seal() -> None:
    completed = _insert_guard(
        "data_snooping_oos_access_attempts",
        "COMPLETED_REQUIRES_COMPLETE_BATCH",
    )
    aggregates = _aggregate_nodes(completed.violation_when)
    assert len(aggregates) == 7

    def matching(table: str, kind: str, value: str | None) -> dict[str, Any]:
        matches = tuple(
            node
            for node in aggregates
            if node["table"] == table and _enum_value(node["aggregate"]) == kind and node["value"] == value
        )
        assert len(matches) == 1, (table, kind, value)
        return matches[0]

    count_specs = (
        ("data_snooping_family_oos_evaluations", "COUNT_ROWS", None),
        ("data_snooping_family_oos_evaluations", "COUNT_DISTINCT", "config_hash"),
        ("data_snooping_evaluation_receipts", "COUNT_ROWS", None),
        ("data_snooping_evaluation_receipts", "COUNT_DISTINCT", "config_hash"),
    )
    for table, kind, value in count_specs:
        node = matching(table, kind, value)
        assert _enum_value(node["source"]) == "RELATED"
        assert _enum_value(node["operator"]) == "NE"
        assert node["order"] == ()
        expected = node["expected"]
        assert expected["table"] == "data_snooping_evaluation_batches"
        assert expected["value"] == "candidate_count"
        assert _enum_value(expected["source"]) == "RELATED"
        assert _enum_value(expected["cardinality"]) == "EXACTLY_ONE"
        assert _enum_value(expected["key_requirement"]) == "PRIMARY_KEY_OR_EXACT_UNIQUE"
        expected_bindings = {binding["related_column"]: binding["value"]["column"] for binding in expected["bindings"]}
        assert expected_bindings["record_hash"] == "evaluation_batch_receipt_hash"
        assert expected_bindings["attempt_id"] == "attempt_id"

    config_hash = matching(
        "data_snooping_family_oos_evaluations",
        "CANONICAL_ORDERED_VALUE_HASH",
        "config_hash",
    )
    receipt_hash = matching(
        "data_snooping_evaluation_receipts",
        "CANONICAL_ORDERED_VALUE_HASH",
        "evaluation_receipt_hash",
    )
    for node, expected_value in (
        (config_hash, "ordered_candidate_config_hashes_hash"),
        (receipt_hash, "ordered_candidate_receipt_hashes_hash"),
    ):
        assert _enum_value(node["operator"]) == "NE"
        assert node["expected"]["value"] == expected_value
        assert node["order"]
        assert all(_enum_value(item["direction"]) == "ASC" for item in node["order"])
        assert all(_enum_value(item["collation"]) == "BINARY" for item in node["order"])

    benchmark = matching("data_snooping_benchmark_returns", "COUNT_ROWS", None)
    assert benchmark["expected"]["value"] == 1
    assert _enum_value(benchmark["operator"]) == "NE"

    # Mutation killers: with candidate_count=2, one evaluation or one receipt
    # makes COUNT_ROWS != the exact batch header and therefore fires the guard.
    # No verifier-only rule can satisfy this assertion because these are typed
    # BEFORE INSERT predicate nodes consumed by Pass M's renderer.
    assert all(
        matching(table, "COUNT_ROWS", None)["expected"]["value"] == "candidate_count"
        for table in (
            "data_snooping_family_oos_evaluations",
            "data_snooping_evaluation_receipts",
        )
    )


def test_batch_children_and_terminal_attempt_bind_the_same_execution_identity() -> None:
    batch_started = _relation_nodes(
        _insert_guard(
            "data_snooping_evaluation_batches",
            "EVALUATION_BATCH_STARTED_ATTEMPT_EXACT",
        ).violation_when,
        "data_snooping_oos_access_attempts",
    )
    assert len(batch_started) == 1
    assert {
        "authority_instance_id": "authority_instance_id",
        "attempt_id": "attempt_id",
        "evaluation_batch_id": "evaluation_batch_id",
        "family_id": "family_id",
        "frozen_config_set_hash": "frozen_config_set_hash",
        "ordered_period_calendar_hash": "ordered_period_keys_hash",
        "cost_contract_hash": "cost_contract_hash",
        "start_execution_attestation_hash": "start_execution_attestation_hash",
        "immutable_artifact_lease_hash": "immutable_artifact_lease_hash",
    }.items() <= _relation_binding_columns(batch_started[0]).items()
    assert "STARTED" in _expression_literals(batch_started[0])

    receipt_batch = _relation_nodes(
        _insert_guard(
            "data_snooping_evaluation_receipts",
            "EVALUATION_RECEIPT_BATCH_ATTEMPT_EXACT",
        ).violation_when,
        "data_snooping_evaluation_batches",
    )
    assert len(receipt_batch) == 1
    common_child_identity = {
        "authority_instance_id": "authority_instance_id",
        "family_id": "family_id",
        "evaluation_batch_id": "evaluation_batch_id",
        "ordered_period_keys_hash": "ordered_period_keys_hash",
        "cost_contract_hash": "cost_contract_hash",
        "start_execution_attestation_hash": "start_execution_attestation_hash",
        "terminal_execution_attestation_hash": "terminal_execution_attestation_hash",
        "immutable_artifact_lease_hash": "immutable_artifact_lease_hash",
    }
    assert common_child_identity.items() <= _relation_binding_columns(receipt_batch[0]).items()

    benchmark_batch = _relation_nodes(
        _insert_guard(
            "data_snooping_benchmark_returns",
            "BENCHMARK_BATCH_ATTEMPT_EXACT",
        ).violation_when,
        "data_snooping_evaluation_batches",
    )
    assert len(benchmark_batch) == 1
    assert {
        **common_child_identity,
        "benchmark_receipt_hash": "benchmark_receipt_hash",
    }.items() <= _relation_binding_columns(benchmark_batch[0]).items()

    completed = _insert_guard(
        "data_snooping_oos_access_attempts",
        "COMPLETED_REQUIRES_COMPLETE_BATCH",
    )
    completed_batch = _relation_nodes(
        completed.violation_when,
        "data_snooping_evaluation_batches",
    )
    assert len(completed_batch) == 1
    assert {
        "authority_instance_id": "authority_instance_id",
        "attempt_id": "attempt_id",
        "evaluation_batch_id": "evaluation_batch_id",
        "family_id": "family_id",
        "record_hash": "evaluation_batch_receipt_hash",
        "frozen_config_set_hash": "frozen_config_set_hash",
        "ordered_period_keys_hash": "ordered_period_calendar_hash",
        "cost_contract_hash": "cost_contract_hash",
        "start_execution_attestation_hash": "start_execution_attestation_hash",
        "terminal_execution_attestation_hash": "terminal_execution_attestation_hash",
        "immutable_artifact_lease_hash": "immutable_artifact_lease_hash",
    }.items() <= _relation_binding_columns(completed_batch[0]).items()

    completed_benchmark = _relation_nodes(
        completed.violation_when,
        "data_snooping_benchmark_returns",
    )
    assert len(completed_benchmark) == 1
    assert {
        "authority_instance_id": "authority_instance_id",
        "family_id": "family_id",
        "evaluation_batch_id": "evaluation_batch_id",
        "benchmark_contract_hash": "benchmark_contract_hash",
        "ordered_period_keys_hash": "ordered_period_calendar_hash",
        "cost_contract_hash": "cost_contract_hash",
        "start_execution_attestation_hash": "start_execution_attestation_hash",
        "terminal_execution_attestation_hash": "terminal_execution_attestation_hash",
        "immutable_artifact_lease_hash": "immutable_artifact_lease_hash",
    }.items() <= _relation_binding_columns(completed_benchmark[0]).items()

    terminal_rule = {
        rule.rule_id: rule for rule in _table_map()["data_snooping_evaluation_batches"].verification_rules
    }["evaluation_batch_attempt_terminal_binding"]
    assert _enum_value(terminal_rule.kind) == "CROSS_TABLE_REFERENCE"
    assert terminal_rule.referenced_table == "data_snooping_oos_access_attempts"
    assert tuple(terminal_rule.columns) == (
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
    )


def test_c6_release_extension_binds_started_execution_identity_not_only_external_validity() -> None:
    c6 = importlib.import_module("services.research.experiment_registry.family_schema_c5_c6")
    release = next(extension for extension in c6.C6_TABLE_EXTENSIONS if extension.extension_id == C6_RELEASE_EXTENSION)
    required_bindings = {
        "authority_instance_id": "authority_instance_id",
        "attempt_id": "attempt_id",
        "attempt_event_id": "started_attempt_event_id",
        "family_id": "family_id",
        "oos_exposure_id": "oos_exposure_id",
        "start_execution_attestation_hash": "start_execution_attestation_hash",
        "immutable_artifact_lease_hash": "immutable_artifact_lease_hash",
    }
    matching_guards: list[Any] = []
    for guard in release.triggers:
        assert _enum_value(guard.kind) == "INSERT_GUARD"
        assert _enum_value(guard.timing) == "BEFORE"
        assert _enum_value(guard.effect) == "ABORT"
        relations = _relation_nodes(
            guard.violation_when,
            "data_snooping_oos_access_attempts",
        )
        if any(required_bindings.items() <= _relation_binding_columns(item).items() for item in relations):
            matching_guards.append(guard)
    assert len(matching_guards) == 1, "C6 release extension must bind its start/lease hashes to C7 STARTED"
    assert {"STARTED", 0}.issubset(_expression_literals(matching_guards[0].violation_when))

    exact_rules = tuple(
        rule
        for rule in release.verification_rules
        if _enum_value(rule.kind) == "CROSS_TABLE_REFERENCE"
        and rule.referenced_table == "data_snooping_oos_access_attempts"
    )
    assert len(exact_rules) == 1
    assert tuple(exact_rules[0].columns) == (
        "authority_instance_id",
        "attempt_id",
        "started_attempt_event_id",
        "family_id",
        "oos_exposure_id",
        "start_execution_attestation_hash",
        "immutable_artifact_lease_hash",
    )


def test_family_oos_evaluation_origin_is_one_exact_accepted_terminal_result() -> None:
    origin = _insert_guard(
        "data_snooping_family_oos_evaluations",
        "FAMILY_OOS_CLEAN_PROPOSAL_ORIGIN_EXACT",
    )
    relations = _relation_nodes(origin.violation_when, "data_snooping_proposals")
    assert len(relations) == 1
    relation = relations[0]
    assert relation["negated"] is True
    assert _relation_binding_columns(relation) == {
        "family_id": "family_id",
        "search_id": "source_search_id",
        "proposal_id": "source_proposal_id",
        "config_hash": "config_hash",
        "alpha_id": "alpha_id",
    }
    assert "ACCEPTED" in _expression_literals(relation["predicate"])
    assert ("parse_status", "RELATED") in _expression_columns(relation["predicate"])


def test_pre_disposition_requires_clean_registration_and_invalid_terminal_result() -> None:
    branch_guard = _insert_guard(
        "data_snooping_candidate_dispositions",
        "DISPOSITION_C5_BRANCH_MATCH",
    )
    payload = _payload(branch_guard.violation_when)
    pre_branches = tuple(
        operand for operand in payload["operands"] if "PRE_EVALUATION_INVALID" in _expression_literals(operand)
    )
    assert len(pre_branches) == 1
    registration_relations = _relation_nodes(
        pre_branches[0],
        "data_snooping_proposal_registrations",
    )
    assert len(registration_relations) == 1
    registration = registration_relations[0]
    assert registration["negated"] is True
    assert _relation_binding_columns(registration) == {
        "family_id": "family_id",
        "proposal_id": "proposal_id",
    }
    assert "CLEAN" in _expression_literals(registration["predicate"])
    assert ("proposal_branch", "RELATED") in _expression_columns(
        registration["predicate"]
    )

    proposal_relations = _relation_nodes(pre_branches[0], "data_snooping_proposals")
    assert len(proposal_relations) == 1
    assert any(
        _enum_value(node.get("operator")) == "OR"
        and len(_relation_nodes(node, "data_snooping_proposal_registrations"))
        == 1
        and len(_relation_nodes(node, "data_snooping_proposals")) == 1
        for node in _walk(pre_branches[0])
    )
    proposal = proposal_relations[0]
    assert proposal["negated"] is True
    assert _relation_binding_columns(proposal) == {
        "family_id": "family_id",
        "proposal_id": "proposal_id",
    }
    assert "INVALID" in _expression_literals(proposal["predicate"])
    assert ("parse_status", "RELATED") in _expression_columns(proposal["predicate"])
    assert {
        ("canonical_proposal_json", "RELATED", "IS_NULL"),
        ("canonical_proposal_hash", "RELATED", "IS_NULL"),
        ("config_hash", "RELATED", "IS_NULL"),
        ("alpha_id", "RELATED", "IS_NULL"),
    }.issubset(_expression_null_tests(proposal["predicate"]))

    disposition = _table_map()["data_snooping_candidate_dispositions"]
    checks = {check.name: check for check in disposition.checks}
    for check_name in (
        "data_snooping_candidate_dispositions_incomplete_reason_domain",
        "data_snooping_candidate_dispositions_pre_evaluation_reason_domain",
        "data_snooping_candidate_dispositions_quarantine_reason_domain",
    ):
        assert check_name in checks
        assert "reason_code" in {
            node["column"] for node in _walk(_payload(checks[check_name].expression)) if "column" in node
        }


def test_disposition_branches_bind_raw_registration_and_terminal_parse_state() -> None:
    branch_guard = _insert_guard(
        "data_snooping_candidate_dispositions",
        "DISPOSITION_C5_BRANCH_MATCH",
    )
    branches = tuple(_payload(branch_guard.violation_when)["operands"])

    for disposition in (
        "PERFORMANCE_EVALUATED",
        "PERFORMANCE_ATTEMPTED_INCOMPLETE",
    ):
        branch = next(
            operand for operand in branches if disposition in _expression_literals(operand)
        )
        registrations = _relation_nodes(
            branch,
            "data_snooping_proposal_registrations",
        )
        proposals = _relation_nodes(branch, "data_snooping_proposals")
        assert len(registrations) == len(proposals) == 1
        assert any(
            _enum_value(node.get("operator")) == "OR"
            and len(
                _relation_nodes(node, "data_snooping_proposal_registrations")
            )
            == 1
            and len(_relation_nodes(node, "data_snooping_proposals")) == 1
            for node in _walk(branch)
        )
        assert "CLEAN" in _expression_literals(registrations[0]["predicate"])
        assert "ACCEPTED" in _expression_literals(proposals[0]["predicate"])
        assert _relation_binding_columns(proposals[0])["config_hash"] == "config_hash"

    quarantined = next(
        operand
        for operand in branches
        if "SECRET_QUARANTINED" in _expression_literals(operand)
    )
    registrations = _relation_nodes(
        quarantined,
        "data_snooping_proposal_registrations",
    )
    proposals = _relation_nodes(quarantined, "data_snooping_proposals")
    assert len(registrations) == len(proposals) == 1
    assert any(
        _enum_value(node.get("operator")) == "OR"
        and len(_relation_nodes(node, "data_snooping_proposal_registrations"))
        == 1
        and len(_relation_nodes(node, "data_snooping_proposals")) == 1
        for node in _walk(quarantined)
    )
    assert _relation_binding_columns(registrations[0])["quarantine_record_id"] == (
        "quarantine_record_id"
    )
    assert "SECRET_QUARANTINED" in _expression_literals(
        registrations[0]["predicate"]
    )
    assert "SECRET_QUARANTINED" in _expression_literals(proposals[0]["predicate"])


def test_pass_c_c7_exposes_no_renderer_ddl_activation_migration_or_service() -> None:
    module = _schema_module()
    source = inspect.getsource(module).upper()
    assert "CREATE TABLE" not in source
    assert "PRAGMA USER_VERSION" not in source
    for forbidden in (
        "render_family_schema_v3",
        "activate_family_schema_v3",
        "verify_family_authority",
        "require_family_schema_v3",
        "begin_evaluation_batch",
        "commit_evaluation_batch",
        "recover_orphan_attempts",
    ):
        assert not hasattr(module, forbidden)
