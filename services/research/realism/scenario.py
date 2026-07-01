"""历史情景重演(现实主义引擎 RE-2)—— 把一条 book 的逐期收益拉过命名危机窗口 + 找全程最差回撤窗口。纯函数、
确定性、无 IO/RNG/墙钟。回答「这条边在真实危机里怎么表现」。"""
from __future__ import annotations

import calendar
import time
from typing import Sequence

# 仅 crypto 数据(2020-06 起)覆盖到的真实危机窗口
CRYPTO_CRISES = [("2021-05 crash", "2021-05-10", "2021-05-31"),
                 ("2022-05 LUNA", "2022-05-05", "2022-05-20"),
                 ("2022-11 FTX", "2022-11-05", "2022-11-21"),
                 ("2023-03 banking/USDC", "2023-03-08", "2023-03-20")]


def iso_to_ms(iso: str) -> int:
    return calendar.timegm(time.strptime(iso, "%Y-%m-%d")) * 1000


def _total_return(rs: Sequence[float]) -> float:
    cum = 1.0
    for r in rs:
        cum *= (1.0 + r)
    return cum - 1.0


def _max_drawdown(rs: Sequence[float]) -> float:
    peak, eq, mdd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return mdd


def window_stats(returns, timestamps, start_ms: int, end_ms: int) -> dict:
    sel = [r for r, t in zip(returns, timestamps) if start_ms <= t <= end_ms]
    if not sel:
        return {"n_periods": 0, "total_return": 0.0, "max_drawdown": 0.0, "worst_period": 0.0}
    return {"n_periods": len(sel), "total_return": _total_return(sel),
            "max_drawdown": _max_drawdown(sel), "worst_period": min(sel)}


def max_drawdown_window(returns, timestamps) -> dict:
    """全程最差回撤峰→谷。peak_ts=回撤前净值最高点的期,trough_ts=谷底期。"""
    peak_eq, eq, mdd = 1.0, 1.0, 0.0
    peak_i, best = 0, {"peak_ts": None, "trough_ts": None, "drawdown": 0.0, "peak_to_trough_periods": 0}
    for i, r in enumerate(returns):
        eq *= (1.0 + r)
        if eq > peak_eq:
            peak_eq, peak_i = eq, i
        dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0.0
        if dd > mdd:
            mdd = dd
            best = {"peak_ts": timestamps[peak_i], "trough_ts": timestamps[i], "drawdown": dd,
                    "peak_to_trough_periods": i - peak_i}
    return best
