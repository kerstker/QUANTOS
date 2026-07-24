"""A-share daily alpha library — pure functions over trailing windows.

Each factor is a function over one instrument's OWN accumulated valid history
(close/volume/amt_proxy parallel lists, gap days skipped — same accumulation
policy as ic_panel.signal_from_window_op: a suspended day is not appended, never
back-filled). Every factor returns Optional[float]; None propagates (fail-closed).

Cross-sectional helpers (cs_zscore / cs_rank) operate on a single date row —
knowable at t, no look-ahead. Raw + rank versions are both exposed; note that the
Spearman rank IC used downstream is invariant to any per-period monotonic
transform, so raw and rank versions have IDENTICAL rank-IC — the rank version
exists for downstream robust-combination use, not because it changes the IC.

Factors (sign conventions are the RAW definitions; the train-only screener in
combine.py learns the sign from the train IC, never hard-coded here):
  reversal_5d / reversal_20d  — short-term reversal = -ret(window)        (A股微盘经典反转)
  momentum_60d_skip5          — close[t-5]/close[t-60] - 1 (skip the most recent week)
  turnover_20d                — mean(amt_proxy, 20): HONEST PROXY — true turnover needs
                                流通市值 (not in the payload); a LEVEL of trading activity
                                that cross-sectionally ranks like turnover for a narrow
                                20-26.5亿 mcap band. Documented as proxy everywhere.
  volatility_20d              — pstdev of daily simple rets over 20 obs   (低波异象)
  amihud_illiq_20d            — mean(|ret| / amt_proxy, 20)               (Amihud 非流动性)
  volume_ratio_5_20           — mean(vol,5)/mean(vol,20)                  (短期放量)
  max_daily_ret_20d           — max daily ret in 20 obs                   (彩票效应/MAX)
  price_position_52w          — (close - min)/(max - min) over 250 obs    (52周位置)
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# per-instrument window functions (hist = trailing valid observations, oldest->newest)
# ---------------------------------------------------------------------------

def _rets(closes: Sequence[float]) -> list:
    out = []
    for k in range(1, len(closes)):
        if closes[k - 1] > 0:
            out.append(closes[k] / closes[k - 1] - 1.0)
    return out


def reversal_5d(closes, volumes, amts) -> Optional[float]:
    if len(closes) < 6 or closes[-6] <= 0:
        return None
    return -(closes[-1] / closes[-6] - 1.0)


def reversal_20d(closes, volumes, amts) -> Optional[float]:
    if len(closes) < 21 or closes[-21] <= 0:
        return None
    return -(closes[-1] / closes[-21] - 1.0)


def momentum_60d_skip5(closes, volumes, amts) -> Optional[float]:
    if len(closes) < 61 or closes[-61] <= 0:
        return None
    return closes[-6] / closes[-61] - 1.0


def turnover_20d(closes, volumes, amts) -> Optional[float]:
    """PROXY: mean CNY-turnover-proxy level over 20 obs (not amt/流通市值 — see module docstring)."""
    w = amts[-20:]
    if len(w) < 20:
        return None
    return statistics.fmean(w)


def volatility_20d(closes, volumes, amts) -> Optional[float]:
    if len(closes) < 21:
        return None
    r = _rets(closes[-21:])
    return statistics.pstdev(r) if len(r) >= 2 else None


def amihud_illiq_20d(closes, volumes, amts) -> Optional[float]:
    if len(closes) < 21:
        return None
    c, a = closes[-21:], amts[-20:]
    vals = []
    for k in range(1, len(c)):
        if c[k - 1] <= 0 or a[k - 1] <= 0:
            return None
        vals.append(abs(c[k] / c[k - 1] - 1.0) / a[k - 1])
    return statistics.fmean(vals)


def volume_ratio_5_20(closes, volumes, amts) -> Optional[float]:
    if len(volumes) < 20:
        return None
    long_mean = statistics.fmean(volumes[-20:])
    if long_mean <= 0:
        return None
    return statistics.fmean(volumes[-5:]) / long_mean


def max_daily_ret_20d(closes, volumes, amts) -> Optional[float]:
    if len(closes) < 21:
        return None
    r = _rets(closes[-21:])
    return max(r) if r else None


def price_position_52w(closes, volumes, amts) -> Optional[float]:
    w = closes[-250:]
    if len(w) < 250:
        return None
    lo, hi = min(w), max(w)
    if hi <= lo:
        return None
    return (closes[-1] - lo) / (hi - lo)


FACTOR_FUNCS = {
    "reversal_5d": reversal_5d,
    "reversal_20d": reversal_20d,
    "momentum_60d_skip5": momentum_60d_skip5,
    "turnover_20d": turnover_20d,
    "volatility_20d": volatility_20d,
    "amihud_illiq_20d": amihud_illiq_20d,
    "volume_ratio_5_20": volume_ratio_5_20,
    "max_daily_ret_20d": max_daily_ret_20d,
    "price_position_52w": price_position_52w,
}


# ---------------------------------------------------------------------------
# panel-level builders
# ---------------------------------------------------------------------------

def build_factor_matrices(panel, aux: dict, eligible: Optional[list] = None) -> dict:
    """{factor_name: Matrix} of RAW factor values.

    Per instrument, accumulate valid (close, volume, amt) observations day by day
    (gap days skipped, never back-filled); at each date where the instrument is
    eligible (tradable today + >= MIN_LISTED_DAYS listed days) evaluate every factor
    on the trailing valid history. Deterministic, no RNG, no clock.
    """
    close, vol, amt = panel.matrices["close"], panel.matrices["volume"], aux["amt"]
    bad = aux.get("bad_bar")   # 坏 bar(超板块涨跌幅带的跳变)= gap,不进尾部窗口
    T, N = panel.shape
    if eligible is None:
        eligible = [[True] * N for _ in range(T)]
    out = {name: [[None] * N for _ in range(T)] for name in FACTOR_FUNCS}
    for i in range(N):
        hc: list = []
        hv: list = []
        ha: list = []
        for t in range(T):
            c = close[t][i]
            if c is not None and not (bad and bad[t][i]):
                hc.append(c)
                hv.append(vol[t][i] if vol[t][i] is not None else 0.0)
                ha.append(amt[t][i] if amt[t][i] is not None else 0.0)
            if not eligible[t][i]:
                continue
            for name, fn in FACTOR_FUNCS.items():
                v = fn(hc, hv, ha)
                if v is not None and math.isfinite(v):
                    out[name][t][i] = float(v)
    return out


# ---------------------------------------------------------------------------
# cross-sectional transforms (single date row — knowable at t)
# ---------------------------------------------------------------------------

def cs_zscore_row(row: list, min_n: int = 3) -> list:
    valid = [v for v in row if v is not None]
    if len(valid) < min_n:
        return [None] * len(row)
    mu, sd = statistics.fmean(valid), statistics.pstdev(valid)
    if sd <= 0:
        return [None] * len(row)
    return [(v - mu) / sd if v is not None else None for v in row]


def cs_rank_row(row: list, min_n: int = 3) -> list:
    """Fractional rank in [0,1]: (rank)/(n-1); ties get the LAST occurrence's rank —
    the same deliberate simplification as ic_panel._rank, kept for house consistency."""
    idx = [i for i, v in enumerate(row) if v is not None]
    if len(idx) < min_n:
        return [None] * len(row)
    order = {v: r for r, v in enumerate(sorted(row[i] for i in idx))}
    n = len(idx)
    out = [None] * len(row)
    for i in idx:
        out[i] = order[row[i]] / (n - 1)
    return out


def rank_versions(factor_mats: dict) -> dict:
    """{name: rank-Matrix} — per-date cross-sectional fractional rank of each raw factor."""
    out = {}
    for name, m in factor_mats.items():
        out[name] = [cs_rank_row(row) for row in m]
    return out
