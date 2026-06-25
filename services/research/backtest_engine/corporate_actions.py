"""Corporate-action handling for the event-driven backtester (sub-project 4).

Applies SPLIT (value-neutral share/cost adjustment) and DIVIDEND (signed cash) to
the Book at the action's ex moment. Unsupported action types fail closed (a
backtest must not silently mishandle a merger/spinoff/rights issue). Forward-sim
convention: bar prices are RAW (they jump on the ex bar); the engine adjusts the
BOOK, not past prices.
"""
from __future__ import annotations

from quantos.contracts.data import CorporateAction, CorporateActionType


def apply_corporate_action(book, action: CorporateAction, *, price=None) -> None:
    if action.action_type == CorporateActionType.SPLIT:
        # `price` (the post-split market price) settles any fractional remainder as
        # cash-in-lieu; without it a fractional split fails closed.
        book.apply_split(action.instrument_id, action.ratio, cash_in_lieu_price=price)
    elif action.action_type == CorporateActionType.DIVIDEND:
        book.pay_dividend(action.instrument_id, action.amount)
    else:
        raise ValueError(f"unsupported corporate action in backtest: {action.action_type}")
