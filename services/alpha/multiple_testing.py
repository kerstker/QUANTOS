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

from typing import Optional

from services.validation.deflated_sharpe import expected_max_sharpe
from services.validation.effective_trials import effective_trials

from quantos.analytics.multiple_testing import (
    benjamini_hochberg,
    bonferroni,
    holm_bonferroni,
    benjamini_hochberg_yekutieli,
    bonferroni_tstat_threshold,
)


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



__all__ = ['catalog_dsr', 'noise_benchmark', 'effective_trials', 'benjamini_hochberg', 'bonferroni', 'holm_bonferroni', 'benjamini_hochberg_yekutieli', 'bonferroni_tstat_threshold']
