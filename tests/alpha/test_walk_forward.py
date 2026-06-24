"""Audit P2 fix — real out-of-sample walk-forward replaces the full-sample
net_sharpe>0 placeholder. A front-loaded edge (positive in-sample mean but
concentrated in one early block) must now FAIL."""
from services.alpha.walk_forward import walk_forward_oos


def test_consistently_positive_passes():
    returns = [0.02, 0.03, 0.01, 0.025] * 4          # every fold positive
    assert walk_forward_oos(returns, n_folds=4, min_positive_frac=0.5) is True


def test_front_loaded_edge_fails_even_though_full_sample_is_positive():
    front = [0.10, 0.12, 0.11, 0.13]                 # block 0: strong positive
    rest = [-0.01, -0.02, -0.005, -0.015] * 3        # blocks 1-3: negative mean
    returns = front + rest                            # 16 obs, 4 folds
    assert sum(returns) / len(returns) > 0           # the OLD net_sharpe>0 check would PASS
    assert walk_forward_oos(returns, n_folds=4, min_positive_frac=0.5) is False   # real OOS FAILS


def test_thin_series_fails_closed():
    assert walk_forward_oos([0.01, 0.02, 0.03], n_folds=4) is False   # < n_folds*2


def test_too_few_folds_fails_closed():
    assert walk_forward_oos([0.01] * 20, n_folds=1) is False


def test_majority_threshold_boundary():
    # 2 of 4 folds positive -> exactly 0.5 passes at min_positive_frac=0.5
    pos = [0.02, 0.03, 0.01, 0.025]
    neg = [-0.02, -0.03, -0.01, -0.025]
    returns = pos + pos + neg + neg                   # folds: +,+,-,- => 2/4
    assert walk_forward_oos(returns, n_folds=4, min_positive_frac=0.5) is True
    assert walk_forward_oos(returns, n_folds=4, min_positive_frac=0.75) is False
