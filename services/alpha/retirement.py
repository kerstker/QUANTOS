"""Alpha retirement (Phase 6, step 11).

Turns decay / regime / redundancy / capacity signals into a RetirementDecision
and, when retiring, routes the alpha to RETIRED THROUGH the lifecycle manager so
the transition is still legality-checked and audited (never a silent state poke).
A regime break is a fast-path trigger (it does not wait for the decay debounce).
Reasons are emitted in a deterministic priority: regime > decay > redundancy >
capacity.
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import Actor, DecayReport, RetirementDecision
from quantos.contracts.research import AlphaState

from .config import AlphaFactoryConfig


def assess_retirement(
    alpha_id: str,
    *,
    config: Optional[AlphaFactoryConfig] = None,
    decay: Optional[DecayReport] = None,
    regime_profitable_frac: Optional[float] = None,
    max_corr_to_approved: Optional[float] = None,
    capacity_notional: Optional[float] = None,
) -> RetirementDecision:
    config = config or AlphaFactoryConfig()
    reasons: list[str] = []
    if regime_profitable_frac is not None and regime_profitable_frac < config.min_profitable_regime_frac:
        reasons.append("REGIME_BREAK")                      # fast-path (no debounce)
    if decay is not None and decay.decayed:
        reasons.append("DECAY")
    if max_corr_to_approved is not None and max_corr_to_approved > config.rho_cut:
        reasons.append("REDUNDANT_WITH_APPROVED")
    if capacity_notional is not None and capacity_notional < config.min_capacity_notional:
        reasons.append("CAPACITY_BELOW_MIN")
    return RetirementDecision(alpha_id=alpha_id, retire=bool(reasons), reasons=tuple(reasons))


class AlphaRetirementSystem:
    def __init__(self, manager, config: Optional[AlphaFactoryConfig] = None):
        self.manager = manager
        self.config = config or AlphaFactoryConfig()

    def assess(self, alpha_id: str, **evidence) -> RetirementDecision:
        return assess_retirement(alpha_id, config=self.config, **evidence)

    def retire_if_needed(self, alpha_id: str, actor: Actor, *, timestamp: int, **evidence):
        """Returns (decision, transition|None). Retirement goes through the manager
        so it is audited; survivors stay queryable in the catalog (no survivorship bias)."""
        decision = self.assess(alpha_id, **evidence)
        if not decision.retire:
            return decision, None
        tr = self.manager.advance(alpha_id, AlphaState.RETIRED, actor, timestamp=timestamp)
        return decision, tr

    def sweep(
        self,
        evidence_by_alpha: dict[str, dict],
        actor: Actor,
        *,
        timestamp: int,
        states: tuple[AlphaState, ...] = (AlphaState.APPROVED, AlphaState.PAPER_TRADING),
    ) -> dict[str, tuple]:
        """The production DRIVER: scan live (APPROVED/PAPER_TRADING) alphas and
        retire those that have decayed. Without a caller of this, decay/retirement
        is dead code (review P0). A scheduler invokes sweep periodically; the
        scheduling itself is out of scope at L2 (no live loop).
        """
        results: dict[str, tuple] = {}
        for aid in self.manager.catalog.all_ids():
            if self.manager.catalog.state(aid) in states:
                results[aid] = self.retire_if_needed(
                    aid, actor, timestamp=timestamp, **evidence_by_alpha.get(aid, {}))
        return results
