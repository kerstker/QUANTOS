"""Barra-lite factor diagnostics for research and dry-run books.

This module estimates a small, caller-supplied factor model from existing local
research artifacts. It is intentionally pure: no IO, no wall clock, no network,
and no execution or broker surface.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence

from quantos.contracts.factor import FactorRiskModel

from .factor_risk import risk_decomposition


NumberMap = Mapping[str, float]
SeriesMap = Mapping[str, Sequence[float]]


def _finite_float(value: float) -> float:
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("values must be finite")
    return x


def winsorize(values: NumberMap, *, limit: float = 0.05) -> dict[str, float]:
    """Clip a cross-section to deterministic order-statistic cut points."""
    if not 0.0 <= limit < 0.5:
        raise ValueError("limit must satisfy 0 <= limit < 0.5")
    if not values:
        return {}
    clean = {k: _finite_float(v) for k, v in values.items()}
    ordered = sorted(clean.values())
    n = len(ordered)
    lo_idx = min(int(math.floor(limit * n)), n - 1)
    hi_idx = max(int(math.ceil((1.0 - limit) * n)) - 1, 0)
    lo = ordered[lo_idx]
    hi = ordered[hi_idx]
    return {k: min(max(v, lo), hi) for k, v in clean.items()}


def zscore(values: NumberMap) -> dict[str, float]:
    """Sample-standard-deviation z-score over a cross-section."""
    if not values:
        return {}
    clean = {k: _finite_float(v) for k, v in values.items()}
    if len(clean) < 2:
        return {k: 0.0 for k in clean}
    vals = list(clean.values())
    mean = statistics.fmean(vals)
    stdev = statistics.stdev(vals)
    if stdev <= 0.0:
        return {k: 0.0 for k in clean}
    return {k: (v - mean) / stdev for k, v in clean.items()}


def _aligned_pairs(lhs: NumberMap, rhs: NumberMap) -> tuple[list[float], list[float]]:
    keys = sorted(set(lhs) & set(rhs))
    return ([_finite_float(lhs[k]) for k in keys], [_finite_float(rhs[k]) for k in keys])


def corr_exposure(series_by_symbol: NumberMap, factor_by_symbol: NumberMap) -> float:
    """Pearson correlation on aligned symbols, fail-closed to 0 for thin data."""
    xs, ys = _aligned_pairs(series_by_symbol, factor_by_symbol)
    if len(xs) < 2:
        return 0.0
    sx = statistics.stdev(xs)
    sy = statistics.stdev(ys)
    if sx <= 0.0 or sy <= 0.0:
        return 0.0
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)
    return cov / (sx * sy)


def _sample_var(series: Sequence[float]) -> float:
    vals = [_finite_float(x) for x in series]
    if len(vals) < 2:
        return 0.0
    return statistics.variance(vals)


def _default_factor_cov(
    factor_exposures: Mapping[str, NumberMap],
    factor_ids: Sequence[str],
) -> tuple[tuple[float, ...], ...]:
    diag: list[float] = []
    for fid in factor_ids:
        vals = [_finite_float(v) for v in factor_exposures.get(fid, {}).values()]
        var = statistics.variance(vals) if len(vals) >= 2 else 1.0
        diag.append(max(var, 1e-12))
    return tuple(
        tuple(diag[i] if i == j else 0.0 for j in range(len(factor_ids)))
        for i in range(len(factor_ids))
    )


def _as_cov_tuple(factor_cov: Sequence[Sequence[float]], k: int) -> tuple[tuple[float, ...], ...]:
    cov = tuple(tuple(_finite_float(x) for x in row) for row in factor_cov)
    if len(cov) != k or any(len(row) != k for row in cov):
        raise ValueError("factor_cov must be k x k")
    return cov


def _residual_specific_var(
    symbol_returns: SeriesMap,
    loadings: Mapping[str, tuple[float, ...]],
) -> dict[str, float]:
    specific: dict[str, float] = {}
    for symbol in sorted(symbol_returns):
        realized = _sample_var(symbol_returns[symbol])
        loading_energy = sum(x * x for x in loadings[symbol])
        residual = realized * max(0.10, 1.0 - min(0.90, 0.05 * loading_energy))
        specific[symbol] = max(residual, 1e-12)
    return specific


def build_barra_lite_model(
    symbol_returns: SeriesMap,
    factor_exposures: Mapping[str, NumberMap],
    *,
    factor_cov: Sequence[Sequence[float]] | None = None,
) -> FactorRiskModel:
    """Build a deterministic caller-supplied factor model for diagnostics."""
    factor_ids = tuple(sorted(factor_exposures))
    if not factor_ids:
        raise ValueError("factor_exposures must contain at least one factor")
    symbols = tuple(sorted(symbol_returns))
    if not symbols:
        raise ValueError("symbol_returns must contain at least one symbol")

    loadings = {
        symbol: tuple(
            _finite_float(factor_exposures.get(fid, {}).get(symbol, 0.0))
            for fid in factor_ids
        )
        for symbol in symbols
    }
    cov = _as_cov_tuple(factor_cov, len(factor_ids)) if factor_cov is not None else _default_factor_cov(
        factor_exposures, factor_ids
    )
    specific_var = _residual_specific_var(symbol_returns, loadings)
    return FactorRiskModel(
        factor_ids=factor_ids,
        loadings=loadings,
        factor_cov=cov,
        specific_var=specific_var,
    )


def barra_lite_report(weights: NumberMap, model: FactorRiskModel, *, top_n: int = 5) -> dict:
    """Summarize factor and specific risk for a symbol-weight map."""
    alpha_ids = sorted(weights)
    w = [_finite_float(weights[aid]) for aid in alpha_ids]
    dec = risk_decomposition(w, model, alpha_ids)
    exposures = dec["exposures"]
    top = sorted(exposures.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:top_n]
    return {
        "total_risk": dec["total_var"],
        "factor_risk": dec["factor_var"],
        "specific_risk": dec["specific_var"],
        "factor_contributions": dec["by_factor"],
        "factor_exposures": exposures,
        "top_exposures": [{"factor": factor, "exposure": exposure} for factor, exposure in top],
    }
