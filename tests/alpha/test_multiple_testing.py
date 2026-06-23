"""Phase 6 step 4 — catalog-wide multiple-testing: DSR deflates with the catalog
trial count, effective-trials shrinkage, Benjamini-Hochberg FDR."""
from services.alpha.catalog import AlphaCatalog
from services.alpha.multiple_testing import (
    benjamini_hochberg, catalog_dsr, effective_trials, noise_benchmark,
)


def test_effective_trials_shrinks_with_correlation():
    assert effective_trials(100, 0.0) == 100.0          # independent
    assert effective_trials(100, 1.0) == 1.0            # all duplicates -> 1 bet
    assert 1.0 < effective_trials(100, 0.5) < 100.0


def test_catalog_dsr_deflates_and_benchmark_grows_with_catalog_count():
    small = AlphaCatalog()
    for k in range(2):
        small.register_trial(f"c{k}")
    big = AlphaCatalog()
    for k in range(300):
        big.register_trial(f"c{k}")
    assert big.global_trial_count() == 300
    # same observed Sharpe; more catalog trials -> lower DSR and a higher noise bar
    assert catalog_dsr(0.15, 252, big) < catalog_dsr(0.15, 252, small)
    assert noise_benchmark(big) > noise_benchmark(small)


def test_benjamini_hochberg_rejects_the_significant_tail():
    # m=5, q=0.10: thresholds k/m*q = .02 .04 .06 .08 .10
    pvals = [0.001, 0.009, 0.04, 0.5, 0.8]
    passed = benjamini_hochberg(pvals, q=0.10)
    assert passed == {0, 1, 2}           # the three smallest survive FDR
    assert benjamini_hochberg([0.9, 0.95], q=0.10) == set()
    assert benjamini_hochberg([None, None]) == set()
