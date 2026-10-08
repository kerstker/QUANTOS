"""隔离重放 Lance 1m 引擎差异，不改变生产回测引擎。

This driver is deliberately a research-only scenario runner.  It uses the
tested single-asset engine, temporarily swaps the EMA/entry-stop semantics in
memory, and restores the module after every run.  It cannot reproduce Aetos
Trusted/Operational health gates, tick-level stop timing, or the pending/fill
latency without the original Go source and event stream; those are reported as
unknown rather than synthesized.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

import services.research.backtest_engine.single_asset_rule_backtest as engine
from services.research.backtest_engine.costs import CostModel
from tools.lance_1m_research import load_bars

_PERIODS_PER_YEAR_1M = 60 * 24 * 365
_OUT = Path("data/cache/lance_1m_engine_gap_validation.json")

# UTC, half-open intervals.  The exact document window is intentionally kept
# separate from the old project's closest full-month benchmark window.
_PERIODS = {
    "doc_exact_2025-12-17_2026-06-29": (1765929600000, 1782691200000),
    "oos_pre_2025-07-01_2025-12-01": (1751328000000, 1764547200000),
    "oos_post_2026-06-01_2026-07-09": (1780272000000, 1783555200000),
    "full_2025-07-01_2026-07-09": (1751328000000, 1783555200000),
}
_FEES_BPS = (1.0, 2.0, 4.0)


def _go_ema(values: Sequence[float], period: int) -> list:
    """Aetos-style EMA: first close seed, then recursive EMA."""
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out: list = [None] * len(values)
    out[0] = float(values[0])
    for i in range(1, len(values)):
        out[i] = alpha * float(values[i]) + (1.0 - alpha) * out[i - 1]
    return out


def _go_ema_with_50_bar_gate(values: Sequence[float], period: int) -> list:
    """Keep the Go EMA path but enforce len(klines) >= 50 before signals."""
    out = _go_ema(values, period)
    return [v if i >= 49 else None for i, v in enumerate(out)]


def _go_stop_open_factory(bars: Sequence) -> Callable:
    """Store Aetos' previous-bar reference with entry-price stop clamp.

    Aetos computes min(prev_low, entry_price)*(.998) for a long and
    max(prev_high, entry_price)*1.002 for a short.  The tested Python exit
    function already applies the percentage to the stored anchor, so the
    wrapper stores the clamped anchor without touching production code.
    """

    original_open = engine._open_position

    def _open(direction, index, bar, equity, params, cost):
        position = original_open(direction, index, bar, equity, params, cost)
        if index <= 0:
            return position
        reference = bars[index - 1]
        if position.direction == engine.LONG:
            return replace(
                position,
                entry_bar_low=min(reference.low, position.entry_price),
            )
        return replace(
            position,
            entry_bar_high=max(reference.high, position.entry_price),
        )

    return _open


def _professional_exit(original_exit: Callable) -> Callable:
    """Separate 0.3% entry threshold from the 0.15% target exit threshold."""

    def _exit(position, bar, trend, index, params):
        return original_exit(
            position,
            bar,
            trend,
            index,
            replace(params, price_dev_threshold=0.0015),
        )

    return _exit


def _run_scenario(
    bars: Sequence,
    *,
    scenario: str,
    ema: Callable,
    go_stop: bool,
    professional_exit: bool,
    conservative_ohlc: bool,
    fee_bps: float,
) -> dict:
    original_ema = engine._ema
    original_open = engine._open_position
    original_exit = engine._evaluate_exit
    try:
        engine._ema = ema
        if go_stop:
            engine._open_position = _go_stop_open_factory(bars)
        if professional_exit:
            engine._evaluate_exit = _professional_exit(original_exit)
        params = engine.LanceRuleParams(
            conservative_ohlc_ambiguity=conservative_ohlc,
        )
        result = engine.run_single_asset_rule_backtest(
            bars,
            params,
            initial_capital=100_000.0,
            cost=CostModel(fee_bps=fee_bps, slippage_bps=0.0),
        )
        summary = result.summary(periods_per_year=_PERIODS_PER_YEAR_1M)
        return {
            key: summary[key]
            for key in (
                "num_trades",
                "win_rate",
                "win_loss_ratio",
                "profit_factor",
                "total_return",
                "total_net_pnl",
                "total_cost",
                "sharpe",
                "max_drawdown",
                "avg_bars_held",
            )
        }
    finally:
        engine._ema = original_ema
        engine._open_position = original_open
        engine._evaluate_exit = original_exit


def _rolling_stress(
    bars: Sequence,
    *,
    ema: Callable,
    go_stop: bool,
    professional_exit: bool,
    conservative_ohlc: bool,
    fee_bps: float,
    window_size: int = 5000,
) -> dict:
    """Independent non-overlapping stress windows, matching the project driver."""
    sharpes: list[float] = []
    net_pnls: list[float] = []
    positives = 0
    for start in range(0, len(bars) - window_size + 1, window_size):
        summary = _run_scenario(
            bars[start : start + window_size],
            scenario="rolling",
            ema=ema,
            go_stop=go_stop,
            professional_exit=professional_exit,
            conservative_ohlc=conservative_ohlc,
            fee_bps=fee_bps,
        )
        sharpes.append(summary["sharpe"])
        net_pnls.append(summary["total_net_pnl"])
        positives += summary["total_net_pnl"] > 0
    sharpes_sorted = sorted(sharpes)
    pnls_sorted = sorted(net_pnls)
    midpoint = len(sharpes_sorted) // 2
    midpoint_pnl = len(pnls_sorted) // 2
    return {
        "fee_bps": fee_bps,
        "window_size": window_size,
        "n_windows": len(sharpes),
        "pct_positive": positives / len(sharpes) if sharpes else 0.0,
        "mean_sharpe": sum(sharpes) / len(sharpes) if sharpes else 0.0,
        "median_sharpe": (
            sharpes_sorted[midpoint]
            if len(sharpes_sorted) % 2
            else (sharpes_sorted[midpoint - 1] + sharpes_sorted[midpoint]) / 2
        ) if sharpes else 0.0,
        "mean_alpha": sum(net_pnls) / len(net_pnls) if net_pnls else 0.0,
        "median_alpha": (
            pnls_sorted[midpoint_pnl]
            if len(pnls_sorted) % 2
            else (pnls_sorted[midpoint_pnl - 1] + pnls_sorted[midpoint_pnl]) / 2
        ) if net_pnls else 0.0,
    }


def main() -> None:
    scenarios = {
        "python_professional": {
            "description": "Python EMA(SMA20 seed), 20-bar warmup, entry-bar stop, 0.15% target",
            "ema": engine._ema,
            "go_stop": False,
            "professional_exit": True,
            "conservative_ohlc": True,
            "replayability": "REPLAYABLE_PYTHON_CORE",
        },
        "python_professional_prev_stop": {
            "description": "Python EMA(SMA20 seed), previous-bar stop, 0.15% target",
            "ema": engine._ema,
            "go_stop": True,
            "professional_exit": True,
            "conservative_ohlc": True,
            "replayability": "REPLAYABLE_PYTHON_CORE_APPROX_PREV_STOP",
        },
        "go_like_best_case": {
            "description": "Go EMA seed + 50-bar gate + clamped previous-bar stop + 0.15% target",
            "ema": _go_ema_with_50_bar_gate,
            "go_stop": True,
            "professional_exit": True,
            "conservative_ohlc": True,
            "replayability": "GO_LOGIC_APPROX_NO_HEALTH_OR_TICK_STREAM",
        },
        "go_like_literal_ohlc": {
            "description": "Go EMA seed + 50-bar gate + clamped previous-bar stop + literal OHLC order",
            "ema": _go_ema_with_50_bar_gate,
            "go_stop": True,
            "professional_exit": True,
            "conservative_ohlc": False,
            "replayability": "GO_LOGIC_APPROX_NO_HEALTH_OR_TICK_STREAM",
        },
    }

    report = {
        "provenance": "REAL_BINANCE_1M_KLINES_ENGINE_GAP_SCENARIOS",
        "readiness": "L2_DRY_RUN_ONLY",
        "source_limitations": [
            "Aetos Go source is not present in this repository, so no native Go backtest was run.",
            "Trusted/Operational health gate histories are unavailable; scenarios assume all bars are trusted and healthy.",
            "Tick-level stop timing and pending-to-fill latency require trade/quote event data and are not synthesized from OHLC.",
            "Eight-track Aetos capital semantics are not collapsed into a single-account result.",
            "All scenarios use 0 slippage, no funding, and no market-impact model; they are optimistic bounds.",
        ],
        "periods": {},
    }

    for period_name, (lo, hi) in _PERIODS.items():
        bars = load_bars(lo, hi)
        period_report = {"n_bars": len(bars), "by_scenario": {}}
        for scenario_name, spec in scenarios.items():
            fee_report = {}
            for fee_bps in _FEES_BPS:
                fee_report[f"{fee_bps:.1f}bps"] = _run_scenario(
                    bars,
                    scenario=scenario_name,
                    ema=spec["ema"],
                    go_stop=spec["go_stop"],
                    professional_exit=spec["professional_exit"],
                    conservative_ohlc=spec["conservative_ohlc"],
                    fee_bps=fee_bps,
                )
            period_report["by_scenario"][scenario_name] = {
                "description": spec["description"],
                "replayability": spec["replayability"],
                "by_fee": fee_report,
            }
        if period_name == "full_2025-07-01_2026-07-09":
            period_report["rolling_5000"] = {}
            for scenario_name in (
                "python_professional",
                "go_like_best_case",
                "go_like_literal_ohlc",
            ):
                spec = scenarios[scenario_name]
                period_report["rolling_5000"][scenario_name] = {
                    f"{fee_bps:.1f}bps": _rolling_stress(
                        bars,
                        ema=spec["ema"],
                        go_stop=spec["go_stop"],
                        professional_exit=spec["professional_exit"],
                        conservative_ohlc=spec["conservative_ohlc"],
                        fee_bps=fee_bps,
                    )
                    for fee_bps in _FEES_BPS
                }
        report["periods"][period_name] = period_report

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(_OUT), "periods": {k: v["n_bars"] for k, v in report["periods"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
