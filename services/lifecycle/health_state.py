"""HealthState — 「它现在表现正常吗」(727 §3.3).

Orthogonal to deployment on purpose. In the legacy mixed field a health recovery
was forced to also move the deployment position (`DEGRADED -> SHADOW` was one
edge doing two jobs); here health changes never touch where the alpha runs.

`services/alpha/degradation_monitor.py` already computes health signals and is
currently forced to express them as deployment states — this module is the type
it should have been writing into all along.

Pure data + pure functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class HealthReason(str, Enum):
    """The 11 reason codes §3.3 requires, plus two bookkeeping members."""

    DATA_STALE = "DATA_STALE"
    DATA_LINEAGE_BROKEN = "DATA_LINEAGE_BROKEN"
    SIGNAL_DECAY = "SIGNAL_DECAY"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    COST_INFLATION = "COST_INFLATION"
    SLIPPAGE_BREACH = "SLIPPAGE_BREACH"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    RISK_BREACH = "RISK_BREACH"
    MODEL_INVALIDATED = "MODEL_INVALIDATED"
    # HEALTHY carries no reason.
    NONE = "NONE"
    # A legacy PAUSED row never recorded WHY it was paused. §11.3's recovery flow
    # starts with "根因消除", so this gap is operational, not cosmetic — but
    # inventing a cause for a historical row would be fabrication.
    UNKNOWN_LEGACY = "UNKNOWN_LEGACY"


HEALTH_ALLOWED_TRANSITIONS: dict[HealthState, frozenset[HealthState]] = {
    HealthState.HEALTHY: frozenset(
        {HealthState.WATCH, HealthState.DEGRADED, HealthState.FAILED}
    ),
    HealthState.WATCH: frozenset(
        {HealthState.HEALTHY, HealthState.DEGRADED, HealthState.FAILED}
    ),
    HealthState.DEGRADED: frozenset(
        {HealthState.WATCH, HealthState.HEALTHY, HealthState.FAILED}
    ),
    HealthState.FAILED: frozenset(
        # FAILED is recoverable but never straight to HEALTHY: a root cause that
        # was resolved must be observed (§11.3 Shadow 冷静期) before the alpha is
        # called healthy again.
        {HealthState.DEGRADED, HealthState.WATCH}
    ),
}

# Health states that force the deployment machine to a de-risked position
# (§3.4 invariant 3).
HEALTH_FORCES_DE_RISK: frozenset[HealthState] = frozenset({HealthState.FAILED})


class InvalidHealthTransition(ValueError):
    def __init__(self, src: HealthState, dst: HealthState) -> None:
        super().__init__(f"illegal health transition: {src.value} -> {dst.value}")
        self.src, self.dst = src, dst


class MissingHealthReason(ValueError):
    def __init__(self, state: HealthState) -> None:
        super().__init__(f"health state {state.value} requires a reason code")
        self.state = state


@dataclass(frozen=True)
class HealthVerdict:
    """A health state and why. Fail-closed: a non-HEALTHY state without a reason
    raises, on the same grounds as §7's "a terminal failure must carry
    failure_reasons" — a degradation nobody can review is not an audit trail."""

    state: HealthState
    reason: HealthReason = HealthReason.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.state, HealthState):
            raise ValueError("state must be a HealthState")
        if not isinstance(self.reason, HealthReason):
            raise ValueError("reason must be a HealthReason")
        if self.state is HealthState.HEALTHY:
            if self.reason is not HealthReason.NONE:
                raise ValueError("HEALTHY must not carry a reason code")
        elif self.reason is HealthReason.NONE:
            raise MissingHealthReason(self.state)

    @property
    def forces_de_risk(self) -> bool:
        return self.state in HEALTH_FORCES_DE_RISK

    @property
    def is_legacy(self) -> bool:
        return self.reason is HealthReason.UNKNOWN_LEGACY


def can_health_transition(src: HealthState, dst: HealthState) -> bool:
    return dst in HEALTH_ALLOWED_TRANSITIONS.get(src, frozenset())


def health_agent_reachable(dst: HealthState) -> bool:
    """Every health edge is reachable by a non-human actor.

    Health is an OBSERVATION. §11.2 explicitly authorizes the system to de-risk
    automatically, and recording that an alpha looks degraded grants it nothing.
    A non-human actor recording FAILED can only cause the deployment machine to
    de-risk, which is always permitted.
    """
    del dst
    return True
