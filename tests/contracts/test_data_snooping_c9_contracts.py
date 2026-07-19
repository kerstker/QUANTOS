"""RED contracts for exact-v3 Pass C slice C9.

C9 freezes the acyclic, exact-byte publication value graph.  These tests are
deliberately contract-only: no store, exporter, scanner process, network, or
lifecycle service is started.  Contract lookup is dynamic so the suite remains
collectable while the six C9 production contracts are still absent.
"""

from __future__ import annotations

import base64
from functools import lru_cache
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

import quantos.contracts.research as research_contracts
from quantos.contracts.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    float64_hex,
    sha256_bytes,
)
from quantos.contracts.hashing import stable_hash
from tools import search_trial_ledger_history as trial_history


ENVELOPE_ROOT = "evidence/data_snooping"
PROJECTION_ROOT = f"{ENVELOPE_ROOT}/publication-receipts"

C9_CONTRACT_FIELDS = {
    "ArtifactPublicationProjectionRef": {
        "record_type",
        "projection_schema_version",
        "authority_instance_id",
        "genesis_record_hash",
        "authority_lineage_hash",
        "family_id",
        "decision_class",
        "assessment_status",
        "artifact_publication_receipt_id",
        "artifact_publication_receipt_hash",
        "publication_projection_hash",
        "publication_projection_path",
        "publication_chain_head",
    },
    "DataSnoopingEvidenceRef": {
        "record_type",
        "artifact_kind",
        "artifact_schema_version",
        "authority_instance_id",
        "genesis_record_hash",
        "authority_lineage_hash",
        "family_id",
        "selection_scope_id",
        "selection_decision_id",
        "decision_class",
        "winner_config_hash",
        "closure_manifest_hash",
        "family_assessment_conclusion_hash",
        "assessment_status",
        "diagnostic_verdict",
        "promotion_effect",
        "published_artifact_envelope_hash",
        "published_artifact_path",
        "artifact_publication_receipt_id",
        "artifact_publication_receipt_hash",
        "publication_projection_hash",
        "publication_projection_path",
        "publication_chain_head",
    },
    "ArtifactCore": {
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
    },
    "ArtifactCoreScanReceipt": {
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
    },
    "PublishedArtifactEnvelope": {
        "record_type",
        "envelope_schema_version",
        "artifact_core",
        "artifact_core_scan_receipt",
    },
    "ArtifactPublicationReceipt": {
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
        "diagnostic_verdict",
        "decision_class",
        "artifact_kind",
        "artifact_core_hash",
        "artifact_core_scan_receipt_hash",
        "published_artifact_envelope_hash",
        "target_content_addressed_path",
        "assessment_status",
        "promotion_effect",
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
        "failure_reasons_json",
        "published_at_us",
        "receipt_record_hash",
    },
    "ArtifactPublicationReceiptProjection": {
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
    },
}


def _contract(name: str) -> type[Any]:
    contract = getattr(research_contracts, name, None)
    assert contract is not None, f"Pass C C9 contract is missing: {name}"
    return contract


def _lineage_hash(authority_instance_id: str, genesis_record_hash: str) -> str:
    return canonical_sha256(
        {
            "authority_instance_id": authority_instance_id,
            "genesis_record_hash": genesis_record_hash,
        }
    )


def _content_path(root: str, digest: str) -> str:
    return f"{root}/sha256-{digest}.json"


def _scan_result_hash(
    *,
    scan_target: str,
    target_sha256: str,
    target_byte_length: int,
    scan_fields: dict[str, Any],
) -> str:
    return canonical_sha256(
        {
            "scan_target": scan_target,
            "target_sha256": target_sha256,
            "target_byte_length": target_byte_length,
            **scan_fields,
        }
    )


def _canonical_json_text(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8").removesuffix("\n")


def _rehash(payload: dict[str, Any], hash_field: str) -> dict[str, Any]:
    result = dict(payload)
    result.pop(hash_field, None)
    result[hash_field] = canonical_sha256(result)
    return result


@pytest.mark.parametrize(("name", "fields"), C9_CONTRACT_FIELDS.items())
def test_c9_contracts_are_present_frozen_strict_and_exact(
    name: str,
    fields: set[str],
) -> None:
    contract = _contract(name)
    assert set(contract.model_fields) == fields
    assert contract.model_config["frozen"] is True
    assert contract.model_config["extra"] == "forbid"
    assert contract.model_config["strict"] is True


def _periods() -> tuple[Any, ...]:
    period_key = _contract("PeriodKey")
    return tuple(
        period_key(
            event_time=1_700_000_000_000_000 + ordinal * 86_400_000_000,
            frequency_id="P1D_UTC",
            label_horizon="P1D",
        )
        for ordinal in range(20)
    )


def _sidecar_scan_receipt(
    *,
    sidecar_kind: str,
    target_sha256: str,
    target_byte_length: int,
) -> dict[str, Any]:
    scan_fields = {
        "public_artifact_scan_policy_id": "PUBLIC_ARTIFACT_SCAN_V1",
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "dlp_policy_hash": "d" * 64,
        "scanner_implementation": "FAIL_CLOSED_SCANNER",
        "scanner_version": "1",
        "scan_status": "ALLOW",
    }
    payload: dict[str, Any] = {
        "record_type": "EXACT_BYTE_SIDECAR_SCAN_RECEIPT_V1",
        "sidecar_kind": sidecar_kind,
        "target_sha256": target_sha256,
        "target_byte_length": target_byte_length,
        **scan_fields,
        "scan_result_hash": _scan_result_hash(
            scan_target=sidecar_kind,
            target_sha256=target_sha256,
            target_byte_length=target_byte_length,
            scan_fields=scan_fields,
        ),
        "scanned_at_us": 1_700_450_000_000_000,
    }
    return _rehash(payload, "scan_receipt_hash")


def _typed_evidence(
    contract_type: str,
    payload: dict[str, Any],
    *,
    provider_subject_context: dict[str, Any] | None = None,
    provider_authority_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload_bytes = canonical_json_bytes(payload)
    digest = sha256_bytes(payload_bytes)
    result = {
        "contract_type": contract_type,
        "canonical_payload": payload,
        "canonical_payload_sha256": digest,
        "canonical_payload_byte_length": len(payload_bytes),
        "content_addressed_path": (
            f"{ENVELOPE_ROOT}/sidecars/typed-{contract_type.casefold()}/"
            f"sha256-{digest}.json"
        ),
        "scan_receipt": _sidecar_scan_receipt(
            sidecar_kind="CANONICAL_TYPED_EVIDENCE",
            target_sha256=digest,
            target_byte_length=len(payload_bytes),
        ),
    }
    if provider_subject_context is not None:
        result["provider_subject_context"] = provider_subject_context
    if provider_authority_projection is not None:
        result["provider_authority_projection"] = provider_authority_projection
    return result


_PROVIDER_AUTHORITY = {
    "ObservedPitDatasetHandle": (
        "pit-authority-1",
        "OBSERVED_PIT_DATA_AUTHORITY",
        "OBSERVED_PIT_PROVIDER_POLICY_V1",
        "1",
        "1" * 64,
    ),
    "CodeManifestEvidence": (
        "code-authority-1",
        "CODE_MANIFEST_AUTHORITY",
        "CODE_MANIFEST_PROVIDER_POLICY_V1",
        "1",
        "2" * 64,
    ),
    "ExecutionCodeAttestation": (
        "execution-authority-1",
        "EXECUTION_ATTESTATION_AUTHORITY",
        "EXECUTION_ATTESTATION_POLICY_V1",
        "1",
        "3" * 64,
    ),
    "ImmutableArtifactLease": (
        "lease-authority-1",
        "IMMUTABLE_ARTIFACT_LEASE_AUTHORITY",
        "IMMUTABLE_LEASE_POLICY_V1",
        "1",
        "4" * 64,
    ),
}


def _provider_genesis(contract_type: str) -> dict[str, Any]:
    authority_id, authority_kind, policy_id, policy_version, binding_hash = (
        _PROVIDER_AUTHORITY[contract_type]
    )
    payload: dict[str, Any] = {
        "record_type": "EXTERNAL_PROVIDER_AUTHORITY_GENESIS_V1",
        "authority_id": authority_id,
        "provider_contract_type": contract_type,
        "provider_authority_kind": authority_kind,
        "provider_policy_id": policy_id,
        "provider_policy_version": policy_version,
        "provider_instance_binding_hash": binding_hash,
        "activated_at_us": 1_699_000_000_000_000,
    }
    return _rehash(payload, "record_hash")


def _provider_projection(
    contract_type: str,
    events: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    genesis = _provider_genesis(contract_type)
    authority_id = genesis["authority_id"]
    previous_hash = genesis["record_hash"]
    ordered_events: list[dict[str, Any]] = []
    for ordinal, event_spec in enumerate(events, start=1):
        event_payload: dict[str, Any] = {
            "record_type": "EXTERNAL_PROVIDER_AUTHORITY_EVENT_V1",
            "authority_id": authority_id,
            "event_ordinal": ordinal,
            "subject_contract_type": contract_type,
            "subject_sha256": canonical_sha256(
                {
                    "contract_type": contract_type,
                    "stable_provider_payload": event_spec["stable_provider_payload"],
                    "provider_subject_context": event_spec["provider_subject_context"],
                }
            ),
            "event_at_us": event_spec["event_at_us"],
            "previous_record_hash": previous_hash,
        }
        event_payload = _rehash(event_payload, "record_hash")
        ordered_events.append(event_payload)
        previous_hash = event_payload["record_hash"]
    projection_payload: dict[str, Any] = {
        "record_type": "EXTERNAL_PROVIDER_AUTHORITY_PROJECTION_V1",
        "provider_contract_type": contract_type,
        "authority_id": authority_id,
        "authority_genesis": genesis,
        "genesis_record_hash": genesis["record_hash"],
        "ordered_events": tuple(ordered_events),
        "authority_head": previous_hash,
    }
    projection_bytes = canonical_json_bytes(projection_payload)
    digest = sha256_bytes(projection_bytes)
    projection_payload.update(
        {
            "canonical_projection_sha256": digest,
            "canonical_projection_byte_length": len(projection_bytes),
            "content_addressed_path": (
                f"{ENVELOPE_ROOT}/sidecars/provider-{contract_type.casefold()}/"
                f"sha256-{digest}.json"
            ),
            "scan_receipt": _sidecar_scan_receipt(
                sidecar_kind="PROVIDER_AUTHORITY_PROJECTION",
                target_sha256=digest,
                target_byte_length=len(projection_bytes),
            ),
        }
    )
    return projection_payload


def _provider_evidence_chain(
    contract_type: str,
    subjects: tuple[tuple[dict[str, Any], dict[str, Any], int], ...],
) -> tuple[dict[str, Any], ...]:
    self_hash_field = {
        "CodeManifestEvidence": "evidence_hash",
        "ExecutionCodeAttestation": "attestation_hash",
        "ImmutableArtifactLease": "lease_hash",
    }.get(contract_type)
    evidence: list[dict[str, Any]] = []
    event_specs: list[dict[str, Any]] = []
    for stable_payload, context, event_at_us in subjects:
        event_specs.append(
            {
                "stable_provider_payload": stable_payload,
                "provider_subject_context": context,
                "event_at_us": event_at_us,
            }
        )
        projection = _provider_projection(contract_type, tuple(event_specs))
        canonical_payload = {
            **stable_payload,
            "authority_id": projection["authority_id"],
            "authority_head": projection["authority_head"],
        }
        if self_hash_field is not None:
            canonical_payload[self_hash_field] = canonical_sha256(canonical_payload)
        evidence.append(
            _typed_evidence(
                contract_type,
                canonical_payload,
                provider_subject_context=context,
                provider_authority_projection=projection,
            )
        )
    return tuple(evidence)


def _observed_pit_evidence() -> dict[str, Any]:
    stable_payload = {
        "handle_id": "pit-handle-1",
        "provenance_class": "OBSERVED_PIT",
        "dataset_id": "crypto-daily",
        "dataset_version": "v1",
        "raw_archive_digest": "5" * 64,
        "raw_archive_byte_length": 4096,
        "normalized_pit_store_digest": "6" * 64,
        "normalized_pit_store_version": "v1",
        "normalized_store_byte_length": 8192,
        "quality_admission_id": "quality-1",
        "quality_authority_head": "7" * 64,
        "universe_id": "large-cap",
        "universe_hash": "8" * 64,
        "ordered_period_calendar_hash": "9" * 64,
        "knowledge_time_policy_id": "PIT_EOD_V1",
        "knowledge_time_policy_version": "1",
        "label_horizon": "P1D",
        "purge_periods": 1,
        "embargo_periods": 1,
        "cost_contract_hash": "d" * 64,
        "benchmark_contract_hash": "f" * 64,
        "content_hash": "2" * 64,
    }
    return _provider_evidence_chain(
        "ObservedPitDatasetHandle",
        (
            (
                stable_payload,
                {
                    "context_type": "OBSERVED_PIT_DATASET_HANDLE_CONTEXT_V1",
                    "family_id": "family-1",
                },
                1_699_100_000_000_000,
            ),
        ),
    )[0]


def _code_manifest_stable_payload() -> dict[str, Any]:
    entries = (
        {
            "repo_path": "services/research/experiment_registry/family_schema_c9.py",
            "git_object_format": "SHA1",
            "git_blob_oid": "a" * 40,
            "blob_sha256": "a" * 64,
        },
    )
    payload: dict[str, Any] = {
        "source_commit": "b" * 40,
        "worktree_state": "CLEAN",
        "dirty_tree_fingerprint": None,
        "policy_ref": {
            "policy_id": "CODE_MANIFEST_POLICY_V1",
            "policy_version": "1",
            "policy_hash": "6" * 64,
        },
        "ordered_entries": entries,
        "path_set_hash": canonical_sha256(tuple(entry["repo_path"] for entry in entries)),
        "runtime_identity": "python-3.12-quantos",
        "dependency_lock_hash": "c" * 64,
    }
    payload["manifest_hash"] = canonical_sha256(payload)
    return payload


def _code_manifest_evidence_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    stable_payload = _code_manifest_stable_payload()
    return _provider_evidence_chain(
        "CodeManifestEvidence",
        (
            (
                stable_payload,
                {
                    "context_type": "CODE_MANIFEST_AUTHORITY_ACTIVATION_CONTEXT_V1",
                    "authority_instance_id": "family-authority-1",
                    "sealed_v3_manifest_hash": sha256_bytes(_schema_manifest_bytes()),
                    "migration_id": "FAMILY_SCHEMA_V2_TO_V3_V1",
                },
                1_699_200_000_000_000,
            ),
            (
                stable_payload,
                {
                    "context_type": "CODE_MANIFEST_RESEARCH_EXECUTION_CONTEXT_V1",
                    "family_id": "family-1",
                },
                1_699_300_000_000_000,
            ),
        ),
    )


def _execution_and_lease_evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    code_payload = _code_manifest_evidence_pair()[1]["canonical_payload"]
    common = {
        "code_manifest_hash": code_payload["manifest_hash"],
        "source_commit": code_payload["source_commit"],
        "runtime_identity": code_payload["runtime_identity"],
        "dependency_lock_hash": code_payload["dependency_lock_hash"],
        "immutable_artifact_id": "artifact-1",
        "immutable_artifact_digest": "e" * 64,
        "entrypoint": "quantos.research:run",
        "process_image_hash": "f" * 64,
        "lease_id": "lease-1",
    }
    start_stable = {**common, "issued_at_us": 1_700_200_000_000_000}
    terminal_stable = {**common, "issued_at_us": 1_700_250_000_000_000}
    start, terminal = _provider_evidence_chain(
        "ExecutionCodeAttestation",
        (
            (
                start_stable,
                {
                    "context_type": "EXECUTION_CODE_ATTESTATION_CONTEXT_V1",
                    "family_id": "family-1",
                    "role": "START",
                    "attempt_id": "attempt-1",
                    "evaluation_batch_id": "batch-1",
                },
                1_700_200_000_000_000,
            ),
            (
                terminal_stable,
                {
                    "context_type": "EXECUTION_CODE_ATTESTATION_CONTEXT_V1",
                    "family_id": "family-1",
                    "role": "TERMINAL",
                    "attempt_id": "attempt-1",
                    "evaluation_batch_id": "batch-1",
                },
                1_700_250_000_000_000,
            ),
        ),
    )
    lease = _provider_evidence_chain(
        "ImmutableArtifactLease",
        (
            (
                {
                    "lease_id": "lease-1",
                    "artifact_id": common["immutable_artifact_id"],
                    "artifact_digest": common["immutable_artifact_digest"],
                    "code_manifest_hash": common["code_manifest_hash"],
                    "process_image_hash": common["process_image_hash"],
                    "acquired_at_us": 1_700_150_000_000_000,
                    "expires_at_us": 1_700_700_000_000_000,
                },
                {
                    "context_type": "IMMUTABLE_ARTIFACT_LEASE_CONTEXT_V1",
                    "family_id": "family-1",
                    "attempt_id": "attempt-1",
                    "evaluation_batch_id": "batch-1",
                },
                1_700_150_000_000_000,
            ),
        ),
    )[0]
    return start, terminal, lease


_SIDECAR_MEDIA_TYPES = {
    "FAMILY_SCHEMA_V3_MANIFEST": "application/vnd.quantos.family-schema-manifest+json",
    "TRIAL_LEDGER_HISTORY": "application/vnd.quantos.trial-ledger-history+json",
    "TRIAL_LEDGER_SNAPSHOT_SQLITE": "application/vnd.sqlite3",
    "TRIAL_LEDGER_SEAL": "application/vnd.quantos.trial-ledger-seal+json",
}


def _exact_byte_sidecar(sidecar_kind: str, content: bytes) -> dict[str, Any]:
    digest = sha256_bytes(content)
    return {
        "record_type": "EXACT_BYTE_SIDECAR_V1",
        "sidecar_kind": sidecar_kind,
        "transport_encoding": "BASE64_STANDARD_V1",
        "media_type": _SIDECAR_MEDIA_TYPES[sidecar_kind],
        "content_addressed_path": (
            f"{ENVELOPE_ROOT}/sidecars/{sidecar_kind.casefold()}/"
            f"sha256-{digest}.bin"
        ),
        "decoded_byte_length": len(content),
        "decoded_sha256": digest,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "scan_receipt": _sidecar_scan_receipt(
            sidecar_kind=sidecar_kind,
            target_sha256=digest,
            target_byte_length=len(content),
        ),
    }


def _schema_manifest_bytes() -> bytes:
    return canonical_json_bytes(
        {
            "manifest_schema_version": "FAMILY_SCHEMA_V3_MANIFEST_V1",
            "component_order": ("C4", "C5", "C6", "C7", "C8", "C9"),
            "authority_table_count": 33,
            "external_provider_contract_types": tuple(sorted(_PROVIDER_AUTHORITY)),
            "external_provider_proof_schema_version": (
                "EXTERNAL_PROVIDER_AUTHORITY_PROJECTION_V1"
            ),
            "semantic_verifier_registry_version": (
                "FAMILY_SCHEMA_V3_SEMANTIC_VERIFIER_REGISTRY_V1"
            ),
        }
    )


def _selection_scope_evidence() -> dict[str, Any]:
    mandate = {
        "mandate_schema_version": "DEPLOYMENT_MANDATE_V1",
        "portfolio_sleeve": "research-only",
        "capital_authority": "none-l2",
        "risk_budget_authority": "research-risk-v1",
        "market_domain": "crypto",
        "universe_domain": "large-cap",
        "label_definition": "next-period-net-return",
        "evaluation_horizon": "P1D",
        "benchmark_id": "cash",
        "cost_policy_class": "conservative-v1",
    }
    return _typed_evidence(
        "SelectionScopeRef",
        {
            "selection_scope_id": "scope-1",
            "deployment_mandate_hash": canonical_sha256(mandate),
            "deployment_mandate": mandate,
        },
    )


def _selection_decision_evidence(decision_class: str = "PUBLISHABLE") -> dict[str, Any]:
    return _typed_evidence(
        "SelectionDecisionRef",
        {
            "selection_decision_id": "decision-1",
            "selection_scope_id": "scope-1",
            "decision_ordinal": 1,
            "family_id": "family-1",
            "decision_class": decision_class,
        },
    )


@lru_cache(maxsize=1)
def _portable_trial_ledger_artifacts() -> tuple[bytes, bytes, bytes, str]:
    ledger_path = "data/cache/experiment_registry.db"
    history_path = "evidence/alpha_trial_ledger_history.json"
    snapshot_path = "evidence/alpha_trial_ledger_snapshot.db"
    search_id = "search-1"
    dataset_version = "dataset-v1"
    candidates = ("6" * 64, "7" * 64)

    evaluations: list[dict[str, Any]] = []
    evaluation_rows: list[tuple[object, ...]] = []
    for ordinal in range(1, 3):
        evaluation = {
            "alpha_id": f"alpha-{ordinal}",
            "catalog_trial_count": ordinal,
            "reproducibility_hash": f"repro-{ordinal}",
            "verdict": "REQUIRES_HUMAN_REVIEW",
        }
        run_id = f"run-{ordinal}"
        content_hash = stable_hash(
            {"evaluation": evaluation, "dataset_version": dataset_version}
        )
        recorded_at = 12 + ordinal
        evaluations.append(
            {
                "run_id": run_id,
                "alpha_id": evaluation["alpha_id"],
                "dataset_version": dataset_version,
                "recorded_ts": recorded_at,
                "content_hash": content_hash,
            }
        )
        evaluation_rows.append(
            (
                run_id,
                evaluation["alpha_id"],
                dataset_version,
                recorded_at,
                evaluation["verdict"],
                content_hash,
                json.dumps(evaluation, sort_keys=True),
            )
        )

    started_event: dict[str, Any] = {
        "search_id": search_id,
        "search_kind": "TEST_SELECTION_SEARCH",
        "source": "tests.contracts.test_data_snooping_c9_contracts",
        "dataset_version": dataset_version,
        "status": "STARTED",
        "recorded_at": 10,
        "prev_event_hash": "",
    }
    started_event["event_hash"] = trial_history._search_event_hash(started_event)
    terminal_event = {
        **started_event,
        "status": "COMPLETED",
        "recorded_at": 20,
        "prev_event_hash": started_event["event_hash"],
    }
    terminal_event["event_hash"] = trial_history._search_event_hash(terminal_event)

    candidate_rows: list[dict[str, Any]] = []
    candidate_head = ""
    for ordinal, config_hash in enumerate(candidates, start=1):
        row: dict[str, Any] = {
            "search_id": search_id,
            "config_hash": config_hash,
            "status": "REGISTERED",
            "registered_at": 10 + ordinal,
            "prev_candidate_hash": candidate_head,
        }
        row["candidate_hash"] = trial_history._search_candidate_hash(row)
        candidate_rows.append(row)
        candidate_head = row["candidate_hash"]

    evaluation_links: list[dict[str, Any]] = []
    evaluation_link_rows: list[dict[str, Any]] = []
    link_head = ""
    for ordinal, (config_hash, evaluation) in enumerate(
        zip(candidates, evaluations),
        start=1,
    ):
        row = {
            "search_id": search_id,
            "config_hash": config_hash,
            "run_id": evaluation["run_id"],
            "recorded_at": 12 + ordinal,
            "evaluation_content_hash": evaluation["content_hash"],
            "prev_link_hash": link_head,
        }
        row["link_hash"] = trial_history._search_evaluation_hash(row)
        evaluation_link_rows.append(row)
        evaluation_links.append(
            {
                "config_hash": config_hash,
                "run_id": evaluation["run_id"],
                "recorded_at": row["recorded_at"],
                "content_hash": evaluation["content_hash"],
                "link_hash": row["link_hash"],
            }
        )
        link_head = row["link_hash"]

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(trial_history._PORTABLE_SCHEMA)
        connection.executemany(
            "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
            tuple((config_hash, 10 + ordinal) for ordinal, config_hash in enumerate(candidates)),
        )
        connection.executemany(
            "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)",
            evaluation_rows,
        )
        for event in (started_event, terminal_event):
            connection.execute(
                "INSERT INTO search_attempt_events("
                "search_id,search_kind,source,dataset_version,status,recorded_at,"
                "prev_event_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
                tuple(
                    event[name]
                    for name in (
                        "search_id",
                        "search_kind",
                        "source",
                        "dataset_version",
                        "status",
                        "recorded_at",
                        "prev_event_hash",
                        "event_hash",
                    )
                ),
            )
        for row in candidate_rows:
            connection.execute(
                "INSERT INTO search_candidates("
                "search_id,config_hash,status,registered_at,prev_candidate_hash,"
                "candidate_hash) VALUES (?,?,?,?,?,?)",
                tuple(
                    row[name]
                    for name in (
                        "search_id",
                        "config_hash",
                        "status",
                        "registered_at",
                        "prev_candidate_hash",
                        "candidate_hash",
                    )
                ),
            )
        for row in evaluation_link_rows:
            connection.execute(
                "INSERT INTO search_evaluations("
                "search_id,config_hash,run_id,recorded_at,evaluation_content_hash,"
                "prev_link_hash,link_hash) VALUES (?,?,?,?,?,?,?)",
                (
                    search_id,
                    row["config_hash"],
                    row["run_id"],
                    row["recorded_at"],
                    row["evaluation_content_hash"],
                    row["prev_link_hash"],
                    row["link_hash"],
                ),
            )
        connection.execute(
            f"PRAGMA user_version={trial_history.LEDGER_CONTRACT_VERSION}"
        )
        connection.commit()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert trial_history._portable_tables(connection) == (
            trial_history._PORTABLE_ALLOWED_TABLES
        )
        trial_history._validate_portable_append_only_triggers(connection)
        snapshot_bytes = connection.serialize()
    finally:
        connection.close()

    provenance_links = sorted(evaluation_links, key=lambda item: item["run_id"])
    record: dict[str, Any] = {
        "sequence": 1,
        "session_id": search_id,
        "recorded_at_ms": 30,
        "session_provenance": {
            "search_kind": started_event["search_kind"],
            "source": started_event["source"],
            "dataset_version": dataset_version,
            "started_at": started_event["recorded_at"],
            "completed_at": terminal_event["recorded_at"],
            "terminal_event_hash": terminal_event["event_hash"],
            "candidates": list(candidates),
            "evaluation_links": provenance_links,
        },
        "trials": list(candidates),
        "evaluations": evaluations,
        "previous_record_hash": "",
    }
    record["record_hash"] = trial_history._record_hash(record)
    history_document = {
        "schema_version": trial_history.HISTORY_SCHEMA_VERSION,
        "mode": trial_history.HISTORY_MODE,
        "ledger_contract_version": trial_history.LEDGER_CONTRACT_VERSION,
        "ledger_path": ledger_path,
        "records": [record],
    }
    trial_history._validate_history_document(
        history_document,
        ledger_path_label=ledger_path,
    )
    history_bytes = canonical_json_bytes(history_document)
    seal_document = {
        "schema_version": trial_history.PORTABLE_SEAL_SCHEMA_VERSION,
        "mode": trial_history.PORTABLE_SEAL_MODE,
        "ledger_path": ledger_path,
        "history_path": history_path,
        "snapshot_path": snapshot_path,
        "history_sha256": sha256_bytes(history_bytes),
        "snapshot_sha256": sha256_bytes(snapshot_bytes),
        "history_head_hash": record["record_hash"],
        "record_count": 1,
        "trial_count": len(candidates),
        "evaluation_count": len(evaluations),
        "sealed_at_ms": 30,
    }
    trial_history._validate_seal_document(
        seal_document,
        ledger_path_label=ledger_path,
        history_path_label=history_path,
        snapshot_path_label=snapshot_path,
    )
    return (
        history_bytes,
        snapshot_bytes,
        canonical_json_bytes(seal_document),
        record["record_hash"],
    )


def _trial_ledger_bundle() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    history_bytes, snapshot_bytes, seal_bytes, history_head = (
        _portable_trial_ledger_artifacts()
    )
    history_sidecar = _exact_byte_sidecar("TRIAL_LEDGER_HISTORY", history_bytes)
    snapshot_sidecar = _exact_byte_sidecar(
        "TRIAL_LEDGER_SNAPSHOT_SQLITE",
        snapshot_bytes,
    )
    seal_sidecar = _exact_byte_sidecar("TRIAL_LEDGER_SEAL", seal_bytes)
    payload: dict[str, Any] = {
        "trial_ledger_authority_id": "trial-ledger-1",
        "history_artifact_id": f"sha256:{history_sidecar['decoded_sha256']}",
        "history_artifact_sha256": history_sidecar["decoded_sha256"],
        "history_head": history_head,
        "snapshot_artifact_id": f"sha256:{snapshot_sidecar['decoded_sha256']}",
        "snapshot_artifact_sha256": snapshot_sidecar["decoded_sha256"],
        "seal_artifact_id": f"sha256:{seal_sidecar['decoded_sha256']}",
        "seal_artifact_sha256": seal_sidecar["decoded_sha256"],
        "global_unique_config_count": 2,
        "captured_at_us": 1_700_400_000_000_000,
    }
    payload["proof_hash"] = canonical_sha256(payload)
    return (
        _typed_evidence("TrialLedgerProof", payload),
        history_sidecar,
        snapshot_sidecar,
        seal_sidecar,
    )


def _trial_ledger_proof_evidence() -> dict[str, Any]:
    return _trial_ledger_bundle()[0]


def test_trial_ledger_bundle_uses_a_real_portable_sqlite_snapshot() -> None:
    history_bytes, snapshot_bytes, seal_bytes, history_head = (
        _portable_trial_ledger_artifacts()
    )
    assert snapshot_bytes.startswith(b"SQLite format 3\x00")

    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(snapshot_bytes)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            trial_history.LEDGER_CONTRACT_VERSION
        )
        assert trial_history._portable_tables(connection) == (
            trial_history._PORTABLE_ALLOWED_TABLES
        )
        trial_history._validate_portable_append_only_triggers(connection)
    finally:
        connection.close()

    history_document = json.loads(history_bytes)
    assert (
        trial_history._validate_history_document(
            history_document,
            ledger_path_label="data/cache/experiment_registry.db",
        )["records"][-1]["record_hash"]
        == history_head
    )
    seal_document = json.loads(seal_bytes)
    trial_history._validate_seal_document(
        seal_document,
        ledger_path_label="data/cache/experiment_registry.db",
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
    )
    assert seal_document["history_sha256"] == sha256_bytes(history_bytes)
    assert seal_document["snapshot_sha256"] == sha256_bytes(snapshot_bytes)
    assert seal_document["history_head_hash"] == history_head


def test_trial_ledger_bundle_passes_the_production_bundle_verifier(
    tmp_path: Path,
) -> None:
    history_bytes, snapshot_bytes, seal_bytes, history_head = (
        _portable_trial_ledger_artifacts()
    )
    history_path = tmp_path / "history.json"
    snapshot_path = tmp_path / "snapshot.db"
    seal_path = tmp_path / "seal.json"
    history_path.write_bytes(history_bytes)
    snapshot_path.write_bytes(snapshot_bytes)
    seal_path.write_bytes(seal_bytes)

    verified = trial_history.check_snapshot(
        history_path=history_path,
        snapshot_path=snapshot_path,
        seal_path=seal_path,
        ledger_path_label="data/cache/experiment_registry.db",
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
    )
    assert verified["history_head_hash"] == history_head
    assert verified["trial_count"] == 2
    assert verified["evaluation_count"] == 2


def test_schema_manifest_is_deployment_neutral_and_future_extensible() -> None:
    validator = getattr(
        importlib.import_module("quantos.contracts.data_snooping_c9"),
        "_validate_deployment_neutral_schema_manifest",
    )
    manifest = json.loads(_schema_manifest_bytes())
    validator(
        {
            **manifest,
            "renderer_registry_version": "FAMILY_SCHEMA_V3_RENDERER_REGISTRY_V1",
            "traceability_catalog": {"catalog_sha256": "a" * 64},
        }
    )

    with pytest.raises(ValueError, match="deployment neutral"):
        validator({**manifest, "trust": {"provider_trust_roots": ["f" * 64]}})
    with pytest.raises(ValueError, match="frozen deployment-neutral fields"):
        validator({**manifest, "authority_table_count": 32})


def _series_payload(
    periods: tuple[Any, ...],
    gross: tuple[str, ...],
    costs: tuple[str, ...],
    net: tuple[str, ...],
    turnovers: tuple[str, ...],
) -> dict[str, Any]:
    period_payloads = tuple(period.model_dump(mode="python") for period in periods)
    payload: dict[str, Any] = {
        "ordered_period_keys": period_payloads,
        "gross_returns": gross,
        "costs": costs,
        "net_returns": net,
        "turnovers": turnovers,
        "ordered_period_keys_hash": canonical_sha256(period_payloads),
        "gross_returns_hash": canonical_sha256(gross),
        "costs_hash": canonical_sha256(costs),
        "net_returns_hash": canonical_sha256(net),
        "turnovers_hash": canonical_sha256(turnovers),
        "cost_contract_hash": "d" * 64,
        "cost_model_input_hash": "e" * 64,
    }
    payload["series_hash"] = canonical_sha256(payload)
    return payload


def _candidate_receipt_evidence(
    *,
    config_hash: str,
    proposal_id: str,
    ordinal: int,
    series: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "family_id": "family-1",
        "evaluation_batch_id": "batch-1",
        "config_hash": config_hash,
        "alpha_id": f"alpha-{ordinal}",
        "evaluation_run_id": f"evaluation-{ordinal}",
        "evaluation_content_hash": str(ordinal) * 64,
        "evaluation_audit_intent_hash": str(ordinal + 2) * 64,
        "source_search_id": "search-1",
        "source_proposal_id": proposal_id,
        "series": series,
    }
    payload["evaluation_receipt_hash"] = canonical_sha256(payload)
    return _typed_evidence("CandidateReturnSeries", payload)


def _benchmark_receipt_evidence(series: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "family_id": "family-1",
        "evaluation_batch_id": "batch-1",
        "benchmark_id": "cash",
        "benchmark_contract_hash": "f" * 64,
        "series": series,
    }
    payload["benchmark_receipt_hash"] = canonical_sha256(payload)
    return _typed_evidence("BenchmarkReturnSeries", payload)


def _batch_evidence(
    *,
    periods: tuple[Any, ...],
    candidate_evidence: tuple[dict[str, Any], ...],
    benchmark_evidence: dict[str, Any],
    start_execution_attestation_hash: str,
    terminal_execution_attestation_hash: str,
    immutable_artifact_lease_hash: str,
) -> dict[str, Any]:
    candidate_order = tuple(
        item["canonical_payload"]["config_hash"] for item in candidate_evidence
    )
    candidate_receipts = tuple(
        item["canonical_payload"]["evaluation_receipt_hash"]
        for item in candidate_evidence
    )
    period_payloads = tuple(period.model_dump(mode="python") for period in periods)
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_EVALUATION_BATCH_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": _genesis_payload()["record_hash"],
        "family_id": "family-1",
        "attempt_id": "attempt-1",
        "evaluation_batch_id": "batch-1",
        "frozen_config_set_hash": canonical_sha256(candidate_order),
        "candidate_count": len(candidate_order),
        "candidate_order": candidate_order,
        "candidate_receipt_hashes": candidate_receipts,
        "benchmark_receipt_hash": benchmark_evidence["canonical_payload"][
            "benchmark_receipt_hash"
        ],
        "ordered_period_keys_hash": canonical_sha256(period_payloads),
        "cost_contract_hash": "d" * 64,
        "start_execution_attestation_hash": start_execution_attestation_hash,
        "terminal_execution_attestation_hash": terminal_execution_attestation_hash,
        "immutable_artifact_lease_hash": immutable_artifact_lease_hash,
        "committed_at_us": 1_700_300_000_000_000,
    }
    authority_row = {
        "record_type": payload["record_type"],
        "authority_instance_id": payload["authority_instance_id"],
        "genesis_record_hash": payload["genesis_record_hash"],
        "evaluation_batch_id": payload["evaluation_batch_id"],
        "family_id": payload["family_id"],
        "attempt_id": payload["attempt_id"],
        "frozen_config_set_hash": payload["frozen_config_set_hash"],
        "candidate_count": payload["candidate_count"],
        "ordered_candidate_config_hashes_hash": canonical_sha256(candidate_order),
        "ordered_candidate_receipt_hashes_hash": canonical_sha256(candidate_receipts),
        "benchmark_receipt_hash": payload["benchmark_receipt_hash"],
        "ordered_period_keys_hash": payload["ordered_period_keys_hash"],
        "cost_contract_hash": payload["cost_contract_hash"],
        "start_execution_attestation_hash": payload["start_execution_attestation_hash"],
        "terminal_execution_attestation_hash": payload["terminal_execution_attestation_hash"],
        "immutable_artifact_lease_hash": payload["immutable_artifact_lease_hash"],
        "committed_at_us": payload["committed_at_us"],
    }
    payload["batch_receipt_hash"] = canonical_sha256(authority_row)
    return _typed_evidence("FamilyEvaluationBatchReceipt", payload)


def _candidate_disposition_evidence(
    *,
    proposal_id: str,
    config_hash: str,
    evaluation_receipt_hash: str,
) -> dict[str, Any]:
    return _typed_evidence(
        "CandidateDisposition",
        {
            "family_id": "family-1",
            "proposal_id": proposal_id,
            "disposition": "PERFORMANCE_EVALUATED",
            "config_hash": config_hash,
            "evaluation_receipt_hash": evaluation_receipt_hash,
            "quarantine_record_id": None,
            "reason_code": None,
            "performance_accessed": True,
        },
    )


def _quarantined_disposition_evidence() -> dict[str, Any]:
    return _typed_evidence(
        "CandidateDisposition",
        {
            "family_id": "family-1",
            "proposal_id": "9" * 64,
            "disposition": "SECRET_QUARANTINED",
            "config_hash": None,
            "evaluation_receipt_hash": None,
            "quarantine_record_id": f"qtn_{'a' * 32}",
            "reason_code": "DLP_SECRET_DETECTED",
            "performance_accessed": False,
        },
    )


def _standard_performance_inputs() -> dict[str, Any]:
    periods = _periods()
    candidate_gross = (
        tuple(float64_hex(0.010 + index * 0.0001) for index in range(20)),
        tuple(float64_hex(0.020 + index * 0.0001) for index in range(20)),
    )
    candidate_costs = tuple(
        tuple(float64_hex(0.001) for _ in periods) for _ in range(2)
    )
    candidate_net = tuple(
        tuple(float64_hex(float.fromhex(gross) - 0.001) for gross in row)
        for row in candidate_gross
    )
    candidate_turnovers = tuple(
        tuple(float64_hex(0.1) for _ in periods) for _ in range(2)
    )
    benchmark_gross = tuple(
        float64_hex(0.005 + index * 0.0001) for index in range(20)
    )
    benchmark_costs = tuple(float64_hex(0.0005) for _ in periods)
    benchmark_net = tuple(
        float64_hex(float.fromhex(gross) - 0.0005) for gross in benchmark_gross
    )
    benchmark_turnovers = tuple(float64_hex(0.05) for _ in periods)
    return {
        "ordered_period_keys": periods,
        "candidate_gross_returns": candidate_gross,
        "candidate_costs": candidate_costs,
        "candidate_net_returns": candidate_net,
        "candidate_turnovers": candidate_turnovers,
        "benchmark_gross_returns": benchmark_gross,
        "benchmark_costs": benchmark_costs,
        "benchmark_net_returns": benchmark_net,
        "benchmark_turnovers": benchmark_turnovers,
    }


def _default_disposition_evidence(*, assessed: bool) -> tuple[dict[str, Any], ...]:
    if not assessed:
        return (_quarantined_disposition_evidence(),)
    inputs = _standard_performance_inputs()
    periods = inputs["ordered_period_keys"]
    evidence: list[dict[str, Any]] = []
    for ordinal, (config_hash, proposal) in enumerate(
        zip(("6" * 64, "7" * 64), _clean_public_proposals()),
        start=1,
    ):
        candidate = _candidate_receipt_evidence(
            config_hash=config_hash,
            proposal_id=proposal["proposal_id"],
            ordinal=ordinal,
            series=_series_payload(
                periods,
                inputs["candidate_gross_returns"][ordinal - 1],
                inputs["candidate_costs"][ordinal - 1],
                inputs["candidate_net_returns"][ordinal - 1],
                inputs["candidate_turnovers"][ordinal - 1],
            ),
        )
        evidence.append(
            _candidate_disposition_evidence(
                proposal_id=proposal["proposal_id"],
                config_hash=config_hash,
                evaluation_receipt_hash=candidate["canonical_payload"][
                    "evaluation_receipt_hash"
                ],
            )
        )
    return tuple(evidence)


def _disposition_projection_row(
    evidence: dict[str, Any],
    *,
    recorded_at_us: int,
) -> dict[str, Any]:
    disposition = evidence["canonical_payload"]
    payload = _complete_authority_row_payload(
        "data_snooping_candidate_dispositions",
        record_type="DATA_SNOOPING_CANDIDATE_DISPOSITION_V1",
        authority_instance_id="family-authority-1",
        genesis_record_hash=_genesis_payload()["record_hash"],
        family_id=disposition["family_id"],
        proposal_id=disposition["proposal_id"],
        disposition=disposition["disposition"],
        config_hash=disposition["config_hash"],
        evaluation_receipt_hash=disposition["evaluation_receipt_hash"],
        quarantine_record_id=disposition["quarantine_record_id"],
        reason_code=disposition["reason_code"],
        performance_accessed=int(disposition["performance_accessed"]),
        recorded_at_us=recorded_at_us,
    )
    return {
        "table_name": "data_snooping_candidate_dispositions",
        "primary_key": {
            "family_id": disposition["family_id"],
            "proposal_id": disposition["proposal_id"],
        },
        "canonical_row_payload": payload,
        "record_hash": canonical_sha256(payload),
        "chain_partition_key": None,
        "chain_ordinal": None,
        "chain_previous_record_hash": None,
    }


def _bind_complete_exact_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    observed_pit_evidence = _observed_pit_evidence()
    (
        activation_code_manifest_evidence,
        code_manifest_evidence,
    ) = _code_manifest_evidence_pair()
    (
        start_execution_attestation_evidence,
        terminal_execution_attestation_evidence,
        immutable_artifact_lease_evidence,
    ) = _execution_and_lease_evidence()
    periods = tuple(payload["ordered_period_keys"])
    candidate_evidence = tuple(
        _candidate_receipt_evidence(
            config_hash=config_hash,
            proposal_id=proposal["proposal_id"],
            ordinal=ordinal,
            series=_series_payload(
                periods,
                payload["candidate_gross_returns"][ordinal - 1],
                payload["candidate_costs"][ordinal - 1],
                payload["candidate_net_returns"][ordinal - 1],
                payload["candidate_turnovers"][ordinal - 1],
            ),
        )
        for ordinal, (config_hash, proposal) in enumerate(
            zip(payload["ordered_candidate_config_hashes"], payload["public_proposals"]),
            start=1,
        )
    )
    benchmark_evidence = _benchmark_receipt_evidence(
        _series_payload(
            periods,
            payload["benchmark_gross_returns"],
            payload["benchmark_costs"],
            payload["benchmark_net_returns"],
            payload["benchmark_turnovers"],
        )
    )
    batch_evidence = _batch_evidence(
        periods=periods,
        candidate_evidence=candidate_evidence,
        benchmark_evidence=benchmark_evidence,
        start_execution_attestation_hash=(
            start_execution_attestation_evidence["canonical_payload"]["attestation_hash"]
        ),
        terminal_execution_attestation_hash=(
            terminal_execution_attestation_evidence["canonical_payload"]["attestation_hash"]
        ),
        immutable_artifact_lease_hash=(
            immutable_artifact_lease_evidence["canonical_payload"]["lease_hash"]
        ),
    )
    disposition_evidence = tuple(
        _candidate_disposition_evidence(
            proposal_id=proposal["proposal_id"],
            config_hash=config_hash,
            evaluation_receipt_hash=candidate["canonical_payload"]["evaluation_receipt_hash"],
        )
        for proposal, config_hash, candidate in zip(
            payload["public_proposals"],
            payload["ordered_candidate_config_hashes"],
            candidate_evidence,
        )
    )
    (
        trial_evidence,
        trial_history_sidecar,
        trial_snapshot_sidecar,
        trial_seal_sidecar,
    ) = _trial_ledger_bundle()
    winner_index = payload["ordered_candidate_config_hashes"].index(payload["winner_config_hash"])
    payload.update(
        {
            "selection_scope_evidence": _selection_scope_evidence(),
            "selection_decision_evidence": _selection_decision_evidence(payload["decision_class"]),
            "evaluation_batch_evidence": batch_evidence,
            "ordered_candidate_receipt_evidence": candidate_evidence,
            "benchmark_receipt_evidence": benchmark_evidence,
            "trial_ledger_proof_evidence": trial_evidence,
            "family_schema_v3_manifest_sidecar": _exact_byte_sidecar(
                "FAMILY_SCHEMA_V3_MANIFEST",
                _schema_manifest_bytes(),
            ),
            "trial_ledger_history_sidecar": trial_history_sidecar,
            "trial_ledger_snapshot_sidecar": trial_snapshot_sidecar,
            "trial_ledger_seal_sidecar": trial_seal_sidecar,
            "observed_pit_evidence": observed_pit_evidence,
            "activation_code_manifest_evidence": activation_code_manifest_evidence,
            "code_manifest_evidence": code_manifest_evidence,
            "start_execution_attestation_evidence": (
                start_execution_attestation_evidence
            ),
            "terminal_execution_attestation_evidence": (
                terminal_execution_attestation_evidence
            ),
            "immutable_artifact_lease_evidence": immutable_artifact_lease_evidence,
            "ordered_candidate_disposition_evidence": disposition_evidence,
            "candidate_disposition_record_hashes": tuple(
                _disposition_projection_row(
                    evidence,
                    recorded_at_us=1_700_120_000_000_000 + ordinal,
                )["record_hash"]
                for ordinal, evidence in enumerate(disposition_evidence, start=1)
            ),
            "evaluation_batch_receipt_hash": batch_evidence["canonical_payload"]["batch_receipt_hash"],
            "benchmark_receipt_hash": benchmark_evidence["canonical_payload"][
                "benchmark_receipt_hash"
            ],
            "ordered_candidate_receipt_hashes": tuple(
                evidence["canonical_payload"]["evaluation_receipt_hash"]
                for evidence in candidate_evidence
            ),
            "trial_ledger_proof_hash": trial_evidence["canonical_payload"]["proof_hash"],
            "winner_evaluation_receipt_hash": candidate_evidence[winner_index]["canonical_payload"][
                "evaluation_receipt_hash"
            ],
        }
    )
    return payload


def _authority_table_spec(table_name: str) -> tuple[Any, tuple[Any, ...]]:
    modules = tuple(
        importlib.import_module(name)
        for name in (
            "services.research.experiment_registry.family_schema_c4",
            "services.research.experiment_registry.family_schema_c5_c6",
            "services.research.experiment_registry.family_schema_c7",
            "services.research.experiment_registry.family_schema_c8",
        )
    )
    tables = (
        *modules[0].C4_AUTHORITY_TABLE_SPECS,
        *modules[1].C5_AUTHORITY_TABLE_SPECS,
        *modules[2].C7_AUTHORITY_TABLE_SPECS,
        *modules[3].C8_AUTHORITY_TABLE_SPECS,
    )
    table = next(table for table in tables if table.table_name == table_name)
    columns = list(table.columns)
    extensions = (
        *modules[1].C6_TABLE_EXTENSIONS,
        *modules[2].C7_TABLE_EXTENSIONS,
        *modules[3].C8_TABLE_EXTENSIONS,
    )
    for extension in extensions:
        if extension.target_table != table_name or extension.column_insertion is None:
            continue
        insertion = extension.column_insertion
        anchor_index = next(
            index
            for index, column in enumerate(columns)
            if column.name == insertion.anchor_column
        )
        columns[anchor_index + 1 : anchor_index + 1] = list(insertion.columns)
    return table, tuple(
        column for column in columns if column.name != table.row_hash.record_hash_column
    )


def _complete_authority_row_payload(
    table_name: str,
    **overrides: Any,
) -> dict[str, Any]:
    table, columns = _authority_table_spec(table_name)
    payload: dict[str, Any] = {}
    for column in columns:
        name = column.name
        if name in overrides:
            payload[name] = overrides[name]
        elif name == "record_type":
            payload[name] = table.row_hash.version_literal
        elif name == "authority_instance_id":
            payload[name] = "family-authority-1"
        elif name == "genesis_record_hash":
            payload[name] = _genesis_payload()["record_hash"]
        elif column.nullable:
            payload[name] = None
        elif column.sql_type.value == "INTEGER":
            payload[name] = 1_700_000_000_000_000 if name.endswith("_at_us") else 1
        elif name.endswith("_json"):
            payload[name] = "[]"
        elif (
            name.endswith(("_hash", "_head", "_fingerprint"))
            or name == "prev_record_hash"
        ):
            payload[name] = "1" * 64
        else:
            payload[name] = f"{name}-value"
    unknown = set(overrides) - set(payload)
    assert not unknown, (table_name, unknown)
    return payload


def _authority_projection(*, include_grid: bool = True) -> dict[str, Any]:
    genesis_hash = _genesis_payload()["record_hash"]
    observed_payload = _observed_pit_evidence()["canonical_payload"]
    code_payload = _code_manifest_evidence_pair()[1]["canonical_payload"]
    start_evidence, terminal_evidence, lease_evidence = _execution_and_lease_evidence()
    start_payload = start_evidence["canonical_payload"]
    terminal_payload = terminal_evidence["canonical_payload"]
    lease_payload = lease_evidence["canonical_payload"]
    disposition_evidence = _default_disposition_evidence(assessed=include_grid)
    disposition_rows = tuple(
        _disposition_projection_row(
            evidence,
            recorded_at_us=1_700_120_000_000_000 + ordinal,
        )
        for ordinal, evidence in enumerate(disposition_evidence, start=1)
    )
    proposal_ids = tuple(
        evidence["canonical_payload"]["proposal_id"] for evidence in disposition_evidence
    )
    disposition_hashes = tuple(row["record_hash"] for row in disposition_rows)
    research_event_payload = _complete_authority_row_payload(
        "research_authority_events",
        authority_instance_id="family-authority-1",
        genesis_record_hash=genesis_hash,
        event_id="selection-history-event-1",
        event_ordinal=1,
        action="SELECTION_DECISION_ISSUED",
        subject_record_type="SELECTION_DECISION_V1",
        subject_id="decision-1",
        subject_record_hash="a" * 64,
        audit_actor_id="research-system-1",
        audit_actor_kind="SYSTEM",
        audit_actor_evidence_hash="c" * 64,
        audit_actor_policy_id="research-actor-policy",
        audit_actor_policy_version="v1",
        audit_actor_policy_hash="d" * 64,
        audit_actor_authority_id="identity-authority-1",
        audit_actor_authority_head="e" * 64,
        event_at_us=1_700_100_000_000_000,
        prev_record_hash=genesis_hash,
    )
    research_event_hash = canonical_sha256(research_event_payload)
    rows = []
    declaration_record_hash: str | None = None
    freeze_record_hash: str | None = None
    closure_record_hash: str | None = None
    for table_name, primary_key_field, primary_key, record_type in (
        (
            "data_snooping_declarations",
            "declaration_id",
            "declaration-1",
            "DATA_SNOOPING_DECLARATION_V1",
        ),
        (
            "data_snooping_family_freezes",
            "freeze_id",
            "freeze-1",
            "DATA_SNOOPING_FAMILY_FREEZE_V1",
        ),
        (
            "data_snooping_family_closures",
            "closure_id",
            "closure-1",
            "DATA_SNOOPING_FAMILY_CLOSURE_V1",
        ),
        (
            "data_snooping_grid_assessments",
            "grid_assessment_id",
            "grid-assessment-1",
            "DATA_SNOOPING_GRID_ASSESSMENT_V1",
        ),
        (
            "data_snooping_assessments",
            "assessment_id",
            "family-conclusion-1",
            "DATA_SNOOPING_ASSESSMENT_V1",
        ),
    ):
        row_overrides: dict[str, Any] = {
            "record_type": record_type,
            "authority_instance_id": "family-authority-1",
            "genesis_record_hash": genesis_hash,
            primary_key_field: primary_key,
            "family_id": "family-1",
        }
        if table_name == "data_snooping_declarations":
            row_overrides.update(
                {
                    "selection_scope_id": "scope-1",
                    "selection_decision_id": "decision-1",
                    "decision_class": "PUBLISHABLE",
                    "observed_pit_authority_id": observed_payload["authority_id"],
                    "observed_pit_handle_id": observed_payload["handle_id"],
                    "observed_pit_content_hash": observed_payload["content_hash"],
                    "knowledge_time_policy_id": observed_payload[
                        "knowledge_time_policy_id"
                    ],
                    "knowledge_time_policy_version": observed_payload[
                        "knowledge_time_policy_version"
                    ],
                    "observed_pit_authority_head": observed_payload["authority_head"],
                    "code_manifest_policy_id": code_payload["policy_ref"]["policy_id"],
                    "code_manifest_policy_version": code_payload["policy_ref"][
                        "policy_version"
                    ],
                    "code_manifest_policy_hash": code_payload["policy_ref"]["policy_hash"],
                    "code_manifest_evidence_hash": code_payload["evidence_hash"],
                    "code_manifest_authority_id": code_payload["authority_id"],
                    "code_manifest_authority_head": code_payload["authority_head"],
                }
            )
            if not include_grid:
                row_overrides["decision_class"] = "DIAGNOSTIC_ONLY"
        elif table_name == "data_snooping_family_freezes":
            assert declaration_record_hash is not None
            row_overrides.update(
                {
                    "declaration_id": "declaration-1",
                    "declaration_record_hash": declaration_record_hash,
                    "raw_proposal_count": 2,
                    "unique_performance_hypothesis_count": 2,
                }
            )
            if not include_grid:
                row_overrides.update(
                    {
                        "raw_proposal_count": 1,
                        "unique_performance_hypothesis_count": 0,
                    }
                )
        elif table_name == "data_snooping_grid_assessments":
            assert closure_record_hash is not None
            row_overrides.update(
                {
                    "closure_manifest_hash": closure_record_hash,
                    "block_length": 5,
                    "schedule_ordinal": 1,
                    "prev_record_hash": genesis_hash,
                    "promotion_effect": "NONE",
                }
            )
        elif table_name == "data_snooping_family_closures":
            assert declaration_record_hash is not None
            assert freeze_record_hash is not None
            row_overrides.update(
                {
                    "declaration_id": "declaration-1",
                    "declaration_record_hash": declaration_record_hash,
                    "freeze_id": "freeze-1",
                    "freeze_record_hash": freeze_record_hash,
                    "closure_status": "COMPLETE",
                    "selection_scope_id": "scope-1",
                    "selection_decision_id": "decision-1",
                    "selection_scope_history_head": research_event_hash,
                    "ordered_proposal_ids_hash": canonical_sha256(proposal_ids),
                    "ordered_disposition_record_hashes_hash": canonical_sha256(
                        disposition_hashes
                    ),
                    "attempt_id": "attempt-1",
                    "terminal_attempt_event_id": "attempt-event-1",
                    "evaluation_batch_id": "batch-1",
                    "oos_exposure_id": "exposure-1",
                    "exposure_fingerprint": "4" * 64,
                    "exposure_authority_id": "family-authority-1",
                    "exposure_authority_head": "5" * 64,
                    "raw_proposal_count": 2,
                    "unique_performance_hypothesis_count": 2,
                    "global_unique_config_count": 2,
                    "performance_access_attempt_count": 1,
                    "failure_reasons_json": "[]",
                    "closed_at_us": 1_700_350_000_000_000,
                    "observed_pit_authority_id": observed_payload["authority_id"],
                    "observed_pit_handle_id": observed_payload["handle_id"],
                    "observed_pit_content_hash": observed_payload["content_hash"],
                    "knowledge_time_policy_id": observed_payload[
                        "knowledge_time_policy_id"
                    ],
                    "knowledge_time_policy_version": observed_payload[
                        "knowledge_time_policy_version"
                    ],
                    "observed_pit_authority_head": observed_payload["authority_head"],
                    "code_manifest_policy_id": code_payload["policy_ref"]["policy_id"],
                    "code_manifest_policy_version": code_payload["policy_ref"][
                        "policy_version"
                    ],
                    "code_manifest_policy_hash": code_payload["policy_ref"]["policy_hash"],
                    "code_manifest_evidence_hash": code_payload["evidence_hash"],
                    "code_manifest_authority_id": code_payload["authority_id"],
                    "code_manifest_authority_head": code_payload["authority_head"],
                    "start_execution_attestation_hash": start_payload["attestation_hash"],
                    "terminal_execution_attestation_hash": terminal_payload[
                        "attestation_hash"
                    ],
                    "execution_attestation_authority_id": terminal_payload["authority_id"],
                    "start_execution_attestation_authority_head": start_payload[
                        "authority_head"
                    ],
                    "terminal_execution_attestation_authority_head": terminal_payload[
                        "authority_head"
                    ],
                    "immutable_artifact_lease_id": lease_payload["lease_id"],
                    "immutable_artifact_lease_hash": lease_payload["lease_hash"],
                    "immutable_artifact_lease_authority_id": lease_payload[
                        "authority_id"
                    ],
                    "immutable_artifact_lease_authority_head": lease_payload[
                        "authority_head"
                    ],
                }
            )
            if not include_grid:
                row_overrides.update(
                    {
                        "closure_status": "NOT_ASSESSABLE",
                        "attempt_id": None,
                        "terminal_attempt_event_id": None,
                        "evaluation_batch_id": None,
                        "evaluation_batch_receipt_hash": None,
                        "oos_release_id": None,
                        "oos_access_attempt_terminal_hash": None,
                        "oos_release_record_hash": None,
                        "raw_proposal_count": 1,
                        "unique_performance_hypothesis_count": 0,
                        "global_unique_config_count": 0,
                        "performance_access_attempt_count": 0,
                        "ordered_period_keys_hash": None,
                        "candidate_series_set_hash": None,
                        "benchmark_receipt_hash": None,
                        "cost_model_input_set_hash": None,
                        "winner_config_hash": None,
                        "winner_evaluation_receipt_hash": None,
                        "failure_reasons_json": '["SECRET_QUARANTINED"]',
                        "start_execution_attestation_hash": None,
                        "terminal_execution_attestation_hash": None,
                        "execution_attestation_authority_id": None,
                        "start_execution_attestation_authority_head": None,
                        "terminal_execution_attestation_authority_head": None,
                        "immutable_artifact_lease_id": None,
                        "immutable_artifact_lease_hash": None,
                        "immutable_artifact_lease_authority_id": None,
                        "immutable_artifact_lease_authority_head": None,
                    }
                )
        elif table_name == "data_snooping_assessments":
            assert closure_record_hash is not None
            row_overrides.update(
                {
                    "closure_manifest_hash": closure_record_hash,
                    "assessment_status": "ASSESSED",
                    "diagnostic_verdict": "DOES_NOT_SUPPORT_SUPERIORITY",
                    "promotion_effect": "NONE",
                    "failure_reasons_json": "[]",
                }
            )
            if not include_grid:
                row_overrides.update(
                    {
                        "assessment_status": "NOT_ASSESSABLE",
                        "diagnostic_verdict": "NOT_APPLICABLE",
                        "failure_reasons_json": '["SECRET_QUARANTINED"]',
                    }
                )
        canonical_row_payload = _complete_authority_row_payload(
            table_name,
            **row_overrides,
        )
        record_hash = canonical_sha256(canonical_row_payload)
        if table_name == "data_snooping_declarations":
            declaration_record_hash = record_hash
        elif table_name == "data_snooping_family_freezes":
            freeze_record_hash = record_hash
        elif table_name == "data_snooping_family_closures":
            closure_record_hash = record_hash
        is_grid = table_name == "data_snooping_grid_assessments"
        rows.append(
            {
                "table_name": table_name,
                "primary_key": {primary_key_field: primary_key},
                "canonical_row_payload": canonical_row_payload,
                "record_hash": record_hash,
                "chain_partition_key": (
                    {
                        "authority_instance_id": "family-authority-1",
                        "family_id": "family-1",
                        "block_length": 5,
                    }
                    if is_grid
                    else None
                ),
                "chain_ordinal": 1 if is_grid else None,
                "chain_previous_record_hash": genesis_hash if is_grid else None,
            }
        )
    rows.append(
        {
            "table_name": "research_authority_events",
            "primary_key": {"event_id": "selection-history-event-1"},
            "canonical_row_payload": research_event_payload,
            "record_hash": research_event_hash,
            "chain_partition_key": {"authority_instance_id": "family-authority-1"},
            "chain_ordinal": 1,
            "chain_previous_record_hash": genesis_hash,
        }
    )
    rows.extend(disposition_rows)
    if not include_grid:
        rows = [row for row in rows if row["table_name"] != "data_snooping_grid_assessments"]
    rows.sort(key=lambda row: (row["table_name"], canonical_json_bytes(row["primary_key"])))
    grid_rows = [row for row in rows if row["table_name"] == "data_snooping_grid_assessments"]
    chain_heads = [
        {
            "table_name": "research_authority_events",
            "partition_key": {"authority_instance_id": "family-authority-1"},
            "row_count": 1,
            "head_record_hash": research_event_hash,
        },
        *(
            {
            "table_name": "data_snooping_grid_assessments",
            "partition_key": row["chain_partition_key"],
            "row_count": 1,
            "head_record_hash": row["record_hash"],
            }
            for row in grid_rows
        ),
    ]
    chain_heads.sort(
        key=lambda head: (
            head["table_name"],
            canonical_json_bytes(head["partition_key"]),
        )
    )
    chain_heads_tuple = tuple(chain_heads)
    ordered_row_hashes = tuple(row["record_hash"] for row in rows)
    return {
        "projection_schema_version": "DATA_SNOOPING_AUTHORITY_PROJECTION_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": genesis_hash,
        "selection_scope_history_head": research_event_hash,
        "canonical_rows": tuple(rows),
        "ordered_row_hashes": ordered_row_hashes,
        "ordered_row_hashes_hash": canonical_sha256(ordered_row_hashes),
        "chain_heads": chain_heads_tuple,
        "chain_heads_hash": canonical_sha256(chain_heads_tuple),
    }


def _rebuild_authority_projection(
    projection: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    chain_heads: list[dict[str, Any]],
) -> dict[str, Any]:
    rows.sort(key=lambda row: (row["table_name"], canonical_json_bytes(row["primary_key"])))
    chain_heads.sort(
        key=lambda head: (
            head["table_name"],
            canonical_json_bytes(head["partition_key"]),
        )
    )
    ordered_row_hashes = tuple(row["record_hash"] for row in rows)
    return {
        **projection,
        "canonical_rows": tuple(rows),
        "ordered_row_hashes": ordered_row_hashes,
        "ordered_row_hashes_hash": canonical_sha256(ordered_row_hashes),
        "chain_heads": tuple(chain_heads),
        "chain_heads_hash": canonical_sha256(tuple(chain_heads)),
    }


def _rebind_projection_to_core_payload(
    payload: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in projection["canonical_rows"]
        if row["table_name"]
        not in {
            "data_snooping_family_searches",
            "data_snooping_proposal_registrations",
            "data_snooping_proposals",
            "data_snooping_hypotheses",
            "data_snooping_search_emission_seals",
            "data_snooping_candidate_dispositions",
        }
    ]
    by_table = {row["table_name"]: row for row in rows}

    def replace_row(table_name: str, updates: dict[str, Any]) -> str:
        row = by_table[table_name]
        canonical_payload = {**row["canonical_row_payload"], **updates}
        rebound = {
            **row,
            "canonical_row_payload": canonical_payload,
            "record_hash": canonical_sha256(canonical_payload),
        }
        rows[rows.index(row)] = rebound
        by_table[table_name] = rebound
        return rebound["record_hash"]

    scope = payload["selection_scope_evidence"]["canonical_payload"]
    observed = payload["observed_pit_evidence"]["canonical_payload"]
    code = payload["code_manifest_evidence"]["canonical_payload"]
    external_updates = {
        "observed_pit_authority_id": observed["authority_id"],
        "observed_pit_handle_id": observed["handle_id"],
        "observed_pit_content_hash": observed["content_hash"],
        "knowledge_time_policy_id": observed["knowledge_time_policy_id"],
        "knowledge_time_policy_version": observed["knowledge_time_policy_version"],
        "observed_pit_authority_head": observed["authority_head"],
        "code_manifest_policy_id": code["policy_ref"]["policy_id"],
        "code_manifest_policy_version": code["policy_ref"]["policy_version"],
        "code_manifest_policy_hash": code["policy_ref"]["policy_hash"],
        "code_manifest_evidence_hash": code["evidence_hash"],
        "code_manifest_authority_id": code["authority_id"],
        "code_manifest_authority_head": code["authority_head"],
    }
    declaration_hash = replace_row(
        "data_snooping_declarations",
        {
            "family_id": payload["family_id"],
            "selection_scope_id": payload["selection_scope_id"],
            "selection_decision_id": payload["selection_decision_id"],
            "decision_class": payload["decision_class"],
            "deployment_mandate_hash": scope["deployment_mandate_hash"],
            **external_updates,
        },
    )
    disposition_rows = [
        _disposition_projection_row(
            evidence,
            recorded_at_us=1_700_120_000_000_000 + ordinal,
        )
        for ordinal, evidence in enumerate(
            payload["ordered_candidate_disposition_evidence"],
            start=1,
        )
    ]
    disposition_by_id = {
        evidence["canonical_payload"]["proposal_id"]: evidence["canonical_payload"]
        for evidence in payload["ordered_candidate_disposition_evidence"]
    }
    public_proposals = [dict(proposal) for proposal in payload["public_proposals"]]
    proposal_count = len(public_proposals)
    config_hashes = tuple(payload["ordered_candidate_config_hashes"])

    declaration = by_table["data_snooping_declarations"]["canonical_row_payload"]
    search_id = "search-1"
    search_payload = _complete_authority_row_payload(
        "data_snooping_family_searches",
        authority_instance_id=payload["authority_instance_id"],
        genesis_record_hash=projection["genesis_record_hash"],
        declaration_id=declaration["declaration_id"],
        declaration_record_hash=declaration_hash,
        family_id=payload["family_id"],
        search_id=search_id,
        research_program_id="program-1",
        search_kind="GUIDED",
        generator_seed=7,
        proposal_budget=max(1, proposal_count),
        stopping_rule_hash="1" * 64,
        external_ingestion_deadline_us=1_700_200_000_000_000,
        search_authority_id="search-authority-1",
        search_authority_head="2" * 64,
        linked_at_us=1_700_110_000_000_000,
        link_ordinal=1,
        prev_record_hash=projection["genesis_record_hash"],
    )
    search_hash = canonical_sha256(search_payload)
    search_row = {
        "table_name": "data_snooping_family_searches",
        "primary_key": {"search_id": search_id},
        "canonical_row_payload": search_payload,
        "record_hash": search_hash,
        "chain_partition_key": {"family_id": payload["family_id"]},
        "chain_ordinal": 1,
        "chain_previous_record_hash": projection["genesis_record_hash"],
    }

    registration_rows: list[dict[str, Any]] = []
    terminal_rows: list[dict[str, Any]] = []
    first_accepted_by_config: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    previous_registration_hash = projection["genesis_record_hash"]
    for ordinal, public in enumerate(public_proposals, start=1):
        proposal_id = public["proposal_id"]
        disposition = disposition_by_id[proposal_id]
        quarantined = public["projection_kind"] == "QUARANTINED"
        invalid = public["projection_kind"] == "NON_QUARANTINED_INVALID"
        raw_hash = None if quarantined else canonical_sha256(
            {"proposal_id": proposal_id, "raw": True}
        )
        registration_payload = _complete_authority_row_payload(
            "data_snooping_proposal_registrations",
            authority_instance_id=payload["authority_instance_id"],
            genesis_record_hash=projection["genesis_record_hash"],
            declaration_id=declaration["declaration_id"],
            declaration_record_hash=declaration_hash,
            proposal_id=proposal_id,
            family_id=payload["family_id"],
            search_id=search_id,
            emission_ordinal=ordinal,
            proposal_branch=("SECRET_QUARANTINED" if quarantined else "CLEAN"),
            identity_payload_json=_canonical_json_text(
                {
                    "family_id": payload["family_id"],
                    "search_id": search_id,
                    "emission_ordinal": ordinal,
                    "proposal_id": proposal_id,
                    "proposal_branch": (
                        "SECRET_QUARANTINED" if quarantined else "CLEAN"
                    ),
                    "raw_envelope_hash": raw_hash,
                    "canonical_proposal_hash": None,
                    "config_hash": None,
                    "alpha_id": None,
                }
            ),
            raw_envelope_hash=raw_hash,
            media_type=public["media_type"],
            raw_byte_length=None if quarantined else 128 + ordinal,
            length_bucket=public.get("length_bucket", "LE_1_KIB"),
            dlp_policy_id=public["dlp_policy_id"],
            dlp_policy_version=public["dlp_policy_version"],
            scanner_implementation="DLP_SCANNER_V1",
            scanner_version="1",
            dlp_result_code=("SECRET_QUARANTINED" if quarantined else "CLEAN"),
            restricted_clean_blob_receipt_hash=(
                None if quarantined else canonical_sha256({"proposal_id": proposal_id, "blob": True})
            ),
            quarantine_record_id=(public.get("quarantine_record_id") if quarantined else None),
            registered_at_us=1_700_115_000_000_000 + ordinal,
            prev_record_hash=previous_registration_hash,
        )
        registration_hash = canonical_sha256(registration_payload)
        public["registration_record_hash"] = registration_hash
        registration_row = {
            "table_name": "data_snooping_proposal_registrations",
            "primary_key": {"proposal_id": proposal_id},
            "canonical_row_payload": registration_payload,
            "record_hash": registration_hash,
            "chain_partition_key": {
                "family_id": payload["family_id"],
                "search_id": search_id,
            },
            "chain_ordinal": ordinal,
            "chain_previous_record_hash": previous_registration_hash,
        }
        previous_registration_hash = registration_hash
        registration_rows.append(registration_row)

        if quarantined:
            parse_status = "SECRET_QUARANTINED"
            canonical_proposal_json = None
            canonical_proposal_hash = None
            config_hash = None
            alpha_id = None
        elif invalid:
            parse_status = "INVALID"
            canonical_proposal_json = None
            canonical_proposal_hash = None
            config_hash = None
            alpha_id = None
        else:
            parse_status = "ACCEPTED"
            canonical_proposal_json = _canonical_json_text(public["canonical_proposal"])
            canonical_proposal_hash = public["canonical_proposal_hash"]
            config_hash = disposition["config_hash"]
            alpha_id = f"alpha-{ordinal}"
        terminal_payload = _complete_authority_row_payload(
            "data_snooping_proposals",
            authority_instance_id=payload["authority_instance_id"],
            genesis_record_hash=projection["genesis_record_hash"],
            proposal_id=proposal_id,
            family_id=payload["family_id"],
            search_id=search_id,
            emission_ordinal=ordinal,
            registration_record_hash=registration_hash,
            parse_status=parse_status,
            canonical_proposal_json=canonical_proposal_json,
            canonical_proposal_hash=canonical_proposal_hash,
            config_hash=config_hash,
            alpha_id=alpha_id,
            parsed_at_us=1_700_116_000_000_000 + ordinal,
        )
        terminal_row = {
            "table_name": "data_snooping_proposals",
            "primary_key": {"proposal_id": proposal_id},
            "canonical_row_payload": terminal_payload,
            "record_hash": canonical_sha256(terminal_payload),
            "chain_partition_key": None,
            "chain_ordinal": None,
            "chain_previous_record_hash": None,
        }
        terminal_rows.append(terminal_row)
        if config_hash is not None:
            first_accepted_by_config.setdefault(
                config_hash,
                (registration_row, terminal_row),
            )

    payload["public_proposals"] = tuple(public_proposals)
    hypothesis_rows: list[dict[str, Any]] = []
    for config_hash, (registration_row, terminal_row) in sorted(
        first_accepted_by_config.items()
    ):
        registration_payload = registration_row["canonical_row_payload"]
        terminal_payload = terminal_row["canonical_row_payload"]
        hypothesis_payload = _complete_authority_row_payload(
            "data_snooping_hypotheses",
            authority_instance_id=payload["authority_instance_id"],
            genesis_record_hash=projection["genesis_record_hash"],
            family_id=payload["family_id"],
            first_search_id=registration_payload["search_id"],
            config_hash=config_hash,
            alpha_id=terminal_payload["alpha_id"],
            canonical_hypothesis_hash=terminal_payload["canonical_proposal_hash"],
            first_proposal_id=registration_payload["proposal_id"],
        )
        hypothesis_rows.append(
            {
                "table_name": "data_snooping_hypotheses",
                "primary_key": {
                    "family_id": payload["family_id"],
                    "config_hash": config_hash,
                },
                "canonical_row_payload": hypothesis_payload,
                "record_hash": canonical_sha256(hypothesis_payload),
                "chain_partition_key": None,
                "chain_ordinal": None,
                "chain_previous_record_hash": None,
            }
        )

    registration_ids = [
        row["canonical_row_payload"]["proposal_id"] for row in registration_rows
    ]
    clean_raw_hashes = [
        row["canonical_row_payload"]["raw_envelope_hash"]
        for row in registration_rows
        if row["canonical_row_payload"]["proposal_branch"] == "CLEAN"
    ]
    canonical_hashes = [
        row["canonical_row_payload"]["canonical_proposal_hash"]
        for row in terminal_rows
        if row["canonical_row_payload"]["parse_status"] == "ACCEPTED"
    ]
    quarantine_ids = [
        row["canonical_row_payload"]["quarantine_record_id"]
        for row in registration_rows
        if row["canonical_row_payload"]["proposal_branch"] == "SECRET_QUARANTINED"
    ]
    accepted_configs = sorted(first_accepted_by_config)
    candidate_count = len(accepted_configs)
    candidate_chain_head = (
        canonical_sha256(
            {
                "search_id": search_id,
                "accepted_config_set": accepted_configs,
            }
        )
        if candidate_count
        else canonical_sha256(
            {
                "domain_separator": "QUANTOS_EMPTY_SEARCH_CANDIDATE_CHAIN_V1",
                "authority_instance_id": payload["authority_instance_id"],
                "family_id": payload["family_id"],
                "search_id": search_id,
            }
        )
    )
    seal_payload = _complete_authority_row_payload(
        "data_snooping_search_emission_seals",
        authority_instance_id=payload["authority_instance_id"],
        genesis_record_hash=projection["genesis_record_hash"],
        family_id=payload["family_id"],
        search_id=search_id,
        declared_proposal_budget=max(1, proposal_count),
        actual_raw_proposal_count=proposal_count,
        ordered_proposal_ids_hash=canonical_sha256(registration_ids),
        ordered_non_quarantined_raw_hashes_hash=canonical_sha256(clean_raw_hashes),
        ordered_canonical_proposal_hashes_hash=canonical_sha256(canonical_hashes),
        ordered_quarantine_record_ids_hash=canonical_sha256(quarantine_ids),
        accepted_config_set_hash=canonical_sha256(accepted_configs),
        candidate_count=candidate_count,
        candidate_chain_head=candidate_chain_head,
        terminal_reason="DECLARED_STOPPING_RULE",
        sealed_at_us=1_700_118_000_000_000,
    )
    seal_hash = canonical_sha256(seal_payload)
    seal_row = {
        "table_name": "data_snooping_search_emission_seals",
        "primary_key": {"family_id": payload["family_id"], "search_id": search_id},
        "canonical_row_payload": seal_payload,
        "record_hash": seal_hash,
        "chain_partition_key": None,
        "chain_ordinal": None,
        "chain_previous_record_hash": None,
    }

    terminal_search_hash = canonical_sha256(
        {"search_id": search_id, "status": "COMPLETED"}
    )
    freeze_hash = replace_row(
        "data_snooping_family_freezes",
        {
            "family_id": payload["family_id"],
            "declaration_record_hash": declaration_hash,
            "search_count": 1,
            "ordered_search_ids_json": _canonical_json_text((search_id,)),
            "ordered_search_ids_hash": canonical_sha256((search_id,)),
            "ordered_search_terminal_hashes_json": _canonical_json_text(
                (terminal_search_hash,)
            ),
            "ordered_search_terminal_hashes_hash": canonical_sha256(
                (terminal_search_hash,)
            ),
            "ordered_emission_seal_hashes_json": _canonical_json_text((seal_hash,)),
            "ordered_emission_seal_hashes_hash": canonical_sha256((seal_hash,)),
            "ordered_candidate_chain_heads_json": _canonical_json_text(
                (candidate_chain_head,)
            ),
            "ordered_candidate_chain_heads_hash": canonical_sha256(
                (candidate_chain_head,)
            ),
            "raw_proposal_count": proposal_count,
            "unique_performance_hypothesis_count": candidate_count,
            "frozen_config_set_hash": canonical_sha256(accepted_configs),
            "observed_pit_content_hash": observed["content_hash"],
            "code_manifest_evidence_hash": code["evidence_hash"],
        },
    )
    disposition_hashes = tuple(row["record_hash"] for row in disposition_rows)
    payload["candidate_disposition_record_hashes"] = disposition_hashes
    proposal_ids = tuple(proposal["proposal_id"] for proposal in payload["public_proposals"])
    batch = (
        payload["evaluation_batch_evidence"]["canonical_payload"]
        if payload["evaluation_batch_evidence"] is not None
        else None
    )
    trial = payload["trial_ledger_proof_evidence"]["canonical_payload"]
    period_payloads = tuple(
        period.model_dump(mode="python") if hasattr(period, "model_dump") else period
        for period in payload["ordered_period_keys"]
    )
    start = (
        payload["start_execution_attestation_evidence"]["canonical_payload"]
        if payload["start_execution_attestation_evidence"] is not None
        else None
    )
    terminal = (
        payload["terminal_execution_attestation_evidence"]["canonical_payload"]
        if payload["terminal_execution_attestation_evidence"] is not None
        else None
    )
    lease = (
        payload["immutable_artifact_lease_evidence"]["canonical_payload"]
        if payload["immutable_artifact_lease_evidence"] is not None
        else None
    )
    closure_updates: dict[str, Any] = {
        "family_id": payload["family_id"],
        "declaration_record_hash": declaration_hash,
        "freeze_record_hash": freeze_hash,
        "closure_status": payload["closure_status"],
        "selection_scope_id": payload["selection_scope_id"],
        "selection_decision_id": payload["selection_decision_id"],
        "selection_scope_history_head": projection["selection_scope_history_head"],
        "ordered_search_ids_hash": canonical_sha256((search_id,)),
        "ordered_search_terminal_hashes_hash": canonical_sha256(
            (terminal_search_hash,)
        ),
        "ordered_emission_seal_hashes_hash": canonical_sha256((seal_hash,)),
        "ordered_proposal_ids_hash": canonical_sha256(proposal_ids),
        "ordered_disposition_record_hashes_hash": canonical_sha256(disposition_hashes),
        "ordered_config_hashes_json": _canonical_json_text(config_hashes),
        "ordered_evaluation_receipt_hashes_json": _canonical_json_text(
            payload["ordered_candidate_receipt_hashes"]
        ),
        "attempt_id": batch["attempt_id"] if batch is not None else None,
        "evaluation_batch_id": batch["evaluation_batch_id"] if batch is not None else None,
        "evaluation_batch_receipt_hash": payload["evaluation_batch_receipt_hash"],
        "oos_exposure_id": "exposure-1" if batch is not None else None,
        "exposure_fingerprint": "4" * 64 if batch is not None else None,
        "exposure_authority_id": payload["authority_instance_id"] if batch is not None else None,
        "exposure_authority_head": "5" * 64 if batch is not None else None,
        "raw_proposal_count": proposal_count,
        "unique_performance_hypothesis_count": len(config_hashes),
        "global_unique_config_count": trial["global_unique_config_count"],
        "performance_access_attempt_count": 1 if batch is not None else 0,
        "ordered_period_keys_hash": (
            canonical_sha256(period_payloads) if period_payloads else None
        ),
        "benchmark_receipt_hash": payload["benchmark_receipt_hash"],
        "winner_config_hash": payload["winner_config_hash"],
        "winner_evaluation_receipt_hash": payload[
            "winner_evaluation_receipt_hash"
        ],
        "trial_ledger_proof_json": _canonical_json_text(trial),
        "trial_ledger_proof_hash": payload["trial_ledger_proof_hash"],
        "failure_reasons_json": _canonical_json_text(payload["failure_reasons"]),
        **external_updates,
        "start_execution_attestation_hash": (
            start["attestation_hash"] if start is not None else None
        ),
        "terminal_execution_attestation_hash": (
            terminal["attestation_hash"] if terminal is not None else None
        ),
        "execution_attestation_authority_id": (
            (terminal or start)["authority_id"] if terminal is not None or start is not None else None
        ),
        "start_execution_attestation_authority_head": (
            start["authority_head"] if start is not None else None
        ),
        "terminal_execution_attestation_authority_head": (
            terminal["authority_head"] if terminal is not None else None
        ),
        "immutable_artifact_lease_id": lease["lease_id"] if lease is not None else None,
        "immutable_artifact_lease_hash": lease["lease_hash"] if lease is not None else None,
        "immutable_artifact_lease_authority_id": (
            lease["authority_id"] if lease is not None else None
        ),
        "immutable_artifact_lease_authority_head": (
            lease["authority_head"] if lease is not None else None
        ),
    }
    closure_hash = replace_row("data_snooping_family_closures", closure_updates)

    grid_rows = sorted(
        (row for row in rows if row["table_name"] == "data_snooping_grid_assessments"),
        key=lambda row: (
            canonical_json_bytes(row["chain_partition_key"]),
            row["chain_ordinal"],
        ),
    )
    grid_heads: dict[str, dict[str, Any]] = {}
    previous_by_partition: dict[str, str] = {}
    rebound_grid_rows: list[dict[str, Any]] = []
    for row in grid_rows:
        partition_key = canonical_sha256(row["chain_partition_key"])
        previous_hash = previous_by_partition.get(
            partition_key,
            projection["genesis_record_hash"],
        )
        canonical_payload = {
            **row["canonical_row_payload"],
            "family_id": payload["family_id"],
            "closure_manifest_hash": closure_hash,
            "promotion_effect": payload["promotion_effect"],
            "prev_record_hash": previous_hash,
        }
        rebound = {
            **row,
            "canonical_row_payload": canonical_payload,
            "record_hash": canonical_sha256(canonical_payload),
            "chain_previous_record_hash": previous_hash,
        }
        previous_by_partition[partition_key] = rebound["record_hash"]
        rebound_grid_rows.append(rebound)
        grid_heads[partition_key] = {
            "table_name": "data_snooping_grid_assessments",
            "partition_key": rebound["chain_partition_key"],
            "row_count": sum(
                item["chain_partition_key"] == rebound["chain_partition_key"]
                for item in grid_rows
            ),
            "head_record_hash": rebound["record_hash"],
        }
    rows = [row for row in rows if row["table_name"] != "data_snooping_grid_assessments"]
    rows.extend(rebound_grid_rows)
    ordered_grid_rows = sorted(
        rebound_grid_rows,
        key=lambda row: (
            row["canonical_row_payload"]["block_length"],
            row["canonical_row_payload"]["schedule_ordinal"],
        ),
    )
    grid_hashes = tuple(row["record_hash"] for row in ordered_grid_rows)
    assessment_hash = replace_row(
        "data_snooping_assessments",
        {
            "family_id": payload["family_id"],
            "closure_manifest_hash": closure_hash,
            "ordered_latest_grid_assessment_hashes_json": _canonical_json_text(
                grid_hashes
            ),
            "ordered_latest_grid_assessment_hashes_hash": canonical_sha256(grid_hashes),
            "candidate_order_hash": canonical_sha256(config_hashes),
            "period_count": len(period_payloads) if period_payloads else None,
            "assessment_status": payload["assessment_status"],
            "diagnostic_verdict": payload["diagnostic_verdict"],
            "promotion_effect": payload["promotion_effect"],
            "failure_reasons_json": _canonical_json_text(payload["failure_reasons"]),
        },
    )
    rows = [
        by_table["data_snooping_declarations"]
        if row["table_name"] == "data_snooping_declarations"
        else by_table["data_snooping_family_freezes"]
        if row["table_name"] == "data_snooping_family_freezes"
        else by_table["data_snooping_family_closures"]
        if row["table_name"] == "data_snooping_family_closures"
        else by_table["data_snooping_assessments"]
        if row["table_name"] == "data_snooping_assessments"
        else row
        for row in rows
    ]
    rows.extend(
        (
            search_row,
            *registration_rows,
            *terminal_rows,
            *hypothesis_rows,
            seal_row,
            *disposition_rows,
        )
    )
    chain_heads = [
        head
        for head in projection["chain_heads"]
        if head["table_name"]
        not in {
            "data_snooping_grid_assessments",
            "data_snooping_family_searches",
            "data_snooping_proposal_registrations",
        }
    ]
    chain_heads.extend(grid_heads.values())
    chain_heads.append(
        {
            "table_name": "data_snooping_family_searches",
            "partition_key": {"family_id": payload["family_id"]},
            "row_count": 1,
            "head_record_hash": search_hash,
        }
    )
    if registration_rows:
        chain_heads.append(
            {
                "table_name": "data_snooping_proposal_registrations",
                "partition_key": {
                    "family_id": payload["family_id"],
                    "search_id": search_id,
                },
                "row_count": len(registration_rows),
                "head_record_hash": registration_rows[-1]["record_hash"],
            }
        )
    rebound_projection = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=chain_heads,
    )
    payload.update(
        {
            "declaration_record_hash": declaration_hash,
            "freeze_record_hash": freeze_hash,
            "closure_manifest_hash": closure_hash,
            "ordered_grid_assessment_record_hashes": grid_hashes,
            "family_assessment_conclusion_hash": assessment_hash,
            "authority_projection": rebound_projection,
            "authority_projection_hash": canonical_sha256(rebound_projection),
            "pre_publication_research_authority_head": next(
                head["head_record_hash"]
                for head in rebound_projection["chain_heads"]
                if head["table_name"] == "research_authority_events"
            ),
        }
    )
    return rebound_projection


def _projection_with_inverse_grid_primary_key_order() -> tuple[dict[str, Any], tuple[str, ...]]:
    projection = _core_payload()["authority_projection"]
    genesis_hash = projection["genesis_record_hash"]
    closure_hash = next(
        row["record_hash"]
        for row in projection["canonical_rows"]
        if row["table_name"] == "data_snooping_family_closures"
    )
    rows = [
        row
        for row in projection["canonical_rows"]
        if row["table_name"] != "data_snooping_grid_assessments"
    ]
    chain_heads = [
        head
        for head in projection["chain_heads"]
        if head["table_name"] != "data_snooping_grid_assessments"
    ]
    first_payload = _complete_authority_row_payload(
        "data_snooping_grid_assessments",
        record_type="DATA_SNOOPING_GRID_ASSESSMENT_V1",
        authority_instance_id="family-authority-1",
        genesis_record_hash=genesis_hash,
        grid_assessment_id="grid-z-first",
        family_id="family-1",
        closure_manifest_hash=closure_hash,
        block_length=5,
        schedule_ordinal=1,
        prev_record_hash=genesis_hash,
        promotion_effect="NONE",
    )
    first_hash = canonical_sha256(first_payload)
    second_payload = {
        **first_payload,
        "grid_assessment_id": "grid-a-second",
        "schedule_ordinal": 2,
        "prev_record_hash": first_hash,
    }
    second_hash = canonical_sha256(second_payload)
    partition = {
        "authority_instance_id": "family-authority-1",
        "family_id": "family-1",
        "block_length": 5,
    }
    rows.extend(
        (
            {
                "table_name": "data_snooping_grid_assessments",
                "primary_key": {"grid_assessment_id": "grid-z-first"},
                "canonical_row_payload": first_payload,
                "record_hash": first_hash,
                "chain_partition_key": partition,
                "chain_ordinal": 1,
                "chain_previous_record_hash": genesis_hash,
            },
            {
                "table_name": "data_snooping_grid_assessments",
                "primary_key": {"grid_assessment_id": "grid-a-second"},
                "canonical_row_payload": second_payload,
                "record_hash": second_hash,
                "chain_partition_key": partition,
                "chain_ordinal": 2,
                "chain_previous_record_hash": first_hash,
            },
        )
    )
    chain_heads.append(
        {
            "table_name": "data_snooping_grid_assessments",
            "partition_key": partition,
            "row_count": 2,
            "head_record_hash": second_hash,
        }
    )
    return (
        _rebuild_authority_projection(
            projection,
            rows=rows,
            chain_heads=chain_heads,
        ),
        (first_hash, second_hash),
    )


_NON_GRID_CHAIN_CASES = (
    (
        "research_authority_events",
        "event_id",
        {"authority_instance_id": "family-authority-1"},
        "event_ordinal",
        1,
    ),
    (
        "selection_decisions",
        "selection_decision_id",
        {
            "authority_instance_id": "family-authority-1",
            "selection_scope_id": "scope-1",
        },
        "decision_ordinal",
        1,
    ),
    (
        "global_oos_exposures",
        "oos_exposure_id",
        {
            "authority_instance_id": "family-authority-1",
            "selection_scope_id": "scope-1",
        },
        "exposure_ordinal",
        1,
    ),
    (
        "data_snooping_family_searches",
        "search_id",
        {"family_id": "family-1"},
        "link_ordinal",
        1,
    ),
    (
        "data_snooping_proposal_registrations",
        "proposal_id",
        {"family_id": "family-1", "search_id": "search-1"},
        "emission_ordinal",
        1,
    ),
    (
        "data_snooping_oos_access_attempts",
        "attempt_event_id",
        {
            "authority_instance_id": "family-authority-1",
            "attempt_id": "attempt-1",
        },
        "sequence_no",
        0,
    ),
)


def _projection_with_non_grid_chain(
    case: tuple[str, str, dict[str, Any], str, int],
    *,
    tamper_second_predecessor: bool = False,
) -> dict[str, Any]:
    table_name, primary_key_field, partition, ordinal_field, first_ordinal = case
    projection = _core_payload()["authority_projection"]
    genesis_hash = projection["genesis_record_hash"]
    original_table_rows = [
        row for row in projection["canonical_rows"] if row["table_name"] == table_name
    ]
    rows = [
        row for row in projection["canonical_rows"] if row["table_name"] != table_name
    ]
    chain_heads = [
        head for head in projection["chain_heads"] if head["table_name"] != table_name
    ]
    preserve_research_ancestor = table_name == "research_authority_events"
    if preserve_research_ancestor:
        assert len(original_table_rows) == 1
        rows.extend(original_table_rows)
        previous_hash = original_table_rows[0]["record_hash"]
        added_rows = []
        offsets = range(1, 2)
    else:
        previous_hash = genesis_hash
        added_rows = []
        offsets = range(2)
    for offset in offsets:
        ordinal = first_ordinal + offset
        row_previous_hash = (
            "f" * 64 if tamper_second_predecessor and offset == 1 else previous_hash
        )
        primary_key_value = f"{table_name}-{ordinal}"
        row_overrides = {
            "authority_instance_id": "family-authority-1",
            "genesis_record_hash": genesis_hash,
            primary_key_field: primary_key_value,
            **partition,
            ordinal_field: ordinal,
            "prev_record_hash": row_previous_hash,
        }
        payload = _complete_authority_row_payload(
            table_name,
            **row_overrides,
        )
        if "family_id" in payload:
            payload["family_id"] = "family-1"
        record_hash = canonical_sha256(payload)
        added_rows.append(
            {
                "table_name": table_name,
                "primary_key": {primary_key_field: primary_key_value},
                "canonical_row_payload": payload,
                "record_hash": record_hash,
                "chain_partition_key": partition,
                "chain_ordinal": ordinal,
                "chain_previous_record_hash": row_previous_hash,
            }
        )
        previous_hash = record_hash
    rows.extend(added_rows)
    chain_heads.append(
        {
            "table_name": table_name,
            "partition_key": partition,
            "row_count": 2,
            "head_record_hash": added_rows[-1]["record_hash"],
        }
    )
    rebuilt = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=chain_heads,
    )
    return rebuilt


def _clean_public_proposals() -> tuple[dict[str, Any], ...]:
    proposals: list[dict[str, Any]] = []
    for ordinal in (1, 2):
        canonical_proposal = {
            "alpha_kind": "MOMENTUM",
            "lookback_periods": 10 * ordinal,
        }
        proposals.append(
            {
            "projection_kind": "NON_QUARANTINED",
            "proposal_id": str(ordinal) * 64,
            "disposition": "PERFORMANCE_EVALUATED",
            "media_type": "application/json",
            "byte_length": len(canonical_json_bytes(canonical_proposal)),
            "dlp_policy_id": "DLP_POLICY_V1",
            "dlp_policy_version": "1",
            "registration_record_hash": "0" * 64,
            "canonical_proposal_hash": canonical_sha256(canonical_proposal),
            "canonical_proposal": canonical_proposal,
            }
        )
    return tuple(proposals)


def _quarantined_public_proposal() -> dict[str, Any]:
    return {
        "projection_kind": "QUARANTINED",
        "proposal_id": "9" * 64,
        "disposition": "SECRET_QUARANTINED",
        "registration_record_hash": "0" * 64,
        "quarantine_record_id": f"qtn_{'a' * 32}",
        "length_bucket": "LE_1_KIB",
        "media_type": "application/json",
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "reason_code": "DLP_SECRET_DETECTED",
    }


def _core_payload(**overrides: Any) -> dict[str, Any]:
    authority_projection = _authority_projection()
    genesis_hash = authority_projection["genesis_record_hash"]
    projected_by_table = {
        row["table_name"]: row for row in authority_projection["canonical_rows"]
    }
    projected_grid_hashes = tuple(
        row["record_hash"]
        for row in authority_projection["canonical_rows"]
        if row["table_name"] == "data_snooping_grid_assessments"
    )
    performance = _standard_performance_inputs()
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_ARTIFACT_CORE_V1",
        "artifact_kind": "DATA_SNOOPING_FAMILY_ARTIFACT_V1",
        "artifact_schema_version": "DATA_SNOOPING_ARTIFACT_CORE_V1",
        "canonical_json_version": "QUANTOS_CANONICAL_JSON_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": genesis_hash,
        "authority_lineage_hash": _lineage_hash("family-authority-1", genesis_hash),
        "family_id": "family-1",
        "selection_scope_id": "scope-1",
        "selection_decision_id": "decision-1",
        "winner_config_hash": "7" * 64,
        "winner_evaluation_receipt_hash": "b" * 64,
        "decision_class": "PUBLISHABLE",
        "declaration_record_hash": projected_by_table["data_snooping_declarations"]["record_hash"],
        "freeze_record_hash": projected_by_table["data_snooping_family_freezes"]["record_hash"],
        "closure_manifest_hash": projected_by_table["data_snooping_family_closures"]["record_hash"],
        "closure_status": "COMPLETE",
        "assessment_status": "ASSESSED",
        "diagnostic_verdict": "DOES_NOT_SUPPORT_SUPERIORITY",
        "promotion_effect": "NONE",
        "ordered_grid_assessment_record_hashes": projected_grid_hashes,
        "family_assessment_conclusion_hash": projected_by_table["data_snooping_assessments"]["record_hash"],
        "authority_projection": authority_projection,
        "authority_projection_hash": canonical_sha256(authority_projection),
        "pre_publication_research_authority_head": next(
            head["head_record_hash"]
            for head in authority_projection["chain_heads"]
            if head["table_name"] == "research_authority_events"
        ),
        "public_proposals": _clean_public_proposals(),
        "candidate_disposition_record_hashes": ("4" * 64, "5" * 64),
        "ordered_period_keys": performance["ordered_period_keys"],
        "ordered_candidate_config_hashes": ("6" * 64, "7" * 64),
        "candidate_gross_returns": performance["candidate_gross_returns"],
        "candidate_costs": performance["candidate_costs"],
        "candidate_net_returns": performance["candidate_net_returns"],
        "candidate_turnovers": performance["candidate_turnovers"],
        "benchmark_gross_returns": performance["benchmark_gross_returns"],
        "benchmark_costs": performance["benchmark_costs"],
        "benchmark_net_returns": performance["benchmark_net_returns"],
        "benchmark_turnovers": performance["benchmark_turnovers"],
        "evaluation_batch_receipt_hash": "8" * 64,
        "benchmark_receipt_hash": "9" * 64,
        "ordered_candidate_receipt_hashes": ("a" * 64, "b" * 64),
        "trial_ledger_proof_hash": "c" * 64,
        "failure_reasons": (),
    }
    _bind_complete_exact_evidence(payload)
    _rebind_projection_to_core_payload(payload, authority_projection)
    payload.update(overrides)
    return payload


def _not_assessable_core_payload(**overrides: Any) -> dict[str, Any]:
    authority_projection = _authority_projection(include_grid=False)
    projected_by_table = {
        row["table_name"]: row for row in authority_projection["canonical_rows"]
    }
    disposition_evidence = (_quarantined_disposition_evidence(),)
    trial_evidence = _trial_ledger_proof_evidence()
    payload = _core_payload(
        decision_class="DIAGNOSTIC_ONLY",
        closure_status="NOT_ASSESSABLE",
        assessment_status="NOT_ASSESSABLE",
        diagnostic_verdict="NOT_APPLICABLE",
        public_proposals=(_quarantined_public_proposal(),),
        selection_scope_evidence=_selection_scope_evidence(),
        selection_decision_evidence=_selection_decision_evidence("DIAGNOSTIC_ONLY"),
        evaluation_batch_evidence=None,
        ordered_candidate_receipt_evidence=(),
        benchmark_receipt_evidence=None,
        start_execution_attestation_evidence=None,
        terminal_execution_attestation_evidence=None,
        immutable_artifact_lease_evidence=None,
        trial_ledger_proof_evidence=trial_evidence,
        ordered_candidate_disposition_evidence=disposition_evidence,
        candidate_disposition_record_hashes=tuple(
            _disposition_projection_row(
                evidence,
                recorded_at_us=1_700_120_000_000_001,
            )["record_hash"]
            for evidence in disposition_evidence
        ),
        ordered_period_keys=(),
        ordered_candidate_config_hashes=(),
        winner_config_hash=None,
        winner_evaluation_receipt_hash=None,
        candidate_gross_returns=(),
        candidate_costs=(),
        candidate_net_returns=(),
        candidate_turnovers=(),
        benchmark_gross_returns=(),
        benchmark_costs=(),
        benchmark_net_returns=(),
        benchmark_turnovers=(),
        evaluation_batch_receipt_hash=None,
        benchmark_receipt_hash=None,
        ordered_candidate_receipt_hashes=(),
        trial_ledger_proof_hash=trial_evidence["canonical_payload"]["proof_hash"],
        ordered_grid_assessment_record_hashes=(),
        declaration_record_hash=projected_by_table["data_snooping_declarations"]["record_hash"],
        freeze_record_hash=projected_by_table["data_snooping_family_freezes"]["record_hash"],
        closure_manifest_hash=projected_by_table["data_snooping_family_closures"]["record_hash"],
        family_assessment_conclusion_hash=projected_by_table["data_snooping_assessments"]["record_hash"],
        authority_projection=authority_projection,
        authority_projection_hash=canonical_sha256(authority_projection),
        failure_reasons=("SECRET_QUARANTINED",),
    )
    payload.update(overrides)
    _rebind_projection_to_core_payload(payload, authority_projection)
    return payload


def _complete_not_assessable_core_payload(**overrides: Any) -> dict[str, Any]:
    payload = _core_payload()
    payload.update(
        {
            "decision_class": "DIAGNOSTIC_ONLY",
            "closure_status": "COMPLETE",
            "assessment_status": "NOT_ASSESSABLE",
            "diagnostic_verdict": "NOT_APPLICABLE",
            "failure_reasons": ("KERNEL_NOT_ASSESSABLE",),
        }
    )
    _bind_complete_exact_evidence(payload)
    payload.update(overrides)
    _rebind_projection_to_core_payload(payload, payload["authority_projection"])
    return payload


def test_artifact_core_exact_bytes_are_deterministic_and_have_no_self_hash() -> None:
    contract = _contract("ArtifactCore")
    left = contract(**_core_payload())
    right = contract(**_core_payload())

    left_bytes = canonical_json_bytes(left)
    assert left_bytes == canonical_json_bytes(right)
    assert left_bytes.endswith(b"\n") and b"\r" not in left_bytes
    assert sha256_bytes(left_bytes) == sha256_bytes(canonical_json_bytes(right))
    assert "artifact_core_hash" not in type(left).model_fields
    assert "data_snooping_evidence_ref" not in type(left).model_fields


def test_artifact_core_accepts_only_honest_assessed_or_not_assessable_branches() -> None:
    artifact_core = _contract("ArtifactCore")
    assessed = artifact_core(**_core_payload())
    blocked = artifact_core(**_not_assessable_core_payload())

    assert assessed.assessment_status == "ASSESSED"
    assert assessed.closure_status == "COMPLETE"
    assert assessed.promotion_effect == "NONE"
    assert assessed.family_assessment_conclusion_hash not in assessed.ordered_grid_assessment_record_hashes
    assert len(assessed.ordered_candidate_config_hashes) == 2
    assert blocked.assessment_status == "NOT_ASSESSABLE"
    assert blocked.closure_status == "NOT_ASSESSABLE"
    assert blocked.diagnostic_verdict == "NOT_APPLICABLE"
    assert blocked.failure_reasons == ("SECRET_QUARANTINED",)
    assert blocked.ordered_period_keys == ()
    assert blocked.promotion_effect == "NONE"


def test_artifact_core_requires_registration_terminal_result_and_public_order_totality() -> None:
    artifact_core = _contract("ArtifactCore")
    payload = _core_payload()
    projection = payload["authority_projection"]

    rows = [
        row
        for row in projection["canonical_rows"]
        if not (
            row["table_name"] == "data_snooping_proposals"
            and row["canonical_row_payload"]["proposal_id"] == "1" * 64
        )
    ]
    missing_result = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=list(projection["chain_heads"]),
    )
    with pytest.raises(ValidationError):
        artifact_core(
            **{
                **payload,
                "authority_projection": missing_result,
                "authority_projection_hash": canonical_sha256(missing_result),
            }
        )

    with pytest.raises(ValidationError):
        artifact_core(
            **{
                **payload,
                "public_proposals": tuple(reversed(payload["public_proposals"])),
            }
        )


def test_terminal_proposal_projection_forbids_registration_chain_metadata() -> None:
    artifact_core = _contract("ArtifactCore")
    payload = _core_payload()
    projection = payload["authority_projection"]
    rows = list(projection["canonical_rows"])
    terminal = next(
        row
        for row in rows
        if row["table_name"] == "data_snooping_proposals"
    )
    rows[rows.index(terminal)] = {
        **terminal,
        "chain_partition_key": {
            "family_id": "family-1",
            "search_id": "search-1",
        },
        "chain_ordinal": terminal["canonical_row_payload"]["emission_ordinal"],
        "chain_previous_record_hash": _genesis_payload()["record_hash"],
    }
    forged = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=list(projection["chain_heads"]),
    )
    with pytest.raises(ValidationError):
        artifact_core(
            **{
                **payload,
                "authority_projection": forged,
                "authority_projection_hash": canonical_sha256(forged),
            }
        )


def test_empty_candidate_head_is_domain_separated_and_not_a_projection_chain_head() -> None:
    artifact_core = _contract("ArtifactCore")
    payload = _not_assessable_core_payload()
    core = artifact_core(**payload)
    seal = next(
        row
        for row in core.authority_projection.canonical_rows
        if row.table_name == "data_snooping_search_emission_seals"
    )
    expected = canonical_sha256(
        {
            "domain_separator": "QUANTOS_EMPTY_SEARCH_CANDIDATE_CHAIN_V1",
            "authority_instance_id": core.authority_instance_id,
            "family_id": core.family_id,
            "search_id": seal.canonical_row_payload["search_id"],
        }
    )
    assert seal.canonical_row_payload["candidate_count"] == 0
    assert seal.canonical_row_payload["candidate_chain_head"] == expected
    assert all(
        head.head_record_hash != expected
        for head in core.authority_projection.chain_heads
    )

    projection = payload["authority_projection"]
    rows = list(projection["canonical_rows"])
    index = next(
        index
        for index, row in enumerate(rows)
        if row["table_name"] == "data_snooping_search_emission_seals"
    )
    tampered_payload = {
        **rows[index]["canonical_row_payload"],
        "candidate_chain_head": "f" * 64,
    }
    rows[index] = {
        **rows[index],
        "canonical_row_payload": tampered_payload,
        "record_hash": canonical_sha256(tampered_payload),
    }
    forged = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=list(projection["chain_heads"]),
    )
    with pytest.raises(ValidationError):
        artifact_core(
            **{
                **payload,
                "authority_projection": forged,
                "authority_projection_hash": canonical_sha256(forged),
            }
        )


def test_zero_raw_proposals_use_an_empty_seal_without_inventing_registration_head() -> None:
    payload = _not_assessable_core_payload(
        public_proposals=(),
        ordered_candidate_disposition_evidence=(),
        candidate_disposition_record_hashes=(),
    )
    core = _contract("ArtifactCore")(**payload)
    assert not any(
        row.table_name == "data_snooping_proposal_registrations"
        for row in core.authority_projection.canonical_rows
    )
    assert not any(
        head.table_name == "data_snooping_proposal_registrations"
        for head in core.authority_projection.chain_heads
    )


def test_artifact_core_accepts_legitimate_zero_proposal_not_assessable_closure() -> None:
    payload = _not_assessable_core_payload()
    payload.update(
        {
            "public_proposals": (),
            "ordered_candidate_disposition_evidence": (),
            "candidate_disposition_record_hashes": (),
            "failure_reasons": ("NO_PROPOSALS_EMITTED",),
        }
    )
    _rebind_projection_to_core_payload(payload, payload["authority_projection"])

    core = _contract("ArtifactCore")(**payload)
    freeze = next(
        row.canonical_row_payload
        for row in core.authority_projection.canonical_rows
        if row.table_name == "data_snooping_family_freezes"
    )
    closure = next(
        row.canonical_row_payload
        for row in core.authority_projection.canonical_rows
        if row.table_name == "data_snooping_family_closures"
    )
    assert freeze["search_count"] > 0
    assert freeze["raw_proposal_count"] == 0
    assert closure["raw_proposal_count"] == 0
    assert core.public_proposals == ()
    assert core.assessment_status == "NOT_ASSESSABLE"


def test_artifact_core_rejects_projection_without_exact_disposition_rows() -> None:
    payload = _core_payload()
    projection = payload["authority_projection"]
    rows = [
        row
        for row in projection["canonical_rows"]
        if row["table_name"] != "data_snooping_candidate_dispositions"
    ]
    forged = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=list(projection["chain_heads"]),
    )
    payload.update(
        {
            "authority_projection": forged,
            "authority_projection_hash": canonical_sha256(forged),
        }
    )
    with pytest.raises(ValidationError, match="disposition"):
        _contract("ArtifactCore")(**payload)


def test_artifact_core_rejects_rehashed_foreign_family_parent_row() -> None:
    payload = _core_payload()
    projection = payload["authority_projection"]
    rows = list(projection["canonical_rows"])
    declaration_index = next(
        index
        for index, row in enumerate(rows)
        if row["table_name"] == "data_snooping_declarations"
    )
    declaration = rows[declaration_index]
    canonical_row_payload = {
        **declaration["canonical_row_payload"],
        "family_id": "foreign-family",
    }
    declaration_hash = canonical_sha256(canonical_row_payload)
    rows[declaration_index] = {
        **declaration,
        "canonical_row_payload": canonical_row_payload,
        "record_hash": declaration_hash,
    }
    forged = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=list(projection["chain_heads"]),
    )
    payload.update(
        {
            "declaration_record_hash": declaration_hash,
            "authority_projection": forged,
            "authority_projection_hash": canonical_sha256(forged),
        }
    )
    with pytest.raises(ValidationError, match="foreign family"):
        _contract("ArtifactCore")(**payload)


def test_selection_decision_class_and_assessment_status_are_orthogonal() -> None:
    artifact_core = _contract("ArtifactCore")
    publishable_assessed = artifact_core(**_core_payload())
    publishable_not_assessable = artifact_core(
        **_not_assessable_core_payload(
            decision_class="PUBLISHABLE",
            selection_decision_evidence=_selection_decision_evidence("PUBLISHABLE"),
        )
    )
    diagnostic_payload = _core_payload()
    diagnostic_payload.update(
        {
            "decision_class": "DIAGNOSTIC_ONLY",
            "selection_decision_evidence": _selection_decision_evidence(
                "DIAGNOSTIC_ONLY"
            ),
        }
    )
    _rebind_projection_to_core_payload(
        diagnostic_payload,
        diagnostic_payload["authority_projection"],
    )
    diagnostic_assessed = artifact_core(**diagnostic_payload)
    diagnostic_not_assessable = artifact_core(**_not_assessable_core_payload())

    assert (
        publishable_assessed.decision_class,
        publishable_assessed.assessment_status,
    ) == ("PUBLISHABLE", "ASSESSED")
    assert (
        publishable_not_assessable.decision_class,
        publishable_not_assessable.assessment_status,
    ) == ("PUBLISHABLE", "NOT_ASSESSABLE")
    assert (diagnostic_assessed.decision_class, diagnostic_assessed.assessment_status) == (
        "DIAGNOSTIC_ONLY",
        "ASSESSED",
    )
    assert (
        diagnostic_not_assessable.decision_class,
        diagnostic_not_assessable.assessment_status,
    ) == ("DIAGNOSTIC_ONLY", "NOT_ASSESSABLE")

    for core in (
        publishable_assessed,
        publishable_not_assessable,
        diagnostic_assessed,
        diagnostic_not_assessable,
    ):
        receipt = _contract("ArtifactPublicationReceipt")(
            **_publication_receipt_payload(core=core)
        )
        assert receipt.decision_class == core.decision_class
        assert receipt.assessment_status == core.assessment_status


@pytest.mark.parametrize(
    "mutation",
    (
        {"authority_lineage_hash": "f" * 64},
        {"authority_projection_hash": "f" * 64},
        {"closure_status": "NOT_ASSESSABLE"},
        {"ordered_grid_assessment_record_hashes": ()},
        {"ordered_grid_assessment_record_hashes": ("a" * 64, "a" * 64)},
        {
            "ordered_grid_assessment_record_hashes": ("a" * 64, "d" * 64),
            "family_assessment_conclusion_hash": "d" * 64,
        },
        {"promotion_effect": "PROMOTE"},
        {"diagnostic_verdict": "NOT_APPLICABLE"},
        {"failure_reasons": ("A_REASON",)},
        {"candidate_net_returns": ()},
        {"ordered_candidate_receipt_hashes": ("a" * 64,)},
        {"winner_config_hash": "6" * 64},
        {"winner_evaluation_receipt_hash": "a" * 64},
    ),
)
def test_assessed_core_rejects_lineage_branch_or_matrix_drift(mutation: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(**_core_payload(**mutation))


@pytest.mark.parametrize(
    "mutation",
    (
        {"failure_reasons": ()},
        {"diagnostic_verdict": "DOES_NOT_SUPPORT_SUPERIORITY"},
        {"promotion_effect": "PROMOTE"},
        {"ordered_period_keys": _periods()},
        {"evaluation_batch_receipt_hash": "8" * 64},
        {"ordered_candidate_config_hashes": ("6" * 64,)},
        {"decision_class": "PUBLISHABLE"},
    ),
)
def test_not_assessable_core_rejects_invented_results_or_promotion(mutation: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(**_not_assessable_core_payload(**mutation))


def test_family_conclusion_is_not_the_effective_grid_row() -> None:
    core = _contract("ArtifactCore")(**_core_payload())

    assert len(core.ordered_grid_assessment_record_hashes) == 1
    assert core.family_assessment_conclusion_hash not in core.ordered_grid_assessment_record_hashes


def test_complete_not_assessable_core_keeps_exact_evidence_and_partial_grid_history() -> None:
    core = _contract("ArtifactCore")(**_complete_not_assessable_core_payload())

    assert len(core.ordered_grid_assessment_record_hashes) == 1
    assert core.family_assessment_conclusion_hash not in core.ordered_grid_assessment_record_hashes
    assert core.closure_status == "COMPLETE"
    assert (
        core.evaluation_batch_receipt_hash
        == core.evaluation_batch_evidence.canonical_payload["batch_receipt_hash"]
    )
    assert len(core.ordered_candidate_receipt_hashes) == 2
    assert core.winner_config_hash == "7" * 64
    assert core.winner_evaluation_receipt_hash == core.ordered_candidate_receipt_hashes[1]


@pytest.mark.parametrize(
    "mutation",
    (
        {"evaluation_batch_receipt_hash": None},
        {"benchmark_receipt_hash": None},
        {"trial_ledger_proof_hash": None},
        {"ordered_candidate_receipt_hashes": ()},
        {"benchmark_net_returns": ()},
        {"failure_reasons": ()},
        {"decision_class": "PUBLISHABLE"},
    ),
)
def test_complete_not_assessable_core_rejects_missing_exact_evidence(
    mutation: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(**_complete_not_assessable_core_payload(**mutation))


def test_authority_projection_rows_bind_safe_exact_canonical_payloads() -> None:
    artifact_core = _contract("ArtifactCore")
    projection = _authority_projection()
    core = artifact_core(**_core_payload())
    assert core.authority_projection.canonical_rows

    tampered_projection = dict(projection)
    rows = list(projection["canonical_rows"])
    rows[0] = {
        **rows[0],
        "canonical_row_payload": {
            **rows[0]["canonical_row_payload"],
            "declaration_id": "tampered-declaration",
        },
    }
    tampered_projection["canonical_rows"] = tuple(rows)
    with pytest.raises(ValidationError):
        artifact_core(
            **_core_payload(
                authority_projection=tampered_projection,
                authority_projection_hash=canonical_sha256(tampered_projection),
            )
        )


def test_winner_uses_exact_mean_net_differential_and_config_hash_tie_break() -> None:
    periods = _periods()
    tied_gross = tuple(float64_hex(0.01) for _ in periods)
    costs = tuple(float64_hex(0.001) for _ in periods)
    tied_net = tuple(
        float64_hex(float.fromhex(gross) - float.fromhex(cost))
        for gross, cost in zip(tied_gross, costs)
    )
    turnovers = tuple(float64_hex(0.1) for _ in periods)
    payload = _core_payload(
        candidate_gross_returns=(tied_gross, tied_gross),
        candidate_costs=(costs, costs),
        candidate_net_returns=(tied_net, tied_net),
        candidate_turnovers=(turnovers, turnovers),
        winner_config_hash="6" * 64,
    )
    _bind_complete_exact_evidence(payload)
    _rebind_projection_to_core_payload(payload, payload["authority_projection"])
    core = _contract("ArtifactCore")(**payload)

    assert core.winner_config_hash == min(core.ordered_candidate_config_hashes)
    assert core.winner_evaluation_receipt_hash == core.ordered_candidate_receipt_hashes[0]


def test_authority_projection_rows_reject_credential_shaped_payloads() -> None:
    artifact_core = _contract("ArtifactCore")
    projection = _authority_projection()
    rows = list(projection["canonical_rows"])
    unsafe_payload = {
        **rows[0]["canonical_row_payload"],
        "api_key": "must-not-be-public",
    }
    rows[0] = {
        **rows[0],
        "canonical_row_payload": unsafe_payload,
        "record_hash": canonical_sha256(unsafe_payload),
    }
    projection["canonical_rows"] = tuple(rows)
    projection["ordered_row_hashes"] = tuple(row["record_hash"] for row in rows)
    projection["ordered_row_hashes_hash"] = canonical_sha256(projection["ordered_row_hashes"])
    with pytest.raises(ValidationError):
        artifact_core(
            **_core_payload(
                authority_projection=projection,
                authority_projection_hash=canonical_sha256(projection),
            )
        )


def test_authority_projection_replays_only_real_partitioned_grid_chains() -> None:
    artifact_core = _contract("ArtifactCore")
    projection = _authority_projection()
    rows = list(projection["canonical_rows"])

    declaration_index = next(
        index
        for index, row in enumerate(rows)
        if row["table_name"] == "data_snooping_declarations"
    )
    rows[declaration_index] = {
        **rows[declaration_index],
        "chain_partition_key": {"authority_instance_id": "family-authority-1"},
        "chain_ordinal": 1,
        "chain_previous_record_hash": projection["genesis_record_hash"],
    }
    forged = {
        **projection,
        "canonical_rows": tuple(rows),
    }
    with pytest.raises(ValidationError):
        artifact_core(
            **_core_payload(
                authority_projection=forged,
                authority_projection_hash=canonical_sha256(forged),
            )
        )

    wrong_heads = {
        **projection,
        "chain_heads": (
            {
                **projection["chain_heads"][0],
                "head_record_hash": "f" * 64,
            },
        ),
    }
    wrong_heads["chain_heads_hash"] = canonical_sha256(wrong_heads["chain_heads"])
    with pytest.raises(ValidationError):
        artifact_core(
            **_core_payload(
                authority_projection=wrong_heads,
                authority_projection_hash=canonical_sha256(wrong_heads),
            )
        )


def test_projection_replays_grid_chain_by_ordinal_not_primary_key_byte_order() -> None:
    projection, grid_hashes = _projection_with_inverse_grid_primary_key_order()
    core = _contract("ArtifactCore")(
        **_core_payload(
            authority_projection=projection,
            authority_projection_hash=canonical_sha256(projection),
            ordered_grid_assessment_record_hashes=grid_hashes,
        )
    )

    projected_grid_rows = tuple(
        row
        for row in core.authority_projection.canonical_rows
        if row.table_name == "data_snooping_grid_assessments"
    )
    assert tuple(row.chain_ordinal for row in projected_grid_rows) == (2, 1)
    assert core.ordered_grid_assessment_record_hashes == grid_hashes


def test_projection_primary_key_is_exact_canonical_structure_with_composite_support() -> None:
    projection = _core_payload()["authority_projection"]
    core = _contract("ArtifactCore")(
        **_core_payload(
            authority_projection=projection,
            authority_projection_hash=canonical_sha256(projection),
        )
    )
    composite = next(
        row
        for row in core.authority_projection.canonical_rows
        if row.table_name == "data_snooping_candidate_dispositions"
    )
    assert set(composite.primary_key) == {"family_id", "proposal_id"}

    rows = list(projection["canonical_rows"])
    composite_index = next(
        index
        for index, row in enumerate(rows)
        if row["table_name"] == "data_snooping_candidate_dispositions"
    )
    rows[composite_index] = {
        **rows[composite_index],
        "primary_key": {"proposal_id": "8" * 64},
    }
    forged = _rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=list(projection["chain_heads"]),
    )
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(
            **_core_payload(
                authority_projection=forged,
                authority_projection_hash=canonical_sha256(forged),
            )
        )


@pytest.mark.parametrize("chain_case", _NON_GRID_CHAIN_CASES)
def test_projection_replays_each_official_non_grid_chain_partition(
    chain_case: tuple[str, str, dict[str, Any], str, int],
) -> None:
    projection = _projection_with_non_grid_chain(chain_case)
    projection_contract = _contract("ArtifactCore").model_fields[
        "authority_projection"
    ].annotation
    core = projection_contract(**projection)

    table_name = chain_case[0]
    rows = tuple(
        row for row in core.canonical_rows if row.table_name == table_name
    )
    assert len(rows) == 2
    assert {head.table_name for head in core.chain_heads} >= {
        table_name,
        "data_snooping_grid_assessments",
    }


@pytest.mark.parametrize("chain_case", _NON_GRID_CHAIN_CASES)
def test_projection_rejects_predecessor_tamper_in_each_non_grid_chain_partition(
    chain_case: tuple[str, str, dict[str, Any], str, int],
) -> None:
    projection = _projection_with_non_grid_chain(
        chain_case,
        tamper_second_predecessor=True,
    )
    with pytest.raises(ValidationError):
        projection_contract = _contract("ArtifactCore").model_fields[
            "authority_projection"
        ].annotation
        projection_contract(**projection)


@pytest.mark.parametrize(
    "field",
    (
        "declaration_record_hash",
        "freeze_record_hash",
        "closure_manifest_hash",
        "family_assessment_conclusion_hash",
    ),
)
def test_artifact_core_top_level_parent_hashes_must_equal_exact_projected_rows(
    field: str,
) -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(**_core_payload(**{field: "f" * 64}))


def test_artifact_core_grid_vector_must_equal_all_projected_grid_rows_in_order() -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(
            **_core_payload(ordered_grid_assessment_record_hashes=("f" * 64,))
        )

def test_c9_contract_module_supports_direct_import_without_research_cycle() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import quantos.contracts.data_snooping_c9"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("unsafe_field", "unsafe_value"),
    (
        ("raw_envelope_hash", "f" * 64),
        ("scan_result_hash", "f" * 64),
        ("exact_byte_length", 173),
        ("restricted_blob_path", "restricted/quarantine/blob"),
        ("secret_value", "Bearer not-public"),
    ),
)
def test_quarantine_projection_forbids_secret_derived_or_exact_length_fields(
    unsafe_field: str,
    unsafe_value: object,
) -> None:
    proposal = _quarantined_public_proposal()
    proposal[unsafe_field] = unsafe_value
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(
            **_not_assessable_core_payload(public_proposals=(proposal,))
        )


def test_safe_canonical_proposal_rejects_credential_shaped_nested_fields() -> None:
    proposals = list(_clean_public_proposals())
    proposals[0] = {
        **proposals[0],
        "canonical_proposal": {
            **proposals[0]["canonical_proposal"],
            "api_key": "not-for-publication",
        },
    }
    with pytest.raises(ValidationError):
        _contract("ArtifactCore")(**_core_payload(public_proposals=tuple(proposals)))


def test_quarantine_projection_exposes_only_coarse_safe_metadata() -> None:
    core = _contract("ArtifactCore")(**_not_assessable_core_payload())
    quarantine = core.model_dump(mode="python")["public_proposals"][0]
    assert set(quarantine) == {
        "projection_kind",
        "proposal_id",
        "disposition",
        "registration_record_hash",
        "quarantine_record_id",
        "length_bucket",
        "media_type",
        "dlp_policy_id",
        "dlp_policy_version",
        "reason_code",
    }
    assert not ({"raw_envelope_hash", "scan_result_hash", "byte_length"} & set(quarantine))


def _core_scan_receipt_payload(core: Any | None = None, **overrides: Any) -> dict[str, Any]:
    core = core or _contract("ArtifactCore")(**_core_payload())
    core_bytes = canonical_json_bytes(core)
    payload: dict[str, Any] = {
        "record_type": "ARTIFACT_CORE_SCAN_RECEIPT_V1",
        "scan_receipt_schema_version": "ARTIFACT_CORE_SCAN_RECEIPT_V1",
        "scan_target": "ARTIFACT_CORE",
        "artifact_core_hash": sha256_bytes(core_bytes),
        "artifact_core_byte_length": len(core_bytes),
        "public_artifact_scan_policy_id": "PUBLIC_ARTIFACT_SCAN_V1",
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "dlp_policy_hash": "d" * 64,
        "scanner_implementation": "FAIL_CLOSED_SCANNER",
        "scanner_version": "1",
        "scan_status": "ALLOW",
        "scan_result_hash": "e" * 64,
        "scanned_at_us": 1_700_500_000_000_000,
    }
    payload.update(overrides)
    if "scan_result_hash" not in overrides:
        payload["scan_result_hash"] = _scan_result_hash(
            scan_target="ARTIFACT_CORE",
            target_sha256=payload["artifact_core_hash"],
            target_byte_length=payload["artifact_core_byte_length"],
            scan_fields={
                key: payload[key]
                for key in (
                    "public_artifact_scan_policy_id",
                    "dlp_policy_id",
                    "dlp_policy_version",
                    "dlp_policy_hash",
                    "scanner_implementation",
                    "scanner_version",
                    "scan_status",
                )
            },
        )
    return _rehash(payload, "scan_receipt_hash")


def test_core_scan_receipt_binds_allow_only_exact_core_bytes() -> None:
    core = _contract("ArtifactCore")(**_core_payload())
    receipt = _contract("ArtifactCoreScanReceipt")(**_core_scan_receipt_payload(core))
    core_bytes = canonical_json_bytes(core)
    assert receipt.scan_status == "ALLOW"
    assert receipt.artifact_core_hash == sha256_bytes(core_bytes)
    assert receipt.artifact_core_byte_length == len(core_bytes)


@pytest.mark.parametrize("status", ("DENY", "ERROR", "UNAVAILABLE"))
def test_core_scan_receipt_is_allow_only(status: str) -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactCoreScanReceipt")(**_core_scan_receipt_payload(scan_status=status))


def _envelope_payload(core: Any | None = None, receipt: Any | None = None) -> dict[str, Any]:
    core = core or _contract("ArtifactCore")(**_core_payload())
    receipt = receipt or _contract("ArtifactCoreScanReceipt")(**_core_scan_receipt_payload(core))
    return {
        "record_type": "PUBLISHED_ARTIFACT_ENVELOPE_V1",
        "envelope_schema_version": "PUBLISHED_ARTIFACT_ENVELOPE_V1",
        "artifact_core": core,
        "artifact_core_scan_receipt": receipt,
    }


def test_envelope_is_acyclic_and_rechecks_the_exact_core_scan_binding() -> None:
    envelope_contract = _contract("PublishedArtifactEnvelope")
    envelope = envelope_contract(**_envelope_payload())
    envelope_bytes = canonical_json_bytes(envelope)
    assert envelope_bytes == canonical_json_bytes(envelope_contract(**_envelope_payload()))
    assert "published_artifact_envelope_hash" not in type(envelope).model_fields
    assert "envelope_scan_result_hash" not in type(envelope).model_fields
    assert "publication_projection_hash" not in type(envelope).model_fields

    wrong_receipt = envelope.artifact_core_scan_receipt.model_copy(
        update={"artifact_core_hash": "f" * 64}
    )
    with pytest.raises(ValidationError):
        envelope_contract(**_envelope_payload(receipt=wrong_receipt))


def _genesis_payload() -> dict[str, Any]:
    activation_evidence_hash = _code_manifest_evidence_pair()[0]["canonical_payload"][
        "evidence_hash"
    ]
    payload: dict[str, Any] = {
        "singleton_key": 1,
        "authority_instance_id": "family-authority-1",
        "v2_origin_fingerprint": "1" * 64,
        "sealed_v3_manifest_hash": sha256_bytes(_schema_manifest_bytes()),
        "activation_code_manifest_evidence_hash": activation_evidence_hash,
        "migration_id": "FAMILY_SCHEMA_V2_TO_V3_V1",
        "activated_at_us": 1_700_000_000_000_000,
        "record_type": "FAMILY_AUTHORITY_GENESIS_V1",
    }
    payload["record_hash"] = canonical_sha256(payload)
    return payload


def _exporter_event_projection_row(
    *,
    genesis_record_hash: str,
    previous_record_hash: str,
    event_ordinal: int,
    publication_receipt_id: str,
    published_artifact_envelope_hash: str,
    event_at_us: int,
) -> dict[str, Any]:
    canonical_row_payload = _complete_authority_row_payload(
        "research_authority_events",
        authority_instance_id="family-authority-1",
        genesis_record_hash=genesis_record_hash,
        event_id=f"exporter-event-{event_ordinal}",
        event_ordinal=event_ordinal,
        action="PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1",
        subject_record_type="PUBLISHED_ARTIFACT_ENVELOPE_V1",
        subject_id=publication_receipt_id,
        subject_record_hash=published_artifact_envelope_hash,
        audit_actor_id="artifact-exporter-1",
        audit_actor_kind="SYSTEM",
        audit_actor_evidence_hash="c" * 64,
        audit_actor_policy_id="export-actor-policy",
        audit_actor_policy_version="v1",
        audit_actor_policy_hash="d" * 64,
        audit_actor_authority_id="identity-authority-1",
        audit_actor_authority_head="e" * 64,
        event_at_us=event_at_us,
        prev_record_hash=previous_record_hash,
    )
    record_hash = canonical_sha256(canonical_row_payload)
    return {
        "table_name": "research_authority_events",
        "primary_key": {"event_id": f"exporter-event-{event_ordinal}"},
        "canonical_row_payload": canonical_row_payload,
        "record_hash": record_hash,
        "chain_partition_key": {"authority_instance_id": "family-authority-1"},
        "chain_ordinal": event_ordinal,
        "chain_previous_record_hash": previous_record_hash,
    }


def _publication_receipt_payload(
    *,
    core: Any | None = None,
    exporter_previous_head: str | None = None,
    exporter_event_ordinal: int = 2,
    **overrides: Any,
) -> dict[str, Any]:
    core = core or _contract("ArtifactCore")(**_core_payload())
    core_scan = _contract("ArtifactCoreScanReceipt")(**_core_scan_receipt_payload(core))
    envelope = _contract("PublishedArtifactEnvelope")(**_envelope_payload(core, core_scan))
    core_hash = sha256_bytes(canonical_json_bytes(core))
    core_scan_hash = sha256_bytes(canonical_json_bytes(core_scan))
    envelope_hash = sha256_bytes(canonical_json_bytes(envelope))
    closure_complete = core.closure_status == "COMPLETE"
    observed_payload = core.observed_pit_evidence.canonical_payload
    code_payload = core.code_manifest_evidence.canonical_payload
    closure_payload = next(
        row.canonical_row_payload
        for row in core.authority_projection.canonical_rows
        if row.table_name == "data_snooping_family_closures"
    )
    grid_hashes_json = _canonical_json_text(core.ordered_grid_assessment_record_hashes)
    failure_reasons_json = _canonical_json_text(core.failure_reasons)
    payload: dict[str, Any] = {
        "record_type": "ARTIFACT_PUBLICATION_RECEIPT_V1",
        "receipt_schema_version": "ARTIFACT_PUBLICATION_RECEIPT_V1",
        "authority_instance_id": core.authority_instance_id,
        "genesis_record_hash": core.genesis_record_hash,
        "observed_pit_authority_id": observed_payload["authority_id"],
        "observed_pit_handle_id": observed_payload["handle_id"],
        "observed_pit_content_hash": observed_payload["content_hash"],
        "knowledge_time_policy_id": observed_payload["knowledge_time_policy_id"],
        "knowledge_time_policy_version": observed_payload[
            "knowledge_time_policy_version"
        ],
        "observed_pit_authority_head": observed_payload["authority_head"],
        "oos_exposure_id": closure_payload["oos_exposure_id"],
        "exposure_fingerprint": closure_payload["exposure_fingerprint"],
        "exposure_authority_id": closure_payload["exposure_authority_id"],
        "exposure_authority_head": closure_payload["exposure_authority_head"],
        "code_manifest_policy_id": code_payload["policy_ref"]["policy_id"],
        "code_manifest_policy_version": code_payload["policy_ref"]["policy_version"],
        "code_manifest_policy_hash": code_payload["policy_ref"]["policy_hash"],
        "code_manifest_evidence_hash": code_payload["evidence_hash"],
        "code_manifest_authority_id": code_payload["authority_id"],
        "code_manifest_authority_head": code_payload["authority_head"],
        "start_execution_attestation_hash": closure_payload[
            "start_execution_attestation_hash"
        ],
        "terminal_execution_attestation_hash": closure_payload[
            "terminal_execution_attestation_hash"
        ],
        "execution_attestation_authority_id": closure_payload[
            "execution_attestation_authority_id"
        ],
        "start_execution_attestation_authority_head": closure_payload[
            "start_execution_attestation_authority_head"
        ],
        "terminal_execution_attestation_authority_head": closure_payload[
            "terminal_execution_attestation_authority_head"
        ],
        "immutable_artifact_lease_id": closure_payload["immutable_artifact_lease_id"],
        "immutable_artifact_lease_hash": closure_payload[
            "immutable_artifact_lease_hash"
        ],
        "immutable_artifact_lease_authority_id": closure_payload[
            "immutable_artifact_lease_authority_id"
        ],
        "immutable_artifact_lease_authority_head": closure_payload[
            "immutable_artifact_lease_authority_head"
        ],
        "authority_lineage_hash": core.authority_lineage_hash,
        "publication_receipt_id": "publication-1",
        "publication_ordinal": 1,
        "previous_record_hash": core.genesis_record_hash,
        "family_id": "family-1",
        "selection_scope_id": core.selection_scope_id,
        "selection_decision_id": core.selection_decision_id,
        "winner_config_hash": core.winner_config_hash,
        "winner_evaluation_receipt_hash": core.winner_evaluation_receipt_hash,
        "closure_manifest_hash": core.closure_manifest_hash,
        "closure_status": core.closure_status,
        "attempt_status": "COMPLETED" if closure_complete else "NOT_STARTED",
        "family_assessment_conclusion_hash": core.family_assessment_conclusion_hash,
        "ordered_grid_assessment_record_hashes_json": grid_hashes_json,
        "ordered_grid_assessment_record_hashes_hash": canonical_sha256(
            core.ordered_grid_assessment_record_hashes
        ),
        "minimum_replication_count": 10_000 if core.assessment_status == "ASSESSED" else None,
        "assessment_status": core.assessment_status,
        "diagnostic_verdict": core.diagnostic_verdict,
        "decision_class": core.decision_class,
        "promotion_effect": "NONE",
        "artifact_kind": "DATA_SNOOPING_FAMILY_ARTIFACT_V1",
        "artifact_core_hash": core_hash,
        "artifact_core_scan_receipt_hash": core_scan_hash,
        "published_artifact_envelope_hash": envelope_hash,
        "target_content_addressed_path": _content_path(ENVELOPE_ROOT, envelope_hash),
        "public_artifact_scan_policy_id": "PUBLIC_ARTIFACT_SCAN_V1",
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "dlp_policy_hash": "d" * 64,
        "scanner_implementation": "FAIL_CLOSED_SCANNER",
        "scanner_version": "1",
        "scan_status": "ALLOW",
        "scan_result_hash": "e" * 64,
        "envelope_scanned_at_us": 1_700_550_000_000_000,
        "exporter_event_hash": "f" * 64,
        "exporter_authority_head": "f" * 64,
        "failure_reasons_json": failure_reasons_json,
        "published_at_us": 1_700_600_000_000_000,
    }
    payload.update(overrides)
    if "scan_result_hash" not in overrides:
        payload["scan_result_hash"] = _scan_result_hash(
            scan_target="PUBLISHED_ARTIFACT_ENVELOPE",
            target_sha256=payload["published_artifact_envelope_hash"],
            target_byte_length=len(canonical_json_bytes(envelope)),
            scan_fields={
                key: payload[key]
                for key in (
                    "public_artifact_scan_policy_id",
                    "dlp_policy_id",
                    "dlp_policy_version",
                    "dlp_policy_hash",
                    "scanner_implementation",
                    "scanner_version",
                    "scan_status",
                )
            },
        )
    if "exporter_event_hash" not in overrides and "exporter_authority_head" not in overrides:
        exporter_row = _exporter_event_projection_row(
            genesis_record_hash=payload["genesis_record_hash"],
            previous_record_hash=(
                exporter_previous_head
                or core.pre_publication_research_authority_head
            ),
            event_ordinal=exporter_event_ordinal,
            publication_receipt_id=payload["publication_receipt_id"],
            published_artifact_envelope_hash=payload[
                "published_artifact_envelope_hash"
            ],
            event_at_us=payload["published_at_us"] - 1,
        )
        payload["exporter_event_hash"] = exporter_row["record_hash"]
        payload["exporter_authority_head"] = exporter_row["record_hash"]
    return _rehash(payload, "receipt_record_hash")


def test_publication_receipt_binds_exact_envelope_allow_path_and_external_lineage() -> None:
    receipt = _contract("ArtifactPublicationReceipt")(**_publication_receipt_payload())
    assert receipt.scan_status == "ALLOW"
    assert receipt.promotion_effect == "NONE"
    assert receipt.target_content_addressed_path == _content_path(
        ENVELOPE_ROOT,
        receipt.published_artifact_envelope_hash,
    )
    assert receipt.previous_record_hash == receipt.genesis_record_hash


def test_safe_not_assessable_core_may_publish_but_cannot_become_an_evidence_ref() -> None:
    blocked_core = _contract("ArtifactCore")(**_not_assessable_core_payload())
    receipt = _contract("ArtifactPublicationReceipt")(
        **_publication_receipt_payload(
            core=blocked_core,
            assessment_status="NOT_ASSESSABLE",
        )
    )
    assert receipt.assessment_status == "NOT_ASSESSABLE"
    assert receipt.promotion_effect == "NONE"
    projection = _contract("ArtifactPublicationReceiptProjection")(
        **_projection_payload((receipt,))
    )
    projection_ref = _contract("ArtifactPublicationProjectionRef")(
        **_projection_ref_payload(receipt=receipt, projection=projection)
    )
    assert projection_ref.decision_class == "DIAGNOSTIC_ONLY"
    assert projection_ref.assessment_status == "NOT_ASSESSABLE"
    core_scan = _contract("ArtifactCoreScanReceipt")(
        **_core_scan_receipt_payload(blocked_core)
    )
    envelope = _contract("PublishedArtifactEnvelope")(
        **_envelope_payload(blocked_core, core_scan)
    )
    structural_splice = getattr(
        importlib.import_module("quantos.contracts.data_snooping_c9"),
        "_verify_exact_byte_graph_splice",
    )
    assert structural_splice(envelope, receipt, projection, projection_ref) is True
    with pytest.raises(ValidationError):
        _contract("DataSnoopingEvidenceRef")(
            **_evidence_ref_payload(assessment_status="NOT_ASSESSABLE")
        )


@pytest.mark.parametrize(
    "mutation",
    (
        {"scan_status": "DENY"},
        {"scan_status": "ERROR"},
        {"scan_status": "UNAVAILABLE"},
        {"promotion_effect": "PROMOTE"},
        {"authority_lineage_hash": "f" * 64},
        {"previous_record_hash": "f" * 64},
        {"target_content_addressed_path": "evidence/data_snooping/not-addressed.json"},
        {"target_content_addressed_path": "../sha256-bad.json"},
        {"target_content_addressed_path": "C:\\evidence\\sha256-bad.json"},
    ),
)
def test_publication_receipt_rejects_non_allow_non_addressed_or_foreign_lineage(
    mutation: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactPublicationReceipt")(**_publication_receipt_payload(**mutation))


def _projection_payload(receipts: tuple[Any, ...] | None = None, **overrides: Any) -> dict[str, Any]:
    if receipts is None:
        receipts = (_contract("ArtifactPublicationReceipt")(**_publication_receipt_payload()),)
    genesis = _genesis_payload()
    terminal = receipts[-1]
    core = _contract("ArtifactCore")(**_core_payload())
    pre_publication_head = core.authority_projection.selection_scope_history_head
    pre_publication_row_count = next(
        head["row_count"]
        for head in core.authority_projection.model_dump(mode="python")["chain_heads"]
        if head["table_name"] == "research_authority_events"
    )
    exporter_rows = []
    previous_exporter_hash = pre_publication_head
    for event_ordinal, receipt in enumerate(receipts, start=2):
        row = _exporter_event_projection_row(
            genesis_record_hash=genesis["record_hash"],
            previous_record_hash=previous_exporter_hash,
            event_ordinal=event_ordinal,
            publication_receipt_id=receipt.publication_receipt_id,
            published_artifact_envelope_hash=receipt.published_artifact_envelope_hash,
            event_at_us=receipt.published_at_us - 1,
        )
        exporter_rows.append(row)
        previous_exporter_hash = row["record_hash"]
    payload: dict[str, Any] = {
        "record_type": "ARTIFACT_PUBLICATION_RECEIPT_PROJECTION_V1",
        "projection_schema_version": "ARTIFACT_PUBLICATION_RECEIPT_PROJECTION_V1",
        "publication_receipt_schema_version": "ARTIFACT_PUBLICATION_RECEIPT_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": genesis["record_hash"],
        "authority_lineage_hash": _lineage_hash("family-authority-1", genesis["record_hash"]),
        "authority_genesis": genesis,
        "publication_receipt": terminal,
        "publication_chain_prefix": receipts,
        "pre_publication_research_authority_head": pre_publication_head,
        "pre_publication_research_authority_row_count": pre_publication_row_count,
        "exporter_authority_event_suffix": tuple(exporter_rows),
        "exporter_authority_head": exporter_rows[-1]["record_hash"],
        "terminal_publication_receipt_id": terminal.publication_receipt_id,
        "terminal_publication_record_hash": terminal.receipt_record_hash,
        "terminal_chain_head": terminal.receipt_record_hash,
    }
    payload.update(overrides)
    return payload


def _two_receipt_chain() -> tuple[Any, Any]:
    genesis_hash = _genesis_payload()["record_hash"]
    receipt_contract = _contract("ArtifactPublicationReceipt")
    first = receipt_contract(
        **_publication_receipt_payload(
            genesis_record_hash=genesis_hash,
            authority_lineage_hash=_lineage_hash("family-authority-1", genesis_hash),
            previous_record_hash=genesis_hash,
        )
    )
    second_envelope_hash = "e" * 64
    second = receipt_contract(
        **_publication_receipt_payload(
            genesis_record_hash=genesis_hash,
            authority_lineage_hash=_lineage_hash("family-authority-1", genesis_hash),
            publication_receipt_id="publication-2",
            publication_ordinal=2,
            published_artifact_envelope_hash=second_envelope_hash,
            target_content_addressed_path=_content_path(ENVELOPE_ROOT, second_envelope_hash),
            previous_record_hash=first.receipt_record_hash,
            published_at_us=1_700_600_000_000_001,
            exporter_previous_head=first.exporter_authority_head,
            exporter_event_ordinal=3,
        )
    )
    return first, second


def test_projection_contains_the_complete_genesis_to_terminal_chain_prefix() -> None:
    receipts = _two_receipt_chain()
    projection = _contract("ArtifactPublicationReceiptProjection")(
        **_projection_payload(receipts)
    )
    assert len(projection.publication_chain_prefix) == 2
    assert projection.publication_chain_prefix[0].previous_record_hash == projection.genesis_record_hash
    assert (
        projection.publication_chain_prefix[1].previous_record_hash
        == projection.publication_chain_prefix[0].receipt_record_hash
    )
    assert projection.publication_receipt == projection.publication_chain_prefix[-1]
    assert projection.terminal_chain_head == projection.publication_receipt.receipt_record_hash
    assert "publication_projection_hash" not in type(projection).model_fields
    assert "publication_projection_path" not in type(projection).model_fields


def test_projection_rejects_truncation_reordering_foreign_lineage_and_wrong_terminal() -> None:
    first, second = _two_receipt_chain()
    projection_contract = _contract("ArtifactPublicationReceiptProjection")
    invalid_payloads = (
        _projection_payload((second,)),
        _projection_payload((second, first)),
        _projection_payload(
            (first,),
            publication_receipt=second,
            terminal_publication_receipt_id=second.publication_receipt_id,
            terminal_publication_record_hash=second.receipt_record_hash,
            terminal_chain_head=second.receipt_record_hash,
        ),
        _projection_payload(
            (first, second),
            authority_instance_id="foreign-authority",
        ),
        _projection_payload(
            (first, second),
            terminal_chain_head="f" * 64,
        ),
        _projection_payload(
            (first, second),
            publication_receipt=first,
        ),
    )
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            projection_contract(**payload)


def test_projection_rejects_a_duplicate_publication_identity() -> None:
    first, _ = _two_receipt_chain()
    duplicate = _contract("ArtifactPublicationReceipt")(
        **_publication_receipt_payload(
            genesis_record_hash=first.genesis_record_hash,
            authority_lineage_hash=first.authority_lineage_hash,
            publication_receipt_id="publication-2",
            publication_ordinal=2,
            previous_record_hash=first.receipt_record_hash,
            published_at_us=first.published_at_us + 1,
        )
    )
    with pytest.raises(ValidationError):
        _contract("ArtifactPublicationReceiptProjection")(
            **_projection_payload((first, duplicate))
        )


def _projection_ref_payload(
    *,
    receipt: Any | None = None,
    projection: Any | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    if receipt is None:
        receipt = _contract("ArtifactPublicationReceipt")(
            **_publication_receipt_payload()
        )
    if projection is None:
        projection = _contract("ArtifactPublicationReceiptProjection")(
            **_projection_payload((receipt,))
        )
    projection_hash = sha256_bytes(canonical_json_bytes(projection))
    payload = {
        "record_type": "ARTIFACT_PUBLICATION_PROJECTION_REF_V1",
        "projection_schema_version": (
            "ARTIFACT_PUBLICATION_RECEIPT_PROJECTION_V1"
        ),
        "authority_instance_id": receipt.authority_instance_id,
        "genesis_record_hash": receipt.genesis_record_hash,
        "authority_lineage_hash": receipt.authority_lineage_hash,
        "family_id": receipt.family_id,
        "decision_class": receipt.decision_class,
        "assessment_status": receipt.assessment_status,
        "artifact_publication_receipt_id": receipt.publication_receipt_id,
        "artifact_publication_receipt_hash": receipt.receipt_record_hash,
        "publication_projection_hash": projection_hash,
        "publication_projection_path": _content_path(PROJECTION_ROOT, projection_hash),
        "publication_chain_head": receipt.receipt_record_hash,
    }
    payload.update(overrides)
    return payload


def _evidence_ref_payload(**overrides: Any) -> dict[str, Any]:
    genesis_hash = _genesis_payload()["record_hash"]
    receipt = _contract("ArtifactPublicationReceipt")(
        **_publication_receipt_payload(
            genesis_record_hash=genesis_hash,
            authority_lineage_hash=_lineage_hash("family-authority-1", genesis_hash),
            previous_record_hash=genesis_hash,
        )
    )
    projection = _contract("ArtifactPublicationReceiptProjection")(
        **_projection_payload((receipt,))
    )
    projection_hash = sha256_bytes(canonical_json_bytes(projection))
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_EVIDENCE_REF_V1",
        "artifact_kind": "DATA_SNOOPING_FAMILY_ARTIFACT_V1",
        "artifact_schema_version": "DATA_SNOOPING_ARTIFACT_CORE_V1",
        "authority_instance_id": receipt.authority_instance_id,
        "genesis_record_hash": receipt.genesis_record_hash,
        "authority_lineage_hash": receipt.authority_lineage_hash,
        "family_id": receipt.family_id,
        "selection_scope_id": receipt.selection_scope_id,
        "selection_decision_id": receipt.selection_decision_id,
        "decision_class": "PUBLISHABLE",
        "winner_config_hash": receipt.winner_config_hash,
        "closure_manifest_hash": receipt.closure_manifest_hash,
        "family_assessment_conclusion_hash": _core_payload()[
            "family_assessment_conclusion_hash"
        ],
        "assessment_status": "ASSESSED",
        "diagnostic_verdict": "DOES_NOT_SUPPORT_SUPERIORITY",
        "promotion_effect": "NONE",
        "published_artifact_envelope_hash": receipt.published_artifact_envelope_hash,
        "published_artifact_path": receipt.target_content_addressed_path,
        "artifact_publication_receipt_id": receipt.publication_receipt_id,
        "artifact_publication_receipt_hash": receipt.receipt_record_hash,
        "publication_projection_hash": projection_hash,
        "publication_projection_path": _content_path(PROJECTION_ROOT, projection_hash),
        "publication_chain_head": receipt.receipt_record_hash,
    }
    payload.update(overrides)
    return payload


def test_evidence_ref_is_assessed_non_promoting_and_content_addressed() -> None:
    ref = _contract("DataSnoopingEvidenceRef")(**_evidence_ref_payload())
    assert ref.assessment_status == "ASSESSED"
    assert ref.promotion_effect == "NONE"
    assert ref.family_assessment_conclusion_hash == _core_payload()[
        "family_assessment_conclusion_hash"
    ]
    assert ref.published_artifact_path == _content_path(
        ENVELOPE_ROOT,
        ref.published_artifact_envelope_hash,
    )
    assert ref.publication_projection_path == _content_path(
        PROJECTION_ROOT,
        ref.publication_projection_hash,
    )


@pytest.mark.parametrize(
    "mutation",
    (
        {"assessment_status": "ASSESSED", "decision_class": "DIAGNOSTIC_ONLY"},
        {"assessment_status": "NOT_ASSESSABLE", "decision_class": "PUBLISHABLE"},
        {"authority_lineage_hash": "f" * 64},
        {"publication_projection_path": "evidence/data_snooping/detached.json"},
        {"publication_chain_head": "f" * 64},
    ),
)
def test_projection_ref_rejects_branch_locator_or_lineage_detachment(
    mutation: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _contract("ArtifactPublicationProjectionRef")(
            **_projection_ref_payload(**mutation)
        )


@pytest.mark.parametrize(
    "mutation",
    (
        {"assessment_status": "NOT_ASSESSABLE"},
        {"decision_class": "DIAGNOSTIC_ONLY"},
        {"diagnostic_verdict": "NOT_APPLICABLE"},
        {"diagnostic_verdict": "PENDING_MORE_REPLICATIONS"},
        {"promotion_effect": "PROMOTE"},
        {"authority_lineage_hash": "f" * 64},
        {"published_artifact_path": "evidence/data_snooping/detached.json"},
        {"publication_projection_path": "evidence/data_snooping/detached.json"},
    ),
)
def test_evidence_ref_rejects_non_assessed_promotion_detachment_or_lineage_drift(
    mutation: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _contract("DataSnoopingEvidenceRef")(**_evidence_ref_payload(**mutation))


def test_c9_dependency_graph_is_explicitly_acyclic_over_exact_bytes() -> None:
    core = _contract("ArtifactCore")(**_core_payload())
    core_hash = sha256_bytes(canonical_json_bytes(core))
    core_scan = _contract("ArtifactCoreScanReceipt")(**_core_scan_receipt_payload(core))
    envelope = _contract("PublishedArtifactEnvelope")(**_envelope_payload(core, core_scan))
    envelope_hash = sha256_bytes(canonical_json_bytes(envelope))

    genesis_hash = _genesis_payload()["record_hash"]
    publication = _contract("ArtifactPublicationReceipt")(
        **_publication_receipt_payload(
            genesis_record_hash=genesis_hash,
            authority_lineage_hash=_lineage_hash("family-authority-1", genesis_hash),
            previous_record_hash=genesis_hash,
        )
    )
    projection = _contract("ArtifactPublicationReceiptProjection")(
        **_projection_payload((publication,))
    )
    projection_hash = sha256_bytes(canonical_json_bytes(projection))
    projection_ref = _contract("ArtifactPublicationProjectionRef")(
        **_projection_ref_payload(receipt=publication, projection=projection)
    )
    ref = _contract("DataSnoopingEvidenceRef")(**_evidence_ref_payload())
    structural_splice = getattr(
        importlib.import_module("quantos.contracts.data_snooping_c9"),
        "_verify_exact_byte_graph_splice",
    )

    assert core_scan.artifact_core_hash == core_hash
    assert publication.published_artifact_envelope_hash == envelope_hash
    assert projection_ref.publication_projection_hash == projection_hash
    assert ref.publication_projection_hash == projection_hash
    assert structural_splice(
        envelope,
        publication,
        projection,
        projection_ref,
        ref,
    ) is True
    assert "artifact_core_hash" not in type(core).model_fields
    assert "published_artifact_envelope_hash" not in type(envelope).model_fields
    assert "publication_projection_hash" not in type(publication).model_fields
    assert "publication_projection_hash" not in type(projection).model_fields
    assert "data_snooping_evidence_ref" not in type(core).model_fields
    assert "data_snooping_evidence_ref" not in type(envelope).model_fields


@pytest.mark.parametrize(
    "genesis_field",
    (
        "sealed_v3_manifest_hash",
        "activation_code_manifest_evidence_hash",
    ),
)
def test_structural_splice_rejects_detached_manifest_or_activation_genesis(
    genesis_field: str,
) -> None:
    core = _contract("ArtifactCore")(**_core_payload())
    core_scan = _contract("ArtifactCoreScanReceipt")(
        **_core_scan_receipt_payload(core)
    )
    envelope = _contract("PublishedArtifactEnvelope")(
        **_envelope_payload(core, core_scan)
    )
    publication = _contract("ArtifactPublicationReceipt")(
        **_publication_receipt_payload(core=core)
    )
    projection = _contract("ArtifactPublicationReceiptProjection")(
        **_projection_payload((publication,))
    )
    forged_genesis = projection.authority_genesis.model_copy(
        update={genesis_field: "f" * 64}
    )
    forged_projection = projection.model_copy(
        update={"authority_genesis": forged_genesis}
    )
    projection_ref = _contract("ArtifactPublicationProjectionRef")(
        **_projection_ref_payload(
            receipt=publication,
            projection=forged_projection,
        )
    )
    structural_splice = getattr(
        importlib.import_module("quantos.contracts.data_snooping_c9"),
        "_verify_exact_byte_graph_splice",
    )

    with pytest.raises(ValueError, match="family genesis"):
        structural_splice(envelope, publication, forged_projection, projection_ref)


def test_publication_receipt_rejects_envelope_scan_after_publication() -> None:
    with pytest.raises(ValidationError, match="envelope scan"):
        _contract("ArtifactPublicationReceipt")(
            **_publication_receipt_payload(
                envelope_scanned_at_us=1_700_700_000_000_000,
            )
        )


def test_structural_splice_rejects_envelope_scan_before_core_scan() -> None:
    core = _contract("ArtifactCore")(**_core_payload())
    core_scan = _contract("ArtifactCoreScanReceipt")(
        **_core_scan_receipt_payload(core)
    )
    envelope = _contract("PublishedArtifactEnvelope")(
        **_envelope_payload(core, core_scan)
    )
    publication = _contract("ArtifactPublicationReceipt")(
        **_publication_receipt_payload(
            core=core,
            envelope_scanned_at_us=core_scan.scanned_at_us - 1,
        )
    )
    projection = _contract("ArtifactPublicationReceiptProjection")(
        **_projection_payload((publication,))
    )
    projection_ref = _contract("ArtifactPublicationProjectionRef")(
        **_projection_ref_payload(receipt=publication, projection=projection)
    )
    structural_splice = getattr(
        importlib.import_module("quantos.contracts.data_snooping_c9"),
        "_verify_exact_byte_graph_splice",
    )

    with pytest.raises(ValueError, match="causal time order"):
        structural_splice(envelope, publication, projection, projection_ref)


def test_structural_splice_rejects_one_exact_input_on_a_foreign_scan_policy() -> None:
    payload = _core_payload()
    sidecar = dict(payload["family_schema_v3_manifest_sidecar"])
    scan_receipt = dict(sidecar["scan_receipt"])
    scan_fields = {
        name: scan_receipt[name]
        for name in (
            "public_artifact_scan_policy_id",
            "dlp_policy_id",
            "dlp_policy_version",
            "dlp_policy_hash",
            "scanner_implementation",
            "scanner_version",
            "scan_status",
        )
    }
    scan_fields["scanner_version"] = "2"
    scan_receipt.update(scan_fields)
    scan_receipt["scan_result_hash"] = _scan_result_hash(
        scan_target=sidecar["sidecar_kind"],
        target_sha256=sidecar["decoded_sha256"],
        target_byte_length=sidecar["decoded_byte_length"],
        scan_fields=scan_fields,
    )
    sidecar["scan_receipt"] = _rehash(scan_receipt, "scan_receipt_hash")
    payload["family_schema_v3_manifest_sidecar"] = sidecar

    core = _contract("ArtifactCore")(**payload)
    core_scan = _contract("ArtifactCoreScanReceipt")(
        **_core_scan_receipt_payload(core)
    )
    envelope = _contract("PublishedArtifactEnvelope")(
        **_envelope_payload(core, core_scan)
    )
    publication = _contract("ArtifactPublicationReceipt")(
        **_publication_receipt_payload(core=core)
    )
    projection = _contract("ArtifactPublicationReceiptProjection")(
        **_projection_payload((publication,))
    )
    projection_ref = _contract("ArtifactPublicationProjectionRef")(
        **_projection_ref_payload(receipt=publication, projection=projection)
    )
    structural_splice = getattr(
        importlib.import_module("quantos.contracts.data_snooping_c9"),
        "_verify_exact_byte_graph_splice",
    )

    with pytest.raises(ValueError, match="frozen publication scan policy"):
        structural_splice(envelope, publication, projection, projection_ref)
