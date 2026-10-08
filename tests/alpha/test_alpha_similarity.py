"""Alpha similarity across the modalities the audit found missing — so near-duplicates can be detected
before a redundant alpha is admitted:
  * expression_similarity — shared-subtree Jaccard over the DSL trees (AST structural similarity),
  * return_similarity     — |Pearson| of PnL series (the existing modality, surfaced here too),
  * holdings_overlap      — cosine of position-weight vectors (same bets),
  * exposure_similarity   — cosine of style/factor-exposure vectors.
All deterministic, symmetric, bounded [0,1]."""
from services.alpha import similarity as sim


# ---- expression (AST) ----
def test_expression_similarity_identical_and_disjoint():
    t = ("cs_rank", ("ts_mean", ("field", "close"), 5))
    assert sim.expression_similarity(t, t) == 1.0
    u = ("cs_zscore", ("ts_std", ("field", "volume"), 7))
    assert sim.expression_similarity(t, u) < 0.3                 # no shared subtrees beyond structure
    assert sim.expression_similarity(t, u) == sim.expression_similarity(u, t)   # symmetric


def test_expression_similarity_partial_overlap_is_between():
    a = ("cs_rank", ("ts_mean", ("field", "close"), 5))
    b = ("cs_zscore", ("ts_mean", ("field", "close"), 5))       # shares the inner ts_mean(close,5)
    s = sim.expression_similarity(a, b)
    assert 0.0 < s < 1.0
    assert s > sim.expression_similarity(a, ("cs_zscore", ("field", "volume")))


# ---- returns ----
def test_return_similarity_is_abs_correlation():
    a = [0.01, -0.02, 0.03, -0.01, 0.02]
    assert abs(sim.return_similarity(a, a) - 1.0) < 1e-9
    anti = [-x for x in a]
    assert abs(sim.return_similarity(a, anti) - 1.0) < 1e-9     # |corr| -> redundant even if inverted
    const = [0.0] * 5
    assert sim.return_similarity(a, const) == 0.0               # no linear relation / zero variance


# ---- holdings ----
def test_holdings_overlap_cosine():
    a = {"BTC": 0.5, "ETH": 0.5}
    assert abs(sim.holdings_overlap(a, a) - 1.0) < 1e-9
    assert sim.holdings_overlap(a, {"SOL": 1.0}) == 0.0         # disjoint names
    half = sim.holdings_overlap(a, {"BTC": 1.0})
    assert 0.0 < half < 1.0


def test_holdings_overlap_sign_aware():
    # a long-BTC book and a short-BTC book are OPPOSITE, not overlapping
    assert sim.holdings_overlap({"BTC": 1.0}, {"BTC": -1.0}) < 0.0


# ---- exposures ----
def test_exposure_similarity_cosine():
    a = {"mom": 1.0, "value": 0.5}
    assert abs(sim.exposure_similarity(a, a) - 1.0) < 1e-9
    assert sim.exposure_similarity(a, {}) == 0.0


def test_edge_cases_and_determinism():
    assert sim.return_similarity([], []) == 0.0
    assert sim.holdings_overlap({}, {}) == 0.0
    t = ("cs_rank", ("field", "close"))
    assert sim.expression_similarity(t, t) == sim.expression_similarity(t, t)


def test_no_rng_or_wallclock_import():
    import inspect
    src = inspect.getsource(sim)
    assert "import random" not in src and "import time" not in src and "Random" not in src
