"""Catalog-wide multiple-testing accountant (Phase 6, build step 4).

Turns the catalog's honest numbers into the deflation that keeps the factory from
fooling itself, WRAPPING (never reimplementing) the validated DSR/PBO math:
  * catalog_dsr(): Deflated Sharpe using the catalog-wide trial count AND the
    catalog-wide Sharpe-variance, fed together (critique P0-1/P0-2).
  * effective_trials(): shrink the trial count for correlated alphas so a basket
    of near-duplicates counts as fewer independent bets (a haircut, not a gift).
  * benjamini_hochberg(): a second, independent catalog-wide lens — an alpha must
    survive BOTH DSR and BH-FDR over the catalog's IC p-values to be called edge.
"""
from __future__ import annotations

from typing import Optional, Sequence

from services.research.validation.deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe


def effective_trials(n_trials: int, mean_abs_corr: float) -> float:
    """n_eff = n / (1 + (n-1)*mean|corr|). corr=0 -> n; corr=1 -> 1."""
    if n_trials <= 1:
        return float(n_trials)
    rho = max(0.0, min(1.0, mean_abs_corr))
    return n_trials / (1.0 + (n_trials - 1) * rho)


def catalog_dsr(observed_sr: float, n_obs: int, catalog) -> Optional[float]:
    """Deflated Sharpe deflated for EVERY alpha ever tried. Returns None when the
    catalog has no honest cross-trial variance estimate yet (< 2 populated
    alphas) — the caller must route to human review, NOT deflate against a
    fabricated floor (review P0)."""
    var_sr = catalog.catalog_var_sr()
    if var_sr is None:
        return None
    return deflated_sharpe_ratio(observed_sr, n_obs, catalog.global_trial_count(), var_sr)


def noise_benchmark(catalog) -> Optional[float]:
    """Expected max Sharpe under the catalog's trial budget — the bar an alpha's
    Sharpe must clear to be more than the best of N coin flips. None if no var_sr."""
    var_sr = catalog.catalog_var_sr()
    if var_sr is None:
        return None
    return expected_max_sharpe(catalog.global_trial_count(), var_sr)


def benjamini_hochberg(pvalues: Sequence[Optional[float]], q: float = 0.10) -> set[int]:
    """Indices (into `pvalues`) of hypotheses rejected at FDR level q (step-up).
    None p-values are ignored (not counted in m)."""
    indexed = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    m = len(indexed)
    if m == 0:
        return set()
    thresh_k = 0
    for rank, (p, _i) in enumerate(indexed, start=1):
        if p <= (rank / m) * q:
            thresh_k = rank
    return {indexed[r - 1][1] for r in range(1, thresh_k + 1)}
