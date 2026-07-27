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

727 C3: the sweep set is selected by DEPLOYMENT state (is the alpha running
anywhere), not by the legacy mixed field — the old `_LIVE_STATES` contained
DEGRADED, a HEALTH value that had no business in a deployment list. Degradation
verdicts are routed through `manager.advance_health` (the health machine), so a
demotion no longer overwrites the alpha's deployment position and now carries a
machine-readable reason code. The report's action strings ("DEGRADED"/"PAUSED")
are UNCHANGED — live_linkage and the maintenance-event outbox consume them.

L2 note: there is no live/paper feed yet (the forward paper-track persistence is a separate gap), so this is
invoked ON-DEMAND with whatever paper evidence exists today (little) rather than on a wall-clock loop — it is
the HOOK a Phase-14 scheduler will call periodically once live alphas + forward metrics exist.
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import Actor
from quantos.contracts.research import AlphaState

from services.lifecycle import DeploymentState, HealthState

from .config import AlphaFactoryConfig
from .degradation_monitor import health_verdict_for, monitor_alpha_health
from .retirement import AlphaRetirementSystem

# 727 C3: the sweep set, expressed in deployment states (where the alpha RUNS).
_LIVE_DEPLOYMENT_STATES = (
    DeploymentState.PAPER,
    DeploymentState.SHADOW,
    DeploymentState.MICRO_LIVE,
    DeploymentState.LIVE,
)

# Back-compat alias for callers that only need the tuple; the semantics are
# deployment states since C3 (see module docstring).
_LIVE_STATES = _LIVE_DEPLOYMENT_STATES


def _in_sweep(catalog, alpha_id: str, deployment_states: tuple) -> bool:
    dep = catalog.deployment_state(alpha_id)
    if dep in deployment_states:
        return True
    # A legacy DEGRADED row lost its deployment position to the mixed field
    # (UNKNOWN_LEGACY) but WAS running — the old _LIVE_STATES kept it swept.
    # Dropping it now would silently un-monitor a degraded alpha: keep it.
    return dep is DeploymentState.UNKNOWN_LEGACY


def run_maintenance(
    manager,
    actor: Actor,
    *,
    timestamp: int,
    health_by_alpha: Optional[dict] = None,
    decay_evidence_by_alpha: Optional[dict] = None,
    config: Optional[AlphaFactoryConfig] = None,
    live_states: tuple = _LIVE_DEPLOYMENT_STATES,
) -> dict:
    """Run one maintenance pass over the LIVE alphas. Returns a report alpha_id -> {action, ...}.

    health_by_alpha: alpha_id -> {"backtest": {...}, "live": {...}} for the degradation monitor (the
      live-vs-backtest metric dicts: ic / return / slippage_bps / turnover / drawdown / capacity_breach_frac).
    decay_evidence_by_alpha: alpha_id -> {decay, regime_profitable_frac, max_corr_to_approved,
      capacity_notional} for the retirement sweep. Both default empty (no evidence -> no action, fail-closed).

    `live_states` takes DeploymentState values since 727 C3; passing legacy
    AlphaState values raises (silently matching nothing would un-monitor every
    alpha — the dangerous direction).
    """
    if actor.is_human:
        raise ValueError("maintenance is an automated non-human pass (it performs no human-only action)")
    if any(isinstance(state, AlphaState) for state in live_states):
        raise TypeError(
            "live_states takes DeploymentState values since 727 C3 "
            "(the legacy mixed field mixed health into a deployment list)"
        )
    config = config or AlphaFactoryConfig()
    health_by_alpha = health_by_alpha or {}
    report: dict[str, dict] = {}

    # 1) degradation_monitor -> health machine (DEGRADED / FAILED+de-risk), audited and isolated per alpha.
    for aid in list(manager.catalog.all_ids()):
        if not _in_sweep(manager.catalog, aid, live_states):
            continue
        ev = health_by_alpha.get(aid)
        if not ev:
            continue
        try:
            health = monitor_alpha_health(ev.get("backtest", {}), ev.get("live", {}))
            recommendation = health.get("recommendation")
            verdict = health_verdict_for(recommendation, health.get("alerts", []))
            if verdict is not None and manager.catalog.health(aid) != verdict:
                manager.advance_health(aid, verdict, actor, timestamp=timestamp)
                # Report strings UNCHANGED for live_linkage / maintenance events:
                # health FAILED is what the legacy PAUSED report action meant.
                action = (
                    "PAUSED" if verdict.state is HealthState.FAILED else "DEGRADED"
                )
                report[aid] = {"action": action, "alerts": health.get("alerts", [])}
        except Exception as exc:                       # isolate: one alpha never aborts the whole sweep
            report.setdefault(aid, {})["error"] = type(exc).__name__

    # 2) retirement sweep -> RETIRED (decay / regime / redundancy / capacity), routed through the manager.
    swept = AlphaRetirementSystem(manager, config).sweep(
        decay_evidence_by_alpha or {}, actor, timestamp=timestamp, states=live_states)
    for aid, (decision, transition) in swept.items():
        if transition is not None:
            report[aid] = {"action": "RETIRED", "reasons": list(decision.reasons)}
    return report
