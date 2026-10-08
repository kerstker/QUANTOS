"""Read-only research driver validating docs/test/lance_1m_strategy.md against real 1-minute
BTC bars (tools/binance_klines_1m_fetch.py -> data/cache/crypto_binance_1m.db), using the
tested pure-function engine at services/research/backtest_engine/single_asset_rule_backtest.py.

Not a production/gate component: a gate-external research script, mirroring the convention of
other tools/*_research.py drivers (e.g. tools/ic_panel_research.py) — reads local cache, prints a
report, writes a JSON snapshot. No network, no trading, no lifecycle/promotion involvement.

Methodology (mirrors how this repo validates every other candidate signal — see CLAUDE.md's
"先怀疑、后相信" posture): reproduce the document's own claimed window as a sanity check on this
engine's correctness, then evaluate genuinely held-out data the document could not have seen,
across a realistic fee sweep (matching the doc's own named breakpoints), a rolling-window stress
test, and parameter sensitivity — not just the single default-parameter, single-window number the
document reports.
"""
from __future__ import annotations

import json
import sqlite3
import statistics

from quantos.contracts.data import Bar
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.single_asset_rule_backtest import (LanceRuleParams,
                                                                          run_single_asset_rule_backtest)

_DB = "data/cache/crypto_binance_1m.db"
_PERIODS_PER_YEAR_1M = 60 * 24 * 365   # 24/7 1-minute bar calendar, matches BTCUSDT context

# doc-labeled window boundaries (epoch ms, UTC) -- see module docstring: the doc's own claimed bar
# count (138,755) does not match a clean full-months slice of this label (262,080 bars for the full
# 2025-12~2026-06 span) -- kept as the closest continuous full-months interval to their label, not
# claimed to be an exact reproduction.
_DOC_WINDOW = (1764547200000, 1780272000000)          # 2025-12-01 .. 2026-06-01
_OOS_PRE = (1751328000000, 1764547200000)              # 2025-07-01 .. 2025-12-01 (before doc's window)
_OOS_POST = (1780272000000, 1783555200000)             # 2026-06-01 .. 2026-07-09 (after doc's window)
_FULL = (1751328000000, 1783555200000)


def load_bars(lo: int, hi: int) -> list:
    conn = sqlite3.connect(_DB)
    cur = conn.cursor()
    cur.execute("SELECT payload_json FROM bars WHERE event_time >= ? AND event_time < ? "
               "ORDER BY event_time", (lo, hi))
    bars = []
    for (pj,) in cur.fetchall():
        d = json.loads(pj)
        bars.append(Bar(instrument_id=d["instrument_id"], event_time=d["event_time"],
                        knowledge_time=d["knowledge_time"], open=d["open"], high=d["high"],
                        low=d["low"], close=d["close"], volume=d["volume"]))
    conn.close()
    return bars


def run(bars: list, *, fee_bps: float, params: LanceRuleParams = LanceRuleParams()) -> dict:
    result = run_single_asset_rule_backtest(
        bars, params, initial_capital=100_000.0,
        cost=CostModel(fee_bps=fee_bps, slippage_bps=0.0),
    )
    s = result.summary(periods_per_year=_PERIODS_PER_YEAR_1M)
    s["n_bars"] = len(bars)
    return s


def fee_sweep(bars: list, label: str, params: LanceRuleParams = LanceRuleParams()) -> dict:
    out = {}
    for fee_bps in (1.0, 2.0, 5.0, 8.0, 10.0):
        out[f"{fee_bps}bps"] = run(bars, fee_bps=fee_bps, params=params)
    return {"label": label, "n_bars": len(bars), "by_fee": out}


def rolling_window_stress(bars: list, *, window: int = 5000, fee_bps: float = 5.0) -> dict:
    """Mirrors the doc's own §10 methodology ('168 sliding windows, 5000 bars each') but applied
    to this independently-fetched dataset, at a realistic mid-range fee rather than the doc's
    optimistic 1bps."""
    sharpes, positives, alphas = [], 0, []
    n_windows = max(0, (len(bars) - window) // window + 1)
    for i in range(n_windows):
        chunk = bars[i * window: i * window + window]
        if len(chunk) < window:
            break
        s = run(chunk, fee_bps=fee_bps)
        sharpes.append(s["sharpe"])
        alphas.append(s["total_net_pnl"])
        if s["total_net_pnl"] > 0:
            positives += 1
    return {
        "fee_bps": fee_bps, "window_size": window, "n_windows": len(sharpes),
        "pct_positive": (positives / len(sharpes)) if sharpes else 0.0,
        "mean_sharpe": statistics.fmean(sharpes) if sharpes else 0.0,
        "median_sharpe": statistics.median(sharpes) if sharpes else 0.0,
        "mean_alpha": statistics.fmean(alphas) if alphas else 0.0,
        "median_alpha": statistics.median(alphas) if alphas else 0.0,
    }


def parameter_sensitivity(bars: list, *, fee_bps: float = 5.0) -> dict:
    """+/-30% single-parameter sweep on the doc's own named tunable thresholds (§8), matching
    the doc's own claimed robustness check ('参数 ±30% 全正') -- at a realistic fee, not 1bps."""
    base = LanceRuleParams()
    targets = {
        "vol_ratio_threshold": base.vol_ratio_threshold,
        "price_dev_threshold": base.price_dev_threshold,
        "candle_spike_threshold": base.candle_spike_threshold,
        "position_pct": base.position_pct,
    }
    out = {}
    for name, base_val in targets.items():
        row = {}
        for mult, tag in ((0.7, "-30%"), (1.0, "base"), (1.3, "+30%")):
            params = LanceRuleParams(**{**base.__dict__, name: base_val * mult})
            row[tag] = run(bars, fee_bps=fee_bps, params=params)["sharpe"]
        out[name] = row
    return out


def ohlc_ambiguity_comparison(bars: list, *, fee_bps: float = 1.0) -> dict:
    """How much does this module's own conservative same-bar-ambiguity tie-break (see
    single_asset_rule_backtest.py docstring) matter vs. the doc's literal §9 if/elif order with
    no override -- run both on the doc-labeled window at the doc's own 1bps assumption."""
    conservative = run(bars, fee_bps=fee_bps,
                       params=LanceRuleParams(conservative_ohlc_ambiguity=True))
    literal = run(bars, fee_bps=fee_bps,
                  params=LanceRuleParams(conservative_ohlc_ambiguity=False))
    return {"conservative_ohlc_ambiguity": conservative, "doc_literal_no_override": literal}


def main() -> None:
    print("Loading bars from", _DB, "...")
    doc_window_bars = load_bars(*_DOC_WINDOW)
    oos_pre_bars = load_bars(*_OOS_PRE)
    oos_post_bars = load_bars(*_OOS_POST)
    full_bars = load_bars(*_FULL)
    print(f"doc-window: {len(doc_window_bars)} bars, oos-pre: {len(oos_pre_bars)} bars, "
         f"oos-post: {len(oos_post_bars)} bars, full: {len(full_bars)} bars")

    report = {
        "provenance": "REAL_BINANCE_1M_KLINES_INDEPENDENT_VALIDATION",
        "engine": "services.research.backtest_engine.single_asset_rule_backtest",
        "doc": "docs/test/lance_1m_strategy.md",
        "fee_sweep": {
            "doc_labeled_window_2025-12_2026-06": fee_sweep(doc_window_bars, "doc-labeled window"),
            "oos_pre_2025-07_2025-12": fee_sweep(oos_pre_bars, "OOS pre-window"),
            "oos_post_2026-06_2026-07-09": fee_sweep(oos_post_bars, "OOS post-window"),
            "full_2025-07_2026-07-09": fee_sweep(full_bars, "full 13-month span"),
        },
        "rolling_window_stress_full_dataset_5bps": rolling_window_stress(full_bars, fee_bps=5.0),
        "rolling_window_stress_full_dataset_1bps": rolling_window_stress(full_bars, fee_bps=1.0),
        "parameter_sensitivity_5bps": parameter_sensitivity(full_bars, fee_bps=5.0),
        "ohlc_ambiguity_comparison_doc_window_1bps": ohlc_ambiguity_comparison(doc_window_bars, fee_bps=1.0),
    }
    with open("data/cache/lance_1m_validation.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print("Wrote data/cache/lance_1m_validation.json")
    print(json.dumps(report["fee_sweep"]["doc_labeled_window_2025-12_2026-06"]["by_fee"]["1.0bps"], indent=2))


if __name__ == "__main__":
    main()
