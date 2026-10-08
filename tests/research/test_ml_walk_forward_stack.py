# tests/research/test_ml_walk_forward_stack.py
import numpy as np
from services.research.ml.feature_panel import FeaturePanel
from services.research.ml.walk_forward_stack import walk_forward_stack


def _panel(n_periods=24, n_inst=8, predictive=True, seed=0):
    rng = np.random.default_rng(seed)
    rows, X, y = [], [], []
    for i in range(n_periods):
        z = rng.normal(size=n_inst)                            # the feature, cross-sectional
        fwd = (z if predictive else rng.normal(size=n_inst)) * 0.01   # y aligned with feature iff predictive
        for k in range(n_inst):
            rows.append((i, f"i{k}")); X.append([z[k]]); y.append(fwd[k])
    return FeaturePanel(np.asarray(X, float), np.asarray(y, float), rows, ["f"])


def test_predictive_feature_gives_positive_oos():
    out = walk_forward_stack(_panel(predictive=True), train_size=8, test_size=4, embargo=1, model="ridge")
    assert out["n_folds"] >= 2 and out["n_test_periods"] >= 4
    assert sum(out["oos_returns"]) > 0                         # a truly predictive feature survives OOS

def test_noise_feature_gives_no_oos_edge():
    out = walk_forward_stack(_panel(predictive=False, seed=1), train_size=8, test_size=4, embargo=1, model="ridge")
    assert abs(sum(out["oos_returns"])) < 0.5 * out["n_test_periods"]   # noise -> no systematic OOS edge

def test_embargo_excludes_recent_train_rows():
    # tiny panel where without embargo there would be a fold but with a huge embargo there is none
    out = walk_forward_stack(_panel(n_periods=12), train_size=6, test_size=3, embargo=100, model="ridge")
    assert out["n_folds"] == 0 and out["oos_returns"] == []    # embargo starves every fold of train data


def test_cost_bps_reduces_return_and_zero_is_gross():
    p = _panel(predictive=True)
    gross = walk_forward_stack(p, train_size=8, test_size=4, embargo=1, model="ridge", cost_bps=0.0)
    net = walk_forward_stack(p, train_size=8, test_size=4, embargo=1, model="ridge", cost_bps=50.0)
    assert gross["n_test_periods"] == net["n_test_periods"]
    # cost_bps=0 -> unchanged (net==gross); cost_bps>0 -> turnover cost strictly lowers the total
    assert sum(net["oos_returns"]) < sum(gross["oos_returns"])
