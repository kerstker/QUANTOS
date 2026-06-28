"""Alpha maintenance pass — the missing CALLER for the decay / degradation engines (review P0 dead code).

`degradation_monitor.monitor_alpha_health` (live-vs-backtest -> DEGRADED/PAUSED) and
`AlphaRetirementSystem.sweep` (decay / regime / redundancy / capacity -> RETIRED) both existed, were tested,
but had NO caller — so a decayed alpha never actually changed state. This is that caller: a periodic,
research-only maintenance pass that, for each LIVE alpha and from SUPPLIED evidence, routes demotions and
retirement THROUGH the audited lifecycle manager as a NON-HUMAN actor.

Containment (unchanged): the maintenance actor is non-human and reaches only DEGRADED / PAUSED / RETIRED —
all AGENT_REACHABLE demotions — and NEVER APPROVED (the human-only promotion). Every transition stays
legality-checked and hash-chain audited by the manager; a per-alpha failure is isolated, never aborting the
sweep. Fail-closed: an alpha with no supplied evidence is left untouched.

L2 note: there is no live/paper feed yet (the forward paper-track persistence is a separate gap), so this is
invoked ON-DEMAND with whatever paper evidence exists today (little) rather than on a wall-clock loop — it is
the HOOK a Phase-14 scheduler will call periodically once live alphas + forward metrics exist.
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import Actor
from quantos.contracts.research import AlphaState

from .config import AlphaFactoryConfig
from .degradation_monitor import monitor_alpha_health
from .retirement import AlphaRetirementSystem

_LIVE_STATES = (AlphaState.APPROVED, AlphaState.PAPER_TRADING, AlphaState.SHADOW, AlphaState.DEGRADED)


def run_maintenance(
    manager,
    actor: Actor,
    *,
    timestamp: int,
    health_by_alpha: Optional[dict] = None,
    decay_evidence_by_alpha: Optional[dict] = None,
    config: Optional[AlphaFactoryConfig] = None,
    live_states: tuple = _LIVE_STATES,
) -> dict:
    """Run one maintenance pass over the LIVE alphas. Returns a report alpha_id -> {action, ...}.

    health_by_alpha: alpha_id -> {"backtest": {...}, "live": {...}} for the degradation monitor (the
      live-vs-backtest metric dicts: ic / return / slippage_bps / turnover / drawdown / capacity_breach_frac).
    decay_evidence_by_alpha: alpha_id -> {decay, regime_profitable_frac, max_corr_to_approved,
      capacity_notional} for the retirement sweep. Both default empty (no evidence -> no action, fail-closed).
    """
    if actor.is_human:
        raise ValueError("maintenance is an automated non-human pass (it performs no human-only action)")
    config = config or AlphaFactoryConfig()
    health_by_alpha = health_by_alpha or {}
    report: dict[str, dict] = {}

    # 1) degradation_monitor -> DEGRADED / PAUSED (live-vs-backtest), audited and isolated per alpha.
    for aid in list(manager.catalog.all_ids()):
        if manager.catalog.state(aid) not in live_states:
            continue
        ev = health_by_alpha.get(aid)
        if not ev:
            continue
        try:
            health = monitor_alpha_health(ev.get("backtest", {}), ev.get("live", {}))
            rec = health.get("recommended_state")
            if rec in (AlphaState.DEGRADED, AlphaState.PAUSED) and manager.catalog.state(aid) != rec:
                manager.advance(aid, rec, actor, timestamp=timestamp)
                report[aid] = {"action": rec.value, "alerts": health.get("alerts", [])}
        except Exception as exc:                       # isolate: one alpha never aborts the whole sweep
            report.setdefault(aid, {})["error"] = type(exc).__name__

    # 2) retirement sweep -> RETIRED (decay / regime / redundancy / capacity), routed through the manager.
    swept = AlphaRetirementSystem(manager, config).sweep(
        decay_evidence_by_alpha or {}, actor, timestamp=timestamp, states=live_states)
    for aid, (decision, transition) in swept.items():
        if transition is not None:
            report[aid] = {"action": "RETIRED", "reasons": list(decision.reasons)}
    return report
