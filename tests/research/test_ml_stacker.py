import numpy as np
import pytest
from services.research.ml.stacker import fit_stacker


def _data(n=120, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3))
    y = 2.0 * X[:, 0] - 1.0 * X[:, 1] + 0.01 * rng.normal(size=n)   # learnable signal
    return X, y


@pytest.mark.parametrize("model", ["gbm", "ridge"])
def test_fits_and_predicts(model):
    X, y = _data()
    s = fit_stacker(X, y, model=model, seed=0)
    p = s.predict(X)
    assert p.shape == (X.shape[0],)
    # predictions correlate with the learnable target (sanity, in-sample)
    assert np.corrcoef(p, y)[0, 1] > 0.5


@pytest.mark.parametrize("model", ["gbm", "ridge"])
def test_deterministic_same_input_same_output(model):
    X, y = _data()
    p1 = fit_stacker(X, y, model=model, seed=0).predict(X)
    p2 = fit_stacker(X, y, model=model, seed=0).predict(X)
    assert np.array_equal(p1, p2)                              # bit-identical within process


def test_unknown_model_raises():
    X, y = _data()
    with pytest.raises(ValueError):
        fit_stacker(X, y, model="nn")
