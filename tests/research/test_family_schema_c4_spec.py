"""Exact Pass-C/C4 schema-AST semantics (intentionally RED).

This file freezes a private, non-executable component specification.  It must
not create a database, render SQL, run a migration, or require any A4 service.
C6 and C7 still own the embedded observed-PIT and access-attempt extensions, so
the C4 component is explicitly incomplete and may never report ``SEALED``.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import importlib
import inspect
from types import ModuleType
from typing import Any

import pytest

from quantos.contracts.canonical import canonical_sha256


MODULE = "services.research.experiment_registry.family_schema_c4"
COMPONENT_ID = "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
C6_EXTENSION = "C6_GLOBAL_OOS_EXPOSURE_EMBEDDED_REFS_V1"
C7_EXTENSION = "C7_GLOBAL_OOS_EXPOSURE_CONSUMPTION_ATTEMPT_BINDING_V1"
C6_COMPONENT = "C6_EXTERNAL_AUTHORITY_REFS_V1"
C7_COMPONENT = "C7_ONE_SHOT_OOS_V1"

TABLE_ORDER = (
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

RECORD_TYPES = {
    "research_questions": "RESEARCH_QUESTION_V1",
    "research_question_aliases": "RESEARCH_QUESTION_ALIAS_V1",
    "strategy_lineage_roots": "STRATEGY_LINEAGE_ROOT_V1",
    "strategy_lineage_nodes": "STRATEGY_LINEAGE_NODE_V1",
    "strategy_config_lineage_bindings": "STRATEGY_CONFIG_LINEAGE_BINDING_V1",
    "research_authority_events": "RESEARCH_AUTHORITY_EVENT_V1",
    "selection_scopes": "SELECTION_SCOPE_V1",
    "selection_scope_aliases": "SELECTION_SCOPE_ALIAS_V1",
    "selection_scope_publishable_decisions": "SELECTION_SCOPE_PUBLISHABLE_DECISION_V1",
    "selection_scope_outcomes": "SELECTION_SCOPE_OUTCOME_V1",
    "research_programs": "RESEARCH_PROGRAM_V1",
    "selection_decisions": "SELECTION_DECISION_V1",
    "global_oos_exposures": "GLOBAL_OOS_EXPOSURE_V1",
    "global_oos_exposure_consumptions": "GLOBAL_OOS_EXPOSURE_CONSUMPTION_V1",
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
    "research_questions": _columns(
        _c("research_question_id"),
        _c("question_hash"),
        _c("question_payload_json"),
        _c("authority_event_id"),
        _c("issued_at_us", "INTEGER"),
    ),
    "research_question_aliases": _columns(
        _c("alias_hash"),
        _c("alias"),
        _c("research_question_id"),
        _c("authority_event_id"),
        _c("registered_at_us", "INTEGER"),
    ),
    "strategy_lineage_roots": _columns(
        _c("strategy_lineage_root_id"),
        _c("source_manifest_hash"),
        _c("source_manifest_payload_json"),
        _c("authority_event_id"),
        _c("registered_at_us", "INTEGER"),
    ),
    "strategy_lineage_nodes": _columns(
        _c("strategy_lineage_node_id"),
        _c("strategy_lineage_root_id"),
        _c("parent_strategy_lineage_node_id", nullable=True),
        _c("node_manifest_hash"),
        _c("node_manifest_payload_json"),
        _c("authority_event_id"),
        _c("registered_at_us", "INTEGER"),
    ),
    "strategy_config_lineage_bindings": _columns(
        _c("config_hash"),
        _c("strategy_lineage_root_id"),
        _c("strategy_lineage_node_id"),
        _c("authority_event_id"),
        _c("bound_at_us", "INTEGER"),
    ),
    "research_authority_events": _columns(
        _c("event_id"),
        _c("event_ordinal", "INTEGER"),
        _c("action"),
        _c("subject_record_type"),
        _c("subject_id"),
        _c("subject_record_hash"),
        _c("grant_id", nullable=True),
        _c("grant_record_hash", nullable=True),
        _c("grant_policy_id", nullable=True),
        _c("grant_policy_version", nullable=True),
        _c("grant_policy_hash", nullable=True),
        _c("grant_authority_id", nullable=True),
        _c("grant_authority_head", nullable=True),
        _c("audit_actor_id"),
        _c("audit_actor_kind"),
        _c("audit_actor_evidence_hash"),
        _c("audit_actor_policy_id"),
        _c("audit_actor_policy_version"),
        _c("audit_actor_policy_hash"),
        _c("audit_actor_authority_id"),
        _c("audit_actor_authority_head"),
        _c("event_at_us", "INTEGER"),
        _c("prev_record_hash"),
    ),
    "selection_scopes": _columns(
        _c("selection_scope_id"),
        _c("deployment_mandate_hash"),
        _c("deployment_mandate_payload_json"),
        _c("authority_event_id"),
        _c("issued_at_us", "INTEGER"),
    ),
    "selection_scope_aliases": _columns(
        _c("alias_hash"),
        _c("alias"),
        _c("selection_scope_id"),
        _c("authority_event_id"),
        _c("registered_at_us", "INTEGER"),
    ),
    "selection_scope_publishable_decisions": _columns(
        _c("selection_scope_id"),
        _c("selection_decision_id"),
        _c("authority_event_id"),
        _c("reserved_at_us", "INTEGER"),
    ),
    "selection_scope_outcomes": _columns(
        _c("selection_scope_id"),
        _c("outcome"),
        _c("selection_decision_id", nullable=True),
        _c("family_id", nullable=True),
        _c("winner_alpha_id", nullable=True),
        _c("evidence_ref_hash", nullable=True),
        _c("authority_event_id"),
        _c("recorded_at_us", "INTEGER"),
    ),
    "research_programs": _columns(
        _c("research_program_id"),
        _c("selection_scope_id"),
        _c("research_question_id"),
        _c("question_hash"),
        _c("strategy_lineage_root_id"),
        _c("source_manifest_hash"),
        _c("authority_event_id"),
        _c("issued_at_us", "INTEGER"),
    ),
    "selection_decisions": _columns(
        _c("selection_decision_id"),
        _c("selection_scope_id"),
        _c("decision_ordinal", "INTEGER"),
        _c("family_id"),
        _c("decision_class"),
        _c("authority_event_id"),
        _c("issued_at_us", "INTEGER"),
        _c("prev_record_hash"),
    ),
    "global_oos_exposures": _columns(
        _c("oos_exposure_id"),
        _c("selection_scope_id"),
        _c("selection_decision_id"),
        _c("family_id"),
        _c("exposure_ordinal", "INTEGER"),
        _c("exposure_fingerprint"),
        _c("period_start_us", "INTEGER"),
        _c("period_end_us", "INTEGER"),
        _c("authority_event_id"),
        _c("issued_at_us", "INTEGER"),
        _c("prev_record_hash"),
    ),
    "global_oos_exposure_consumptions": _columns(
        _c("oos_exposure_id"),
        _c("selection_decision_id"),
        _c("family_id"),
        _c("access_attempt_id"),
        _c("expected_exposure_version", "INTEGER"),
        _c("authority_event_id"),
        _c("consumed_at_us", "INTEGER"),
    ),
}


EXPECTED_COLUMN_COVERAGE = {
    "ResearchQuestionRef": {
        "research_question_id": ("research_questions", "research_question_id"),
        "question_hash": ("research_questions", "question_hash"),
    },
    "ResearchQuestionAlias": {
        "alias": ("research_question_aliases", "alias"),
        "alias_hash": ("research_question_aliases", "alias_hash"),
        "research_question_id": (
            "research_question_aliases",
            "research_question_id",
        ),
    },
    "StrategyLineageRootRef": {
        "strategy_lineage_root_id": (
            "strategy_lineage_roots",
            "strategy_lineage_root_id",
        ),
        "source_manifest_hash": (
            "strategy_lineage_roots",
            "source_manifest_hash",
        ),
    },
    "StrategyLineageNodeRef": {
        "strategy_lineage_node_id": (
            "strategy_lineage_nodes",
            "strategy_lineage_node_id",
        ),
        "strategy_lineage_root_id": (
            "strategy_lineage_nodes",
            "strategy_lineage_root_id",
        ),
        "parent_strategy_lineage_node_id": (
            "strategy_lineage_nodes",
            "parent_strategy_lineage_node_id",
        ),
        "node_manifest_hash": (
            "strategy_lineage_nodes",
            "node_manifest_hash",
        ),
    },
    "SelectionScopeRef": {
        "selection_scope_id": ("selection_scopes", "selection_scope_id"),
        "deployment_mandate_hash": (
            "selection_scopes",
            "deployment_mandate_hash",
        ),
    },
    "SelectionScopeAlias": {
        "alias": ("selection_scope_aliases", "alias"),
        "alias_hash": ("selection_scope_aliases", "alias_hash"),
        "selection_scope_id": (
            "selection_scope_aliases",
            "selection_scope_id",
        ),
    },
    "SelectionScopeOutcome": {
        field: ("selection_scope_outcomes", field)
        for field in (
            "selection_scope_id",
            "outcome",
            "selection_decision_id",
            "family_id",
            "winner_alpha_id",
            "evidence_ref_hash",
        )
    },
    "ResearchProgramRef": {
        field: ("research_programs", field)
        for field in (
            "research_program_id",
            "selection_scope_id",
            "research_question_id",
            "question_hash",
            "strategy_lineage_root_id",
            "source_manifest_hash",
        )
    },
    "SelectionDecisionRef": {
        field: ("selection_decisions", field)
        for field in (
            "selection_decision_id",
            "selection_scope_id",
            "decision_ordinal",
            "family_id",
            "decision_class",
        )
    },
    "OosExposureRef": {
        field: ("global_oos_exposures", field)
        for field in (
            "oos_exposure_id",
            "selection_scope_id",
            "selection_decision_id",
            "family_id",
            "exposure_ordinal",
            "period_start_us",
            "period_end_us",
            "exposure_fingerprint",
        )
    },
    "OosExposureConsumption": {
        **{
            field: ("global_oos_exposure_consumptions", field)
            for field in (
                "oos_exposure_id",
                "selection_decision_id",
                "family_id",
                "access_attempt_id",
                "expected_exposure_version",
                "consumed_at_us",
            )
        },
        "consumption_authority_id": (
            "global_oos_exposure_consumptions",
            "authority_instance_id",
        ),
        "consumption_record_hash": (
            "global_oos_exposure_consumptions",
            "record_hash",
        ),
    },
}

EXPECTED_CANONICAL_PAYLOAD_COVERAGE = {
    "StructuredResearchQuestion": {
        field: ("research_questions", "question_payload_json")
        for field in (
            "question_schema_version",
            "market_domain",
            "universe_domain",
            "signal_class",
            "label_definition",
            "evaluation_horizon",
        )
    },
    "ResearchQuestionRef": {"question": ("research_questions", "question_payload_json")},
    "SourceManifest": {
        field: ("strategy_lineage_roots", "source_manifest_payload_json")
        for field in (
            "source_manifest_schema_version",
            "generator_kind",
            "strategy_class",
            "generator_entrypoint",
            "source_tree_hash",
        )
    },
    "StrategyLineageRootRef": {
        "source_manifest": (
            "strategy_lineage_roots",
            "source_manifest_payload_json",
        )
    },
    "DeploymentMandate": {
        field: ("selection_scopes", "deployment_mandate_payload_json")
        for field in (
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
    },
    "SelectionScopeRef": {
        "deployment_mandate": (
            "selection_scopes",
            "deployment_mandate_payload_json",
        )
    },
}

EXPECTED_DERIVED_COVERAGE = {
    ("OosExposureConsumption", "consumption_authority_head"): (
        "C4_CONSUMPTION_AUTHORITY_HEAD_FROM_AUTHORITY_EVENT_RECORD_HASH_V1"
    ),
}

EXPECTED_DERIVATION_METADATA = {
    "C4_CONSUMPTION_AUTHORITY_HEAD_FROM_AUTHORITY_EVENT_RECORD_HASH_V1": (
        "OosExposureConsumption",
        "consumption_authority_head",
        "IMMUTABLE_ROW_LOOKUP",
        "global_oos_exposure_consumptions",
        "authority_event_id",
        "research_authority_events",
        "event_id",
        "record_hash",
    ),
}

C6_REQUIRED_CONTRACT_FIELDS = (
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

PRIMARY_KEYS = {
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
    _name: str,
    columns: tuple[str, ...],
    ref_table: str,
    ref_columns: tuple[str, ...],
) -> tuple[tuple[str, ...], str, tuple[str, ...], str, str, str]:
    return (
        columns,
        ref_table,
        ref_columns,
        "RESTRICT",
        "RESTRICT",
        "NOT_DEFERRABLE",
    )


def _base_fks(table: str, *, event: bool = True) -> list[tuple[Any, ...]]:
    result: list[tuple[Any, ...]] = [
        _fk(
            f"fk_{table}_genesis",
            ("authority_instance_id", "genesis_record_hash"),
            "family_authority_genesis",
            ("authority_instance_id", "record_hash"),
        )
    ]
    if event:
        result.append(
            _fk(
                f"fk_{table}_authority_event",
                ("authority_event_id",),
                "research_authority_events",
                ("event_id",),
            )
        )
    return result


FOREIGN_KEYS: dict[str, tuple[tuple[Any, ...], ...]] = {
    table: tuple(_base_fks(table, event=table != "research_authority_events")) for table in TABLE_ORDER
}
FOREIGN_KEYS.update(
    {
        "research_question_aliases": tuple(_base_fks("research_question_aliases"))
        + (
            _fk(
                "fk_research_question_aliases_question",
                ("research_question_id",),
                "research_questions",
                ("research_question_id",),
            ),
        ),
        "strategy_lineage_nodes": tuple(_base_fks("strategy_lineage_nodes"))
        + (
            _fk(
                "fk_strategy_lineage_nodes_root",
                ("strategy_lineage_root_id",),
                "strategy_lineage_roots",
                ("strategy_lineage_root_id",),
            ),
            _fk(
                "fk_strategy_lineage_nodes_parent_root",
                ("parent_strategy_lineage_node_id", "strategy_lineage_root_id"),
                "strategy_lineage_nodes",
                ("strategy_lineage_node_id", "strategy_lineage_root_id"),
            ),
        ),
        "strategy_config_lineage_bindings": tuple(_base_fks("strategy_config_lineage_bindings"))
        + (
            _fk(
                "fk_strategy_config_lineage_bindings_root",
                ("strategy_lineage_root_id",),
                "strategy_lineage_roots",
                ("strategy_lineage_root_id",),
            ),
            _fk(
                "fk_strategy_config_lineage_bindings_node_root",
                ("strategy_lineage_node_id", "strategy_lineage_root_id"),
                "strategy_lineage_nodes",
                ("strategy_lineage_node_id", "strategy_lineage_root_id"),
            ),
        ),
        "selection_scope_aliases": tuple(_base_fks("selection_scope_aliases"))
        + (
            _fk(
                "fk_selection_scope_aliases_scope", ("selection_scope_id",), "selection_scopes", ("selection_scope_id",)
            ),
        ),
        "selection_scope_publishable_decisions": tuple(_base_fks("selection_scope_publishable_decisions"))
        + (
            _fk(
                "fk_selection_scope_publishable_decisions_scope",
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
            _fk(
                "fk_selection_scope_publishable_decisions_decision",
                ("selection_scope_id", "selection_decision_id"),
                "selection_decisions",
                ("selection_scope_id", "selection_decision_id"),
            ),
        ),
        "selection_scope_outcomes": tuple(_base_fks("selection_scope_outcomes"))
        + (
            _fk(
                "fk_selection_scope_outcomes_scope",
                ("selection_scope_id",),
                "selection_scopes",
                ("selection_scope_id",),
            ),
            _fk(
                "fk_selection_scope_outcomes_winner",
                ("selection_scope_id", "selection_decision_id", "family_id"),
                "selection_decisions",
                ("selection_scope_id", "selection_decision_id", "family_id"),
            ),
        ),
        "research_programs": tuple(_base_fks("research_programs"))
        + (
            _fk("fk_research_programs_scope", ("selection_scope_id",), "selection_scopes", ("selection_scope_id",)),
            _fk(
                "fk_research_programs_question_provenance",
                ("research_question_id", "question_hash"),
                "research_questions",
                ("research_question_id", "question_hash"),
            ),
            _fk(
                "fk_research_programs_root_provenance",
                ("strategy_lineage_root_id", "source_manifest_hash"),
                "strategy_lineage_roots",
                ("strategy_lineage_root_id", "source_manifest_hash"),
            ),
        ),
        "selection_decisions": tuple(_base_fks("selection_decisions"))
        + (_fk("fk_selection_decisions_scope", ("selection_scope_id",), "selection_scopes", ("selection_scope_id",)),),
        "global_oos_exposures": tuple(_base_fks("global_oos_exposures"))
        + (
            _fk("fk_global_oos_exposures_scope", ("selection_scope_id",), "selection_scopes", ("selection_scope_id",)),
            _fk(
                "fk_global_oos_exposures_decision_family",
                ("selection_scope_id", "selection_decision_id", "family_id"),
                "selection_decisions",
                ("selection_scope_id", "selection_decision_id", "family_id"),
            ),
        ),
        "global_oos_exposure_consumptions": tuple(_base_fks("global_oos_exposure_consumptions"))
        + (
            _fk(
                "fk_global_oos_exposure_consumptions_exact_exposure",
                ("oos_exposure_id", "selection_decision_id", "family_id"),
                "global_oos_exposures",
                ("oos_exposure_id", "selection_decision_id", "family_id"),
            ),
        ),
    }
)


def _ix(
    table: str,
    suffix: str,
    kind: str,
    columns: tuple[str, ...],
    predicate: tuple[str, str, str] | None = None,
) -> tuple[str, str, tuple[str, ...], tuple[str, str, str] | None]:
    return (f"{kind.lower()}_{table}_{suffix}", kind, columns, predicate)


INDEXES = {
    "research_questions": (
        _ix("research_questions", "question_hash", "UNIQUE", ("question_hash",)),
        _ix("research_questions", "id_hash", "UNIQUE", ("research_question_id", "question_hash")),
        _ix("research_questions", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("research_questions", "record_hash", "UNIQUE", ("record_hash",)),
    ),
    "research_question_aliases": (
        _ix("research_question_aliases", "alias", "UNIQUE", ("alias",)),
        _ix("research_question_aliases", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("research_question_aliases", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("research_question_aliases", "question", "ORDINARY", ("research_question_id",)),
    ),
    "strategy_lineage_roots": (
        _ix("strategy_lineage_roots", "manifest", "UNIQUE", ("source_manifest_hash",)),
        _ix("strategy_lineage_roots", "id_manifest", "UNIQUE", ("strategy_lineage_root_id", "source_manifest_hash")),
        _ix("strategy_lineage_roots", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("strategy_lineage_roots", "record_hash", "UNIQUE", ("record_hash",)),
    ),
    "strategy_lineage_nodes": (
        _ix("strategy_lineage_nodes", "root_manifest", "UNIQUE", ("strategy_lineage_root_id", "node_manifest_hash")),
        _ix("strategy_lineage_nodes", "id_root", "UNIQUE", ("strategy_lineage_node_id", "strategy_lineage_root_id")),
        _ix("strategy_lineage_nodes", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("strategy_lineage_nodes", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("strategy_lineage_nodes", "parent", "ORDINARY", ("parent_strategy_lineage_node_id",)),
    ),
    "strategy_config_lineage_bindings": (
        _ix("strategy_config_lineage_bindings", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("strategy_config_lineage_bindings", "record_hash", "UNIQUE", ("record_hash",)),
        _ix(
            "strategy_config_lineage_bindings",
            "node_root",
            "ORDINARY",
            ("strategy_lineage_node_id", "strategy_lineage_root_id"),
        ),
    ),
    "research_authority_events": (
        _ix("research_authority_events", "authority_ordinal", "UNIQUE", ("authority_instance_id", "event_ordinal")),
        _ix(
            "research_authority_events",
            "subject_action",
            "UNIQUE",
            ("action", "subject_record_type", "subject_id", "subject_record_hash"),
        ),
        _ix("research_authority_events", "grant", "UNIQUE", ("grant_id",)),
        _ix("research_authority_events", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("research_authority_events", "subject", "ORDINARY", ("subject_record_type", "subject_id")),
    ),
    "selection_scopes": (
        _ix("selection_scopes", "mandate", "UNIQUE", ("deployment_mandate_hash",)),
        _ix("selection_scopes", "id_mandate", "UNIQUE", ("selection_scope_id", "deployment_mandate_hash")),
        _ix("selection_scopes", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("selection_scopes", "record_hash", "UNIQUE", ("record_hash",)),
    ),
    "selection_scope_aliases": (
        _ix("selection_scope_aliases", "alias", "UNIQUE", ("alias",)),
        _ix("selection_scope_aliases", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("selection_scope_aliases", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("selection_scope_aliases", "scope", "ORDINARY", ("selection_scope_id",)),
    ),
    "selection_scope_publishable_decisions": (
        _ix("selection_scope_publishable_decisions", "decision", "UNIQUE", ("selection_decision_id",)),
        _ix("selection_scope_publishable_decisions", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("selection_scope_publishable_decisions", "record_hash", "UNIQUE", ("record_hash",)),
    ),
    "selection_scope_outcomes": (
        _ix("selection_scope_outcomes", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("selection_scope_outcomes", "record_hash", "UNIQUE", ("record_hash",)),
        _ix(
            "selection_scope_outcomes",
            "winner_evidence",
            "PARTIAL_UNIQUE",
            ("evidence_ref_hash",),
            ("EQ_LITERAL", "outcome", "WINNER_PINNED"),
        ),
        _ix("selection_scope_outcomes", "decision_family", "ORDINARY", ("selection_decision_id", "family_id")),
    ),
    "research_programs": (
        _ix(
            "research_programs",
            "scope_question_root",
            "UNIQUE",
            ("selection_scope_id", "research_question_id", "strategy_lineage_root_id"),
        ),
        _ix("research_programs", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("research_programs", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("research_programs", "question_provenance", "ORDINARY", ("research_question_id", "question_hash")),
        _ix("research_programs", "root_provenance", "ORDINARY", ("strategy_lineage_root_id", "source_manifest_hash")),
    ),
    "selection_decisions": (
        _ix("selection_decisions", "scope_ordinal", "UNIQUE", ("selection_scope_id", "decision_ordinal")),
        _ix("selection_decisions", "scope_id", "UNIQUE", ("selection_scope_id", "selection_decision_id")),
        _ix(
            "selection_decisions",
            "scope_id_family",
            "UNIQUE",
            ("selection_scope_id", "selection_decision_id", "family_id"),
        ),
        _ix("selection_decisions", "family", "UNIQUE", ("family_id",)),
        _ix("selection_decisions", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("selection_decisions", "record_hash", "UNIQUE", ("record_hash",)),
    ),
    "global_oos_exposures": (
        _ix("global_oos_exposures", "scope_ordinal", "UNIQUE", ("selection_scope_id", "exposure_ordinal")),
        _ix("global_oos_exposures", "fingerprint", "UNIQUE", ("exposure_fingerprint",)),
        _ix("global_oos_exposures", "decision", "UNIQUE", ("selection_decision_id",)),
        _ix("global_oos_exposures", "family", "UNIQUE", ("family_id",)),
        _ix("global_oos_exposures", "exact_tuple", "UNIQUE", ("oos_exposure_id", "selection_decision_id", "family_id")),
        _ix("global_oos_exposures", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("global_oos_exposures", "record_hash", "UNIQUE", ("record_hash",)),
        _ix(
            "global_oos_exposures",
            "scope_interval",
            "ORDINARY",
            ("selection_scope_id", "period_start_us", "period_end_us"),
        ),
    ),
    "global_oos_exposure_consumptions": (
        _ix("global_oos_exposure_consumptions", "access_attempt", "UNIQUE", ("access_attempt_id",)),
        _ix("global_oos_exposure_consumptions", "authority_event", "UNIQUE", ("authority_event_id",)),
        _ix("global_oos_exposure_consumptions", "record_hash", "UNIQUE", ("record_hash",)),
        _ix("global_oos_exposure_consumptions", "decision_family", "ORDINARY", ("selection_decision_id", "family_id")),
    ),
}


CHAIN_SPECS = {
    "research_authority_events": (
        ("authority_instance_id",),
        "event_ordinal",
        "FAMILY_AUTHORITY_GENESIS_RECORD_HASH",
        "prev_record_hash",
        "record_hash",
        "LATEST_RECORD_HASH",
    ),
    "selection_decisions": (
        ("authority_instance_id", "selection_scope_id"),
        "decision_ordinal",
        "FAMILY_AUTHORITY_GENESIS_RECORD_HASH",
        "prev_record_hash",
        "record_hash",
        "LATEST_RECORD_HASH",
    ),
    "global_oos_exposures": (
        ("authority_instance_id", "selection_scope_id"),
        "exposure_ordinal",
        "FAMILY_AUTHORITY_GENESIS_RECORD_HASH",
        "prev_record_hash",
        "record_hash",
        "LATEST_RECORD_HASH",
    ),
}

ACTION_POLICIES = (
    ("QUESTION_ISSUED", "research_questions", True),
    ("QUESTION_ALIAS_REGISTERED", "research_question_aliases", True),
    ("LINEAGE_ROOT_REGISTERED", "strategy_lineage_roots", True),
    ("LINEAGE_NODE_REGISTERED", "strategy_lineage_nodes", True),
    ("CONFIG_LINEAGE_BOUND", "strategy_config_lineage_bindings", True),
    ("SELECTION_SCOPE_ISSUED", "selection_scopes", True),
    ("SELECTION_SCOPE_ALIAS_REGISTERED", "selection_scope_aliases", True),
    ("RESEARCH_PROGRAM_ISSUED", "research_programs", False),
    ("SELECTION_DECISION_ISSUED", "selection_decisions", False),
    ("PUBLISHABLE_DECISION_RESERVED", "selection_scope_publishable_decisions", False),
    ("SELECTION_SCOPE_OUTCOME_RECORDED", "selection_scope_outcomes", False),
    ("OOS_EXPOSURE_ISSUED", "global_oos_exposures", False),
    ("OOS_EXPOSURE_CONSUMED", "global_oos_exposure_consumptions", False),
    (
        "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1",
        "artifact_publication_receipts",
        False,
    ),
)

GOVERNED_SUBJECT_ID_COLUMNS = {
    "research_questions": "research_question_id",
    "research_question_aliases": "alias_hash",
    "strategy_lineage_roots": "strategy_lineage_root_id",
    "strategy_lineage_nodes": "strategy_lineage_node_id",
    "strategy_config_lineage_bindings": "config_hash",
    "selection_scopes": "selection_scope_id",
    "selection_scope_aliases": "alias_hash",
}

AUTHORITY_DERIVED_IDENTITIES = {
    "research_questions": ("research_question_id",),
    "strategy_lineage_roots": ("strategy_lineage_root_id",),
    "strategy_lineage_nodes": ("strategy_lineage_node_id",),
    "research_authority_events": ("event_id",),
    "selection_scopes": ("selection_scope_id",),
    "research_programs": ("research_program_id",),
    "selection_decisions": ("selection_decision_id", "family_id"),
    "global_oos_exposures": ("oos_exposure_id",),
}

IDENTITY_DERIVATIONS = {
    ("research_questions", "research_question_id"): (
        "research_questions_question_id_issuance",
        "QUANTOS_RESEARCH_QUESTION_ID_V1",
        ("authority_instance_id", "question_hash"),
    ),
    ("strategy_lineage_roots", "strategy_lineage_root_id"): (
        "strategy_lineage_roots_root_id_issuance",
        "QUANTOS_STRATEGY_LINEAGE_ROOT_ID_V1",
        ("authority_instance_id", "source_manifest_hash"),
    ),
    ("strategy_lineage_nodes", "strategy_lineage_node_id"): (
        "strategy_lineage_nodes_node_id_issuance",
        "QUANTOS_STRATEGY_LINEAGE_NODE_ID_V1",
        (
            "authority_instance_id",
            "strategy_lineage_root_id",
            "parent_strategy_lineage_node_id",
            "node_manifest_hash",
        ),
    ),
    ("research_authority_events", "event_id"): (
        "research_authority_events_event_id_issuance",
        "QUANTOS_RESEARCH_AUTHORITY_EVENT_ID_V1",
        ("authority_instance_id", "event_ordinal"),
    ),
    ("selection_scopes", "selection_scope_id"): (
        "selection_scopes_scope_id_issuance",
        "QUANTOS_SELECTION_SCOPE_ID_V1",
        ("authority_instance_id", "deployment_mandate_hash"),
    ),
    ("research_programs", "research_program_id"): (
        "research_programs_program_id_issuance",
        "QUANTOS_RESEARCH_PROGRAM_ID_V1",
        (
            "authority_instance_id",
            "selection_scope_id",
            "research_question_id",
            "strategy_lineage_root_id",
        ),
    ),
    ("selection_decisions", "selection_decision_id"): (
        "selection_decisions_decision_and_family_id_issuance",
        "QUANTOS_SELECTION_DECISION_ID_V1",
        ("authority_instance_id", "selection_scope_id", "decision_ordinal"),
    ),
    ("selection_decisions", "family_id"): (
        "selection_decisions_decision_and_family_id_issuance",
        "QUANTOS_DATA_SNOOPING_FAMILY_ID_V1",
        (
            "authority_instance_id",
            "selection_scope_id",
            "decision_ordinal",
            "decision_class",
        ),
    ),
    ("global_oos_exposures", "oos_exposure_id"): (
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
    ),
}

IDENTITY_READ_SURFACES = {
    "research_questions_question_id_issuance": (
        "research_question_id",
        "authority_instance_id",
        "question_hash",
        "authority_event_id",
    ),
    "strategy_lineage_roots_root_id_issuance": (
        "strategy_lineage_root_id",
        "authority_instance_id",
        "source_manifest_hash",
        "authority_event_id",
    ),
    "strategy_lineage_nodes_node_id_issuance": (
        "strategy_lineage_node_id",
        "authority_instance_id",
        "strategy_lineage_root_id",
        "parent_strategy_lineage_node_id",
        "node_manifest_hash",
        "authority_event_id",
    ),
    "research_authority_events_event_id_issuance": (
        "event_id",
        "authority_instance_id",
        "event_ordinal",
        "prev_record_hash",
    ),
    "selection_scopes_scope_id_issuance": (
        "selection_scope_id",
        "authority_instance_id",
        "deployment_mandate_hash",
        "authority_event_id",
    ),
    "research_programs_program_id_issuance": (
        "research_program_id",
        "authority_instance_id",
        "selection_scope_id",
        "research_question_id",
        "strategy_lineage_root_id",
        "authority_event_id",
    ),
    "selection_decisions_decision_and_family_id_issuance": (
        "selection_decision_id",
        "family_id",
        "authority_instance_id",
        "selection_scope_id",
        "decision_ordinal",
        "decision_class",
        "authority_event_id",
    ),
    "global_oos_exposures_global_exposure_id": (
        "oos_exposure_id",
        "authority_instance_id",
        "selection_scope_id",
        "selection_decision_id",
        "family_id",
        "exposure_ordinal",
        "exposure_fingerprint",
        "authority_event_id",
    ),
}

IDENTITY_PAYLOAD_SCHEMA_VERSION = "QUANTOS_AUTHORITY_DERIVED_ID_PAYLOAD_V1"
IDENTITY_PAYLOAD_KEYS = (
    "schema_version",
    "domain_separator",
    "authority_chain_predecessor",
    "ordered_source_fields",
    "field_name",
    "field_value",
)
IDENTITY_GOLDEN_PREDECESSOR = "0" * 64
IDENTITY_GOLDEN_VECTORS = {
    "QUANTOS_RESEARCH_QUESTION_ID_V1": (
        ("authority:g1", "1" * 64),
        "4700118ebfcefbc24bda6dc42762f39f67be448335a202d5f09e9e3b193be519",
    ),
    "QUANTOS_STRATEGY_LINEAGE_ROOT_ID_V1": (
        ("authority:g1", "2" * 64),
        "9c53bde4fd81ac49ad4642230fc6a1e10c40696b209c205e0d472597e373ed9c",
    ),
    "QUANTOS_STRATEGY_LINEAGE_NODE_ID_V1": (
        ("authority:g1", "3" * 64, None, "4" * 64),
        "b6a6e7b90e7a906103a9bed7884a35f23bc78957ae7fd127793f08332871d871",
    ),
    "QUANTOS_RESEARCH_AUTHORITY_EVENT_ID_V1": (
        ("authority:g1", 1),
        "f69e0288782fcc3efa38847bbc2b112d524d285e6655eb789894d3069e84f492",
    ),
    "QUANTOS_SELECTION_SCOPE_ID_V1": (
        ("authority:g1", "5" * 64),
        "66df0045ca2e5a33f60ba275f4cdd74276a14c5a1d2994c321aedd21aea21f67",
    ),
    "QUANTOS_RESEARCH_PROGRAM_ID_V1": (
        ("authority:g1", "6" * 64, "7" * 64, "8" * 64),
        "70c2ec931de72eea8c0196024037aba00c272e7e5c3440f0db864e139602fed6",
    ),
    "QUANTOS_SELECTION_DECISION_ID_V1": (
        ("authority:g1", "6" * 64, 1),
        "e68640179178a5c2ad0eff007990bfbe8b4eae9c0ea3d6262691c455be58b373",
    ),
    "QUANTOS_DATA_SNOOPING_FAMILY_ID_V1": (
        ("authority:g1", "6" * 64, 1, "PUBLISHABLE"),
        "7d10f4932d2c72203ed5a86357df9e7e185d8f10ff0ca359a2f58875826db6db",
    ),
    "QUANTOS_GLOBAL_OOS_EXPOSURE_ID_V1": (
        ("authority:g1", "6" * 64, "9" * 64, "a" * 64, 1, "b" * 64),
        "4735da442cdc53da4ddec25ce3a8ba56010e64e7569748e8b49783267570c775",
    ),
}

LOCAL_EVENT_PREDECESSOR = (
    "LOCAL_COLUMN",
    "prev_record_hash",
    None,
    None,
    None,
    None,
)
SUBJECT_EVENT_PREDECESSOR = (
    "EXACT_AUTHORITY_EVENT_LOOKUP",
    None,
    "authority_event_id",
    "research_authority_events",
    "event_id",
    "prev_record_hash",
)

CANONICAL_CONTENT_HASHES = {
    ("research_questions", "question_hash"): (
        "question_payload_json",
        "CANONICAL_JSON_DOCUMENT",
        None,
        "research_questions_canonical_question_hash",
    ),
    ("research_question_aliases", "alias_hash"): (
        "alias",
        "CANONICAL_JSON_SINGLE_FIELD_OBJECT",
        "alias",
        "research_question_aliases_canonical_alias_hash",
    ),
    ("strategy_lineage_roots", "source_manifest_hash"): (
        "source_manifest_payload_json",
        "CANONICAL_JSON_DOCUMENT",
        None,
        "strategy_lineage_roots_canonical_source_manifest_hash",
    ),
    ("strategy_lineage_nodes", "node_manifest_hash"): (
        "node_manifest_payload_json",
        "CANONICAL_JSON_DOCUMENT",
        None,
        "strategy_lineage_nodes_node_manifest_hash",
    ),
    ("selection_scopes", "deployment_mandate_hash"): (
        "deployment_mandate_payload_json",
        "CANONICAL_JSON_DOCUMENT",
        None,
        "selection_scopes_canonical_mandate_hash",
    ),
    ("selection_scope_aliases", "alias_hash"): (
        "alias",
        "CANONICAL_JSON_SINGLE_FIELD_OBJECT",
        "alias",
        "selection_scope_aliases_canonical_alias_hash",
    ),
}

GUARDS = {
    "research_questions": ("AUTHORITY_EVENT_MATCH", "CANONICAL_PAYLOAD_HASH_MATCH"),
    "research_question_aliases": ("AUTHORITY_EVENT_MATCH",),
    "strategy_lineage_roots": (
        "AUTHORITY_EVENT_MATCH",
        "CANONICAL_PAYLOAD_HASH_MATCH",
    ),
    "strategy_lineage_nodes": ("AUTHORITY_EVENT_MATCH", "CANONICAL_PAYLOAD_HASH_MATCH"),
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

VERIFIER_OBLIGATIONS = {
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


def _base_checks(table: str, time_column: str | None) -> dict[str, tuple[set[str], set[object]]]:
    checks: dict[str, tuple[set[str], set[object]]] = {
        f"{table}_record_type": ({"record_type"}, {RECORD_TYPES[table]}),
    }
    if time_column is not None:
        checks[f"{table}_positive_time"] = ({time_column}, {0})
    return checks


CHECK_REQUIREMENTS = {
    "research_questions": _base_checks("research_questions", "issued_at_us"),
    "research_question_aliases": _base_checks("research_question_aliases", "registered_at_us"),
    "strategy_lineage_roots": _base_checks("strategy_lineage_roots", "registered_at_us"),
    "strategy_lineage_nodes": {
        **_base_checks("strategy_lineage_nodes", "registered_at_us"),
        "strategy_lineage_nodes_parent_not_self": (
            {"strategy_lineage_node_id", "parent_strategy_lineage_node_id"},
            set(),
        ),
    },
    "strategy_config_lineage_bindings": _base_checks("strategy_config_lineage_bindings", "bound_at_us"),
    "research_authority_events": {
        **_base_checks("research_authority_events", "event_at_us"),
        "research_authority_events_positive_ordinal": ({"event_ordinal"}, {0}),
        "research_authority_events_action_domain": (
            {"action"},
            {row[0] for row in ACTION_POLICIES},
        ),
        "research_authority_events_actor_kind_domain": (
            {"audit_actor_kind"},
            {"HUMAN", "AGENT", "SYSTEM"},
        ),
        "research_authority_events_grant_reference_tuple": (
            {
                "grant_id",
                "grant_record_hash",
                "grant_policy_id",
                "grant_policy_version",
                "grant_policy_hash",
                "grant_authority_id",
                "grant_authority_head",
            },
            set(),
        ),
    },
    "selection_scopes": _base_checks("selection_scopes", "issued_at_us"),
    "selection_scope_aliases": _base_checks("selection_scope_aliases", "registered_at_us"),
    "selection_scope_publishable_decisions": _base_checks("selection_scope_publishable_decisions", "reserved_at_us"),
    "selection_scope_outcomes": {
        **_base_checks("selection_scope_outcomes", "recorded_at_us"),
        "selection_scope_outcomes_domain": (
            {"outcome"},
            {"WINNER_PINNED", "CLOSED_WITHOUT_PROMOTION"},
        ),
        "selection_scope_outcomes_branch": (
            {
                "outcome",
                "selection_decision_id",
                "family_id",
                "winner_alpha_id",
                "evidence_ref_hash",
            },
            {"WINNER_PINNED", "CLOSED_WITHOUT_PROMOTION"},
        ),
    },
    "research_programs": _base_checks("research_programs", "issued_at_us"),
    "selection_decisions": {
        **_base_checks("selection_decisions", "issued_at_us"),
        "selection_decisions_positive_ordinal": ({"decision_ordinal"}, {0}),
        "selection_decisions_class_domain": (
            {"decision_class"},
            {"PUBLISHABLE", "DIAGNOSTIC_ONLY"},
        ),
    },
    "global_oos_exposures": {
        **_base_checks("global_oos_exposures", "issued_at_us"),
        "global_oos_exposures_positive_ordinal": ({"exposure_ordinal"}, {0}),
        "global_oos_exposures_strict_interval": (
            {"period_start_us", "period_end_us"},
            {0},
        ),
    },
    "global_oos_exposure_consumptions": {
        **_base_checks("global_oos_exposure_consumptions", "consumed_at_us"),
        "global_oos_exposure_consumptions_positive_expected_version": (
            {"expected_exposure_version"},
            {0},
        ),
    },
}


def _schema_module() -> ModuleType:
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == MODULE:
            pytest.fail(f"missing C4 schema-spec module: {MODULE}")
        raise
    assert hasattr(module, "C4_SCHEMA_COMPONENT"), "missing C4 schema-spec symbol: C4_SCHEMA_COMPONENT"
    assert hasattr(module, "C4_AUTHORITY_TABLE_SPECS"), "missing C4 schema-spec symbol: C4_AUTHORITY_TABLE_SPECS"
    return module


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _payload(value: object) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")  # type: ignore[no-any-return,union-attr]
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise AssertionError(f"schema AST value is not inspectable: {type(value).__name__}")


def _walk(value: object):
    if is_dataclass(value):
        yield from _walk(asdict(value))
    elif isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk(item)


def _table_map(component: object) -> dict[str, object]:
    return {table.table_name: table for table in component.tables}  # type: ignore[attr-defined]


def _column_shape(column: object) -> tuple[object, object, object]:
    assert column.default is None  # type: ignore[attr-defined]
    return (column.name, _enum_value(column.sql_type), column.nullable)  # type: ignore[attr-defined]


def _fk_shape(fk: object) -> tuple[object, ...]:
    return (
        tuple(fk.columns),
        fk.referenced_table,
        tuple(fk.referenced_columns),
        _enum_value(fk.on_update),
        _enum_value(fk.on_delete),
        _enum_value(fk.deferrability),
    )


def _predicate_shape(predicate: object | None) -> tuple[object, ...] | None:
    if predicate is None:
        return None
    payload = _payload(predicate)
    left = payload.get("left")
    right = payload.get("right")
    if (
        isinstance(left, dict)
        and isinstance(right, dict)
        and "column" in left
        and "value" in right
        and _enum_value(payload.get("operator")) == "EQ"
    ):
        return ("EQ_LITERAL", left["column"], right["value"])
    raise AssertionError(f"unexpected partial-index predicate: {payload!r}")


def _index_shape(index: object) -> tuple[object, ...]:
    columns = tuple(item.column for item in index.columns)  # type: ignore[attr-defined]
    assert all(_enum_value(item.direction) == "ASC" for item in index.columns)  # type: ignore[attr-defined]
    assert all(_enum_value(item.collation) == "BINARY" for item in index.columns)  # type: ignore[attr-defined]
    kind = (
        "PARTIAL_UNIQUE"
        if index.unique and index.predicate is not None  # type: ignore[attr-defined]
        else "UNIQUE"
        if index.unique  # type: ignore[attr-defined]
        else "ORDINARY"
    )
    return (
        index.name,
        kind,
        columns,
        _predicate_shape(index.predicate),  # type: ignore[attr-defined]
    )


def _chain_shape(chain: object) -> tuple[object, ...]:
    return (
        tuple(chain.partition_key_columns),
        chain.ordinal_column,
        _enum_value(chain.genesis_rule),
        chain.previous_hash_column,
        chain.record_hash_column,
        _enum_value(chain.terminal_head_rule),
    )


def test_c4_schema_spec_module_and_root_symbol_exist() -> None:
    module = _schema_module()
    assert module.C4_SCHEMA_COMPONENT is not None
    assert isinstance(module.C4_AUTHORITY_TABLE_SPECS, tuple)


def test_component_has_exact_14_table_order_and_remains_unsealed_until_c6_c7() -> None:
    module = _schema_module()
    component = module.C4_SCHEMA_COMPONENT
    assert component.component_id == COMPONENT_ID
    assert component.capabilities == (
        "c4.questions_lineage_scope_decisions_exposures.core",
        "c4.global_oos_exposure.core",
        "c4.global_oos_exposure.verify",
        "c4.research_program.core",
    )
    assert component.tables is _schema_module().C4_AUTHORITY_TABLE_SPECS
    assert tuple(table.table_name for table in component.tables) == TABLE_ORDER
    extensions = tuple(
        (
            item.extension_id,
            item.target_table,
            item.provider_component_id,
            item.required_capability,
        )
        for item in component.required_extensions
    )
    assert extensions == (
        (
            C6_EXTENSION,
            "global_oos_exposures",
            C6_COMPONENT,
            "c6.global_oos_exposure.embedded_refs",
        ),
        (
            C7_EXTENSION,
            "global_oos_exposure_consumptions",
            C7_COMPONENT,
            "c7.global_oos_consumption.attempt_binding",
        ),
    )

    recipes = component.auxiliary_semantic_recipes
    assert recipes == (
        *module.C4_ACTION_POLICIES,
        *module.C4_IDENTITY_ISSUANCE,
        *module.C4_CANONICAL_CONTENT_HASHES,
    )
    assert len(recipes) == 29
    assert {
        _enum_value(recipe.recipe_kind): sum(
            other.recipe_kind is recipe.recipe_kind for other in recipes
        )
        for recipe in recipes
    } == {
        "ACTION_POLICY": 14,
        "IDENTITY_DERIVATION": 9,
        "CANONICAL_CONTENT_HASH": 6,
    }
    assert len({recipe.recipe_id for recipe in recipes}) == len(recipes)
    assert all(recipe.columns for recipe in recipes)
    assert all(recipe.required_rule_ids for recipe in recipes)
    assert not hasattr(component, "sealed"), "only Pass M may derive a SEALED manifest"


def test_c4_contract_coverage_resolves_real_contracts_fields_and_owned_storage() -> None:
    module = _schema_module()
    research_contracts = importlib.import_module("quantos.contracts.research")
    coverage = module.C4_CONTRACT_COVERAGE

    assert isinstance(coverage, tuple)
    assert module.C4_SCHEMA_COMPONENT.coverage is coverage
    assert module.C4_SCHEMA_COMPONENT.contract_derivations is module.C4_CONTRACT_DERIVATIONS
    assert module.C4_SCHEMA_COMPONENT.required_contract_surfaces is module.C4_REQUIRED_CONTRACT_SURFACES

    tables = _table_map(module.C4_SCHEMA_COMPONENT)
    actual: dict[tuple[str, str], tuple[str, str | None, str | None, str | None]] = {}
    for item in coverage:
        assert hasattr(research_contracts, item.contract_type), item.contract_type
        contract = getattr(research_contracts, item.contract_type)
        assert item.field_name in contract.model_fields, (
            item.contract_type,
            item.field_name,
        )
        key = (item.contract_type, item.field_name)
        assert key not in actual, f"duplicate C4 contract coverage: {key!r}"
        kind = str(_enum_value(item.kind))
        actual[key] = (
            kind,
            item.table_name,
            item.target_field,
            item.derivation_id,
        )
        if kind in {"COLUMN", "CANONICAL_PAYLOAD"}:
            assert item.table_name in tables, key
            assert item.target_field in {column.name for column in tables[item.table_name].columns}, key
            assert item.derivation_id is None, key
        else:
            assert kind == "NON_PERSISTED_DERIVATION", key
            assert item.table_name is None and item.target_field is None, key
            assert item.derivation_id, key

    expected: dict[tuple[str, str], tuple[str, str | None, str | None, str | None]] = {}
    for contract_type, fields in EXPECTED_COLUMN_COVERAGE.items():
        for field_name, (table_name, target_field) in fields.items():
            expected[(contract_type, field_name)] = (
                "COLUMN",
                table_name,
                target_field,
                None,
            )
    for contract_type, fields in EXPECTED_CANONICAL_PAYLOAD_COVERAGE.items():
        for field_name, (table_name, target_field) in fields.items():
            expected[(contract_type, field_name)] = (
                "CANONICAL_PAYLOAD",
                table_name,
                target_field,
                None,
            )
    for key, derivation_id in EXPECTED_DERIVED_COVERAGE.items():
        expected[key] = (
            "NON_PERSISTED_DERIVATION",
            None,
            None,
            derivation_id,
        )
    assert actual == expected

    actual_derivations = {
        item.derivation_id: (
            item.contract_type,
            item.field_name,
            str(_enum_value(item.kind)),
            item.binding_table,
            item.binding_field,
            item.source_table,
            item.source_key_field,
            item.source_value_field,
        )
        for item in module.C4_CONTRACT_DERIVATIONS
    }
    assert actual_derivations == EXPECTED_DERIVATION_METADATA
    for item in module.C4_CONTRACT_DERIVATIONS:
        assert item.binding_table in tables
        assert item.binding_field in {column.name for column in tables[item.binding_table].columns}
        assert item.source_table in tables
        source_columns = {column.name for column in tables[item.source_table].columns}
        assert item.source_key_field in source_columns
        assert item.source_value_field in source_columns


def test_c4_and_required_c6_surfaces_partition_every_real_contract_field_once() -> None:
    module = _schema_module()
    research_contracts = importlib.import_module("quantos.contracts.research")
    required = module.C4_REQUIRED_CONTRACT_SURFACES
    assert tuple(
        (
            item.contract_type,
            item.provider_component_id,
            item.required_capability,
            tuple(item.field_names),
        )
        for item in required
    ) == (
        (
            "OosExposureRef",
            C6_COMPONENT,
            "c6.global_oos_exposure.embedded_refs",
            C6_REQUIRED_CONTRACT_FIELDS,
        ),
    )

    owned: dict[str, set[str]] = {}
    for item in module.C4_CONTRACT_COVERAGE:
        owned.setdefault(item.contract_type, set()).add(item.field_name)
    delegated: dict[str, set[str]] = {}
    for item in required:
        assert hasattr(research_contracts, item.contract_type)
        contract = getattr(research_contracts, item.contract_type)
        assert len(set(item.field_names)) == len(item.field_names)
        assert set(item.field_names).issubset(contract.model_fields)
        delegated.setdefault(item.contract_type, set()).update(item.field_names)

    contract_names = (
        "StructuredResearchQuestion",
        "ResearchQuestionRef",
        "ResearchQuestionAlias",
        "SourceManifest",
        "StrategyLineageRootRef",
        "StrategyLineageNodeRef",
        "DeploymentMandate",
        "SelectionScopeRef",
        "SelectionScopeAlias",
        "SelectionScopeOutcome",
        "ResearchProgramRef",
        "SelectionDecisionRef",
        "OosExposureRef",
        "OosExposureConsumption",
    )
    for contract_name in contract_names:
        contract = getattr(research_contracts, contract_name)
        assert owned.get(contract_name, set()).isdisjoint(delegated.get(contract_name, set()))
        assert owned.get(contract_name, set()) | delegated.get(contract_name, set()) == set(contract.model_fields), (
            contract_name
        )


def test_columns_primary_keys_foreign_keys_and_indexes_are_exact() -> None:
    module = _schema_module()
    tables = _table_map(module.C4_SCHEMA_COMPONENT)
    for name in TABLE_ORDER:
        table = tables[name]
        assert isinstance(table, module.AuthorityTableSpec), name
        assert tuple(_column_shape(column) for column in table.columns) == COLUMNS[name], name
        assert tuple(table.primary_key_columns) == PRIMARY_KEYS[name], name
        assert tuple(_fk_shape(fk) for fk in table.foreign_keys) == FOREIGN_KEYS[name], name
        assert tuple(_index_shape(index) for index in table.indexes) == INDEXES[name], name


def test_every_row_hash_payload_is_exact_and_genesis_bound() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    for name in TABLE_ORDER:
        table = tables[name]
        expected_fields = tuple(column[0] for column in COLUMNS[name] if column[0] != "record_hash")
        assert _enum_value(table.row_hash.encoding) == "QUANTOS_CANONICAL_JSON_V1", name
        assert _enum_value(table.row_hash.algorithm) == "SHA256", name
        assert table.row_hash.version_literal == RECORD_TYPES[name], name
        assert tuple(table.row_hash.canonical_fields) == expected_fields, name
        assert table.row_hash.record_hash_column == "record_hash", name
        assert expected_fields[:3] == ("record_type", "authority_instance_id", "genesis_record_hash")


def test_exactly_three_ordered_authorities_have_unambiguous_chains() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    chained = tuple(name for name in TABLE_ORDER if tables[name].chain is not None)
    assert chained == tuple(CHAIN_SPECS)
    for name, expected in CHAIN_SPECS.items():
        assert _chain_shape(tables[name].chain) == expected, name


def test_all_tables_are_append_only_with_exact_owned_trigger_names() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    for name in TABLE_ORDER:
        immutable = tuple(
            trigger for trigger in tables[name].triggers if _enum_value(trigger.kind) in {"NO_UPDATE", "NO_DELETE"}
        )
        assert tuple(_enum_value(trigger.kind) for trigger in immutable) == (
            "NO_UPDATE",
            "NO_DELETE",
        ), name
        assert tuple(trigger.name for trigger in immutable) == (
            f"{name}_no_update",
            f"{name}_no_delete",
        ), name
        assert all(_enum_value(trigger.timing) == "BEFORE" for trigger in immutable)
        assert all(_enum_value(trigger.effect) == "ABORT" for trigger in immutable)
        assert all(trigger.error_code == "AUTHORITY_ROW_IMMUTABLE" for trigger in immutable)


def test_checks_are_exact_closed_ast_expressions() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    for name, expected in CHECK_REQUIREMENTS.items():
        actual = {check.name: check for check in tables[name].checks}
        assert set(actual) == set(expected), name
        for check_name, (expected_columns, expected_literals) in expected.items():
            payload = _payload(actual[check_name].expression)
            nodes = tuple(_walk(payload))
            columns = {str(node["column"]) for node in nodes if "column" in node}
            literals = {_enum_value(node["value"]) for node in nodes if set(node) == {"value"}}
            assert columns == expected_columns, check_name
            assert literals == expected_literals, check_name


def test_authority_actions_are_closed_and_grants_are_required_only_for_governed_identity_actions() -> None:
    module = _schema_module()
    assert hasattr(module, "C4AuthorityAction"), "missing closed C4AuthorityAction enum"
    assert tuple(member.value for member in module.C4AuthorityAction) == tuple(row[0] for row in ACTION_POLICIES)
    assert hasattr(module, "C4_ACTION_POLICIES"), "missing C4_ACTION_POLICIES metadata"
    policies = tuple(
        (_enum_value(item.action), item.subject_table, item.grant_required) for item in module.C4_ACTION_POLICIES
    )
    assert policies == ACTION_POLICIES


def test_authority_issued_ids_are_explicit_schema_metadata_not_caller_owned() -> None:
    module = _schema_module()
    assert hasattr(module, "IdentityIssuance"), "missing IdentityIssuance enum"
    assert "AUTHORITY_DERIVED" in {member.value for member in module.IdentityIssuance}
    assert hasattr(module, "C4_IDENTITY_ISSUANCE"), "missing C4_IDENTITY_ISSUANCE metadata"
    for name, fields in AUTHORITY_DERIVED_IDENTITIES.items():
        actual = tuple(
            identity.field_name
            for identity in module.C4_IDENTITY_ISSUANCE
            if identity.table_name == name
            if _enum_value(identity.issuance) == "AUTHORITY_DERIVED"
        )
        assert actual == fields, name

    actual_derivations = {
        (item.table_name, item.field_name): (
            item.verification_rule_id,
            item.domain_separator,
            item.source_fields,
        )
        for item in module.C4_IDENTITY_ISSUANCE
    }
    assert actual_derivations == IDENTITY_DERIVATIONS
    assert all(_enum_value(item.encoding) == "QUANTOS_CANONICAL_JSON_V1" for item in module.C4_IDENTITY_ISSUANCE)
    assert all(_enum_value(item.algorithm) == "SHA256" for item in module.C4_IDENTITY_ISSUANCE)
    assert all(
        _enum_value(item.payload_shape)
        == "DOMAIN_SEPARATED_PREDECESSOR_ORDERED_FIELD_VALUES_V1"
        for item in module.C4_IDENTITY_ISSUANCE
    )


def test_authority_id_formula_predecessor_source_and_golden_vectors_are_exact() -> None:
    module = _schema_module()
    assert len(module.C4_IDENTITY_ISSUANCE) == len(IDENTITY_GOLDEN_VECTORS) == 9

    for item in module.C4_IDENTITY_ISSUANCE:
        predecessor = item.predecessor_source
        actual_predecessor = (
            _enum_value(predecessor.kind),
            predecessor.local_column,
            predecessor.binding_field,
            predecessor.source_table,
            predecessor.source_key_field,
            predecessor.source_value_field,
        )
        expected_predecessor = (
            LOCAL_EVENT_PREDECESSOR
            if item.table_name == "research_authority_events"
            else SUBJECT_EVENT_PREDECESSOR
        )
        assert actual_predecessor == expected_predecessor

        assert item.payload_schema_version == IDENTITY_PAYLOAD_SCHEMA_VERSION
        assert (
            item.payload_schema_version_field,
            item.domain_separator_field,
            item.predecessor_field,
            item.ordered_fields_field,
            item.ordered_field_name_field,
            item.ordered_field_value_field,
        ) == IDENTITY_PAYLOAD_KEYS
        assert _enum_value(item.digest_representation) == "LOWERCASE_HEX_64"

        source_values, expected_digest = IDENTITY_GOLDEN_VECTORS[item.domain_separator]
        assert item.golden_vector.predecessor_record_hash == IDENTITY_GOLDEN_PREDECESSOR
        assert item.golden_vector.source_values == source_values
        assert item.golden_vector.expected_sha256 == expected_digest
        assert len(source_values) == len(item.source_fields)
        payload = {
            item.payload_schema_version_field: item.payload_schema_version,
            item.domain_separator_field: item.domain_separator,
            item.predecessor_field: item.golden_vector.predecessor_record_hash,
            item.ordered_fields_field: [
                {
                    item.ordered_field_name_field: field_name,
                    item.ordered_field_value_field: value,
                }
                for field_name, value in zip(
                    item.source_fields,
                    item.golden_vector.source_values,
                    strict=True,
                )
            ],
        }
        assert canonical_sha256(payload) == expected_digest


def test_canonical_content_hashes_freeze_exact_sources_and_named_rules() -> None:
    module = _schema_module()
    actual = {
        (item.table_name, item.hash_field): (
            item.source_field,
            _enum_value(item.source_kind),
            item.object_field_name,
            item.verification_rule_id,
        )
        for item in module.C4_CANONICAL_CONTENT_HASHES
    }
    assert actual == CANONICAL_CONTENT_HASHES
    assert all(_enum_value(item.encoding) == "QUANTOS_CANONICAL_JSON_V1" for item in module.C4_CANONICAL_CONTENT_HASHES)
    assert all(_enum_value(item.algorithm) == "SHA256" for item in module.C4_CANONICAL_CONTENT_HASHES)

    rules = {
        rule.rule_id: rule
        for table in module.C4_SCHEMA_COMPONENT.tables
        for rule in table.verification_rules
    }
    for item in module.C4_CANONICAL_CONTENT_HASHES:
        rule = rules[item.verification_rule_id]
        if _enum_value(item.source_kind) == "CANONICAL_JSON_SINGLE_FIELD_OBJECT":
            assert rule.verifier_code == f"c4.verify_{item.verification_rule_id}_v1"
            assert rule.structural_source is None
            assert item.object_field_name == "alias"
            assert item.golden_vector.source_value == "golden-alias"
            assert (
                item.golden_vector.expected_sha256
                == "348dc09b60d9a07d59a94cec80e1ab18e2d8651490cb90577aed14b62a07a668"
            )
            assert canonical_sha256({"alias": item.golden_vector.source_value}) == (
                item.golden_vector.expected_sha256
            )
        else:
            assert rule.verifier_code is None
            assert _enum_value(rule.structural_source.kind) == "TRIGGER_POLICY"
            assert item.object_field_name is None
            assert item.golden_vector is None


def test_grant_and_actor_provenance_are_typed_fail_closed_external_references() -> None:
    component = _schema_module().C4_SCHEMA_COMPONENT
    table = _table_map(component)["research_authority_events"]
    rules = {rule.rule_id: rule for rule in table.verification_rules}

    grant = rules["research_authority_events_grant_policy"]
    assert _enum_value(grant.kind) == "EXTERNAL_AUTHORITY_REFERENCE"
    assert _enum_value(grant.external_reference_kind) == "MULTI_PARTY_GRANT"
    assert grant.provider_contract_type == "MultiPartyGrantEvidence"
    assert grant.provider_capability == "multi_party_grant_evidence.verify"
    assert grant.provider_component_id is None
    assert grant.nullable_reference is True
    assert grant.reference_presence_column == "grant_id"
    assert grant.columns == tuple(binding.local_column for binding in grant.external_bindings)
    assert tuple(
        (binding.local_column, binding.provider_field_path)
        for binding in grant.external_bindings
    ) == (
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

    actor = rules["research_authority_events_audit_actor_provenance"]
    assert _enum_value(actor.kind) == "EXTERNAL_AUTHORITY_REFERENCE"
    assert _enum_value(actor.external_reference_kind) == "ACTOR_PROVENANCE"
    assert actor.provider_contract_type == "ActorProvenanceEvidence"
    assert actor.provider_capability == "actor_provenance_evidence.verify"
    assert actor.provider_component_id is None
    assert actor.nullable_reference is False
    assert actor.reference_presence_column is None
    assert actor.columns == tuple(binding.local_column for binding in actor.external_bindings)
    assert tuple(
        (binding.local_column, binding.provider_field_path)
        for binding in actor.external_bindings
    ) == (
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
    assert grant.verifier_code == "c4.verify_research_authority_events_grant_policy_v1"
    assert actor.verifier_code == (
        "c4.verify_research_authority_events_audit_actor_provenance_v1"
    )
    assert grant.structural_source is actor.structural_source is None

    requirements = tuple(
        (
            requirement.provider_contract_type,
            requirement.provider_capability,
            _enum_value(requirement.resolution_stage),
        )
        for requirement in component.required_external_provider_capabilities
    )
    assert requirements == (
        (
            "MultiPartyGrantEvidence",
            "multi_party_grant_evidence.verify",
            "C8_PASS_M",
        ),
        (
            "ActorProvenanceEvidence",
            "actor_provenance_evidence.verify",
            "C8_PASS_M",
        ),
    )

    profiles = {
        profile.provider_contract_type: profile
        for profile in component.external_authority_claim_profiles
    }
    assert set(profiles) == {"MultiPartyGrantEvidence", "ActorProvenanceEvidence"}
    grant_profile = profiles["MultiPartyGrantEvidence"]
    assert grant_profile.provider_capability == "multi_party_grant_evidence.verify"
    assert _enum_value(grant_profile.external_reference_kind) == "MULTI_PARTY_GRANT"
    assert grant_profile.required_binding_columns == grant.columns
    assert grant_profile.fail_closed is True
    assert grant_profile.default_pass is False
    assert tuple(
        (
            claim.claim_id,
            claim.provider_field_path,
            _enum_value(claim.operator),
            claim.expected_value,
        )
        for claim in grant_profile.required_claims
    ) == (
        ("grant_consumed", "claims.state", "EQUALS", "CONSUMED"),
        ("three_verified_humans", "claims.verified_human_actor_count", "AT_LEAST", 3),
        ("distinct_human_actors", "claims.distinct_human_actors", "IS_TRUE", True),
        ("complete_hash_chain", "claims.complete_hash_chain", "IS_TRUE", True),
        ("grant_not_reused", "claims.grant_not_reused", "IS_TRUE", True),
        ("subject_action_bound", "claims.subject_action_bound", "IS_TRUE", True),
        ("consumption_time_bound", "claims.consumption_time_bound", "IS_TRUE", True),
        ("policy_verified", "claims.policy_verified", "IS_TRUE", True),
        ("authority_head_verified", "claims.authority_head_verified", "IS_TRUE", True),
    )

    actor_profile = profiles["ActorProvenanceEvidence"]
    assert actor_profile.provider_capability == "actor_provenance_evidence.verify"
    assert _enum_value(actor_profile.external_reference_kind) == "ACTOR_PROVENANCE"
    assert actor_profile.required_binding_columns == actor.columns
    assert actor_profile.fail_closed is True
    assert actor_profile.default_pass is False
    assert tuple(
        (
            claim.claim_id,
            claim.provider_field_path,
            _enum_value(claim.operator),
            claim.expected_value,
        )
        for claim in actor_profile.required_claims
    ) == (
        ("provenance_verified", "claims.provenance_verified", "IS_TRUE", True),
        ("actor_kind_verified", "claims.actor_kind_verified", "IS_TRUE", True),
        ("action_context_bound", "claims.action_context_bound", "IS_TRUE", True),
        ("subject_context_bound", "claims.subject_context_bound", "IS_TRUE", True),
        ("event_time_bound", "claims.event_time_bound", "IS_TRUE", True),
        ("evidence_hash_verified", "claims.evidence_hash_verified", "IS_TRUE", True),
        ("policy_verified", "claims.policy_verified", "IS_TRUE", True),
        ("authority_head_verified", "claims.authority_head_verified", "IS_TRUE", True),
    )


def test_named_local_verifiers_have_exact_read_surfaces_and_grant_adapter_dependencies() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    rules = {
        rule.rule_id: rule
        for table in tables.values()
        for rule in table.verification_rules
        if rule.verifier_code is not None
    }
    assert len(rules) == 20
    assert all(rule.columns for rule in rules.values())
    assert all(len(set(rule.columns)) == len(rule.columns) for rule in rules.values())

    for rule_id, expected_columns in IDENTITY_READ_SURFACES.items():
        rule = rules[rule_id]
        assert rule.columns == expected_columns
        if rule_id == "research_authority_events_event_id_issuance":
            assert rule.referenced_table is None
            assert rule.required_rule_ids == ()
        else:
            table_name = next(
                identity.table_name
                for identity in _schema_module().C4_IDENTITY_ISSUANCE
                if identity.verification_rule_id == rule_id
            )
            assert rule.referenced_table == "research_authority_events"
            expected_dependencies = [f"{table_name}_authority_event_binding"]
            if rule_id == "global_oos_exposures_global_exposure_id":
                expected_dependencies.append(
                    "global_oos_exposures_global_exposure_fingerprint"
                )
            assert rule.required_rule_ids == tuple(expected_dependencies)

    for rule_id in (
        "research_question_aliases_canonical_alias_hash",
        "selection_scope_aliases_canonical_alias_hash",
    ):
        assert rules[rule_id].columns == ("alias", "alias_hash")
        assert rules[rule_id].referenced_table is None

    slot = rules["selection_decisions_decision_class_slot_policy"]
    assert slot.columns == (
        "selection_scope_id",
        "selection_decision_id",
        "decision_ordinal",
        "decision_class",
        "prev_record_hash",
    )
    assert slot.referenced_table == "selection_scope_publishable_decisions"
    assert slot.required_rule_ids == (
        "selection_decisions_scope_monotone_decision_chain",
        "selection_scope_publishable_decisions_one_slot_per_scope",
        "selection_scope_publishable_decisions_one_scope_per_slot_decision",
        "selection_scope_publishable_decisions_publishable_decision_match",
    )

    adapter_code = "c4.verify_subject_grant_required_v1"
    event_verifier_code = "c4.verify_research_authority_events_grant_policy_v1"
    for table_name, subject_id_column in GOVERNED_SUBJECT_ID_COLUMNS.items():
        rule = rules[f"{table_name}_grant_required"]
        assert rule.columns == (
            "authority_instance_id",
            "genesis_record_hash",
            "authority_event_id",
            "record_type",
            subject_id_column,
            "record_hash",
        )
        assert _enum_value(rule.kind) == "CROSS_TABLE_REFERENCE"
        assert rule.referenced_table == "research_authority_events"
        assert rule.verifier_code == adapter_code
        assert rule.required_rule_ids == (
            f"{table_name}_authority_event_binding",
            "research_authority_events_grant_policy",
        )
        assert rule.required_verifier_codes == (event_verifier_code,)

def test_cross_table_and_terminal_insert_guards_are_exact_fail_closed_metadata() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    for name, expected_error_codes in GUARDS.items():
        guards = tuple(trigger for trigger in tables[name].triggers if _enum_value(trigger.kind) == "INSERT_GUARD")
        assert tuple(guard.error_code for guard in guards) == expected_error_codes, name
        assert all(guard.violation_when is not None for guard in guards), name
        assert all(_enum_value(guard.event) == "INSERT" for guard in guards), name
        assert all(_enum_value(guard.timing) == "BEFORE" for guard in guards), name
        assert all(_enum_value(guard.effect) == "ABORT" for guard in guards), name


def test_c4_insert_guards_are_closed_semantic_proofs_not_record_type_markers() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    guards = {
        (table_name, guard.error_code): guard.violation_when
        for table_name, table in tables.items()
        for guard in table.triggers
        if _enum_value(guard.kind) == "INSERT_GUARD"
    }
    assert len(guards) == 28

    for table_name in set(TABLE_ORDER) - {"research_authority_events"}:
        node = guards[(table_name, "AUTHORITY_EVENT_MATCH")]
        assert type(node).__name__ == "RelationExistsExpression"
        assert node.table == "research_authority_events"
        assert node.negated is True
        assert "event_id" in {binding.related_column for binding in node.bindings}

    for table_name in (
        "research_questions",
        "strategy_lineage_roots",
        "strategy_lineage_nodes",
        "selection_scopes",
    ):
        assert (
            type(guards[(table_name, "CANONICAL_PAYLOAD_HASH_MATCH")]).__name__
            == "CanonicalJsonPayloadHashExpression"
        )

    for table_name in (
        "research_authority_events",
        "selection_decisions",
        "global_oos_exposures",
    ):
        node = guards[(table_name, "CHAIN_APPEND_NEXT")]
        assert type(node).__name__ == "ChainPredecessorExpression"
        assert node.table == table_name
        assert node.negated is True

    action_subject = guards[("research_authority_events", "CLOSED_ACTION_SUBJECT_MATCH")]
    assert type(action_subject).__name__ == "NotExpression"
    assert _enum_value(action_subject.operand.operator) == "OR"
    assert len(action_subject.operand.operands) == len(ACTION_POLICIES)

    grant_presence = guards[("research_authority_events", "GRANT_PRESENCE_POLICY_MATCH")]
    assert type(grant_presence).__name__ == "NotExpression"
    assert _enum_value(grant_presence.operand.operator) == "OR"
    assert len(grant_presence.operand.operands) == len(ACTION_POLICIES)

    overlap = guards[("global_oos_exposures", "NO_OVERLAPPING_SCOPE_INTERVAL")]
    assert type(overlap).__name__ == "RelationExistsExpression"
    assert overlap.table == "global_oos_exposures"
    assert overlap.negated is False

    version = guards[("global_oos_exposure_consumptions", "EXPECTED_EXPOSURE_VERSION_MATCH")]
    assert type(version).__name__ == "RelationExistsExpression"
    assert version.table == "global_oos_exposures"
    assert version.negated is True
    assert version.predicate is not None

    diagnostic = guards[("global_oos_exposure_consumptions", "DIAGNOSTIC_REQUIRES_SCOPE_OUTCOME")]
    assert type(diagnostic).__name__ == "LogicalExpression"
    assert _enum_value(diagnostic.operator) == "AND"
    assert tuple(type(item).__name__ for item in diagnostic.operands) == (
        "RelationJoinAggregateExpression",
        "RelationJoinAggregateExpression",
    )


def test_read_verifier_metadata_covers_hash_chain_fk_and_c4_invariants() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)
    for name, specific in VERIFIER_OBLIGATIONS.items():
        rules = tables[name].verification_rules
        required_ids = {
            f"{name}_genesis_binding",
            f"{name}_canonical_row_hash",
            f"{name}_foreign_keys",
            *(f"{name}_{item.lower()}" for item in specific),
        }
        if name in CHAIN_SPECS:
            required_ids.update({f"{name}_ordered_chain", f"{name}_terminal_head"})
        assert {rule.rule_id for rule in rules} == required_ids, name
        by_id = {rule.rule_id: rule for rule in rules}
        assert _enum_value(by_id[f"{name}_genesis_binding"].kind) == "GENESIS_BINDING"
        assert _enum_value(by_id[f"{name}_canonical_row_hash"].kind) == "ROW_HASH"
        assert _enum_value(by_id[f"{name}_foreign_keys"].kind) == "FOREIGN_KEY"
        if name == "global_oos_exposure_consumptions":
            attempt_binding = by_id["global_oos_exposure_consumptions_require_c7_attempt_binding"]
            assert _enum_value(attempt_binding.kind) == "ATTEMPT_BINDING"
        if name in CHAIN_SPECS:
            assert _enum_value(by_id[f"{name}_ordered_chain"].kind) == "ORDERED_CHAIN"
            assert _enum_value(by_id[f"{name}_terminal_head"].kind) == "TERMINAL_HEAD"
        assert all(not callable(rule) for rule in rules)


def test_every_c4_read_rule_binds_one_exact_structural_or_named_verifier() -> None:
    tables = _table_map(_schema_module().C4_SCHEMA_COMPONENT)

    generic_kinds = {
        "genesis_binding": "GENESIS_FOREIGN_KEY",
        "canonical_row_hash": "ROW_HASH_SPEC",
        "foreign_keys": "ALL_FOREIGN_KEYS",
    }
    for table_name, table in tables.items():
        rules = {rule.rule_id: rule for rule in table.verification_rules}
        for suffix, expected_kind in generic_kinds.items():
            source = rules[f"{table_name}_{suffix}"].structural_source
            assert source is not None
            assert _enum_value(source.kind) == expected_kind

    for table_name in CHAIN_SPECS:
        rules = {rule.rule_id: rule for rule in tables[table_name].verification_rules}
        assert _enum_value(rules[f"{table_name}_ordered_chain"].structural_source.kind) == "CHAIN_SPEC"
        assert _enum_value(rules[f"{table_name}_terminal_head"].structural_source.kind) == "TERMINAL_HEAD"

    expected_sources = {
        "research_question_aliases_global_alias_immutability": (
            "UNIQUE_INDEX",
            "unique_research_question_aliases_alias",
            (),
            None,
            (),
        ),
        "research_question_aliases_question_reference": (
            "EXACT_FOREIGN_KEY",
            None,
            ("research_question_id",),
            "research_questions",
            ("research_question_id",),
        ),
        "strategy_lineage_roots_source_manifest_single_root": (
            "UNIQUE_INDEX",
            "unique_strategy_lineage_roots_manifest",
            (),
            None,
            (),
        ),
        "strategy_lineage_nodes_parent_root_inheritance": (
            "EXACT_FOREIGN_KEY",
            None,
            ("parent_strategy_lineage_node_id", "strategy_lineage_root_id"),
            "strategy_lineage_nodes",
            ("strategy_lineage_node_id", "strategy_lineage_root_id"),
        ),
        "strategy_config_lineage_bindings_config_single_root": (
            "PRIMARY_KEY",
            None,
            (),
            None,
            (),
        ),
        "strategy_config_lineage_bindings_node_root_match": (
            "EXACT_FOREIGN_KEY",
            None,
            ("strategy_lineage_node_id", "strategy_lineage_root_id"),
            "strategy_lineage_nodes",
            ("strategy_lineage_node_id", "strategy_lineage_root_id"),
        ),
        "research_authority_events_closed_action_enum": (
            "CHECK_CONSTRAINT",
            "research_authority_events_action_domain",
            (),
            None,
            (),
        ),
        "research_authority_events_complete_chain": ("CHAIN_SPEC", None, (), None, ()),
        "selection_scopes_mandate_single_scope": (
            "UNIQUE_INDEX",
            "unique_selection_scopes_mandate",
            (),
            None,
            (),
        ),
        "selection_scope_aliases_global_alias_immutability": (
            "UNIQUE_INDEX",
            "unique_selection_scope_aliases_alias",
            (),
            None,
            (),
        ),
        "selection_scope_aliases_scope_reference": (
            "EXACT_FOREIGN_KEY",
            None,
            ("selection_scope_id",),
            "selection_scopes",
            ("selection_scope_id",),
        ),
        "selection_scope_publishable_decisions_one_slot_per_scope": (
            "PRIMARY_KEY",
            None,
            (),
            None,
            (),
        ),
        "selection_scope_publishable_decisions_one_scope_per_slot_decision": (
            "UNIQUE_INDEX",
            "unique_selection_scope_publishable_decisions_decision",
            (),
            None,
            (),
        ),
        "selection_scope_outcomes_one_terminal_outcome_per_scope": (
            "PRIMARY_KEY",
            None,
            (),
            None,
            (),
        ),
        "selection_scope_outcomes_outcome_branch": (
            "CHECK_CONSTRAINT",
            "selection_scope_outcomes_branch",
            (),
            None,
            (),
        ),
        "research_programs_one_scope_question_root_tuple": (
            "UNIQUE_INDEX",
            "unique_research_programs_scope_question_root",
            (),
            None,
            (),
        ),
        "research_programs_question_hash_provenance": (
            "EXACT_FOREIGN_KEY",
            None,
            ("research_question_id", "question_hash"),
            "research_questions",
            ("research_question_id", "question_hash"),
        ),
        "research_programs_root_manifest_provenance": (
            "EXACT_FOREIGN_KEY",
            None,
            ("strategy_lineage_root_id", "source_manifest_hash"),
            "strategy_lineage_roots",
            ("strategy_lineage_root_id", "source_manifest_hash"),
        ),
        "selection_decisions_scope_monotone_decision_chain": ("CHAIN_SPEC", None, (), None, ()),
        "selection_decisions_unique_family_id": (
            "UNIQUE_INDEX",
            "unique_selection_decisions_family",
            (),
            None,
            (),
        ),
        "global_oos_exposures_scope_monotone_exposure_chain": ("CHAIN_SPEC", None, (), None, ()),
        "global_oos_exposures_decision_scope_family_match": (
            "EXACT_FOREIGN_KEY",
            None,
            ("selection_scope_id", "selection_decision_id", "family_id"),
            "selection_decisions",
            ("selection_scope_id", "selection_decision_id", "family_id"),
        ),
        "global_oos_exposure_consumptions_one_shot_consumption": (
            "PRIMARY_KEY",
            None,
            (),
            None,
            (),
        ),
        "global_oos_exposure_consumptions_exact_exposure_decision_family_tuple": (
            "EXACT_FOREIGN_KEY",
            None,
            ("oos_exposure_id", "selection_decision_id", "family_id"),
            "global_oos_exposures",
            ("oos_exposure_id", "selection_decision_id", "family_id"),
        ),
        "global_oos_exposure_consumptions_unique_access_attempt": (
            "UNIQUE_INDEX",
            "unique_global_oos_exposure_consumptions_access_attempt",
            (),
            None,
            (),
        ),
    }
    all_rules = {
        rule.rule_id: rule
        for table in tables.values()
        for rule in table.verification_rules
    }
    for rule_id, expected in expected_sources.items():
        source = all_rules[rule_id].structural_source
        assert source is not None, rule_id
        actual = (
            _enum_value(source.kind),
            source.source_id,
            source.columns,
            source.referenced_table,
            source.referenced_columns,
        )
        assert actual == expected, rule_id

    trigger_sources = {
        "research_questions_canonical_question_hash": "research_questions_canonical_payload_hash_match",
        "strategy_lineage_roots_canonical_source_manifest_hash": (
            "strategy_lineage_roots_canonical_payload_hash_match"
        ),
        "strategy_lineage_nodes_node_manifest_hash": "strategy_lineage_nodes_canonical_payload_hash_match",
        "selection_scopes_canonical_mandate_hash": "selection_scopes_canonical_payload_hash_match",
        "research_authority_events_action_subject_match": (
            "research_authority_events_closed_action_subject_match"
        ),
        "selection_scope_publishable_decisions_publishable_decision_match": (
            "selection_scope_publishable_decisions_publishable_decision_only"
        ),
        "selection_scope_publishable_decisions_outcome_not_already_terminal": (
            "selection_scope_publishable_decisions_scope_outcome_absent"
        ),
        "selection_scope_outcomes_winner_slot_decision_family_match": (
            "selection_scope_outcomes_winner_matches_publishable_slot"
        ),
        "global_oos_exposures_no_overlapping_scope_interval": (
            "global_oos_exposures_no_overlapping_scope_interval"
        ),
        "global_oos_exposure_consumptions_expected_exposure_version_match": (
            "global_oos_exposure_consumptions_expected_exposure_version_match"
        ),
        "global_oos_exposure_consumptions_diagnostic_scope_outcome": (
            "global_oos_exposure_consumptions_diagnostic_requires_scope_outcome"
        ),
        **{
            f"{table_name}_authority_event_binding": f"{table_name}_authority_event_match"
            for table_name in set(TABLE_ORDER) - {"research_authority_events"}
        },
    }
    assert len(trigger_sources) == 24
    for rule_id, trigger_name in trigger_sources.items():
        source = all_rules[rule_id].structural_source
        assert source is not None, rule_id
        assert _enum_value(source.kind) == "TRIGGER_POLICY"
        assert source.source_id == trigger_name

    delegated = {
        "global_oos_exposures_global_exposure_fingerprint": (
            C6_EXTENSION,
            (),
            ("VERIFY_EXPOSURE_FINGERPRINT",),
        ),
        "global_oos_exposures_require_c6_embedded_refs": (
            C6_EXTENSION,
            (),
            (
                "VERIFY_GLOBAL_OOS_EXPOSURE_IDENTITY",
                "VERIFY_OBSERVED_PIT_EXPOSURE_BINDING",
                "VERIFY_EXPOSURE_FINGERPRINT",
            ),
        ),
        "global_oos_exposure_consumptions_require_c7_attempt_binding": (
            C7_EXTENSION,
            (
                "global_oos_consumption_started_attempt_exact_tuple",
                "global_oos_consumption_attempt_authority_binding",
            ),
            (),
        ),
    }
    for rule_id, expected in delegated.items():
        source = all_rules[rule_id].structural_source
        assert source is not None and _enum_value(source.kind) == "DELEGATED_EXTENSION"
        assert (source.source_id, source.required_rule_ids, source.required_verifier_codes) == expected

    generic_ids = {
        f"{table_name}_{suffix}"
        for table_name in tables
        for suffix in generic_kinds
    }
    chain_ids = {
        f"{table_name}_{suffix}"
        for table_name in CHAIN_SPECS
        for suffix in ("ordered_chain", "terminal_head")
    }
    structural_ids = (
        generic_ids
        | chain_ids
        | set(expected_sources)
        | set(trigger_sources)
        | set(delegated)
    )
    assert len(structural_ids) == 100
    assert len(all_rules) == 120
    for rule_id, rule in all_rules.items():
        assert (rule.structural_source is None) != (rule.verifier_code is None), rule_id
        if rule_id in structural_ids:
            assert rule.structural_source is not None and rule.verifier_code is None
        else:
            assert rule.structural_source is None
            if rule_id.endswith("_grant_required"):
                assert rule.verifier_code == "c4.verify_subject_grant_required_v1"
            else:
                assert rule.verifier_code == f"c4.verify_{rule_id}_v1"


def test_c4_spec_is_typed_ast_only_with_no_sql_renderer_ddl_or_migration_surface() -> None:
    module = _schema_module()
    public_names = tuple(name.lower() for name in vars(module) if not name.startswith("__"))
    for forbidden in ("render_sql", "render_ddl", "activate_family_schema", "migrate_family_schema"):
        assert forbidden not in public_names

    source = inspect.getsource(module)
    for forbidden_source in (
        "CREATE TABLE",
        "CREATE TRIGGER",
        "CREATE INDEX",
        "executescript(",
        "sqlite3.connect(",
    ):
        assert forbidden_source not in source

    tables = _table_map(module.C4_SCHEMA_COMPONENT)
    for table in tables.values():
        for node in (*table.checks, *table.triggers, *table.verification_rules):
            assert not hasattr(node, "raw_sql")
            assert not hasattr(node, "sql_text")
