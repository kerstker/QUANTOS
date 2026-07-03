"""Deployment governance gate (Phase 14) — fail-closed, multi-step, human-gated.

evaluate_deployment(environment, ...) -> DeploymentDecision:
  * DEV / TEST: always allowed (research).
  * PAPER: requires a VERIFIED HUMAN ApprovalRecord (approved, approver == actor, actor
    human) AND content-hash-pinned versions (Rank #9): the deploy must state WHAT it
    deploys (ConfigVersion/StrategyVersion/ModelVersion payload) and every stated
    version's content_hash must match the approved pin registry.
  * STAGING / LIVE: ALSO require readiness == L4_LIVE_APPROVED AND the SafetyConfig
    live flags enabled — both OFF/below at L2_DRY_RUN_ONLY, so these are blocked.
Any error -> blocked (fail-closed). Mirrors the DeploymentRecord contract, which
already refuses to construct a LIVE record without approved=True.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence, Union

from quantos.contracts.alpha import Actor
from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyConfig
from quantos.contracts.governance import (
    ApprovalRecord, ConfigVersion, Environment, ModelVersion, StrategyVersion,
)
from services.governance.approval import ApprovalRequest, verify_multi_party_grant

VersionRecord = Union[ConfigVersion, StrategyVersion, ModelVersion]


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


def _version_id(version: VersionRecord) -> str:
    for attr in ("config_id", "strategy_id", "model_id"):
        vid = getattr(version, attr, None)
        if vid:
            return str(vid)
    return ""


def _verify_version_pins(versions: Sequence[VersionRecord],
                         approved_pins: Optional[Mapping[str, str]]) -> list[str]:
    """Rank #9 — fail-closed pin verification. A PAPER+ deploy must state its versions
    and each must match the approved pin registry (id -> expected content_hash). The
    version CONTRACTS predate this check; the gate never verified them before — this
    closes the contract-to-gate gap ('version-pin gate' is now literally true)."""
    if not versions or approved_pins is None:
        return ["VERSION_PIN_REQUIRED"]
    reasons: list[str] = []
    for version in versions:
        vid = _version_id(version)
        if not version.content_hash:
            reasons.append("VERSION_HASH_MISSING")
        elif vid not in approved_pins:
            reasons.append("VERSION_NOT_PINNED")
        elif approved_pins[vid] != version.content_hash:
            reasons.append("VERSION_PIN_MISMATCH")
        if isinstance(version, StrategyVersion) and not version.approved:
            reasons.append("STRATEGY_VERSION_NOT_APPROVED")
    return reasons


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
    grant: Optional[ApprovalRequest] = None,
    versions: Sequence[VersionRecord] = (),
    approved_pins: Optional[Mapping[str, str]] = None,
) -> DeploymentDecision:
    try:
        if environment in _RESEARCH_ENVS:
            return DeploymentDecision(True, environment, ())

        # PAPER and above need human approval. Multi-party path (grant=) raises the
        # bar to 3 distinct humans + hash-chain audit; the single-record path is kept
        # unchanged for backward compatibility (additive, no weakening).
        if grant is not None:
            reasons = [] if verify_multi_party_grant(grant, subject) else ["NO_MULTI_PARTY_APPROVAL"]
        else:
            reasons = _verified_human_approval(approval, actor, subject)

        # Rank #9: PAPER and above must also state WHAT is deployed, content-hash
        # pinned against the approved registry — the stronger grant path does not
        # weaken this (approval says WHO may deploy; pins say WHAT may deploy).
        reasons.extend(_verify_version_pins(versions, approved_pins))

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
