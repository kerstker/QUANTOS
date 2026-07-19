"""RED contracts for exact-v3 Pass C slices C5 and C6.

These tests intentionally describe the wished-for public contracts.  They do
not activate schema v3, render DDL, or exercise a persistence service.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

import quantos.contracts.research as research_contracts
from quantos.contracts.canonical import canonical_json_bytes, canonical_sha256


C5_CONTRACT_FIELDS = {
    "FamilySearchRecord": {
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
    },
    "CandidateIdentity": {
        "family_id",
        "search_id",
        "emission_ordinal",
        "proposal_id",
        "proposal_branch",
        "raw_envelope_hash",
    },
    "CandidateProposalRegistrationRecord": {
        "identity",
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
    },
    "CandidateProposalRecord": {
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
    },
    "CandidateHypothesisRecord": {
        "family_id",
        "first_search_id",
        "config_hash",
        "alpha_id",
        "canonical_hypothesis_hash",
        "first_proposal_id",
    },
    "CandidateEmissionSeal": {
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
    "RegisteredEnvelopeHandle": {
        "proposal_id",
        "family_id",
        "search_id",
        "emission_ordinal",
        "restricted_clean_blob_receipt_hash",
        "registration_record_hash",
        "registration_authority_head",
    },
}

C6_CONTRACT_FIELDS = {
    "CodeManifestPolicyRef": {"policy_id", "policy_version", "policy_hash"},
    "CodeManifestEntry": {
        "repo_path",
        "git_object_format",
        "git_blob_oid",
        "blob_sha256",
    },
    "CodeManifestEvidence": {
        "source_commit",
        "worktree_state",
        "dirty_tree_fingerprint",
        "policy_ref",
        "ordered_entries",
        "path_set_hash",
        "manifest_hash",
        "runtime_identity",
        "dependency_lock_hash",
        "authority_id",
        "authority_head",
        "evidence_hash",
    },
    "ExecutionCodeAttestation": {
        "code_manifest_hash",
        "source_commit",
        "runtime_identity",
        "dependency_lock_hash",
        "immutable_artifact_id",
        "immutable_artifact_digest",
        "entrypoint",
        "process_image_hash",
        "lease_id",
        "authority_id",
        "authority_head",
        "issued_at_us",
        "attestation_hash",
    },
    "ImmutableArtifactLease": {
        "lease_id",
        "artifact_id",
        "artifact_digest",
        "code_manifest_hash",
        "process_image_hash",
        "authority_id",
        "authority_head",
        "acquired_at_us",
        "expires_at_us",
        "lease_hash",
    },
    "ObservedPitDatasetHandle": {
        "authority_id",
        "handle_id",
        "provenance_class",
        "dataset_id",
        "dataset_version",
        "raw_archive_digest",
        "raw_archive_byte_length",
        "normalized_pit_store_digest",
        "normalized_pit_store_version",
        "normalized_store_byte_length",
        "quality_admission_id",
        "quality_authority_head",
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
        "content_hash",
        "authority_head",
    },
    "OosExposureRef": {
        "oos_exposure_id",
        "selection_scope_id",
        "selection_decision_id",
        "family_id",
        "exposure_ordinal",
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
        "period_start_us",
        "period_end_us",
        "exposure_fingerprint",
        "exposure_version",
        "observed_pit_authority_head",
    },
    "OosExposureConsumption": {
        "oos_exposure_id",
        "selection_decision_id",
        "family_id",
        "access_attempt_id",
        "expected_exposure_version",
        "consumed_at_us",
        "consumption_authority_id",
        "consumption_record_hash",
        "consumption_authority_head",
    },
}

AUTHORITY_TIMESTAMP_FIELDS = {
    "FamilySearchRecord": (
        "external_ingestion_deadline_us",
        "linked_at_us",
    ),
    "CandidateProposalRegistrationRecord": ("registered_at_us",),
    "CandidateProposalRecord": ("parsed_at_us",),
    "CandidateEmissionSeal": ("sealed_at_us",),
    "ExecutionCodeAttestation": ("issued_at_us",),
    "ImmutableArtifactLease": ("acquired_at_us", "expires_at_us"),
    "OosExposureConsumption": ("consumed_at_us",),
}

PUBLIC_DISPOSITION_REASONS = (
    "EVALUATION_EXCEPTION",
    "EVALUATION_TIMEOUT",
    "ATOMIC_COMMIT_FAILED",
    "ARTIFACT_LEASE_INVALID",
    "EXECUTION_ATTESTATION_MISMATCH",
    "RECOVERY_INCOMPLETE",
    "INVALID_UTF8",
    "MALFORMED_JSON",
    "DUPLICATE_JSON_KEY",
    "NON_NFC_TEXT",
    "SCHEMA_INVALID",
    "TYPE_INVALID",
    "GUARDRAIL_REJECTED",
    "DLP_SECRET_DETECTED",
    "DLP_SCANNER_UNAVAILABLE",
    "DLP_SCANNER_ERROR",
)


def _contract(name: str) -> type[Any]:
    contract = getattr(research_contracts, name, None)
    assert contract is not None, f"Pass C contract is missing: {name}"
    return contract


def _assert_frozen_exact_contract(name: str, fields: set[str]) -> None:
    contract = _contract(name)
    assert set(contract.model_fields) == fields


def test_family_search_generator_seed_matches_sqlite_integer_domain() -> None:
    contract = _contract("FamilySearchRecord")
    payload = {
        "family_id": "family-1",
        "search_id": "search-1",
        "research_program_id": "program-1",
        "search_kind": "GUIDED",
        "generator_seed": (1 << 63) - 1,
        "proposal_budget": 10,
        "stopping_rule_hash": "a" * 64,
        "external_ingestion_deadline_us": 20,
        "search_authority_id": "search-authority-1",
        "search_authority_head": "b" * 64,
        "linked_at_us": 10,
        "link_ordinal": 1,
    }

    assert contract(**payload).generator_seed == (1 << 63) - 1
    with pytest.raises(ValidationError):
        contract(**{**payload, "generator_seed": 1 << 63})
    assert contract.model_config["frozen"] is True
    assert contract.model_config["extra"] == "forbid"


@pytest.mark.parametrize(
    ("name", "fields"),
    tuple(C5_CONTRACT_FIELDS.items()) + tuple(C6_CONTRACT_FIELDS.items()),
)
def test_c5_c6_contracts_are_present_frozen_and_exact(
    name: str,
    fields: set[str],
) -> None:
    _assert_frozen_exact_contract(name, fields)


@pytest.mark.parametrize(
    ("name", "timestamp_fields"),
    AUTHORITY_TIMESTAMP_FIELDS.items(),
)
def test_authority_timestamps_are_positive_epoch_microseconds_with_explicit_units(
    name: str,
    timestamp_fields: tuple[str, ...],
) -> None:
    contract = _contract(name)
    properties = contract.model_json_schema()["properties"]
    for field_name in timestamp_fields:
        assert field_name.endswith("_us")
        assert properties[field_name]["exclusiveMinimum"] == 0
    assert all(
        not field_name.endswith("_at") and "deadline" not in field_name
        for field_name in contract.model_fields
        if not field_name.endswith("_us")
    )


@pytest.mark.parametrize("name", C5_CONTRACT_FIELDS)
def test_c5_public_contracts_do_not_accept_private_chain_authority_fields(
    name: str,
) -> None:
    fields = set(_contract(name).model_fields)
    assert fields.isdisjoint({"record_hash", "prev_record_hash", "previous_record_hash", "authority_instance_id"})


def _candidate_identity_payload(*, branch: str, raw_hash: str | None) -> dict[str, Any]:
    identity_payload: dict[str, Any] = {
        "family_id": "family-1",
        "search_id": "search-1",
        "emission_ordinal": 7,
        "proposal_branch": branch,
        "raw_envelope_hash": raw_hash,
    }
    proposal_key = {
        "family_id": identity_payload["family_id"],
        "search_id": identity_payload["search_id"],
        "emission_ordinal": identity_payload["emission_ordinal"],
    }
    if branch == "SECRET_QUARANTINED":
        proposal_key["status"] = "SECRET_QUARANTINED"
    else:
        proposal_key["raw_envelope_hash"] = raw_hash
    identity_payload["proposal_id"] = canonical_sha256(proposal_key)
    return identity_payload


def test_quarantined_proposal_identity_is_the_exact_safe_public_formula() -> None:
    identity_type = _contract("CandidateIdentity")
    first = identity_type(**_candidate_identity_payload(branch="SECRET_QUARANTINED", raw_hash=None))
    second = identity_type(**_candidate_identity_payload(branch="SECRET_QUARANTINED", raw_hash=None))

    expected = canonical_sha256(
        {
            "family_id": "family-1",
            "search_id": "search-1",
            "emission_ordinal": 7,
            "status": "SECRET_QUARANTINED",
        }
    )
    assert first.proposal_id == second.proposal_id == expected
    assert first.raw_envelope_hash is None


def test_quarantined_identity_rejects_any_raw_or_derived_secret_hash() -> None:
    identity_type = _contract("CandidateIdentity")
    payload = _candidate_identity_payload(branch="SECRET_QUARANTINED", raw_hash="a" * 64)
    with pytest.raises(ValidationError):
        identity_type(**payload)


def test_clean_proposal_identity_binds_the_exact_raw_envelope_hash() -> None:
    identity_type = _contract("CandidateIdentity")
    first = identity_type(**_candidate_identity_payload(branch="CLEAN", raw_hash="a" * 64))
    second = identity_type(**_candidate_identity_payload(branch="CLEAN", raw_hash="b" * 64))

    assert first.proposal_id != second.proposal_id
    with pytest.raises(ValidationError):
        identity_type(**_candidate_identity_payload(branch="CLEAN", raw_hash=None))


def _quarantine_record_payload() -> dict[str, Any]:
    return {
        "identity": _candidate_identity_payload(
            branch="SECRET_QUARANTINED",
            raw_hash=None,
        ),
        "media_type": "application/json",
        "raw_byte_length": None,
        "length_bucket": "GT_1_LE_4_KIB",
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "scanner_implementation": "QUANTOS_DLP_SCANNER",
        "scanner_version": "1",
        "dlp_result_code": "SECRET_QUARANTINED",
        "restricted_clean_blob_receipt_hash": None,
        "quarantine_record_id": "qtn_" + "a" * 32,
        "registered_at_us": 1,
    }


def test_quarantine_public_record_contains_only_safe_coarse_metadata() -> None:
    record_type = _contract("CandidateProposalRegistrationRecord")
    record = record_type(**_quarantine_record_payload())
    public = record.model_dump(mode="json", exclude_none=True)
    public_text = repr(public)

    for forbidden in (
        "raw_byte_length",
        "raw_envelope_hash",
        "scan_result_hash",
        "matched_result_hash",
        "restricted_blob_address",
        "restricted_authority_head",
    ):
        assert forbidden not in public_text
    assert public["length_bucket"] == "GT_1_LE_4_KIB"
    assert public["quarantine_record_id"] == "qtn_" + "a" * 32


def test_quarantine_record_rejects_clean_branch_or_exact_length_leakage() -> None:
    record_type = _contract("CandidateProposalRegistrationRecord")
    payload = _quarantine_record_payload()
    payload["raw_byte_length"] = 1234
    payload["restricted_clean_blob_receipt_hash"] = "c" * 64
    with pytest.raises(ValidationError):
        record_type(**payload)


def _clean_record_payload(
    *,
    raw_byte_length: int = 512,
    length_bucket: str = "LE_1_KIB",
) -> dict[str, Any]:
    return {
        "identity": _candidate_identity_payload(
            branch="CLEAN",
            raw_hash="a" * 64,
        ),
        "media_type": "application/json",
        "raw_byte_length": raw_byte_length,
        "length_bucket": length_bucket,
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "scanner_implementation": "QUANTOS_DLP_SCANNER",
        "scanner_version": "1",
        "dlp_result_code": "CLEAN",
        "restricted_clean_blob_receipt_hash": "c" * 64,
        "quarantine_record_id": None,
        "registered_at_us": 1,
    }


@pytest.mark.parametrize(
    ("raw_byte_length", "length_bucket"),
    (
        (1, "LE_1_KIB"),
        (1024, "LE_1_KIB"),
        (1025, "GT_1_LE_4_KIB"),
        (4096, "GT_1_LE_4_KIB"),
        (4097, "GT_4_LE_16_KIB"),
        (16384, "GT_4_LE_16_KIB"),
        (16385, "GT_16_LE_64_KIB"),
        (65536, "GT_16_LE_64_KIB"),
        (65537, "GT_64_LE_256_KIB"),
        (262144, "GT_64_LE_256_KIB"),
        (262145, "GT_256_LE_1024_KIB"),
        (1048576, "GT_256_LE_1024_KIB"),
        (1048577, "GT_1_MIB"),
    ),
)
def test_clean_length_bucket_is_closed_and_recomputed_from_exact_length(
    raw_byte_length: int,
    length_bucket: str,
) -> None:
    record_type = _contract("CandidateProposalRegistrationRecord")
    assert (
        record_type(
            **_clean_record_payload(
                raw_byte_length=raw_byte_length,
                length_bucket=length_bucket,
            )
        ).length_bucket
        == length_bucket
    )
    with pytest.raises(ValidationError):
        record_type(
            **_clean_record_payload(
                raw_byte_length=raw_byte_length,
                length_bucket=("GT_1_LE_4_KIB" if length_bucket == "LE_1_KIB" else "LE_1_KIB"),
            )
        )


@pytest.mark.parametrize(
    "media_type",
    (
        "Application/JSON",
        "application/json; charset=utf-8",
        "application/" + "x" * 128,
        "application/json" + chr(10),
    ),
)
def test_media_type_rejects_noncanonical_parameters_controls_and_overflow(
    media_type: str,
) -> None:
    payload = _quarantine_record_payload()
    payload["media_type"] = media_type
    with pytest.raises(ValidationError):
        _contract("CandidateProposalRegistrationRecord")(**payload)


def test_public_quarantine_metadata_rejects_secret_shaped_or_self_describing_text() -> None:
    record_type = _contract("CandidateProposalRegistrationRecord")
    secret_shaped = "Bear" + "er " + "x" * 48
    private_marker = "-" * 5 + "BEGIN " + "PRIVATE " + "KEY" + "-" * 5
    mutations = {
        "media_type": secret_shaped,
        "length_bucket": str(2048),
        "dlp_policy_id": secret_shaped,
        "dlp_policy_version": private_marker,
        "scanner_implementation": secret_shaped,
        "scanner_version": private_marker,
        "quarantine_record_id": "opaque-" + secret_shaped,
    }
    for field_name, value in mutations.items():
        payload = _quarantine_record_payload()
        payload[field_name] = value
        with pytest.raises(ValidationError):
            record_type(**payload)


def test_dlp_result_code_cannot_be_swapped_between_public_branches() -> None:
    record_type = _contract("CandidateProposalRegistrationRecord")
    quarantine = _quarantine_record_payload()
    quarantine["dlp_result_code"] = "CLEAN"
    with pytest.raises(ValidationError):
        record_type(**quarantine)

    clean = _clean_record_payload()
    clean["dlp_result_code"] = "SECRET_QUARANTINED"
    with pytest.raises(ValidationError):
        record_type(**clean)


def _canonical_alpha_proposal() -> dict[str, Any]:
    return {
        "alpha_id": "alpha-1",
        "factor_ids": ["factor-a", "factor-b"],
        "name": "Alpha One",
        "params": {"lookback": "20", "side": "long_short"},
        "universe": "crypto-liquid",
    }


def _terminal_proposal_payload(parse_status: str = "ACCEPTED") -> dict[str, Any]:
    canonical_proposal = _canonical_alpha_proposal()
    accepted = parse_status == "ACCEPTED"
    return {
        "proposal_id": "a" * 64,
        "family_id": "family-1",
        "search_id": "search-1",
        "emission_ordinal": 1,
        "registration_record_hash": "b" * 64,
        "parse_status": parse_status,
        "canonical_proposal_json": (
            canonical_json_bytes(canonical_proposal).decode("utf-8")
            if accepted
            else None
        ),
        "canonical_proposal_hash": (
            canonical_sha256(canonical_proposal) if accepted else None
        ),
        "config_hash": (
            canonical_sha256(
                {
                    "factor_ids": canonical_proposal["factor_ids"],
                    "universe": canonical_proposal["universe"],
                    "params": canonical_proposal["params"],
                }
            )
            if accepted
            else None
        ),
        "alpha_id": canonical_proposal["alpha_id"] if accepted else None,
        "parsed_at_us": 2,
    }


def test_terminal_proposal_accepted_branch_recomputes_every_canonical_identity() -> None:
    record_type = _contract("CandidateProposalRecord")
    accepted = _terminal_proposal_payload()
    assert record_type(**accepted).parse_status == "ACCEPTED"

    for field, replacement in (
        ("canonical_proposal_hash", "c" * 64),
        ("config_hash", "d" * 64),
        ("alpha_id", "alpha-forged"),
        (
            "canonical_proposal_json",
            '{"alpha_id":"alpha-1","factor_ids":["factor-a","factor-b"],'
            '"name":"Alpha One","params":{"lookback":"20","side":"long_short"},'
            '"universe":"crypto-liquid"}',
        ),
    ):
        with pytest.raises(ValidationError):
            record_type(**{**accepted, field: replacement})


@pytest.mark.parametrize("parse_status", ("INVALID", "SECRET_QUARANTINED"))
def test_terminal_nonaccepted_proposal_forbids_every_parsed_field(
    parse_status: str,
) -> None:
    record_type = _contract("CandidateProposalRecord")
    terminal = _terminal_proposal_payload(parse_status)
    assert record_type(**terminal).parse_status == parse_status

    accepted = _terminal_proposal_payload()
    for field in (
        "canonical_proposal_json",
        "canonical_proposal_hash",
        "config_hash",
        "alpha_id",
    ):
        with pytest.raises(ValidationError):
            record_type(**{**terminal, field: accepted[field]})


def test_terminal_proposal_status_domain_is_closed() -> None:
    with pytest.raises(ValidationError):
        _contract("CandidateProposalRecord")(
            **_terminal_proposal_payload("PENDING")
        )


def test_hypothesis_contract_is_one_shot_and_names_the_first_search() -> None:
    hypothesis_type = _contract("CandidateHypothesisRecord")
    payload = {
        "family_id": "family-1",
        "first_search_id": "search-1",
        "config_hash": "a" * 64,
        "alpha_id": "alpha-1",
        "canonical_hypothesis_hash": "b" * 64,
        "first_proposal_id": "c" * 64,
    }
    assert hypothesis_type(**payload).first_search_id == "search-1"
    with pytest.raises(ValidationError):
        hypothesis_type(**payload, accepted_proposal_count=2)


def _emission_seal_payload(
    *,
    declared: int = 3,
    raw: int = 2,
    candidates: int = 1,
    terminal_reason: str = "COMPLETED",
) -> dict[str, Any]:
    return {
        "family_id": "family-1",
        "search_id": "search-1",
        "declared_proposal_budget": declared,
        "actual_raw_proposal_count": raw,
        "ordered_proposal_ids_hash": "a" * 64,
        "ordered_non_quarantined_raw_hashes_hash": "b" * 64,
        "ordered_canonical_proposal_hashes_hash": "c" * 64,
        "ordered_quarantine_record_ids_hash": "d" * 64,
        "accepted_config_set_hash": "e" * 64,
        "candidate_count": candidates,
        "candidate_chain_head": "f" * 64,
        "terminal_reason": terminal_reason,
        "sealed_at_us": 1,
    }


def test_emission_seal_count_order_is_closed_including_zero_search() -> None:
    seal_type = _contract("CandidateEmissionSeal")
    zero = seal_type(**_emission_seal_payload(declared=1, raw=0, candidates=0))
    assert zero.actual_raw_proposal_count == zero.candidate_count == 0
    assert seal_type(**_emission_seal_payload(declared=3, raw=3, candidates=3))

    for raw, candidates in ((0, 1), (1, 2), (2, 3)):
        with pytest.raises(ValidationError):
            seal_type(
                **_emission_seal_payload(
                    declared=3,
                    raw=raw,
                    candidates=candidates,
                )
            )
    with pytest.raises(ValidationError):
        seal_type(**_emission_seal_payload(declared=1, raw=2, candidates=1))


@pytest.mark.parametrize(
    "terminal_reason",
    (
        "Bearer " + "x" * 48,
        "-----BEGIN PRIVATE KEY-----",
        "COMPLETED\nFORGED",
        "X" * 65,
    ),
)
def test_emission_seal_terminal_reason_is_a_secret_safe_public_token(
    terminal_reason: str,
) -> None:
    with pytest.raises(ValidationError):
        _contract("CandidateEmissionSeal")(
            **_emission_seal_payload(terminal_reason=terminal_reason)
        )


def _disposition_payload(
    disposition: str,
    reason_code: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "family_id": "family-1",
        "proposal_id": "a" * 64,
        "disposition": disposition,
        "config_hash": None,
        "evaluation_receipt_hash": None,
        "quarantine_record_id": None,
        "reason_code": reason_code,
        "performance_accessed": False,
    }
    if disposition == "PERFORMANCE_EVALUATED":
        payload["config_hash"] = "b" * 64
        payload["evaluation_receipt_hash"] = "c" * 64
        payload["performance_accessed"] = True
    elif disposition == "PERFORMANCE_ATTEMPTED_INCOMPLETE":
        payload["config_hash"] = "b" * 64
        payload["performance_accessed"] = True
    elif disposition == "SECRET_QUARANTINED":
        payload["quarantine_record_id"] = "qtn_" + "a" * 32
    return payload


def test_disposition_reason_domain_is_closed_and_branch_specific() -> None:
    reason_type = getattr(research_contracts, "CandidateDispositionReason")
    assert tuple(member.value for member in reason_type) == PUBLIC_DISPOSITION_REASONS
    disposition_type = _contract("CandidateDisposition")
    assert disposition_type(**_disposition_payload("PERFORMANCE_EVALUATED", None)).reason_code is None
    assert (
        disposition_type(
            **_disposition_payload(
                "PERFORMANCE_ATTEMPTED_INCOMPLETE",
                "EVALUATION_TIMEOUT",
            )
        ).reason_code
        == "EVALUATION_TIMEOUT"
    )
    assert (
        disposition_type(**_disposition_payload("PRE_EVALUATION_INVALID", "INVALID_UTF8")).reason_code == "INVALID_UTF8"
    )
    assert (
        disposition_type(**_disposition_payload("SECRET_QUARANTINED", "DLP_SECRET_DETECTED")).reason_code
        == "DLP_SECRET_DETECTED"
    )
    with pytest.raises(ValidationError):
        disposition_type(
            **_disposition_payload(
                "PRE_EVALUATION_INVALID",
                "EVALUATION_TIMEOUT",
            )
        )
    with pytest.raises(ValidationError):
        disposition_type(
            **_disposition_payload(
                "SECRET_QUARANTINED",
                "CUSTOM_" + "REASON",
            )
        )
    with pytest.raises(ValidationError):
        disposition_type(
            **_disposition_payload(
                "PERFORMANCE_EVALUATED",
                "EVALUATION_EXCEPTION",
            )
        )


def _manifest_entry_payload(
    *,
    object_format: str = "SHA1",
    oid: str = "a" * 40,
) -> dict[str, Any]:
    return {
        "repo_path": "services/research/example.py",
        "git_object_format": object_format,
        "git_blob_oid": oid,
        "blob_sha256": "b" * 64,
    }


def _manifest_payload(
    *,
    worktree_state: str = "CLEAN",
    dirty_tree_fingerprint: str | None = None,
) -> dict[str, Any]:
    entries = (_manifest_entry_payload(),)
    payload: dict[str, Any] = {
        "source_commit": "c" * 40,
        "worktree_state": worktree_state,
        "dirty_tree_fingerprint": dirty_tree_fingerprint,
        "policy_ref": {
            "policy_id": "CODE_MANIFEST_POLICY_V1",
            "policy_version": "1",
            "policy_hash": "d" * 64,
        },
        "ordered_entries": entries,
        "path_set_hash": canonical_sha256(
            tuple(entry["repo_path"] for entry in entries)
        ),
        "runtime_identity": "cpython-3.12",
        "dependency_lock_hash": "1" * 64,
    }
    payload["manifest_hash"] = canonical_sha256(payload)
    payload.update(
        {
        "authority_id": "code-manifest-authority",
        "authority_head": "2" * 64,
        }
    )
    payload["evidence_hash"] = canonical_sha256(payload)
    return payload


def test_code_manifest_entry_cross_checks_git_object_format_and_oid_length() -> None:
    entry_type = _contract("CodeManifestEntry")
    assert entry_type(**_manifest_entry_payload()).git_object_format == "SHA1"
    assert entry_type(**_manifest_entry_payload(object_format="SHA256", oid="a" * 64)).git_object_format == "SHA256"
    with pytest.raises(ValidationError):
        entry_type(**_manifest_entry_payload(object_format="SHA1", oid="a" * 64))
    with pytest.raises(ValidationError):
        entry_type(**_manifest_entry_payload(object_format="SHA256", oid="a" * 40))


def test_code_manifest_worktree_state_is_a_closed_consistent_branch() -> None:
    evidence_type = _contract("CodeManifestEvidence")
    clean = evidence_type(**_manifest_payload())
    dirty = evidence_type(
        **_manifest_payload(
            worktree_state="DIRTY",
            dirty_tree_fingerprint="4" * 64,
        )
    )
    assert clean.worktree_state == "CLEAN"
    assert dirty.worktree_state == "DIRTY"

    with pytest.raises(ValidationError):
        evidence_type(**_manifest_payload(worktree_state="DIRTY"))
    with pytest.raises(ValidationError):
        evidence_type(
            **_manifest_payload(
                worktree_state="CLEAN",
                dirty_tree_fingerprint="4" * 64,
            )
        )
    with pytest.raises(ValidationError):
        evidence_type(**_manifest_payload(worktree_state="MUTABLE"))


@pytest.mark.parametrize(
    "mutation",
    (
        {"path_set_hash": "e" * 64},
        {"manifest_hash": "f" * 64},
        {"evidence_hash": "3" * 64},
    ),
)
def test_code_manifest_rejects_detached_internal_hashes(
    mutation: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _contract("CodeManifestEvidence")(**{**_manifest_payload(), **mutation})


def _observed_pit_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "authority_id": "pit-authority",
        "handle_id": "pit-handle-1",
        "provenance_class": "OBSERVED_PIT",
        "dataset_id": "crypto-daily",
        "dataset_version": "v1",
        "raw_archive_digest": "5" * 64,
        "raw_archive_byte_length": 1024,
        "normalized_pit_store_digest": "6" * 64,
        "normalized_pit_store_version": "v1",
        "normalized_store_byte_length": 2048,
        "quality_admission_id": "quality-1",
        "quality_authority_head": "7" * 64,
        "universe_id": "crypto-observed",
        "universe_hash": "8" * 64,
        "ordered_period_calendar_hash": "9" * 64,
        "knowledge_time_policy_id": "PIT_KNOWLEDGE_TIME_V1",
        "knowledge_time_policy_version": "1",
        "label_horizon": "7d",
        "purge_periods": 1,
        "embargo_periods": 1,
        "cost_contract_hash": "a" * 64,
        "benchmark_contract_hash": "b" * 64,
        "content_hash": "c" * 64,
        "authority_head": "d" * 64,
    }
    payload.update(overrides)
    return payload


def test_observed_pit_handle_requires_observed_provenance_and_nonzero_bytes() -> None:
    handle_type = _contract("ObservedPitDatasetHandle")
    assert handle_type(**_observed_pit_payload()).provenance_class == "OBSERVED_PIT"
    with pytest.raises(ValidationError):
        handle_type(**_observed_pit_payload(provenance_class="SYNTHETIC"))
    with pytest.raises(ValidationError):
        handle_type(**_observed_pit_payload(raw_archive_byte_length=0))
    with pytest.raises(ValidationError):
        handle_type(**_observed_pit_payload(normalized_store_byte_length=0))


def _exposure_payload(**overrides: Any) -> dict[str, Any]:
    fingerprint_binding = {
        "observed_pit_content_hash": "c" * 64,
        "dataset_version": "v1",
        "universe_id": "crypto-observed",
        "universe_hash": "8" * 64,
        "ordered_period_calendar_hash": "9" * 64,
        "label_horizon": "7d",
        "purge_periods": 1,
        "embargo_periods": 1,
    }
    payload: dict[str, Any] = {
        "oos_exposure_id": "exposure-1",
        "selection_scope_id": "scope-1",
        "selection_decision_id": "decision-1",
        "family_id": "family-1",
        "exposure_ordinal": 1,
        "observed_pit_authority_id": "pit-authority",
        "observed_pit_handle_id": "pit-handle-1",
        **fingerprint_binding,
        "dataset_id": "crypto-daily",
        "knowledge_time_policy_id": "PIT_KNOWLEDGE_TIME_V1",
        "knowledge_time_policy_version": "1",
        "cost_contract_hash": "a" * 64,
        "benchmark_contract_hash": "b" * 64,
        "period_start_us": 1_700_000_000_000_000,
        "period_end_us": 1_710_000_000_000_000,
        "exposure_fingerprint": canonical_sha256(fingerprint_binding),
        "exposure_version": 1,
        "observed_pit_authority_head": "d" * 64,
    }
    payload.update(overrides)
    return payload


def test_oos_exposure_fingerprint_binds_dataset_universe_calendar_label_and_gaps() -> None:
    exposure_type = _contract("OosExposureRef")
    exposure = exposure_type(**_exposure_payload())
    assert exposure.exposure_fingerprint == _exposure_payload()["exposure_fingerprint"]

    for field, changed in (
        ("observed_pit_content_hash", "a" * 64),
        ("dataset_version", "v2"),
        ("universe_id", "crypto-observed-v2"),
        ("universe_hash", "e" * 64),
        ("ordered_period_calendar_hash", "f" * 64),
        ("label_horizon", "14d"),
        ("purge_periods", 2),
        ("embargo_periods", 2),
    ):
        with pytest.raises(ValidationError):
            exposure_type(**_exposure_payload(**{field: changed}))


def test_oos_exposure_fingerprint_excludes_identity_ordinal_period_and_authority() -> None:
    exposure_type = _contract("OosExposureRef")
    fingerprint = _exposure_payload()["exposure_fingerprint"]
    for field, changed in (
        ("oos_exposure_id", "exposure-2"),
        ("selection_scope_id", "scope-2"),
        ("selection_decision_id", "decision-2"),
        ("family_id", "family-2"),
        ("exposure_ordinal", 2),
        ("observed_pit_handle_id", "pit-handle-2"),
        ("dataset_id", "renamed-crypto-daily"),
        ("period_start_us", 1_700_000_000_000_001),
        ("period_end_us", 1_710_000_000_000_001),
        ("exposure_version", 2),
        ("observed_pit_authority_id", "pit-authority-2"),
        ("observed_pit_authority_head", "b" * 64),
    ):
        exposure = exposure_type(**_exposure_payload(**{field: changed}))
        assert exposure.exposure_fingerprint == fingerprint


@pytest.mark.parametrize(
    ("period_start_us", "period_end_us"),
    (
        (1_700_000_000_000_000, 1_700_000_000_000_000),
        (1_700_000_000_000_001, 1_700_000_000_000_000),
    ),
)
def test_oos_exposure_requires_strictly_increasing_period(
    period_start_us: int,
    period_end_us: int,
) -> None:
    exposure_type = _contract("OosExposureRef")
    with pytest.raises(ValidationError):
        exposure_type(
            **_exposure_payload(
                period_start_us=period_start_us,
                period_end_us=period_end_us,
            )
        )


@pytest.mark.parametrize(
    "name",
    (
        "CodeManifestEvidence",
        "ExecutionCodeAttestation",
        "ImmutableArtifactLease",
        "ObservedPitDatasetHandle",
        "OosExposureRef",
        "OosExposureConsumption",
    ),
)
def test_external_reference_contracts_require_identity_content_policy_and_head(
    name: str,
) -> None:
    fields = set(_contract(name).model_fields)
    assert any(field.endswith("id") for field in fields)
    assert any("hash" in field or "digest" in field for field in fields)
    assert any(field.endswith("authority_head") for field in fields)
    assert "mutable" not in fields
    assert "is_live" not in fields
