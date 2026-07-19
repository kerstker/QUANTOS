"""Executable Pass-M semantics for every C6/C9 named verifier.

The positive fixture is one globally coherent publication: its execution
attestation is START -> TERMINAL, its closure precedes its assessment and
publication, and its projection contains the complete genesis-to-terminal
publication prefix.  Mutations exercise local references, external claims,
authority heads, content hashes, chronology, branch totality, and trust roots.
"""

from __future__ import annotations

import importlib
import inspect
from copy import deepcopy
from dataclasses import dataclass, replace
from types import FunctionType
from typing import Any

import pytest

from quantos.contracts import canonical as canonical
from quantos.contracts import data_snooping_c9 as c9_contracts
from quantos.contracts import research as research_contracts
from services.research.experiment_registry.family_schema_external_verification_c6_c9 import (
    C6C9ExternalProviderBundle,
    C9ArtifactGraphProvider,
    C9PublicationTrustRootProvider,
    CodeManifestEvidenceProvider,
    ExecutionAttestationSubjectContext,
    ExecutionCodeAttestationProvider,
    ImmutableArtifactLeaseProvider,
    LeaseSubjectContext,
    ObservedPitDatasetHandleProvider,
    VerifiedExecutionCodeAttestation,
    VerifiedImmutableArtifactLease,
)
from services.research.experiment_registry.family_schema_named_verifiers_c6_c9 import (
    C6_C9_EXTERNAL_COMPONENT_VERIFIER_CODES,
    C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS,
    verify_c6_c9_named,
)
from services.research.experiment_registry.family_schema_verification_view import (
    FrozenFamilySchemaVerificationView,
)
from services.research.experiment_registry.family_schema_verifier_registry import (
    FAMILY_SCHEMA_VERIFIER_BINDINGS,
)


_C6 = "C6_EXTERNAL_AUTHORITY_REFS_V1"
_C9 = "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1"
_SHA = "a" * 64


def _contract(name: str) -> type[Any]:
    return getattr(c9_contracts, name)


def _c9_fixtures() -> Any:
    return importlib.import_module("tests.contracts.test_data_snooping_c9_contracts")


def _projection_row(row: Any) -> dict[str, object]:
    payload = dict(row.canonical_row_payload)
    payload["record_hash"] = row.record_hash
    return payload


@dataclass(frozen=True, slots=True)
class _Bundle:
    owner_by_table: dict[str, dict[str, object]]
    view: FrozenFamilySchemaVerificationView
    providers: C6C9ExternalProviderBundle
    envelope: Any
    receipt: Any
    projection: Any
    projection_ref: Any


class _ObservedProvider(ObservedPitDatasetHandleProvider):
    def __init__(self, value: research_contracts.ObservedPitDatasetHandle) -> None:
        self.value = value
        self.calls = 0
        self.error = False
        self.force_return = False

    def verify_observed_pit_dataset_handle(
        self,
        authority_id: str,
        handle_id: str,
        content_hash: str,
        authority_head: str,
        /,
    ) -> research_contracts.ObservedPitDatasetHandle | None:
        self.calls += 1
        if self.error:
            raise RuntimeError("provider unavailable")
        value = self.value
        if not self.force_return and (
            authority_id,
            handle_id,
            content_hash,
            authority_head,
        ) != (
            value.authority_id,
            value.handle_id,
            value.content_hash,
            value.authority_head,
        ):
            return None
        return value


class _CodeProvider(CodeManifestEvidenceProvider):
    def __init__(self, value: research_contracts.CodeManifestEvidence) -> None:
        self.value = value
        self.calls = 0
        self.error = False

    def verify_code_manifest_evidence(
        self,
        evidence_hash: str,
        authority_id: str,
        authority_head: str,
        /,
    ) -> research_contracts.CodeManifestEvidence | None:
        self.calls += 1
        if self.error:
            raise RuntimeError("provider unavailable")
        value = self.value
        if (evidence_hash, authority_id, authority_head) != (
            value.evidence_hash,
            value.authority_id,
            value.authority_head,
        ):
            return None
        return value


class _ExecutionProvider(ExecutionCodeAttestationProvider):
    def __init__(
        self,
        values: tuple[VerifiedExecutionCodeAttestation, ...],
    ) -> None:
        self.values = values
        self.calls = 0
        self.error = False

    def verify_execution_code_attestation(
        self,
        attestation_hash: str,
        authority_id: str,
        authority_head: str,
        /,
    ) -> VerifiedExecutionCodeAttestation | None:
        self.calls += 1
        if self.error:
            raise RuntimeError("provider unavailable")
        for value in self.values:
            evidence = value.evidence
            if (attestation_hash, authority_id, authority_head) == (
                evidence.attestation_hash,
                evidence.authority_id,
                evidence.authority_head,
            ):
                return value
        return None


class _LeaseProvider(ImmutableArtifactLeaseProvider):
    def __init__(self, value: VerifiedImmutableArtifactLease) -> None:
        self.value = value
        self.calls = 0
        self.error = False

    def verify_immutable_artifact_lease(
        self,
        lease_id: str,
        lease_hash: str,
        authority_id: str,
        authority_head: str,
        /,
    ) -> VerifiedImmutableArtifactLease | None:
        self.calls += 1
        if self.error:
            raise RuntimeError("provider unavailable")
        evidence = self.value.evidence
        if (lease_id, lease_hash, authority_id, authority_head) != (
            evidence.lease_id,
            evidence.lease_hash,
            evidence.authority_id,
            evidence.authority_head,
        ):
            return None
        return self.value


class _ArtifactProvider(C9ArtifactGraphProvider):
    def __init__(
        self,
        *,
        content: dict[tuple[str, str], bytes],
        projection_refs: dict[tuple[str, str], bytes],
        evidence_refs: dict[tuple[str, str], bytes] | None = None,
    ) -> None:
        self.content = content
        self.projection_refs = projection_refs
        self.evidence_refs = evidence_refs or {}
        self.error = False

    def read_content_addressed_bytes(
        self,
        path: str,
        expected_sha256: str,
        /,
    ) -> bytes | None:
        if self.error:
            raise RuntimeError("artifact store unavailable")
        return self.content.get((path, expected_sha256))

    def read_publication_projection_ref_bytes(
        self,
        publication_receipt_id: str,
        receipt_record_hash: str,
        /,
    ) -> bytes | None:
        if self.error:
            raise RuntimeError("artifact store unavailable")
        return self.projection_refs.get((publication_receipt_id, receipt_record_hash))

    def read_data_snooping_evidence_ref_bytes(
        self,
        publication_receipt_id: str,
        receipt_record_hash: str,
        /,
    ) -> bytes | None:
        if self.error:
            raise RuntimeError("artifact store unavailable")
        return self.evidence_refs.get((publication_receipt_id, receipt_record_hash))


class _TrustProvider(C9PublicationTrustRootProvider):
    def __init__(self, *, core: Any, projection: Any) -> None:
        self.core = core
        self.projection = projection
        self.reject_provider_genesis = False
        self.reject_scanner = False
        self.manifest_hash = projection.authority_genesis.sealed_v3_manifest_hash
        self.activation_hash = projection.authority_genesis.activation_code_manifest_evidence_hash

    def verify_external_provider_authority_genesis(
        self,
        provider_contract_type: str,
        authority_id: str,
        genesis_record_hash: str,
        /,
    ) -> bool:
        if self.reject_provider_genesis:
            return False
        for evidence in (
            self.core.observed_pit_evidence,
            self.core.activation_code_manifest_evidence,
            self.core.code_manifest_evidence,
            self.core.start_execution_attestation_evidence,
            self.core.terminal_execution_attestation_evidence,
            self.core.immutable_artifact_lease_evidence,
        ):
            if evidence is None:
                continue
            authority = evidence.provider_authority_projection
            if authority is None:
                continue
            if (
                evidence.contract_type,
                authority.authority_id,
                authority.genesis_record_hash,
            ) == (provider_contract_type, authority_id, genesis_record_hash):
                return True
        return False

    def verify_trusted_scanner(
        self,
        public_artifact_scan_policy_id: str,
        dlp_policy_id: str,
        dlp_policy_version: str,
        dlp_policy_hash: str,
        scanner_implementation: str,
        scanner_version: str,
        /,
    ) -> bool:
        if self.reject_scanner:
            return False
        return (
            public_artifact_scan_policy_id,
            dlp_policy_id,
            dlp_policy_version,
            dlp_policy_hash,
            scanner_implementation,
            scanner_version,
        ) == (
            "PUBLIC_ARTIFACT_SCAN_V1",
            "DLP_POLICY_V1",
            "1",
            "d" * 64,
            "FAIL_CLOSED_SCANNER",
            "1",
        )

    def expected_family_schema_v3_manifest_hash(self, /) -> str | None:
        return self.manifest_hash

    def expected_activation_code_manifest_evidence_hash(self, /) -> str | None:
        return self.activation_hash


def _verified_attestation(evidence: Any) -> VerifiedExecutionCodeAttestation:
    payload = research_contracts.ExecutionCodeAttestation.model_validate(
        evidence.canonical_payload,
        strict=True,
    )
    context = evidence.provider_subject_context
    projection = evidence.provider_authority_projection
    assert context is not None and projection is not None
    return VerifiedExecutionCodeAttestation(
        evidence=payload,
        authority_genesis_record_hash=projection.genesis_record_hash,
        subject_context=ExecutionAttestationSubjectContext(
            family_id=context.family_id,
            attempt_id=context.attempt_id,
            evaluation_batch_id=context.evaluation_batch_id,
            role=context.role,
        ),
        authority_projection_record_hashes=(
            projection.genesis_record_hash,
            *(event.record_hash for event in projection.ordered_events),
        ),
    )


def _verified_lease(evidence: Any) -> VerifiedImmutableArtifactLease:
    payload = research_contracts.ImmutableArtifactLease.model_validate(
        evidence.canonical_payload,
        strict=True,
    )
    context = evidence.provider_subject_context
    projection = evidence.provider_authority_projection
    assert context is not None and projection is not None
    return VerifiedImmutableArtifactLease(
        evidence=payload,
        authority_genesis_record_hash=projection.genesis_record_hash,
        subject_context=LeaseSubjectContext(
            family_id=context.family_id,
            attempt_id=context.attempt_id,
            evaluation_batch_id=context.evaluation_batch_id,
        ),
        authority_projection_record_hashes=(
            projection.genesis_record_hash,
            *(event.record_hash for event in projection.ordered_events),
        ),
    )


def _authority_projection_row(
    fixtures: Any,
    table_name: str,
    primary_key: dict[str, object],
    overrides: dict[str, object],
    *,
    chain_partition_key: dict[str, object] | None = None,
    chain_ordinal: int | None = None,
    chain_previous_record_hash: str | None = None,
) -> dict[str, object]:
    payload = fixtures._complete_authority_row_payload(table_name, **overrides)
    record_hash = canonical.canonical_sha256(payload)
    return {
        "table_name": table_name,
        "primary_key": primary_key,
        "canonical_row_payload": payload,
        "record_hash": record_hash,
        "chain_partition_key": chain_partition_key,
        "chain_ordinal": chain_ordinal,
        "chain_previous_record_hash": chain_previous_record_hash,
    }


def _globally_coherent_core_payload(fixtures: Any) -> dict[str, object]:
    """Close the fixture's C4/C6/C7 authority references before publication."""

    payload = deepcopy(fixtures._core_payload())
    projection = deepcopy(payload["authority_projection"])
    genesis_hash = projection["genesis_record_hash"]
    authority_id = payload["authority_instance_id"]
    family_id = payload["family_id"]
    scope_id = payload["selection_scope_id"]
    decision_id = payload["selection_decision_id"]
    observed = payload["observed_pit_evidence"]["canonical_payload"]
    start = payload["start_execution_attestation_evidence"]["canonical_payload"]
    terminal = payload["terminal_execution_attestation_evidence"]["canonical_payload"]
    lease = payload["immutable_artifact_lease_evidence"]["canonical_payload"]
    batch = payload["evaluation_batch_evidence"]["canonical_payload"]

    exposure_fingerprint = canonical.canonical_sha256(
        {
            "observed_pit_content_hash": observed["content_hash"],
            "dataset_version": observed["dataset_version"],
            "universe_id": observed["universe_id"],
            "universe_hash": observed["universe_hash"],
            "ordered_period_calendar_hash": observed["ordered_period_calendar_hash"],
            "label_horizon": observed["label_horizon"],
            "purge_periods": observed["purge_periods"],
            "embargo_periods": observed["embargo_periods"],
        }
    )
    exposure_partition = {
        "authority_instance_id": authority_id,
        "selection_scope_id": scope_id,
    }
    exposure_row = _authority_projection_row(
        fixtures,
        "global_oos_exposures",
        {"oos_exposure_id": "exposure-1"},
        {
            "authority_instance_id": authority_id,
            "genesis_record_hash": genesis_hash,
            "observed_pit_authority_id": observed["authority_id"],
            "observed_pit_handle_id": observed["handle_id"],
            "observed_pit_content_hash": observed["content_hash"],
            "observed_pit_authority_head": observed["authority_head"],
            "dataset_id": observed["dataset_id"],
            "dataset_version": observed["dataset_version"],
            "universe_id": observed["universe_id"],
            "universe_hash": observed["universe_hash"],
            "ordered_period_calendar_hash": observed["ordered_period_calendar_hash"],
            "knowledge_time_policy_id": observed["knowledge_time_policy_id"],
            "knowledge_time_policy_version": observed["knowledge_time_policy_version"],
            "label_horizon": observed["label_horizon"],
            "purge_periods": observed["purge_periods"],
            "embargo_periods": observed["embargo_periods"],
            "cost_contract_hash": observed["cost_contract_hash"],
            "benchmark_contract_hash": observed["benchmark_contract_hash"],
            "exposure_version": 1,
            "oos_exposure_id": "exposure-1",
            "selection_scope_id": scope_id,
            "selection_decision_id": decision_id,
            "family_id": family_id,
            "exposure_ordinal": 1,
            "exposure_fingerprint": exposure_fingerprint,
            "period_start_us": 1_700_100_000_000_000,
            "period_end_us": 1_700_500_000_000_000,
            "authority_event_id": "selection-history-event-1",
            "issued_at_us": 1_700_180_000_000_000,
            "prev_record_hash": genesis_hash,
        },
        chain_partition_key=exposure_partition,
        chain_ordinal=1,
        chain_previous_record_hash=genesis_hash,
    )

    batch_row = _authority_projection_row(
        fixtures,
        "data_snooping_evaluation_batches",
        {"evaluation_batch_id": batch["evaluation_batch_id"]},
        {
            "authority_instance_id": authority_id,
            "genesis_record_hash": genesis_hash,
            "evaluation_batch_id": batch["evaluation_batch_id"],
            "family_id": family_id,
            "attempt_id": batch["attempt_id"],
            "frozen_config_set_hash": batch["frozen_config_set_hash"],
            "candidate_count": batch["candidate_count"],
            "ordered_candidate_config_hashes_hash": canonical.canonical_sha256(batch["candidate_order"]),
            "ordered_candidate_receipt_hashes_hash": canonical.canonical_sha256(batch["candidate_receipt_hashes"]),
            "benchmark_receipt_hash": batch["benchmark_receipt_hash"],
            "ordered_period_keys_hash": batch["ordered_period_keys_hash"],
            "cost_contract_hash": batch["cost_contract_hash"],
            "start_execution_attestation_hash": batch["start_execution_attestation_hash"],
            "terminal_execution_attestation_hash": batch["terminal_execution_attestation_hash"],
            "immutable_artifact_lease_hash": batch["immutable_artifact_lease_hash"],
            "committed_at_us": batch["committed_at_us"],
        },
    )
    assert batch_row["record_hash"] == batch["batch_receipt_hash"]

    attempt_partition = {
        "authority_instance_id": authority_id,
        "attempt_id": batch["attempt_id"],
    }
    attempt_common = {
        "authority_instance_id": authority_id,
        "genesis_record_hash": genesis_hash,
        "attempt_id": batch["attempt_id"],
        "family_id": family_id,
        "selection_decision_id": decision_id,
        "oos_exposure_id": "exposure-1",
        "evaluation_batch_id": batch["evaluation_batch_id"],
        "frozen_config_set_hash": batch["frozen_config_set_hash"],
        "benchmark_contract_hash": observed["benchmark_contract_hash"],
        "ordered_period_calendar_hash": observed["ordered_period_calendar_hash"],
        "cost_contract_hash": batch["cost_contract_hash"],
        "start_execution_attestation_hash": start["attestation_hash"],
        "immutable_artifact_lease_hash": lease["lease_hash"],
    }
    start_attempt_row = _authority_projection_row(
        fixtures,
        "data_snooping_oos_access_attempts",
        {"attempt_event_id": "attempt-event-start-1"},
        {
            **attempt_common,
            "attempt_event_id": "attempt-event-start-1",
            "sequence_no": 0,
            "status": "STARTED",
            "terminal_execution_attestation_hash": None,
            "evaluation_batch_receipt_hash": None,
            "reason_code": None,
            "event_at_us": start["issued_at_us"],
            "prev_record_hash": genesis_hash,
        },
        chain_partition_key=attempt_partition,
        chain_ordinal=0,
        chain_previous_record_hash=genesis_hash,
    )
    terminal_attempt_row = _authority_projection_row(
        fixtures,
        "data_snooping_oos_access_attempts",
        {"attempt_event_id": "attempt-event-1"},
        {
            **attempt_common,
            "attempt_event_id": "attempt-event-1",
            "sequence_no": 1,
            "status": "COMPLETED",
            "terminal_execution_attestation_hash": terminal["attestation_hash"],
            "evaluation_batch_receipt_hash": batch["batch_receipt_hash"],
            "reason_code": None,
            "event_at_us": batch["committed_at_us"],
            "prev_record_hash": start_attempt_row["record_hash"],
        },
        chain_partition_key=attempt_partition,
        chain_ordinal=1,
        chain_previous_record_hash=start_attempt_row["record_hash"],
    )

    release_row = _authority_projection_row(
        fixtures,
        "data_snooping_oos_releases",
        {"oos_release_id": "release-1"},
        {
            "authority_instance_id": authority_id,
            "genesis_record_hash": genesis_hash,
            "exposure_fingerprint": exposure_fingerprint,
            "exposure_authority_id": authority_id,
            "exposure_authority_head": exposure_row["record_hash"],
            "start_execution_attestation_hash": start["attestation_hash"],
            "execution_attestation_authority_id": start["authority_id"],
            "execution_attestation_authority_head": start["authority_head"],
            "immutable_artifact_lease_id": lease["lease_id"],
            "immutable_artifact_lease_hash": lease["lease_hash"],
            "immutable_artifact_lease_authority_id": lease["authority_id"],
            "immutable_artifact_lease_authority_head": lease["authority_head"],
            "oos_release_id": "release-1",
            "family_id": family_id,
            "attempt_id": batch["attempt_id"],
            "started_attempt_event_id": "attempt-event-start-1",
            "family_freeze_record_hash": payload["freeze_record_hash"],
            "exposure_consumption_record_hash": "3" * 64,
            "oos_exposure_id": "exposure-1",
            "released_at_us": 1_700_190_000_000_000,
        },
    )

    rows = [
        deepcopy(row)
        for row in projection["canonical_rows"]
        if row["table_name"]
        not in {
            "global_oos_exposures",
            "data_snooping_oos_releases",
            "data_snooping_oos_access_attempts",
            "data_snooping_evaluation_batches",
        }
    ]
    by_table = {row["table_name"]: row for row in rows}
    closure = by_table["data_snooping_family_closures"]
    closure_payload = {
        **closure["canonical_row_payload"],
        "oos_exposure_id": "exposure-1",
        "exposure_fingerprint": exposure_fingerprint,
        "exposure_authority_id": authority_id,
        "exposure_authority_head": exposure_row["record_hash"],
        "attempt_id": batch["attempt_id"],
        "terminal_attempt_event_id": "attempt-event-1",
        "evaluation_batch_id": batch["evaluation_batch_id"],
        "evaluation_batch_receipt_hash": batch_row["record_hash"],
        "oos_release_id": "release-1",
        "oos_access_attempt_terminal_hash": terminal_attempt_row["record_hash"],
        "oos_release_record_hash": release_row["record_hash"],
    }
    closure.update(
        canonical_row_payload=closure_payload,
        record_hash=canonical.canonical_sha256(closure_payload),
    )

    grid = by_table["data_snooping_grid_assessments"]
    grid_payload = {
        **grid["canonical_row_payload"],
        "closure_manifest_hash": closure["record_hash"],
        "period_count": len(payload["ordered_period_keys"]),
        "replication_count": 10_000,
        "assessed_at_us": 1_700_400_000_000_000,
    }
    grid.update(
        canonical_row_payload=grid_payload,
        record_hash=canonical.canonical_sha256(grid_payload),
    )
    assessment = by_table["data_snooping_assessments"]
    grid_hashes = (grid["record_hash"],)
    assessment_payload = {
        **assessment["canonical_row_payload"],
        "closure_manifest_hash": closure["record_hash"],
        "ordered_latest_grid_assessment_hashes_json": fixtures._canonical_json_text(grid_hashes),
        "ordered_latest_grid_assessment_hashes_hash": canonical.canonical_sha256(grid_hashes),
        "minimum_replication_count": 10_000,
        "concluded_at_us": 1_700_450_000_000_000,
    }
    assessment.update(
        canonical_row_payload=assessment_payload,
        record_hash=canonical.canonical_sha256(assessment_payload),
    )
    rows.extend(
        (
            exposure_row,
            release_row,
            start_attempt_row,
            terminal_attempt_row,
            batch_row,
        )
    )

    chain_heads = [
        deepcopy(head)
        for head in projection["chain_heads"]
        if head["table_name"]
        not in {
            "global_oos_exposures",
            "data_snooping_oos_access_attempts",
            "data_snooping_grid_assessments",
        }
    ]
    chain_heads.extend(
        (
            {
                "table_name": "global_oos_exposures",
                "partition_key": exposure_partition,
                "row_count": 1,
                "head_record_hash": exposure_row["record_hash"],
            },
            {
                "table_name": "data_snooping_oos_access_attempts",
                "partition_key": attempt_partition,
                "row_count": 2,
                "head_record_hash": terminal_attempt_row["record_hash"],
            },
            {
                "table_name": "data_snooping_grid_assessments",
                "partition_key": grid["chain_partition_key"],
                "row_count": 1,
                "head_record_hash": grid["record_hash"],
            },
        )
    )
    projection = fixtures._rebuild_authority_projection(
        projection,
        rows=rows,
        chain_heads=chain_heads,
    )
    payload.update(
        {
            "closure_manifest_hash": closure["record_hash"],
            "ordered_grid_assessment_record_hashes": grid_hashes,
            "family_assessment_conclusion_hash": assessment["record_hash"],
            "authority_projection": projection,
            "authority_projection_hash": canonical.canonical_sha256(projection),
        }
    )
    return payload


def _valid_bundle() -> _Bundle:
    fixtures = _c9_fixtures()
    core = _contract("ArtifactCore")(**_globally_coherent_core_payload(fixtures))
    core_scan = _contract("ArtifactCoreScanReceipt")(**fixtures._core_scan_receipt_payload(core))
    envelope = _contract("PublishedArtifactEnvelope")(**fixtures._envelope_payload(core, core_scan))
    receipt = _contract("ArtifactPublicationReceipt")(**fixtures._publication_receipt_payload(core=core))
    projection = _contract("ArtifactPublicationReceiptProjection")(**fixtures._projection_payload((receipt,)))
    projection_ref = _contract("ArtifactPublicationProjectionRef")(
        **fixtures._projection_ref_payload(receipt=receipt, projection=projection)
    )
    projection_hash = canonical.sha256_bytes(canonical.canonical_json_bytes(projection))
    evidence_ref = _contract("DataSnoopingEvidenceRef")(
        record_type="DATA_SNOOPING_EVIDENCE_REF_V1",
        artifact_kind="DATA_SNOOPING_FAMILY_ARTIFACT_V1",
        artifact_schema_version="DATA_SNOOPING_ARTIFACT_CORE_V1",
        authority_instance_id=receipt.authority_instance_id,
        genesis_record_hash=receipt.genesis_record_hash,
        authority_lineage_hash=receipt.authority_lineage_hash,
        family_id=receipt.family_id,
        selection_scope_id=receipt.selection_scope_id,
        selection_decision_id=receipt.selection_decision_id,
        decision_class=receipt.decision_class,
        winner_config_hash=receipt.winner_config_hash,
        closure_manifest_hash=receipt.closure_manifest_hash,
        family_assessment_conclusion_hash=(receipt.family_assessment_conclusion_hash),
        assessment_status=receipt.assessment_status,
        diagnostic_verdict=receipt.diagnostic_verdict,
        promotion_effect=receipt.promotion_effect,
        published_artifact_envelope_hash=(receipt.published_artifact_envelope_hash),
        published_artifact_path=receipt.target_content_addressed_path,
        artifact_publication_receipt_id=receipt.publication_receipt_id,
        artifact_publication_receipt_hash=receipt.receipt_record_hash,
        publication_projection_hash=projection_hash,
        publication_projection_path=projection_ref.publication_projection_path,
        publication_chain_head=receipt.receipt_record_hash,
    )

    rows_by_table: dict[str, list[dict[str, object]]] = {}
    for row in core.authority_projection.canonical_rows:
        rows_by_table.setdefault(row.table_name, []).append(_projection_row(row))
    for row in projection.exporter_authority_event_suffix:
        rows_by_table.setdefault(row.table_name, []).append(_projection_row(row))
    rows_by_table["family_authority_genesis"] = [projection.authority_genesis.model_dump(mode="python")]
    rows_by_table["artifact_publication_receipts"] = [receipt.model_dump(mode="python")]
    view = FrozenFamilySchemaVerificationView.from_rows(rows_by_table)

    observed = research_contracts.ObservedPitDatasetHandle.model_validate(
        core.observed_pit_evidence.canonical_payload,
        strict=True,
    )
    code = research_contracts.CodeManifestEvidence.model_validate(
        core.code_manifest_evidence.canonical_payload,
        strict=True,
    )
    start = _verified_attestation(core.start_execution_attestation_evidence)
    terminal = _verified_attestation(core.terminal_execution_attestation_evidence)
    lease = _verified_lease(core.immutable_artifact_lease_evidence)

    envelope_bytes = canonical.canonical_json_bytes(envelope)
    projection_bytes = canonical.canonical_json_bytes(projection)
    projection_ref_bytes = canonical.canonical_json_bytes(projection_ref)
    evidence_ref_bytes = canonical.canonical_json_bytes(evidence_ref)
    artifact_provider = _ArtifactProvider(
        content={
            (
                receipt.target_content_addressed_path,
                receipt.published_artifact_envelope_hash,
            ): envelope_bytes,
            (
                projection_ref.publication_projection_path,
                projection_ref.publication_projection_hash,
            ): projection_bytes,
        },
        projection_refs={
            (
                receipt.publication_receipt_id,
                receipt.receipt_record_hash,
            ): projection_ref_bytes,
        },
        evidence_refs={
            (
                receipt.publication_receipt_id,
                receipt.receipt_record_hash,
            ): evidence_ref_bytes,
        },
    )
    providers = C6C9ExternalProviderBundle(
        observed_pit=_ObservedProvider(observed),
        code_manifest=_CodeProvider(code),
        execution_attestation=_ExecutionProvider((start, terminal)),
        immutable_artifact_lease=_LeaseProvider(lease),
        artifact_graph=artifact_provider,
        publication_trust=_TrustProvider(core=core, projection=projection),
    )
    owner_by_table = {table_name: rows[0] for table_name, rows in rows_by_table.items() if rows}
    return _Bundle(
        owner_by_table=owner_by_table,
        view=view,
        providers=providers,
        envelope=envelope,
        receipt=receipt,
        projection=projection,
        projection_ref=projection_ref,
    )


def _c6_c9_bindings() -> tuple[Any, ...]:
    return tuple(
        binding for binding in FAMILY_SCHEMA_VERIFIER_BINDINGS if binding.binding_key.startswith((_C6 + ":", _C9 + ":"))
    )


def test_registry_exactly_covers_unique_c6_c9_implementations_and_shared_codes() -> None:
    bindings = _c6_c9_bindings()
    grouped: dict[str, list[Any]] = {}
    for binding in bindings:
        grouped.setdefault(binding.verifier_code, []).append(binding)

    assert C6_C9_EXTERNAL_COMPONENT_VERIFIER_CODES == ("c8.verify_family_assessment_v1",)
    implementations = {
        implementation.verifier_code: implementation for implementation in C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS
    }
    assert len(implementations) == len(C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS)
    assert set(implementations) == set(grouped) - set(C6_C9_EXTERNAL_COMPONENT_VERIFIER_CODES)
    assert len(grouped["c9.verify_complete_acyclic_projection_v1"]) == 2
    assert len(implementations["c9.verify_complete_acyclic_projection_v1"].binding_keys) == 2
    assert grouped["c8.verify_family_assessment_v1"][0].implementation_id == (
        "FAMILY_SCHEMA_NAMED_VERIFIER::c8.verify_family_assessment_v1"
    )

    for code, implementation in implementations.items():
        assert implementation.binding_keys == tuple(binding.binding_key for binding in grouped[code])
        assert type(implementation.implementation) is FunctionType
        parameters = tuple(inspect.signature(implementation.implementation).parameters.values())
        assert tuple(parameter.name for parameter in parameters) == (
            "owner_row",
            "view",
            "providers",
        )
        assert all(
            parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and parameter.default is inspect.Parameter.empty
            for parameter in parameters
        )


_DIRECT_C6_CODES = (
    ("VERIFY_OBSERVED_PIT_DECLARATION_BINDING", "data_snooping_declarations"),
    ("VERIFY_CODE_MANIFEST_DECLARATION_BINDING", "data_snooping_declarations"),
    ("VERIFY_GLOBAL_OOS_EXPOSURE_IDENTITY", "global_oos_exposures"),
    ("VERIFY_OBSERVED_PIT_EXPOSURE_BINDING", "global_oos_exposures"),
    ("VERIFY_EXPOSURE_FINGERPRINT", "global_oos_exposures"),
    ("VERIFY_OOS_RELEASE_EXPOSURE_BINDING", "data_snooping_oos_releases"),
    ("VERIFY_START_EXECUTION_ATTESTATION", "data_snooping_oos_releases"),
    ("VERIFY_IMMUTABLE_ARTIFACT_LEASE", "data_snooping_oos_releases"),
    (
        "VERIFY_OOS_RELEASE_STARTED_EXECUTION_IDENTITY",
        "data_snooping_oos_releases",
    ),
    ("VERIFY_CLOSURE_EXTERNAL_REFERENCE_BRANCH", "data_snooping_family_closures"),
    ("VERIFY_CLOSURE_OBSERVED_PIT_BINDING", "data_snooping_family_closures"),
    ("VERIFY_CLOSURE_OOS_EXPOSURE_BINDING", "data_snooping_family_closures"),
    ("VERIFY_CLOSURE_CODE_MANIFEST_BINDING", "data_snooping_family_closures"),
    (
        "VERIFY_CLOSURE_START_EXECUTION_ATTESTATION",
        "data_snooping_family_closures",
    ),
    (
        "VERIFY_CLOSURE_TERMINAL_EXECUTION_ATTESTATION",
        "data_snooping_family_closures",
    ),
    ("VERIFY_CLOSURE_IMMUTABLE_ARTIFACT_LEASE", "data_snooping_family_closures"),
    ("VERIFY_PUBLICATION_CLOSURE_BINDING", "artifact_publication_receipts"),
    (
        "VERIFY_PUBLICATION_EXTERNAL_REFERENCE_TOTALITY",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_OBSERVED_PIT_BINDING",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_OOS_EXPOSURE_BINDING",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_CODE_MANIFEST_BINDING",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_START_EXECUTION_ATTESTATION",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_TERMINAL_EXECUTION_ATTESTATION",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_IMMUTABLE_ARTIFACT_LEASE",
        "artifact_publication_receipts",
    ),
)


@pytest.mark.parametrize(("code", "table_name"), _DIRECT_C6_CODES)
def test_all_direct_c6_verifiers_accept_one_globally_coherent_bundle(
    code: str,
    table_name: str,
) -> None:
    bundle = _valid_bundle()
    assert verify_c6_c9_named(
        code,
        bundle.owner_by_table[table_name],
        bundle.view,
        bundle.providers,
    )


_CONTINUITY_CODES = (
    ("VERIFY_OOS_RELEASE_START_LEASE_CONTINUITY", "data_snooping_oos_releases"),
    (
        "VERIFY_CLOSURE_OBSERVED_PIT_EXPOSURE_CONTINUITY",
        "data_snooping_family_closures",
    ),
    ("VERIFY_CLOSURE_CODE_START_CONTINUITY", "data_snooping_family_closures"),
    (
        "VERIFY_CLOSURE_START_TERMINAL_CONTINUITY",
        "data_snooping_family_closures",
    ),
    ("VERIFY_CLOSURE_START_LEASE_CONTINUITY", "data_snooping_family_closures"),
    (
        "VERIFY_PUBLICATION_OBSERVED_PIT_EXPOSURE_CONTINUITY",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_CODE_START_CONTINUITY",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_START_TERMINAL_CONTINUITY",
        "artifact_publication_receipts",
    ),
    (
        "VERIFY_PUBLICATION_START_LEASE_CONTINUITY",
        "artifact_publication_receipts",
    ),
)


@pytest.mark.parametrize(("code", "table_name"), _CONTINUITY_CODES)
def test_all_c6_continuity_verifiers_execute_strict_provider_and_time_semantics(
    code: str,
    table_name: str,
) -> None:
    bundle = _valid_bundle()
    assert verify_c6_c9_named(
        code,
        bundle.owner_by_table[table_name],
        bundle.view,
        bundle.providers,
    )


def _mutated_view(
    bundle: _Bundle,
    table_name: str,
    mutation: dict[str, object],
) -> FrozenFamilySchemaVerificationView:
    tables: dict[str, list[dict[str, object]]] = {}
    for name, rows in bundle.view._tables:
        tables[name] = [dict(row) for row in rows]
    tables[table_name][0].update(mutation)
    return FrozenFamilySchemaVerificationView.from_rows(tables)


@pytest.mark.parametrize(
    ("code", "table_name", "field"),
    (
        (
            "VERIFY_CLOSURE_OBSERVED_PIT_EXPOSURE_CONTINUITY",
            "data_snooping_family_closures",
            "observed_pit_authority_head",
        ),
        (
            "VERIFY_CLOSURE_CODE_START_CONTINUITY",
            "data_snooping_family_closures",
            "code_manifest_evidence_hash",
        ),
        (
            "VERIFY_CLOSURE_START_TERMINAL_CONTINUITY",
            "data_snooping_family_closures",
            "terminal_execution_attestation_hash",
        ),
        (
            "VERIFY_PUBLICATION_START_LEASE_CONTINUITY",
            "artifact_publication_receipts",
            "immutable_artifact_lease_hash",
        ),
    ),
)
def test_c6_continuity_rejects_provider_head_hash_or_branch_tampering(
    code: str,
    table_name: str,
    field: str,
) -> None:
    bundle = _valid_bundle()
    owner = dict(bundle.owner_by_table[table_name])
    owner[field] = _SHA
    assert not verify_c6_c9_named(code, owner, bundle.view, bundle.providers)


def test_start_terminal_rejects_non_extension_and_wrong_subject_context() -> None:
    bundle = _valid_bundle()
    provider = bundle.providers.execution_attestation
    assert isinstance(provider, _ExecutionProvider)
    start, terminal = provider.values
    provider.values = (
        start,
        replace(
            terminal,
            authority_projection_record_hashes=(
                terminal.authority_genesis_record_hash,
                "e" * 64,
                terminal.evidence.authority_head,
            ),
        ),
    )
    assert not verify_c6_c9_named(
        "VERIFY_CLOSURE_START_TERMINAL_CONTINUITY",
        bundle.owner_by_table["data_snooping_family_closures"],
        bundle.view,
        bundle.providers,
    )

    provider.values = (
        start,
        replace(
            terminal,
            subject_context=replace(
                terminal.subject_context,
                family_id="foreign-family",
            ),
        ),
    )
    assert not verify_c6_c9_named(
        "VERIFY_CLOSURE_START_TERMINAL_CONTINUITY",
        bundle.owner_by_table["data_snooping_family_closures"],
        bundle.view,
        bundle.providers,
    )


def test_start_lease_rejects_closure_outside_lease_or_wrong_attempt_row() -> None:
    bundle = _valid_bundle()
    closure = bundle.owner_by_table["data_snooping_family_closures"]
    late = dict(closure)
    late["closed_at_us"] = 9_000_000_000_000_000
    assert not verify_c6_c9_named(
        "VERIFY_CLOSURE_START_LEASE_CONTINUITY",
        late,
        bundle.view,
        bundle.providers,
    )
    bad_view = _mutated_view(
        bundle,
        "data_snooping_oos_access_attempts",
        {"status": "INCOMPLETE"},
    )
    assert not verify_c6_c9_named(
        "VERIFY_CLOSURE_START_LEASE_CONTINUITY",
        closure,
        bad_view,
        bundle.providers,
    )


_C9_LOCAL_CODES = (
    "c9.verify_publication_genesis_binding_v1",
    "c9.verify_complete_publication_chain_v1",
    "c9.verify_publication_closure_v1",
    "c9.verify_publication_family_conclusion_v1",
    "c9.verify_exporter_event_v1",
    "c9.verify_publication_branch_v1",
    "c9.verify_publication_receipt_row_hash_v1",
)


@pytest.mark.parametrize("code", _C9_LOCAL_CODES)
def test_c9_local_verifiers_accept_exact_chronological_publication(code: str) -> None:
    bundle = _valid_bundle()
    assert verify_c6_c9_named(
        code,
        bundle.receipt.model_dump(mode="python"),
        bundle.view,
        bundle.providers,
    )


_C9_GRAPH_CODES = (
    "c9.verify_exact_envelope_scan_allow_v1",
    "c9.verify_exact_envelope_graph_v1",
    "c9.verify_self_contained_artifact_bundle_v1",
    "c9.verify_complete_acyclic_projection_v1",
)


@pytest.mark.parametrize("code", _C9_GRAPH_CODES)
def test_c9_graph_verifiers_load_and_validate_exact_content_addressed_bytes(
    code: str,
) -> None:
    bundle = _valid_bundle()
    assert verify_c6_c9_named(
        code,
        bundle.receipt.model_dump(mode="python"),
        bundle.view,
        bundle.providers,
    )


@pytest.mark.parametrize(
    ("code", "mutation"),
    (
        (
            "c9.verify_publication_genesis_binding_v1",
            {"genesis_record_hash": "f" * 64},
        ),
        (
            "c9.verify_publication_closure_v1",
            {"closure_manifest_hash": "f" * 64},
        ),
        (
            "c9.verify_publication_family_conclusion_v1",
            {"family_assessment_conclusion_hash": "f" * 64},
        ),
        (
            "c9.verify_exporter_event_v1",
            {"exporter_authority_head": "f" * 64},
        ),
        (
            "c9.verify_publication_branch_v1",
            {"closure_status": "NOT_ASSESSABLE"},
        ),
        (
            "c9.verify_publication_receipt_row_hash_v1",
            {"receipt_record_hash": "f" * 64},
        ),
    ),
)
def test_c9_local_verifiers_reject_internal_reference_head_hash_and_branch_tamper(
    code: str,
    mutation: dict[str, object],
) -> None:
    bundle = _valid_bundle()
    owner = bundle.receipt.model_dump(mode="python")
    owner.update(mutation)
    assert not verify_c6_c9_named(code, owner, bundle.view, bundle.providers)


def test_c9_graph_rejects_detached_bytes_projection_and_publication_totality() -> None:
    bundle = _valid_bundle()
    provider = bundle.providers.artifact_graph
    assert isinstance(provider, _ArtifactProvider)
    envelope_key = (
        bundle.receipt.target_content_addressed_path,
        bundle.receipt.published_artifact_envelope_hash,
    )
    provider.content[envelope_key] = b"{}\n"
    for code in _C9_GRAPH_CODES:
        assert not verify_c6_c9_named(
            code,
            bundle.receipt.model_dump(mode="python"),
            bundle.view,
            bundle.providers,
        )

    bundle = _valid_bundle()
    provider = bundle.providers.artifact_graph
    assert isinstance(provider, _ArtifactProvider)
    provider.projection_refs.clear()
    assert not verify_c6_c9_named(
        "c9.verify_complete_acyclic_projection_v1",
        bundle.receipt.model_dump(mode="python"),
        bundle.view,
        bundle.providers,
    )

    extra_closure = dict(bundle.owner_by_table["data_snooping_family_closures"])
    extra_closure["family_id"] = "unpublished-family"
    extra_closure["closure_id"] = "unpublished-closure"
    extra_closure["record_hash"] = "f" * 64
    tables = {name: [dict(row) for row in rows] for name, rows in bundle.view._tables}
    tables["data_snooping_family_closures"].append(extra_closure)
    incomplete_view = FrozenFamilySchemaVerificationView.from_rows(tables)
    assert not verify_c6_c9_named(
        "c9.verify_complete_acyclic_projection_v1",
        bundle.receipt.model_dump(mode="python"),
        incomplete_view,
        bundle.providers,
    )

    for table_name in (
        "data_snooping_proposal_registrations",
        "data_snooping_proposals",
        "data_snooping_search_emission_seals",
    ):
        bundle = _valid_bundle()
        tables = {
            name: [dict(row) for row in rows]
            for name, rows in bundle.view._tables
        }
        assert tables[table_name]
        tables[table_name].pop()
        incomplete_graph_view = FrozenFamilySchemaVerificationView.from_rows(tables)
        assert not verify_c6_c9_named(
            "c9.verify_complete_acyclic_projection_v1",
            bundle.receipt.model_dump(mode="python"),
            incomplete_graph_view,
            bundle.providers,
        )


def test_c9_graph_rejects_untrusted_scanner_provider_genesis_and_activation_anchors() -> None:
    for mutation in (
        "reject_scanner",
        "reject_provider_genesis",
        "manifest_hash",
        "activation_hash",
    ):
        bundle = _valid_bundle()
        trust = bundle.providers.publication_trust
        assert isinstance(trust, _TrustProvider)
        if mutation in ("reject_scanner", "reject_provider_genesis"):
            setattr(trust, mutation, True)
        else:
            setattr(trust, mutation, "f" * 64)
        assert not verify_c6_c9_named(
            "c9.verify_self_contained_artifact_bundle_v1",
            bundle.receipt.model_dump(mode="python"),
            bundle.view,
            bundle.providers,
        )


@pytest.mark.parametrize(
    "code",
    (
        "VERIFY_OBSERVED_PIT_DECLARATION_BINDING",
        "VERIFY_CLOSURE_START_TERMINAL_CONTINUITY",
        "c9.verify_exact_envelope_graph_v1",
    ),
)
def test_missing_or_throwing_external_provider_fails_closed(code: str) -> None:
    bundle = _valid_bundle()
    owner = (
        bundle.owner_by_table["data_snooping_declarations"]
        if code == "VERIFY_OBSERVED_PIT_DECLARATION_BINDING"
        else bundle.owner_by_table["data_snooping_family_closures"]
        if code.startswith("VERIFY_")
        else bundle.receipt.model_dump(mode="python")
    )
    assert not verify_c6_c9_named(code, owner, bundle.view, None)

    if code == "VERIFY_OBSERVED_PIT_DECLARATION_BINDING":
        provider = bundle.providers.observed_pit
    elif code.startswith("VERIFY_"):
        provider = bundle.providers.execution_attestation
    else:
        provider = bundle.providers.artifact_graph
    assert provider is not None
    provider.error = True
    assert not verify_c6_c9_named(code, owner, bundle.view, bundle.providers)


def test_wrong_external_provider_claim_fails_closed() -> None:
    bundle = _valid_bundle()
    provider = bundle.providers.observed_pit
    assert isinstance(provider, _ObservedProvider)
    wrong_payload = provider.value.model_dump(mode="python")
    wrong_payload["handle_id"] = "foreign-handle"
    provider.value = research_contracts.ObservedPitDatasetHandle.model_validate(
        wrong_payload,
        strict=True,
    )
    provider.force_return = True
    assert not verify_c6_c9_named(
        "VERIFY_OBSERVED_PIT_DECLARATION_BINDING",
        bundle.owner_by_table["data_snooping_declarations"],
        bundle.view,
        bundle.providers,
    )


def test_dispatch_rejects_unknown_code_and_malformed_inputs() -> None:
    bundle = _valid_bundle()
    assert not verify_c6_c9_named(
        "UNKNOWN",
        bundle.receipt.model_dump(mode="python"),
        bundle.view,
        bundle.providers,
    )
    assert not verify_c6_c9_named(1, {}, bundle.view, bundle.providers)
    assert not verify_c6_c9_named(
        "c9.verify_publication_branch_v1",
        [],
        bundle.view,
        bundle.providers,
    )
