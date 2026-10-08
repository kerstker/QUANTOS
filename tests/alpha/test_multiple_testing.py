"""Phase 6 step 4 — catalog-wide multiple-testing: DSR deflates with the catalog
trial count, effective-trials shrinkage, Benjamini-Hochberg FDR."""
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.catalog import AlphaCatalog
from services.alpha.multiple_testing import (
    benjamini_hochberg, catalog_dsr, effective_trials, noise_benchmark,
)


def _cat(extra_trials):
    """Catalog with 2 populated alphas (real var_sr) + extra distinct trials."""
    c = AlphaCatalog()
    for i, r in enumerate([(0.01, 0.02, -0.01, 0.03), (0.02, -0.01, 0.0, 0.01)]):
        c.register(AlphaDefinition(alpha_id=f"s{i}", name="s", factor_ids=(f"f{i}",)))
        c.set_returns(f"s{i}", r)
    for k in range(extra_trials):
        c.register_trial(stable_hash({"extra": k}))
    return c


def test_effective_trials_shrinks_with_correlation():
    assert effective_trials(100, 0.0) == 100.0          # independent
    assert effective_trials(100, 1.0) == 1.0            # all duplicates -> 1 bet
    assert 1.0 < effective_trials(100, 0.5) < 100.0


def test_catalog_dsr_deflates_and_benchmark_grows_with_catalog_count():
    small, big = _cat(0), _cat(300)     # counts 2 vs 302, same var_sr population
    # same observed Sharpe; more catalog trials -> lower DSR and a higher noise bar
    assert catalog_dsr("s0", big) < catalog_dsr("s0", small)
    assert noise_benchmark(big) > noise_benchmark(small)


def test_catalog_dsr_is_none_without_var_sr_population():
    c = AlphaCatalog()
    for k in range(50):
        c.register_trial(f"c{k}")       # trials but no populated returns
    assert catalog_dsr("missing", c) is None and noise_benchmark(c) is None


def test_benjamini_hochberg_rejects_the_significant_tail():
    # m=5, q=0.10: thresholds k/m*q = .02 .04 .06 .08 .10
    pvals = [0.001, 0.009, 0.04, 0.5, 0.8]
    passed = benjamini_hochberg(pvals, q=0.10)
    assert passed == {0, 1, 2}           # the three smallest survive FDR
    assert benjamini_hochberg([0.9, 0.95], q=0.10) == set()
    assert benjamini_hochberg([None, None]) == set()
