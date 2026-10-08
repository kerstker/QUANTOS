"""Live/paper-vs-backtest degradation monitor (Alpha Factory upgrade Phase 6).

The audit found no comparator between an alpha's REALIZED behaviour and its backtest expectation. This
diffs live (or paper) IC / return / slippage / turnover / drawdown / capacity against the backtest and
RECOMMENDS a lifecycle action:

  RETIRED  — the edge is gone (live IC flipped negative, or live return lost ~all of the backtest edge);
  PAUSED   — execution broke or risk spiked (slippage blow-out, capacity breach, drawdown over limit);
  DEGRADED — predictive power eroded but not gone (IC retention below floor, return/turnover drift);
  OK       — realized behaviour tracks the backtest.

It ONLY RECOMMENDS. A human + the lifecycle manager decide and enforce the transition (which the new
SHADOW/DEGRADED/PAUSED guards permit) — containment is unchanged; the monitor holds no authority and
drives no state. Severity priority RETIRED > PAUSED > DEGRADED > OK; every breach is still reported.
Pure-Python stdlib, deterministic (no RNG, no wall-clock); fail-closed (missing inputs are flagged, a
comparison is never fabricated).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from quantos.contracts.research import AlphaState

from services.lifecycle import HealthReason, HealthState, HealthVerdict

_RANK = {"OK": 0, "DEGRADED": 1, "PAUSED": 2, "RETIRED": 3}
_STATE = {"OK": None, "DEGRADED": AlphaState.DEGRADED, "PAUSED": AlphaState.PAUSED,
          "RETIRED": AlphaState.RETIRED}

# 727 C3: the monitor's legacy recommendation translated into the orthogonal
# health machine. A PAUSED recommendation means execution or risk BROKE — that
# is health FAILED (which forces the deployment machine to de-risk, §3.4
# invariant 3); a DEGRADED recommendation is eroded-but-present predictive
# power. RETIRED is deliberately absent: retirement stays with the retirement
# sweep (research INVALIDATED + deployment CLOSED), not the health machine.
_RECOMMENDATION_TO_HEALTH = {
    "DEGRADED": HealthState.DEGRADED,
    "PAUSED": HealthState.FAILED,
}

_ALERT_METRIC_TO_REASON = {
    "ic_sign": HealthReason.SIGNAL_DECAY,
    "ic_retention": HealthReason.SIGNAL_DECAY,
    "return": HealthReason.SIGNAL_DECAY,
    "turnover": HealthReason.COST_INFLATION,
    "slippage": HealthReason.SLIPPAGE_BREACH,
    "drawdown": HealthReason.RISK_BREACH,
    "capacity": HealthReason.RISK_BREACH,
    "data": HealthReason.DATA_STALE,
}

_FALLBACK_REASON = {
    "DEGRADED": HealthReason.SIGNAL_DECAY,
    "PAUSED": HealthReason.RISK_BREACH,
}


def health_verdict_for(recommendation: object, alerts: object) -> Optional[HealthVerdict]:
    """Map one monitor output to a HealthVerdict (727 C3), or None when the
    recommendation is not a health-machine write (OK / RETIRED / unknown).

    The reason code comes from the highest-priority alert that produced the
    recommendation, so §11.3's 根因消除 starts from a machine-readable cause
    instead of an audit-chain archaeology dig."""
    if not isinstance(recommendation, str):
        return None
    state = _RECOMMENDATION_TO_HEALTH.get(recommendation)
    if state is None:
        return None
    reason = _FALLBACK_REASON[recommendation]
    if isinstance(alerts, list):
        matching = [a for a in alerts
                    if isinstance(a, dict) and a.get("severity") == recommendation]
        if matching:
            # alerts are appended in fixed check order; the first match is the
            # earliest-fired (most fundamental) cause, deterministic.
            metric = matching[0].get("metric")
            reason = _ALERT_METRIC_TO_REASON.get(metric, reason)
    return HealthVerdict(state, reason)


@dataclass(frozen=True)
class DegradationThresholds:
    min_ic_retention: float = 0.30      # live IC / backtest IC floor
    max_return_gap: float = 0.60        # (backtest - live) / |backtest| ceiling (DEGRADED)
    retire_return_gap: float = 1.00     # return gap that means the edge is gone (RETIRED)
    max_slippage_mult: float = 2.0      # live slippage / backtest slippage
    max_turnover_mult: float = 2.0
    max_drawdown: float = 0.30
    max_capacity_breach_frac: float = 0.50


def monitor_alpha_health(backtest: Mapping[str, float], live: Mapping[str, float], *,
                         thresholds: Optional[DegradationThresholds] = None) -> dict:
    """Compare realized `live` metrics to the `backtest` expectation and recommend a lifecycle action.
    Recognised keys: ic, return, slippage_bps, turnover (both); drawdown, capacity_breach_frac (live)."""
    th = thresholds or DegradationThresholds()
    alerts: list = []

    def alert(metric, severity, detail):
        alerts.append({"metric": metric, "severity": severity, "detail": detail})

    bt_ic, lv_ic = backtest.get("ic"), live.get("ic")
    bt_ret, lv_ret = backtest.get("return"), live.get("return")

    # --- edge gone (RETIRED) ---
    if bt_ic is not None and lv_ic is not None and bt_ic > 0 and lv_ic <= 0:
        alert("ic_sign", "RETIRED", f"live IC {lv_ic:.4f} <= 0 vs backtest {bt_ic:.4f}")
    if bt_ret is not None and lv_ret is not None and abs(bt_ret) > 1e-12:
        gap = (bt_ret - lv_ret) / abs(bt_ret)
        if gap >= th.retire_return_gap:
            alert("return", "RETIRED", f"return gap {gap:.2f} >= {th.retire_return_gap}")
        elif gap >= th.max_return_gap:
            alert("return", "DEGRADED", f"return gap {gap:.2f} >= {th.max_return_gap}")

    # --- execution / risk broke (PAUSED) ---
    bt_slip, lv_slip = backtest.get("slippage_bps"), live.get("slippage_bps")
    if bt_slip is not None and lv_slip is not None and bt_slip > 1e-12:
        mult = lv_slip / bt_slip
        if mult >= th.max_slippage_mult:
            alert("slippage", "PAUSED", f"slippage {mult:.1f}x backtest")
    dd = live.get("drawdown")
    if dd is not None and dd >= th.max_drawdown:
        alert("drawdown", "PAUSED", f"drawdown {dd:.2f} >= {th.max_drawdown}")
    cap = live.get("capacity_breach_frac")
    if cap is not None and cap >= th.max_capacity_breach_frac:
        alert("capacity", "PAUSED", f"capacity breach frac {cap:.2f} >= {th.max_capacity_breach_frac}")

    # --- predictive power eroded (DEGRADED) ---
    if bt_ic is not None and lv_ic is not None and bt_ic > 1e-12:
        retention = lv_ic / bt_ic
        if retention < th.min_ic_retention:
            alert("ic_retention", "DEGRADED", f"IC retention {retention:.2f} < {th.min_ic_retention}")
    bt_to, lv_to = backtest.get("turnover"), live.get("turnover")
    if bt_to is not None and lv_to is not None and bt_to > 1e-12:
        mult = lv_to / bt_to
        if mult >= th.max_turnover_mult:
            alert("turnover", "DEGRADED", f"turnover {mult:.1f}x backtest")

    # --- fail-closed: flag missing core inputs (never fabricate a comparison) ---
    missing = [k for k in ("ic", "return") if live.get(k) is None]
    if missing:
        alert("data", "DEGRADED", f"missing live metric(s): {missing}")

    recommendation = max((a["severity"] for a in alerts), key=lambda s: _RANK[s], default="OK")
    return {
        "recommendation": recommendation,
        "recommended_state": _STATE[recommendation],
        "healthy": recommendation == "OK",
        "n_alerts": len(alerts),
        "alerts": alerts,
    }
