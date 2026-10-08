"""Pure helpers for point-in-time CFTC positioning research.

The caller supplies already-normalized observations and daily close series.  A
signal is never allowed to enter on a bar whose timestamp is equal to its
knowledge timestamp: the first strictly later close is the entry anchor.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
import math
import statistics


DAY_MS = 86_400_000


def trailing_zscores(values, *, window: int) -> list[float | None]:
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError("window must be an integer >= 2")
    normalized = []
    for value in values:
        if value is None:
            normalized.append(None)
            continue
        number = float(value)
        normalized.append(number if math.isfinite(number) else None)
    out: list[float | None] = []
    for index, value in enumerate(normalized):
        start = index - window + 1
        if start < 0 or value is None:
            out.append(None)
            continue
        sample = normalized[start:index + 1]
        if any(item is None for item in sample):
            out.append(None)
            continue
        finite = [float(item) for item in sample]
        deviation = statistics.pstdev(finite)
        out.append(None if deviation <= 0.0 else (value - statistics.fmean(finite)) / deviation)
    return out


def _price_arrays(prices):
    rows = sorted((int(timestamp), float(value)) for timestamp, value in prices)
    if not rows:
        return (), (), {}
    if len({timestamp for timestamp, _value in rows}) != len(rows):
        raise ValueError("price timestamps must be unique")
    if any(not math.isfinite(value) or value <= 0.0 for _timestamp, value in rows):
        raise ValueError("prices must be finite and positive")
    times = tuple(timestamp for timestamp, _value in rows)
    values = tuple(value for _timestamp, value in rows)
    return times, values, dict(rows)


def align_forward_returns(
    observations,
    prices,
    *,
    benchmark_prices=None,
    horizon_days: int = 7,
) -> list[dict]:
    """Align signal knowledge times to strictly-future close-to-close returns."""
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days <= 0:
        raise ValueError("horizon_days must be a positive integer")
    times, values, _by_time = _price_arrays(prices)
    if not times:
        return []
    benchmark_by_time = None
    if benchmark_prices is not None:
        _bench_times, _bench_values, benchmark_by_time = _price_arrays(benchmark_prices)
    aligned = []
    for knowledge_time, signal in sorted(observations):
        signal = float(signal)
        if not math.isfinite(signal):
            continue
        entry_index = bisect_right(times, int(knowledge_time))
        if entry_index >= len(times):
            continue
        entry_time = times[entry_index]
        exit_index = bisect_left(times, entry_time + horizon_days * DAY_MS)
        if exit_index >= len(times):
            continue
        exit_time = times[exit_index]
        asset_leg_return = values[exit_index] / values[entry_index] - 1.0
        row = {
            "knowledge_time_ms": int(knowledge_time),
            "entry_time_ms": entry_time,
            "exit_time_ms": exit_time,
            "signal": signal,
            "asset_leg_return": asset_leg_return,
        }
        if benchmark_by_time is None:
            row["asset_return"] = asset_leg_return
        else:
            if entry_time not in benchmark_by_time or exit_time not in benchmark_by_time:
                continue
            benchmark_return = benchmark_by_time[exit_time] / benchmark_by_time[entry_time] - 1.0
            row["benchmark_return"] = benchmark_return
            row["asset_return"] = (
                (1.0 + asset_leg_return) / (1.0 + benchmark_return) - 1.0
            )
        aligned.append(row)
    return aligned


def _max_drawdown(returns) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
    return worst


def evaluate_positions(aligned, *, cost_bps: float, periods_per_year: int = 52) -> dict:
    if not math.isfinite(float(cost_bps)) or cost_bps < 0:
        raise ValueError("cost_bps must be finite and non-negative")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    gross_returns = []
    net_returns = []
    turnovers = []
    per_year: dict[str, list[float]] = {}
    side_returns = {"long": [], "short": []}
    previous = 0.0
    for row in aligned:
        position = max(-1.0, min(1.0, float(row["signal"])))
        realized = float(row["asset_return"])
        if not math.isfinite(position) or not math.isfinite(realized):
            continue
        turnover = abs(position - previous)
        gross = position * realized
        net = gross - turnover * float(cost_bps) / 10_000.0
        gross_returns.append(gross)
        net_returns.append(net)
        turnovers.append(turnover)
        year = "1970"
        try:
            year = str(datetime.fromtimestamp(row["entry_time_ms"] / 1000, timezone.utc).year)
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            pass
        per_year.setdefault(year, []).append(net)
        if position > 0:
            side_returns["long"].append(net)
        elif position < 0:
            side_returns["short"].append(net)
        previous = position
    n = len(net_returns)
    if n > 1:
        deviation = statistics.stdev(net_returns)
        mean = statistics.fmean(net_returns)
        t_stat = 0.0 if deviation <= 0.0 else mean / (deviation / math.sqrt(n))
        sharpe = 0.0 if deviation <= 0.0 else mean / deviation * math.sqrt(periods_per_year)
    else:
        mean = t_stat = sharpe = 0.0
    return {
        "n_periods": n,
        "mean_net_return": mean,
        "t_stat": t_stat,
        "net_sharpe": sharpe,
        "total_return": math.prod(1.0 + value for value in net_returns) - 1.0 if n else 0.0,
        "max_drawdown": _max_drawdown(net_returns),
        "turnover": sum(turnovers),
        "average_turnover": statistics.fmean(turnovers) if turnovers else 0.0,
        "total_cost": sum(turnovers) * float(cost_bps) / 10_000.0,
        "per_year_mean": {
            year: statistics.fmean(values) for year, values in sorted(per_year.items())
        },
        "side_mean": {
            side: (statistics.fmean(values) if values else None)
            for side, values in side_returns.items()
        },
        "gross_returns": gross_returns,
        "net_returns": net_returns,
    }


__all__ = [
    "DAY_MS", "align_forward_returns", "evaluate_positions", "trailing_zscores",
]
