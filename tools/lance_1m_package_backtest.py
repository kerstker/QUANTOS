"""Python reproduction of the Lance technical package's lance_backtest.go.

This is a research-only port of the supplied Go source.  It deliberately
keeps the Go implementation's quirks (EMA seed, target condition, fee timing,
trade-stat commission omission, and sliding-window step) so the package claim
can be audited without requiring a Go toolchain or making a network request.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

from tools.lance_1m_research import load_bars

_OUT = Path("data/cache/lance_1m_package_backtest.json")
_YEAR_1M = 60 * 24 * 365

_EXACT = (1765929600000, 1782777600000)  # 2025-12-17 .. 2026-06-30, half-open
_FULL = (1751328000000, 1783555200000)   # 2025-07-01 .. 2026-07-09, half-open
_FEES = (1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0)


def _ema(values: Sequence[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    if not values:
        return out
    out[0] = float(values[0])
    alpha = 2.0 / float(period + 1)
    for i in range(1, len(values)):
        out[i] = alpha * float(values[i]) + (1.0 - alpha) * out[i - 1]
    return out


def _sma(values: Sequence[float], period: int) -> list[float]:
    # Go source returns values[i] before the window is full.
    out = [0.0] * len(values)
    for i in range(len(values)):
        if i < period - 1:
            out[i] = float(values[i])
            continue
        out[i] = sum(values[i - period + 1 : i + 1]) / float(period)
    return out


def _arrays(bars: Sequence) -> tuple[list[float], list[float], list[float], list[float]]:
    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    volumes = [float(b.volume) for b in bars]
    return closes, highs, lows, volumes


def _backtest_one(bars: Sequence, commission_rate: float) -> dict | None:
    n = len(bars)
    if n < 50:
        return None
    closes, highs, lows, volumes = _arrays(bars)
    e20 = _ema(closes, 20)
    volume_mean = _sma(volumes, 20)
    candle_sizes = [(h - l) / c for h, l, c in zip(highs, lows, closes)]
    candle_mean = _sma(candle_sizes, 20)

    cash = 10000.0
    pos = 0.0
    entry_price = 0.0
    stop_price = 0.0
    held = 0
    for i in range(50, n):
        px = closes[i]
        held += 1
        if pos != 0.0:
            trend_now = abs((px - e20[i]) / e20[i])
            trend_prev = abs((closes[i - 1] - e20[i - 1]) / e20[i - 1])
            hit_target = trend_prev > 0.005 and trend_now < 0.003
            quick = pos * (px - entry_price) / entry_price > 0.0015
            stopped = (pos > 0 and lows[i] <= stop_price) or (pos < 0 and highs[i] >= stop_price)
            if hit_target or quick or held >= 20 or stopped:
                exit_price = px
                if stopped:
                    exit_price = stop_price
                gross = pos * (exit_price - entry_price) / entry_price * cash
                commission = abs(pos) * cash * commission_rate
                cash += gross - commission
                pos = 0.0
                held = 0
        if pos == 0.0:
            trend = (px - e20[i]) / e20[i]
            if volumes[i] / volume_mean[i] > 1.3 and abs(trend) > 0.003 and candle_sizes[i] / candle_mean[i] > 1.3:
                if trend < 0:
                    pos = 0.4
                    entry_price = px
                    stop_price = lows[i - 1] * 0.998
                elif trend > 0:
                    pos = -0.4
                    entry_price = px
                    stop_price = highs[i - 1] * 1.002
                cash -= abs(pos) * cash * commission_rate
                held = 0
    if pos != 0.0:
        cash += pos * (closes[-1] - entry_price) / entry_price * cash
    bh_return = (closes[-1] / closes[50] - 1.0) * 10000.0
    return {"alpha": cash - 10000.0 - bh_return}


def _backtest(bars: Sequence, commission_rate: float, *, include_sliding: bool) -> dict:
    n = len(bars)
    closes, highs, lows, volumes = _arrays(bars)
    e20 = _ema(closes, 20)
    volume_mean = _sma(volumes, 20)
    candle_sizes = [(h - l) / c for h, l, c in zip(highs, lows, closes)]
    candle_mean = _sma(candle_sizes, 20)

    cash = 10000.0
    pos = 0.0
    entry_price = 0.0
    stop_price = 0.0
    held = 0
    equity: list[float] = []
    trades: list[dict] = []
    for i in range(50, n):
        px = closes[i]
        held += 1
        if pos != 0.0:
            equity.append(cash + pos * (px - entry_price) / entry_price * cash)
        else:
            equity.append(cash)
        if pos != 0.0:
            trend_now = abs((px - e20[i]) / e20[i])
            trend_prev = abs((closes[i - 1] - e20[i - 1]) / e20[i - 1])
            hit_target = trend_prev > 0.005 and trend_now < 0.003
            quick = pos * (px - entry_price) / entry_price > 0.0015
            stopped = (pos > 0 and lows[i] <= stop_price) or (pos < 0 and highs[i] >= stop_price)
            if hit_target or quick or held >= 20 or stopped:
                exit_price = stop_price if stopped else px
                reason = "STOP" if stopped else "TARGET"
                if not stopped and quick:
                    reason = "QUICK"
                elif not stopped and held >= 20:
                    reason = "TIME"
                gross = pos * (exit_price - entry_price) / entry_price * cash
                commission = abs(pos) * cash * commission_rate
                cash += gross - commission
                # Matches Go Trade.NetPnL: exit commission only; entry fee is
                # already reflected in cash but omitted from this trade stat.
                trades.append({"net_pnl": gross - commission, "reason": reason})
                pos = 0.0
                held = 0
        if pos == 0.0:
            trend = (px - e20[i]) / e20[i]
            if volumes[i] / volume_mean[i] > 1.3 and abs(trend) > 0.003 and candle_sizes[i] / candle_mean[i] > 1.3:
                if trend < 0:
                    pos = 0.4
                    entry_price = px
                    stop_price = lows[i - 1] * 0.998
                elif trend > 0:
                    pos = -0.4
                    entry_price = px
                    stop_price = highs[i - 1] * 1.002
                cash -= abs(pos) * cash * commission_rate
                held = 0
    if pos != 0.0:
        cash += pos * (closes[-1] - entry_price) / entry_price * cash

    returns = [(equity[i] - equity[i - 1]) / equity[i - 1] for i in range(1, len(equity))]
    mean_return = sum(returns) / len(returns) if returns else 0.0
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns) if returns else 0.0
    std_return = math.sqrt(variance)
    sharpe = mean_return / std_return * math.sqrt(_YEAR_1M) if std_return > 0 else 0.0
    peak = equity[0] if equity else cash
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        max_dd = min(max_dd, (value - peak) / peak)
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [abs(t["net_pnl"]) for t in trades if t["net_pnl"] <= 0]
    positive_sum = sum(wins)
    loss_sum = sum(losses)
    sliding = None
    if include_sliding:
        alphas = []
        positives = 0
        total = 0
        for start in range(50, n - 5000, 3000):
            mini = _backtest_one(bars[start : start + 5000], commission_rate)
            if mini is None:
                continue
            total += 1
            alphas.append(mini["alpha"])
            positives += mini["alpha"] > 0
        sorted_alphas = sorted(alphas)
        sliding = {
            "window_size": 5000,
            "step": 3000,
            "n_windows": total,
            "pct_positive": positives / total if total else 0.0,
            "mean_alpha": sum(alphas) / len(alphas) if alphas else 0.0,
            "median_alpha": sorted_alphas[len(sorted_alphas) // 2] if sorted_alphas else 0.0,
        }
    return {
        "n_bars": n,
        "total_return": cash / 10000.0 - 1.0,
        "total_pnl": cash - 10000.0,
        "bh_return": closes[-1] / closes[50] - 1.0,
        "alpha": cash - 10000.0 - (closes[-1] / closes[50] - 1.0) * 10000.0,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": positive_sum / loss_sum if loss_sum else 0.0,
        "trade_count": len(trades),
        "total_cost_note": "Go cash accounting charges entry and exit, but Trade.NetPnL/PF omit entry commission",
        "sliding": sliding,
    }


def main() -> None:
    report = {
        "provenance": "REAL_BINANCE_1M_KLINES_PYTHON_PORT_OF_SUPPLIED_LANCE_BACKTEST_GO",
        "source_archive": "I:/qqFILE/Lance-1m-技术包.zip",
        "source_file": "lance_backtest.go",
        "data_source": "data/cache/crypto_binance_1m.db",
        "limitations": [
            "The supplied Go source fetches Binance directly and does not archive raw responses; this port uses the project's local cache.",
            "The Go fetch loop does not pass endTime and can ingest a final page beyond the requested end; local half-open intervals are kept explicit.",
            "The source backtest has no funding, slippage, latency or real Trusted/Operational health stream.",
        ],
        "periods": {},
    }
    for name, (lo, hi) in (("exact_2025-12-17_2026-06-30", _EXACT), ("full_2025-07-01_2026-07-09", _FULL)):
        bars = load_bars(lo, hi)
        report["periods"][name] = {
            "n_bars": len(bars),
            "by_fee": {
                f"{fee:.1f}bps": _backtest(
                    bars,
                    fee / 10000.0,
                    include_sliding=(fee in (1.0, 2.0) or name.startswith("full")),
                )
                for fee in _FEES
            },
        }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(_OUT), "periods": {k: v["n_bars"] for k, v in report["periods"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
