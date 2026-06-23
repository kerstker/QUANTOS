"""Deployment governance gate (Phase 14) — fail-closed, multi-step, human-gated.

evaluate_deployment(environment, ...) -> DeploymentDecision:
  * DEV / TEST: always allowed (research).
  * PAPER: requires a VERIFIED HUMAN ApprovalRecord (approved, approver == actor, actor human).
  * STAGING / LIVE: ALSO require readiness == L4_LIVE_APPROVED AND the SafetyConfig
    live flags enabled — both OFF/below at L2_DRY_RUN_ONLY, so these are blocked.
Any error -> blocked (fail-closed). Mirrors the DeploymentRecord contract, which
already refuses to construct a LIVE record without approved=True.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from quantos.contracts.alpha import Actor
from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyConfig
from quantos.contracts.governance import ApprovalRecord, Environment


class ReadinessLevel(str, Enum):
    L2_DRY_RUN_ONLY = "L2_DRY_RUN_ONLY"
    L4_LIVE_APPROVED = "L4_LIVE_APPROVED"


_RANK = {ReadinessLevel.L2_DRY_RUN_ONLY: 2, ReadinessLevel.L4_LIVE_APPROVED: 4}

# The platform's pinned readiness — a single, non-overridable source of truth.
# It is NOT a caller-supplied argument: trusted code cannot pass a higher
# readiness to unlock STAGING/LIVE (audit P2 — the previous L2 block depended on
# argument DEFAULTS, so a caller passing readiness=L4 + live flags flipped it).
# A requested readiness is capped at this pin, so LIVE stays structurally blocked
# until the platform itself is re-pinned. Today the platform is L2_DRY_RUN_ONLY.
PLATFORM_READINESS = ReadinessLevel.L2_DRY_RUN_ONLY

_RESEARCH_ENVS = {Environment.DEV, Environment.TEST}
_LIVE_ENVS = {Environment.STAGING, Environment.LIVE}


@dataclass(frozen=True)
class DeploymentDecision:
    allowed: bool
    environment: Environment
    reasons: tuple[str, ...] = ()


def _verified_human_approval(approval: Optional[ApprovalRecord], actor: Optional[Actor],
                             subject: str) -> list[str]:
    reasons = []
    if not subject:
        reasons.append("SUBJECT_REQUIRED")          # an empty subject must not bypass binding (review)
    if approval is None or not approval.approved:
        reasons.append("NO_HUMAN_APPROVAL")
        return reasons
    if actor is None or not actor.is_human:
        reasons.append("APPROVER_NOT_HUMAN")
    if approval.approver != (actor.actor_id if actor else None):
        reasons.append("APPROVER_IDENTITY_MISMATCH")
    if not subject or approval.subject_id != subject:
        reasons.append("APPROVAL_SUBJECT_MISMATCH")
    return reasons


def evaluate_deployment(
    environment: Environment,
    *,
    safety_config: SafetyConfig = RESEARCH_SAFE_DEFAULTS,
    approval: Optional[ApprovalRecord] = None,
    actor: Optional[Actor] = None,
    readiness: Optional[ReadinessLevel] = None,
    subject: str = "",
) -> DeploymentDecision:
    try:
        if environment in _RESEARCH_ENVS:
            return DeploymentDecision(True, environment, ())

        reasons = _verified_human_approval(approval, actor, subject)   # PAPER and above need this

        # A caller may REQUEST a readiness, but it is capped at the non-overridable
        # platform pin — passing readiness=L4 cannot unlock LIVE while the platform
        # is pinned at L2 (audit P2).
        requested = readiness or PLATFORM_READINESS
        effective = requested if _RANK[requested] <= _RANK[PLATFORM_READINESS] else PLATFORM_READINESS

        if environment in _LIVE_ENVS:
            if effective != ReadinessLevel.L4_LIVE_APPROVED:
                reasons.append("READINESS_BELOW_LIVE")
            if not (safety_config.live_trading_enabled and safety_config.real_broker_enabled
                    and safety_config.allow_order_submission):
                reasons.append("LIVE_FLAGS_DISABLED")

        return DeploymentDecision(not reasons, environment, tuple(reasons))
    except Exception:
        return DeploymentDecision(False, environment, ("DEPLOYMENT_ERROR",))   # fail-closed
