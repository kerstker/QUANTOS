"""Public aggregate input port; adapters own venue parsing and sequencing."""
from decimal import Decimal
from typing import Callable, Protocol


class AggregateTrade(Protocol):
    transaction_time_ms: int
    received_at_ms: int
    price: Decimal
    quantity: Decimal
    aggressor_side: str
    source_hash: str


class TradeCursor(Protocol):
    def apply(self, payload: dict, *, received_at_ms: int) -> AggregateTrade | None:
        """Return a validated fact, ignore duplicates, or raise ValueError."""
        ...


TradeCursorFactory = Callable[[], TradeCursor]
