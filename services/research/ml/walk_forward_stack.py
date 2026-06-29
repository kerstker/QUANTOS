"""无泄漏的滚动 stacking + 截面多空 OOS。每折:用 period < test_start-embargo 的行拟合 stacker → 预测测试期
各行分数 → 同期截面 rank-去均值-gross1 多空权重 → 该期组合收益 = w·forward_return。拼成 OOS 收益序列。
purged+embargo 保证测试块不入训练。确定性、纯编排。"""
from __future__ import annotations

import numpy as np

from .feature_panel import FeaturePanel
from .stacker import fit_stacker


def _ls_weights(scores: np.ndarray) -> np.ndarray:
    """截面分数 -> 多空权重:rank 去均值后归一到 gross=1(sum|w|=1)。单标的或全相等 -> 全零(无头寸)。"""
    n = len(scores)
    if n < 2:
        return np.zeros(n)
    order = np.argsort(np.argsort(scores)).astype(float)       # ranks 0..n-1, deterministic
    w = order - order.mean()
    s = np.abs(w).sum()
    return w / s if s > 0 else np.zeros(n)


def walk_forward_stack(panel: FeaturePanel, *, train_size: int, test_size: int, embargo: int = 1,
                       model: str = "gbm", seed: int = 0, cost_bps: float = 0.0) -> dict:
    """oos_returns 为 NET:每期组合收益减换手成本 = (cost_bps/1e4) * sum|w_p - w_{p-1}|(逐标的对齐,缺失记
    0 权重)。cost_bps=0(默认)-> net==gross,与既有调用/测试一致。"""
    rows = panel.rows
    if not rows:
        return {"oos_returns": [], "n_folds": 0, "n_test_periods": 0}
    periods = sorted({i for i, _ in rows})
    by_period = {p: np.array([k for k, (i, _) in enumerate(rows) if i == p]) for p in periods}
    cost_frac = cost_bps / 1e4
    oos, n_folds, test_start = [], 0, train_size
    prev_w: dict = {}                                          # instrument -> weight,跨连续测试期对齐算换手
    while test_start < len(periods):
        test_ps = periods[test_start: test_start + test_size]
        if not test_ps:
            break
        train_cutoff = test_ps[0] - embargo                    # purge: train periods strictly before cutoff
        train_idx = np.concatenate([by_period[p] for p in periods if p < train_cutoff]) \
            if any(p < train_cutoff for p in periods) else np.array([], dtype=int)
        if train_idx.size >= 2 * panel.X.shape[1] + 5:         # enough rows to fit
            st = fit_stacker(panel.X[train_idx], panel.y[train_idx], model=model, seed=seed)
            n_folds += 1
            for p in test_ps:
                idx = by_period[p]
                w = _ls_weights(st.predict(panel.X[idx]))
                curr_w = {panel.rows[k][1]: float(wi) for k, wi in zip(idx, w)}
                turnover = sum(abs(curr_w.get(i, 0.0) - prev_w.get(i, 0.0))
                               for i in set(curr_w) | set(prev_w))
                gross = float(np.dot(w, panel.y[idx]))
                oos.append(gross - cost_frac * turnover)       # NET of turnover cost
                prev_w = curr_w
        test_start += test_size
    return {"oos_returns": oos, "n_folds": n_folds, "n_test_periods": len(oos)}
