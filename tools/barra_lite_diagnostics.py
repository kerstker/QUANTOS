"""Read-only Barra-lite factor diagnostics for local research books.

Outputs data/cache/barra_lite_snapshot.json from cached research data only. This
is a diagnostic artifact: it does not alter dry-run weights, risk gates, orders,
broker adapters, approvals, or readiness.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
from collections.abc import Sequence

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.portfolio.barra_lite import (
    barra_lite_report,
    build_barra_lite_model,
    corr_exposure,
    winsorize,
    zscore,
)
from services.research import alpha_expression as ax
from tools.capacity_research import _carry_panel, _weekly_rows
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import QUANTILE as OI_Q, _legs as oi_legs
from tools.crypto_oi_verify import CROWD_CONTRA

_CACHE = os.path.join("data", "cache")
_OUT = os.path.join(_CACHE, "barra_lite_snapshot.json")
_OI = os.path.join(_CACHE, "crypto_oi_metrics.json")


def _finite(value) -> bool:
    return value is not None and math.isfinite(float(value))


def _add_global_ls(panel):
    with open(_OI, encoding="utf-8") as handle:
        oi_cache = json.load(handle)
    oi_data = oi_cache.get("data", oi_cache)
    oi_by_inst = {
        inst: {int(k): v for k, v in rows.items()}
        for inst, rows in oi_data.items()
        if not inst.startswith("_")
    }
    panel.matrices["global_ls"] = [
        [(oi_by_inst.get(inst, {}).get(t) or {}).get("global_ls") for inst in panel.instruments]
        for t in panel.event_times
    ]
    return panel


def _series_stdev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _weekly_returns(panel, rows: Sequence[int]) -> tuple[dict[str, tuple[float, ...]], list[float]]:
    close = panel.matrices["close"]
    returns_by_symbol: dict[str, list[float]] = {inst: [] for inst in panel.instruments}
    market_returns: list[float] = []
    for r0, r1 in zip(rows, rows[1:]):
        row_returns: dict[str, float] = {}
        for j, inst in enumerate(panel.instruments):
            p0 = close[r0][j]
            p1 = close[r1][j]
            if _finite(p0) and _finite(p1) and p0 > 0:
                row_returns[inst] = float(p1) / float(p0) - 1.0
        market = statistics.fmean(row_returns.values()) if row_returns else 0.0
        market_returns.append(market)
        for inst in panel.instruments:
            returns_by_symbol[inst].append(row_returns.get(inst, 0.0))
    return {inst: tuple(vals) for inst, vals in returns_by_symbol.items()}, market_returns


def _book_from_legs(panel, legs: dict[int, float]) -> dict[str, float]:
    return {panel.instruments[i]: float(w) for i, w in legs.items()}


def _latest_non_empty_book(panel, rows, signal, legs_fn, quantile) -> tuple[int, dict[str, float]]:
    for r in reversed(rows):
        legs = legs_fn(signal[r], quantile)
        if legs:
            return int(panel.event_times[r]), _book_from_legs(panel, legs)
    raise ValueError("no non-empty book found")


def _latest_common_books(panel, rows, carry_signal, crowd_signal) -> tuple[int, dict[str, float], dict[str, float]]:
    for r in reversed(rows):
        carry_legs_at_row = carry_legs(carry_signal[r], CARRY_Q)
        crowd_legs_at_row = oi_legs(crowd_signal[r], OI_Q)
        if carry_legs_at_row and crowd_legs_at_row:
            return (
                int(panel.event_times[r]),
                _book_from_legs(panel, carry_legs_at_row),
                _book_from_legs(panel, crowd_legs_at_row),
            )
    raise ValueError("no common non-empty carry/crowd book found")


def _median_dollar_volume(panel, rows: Sequence[int]) -> dict[str, float]:
    close = panel.matrices["close"]
    volume = panel.matrices["volume"]
    out: dict[str, float] = {}
    for j, inst in enumerate(panel.instruments):
        dollars = [
            float(close[r][j]) * float(volume[r][j])
            for r in rows
            if _finite(close[r][j]) and _finite(volume[r][j]) and close[r][j] > 0 and volume[r][j] > 0
        ]
        out[inst] = statistics.median(dollars) if dollars else 0.0
    return out


def _latest_window_mean(panel, field: str, rows: Sequence[int], lookback: int) -> dict[str, float]:
    matrix = panel.matrices[field]
    use_rows = rows[-lookback:]
    out: dict[str, float] = {}
    for j, inst in enumerate(panel.instruments):
        vals = [float(matrix[r][j]) for r in use_rows if _finite(matrix[r][j])]
        if vals:
            out[inst] = statistics.fmean(vals)
    return out


def _factor_exposures(panel, rows: Sequence[int], returns_by_symbol: dict[str, tuple[float, ...]],
                      market_returns: Sequence[float]) -> dict[str, dict[str, float]]:
    beta = {inst: corr_exposure({str(i): v for i, v in enumerate(rs)},
                                {str(i): v for i, v in enumerate(market_returns)})
            for inst, rs in returns_by_symbol.items()}
    volatility = {inst: _series_stdev(rs) for inst, rs in returns_by_symbol.items()}
    funding = _latest_window_mean(panel, "funding", rows, lookback=8)
    crowding = _latest_window_mean(panel, "global_ls", rows, lookback=8)
    liquidity = {inst: math.log1p(v) for inst, v in _median_dollar_volume(panel, rows).items() if v > 0}
    return {
        "carry": zscore(winsorize(funding, limit=0.05)) if funding else {},
        "crowding": zscore(winsorize(crowding, limit=0.05)) if crowding else {},
        "liquidity": zscore(winsorize(liquidity, limit=0.05)) if liquidity else {},
        "market_beta": beta,
        "volatility": zscore(winsorize(volatility, limit=0.05)) if volatility else {},
    }


def _top_weights(weights: dict[str, float], n: int = 8) -> list[dict]:
    return [
        {"symbol": symbol, "weight": weight}
        for symbol, weight in sorted(weights.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:n]
    ]


def _book_snapshot(name: str, weights: dict[str, float], model) -> dict:
    report = barra_lite_report(weights, model)
    return {
        "book": name,
        "n_positions": len(weights),
        "gross_weight": sum(abs(v) for v in weights.values()),
        "net_weight": sum(weights.values()),
        "top_weights": _top_weights(weights),
        "risk_report": report,
    }


def main() -> None:
    panel = _add_global_ls(_carry_panel())
    rows = _weekly_rows(panel.event_times)
    returns_by_symbol, market_returns = _weekly_returns(panel, rows)
    factors = _factor_exposures(panel, rows, returns_by_symbol, market_returns)
    model = build_barra_lite_model(returns_by_symbol, factors)

    carry_signal = ax.evaluate(CARRY_COMPOSITE, panel)
    crowd_signal = ax.evaluate(CROWD_CONTRA, panel)
    common_as_of, carry_weights, crowd_weights = _latest_common_books(panel, rows, carry_signal, crowd_signal)
    combined = {symbol: 0.5 * weight for symbol, weight in carry_weights.items()}
    for symbol, weight in crowd_weights.items():
        combined[symbol] = combined.get(symbol, 0.0) + 0.5 * weight
    combined = {symbol: weight for symbol, weight in combined.items() if abs(weight) > 1e-12}

    snapshot = {
        "provenance": "REAL_LOCAL_DIAGNOSTIC",
        "readiness": "L2_DRY_RUN_ONLY",
        "as_of": common_as_of,
        "model": {
            "n_symbols": len(model.loadings),
            "factor_ids": list(model.factor_ids),
            "n_weekly_return_periods": len(market_returns),
        },
        "books": {
            "carry": _book_snapshot("carry", carry_weights, model),
            "crowd_contra": _book_snapshot("crowd_contra", crowd_weights, model),
            "carry_crowd_equal": _book_snapshot("carry_crowd_equal", combined, model),
        },
        "notes": [
            "Diagnostic only: does not feed evaluator, lifecycle, pre-trade gate, broker, or dry-run weights.",
            "Risk fields are variance components from the existing FactorRiskModel decomposition.",
        ],
    }
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
    print(f"=== Barra-lite diagnostics: {len(model.loadings)} symbols, {len(model.factor_ids)} factors ===", flush=True)
    for name, book in snapshot["books"].items():
        rpt = book["risk_report"]
        print(
            f"  {name:17s} n={book['n_positions']:2d} gross={book['gross_weight']:.3f} "
            f"net={book['net_weight']:+.3f} total_var={rpt['total_risk']:.6g} "
            f"factor_var={rpt['factor_risk']:.6g} specific_var={rpt['specific_risk']:.6g}",
            flush=True,
        )
    print(f"-> wrote {_OUT}", flush=True)


if __name__ == "__main__":
    main()
