"""Dollar-neutral cross-sectional long/short panel backtest (Phase 6, critique P0-4).

The single-instrument engine uses integer shares on a per-leg $1M base — which
drops cheap-but-high-signal names and is not dollar-neutral. This computes the
long/short book directly with NOTIONAL weights (fractional) on ONE shared
capital base, dollar-neutral (equal long & short notional), net of fee +
slippage + per-name sqrt market impact (so capacity actually bites). Deterministic.
Net return decomposes exactly: net = gross - cost (reconciles per period).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from quantos.contracts.cost import CostModel


@dataclass(frozen=True)
class PanelPeriod:
    instruments: tuple[str, ...]
    signals: tuple[float, ...]            # cross-sectional alpha signal
    forward_returns: tuple[float, ...]    # next-period return per instrument
    adv_notional: tuple[float, ...] = ()  # per-name ADV (for impact); optional
    # 2026-07-09 (real-data bug, see services/alpha/factory.py::batch_pbo docstring for full
    # coverage list): the panel's own event_time for this period, when the caller has it.
    # Optional/backward-compatible (None only for hand-built PanelPeriods in tests/scratch code
    # with no real calendar to draw from) — every real PanelPeriod-construction site populates it.
    # Exists so batch_pbo can align candidates by CALENDAR time instead of by list position when
    # candidates have different valid-period histories (the normal case, not the exception, once
    # real factors have different data-availability windows).
    event_time: Optional[int] = None
    # ADV for names that were held in the previous period but have left the
    # current signal cross-section. This lets dynamic-universe liquidation pay
    # impact and prove participation instead of disappearing at base cost only.
    exit_adv_notional: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class PanelResult:
    period_returns: tuple[float, ...]     # net-of-cost long/short return each period
    gross_returns: tuple[float, ...]
    costs: tuple[float, ...]
    turnovers: tuple[float, ...] = ()     # sum |Δweight| each period
    max_participations: tuple[Optional[float], ...] = ()

    def total_net_return(self) -> float:
        return math.prod(1.0 + r for r in self.period_returns) - 1.0 if self.period_returns else 0.0

    def total_gross_return(self) -> float:
        return math.prod(1.0 + r for r in self.gross_returns) - 1.0 if self.gross_returns else 0.0

    def net_equity_curve(self, start: float = 1.0) -> list[float]:
        eq, v = [start], start
        for r in self.period_returns:
            v *= (1.0 + r)
            eq.append(v)
        return eq

    def reconciliation_residual(self) -> float:
        return max((abs(g - c - n) for g, c, n in
                    zip(self.gross_returns, self.costs, self.period_returns)), default=0.0)


def _quantile_legs(signals: Sequence[float], quantile: float) -> tuple[list[int], list[int]]:
    n = len(signals)
    k = max(1, int(round(n * quantile)))
    order = sorted(range(n), key=lambda i: (signals[i], i))   # deterministic tie-break
    return order[-k:], order[:k]   # (long = top, short = bottom)


def _dollar_neutral_weights(instruments, long_idx, short_idx, gross: float) -> dict[str, float]:
    w = {inst: 0.0 for inst in instruments}
    if long_idx:
        per = (gross / 2.0) / len(long_idx)
        for i in long_idx:
            w[instruments[i]] += per
    if short_idx:
        per = (gross / 2.0) / len(short_idx)
        for i in short_idx:
            w[instruments[i]] -= per
    return w


def run_panel_backtest(
    periods: Sequence[PanelPeriod],
    cost: CostModel = CostModel(),
    *,
    gross: float = 1.0,
    quantile: float = 0.2,
    target_aum: float = 1e7,
) -> PanelResult:
    prev_w: dict[str, float] = {}
    period_returns, gross_returns, costs, turnovers, max_participations = [], [], [], [], []
    for p in periods:
        # fail-closed on malformed cross-sections (review P1/P2)
        if len(set(p.instruments)) != len(p.instruments):
            raise ValueError("PanelPeriod.instruments must be unique (duplicates corrupt weights)")
        if not all(math.isfinite(s) for s in p.signals):
            raise ValueError("PanelPeriod.signals must all be finite")
        long_idx, short_idx = _quantile_legs(p.signals, quantile)
        w = _dollar_neutral_weights(p.instruments, long_idx, short_idx, gross)
        if p.adv_notional and (
            len(p.adv_notional) != len(p.instruments)
            or any(
                not math.isfinite(value) or value <= 0.0
                for value in p.adv_notional
            )
        ):
            raise ValueError(
                "PanelPeriod.adv_notional must be an aligned positive finite vector"
            )
        exit_adv = dict(p.exit_adv_notional)
        if (
            len(exit_adv) != len(p.exit_adv_notional)
            or set(exit_adv) & set(p.instruments)
            or any(
                not isinstance(name, str)
                or not name
                or not math.isfinite(value)
                or value <= 0.0
                for name, value in p.exit_adv_notional
            )
        ):
            raise ValueError(
                "PanelPeriod.exit_adv_notional must be unique, positive, and exit-only"
            )

        gross_r = sum(w[inst] * p.forward_returns[i] for i, inst in enumerate(p.instruments))

        # Stable accumulation order is part of the immutable evaluation
        # contract. Iterating a set made cost totals differ by ~1e-18 across
        # PYTHONHASHSEED values, so an exact catalog rerun appeared to contain
        # a conflicting OOS return history.
        names = sorted(set(w) | set(prev_w))
        base_bps = cost.fee_bps + cost.slippage_bps
        cst = 0.0
        turnover = 0.0
        period_max_participation: Optional[float] = 0.0 if p.adv_notional else None
        for n in names:
            dw = abs(w.get(n, 0.0) - prev_w.get(n, 0.0))   # fraction of AUM traded
            turnover += dw
            cst += dw * base_bps / 10_000.0
        if p.adv_notional:
            adv_by_instrument = {
                **dict(zip(p.instruments, p.adv_notional, strict=True)),
                **exit_adv,
            }
            for instrument in names:
                dw = abs(w.get(instrument, 0.0) - prev_w.get(instrument, 0.0))
                if dw == 0.0:
                    continue
                adv = adv_by_instrument.get(instrument)
                if adv is None:
                    raise ValueError(
                        "dynamic-universe liquidation is missing current exit ADV"
                    )
                traded_notional = dw * target_aum
                if cost.impact_coef > 0:
                    cst += (
                        dw
                        * cost.impact_bps(traded_notional, adv)
                        / 10_000.0
                    )
                participation = dw * target_aum / adv
                if period_max_participation is not None:
                    period_max_participation = max(
                        period_max_participation, participation
                    )

        period_returns.append(gross_r - cst)
        gross_returns.append(gross_r)
        costs.append(cst)
        turnovers.append(turnover)
        max_participations.append(period_max_participation)
        prev_w = w

    return PanelResult(
        tuple(period_returns),
        tuple(gross_returns),
        tuple(costs),
        tuple(turnovers),
        tuple(max_participations),
    )
