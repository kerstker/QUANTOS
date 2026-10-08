"""Multiple-testing corrections beyond BH-FDR (audit gap: no Bonferroni, no Harvey-Liu-Zhu t-hurdle).
Harvey-Liu-Zhu (2016) argue a single-test t>2 is far too lenient once you account for the hundreds of
factors tried; they recommend FWER (Bonferroni/Holm) and dependence-robust FDR (BHY) corrections and a
RAISED t-stat hurdle. These pin the family + their ordering relationships."""
import math

from services.alpha.multiple_testing import (benjamini_hochberg, bonferroni, holm_bonferroni,
                                              benjamini_hochberg_yekutieli, bonferroni_tstat_threshold)

P = [0.001, 0.008, 0.02, 0.04, 0.2]


def test_bonferroni_rejects_only_below_alpha_over_m():
    assert bonferroni(P, alpha=0.05) == {0, 1}          # alpha/m = 0.01 -> p<=0.01


def test_holm_is_at_least_as_powerful_as_bonferroni():
    b = bonferroni(P, alpha=0.05)
    h = holm_bonferroni(P, alpha=0.05)
    assert b <= h                                       # Holm rejects a superset (uniformly more powerful)


def test_bhy_is_stricter_than_bh():
    bh = benjamini_hochberg(P, q=0.05)
    bhy = benjamini_hochberg_yekutieli(P, q=0.05)
    assert bhy <= bh                                    # BHY (dependence-robust) is more conservative
    assert bhy == {0, 1} and bh == {0, 1, 2, 3}


def test_tstat_threshold_rises_with_trials():
    assert abs(bonferroni_tstat_threshold(1, 0.05) - 1.959964) < 1e-4   # standard two-sided 5%
    t100 = bonferroni_tstat_threshold(100, 0.05)
    assert t100 > 3.4                                   # ~3.48 — a t>2 factor would NOT survive 100 trials
    assert bonferroni_tstat_threshold(500, 0.05) > t100  # monotone in the trial count


def test_none_pvalues_ignored_and_deterministic():
    pv = [0.001, None, 0.008, None, 0.2]
    assert bonferroni(pv, alpha=0.05) == bonferroni(pv, alpha=0.05)
    # m counts only the 3 non-None -> alpha/m = 0.05/3 = 0.0167 -> p<=0.0167 -> indices 0 and 2
    assert bonferroni(pv, alpha=0.05) == {0, 2}


def test_empty_and_degenerate():
    assert bonferroni([], alpha=0.05) == set()
    assert holm_bonferroni([None, None], alpha=0.05) == set()
    assert benjamini_hochberg_yekutieli([], q=0.05) == set()
