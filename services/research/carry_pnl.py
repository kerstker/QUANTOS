"""Economic (carry-aware) return series for a cross-sectional perp book (Alpha Factory — real P&L).

The panel backtest measures the PRICE return of a dollar-neutral long/short book. A perp carry book
ALSO COLLECTS the funding: a position of signed weight w pays w*funding, so it EARNS -w*funding.
Longing negative-funding names and shorting positive-funding names therefore receives funding on BOTH
legs. The honest economic per-period return is:

    economic = price_LS_return  +  funding_collected   (fees are applied by the caller/cost model)

PIT-safe by construction: the SIGNAL (the alpha matrix) only reads data <= t; the realized P&L over the
holding period uses price[t+h] and funding[t+h] (the outcome, not the signal). Mirrors the validated
tools/crypto_carry_strategy.py logic, as a reusable, tested service primitive. Pure stdlib, deterministic.
"""
from __future__ import annotations

import math
from typing import Sequence

from .alpha_expression import _dims


def _legs(signal_row: Sequence, valid: Sequence[int], quantile: float) -> dict:
    """Dollar-neutral weights: long the top-`quantile` of the signal, short the bottom. {} if too thin."""
    idx = sorted(valid, key=lambda i: signal_row[i])
    if len(idx) < 4:
        return {}
    k = max(1, int(round(len(idx) * quantile)))
    longs, shorts = idx[-k:], idx[:k]
    w: dict = {}
    for i in longs:
        w[i] = 0.5 / len(longs)
    for i in shorts:
        w[i] = w.get(i, 0.0) - 0.5 / len(shorts)
    return w


def economic_return_series(signal, panel, *, fwd_horizon: int = 1, quantile: float = 0.2,
                           with_funding: bool = True, price_field: str = "close") -> list:
    """Per-period economic net return of the dollar-neutral L/S book implied by `signal`:
    price return + (when `with_funding` and the panel carries "funding") funding collected. A period
    that cannot be priced contributes 0.0. Deterministic; PIT-safe."""
    px = panel.matrices[price_field]
    fund = panel.matrices.get("funding") if with_funding else None
    t, n = _dims(signal)
    if not (0.0 < quantile <= 1.0):                       # fail-closed: an out-of-range quantile silently
        raise ValueError(f"quantile must be in (0, 1], got {quantile}")  # collapsed the book to all-zero
    out: list = []
    for r in range(t - fwd_horizon):
        rf = r + fwd_horizon
        row = signal[r]
        valid = [i for i in range(n)
                 if row[i] is not None and math.isfinite(row[i])
                 and px[r][i] is not None and px[r][i] > 0 and px[rf][i] is not None]
        w = _legs(row, valid, quantile)
        if not w:
            out.append(0.0)
            continue
        ret = 0.0
        for i, wi in w.items():
            ret += wi * (px[rf][i] / px[r][i] - 1.0)               # price leg
            if fund is not None:
                f1 = fund[rf][i]
                if f1 is not None:
                    ret += -wi * f1                                # funding collected over the holding day
        out.append(ret)
    return out
