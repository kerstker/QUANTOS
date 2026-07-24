"""Strict, deterministic primitives for the crypto robust-optimisation study.

This module deliberately contains no file, clock, broker, or network access.  It
is the common contract shared by the CUDA screening driver and the CPU paper
replay.  The CUDA driver is allowed to approximate the execution cost surface,
but it must use these same definitions for hashes, metrics, and hard gates.

The alpha formulas are intentionally absent here.  A caller supplies three
already computed, PIT-aligned return sleeves and an observed turnover series.
Missing evidence is represented as a rejected gate, never as a synthetic zero.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


ALPHA_IDS: tuple[str, str, str] = (
    "carry_oi_majors",
    "crowd_contra",
    "top_contra",
)
LEVERAGE_CAPS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
FEES_BPS: tuple[float, ...] = (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)
IMPACT_BPS: tuple[float, ...] = (5.0, 10.0, 20.0)
PARTICIPATION_RATES: tuple[float, ...] = (0.05, 0.10, 0.20)
EXECUTION_HORIZONS_DAYS: tuple[int, ...] = (1, 3, 5)
DRAWDOWN_THRESHOLDS: tuple[float, ...] = (0.08, 0.10, 0.12, 0.15, 0.20)
# Short spelling used by the grid and a backwards-compatible spelling from the
# first draft of this module.
DD_THRESHOLDS = DRAWDOWN_THRESHOLDS
DRAWdown_THRESHOLDS = DRAWDOWN_THRESHOLDS

PPY_WEEKLY = 52.0
MIN_DSR = 0.95
MAX_PBO = 0.25
MIN_OOS_VT_SHARPE = 0.8
MIN_PROFITABLE_FRACTION = 0.75
MAX_CANDIDATE_DD_THRESHOLD = 0.15
MAX_CPU_RESIDUAL = 1e-6
MIN_FORWARD_PERIODS = 8


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _round(value: float) -> float:
    return round(float(value), 12)


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        return _round(value)
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    return value


def _stable_hash(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateSpec:
    """A research candidate, independent of a stress scenario."""

    weights: tuple[float, float, float]
    leverage_cap: float
    dd_threshold: float
    cadence: str = "weekly"

    def __post_init__(self) -> None:
        weights = tuple(float(x) for x in self.weights)
        if len(weights) != len(ALPHA_IDS):
            raise ValueError("exactly three alpha weights are required")
        if any(not _finite(x) or x < 0.0 for x in weights):
            raise ValueError("alpha weights must be finite and non-negative")
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("alpha weights must sum to one")
        if not _finite(self.leverage_cap) or float(self.leverage_cap) <= 0.0:
            raise ValueError("leverage_cap must be positive and finite")
        if not _finite(self.dd_threshold) or float(self.dd_threshold) <= 0.0:
            raise ValueError("dd_threshold must be positive and finite")
        if self.cadence != "weekly":
            raise ValueError("the robust study is fixed to weekly cadence")
        object.__setattr__(self, "weights", tuple(_round(x) for x in weights))
        object.__setattr__(self, "leverage_cap", _round(float(self.leverage_cap)))
        object.__setattr__(self, "dd_threshold", _round(float(self.dd_threshold)))

    @property
    def alpha_weights(self) -> tuple[float, float, float]:
        return self.weights

    @property
    def config_hash(self) -> str:
        return candidate_config_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "alpha_ids": list(ALPHA_IDS),
            "weights": list(self.weights),
            "leverage_cap": self.leverage_cap,
            "dd_threshold": self.dd_threshold,
            "cadence": self.cadence,
        }


@dataclass(frozen=True)
class StressSpec:
    """One execution-cost pressure cell."""

    fee_bps: float
    impact_bps: float
    participation_rate: float
    execution_days: int

    def __post_init__(self) -> None:
        if not _finite(self.fee_bps) or float(self.fee_bps) < 0.0:
            raise ValueError("fee_bps must be finite and non-negative")
        if not _finite(self.impact_bps) or float(self.impact_bps) < 0.0:
            raise ValueError("impact_bps must be finite and non-negative")
        if not _finite(self.participation_rate) or not 0.0 < float(self.participation_rate) <= 1.0:
            raise ValueError("participation_rate must be in (0, 1]")
        if isinstance(self.execution_days, bool) or int(self.execution_days) <= 0:
            raise ValueError("execution_days must be positive")
        object.__setattr__(self, "fee_bps", _round(float(self.fee_bps)))
        object.__setattr__(self, "impact_bps", _round(float(self.impact_bps)))
        object.__setattr__(self, "participation_rate", _round(float(self.participation_rate)))
        object.__setattr__(self, "execution_days", int(self.execution_days))

    def as_dict(self) -> dict[str, Any]:
        return {
            "fee_bps": self.fee_bps,
            "impact_bps": self.impact_bps,
            "participation_rate": self.participation_rate,
            "execution_days": self.execution_days,
        }


@dataclass(frozen=True)
class MatrixMetrics:
    """Metrics emitted for one candidate/stress cell."""

    net_return: float
    vt_return: float
    annualized_sharpe: float
    vt_sharpe: float
    max_drawdown: float
    vt_max_drawdown: float
    calmar: float
    turnover: float
    fee_drag: float
    impact_drag: float
    worst_state: str = ""
    positive: bool = False
    n_periods: int = 0
    returns: tuple[float, ...] = field(default_factory=tuple, repr=False, compare=False)
    vt_returns: tuple[float, ...] = field(default_factory=tuple, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "net_return": _round(self.net_return),
            "vt_return": _round(self.vt_return),
            "annualized_sharpe": _round(self.annualized_sharpe),
            "vt_sharpe": _round(self.vt_sharpe),
            "max_drawdown": _round(self.max_drawdown),
            "vt_max_drawdown": _round(self.vt_max_drawdown),
            "calmar": _round(self.calmar),
            "turnover": _round(self.turnover),
            "fee_drag": _round(self.fee_drag),
            "impact_drag": _round(self.impact_drag),
            "worst_state": self.worst_state,
            "positive": bool(self.positive),
            "n_periods": int(self.n_periods),
        }


@dataclass(frozen=True)
class GateDecision:
    status: str
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "passed": self.passed, "reasons": list(self.reasons), "evidence": dict(self.evidence)}


@dataclass(frozen=True)
class StableRegion:
    region_id: str
    candidate_hashes: tuple[str, ...]
    selected_hash: str
    selected_candidate: CandidateSpec
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "candidate_hashes": list(self.candidate_hashes),
            "selected_hash": self.selected_hash,
            "selected_candidate": self.selected_candidate.as_dict(),
            "size": self.size,
        }


def generate_simplex_weights(*, step: float = 0.1) -> tuple[tuple[float, float, float], ...]:
    """Enumerate the closed three-alpha simplex with no duplicates."""
    if not _finite(step) or float(step) <= 0.0 or float(step) > 1.0:
        raise ValueError("step must be in (0, 1]")
    units = int(round(1.0 / float(step)))
    if abs(units * float(step) - 1.0) > 1e-9:
        raise ValueError("step must divide one exactly")
    result: list[tuple[float, float, float]] = []
    for i in range(units + 1):
        for j in range(units - i + 1):
            k = units - i - j
            result.append(tuple(_round(x / units) for x in (i, j, k)))
    return tuple(result)


def iter_stress_specs() -> tuple[StressSpec, ...]:
    return tuple(
        StressSpec(fee, impact, participation, days)
        for fee in FEES_BPS
        for impact in IMPACT_BPS
        for participation in PARTICIPATION_RATES
        for days in EXECUTION_HORIZONS_DAYS
    )


def iter_candidate_specs() -> tuple[CandidateSpec, ...]:
    return tuple(
        CandidateSpec(weights, leverage, dd)
        for weights in generate_simplex_weights()
        for leverage in LEVERAGE_CAPS
        for dd in DD_THRESHOLDS
    )


def candidate_config_hash(candidate: CandidateSpec | Mapping[str, Any], *, dataset_version: str | None = None) -> str:
    """Hash only model/candidate knobs, never a stress cell.

    ``dataset_version`` is optional for callers that need a dataset-bound
    identity in an external artifact.  The default hash remains global so the
    experiment registry cannot be reset by changing the data snapshot.
    """
    if isinstance(candidate, CandidateSpec):
        payload = candidate.as_dict()
    elif isinstance(candidate, Mapping):
        payload = dict(candidate)
    else:
        raise TypeError("candidate must be CandidateSpec or mapping")
    if dataset_version is not None:
        payload["dataset_version"] = str(dataset_version)
    return _stable_hash(payload)[:16]


def _clean_series(values: Sequence[float], name: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise ValueError(f"{name} is missing or empty")
    result = tuple(float(x) for x in values)
    if any(not math.isfinite(x) for x in result):
        raise ValueError(f"{name} contains non-finite values")
    return result


def vol_target_returns(
    returns: Sequence[float],
    *,
    target_ann: float = 0.10,
    lookback: int = 8,
    leverage_cap: float = 1.0,
    ppy: float = PPY_WEEKLY,
) -> tuple[float, ...]:
    """PIT vol-target implementation shared with the existing CPU pipeline."""
    values = _clean_series(returns, "returns")
    if not _finite(target_ann) or target_ann <= 0.0 or not _finite(ppy) or ppy <= 0.0:
        raise ValueError("target_ann and ppy must be positive")
    if isinstance(lookback, bool) or lookback <= 0 or not _finite(leverage_cap) or leverage_cap <= 0.0:
        raise ValueError("lookback and leverage_cap are invalid")
    target_per = float(target_ann) / math.sqrt(float(ppy))
    out: list[float] = []
    for i, current in enumerate(values):
        if i < int(lookback):
            out.append(current)
            continue
        window = values[i - int(lookback):i]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        sd = math.sqrt(variance) or 1e-9
        out.append(min(float(leverage_cap), target_per / sd) * current)
    return tuple(out)


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1.0 + float(value)
        peak = max(peak, equity)
        if peak > 0.0:
            drawdown = max(drawdown, (peak - equity) / peak)
    return max(0.0, float(drawdown))


def _annualized_sharpe(returns: Sequence[float], ppy: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(float(x) for x in returns) / len(returns)
    variance = sum((float(x) - mean) ** 2 for x in returns) / len(returns)
    sd = math.sqrt(variance)
    return mean / sd * math.sqrt(ppy) if sd > 0.0 else 0.0


def _annualized_return(returns: Sequence[float], ppy: float) -> float:
    if not returns:
        return 0.0
    growth = 1.0
    for value in returns:
        growth *= 1.0 + float(value)
    if growth <= 0.0:
        return -1.0
    return growth ** (float(ppy) / len(returns)) - 1.0


def portfolio_returns(
    alpha_returns: Mapping[str, Sequence[float]],
    weights: Sequence[float],
    *,
    leverage_cap: float = 1.0,
    fee_bps: float = 0.0,
    impact_bps: float = 0.0,
    participation_rate: float = 0.10,
    execution_days: int = 1,
    turnover: Sequence[float] | None = None,
) -> tuple[tuple[float, ...], float, float, float]:
    """Combine frozen alpha sleeves and apply observed execution costs.

    ``turnover`` is period-aligned and is intentionally required whenever a
    non-zero fee or impact is requested.  This prevents a caller from silently
    treating missing turnover as free execution.
    """
    if tuple(alpha_returns.keys()) != tuple(ALPHA_IDS):
        # Mapping order is not a data contract; accept any order but require all
        # three IDs and never silently substitute a missing sleeve.
        if set(alpha_returns) != set(ALPHA_IDS):
            raise ValueError("alpha_returns must contain exactly the three frozen alpha IDs")
    series = {alpha: _clean_series(alpha_returns[alpha], alpha) for alpha in ALPHA_IDS}
    n = len(series[ALPHA_IDS[0]])
    if any(len(series[alpha]) != n for alpha in ALPHA_IDS):
        raise ValueError("alpha return sleeves are not aligned")
    weight_tuple = tuple(float(x) for x in weights)
    if len(weight_tuple) != 3 or any(not _finite(x) or x < 0 for x in weight_tuple) or abs(sum(weight_tuple) - 1.0) > 1e-9:
        raise ValueError("weights must be a three-element simplex")
    if not _finite(leverage_cap) or leverage_cap <= 0.0:
        raise ValueError("leverage_cap must be positive")
    if not _finite(fee_bps) or fee_bps < 0.0 or not _finite(impact_bps) or impact_bps < 0.0:
        raise ValueError("costs must be finite and non-negative")
    if not _finite(participation_rate) or not 0.0 < participation_rate <= 1.0 or int(execution_days) <= 0:
        raise ValueError("execution terms are invalid")
    if turnover is None:
        if fee_bps or impact_bps:
            raise ValueError("turnover is required for non-zero execution costs")
        turnover_values = (0.0,) * n
    else:
        turnover_values = _clean_series(turnover, "turnover")
        if len(turnover_values) != n or any(x < 0.0 for x in turnover_values):
            raise ValueError("turnover is not aligned or contains negative values")
    # Longer execution lowers temporary impact; higher participation raises it.
    impact_scale = math.sqrt(float(participation_rate) / 0.10) / math.sqrt(int(execution_days))
    fee_rate = float(fee_bps) / 10_000.0
    impact_rate = float(impact_bps) / 10_000.0 * impact_scale
    raw: list[float] = []
    net: list[float] = []
    fee_drag = 0.0
    impact_drag = 0.0
    for i in range(n):
        sleeve = sum(weight_tuple[j] * series[ALPHA_IDS[j]][i] for j in range(3))
        leveraged = float(leverage_cap) * sleeve
        fee = turnover_values[i] * fee_rate
        impact = turnover_values[i] * impact_rate
        raw.append(leveraged)
        net.append(leveraged - fee - impact)
        fee_drag += fee
        impact_drag += impact
    return tuple(net), sum(raw) / n if raw else 0.0, fee_drag, impact_drag


def compute_metrics(
    returns: Sequence[float],
    *,
    ppy: float = PPY_WEEKLY,
    leverage_cap: float = 1.0,
    turnover: float = 0.0,
    fee_drag: float = 0.0,
    impact_drag: float = 0.0,
    worst_state: str = "",
    vt_returns: Sequence[float] | None = None,
) -> MatrixMetrics:
    values = _clean_series(returns, "returns")
    vt_values = tuple(values if vt_returns is None else _clean_series(vt_returns, "vt_returns"))
    if len(vt_values) != len(values):
        raise ValueError("vt_returns are not aligned")
    growth = 1.0
    vt_growth = 1.0
    for value in values:
        growth *= 1.0 + value
    for value in vt_values:
        vt_growth *= 1.0 + value
    dd = _max_drawdown(values)
    vt_dd = _max_drawdown(vt_values)
    annual = _annualized_return(values, ppy)
    # A zero-drawdown series has no finite Calmar denominator; represent it as
    # zero rather than serialising JSON ``Infinity`` into an audit artifact.
    calmar = annual / vt_dd if vt_dd > 0.0 else 0.0
    return MatrixMetrics(
        net_return=growth - 1.0,
        vt_return=vt_growth - 1.0,
        annualized_sharpe=_annualized_sharpe(values, ppy),
        vt_sharpe=_annualized_sharpe(vt_values, ppy),
        max_drawdown=dd,
        vt_max_drawdown=vt_dd,
        calmar=calmar,
        turnover=float(turnover),
        fee_drag=float(fee_drag),
        impact_drag=float(impact_drag),
        worst_state=str(worst_state),
        positive=(vt_growth - 1.0) > 0.0,
        n_periods=len(values),
        returns=values,
        vt_returns=vt_values,
    )


def screen_reference(
    alpha_returns: Mapping[str, Sequence[float]],
    candidate: CandidateSpec,
    stress: StressSpec,
    *,
    turnover: Sequence[float],
    ppy: float = PPY_WEEKLY,
    target_ann: float = 0.10,
    lookback: int = 8,
) -> MatrixMetrics:
    """CPU reference implementation used by both tests and the CUDA driver."""
    net, _raw_mean, fee_drag, impact_drag = portfolio_returns(
        alpha_returns,
        candidate.weights,
        leverage_cap=candidate.leverage_cap,
        fee_bps=stress.fee_bps,
        impact_bps=stress.impact_bps,
        participation_rate=stress.participation_rate,
        execution_days=stress.execution_days,
        turnover=turnover,
    )
    vt = vol_target_returns(
        net,
        target_ann=target_ann,
        lookback=lookback,
        leverage_cap=candidate.leverage_cap,
        ppy=ppy,
    )
    return compute_metrics(
        net,
        ppy=ppy,
        leverage_cap=candidate.leverage_cap,
        turnover=sum(float(x) for x in turnover),
        fee_drag=fee_drag,
        impact_drag=impact_drag,
        vt_returns=vt,
    )


def aggregate_stress(metrics: Iterable[MatrixMetrics]) -> dict[str, Any]:
    rows = tuple(metrics)
    if not rows:
        return {"status": "BLOCK", "reasons": ["stress_cells_missing"], "cells": 0}
    worst = min(rows, key=lambda row: (row.vt_sharpe, row.vt_max_drawdown, row.vt_return))
    positive = sum(1 for row in rows if row.vt_return > 0.0)
    return {
        "status": "AVAILABLE",
        "cells": len(rows),
        "worst_vt_sharpe": float(worst.vt_sharpe),
        "worst_vt_max_drawdown": float(worst.vt_max_drawdown),
        "worst_state": worst.worst_state,
        "positive_fraction": positive / len(rows),
        "median_vt_return": sorted(row.vt_return for row in rows)[len(rows) // 2],
        "median_turnover": sorted(row.turnover for row in rows)[len(rows) // 2],
        "fee_drag": sum(row.fee_drag for row in rows) / len(rows),
        "impact_drag": sum(row.impact_drag for row in rows) / len(rows),
    }


def strict_backtest_gate(
    *,
    candidate: CandidateSpec,
    data_ok: bool,
    dsr: float | None,
    pbo: float | None,
    oos_vt_sharpe: float | None,
    walk_forward_profitable_fraction: float | None,
    market_state_profitable_fraction: float | None,
    stress: Mapping[str, Any] | None,
    full_research_risk: bool,
    cpu: Mapping[str, Any] | None = None,
    forward: Mapping[str, Any] | None = None,
    require_forward: bool = False,
) -> GateDecision:
    """Apply the non-negotiable research, cost, CPU, and forward hard gates."""
    reasons: list[str] = []
    evidence: dict[str, Any] = {}
    if not isinstance(candidate, CandidateSpec):
        reasons.append("candidate_invalid")
    if not data_ok:
        reasons.append("data_quality_or_pit_gate_failed")
    if candidate.dd_threshold > MAX_CANDIDATE_DD_THRESHOLD:
        reasons.append("drawdown_threshold_above_15_percent")
    numeric = (
        ("dsr", dsr, lambda x: x >= MIN_DSR),
        ("pbo", pbo, lambda x: x <= MAX_PBO),
        ("oos_vt_sharpe", oos_vt_sharpe, lambda x: x >= MIN_OOS_VT_SHARPE),
        ("walk_forward_profitable_fraction", walk_forward_profitable_fraction, lambda x: x >= MIN_PROFITABLE_FRACTION),
        ("market_state_profitable_fraction", market_state_profitable_fraction, lambda x: x >= MIN_PROFITABLE_FRACTION),
    )
    for name, value, predicate in numeric:
        if value is None or not _finite(value):
            reasons.append(f"{name}_missing")
        elif not predicate(float(value)):
            reasons.append(f"{name}_below_hard_floor")
        else:
            evidence[name] = float(value)
    if not full_research_risk:
        reasons.append("full_nine_engine_research_risk_missing")
    if not isinstance(stress, Mapping) or stress.get("cells", 0) <= 0:
        reasons.append("stress_evidence_missing")
    else:
        for name, predicate in (
            ("worst_vt_sharpe", lambda x: x >= MIN_OOS_VT_SHARPE),
            ("worst_vt_max_drawdown", lambda x: x <= candidate.dd_threshold),
            ("positive_fraction", lambda x: x >= MIN_PROFITABLE_FRACTION),
        ):
            value = stress.get(name)
            if value is None or not _finite(value):
                reasons.append(f"stress_{name}_missing")
            elif not predicate(float(value)):
                reasons.append(f"stress_{name}_below_hard_floor")
            else:
                evidence[f"stress_{name}"] = float(value)
    if cpu is not None:
        residual = cpu.get("residual")
        if residual is None or not _finite(residual) or abs(float(residual)) > MAX_CPU_RESIDUAL:
            reasons.append("cpu_reconciliation_residual")
        if cpu.get("unexplained_rejections", 0):
            reasons.append("cpu_unexplained_rejections")
        if cpu.get("kill_switch_anomaly", False):
            reasons.append("cpu_kill_switch_anomaly")
        if not cpu.get("complete", False):
            reasons.append("cpu_exact_replay_incomplete")
    elif require_forward:
        reasons.append("cpu_evidence_missing")
    if require_forward:
        if not isinstance(forward, Mapping):
            reasons.append("forward_evidence_missing")
        else:
            periods = forward.get("valid_periods")
            try:
                period_count = int(periods) if periods is not None else 0
            except (TypeError, ValueError):
                period_count = 0
            if period_count < MIN_FORWARD_PERIODS:
                reasons.append("forward_less_than_8_periods")
            if forward.get("data_fresh", False) is not True:
                reasons.append("forward_data_not_fresh")
            if forward.get("positive_net", False) is not True:
                reasons.append("forward_net_not_positive")
            if forward.get("ledger_consistent", False) is not True:
                reasons.append("forward_ledger_inconsistent")
            forward_dd = forward.get("max_drawdown")
            if not _finite(forward_dd) or float(forward_dd) > candidate.dd_threshold:
                reasons.append("forward_drawdown_above_candidate_threshold")
    if reasons:
        return GateDecision("BLOCK", tuple(sorted(set(reasons))), evidence)
    return GateDecision("PASS", (), evidence)


def _candidate_from_record(record: Mapping[str, Any]) -> CandidateSpec:
    value = record.get("candidate")
    if isinstance(value, CandidateSpec):
        return value
    if isinstance(value, Mapping):
        return CandidateSpec(
            tuple(value["weights"]),
            float(value["leverage_cap"]),
            float(value["dd_threshold"]),
            str(value.get("cadence", "weekly")),
        )
    raise ValueError("record has no candidate")


def _neighbor_hashes(candidate: CandidateSpec) -> set[str]:
    result: set[str] = set()
    weights = candidate.weights
    # A simplex neighbor transfers one 10%-unit from one sleeve to another.
    for source in range(3):
        for target in range(3):
            if source == target or weights[source] < 0.1 - 1e-9:
                continue
            nxt = list(weights)
            nxt[source] = _round(nxt[source] - 0.1)
            nxt[target] = _round(nxt[target] + 0.1)
            result.add(candidate_config_hash(CandidateSpec(tuple(nxt), candidate.leverage_cap, candidate.dd_threshold)))
    lev = list(LEVERAGE_CAPS)
    try:
        index = lev.index(candidate.leverage_cap)
    except ValueError:
        index = -1
    if index >= 0:
        for new in (lev[index - 1] if index > 0 else None, lev[index + 1] if index + 1 < len(lev) else None):
            if new is not None:
                result.add(candidate_config_hash(CandidateSpec(weights, new, candidate.dd_threshold)))
    dd = list(DD_THRESHOLDS)
    try:
        index = dd.index(candidate.dd_threshold)
    except ValueError:
        index = -1
    if index >= 0:
        for new in (dd[index - 1] if index > 0 else None, dd[index + 1] if index + 1 < len(dd) else None):
            if new is not None:
                result.add(candidate_config_hash(CandidateSpec(weights, candidate.leverage_cap, new)))
    return result


def _ranking_key(record: Mapping[str, Any]) -> tuple[float, ...]:
    stress = record.get("stress") or {}
    return (
        -float(stress.get("worst_vt_sharpe", -math.inf)),
        float(stress.get("worst_vt_max_drawdown", math.inf)),
        float(stress.get("fee_drag", math.inf)) + float(stress.get("impact_drag", math.inf)),
        -float(stress.get("median_vt_return", -math.inf)),
        float(stress.get("median_turnover", math.inf)),
        str(record.get("candidate_hash", "")),
    )


def select_stable_regions(records: Sequence[Mapping[str, Any]]) -> tuple[StableRegion, ...]:
    """Return connected PASS regions with at least one PASS neighbor.

    A single historical peak is deliberately not a region.  The returned
    regions are sorted by the fixed lexicographic robustness ranking.
    """
    by_hash: dict[str, Mapping[str, Any]] = {}
    for record in records:
        candidate = _candidate_from_record(record)
        digest = str(record.get("candidate_hash") or candidate_config_hash(candidate))
        status = str((record.get("gate") or {}).get("status", record.get("status", "BLOCK")))
        if status == "PASS":
            by_hash[digest] = {**dict(record), "candidate_hash": digest, "candidate": candidate}
    if not by_hash:
        return ()
    stable = {digest for digest, record in by_hash.items() if _neighbor_hashes(record["candidate"]) & set(by_hash)}
    if not stable:
        return ()
    # Connected components over the same neighbor graph.
    regions: list[StableRegion] = []
    remaining = set(stable)
    while remaining:
        seed = min(remaining)
        component = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            candidate = by_hash[current]["candidate"]
            adjacent = _neighbor_hashes(candidate) & remaining
            for nxt in adjacent:
                if nxt not in component:
                    component.add(nxt)
                    frontier.append(nxt)
        remaining -= component
        ordered = tuple(sorted(component))
        selected = min((by_hash[d] for d in component), key=_ranking_key)
        chosen = selected["candidate"]
        region_id = _stable_hash({"members": ordered})[:16]
        regions.append(StableRegion(region_id, ordered, candidate_config_hash(chosen), chosen, len(ordered)))
    return tuple(sorted(regions, key=lambda region: _ranking_key(by_hash[region.selected_hash])))


# Explicit names used by the operator/report layer and by downstream research
# notebooks.  They are aliases, not alternate implementations, so CPU/GPU and
# test code cannot drift in their definitions.
generate_candidate_grid = iter_candidate_specs
generate_stress_grid = iter_stress_specs
apply_vol_target = vol_target_returns
strict_gate = strict_backtest_gate


__all__ = [
    "ALPHA_IDS", "LEVERAGE_CAPS", "FEES_BPS", "IMPACT_BPS", "PARTICIPATION_RATES",
    "EXECUTION_HORIZONS_DAYS", "DRAWDOWN_THRESHOLDS", "DD_THRESHOLDS", "PPY_WEEKLY", "CandidateSpec", "StressSpec",
    "MatrixMetrics", "GateDecision", "StableRegion", "generate_simplex_weights", "iter_stress_specs",
    "iter_candidate_specs", "candidate_config_hash", "vol_target_returns", "portfolio_returns",
    "compute_metrics", "screen_reference", "aggregate_stress", "strict_backtest_gate",
    "strict_gate", "generate_candidate_grid", "generate_stress_grid", "apply_vol_target",
    "select_stable_regions",
]
