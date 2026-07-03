"""Point-in-time-correct corporate action adjustment (Phase 2).

The critical property: an adjustment is applied ONLY if the corporate action was
knowable at the query's as-of time. A split announced/effective at knowledge_time
T must not retroactively change prices for queries as-of < T — otherwise the
backtest peeks at the future. This is enforced here, not assumed.
"""
from __future__ import annotations

from typing import Iterable, List

from quantos.contracts.data import Bar, CorporateAction, CorporateActionType


def split_factor(actions: Iterable[CorporateAction], bar_event_time: int, as_of: int) -> float:
    """Cumulative split factor applicable to a historical bar, using only splits
    that (a) were knowable by `as_of` and (b) took effect after the bar."""
    factor = 1.0
    for a in actions:
        if (
            a.action_type == CorporateActionType.SPLIT
            and a.knowledge_time <= as_of
            and a.event_time > bar_event_time
        ):
            factor *= a.ratio
    return factor


def adjusted_close(bar: Bar, actions: Iterable[CorporateAction], as_of: int) -> float:
    """Split-adjusted close as it would have been known at `as_of`.

    NOTE: this is PRICE (split) adjustment only. Dividend price back-adjustment
    requires the ex-date reference price and is performed by the backtester
    (Phase 4); use `cash_dividend_total` for PIT dividend accrual meanwhile.
    """
    return bar.close / split_factor(actions, bar.event_time, as_of)


def cash_dividend_total(
    actions: Iterable[CorporateAction],
    start_exclusive: int,
    end_inclusive: int,
    as_of: int,
) -> float:
    """Sum of per-share cash dividends going ex within (start_exclusive,
    end_inclusive] that were knowable by `as_of` (PIT). This is the correct
    dividend-accrual primitive for total-return accounting; full dividend price
    back-adjustment (needs the ex-date reference price) is a Phase-4 concern."""
    total = 0.0
    for a in actions:
        if (
            a.action_type == CorporateActionType.DIVIDEND
            and a.knowledge_time <= as_of
            and start_exclusive < a.event_time <= end_inclusive
        ):
            total += a.amount
    return total


def total_return_series(bars: Iterable[Bar], actions: Iterable[CorporateAction], as_of: int) -> List[float]:
    """PIT total-return price series (Rank #23) — assembles the split + dividend primitives into the
    total-return-adjusted close series a backtest needs for value/dividend/low-vol factors.

    Each step compounds the split-adjusted price return PLUS the dividend yield of any dividend going
    ex in that step and knowable by `as_of`. Splits are handled by `adjusted_close`; the dividend
    yield is taken against the prior close, so it is scale-free and needs NO separate ex-date
    reference price (the gap `adjusted_close` deferred to 'Phase 4'). Only actions with
    knowledge_time<=as_of are applied, so a backtest at `as_of` never peeks at a later action. The
    series starts at bars[0]'s split-adjusted close; a non-positive prior price carries flat."""
    bars = list(bars)
    if not bars:
        return []
    actions = list(actions)
    out = [adjusted_close(bars[0], actions, as_of)]
    for i in range(1, len(bars)):
        p0 = adjusted_close(bars[i - 1], actions, as_of)
        p1 = adjusted_close(bars[i], actions, as_of)
        raw0 = bars[i - 1].close
        if p0 <= 0 or raw0 <= 0:
            out.append(out[-1])
            continue
        div = cash_dividend_total(actions, bars[i - 1].event_time, bars[i].event_time, as_of)
        total_ret = (p1 / p0 - 1.0) + (div / raw0)
        out.append(out[-1] * (1.0 + total_ret))
    return out
