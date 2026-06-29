"""把因子池的多份 PanelPeriod 按 (period_index, instrument) 对齐成 stacker 的特征矩阵 X / 目标 y。
特征 = 各因子在决策时的截面信号;y = 该 (期,标的) 的前向收益(因子无关,取任一因子的值)。inner-join:
只保留所有因子都有值的 (期,标的)(fail-soft 丢缺行)。纯 numpy 数据整形,无 RNG / 无墙钟。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from services.alpha.panel_backtest import PanelPeriod


@dataclass(frozen=True)
class FeaturePanel:
    X: np.ndarray            # (n_rows, n_factors)
    y: np.ndarray            # (n_rows,)
    rows: list               # list[tuple[int, str]]  -> (period_index, instrument)
    factor_names: list       # list[str], 与 X 列一一对应


def build_feature_panel(factor_periods: Mapping[str, Sequence[PanelPeriod]]) -> FeaturePanel:
    names = sorted(factor_periods)
    if not names:
        return FeaturePanel(np.empty((0, 0)), np.empty((0,)), [], [])
    n_periods = min(len(factor_periods[n]) for n in names)
    # per factor per period: instrument -> (signal, forward_return)
    lut = {n: [{inst: (s, fr) for inst, s, fr in zip(p.instruments, p.signals, p.forward_returns)}
               for p in list(factor_periods[n])[:n_periods]] for n in names}
    rows, X_rows, y_vals = [], [], []
    for i in range(n_periods):
        # instruments present in EVERY factor at period i (inner-join), sorted for determinism
        common = set.intersection(*[set(lut[n][i]) for n in names]) if names else set()
        for inst in sorted(common):
            X_rows.append([lut[n][i][inst][0] for n in names])
            y_vals.append(lut[names[0]][i][inst][1])           # forward return is factor-independent
            rows.append((i, inst))
    X = np.asarray(X_rows, dtype=float) if X_rows else np.empty((0, len(names)))
    y = np.asarray(y_vals, dtype=float)
    return FeaturePanel(X=X, y=y, rows=rows, factor_names=names)
