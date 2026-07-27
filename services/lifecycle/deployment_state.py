"""DeploymentState — 「它跑在哪里」(727 §3.2).

The ONLY one of the three machines with human-only edges, and they are exactly
the edges that put real capital at risk. That coincidence is the design: §1's
「运营自治,资本受控」 becomes a machine boundary instead of a list someone has to
remember to maintain.

Pure data + pure functions. No I/O, no wall clock, no broker or network import.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from quantos.contracts.alpha import Actor


class DeploymentState(str, Enum):
    OFFLINE = "OFFLINE"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    MICRO_LIVE = "MICRO_LIVE"
    LIVE = "LIVE"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"
    # Honest sentinel for rows written before the three machines existed, where
    # this dimension was never recorded (the legacy DEGRADED overwrote it). It is
    # IN the enum so it round-trips through storage as a real value, but it has
    # ZERO inbound edges — structurally unreachable going forward, and a test
    # pins that. Guessing a position instead would be fabrication.
    UNKNOWN_LEGACY = "UNKNOWN_LEGACY"


# Deployment states in which REAL capital is at risk. This set — not a ladder
# position — is what requires a human. PAPER and SHADOW sit above OFFLINE but
# risk nothing, which is why the legacy `AGENT_REACHABLE` correctly let an agent
# drive into them; that parity is preserved here.
REAL_CAPITAL_STATES: frozenset[DeploymentState] = frozenset(
    {DeploymentState.MICRO_LIVE, DeploymentState.LIVE}
)

# De-risked positions. Any actor may drive INTO these at any time (§11.2 —
# the system may always reduce risk automatically, never increase it).
DE_RISKED_STATES: frozenset[DeploymentState] = frozenset(
    {DeploymentState.PAUSED, DeploymentState.CLOSED}
)

DEPLOYMENT_ALLOWED_TRANSITIONS: dict[DeploymentState, frozenset[DeploymentState]] = {
    DeploymentState.OFFLINE: frozenset({DeploymentState.PAPER, DeploymentState.CLOSED}),
    DeploymentState.PAPER: frozenset(
        {DeploymentState.SHADOW, DeploymentState.PAUSED, DeploymentState.CLOSED}
    ),
    DeploymentState.SHADOW: frozenset(
        {DeploymentState.MICRO_LIVE, DeploymentState.PAUSED, DeploymentState.CLOSED}
    ),
    DeploymentState.MICRO_LIVE: frozenset(
        {DeploymentState.LIVE, DeploymentState.PAUSED, DeploymentState.CLOSED}
    ),
    # LIVE has no upward edge; it is the top.
    DeploymentState.LIVE: frozenset({DeploymentState.PAUSED, DeploymentState.CLOSED}),
    # §11.2 forbids PAUSED -> LIVE outright. Recovery re-enters at MICRO_LIVE and
    # must re-earn LIVE, so a paused live book cannot be restored in one step.
    DeploymentState.PAUSED: frozenset(
        {
            DeploymentState.PAPER,
            DeploymentState.SHADOW,
            DeploymentState.MICRO_LIVE,
            DeploymentState.CLOSED,
        }
    ),
    # §3.2: CLOSED 不得原地恢复 — only a NEW deployment instance may follow.
    DeploymentState.CLOSED: frozenset(),
    # A legacy row can always be made safe without first guessing where it was.
    DeploymentState.UNKNOWN_LEGACY: frozenset(
        {DeploymentState.PAUSED, DeploymentState.CLOSED}
    ),
}

# Ordering used only to express de-risking DIRECTION (for §11.2 checks and the
# capital-weight ladder). Authorization does NOT read this — see
# requires_human_authorization.
_RISK_RANK: dict[DeploymentState, int] = {
    DeploymentState.CLOSED: -1,
    DeploymentState.PAUSED: -1,
    DeploymentState.OFFLINE: 0,
    DeploymentState.PAPER: 1,
    DeploymentState.SHADOW: 2,
    DeploymentState.MICRO_LIVE: 3,
    DeploymentState.LIVE: 4,
}


class InvalidDeploymentTransition(ValueError):
    def __init__(self, src: DeploymentState, dst: DeploymentState) -> None:
        super().__init__(f"illegal deployment transition: {src.value} -> {dst.value}")
        self.src, self.dst = src, dst


class UnauthorizedDeployment(PermissionError):
    def __init__(self, actor: Actor, dst: DeploymentState) -> None:
        super().__init__(
            f"actor kind {actor.kind.value} may not deploy to {dst.value}"
        )
        self.actor, self.dst = actor, dst


def can_deployment_transition(src: DeploymentState, dst: DeploymentState) -> bool:
    return dst in DEPLOYMENT_ALLOWED_TRANSITIONS.get(src, frozenset())


def requires_human_authorization(src: DeploymentState, dst: DeploymentState) -> bool:
    """True iff this edge puts real capital at risk that was not already at risk.

    Structural, not enumerated: adding a future real-capital state to
    REAL_CAPITAL_STATES makes this rule cover it automatically. The legacy
    `AGENT_REACHABLE` was a list whose correctness depended on remembering to
    keep APPROVED out of it.

    De-risking is never gated — an edge into PAUSED/CLOSED returns False for any
    actor, because the system must always be able to reduce risk (§11.2).
    """
    if dst in DE_RISKED_STATES:
        return False
    if dst not in REAL_CAPITAL_STATES:
        return False
    # Already at real-capital risk and staying within it still counts: §11.2
    # names MICRO_LIVE -> LIVE explicitly as human-only.
    del src
    return True


def authorize_deployment_transition(
    *,
    src: DeploymentState,
    dst: DeploymentState,
    actor: Actor,
) -> tuple[bool, tuple[str, ...]]:
    """Fail-closed authorization. Returns (allowed, reasons).

    This answers "is a human required and present", NOT "is the Grant valid" —
    §3.2 additionally requires a human-signed Deployment Grant for MICRO_LIVE and
    LIVE, which the caller must verify. Both checks must pass.
    """
    reasons: list[str] = []
    if not can_deployment_transition(src, dst):
        reasons.append("ILLEGAL_TRANSITION")
    if requires_human_authorization(src, dst) and not actor.is_human:
        reasons.append("HUMAN_ACTOR_REQUIRED")
    if dst in REAL_CAPITAL_STATES:
        # Defence in depth, and the reason this module can define MICRO_LIVE and
        # LIVE at all: the enum member exists so the model is complete, but the
        # path stays structurally shut at L2 regardless of actor or grant. Same
        # shape as LiveBrokerAdapter — a seam that refuses.
        reasons.append("REAL_CAPITAL_BLOCKED_AT_L2")
    return (not reasons), tuple(reasons)


def is_deployed(state: DeploymentState) -> bool:
    """Is the alpha running anywhere (paper included)? §3.4 invariant 1 reads this."""
    return state in {
        DeploymentState.PAPER,
        DeploymentState.SHADOW,
        DeploymentState.MICRO_LIVE,
        DeploymentState.LIVE,
    }


def risk_rank(state: DeploymentState) -> Optional[int]:
    """Position on the risk ladder, or None when unrankable (legacy sentinel)."""
    return _RISK_RANK.get(state)


def is_de_risking(src: DeploymentState, dst: DeploymentState) -> Optional[bool]:
    """True if this edge lowers risk. None when either end is unrankable —
    the caller must not read None as "safe"."""
    a, b = risk_rank(src), risk_rank(dst)
    if a is None or b is None:
        return None
    return b < a
