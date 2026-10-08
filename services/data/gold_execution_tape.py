"""XAUUSDT public research input, isolated from the BTC execution scope.

These classes validate retained events only. They neither fetch data nor grant
execution authority. Contract filters and tradeable quantities need a separate
point-in-time venue profile; a valid tape is not proof of executable liquidity.
"""
from services.data.binance_execution_tape import (
    BtcDepthBook, PublicTradeCursor, ExecutionTapeError, DepthView,
)


class GoldDepthBook(BtcDepthBook):
    """Share sequence/clock semantics while admitting only XAUUSDT."""

    _supported_symbols = frozenset({'XAUUSDT'})

    def view(self, *, as_of_ms: int, max_age_ms: int) -> DepthView:
        if type(max_age_ms) is not int or max_age_ms <= 0:
            raise ExecutionTapeError('INVALID_GOLD_MAX_AGE')
        view = super().view(as_of_ms=as_of_ms)
        # Receipt age alone cannot detect a delayed but newly received event.
        if as_of_ms - view.transaction_time_ms > max_age_ms:
            raise ExecutionTapeError('STALE_GOLD_BOOK')
        return view


class GoldTradeCursor(PublicTradeCursor):
    """Only XAUUSDT aggregates; no synthetic individual-order identity."""

    _supported_symbols = frozenset({'XAUUSDT'})


def new_gold_trade_cursor():
    """Composition entrypoint for the bounded XAUUSDT aggregate input port."""
    return GoldTradeCursor("XAUUSDT")
