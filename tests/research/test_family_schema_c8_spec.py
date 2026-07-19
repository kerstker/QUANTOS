"""Exact Pass-C/C8 five-table schema-AST semantics.

C8 owns declaration, freeze, closure, grid-assessment, and one family-
conclusion table plus every late insert guard requested by C5/C7.  C6 remains
the provider of declaration and closure external-authority fields.  C9
publication is outside this slice.
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


MODULE = "services.research.experiment_registry.family_schema_c8"
COMPONENT_ID = "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1"
C4_COMPONENT = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
C5_COMPONENT = "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1"
C6_COMPONENT = "C6_EXTERNAL_AUTHORITY_REFS_V1"
C7_COMPONENT = "C7_ONE_SHOT_OOS_V1"

ASSESSMENT_ALGORITHM_VERSION = "WHITE_RC_HANSEN_SPA_FAMILY_V1"

C8_CAPABILITIES = (
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

TABLE_ORDER = (
    "data_snooping_declarations",
    "data_snooping_family_freezes",
    "data_snooping_family_closures",
    "data_snooping_grid_assessments",
    "data_snooping_assessments",
)

RECORD_TYPES = {
    "data_snooping_declarations": "DATA_SNOOPING_DECLARATION_V1",
    "data_snooping_family_freezes": "DATA_SNOOPING_FAMILY_FREEZE_V1",
    "data_snooping_family_closures": "DATA_SNOOPING_FAMILY_CLOSURE_V1",
    "data_snooping_grid_assessments": "DATA_SNOOPING_GRID_ASSESSMENT_V1",
    "data_snooping_assessments": "DATA_SNOOPING_ASSESSMENT_V1",
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
    "data_snooping_declarations": _columns(
        _c("declaration_id"),
        _c("family_id"),
        _c("selection_scope_id"),
        _c("deployment_mandate_hash"),
        _c("selection_decision_id"),
        _c("selection_scope_predecessor_head"),
        _c("decision_class"),
        _c("ordered_research_program_ids_json"),
        _c("ordered_research_program_ids_hash"),
        _c("ordered_research_question_ids_json"),
        _c("ordered_research_question_ids_hash"),
        _c("ordered_strategy_lineage_root_ids_json"),
        _c("ordered_strategy_lineage_root_ids_hash"),
        _c("objective_id"),
        _c("winner_selection_rule_id"),
        _c("search_data_contract_hash"),
        _c("oos_exposure_id"),
        _c("oos_release_policy_id"),
        _c("oos_access_policy_id"),
        _c("non_overlap_proof_hash"),
        _c("universe_id"),
        _c("universe_hash"),
        _c("ordered_period_calendar_hash"),
        _c("label_horizon"),
        _c("purge_periods", "INTEGER"),
        _c("embargo_periods", "INTEGER"),
        _c("return_currency"),
        _c("return_scale"),
        _c("compounding_convention"),
        _c("cost_model_id"),
        _c("cost_contract_hash"),
        _c("cost_parameters_hash"),
        _c("benchmark_id"),
        _c("benchmark_contract_hash"),
        _c("alpha_level"),
        _c("canonical_json_version"),
        _c("reality_check_algorithm_version"),
        _c("spa_algorithm_version"),
        _c("assessment_algorithm_version"),
        _c("bootstrap_algorithm_version"),
        _c("prng_algorithm_version"),
        _c("block_length_policy_id"),
        _c("block_length_grid_json"),
        _c("replication_schedule_json"),
        _c("bootstrap_seed", "INTEGER"),
        _c("dlp_policy_id"),
        _c("dlp_policy_version"),
        _c("dlp_policy_hash"),
        _c("scanner_implementation"),
        _c("scanner_version"),
        _c("restricted_envelope_authority_id"),
        _c("quarantine_policy_id"),
        _c("public_artifact_scan_policy_id"),
        _c("allowed_search_kinds_json"),
        _c("generator_seeds_json"),
        _c("search_proposal_budgets_json"),
        _c("required_search_count", "INTEGER"),
        _c("maximum_search_count", "INTEGER"),
        _c("stopping_rule_hash"),
        _c("external_ingestion_deadline_us", "INTEGER"),
        _c("search_attachment_rule_id"),
        _c("trial_ledger_authority_id"),
        _c("raw_proposal_count_definition"),
        _c("unique_performance_hypothesis_count_definition"),
        _c("global_unique_config_count_definition"),
        _c("performance_access_attempt_count_definition"),
        _c("declared_at_us", "INTEGER"),
    ),
    "data_snooping_family_freezes": _columns(
        _c("freeze_id"),
        _c("family_id"),
        _c("declaration_id"),
        _c("declaration_record_hash"),
        _c("search_count", "INTEGER"),
        _c("ordered_search_ids_json"),
        _c("ordered_search_ids_hash"),
        _c("ordered_search_terminal_hashes_json"),
        _c("ordered_search_terminal_hashes_hash"),
        _c("ordered_emission_seal_hashes_json"),
        _c("ordered_emission_seal_hashes_hash"),
        _c("ordered_candidate_chain_heads_json"),
        _c("ordered_candidate_chain_heads_hash"),
        _c("raw_proposal_count", "INTEGER"),
        _c("unique_performance_hypothesis_count", "INTEGER"),
        _c("frozen_config_set_hash"),
        _c("observed_pit_content_hash"),
        _c("code_manifest_evidence_hash"),
        _c("dlp_policy_hash"),
        _c("stopping_rule_hash"),
        _c("frozen_at_us", "INTEGER"),
    ),
    "data_snooping_family_closures": _columns(
        _c("closure_id"),
        _c("family_id"),
        _c("declaration_id"),
        _c("declaration_record_hash"),
        _c("freeze_id"),
        _c("freeze_record_hash"),
        _c("closure_status"),
        _c("selection_scope_id"),
        _c("selection_decision_id"),
        _c("selection_scope_history_head"),
        _c("selection_scope_outcome_record_hash", nullable=True),
        _c("ordered_search_ids_hash"),
        _c("ordered_search_terminal_hashes_hash"),
        _c("ordered_emission_seal_hashes_hash"),
        _c("ordered_proposal_ids_hash"),
        _c("ordered_disposition_record_hashes_hash"),
        _c("ordered_config_hashes_json"),
        _c("ordered_evaluation_receipt_hashes_json"),
        _c("attempt_id", nullable=True),
        _c("terminal_attempt_event_id", nullable=True),
        _c("evaluation_batch_id", nullable=True),
        _c("evaluation_batch_receipt_hash", nullable=True),
        _c("oos_release_id", nullable=True),
        _c("oos_access_attempt_terminal_hash", nullable=True),
        _c("oos_release_record_hash", nullable=True),
        _c("raw_proposal_count", "INTEGER"),
        _c("unique_performance_hypothesis_count", "INTEGER"),
        _c("global_unique_config_count", "INTEGER"),
        _c("performance_access_attempt_count", "INTEGER"),
        _c("ordered_period_keys_hash", nullable=True),
        _c("candidate_series_set_hash", nullable=True),
        _c("benchmark_receipt_hash", nullable=True),
        _c("cost_model_input_set_hash", nullable=True),
        _c("winner_config_hash", nullable=True),
        _c("winner_evaluation_receipt_hash", nullable=True),
        _c("trial_ledger_proof_json"),
        _c("trial_ledger_proof_hash"),
        _c("failure_reasons_json"),
        _c("closed_at_us", "INTEGER"),
    ),
    "data_snooping_grid_assessments": _columns(
        _c("grid_assessment_id"),
        _c("family_id"),
        _c("closure_manifest_hash"),
        _c("algorithm_version"),
        _c("period_count", "INTEGER"),
        _c("block_length", "INTEGER"),
        _c("replication_count", "INTEGER"),
        _c("schedule_ordinal", "INTEGER"),
        _c("prefix_index_stream_hash"),
        _c("prev_record_hash"),
        _c("reality_check_payload_json", nullable=True),
        _c("reality_check_result_hash", nullable=True),
        _c("spa_payload_json", nullable=True),
        _c("spa_result_hash", nullable=True),
        _c("grid_point_status"),
        _c("grid_point_verdict"),
        _c("promotion_effect"),
        _c("failure_reasons_json"),
        _c("assessed_at_us", "INTEGER"),
    ),
    "data_snooping_assessments": _columns(
        _c("assessment_id"),
        _c("family_id"),
        _c("closure_manifest_hash"),
        _c("algorithm_version"),
        _c("ordered_latest_grid_assessment_hashes_json"),
        _c("ordered_latest_grid_assessment_hashes_hash"),
        _c("candidate_order_hash", nullable=True),
        _c("period_count", "INTEGER", nullable=True),
        _c("minimum_replication_count", "INTEGER", nullable=True),
        _c("assessment_status"),
        _c("diagnostic_verdict"),
        _c("promotion_effect"),
        _c("failure_reasons_json"),
        _c("concluded_at_us", "INTEGER"),
    ),
}

PRIMARY_KEYS = {
    "data_snooping_declarations": ("declaration_id",),
    "data_snooping_family_freezes": ("freeze_id",),
    "data_snooping_family_closures": ("closure_id",),
    "data_snooping_grid_assessments": ("grid_assessment_id",),
    "data_snooping_assessments": ("assessment_id",),
}


def _fk(
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
) -> tuple[object, ...]:
    return (
        columns,
        referenced_table,
        referenced_columns,
        "RESTRICT",
        "RESTRICT",
        "NOT_DEFERRABLE",
    )


def _genesis_fk() -> tuple[object, ...]:
    return _fk(
        ("authority_instance_id", "genesis_record_hash"),
        "family_authority_genesis",
        ("authority_instance_id", "record_hash"),
    )


FOREIGN_KEYS = {
    "data_snooping_declarations": (
        _genesis_fk(),
        _fk(
            ("selection_scope_id", "selection_decision_id", "family_id"),
            "selection_decisions",
            ("selection_scope_id", "selection_decision_id", "family_id"),
        ),
        _fk(
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
    ),
    "data_snooping_family_freezes": (
        _genesis_fk(),
        _fk(
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
    ),
    "data_snooping_family_closures": (
        _genesis_fk(),
        _fk(
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
        _fk(
            (
                "authority_instance_id",
                "family_id",
                "freeze_id",
                "freeze_record_hash",
            ),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "freeze_id", "record_hash"),
        ),
        _fk(
            ("selection_scope_history_head",),
            "research_authority_events",
            ("record_hash",),
        ),
        _fk(
            ("selection_scope_outcome_record_hash",),
            "selection_scope_outcomes",
            ("record_hash",),
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
        ),
        _fk(
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
        _fk(
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
        _fk(
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
        ),
    ),
    "data_snooping_grid_assessments": (
        _genesis_fk(),
        _fk(
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            "data_snooping_family_closures",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        _fk(
            ("authority_instance_id", "family_id", "algorithm_version"),
            "data_snooping_declarations",
            (
                "authority_instance_id",
                "family_id",
                "assessment_algorithm_version",
            ),
        ),
    ),
    "data_snooping_assessments": (
        _genesis_fk(),
        _fk(
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            "data_snooping_family_closures",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        _fk(
            ("authority_instance_id", "family_id", "algorithm_version"),
            "data_snooping_declarations",
            (
                "authority_instance_id",
                "family_id",
                "assessment_algorithm_version",
            ),
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
    "data_snooping_declarations": (
        _ix("data_snooping_declarations", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_declarations", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _ix("data_snooping_declarations", "decision", "UNIQUE", ("selection_decision_id",)),
        _ix("data_snooping_declarations", "exposure", "UNIQUE", ("oos_exposure_id",)),
        _ix(
            "data_snooping_declarations",
            "assessment_algorithm",
            "UNIQUE",
            (
                "authority_instance_id",
                "family_id",
                "assessment_algorithm_version",
            ),
        ),
        _ix(
            "data_snooping_declarations",
            "exact_binding",
            "UNIQUE",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
    ),
    "data_snooping_family_freezes": (
        _ix("data_snooping_family_freezes", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_family_freezes", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _ix("data_snooping_family_freezes", "declaration", "UNIQUE", ("declaration_id",)),
        _ix(
            "data_snooping_family_freezes",
            "exact_binding",
            "UNIQUE",
            ("authority_instance_id", "family_id", "freeze_id", "record_hash"),
        ),
        _ix(
            "data_snooping_family_freezes",
            "family_record",
            "UNIQUE",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
    ),
    "data_snooping_family_closures": (
        _ix("data_snooping_family_closures", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("data_snooping_family_closures", "family", "UNIQUE", ("authority_instance_id", "family_id")),
        _ix("data_snooping_family_closures", "declaration", "UNIQUE", ("declaration_id",)),
        _ix("data_snooping_family_closures", "freeze", "UNIQUE", ("freeze_id",)),
        _ix(
            "data_snooping_family_closures",
            "family_record",
            "UNIQUE",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        _ix(
            "data_snooping_family_closures",
            "winner",
            "ORDINARY",
            ("family_id", "winner_config_hash", "winner_evaluation_receipt_hash"),
        ),
    ),
    "data_snooping_grid_assessments": (
        _ix("data_snooping_grid_assessments", "record_hash", "UNIQUE", ("record_hash",)),
        _ix(
            "data_snooping_grid_assessments",
            "grid",
            "UNIQUE",
            ("authority_instance_id", "family_id", "block_length", "replication_count", "algorithm_version"),
        ),
        _ix(
            "data_snooping_grid_assessments",
            "schedule",
            "UNIQUE",
            ("authority_instance_id", "family_id", "block_length", "schedule_ordinal"),
        ),
        _ix(
            "data_snooping_grid_assessments",
            "closure",
            "ORDINARY",
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
        ),
    ),
    "data_snooping_assessments": (
        _ix("data_snooping_assessments", "record_hash", "UNIQUE", ("record_hash",)),
        _ix(
            "data_snooping_assessments",
            "lineage",
            "UNIQUE",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        _ix(
            "data_snooping_assessments",
            "family",
            "UNIQUE",
            ("authority_instance_id", "family_id"),
        ),
        _ix(
            "data_snooping_assessments",
            "closure",
            "UNIQUE",
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
        ),
    ),
}


TIMESTAMP_COLUMNS = {
    "data_snooping_declarations": {"declared_at_us", "external_ingestion_deadline_us"},
    "data_snooping_family_freezes": {"frozen_at_us"},
    "data_snooping_family_closures": {"closed_at_us"},
    "data_snooping_grid_assessments": {"assessed_at_us"},
    "data_snooping_assessments": {"concluded_at_us"},
}


def _base_checks(table: str, time_column: str) -> dict[str, tuple[set[str], set[object]]]:
    return {
        f"{table}_record_type": ({"record_type"}, {RECORD_TYPES[table]}),
        f"{table}_positive_time": ({time_column}, {0}),
    }


CHECK_REQUIREMENTS = {
    "data_snooping_declarations": {
        **_base_checks("data_snooping_declarations", "declared_at_us"),
        "data_snooping_declarations_positive_deadline": ({"external_ingestion_deadline_us"}, {0}),
        "data_snooping_declarations_deadline_after_declaration": (
            {"external_ingestion_deadline_us", "declared_at_us"},
            set(),
        ),
        "data_snooping_declarations_decision_class_domain": (
            {"decision_class"},
            {"PUBLISHABLE", "DIAGNOSTIC_ONLY"},
        ),
        "data_snooping_declarations_nonnegative_purge_embargo": (
            {"purge_periods", "embargo_periods"},
            {0},
        ),
        "data_snooping_declarations_positive_search_bounds": (
            {"required_search_count", "maximum_search_count"},
            {0},
        ),
        "data_snooping_declarations_search_bounds_order": (
            {"required_search_count", "maximum_search_count"},
            set(),
        ),
        "data_snooping_declarations_algorithm_versions": (
            {
                "canonical_json_version",
                "reality_check_algorithm_version",
                "spa_algorithm_version",
                "assessment_algorithm_version",
                "bootstrap_algorithm_version",
                "prng_algorithm_version",
                "block_length_policy_id",
            },
            {
                "QUANTOS_CANONICAL_JSON_V1",
                "WHITE_REALITY_CHECK_V1",
                "HANSEN_SPA_V1",
                ASSESSMENT_ALGORITHM_VERSION,
                "QUANTOS_STATIONARY_BOOTSTRAP_V1",
                "SPLITMIX64_V1",
                "QUANTOS_BLOCK_LENGTH_GRID_V1",
            },
        ),
        "data_snooping_declarations_counter_definitions": (
            {
                "raw_proposal_count_definition",
                "unique_performance_hypothesis_count_definition",
                "global_unique_config_count_definition",
                "performance_access_attempt_count_definition",
            },
            {
                "EVERY_REGISTERED_OPAQUE_ENVELOPE_V1",
                "DISTINCT_ACCEPTED_CONFIG_HASH_V1",
                "TRIAL_LEDGER_DISTINCT_CONFIG_AT_HISTORY_HEAD_V1",
                "DISTINCT_COMMITTED_STARTED_ATTEMPT_V1",
            },
        ),
    },
    "data_snooping_family_freezes": {
        **_base_checks("data_snooping_family_freezes", "frozen_at_us"),
        "data_snooping_family_freezes_positive_search_count": ({"search_count"}, {0}),
        "data_snooping_family_freezes_nonnegative_counts": (
            {"raw_proposal_count", "unique_performance_hypothesis_count"},
            {0},
        ),
        "data_snooping_family_freezes_unique_not_above_raw": (
            {"raw_proposal_count", "unique_performance_hypothesis_count"},
            set(),
        ),
    },
    "data_snooping_family_closures": {
        **_base_checks("data_snooping_family_closures", "closed_at_us"),
        "data_snooping_family_closures_status_domain": (
            {"closure_status"},
            {"COMPLETE", "NOT_ASSESSABLE"},
        ),
        "data_snooping_family_closures_nonnegative_counts": (
            {
                "raw_proposal_count",
                "unique_performance_hypothesis_count",
                "global_unique_config_count",
                "performance_access_attempt_count",
            },
            {0},
        ),
        "data_snooping_family_closures_access_count_domain": (
            {"performance_access_attempt_count"},
            {0, 1},
        ),
        "data_snooping_family_closures_unique_not_above_raw": (
            {"raw_proposal_count", "unique_performance_hypothesis_count"},
            set(),
        ),
        "data_snooping_family_closures_global_count_covers_family_subset": (
            {
                "global_unique_config_count",
                "unique_performance_hypothesis_count",
            },
            set(),
        ),
        "data_snooping_family_closures_exact_branch": (
            {
                "closure_status",
                "attempt_id",
                "terminal_attempt_event_id",
                "evaluation_batch_id",
                "evaluation_batch_receipt_hash",
                "oos_release_id",
                "oos_access_attempt_terminal_hash",
                "oos_release_record_hash",
                "performance_access_attempt_count",
                "ordered_period_keys_hash",
                "candidate_series_set_hash",
                "benchmark_receipt_hash",
                "cost_model_input_set_hash",
                "winner_config_hash",
                "winner_evaluation_receipt_hash",
                "ordered_config_hashes_json",
                "ordered_evaluation_receipt_hashes_json",
                "failure_reasons_json",
            },
            {"COMPLETE", "NOT_ASSESSABLE", 0, 1, "[]"},
        ),
    },
    "data_snooping_grid_assessments": {
        **_base_checks("data_snooping_grid_assessments", "assessed_at_us"),
        "data_snooping_grid_assessments_positive_grid": (
            {"period_count", "block_length", "replication_count", "schedule_ordinal"},
            {0, 20},
        ),
        "data_snooping_grid_assessments_status_domain": (
            {"grid_point_status"},
            {"RESOLVED", "AMBIGUOUS", "INVALID"},
        ),
        "data_snooping_grid_assessments_verdict_domain": (
            {"grid_point_verdict"},
            {
                "SUPPORTS_SUPERIORITY",
                "DOES_NOT_SUPPORT_SUPERIORITY",
                "METHOD_DISAGREEMENT",
                "PENDING_MORE_REPLICATIONS",
                "MONTE_CARLO_UNRESOLVED",
                "NOT_APPLICABLE",
            },
        ),
        "data_snooping_grid_assessments_promotion_none": (
            {"promotion_effect"},
            {"NONE"},
        ),
        "data_snooping_grid_assessments_algorithm_version": (
            {"algorithm_version"},
            {ASSESSMENT_ALGORITHM_VERSION},
        ),
        "data_snooping_grid_assessments_exact_branch": (
            {
                "reality_check_payload_json",
                "reality_check_result_hash",
                "spa_payload_json",
                "spa_result_hash",
                "grid_point_status",
                "grid_point_verdict",
                "failure_reasons_json",
            },
            {
                "RESOLVED",
                "AMBIGUOUS",
                "INVALID",
                "SUPPORTS_SUPERIORITY",
                "DOES_NOT_SUPPORT_SUPERIORITY",
                "METHOD_DISAGREEMENT",
                "PENDING_MORE_REPLICATIONS",
                "MONTE_CARLO_UNRESOLVED",
                "NOT_APPLICABLE",
                "[]",
            },
        ),
    },
    "data_snooping_assessments": {
        **_base_checks("data_snooping_assessments", "concluded_at_us"),
        "data_snooping_assessments_status_domain": (
            {"assessment_status"},
            {"ASSESSED", "NOT_ASSESSABLE"},
        ),
        "data_snooping_assessments_verdict_domain": (
            {"diagnostic_verdict"},
            {
                "SUPPORTS_SUPERIORITY",
                "DOES_NOT_SUPPORT_SUPERIORITY",
                "METHOD_DISAGREEMENT",
                "MONTE_CARLO_UNRESOLVED",
                "NOT_APPLICABLE",
            },
        ),
        "data_snooping_assessments_promotion_none": (
            {"promotion_effect"},
            {"NONE"},
        ),
        "data_snooping_assessments_algorithm_version": (
            {"algorithm_version"},
            {ASSESSMENT_ALGORITHM_VERSION},
        ),
        "data_snooping_assessments_exact_branch": (
            {
                "ordered_latest_grid_assessment_hashes_json",
                "candidate_order_hash",
                "period_count",
                "minimum_replication_count",
                "assessment_status",
                "diagnostic_verdict",
                "failure_reasons_json",
            },
            {
                "ASSESSED",
                "NOT_ASSESSABLE",
                "SUPPORTS_SUPERIORITY",
                "DOES_NOT_SUPPORT_SUPERIORITY",
                "METHOD_DISAGREEMENT",
                "MONTE_CARLO_UNRESOLVED",
                "NOT_APPLICABLE",
                20,
                10_000,
                "[]",
            },
        ),
    },
}


GUARD_ERROR_CODES = {
    "data_snooping_declarations": (
        "DECLARATION_PRECEDES_LINKED_SEARCH",
        "DECLARATION_PRECEDES_FAMILY_PROPOSAL",
        "DECLARATION_PRECEDES_OOS_ACCESS",
    ),
    "data_snooping_family_freezes": (
        "FREEZE_DECLARATION_EXACT",
        "FREEZE_ALL_DECLARED_SEARCHES_PRESENT",
        "FREEZE_SEARCHES_TERMINAL",
        "FREEZE_EMISSION_SEALS_COMPLETE",
        "FREEZE_SEARCH_BUDGETS_MATCH",
        "FREEZE_COUNTS_MATCH",
        "FREEZE_DATA_CODE_DLP_STABLE",
        "FREEZE_STOPPING_RULE_SATISFIED",
        "FREEZE_PRECEDES_OOS_ACCESS",
    ),
    "data_snooping_family_closures": (
        "CLOSURE_FREEZE_EXACT",
        "CLOSURE_NO_ORPHAN_STARTED_ATTEMPT",
        "CLOSURE_PROPOSAL_DISPOSITIONS_TOTAL",
        "CLOSURE_ATTEMPT_BATCH_EXACT_BRANCH",
        "CLOSURE_SERIES_CALENDAR_COST_TOTAL",
        "CLOSURE_TRIAL_PROOF_EXACT",
        "CLOSURE_SCOPE_HISTORY_PREFIX",
        "CLOSURE_SCOPE_OUTCOME_BINDING",
        "CLOSURE_WINNER_RULE_EXACT",
        "CLOSURE_FOUR_COUNTS_RECOMPUTED",
    ),
    "data_snooping_grid_assessments": (
        "GRID_CLOSURE_COMPLETE_EXACT",
        "GRID_DECLARED_POINT_EXACT",
        "GRID_SCHEDULE_NEXT",
        "GRID_PREFIX_PROOF",
        "GRID_PREVIOUS_MUST_BE_PENDING",
        "GRID_VERDICT_SCHEDULE_EXACT",
        "GRID_REALITY_CHECK_PAYLOAD_HASH_EXACT",
        "GRID_SPA_PAYLOAD_HASH_EXACT",
        "GRID_AFTER_FAMILY_ASSESSMENT_FORBIDDEN",
    ),
    "data_snooping_assessments": (
        "ASSESSMENT_CLOSURE_EXACT",
        "ASSESSMENT_COMPLETE_CLOSURE_REQUIRED",
        "ASSESSMENT_LATEST_GRID_POLICY_EXACT",
        "ASSESSMENT_LATEST_GRID_HASHES_EXACT",
        "ASSESSMENT_NA_CLOSURE_FORBIDS_GRID_EVIDENCE",
    ),
}


REQUIRED_VERIFICATION_RULES = {
    "data_snooping_declarations": {
        "declaration_row_hash",
        "declaration_genesis_binding",
        "declaration_foreign_keys",
        "declaration_scope_decision_exact",
        "declaration_scope_history_predecessor",
        "declaration_program_question_root_totality",
        "declaration_oos_exposure_exact",
        "declaration_search_oos_non_overlap",
        "declaration_ordered_sets_hashes",
        "declaration_grid_schedule",
        "declaration_code_data_dlp_binding",
        "declaration_search_plan",
        "declaration_four_counter_definitions",
        "declaration_precedes_family_activity",
    },
    "data_snooping_family_freezes": {
        "family_freeze_row_hash",
        "family_freeze_genesis_binding",
        "family_freeze_foreign_keys",
        "family_freeze_declaration_exact",
        "family_freeze_declared_search_totality",
        "family_freeze_terminal_search_totality",
        "family_freeze_emission_seal_totality",
        "family_freeze_candidate_chain_heads",
        "family_freeze_independent_counts",
        "family_freeze_config_set",
        "family_freeze_declaration_content_stability",
        "family_freeze_activity_cutoff",
    },
    "data_snooping_family_closures": {
        "family_closure_row_hash",
        "family_closure_genesis_binding",
        "family_closure_foreign_keys",
        "family_closure_freeze_exact",
        "family_closure_scope_history",
        "family_closure_search_manifest",
        "family_closure_proposal_disposition_totality",
        "family_closure_attempt_batch_branch",
        "family_closure_series_calendar_cost_totality",
        "family_closure_four_counts",
        "family_closure_trial_ledger_proof",
        "family_closure_deterministic_winner",
        "family_closure_branch_totality",
    },
    "data_snooping_grid_assessments": {
        "grid_assessment_row_hash",
        "grid_assessment_genesis_binding",
        "grid_assessment_foreign_keys",
        "grid_assessment_chain",
        "grid_assessment_closure_complete_exact",
        "grid_assessment_declared_point",
        "grid_assessment_schedule_order",
        "grid_assessment_prefix_stream_extension",
        "grid_assessment_pending_predecessor",
        "grid_assessment_result_payload_hashes",
        "grid_assessment_branch_totality",
        "grid_assessment_family_conclusion_cutoff",
        "grid_assessment_promotion_none",
    },
    "data_snooping_assessments": {
        "assessment_row_hash",
        "assessment_genesis_binding",
        "assessment_foreign_keys",
        "assessment_closure_exact",
        "assessment_latest_grid_policy",
        "assessment_branch_totality",
        "assessment_semantic_recomputation",
        "assessment_promotion_none",
    },
}


# These variable-cardinality sets and schedule-wide predicates cannot be
# represented by an ordinary SQLite FK.  Exact typed verifier metadata makes
# the non-FK relationship explicit rather than leaving Pass M to infer it.
NON_FK_TYPED_REFERENCES = {
    "data_snooping_declarations": {
        "declaration_program_question_root_totality": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "ordered_research_program_ids_hash",
                "ordered_research_question_ids_hash",
                "ordered_strategy_lineage_root_ids_hash",
            ),
            "research_programs",
        ),
        "declaration_precedes_family_activity": (
            "CROSS_TABLE_TOTALITY",
            ("family_id", "declaration_id", "declared_at_us"),
            "data_snooping_family_searches",
        ),
    },
    "data_snooping_family_freezes": {
        "family_freeze_declared_search_totality": (
            "CROSS_TABLE_TOTALITY",
            ("family_id", "search_count", "ordered_search_ids_hash"),
            "data_snooping_family_searches",
        ),
        "family_freeze_terminal_search_totality": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "ordered_search_terminal_hashes_json",
                "ordered_search_terminal_hashes_hash",
            ),
            "search_attempt_events",
        ),
        "family_freeze_emission_seal_totality": (
            "CROSS_TABLE_TOTALITY",
            ("family_id", "ordered_emission_seal_hashes_hash"),
            "data_snooping_search_emission_seals",
        ),
        "family_freeze_candidate_chain_heads": (
            "CROSS_TABLE_TOTALITY",
            ("family_id", "ordered_candidate_chain_heads_hash"),
            "data_snooping_search_emission_seals",
        ),
        "family_freeze_independent_counts": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "raw_proposal_count",
                "unique_performance_hypothesis_count",
            ),
            "data_snooping_proposal_registrations",
        ),
    },
    "data_snooping_family_closures": {
        "family_closure_search_manifest": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "ordered_search_ids_hash",
                "ordered_search_terminal_hashes_hash",
                "ordered_emission_seal_hashes_hash",
            ),
            "data_snooping_family_searches",
        ),
        "family_closure_proposal_disposition_totality": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "ordered_proposal_ids_hash",
                "ordered_disposition_record_hashes_hash",
            ),
            "data_snooping_candidate_dispositions",
        ),
        "family_closure_attempt_batch_branch": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "attempt_id",
                "terminal_attempt_event_id",
                "oos_access_attempt_terminal_hash",
                "evaluation_batch_id",
                "evaluation_batch_receipt_hash",
                "closure_status",
            ),
            "data_snooping_oos_access_attempts",
        ),
        "family_closure_series_calendar_cost_totality": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "evaluation_batch_id",
                "ordered_period_keys_hash",
                "candidate_series_set_hash",
                "benchmark_receipt_hash",
                "cost_model_input_set_hash",
            ),
            "data_snooping_evaluation_receipts",
        ),
        "family_closure_four_counts": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "raw_proposal_count",
                "unique_performance_hypothesis_count",
                "global_unique_config_count",
                "performance_access_attempt_count",
            ),
            "data_snooping_proposal_registrations",
        ),
    },
    "data_snooping_grid_assessments": {
        "grid_assessment_declared_point": (
            "CROSS_TABLE_REFERENCE",
            (
                "family_id",
                "period_count",
                "block_length",
                "replication_count",
                "algorithm_version",
            ),
            "data_snooping_declarations",
        ),
        "grid_assessment_pending_predecessor": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "block_length",
                "replication_count",
                "schedule_ordinal",
                "grid_point_verdict",
            ),
            "data_snooping_grid_assessments",
        ),
        "grid_assessment_family_conclusion_cutoff": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "assessed_at_us",
            ),
            "data_snooping_assessments",
        ),
    },
    "data_snooping_assessments": {
        "assessment_latest_grid_policy": (
            "CROSS_TABLE_TOTALITY",
            (
                "family_id",
                "ordered_latest_grid_assessment_hashes_json",
                "ordered_latest_grid_assessment_hashes_hash",
                "candidate_order_hash",
                "period_count",
                "minimum_replication_count",
                "diagnostic_verdict",
            ),
            "data_snooping_grid_assessments",
        ),
    },
}


C5_EXTENSION_ORDER = (
    "C8_FAMILY_SEARCH_DECLARATION_AND_LATE_GUARDS_V1",
    "C8_PROPOSAL_DECLARATION_DLP_AND_LATE_GUARDS_V1",
    "C8_PROPOSAL_TERMINAL_RESULT_LATE_GUARDS_V1",
    "C8_HYPOTHESIS_LATE_GUARDS_V1",
    "C8_EMISSION_SEAL_LATE_GUARDS_V1",
)

C7_TABLE_ORDER = (
    "data_snooping_oos_releases",
    "data_snooping_oos_access_attempts",
    "data_snooping_candidate_dispositions",
    "data_snooping_evaluation_batches",
    "data_snooping_family_oos_evaluations",
    "data_snooping_evaluation_receipts",
    "data_snooping_benchmark_returns",
)

C7_EXTENSION_ORDER = tuple(f"C8_{table.upper()}_LATE_INSERT_GUARDS_V1" for table in C7_TABLE_ORDER)

EXTENSION_ORDER = (*C5_EXTENSION_ORDER, *C7_EXTENSION_ORDER)

EXTENSION_TARGETS = {
    C5_EXTENSION_ORDER[0]: "data_snooping_family_searches",
    C5_EXTENSION_ORDER[1]: "data_snooping_proposal_registrations",
    C5_EXTENSION_ORDER[2]: "data_snooping_proposals",
    C5_EXTENSION_ORDER[3]: "data_snooping_hypotheses",
    C5_EXTENSION_ORDER[4]: "data_snooping_search_emission_seals",
    **dict(zip(C7_EXTENSION_ORDER, C7_TABLE_ORDER, strict=True)),
}

DECLARATION_BINDING_COLUMNS = (
    _c("declaration_id"),
    _c("declaration_record_hash"),
)

EXTENSION_COLUMNS = {
    C5_EXTENSION_ORDER[0]: DECLARATION_BINDING_COLUMNS,
    C5_EXTENSION_ORDER[1]: DECLARATION_BINDING_COLUMNS,
}

_DECLARATION_BINDING_FK = _fk(
    (
        "authority_instance_id",
        "family_id",
        "declaration_id",
        "declaration_record_hash",
    ),
    "data_snooping_declarations",
    ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
)

EXTENSION_FOREIGN_KEYS = {
    C5_EXTENSION_ORDER[0]: (_DECLARATION_BINDING_FK,),
    C5_EXTENSION_ORDER[1]: (_DECLARATION_BINDING_FK,),
    C7_EXTENSION_ORDER[0]: (
        _fk(
            (
                "authority_instance_id",
                "family_id",
                "family_freeze_record_hash",
            ),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
    ),
}

EXTENSION_INDEXES = {
    C5_EXTENSION_ORDER[0]: (
        _ix(
            "data_snooping_family_searches",
            "declaration_binding",
            "ORDINARY",
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
        ),
    ),
    C5_EXTENSION_ORDER[1]: (
        _ix(
            "data_snooping_proposal_registrations",
            "declaration_binding",
            "ORDINARY",
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
        ),
    ),
    C7_EXTENSION_ORDER[0]: (
        _ix(
            "data_snooping_oos_releases",
            "family_freeze",
            "ORDINARY",
            ("authority_instance_id", "family_id", "family_freeze_record_hash"),
        ),
        _ix(
            "data_snooping_oos_releases",
            "closure_exact_binding",
            "UNIQUE",
            (
                "authority_instance_id",
                "family_id",
                "attempt_id",
                "oos_release_id",
                "record_hash",
            ),
        ),
    ),
    C7_EXTENSION_ORDER[1]: (
        _ix(
            "data_snooping_oos_access_attempts",
            "closure_terminal_exact_binding",
            "UNIQUE",
            (
                "authority_instance_id",
                "attempt_id",
                "attempt_event_id",
                "family_id",
                "record_hash",
            ),
        ),
    ),
}

EXTENSION_GUARD_CODES = {
    C5_EXTENSION_ORDER[0]: (
        "SEARCH_REQUIRES_EXACT_DECLARATION",
        "SEARCH_MATCHES_DECLARED_PLAN",
        "SEARCH_LINK_BEFORE_FREEZE",
        "SEARCH_LINK_BEFORE_CLOSURE",
    ),
    C5_EXTENSION_ORDER[1]: (
        "PROPOSAL_REQUIRES_EXACT_DECLARATION",
        "PROPOSAL_DECLARATION_DLP_MATCH",
        "PROPOSAL_WITHIN_DECLARED_EXTERNAL_DEADLINE",
        "PROPOSAL_BEFORE_FREEZE",
        "PROPOSAL_BEFORE_CLOSURE",
    ),
    C5_EXTENSION_ORDER[2]: (
        "PROPOSAL_TERMINAL_RESULT_BEFORE_FREEZE",
        "PROPOSAL_TERMINAL_RESULT_BEFORE_CLOSURE",
    ),
    C5_EXTENSION_ORDER[3]: (
        "HYPOTHESIS_BEFORE_FREEZE",
        "HYPOTHESIS_BEFORE_CLOSURE",
    ),
    C5_EXTENSION_ORDER[4]: (
        "EMISSION_SEAL_BEFORE_FREEZE",
        "EMISSION_SEAL_BEFORE_CLOSURE",
    ),
    C7_EXTENSION_ORDER[0]: (
        "OOS_RELEASE_REQUIRES_EXACT_FREEZE",
        "OOS_RELEASE_SCOPE_OUTCOME_POLICY",
        "OOS_RELEASE_BEFORE_CLOSURE",
    ),
    C7_EXTENSION_ORDER[1]: (
        "OOS_ATTEMPT_REQUIRES_FREEZE",
        "OOS_ATTEMPT_SCOPE_OUTCOME_POLICY",
        "OOS_ATTEMPT_BEFORE_CLOSURE",
    ),
    C7_EXTENSION_ORDER[2]: (
        "CANDIDATE_DISPOSITION_REQUIRES_FREEZE",
        "CANDIDATE_DISPOSITION_BEFORE_CLOSURE",
    ),
    C7_EXTENSION_ORDER[3]: (
        "EVALUATION_BATCH_REQUIRES_FREEZE",
        "EVALUATION_BATCH_BEFORE_CLOSURE",
    ),
    C7_EXTENSION_ORDER[4]: (
        "FAMILY_OOS_EVALUATION_REQUIRES_FREEZE",
        "FAMILY_OOS_EVALUATION_BEFORE_CLOSURE",
    ),
    C7_EXTENSION_ORDER[5]: (
        "EVALUATION_RECEIPT_REQUIRES_FREEZE",
        "EVALUATION_RECEIPT_BEFORE_CLOSURE",
    ),
    C7_EXTENSION_ORDER[6]: (
        "BENCHMARK_RETURN_REQUIRES_FREEZE",
        "BENCHMARK_RETURN_BEFORE_CLOSURE",
    ),
}


def _rule_shape(
    kind: str,
    columns: tuple[str, ...],
    referenced_table: str | None = None,
) -> tuple[str, tuple[str, ...], str | None]:
    return (kind, columns, referenced_table)


EXTENSION_RULES = {
    C5_EXTENSION_ORDER[0]: {
        "family_search_declaration_exact": _rule_shape(
            "CROSS_TABLE_REFERENCE",
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
            "data_snooping_declarations",
        ),
        "family_search_declared_plan_match": _rule_shape(
            "CROSS_TABLE_REFERENCE",
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
            "data_snooping_declarations",
        ),
        "family_search_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "linked_at_us"),
        ),
    },
    C5_EXTENSION_ORDER[1]: {
        "proposal_declaration_exact": _rule_shape(
            "CROSS_TABLE_REFERENCE",
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
            "data_snooping_declarations",
        ),
        "proposal_declaration_dlp_match": _rule_shape(
            "CROSS_TABLE_REFERENCE",
            (
                "family_id",
                "dlp_policy_id",
                "dlp_policy_version",
                "scanner_implementation",
                "scanner_version",
            ),
            "data_snooping_declarations",
        ),
        "proposal_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "registered_at_us"),
        ),
    },
    C5_EXTENSION_ORDER[2]: {
        "proposal_terminal_result_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "parsed_at_us"),
        ),
    },
    C5_EXTENSION_ORDER[3]: {
        "hypothesis_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "first_search_id", "first_proposal_id"),
        ),
    },
    C5_EXTENSION_ORDER[4]: {
        "emission_seal_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "search_id", "sealed_at_us"),
        ),
    },
    C7_EXTENSION_ORDER[0]: {
        "oos_release_family_freeze_exact": _rule_shape(
            "CROSS_TABLE_REFERENCE",
            (
                "authority_instance_id",
                "family_id",
                "family_freeze_record_hash",
            ),
            "data_snooping_family_freezes",
        ),
        "oos_release_scope_outcome_policy": _rule_shape(
            "SELECTION_SCOPE",
            (
                "family_id",
                "attempt_id",
                "started_attempt_event_id",
                "family_freeze_record_hash",
                "oos_exposure_id",
            ),
            "data_snooping_declarations",
        ),
        "oos_release_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "released_at_us"),
        ),
    },
    C7_EXTENSION_ORDER[1]: {
        "oos_attempt_family_freeze_totality": _rule_shape(
            "CROSS_TABLE_TOTALITY",
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
            "data_snooping_family_freezes",
        ),
        "oos_attempt_scope_outcome_policy": _rule_shape(
            "SELECTION_SCOPE",
            ("family_id", "selection_decision_id", "oos_exposure_id"),
            "selection_scope_outcomes",
        ),
        "oos_attempt_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "event_at_us"),
        ),
    },
    C7_EXTENSION_ORDER[2]: {
        "candidate_disposition_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "recorded_at_us"),
        ),
    },
    C7_EXTENSION_ORDER[3]: {
        "evaluation_batch_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "committed_at_us"),
        ),
    },
    C7_EXTENSION_ORDER[4]: {
        "family_oos_evaluation_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "linked_at_us"),
        ),
    },
    C7_EXTENSION_ORDER[5]: {
        "evaluation_receipt_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "committed_at_us"),
        ),
    },
    C7_EXTENSION_ORDER[6]: {
        "benchmark_return_freeze_closure_cutoff": _rule_shape(
            "FREEZE_CLOSURE_ASSESSMENT",
            ("family_id", "committed_at_us"),
        ),
    },
}

EXTENSION_RELATION_TABLES = {
    C5_EXTENSION_ORDER[0]: {
        "data_snooping_declarations",
        "data_snooping_family_freezes",
        "data_snooping_family_closures",
    },
    C5_EXTENSION_ORDER[1]: {
        "data_snooping_declarations",
        "data_snooping_family_freezes",
        "data_snooping_family_closures",
    },
    C5_EXTENSION_ORDER[2]: {
        "data_snooping_family_freezes",
        "data_snooping_family_closures",
    },
    C5_EXTENSION_ORDER[3]: {
        "data_snooping_family_freezes",
        "data_snooping_family_closures",
    },
    C5_EXTENSION_ORDER[4]: {
        "data_snooping_family_freezes",
        "data_snooping_family_closures",
    },
    C7_EXTENSION_ORDER[0]: {
        "data_snooping_family_freezes",
        "data_snooping_declarations",
        "selection_scope_outcomes",
        "data_snooping_family_closures",
    },
    C7_EXTENSION_ORDER[1]: {
        "data_snooping_declarations",
        "selection_scope_outcomes",
        "data_snooping_family_closures",
    },
    **{
        extension_id: {
            "data_snooping_family_freezes",
            "data_snooping_family_closures",
        }
        for extension_id in C7_EXTENSION_ORDER[2:]
    },
}

C6_DECLARATION_EXTENSION = (
    "C6_DATA_SNOOPING_DECLARATION_EMBEDDED_REFS_V1",
    "data_snooping_declarations",
    C6_COMPONENT,
    "c6.declaration.embedded_refs",
)
C6_CLOSURE_EXTENSION = (
    "C6_FAMILY_CLOSURE_EMBEDDED_REFS_V1",
    "data_snooping_family_closures",
    C6_COMPONENT,
    "c6.family_closure.embedded_refs",
)
C6_REQUIRED_EXTENSIONS = (C6_DECLARATION_EXTENSION, C6_CLOSURE_EXTENSION)

C6_DECLARATION_CONTRACT_FIELDS = (
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

C6_CLOSURE_CONTRACT_FIELDS = (
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

REQUIRED_CONTRACT_SURFACES = (
    (
        "DataSnoopingDeclaration",
        C6_DECLARATION_CONTRACT_FIELDS,
        C6_COMPONENT,
        "c6.declaration.embedded_refs",
    ),
    (
        "DataSnoopingFamilyManifest",
        C6_CLOSURE_CONTRACT_FIELDS,
        C6_COMPONENT,
        "c6.family_closure.embedded_refs",
    ),
)

DEPENDENCIES = {
    C4_COMPONENT: (
        "CROSS_TABLE_REFERENCE",
        (
            "c4.questions_lineage_scope_decisions_exposures.core",
            "c4.research_program.core",
            "c4.global_oos_exposure.core",
        ),
    ),
    C5_COMPONENT: (
        "CROSS_TABLE_REFERENCE",
        (
            "c5.proposals_hypotheses.dispositions",
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
        ),
    ),
}

CONTRACT_TABLES = {
    "DataSnoopingDeclaration": "data_snooping_declarations",
    "FamilyFreezeReceipt": "data_snooping_family_freezes",
    "TrialLedgerProof": "data_snooping_family_closures",
    "DataSnoopingFamilyManifest": "data_snooping_family_closures",
    "DataSnoopingGridAssessment": "data_snooping_grid_assessments",
    "DataSnoopingAssessment": "data_snooping_assessments",
}

CANONICAL_PAYLOAD_FIELDS = {
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

TARGET_ALIASES = {
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
    (
        "FamilyFreezeReceipt",
        "ordered_search_terminal_hashes",
    ): "ordered_search_terminal_hashes_json",
    (
        "FamilyFreezeReceipt",
        "ordered_emission_seal_hashes",
    ): "ordered_emission_seal_hashes_json",
    (
        "FamilyFreezeReceipt",
        "ordered_candidate_chain_heads",
    ): "ordered_candidate_chain_heads_json",
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

NON_PERSISTED_DERIVATIONS = {
    ("DataSnoopingFamilyManifest", "authority_head"): "c8.closure.authority_head",
}

C9_FORBIDDEN_CONTRACTS = {
    "DataSnoopingEvidenceRef",
    "ArtifactCore",
    "ArtifactCoreScanReceipt",
    "PublishedArtifactEnvelope",
    "ArtifactPublicationReceipt",
    "ArtifactPublicationReceiptProjection",
}

CORE_GUARD_RELATION_TABLES = {
    "data_snooping_declarations": {
        "data_snooping_family_searches",
        "data_snooping_proposal_registrations",
        "data_snooping_oos_access_attempts",
    },
    "data_snooping_family_freezes": {
        "data_snooping_declarations",
        "data_snooping_family_searches",
        "search_attempt_events",
        "data_snooping_search_emission_seals",
        "data_snooping_proposal_registrations",
        "data_snooping_hypotheses",
        "data_snooping_oos_access_attempts",
    },
    "data_snooping_family_closures": {
        "data_snooping_declarations",
        "data_snooping_family_freezes",
        "data_snooping_proposal_registrations",
        "data_snooping_hypotheses",
        "data_snooping_candidate_dispositions",
        "data_snooping_oos_releases",
        "data_snooping_oos_access_attempts",
        "data_snooping_evaluation_batches",
        "data_snooping_evaluation_receipts",
        "data_snooping_benchmark_returns",
        "research_authority_events",
        "selection_scope_outcomes",
    },
    "data_snooping_grid_assessments": {
        "data_snooping_declarations",
        "data_snooping_family_closures",
        "data_snooping_grid_assessments",
        "data_snooping_assessments",
    },
    "data_snooping_assessments": {
        "data_snooping_declarations",
        "data_snooping_family_closures",
        "data_snooping_grid_assessments",
    },
}


def _schema_module() -> ModuleType:
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == MODULE:
            pytest.fail(f"missing C8 schema-spec module: {MODULE}")
        raise
    return module


def _required(name: str) -> Any:
    value = getattr(_schema_module(), name, None)
    assert value is not None, f"Pass C C8 schema symbol is missing: {name}"
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
    component = _required("C8_SCHEMA_COMPONENT")
    return {table.table_name: table for table in component.tables}


def _extension_map() -> dict[str, Any]:
    component = _required("C8_SCHEMA_COMPONENT")
    return {extension.extension_id: extension for extension in component.extensions}


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


def _rule_actual_shape(rule: Any) -> tuple[object, tuple[str, ...], str | None]:
    return (
        _enum_value(rule.kind),
        tuple(rule.columns),
        rule.referenced_table,
    )


def _guard_relation_tables(guards: Sequence[Any]) -> set[str]:
    return {str(node["table"]) for guard in guards for node in _walk(_payload(guard.violation_when)) if "table" in node}


def _expression_shape(expression: Any) -> tuple[Any, ...]:
    """Return the complete closed-AST shape without erasing nested node types."""

    node_type = type(expression).__name__
    if node_type == "ColumnValue":
        return ("COLUMN", expression.column, _enum_value(expression.source))
    if node_type == "OrdinalOffsetValue":
        return (
            "ORDINAL_OFFSET",
            _expression_shape(expression.value),
            expression.offset,
        )
    if node_type == "LiteralValue":
        return ("LITERAL", _enum_value(expression.value))
    if node_type == "QualifiedColumnValue":
        return (
            "QUALIFIED_COLUMN",
            expression.relation_alias,
            expression.column,
            _enum_value(expression.source),
        )
    if node_type == "CanonicalJsonFieldValue":
        return (
            "CANONICAL_JSON_FIELD",
            _expression_shape(expression.document),
            tuple(expression.path),
            _enum_value(expression.scalar_type),
        )
    if node_type == "CanonicalJsonPayloadHashExpression":
        return (
            "CANONICAL_JSON_PAYLOAD_HASH",
            _expression_shape(expression.document),
            tuple(tuple(path) for path in expression.excluded_paths),
            _enum_value(expression.encoding),
            _enum_value(expression.algorithm),
            _enum_value(expression.operator),
            _expression_shape(expression.expected_hash),
        )
    if node_type == "RelatedCanonicalJsonArrayValueSpec":
        return (
            "RELATED_JSON_ARRAY_VALUE",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            expression.column,
            _enum_value(expression.element_type),
            _enum_value(expression.projection),
            None if expression.ordinal is None else _expression_shape(expression.ordinal),
            expression.ordinal_offset,
            _enum_value(expression.source),
            _enum_value(expression.cardinality),
            _enum_value(expression.key_requirement),
        )
    if node_type == "RelatedCanonicalJsonArrayLengthValueSpec":
        return (
            "RELATED_JSON_ARRAY_LENGTH",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            expression.column,
            _enum_value(expression.element_type),
            _enum_value(expression.source),
            _enum_value(expression.cardinality),
            _enum_value(expression.key_requirement),
            expression.require_canonical_json,
            _enum_value(expression.failure_policy),
        )
    if node_type == "RelatedCanonicalJsonArrayContainsExpression":
        return (
            "RELATED_JSON_ARRAY_CONTAINS",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            expression.column,
            _enum_value(expression.element_type),
            _expression_shape(expression.element),
            expression.negated,
            _enum_value(expression.source),
            _enum_value(expression.cardinality),
            _enum_value(expression.key_requirement),
        )
    if node_type == "ComparisonExpression":
        return (
            "COMPARISON",
            _enum_value(expression.operator),
            _expression_shape(expression.left),
            _expression_shape(expression.right),
        )
    if node_type == "NullExpression":
        return (
            "NULL_TEST",
            _enum_value(expression.operator),
            _expression_shape(expression.value),
        )
    if node_type == "MembershipExpression":
        return (
            "MEMBERSHIP",
            _expression_shape(expression.value),
            tuple(_expression_shape(value) for value in expression.values),
            expression.negated,
        )
    if node_type == "LogicalExpression":
        return (
            "LOGICAL",
            _enum_value(expression.operator),
            tuple(_expression_shape(operand) for operand in expression.operands),
        )
    if node_type == "NotExpression":
        return ("NOT", _expression_shape(expression.operand))
    if node_type == "RelatedValueLookupSpec":
        return (
            "RELATED_VALUE",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            expression.value,
            _enum_value(expression.source),
            _enum_value(expression.cardinality),
            _enum_value(expression.key_requirement),
        )
    if node_type == "RelationAggregateExpression":
        return (
            "RELATION_AGGREGATE",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            _enum_value(expression.aggregate),
            _enum_value(expression.source),
            expression.value,
            tuple(
                (
                    item.column,
                    _enum_value(item.direction),
                    _enum_value(item.collation),
                )
                for item in expression.order
            ),
            _enum_value(expression.operator),
            _expression_shape(expression.expected),
            None if expression.predicate is None else _expression_shape(expression.predicate),
        )
    if node_type == "RelationJoinAggregateExpression":
        return (
            "RELATION_JOIN_AGGREGATE",
            expression.anchor_table,
            expression.anchor_alias,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            tuple(
                (
                    join.table,
                    join.alias,
                    tuple(
                        (
                            item.left_alias,
                            item.left_column,
                            item.right_alias,
                            item.right_column,
                        )
                        for item in join.on
                    ),
                    None if join.predicate is None else _expression_shape(join.predicate),
                    _enum_value(join.cardinality),
                )
                for join in expression.joins
            ),
            _enum_value(expression.aggregate),
            _enum_value(expression.source),
            (
                None
                if expression.value is None
                else (
                    expression.value.relation_alias,
                    expression.value.column,
                )
            ),
            tuple(
                (
                    item.relation_alias,
                    item.column,
                    _enum_value(item.direction),
                    _enum_value(item.collation),
                )
                for item in expression.order
            ),
            _enum_value(expression.operator),
            _expression_shape(expression.expected),
        )
    if node_type == "RelationArgExtremumExpression":
        return (
            "RELATION_ARG_EXTREMUM",
            expression.anchor_table,
            expression.anchor_alias,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            tuple(
                (
                    join.table,
                    join.alias,
                    tuple(
                        (
                            item.left_alias,
                            item.left_column,
                            item.right_alias,
                            item.right_column,
                        )
                        for item in join.on
                    ),
                    None if join.predicate is None else _expression_shape(join.predicate),
                    _enum_value(join.cardinality),
                )
                for join in expression.joins
            ),
            (
                expression.metric.metric_id,
                tuple((item.relation_alias, item.column) for item in expression.metric.inputs),
            ),
            _enum_value(expression.extremum),
            tuple(
                (
                    item.relation_alias,
                    item.column,
                    _enum_value(item.direction),
                    _enum_value(item.collation),
                )
                for item in expression.tie_break
            ),
            tuple((item.relation_alias, item.column) for item in expression.result),
            _enum_value(expression.operator),
            tuple(_expression_shape(item) for item in expression.expected),
        )
    if node_type == "RelationPolicyAggregateExpression":
        return (
            "RELATION_POLICY_AGGREGATE",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            expression.policy_id,
            _enum_value(expression.source),
            tuple(expression.inputs),
            tuple(
                (
                    item.column,
                    _enum_value(item.direction),
                    _enum_value(item.collation),
                )
                for item in expression.order
            ),
            _enum_value(expression.operator),
            _expression_shape(expression.expected),
            None if expression.predicate is None else _expression_shape(expression.predicate),
            tuple((item.column, _expression_shape(item.value)) for item in expression.include_row),
            _expression_shape(expression.declared_domain),
            expression.domain_column,
            expression.latest_ordinal_column,
            _expression_shape(expression.include_latest_ordinal),
            _enum_value(expression.latest_cardinality),
        )
    if node_type == "RelationLatestPerDomainPolicyExpression":
        return (
            "RELATION_LATEST_PER_DOMAIN_POLICY",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            expression.policy_id,
            _enum_value(expression.source),
            tuple(expression.inputs),
            tuple(
                (
                    item.column,
                    _enum_value(item.direction),
                    _enum_value(item.collation),
                )
                for item in expression.order
            ),
            _expression_shape(expression.expected_verdict),
            _expression_shape(expression.expected_ordered_record_hashes_hash),
            _expression_shape(expression.declared_domain),
            expression.domain_column,
            expression.latest_ordinal_column,
            _enum_value(expression.latest_cardinality),
            expression.negated,
        )
    if node_type == "PrefixStreamExtensionExpression":
        return (
            "PREFIX_STREAM_EXTENSION",
            expression.previous_table,
            tuple(
                (binding.related_column, _expression_shape(binding.value)) for binding in expression.previous_bindings
            ),
            expression.previous_prefix_hash_column,
            expression.previous_replication_count_column,
            expression.previous_period_count_column,
            expression.declaration_table,
            tuple(
                (binding.related_column, _expression_shape(binding.value))
                for binding in expression.declaration_bindings
            ),
            tuple(expression.algorithm_columns),
            _expression_shape(expression.current_prefix_hash),
            _expression_shape(expression.current_replication_count),
            _expression_shape(expression.current_period_count),
            _expression_shape(expression.block_length),
            _expression_shape(expression.current_ordinal),
            _expression_shape(expression.genesis_hash),
            expression.first_ordinal,
            _enum_value(expression.predecessor_cardinality),
            expression.genesis_empty_prefix,
            expression.negated,
        )
    if node_type == "ChainPredecessorExpression":
        return (
            "CHAIN_PREDECESSOR",
            expression.table,
            tuple(
                (binding.related_column, _expression_shape(binding.value)) for binding in expression.partition_bindings
            ),
            expression.ordinal_column,
            _expression_shape(expression.current_ordinal),
            expression.record_hash_column,
            _expression_shape(expression.current_previous_hash),
            _expression_shape(expression.genesis_hash),
            expression.negated,
        )
    if node_type == "RelationChainPrefixExpression":
        return (
            "RELATION_CHAIN_PREFIX",
            expression.table,
            tuple(
                (binding.related_column, _expression_shape(binding.value))
                for binding in expression.partition_bindings
            ),
            expression.ordinal_column,
            expression.record_hash_column,
            expression.previous_hash_column,
            _expression_shape(expression.ancestor_hash),
            _expression_shape(expression.head_hash),
            _enum_value(expression.source),
            _enum_value(expression.proof),
            expression.negated,
        )
    if node_type == "RelationSetEqualityExpression":
        return (
            "RELATION_SET_EQUALITY",
            expression.left_table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.left_bindings),
            expression.left_value,
            None if expression.left_predicate is None else _expression_shape(expression.left_predicate),
            tuple(_expression_shape(value) for value in expression.left_include_values),
            expression.right_table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.right_bindings),
            expression.right_value,
            None if expression.right_predicate is None else _expression_shape(expression.right_predicate),
            tuple(
                (
                    item.column,
                    _enum_value(item.direction),
                    _enum_value(item.collation),
                )
                for item in expression.order
            ),
            expression.negated,
        )
    if node_type == "RelationExistsExpression":
        return (
            "RELATION_EXISTS",
            expression.table,
            tuple((binding.related_column, _expression_shape(binding.value)) for binding in expression.bindings),
            None if expression.predicate is None else _expression_shape(expression.predicate),
            expression.negated,
        )
    raise AssertionError(f"unknown closed schema-AST node: {node_type}")


def _expression_nodes(expression: Any):
    """Yield typed predicate nodes without dataclass ``asdict`` type loss."""

    yield expression
    node_type = type(expression).__name__
    if node_type == "LogicalExpression":
        for operand in expression.operands:
            yield from _expression_nodes(operand)
    elif node_type == "NotExpression":
        yield from _expression_nodes(expression.operand)
    elif node_type in {"RelationExistsExpression", "RelationAggregateExpression"}:
        if expression.predicate is not None:
            yield from _expression_nodes(expression.predicate)
    elif node_type == "RelationJoinAggregateExpression":
        for join in expression.joins:
            if join.predicate is not None:
                yield from _expression_nodes(join.predicate)
    elif node_type == "RelationArgExtremumExpression":
        for join in expression.joins:
            if join.predicate is not None:
                yield from _expression_nodes(join.predicate)
    elif node_type == "RelationPolicyAggregateExpression":
        if expression.predicate is not None:
            yield from _expression_nodes(expression.predicate)


def _ast_column(column: str, source: str = "CURRENT") -> tuple[Any, ...]:
    return ("COLUMN", column, source)


def _ast_ordinal_offset(value: tuple[Any, ...], offset: int) -> tuple[Any, ...]:
    return ("ORDINAL_OFFSET", value, offset)


def _ast_literal(value: object) -> tuple[Any, ...]:
    return ("LITERAL", value)


def _ast_qualified_column(
    relation_alias: str,
    column: str,
    source: str = "RELATED",
) -> tuple[Any, ...]:
    return ("QUALIFIED_COLUMN", relation_alias, column, source)


def _ast_json_field(
    column: str,
    path: tuple[str, ...],
    scalar_type: str,
    source: str = "NEW",
) -> tuple[Any, ...]:
    return (
        "CANONICAL_JSON_FIELD",
        _ast_column(column, source),
        path,
        scalar_type,
    )


def _ast_json_payload_hash(
    column: str,
    excluded_paths: tuple[tuple[str, ...], ...],
    expected_hash_column: str,
    *,
    source: str = "NEW",
    operator: str = "NE",
) -> tuple[Any, ...]:
    return (
        "CANONICAL_JSON_PAYLOAD_HASH",
        _ast_column(column, source),
        excluded_paths,
        "QUANTOS_CANONICAL_JSON_V1",
        "SHA256",
        operator,
        _ast_column(expected_hash_column, source),
    )


def _ast_json_document_hash(
    column: str,
    expected_hash: tuple[Any, ...],
    *,
    source: str = "NEW",
    operator: str = "NE",
) -> tuple[Any, ...]:
    return (
        "CANONICAL_JSON_PAYLOAD_HASH",
        _ast_column(column, source),
        (),
        "QUANTOS_CANONICAL_JSON_V1",
        "SHA256",
        operator,
        expected_hash,
    )


def _ast_related_json_array(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    column: str,
    element_type: str,
    projection: str,
    *,
    ordinal: tuple[Any, ...] | None = None,
    ordinal_offset: int = 0,
) -> tuple[Any, ...]:
    return (
        "RELATED_JSON_ARRAY_VALUE",
        table,
        bindings,
        column,
        element_type,
        projection,
        ordinal,
        ordinal_offset,
        "RELATED",
        "EXACTLY_ONE",
        "PRIMARY_KEY_OR_EXACT_UNIQUE",
    )


def _ast_related_json_length(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    column: str,
    element_type: str,
) -> tuple[Any, ...]:
    return (
        "RELATED_JSON_ARRAY_LENGTH",
        table,
        bindings,
        column,
        element_type,
        "RELATED",
        "EXACTLY_ONE",
        "PRIMARY_KEY_OR_EXACT_UNIQUE",
        True,
        "REJECT",
    )


def _ast_related_json_contains(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    column: str,
    element_type: str,
    element: tuple[Any, ...],
    *,
    negated: bool,
) -> tuple[Any, ...]:
    return (
        "RELATED_JSON_ARRAY_CONTAINS",
        table,
        bindings,
        column,
        element_type,
        element,
        negated,
        "RELATED",
        "EXACTLY_ONE",
        "PRIMARY_KEY_OR_EXACT_UNIQUE",
    )


def _ast_compare(
    operator: str,
    left: tuple[Any, ...],
    right: tuple[Any, ...],
) -> tuple[Any, ...]:
    return ("COMPARISON", operator, left, right)


def _ast_eq(column: str, value: object, source: str = "CURRENT") -> tuple[Any, ...]:
    return _ast_compare("EQ", _ast_column(column, source), _ast_literal(value))


def _ast_ne(column: str, value: object, source: str = "CURRENT") -> tuple[Any, ...]:
    return _ast_compare("NE", _ast_column(column, source), _ast_literal(value))


def _ast_null(
    column: str,
    operator: str,
    source: str = "CURRENT",
) -> tuple[Any, ...]:
    return ("NULL_TEST", operator, _ast_column(column, source))


def _ast_membership(
    column: str,
    values: tuple[object, ...],
    *,
    source: str = "CURRENT",
    negated: bool = False,
) -> tuple[Any, ...]:
    return (
        "MEMBERSHIP",
        _ast_column(column, source),
        tuple(_ast_literal(value) for value in values),
        negated,
    )


def _ast_logical(operator: str, *operands: tuple[Any, ...]) -> tuple[Any, ...]:
    return ("LOGICAL", operator, operands)


def _ast_relation(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    *,
    predicate: tuple[Any, ...] | None = None,
    negated: bool = False,
) -> tuple[Any, ...]:
    return ("RELATION_EXISTS", table, bindings, predicate, negated)


def _ast_related_value(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    value: str,
) -> tuple[Any, ...]:
    return (
        "RELATED_VALUE",
        table,
        bindings,
        value,
        "RELATED",
        "EXACTLY_ONE",
        "PRIMARY_KEY_OR_EXACT_UNIQUE",
    )


def _ast_aggregate(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    aggregate: str,
    *,
    value: str | None = None,
    order: tuple[tuple[str, str, str], ...] = (),
    operator: str = "EQ",
    expected: tuple[Any, ...] = ("LITERAL", 0),
    predicate: tuple[Any, ...] | None = None,
) -> tuple[Any, ...]:
    return (
        "RELATION_AGGREGATE",
        table,
        bindings,
        aggregate,
        "RELATED",
        value,
        order,
        operator,
        expected,
        predicate,
    )


def _ast_join_aggregate(
    anchor_table: str,
    anchor_alias: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    joins: tuple[
        tuple[
            str,
            str,
            tuple[tuple[str, str, str, str], ...],
            tuple[Any, ...] | None,
            str,
        ],
        ...,
    ],
    aggregate: str,
    *,
    value: tuple[str, str] | None,
    order: tuple[tuple[str, str, str, str], ...],
    operator: str,
    expected: tuple[Any, ...],
) -> tuple[Any, ...]:
    return (
        "RELATION_JOIN_AGGREGATE",
        anchor_table,
        anchor_alias,
        bindings,
        joins,
        aggregate,
        "RELATED",
        value,
        order,
        operator,
        expected,
    )


def _ast_arg_extremum(
    anchor_table: str,
    anchor_alias: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    joins: tuple[
        tuple[
            str,
            str,
            tuple[tuple[str, str, str, str], ...],
            tuple[Any, ...] | None,
            str,
        ],
        ...,
    ],
    metric: tuple[str, tuple[tuple[str, str], ...]],
    extremum: str,
    tie_break: tuple[tuple[str, str, str, str], ...],
    result: tuple[tuple[str, str], ...],
    operator: str,
    expected: tuple[tuple[Any, ...], ...],
) -> tuple[Any, ...]:
    return (
        "RELATION_ARG_EXTREMUM",
        anchor_table,
        anchor_alias,
        bindings,
        joins,
        metric,
        extremum,
        tie_break,
        result,
        operator,
        expected,
    )


def _ast_policy_aggregate(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    policy_id: str,
    inputs: tuple[str, ...],
    order: tuple[tuple[str, str, str], ...],
    *,
    operator: str,
    expected: tuple[Any, ...],
    predicate: tuple[Any, ...] | None,
    include_row: tuple[tuple[str, tuple[Any, ...]], ...],
    declared_domain: tuple[Any, ...],
    domain_column: str,
    latest_ordinal_column: str,
    include_latest_ordinal: tuple[Any, ...],
    latest_cardinality: str = "EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE",
) -> tuple[Any, ...]:
    return (
        "RELATION_POLICY_AGGREGATE",
        table,
        bindings,
        policy_id,
        "RELATED",
        inputs,
        order,
        operator,
        expected,
        predicate,
        include_row,
        declared_domain,
        domain_column,
        latest_ordinal_column,
        include_latest_ordinal,
        latest_cardinality,
    )


def _ast_latest_per_domain_policy(
    table: str,
    bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    policy_id: str,
    inputs: tuple[str, ...],
    order: tuple[tuple[str, str, str], ...],
    expected_verdict: tuple[Any, ...],
    expected_ordered_record_hashes_hash: tuple[Any, ...],
    declared_domain: tuple[Any, ...],
    domain_column: str,
    latest_ordinal_column: str,
    *,
    negated: bool,
    latest_cardinality: str = "EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE",
) -> tuple[Any, ...]:
    return (
        "RELATION_LATEST_PER_DOMAIN_POLICY",
        table,
        bindings,
        policy_id,
        "RELATED",
        inputs,
        order,
        expected_verdict,
        expected_ordered_record_hashes_hash,
        declared_domain,
        domain_column,
        latest_ordinal_column,
        latest_cardinality,
        negated,
    )


def _ast_prefix_extension(
    previous_table: str,
    previous_bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    previous_prefix_hash_column: str,
    previous_replication_count_column: str,
    previous_period_count_column: str,
    declaration_table: str,
    declaration_bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    algorithm_columns: tuple[str, ...],
    current_prefix_hash: tuple[Any, ...],
    current_replication_count: tuple[Any, ...],
    current_period_count: tuple[Any, ...],
    block_length: tuple[Any, ...],
    current_ordinal: tuple[Any, ...],
    genesis_hash: tuple[Any, ...],
    *,
    negated: bool,
) -> tuple[Any, ...]:
    return (
        "PREFIX_STREAM_EXTENSION",
        previous_table,
        previous_bindings,
        previous_prefix_hash_column,
        previous_replication_count_column,
        previous_period_count_column,
        declaration_table,
        declaration_bindings,
        algorithm_columns,
        current_prefix_hash,
        current_replication_count,
        current_period_count,
        block_length,
        current_ordinal,
        genesis_hash,
        1,
        "ZERO_AT_GENESIS_ELSE_EXACTLY_ONE",
        True,
        negated,
    )


def _ast_chain_predecessor(
    table: str,
    partition_bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    ordinal_column: str,
    current_ordinal: tuple[Any, ...],
    record_hash_column: str,
    current_previous_hash: tuple[Any, ...],
    genesis_hash: tuple[Any, ...],
    *,
    negated: bool,
) -> tuple[Any, ...]:
    return (
        "CHAIN_PREDECESSOR",
        table,
        partition_bindings,
        ordinal_column,
        current_ordinal,
        record_hash_column,
        current_previous_hash,
        genesis_hash,
        negated,
    )


def _ast_chain_prefix(
    table: str,
    partition_bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    ordinal_column: str,
    record_hash_column: str,
    previous_hash_column: str,
    ancestor_hash: tuple[Any, ...],
    head_hash: tuple[Any, ...],
    *,
    negated: bool,
) -> tuple[Any, ...]:
    return (
        "RELATION_CHAIN_PREFIX",
        table,
        partition_bindings,
        ordinal_column,
        record_hash_column,
        previous_hash_column,
        ancestor_hash,
        head_hash,
        "RELATED",
        "COMPLETE_ANCESTOR_TO_CURRENT_TIP",
        negated,
    )


def _ast_set_equality(
    left_table: str,
    left_bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    left_value: str,
    left_predicate: tuple[Any, ...] | None,
    left_include_values: tuple[tuple[Any, ...], ...],
    right_table: str,
    right_bindings: tuple[tuple[str, tuple[Any, ...]], ...],
    right_value: str,
    right_predicate: tuple[Any, ...] | None,
    order: tuple[tuple[str, str, str], ...],
    *,
    negated: bool,
) -> tuple[Any, ...]:
    return (
        "RELATION_SET_EQUALITY",
        left_table,
        left_bindings,
        left_value,
        left_predicate,
        left_include_values,
        right_table,
        right_bindings,
        right_value,
        right_predicate,
        order,
        negated,
    )


def _new_bindings(*columns: str) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    return tuple((column, _ast_column(column, "NEW")) for column in columns)


def _ast_nulls(operator: str, *columns: str) -> tuple[tuple[Any, ...], ...]:
    return tuple(_ast_null(column, operator) for column in columns)


def _guard(table_name: str, error_code: str) -> Any:
    matches = tuple(
        trigger
        for trigger in _table_map()[table_name].triggers
        if _enum_value(trigger.kind) == "INSERT_GUARD" and trigger.error_code == error_code
    )
    assert len(matches) == 1, (table_name, error_code)
    return matches[0]


def _extension_guard(extension_id: str, error_code: str) -> Any:
    matches = tuple(
        trigger
        for trigger in _extension_map()[extension_id].triggers
        if _enum_value(trigger.kind) == "INSERT_GUARD" and trigger.error_code == error_code
    )
    assert len(matches) == 1, (extension_id, error_code)
    return matches[0]


def test_c8_component_has_exact_table_extension_order_and_private_ast_only() -> None:
    module = _schema_module()
    component = _required("C8_SCHEMA_COMPONENT")
    assert component.component_id == COMPONENT_ID
    assert tuple(component.capabilities) == C8_CAPABILITIES
    assert component.tables is module.C8_AUTHORITY_TABLE_SPECS
    assert component.extensions is module.C8_TABLE_EXTENSIONS
    assert tuple(table.table_name for table in component.tables) == TABLE_ORDER
    assert tuple(extension.extension_id for extension in component.extensions) == EXTENSION_ORDER
    assert isinstance(module.C8_AUTHORITY_TABLE_SPECS, tuple)
    assert isinstance(module.C8_TABLE_EXTENSIONS, tuple)
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


def test_every_c8_table_is_genesis_bound_append_only_and_timestamped_in_us() -> None:
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
        assert all(columns[column_name].nullable is False for column_name in TIMESTAMP_COLUMNS[table_name])
        check_columns = {
            str(node["column"])
            for check in table.checks
            for node in _walk(_payload(check.expression))
            if "column" in node
        }
        assert TIMESTAMP_COLUMNS[table_name].issubset(check_columns), table_name


def test_grid_assessment_is_the_only_c8_chain_and_uses_declared_schedule_order() -> None:
    tables = _table_map()
    assert tuple(name for name in TABLE_ORDER if tables[name].chain is not None) == (
        "data_snooping_grid_assessments",
    )
    chain = tables["data_snooping_grid_assessments"].chain
    assert tuple(chain.partition_key_columns) == (
        "authority_instance_id",
        "family_id",
        "block_length",
    )
    assert chain.ordinal_column == "schedule_ordinal"
    assert _enum_value(chain.genesis_rule) == "FAMILY_AUTHORITY_GENESIS_RECORD_HASH"
    assert chain.previous_hash_column == "prev_record_hash"
    assert chain.record_hash_column == "record_hash"
    assert _enum_value(chain.terminal_head_rule) == "LATEST_RECORD_HASH"


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


def test_load_bearing_closure_grid_and_family_conclusion_branches_have_exact_recursive_ast() -> None:
    closure_checks = {check.name: check for check in _table_map()["data_snooping_family_closures"].checks}
    closure_actual = _expression_shape(closure_checks["data_snooping_family_closures_exact_branch"].expression)
    completed_reference_columns = (
        "attempt_id",
        "terminal_attempt_event_id",
        "evaluation_batch_id",
        "evaluation_batch_receipt_hash",
        "oos_release_id",
        "oos_access_attempt_terminal_hash",
        "oos_release_record_hash",
    )
    result_columns = (
        "ordered_period_keys_hash",
        "candidate_series_set_hash",
        "benchmark_receipt_hash",
        "cost_model_input_set_hash",
        "winner_config_hash",
        "winner_evaluation_receipt_hash",
    )
    common_not_assessable = (
        *_ast_nulls(
            "IS_NULL",
            "evaluation_batch_id",
            "evaluation_batch_receipt_hash",
            "oos_release_id",
            "oos_release_record_hash",
            *result_columns,
        ),
        _ast_eq("ordered_config_hashes_json", "[]"),
        _ast_eq("ordered_evaluation_receipt_hashes_json", "[]"),
        _ast_ne("failure_reasons_json", "[]"),
    )
    closure_expected = _ast_logical(
        "OR",
        _ast_logical(
            "AND",
            _ast_eq("closure_status", "COMPLETE"),
            *_ast_nulls("IS_NOT_NULL", *completed_reference_columns),
            _ast_eq("performance_access_attempt_count", 1),
            *_ast_nulls("IS_NOT_NULL", *result_columns),
            _ast_ne("ordered_config_hashes_json", "[]"),
            _ast_ne("ordered_evaluation_receipt_hashes_json", "[]"),
            _ast_eq("failure_reasons_json", "[]"),
        ),
        _ast_logical(
            "AND",
            _ast_eq("closure_status", "NOT_ASSESSABLE"),
            *_ast_nulls(
                "IS_NULL",
                "attempt_id",
                "terminal_attempt_event_id",
                "oos_access_attempt_terminal_hash",
            ),
            _ast_eq("performance_access_attempt_count", 0),
            *common_not_assessable,
        ),
        _ast_logical(
            "AND",
            _ast_eq("closure_status", "NOT_ASSESSABLE"),
            *_ast_nulls(
                "IS_NOT_NULL",
                "attempt_id",
                "terminal_attempt_event_id",
                "oos_access_attempt_terminal_hash",
            ),
            _ast_eq("performance_access_attempt_count", 1),
            *common_not_assessable,
        ),
    )
    assert closure_actual == closure_expected

    grid_checks = {
        check.name: check
        for check in _table_map()["data_snooping_grid_assessments"].checks
    }
    grid_actual = _expression_shape(
        grid_checks["data_snooping_grid_assessments_exact_branch"].expression
    )
    kernel_columns = (
        "reality_check_payload_json",
        "reality_check_result_hash",
        "spa_payload_json",
        "spa_result_hash",
    )
    valid_grid_evidence = (
        *_ast_nulls("IS_NOT_NULL", *kernel_columns),
        _ast_eq("failure_reasons_json", "[]"),
    )
    grid_expected = _ast_logical(
        "OR",
        _ast_logical(
            "AND",
            _ast_eq("grid_point_status", "RESOLVED"),
            _ast_membership(
                "grid_point_verdict",
                (
                    "SUPPORTS_SUPERIORITY",
                    "DOES_NOT_SUPPORT_SUPERIORITY",
                    "METHOD_DISAGREEMENT",
                ),
            ),
            *valid_grid_evidence,
        ),
        _ast_logical(
            "AND",
            _ast_eq("grid_point_status", "RESOLVED"),
            _ast_eq("grid_point_verdict", "PENDING_MORE_REPLICATIONS"),
            *valid_grid_evidence,
        ),
        _ast_logical(
            "AND",
            _ast_eq("grid_point_status", "AMBIGUOUS"),
            _ast_eq("grid_point_verdict", "PENDING_MORE_REPLICATIONS"),
            *valid_grid_evidence,
        ),
        _ast_logical(
            "AND",
            _ast_eq("grid_point_status", "AMBIGUOUS"),
            _ast_eq("grid_point_verdict", "MONTE_CARLO_UNRESOLVED"),
            *valid_grid_evidence,
        ),
        _ast_logical(
            "AND",
            _ast_eq("grid_point_status", "INVALID"),
            _ast_eq("grid_point_verdict", "NOT_APPLICABLE"),
            *_ast_nulls("IS_NULL", *kernel_columns),
            _ast_ne("failure_reasons_json", "[]"),
        ),
    )
    assert grid_actual == grid_expected

    assessment_checks = {
        check.name: check for check in _table_map()["data_snooping_assessments"].checks
    }
    assessment_actual = _expression_shape(
        assessment_checks["data_snooping_assessments_exact_branch"].expression
    )
    derived_columns = (
        "candidate_order_hash",
        "period_count",
        "minimum_replication_count",
    )
    assessment_expected = _ast_logical(
        "OR",
        _ast_logical(
            "AND",
            _ast_eq("assessment_status", "ASSESSED"),
            _ast_membership(
                "diagnostic_verdict",
                (
                    "SUPPORTS_SUPERIORITY",
                    "DOES_NOT_SUPPORT_SUPERIORITY",
                    "METHOD_DISAGREEMENT",
                    "MONTE_CARLO_UNRESOLVED",
                ),
            ),
            _ast_ne("ordered_latest_grid_assessment_hashes_json", "[]"),
            *_ast_nulls("IS_NOT_NULL", *derived_columns),
            _ast_compare("GE", _ast_column("period_count"), _ast_literal(20)),
            _ast_compare(
                "GE",
                _ast_column("minimum_replication_count"),
                _ast_literal(10_000),
            ),
            _ast_eq("failure_reasons_json", "[]"),
        ),
        _ast_logical(
            "AND",
            _ast_eq("assessment_status", "NOT_ASSESSABLE"),
            _ast_eq("diagnostic_verdict", "NOT_APPLICABLE"),
            *_ast_nulls("IS_NULL", *derived_columns),
            _ast_ne("failure_reasons_json", "[]"),
        ),
    )
    assert assessment_actual == assessment_expected


def test_all_core_verifier_obligations_are_complete_and_exact() -> None:
    for table_name, required in REQUIRED_VERIFICATION_RULES.items():
        rules = {rule.rule_id for rule in _table_map()[table_name].verification_rules}
        assert rules == required, table_name


def test_every_non_fk_same_database_reference_has_an_exact_typed_reason() -> None:
    for table_name, expected in NON_FK_TYPED_REFERENCES.items():
        rules = {rule.rule_id: rule for rule in _table_map()[table_name].verification_rules}
        for rule_id, (kind, columns, referenced_table) in expected.items():
            rule = rules[rule_id]
            assert _enum_value(rule.kind) == kind, rule_id
            assert tuple(rule.columns) == columns, rule_id
            assert rule.referenced_table == referenced_table, rule_id


def test_all_owned_insert_guards_are_complete_exact_and_fail_closed() -> None:
    for table_name, expected_codes in GUARD_ERROR_CODES.items():
        guards = tuple(
            trigger for trigger in _table_map()[table_name].triggers if _enum_value(trigger.kind) == "INSERT_GUARD"
        )
        assert tuple(guard.error_code for guard in guards) == expected_codes, table_name
        assert all(guard.violation_when is not None for guard in guards), table_name
        assert all(_enum_value(guard.event) == "INSERT" for guard in guards), table_name
        assert all(_enum_value(guard.timing) == "BEFORE" for guard in guards), table_name
        assert all(_enum_value(guard.effect) == "ABORT" for guard in guards), table_name
        assert _guard_relation_tables(guards) == CORE_GUARD_RELATION_TABLES[table_name]


def test_core_exact_declaration_freeze_and_closure_guards_bind_one_whole_parent_row() -> None:
    declaration_exact = _ast_relation(
        "data_snooping_declarations",
        (
            *_new_bindings("authority_instance_id", "family_id", "declaration_id"),
            ("record_hash", _ast_column("declaration_record_hash", "NEW")),
        ),
        negated=True,
    )
    assert (
        _expression_shape(_guard("data_snooping_family_freezes", "FREEZE_DECLARATION_EXACT").violation_when)
        == declaration_exact
    )

    freeze_exact = _ast_relation(
        "data_snooping_family_freezes",
        (
            *_new_bindings("authority_instance_id", "family_id", "freeze_id"),
            ("record_hash", _ast_column("freeze_record_hash", "NEW")),
        ),
        predicate=_ast_logical(
            "AND",
            *(
                _ast_compare(
                    "EQ",
                    _ast_column(column, "RELATED"),
                    _ast_column(column, "NEW"),
                )
                for column in (
                    "ordered_search_ids_hash",
                    "ordered_search_terminal_hashes_hash",
                    "ordered_emission_seal_hashes_hash",
                )
            ),
        ),
        negated=True,
    )
    assert (
        _expression_shape(_guard("data_snooping_family_closures", "CLOSURE_FREEZE_EXACT").violation_when)
        == freeze_exact
    )

    closure_exact = _ast_relation(
        "data_snooping_family_closures",
        (
            *_new_bindings("authority_instance_id", "family_id"),
            ("record_hash", _ast_column("closure_manifest_hash", "NEW")),
        ),
        negated=True,
    )
    assert (
        _expression_shape(_guard("data_snooping_assessments", "ASSESSMENT_CLOSURE_EXACT").violation_when)
        == closure_exact
    )


def test_freeze_binds_exact_declared_data_code_dlp_and_stopping_identity() -> None:
    bindings = (
        *_new_bindings("authority_instance_id", "family_id", "declaration_id"),
        ("record_hash", _ast_column("declaration_record_hash", "NEW")),
    )
    expected = _ast_relation(
        "data_snooping_declarations",
        bindings,
        predicate=_ast_logical(
            "AND",
            *(
                _ast_compare(
                    "EQ",
                    _ast_column(column, "RELATED"),
                    _ast_column(column, "NEW"),
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
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_freezes",
                "FREEZE_DATA_CODE_DLP_STABLE",
            ).violation_when
        )
        == expected
    )


def test_freeze_totals_and_ordered_hashes_are_closed_relation_aggregates() -> None:
    family_bindings = _new_bindings("authority_instance_id", "family_id")
    declaration_bindings = (
        *family_bindings,
        ("declaration_id", _ast_column("declaration_id", "NEW")),
        ("record_hash", _ast_column("declaration_record_hash", "NEW")),
    )
    search_order = (
        ("link_ordinal", "ASC", "BINARY"),
        ("search_id", "ASC", "BINARY"),
    )
    seal_order = (("search_id", "ASC", "BINARY"),)
    config_order = (("config_hash", "ASC", "BINARY"),)

    expected_by_guard = {
        "FREEZE_ALL_DECLARED_SEARCHES_PRESENT": _ast_logical(
            "OR",
            _ast_relation(
                "data_snooping_declarations",
                declaration_bindings,
                predicate=_ast_logical(
                    "AND",
                    _ast_compare(
                        "EQ",
                        _ast_column("required_search_count", "RELATED"),
                        _ast_column("search_count", "NEW"),
                    ),
                    _ast_compare(
                        "EQ",
                        _ast_column("maximum_search_count", "RELATED"),
                        _ast_column("search_count", "NEW"),
                    ),
                ),
                negated=True,
            ),
            _ast_aggregate(
                "data_snooping_family_searches",
                family_bindings,
                "COUNT_ROWS",
                operator="NE",
                expected=_ast_column("search_count", "NEW"),
            ),
            _ast_aggregate(
                "data_snooping_family_searches",
                family_bindings,
                "CANONICAL_ORDERED_VALUE_HASH",
                value="search_id",
                order=search_order,
                operator="NE",
                expected=_ast_column("ordered_search_ids_hash", "NEW"),
            ),
        ),
        "FREEZE_SEARCHES_TERMINAL": _ast_join_aggregate(
            "data_snooping_family_searches",
            "family_search",
            family_bindings,
            (
                (
                    "search_attempt_events",
                    "terminal_event",
                    (("family_search", "search_id", "terminal_event", "search_id"),),
                    (
                        "MEMBERSHIP",
                        _ast_qualified_column("terminal_event", "status"),
                        (
                            _ast_literal("FAILED"),
                            _ast_literal("COMPLETED"),
                        ),
                        False,
                    ),
                    "EXACTLY_ONE",
                ),
            ),
            "CANONICAL_ORDERED_VALUE_HASH",
            value=("terminal_event", "event_hash"),
            order=(
                ("family_search", "link_ordinal", "ASC", "BINARY"),
                ("family_search", "search_id", "ASC", "BINARY"),
            ),
            operator="NE",
            expected=_ast_column("ordered_search_terminal_hashes_hash", "NEW"),
        ),
        "FREEZE_EMISSION_SEALS_COMPLETE": _ast_logical(
            "OR",
            _ast_aggregate(
                "data_snooping_search_emission_seals",
                family_bindings,
                "COUNT_ROWS",
                operator="NE",
                expected=_ast_column("search_count", "NEW"),
            ),
            _ast_aggregate(
                "data_snooping_search_emission_seals",
                family_bindings,
                "CANONICAL_ORDERED_VALUE_HASH",
                value="record_hash",
                order=seal_order,
                operator="NE",
                expected=_ast_column("ordered_emission_seal_hashes_hash", "NEW"),
            ),
            _ast_aggregate(
                "data_snooping_search_emission_seals",
                family_bindings,
                "CANONICAL_ORDERED_VALUE_HASH",
                value="candidate_chain_head",
                order=seal_order,
                operator="NE",
                expected=_ast_column("ordered_candidate_chain_heads_hash", "NEW"),
            ),
        ),
        "FREEZE_COUNTS_MATCH": _ast_logical(
            "OR",
            _ast_aggregate(
                "data_snooping_proposal_registrations",
                family_bindings,
                "COUNT_ROWS",
                operator="NE",
                expected=_ast_column("raw_proposal_count", "NEW"),
            ),
            _ast_aggregate(
                "data_snooping_hypotheses",
                family_bindings,
                "COUNT_DISTINCT",
                value="config_hash",
                operator="NE",
                expected=_ast_column("unique_performance_hypothesis_count", "NEW"),
            ),
            _ast_aggregate(
                "data_snooping_hypotheses",
                family_bindings,
                "CANONICAL_ORDERED_VALUE_HASH",
                value="config_hash",
                order=config_order,
                operator="NE",
                expected=_ast_column("frozen_config_set_hash", "NEW"),
            ),
        ),
    }
    for error_code, expected in expected_by_guard.items():
        actual = _guard("data_snooping_family_freezes", error_code).violation_when
        assert _expression_shape(actual) == expected, error_code


def test_search_rows_match_every_declared_plan_axis_at_one_based_ordinal() -> None:
    bindings = (
        *_new_bindings("authority_instance_id", "family_id", "declaration_id"),
        ("record_hash", _ast_column("declaration_record_hash", "NEW")),
    )
    ordinal = _ast_column("link_ordinal", "NEW")
    expected = _ast_logical(
        "OR",
        _ast_related_json_contains(
            "data_snooping_declarations",
            bindings,
            "ordered_research_program_ids_json",
            "TEXT",
            _ast_column("research_program_id", "NEW"),
            negated=True,
        ),
        _ast_compare(
            "NE",
            _ast_column("search_kind", "NEW"),
            _ast_related_json_array(
                "data_snooping_declarations",
                bindings,
                "allowed_search_kinds_json",
                "TEXT",
                "ELEMENT_AT_ORDINAL",
                ordinal=ordinal,
                ordinal_offset=-1,
            ),
        ),
        _ast_compare(
            "NE",
            _ast_column("generator_seed", "NEW"),
            _ast_related_json_array(
                "data_snooping_declarations",
                bindings,
                "generator_seeds_json",
                "INTEGER",
                "ELEMENT_AT_ORDINAL",
                ordinal=ordinal,
                ordinal_offset=-1,
            ),
        ),
        _ast_compare(
            "NE",
            _ast_column("proposal_budget", "NEW"),
            _ast_related_json_array(
                "data_snooping_declarations",
                bindings,
                "search_proposal_budgets_json",
                "INTEGER",
                "ELEMENT_AT_ORDINAL",
                ordinal=ordinal,
                ordinal_offset=-1,
            ),
        ),
        _ast_relation(
            "data_snooping_declarations",
            bindings,
            predicate=_ast_logical(
                "AND",
                _ast_compare(
                    "EQ",
                    _ast_column("stopping_rule_hash", "RELATED"),
                    _ast_column("stopping_rule_hash", "NEW"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_column("external_ingestion_deadline_us", "RELATED"),
                    _ast_column("external_ingestion_deadline_us", "NEW"),
                ),
            ),
            negated=True,
        ),
    )
    assert (
        _expression_shape(
            _extension_guard(C5_EXTENSION_ORDER[0], "SEARCH_MATCHES_DECLARED_PLAN").violation_when
        )
        == expected
    )


def test_closure_four_counts_recompute_rows_distinct_configs_and_logical_starts() -> None:
    family_bindings = _new_bindings("authority_instance_id", "family_id")
    expected = _ast_logical(
        "OR",
        _ast_aggregate(
            "data_snooping_proposal_registrations",
            family_bindings,
            "COUNT_ROWS",
            operator="NE",
            expected=_ast_column("raw_proposal_count", "NEW"),
        ),
        _ast_aggregate(
            "data_snooping_hypotheses",
            family_bindings,
            "COUNT_DISTINCT",
            value="config_hash",
            operator="NE",
            expected=_ast_column("unique_performance_hypothesis_count", "NEW"),
        ),
        _ast_aggregate(
            "data_snooping_oos_access_attempts",
            family_bindings,
            "COUNT_DISTINCT",
            value="attempt_id",
            operator="NE",
            expected=_ast_column("performance_access_attempt_count", "NEW"),
            predicate=_ast_logical(
                "AND",
                _ast_eq("sequence_no", 0, "RELATED"),
                _ast_eq("status", "STARTED", "RELATED"),
            ),
        ),
        _ast_compare(
            "NE",
            _ast_column("global_unique_config_count", "NEW"),
            _ast_json_field(
                "trial_ledger_proof_json",
                ("global_unique_config_count",),
                "INTEGER",
            ),
        ),
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_FOUR_COUNTS_RECOMPUTED",
            ).violation_when
        )
        == expected
    )


def test_closure_trial_ledger_count_is_bound_to_the_verified_embedded_proof() -> None:
    global_count_mismatch = _ast_compare(
        "NE",
        _ast_column("global_unique_config_count", "NEW"),
        _ast_json_field(
            "trial_ledger_proof_json",
            ("global_unique_config_count",),
            "INTEGER",
        ),
    )
    proof_hash_mismatch = _ast_json_payload_hash(
        "trial_ledger_proof_json",
        (("proof_hash",),),
        "trial_ledger_proof_hash",
    )
    expected = _ast_logical("OR", proof_hash_mismatch, global_count_mismatch)
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_TRIAL_PROOF_EXACT",
            ).violation_when
        )
        == expected
    )


def test_closure_scope_identity_uses_exact_declaration_chain_prefix_and_optional_outcome() -> None:
    declaration_bindings = (
        *_new_bindings("authority_instance_id", "family_id", "declaration_id"),
        ("record_hash", _ast_column("declaration_record_hash", "NEW")),
    )
    declaration_scope_mismatch = _ast_relation(
        "data_snooping_declarations",
        declaration_bindings,
        predicate=_ast_logical(
            "AND",
            _ast_compare(
                "EQ",
                _ast_column("selection_scope_id", "RELATED"),
                _ast_column("selection_scope_id", "NEW"),
            ),
            _ast_compare(
                "EQ",
                _ast_column("selection_decision_id", "RELATED"),
                _ast_column("selection_decision_id", "NEW"),
            ),
        ),
        negated=True,
    )
    prefix_mismatch = _ast_chain_prefix(
        "research_authority_events",
        _new_bindings("authority_instance_id"),
        "event_ordinal",
        "record_hash",
        "prev_record_hash",
        _ast_related_value(
            "data_snooping_declarations",
            declaration_bindings,
            "selection_scope_predecessor_head",
        ),
        _ast_column("selection_scope_history_head", "NEW"),
        negated=True,
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_SCOPE_HISTORY_PREFIX",
            ).violation_when
        )
        == _ast_logical("OR", declaration_scope_mismatch, prefix_mismatch)
    )

    optional_outcome_mismatch = _ast_logical(
        "AND",
        _ast_null(
            "selection_scope_outcome_record_hash",
            "IS_NOT_NULL",
            "NEW",
        ),
        _ast_relation(
            "selection_scope_outcomes",
            (
                *_new_bindings(
                    "authority_instance_id",
                    "selection_scope_id",
                    "selection_decision_id",
                    "family_id",
                ),
                (
                    "record_hash",
                    _ast_column("selection_scope_outcome_record_hash", "NEW"),
                ),
            ),
            negated=True,
        ),
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_SCOPE_OUTCOME_BINDING",
            ).violation_when
        )
        == optional_outcome_mismatch
    )


def test_closure_attempt_batch_release_guard_uses_same_parent_composite_rows() -> None:
    attempt_bindings = (
        ("authority_instance_id", _ast_column("authority_instance_id", "NEW")),
        ("attempt_id", _ast_column("attempt_id", "NEW")),
        ("attempt_event_id", _ast_column("terminal_attempt_event_id", "NEW")),
        ("family_id", _ast_column("family_id", "NEW")),
        ("record_hash", _ast_column("oos_access_attempt_terminal_hash", "NEW")),
    )
    completed_attempt_missing = _ast_relation(
        "data_snooping_oos_access_attempts",
        attempt_bindings,
        predicate=_ast_eq("status", "COMPLETED", "RELATED"),
        negated=True,
    )
    incomplete_attempt_missing = _ast_relation(
        "data_snooping_oos_access_attempts",
        attempt_bindings,
        predicate=_ast_eq("status", "INCOMPLETE", "RELATED"),
        negated=True,
    )
    batch_missing = _ast_relation(
        "data_snooping_evaluation_batches",
        (
            ("authority_instance_id", _ast_column("authority_instance_id", "NEW")),
            ("evaluation_batch_id", _ast_column("evaluation_batch_id", "NEW")),
            ("family_id", _ast_column("family_id", "NEW")),
            ("attempt_id", _ast_column("attempt_id", "NEW")),
            ("record_hash", _ast_column("evaluation_batch_receipt_hash", "NEW")),
            ("ordered_period_keys_hash", _ast_column("ordered_period_keys_hash", "NEW")),
            ("benchmark_receipt_hash", _ast_column("benchmark_receipt_hash", "NEW")),
        ),
        negated=True,
    )
    release_missing = _ast_relation(
        "data_snooping_oos_releases",
        (
            ("authority_instance_id", _ast_column("authority_instance_id", "NEW")),
            ("family_id", _ast_column("family_id", "NEW")),
            ("attempt_id", _ast_column("attempt_id", "NEW")),
            ("oos_release_id", _ast_column("oos_release_id", "NEW")),
            ("record_hash", _ast_column("oos_release_record_hash", "NEW")),
        ),
        negated=True,
    )
    expected = _ast_logical(
        "OR",
        _ast_logical(
            "AND",
            _ast_eq("closure_status", "COMPLETE", "NEW"),
            _ast_logical(
                "OR",
                completed_attempt_missing,
                batch_missing,
                release_missing,
            ),
        ),
        _ast_logical(
            "AND",
            _ast_eq("closure_status", "NOT_ASSESSABLE", "NEW"),
            _ast_eq("performance_access_attempt_count", 1, "NEW"),
            incomplete_attempt_missing,
        ),
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_ATTEMPT_BATCH_EXACT_BRANCH",
            ).violation_when
        )
        == expected
    )


def test_complete_closure_binds_batch_arrays_calendar_series_and_cost_inputs() -> None:
    family_batch_bindings = _new_bindings(
        "authority_instance_id",
        "family_id",
        "evaluation_batch_id",
    )
    exact_batch_bindings = (
        ("authority_instance_id", _ast_column("authority_instance_id", "NEW")),
        ("evaluation_batch_id", _ast_column("evaluation_batch_id", "NEW")),
        ("family_id", _ast_column("family_id", "NEW")),
        ("attempt_id", _ast_column("attempt_id", "NEW")),
        ("record_hash", _ast_column("evaluation_batch_receipt_hash", "NEW")),
        ("ordered_period_keys_hash", _ast_column("ordered_period_keys_hash", "NEW")),
        ("benchmark_receipt_hash", _ast_column("benchmark_receipt_hash", "NEW")),
    )
    batch_config_hash = _ast_related_value(
        "data_snooping_evaluation_batches",
        exact_batch_bindings,
        "ordered_candidate_config_hashes_hash",
    )
    batch_receipt_hash = _ast_related_value(
        "data_snooping_evaluation_batches",
        exact_batch_bindings,
        "ordered_candidate_receipt_hashes_hash",
    )
    batch_declaration_join = (
        (
            "data_snooping_declarations",
            "declaration",
            (
                ("batch", "authority_instance_id", "declaration", "authority_instance_id"),
                ("batch", "family_id", "declaration", "family_id"),
            ),
            _ast_logical(
                "AND",
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("declaration", "record_hash"),
                    _ast_column("declaration_record_hash", "NEW"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("batch", "ordered_period_keys_hash"),
                    _ast_qualified_column("declaration", "ordered_period_calendar_hash"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("batch", "cost_contract_hash"),
                    _ast_qualified_column("declaration", "cost_contract_hash"),
                ),
            ),
            "EXACTLY_ONE",
        ),
    )
    mismatch = _ast_logical(
        "OR",
        _ast_json_document_hash("ordered_config_hashes_json", batch_config_hash),
        _ast_json_document_hash(
            "ordered_evaluation_receipt_hashes_json",
            batch_receipt_hash,
        ),
        _ast_join_aggregate(
            "data_snooping_evaluation_batches",
            "batch",
            exact_batch_bindings,
            batch_declaration_join,
            "COUNT_ROWS",
            value=None,
            order=(),
            operator="NE",
            expected=_ast_literal(1),
        ),
        _ast_aggregate(
            "data_snooping_evaluation_receipts",
            family_batch_bindings,
            "CANONICAL_ORDERED_VALUE_HASH",
            value="series_hash",
            order=(("config_hash", "ASC", "BINARY"),),
            operator="NE",
            expected=_ast_column("candidate_series_set_hash", "NEW"),
        ),
        _ast_aggregate(
            "data_snooping_evaluation_receipts",
            family_batch_bindings,
            "CANONICAL_ORDERED_VALUE_HASH",
            value="cost_model_input_hash",
            order=(("config_hash", "ASC", "BINARY"),),
            operator="NE",
            expected=_ast_column("cost_model_input_set_hash", "NEW"),
        ),
        _ast_relation(
            "data_snooping_benchmark_returns",
            (
                *_new_bindings(
                    "authority_instance_id",
                    "family_id",
                    "evaluation_batch_id",
                ),
                ("benchmark_receipt_hash", _ast_column("benchmark_receipt_hash", "NEW")),
            ),
            negated=True,
        ),
    )
    expected = _ast_logical(
        "AND",
        _ast_eq("closure_status", "COMPLETE", "NEW"),
        mismatch,
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_SERIES_CALENDAR_COST_TOTAL",
            ).violation_when
        )
        == expected
    )


def test_closure_proposal_disposition_totality_is_one_exact_joined_row_per_proposal() -> None:
    family_bindings = _new_bindings("authority_instance_id", "family_id")
    join = (
        (
            "data_snooping_candidate_dispositions",
            "disposition",
            (
                ("proposal", "authority_instance_id", "disposition", "authority_instance_id"),
                ("proposal", "family_id", "disposition", "family_id"),
                ("proposal", "proposal_id", "disposition", "proposal_id"),
            ),
            None,
            "EXACTLY_ONE",
        ),
    )
    order = (
        ("proposal", "search_id", "ASC", "BINARY"),
        ("proposal", "emission_ordinal", "ASC", "BINARY"),
        ("proposal", "proposal_id", "ASC", "BINARY"),
    )
    expected = _ast_logical(
        "OR",
        _ast_join_aggregate(
            "data_snooping_proposal_registrations",
            "proposal",
            family_bindings,
            join,
            "COUNT_ROWS",
            value=None,
            order=(),
            operator="NE",
            expected=_ast_column("raw_proposal_count", "NEW"),
        ),
        _ast_join_aggregate(
            "data_snooping_proposal_registrations",
            "proposal",
            family_bindings,
            join,
            "CANONICAL_ORDERED_VALUE_HASH",
            value=("proposal", "proposal_id"),
            order=order,
            operator="NE",
            expected=_ast_column("ordered_proposal_ids_hash", "NEW"),
        ),
        _ast_join_aggregate(
            "data_snooping_proposal_registrations",
            "proposal",
            family_bindings,
            join,
            "CANONICAL_ORDERED_VALUE_HASH",
            value=("disposition", "record_hash"),
            order=order,
            operator="NE",
            expected=_ast_column("ordered_disposition_record_hashes_hash", "NEW"),
        ),
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_PROPOSAL_DISPOSITIONS_TOTAL",
            ).violation_when
        )
        == expected
    )


def test_closure_winner_is_the_declared_argmax_not_a_caller_selected_receipt() -> None:
    declaration_rule_missing = _ast_relation(
        "data_snooping_declarations",
        (
            *_new_bindings("authority_instance_id", "family_id", "declaration_id"),
            ("record_hash", _ast_column("declaration_record_hash", "NEW")),
        ),
        predicate=_ast_logical(
            "AND",
            _ast_eq("objective_id", "MEAN_NET_RETURN_DIFFERENTIAL", "RELATED"),
            _ast_eq(
                "winner_selection_rule_id",
                "MAX_MEAN_NET_RETURN_DIFFERENTIAL_THEN_CONFIG_HASH_V1",
                "RELATED",
            ),
        ),
        negated=True,
    )
    benchmark_join = (
        (
            "data_snooping_benchmark_returns",
            "benchmark",
            (
                ("candidate", "authority_instance_id", "benchmark", "authority_instance_id"),
                ("candidate", "family_id", "benchmark", "family_id"),
                ("candidate", "evaluation_batch_id", "benchmark", "evaluation_batch_id"),
                ("candidate", "ordered_period_keys_hash", "benchmark", "ordered_period_keys_hash"),
                ("candidate", "cost_contract_hash", "benchmark", "cost_contract_hash"),
            ),
            _ast_compare(
                "EQ",
                _ast_qualified_column("benchmark", "benchmark_receipt_hash"),
                _ast_column("benchmark_receipt_hash", "NEW"),
            ),
            "EXACTLY_ONE",
        ),
    )
    argmax_mismatch = _ast_arg_extremum(
        "data_snooping_evaluation_receipts",
        "candidate",
        _new_bindings(
            "authority_instance_id",
            "family_id",
            "evaluation_batch_id",
        ),
        benchmark_join,
        (
            "MEAN_NET_RETURN_DIFFERENTIAL_V1",
            (
                ("candidate", "series_payload_json"),
                ("benchmark", "series_payload_json"),
            ),
        ),
        "MAX",
        (("candidate", "config_hash", "ASC", "BINARY"),),
        (
            ("candidate", "config_hash"),
            ("candidate", "evaluation_receipt_hash"),
        ),
        "NE",
        (
            _ast_column("winner_config_hash", "NEW"),
            _ast_column("winner_evaluation_receipt_hash", "NEW"),
        ),
    )
    expected = _ast_logical(
        "AND",
        _ast_eq("closure_status", "COMPLETE", "NEW"),
        _ast_logical("OR", declaration_rule_missing, argmax_mismatch),
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_family_closures",
                "CLOSURE_WINNER_RULE_EXACT",
            ).violation_when
        )
        == expected
    )


def test_family_conclusion_is_unique_and_neither_grid_nor_conclusion_promotes() -> None:
    tables = _table_map()
    conclusion_indexes = tuple(
        _index_shape(index) for index in tables["data_snooping_assessments"].indexes
    )
    assert conclusion_indexes == INDEXES["data_snooping_assessments"]
    assert not any(shape[1] == "PARTIAL_UNIQUE" for shape in conclusion_indexes)
    assert {
        shape[2]
        for shape in conclusion_indexes
        if shape[1] == "UNIQUE"
    } == {
        ("record_hash",),
        ("authority_instance_id", "family_id", "record_hash"),
        ("authority_instance_id", "family_id"),
        ("authority_instance_id", "family_id", "closure_manifest_hash"),
    }
    assert tables["data_snooping_assessments"].chain is None

    for table_name in (
        "data_snooping_grid_assessments",
        "data_snooping_assessments",
    ):
        checks = {check.name: check for check in tables[table_name].checks}
        promotion = checks[f"{table_name}_promotion_none"]
        nodes = tuple(_walk(_payload(promotion.expression)))
        assert {node["value"] for node in nodes if set(node) == {"value"}} == {"NONE"}


def test_grid_declared_point_and_schedule_use_typed_canonical_json_ast() -> None:
    declaration_bindings = _new_bindings("authority_instance_id", "family_id")
    block_is_undeclared = _ast_related_json_contains(
        "data_snooping_declarations",
        declaration_bindings,
        "block_length_grid_json",
        "INTEGER",
        _ast_column("block_length", "NEW"),
        negated=True,
    )
    declared_replication = _ast_related_json_array(
        "data_snooping_declarations",
        declaration_bindings,
        "replication_schedule_json",
        "INTEGER",
        "ELEMENT_AT_ORDINAL",
        ordinal=_ast_column("schedule_ordinal", "NEW"),
        ordinal_offset=-1,
    )
    replication_mismatch = _ast_compare(
        "NE",
        _ast_column("replication_count", "NEW"),
        declared_replication,
    )
    assert _expression_shape(
        _guard(
            "data_snooping_grid_assessments",
            "GRID_DECLARED_POINT_EXACT",
        ).violation_when
    ) == _ast_logical("OR", block_is_undeclared, replication_mismatch)

    predecessor_invalid = _ast_chain_predecessor(
        "data_snooping_grid_assessments",
        _new_bindings("authority_instance_id", "family_id", "block_length"),
        "schedule_ordinal",
        _ast_column("schedule_ordinal", "NEW"),
        "record_hash",
        _ast_column("prev_record_hash", "NEW"),
        _ast_column("genesis_record_hash", "NEW"),
        negated=True,
    )
    assert _expression_shape(
        _guard(
            "data_snooping_grid_assessments",
            "GRID_SCHEDULE_NEXT",
        ).violation_when
    ) == _ast_logical("OR", predecessor_invalid, replication_mismatch)


def test_grid_prefix_guard_proves_exact_stream_extension_not_only_hash_presence() -> None:
    expected = _ast_prefix_extension(
        "data_snooping_grid_assessments",
        (
            *_new_bindings(
                "authority_instance_id",
                "family_id",
                "block_length",
            ),
            ("record_hash", _ast_column("prev_record_hash", "NEW")),
        ),
        "prefix_index_stream_hash",
        "replication_count",
        "period_count",
        "data_snooping_declarations",
        _new_bindings("authority_instance_id", "family_id"),
        (
            "bootstrap_seed",
            "bootstrap_algorithm_version",
            "prng_algorithm_version",
            "canonical_json_version",
            "block_length_policy_id",
        ),
        _ast_column("prefix_index_stream_hash", "NEW"),
        _ast_column("replication_count", "NEW"),
        _ast_column("period_count", "NEW"),
        _ast_column("block_length", "NEW"),
        _ast_column("schedule_ordinal", "NEW"),
        _ast_column("genesis_record_hash", "NEW"),
        negated=True,
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_grid_assessments",
                "GRID_PREFIX_PROOF",
            ).violation_when
        )
        == expected
    )


def test_grid_schedule_exhaustion_and_pending_predecessor_are_exact() -> None:
    declaration_bindings = _new_bindings("authority_instance_id", "family_id")
    schedule_length = _ast_related_json_length(
        "data_snooping_declarations",
        declaration_bindings,
        "replication_schedule_json",
        "INTEGER",
    )
    expected_exhaustion = _ast_logical(
        "OR",
        _ast_logical(
            "AND",
            _ast_eq("grid_point_verdict", "PENDING_MORE_REPLICATIONS", "NEW"),
            _ast_compare(
                "GE",
                _ast_column("schedule_ordinal", "NEW"),
                schedule_length,
            ),
        ),
        _ast_logical(
            "AND",
            _ast_eq("grid_point_verdict", "MONTE_CARLO_UNRESOLVED", "NEW"),
            _ast_compare(
                "NE",
                _ast_column("schedule_ordinal", "NEW"),
                schedule_length,
            ),
        ),
    )
    assert _expression_shape(
        _guard(
            "data_snooping_grid_assessments",
            "GRID_VERDICT_SCHEDULE_EXACT",
        ).violation_when
    ) == expected_exhaustion

    pending_predecessor = _ast_logical(
        "AND",
        _ast_compare("GT", _ast_column("schedule_ordinal", "NEW"), _ast_literal(1)),
        _ast_relation(
            "data_snooping_grid_assessments",
            (
                *_new_bindings(
                    "authority_instance_id",
                    "family_id",
                    "algorithm_version",
                    "block_length",
                ),
                ("record_hash", _ast_column("prev_record_hash", "NEW")),
            ),
            predicate=_ast_eq(
                "grid_point_verdict",
                "PENDING_MORE_REPLICATIONS",
                "RELATED",
            ),
            negated=True,
        ),
    )
    assert _expression_shape(
        _guard(
            "data_snooping_grid_assessments",
            "GRID_PREVIOUS_MUST_BE_PENDING",
        ).violation_when
    ) == pending_predecessor


def test_family_conclusion_uses_exact_latest_per_domain_policy_and_terminal_cutoff() -> None:
    declaration_bindings = _new_bindings("authority_instance_id", "family_id")
    latest_grid_bindings = _new_bindings(
        "authority_instance_id",
        "family_id",
        "algorithm_version",
    )
    aggregate_mismatch = _ast_latest_per_domain_policy(
        "data_snooping_grid_assessments",
        latest_grid_bindings,
        "ANY_GRID_DISAGREEMENT_MAX_P_V1",
        (
            "block_length",
            "replication_count",
            "reality_check_result_hash",
            "spa_result_hash",
            "grid_point_status",
            "grid_point_verdict",
            "record_hash",
        ),
        (("block_length", "ASC", "BINARY"),),
        _ast_column("diagnostic_verdict", "NEW"),
        _ast_column("ordered_latest_grid_assessment_hashes_hash", "NEW"),
        declared_domain=_ast_related_json_array(
            "data_snooping_declarations",
            declaration_bindings,
            "block_length_grid_json",
            "INTEGER",
            "CANONICAL_ARRAY",
        ),
        domain_column="block_length",
        latest_ordinal_column="schedule_ordinal",
        negated=True,
    )
    expected = _ast_logical(
        "AND",
        _ast_eq("assessment_status", "ASSESSED", "NEW"),
        aggregate_mismatch,
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_assessments",
                "ASSESSMENT_LATEST_GRID_POLICY_EXACT",
            ).violation_when
        )
        == expected
    )

    grid_hash_mismatch = _ast_json_document_hash(
        "ordered_latest_grid_assessment_hashes_json",
        _ast_column("ordered_latest_grid_assessment_hashes_hash", "NEW"),
    )
    assert _expression_shape(
        _guard(
            "data_snooping_assessments",
            "ASSESSMENT_LATEST_GRID_HASHES_EXACT",
        ).violation_when
    ) == grid_hash_mismatch

    conclusion_exists = _ast_relation(
        "data_snooping_assessments",
        _new_bindings("authority_instance_id", "family_id"),
        negated=False,
    )
    assert (
        _expression_shape(
            _guard(
                "data_snooping_grid_assessments",
                "GRID_AFTER_FAMILY_ASSESSMENT_FORBIDDEN",
            ).violation_when
        )
        == conclusion_exists
    )


def _all_c8_rules() -> dict[str, Any]:
    rules = {
        rule.rule_id: rule
        for table in _table_map().values()
        for rule in table.verification_rules
    }
    extension_rules = {
        rule.rule_id: rule
        for extension in _extension_map().values()
        for rule in extension.verification_rules
    }
    assert not set(rules).intersection(extension_rules)
    return {**rules, **extension_rules}


def _structural_source_shape(source: Any) -> tuple[object, ...]:
    return (
        _enum_value(source.kind),
        source.source_id,
        tuple(source.columns),
        source.referenced_table,
        tuple(source.referenced_columns),
        tuple(source.required_rule_ids),
        tuple(source.required_verifier_codes),
    )


def _expected_structural_source(
    kind: str,
    *,
    source_id: str | None = None,
    columns: tuple[str, ...] = (),
    referenced_table: str | None = None,
    referenced_columns: tuple[str, ...] = (),
) -> tuple[object, ...]:
    return (
        kind,
        source_id,
        columns,
        referenced_table,
        referenced_columns,
        (),
        (),
    )


def _expected_c8_structural_sources() -> dict[str, tuple[object, ...]]:
    expected: dict[str, tuple[object, ...]] = {}
    prefixes = (
        "declaration",
        "family_freeze",
        "family_closure",
        "grid_assessment",
        "assessment",
    )
    for prefix in prefixes:
        expected[f"{prefix}_row_hash"] = _expected_structural_source("ROW_HASH_SPEC")
        expected[f"{prefix}_genesis_binding"] = _expected_structural_source(
            "GENESIS_FOREIGN_KEY"
        )
        expected[f"{prefix}_foreign_keys"] = _expected_structural_source(
            "ALL_FOREIGN_KEYS"
        )

    exact_foreign_keys = {
        "declaration_scope_decision_exact": (
            ("selection_scope_id", "selection_decision_id", "family_id"),
            "selection_decisions",
            ("selection_scope_id", "selection_decision_id", "family_id"),
        ),
        "declaration_oos_exposure_exact": (
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
        "family_freeze_declaration_exact": (
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
        "family_closure_freeze_exact": (
            ("authority_instance_id", "family_id", "freeze_id", "freeze_record_hash"),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "freeze_id", "record_hash"),
        ),
        "assessment_closure_exact": (
            ("authority_instance_id", "family_id", "closure_manifest_hash"),
            "data_snooping_family_closures",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
        "family_search_declaration_exact": (
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
        "proposal_declaration_exact": (
            (
                "authority_instance_id",
                "family_id",
                "declaration_id",
                "declaration_record_hash",
            ),
            "data_snooping_declarations",
            ("authority_instance_id", "family_id", "declaration_id", "record_hash"),
        ),
        "oos_release_family_freeze_exact": (
            ("authority_instance_id", "family_id", "family_freeze_record_hash"),
            "data_snooping_family_freezes",
            ("authority_instance_id", "family_id", "record_hash"),
        ),
    }
    for rule_id, (columns, referenced_table, referenced_columns) in exact_foreign_keys.items():
        expected[rule_id] = _expected_structural_source(
            "EXACT_FOREIGN_KEY",
            columns=columns,
            referenced_table=referenced_table,
            referenced_columns=referenced_columns,
        )

    checks = {
        "declaration_four_counter_definitions": "data_snooping_declarations_counter_definitions",
        "family_closure_branch_totality": "data_snooping_family_closures_exact_branch",
        "grid_assessment_branch_totality": "data_snooping_grid_assessments_exact_branch",
        "grid_assessment_promotion_none": "data_snooping_grid_assessments_promotion_none",
        "assessment_branch_totality": "data_snooping_assessments_exact_branch",
        "assessment_promotion_none": "data_snooping_assessments_promotion_none",
    }
    for rule_id, source_id in checks.items():
        expected[rule_id] = _expected_structural_source(
            "CHECK_CONSTRAINT",
            source_id=source_id,
        )

    triggers = {
        "family_freeze_declared_search_totality": (
            "data_snooping_family_freezes_freeze_all_declared_searches_present"
        ),
        "family_freeze_emission_seal_totality": (
            "data_snooping_family_freezes_freeze_emission_seals_complete"
        ),
        "family_freeze_candidate_chain_heads": (
            "data_snooping_family_freezes_freeze_emission_seals_complete"
        ),
        "family_freeze_independent_counts": (
            "data_snooping_family_freezes_freeze_counts_match"
        ),
        "family_freeze_config_set": "data_snooping_family_freezes_freeze_counts_match",
        "family_freeze_declaration_content_stability": (
            "data_snooping_family_freezes_freeze_data_code_dlp_stable"
        ),
        "family_closure_search_manifest": (
            "data_snooping_family_closures_closure_freeze_exact"
        ),
        "family_closure_proposal_disposition_totality": (
            "data_snooping_family_closures_closure_proposal_dispositions_total"
        ),
        "family_closure_attempt_batch_branch": (
            "data_snooping_family_closures_closure_attempt_batch_exact_branch"
        ),
        "family_closure_series_calendar_cost_totality": (
            "data_snooping_family_closures_closure_series_calendar_cost_total"
        ),
        "family_closure_four_counts": (
            "data_snooping_family_closures_closure_four_counts_recomputed"
        ),
        "family_closure_trial_ledger_proof": (
            "data_snooping_family_closures_closure_trial_proof_exact"
        ),
        "family_closure_deterministic_winner": (
            "data_snooping_family_closures_closure_winner_rule_exact"
        ),
        "grid_assessment_closure_complete_exact": (
            "data_snooping_grid_assessments_grid_closure_complete_exact"
        ),
        "grid_assessment_declared_point": (
            "data_snooping_grid_assessments_grid_declared_point_exact"
        ),
        "grid_assessment_schedule_order": (
            "data_snooping_grid_assessments_grid_schedule_next"
        ),
        "grid_assessment_prefix_stream_extension": (
            "data_snooping_grid_assessments_grid_prefix_proof"
        ),
        "grid_assessment_pending_predecessor": (
            "data_snooping_grid_assessments_grid_previous_must_be_pending"
        ),
        "grid_assessment_family_conclusion_cutoff": (
            "data_snooping_grid_assessments_grid_after_family_assessment_forbidden"
        ),
        "assessment_latest_grid_policy": (
            "data_snooping_assessments_assessment_latest_grid_policy_exact"
        ),
        "family_search_declared_plan_match": (
            "data_snooping_family_searches_search_matches_declared_plan"
        ),
        "proposal_declaration_dlp_match": (
            "data_snooping_proposal_registrations_proposal_declaration_dlp_match"
        ),
        "oos_release_scope_outcome_policy": (
            "data_snooping_oos_releases_oos_release_scope_outcome_policy"
        ),
        "oos_release_closure_cutoff": (
            "data_snooping_oos_releases_oos_release_before_closure"
        ),
        "oos_attempt_family_freeze_totality": (
            "data_snooping_oos_access_attempts_oos_attempt_requires_freeze"
        ),
        "oos_attempt_scope_outcome_policy": (
            "data_snooping_oos_access_attempts_oos_attempt_scope_outcome_policy"
        ),
        "oos_attempt_closure_cutoff": (
            "data_snooping_oos_access_attempts_oos_attempt_before_closure"
        ),
    }
    for rule_id, source_id in triggers.items():
        expected[rule_id] = _expected_structural_source(
            "TRIGGER_POLICY",
            source_id=source_id,
        )

    expected["grid_assessment_chain"] = _expected_structural_source("CHAIN_SPEC")
    assert len(expected) == 57
    return expected


NAMED_C8_VERIFIER_CODES = {
    "declaration_scope_history_predecessor": "c8.verify_declaration_scope_history_predecessor_v1",
    "declaration_program_question_root_totality": (
        "c8.verify_declaration_program_question_root_totality_v1"
    ),
    "declaration_search_oos_non_overlap": "c8.verify_declaration_search_oos_non_overlap_v1",
    "declaration_ordered_sets_hashes": "c8.verify_declaration_ordered_sets_hashes_v1",
    "declaration_grid_schedule": "c8.verify_declaration_grid_schedule_v1",
    "declaration_code_data_dlp_binding": "c8.verify_declaration_code_data_dlp_binding_v1",
    "declaration_search_plan": "c8.verify_declaration_search_plan_v1",
    "declaration_precedes_family_activity": "c8.verify_declaration_precedes_family_activity_v1",
    "family_freeze_terminal_search_totality": (
        "c8.verify_family_freeze_terminal_search_totality_v1"
    ),
    "family_freeze_activity_cutoff": "c8.verify_family_freeze_activity_cutoff_v1",
    "family_closure_scope_history": "c8.verify_family_closure_scope_history_v1",
    "grid_assessment_result_payload_hashes": (
        "c8.verify_grid_assessment_result_payload_hashes_v1"
    ),
    "family_search_freeze_closure_cutoff": (
        "c8.verify_family_search_freeze_closure_cutoff_v1"
    ),
    "proposal_freeze_closure_cutoff": "c8.verify_proposal_freeze_closure_cutoff_v1",
    "proposal_terminal_result_freeze_closure_cutoff": (
        "c8.verify_proposal_terminal_result_freeze_closure_cutoff_v1"
    ),
    "hypothesis_freeze_closure_cutoff": "c8.verify_hypothesis_freeze_closure_cutoff_v1",
    "emission_seal_freeze_closure_cutoff": (
        "c8.verify_emission_seal_freeze_closure_cutoff_v1"
    ),
    "candidate_disposition_freeze_closure_cutoff": (
        "c8.verify_candidate_disposition_freeze_closure_cutoff_v1"
    ),
    "evaluation_batch_freeze_closure_cutoff": (
        "c8.verify_evaluation_batch_freeze_closure_cutoff_v1"
    ),
    "family_oos_evaluation_freeze_closure_cutoff": (
        "c8.verify_family_oos_evaluation_freeze_closure_cutoff_v1"
    ),
    "evaluation_receipt_freeze_closure_cutoff": (
        "c8.verify_evaluation_receipt_freeze_closure_cutoff_v1"
    ),
    "benchmark_return_freeze_closure_cutoff": (
        "c8.verify_benchmark_return_freeze_closure_cutoff_v1"
    ),
    "assessment_semantic_recomputation": "c8.verify_family_assessment_v1",
}


def test_every_c8_read_rule_has_one_exact_structural_or_named_proof() -> None:
    rules = _all_c8_rules()
    structural = _expected_c8_structural_sources()
    assert len(NAMED_C8_VERIFIER_CODES) == 23  # 22 new plus the existing assessment verifier.
    assert set(rules) == set(structural) | set(NAMED_C8_VERIFIER_CODES)

    for rule_id, expected_source in structural.items():
        rule = rules[rule_id]
        assert rule.verifier_code is None, rule_id
        assert rule.structural_source is not None, rule_id
        assert _structural_source_shape(rule.structural_source) == expected_source, rule_id

    actual_named = {
        rule_id: rule.verifier_code
        for rule_id, rule in rules.items()
        if rule.verifier_code is not None
    }
    assert actual_named == NAMED_C8_VERIFIER_CODES
    assert len(set(actual_named.values())) == len(actual_named)
    assert all(rules[rule_id].structural_source is None for rule_id in actual_named)


def test_freeze_search_terminal_totality_requires_complete_event_chain_verifier() -> None:
    rule = _all_c8_rules()["family_freeze_terminal_search_totality"]
    assert rule.structural_source is None
    assert rule.verifier_code == "c8.verify_family_freeze_terminal_search_totality_v1"
    assert tuple(rule.columns) == (
        "family_id",
        "ordered_search_terminal_hashes_json",
        "ordered_search_terminal_hashes_hash",
    )
    assert rule.referenced_table == "search_attempt_events"

    # This code owns the non-SQL proof: for every declared search it must load
    # the complete event_seq/prev_hash/event_hash chain, keep search identity
    # stable, and prove STARTED reaches exactly one FAILED or COMPLETED terminal
    # before matching the ordered terminal JSON and its canonical hash.
    assert rule.verifier_code not in {
        trigger.name
        for table in _table_map().values()
        for trigger in table.triggers
    }


def test_family_conclusion_semantic_verifier_contract_is_preserved() -> None:
    rule = _all_c8_rules()["assessment_semantic_recomputation"]
    assert rule.verifier_code == "c8.verify_family_assessment_v1"
    assert rule.structural_source is None
    assert _enum_value(rule.kind) == "CONTENT_BINDING"
    assert tuple(rule.columns) == (
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
    )


def test_c5_and_c7_extensions_have_exact_columns_keys_indexes_guards_and_rules() -> None:
    module = _schema_module()
    extensions = _extension_map()
    assert tuple(extensions) == EXTENSION_ORDER
    for extension_id in EXTENSION_ORDER:
        extension = extensions[extension_id]
        assert isinstance(extension, module.AuthorityTableExtensionSpec)
        assert extension.target_table == EXTENSION_TARGETS[extension_id]

        expected_columns = EXTENSION_COLUMNS.get(extension_id, ())
        if expected_columns:
            insertion = extension.column_insertion
            row_hash_insertion = extension.row_hash_field_insertion
            assert _enum_value(insertion.position) == "AFTER"
            assert insertion.anchor_column == "genesis_record_hash"
            assert tuple(_column_shape(column) for column in insertion.columns) == expected_columns
            assert _enum_value(row_hash_insertion.position) == "AFTER"
            assert row_hash_insertion.anchor_field == "genesis_record_hash"
            assert tuple(row_hash_insertion.fields) == tuple(column[0] for column in expected_columns)
        else:
            assert extension.column_insertion is None
            assert extension.row_hash_field_insertion is None

        assert tuple(_fk_shape(item) for item in extension.foreign_keys) == (
            EXTENSION_FOREIGN_KEYS.get(extension_id, ())
        )
        assert tuple(_index_shape(item) for item in extension.indexes) == (EXTENSION_INDEXES.get(extension_id, ()))
        assert tuple(extension.checks) == ()

        guards = tuple(extension.triggers)
        assert tuple(guard.error_code for guard in guards) == EXTENSION_GUARD_CODES[extension_id]
        assert all(guard.violation_when is not None for guard in guards)
        assert all(_enum_value(guard.kind) == "INSERT_GUARD" for guard in guards)
        assert all(_enum_value(guard.event) == "INSERT" for guard in guards)
        assert all(_enum_value(guard.timing) == "BEFORE" for guard in guards)
        assert all(_enum_value(guard.effect) == "ABORT" for guard in guards)

        rules = {rule.rule_id: _rule_actual_shape(rule) for rule in extension.verification_rules}
        assert rules == EXTENSION_RULES[extension_id]
        assert _guard_relation_tables(guards) == EXTENSION_RELATION_TABLES[extension_id]


def test_every_late_insert_cutoff_has_exact_new_row_binding_and_no_backdating_escape() -> None:
    freeze_cutoff = _ast_relation(
        "data_snooping_family_freezes",
        _new_bindings("authority_instance_id", "family_id"),
        predicate=None,
        negated=False,
    )
    closure_cutoff = _ast_relation(
        "data_snooping_family_closures",
        _new_bindings("authority_instance_id", "family_id"),
        predicate=None,
        negated=False,
    )
    for extension_id, error_codes in EXTENSION_GUARD_CODES.items():
        for error_code in error_codes:
            if error_code.endswith("BEFORE_FREEZE"):
                expected = freeze_cutoff
            elif error_code.endswith("BEFORE_CLOSURE"):
                expected = closure_cutoff
            else:
                continue
            assert _expression_shape(_extension_guard(extension_id, error_code).violation_when) == expected, (
                extension_id,
                error_code,
            )


def test_c5_declaration_guards_bind_exact_parent_and_deadline_direction() -> None:
    exact_declaration = _ast_relation(
        "data_snooping_declarations",
        (
            *_new_bindings("authority_instance_id", "family_id", "declaration_id"),
            ("record_hash", _ast_column("declaration_record_hash", "NEW")),
        ),
        negated=True,
    )
    for extension_id, error_code in (
        (C5_EXTENSION_ORDER[0], "SEARCH_REQUIRES_EXACT_DECLARATION"),
        (C5_EXTENSION_ORDER[1], "PROPOSAL_REQUIRES_EXACT_DECLARATION"),
    ):
        assert _expression_shape(_extension_guard(extension_id, error_code).violation_when) == exact_declaration

    deadline_guard = _ast_relation(
        "data_snooping_declarations",
        (
            *_new_bindings("authority_instance_id", "family_id", "declaration_id"),
            ("record_hash", _ast_column("declaration_record_hash", "NEW")),
        ),
        predicate=_ast_compare(
            "GE",
            _ast_column("external_ingestion_deadline_us", "RELATED"),
            _ast_column("registered_at_us", "NEW"),
        ),
        negated=True,
    )
    assert (
        _expression_shape(
            _extension_guard(
                C5_EXTENSION_ORDER[1],
                "PROPOSAL_WITHIN_DECLARED_EXTERNAL_DEADLINE",
            ).violation_when
        )
        == deadline_guard
    )


def test_c7_freeze_guards_bind_exact_freeze_identity_before_any_oos_row() -> None:
    release_exact = _ast_relation(
        "data_snooping_family_freezes",
        (
            *_new_bindings("authority_instance_id", "family_id"),
            ("record_hash", _ast_column("family_freeze_record_hash", "NEW")),
        ),
        negated=True,
    )
    assert (
        _expression_shape(
            _extension_guard(
                C7_EXTENSION_ORDER[0],
                "OOS_RELEASE_REQUIRES_EXACT_FREEZE",
            ).violation_when
        )
        == release_exact
    )

    freeze_exists = _ast_relation(
        "data_snooping_family_freezes",
        _new_bindings("authority_instance_id", "family_id"),
        negated=True,
    )
    for extension_id, error_codes in EXTENSION_GUARD_CODES.items():
        if extension_id not in C7_EXTENSION_ORDER[2:]:
            continue
        freeze_codes = tuple(code for code in error_codes if code.endswith("REQUIRES_FREEZE"))
        assert len(freeze_codes) == 1, extension_id
        assert _expression_shape(_extension_guard(extension_id, freeze_codes[0]).violation_when) == freeze_exists


def test_oos_attempt_and_release_follow_publishable_or_terminal_diagnostic_scope_policy() -> None:
    attempt_freeze_bindings = _new_bindings("authority_instance_id", "family_id")
    freeze_declaration_join = (
        (
            "data_snooping_declarations",
            "declaration",
            (
                ("freeze", "authority_instance_id", "declaration", "authority_instance_id"),
                ("freeze", "family_id", "declaration", "family_id"),
                ("freeze", "declaration_id", "declaration", "declaration_id"),
                ("freeze", "declaration_record_hash", "declaration", "record_hash"),
            ),
            _ast_logical(
                "AND",
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("freeze", "frozen_config_set_hash"),
                    _ast_column("frozen_config_set_hash", "NEW"),
                ),
                *(
                    _ast_compare(
                        "EQ",
                        _ast_qualified_column("declaration", related),
                        _ast_column(local, "NEW"),
                    )
                    for related, local in (
                        ("selection_decision_id", "selection_decision_id"),
                        ("oos_exposure_id", "oos_exposure_id"),
                        ("benchmark_contract_hash", "benchmark_contract_hash"),
                        ("ordered_period_calendar_hash", "ordered_period_calendar_hash"),
                        ("cost_contract_hash", "cost_contract_hash"),
                    )
                ),
            ),
            "EXACTLY_ONE",
        ),
    )
    exact_started_plan_missing = _ast_join_aggregate(
        "data_snooping_family_freezes",
        "freeze",
        attempt_freeze_bindings,
        freeze_declaration_join,
        "COUNT_ROWS",
        value=None,
        order=(),
        operator="NE",
        expected=_ast_literal(1),
    )
    assert _expression_shape(
        _extension_guard(
            C7_EXTENSION_ORDER[1],
            "OOS_ATTEMPT_REQUIRES_FREEZE",
        ).violation_when
    ) == _ast_logical(
        "AND",
        _ast_eq("status", "STARTED", "NEW"),
        exact_started_plan_missing,
    )

    declaration_bindings = _new_bindings("authority_instance_id", "family_id")
    publishable_missing = _ast_relation(
        "data_snooping_declarations",
        declaration_bindings,
        predicate=_ast_logical(
            "AND",
            _ast_eq("decision_class", "PUBLISHABLE", "RELATED"),
            _ast_compare(
                "EQ",
                _ast_column("selection_decision_id", "RELATED"),
                _ast_column("selection_decision_id", "NEW"),
            ),
            _ast_compare(
                "EQ",
                _ast_column("oos_exposure_id", "RELATED"),
                _ast_column("oos_exposure_id", "NEW"),
            ),
        ),
        negated=True,
    )
    diagnostic_join = (
        (
            "selection_scope_outcomes",
            "outcome",
            (("declaration", "selection_scope_id", "outcome", "selection_scope_id"),),
            _ast_logical(
                "AND",
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("declaration", "decision_class"),
                    _ast_literal("DIAGNOSTIC_ONLY"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("declaration", "selection_decision_id"),
                    _ast_column("selection_decision_id", "NEW"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("declaration", "oos_exposure_id"),
                    _ast_column("oos_exposure_id", "NEW"),
                ),
                (
                    "MEMBERSHIP",
                    _ast_qualified_column("outcome", "outcome"),
                    (
                        _ast_literal("WINNER_PINNED"),
                        _ast_literal("CLOSED_WITHOUT_PROMOTION"),
                    ),
                    False,
                ),
            ),
            "EXACTLY_ONE",
        ),
    )
    diagnostic_terminal_missing = _ast_join_aggregate(
        "data_snooping_declarations",
        "declaration",
        declaration_bindings,
        diagnostic_join,
        "COUNT_ROWS",
        value=None,
        order=(),
        operator="NE",
        expected=_ast_literal(1),
    )
    assert _expression_shape(
        _extension_guard(
            C7_EXTENSION_ORDER[1],
            "OOS_ATTEMPT_SCOPE_OUTCOME_POLICY",
        ).violation_when
    ) == _ast_logical(
        "AND",
        _ast_eq("status", "STARTED", "NEW"),
        publishable_missing,
        diagnostic_terminal_missing,
    )

    release_attempt_bindings = (
        *_new_bindings("authority_instance_id", "family_id", "attempt_id"),
        ("attempt_event_id", _ast_column("started_attempt_event_id", "NEW")),
        ("oos_exposure_id", _ast_column("oos_exposure_id", "NEW")),
    )
    release_joins = (
        (
            "data_snooping_family_freezes",
            "freeze",
            (
                ("attempt", "authority_instance_id", "freeze", "authority_instance_id"),
                ("attempt", "family_id", "freeze", "family_id"),
            ),
            _ast_compare(
                "EQ",
                _ast_qualified_column("freeze", "record_hash"),
                _ast_column("family_freeze_record_hash", "NEW"),
            ),
            "EXACTLY_ONE",
        ),
        (
            "data_snooping_declarations",
            "declaration",
            (
                ("freeze", "authority_instance_id", "declaration", "authority_instance_id"),
                ("freeze", "family_id", "declaration", "family_id"),
                ("freeze", "declaration_id", "declaration", "declaration_id"),
                ("freeze", "declaration_record_hash", "declaration", "record_hash"),
            ),
            _ast_logical(
                "AND",
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("attempt", "status"),
                    _ast_literal("STARTED"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("attempt", "frozen_config_set_hash"),
                    _ast_qualified_column("freeze", "frozen_config_set_hash"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("declaration", "decision_class"),
                    _ast_literal("PUBLISHABLE"),
                ),
                *(
                    _ast_compare(
                        "EQ",
                        _ast_qualified_column("attempt", attempt_column),
                        _ast_qualified_column("declaration", declaration_column),
                    )
                    for attempt_column, declaration_column in (
                        ("selection_decision_id", "selection_decision_id"),
                        ("oos_exposure_id", "oos_exposure_id"),
                        ("benchmark_contract_hash", "benchmark_contract_hash"),
                        ("ordered_period_calendar_hash", "ordered_period_calendar_hash"),
                        ("cost_contract_hash", "cost_contract_hash"),
                    )
                ),
            ),
            "EXACTLY_ONE",
        ),
    )
    release_publishable_missing = _ast_join_aggregate(
        "data_snooping_oos_access_attempts",
        "attempt",
        release_attempt_bindings,
        release_joins,
        "COUNT_ROWS",
        value=None,
        order=(),
        operator="NE",
        expected=_ast_literal(1),
    )
    diagnostic_release_joins = (
        release_joins[0],
        (
            "data_snooping_declarations",
            "declaration",
            release_joins[1][2],
            _ast_logical(
                "AND",
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("attempt", "status"),
                    _ast_literal("STARTED"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("attempt", "frozen_config_set_hash"),
                    _ast_qualified_column("freeze", "frozen_config_set_hash"),
                ),
                _ast_compare(
                    "EQ",
                    _ast_qualified_column("declaration", "decision_class"),
                    _ast_literal("DIAGNOSTIC_ONLY"),
                ),
                *(
                    _ast_compare(
                        "EQ",
                        _ast_qualified_column("attempt", attempt_column),
                        _ast_qualified_column("declaration", declaration_column),
                    )
                    for attempt_column, declaration_column in (
                        ("selection_decision_id", "selection_decision_id"),
                        ("oos_exposure_id", "oos_exposure_id"),
                        ("benchmark_contract_hash", "benchmark_contract_hash"),
                        ("ordered_period_calendar_hash", "ordered_period_calendar_hash"),
                        ("cost_contract_hash", "cost_contract_hash"),
                    )
                ),
            ),
            "EXACTLY_ONE",
        ),
        (
            "selection_scope_outcomes",
            "outcome",
            (("declaration", "selection_scope_id", "outcome", "selection_scope_id"),),
            (
                "MEMBERSHIP",
                _ast_qualified_column("outcome", "outcome"),
                (
                    _ast_literal("WINNER_PINNED"),
                    _ast_literal("CLOSED_WITHOUT_PROMOTION"),
                ),
                False,
            ),
            "EXACTLY_ONE",
        ),
    )
    release_diagnostic_missing = _ast_join_aggregate(
        "data_snooping_oos_access_attempts",
        "attempt",
        release_attempt_bindings,
        diagnostic_release_joins,
        "COUNT_ROWS",
        value=None,
        order=(),
        operator="NE",
        expected=_ast_literal(1),
    )
    assert _expression_shape(
        _extension_guard(
            C7_EXTENSION_ORDER[0],
            "OOS_RELEASE_SCOPE_OUTCOME_POLICY",
        ).violation_when
    ) == _ast_logical(
        "AND",
        release_publishable_missing,
        release_diagnostic_missing,
    )


def test_five_table_twelve_extension_insert_topology_remains_acyclic() -> None:
    assert len(TABLE_ORDER) == 5
    assert len(EXTENSION_ORDER) == 12
    rank = {
        "data_snooping_declarations": 0,
        **{EXTENSION_TARGETS[extension_id]: 1 for extension_id in C5_EXTENSION_ORDER},
        "data_snooping_family_freezes": 2,
        **{table: 3 for table in C7_TABLE_ORDER},
        "data_snooping_family_closures": 4,
        "data_snooping_grid_assessments": 5,
        "data_snooping_assessments": 6,
    }
    required_parent_edges = {
        ("data_snooping_family_freezes", "data_snooping_declarations"),
        ("data_snooping_family_closures", "data_snooping_declarations"),
        ("data_snooping_family_closures", "data_snooping_family_freezes"),
        ("data_snooping_grid_assessments", "data_snooping_family_closures"),
        ("data_snooping_assessments", "data_snooping_family_closures"),
        ("data_snooping_assessments", "data_snooping_grid_assessments"),
        *{(EXTENSION_TARGETS[extension_id], "data_snooping_declarations") for extension_id in C5_EXTENSION_ORDER[:2]},
        *{(EXTENSION_TARGETS[extension_id], "data_snooping_family_freezes") for extension_id in C7_EXTENSION_ORDER},
    }
    assert all(rank[parent] < rank[child] for child, parent in required_parent_edges)

    for extension_id, error_codes in EXTENSION_GUARD_CODES.items():
        target = EXTENSION_TARGETS[extension_id]
        if any(code.endswith("BEFORE_FREEZE") for code in error_codes):
            assert rank[target] < rank["data_snooping_family_freezes"]
        if any(code.endswith("BEFORE_CLOSURE") for code in error_codes):
            assert rank[target] < rank["data_snooping_family_closures"]


def test_c8_resolves_every_real_c5_and_c7_late_guard_requirement_exactly_once() -> None:
    component = _required("C8_SCHEMA_COMPONENT")
    c5 = importlib.import_module("services.research.experiment_registry.family_schema_c5_c6").C5_SCHEMA_COMPONENT
    c7 = importlib.import_module("services.research.experiment_registry.family_schema_c7").C7_SCHEMA_COMPONENT

    live_requirements = {
        requirement.extension_id: (
            requirement.target_table,
            requirement.provider_component_id,
            requirement.required_capability,
        )
        for owner in (c5, c7)
        for requirement in owner.required_extensions
        if requirement.provider_component_id == COMPONENT_ID
    }
    assert set(live_requirements) == set(EXTENSION_ORDER)
    assert tuple(extension.extension_id for extension in component.extensions) == EXTENSION_ORDER
    assert all(
        live_requirements[extension.extension_id][0] == extension.target_table
        for extension in component.extensions
    )
    assert all(capability in component.capabilities for _, _, capability in live_requirements.values())
    assert len(live_requirements) == len(c5.required_extensions) + 7


def test_c6_declaration_and_closure_extensions_are_required_not_fake_dependencies() -> None:
    component = _required("C8_SCHEMA_COMPONENT")
    requirements = tuple(
        (
            requirement.extension_id,
            requirement.target_table,
            requirement.provider_component_id,
            requirement.required_capability,
        )
        for requirement in component.required_extensions
    )
    assert requirements == C6_REQUIRED_EXTENSIONS
    assert tuple(_required("C8_REQUIRED_EXTENSION_SPECS")) == tuple(component.required_extensions)

    c6 = importlib.import_module("services.research.experiment_registry.family_schema_c5_c6").C6_SCHEMA_COMPONENT
    provided = {extension.extension_id: extension for extension in c6.extensions}
    for extension_id, target_table, provider_id, capability in requirements:
        assert provider_id == c6.component_id
        assert capability in c6.capabilities
        assert extension_id in provided
        assert provided[extension_id].target_table == target_table

    declaration_fields = tuple(column.name for column in provided[C6_DECLARATION_EXTENSION[0]].column_insertion.columns)
    closure_fields = tuple(column.name for column in provided[C6_CLOSURE_EXTENSION[0]].column_insertion.columns)
    assert declaration_fields == C6_DECLARATION_CONTRACT_FIELDS
    assert closure_fields == C6_CLOSURE_CONTRACT_FIELDS

    dependency_ids = {dependency.component_id for dependency in component.dependencies}
    assert C6_COMPONENT not in dependency_ids


def test_dependencies_name_only_live_c4_c5_c7_components_and_capabilities() -> None:
    component = _required("C8_SCHEMA_COMPONENT")
    actual = {
        dependency.component_id: (
            _enum_value(dependency.kind),
            tuple(dependency.required_capabilities),
        )
        for dependency in component.dependencies
    }
    assert actual == DEPENDENCIES

    live_components = {
        C4_COMPONENT: importlib.import_module(
            "services.research.experiment_registry.family_schema_c4"
        ).C4_SCHEMA_COMPONENT,
        C5_COMPONENT: importlib.import_module(
            "services.research.experiment_registry.family_schema_c5_c6"
        ).C5_SCHEMA_COMPONENT,
        C7_COMPONENT: importlib.import_module(
            "services.research.experiment_registry.family_schema_c7"
        ).C7_SCHEMA_COMPONENT,
    }
    assert set(actual) == set(live_components)
    for component_id, live in live_components.items():
        assert live.component_id == component_id
        assert set(actual[component_id][1]).issubset(live.capabilities)


def test_c6_contract_fields_are_required_surfaces_and_never_double_covered() -> None:
    component = _required("C8_SCHEMA_COMPONENT")
    required = tuple(
        (
            surface.contract_type,
            tuple(surface.field_names),
            surface.provider_component_id,
            surface.required_capability,
        )
        for surface in component.required_contract_surfaces
    )
    assert required == REQUIRED_CONTRACT_SURFACES
    assert tuple(_required("C8_REQUIRED_CONTRACT_SURFACES")) == tuple(component.required_contract_surfaces)

    coverage_pairs = {(item.contract_type, item.field_name) for item in component.coverage}
    required_pairs = {
        (contract_type, field_name)
        for contract_type, field_names, _, _ in REQUIRED_CONTRACT_SURFACES
        for field_name in field_names
    }
    assert coverage_pairs.isdisjoint(required_pairs)


def test_c8_contract_coverage_is_total_single_owned_typed_and_pass_m_ready() -> None:
    component = _required("C8_SCHEMA_COMPONENT")
    research_contracts = importlib.import_module("quantos.contracts.research")
    required_by_contract = {
        contract_type: set(field_names) for contract_type, field_names, _, _ in REQUIRED_CONTRACT_SURFACES
    }

    coverage: dict[str, dict[str, Any]] = {}
    for item in component.coverage:
        assert item.field_name not in coverage.setdefault(item.contract_type, {})
        coverage[item.contract_type][item.field_name] = item
    assert set(coverage) == set(CONTRACT_TABLES)

    for contract_type, table_name in CONTRACT_TABLES.items():
        contract = getattr(research_contracts, contract_type)
        expected_owned = set(contract.model_fields) - required_by_contract.get(
            contract_type,
            set(),
        )
        assert set(coverage[contract_type]) == expected_owned

        for field_name, item in coverage[contract_type].items():
            key = (contract_type, field_name)
            if key in NON_PERSISTED_DERIVATIONS:
                assert _enum_value(item.kind) == "NON_PERSISTED_DERIVATION"
                assert item.table_name is None and item.target_field is None
                assert item.derivation_id == NON_PERSISTED_DERIVATIONS[key]
                continue

            assert item.table_name == table_name
            expected_target = TARGET_ALIASES.get(key, field_name)
            if contract_type == "TrialLedgerProof" and field_name != "proof_hash":
                expected_target = "trial_ledger_proof_json"
            if field_name in CANONICAL_PAYLOAD_FIELDS.get(contract_type, set()):
                assert _enum_value(item.kind) == "CANONICAL_PAYLOAD", key
            else:
                assert _enum_value(item.kind) == "COLUMN", key
            assert item.target_field == expected_target, key
            assert item.derivation_id is None

    derivations = tuple(
        (
            item.derivation_id,
            item.contract_type,
            item.field_name,
            _enum_value(item.kind),
            item.binding_table,
            item.binding_field,
            item.source_table,
            item.source_key_field,
            item.source_value_field,
            tuple(item.order_by_fields),
        )
        for item in component.contract_derivations
    )
    assert derivations == (
        (
            "c8.closure.authority_head",
            "DataSnoopingFamilyManifest",
            "authority_head",
            "IMMUTABLE_ROW_LOOKUP",
            "data_snooping_family_closures",
            "record_hash",
            "data_snooping_family_closures",
            "record_hash",
            "record_hash",
            (),
        ),
    )


def test_c8_contains_no_c9_publication_table_contract_surface_or_dependency() -> None:
    component = _required("C8_SCHEMA_COMPONENT")
    assert "artifact_publication_receipts" not in {table.table_name for table in component.tables}
    assert not C9_FORBIDDEN_CONTRACTS & {item.contract_type for item in component.coverage}
    assert not C9_FORBIDDEN_CONTRACTS & {item.contract_type for item in component.required_contract_surfaces}
    assert all("C9_" not in dependency.component_id for dependency in component.dependencies)
    assert not any("publication" in column.name for table in component.tables for column in table.columns)


def test_pass_c_c8_exposes_no_renderer_ddl_activation_migration_or_service() -> None:
    module = _schema_module()
    source = inspect.getsource(module).upper()
    assert "CREATE TABLE" not in source
    assert "PRAGMA USER_VERSION" not in source
    for forbidden in (
        "render_family_schema_v3",
        "activate_family_schema_v3",
        "verify_family_authority",
        "require_family_schema_v3",
        "declare_family",
        "attach_search",
        "seal_search",
        "freeze_family",
        "close_family",
        "run_declared_assessment_schedule",
        "derive_effective_assessment",
    ):
        assert not hasattr(module, forbidden)
