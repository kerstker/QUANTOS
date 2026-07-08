"""截面 IC 诊断面板 —— 纯函数核心(无 IO、无 RNG、无墙钟)。

把 tools/crypto_oi_research.py::_rank_ic 的"逐期截面 rank IC"逻辑泛化成可复用、可测的模块:
compute_period_ic_series 给出逐期 IC 序列(而非只回均值),aggregate_ic_series 对序列做标准聚合统计
(mean/ICIR/hit-rate/t-stat/逐年)。signal_from_window_op/signal_composite 是第二组纯函数:把
services/research/signals.py 里的窗口算子应用到一个 FieldPanel 上,产出可直接喂给 compute_period_ic_series
的信号矩阵——carry/crowd_contra 走 alpha_expression 的 DSL evaluate() 路径(它们的真实定义就是 DSL 元组),
trend/单因子基线走这里的窗口算子+截面 z-score 组合路径(它们的真实定义是 (op,window) 对,不是 DSL)。

诚实边界:本模块只做"初筛诊断"的统计,不是晋级门禁——真正的门禁是 services/alpha/evaluator 的
DSR/PBO/walk-forward,本模块的输出不驱动任何自动晋级。
"""
from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from typing import Callable, Optional, Sequence

from services.research.signals import SIGNAL_OPS

Matrix = list  # list[list[Optional[float]]]

_MIN_PAIRS_PER_PERIOD = 4     # matches _rank_ic's threshold
_MIN_PERIODS_FOR_STATS = 10


def _rank(values: list) -> dict:
    """sorted-value -> rank index (matches _rank_ic's tie handling: duplicate values collapse to the
    last occurrence's rank — a deliberate, already-shipped simplification kept for cross-check
    reproducibility against existing crypto_oi_verify.json numbers, not "corrected" to averaged ties)."""
    return {v: i for i, v in enumerate(sorted(values))}


def compute_period_ic_series(signal_matrix: Matrix, price_matrix: Matrix) -> list:
    """逐期截面 Spearman rank IC:period t 的信号 vs price[t+1]/price[t]-1 的截面排名相关。
    少于 _MIN_PAIRS_PER_PERIOD 对有效值该期记 None(诚实,不硬凑)。"""
    T = len(signal_matrix)
    out = []
    for t in range(T - 1):
        pairs = []
        for i in range(len(signal_matrix[t])):
            s = signal_matrix[t][i]
            p0 = price_matrix[t][i] if t < len(price_matrix) else None
            p1 = price_matrix[t + 1][i] if t + 1 < len(price_matrix) else None
            if s is None or p0 is None or p1 is None or p0 <= 0:
                continue
            pairs.append((s, p1 / p0 - 1.0))
        if len(pairs) < _MIN_PAIRS_PER_PERIOD:
            out.append(None)
            continue
        sr = _rank([p[0] for p in pairs])
        rr = _rank([p[1] for p in pairs])
        a = [sr[p[0]] for p in pairs]
        b = [rr[p[1]] for p in pairs]
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5
        db = sum((y - mb) ** 2 for y in b) ** 0.5
        out.append(num / (da * db) if da > 0 and db > 0 else None)
    return out


def compute_horizon_ic_series(signal_matrix: Matrix, price_matrix: Matrix, horizon: int) -> tuple:
    """h 期持有的截面 rank IC:起点 t 的 IC = corr_rank(signal[t], price[t+h]/price[t]-1)。

    抽样 = **非重叠**(起点 t=0,h,2h,…,t+h<T)。这是唯一诚实的口径:重叠收益会让 IC 序列
    自相关、naive t-stat 虚高——所以这里刻意不提供 stride/重叠参数(比 Newey-West 简单、可审计,
    与 crowd_contra 周度重算 t=2.99 的方法论同族)。抽样起点固定 0 → 有相位依赖(确定性、已知局限)。

    "持有期"单位 = 面板自身的周期(当前四个宇宙全是日频面板,h=7≈周度口径),不是绝对日历天。

    返回 (ics, start_indices):start_indices 是每个 IC 的起点行号,调用方用它对齐 event_times
    再喂 aggregate_ic_series 做 per_year。性质(有测试钉死):horizon=1 时 ics 与
    compute_period_ic_series 逐元素**逐位**相等(同一套浮点运算顺序)。每期有效对 <
    _MIN_PAIRS_PER_PERIOD 仍记 None;tie 处理沿用 _rank 的既有简化。"""
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    T = len(signal_matrix)
    ics: list = []
    starts: list = []
    for t in range(0, T - horizon, horizon):
        pairs = []
        for i in range(len(signal_matrix[t])):
            s = signal_matrix[t][i]
            p0 = price_matrix[t][i] if t < len(price_matrix) else None
            p1 = price_matrix[t + horizon][i] if t + horizon < len(price_matrix) else None
            if s is None or p0 is None or p1 is None or p0 <= 0:
                continue
            pairs.append((s, p1 / p0 - 1.0))
        starts.append(t)
        if len(pairs) < _MIN_PAIRS_PER_PERIOD:
            ics.append(None)
            continue
        sr = _rank([p[0] for p in pairs])
        rr = _rank([p[1] for p in pairs])
        a = [sr[p[0]] for p in pairs]
        b = [rr[p[1]] for p in pairs]
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5
        db = sum((y - mb) ** 2 for y in b) ** 0.5
        ics.append(num / (da * db) if da > 0 and db > 0 else None)
    return ics, starts


def aggregate_ic_series(ics: list, event_times: Optional[Sequence[int]] = None) -> dict:
    """逐期 IC 序列 -> 聚合统计。样本不足(n<10)时导出字段全 None(诚实);n_periods 恒是真实计数。"""
    valid = [(i, v) for i, v in enumerate(ics) if v is not None]
    n = len(valid)
    if n < _MIN_PERIODS_FOR_STATS:
        return {"mean_ic": None, "ic_std": None, "icir": None, "hit_rate": None, "t_stat": None,
                "n_periods": n, "per_year": {}}
    vals = [v for _, v in valid]
    mean_ic = statistics.fmean(vals)
    ic_std = statistics.pstdev(vals)
    icir = (mean_ic / ic_std) if ic_std > 0 else None
    hit_rate = sum(1 for v in vals if (v > 0) == (mean_ic > 0)) / n
    t_stat = (mean_ic / (ic_std / math.sqrt(n))) if ic_std > 0 else None

    per_year: dict = {}
    if event_times is not None:
        by_year = defaultdict(list)
        for i, v in valid:
            y = time.gmtime(event_times[i] / 1000).tm_year
            by_year[y].append(v)
        for y, vs in by_year.items():
            per_year[str(y)] = round(statistics.fmean(vs), 4) if len(vs) >= _MIN_PERIODS_FOR_STATS else None

    return {"mean_ic": round(mean_ic, 4), "ic_std": round(ic_std, 4),
            "icir": round(icir, 4) if icir is not None else None,
            "hit_rate": round(hit_rate, 4), "t_stat": round(t_stat, 4) if t_stat is not None else None,
            "n_periods": n, "per_year": per_year}


def signal_from_window_op(panel, op: str, window: int) -> Matrix:
    """把 SIGNAL_OPS[op] 应用到 panel 的 close 矩阵——逐标的累积"到目前为止真实存在的收盘价",
    缺口日(close is None)不追加历史、该格保持 None(不补 None 占位、不用陈旧值裸算)。"""
    fn: Callable = SIGNAL_OPS[op]
    close = panel.matrices["close"]
    T, N = len(panel.event_times), len(panel.instruments)
    out = [[None] * N for _ in range(T)]
    for i in range(N):
        hist: list = []
        for t in range(T):
            c = close[t][i]
            if c is None:
                continue
            hist.append(c)
            out[t][i] = fn(hist, window)
    return out


def signal_composite(panel, specs: list) -> Matrix:
    """多个 (op, window) 子信号 -> 每期截面 z-score -> 平均(复刻 crypto_daily_composite.py 的组合逻辑)。
    某标的当期只要有一个子信号缺失就整体记 None(match `_composite_periods` 的 all-or-nothing 门槛)。"""
    subs = [signal_from_window_op(panel, op, w) for op, w in specs]
    T, N = len(panel.event_times), len(panel.instruments)
    out = [[None] * N for _ in range(T)]
    for t in range(T):
        z_cols = []
        for sub in subs:
            vals = [sub[t][i] for i in range(N)]
            valid = [v for v in vals if v is not None]
            if len(valid) < 3:
                z_cols.append([None] * N)
                continue
            mu, sd = statistics.fmean(valid), statistics.pstdev(valid)
            z_cols.append([(v - mu) / sd if (v is not None and sd > 0) else None for v in vals])
        for i in range(N):
            zs = [col[i] for col in z_cols]
            if any(z is None for z in zs):
                continue
            out[t][i] = sum(zs) / len(zs)
    return out
