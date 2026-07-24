import numpy as np
import pytest

import services.research.ml.accelerator as accelerator_module
from services.research.ml.accelerator import inspect_accelerator, resolve_accelerator
from services.research.ml.feature_panel import FeaturePanel
from services.research.ml.stacker import fit_stacker
from services.research.ml.walk_forward_stack import walk_forward_stack


def _data(n=64, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3))
    y = 2.0 * X[:, 0] - X[:, 1]
    return X, y


def test_cpu_is_explicit_and_does_not_require_gpu():
    status = inspect_accelerator("cpu")
    assert status.selected == "cpu"
    assert resolve_accelerator("cpu") == "cpu"


def test_invalid_accelerator_is_rejected():
    with pytest.raises(ValueError):
        inspect_accelerator("tpu")
    with pytest.raises(ValueError):
        resolve_accelerator("tpu")


def test_explicit_cuda_fails_closed_without_backend(monkeypatch):
    monkeypatch.setattr(accelerator_module, "_xgboost_info", lambda: (False, False))
    monkeypatch.setattr(accelerator_module, "_cuda_device_count", lambda: 0)
    with pytest.raises(RuntimeError, match="CUDA research accelerator requested"):
        resolve_accelerator("cuda")


def test_xgb_cpu_backend_is_optional_research_only():
    pytest.importorskip("xgboost")
    X, y = _data()
    s = fit_stacker(X, y, model="xgb", accelerator="cpu", seed=0)
    assert s.model_name == "xgb"
    assert s.accelerator == "cpu"
    assert s.predict(X).shape == (len(X),)


def test_cuda_xgb_backend_runs_when_machine_has_cuda():
    status = inspect_accelerator("cuda")
    if not status.cuda_available:
        pytest.skip(f"CUDA research backend unavailable: {status.reason}")
    X, y = _data()
    s1 = fit_stacker(X, y, model="xgb", accelerator="cuda", seed=0)
    s2 = fit_stacker(X, y, model="xgb", accelerator="cuda", seed=0)
    p1, p2 = s1.predict(X), s2.predict(X)
    assert s1.accelerator == "cuda"
    assert p1.shape == (len(X),)
    # GPU reductions may differ by a few ulps; the configured run is otherwise
    # deterministic at the one-worker/one-device boundary.
    np.testing.assert_allclose(p1, p2, rtol=0.0, atol=1e-12)


def test_cuda_walk_forward_path_is_wired():
    status = inspect_accelerator("cuda")
    if not status.cuda_available:
        pytest.skip(f"CUDA research backend unavailable: {status.reason}")
    rows, xs, ys = [], [], []
    for period in range(12):
        for instrument in range(8):
            signal = float(period + instrument) / 10.0
            rows.append((period, f"i{instrument}"))
            xs.append([signal])
            ys.append(signal * 0.01)
    out = walk_forward_stack(
        FeaturePanel(np.asarray(xs), np.asarray(ys), rows, ["signal"]),
        train_size=6,
        test_size=2,
        embargo=1,
        model="xgb",
        accelerator="cuda",
    )
    assert out["accelerator"] == "cuda"
    assert out["n_folds"] >= 1
    assert len(out["oos_returns"]) > 0
