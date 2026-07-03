"""Pure Brinson-Fachler portfolio attribution.

This module is a research explainability primitive. It performs no IO, does not
read stores, and has no execution, broker, order, credential, or live-trading
surface.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Mapping


WEIGHT_SUM_TOL = 1e-8
IDENTITY_TOL = 1e-10
ZERO_WEIGHT_TOL = 1e-12


@dataclass(frozen=True)
class BrinsonGroupAttribution:
    group: str
    portfolio_weight: float
    benchmark_weight: float
    portfolio_return: float
    benchmark_return: float
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    active_return: float


@dataclass(frozen=True)
class BrinsonAttributionReport:
    status: str
    groups: tuple[BrinsonGroupAttribution, ...]
    totals: dict[str, float]
    reasons: tuple[str, ...]
    diagnostics: dict[str, float | int | str]


def _empty_totals() -> dict[str, float]:
    return {
        "allocation_effect": 0.0,
        "selection_effect": 0.0,
        "interaction_effect": 0.0,
        "active_return": 0.0,
        "portfolio_return": 0.0,
        "benchmark_return": 0.0,
    }


def _blocked(reason: str, diagnostics: dict[str, float | int | str] | None = None) -> BrinsonAttributionReport:
    return BrinsonAttributionReport(
        status="BLOCKED",
        groups=(),
        totals=_empty_totals(),
        reasons=(reason,),
        diagnostics=diagnostics or {},
    )


def _finite_float(value: object, reason: str) -> tuple[float | None, str | None]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None, reason
    if not math.isfinite(x):
        return None, reason
    return x, None


def _clean_weight_map(values: Mapping[str, float], reason: str) -> tuple[dict[str, float] | None, str | None]:
    clean: dict[str, float] = {}
    for key in sorted(values):
        value, error = _finite_float(values[key], reason)
        if error is not None:
            return None, error
        clean[str(key)] = value if value is not None else 0.0
    return clean, None


def _clean_returns(values: Mapping[str, float]) -> tuple[dict[str, float] | None, str | None]:
    clean: dict[str, float] = {}
    for key in sorted(values):
        value, error = _finite_float(values[key], "non_finite_return")
        if error is not None:
            return None, error
        clean[str(key)] = value if value is not None else 0.0
    return clean, None


def _nonzero_assets(weights: Mapping[str, float]) -> set[str]:
    return {asset for asset, weight in weights.items() if abs(weight) > ZERO_WEIGHT_TOL}


def _validate_weight_sum(weights: Mapping[str, float], reason: str) -> str | None:
    if not weights or sum(abs(v) for v in weights.values()) <= ZERO_WEIGHT_TOL:
        return reason
    if abs(sum(weights.values()) - 1.0) > WEIGHT_SUM_TOL:
        return reason
    return None


def _group_return(
    assets: set[str],
    weights: Mapping[str, float],
    returns: Mapping[str, float],
    group_weight: float,
) -> float:
    if abs(group_weight) <= ZERO_WEIGHT_TOL:
        return 0.0
    return sum(weights.get(asset, 0.0) * returns[asset] for asset in assets) / group_weight


def brinson_fachler_attribution(
    portfolio_weights: Mapping[str, float],
    benchmark_weights: Mapping[str, float],
    asset_returns: Mapping[str, float],
    groups: Mapping[str, str],
) -> BrinsonAttributionReport:
    """Compute single-period Brinson-Fachler active return attribution.

    The caller supplies portfolio weights, benchmark weights, asset returns, and
    one group label per weighted asset. Invalid inputs return a BLOCKED report.
    """
    p_weights, error = _clean_weight_map(portfolio_weights, "non_finite_weight")
    if error is not None or p_weights is None:
        return _blocked(error or "non_finite_weight")
    b_weights, error = _clean_weight_map(benchmark_weights, "non_finite_weight")
    if error is not None or b_weights is None:
        return _blocked(error or "non_finite_weight")
    returns, error = _clean_returns(asset_returns)
    if error is not None or returns is None:
        return _blocked(error or "non_finite_return")

    error = _validate_weight_sum(p_weights, "portfolio_weights_sum_not_one")
    if error is not None:
        return _blocked(error, {"portfolio_weight_sum": sum(p_weights.values())})
    error = _validate_weight_sum(b_weights, "benchmark_weights_sum_not_one")
    if error is not None:
        return _blocked(error, {"benchmark_weight_sum": sum(b_weights.values())})

    weighted_assets = _nonzero_assets(p_weights) | _nonzero_assets(b_weights)
    for asset in sorted(weighted_assets):
        if asset not in returns:
            return _blocked("missing_asset_return", {"asset": asset})
        if asset not in groups:
            return _blocked("missing_asset_group", {"asset": asset})
        if groups[asset] is None or str(groups[asset]) == "":
            return _blocked("missing_asset_group", {"asset": asset})

    group_assets: dict[str, set[str]] = {}
    for asset in sorted(weighted_assets):
        group = str(groups[asset])
        group_assets.setdefault(group, set()).add(asset)

    portfolio_return = sum(p_weights.get(asset, 0.0) * returns.get(asset, 0.0) for asset in p_weights)
    benchmark_return = sum(b_weights.get(asset, 0.0) * returns.get(asset, 0.0) for asset in b_weights)

    rows: list[BrinsonGroupAttribution] = []
    for group in sorted(group_assets):
        assets = group_assets[group]
        p_weight = sum(p_weights.get(asset, 0.0) for asset in assets)
        b_weight = sum(b_weights.get(asset, 0.0) for asset in assets)
        p_return = _group_return(assets, p_weights, returns, p_weight)
        b_return = _group_return(assets, b_weights, returns, b_weight)

        allocation = (p_weight - b_weight) * (b_return - benchmark_return)
        selection = b_weight * (p_return - b_return)
        interaction = (p_weight - b_weight) * (p_return - b_return)
        active = p_weight * p_return - b_weight * b_return
        rows.append(
            BrinsonGroupAttribution(
                group=group,
                portfolio_weight=p_weight,
                benchmark_weight=b_weight,
                portfolio_return=p_return,
                benchmark_return=b_return,
                allocation_effect=allocation,
                selection_effect=selection,
                interaction_effect=interaction,
                active_return=active,
            )
        )

    totals = {
        "allocation_effect": sum(row.allocation_effect for row in rows),
        "selection_effect": sum(row.selection_effect for row in rows),
        "interaction_effect": sum(row.interaction_effect for row in rows),
        "active_return": portfolio_return - benchmark_return,
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
    }
    identity_sum = totals["allocation_effect"] + totals["selection_effect"] + totals["interaction_effect"]
    residual = identity_sum - totals["active_return"]
    if abs(residual) > IDENTITY_TOL:
        return _blocked(
            "identity_residual_exceeds_tolerance",
            {"identity_residual": residual, "identity_tolerance": IDENTITY_TOL},
        )

    return BrinsonAttributionReport(
        status="ATTRIBUTED",
        groups=tuple(rows),
        totals=totals,
        reasons=(),
        diagnostics={
            "asset_count": len(weighted_assets),
            "group_count": len(rows),
            "identity_residual": residual,
            "identity_tolerance": IDENTITY_TOL,
        },
    )
