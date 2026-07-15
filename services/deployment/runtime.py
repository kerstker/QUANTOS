"""Authoritative L2 deployment-evaluation entry point."""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from quantos.contracts.alpha import Actor
from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyConfig
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.governance import ApprovalRecord, Environment
from services.governance.approval import ApprovalRequest

from .events import deployment_recorded_event
from .gate import (
    DeploymentDecision,
    ReadinessLevel,
    VersionRecord,
    evaluate_deployment,
)
from .store import (
    DeploymentDecisionStore,
    DeploymentEvaluation,
    DeploymentEvaluationState,
)


DEPLOYMENT_STATE_DB = os.path.join("data", "cache", "deployment_state.db")


def open_deployment_decision_store(
    path: str = DEPLOYMENT_STATE_DB,
    *,
    create: bool = True,
) -> DeploymentDecisionStore:
    return DeploymentDecisionStore(path, create=create)


def _actor_id(actor: Actor | None, grant: ApprovalRequest | None) -> str:
    if actor is not None:
        return actor.actor_id
    return "multi_party_grant" if grant is not None else "unknown"


def _version_id(version: VersionRecord) -> str:
    for attr in ("config_id", "strategy_id", "model_id"):
        value = getattr(version, attr, None)
        if value:
            return str(value)
    return ""


def evaluate_and_record_deployment(
    store: DeploymentDecisionStore,
    environment: Environment,
    *,
    safety_config: SafetyConfig = RESEARCH_SAFE_DEFAULTS,
    approval: ApprovalRecord | None = None,
    actor: Actor | None = None,
    readiness: ReadinessLevel | None = None,
    subject: str = "",
    grant: ApprovalRequest | None = None,
    versions: Sequence[VersionRecord] = (),
    approved_pins: Mapping[str, str] | None = None,
    at: int = 0,
) -> DeploymentDecision:
    """Evaluate and atomically retain state plus outbox; never apply a deployment."""
    decision = evaluate_deployment(
        environment,
        safety_config=safety_config,
        approval=approval,
        actor=actor,
        readiness=readiness,
        subject=subject,
        grant=grant,
        versions=versions,
        approved_pins=approved_pins,
        at=at,
    )
    actor_id = _actor_id(actor, grant)
    version_ids = tuple(_version_id(version) for version in versions)
    causation_id = (
        grant.request_id
        if grant is not None
        else approval.approval_id if approval is not None else ""
    )
    try:
        event = deployment_recorded_event(
            environment=environment.value,
            subject=subject,
            actor_id=actor_id,
            allowed=decision.allowed,
            reasons=decision.reasons,
            version_ids=version_ids,
            at_ms=at,
        )
        record = DeploymentEvaluation(
            decision_id=event.event_id,
            environment=environment,
            subject=subject,
            actor_id=actor_id,
            state=(
                DeploymentEvaluationState.ELIGIBLE_NOT_APPLIED
                if decision.allowed
                else DeploymentEvaluationState.DENIED
            ),
            allowed=decision.allowed,
            reasons=decision.reasons,
            version_ids=version_ids,
            evaluated_at_ms=at,
            causation_id=causation_id,
        )
        store.record_evaluation(
            record,
            EventOutboxMessage(
                event=event,
                correlation_id=subject,
                causation_id=causation_id,
            ),
        )
        return decision
    except Exception:
        return DeploymentDecision(
            False,
            environment,
            ("DEPLOYMENT_STATE_ERROR",),
        )


__all__ = [
    "DEPLOYMENT_STATE_DB",
    "evaluate_and_record_deployment",
    "open_deployment_decision_store",
]
