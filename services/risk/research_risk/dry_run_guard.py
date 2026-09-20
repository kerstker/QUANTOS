"""Fail-closed research-risk gate for a dry-run book.

The complete ``RiskApprovalGate`` runs nine engines and requires per-leg
returns, capacity/ADV, PIT regime labels, and immutable alpha metadata. Legacy
dry-run callers do not always have that evidence. They may use the four-engine
subset for diagnostics, but its ``REAL_RESEARCH_RISK_SUBSET`` provenance is
never an admission decision.

When ``require_full_inputs=True`` (the paper-order path), missing or malformed
evidence produces an explicit ``PENDING``/``RISK_REJECTED`` result and zero
fills. Only a real nine-engine ``REAL_RESEARCH_RISK_FULL`` result can satisfy
the paper execution contract, and that label additionally requires the stress
leg to have replayed real history on the aligned PIT timestamp axis; this module
never fabricates missing inputs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional

from .domain.gate import RiskApprovalGate
from .domain.models import RiskConfig, RiskRun
from .domain.risk_modules import (ConcentrationRiskEngine, DrawdownRiskEngine,
                                   MarketRiskEngine, StressTestEngine)
from services.validation.research_risk_authority import (
    FULL_RESEARCH_RISK_ENGINES,
    full_research_risk_approved,
)

# engines the dry-run seam CANNOT honestly feed — skipped, never run on fabricated inputs
_SKIPPED = ("liquidity", "correlation", "regime", "capacity", "model_alpha")


@dataclass(frozen=True)
class DryRunResearchRiskInputs:
    """Real inputs required to run the five engines absent at the legacy seam.

    The dry-run caller must provide these values from the same PIT snapshot as the
    returns/weights.  This value object intentionally carries no defaults: missing
    values are a pending risk decision, never synthetic data or an implicit pass.
    ``capacity_cny`` is keyed by the same alpha/instrument identifiers as
    ``weights``; ``alpha_returns`` and ``regime_labels`` are period-aligned with
    ``returns``.  ``timestamps`` is the bundle's declared PIT axis for those same
    periods and must equal the axis handed to the engines, otherwise the stress
    leg cannot be presented as a real historical replay.
    """

    capacity_cny: Mapping[str, float]
    daily_volume_cny: float
    total_aum: float
    alpha_returns: Mapping[str, Sequence[float]]
    regime_labels: Sequence[str]
    alpha_metadata: Sequence[Mapping[str, Any]]
    timestamps: Sequence[int] | None = None


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _stress_axis_reason(axis, expected_len: int) -> Optional[str]:
    """Return why ``axis`` cannot drive a real historical stress replay, else None.

    A replay-capable axis is a finite, period-aligned, strictly increasing time
    axis; anything else would silently downgrade the stress leg to synthetic data.
    """
    if not isinstance(axis, Sequence) or isinstance(axis, (str, bytes)):
        return "stress_timestamps_invalid"
    if len(axis) != expected_len:
        return "stress_timestamps_length"
    if any(not _finite_number(value) for value in axis):
        return "stress_timestamps_non_finite"
    if any(right <= left for left, right in zip(axis, axis[1:])):
        return "stress_timestamps_not_strictly_increasing"
    return None


def _validate_full_inputs(
    inputs: DryRunResearchRiskInputs,
    *,
    weights: Mapping[str, float],
    returns: Sequence[float],
    equity_curve: Sequence[float],
    timestamps: Optional[Sequence[int]],
) -> tuple[str, ...]:
    """Validate completeness/alignment before calling the full gate.

    This is deliberately stricter than the individual engines, whose historical
    APIs accept defaults for backwards compatibility.  The dry-run chokepoint has
    to distinguish a real missing datum from an omitted optional argument.
    """
    if not isinstance(inputs, DryRunResearchRiskInputs):
        return ("full_inputs_invalid_type",)

    reasons: list[str] = []
    if not isinstance(weights, Mapping) or not weights:
        reasons.append("weights_missing")
    elif any(not _finite_number(value) for value in weights.values()):
        reasons.append("weights_non_finite")

    if not isinstance(returns, Sequence) or isinstance(returns, (str, bytes)):
        reasons.append("returns_missing")
        expected_len = 0
    else:
        expected_len = len(returns)
        if not returns:
            reasons.append("returns_empty")
        elif any(not _finite_number(value) for value in returns):
            reasons.append("returns_non_finite")

    if not isinstance(equity_curve, Sequence) or isinstance(equity_curve, (str, bytes)):
        reasons.append("equity_curve_missing")
    else:
        if len(equity_curve) != expected_len:
            reasons.append("equity_curve_length")
        if not equity_curve:
            reasons.append("equity_curve_empty")
        elif any(not _finite_number(value) for value in equity_curve):
            reasons.append("equity_curve_non_finite")

    if timestamps is not None:
        if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes)):
            reasons.append("timestamps_invalid")
        elif len(timestamps) != expected_len:
            reasons.append("timestamps_length")
        elif any(not _finite_number(value) for value in timestamps):
            reasons.append("timestamps_non_finite")

    # Stress replay alignment: a full nine-engine claim rests on the stress engine
    # having replayed REAL history, which needs both the bundle's declared PIT axis
    # and the axis actually handed to the engines, describing the same periods.
    declared = inputs.timestamps
    if timestamps is None or declared is None:
        reasons.append("stress_timestamps_missing")
    else:
        defect = _stress_axis_reason(
            declared, expected_len
        ) or _stress_axis_reason(timestamps, expected_len)
        if defect is not None:
            reasons.append(defect)
        elif list(declared) != list(timestamps):
            reasons.append("stress_timestamps_not_aligned")

    if not isinstance(inputs.capacity_cny, Mapping):
        reasons.append("capacity_cny_missing")
    else:
        missing = sorted(str(k) for k in weights if k not in inputs.capacity_cny)
        if missing:
            reasons.append("capacity_cny_missing:" + ",".join(missing))
        invalid = sorted(
            str(k) for k, value in inputs.capacity_cny.items()
            if not _finite_number(value) or float(value) <= 0.0
        )
        if invalid:
            reasons.append("capacity_cny_invalid:" + ",".join(invalid))

    if not _finite_number(inputs.daily_volume_cny) or float(inputs.daily_volume_cny) <= 0.0:
        reasons.append("daily_volume_cny_invalid")
    if not _finite_number(inputs.total_aum) or float(inputs.total_aum) <= 0.0:
        reasons.append("total_aum_invalid")

    if not isinstance(inputs.alpha_returns, Mapping) or len(inputs.alpha_returns) < 2:
        reasons.append("alpha_returns_need_at_least_two")
    else:
        bad = []
        for alpha_id, series in inputs.alpha_returns.items():
            if not isinstance(series, Sequence) or isinstance(series, (str, bytes)) or not series:
                bad.append(f"{alpha_id}:empty")
                continue
            if len(series) != expected_len:
                bad.append(f"{alpha_id}:length")
                continue
            if any(not _finite_number(value) for value in series):
                bad.append(f"{alpha_id}:non_finite")
        if bad:
            reasons.append("alpha_returns_invalid:" + ",".join(sorted(bad)))

    if not isinstance(inputs.regime_labels, Sequence) or isinstance(inputs.regime_labels, (str, bytes)):
        reasons.append("regime_labels_missing")
    elif len(inputs.regime_labels) != expected_len:
        reasons.append("regime_labels_length")
    elif any(not isinstance(label, str) or not label.strip() for label in inputs.regime_labels):
        reasons.append("regime_labels_invalid")

    if not isinstance(inputs.alpha_metadata, Sequence) or isinstance(inputs.alpha_metadata, (str, bytes)):
        reasons.append("alpha_metadata_missing")
    elif not inputs.alpha_metadata:
        reasons.append("alpha_metadata_empty")
    else:
        required = ("age_days", "validation_confidence", "paper_confidence",
                    "decay_flag", "overfit_flag", "leakage_flag")
        bad_meta = []
        for index, item in enumerate(inputs.alpha_metadata):
            if not isinstance(item, Mapping):
                bad_meta.append(f"{index}:not_mapping")
                continue
            missing = [key for key in required if key not in item]
            if missing:
                bad_meta.append(f"{index}:missing_{','.join(missing)}")
                continue
            if any(not _finite_number(item[key]) for key in ("age_days", "validation_confidence", "paper_confidence")):
                bad_meta.append(f"{index}:non_finite")
        if bad_meta:
            reasons.append("alpha_metadata_invalid:" + ",".join(bad_meta))
    return tuple(reasons)


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
        out["worst_scenario"] = str(getattr(report, "worst_scenario", ""))
        out["worst_stress_loss"] = round(float(getattr(report, "worst_stress_loss", 0.0)), 6)
        out["scenarios_survived"] = int(report.scenarios_survived)
        out["total_scenarios"] = int(report.total_scenarios)
        out["scenarios_skipped"] = int(getattr(report, "scenarios_skipped", 0))
        out["scenario_losses"] = {
            str(key): round(float(value), 6)
            for key, value in dict(getattr(report, "scenario_losses", {})).items()
        }
    elif name == "liquidity":
        out.update({
            "avg_daily_volume_usage": round(float(report.avg_daily_volume_usage), 6),
            "participation_rate": round(float(report.participation_rate), 6),
            "liquidation_days": int(report.liquidation_days),
            "cost_to_liquidate_bps": round(float(report.cost_to_liquidate_bps), 6),
        })
    elif name == "correlation":
        out.update({
            "avg_correlation": round(float(report.avg_correlation), 6),
            "max_pairwise": round(float(report.max_pairwise), 6),
            "diversification_loss": round(float(report.diversification_loss), 6),
        })
    elif name == "regime":
        out.update({
            "worst_regime": str(report.worst_regime),
            "worst_regime_loss": round(float(report.worst_regime_loss), 6),
            "total_regimes": int(report.total_regimes),
        })
    elif name == "capacity":
        out.update({
            "capacity_usage": round(float(report.capacity_usage), 6),
            "bottleneck_alpha": str(report.bottleneck_alpha),
            "capacity_adjusted_sharpe": round(float(report.capacity_adjusted_sharpe), 6),
        })
    elif name == "model_alpha":
        out.update({
            "avg_alpha_age_days": round(float(report.avg_alpha_age_days), 6),
            "alphas_with_decay": int(report.alphas_with_decay),
            "alphas_with_overfitting_flags": int(report.alphas_with_overfitting_flags),
            "alphas_with_leakage_flags": int(report.alphas_with_leakage_flags),
            "min_validation_confidence": round(float(report.min_validation_confidence), 6),
            "min_paper_confidence": round(float(report.min_paper_confidence), 6),
        })
    return out


def evaluate_dry_run_risk(returns: List[float], weights: Dict[str, float], equity_curve: List[float],
                          *, timestamps: Optional[List[int]] = None,
                          config: Optional[RiskConfig] = None,
                          full_inputs: Optional[DryRunResearchRiskInputs] = None,
                          require_full_inputs: bool = False) -> dict:
    """Run the four honestly-feedable research-risk engines fail-closed over a dry-run book's
    realized series. Returns a decision dict (never raises for a normal risk failure — a failed
    engine is a REJECT, an engine ERROR is fail-closed to not-passed)."""
    config = config or RiskConfig()

    # The opt-in strict path is the only path that may claim all nine engines ran.
    # A missing bundle is explicitly pending/rejected; it can never be represented
    # as a four-engine approval when the caller requested the full contract.
    if require_full_inputs:
        if full_inputs is None:
            pending = {engine: "MISSING_REQUIRED_INPUTS" for engine in _SKIPPED}
            return {
                "decision": "RISK_REJECTED",
                "status": "PENDING",
                "gates_passed": 0,
                "gates_run": 0,
                "engines_run": {},
                "engines_skipped": pending,
                "engines_pending": pending,
                "reports": {},
                "reasons": ["full_research_risk_inputs_missing"],
                "provenance": "REAL_RESEARCH_RISK_FULL_PENDING",
                "config_provenance": config.compute_hash(),
            }
        validation = _validate_full_inputs(
            full_inputs,
            weights=weights,
            returns=returns,
            equity_curve=equity_curve,
            timestamps=timestamps,
        )
        if validation:
            pending = {engine: "INVALID_REQUIRED_INPUTS" for engine in _SKIPPED}
            return {
                "decision": "RISK_REJECTED",
                "status": "PENDING",
                "gates_passed": 0,
                "gates_run": 0,
                "engines_run": {},
                "engines_skipped": pending,
                "engines_pending": pending,
                "reports": {},
                "reasons": list(validation),
                "provenance": "REAL_RESEARCH_RISK_FULL_PENDING",
                "config_provenance": config.compute_hash(),
            }
        run = RiskRun(portfolio_id="dry_run", config=config)
        decision = RiskApprovalGate(config).evaluate(
            run=run,
            returns=list(returns),
            weights=dict(weights),
            equity_curve=list(equity_curve),
            capacity_cny=dict(full_inputs.capacity_cny),
            alpha_returns={str(k): list(v) for k, v in full_inputs.alpha_returns.items()},
            regime_labels=list(full_inputs.regime_labels),
            alpha_metadata=[dict(item) for item in full_inputs.alpha_metadata],
            daily_volume_cny=float(full_inputs.daily_volume_cny),
            total_aum=float(full_inputs.total_aum),
            timestamps=timestamps,
        )
        # The nine-engine label is an ADMISSION claim, so the run's own stress report
        # decides it: a stress leg that fell back to the (honestly labeled) synthetic
        # multipliers is not real historical replay and cannot carry the full label,
        # however many engines passed.  Fail closed to the pending shape instead.
        stress_report = run.gate_reports.get("stress")
        if getattr(stress_report, "provenance", "") != "REAL_HISTORICAL_REPLAY":
            pending = {"stress": "STRESS_REPLAY_REQUIRED"}
            return {
                "decision": "RISK_REJECTED",
                "status": "PENDING",
                "gates_passed": 0,
                "gates_run": 0,
                "engines_run": {},
                "engines_skipped": pending,
                "engines_pending": pending,
                "reports": {},
                "reasons": ["stress_replay_required"],
                "provenance": "REAL_RESEARCH_RISK_FULL_PENDING",
                "config_provenance": config.compute_hash(),
            }
        reports = {
            name: _summary(name, report)
            for name, report in sorted(run.gate_reports.items())
            if report is not None
        }
        results = {str(name): bool(value) for name, value in sorted(run.gate_results.items())}
        reasons = [str(reason) for reason in decision.reasons]
        return {
            "decision": "RISK_APPROVED" if all(results.values()) else "RISK_REJECTED",
            "status": "APPROVED" if all(results.values()) else "REJECTED",
            "gates_passed": sum(1 for value in results.values() if value),
            "gates_run": len(results),
            "engines_run": results,
            "engines_skipped": {},
            "engines_pending": {},
            "reports": reports,
            "reasons": reasons,
            "provenance": "REAL_RESEARCH_RISK_FULL",
            "config_provenance": config.compute_hash(),
        }
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
        # ``decision`` remains the outcome of the four engines that actually
        # ran, so historical diagnostics stay interpretable.  It is not an
        # admission decision: five mandatory engines are still pending.
        "decision": "RISK_APPROVED" if approved else "RISK_REJECTED",
        "status": "DIAGNOSTIC_ONLY",
        "gates_passed": sum(1 for v in results.values() if v),
        "gates_run": len(results),
        "engines_run": results,
        "engines_skipped": {e: "NO_HONEST_INPUT_AT_DRY_RUN_SEAM" for e in _SKIPPED},
        "engines_pending": {e: "NO_HONEST_INPUT_AT_DRY_RUN_SEAM" for e in _SKIPPED},
        "reports": reports,
        "reasons": reasons,
        "provenance": "REAL_RESEARCH_RISK_SUBSET",
        "config_provenance": config.compute_hash(),
    }


__all__ = [
    "DryRunResearchRiskInputs",
    "FULL_RESEARCH_RISK_ENGINES",
    "evaluate_dry_run_risk",
    "full_research_risk_approved",
]
