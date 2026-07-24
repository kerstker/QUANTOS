"""共享的命名历史情景重演纯函数。"""
from __future__ import annotations

import calendar
import time
from typing import Sequence


CRYPTO_CRISES = [
    ("2021-05 crash", "2021-05-10", "2021-05-31"),
    ("2022-05 LUNA", "2022-05-05", "2022-05-20"),
    ("2022-11 FTX", "2022-11-05", "2022-11-21"),
    ("2023-03 banking/USDC", "2023-03-08", "2023-03-20"),
]


def iso_to_ms(iso: str) -> int:
    return calendar.timegm(time.strptime(iso, "%Y-%m-%d")) * 1000


def _total_return(returns: Sequence[float]) -> float:
    cumulative = 1.0
    for value in returns:
        cumulative *= 1.0 + value
    return cumulative - 1.0


def _max_drawdown(returns: Sequence[float]) -> float:
    peak, equity, drawdown = 1.0, 1.0, 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return drawdown


def window_stats(returns, timestamps, start_ms: int, end_ms: int) -> dict:
    selected = [
        value
        for value, timestamp in zip(returns, timestamps)
        if start_ms <= timestamp <= end_ms
    ]
    if not selected:
        return {
            "n_periods": 0,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "worst_period": 0.0,
        }
    return {
        "n_periods": len(selected),
        "total_return": _total_return(selected),
        "max_drawdown": _max_drawdown(selected),
        "worst_period": min(selected),
    }


def max_drawdown_window(returns, timestamps) -> dict:
    """返回全程最差的峰谷回撤窗口。"""
    peak_equity, equity, max_drawdown = 1.0, 1.0, 0.0
    peak_index = 0
    result = {
        "peak_ts": None,
        "trough_ts": None,
        "drawdown": 0.0,
        "peak_to_trough_periods": 0,
    }
    for index, value in enumerate(returns):
        equity *= 1.0 + value
        if equity > peak_equity:
            peak_equity, peak_index = equity, index
        drawdown = (
            (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        )
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            result = {
                "peak_ts": timestamps[peak_index],
                "trough_ts": timestamps[index],
                "drawdown": drawdown,
                "peak_to_trough_periods": index - peak_index,
            }
    return result
