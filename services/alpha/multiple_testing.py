"""Catalog-wide multiple-testing accountant (Phase 6, build step 4).

Turns the catalog's honest numbers into the deflation that keeps the factory from
fooling itself, WRAPPING (never reimplementing) the validated DSR/PBO math:
  * catalog_dsr(): read replayable Deflated Sharpe minted by the catalog.
  * effective_trials(): shrink the trial count for correlated alphas so a basket
    of near-duplicates counts as fewer independent bets (a haircut, not a gift).
  * benjamini_hochberg(): a second, independent catalog-wide lens — an alpha must
    survive BOTH DSR and BH-FDR over the catalog's IC p-values to be called edge.
"""
from __future__ import annotations

from statistics import NormalDist
from typing import Optional, Sequence

from services.validation.deflated_sharpe import expected_max_sharpe
from services.validation.effective_trials import effective_trials

_N = NormalDist()


def catalog_dsr(alpha_id: str, catalog) -> Optional[float]:
    """Return the replayable Deflated Sharpe minted by the catalog. None means the
    catalog has no honest cross-trial variance estimate yet (< 2 populated
    alphas) — the caller must route to human review, NOT deflate against a
    fabricated floor (review P0)."""
    evidence = catalog.evidence(alpha_id)
    return evidence.deflated_sharpe if evidence is not None else None


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


def bonferroni(pvalues: Sequence[Optional[float]], alpha: float = 0.05) -> set[int]:
    """Family-wise (Bonferroni) rejections: reject hypothesis i iff p_i <= alpha/m, where m is the
    number of non-None p-values. The strictest, dependence-agnostic FWER control. None p's ignored."""
    present = [(p, i) for i, p in enumerate(pvalues) if p is not None]
    m = len(present)
    if m == 0:
        return set()
    thresh = alpha / m
    return {i for p, i in present if p <= thresh}


def holm_bonferroni(pvalues: Sequence[Optional[float]], alpha: float = 0.05) -> set[int]:
    """Holm step-down FWER control: sort ascending; reject p_(k) while p_(k) <= alpha/(m-k+1), stopping
    at the first failure. Uniformly more powerful than Bonferroni (rejects a superset) at the same FWER."""
    indexed = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    m = len(indexed)
    rejected: set = set()
    for k, (p, i) in enumerate(indexed, start=1):
        if p <= alpha / (m - k + 1):
            rejected.add(i)
        else:
            break
    return rejected


def benjamini_hochberg_yekutieli(pvalues: Sequence[Optional[float]], q: float = 0.10) -> set[int]:
    """BHY step-up FDR control valid under ARBITRARY dependence (Harvey-Liu-Zhu's recommended factor-zoo
    correction). Same as BH but the level is divided by the harmonic number c(m)=sum_{i=1..m} 1/i, so it
    is strictly more conservative than plain BH. None p's ignored."""
    indexed = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    m = len(indexed)
    if m == 0:
        return set()
    c_m = sum(1.0 / i for i in range(1, m + 1))
    thresh_k = 0
    for rank, (p, _i) in enumerate(indexed, start=1):
        if p <= (rank / (m * c_m)) * q:
            thresh_k = rank
    return {indexed[r - 1][1] for r in range(1, thresh_k + 1)}


def bonferroni_tstat_threshold(n_trials: int, alpha: float = 0.05) -> float:
    """The minimum |t-stat| a factor must clear under a two-sided Bonferroni correction for `n_trials`
    tested factors: t* = Phi^-1(1 - alpha/(2*n)). This is the Harvey-Liu-Zhu 'haircut' that makes the
    customary t>2 hurdle far stricter once hundreds of factors have been mined (e.g. n=100 -> ~3.48)."""
    n = max(1, int(n_trials))
    return _N.inv_cdf(1.0 - alpha / (2.0 * n))
