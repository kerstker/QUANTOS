"""RED contracts for exact-v3 Pass C slice C8.

This file freezes only declaration/freeze/closure/assessment values owned by
C8.  Publication envelopes, evidence references, artifact scans, and
publication receipts belong to C9 and are deliberately absent.
"""

from __future__ import annotations

from math import sqrt
from typing import Any

import pytest
from pydantic import ValidationError

import quantos.contracts.research as research_contracts
from quantos.contracts.canonical import canonical_sha256, float64_hex
from services.validation.reality_check import compute_reality_check_and_spa


C6_DECLARATION_FIELDS = {
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
}

C6_CLOSURE_FIELDS = {
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
}

C8_CONTRACT_FIELDS = {
    "DataSnoopingDeclaration": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "declaration_id",
        "family_id",
        "selection_scope_id",
        "deployment_mandate_hash",
        "selection_decision_id",
        "selection_scope_predecessor_head",
        "decision_class",
        "ordered_research_program_ids",
        "ordered_research_program_ids_hash",
        "ordered_research_question_ids",
        "ordered_research_question_ids_hash",
        "ordered_strategy_lineage_root_ids",
        "ordered_strategy_lineage_root_ids_hash",
        "objective_id",
        "winner_selection_rule_id",
        "search_data_contract_hash",
        "oos_exposure_id",
        "oos_release_policy_id",
        "oos_access_policy_id",
        "non_overlap_proof_hash",
        "universe_id",
        "universe_hash",
        "ordered_period_calendar_hash",
        "label_horizon",
        "purge_periods",
        "embargo_periods",
        "return_currency",
        "return_scale",
        "compounding_convention",
        "cost_model_id",
        "cost_contract_hash",
        "cost_parameters_hash",
        "benchmark_id",
        "benchmark_contract_hash",
        "alpha_level",
        "canonical_json_version",
        "assessment_algorithm_version",
        "reality_check_algorithm_version",
        "spa_algorithm_version",
        "bootstrap_algorithm_version",
        "prng_algorithm_version",
        "block_length_policy_id",
        "block_length_grid",
        "replication_schedule",
        "bootstrap_seed",
        "dlp_policy_id",
        "dlp_policy_version",
        "dlp_policy_hash",
        "scanner_implementation",
        "scanner_version",
        "restricted_envelope_authority_id",
        "quarantine_policy_id",
        "public_artifact_scan_policy_id",
        "allowed_search_kinds",
        "generator_seeds",
        "search_proposal_budgets",
        "required_search_count",
        "maximum_search_count",
        "stopping_rule_hash",
        "external_ingestion_deadline_us",
        "search_attachment_rule_id",
        "trial_ledger_authority_id",
        "raw_proposal_count_definition",
        "unique_performance_hypothesis_count_definition",
        "global_unique_config_count_definition",
        "performance_access_attempt_count_definition",
        "declared_at_us",
        "declaration_record_hash",
        *C6_DECLARATION_FIELDS,
    },
    "FamilyFreezeReceipt": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "freeze_id",
        "family_id",
        "declaration_id",
        "declaration_record_hash",
        "search_count",
        "ordered_search_ids",
        "ordered_search_ids_hash",
        "ordered_search_terminal_hashes",
        "ordered_search_terminal_hashes_hash",
        "ordered_emission_seal_hashes",
        "ordered_emission_seal_hashes_hash",
        "ordered_candidate_chain_heads",
        "ordered_candidate_chain_heads_hash",
        "raw_proposal_count",
        "unique_performance_hypothesis_count",
        "frozen_config_set_hash",
        "observed_pit_content_hash",
        "code_manifest_evidence_hash",
        "dlp_policy_hash",
        "stopping_rule_hash",
        "frozen_at_us",
        "freeze_record_hash",
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
        "proof_hash",
    },
    "DataSnoopingFamilyManifest": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "closure_id",
        "family_id",
        "declaration_id",
        "declaration_record_hash",
        "freeze_id",
        "freeze_record_hash",
        "closure_status",
        "selection_scope_id",
        "selection_decision_id",
        "selection_scope_history_head",
        "selection_scope_outcome_record_hash",
        "ordered_search_ids_hash",
        "ordered_search_terminal_hashes_hash",
        "ordered_emission_seal_hashes_hash",
        "ordered_proposal_ids_hash",
        "ordered_disposition_record_hashes_hash",
        "ordered_config_hashes",
        "ordered_evaluation_receipt_hashes",
        "attempt_id",
        "terminal_attempt_event_id",
        "evaluation_batch_id",
        "evaluation_batch_receipt_hash",
        "oos_release_id",
        "oos_access_attempt_terminal_hash",
        "oos_release_record_hash",
        "raw_proposal_count",
        "unique_performance_hypothesis_count",
        "global_unique_config_count",
        "performance_access_attempt_count",
        "ordered_period_keys_hash",
        "candidate_series_set_hash",
        "benchmark_receipt_hash",
        "cost_model_input_set_hash",
        "winner_config_hash",
        "winner_evaluation_receipt_hash",
        "trial_ledger_proof",
        "trial_ledger_proof_hash",
        "failure_reasons",
        "closed_at_us",
        "closure_manifest_hash",
        "authority_head",
        *C6_CLOSURE_FIELDS,
    },
    "DataSnoopingGridAssessment": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "grid_assessment_id",
        "family_id",
        "closure_manifest_hash",
        "algorithm_version",
        "period_count",
        "block_length",
        "replication_count",
        "schedule_ordinal",
        "prefix_index_stream_hash",
        "previous_grid_assessment_hash",
        "reality_check_result",
        "reality_check_result_hash",
        "spa_result",
        "spa_result_hash",
        "grid_point_status",
        "grid_point_verdict",
        "promotion_effect",
        "failure_reasons",
        "assessed_at_us",
        "grid_assessment_record_hash",
    },
    "DataSnoopingAssessment": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "assessment_id",
        "family_id",
        "closure_manifest_hash",
        "algorithm_version",
        "ordered_latest_grid_assessment_hashes",
        "ordered_latest_grid_assessment_hashes_hash",
        "candidate_order_hash",
        "period_count",
        "minimum_replication_count",
        "assessment_status",
        "diagnostic_verdict",
        "promotion_effect",
        "failure_reasons",
        "concluded_at_us",
        "assessment_record_hash",
    },
}


def _contract(name: str) -> type[Any]:
    contract = getattr(research_contracts, name, None)
    assert contract is not None, f"Pass C C8 contract is missing: {name}"
    return contract


@pytest.mark.parametrize(("name", "fields"), C8_CONTRACT_FIELDS.items())
def test_c8_contracts_are_present_frozen_strict_and_exact(
    name: str,
    fields: set[str],
) -> None:
    contract = _contract(name)
    assert set(contract.model_fields) == fields
    assert contract.model_config["frozen"] is True
    assert contract.model_config["extra"] == "forbid"
    assert contract.model_config["strict"] is True


def _declaration_payload(**overrides: Any) -> dict[str, Any]:
    programs = ("program-1", "program-2")
    questions = ("question-1", "question-2")
    roots = ("root-1", "root-2")
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_DECLARATION_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": "0" * 64,
        "declaration_id": "declaration-1",
        "family_id": "family-1",
        "selection_scope_id": "scope-1",
        "deployment_mandate_hash": "1" * 64,
        "selection_decision_id": "decision-1",
        "selection_scope_predecessor_head": "2" * 64,
        "decision_class": "PUBLISHABLE",
        "ordered_research_program_ids": programs,
        "ordered_research_program_ids_hash": canonical_sha256(programs),
        "ordered_research_question_ids": questions,
        "ordered_research_question_ids_hash": canonical_sha256(questions),
        "ordered_strategy_lineage_root_ids": roots,
        "ordered_strategy_lineage_root_ids_hash": canonical_sha256(roots),
        "objective_id": "MEAN_NET_RETURN_DIFFERENTIAL",
        "winner_selection_rule_id": "MAX_MEAN_NET_RETURN_DIFFERENTIAL_THEN_CONFIG_HASH_V1",
        "search_data_contract_hash": "3" * 64,
        "oos_exposure_id": "exposure-1",
        "oos_release_policy_id": "RELEASE_AFTER_FREEZE_AND_CONSUMPTION_V1",
        "oos_access_policy_id": "ONE_SHOT_NO_RETRY_V1",
        "non_overlap_proof_hash": "4" * 64,
        "universe_id": "universe-1",
        "universe_hash": "5" * 64,
        "ordered_period_calendar_hash": "6" * 64,
        "label_horizon": "P1D",
        "purge_periods": 2,
        "embargo_periods": 1,
        "return_currency": "USD",
        "return_scale": "DECIMAL_RETURN_V1",
        "compounding_convention": "GEOMETRIC_V1",
        "cost_model_id": "cost-model-1",
        "cost_contract_hash": "7" * 64,
        "cost_parameters_hash": "8" * 64,
        "benchmark_id": "EQUAL_WEIGHT_UNIVERSE_V1",
        "benchmark_contract_hash": "9" * 64,
        "alpha_level": float64_hex(0.05),
        "canonical_json_version": "QUANTOS_CANONICAL_JSON_V1",
        "assessment_algorithm_version": "WHITE_RC_HANSEN_SPA_FAMILY_V1",
        "reality_check_algorithm_version": "WHITE_REALITY_CHECK_V1",
        "spa_algorithm_version": "HANSEN_SPA_V1",
        "bootstrap_algorithm_version": "QUANTOS_STATIONARY_BOOTSTRAP_V1",
        "prng_algorithm_version": "SPLITMIX64_V1",
        "block_length_policy_id": "QUANTOS_BLOCK_LENGTH_GRID_V1",
        "block_length_grid": (1, 2, 4),
        "replication_schedule": (99, 199),
        "bootstrap_seed": 7,
        "observed_pit_authority_id": "pit-authority-1",
        "observed_pit_handle_id": "pit-handle-1",
        "observed_pit_content_hash": "a" * 64,
        "knowledge_time_policy_id": "PIT_EOD_V1",
        "knowledge_time_policy_version": "1",
        "observed_pit_authority_head": "b" * 64,
        "code_manifest_policy_id": "CODE_MANIFEST_POLICY_V1",
        "code_manifest_policy_version": "1",
        "code_manifest_policy_hash": "c" * 64,
        "code_manifest_evidence_hash": "d" * 64,
        "code_manifest_authority_id": "code-authority-1",
        "code_manifest_authority_head": "e" * 64,
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "dlp_policy_hash": "f" * 64,
        "scanner_implementation": "FAIL_CLOSED_SCANNER",
        "scanner_version": "1",
        "restricted_envelope_authority_id": "restricted-envelope-authority-1",
        "quarantine_policy_id": "OPAQUE_PUBLIC_QUARANTINE_V1",
        "public_artifact_scan_policy_id": "PUBLIC_CORE_SCAN_POLICY_V1",
        "allowed_search_kinds": ("GP", "GUIDED"),
        "generator_seeds": (11, 13),
        "search_proposal_budgets": (20, 30),
        "required_search_count": 2,
        "maximum_search_count": 2,
        "stopping_rule_hash": "1" * 64,
        "external_ingestion_deadline_us": 1_700_100_000_000_000,
        "search_attachment_rule_id": "AUTHORITY_LINK_BEFORE_SEARCH_START_V1",
        "trial_ledger_authority_id": "trial-ledger-authority-1",
        "raw_proposal_count_definition": "EVERY_REGISTERED_OPAQUE_ENVELOPE_V1",
        "unique_performance_hypothesis_count_definition": "DISTINCT_ACCEPTED_CONFIG_HASH_V1",
        "global_unique_config_count_definition": "TRIAL_LEDGER_DISTINCT_CONFIG_AT_HISTORY_HEAD_V1",
        "performance_access_attempt_count_definition": "DISTINCT_COMMITTED_STARTED_ATTEMPT_V1",
        "declared_at_us": 1_700_000_000_000_000,
    }
    payload.update(overrides)
    payload["declaration_record_hash"] = canonical_sha256(payload)
    return payload


def _rehash(
    payload: dict[str, Any],
    hash_field: str,
    *,
    head_field: str | None = None,
) -> dict[str, Any]:
    result = dict(payload)
    result.pop(hash_field, None)
    if head_field is not None:
        result.pop(head_field, None)
    result[hash_field] = canonical_sha256(result)
    if head_field is not None:
        result[head_field] = result[hash_field]
    return result


def test_declaration_binds_complete_sorted_scope_grid_schedule_and_four_counters() -> None:
    declaration = _contract("DataSnoopingDeclaration")(**_declaration_payload())
    assert declaration.block_length_grid == (1, 2, 4)
    assert declaration.replication_schedule == (99, 199)
    assert declaration.declaration_record_hash == _declaration_payload()["declaration_record_hash"]


def test_declaration_seeds_match_the_governed_sqlite_integer_domain() -> None:
    maximum = (1 << 63) - 1
    payload = _rehash(
        _declaration_payload(
            bootstrap_seed=maximum,
            generator_seeds=(maximum - 1, maximum),
        ),
        "declaration_record_hash",
    )

    declaration = _contract("DataSnoopingDeclaration")(**payload)
    assert declaration.bootstrap_seed == maximum
    assert declaration.generator_seeds == (maximum - 1, maximum)


@pytest.mark.parametrize(
    "mutation",
    (
        {"ordered_research_program_ids": ("program-2", "program-1")},
        {"block_length_grid": (4, 2, 1)},
        {"replication_schedule": (99, 99)},
        {"required_search_count": 3},
        {"alpha_level": float64_hex(1.0)},
        {"assessment_algorithm_version": "UNDECLARED_ASSESSMENT_V1"},
        {"prng_algorithm_version": "PCG64_V1"},
        {"block_length_policy_id": "INTEGER_GRID_V1"},
        {"bootstrap_seed": 1 << 63},
        {"generator_seeds": (11, 1 << 63)},
        {"external_ingestion_deadline_us": 1_699_999_999_999_999},
        {"raw_proposal_count_definition": "UNDECLARED_COUNTER"},
        {"declaration_record_hash": "f" * 64},
    ),
)
def test_declaration_rejects_scope_schedule_counter_or_hash_drift(
    mutation: dict[str, Any],
) -> None:
    payload = _declaration_payload()
    payload.update(mutation)
    if "declaration_record_hash" not in mutation:
        payload = _rehash(payload, "declaration_record_hash")
    with pytest.raises(ValidationError):
        _contract("DataSnoopingDeclaration")(**payload)


def test_declaration_allows_an_explicit_diagnostic_only_family() -> None:
    declaration = _contract("DataSnoopingDeclaration")(**_declaration_payload(decision_class="DIAGNOSTIC_ONLY"))
    assert declaration.decision_class == "DIAGNOSTIC_ONLY"


def _freeze_payload(**overrides: Any) -> dict[str, Any]:
    search_ids = ("search-1", "search-2")
    terminals = ("1" * 64, "2" * 64)
    seals = ("3" * 64, "4" * 64)
    heads = ("5" * 64, "6" * 64)
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_FAMILY_FREEZE_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": "0" * 64,
        "freeze_id": "freeze-1",
        "family_id": "family-1",
        "declaration_id": "declaration-1",
        "declaration_record_hash": _declaration_payload()["declaration_record_hash"],
        "search_count": 2,
        "ordered_search_ids": search_ids,
        "ordered_search_ids_hash": canonical_sha256(search_ids),
        "ordered_search_terminal_hashes": terminals,
        "ordered_search_terminal_hashes_hash": canonical_sha256(terminals),
        "ordered_emission_seal_hashes": seals,
        "ordered_emission_seal_hashes_hash": canonical_sha256(seals),
        "ordered_candidate_chain_heads": heads,
        "ordered_candidate_chain_heads_hash": canonical_sha256(heads),
        "raw_proposal_count": 20,
        "unique_performance_hypothesis_count": 2,
        "frozen_config_set_hash": "7" * 64,
        "observed_pit_content_hash": "a" * 64,
        "code_manifest_evidence_hash": "d" * 64,
        "dlp_policy_hash": "f" * 64,
        "stopping_rule_hash": "1" * 64,
        "frozen_at_us": 1_700_200_000_000_000,
    }
    payload.update(overrides)
    payload["freeze_record_hash"] = canonical_sha256(payload)
    return payload


def test_freeze_receipt_binds_every_terminal_search_and_emission_seal() -> None:
    freeze = _contract("FamilyFreezeReceipt")(**_freeze_payload())
    assert freeze.search_count == len(freeze.ordered_search_ids) == 2
    assert freeze.raw_proposal_count >= freeze.unique_performance_hypothesis_count


@pytest.mark.parametrize(
    "mutation",
    (
        {"search_count": 1},
        {"ordered_search_ids": ("search-2", "search-1")},
        {"ordered_search_terminal_hashes": ("1" * 64,)},
        {"ordered_emission_seal_hashes": ("3" * 64,)},
        {"unique_performance_hypothesis_count": 21},
        {"freeze_record_hash": "f" * 64},
    ),
)
def test_freeze_rejects_open_mismatched_or_rewritten_search_state(
    mutation: dict[str, Any],
) -> None:
    payload = _freeze_payload()
    payload.update(mutation)
    if "freeze_record_hash" not in mutation:
        payload = _rehash(payload, "freeze_record_hash")
    with pytest.raises(ValidationError):
        _contract("FamilyFreezeReceipt")(**payload)


def _trial_proof_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "trial_ledger_authority_id": "trial-ledger-authority-1",
        "history_artifact_id": f"sha256:{'1' * 64}",
        "history_artifact_sha256": "1" * 64,
        "history_head": "2" * 64,
        "snapshot_artifact_id": f"sha256:{'3' * 64}",
        "snapshot_artifact_sha256": "3" * 64,
        "seal_artifact_id": f"sha256:{'4' * 64}",
        "seal_artifact_sha256": "4" * 64,
        "global_unique_config_count": 100,
        "captured_at_us": 1_700_300_000_000_000,
    }
    payload.update(overrides)
    payload["proof_hash"] = canonical_sha256(payload)
    return payload


def test_trial_ledger_proof_is_content_addressed_and_exact() -> None:
    proof = _contract("TrialLedgerProof")(**_trial_proof_payload())
    assert proof.proof_hash == _trial_proof_payload()["proof_hash"]
    with pytest.raises(ValidationError):
        _contract("TrialLedgerProof")(
            **_rehash(
                {**_trial_proof_payload(), "history_artifact_id": f"sha256:{'f' * 64}"},
                "proof_hash",
            )
        )


def _manifest_payload(**overrides: Any) -> dict[str, Any]:
    configs = ("1" * 64, "2" * 64)
    receipts = ("3" * 64, "4" * 64)
    proof = _trial_proof_payload()
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_FAMILY_CLOSURE_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": "0" * 64,
        "closure_id": "closure-1",
        "family_id": "family-1",
        "declaration_id": "declaration-1",
        "declaration_record_hash": _declaration_payload()["declaration_record_hash"],
        "freeze_id": "freeze-1",
        "freeze_record_hash": _freeze_payload()["freeze_record_hash"],
        "closure_status": "COMPLETE",
        "selection_scope_id": "scope-1",
        "selection_decision_id": "decision-1",
        "selection_scope_history_head": "5" * 64,
        "selection_scope_outcome_record_hash": "5" * 64,
        "ordered_search_ids_hash": _freeze_payload()["ordered_search_ids_hash"],
        "ordered_search_terminal_hashes_hash": _freeze_payload()["ordered_search_terminal_hashes_hash"],
        "ordered_emission_seal_hashes_hash": _freeze_payload()["ordered_emission_seal_hashes_hash"],
        "ordered_proposal_ids_hash": "6" * 64,
        "ordered_disposition_record_hashes_hash": "7" * 64,
        "ordered_config_hashes": configs,
        "ordered_evaluation_receipt_hashes": receipts,
        "attempt_id": "attempt-1",
        "terminal_attempt_event_id": "attempt-1-event-1",
        "evaluation_batch_id": "batch-1",
        "evaluation_batch_receipt_hash": "8" * 64,
        "oos_release_id": "release-1",
        "oos_access_attempt_terminal_hash": "9" * 64,
        "oos_release_record_hash": "a" * 64,
        "raw_proposal_count": 20,
        "unique_performance_hypothesis_count": 2,
        "global_unique_config_count": proof["global_unique_config_count"],
        "performance_access_attempt_count": 1,
        "ordered_period_keys_hash": "b" * 64,
        "candidate_series_set_hash": "c" * 64,
        "benchmark_receipt_hash": "d" * 64,
        "cost_model_input_set_hash": "e" * 64,
        "winner_config_hash": configs[1],
        "winner_evaluation_receipt_hash": receipts[1],
        "trial_ledger_proof": proof,
        "trial_ledger_proof_hash": proof["proof_hash"],
        "failure_reasons": (),
        "observed_pit_authority_id": "pit-authority-1",
        "observed_pit_handle_id": "pit-handle-1",
        "observed_pit_content_hash": "a" * 64,
        "knowledge_time_policy_id": "PIT_EOD_V1",
        "knowledge_time_policy_version": "1",
        "observed_pit_authority_head": "b" * 64,
        "oos_exposure_id": "exposure-1",
        "exposure_fingerprint": "c" * 64,
        "exposure_authority_id": "exposure-authority-1",
        "exposure_authority_head": "d" * 64,
        "code_manifest_policy_id": "CODE_MANIFEST_POLICY_V1",
        "code_manifest_policy_version": "1",
        "code_manifest_policy_hash": "e" * 64,
        "code_manifest_evidence_hash": "d" * 64,
        "code_manifest_authority_id": "code-authority-1",
        "code_manifest_authority_head": "1" * 64,
        "start_execution_attestation_hash": "2" * 64,
        "terminal_execution_attestation_hash": "3" * 64,
        "execution_attestation_authority_id": "execution-authority-1",
        "start_execution_attestation_authority_head": "4" * 64,
        "terminal_execution_attestation_authority_head": "7" * 64,
        "immutable_artifact_lease_id": "lease-1",
        "immutable_artifact_lease_hash": "5" * 64,
        "immutable_artifact_lease_authority_id": "lease-authority-1",
        "immutable_artifact_lease_authority_head": "6" * 64,
        "closed_at_us": 1_700_400_000_000_000,
    }
    payload.update(overrides)
    payload["closure_manifest_hash"] = canonical_sha256(payload)
    payload["authority_head"] = payload["closure_manifest_hash"]
    return payload


def test_complete_manifest_binds_four_independent_counts_winner_and_trial_proof() -> None:
    manifest = _contract("DataSnoopingFamilyManifest")(**_manifest_payload())
    assert manifest.performance_access_attempt_count == 1
    assert manifest.global_unique_config_count == manifest.trial_ledger_proof.global_unique_config_count
    assert manifest.authority_head == manifest.closure_manifest_hash


def test_not_assessable_manifest_forbids_a_winner_and_requires_reasons() -> None:
    payload = _manifest_payload(
        closure_status="NOT_ASSESSABLE",
        evaluation_batch_receipt_hash=None,
        evaluation_batch_id=None,
        oos_release_id=None,
        oos_access_attempt_terminal_hash="9" * 64,
        oos_release_record_hash=None,
        ordered_config_hashes=(),
        ordered_evaluation_receipt_hashes=(),
        performance_access_attempt_count=1,
        ordered_period_keys_hash=None,
        candidate_series_set_hash=None,
        benchmark_receipt_hash=None,
        cost_model_input_set_hash=None,
        winner_config_hash=None,
        winner_evaluation_receipt_hash=None,
        terminal_execution_attestation_hash=None,
        terminal_execution_attestation_authority_head=None,
        failure_reasons=("INCOMPLETE_ATTEMPT",),
    )
    manifest = _contract("DataSnoopingFamilyManifest")(**payload)
    assert manifest.closure_status == "NOT_ASSESSABLE"

    for mutation in (
        {"failure_reasons": ()},
        {"failure_reasons": ("INCOMPLETE_ATTEMPT", "INCOMPLETE_ATTEMPT")},
        {"failure_reasons": ("Z_REASON", "A_REASON")},
        {"failure_reasons": ("free text",)},
        {"ordered_config_hashes": ("1" * 64,)},
        {"ordered_evaluation_receipt_hashes": ("3" * 64,)},
        {"winner_config_hash": "f" * 64},
        {"winner_evaluation_receipt_hash": "f" * 64},
    ):
        invalid = dict(payload)
        invalid.update(mutation)
        invalid = _rehash(
            invalid,
            "closure_manifest_hash",
            head_field="authority_head",
        )
        with pytest.raises(ValidationError):
            _contract("DataSnoopingFamilyManifest")(**invalid)


def test_not_assessable_manifest_allows_an_honest_pre_oos_terminal_block() -> None:
    payload = _manifest_payload(
        closure_status="NOT_ASSESSABLE",
        attempt_id=None,
        terminal_attempt_event_id=None,
        evaluation_batch_id=None,
        evaluation_batch_receipt_hash=None,
        oos_release_id=None,
        oos_access_attempt_terminal_hash=None,
        oos_release_record_hash=None,
        ordered_config_hashes=(),
        ordered_evaluation_receipt_hashes=(),
        performance_access_attempt_count=0,
        ordered_period_keys_hash=None,
        candidate_series_set_hash=None,
        benchmark_receipt_hash=None,
        cost_model_input_set_hash=None,
        winner_config_hash=None,
        winner_evaluation_receipt_hash=None,
        oos_exposure_id=None,
        exposure_fingerprint=None,
        exposure_authority_id=None,
        exposure_authority_head=None,
        start_execution_attestation_hash=None,
        terminal_execution_attestation_hash=None,
        execution_attestation_authority_id=None,
        start_execution_attestation_authority_head=None,
        terminal_execution_attestation_authority_head=None,
        immutable_artifact_lease_id=None,
        immutable_artifact_lease_hash=None,
        immutable_artifact_lease_authority_id=None,
        immutable_artifact_lease_authority_head=None,
        failure_reasons=("SECRET_QUARANTINED",),
    )
    manifest = _contract("DataSnoopingFamilyManifest")(**payload)
    assert manifest.performance_access_attempt_count == 0
    assert manifest.attempt_id is None


@pytest.mark.parametrize(
    "mutation",
    (
        {"trial_ledger_proof_hash": "f" * 64},
        {"global_unique_config_count": 99},
        {"unique_performance_hypothesis_count": 1},
        {"winner_evaluation_receipt_hash": "3" * 64},
    ),
)
def test_complete_manifest_rejects_proof_count_or_winner_correspondence_drift(
    mutation: dict[str, Any],
) -> None:
    payload = _manifest_payload(**mutation)
    with pytest.raises(ValidationError):
        _contract("DataSnoopingFamilyManifest")(**payload)


def test_manifest_rejects_global_trial_count_smaller_than_the_family_subset() -> None:
    proof = _trial_proof_payload(global_unique_config_count=1)
    payload = _manifest_payload(
        global_unique_config_count=1,
        trial_ledger_proof=proof,
        trial_ledger_proof_hash=proof["proof_hash"],
    )

    with pytest.raises(ValidationError):
        _contract("DataSnoopingFamilyManifest")(**payload)


def test_complete_manifest_allows_selection_scope_outcome_evidence_to_be_attached_later() -> None:
    manifest = _contract("DataSnoopingFamilyManifest")(**_manifest_payload(selection_scope_outcome_record_hash=None))
    assert manifest.closure_status == "COMPLETE"
    assert manifest.selection_scope_outcome_record_hash is None


def _resolved_kernel_results(
    replication_count: int = 10_000,
    *,
    ambiguous: bool = False,
) -> tuple[Any, Any]:
    exceedance_count = max(0, round(0.05 * (replication_count + 1) - 1)) if ambiguous else replication_count
    tail = _contract("MonteCarloTailResult")(
        exceedance_count=exceedance_count,
        replication_count=replication_count,
        p_numerator=exceedance_count + 1,
        p_denominator=replication_count + 1,
        p_value=(exceedance_count + 1) / (replication_count + 1),
        conditional_variance_formula_id="BINOMIAL_CONDITIONAL_V1",
        conservative_radius=float64_hex(sqrt(replication_count) / (replication_count + 1)),
        ambiguous=ambiguous,
    )
    prefix_counts = (99, replication_count) if replication_count > 99 else (replication_count,)
    prefixes = tuple(
        _contract("BootstrapPrefixHash")(
            algorithm_version="QUANTOS_STATIONARY_BOOTSTRAP_V1",
            block_length=2,
            replication_count=count,
            index_stream_sha256=("1" if count == replication_count else "2") * 64,
        )
        for count in prefix_counts
    )
    run_status = "PUBLISHABLE" if replication_count >= 10_000 else "NON_PUBLISHABLE_TEST_RUN"
    decision = "AMBIGUOUS" if ambiguous else "NON_REJECT"
    candidate_order = ("1" * 64, "2" * 64)
    reality = _contract("RealityCheckResult")(
        algorithm_version="WHITE_REALITY_CHECK_V1",
        objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
        period_count=20,
        candidate_count=2,
        block_length=2,
        candidate_order=candidate_order,
        column_means=("0x0.0p+0", "0x1.0000000000000p-4"),
        observed_statistic="0x1.0000000000000p-4",
        tail_result=tail,
        prefix_hashes=prefixes,
        decision=decision,
        run_status=run_status,
        reasons=(),
    )
    variants = tuple(
        _contract("SpaVariantResult")(
            variant=variant,
            centers=("0x0.0p+0", "0x1.0000000000000p-4"),
            tail_result=tail,
            decision=decision,
        )
        for variant in ("LOWER", "CONSISTENT", "UPPER")
    )
    spa = _contract("SpaResult")(
        algorithm_version="HANSEN_SPA_V1",
        objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
        period_count=20,
        candidate_count=2,
        block_length=2,
        candidate_order=candidate_order,
        observed_statistic="0x1.0000000000000p-4",
        gamma_zero=("0x0.0p+0", "0x1.0000000000000p-4"),
        degeneracy_floors=("0x1.0000000000000p-10",) * 2,
        long_run_variances=("0x1.0000000000000p-2",) * 2,
        thresholds=("0x1.0000000000000p-3",) * 2,
        variants=variants,
        primary_variant="CONSISTENT",
        prefix_hashes=prefixes,
        run_status=run_status,
        reasons=(),
    )
    return reality, spa


def _grid_assessment_payload(**overrides: Any) -> dict[str, Any]:
    reality, spa = _resolved_kernel_results()
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_GRID_ASSESSMENT_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": "0" * 64,
        "grid_assessment_id": "grid-assessment-1",
        "family_id": "family-1",
        "closure_manifest_hash": _manifest_payload()["closure_manifest_hash"],
        "algorithm_version": "WHITE_RC_HANSEN_SPA_FAMILY_V1",
        "period_count": 20,
        "block_length": 2,
        "replication_count": 10_000,
        "schedule_ordinal": 1,
        "prefix_index_stream_hash": "1" * 64,
        "previous_grid_assessment_hash": "0" * 64,
        "reality_check_result": reality,
        "reality_check_result_hash": canonical_sha256(reality),
        "spa_result": spa,
        "spa_result_hash": canonical_sha256(spa),
        "grid_point_status": "RESOLVED",
        "grid_point_verdict": "DOES_NOT_SUPPORT_SUPERIORITY",
        "promotion_effect": "NONE",
        "failure_reasons": (),
        "assessed_at_us": 1_700_500_000_000_000,
    }
    payload.update(overrides)
    payload["grid_assessment_record_hash"] = canonical_sha256(payload)
    return payload


def test_kernel_result_contracts_enforce_the_official_candidate_period_and_block_domain() -> None:
    reality, spa = _resolved_kernel_results()

    one_candidate_reality = {
        **reality.model_dump(mode="python"),
        "candidate_count": 1,
        "candidate_order": (reality.candidate_order[0],),
        "column_means": (reality.column_means[0],),
    }
    with pytest.raises(ValidationError):
        _contract("RealityCheckResult")(**one_candidate_reality)

    one_candidate_spa = {
        **spa.model_dump(mode="python"),
        "candidate_count": 1,
        "candidate_order": (spa.candidate_order[0],),
        "gamma_zero": (spa.gamma_zero[0],),
        "degeneracy_floors": (spa.degeneracy_floors[0],),
        "long_run_variances": (spa.long_run_variances[0],),
        "thresholds": (spa.thresholds[0],),
        "variants": tuple(
            {**variant.model_dump(mode="python"), "centers": (variant.centers[0],)}
            for variant in spa.variants
        ),
    }
    with pytest.raises(ValidationError):
        _contract("SpaResult")(**one_candidate_spa)

    for contract_name, result in (
        ("RealityCheckResult", reality),
        ("SpaResult", spa),
    ):
        below_minimum_periods = {
            **result.model_dump(mode="python"),
            "period_count": 19,
        }
        with pytest.raises(ValidationError):
            _contract(contract_name)(**below_minimum_periods)

        oversized_block = {
            **result.model_dump(mode="python"),
            "block_length": 11,
            "prefix_hashes": tuple(
                {**prefix.model_dump(mode="python"), "block_length": 11}
                for prefix in result.prefix_hashes
            ),
        }
        with pytest.raises(ValidationError):
            _contract(contract_name)(**oversized_block)


def test_grid_and_assessed_family_conclusion_enforce_the_official_period_floor() -> None:
    invalid_grid = _grid_assessment_payload(
        period_count=19,
        reality_check_result=None,
        reality_check_result_hash=None,
        spa_result=None,
        spa_result_hash=None,
        grid_point_status="INVALID",
        grid_point_verdict="NOT_APPLICABLE",
        failure_reasons=("INSUFFICIENT_PERIODS",),
    )
    with pytest.raises(ValidationError):
        _contract("DataSnoopingGridAssessment")(**invalid_grid)

    below_floor_conclusion = _family_assessment_payload(period_count=19)
    with pytest.raises(ValidationError):
        _contract("DataSnoopingAssessment")(**below_floor_conclusion)


def _family_assessment_payload(**overrides: Any) -> dict[str, Any]:
    grid_hashes = ("a" * 64, "b" * 64)
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_ASSESSMENT_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": "0" * 64,
        "assessment_id": "assessment-1",
        "family_id": "family-1",
        "closure_manifest_hash": _manifest_payload()["closure_manifest_hash"],
        "algorithm_version": "WHITE_RC_HANSEN_SPA_FAMILY_V1",
        "ordered_latest_grid_assessment_hashes": grid_hashes,
        "ordered_latest_grid_assessment_hashes_hash": canonical_sha256(grid_hashes),
        "candidate_order_hash": "c" * 64,
        "period_count": 20,
        "minimum_replication_count": 10_000,
        "assessment_status": "ASSESSED",
        "diagnostic_verdict": "DOES_NOT_SUPPORT_SUPERIORITY",
        "promotion_effect": "NONE",
        "failure_reasons": (),
        "concluded_at_us": 1_700_500_100_000_000,
    }
    payload.update(overrides)
    payload["assessment_record_hash"] = canonical_sha256(payload)
    return payload


def test_grid_assessment_requires_both_exact_kernel_results_and_never_promotes() -> None:
    payload = _grid_assessment_payload()
    assessment = _contract("DataSnoopingGridAssessment")(**payload)
    assert assessment.grid_point_status == "RESOLVED"
    assert assessment.reality_check_result_hash == canonical_sha256(assessment.reality_check_result)
    assert assessment.spa_result_hash == canonical_sha256(assessment.spa_result)
    assert assessment.promotion_effect == "NONE"

    invalid = _rehash(
        {**payload, "spa_result": None, "spa_result_hash": None},
        "grid_assessment_record_hash",
    )
    with pytest.raises(ValidationError):
        _contract("DataSnoopingGridAssessment")(**invalid)


def test_invalid_grid_assessment_is_an_honest_audit_row_without_kernel_payloads() -> None:
    invalid_payload = _grid_assessment_payload(
        reality_check_result=None,
        reality_check_result_hash=None,
        spa_result=None,
        spa_result_hash=None,
        grid_point_status="INVALID",
        grid_point_verdict="NOT_APPLICABLE",
        failure_reasons=("KERNEL_VERIFICATION_FAILED",),
    )
    invalid = _contract("DataSnoopingGridAssessment")(**invalid_payload)
    assert invalid.grid_point_status == "INVALID"
    assert invalid.grid_point_verdict == "NOT_APPLICABLE"
    assert invalid.reality_check_result is None

    for mutation in (
        {"failure_reasons": ()},
        {"grid_point_verdict": "PENDING_MORE_REPLICATIONS"},
        {"reality_check_result": _resolved_kernel_results()[0]},
    ):
        payload = _rehash({**invalid_payload, **mutation}, "grid_assessment_record_hash")
        with pytest.raises(ValidationError):
            _contract("DataSnoopingGridAssessment")(**payload)


@pytest.mark.parametrize(
    ("verdict", "rc_decision", "spa_decision", "valid"),
    (
        ("SUPPORTS_SUPERIORITY", "REJECT", "REJECT", True),
        ("DOES_NOT_SUPPORT_SUPERIORITY", "NON_REJECT", "NON_REJECT", True),
        ("METHOD_DISAGREEMENT", "REJECT", "NON_REJECT", True),
        ("SUPPORTS_SUPERIORITY", "REJECT", "NON_REJECT", False),
        ("DOES_NOT_SUPPORT_SUPERIORITY", "REJECT", "NON_REJECT", False),
        ("METHOD_DISAGREEMENT", "NON_REJECT", "NON_REJECT", False),
    ),
)
def test_grid_verdict_exactly_matches_both_primary_kernel_decisions(
    verdict: str,
    rc_decision: str,
    spa_decision: str,
    valid: bool,
) -> None:
    reality, spa = _resolved_kernel_results()
    reality = reality.model_copy(update={"decision": rc_decision})
    variants = list(spa.variants)
    variants[1] = variants[1].model_copy(update={"decision": spa_decision})
    spa = spa.model_copy(update={"variants": tuple(variants)})
    payload = _grid_assessment_payload(
        reality_check_result=reality,
        reality_check_result_hash=canonical_sha256(reality),
        spa_result=spa,
        spa_result_hash=canonical_sha256(spa),
        grid_point_status="RESOLVED",
        grid_point_verdict=verdict,
    )
    if valid:
        assert _contract("DataSnoopingGridAssessment")(**payload).grid_point_verdict == verdict
    else:
        with pytest.raises(ValidationError):
            _contract("DataSnoopingGridAssessment")(**payload)


def test_grid_assessment_rejects_invalid_kernel_status_or_decision() -> None:
    reality, spa = _resolved_kernel_results()
    for invalid_reality, invalid_spa in (
        (
            reality.model_copy(update={"run_status": "INVALID"}),
            spa.model_copy(update={"run_status": "INVALID"}),
        ),
        (reality.model_copy(update={"decision": "INVALID"}), spa),
    ):
        payload = _grid_assessment_payload(
            reality_check_result=invalid_reality,
            reality_check_result_hash=canonical_sha256(invalid_reality),
            spa_result=invalid_spa,
            spa_result_hash=canonical_sha256(invalid_spa),
            grid_point_status="RESOLVED",
            grid_point_verdict="DOES_NOT_SUPPORT_SUPERIORITY",
        )
        with pytest.raises(ValidationError):
            _contract("DataSnoopingGridAssessment")(**payload)


def test_grid_assessment_rejects_declared_period_candidate_or_complete_prefix_drift() -> None:
    reality, spa = _resolved_kernel_results()
    mismatched_period_spa = spa.model_copy(update={"period_count": spa.period_count + 1})
    mismatched_candidate_spa = spa.model_copy(update={"candidate_order": tuple(reversed(spa.candidate_order))})

    changed_prefixes = list(spa.prefix_hashes)
    changed_prefixes[0] = changed_prefixes[0].model_copy(
        update={"index_stream_sha256": "f" * 64},
    )
    mismatched_prefix_spa = spa.model_copy(update={"prefix_hashes": tuple(changed_prefixes)})

    for invalid_spa in (mismatched_period_spa, mismatched_candidate_spa, mismatched_prefix_spa):
        payload = _grid_assessment_payload(
            reality_check_result=reality,
            reality_check_result_hash=canonical_sha256(reality),
            spa_result=invalid_spa,
            spa_result_hash=canonical_sha256(invalid_spa),
            grid_point_status="RESOLVED",
            grid_point_verdict="DOES_NOT_SUPPORT_SUPERIORITY",
        )
        with pytest.raises(ValidationError):
            _contract("DataSnoopingGridAssessment")(**payload)

    period_drift = _grid_assessment_payload(period_count=reality.period_count + 1)
    with pytest.raises(ValidationError):
        _contract("DataSnoopingGridAssessment")(**period_drift)


def test_real_reality_check_kernel_output_constructs_a_c8_grid_assessment() -> None:
    cycle = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0)
    period_count = 24

    def series(offset: float, amplitude: float, phase: int) -> tuple[float, ...]:
        return tuple(offset + amplitude * cycle[(index + phase) % len(cycle)] for index in range(period_count))

    period_keys = tuple(
        _contract("PeriodKey")(
            event_time=1_700_000_000_000_000 + index * 86_400_000_000,
            frequency_id="UTC_DAILY",
            label_horizon="P1D",
        )
        for index in range(period_count)
    )
    reality, spa = compute_reality_check_and_spa(
        candidate_net_returns=(
            ("1" * 64, series(0.006, 0.004, 3)),
            ("2" * 64, series(0.018, 0.003, 0)),
        ),
        benchmark_net_returns=series(0.0, 0.001, 5),
        period_keys=period_keys,
        block_length=4,
        alpha=0.05,
        seed=81_985_529_216_486_895,
        replication_count=199,
        prefix_replication_counts=(99, 199),
    )
    ambiguous = reality.tail_result.ambiguous or any(variant.tail_result.ambiguous for variant in spa.variants)
    payload = _grid_assessment_payload(
        period_count=period_count,
        block_length=4,
        replication_count=199,
        schedule_ordinal=2,
        prefix_index_stream_hash=reality.prefix_hashes[-1].index_stream_sha256,
        reality_check_result=reality,
        reality_check_result_hash=canonical_sha256(reality),
        spa_result=spa,
        spa_result_hash=canonical_sha256(spa),
        grid_point_status="AMBIGUOUS" if ambiguous else "RESOLVED",
        grid_point_verdict="PENDING_MORE_REPLICATIONS",
    )
    assessment = _contract("DataSnoopingGridAssessment")(**payload)
    assert assessment.reality_check_result == reality
    assert assessment.spa_result == spa
    assert reality.prefix_hashes == spa.prefix_hashes


def test_non_publishable_grid_checkpoint_must_remain_pending() -> None:
    small_reality, small_spa = _resolved_kernel_results(replication_count=99)
    small_payload = _grid_assessment_payload(
        replication_count=99,
        reality_check_result=small_reality,
        reality_check_result_hash=canonical_sha256(small_reality),
        spa_result=small_spa,
        spa_result_hash=canonical_sha256(small_spa),
        grid_point_status="RESOLVED",
        grid_point_verdict="PENDING_MORE_REPLICATIONS",
    )
    checkpoint = _contract("DataSnoopingGridAssessment")(**small_payload)
    assert checkpoint.grid_point_verdict == "PENDING_MORE_REPLICATIONS"

    non_pending = _rehash(
        {**small_payload, "grid_point_verdict": "DOES_NOT_SUPPORT_SUPERIORITY"},
        "grid_assessment_record_hash",
    )
    with pytest.raises(ValidationError):
        _contract("DataSnoopingGridAssessment")(**non_pending)

    falsely_publishable_reality = small_reality.model_copy(update={"run_status": "PUBLISHABLE"})
    falsely_publishable_spa = small_spa.model_copy(update={"run_status": "PUBLISHABLE"})
    falsely_publishable = _grid_assessment_payload(
        **{
            **small_payload,
            "reality_check_result": falsely_publishable_reality,
            "reality_check_result_hash": canonical_sha256(falsely_publishable_reality),
            "spa_result": falsely_publishable_spa,
            "spa_result_hash": canonical_sha256(falsely_publishable_spa),
        }
    )
    with pytest.raises(ValidationError):
        _contract("DataSnoopingGridAssessment")(**falsely_publishable)


def test_ambiguous_grid_points_allow_only_pending_or_unresolved_verdicts() -> None:
    reality, spa = _resolved_kernel_results(replication_count=199, ambiguous=True)
    intermediate = _contract("DataSnoopingGridAssessment")(
        **_grid_assessment_payload(
            replication_count=199,
            schedule_ordinal=2,
            reality_check_result=reality,
            reality_check_result_hash=canonical_sha256(reality),
            spa_result=spa,
            spa_result_hash=canonical_sha256(spa),
            grid_point_status="AMBIGUOUS",
            grid_point_verdict="PENDING_MORE_REPLICATIONS",
        )
    )
    assert intermediate.grid_point_verdict == "PENDING_MORE_REPLICATIONS"

    final_reality, final_spa = _resolved_kernel_results(ambiguous=True)
    terminal = _contract("DataSnoopingGridAssessment")(
        **_grid_assessment_payload(
            reality_check_result=final_reality,
            reality_check_result_hash=canonical_sha256(final_reality),
            spa_result=final_spa,
            spa_result_hash=canonical_sha256(final_spa),
            grid_point_status="AMBIGUOUS",
            grid_point_verdict="MONTE_CARLO_UNRESOLVED",
        )
    )
    assert terminal.grid_point_verdict == "MONTE_CARLO_UNRESOLVED"

    invalid = _rehash(
        {**terminal.model_dump(mode="python"), "grid_point_verdict": "DOES_NOT_SUPPORT_SUPERIORITY"},
        "grid_assessment_record_hash",
    )
    with pytest.raises(ValidationError):
        _contract("DataSnoopingGridAssessment")(**invalid)


def test_non_primary_spa_ambiguity_does_not_control_the_grid_schedule() -> None:
    reality, spa = _resolved_kernel_results()
    variants = list(spa.variants)
    lower = variants[0]
    variants[0] = lower.model_copy(
        update={
            "tail_result": lower.tail_result.model_copy(update={"ambiguous": True}),
            "decision": "AMBIGUOUS",
        }
    )
    spa = spa.model_copy(update={"variants": tuple(variants)})

    assessment = _contract("DataSnoopingGridAssessment")(
        **_grid_assessment_payload(
            reality_check_result=reality,
            reality_check_result_hash=canonical_sha256(reality),
            spa_result=spa,
            spa_result_hash=canonical_sha256(spa),
            grid_point_status="RESOLVED",
            grid_point_verdict="DOES_NOT_SUPPORT_SUPERIORITY",
        )
    )

    assert assessment.spa_result is not None
    assert assessment.spa_result.variants[0].decision == "AMBIGUOUS"
    assert assessment.spa_result.variants[1].decision == "NON_REJECT"
    assert assessment.grid_point_status == "RESOLVED"


def test_grid_assessment_rejects_non_kernel_monte_carlo_radius() -> None:
    reality, spa = _resolved_kernel_results()
    invalid_tail = reality.tail_result.model_copy(
        update={"conservative_radius": float64_hex(0.5)},
    )
    invalid_reality = reality.model_copy(update={"tail_result": invalid_tail})
    invalid_radius_payload = _grid_assessment_payload(
        reality_check_result=invalid_reality,
        reality_check_result_hash=canonical_sha256(invalid_reality),
        spa_result=spa,
        spa_result_hash=canonical_sha256(spa),
        grid_point_status="RESOLVED",
        grid_point_verdict="DOES_NOT_SUPPORT_SUPERIORITY",
    )
    with pytest.raises(ValidationError):
        _contract("DataSnoopingGridAssessment")(**invalid_radius_payload)


def test_assessed_family_conclusion_is_independent_non_promoting_and_never_pending() -> None:
    assessment = _contract("DataSnoopingAssessment")(**_family_assessment_payload())
    assert assessment.assessment_status == "ASSESSED"
    assert assessment.diagnostic_verdict == "DOES_NOT_SUPPORT_SUPERIORITY"
    assert assessment.promotion_effect == "NONE"

    pending = _rehash(
        {**assessment.model_dump(mode="python"), "diagnostic_verdict": "PENDING_MORE_REPLICATIONS"},
        "assessment_record_hash",
    )
    with pytest.raises(ValidationError):
        _contract("DataSnoopingAssessment")(**pending)


def test_mixed_local_grid_verdicts_can_form_an_independent_family_conclusion() -> None:
    non_reject = _contract("DataSnoopingGridAssessment")(**_grid_assessment_payload())
    reject_reality, reject_spa = _resolved_kernel_results()
    reject_reality = reject_reality.model_copy(update={"decision": "REJECT"})
    reject_variants = tuple(variant.model_copy(update={"decision": "REJECT"}) for variant in reject_spa.variants)
    reject_spa = reject_spa.model_copy(update={"variants": reject_variants})
    reject_payload = _grid_assessment_payload(
        grid_assessment_id="grid-assessment-2",
        previous_grid_assessment_hash=non_reject.grid_assessment_record_hash,
        reality_check_result=reject_reality,
        reality_check_result_hash=canonical_sha256(reject_reality),
        spa_result=reject_spa,
        spa_result_hash=canonical_sha256(reject_spa),
        grid_point_verdict="SUPPORTS_SUPERIORITY",
    )
    reject = _contract("DataSnoopingGridAssessment")(**reject_payload)
    grid_hashes = (
        non_reject.grid_assessment_record_hash,
        reject.grid_assessment_record_hash,
    )
    conclusion = _contract("DataSnoopingAssessment")(
        **_family_assessment_payload(
            ordered_latest_grid_assessment_hashes=grid_hashes,
            ordered_latest_grid_assessment_hashes_hash=canonical_sha256(grid_hashes),
            diagnostic_verdict="DOES_NOT_SUPPORT_SUPERIORITY",
        )
    )
    assert non_reject.grid_point_verdict == "DOES_NOT_SUPPORT_SUPERIORITY"
    assert reject.grid_point_verdict == "SUPPORTS_SUPERIORITY"
    assert conclusion.diagnostic_verdict == "DOES_NOT_SUPPORT_SUPERIORITY"
    assert conclusion.assessment_record_hash not in grid_hashes


def test_not_assessable_family_conclusion_can_have_zero_or_partial_grid_evidence() -> None:
    payload = _family_assessment_payload(
        ordered_latest_grid_assessment_hashes=(),
        ordered_latest_grid_assessment_hashes_hash=canonical_sha256(()),
        candidate_order_hash=None,
        period_count=None,
        minimum_replication_count=None,
        assessment_status="NOT_ASSESSABLE",
        diagnostic_verdict="NOT_APPLICABLE",
        failure_reasons=("INCOMPLETE_FAMILY",),
    )
    assessment = _contract("DataSnoopingAssessment")(**payload)
    assert assessment.assessment_status == "NOT_ASSESSABLE"
    assert assessment.diagnostic_verdict == "NOT_APPLICABLE"
    assert assessment.promotion_effect == "NONE"

    partial = _contract("DataSnoopingAssessment")(
        **_family_assessment_payload(
            candidate_order_hash=None,
            period_count=None,
            minimum_replication_count=None,
            assessment_status="NOT_ASSESSABLE",
            diagnostic_verdict="NOT_APPLICABLE",
            failure_reasons=("KERNEL_VERIFICATION_FAILED",),
        )
    )
    assert partial.ordered_latest_grid_assessment_hashes


def test_family_conclusion_derivation_fields_are_total_only_when_assessed() -> None:
    assessed = _contract("DataSnoopingAssessment")(**_family_assessment_payload())
    assert assessed.candidate_order_hash is not None
    assert assessed.period_count == 20
    assert assessed.minimum_replication_count == 10_000

    for mutation in (
        {"candidate_order_hash": None},
        {"period_count": None},
        {"minimum_replication_count": None},
        {"minimum_replication_count": 9_999},
    ):
        with pytest.raises(ValidationError):
            _contract("DataSnoopingAssessment")(
                **_rehash(
                    {**_family_assessment_payload(), **mutation},
                    "assessment_record_hash",
                )
            )

    not_assessable = _family_assessment_payload(
        candidate_order_hash=None,
        period_count=None,
        minimum_replication_count=None,
        assessment_status="NOT_ASSESSABLE",
        diagnostic_verdict="NOT_APPLICABLE",
        failure_reasons=("KERNEL_VERIFICATION_FAILED",),
    )
    assert _contract("DataSnoopingAssessment")(**not_assessable).period_count is None
    with pytest.raises(ValidationError):
        _contract("DataSnoopingAssessment")(
            **_rehash(
                {**not_assessable, "period_count": 20},
                "assessment_record_hash",
            )
        )


@pytest.mark.parametrize(
    "mutation",
    (
        {"promotion_effect": "PROMOTE"},
        {"algorithm_version": "UNDECLARED_ASSESSMENT_V1"},
        {"failure_reasons": ("INCOMPLETE_FAMILY", "INCOMPLETE_FAMILY")},
        {"failure_reasons": ("Z_REASON", "A_REASON")},
        {"failure_reasons": ("free text",)},
        {"diagnostic_verdict": "NOT_APPLICABLE"},
        {"ordered_latest_grid_assessment_hashes": ()},
        {"ordered_latest_grid_assessment_hashes": ("a" * 64, "a" * 64)},
        {"ordered_latest_grid_assessment_hashes_hash": "f" * 64},
        {"failure_reasons": ()},
        {"assessment_record_hash": "f" * 64},
    ),
)
def test_family_assessment_rejects_promotion_branch_or_hash_drift(
    mutation: dict[str, Any],
) -> None:
    payload = (
        _family_assessment_payload(
            assessment_status="NOT_ASSESSABLE",
            diagnostic_verdict="NOT_APPLICABLE",
            failure_reasons=("INCOMPLETE_FAMILY",),
        )
        if mutation == {"failure_reasons": ()}
        else _family_assessment_payload()
    )
    payload.update(mutation)
    if "assessment_record_hash" not in mutation:
        payload = _rehash(payload, "assessment_record_hash")
    with pytest.raises(ValidationError):
        _contract("DataSnoopingAssessment")(**payload)


def test_grid_and_family_record_hashes_are_separate_complete_canonical_records() -> None:
    grid = _contract("DataSnoopingGridAssessment")(**_grid_assessment_payload())
    conclusion_payload = _family_assessment_payload(
        ordered_latest_grid_assessment_hashes=(grid.grid_assessment_record_hash,),
        ordered_latest_grid_assessment_hashes_hash=canonical_sha256((grid.grid_assessment_record_hash,)),
    )
    conclusion = _contract("DataSnoopingAssessment")(**conclusion_payload)
    assert grid.grid_assessment_record_hash == canonical_sha256(
        grid.model_dump(mode="python", exclude={"grid_assessment_record_hash"})
    )
    assert conclusion.assessment_record_hash == canonical_sha256(
        conclusion.model_dump(mode="python", exclude={"assessment_record_hash"})
    )
    assert grid.grid_assessment_record_hash != conclusion.assessment_record_hash


def test_c8_contract_surface_contains_no_c9_publication_fields() -> None:
    forbidden = {
        "artifact_core_hash",
        "artifact_core_scan_receipt_hash",
        "published_artifact_envelope_hash",
        "artifact_publication_receipt_hash",
        "publication_projection_hash",
        "publication_chain_head",
    }
    for name in C8_CONTRACT_FIELDS:
        assert not (set(_contract(name).model_fields) & forbidden)


def test_c8_contract_text_remains_nfc_and_extra_fields_fail_closed() -> None:
    declaration = _declaration_payload()
    for invalid in (
        {**declaration, "objective_id": "e\u0301"},
        {**declaration, "unexpected": "field"},
    ):
        with pytest.raises(ValidationError):
            _contract("DataSnoopingDeclaration")(**invalid)
