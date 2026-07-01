"""收益流组合(分散验证)—— 把两条不同宇宙/网格的边收益流按 ISO 年-周对齐,再等权/逆波动组合。纯函数、
确定性、无 IO/RNG/墙钟/网络。用于检验「宏观 regime 画像互补的两条边合起来是否真分散」。"""
from __future__ import annotations

import statistics
import time
from typing import Sequence


def _iso_week(ms: int) -> str:
    tm = time.gmtime(ms / 1000)
    iso = time.strftime("%G-W%V", tm)     # ISO 年-周
    return iso


def align_on_common_weeks(a_ret: Sequence[float], a_ts: Sequence[int],
                          b_ret: Sequence[float], b_ts: Sequence[int]) -> dict:
    """两条 (returns, timestamps_ms) 按 ISO 年-周对齐:同周多值取首个;仅保留两边都有的周,按周排序。
    返回 {weeks, a, b, ts}(ts=该周 a 的原始 ms,供后续 regime 对齐)。"""
    am, amts = {}, {}
    for r, t in zip(a_ret, a_ts):
        w = _iso_week(t)
        if w not in am:
            am[w], amts[w] = r, t
    bm = {}
    for r, t in zip(b_ret, b_ts):
        w = _iso_week(t)
        if w not in bm:
            bm[w] = r
    weeks = sorted(set(am) & set(bm))
    return {"weeks": weeks, "a": [am[w] for w in weeks], "b": [bm[w] for w in weeks],
            "ts": [amts[w] for w in weeks]}


def inverse_vol_weights(series_list: Sequence[Sequence[float]]) -> list:
    """逆波动权重 ∝ 1/pstdev,归一化到和=1。任一条零波动/太短 → 等权回退。"""
    n = len(series_list)
    if n == 0:
        return []
    vols = []
    for s in series_list:
        vols.append(statistics.pstdev(s) if len(s) >= 2 else 0.0)
    if any(v <= 0 for v in vols):
        return [1.0 / n] * n
    inv = [1.0 / v for v in vols]
    tot = sum(inv)
    return [x / tot for x in inv]


def rolling_inverse_vol_weights(sleeve_returns: Sequence[Sequence[float]], end_idx: int,
                                lookback: int, min_history: int = 4) -> list:
    """PIT 风险平衡:在时点 end_idx 用**仅过去**的 trailing 窗口 `[end_idx-lookback, end_idx)` 给各 sleeve
    逆波动权重(归一到和=1)。可用历史 < min_history → 等权回退。end_idx 处**不含未来**(切片到 end_idx)。"""
    n = len(sleeve_returns)
    if n == 0:
        return []
    lo = max(0, end_idx - lookback)
    windows = [list(s[lo:end_idx]) for s in sleeve_returns]
    if min(len(w) for w in windows) < min_history:
        return [1.0 / n] * n
    return inverse_vol_weights(windows)


def combine_returns(series_list: Sequence[Sequence[float]], weights: Sequence[float]) -> list:
    """逐期加权和 Σ w_i·r_i(所有序列等长;长度不齐 → ValueError)。"""
    if not series_list:
        return []
    m = len(series_list[0])
    if any(len(s) != m for s in series_list) or len(weights) != len(series_list):
        raise ValueError("series must be equal-length and match weights")
    return [sum(w * s[k] for w, s in zip(weights, series_list)) for k in range(m)]
