"""Projection: legacy mixed `AlphaState` -> the three orthogonal machines (727 §4 of
the design doc, §3.4 invariants).

Why a projection instead of a rename: `AlphaTransition` feeds `src.value` /
`dst.value` into `evidence_hash` on a `prev_hash` chain, and stored Deployment
Grants are bound to the literal subject `promotion:{alpha_id}:APPROVED` which
`parse_promotion_subject` parses back with `AlphaState(...)`. Renaming a value
would invalidate every historical transition chain AND every grant three real
humans already signed. So the legacy enum stays as the historical/audit identity
and the three machines land alongside it.

Three legacy values lost information. This module returns an explicit sentinel and
names the lost dimension in `lossy` rather than guessing — the same call as
refusing to backfill trial provenance: what was never recorded cannot be invented
afterwards.

Pure functions. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from quantos.contracts.research import AlphaState

from .deployment_state import DeploymentState, is_deployed
from .health_state import HealthReason, HealthState, HealthVerdict
from .research_state import RESEARCH_DEPLOYABLE, ResearchState


@dataclass(frozen=True)
class LifecycleTriple:
    """The three machines' values for one alpha, plus which dimensions the source
    never recorded. `lossy` being machine-readable is the point: it makes the
    information loss testable instead of a footnote."""

    research: ResearchState
    deployment: DeploymentState
    health: HealthVerdict
    lossy: tuple[str, ...] = ()

    @property
    def is_legacy(self) -> bool:
        return bool(self.lossy)


# Dimension labels used in `lossy`.
LOSSY_DEPLOYMENT = "deployment"
LOSSY_HEALTH = "health"
LOSSY_HEALTH_REASON = "health_reason"
# The legacy machine had no CHALLENGED state, so a deployed alpha is recorded as
# VALIDATED without knowing whether it was under challenge at the time.
LOSSY_RESEARCH_CHALLENGED = "research_challenged"


_PROJECTION: dict[AlphaState, LifecycleTriple] = {
    AlphaState.DRAFT: LifecycleTriple(
        research=ResearchState.DRAFT,
        deployment=DeploymentState.OFFLINE,
        health=HealthVerdict(HealthState.HEALTHY),
    ),
    AlphaState.TESTING: LifecycleTriple(
        research=ResearchState.TESTING,
        deployment=DeploymentState.OFFLINE,
        health=HealthVerdict(HealthState.HEALTHY),
    ),
    AlphaState.VALIDATED: LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.OFFLINE,
        health=HealthVerdict(HealthState.HEALTHY),
    ),
    AlphaState.PAPER_TRADING: LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.PAPER,
        health=HealthVerdict(HealthState.HEALTHY),
        lossy=(LOSSY_RESEARCH_CHALLENGED,),
    ),
    AlphaState.SHADOW: LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.SHADOW,
        health=HealthVerdict(HealthState.HEALTHY),
        lossy=(LOSSY_RESEARCH_CHALLENGED,),
    ),
    # APPROVED is an AUTHORIZATION state, not evidence that capital is deployed.
    # It projects to LIVE because that is what the human authorized; the five
    # safety flags and the readiness pin remain the INDEPENDENT structural block
    # on execution. A test pins that this projection confers no order capability.
    AlphaState.APPROVED: LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.LIVE,
        health=HealthVerdict(HealthState.HEALTHY),
        lossy=(LOSSY_RESEARCH_CHALLENGED,),
    ),
    # The deployment position is genuinely GONE: one field meant moving to
    # DEGRADED overwrote wherever it was running. Refusing to guess SHADOW.
    AlphaState.DEGRADED: LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.UNKNOWN_LEGACY,
        health=HealthVerdict(HealthState.DEGRADED, HealthReason.UNKNOWN_LEGACY),
        lossy=(
            LOSSY_DEPLOYMENT,
            LOSSY_HEALTH_REASON,
            LOSSY_RESEARCH_CHALLENGED,
        ),
    ),
    # WATCH, not HEALTHY and not FAILED: the honest middle ground for "was stopped,
    # cause never recorded". §15.8's WATCH disposition — do not add capital, raise
    # monitoring — is exactly right for this row.
    AlphaState.PAUSED: LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.PAUSED,
        health=HealthVerdict(HealthState.WATCH, HealthReason.UNKNOWN_LEGACY),
        lossy=(
            LOSSY_HEALTH,
            LOSSY_HEALTH_REASON,
            LOSSY_RESEARCH_CHALLENGED,
        ),
    ),
    AlphaState.RETIRED: LifecycleTriple(
        research=ResearchState.RESEARCH_RETIRED,
        deployment=DeploymentState.CLOSED,
        health=HealthVerdict(HealthState.HEALTHY),
    ),
    AlphaState.REJECTED: LifecycleTriple(
        research=ResearchState.INVALIDATED,
        deployment=DeploymentState.CLOSED,
        health=HealthVerdict(HealthState.HEALTHY),
    ),
}


def project(state: AlphaState) -> LifecycleTriple:
    """Project one legacy state onto the three machines. Total over AlphaState;
    an unmapped member raises rather than defaulting to something plausible."""
    triple = _PROJECTION.get(state)
    if triple is None:
        raise KeyError(f"AlphaState has no projection: {state!r}")
    return triple


def check_composite_invariants(triple: LifecycleTriple) -> tuple[str, ...]:
    """Return the §3.4 invariants this triple VIOLATES (empty tuple = consistent).

    A legacy triple (one with any `lossy` dimension bearing a sentinel) is exempt
    from the invariants that need the missing information: holding it to them
    would force either rewriting history or fabricating the absent dimension.
    Sentinel-free dimensions are still checked.
    """
    violations: list[str] = []

    unknown_deployment = triple.deployment is DeploymentState.UNKNOWN_LEGACY

    # 1. Deployed anywhere => research must be deployable.
    if not unknown_deployment and is_deployed(triple.deployment):
        if triple.research not in RESEARCH_DEPLOYABLE:
            violations.append("DEPLOYED_WITHOUT_DEPLOYABLE_RESEARCH")

    # 2. Invalidated research => must not be deployed.
    if triple.research is ResearchState.INVALIDATED and not unknown_deployment:
        if is_deployed(triple.deployment):
            violations.append("INVALIDATED_RESEARCH_STILL_DEPLOYED")

    # 3. FAILED health => must be de-risked.
    if triple.health.forces_de_risk and not unknown_deployment:
        if is_deployed(triple.deployment):
            violations.append("FAILED_HEALTH_STILL_DEPLOYED")

    # 4. Non-HEALTHY => must carry a reason. (HealthVerdict enforces this at
    #    construction; re-checked here so a hand-built triple cannot slip past.)
    if (
        triple.health.state is not HealthState.HEALTHY
        and triple.health.reason is HealthReason.NONE
    ):
        violations.append("NON_HEALTHY_WITHOUT_REASON")

    return tuple(violations)
