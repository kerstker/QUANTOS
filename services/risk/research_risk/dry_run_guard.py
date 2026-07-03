"""Rank #13 — fail-closed research-risk gate for a dry-run book, honestly graded.

The full `RiskApprovalGate` runs 9 engines and needs inputs (per-leg alpha_returns, capacity,
daily volume, regime labels, alpha metadata) the dry-run seam does NOT honestly have. Feeding
those engines empty/fabricated inputs would either fail-closed (rejecting every book) or — worse —
manufacture a risk clearance from invented data, which is exactly what this platform must never do.

So this gate runs ONLY the four engines whose inputs the dry-run seam genuinely provides
(market / drawdown / concentration / stress), each fail-closed, and marks the other five
`NO_HONEST_INPUT_AT_DRY_RUN_SEAM` — SKIPPED, never faked. A `RISK_APPROVED` here means "passed the
four honestly-fed engines", with the five skipped engines named in the decision (provenance
`REAL_RESEARCH_RISK_SUBSET`). Wiring the five to real inputs (per-leg attribution, capacity/ADV,
PIT regime labels, the alpha's AlphaEvaluation) is a later increment; until then they stay honest-SKIPPED.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .domain.models import RiskConfig
from .domain.risk_modules import (ConcentrationRiskEngine, DrawdownRiskEngine,
                                   MarketRiskEngine, StressTestEngine)

# engines the dry-run seam CANNOT honestly feed — skipped, never run on fabricated inputs
_SKIPPED = ("liquidity", "correlation", "regime", "capacity", "model_alpha")


def _summary(name: str, report) -> dict:
    out = {"passed": bool(report.passed)}
    if name == "drawdown":
        out["max_drawdown"] = round(float(report.max_drawdown), 6)
    elif name == "market":
        out["volatility"] = round(float(report.volatility), 6)
        out["cvar_95"] = round(float(report.cvar_95), 6)
    elif name == "concentration":
        out["hhi"] = round(float(report.hhi), 6)
    elif name == "stress":
        out["provenance"] = getattr(report, "provenance", "")
        out["scenarios_survived"] = int(report.scenarios_survived)
        out["total_scenarios"] = int(report.total_scenarios)
    return out


def evaluate_dry_run_risk(returns: List[float], weights: Dict[str, float], equity_curve: List[float],
                          *, timestamps: Optional[List[int]] = None,
                          config: Optional[RiskConfig] = None) -> dict:
    """Run the four honestly-feedable research-risk engines fail-closed over a dry-run book's
    realized series. Returns a decision dict (never raises for a normal risk failure — a failed
    engine is a REJECT, an engine ERROR is fail-closed to not-passed)."""
    config = config or RiskConfig()
    engines = {
        "market": lambda: MarketRiskEngine().evaluate(returns, config),
        "drawdown": lambda: DrawdownRiskEngine().evaluate(equity_curve, config),
        "concentration": lambda: ConcentrationRiskEngine().evaluate(weights, None, config),
        "stress": lambda: StressTestEngine().evaluate(returns, config, timestamps),
    }
    results: Dict[str, bool] = {}
    reports: Dict[str, dict] = {}
    reasons: List[str] = []
    for name, thunk in engines.items():
        try:
            report = thunk()
            passed = bool(report.passed)
            reports[name] = _summary(name, report)
            if not passed:
                reasons.append(f"{name}: not passed")
        except Exception as exc:                       # fail-closed: an engine error is NOT a pass
            passed, reports[name] = False, {"passed": False, "error": type(exc).__name__}
            reasons.append(f"{name}: engine error ({type(exc).__name__})")
        results[name] = passed
    approved = all(results.values())
    return {
        "decision": "RISK_APPROVED" if approved else "RISK_REJECTED",
        "gates_passed": sum(1 for v in results.values() if v),
        "gates_run": len(results),
        "engines_run": results,
        "engines_skipped": {e: "NO_HONEST_INPUT_AT_DRY_RUN_SEAM" for e in _SKIPPED},
        "reports": reports,
        "reasons": reasons,
        "provenance": "REAL_RESEARCH_RISK_SUBSET",
    }
