"""Book-level research capital allocation primitive.

This module recommends research AUM weights across strategy books from
caller-supplied evidence. It is pure portfolio code: no stores, no runtime
execution paths, no broker adapters, no credentials, and no network.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from .constrained_optimizer import OptimizerError, min_variance_weights
from .constraints import InfeasibleConstraints, PortfolioConstraints
from .optimizer import cost_aware_rebalance
from .risk_model import covariance_matrix, percent_risk_contributions, shrink_covariance


ALLOCATED = "ALLOCATED"
BLOCKED = "BLOCKED"
_EPS = 1e-12
_WEIGHT_SUM_TOL = 1e-8
_CAP_TOL = 1e-10


@dataclass(frozen=True)
class CapitalAllocatorConfig:
    method: str = "covariance_mean_variance"
    min_observations: int = 20
    max_weight: float = 0.70
    no_trade_band: float = 0.02
    risk_aversion: float = 1.0
    mean_shrink_z: float = 1.0
    min_positive_mean: float = 0.0
    cvar_penalty: float = 0.0        # Rank #10c: >0 adds a tail-aware CVaR penalty to the optimizer
    cvar_alpha: float = 0.95         # CVaR confidence level (tail = worst 1-alpha of scenarios)


@dataclass(frozen=True)
class BookAllocation:
    book_id: str
    target_weight: float
    target_aum: float | None
    unconstrained_weight: float
    cap_weight: float | None


@dataclass(frozen=True)
class CapitalAllocationPlan:
    status: str
    allocations: tuple[BookAllocation, ...]
    turnover: float
    reasons: tuple[str, ...]
    diagnostics: dict


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _blocked(reason: str, diagnostics: dict | None = None) -> CapitalAllocationPlan:
    return CapitalAllocationPlan(
        status=BLOCKED,
        allocations=(),
        turnover=0.0,
        reasons=(reason,),
        diagnostics=diagnostics or {},
    )


def _validate_config(config: CapitalAllocatorConfig) -> str | None:
    if config.method != "covariance_mean_variance":
        return f"invalid_method:{config.method}"
    if not isinstance(config.min_observations, int) or config.min_observations < 2:
        return "invalid_min_observations"
    if not _is_finite_number(config.max_weight) or not (0.0 < float(config.max_weight) <= 1.0):
        return "invalid_max_weight"
    if not _is_finite_number(config.no_trade_band) or float(config.no_trade_band) < 0.0:
        return "invalid_no_trade_band"
    if not _is_finite_number(config.risk_aversion) or float(config.risk_aversion) <= 0.0:
        return "invalid_risk_aversion"
    if not _is_finite_number(config.mean_shrink_z) or float(config.mean_shrink_z) < 0.0:
        return "invalid_mean_shrink_z"
    if not _is_finite_number(config.cvar_penalty) or float(config.cvar_penalty) < 0.0:
        return "invalid_cvar_penalty"
    if not _is_finite_number(config.cvar_alpha) or not (0.0 < float(config.cvar_alpha) < 1.0):
        return "invalid_cvar_alpha"
    if not _is_finite_number(config.min_positive_mean):
        return "invalid_min_positive_mean"
    return None


def _stats(values: Sequence[float]) -> tuple[float, float, float, float]:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    volatility = math.sqrt(max(variance, 0.0))
    standard_error = volatility / math.sqrt(len(values))
    return mean, variance, volatility, standard_error


def _validate_total_aum(total_aum: float | None) -> str | None:
    if total_aum is None:
        return None
    if not _is_finite_number(total_aum) or float(total_aum) <= 0.0:
        return "invalid_total_aum"
    return None


def _effective_caps(
    book_ids: Sequence[str],
    *,
    positive_books: set[str],
    total_aum: float | None,
    capacity_by_book: Mapping[str, float] | None,
    config: CapitalAllocatorConfig,
) -> tuple[dict[str, float], dict[str, float], str | None]:
    caps = {
        book_id: (float(config.max_weight) if book_id in positive_books else 0.0)
        for book_id in book_ids
    }
    capacity_caps: dict[str, float] = {}
    if capacity_by_book is None:
        return caps, capacity_caps, None

    aum_reason = _validate_total_aum(total_aum)
    if aum_reason is not None:
        return caps, capacity_caps, aum_reason

    known = set(book_ids)
    for book_id, capacity in capacity_by_book.items():
        if book_id not in known:
            return caps, capacity_caps, f"unknown_capacity:{book_id}"
        if not _is_finite_number(capacity) or float(capacity) < 0.0:
            return caps, capacity_caps, f"invalid_capacity:{book_id}"
        cap_weight = float(capacity) / float(total_aum)
        capacity_caps[book_id] = cap_weight
        caps[book_id] = min(caps[book_id], cap_weight)

    return caps, capacity_caps, None


def _validate_prev_weights(prev_weights: Mapping[str, float], known: set[str]) -> str | None:
    total = 0.0
    for book_id in sorted(prev_weights):
        weight = prev_weights[book_id]
        if book_id not in known:
            return f"unknown_prev_weight:{book_id}"
        if not _is_finite_number(weight) or float(weight) < 0.0:
            return f"invalid_prev_weight:{book_id}"
        total += float(weight)
    if abs(total - 1.0) > 1e-6:
        return "invalid_prev_weight_sum"
    return None


def _risk_diagnostics(
    returns_by_book: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
    min_observations: int,
) -> tuple[dict[str, float], list[str], list[list[float]]]:
    ids, cov = covariance_matrix(dict(returns_by_book), min_overlap=min_observations)
    cov = shrink_covariance(cov, intensity=0.2)
    vector = [weights.get(book_id, 0.0) for book_id in ids]
    prc = percent_risk_contributions(vector, cov)
    return {ids[i]: prc[i] for i in range(len(ids))}, ids, cov


def _optimizer_expected_returns(expected_returns: Sequence[float], cov: Sequence[Sequence[float]]) -> list[float]:
    diag = [float(cov[i][i]) for i in range(len(cov)) if float(cov[i][i]) > _EPS]
    scale = sum(diag) / len(diag) if diag else 1.0
    return [float(value) * scale for value in expected_returns]


def _clean_weights(weights: Mapping[str, float], caps: Mapping[str, float]) -> dict[str, float] | None:
    cleaned = {}
    for book_id in sorted(weights):
        value = float(weights[book_id])
        cap = float(caps[book_id])
        if abs(value) <= 1e-9:
            value = 0.0
        if abs(value - cap) <= 1e-9:
            value = cap
        if value < -_CAP_TOL or value > cap + _CAP_TOL:
            return None
        cleaned[book_id] = min(max(value, 0.0), cap)
    return _waterfill_to_budget(cleaned, {book_id: float(caps[book_id]) for book_id in cleaned})


def _waterfill_to_budget(cleaned: dict[str, float], caps: Mapping[str, float],
                         budget: float = 1.0) -> dict[str, float] | None:
    """Rank #10b — bounded ITERATIVE waterfill to the budget, clipping each round so a cap can
    never be breached (auditable termination + fail-closed, replacing the implicit single pass).
    Deficit is filled proportional to remaining room; excess is drawn proportional to weight; a
    book that hits its cap (fill) or 0 (draw) freezes, so each round resolves the residual or
    freezes ≥1 book — convergence in ≤ len(books)+1 rounds. Returns None (BLOCKED) if the caps
    cannot absorb the budget (infeasible) or it does not settle."""
    ids = list(cleaned)
    for _ in range(len(ids) + 1):
        residual = budget - sum(cleaned.values())
        if abs(residual) <= _WEIGHT_SUM_TOL:
            break
        if residual > 0.0:                                   # fill: distribute to books with room
            room = {b: caps[b] - cleaned[b] for b in ids}
            room_total = sum(v for v in room.values() if v > _EPS)
            if room_total <= _EPS:
                return None                                  # no room to absorb the deficit -> infeasible
            for b in ids:
                if room[b] > _EPS:
                    cleaned[b] = min(caps[b], cleaned[b] + residual * room[b] / room_total)
        else:                                                # draw: remove proportional to weight
            excess = -residual
            positive_total = sum(v for v in cleaned.values() if v > _EPS)
            if positive_total <= _EPS:
                return None
            for b in ids:
                if cleaned[b] > _EPS:
                    cleaned[b] = max(0.0, cleaned[b] - excess * cleaned[b] / positive_total)

    # snap the sub-tolerance convergence residual (FP drift from the rounds) exactly onto the
    # slackest book, so the budget is met to FP precision without breaching any cap
    residual = budget - sum(cleaned.values())
    if 0.0 < abs(residual) <= _WEIGHT_SUM_TOL and ids:
        if residual > 0.0:
            b = max(ids, key=lambda x: caps[x] - cleaned[x])
            if caps[b] - cleaned[b] + _CAP_TOL >= residual:
                cleaned[b] += residual
        else:
            b = max(ids, key=lambda x: cleaned[x])
            if cleaned[b] + _CAP_TOL >= -residual:
                cleaned[b] += residual

    if abs(sum(cleaned.values()) - budget) > _WEIGHT_SUM_TOL:
        return None
    if any(cleaned[b] < -_CAP_TOL or cleaned[b] > caps[b] + _CAP_TOL for b in ids):
        return None
    return cleaned


def allocate_capital(
    returns_by_book: Mapping[str, Sequence[float]],
    *,
    total_aum: float | None = None,
    capacity_by_book: Mapping[str, float] | None = None,
    quality_multiplier_by_book: Mapping[str, float] | None = None,
    prev_weights: Mapping[str, float] | None = None,
    config: CapitalAllocatorConfig | None = None,
) -> CapitalAllocationPlan:
    config = config or CapitalAllocatorConfig()
    config_reason = _validate_config(config)
    if config_reason is not None:
        return _blocked(config_reason)

    aum_reason = _validate_total_aum(total_aum)
    if aum_reason is not None:
        return _blocked(aum_reason)

    if not returns_by_book:
        return _blocked("empty_returns_by_book")

    book_ids = sorted(returns_by_book)
    for book_id in book_ids:
        if not isinstance(book_id, str) or not book_id.strip():
            return _blocked(f"invalid_book_id:{book_id}")

    known = set(book_ids)
    quality = {book_id: 1.0 for book_id in book_ids}
    if quality_multiplier_by_book is not None:
        for book_id in sorted(quality_multiplier_by_book):
            multiplier = quality_multiplier_by_book[book_id]
            if book_id not in known:
                return _blocked(f"unknown_quality_multiplier:{book_id}")
            if (
                not _is_finite_number(multiplier)
                or float(multiplier) < 0.0
                or float(multiplier) > 1.0
            ):
                return _blocked(f"invalid_quality_multiplier:{book_id}")
            quality[book_id] = float(multiplier)

    book_stats: dict[str, dict[str, float | int]] = {}
    clean_returns: dict[str, tuple[float, ...]] = {}
    expected_by_book: dict[str, float] = {}
    for book_id in book_ids:
        values: list[float] = []
        for value in returns_by_book[book_id]:
            if not _is_finite_number(value):
                return _blocked(f"invalid_return:{book_id}")
            values.append(float(value))
        if len(values) < config.min_observations:
            return _blocked(f"insufficient_observations:{book_id}")
        mean, variance, volatility, standard_error = _stats(values)
        if not math.isfinite(variance) or variance <= _EPS:
            return _blocked(f"zero_variance:{book_id}")
        shrunk_mean = mean - float(config.mean_shrink_z) * standard_error
        expected_return = max(shrunk_mean - float(config.min_positive_mean), 0.0) * quality[book_id]
        clean_returns[book_id] = tuple(values)
        expected_by_book[book_id] = expected_return
        book_stats[book_id] = {
            "mean": mean,
            "variance": variance,
            "volatility": volatility,
            "standard_error": standard_error,
            "shrunk_mean": shrunk_mean,
            "expected_return": expected_return,
            "quality_multiplier": quality[book_id],
            "observations": len(values),
        }

    positive_books = {book_id for book_id, value in expected_by_book.items() if value > _EPS}
    if not positive_books:
        return _blocked("no_positive_expected_return", {"book_stats": book_stats})

    caps, capacity_caps, cap_reason = _effective_caps(
        book_ids,
        positive_books=positive_books,
        total_aum=total_aum,
        capacity_by_book=capacity_by_book,
        config=config,
    )
    if cap_reason is not None:
        return _blocked(cap_reason, {"book_stats": book_stats})
    if sum(caps.values()) < 1.0 - _CAP_TOL:
        return _blocked(
            "infeasible_capacity_caps",
            {"book_stats": book_stats, "capacity_caps": capacity_caps, "effective_caps": caps},
        )

    try:
        ids, cov = covariance_matrix(clean_returns, min_overlap=config.min_observations)
        cov = shrink_covariance(cov, intensity=0.2)
        expected = [expected_by_book[book_id] for book_id in ids]
        optimizer_expected = _optimizer_expected_returns(expected, cov)
        constraints = PortfolioConstraints(lower=0.0, upper={book_id: caps[book_id] for book_id in ids}, budget=1.0)
        # Rank #10c: when cvar_penalty>0, feed the tail-aligned cross-book return scenarios to the
        # optimizer so it sizes for the fat left tail (Hill 2.4-2.7), not variance alone.
        cvar_scenarios = None
        if float(config.cvar_penalty) > 0.0:
            span = min(len(clean_returns[b]) for b in ids)
            cvar_scenarios = [[clean_returns[b][-span + t] for b in ids] for t in range(span)]
        raw_weights = min_variance_weights(
            cov,
            ids,
            constraints,
            expected_returns=optimizer_expected,
            risk_aversion=float(config.risk_aversion),
            cvar_scenarios=cvar_scenarios,
            cvar_penalty=float(config.cvar_penalty),
            cvar_alpha=float(config.cvar_alpha),
        )
    except (ValueError, InfeasibleConstraints, OptimizerError, IndexError) as exc:
        return _blocked(
            "optimizer_failed",
            {
                "book_stats": book_stats,
                "capacity_caps": capacity_caps,
                "effective_caps": caps,
                "error": str(exc),
            },
        )

    optimized = {ids[i]: raw_weights[i] for i in range(len(ids))}
    for book_id in book_ids:
        optimized.setdefault(book_id, 0.0)
    if any(not math.isfinite(optimized[book_id]) for book_id in book_ids):
        return _blocked("optimizer_failed", {"book_stats": book_stats})
    cleaned = _clean_weights(optimized, caps)
    if cleaned is None:
        return _blocked("optimizer_failed", {"book_stats": book_stats, "effective_caps": caps})
    optimized = cleaned

    turnover = sum(abs(optimized.get(book_id, 0.0)) for book_id in book_ids)
    final_weights = dict(optimized)
    if prev_weights is not None:
        prev_reason = _validate_prev_weights(prev_weights, known)
        if prev_reason is not None:
            return _blocked(prev_reason, {"book_stats": book_stats})
        final_weights, turnover = cost_aware_rebalance(
            dict(prev_weights),
            optimized,
            no_trade_band=float(config.no_trade_band),
        )
        final_weights = {book_id: final_weights.get(book_id, 0.0) for book_id in book_ids}
        for book_id in book_ids:
            if final_weights[book_id] > caps[book_id] + _CAP_TOL:
                return _blocked("post_rebalance_cap_violation", {"book_stats": book_stats, "effective_caps": caps})

    total_weight = sum(final_weights.values())
    if not math.isfinite(total_weight) or abs(total_weight - 1.0) > _WEIGHT_SUM_TOL:
        return _blocked("allocation_not_normalized", {"book_stats": book_stats})

    risk_contribution_pct = _risk_diagnostics(clean_returns, final_weights, config.min_observations)[0]
    diagnostics = {
        "method": config.method,
        "covariance_aware": True,
        "book_stats": book_stats,
        "capacity_caps": capacity_caps,
        "effective_caps": caps,
        "risk_contribution_pct": risk_contribution_pct,
        "numerical_tolerance": {
            "weight_sum": _WEIGHT_SUM_TOL,
            "cap": _CAP_TOL,
            "zero_variance": _EPS,
        },
    }

    allocations = tuple(
        BookAllocation(
            book_id=book_id,
            target_weight=final_weights[book_id],
            target_aum=(final_weights[book_id] * float(total_aum) if total_aum is not None else None),
            unconstrained_weight=optimized[book_id],
            cap_weight=caps[book_id],
        )
        for book_id in book_ids
    )
    return CapitalAllocationPlan(
        status=ALLOCATED,
        allocations=allocations,
        turnover=turnover,
        reasons=("capital_allocated",),
        diagnostics=diagnostics,
    )
