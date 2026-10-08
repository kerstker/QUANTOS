"""Executable coverage and fail-closed semantics for every C8 named verifier."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
from typing import Any

import pytest

from quantos.contracts import canonical as _canonical
from quantos.contracts.research import (
    DataSnoopingAssessment,
    DataSnoopingDeclaration,
    DataSnoopingGridAssessment,
    FamilyFreezeReceipt,
    PeriodKey,
)

from services.research.experiment_registry.family_schema_c8 import C8_SCHEMA_COMPONENT
from services.research.experiment_registry import family_schema_named_verifiers_c8 as _subject
from services.research.experiment_registry.family_schema_named_verifiers_c8 import (
    C8_NAMED_VERIFIER_IMPLEMENTATIONS,
    verify_c8_named,
)
from services.research.experiment_registry.family_schema_verification_view import (
    FrozenFamilySchemaVerificationView,
)
from services.validation.reality_check import compute_reality_check_and_spa


def _h(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return _canonical.canonical_json_bytes(value).decode("utf-8")


def _series_payload(
    values: tuple[float, ...],
    period_keys: tuple[PeriodKey, ...],
    *,
    cost_contract_hash: str,
    cost_model_input_hash: str,
) -> dict[str, object]:
    periods = [key.model_dump(mode="python") for key in period_keys]
    gross = [_canonical.float64_hex(value) for value in values]
    costs = [_canonical.float64_hex(0.0) for _ in values]
    net = list(gross)
    turnovers = [_canonical.float64_hex(0.0) for _ in values]
    payload: dict[str, object] = {
        "ordered_period_keys": periods,
        "gross_returns": gross,
        "costs": costs,
        "net_returns": net,
        "turnovers": turnovers,
        "ordered_period_keys_hash": _canonical.canonical_sha256(periods),
        "gross_returns_hash": _canonical.canonical_sha256(gross),
        "costs_hash": _canonical.canonical_sha256(costs),
        "net_returns_hash": _canonical.canonical_sha256(net),
        "turnovers_hash": _canonical.canonical_sha256(turnovers),
        "cost_contract_hash": cost_contract_hash,
        "cost_model_input_hash": cost_model_input_hash,
    }
    payload["series_hash"] = _canonical.canonical_sha256(payload)
    return payload


def _declaration_row(contract: DataSnoopingDeclaration) -> dict[str, object]:
    row = contract.model_dump(mode="python")
    for field in (
        "ordered_research_program_ids",
        "ordered_research_question_ids",
        "ordered_strategy_lineage_root_ids",
        "block_length_grid",
        "replication_schedule",
        "allowed_search_kinds",
        "generator_seeds",
        "search_proposal_budgets",
    ):
        row[f"{field}_json"] = _json(row.pop(field))
    row["record_hash"] = row.pop("declaration_record_hash")
    return row


def _freeze_row(contract: FamilyFreezeReceipt) -> dict[str, object]:
    row = contract.model_dump(mode="python")
    for field in (
        "ordered_search_ids",
        "ordered_search_terminal_hashes",
        "ordered_emission_seal_hashes",
        "ordered_candidate_chain_heads",
    ):
        row[f"{field}_json"] = _json(row.pop(field))
    row["record_hash"] = row.pop("freeze_record_hash")
    return row


def _grid_row(contract: DataSnoopingGridAssessment) -> dict[str, object]:
    row = contract.model_dump(mode="python")
    reality = row.pop("reality_check_result")
    spa = row.pop("spa_result")
    row["reality_check_payload_json"] = None if reality is None else _json(reality)
    row["spa_payload_json"] = None if spa is None else _json(spa)
    row["failure_reasons_json"] = _json(row.pop("failure_reasons"))
    row["prev_record_hash"] = row.pop("previous_grid_assessment_hash")
    row["record_hash"] = row.pop("grid_assessment_record_hash")
    return row


def _assessment_row(contract: DataSnoopingAssessment) -> dict[str, object]:
    row = contract.model_dump(mode="python")
    row["ordered_latest_grid_assessment_hashes_json"] = _json(row.pop("ordered_latest_grid_assessment_hashes"))
    row["failure_reasons_json"] = _json(row.pop("failure_reasons"))
    row["record_hash"] = row.pop("assessment_record_hash")
    return row


@pytest.fixture(scope="module")
def coherent_tables() -> dict[str, list[dict[str, object]]]:
    genesis = _h("genesis")
    authority = "authority-1"
    family = "family-1"
    scope = "scope-1"
    decision = "decision-1"
    declaration_id = "declaration-1"
    program_ids = ("program-a", "program-b")
    question_ids = ("question-a", "question-b")
    root_ids = ("root-a", "root-b")
    search_ids = ("search-a", "search-b")
    config_hashes = tuple(sorted((_h("config-a"), _h("config-b"))))
    receipt_hashes = (_h("receipt-a"), _h("receipt-b"))
    terminal_hashes = (_h("terminal-a"), _h("terminal-b"))
    seal_hashes = (_h("seal-a"), _h("seal-b"))
    chain_heads = (_h("candidate-head-a"), _h("candidate-head-b"))
    predecessor = _h("authority-predecessor")
    decision_event_hash = _h("authority-decision-event")
    exposure_fingerprint = _h("oos-exposure-fingerprint")
    search_content_hash = _h("search-observed-content")
    oos_content_hash = _h("oos-observed-content")
    search_contract_hash = _h("search-data-contract")
    cost_contract_hash = _h("cost-contract")
    cost_input_hash = _h("cost-input")
    oos_exposure_id = "oos-exposure-1"
    non_overlap_proof_hash = _canonical.canonical_sha256(
        {
            "proof_version": "SEARCH_OOS_NON_OVERLAP_V1",
            "search_data_contract_hash": search_contract_hash,
            "search_observed_pit_handle_id": "search-handle",
            "search_observed_pit_content_hash": search_content_hash,
            "oos_exposure_id": oos_exposure_id,
            "oos_exposure_fingerprint": exposure_fingerprint,
            "oos_observed_pit_handle_id": "oos-handle",
            "oos_observed_pit_content_hash": oos_content_hash,
            "purge_periods": 1,
            "embargo_periods": 1,
        }
    )

    declaration_payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_DECLARATION_V1",
        "authority_instance_id": authority,
        "genesis_record_hash": genesis,
        "declaration_id": declaration_id,
        "family_id": family,
        "selection_scope_id": scope,
        "deployment_mandate_hash": _h("mandate"),
        "selection_decision_id": decision,
        "selection_scope_predecessor_head": predecessor,
        "decision_class": "PUBLISHABLE",
        "ordered_research_program_ids": program_ids,
        "ordered_research_program_ids_hash": _canonical.canonical_sha256(program_ids),
        "ordered_research_question_ids": question_ids,
        "ordered_research_question_ids_hash": _canonical.canonical_sha256(question_ids),
        "ordered_strategy_lineage_root_ids": root_ids,
        "ordered_strategy_lineage_root_ids_hash": _canonical.canonical_sha256(root_ids),
        "objective_id": "MEAN_NET_RETURN_DIFFERENTIAL",
        "winner_selection_rule_id": "MAX_MEAN_NET_RETURN_DIFFERENTIAL_THEN_CONFIG_HASH_V1",
        "search_data_contract_hash": search_contract_hash,
        "oos_exposure_id": oos_exposure_id,
        "oos_release_policy_id": "RELEASE_AFTER_FREEZE_AND_CONSUMPTION_V1",
        "oos_access_policy_id": "ONE_SHOT_NO_RETRY_V1",
        "non_overlap_proof_hash": non_overlap_proof_hash,
        "universe_id": "universe-1",
        "universe_hash": _h("universe"),
        "ordered_period_calendar_hash": _h("calendar"),
        "label_horizon": "P1D",
        "purge_periods": 1,
        "embargo_periods": 1,
        "return_currency": "USD",
        "return_scale": "DECIMAL_RETURN_V1",
        "compounding_convention": "GEOMETRIC_V1",
        "cost_model_id": "cost-model-1",
        "cost_contract_hash": cost_contract_hash,
        "cost_parameters_hash": _h("cost-parameters"),
        "benchmark_id": "benchmark-1",
        "benchmark_contract_hash": _h("benchmark-contract"),
        "alpha_level": _canonical.float64_hex(0.05),
        "canonical_json_version": "QUANTOS_CANONICAL_JSON_V1",
        "assessment_algorithm_version": "WHITE_RC_HANSEN_SPA_FAMILY_V1",
        "reality_check_algorithm_version": "WHITE_REALITY_CHECK_V1",
        "spa_algorithm_version": "HANSEN_SPA_V1",
        "bootstrap_algorithm_version": "QUANTOS_STATIONARY_BOOTSTRAP_V1",
        "prng_algorithm_version": "SPLITMIX64_V1",
        "block_length_policy_id": "QUANTOS_BLOCK_LENGTH_GRID_V1",
        "block_length_grid": (2,),
        "replication_schedule": (10_000,),
        "bootstrap_seed": 7,
        "observed_pit_authority_id": "pit-authority",
        "observed_pit_handle_id": "search-handle",
        "observed_pit_content_hash": search_content_hash,
        "knowledge_time_policy_id": "PIT_V1",
        "knowledge_time_policy_version": "1",
        "observed_pit_authority_head": _h("search-pit-head"),
        "code_manifest_policy_id": "CODE_MANIFEST_V1",
        "code_manifest_policy_version": "1",
        "code_manifest_policy_hash": _h("code-policy"),
        "code_manifest_evidence_hash": _h("code-evidence"),
        "code_manifest_authority_id": "code-authority",
        "code_manifest_authority_head": _h("code-head"),
        "dlp_policy_id": "DLP_V1",
        "dlp_policy_version": "1",
        "dlp_policy_hash": _h("dlp-policy"),
        "scanner_implementation": "quantos-dlp",
        "scanner_version": "1",
        "restricted_envelope_authority_id": "restricted-authority",
        "quarantine_policy_id": "QUARANTINE_V1",
        "public_artifact_scan_policy_id": "PUBLIC_SCAN_V1",
        "allowed_search_kinds": ("EVOLUTIONARY", "MCTS"),
        "generator_seeds": (11, 22),
        "search_proposal_budgets": (5, 7),
        "required_search_count": 2,
        "maximum_search_count": 2,
        "stopping_rule_hash": _h("stopping-rule"),
        "external_ingestion_deadline_us": 180,
        "search_attachment_rule_id": "DECLARED_ORDINAL_V1",
        "trial_ledger_authority_id": "trial-ledger",
        "raw_proposal_count_definition": "EVERY_REGISTERED_OPAQUE_ENVELOPE_V1",
        "unique_performance_hypothesis_count_definition": "DISTINCT_ACCEPTED_CONFIG_HASH_V1",
        "global_unique_config_count_definition": "TRIAL_LEDGER_DISTINCT_CONFIG_AT_HISTORY_HEAD_V1",
        "performance_access_attempt_count_definition": "DISTINCT_COMMITTED_STARTED_ATTEMPT_V1",
        "declared_at_us": 100,
    }
    declaration_payload["declaration_record_hash"] = _canonical.canonical_sha256(declaration_payload)
    declaration = DataSnoopingDeclaration(**declaration_payload)
    declaration_row = _declaration_row(declaration)

    searches = [
        {
            "authority_instance_id": authority,
            "family_id": family,
            "declaration_id": declaration_id,
            "declaration_record_hash": declaration.declaration_record_hash,
            "search_id": search_id,
            "research_program_id": program_ids[index],
            "search_kind": declaration.allowed_search_kinds[index],
            "generator_seed": declaration.generator_seeds[index],
            "proposal_budget": declaration.search_proposal_budgets[index],
            "stopping_rule_hash": declaration.stopping_rule_hash,
            "external_ingestion_deadline_us": declaration.external_ingestion_deadline_us,
            "linked_at_us": 110 + index,
            "link_ordinal": index + 1,
            "record_hash": _h(f"search-row-{index}"),
        }
        for index, search_id in enumerate(search_ids)
    ]
    proposal_registrations = [
        {
            "authority_instance_id": authority,
            "family_id": family,
            "declaration_id": declaration_id,
            "declaration_record_hash": declaration.declaration_record_hash,
            "proposal_id": _h(f"proposal-{index}"),
            "search_id": search_ids[index],
            "emission_ordinal": index + 1,
            "proposal_branch": "CLEAN",
            "dlp_policy_id": declaration.dlp_policy_id,
            "dlp_policy_version": declaration.dlp_policy_version,
            "scanner_implementation": declaration.scanner_implementation,
            "scanner_version": declaration.scanner_version,
            "registered_at_us": 120 + index,
            "record_hash": _h(f"proposal-registration-row-{index}"),
        }
        for index in range(2)
    ]
    proposals = [
        {
            "authority_instance_id": authority,
            "family_id": family,
            "proposal_id": proposal_registrations[index]["proposal_id"],
            "search_id": search_ids[index],
            "emission_ordinal": index + 1,
            "registration_record_hash": proposal_registrations[index]["record_hash"],
            "parse_status": "ACCEPTED",
            "canonical_proposal_hash": _h(f"canonical-proposal-{index}"),
            "config_hash": config_hashes[index],
            "alpha_id": f"alpha-{index}",
            "parsed_at_us": 130 + index,
            "record_hash": _h(f"proposal-result-row-{index}"),
        }
        for index in range(2)
    ]
    hypotheses = [
        {
            "authority_instance_id": authority,
            "family_id": family,
            "first_search_id": search_ids[index],
            "first_proposal_id": proposals[index]["proposal_id"],
            "config_hash": config_hashes[index],
            "canonical_hypothesis_hash": proposals[index]["canonical_proposal_hash"],
            "alpha_id": proposals[index]["alpha_id"],
            "record_hash": _h(f"hypothesis-row-{index}"),
        }
        for index in range(2)
    ]
    search_events = [
        {
            "event_seq": index + 1,
            "search_id": search_ids[index],
            "status": "COMPLETED",
            "recorded_at": 140 + index,
            "event_hash": terminal_hashes[index],
        }
        for index in range(2)
    ]
    seals = [
        {
            "authority_instance_id": authority,
            "family_id": family,
            "search_id": search_ids[index],
            "candidate_chain_head": chain_heads[index],
            "sealed_at_us": 150 + index,
            "record_hash": seal_hashes[index],
        }
        for index in range(2)
    ]

    freeze_payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_FAMILY_FREEZE_V1",
        "authority_instance_id": authority,
        "genesis_record_hash": genesis,
        "freeze_id": "freeze-1",
        "family_id": family,
        "declaration_id": declaration_id,
        "declaration_record_hash": declaration.declaration_record_hash,
        "search_count": 2,
        "ordered_search_ids": search_ids,
        "ordered_search_ids_hash": _canonical.canonical_sha256(search_ids),
        "ordered_search_terminal_hashes": terminal_hashes,
        "ordered_search_terminal_hashes_hash": _canonical.canonical_sha256(terminal_hashes),
        "ordered_emission_seal_hashes": seal_hashes,
        "ordered_emission_seal_hashes_hash": _canonical.canonical_sha256(seal_hashes),
        "ordered_candidate_chain_heads": chain_heads,
        "ordered_candidate_chain_heads_hash": _canonical.canonical_sha256(chain_heads),
        "raw_proposal_count": 2,
        "unique_performance_hypothesis_count": 2,
        "frozen_config_set_hash": _canonical.canonical_sha256(config_hashes),
        "observed_pit_content_hash": declaration.observed_pit_content_hash,
        "code_manifest_evidence_hash": declaration.code_manifest_evidence_hash,
        "dlp_policy_hash": declaration.dlp_policy_hash,
        "stopping_rule_hash": declaration.stopping_rule_hash,
        "frozen_at_us": 200,
    }
    freeze_payload["freeze_record_hash"] = _canonical.canonical_sha256(freeze_payload)
    freeze = FamilyFreezeReceipt(**freeze_payload)
    freeze_row = _freeze_row(freeze)

    period_keys = tuple(
        PeriodKey(
            event_time=1_700_000_000_000_000 + index * 86_400_000_000,
            frequency_id="UTC_DAILY",
            label_horizon="P1D",
        )
        for index in range(24)
    )
    cycle = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0)
    benchmark = tuple(0.0005 + 0.0002 * cycle[index % len(cycle)] for index in range(24))
    candidate_returns = (
        tuple(0.003 + 0.0010 * cycle[index % len(cycle)] for index in range(24)),
        tuple(0.001 + 0.0015 * cycle[(index + 3) % len(cycle)] for index in range(24)),
    )
    candidate_payloads = tuple(
        _series_payload(
            values,
            period_keys,
            cost_contract_hash=cost_contract_hash,
            cost_model_input_hash=cost_input_hash,
        )
        for values in candidate_returns
    )
    benchmark_payload = _series_payload(
        benchmark,
        period_keys,
        cost_contract_hash=cost_contract_hash,
        cost_model_input_hash=cost_input_hash,
    )
    receipts = [
        {
            "authority_instance_id": authority,
            "family_id": family,
            "evaluation_batch_id": "batch-1",
            "evaluation_receipt_hash": receipt_hashes[index],
            "config_hash": config_hashes[index],
            "series_payload_json": _json(candidate_payloads[index]),
            "ordered_period_keys_hash": candidate_payloads[index]["ordered_period_keys_hash"],
            "net_returns_hash": candidate_payloads[index]["net_returns_hash"],
            "series_hash": candidate_payloads[index]["series_hash"],
            "committed_at_us": 240 + index,
            "record_hash": _h(f"receipt-row-{index}"),
        }
        for index in range(2)
    ]
    benchmark_receipt_hash = _h("benchmark-receipt")
    benchmark_row = {
        "authority_instance_id": authority,
        "family_id": family,
        "evaluation_batch_id": "batch-1",
        "benchmark_receipt_hash": benchmark_receipt_hash,
        "series_payload_json": _json(benchmark_payload),
        "ordered_period_keys_hash": benchmark_payload["ordered_period_keys_hash"],
        "net_returns_hash": benchmark_payload["net_returns_hash"],
        "series_hash": benchmark_payload["series_hash"],
        "committed_at_us": 242,
        "record_hash": _h("benchmark-row"),
    }
    closure_hash = _h("closure-manifest")
    closure_row = {
        "authority_instance_id": authority,
        "family_id": family,
        "declaration_id": declaration_id,
        "declaration_record_hash": declaration.declaration_record_hash,
        "freeze_id": freeze.freeze_id,
        "freeze_record_hash": freeze.freeze_record_hash,
        "closure_status": "COMPLETE",
        "selection_scope_id": scope,
        "selection_decision_id": decision,
        "selection_scope_history_head": decision_event_hash,
        "selection_scope_outcome_record_hash": None,
        "ordered_config_hashes_json": _json(config_hashes),
        "ordered_evaluation_receipt_hashes_json": _json(receipt_hashes),
        "evaluation_batch_id": "batch-1",
        "benchmark_receipt_hash": benchmark_receipt_hash,
        "closed_at_us": 300,
        "record_hash": closure_hash,
    }
    reality, spa = compute_reality_check_and_spa(
        tuple(zip(config_hashes, candidate_returns, strict=True)),
        benchmark,
        period_keys,
        2,
        0.05,
        7,
        10_000,
        (10_000,),
    )
    primary = (reality.decision, spa.variants[1].decision)
    ambiguous = reality.tail_result.ambiguous or spa.variants[1].tail_result.ambiguous
    if ambiguous:
        grid_status = "AMBIGUOUS"
        grid_verdict = "MONTE_CARLO_UNRESOLVED"
    elif primary == ("REJECT", "REJECT"):
        grid_status = "RESOLVED"
        grid_verdict = "SUPPORTS_SUPERIORITY"
    elif primary == ("NON_REJECT", "NON_REJECT"):
        grid_status = "RESOLVED"
        grid_verdict = "DOES_NOT_SUPPORT_SUPERIORITY"
    else:
        grid_status = "RESOLVED"
        grid_verdict = "METHOD_DISAGREEMENT"
    grid_payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_GRID_ASSESSMENT_V1",
        "authority_instance_id": authority,
        "genesis_record_hash": genesis,
        "grid_assessment_id": "grid-1",
        "family_id": family,
        "closure_manifest_hash": closure_hash,
        "algorithm_version": "WHITE_RC_HANSEN_SPA_FAMILY_V1",
        "period_count": 24,
        "block_length": 2,
        "replication_count": 10_000,
        "schedule_ordinal": 1,
        "prefix_index_stream_hash": reality.prefix_hashes[-1].index_stream_sha256,
        "previous_grid_assessment_hash": genesis,
        "reality_check_result": reality,
        "reality_check_result_hash": _canonical.canonical_sha256(reality),
        "spa_result": spa,
        "spa_result_hash": _canonical.canonical_sha256(spa),
        "grid_point_status": grid_status,
        "grid_point_verdict": grid_verdict,
        "promotion_effect": "NONE",
        "failure_reasons": (),
        "assessed_at_us": 400,
    }
    grid_payload["grid_assessment_record_hash"] = _canonical.canonical_sha256(grid_payload)
    grid = DataSnoopingGridAssessment(**grid_payload)
    grid_row = _grid_row(grid)
    assessment_payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_ASSESSMENT_V1",
        "authority_instance_id": authority,
        "genesis_record_hash": genesis,
        "assessment_id": "assessment-1",
        "family_id": family,
        "closure_manifest_hash": closure_hash,
        "algorithm_version": "WHITE_RC_HANSEN_SPA_FAMILY_V1",
        "ordered_latest_grid_assessment_hashes": (grid.grid_assessment_record_hash,),
        "ordered_latest_grid_assessment_hashes_hash": _canonical.canonical_sha256((grid.grid_assessment_record_hash,)),
        "candidate_order_hash": _canonical.canonical_sha256(reality.candidate_order),
        "period_count": 24,
        "minimum_replication_count": 10_000,
        "assessment_status": "ASSESSED",
        "diagnostic_verdict": grid_verdict,
        "promotion_effect": "NONE",
        "failure_reasons": (),
        "concluded_at_us": 500,
    }
    assessment_payload["assessment_record_hash"] = _canonical.canonical_sha256(assessment_payload)
    assessment = DataSnoopingAssessment(**assessment_payload)
    assessment_row = _assessment_row(assessment)
    publication_row = {
        "authority_instance_id": authority,
        "family_id": family,
        "closure_manifest_hash": closure_hash,
        "family_assessment_conclusion_hash": assessment.assessment_record_hash,
        "ordered_grid_assessment_record_hashes_json": _json((grid.grid_assessment_record_hash,)),
        "ordered_grid_assessment_record_hashes_hash": _canonical.canonical_sha256((grid.grid_assessment_record_hash,)),
        "minimum_replication_count": 10_000,
        "assessment_status": "ASSESSED",
        "diagnostic_verdict": grid_verdict,
        "receipt_record_hash": _h("publication-row"),
    }

    return {
        "data_snooping_declarations": [declaration_row],
        "research_programs": [
            {
                "authority_instance_id": authority,
                "research_program_id": program_ids[index],
                "selection_scope_id": scope,
                "research_question_id": question_ids[index],
                "strategy_lineage_root_id": root_ids[index],
                "issued_at_us": 80 + index,
                "record_hash": _h(f"program-row-{index}"),
            }
            for index in range(2)
        ],
        "selection_decisions": [
            {
                "authority_instance_id": authority,
                "selection_scope_id": scope,
                "selection_decision_id": decision,
                "family_id": family,
                "authority_event_id": "decision-event",
                "issued_at_us": 90,
                "record_hash": _h("decision-row"),
            }
        ],
        "research_authority_events": [
            {
                "authority_instance_id": authority,
                "event_id": "predecessor-event",
                "event_ordinal": 1,
                "subject_id": "scope-creation",
                "subject_record_hash": _h("scope-row"),
                "prev_record_hash": genesis,
                "record_hash": predecessor,
            },
            {
                "authority_instance_id": authority,
                "event_id": "decision-event",
                "event_ordinal": 2,
                "subject_id": decision,
                "subject_record_hash": _h("decision-row"),
                "prev_record_hash": predecessor,
                "record_hash": decision_event_hash,
            },
        ],
        "global_oos_exposures": [
            {
                "authority_instance_id": authority,
                "oos_exposure_id": oos_exposure_id,
                "selection_scope_id": scope,
                "selection_decision_id": decision,
                "family_id": family,
                "exposure_fingerprint": exposure_fingerprint,
                "observed_pit_handle_id": "oos-handle",
                "observed_pit_content_hash": oos_content_hash,
                "record_hash": _h("exposure-row"),
            }
        ],
        "data_snooping_family_searches": searches,
        "data_snooping_proposal_registrations": proposal_registrations,
        "data_snooping_proposals": proposals,
        "data_snooping_hypotheses": hypotheses,
        "search_attempt_events": search_events,
        "data_snooping_search_emission_seals": seals,
        "data_snooping_family_freezes": [freeze_row],
        "data_snooping_candidate_dispositions": [
            {
                "authority_instance_id": authority,
                "family_id": family,
                "proposal_id": proposals[index]["proposal_id"],
                "recorded_at_us": 220 + index,
                "record_hash": _h(f"disposition-row-{index}"),
            }
            for index in range(2)
        ],
        "data_snooping_evaluation_batches": [
            {
                "authority_instance_id": authority,
                "family_id": family,
                "evaluation_batch_id": "batch-1",
                "committed_at_us": 235,
                "record_hash": _h("batch-row"),
            }
        ],
        "data_snooping_family_oos_evaluations": [
            {
                "authority_instance_id": authority,
                "family_id": family,
                "evaluation_batch_id": "batch-1",
                "config_hash": config_hashes[0],
                "linked_at_us": 238,
                "record_hash": _h("family-evaluation-row"),
            }
        ],
        "data_snooping_evaluation_receipts": receipts,
        "data_snooping_benchmark_returns": [benchmark_row],
        "data_snooping_family_closures": [closure_row],
        "data_snooping_grid_assessments": [grid_row],
        "data_snooping_assessments": [assessment_row],
        "artifact_publication_receipts": [publication_row],
    }


def _view(tables: dict[str, list[dict[str, object]]]) -> FrozenFamilySchemaVerificationView:
    return FrozenFamilySchemaVerificationView.from_rows(tables)


def _owner_for(
    item: object,
    tables: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    owner_table = getattr(item, "owner_table")
    return tables[owner_table][0]


def _declared_codes() -> tuple[str, ...]:
    return tuple(
        rule.verifier_code
        for table in C8_SCHEMA_COMPONENT.tables
        for rule in table.verification_rules
        if rule.verifier_code is not None
    ) + tuple(
        rule.verifier_code
        for extension in C8_SCHEMA_COMPONENT.extensions
        for rule in extension.verification_rules
        if rule.verifier_code is not None
    )


def test_c8_named_registry_exactly_covers_all_23_declarations() -> None:
    declared = _declared_codes()
    implementations = C8_NAMED_VERIFIER_IMPLEMENTATIONS

    assert len(declared) == 23
    assert type(implementations) is tuple
    assert len(implementations) == 23
    assert {item.verifier_code for item in implementations} == set(declared)
    assert len({item.verifier_code for item in implementations}) == 23
    for item in implementations:
        parameters = tuple(inspect.signature(item.implementation).parameters.values())
        assert tuple(parameter.name for parameter in parameters) == (
            "owner_row",
            "view",
            "providers",
        )
        assert all(parameter.default is inspect.Parameter.empty for parameter in parameters)


def test_c8_dispatcher_fails_closed_for_unknown_and_malformed_inputs() -> None:
    assert verify_c8_named("c8.verify_unknown_v1", {}, object()) is False
    assert verify_c8_named(None, {}, object()) is False


def test_every_c8_named_verifier_accepts_one_coherent_real_authority_view(
    coherent_tables: dict[str, list[dict[str, object]]],
) -> None:
    view = _view(coherent_tables)
    for item in C8_NAMED_VERIFIER_IMPLEMENTATIONS:
        assert verify_c8_named(
            item.verifier_code,
            _owner_for(item, coherent_tables),
            view,
            object(),
        ), item.verifier_code


def test_shared_c8_assessment_implementation_accepts_c9_publication_owner_and_rejects_tamper(
    coherent_tables: dict[str, list[dict[str, object]]],
) -> None:
    view = _view(coherent_tables)
    publication = coherent_tables["artifact_publication_receipts"][0]
    code = "c8.verify_family_assessment_v1"

    assert verify_c8_named(code, publication, view, None)
    tampered = dict(publication, diagnostic_verdict="DOES_NOT_SUPPORT_SUPERIORITY")
    assert verify_c8_named(code, tampered, view, None) is False


@pytest.mark.parametrize(
    ("code", "table", "field", "replacement"),
    (
        (
            "c8.verify_declaration_scope_history_predecessor_v1",
            "data_snooping_declarations",
            "selection_scope_predecessor_head",
            _h("wrong-predecessor"),
        ),
        (
            "c8.verify_declaration_ordered_sets_hashes_v1",
            "data_snooping_declarations",
            "ordered_research_program_ids_hash",
            _h("wrong-program-order"),
        ),
        (
            "c8.verify_declaration_search_oos_non_overlap_v1",
            "data_snooping_declarations",
            "non_overlap_proof_hash",
            _h("wrong-proof"),
        ),
        (
            "c8.verify_declaration_grid_schedule_v1",
            "data_snooping_declarations",
            "replication_schedule_json",
            "[10000,10000]",
        ),
        (
            "c8.verify_declaration_search_plan_v1",
            "data_snooping_declarations",
            "required_search_count",
            3,
        ),
        (
            "c8.verify_family_freeze_terminal_search_totality_v1",
            "data_snooping_family_freezes",
            "search_count",
            3,
        ),
        (
            "c8.verify_family_closure_scope_history_v1",
            "data_snooping_family_closures",
            "selection_scope_history_head",
            _h("wrong-history-tip"),
        ),
        (
            "c8.verify_family_assessment_v1",
            "data_snooping_assessments",
            "diagnostic_verdict",
            "DOES_NOT_SUPPORT_SUPERIORITY",
        ),
    ),
)
def test_single_field_count_order_and_branch_tampering_fails_closed(
    coherent_tables: dict[str, list[dict[str, object]]],
    code: str,
    table: str,
    field: str,
    replacement: object,
) -> None:
    tampered_tables = deepcopy(coherent_tables)
    tampered_tables[table][0][field] = replacement
    assert (
        verify_c8_named(
            code,
            tampered_tables[table][0],
            _view(tampered_tables),
            None,
        )
        is False
    )


def test_declaration_dlp_binding_reads_registration_stage_only(
    coherent_tables: dict[str, list[dict[str, object]]],
) -> None:
    code = "c8.verify_declaration_code_data_dlp_binding_v1"
    owner = coherent_tables["data_snooping_declarations"][0]
    tampered_tables = deepcopy(coherent_tables)
    tampered_tables["data_snooping_proposal_registrations"][0][
        "scanner_version"
    ] = "DIFFERENT"
    assert verify_c8_named(code, owner, _view(tampered_tables), None) is False


@pytest.mark.parametrize(
    ("table", "time_field"),
    (
        ("data_snooping_proposal_registrations", "registered_at_us"),
        ("data_snooping_proposals", "parsed_at_us"),
    ),
)
def test_declaration_precedence_covers_registration_and_terminal_result(
    coherent_tables: dict[str, list[dict[str, object]]],
    table: str,
    time_field: str,
) -> None:
    code = "c8.verify_declaration_precedes_family_activity_v1"
    owner = coherent_tables["data_snooping_declarations"][0]
    tampered_tables = deepcopy(coherent_tables)
    tampered_tables[table][0][time_field] = 100
    assert verify_c8_named(code, owner, _view(tampered_tables), None) is False


@pytest.mark.parametrize(
    ("code", "table", "time_field"),
    (
        (
            "c8.verify_family_search_freeze_closure_cutoff_v1",
            "data_snooping_family_searches",
            "linked_at_us",
        ),
        (
            "c8.verify_proposal_freeze_closure_cutoff_v1",
            "data_snooping_proposal_registrations",
            "registered_at_us",
        ),
        (
            "c8.verify_proposal_terminal_result_freeze_closure_cutoff_v1",
            "data_snooping_proposals",
            "parsed_at_us",
        ),
        (
            "c8.verify_emission_seal_freeze_closure_cutoff_v1",
            "data_snooping_search_emission_seals",
            "sealed_at_us",
        ),
        (
            "c8.verify_candidate_disposition_freeze_closure_cutoff_v1",
            "data_snooping_candidate_dispositions",
            "recorded_at_us",
        ),
        (
            "c8.verify_evaluation_batch_freeze_closure_cutoff_v1",
            "data_snooping_evaluation_batches",
            "committed_at_us",
        ),
        (
            "c8.verify_family_oos_evaluation_freeze_closure_cutoff_v1",
            "data_snooping_family_oos_evaluations",
            "linked_at_us",
        ),
        (
            "c8.verify_evaluation_receipt_freeze_closure_cutoff_v1",
            "data_snooping_evaluation_receipts",
            "committed_at_us",
        ),
        (
            "c8.verify_benchmark_return_freeze_closure_cutoff_v1",
            "data_snooping_benchmark_returns",
            "committed_at_us",
        ),
    ),
)
def test_each_timestamped_freeze_closure_cutoff_rejects_wrong_side_of_boundary(
    coherent_tables: dict[str, list[dict[str, object]]],
    code: str,
    table: str,
    time_field: str,
) -> None:
    tampered_tables = deepcopy(coherent_tables)
    before_freeze = table in {
        "data_snooping_family_searches",
        "data_snooping_proposal_registrations",
        "data_snooping_proposals",
        "data_snooping_search_emission_seals",
    }
    tampered_tables[table][0][time_field] = 210 if before_freeze else 190
    assert (
        verify_c8_named(
            code,
            tampered_tables[table][0],
            _view(tampered_tables),
            None,
        )
        is False
    )


def test_hypothesis_cutoff_resolves_its_first_proposal_time(
    coherent_tables: dict[str, list[dict[str, object]]],
) -> None:
    code = "c8.verify_hypothesis_freeze_closure_cutoff_v1"
    view = _view(coherent_tables)
    owner = coherent_tables["data_snooping_hypotheses"][0]
    assert verify_c8_named(code, owner, view, None)

    tampered_tables = deepcopy(coherent_tables)
    tampered_tables["data_snooping_proposals"][0]["parsed_at_us"] = 210
    assert verify_c8_named(code, owner, _view(tampered_tables), None) is False

    tampered_tables = deepcopy(coherent_tables)
    tampered_tables["data_snooping_proposals"][0]["parse_status"] = "INVALID"
    assert verify_c8_named(code, owner, _view(tampered_tables), None) is False


def test_grid_verifier_calls_canonical_kernel_and_rejects_statistical_input_tamper(
    coherent_tables: dict[str, list[dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    canonical_kernel = _subject.compute_reality_check_and_spa

    def recording_kernel(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return canonical_kernel(*args, **kwargs)

    monkeypatch.setattr(_subject, "compute_reality_check_and_spa", recording_kernel)
    code = "c8.verify_grid_assessment_result_payload_hashes_v1"
    grid = coherent_tables["data_snooping_grid_assessments"][0]
    assert verify_c8_named(code, grid, _view(coherent_tables), None)
    assert calls == 1

    tampered_tables = deepcopy(coherent_tables)
    receipt = tampered_tables["data_snooping_evaluation_receipts"][0]
    payload = _canonical.load_canonical_json_bytes(str(receipt["series_payload_json"]).encode("utf-8"))
    assert isinstance(payload, dict)
    net = list(payload["net_returns"])
    net[0] = _canonical.float64_hex(_canonical.parse_float64_hex(net[0]) + 0.25)
    payload["net_returns"] = net
    payload["net_returns_hash"] = _canonical.canonical_sha256(net)
    without_series_hash = {key: value for key, value in payload.items() if key != "series_hash"}
    payload["series_hash"] = _canonical.canonical_sha256(without_series_hash)
    receipt["series_payload_json"] = _json(payload)
    receipt["net_returns_hash"] = payload["net_returns_hash"]
    receipt["series_hash"] = payload["series_hash"]
    assert verify_c8_named(code, grid, _view(tampered_tables), None) is False
