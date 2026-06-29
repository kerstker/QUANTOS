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


class Stacker:
    """已拟合的组合器。predict(X) -> 每行一个分数。"""
    def __init__(self, model) -> None:
        self._model = model

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.predict(np.asarray(X, dtype=float)), dtype=float)


def fit_stacker(X: np.ndarray, y: np.ndarray, *, model: str = "gbm", seed: int = 0) -> Stacker:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if model == "gbm":
        m = HistGradientBoostingRegressor(random_state=seed, early_stopping=False,
                                          max_iter=200, max_depth=3, learning_rate=0.05,
                                          l2_regularization=1.0)
    elif model == "ridge":
        m = Ridge(alpha=1.0, solver="cholesky")               # 确定性闭式解,无 RNG
    else:
        raise ValueError(f"unknown stacker model: {model!r} (expected 'gbm' or 'ridge')")
    m.fit(X, y)
    return Stacker(m)
