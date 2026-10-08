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

from quantos.contracts.panel import PanelPeriod

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


def funding_adjusted_periods(
    signal,
    panel,
    *,
    fwd_horizon: int = 1,
    price_field: str = "close",
    funding_field: str = "funding",
    non_overlapping: bool = False,
) -> list[PanelPeriod]:
    """Build evaluator periods on the executable perp payoff.

    A per-name label is the long-position payoff over the holding window:
    ``price[t+h] / price[t] - 1 - sum(funding[t+1:t+h])``.  The panel
    backtester applies signed cross-sectional weights, so short legs receive
    the opposite price and funding payoff automatically.  Every funding cell
    in the holding window is required; a missing/non-finite observation drops
    that instrument instead of being fabricated as zero.
    """
    if isinstance(fwd_horizon, bool) or not isinstance(fwd_horizon, int):
        raise TypeError("fwd_horizon must be an integer")
    if fwd_horizon < 1:
        raise ValueError("fwd_horizon must be positive")
    if price_field not in panel.matrices:
        raise ValueError(f"price authority is missing: {price_field}")
    if funding_field not in panel.matrices:
        raise ValueError(f"funding authority is missing: {funding_field}")

    prices = panel.matrices[price_field]
    funding = panel.matrices[funding_field]
    rows, instruments = _dims(signal)
    periods: list[PanelPeriod] = []
    last_kept_row: int | None = None
    for row in range(rows - fwd_horizon):
        if (
            non_overlapping
            and last_kept_row is not None
            and row - last_kept_row < fwd_horizon
        ):
            continue
        future_row = row + fwd_horizon
        names: list[str] = []
        signals: list[float] = []
        payoffs: list[float] = []
        for column in range(instruments):
            alpha_value = signal[row][column]
            start_price = prices[row][column]
            end_price = prices[future_row][column]
            if (
                alpha_value is None
                or not math.isfinite(alpha_value)
                or start_price is None
                or end_price is None
                or start_price <= 0
            ):
                continue
            rates = [
                funding[accrual_row][column]
                for accrual_row in range(row + 1, future_row + 1)
            ]
            if any(rate is None or not math.isfinite(rate) for rate in rates):
                continue
            names.append(panel.instruments[column])
            signals.append(float(alpha_value))
            payoffs.append(
                float(end_price / start_price - 1.0 - math.fsum(rates))
            )
        if len(names) >= 2:
            periods.append(
                PanelPeriod(
                    tuple(names),
                    tuple(signals),
                    tuple(payoffs),
                    event_time=int(panel.event_times[row]),
                )
            )
            last_kept_row = row
    return periods
