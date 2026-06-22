"""Point-in-time-correct corporate action adjustment (Phase 2).

The critical property: an adjustment is applied ONLY if the corporate action was
knowable at the query's as-of time. A split announced/effective at knowledge_time
T must not retroactively change prices for queries as-of < T — otherwise the
backtest peeks at the future. This is enforced here, not assumed.
"""
from __future__ import annotations

from typing import Iterable

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
