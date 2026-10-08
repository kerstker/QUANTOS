"""Regime-conditional evaluation (tests-first).

A factor's performance often depends strongly on the market state (calm vs stressed);
averaging across states hides a factor that only works in one. We label each period into
a volatility regime using ONLY information knowable at that period (trailing vol vs the
EXPANDING distribution seen so far — no full-sample lookahead), then report a factor's
performance WITHIN each regime, plus the fraction of regimes it is profitable in.
"""
from services.research.regime import (
    label_regimes_by_vol, regime_conditional_stats, regime_profitable_frac)

# 30 calm bars (~0.5% moves) then 30 stormy bars (~5% moves)
CALM = [100.0 + (1 if i % 2 else -1) * 0.5 for i in range(30)]
STORM = [100.0 + (1 if i % 2 else -1) * 5.0 for i in range(30)]
MARKET = CALM + STORM


def test_high_vol_periods_get_the_higher_regime():
    labels = label_regimes_by_vol(MARKET, vol_window=10, n_regimes=2)
    calm_lbls = [x for x in labels[10:30] if x is not None]      # after warm-up, calm half
    onset_lbls = [x for x in labels[30:42] if x is not None]      # storm ONSET (elevated vs history)
    assert sum(calm_lbls) / len(calm_lbls) < 0.4                  # mostly regime 0 (low vol)
    assert sum(onset_lbls) / len(onset_lbls) > 0.8               # mostly regime 1 (high vol)


def test_pit_no_lookahead_prefix_stable():
    # a period's regime label must not change when FUTURE bars are appended (PIT)
    full = label_regimes_by_vol(MARKET, vol_window=10, n_regimes=2)
    prefix = label_regimes_by_vol(MARKET[:45], vol_window=10, n_regimes=2)
    assert prefix == full[:45]


def test_regime_conditional_stats_splits_performance():
    labels = [0, 0, 0, 0, 1, 1, 1, 1]
    returns = [0.02, 0.03, 0.01, 0.02, -0.02, -0.03, -0.01, -0.02]   # +in regime0, -in regime1
    stats = regime_conditional_stats(returns, labels)
    assert stats[0]["sharpe"] > 0 and stats[1]["sharpe"] < 0
    assert stats[0]["positive_frac"] == 1.0 and stats[1]["positive_frac"] == 0.0


def test_regime_profitable_frac():
    labels = [0, 0, 0, 0, 1, 1, 1, 1]
    returns = [0.02, 0.03, 0.01, 0.02, -0.02, -0.03, -0.01, -0.02]
    assert regime_profitable_frac(returns, labels) == 0.5         # profitable in 1 of 2 regimes
    good = [0.02, 0.03, 0.01, 0.02, 0.02, 0.03, 0.01, 0.02]
    assert regime_profitable_frac(good, labels) == 1.0            # profitable in both


def test_fail_closed_on_thin():
    assert label_regimes_by_vol([100.0, 101.0], vol_window=10) == [None, None]
    assert regime_profitable_frac([0.01], [0]) is None            # a single point -> not assessable
    assert regime_conditional_stats([], []) == {}
