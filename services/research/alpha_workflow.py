"""Resumable Garden -> deduplication -> validation-DAG orchestration.

All clocks are caller-supplied deterministic integers.  The workflow stops at
validation; it never promotes, deploys, signs a mandate, or submits an order.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from quantos.contracts.hashing import stable_hash
from services.research.alpha_family import AlphaCandidate, deduplicate_candidates
from services.research.alpha_garden import GenerationBudget, GeneratorRegistry, generate_batch
from services.research.alpha_workflow_store import (
    AlphaWorkflowConflict,
    WorkflowSnapshot,
    WorkflowStore,
)
from services.research.trial_ledger import TrialLedger, TrialLedgerConflict, TrialRecord
from services.research.validation_dag import (
    ValidationPolicy,
    run_validation_dag,
    validation_panel_digest,
    validation_trial_identity,
)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VALIDATION_TERMINAL_STATES = {"VALIDATED", "BLOCKED"}
_POST_VALIDATION_STATES = {"SHADOW", "PAPER", "PAUSED", "RETIRED"}


@dataclass(frozen=True, slots=True)
class CandidateReceipt:
    candidate_id: str
    state: str
    trial_id: str
    artifact_commitment: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or _HEX64.fullmatch(value) is None
            for value in (self.candidate_id, self.trial_id, self.artifact_commitment)
        ):
            raise ValueError("candidate receipt identities must be lowercase sha256")
        if self.state not in _VALIDATION_TERMINAL_STATES:
            raise ValueError("candidate receipt must be VALIDATED or BLOCKED")


@dataclass(frozen=True, slots=True)
class BatchReceipt:
    batch_id: str
    state: str
    candidate_ids: tuple[str, ...]
    candidate_receipts: tuple[CandidateReceipt, ...]
    panel_digest: str
    registry_digest: str
    policy_digest: str
    budget_digest: str
    event_at: int
    generated_count: int
    representative_count: int
    validated_count: int
    blocked_count: int
    receipt_digest: str

    def __post_init__(self) -> None:
        if self.state not in _VALIDATION_TERMINAL_STATES:
            raise ValueError("batch receipt must be VALIDATED or BLOCKED")
        for value in (
            self.batch_id,
            self.panel_digest,
            self.registry_digest,
            self.policy_digest,
            self.budget_digest,
            self.receipt_digest,
        ):
            if type(value) is not str or _HEX64.fullmatch(value) is None:
                raise ValueError("batch receipt identities must be lowercase sha256")
        if type(self.event_at) is not int:
            raise ValueError("event_at must be an int")
        for name in (
            "generated_count",
            "representative_count",
            "validated_count",
            "blocked_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        candidate_ids = tuple(self.candidate_ids)
        candidate_receipts = tuple(self.candidate_receipts)
        if candidate_ids != tuple(item.candidate_id for item in candidate_receipts):
            raise ValueError("candidate receipt order does not match candidate_ids")
        if self.representative_count != len(candidate_ids):
            raise ValueError("representative_count mismatch")
        if self.generated_count < self.representative_count:
            raise ValueError("generated_count cannot be below representative_count")
        if self.validated_count != sum(item.state == "VALIDATED" for item in candidate_receipts):
            raise ValueError("validated_count mismatch")
        if self.blocked_count != sum(item.state == "BLOCKED" for item in candidate_receipts):
            raise ValueError("blocked_count mismatch")
        if self.validated_count + self.blocked_count != self.representative_count:
            raise ValueError("candidate terminal count mismatch")
        expected_state = "VALIDATED" if self.validated_count else "BLOCKED"
        if self.state != expected_state:
            raise ValueError("batch state disagrees with candidate outcomes")
        object.__setattr__(self, "candidate_ids", candidate_ids)
        object.__setattr__(self, "candidate_receipts", candidate_receipts)
        if stable_hash(_receipt_body(self)) != self.receipt_digest:
            raise ValueError("batch receipt digest mismatch")


def _registry_digest(registry: GeneratorRegistry) -> str:
    return stable_hash(
        {
            "schema": "quantos.alpha-generator-registry.v1",
            "entries": [
                {
                    "name": entry.name,
                    "version": entry.version,
                    "family": entry.family,
                    "max_share": entry.max_share,
                }
                for entry in registry.entries
            ],
        }
    )


def _budget_digest(budget: GenerationBudget, correlation_threshold: float) -> str:
    return stable_hash(
        {
            "schema": "quantos.alpha-generation-budget.v1",
            "max_candidates": budget.max_candidates,
            "max_family_candidates": budget.max_family_candidates,
            "seed": budget.seed,
            "correlation_threshold": correlation_threshold,
        }
    )


def _receipt_body(receipt: BatchReceipt) -> dict:
    return _receipt_body_from_parts(
        batch_id=receipt.batch_id,
        state=receipt.state,
        candidate_ids=receipt.candidate_ids,
        candidate_receipts=receipt.candidate_receipts,
        panel_digest=receipt.panel_digest,
        registry_digest=receipt.registry_digest,
        policy_digest=receipt.policy_digest,
        budget_digest=receipt.budget_digest,
        event_at=receipt.event_at,
        generated_count=receipt.generated_count,
    )


def _receipt_body_from_parts(
    *,
    batch_id: str,
    state: str,
    candidate_ids: tuple[str, ...],
    candidate_receipts: tuple[CandidateReceipt, ...],
    panel_digest: str,
    registry_digest: str,
    policy_digest: str,
    budget_digest: str,
    event_at: int,
    generated_count: int,
) -> dict:
    validated_count = sum(item.state == "VALIDATED" for item in candidate_receipts)
    blocked_count = len(candidate_receipts) - validated_count
    return {
        "schema": "quantos.alpha-batch-receipt.v1",
        "batch_id": batch_id,
        "state": state,
        "candidate_ids": list(candidate_ids),
        "candidate_receipts": [
            {
                "candidate_id": item.candidate_id,
                "state": item.state,
                "trial_id": item.trial_id,
                "artifact_commitment": item.artifact_commitment,
            }
            for item in candidate_receipts
        ],
        "panel_digest": panel_digest,
        "registry_digest": registry_digest,
        "policy_digest": policy_digest,
        "budget_digest": budget_digest,
        "event_at": event_at,
        "generated_count": generated_count,
        "representative_count": len(candidate_receipts),
        "validated_count": validated_count,
        "blocked_count": blocked_count,
    }


def _receipt_from_snapshot(snapshot: WorkflowSnapshot) -> BatchReceipt:
    receipts = tuple(
        CandidateReceipt(
            candidate.candidate_id,
            candidate.state,
            candidate.trial_id,
            candidate.artifact_commitment,
        )
        for candidate in snapshot.candidates
    )
    validated_count = sum(item.state == "VALIDATED" for item in receipts)
    state = "VALIDATED" if validated_count else "BLOCKED"
    digest = stable_hash(
        _receipt_body_from_parts(
            batch_id=snapshot.batch_id,
            state=state,
            candidate_ids=snapshot.candidate_ids,
            candidate_receipts=receipts,
            panel_digest=snapshot.panel_digest,
            registry_digest=snapshot.registry_digest,
            policy_digest=snapshot.policy_digest,
            budget_digest=snapshot.budget_digest,
            event_at=snapshot.event_at,
            generated_count=snapshot.generated_count,
        )
    )
    receipt = BatchReceipt(
        batch_id=snapshot.batch_id,
        state=state,
        candidate_ids=snapshot.candidate_ids,
        candidate_receipts=receipts,
        panel_digest=snapshot.panel_digest,
        registry_digest=snapshot.registry_digest,
        policy_digest=snapshot.policy_digest,
        budget_digest=snapshot.budget_digest,
        event_at=snapshot.event_at,
        generated_count=snapshot.generated_count,
        representative_count=len(receipts),
        validated_count=validated_count,
        blocked_count=len(receipts) - validated_count,
        receipt_digest=digest,
    )
    if snapshot.receipt_digest is not None and snapshot.receipt_digest != receipt.receipt_digest:
        raise AlphaWorkflowConflict("persisted batch receipt digest mismatch")
    return receipt


def _plan_trials(
    candidates: tuple[AlphaCandidate, ...],
    panel,
    policy: ValidationPolicy,
    ledger: TrialLedger,
    *,
    event_at: int,
    production: bool,
):
    planned = tuple(
        validation_trial_identity(
            candidate,
            panel,
            policy,
            event_at=event_at,
            production=production,
        )
        for candidate in candidates
    )
    for identity in planned:
        try:
            ledger.append(
                TrialRecord(
                    identity.trial_id,
                    identity.alpha_id,
                    identity.family_id,
                    identity.exact_definition_id,
                    identity.policy_digest,
                    "PLANNED",
                    0,
                    event_at,
                )
            )
        except TrialLedgerConflict:
            existing = ledger.load(identity.trial_id)
            expected = (
                identity.alpha_id,
                identity.family_id,
                identity.exact_definition_id,
                identity.policy_digest,
            )
            if existing.events[0].at != event_at or any(
                (
                    event.alpha_id,
                    event.family_id,
                    event.exact_definition_id,
                    event.validation_policy_digest,
                )
                != expected
                for event in existing.events
            ):
                raise
        aggregate = ledger.load(identity.trial_id)
        if aggregate.status == "PLANNED":
            try:
                ledger.append(
                    TrialRecord(
                        identity.trial_id,
                        identity.alpha_id,
                        identity.family_id,
                        identity.exact_definition_id,
                        identity.policy_digest,
                        "RUNNING",
                        1,
                        event_at,
                    )
                )
            except TrialLedgerConflict:
                if ledger.load(identity.trial_id).status != "RUNNING":
                    raise
    return planned


def run_candidate_batch(
    *,
    batch_id: str,
    registry: GeneratorRegistry,
    budget: GenerationBudget,
    context,
    panel,
    policy: ValidationPolicy,
    ledger: TrialLedger,
    store: WorkflowStore,
    event_at: int,
    worker_id: str,
    lease_now: int,
    lease_until: int,
    correlation_threshold: float,
    production: bool = False,
) -> BatchReceipt:
    """Run or resume a deterministic candidate batch through validation only."""
    if not isinstance(registry, GeneratorRegistry) or not isinstance(budget, GenerationBudget):
        raise TypeError("registry and budget types required")
    if not isinstance(policy, ValidationPolicy) or not isinstance(ledger, TrialLedger):
        raise TypeError("policy and ledger types required")
    if not isinstance(store, WorkflowStore):
        raise TypeError("store must be WorkflowStore")
    if type(event_at) is not int or type(lease_now) is not int or type(lease_until) is not int:
        raise ValueError("event and lease times must be deterministic ints")
    if lease_until <= lease_now:
        raise ValueError("lease_until must be after lease_now")
    if type(worker_id) is not str or not worker_id:
        raise ValueError("worker_id is required")
    if type(correlation_threshold) not in (int, float) or not math.isfinite(correlation_threshold):
        raise ValueError("correlation_threshold must be finite")

    generated = generate_batch(registry, budget, context)
    deduplicated = deduplicate_candidates(
        generated.candidates,
        correlation_threshold=correlation_threshold,
    )
    candidates = deduplicated.representatives
    candidate_ids = tuple(candidate.alpha_id for candidate in candidates)
    panel_digest = validation_panel_digest(panel)
    registry_digest = _registry_digest(registry)
    budget_digest = _budget_digest(budget, correlation_threshold)
    snapshot = store.register_batch(
        batch_id,
        candidate_ids=candidate_ids,
        panel_digest=panel_digest,
        registry_digest=registry_digest,
        policy_digest=policy.digest,
        budget_digest=budget_digest,
        event_at=event_at,
        generated_count=len(generated.candidates),
    )
    if snapshot.state in _VALIDATION_TERMINAL_STATES | _POST_VALIDATION_STATES:
        return _receipt_from_snapshot(snapshot)

    planned = _plan_trials(
        candidates,
        panel,
        policy,
        ledger,
        event_at=event_at,
        production=production,
    )
    if any(identity.panel_digest != panel_digest for identity in planned):
        raise AlphaWorkflowConflict("candidate trial panel binding mismatch")

    claim = store.claim(batch_id, worker_id, lease_until, now=lease_now)
    lease_seconds = claim.lease_until - lease_now
    if lease_seconds <= 0:
        raise AlphaWorkflowConflict("workflow lease expired before validation")
    owner = f"alpha-workflow:{batch_id}:{worker_id}:{claim.fencing_token}"
    for candidate in candidates[claim.checkpoint_watermark :]:
        artifact = run_validation_dag(
            candidate,
            panel,
            policy,
            ledger,
            event_at=event_at,
            owner=owner,
            lease_seconds=lease_seconds,
            lease_now=lease_now,
            production=production,
        )
        candidate_state = "VALIDATED" if artifact.verdict == "PASS" else "BLOCKED"
        snapshot = store.checkpoint(
            batch_id,
            worker_id,
            claim.fencing_token,
            candidate.alpha_id,
            candidate_state,
            trial_id=artifact.trial_id,
            artifact_commitment=artifact.artifact_commitment,
            now=lease_now,
        )

    receipt = _receipt_from_snapshot(snapshot)
    completed = store.complete(
        batch_id,
        worker_id,
        claim.fencing_token,
        receipt.state,
        receipt.receipt_digest,
        now=lease_now,
    )
    return _receipt_from_snapshot(completed)


__all__ = ["CandidateReceipt", "BatchReceipt", "run_candidate_batch"]
