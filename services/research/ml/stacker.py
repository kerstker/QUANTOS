"""确定性 stacker:把多因子特征学成一个分数(GBM 抓非线性交互;Ridge 当线性诚实地板)。确定性使用——
线程在导入 numpy/sklearn 之前钉死为 1,固定 random_state,Ridge 用确定性闭式 solver。同输入两次 → 逐位相同。
零下单路径,只产分数。"""
from __future__ import annotations

import os as _os

# 线程必须在 numpy/sklearn 初始化 OpenMP/BLAS 之前钉死,确保确定性(同输入逐位相同)。
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_v, "1")

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

from .accelerator import resolve_accelerator


class Stacker:
    """已拟合的组合器。predict(X) -> 每行一个分数。"""
    def __init__(self, model, *, model_name: str = "unknown", accelerator: str = "cpu") -> None:
        self._model = model
        self.model_name = model_name
        self.accelerator = accelerator

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.predict(np.asarray(X, dtype=float)), dtype=float)


def fit_stacker(X: np.ndarray, y: np.ndarray, *, model: str = "gbm", seed: int = 0,
                accelerator: str = "cpu") -> Stacker:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if accelerator not in {"cpu", "cuda", "auto"}:
        raise ValueError("accelerator must be one of: cpu, cuda, auto")
    if model in {"gbm", "ridge"} and accelerator != "cpu":
        raise ValueError(f"accelerator={accelerator!r} is only supported by model='xgb'")
    if model == "gbm":
        m = HistGradientBoostingRegressor(random_state=seed, early_stopping=False,
                                          max_iter=200, max_depth=3, learning_rate=0.05,
                                          l2_regularization=1.0)
    elif model == "ridge":
        m = Ridge(alpha=1.0, solver="cholesky")               # 确定性闭式解,无 RNG
    elif model == "xgb":
        active = resolve_accelerator(accelerator)
        try:
            import xgboost as xgb
        except Exception as exc:  # optional dependency; keep the CPU path importable
            raise RuntimeError("model='xgb' requires the optional research-ml-gpu dependency") from exc
        # Fixed seed, one worker, and no subsampling keep this an isolated,
        # reproducible research comparison rather than an online decision path.
        m = xgb.XGBRegressor(
            objective="reg:squarederror",
            n_estimators=200,
            max_depth=3,
            learning_rate=0.05,
            reg_lambda=1.0,
            tree_method="hist",
            device=active,
            random_state=seed,
            n_jobs=1,
            subsample=1.0,
            colsample_bytree=1.0,
            verbosity=0,
        )
        m.fit(X, y, verbose=False)
        return Stacker(m, model_name=model, accelerator=active)
    else:
        raise ValueError(f"unknown stacker model: {model!r} (expected 'gbm', 'ridge' or 'xgb')")
    m.fit(X, y)
    return Stacker(m, model_name=model, accelerator="cpu")
