"""ResearchState — 「研究本身站不站得住」(727 §3.1).

One of the three ORTHOGONAL machines. It answers only whether the research holds
up; it says nothing about where the alpha runs or whether it is behaving. See
`services/lifecycle/__init__.py` for why all three exist alongside the legacy
mixed `AlphaState` rather than replacing it.

Pure data + pure functions: no I/O, no wall clock, no RNG, and no imports from
`services/*` — only `quantos.contracts`. That keeps this module a leaf so the
orchestrator (`services/alpha/lifecycle.py`) can adopt it without a cycle.
"""

from __future__ import annotations

from enum import Enum


class ResearchState(str, Enum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    # CHALLENGED has NO equivalent in the legacy AlphaState. §10.2's Challenger
    # flow needs it: today a Champion detected as decaying has nowhere to record
    # "is currently being challenged", so that fact lives only in prose.
    CHALLENGED = "CHALLENGED"
    INVALIDATED = "INVALIDATED"
    RESEARCH_RETIRED = "RESEARCH_RETIRED"


RESEARCH_ALLOWED_TRANSITIONS: dict[ResearchState, frozenset[ResearchState]] = {
    ResearchState.DRAFT: frozenset(
        # DRAFT -> INVALIDATED is not in §3.1's list, but the legacy machine has
        # DRAFT -> REJECTED. Omitting it would make the projection of a real
        # historical transition illegal, so it is kept; §3.1 says "核心转换"
        # (core transitions), not an exhaustive enumeration.
        {ResearchState.TESTING, ResearchState.INVALIDATED}
    ),
    ResearchState.TESTING: frozenset(
        {ResearchState.VALIDATED, ResearchState.INVALIDATED}
    ),
    ResearchState.VALIDATED: frozenset(
        {ResearchState.CHALLENGED, ResearchState.INVALIDATED}
    ),
    # A challenge that the incumbent survives returns it to VALIDATED — the cycle
    # §10.2 depends on. A challenge it loses invalidates it.
    ResearchState.CHALLENGED: frozenset(
        {ResearchState.VALIDATED, ResearchState.INVALIDATED}
    ),
    ResearchState.INVALIDATED: frozenset({ResearchState.RESEARCH_RETIRED}),
    ResearchState.RESEARCH_RETIRED: frozenset(),
}

RESEARCH_TERMINAL_STATES: frozenset[ResearchState] = frozenset(
    {ResearchState.RESEARCH_RETIRED}
)

# Research states that permit an alpha to be deployed at all (§3.4 invariant 1).
RESEARCH_DEPLOYABLE: frozenset[ResearchState] = frozenset(
    {ResearchState.VALIDATED, ResearchState.CHALLENGED}
)


class InvalidResearchTransition(ValueError):
    def __init__(self, src: ResearchState, dst: ResearchState) -> None:
        super().__init__(f"illegal research transition: {src.value} -> {dst.value}")
        self.src, self.dst = src, dst


def can_research_transition(src: ResearchState, dst: ResearchState) -> bool:
    return dst in RESEARCH_ALLOWED_TRANSITIONS.get(src, frozenset())


def is_research_terminal(state: ResearchState) -> bool:
    return state in RESEARCH_TERMINAL_STATES


def research_agent_reachable(dst: ResearchState) -> bool:
    """EVERY research edge is reachable by a non-human actor.

    This is not laxity, it is the point of the split: no research transition can
    grant capital authority, so none of them needs a human gate. Invalidating or
    retiring research only ever REMOVES authority. The human-only boundary lives
    entirely in `deployment_state.py`, which is why it stops being a
    hand-maintained list (the legacy `AGENT_REACHABLE`) and becomes structural.
    """
    del dst
    return True
