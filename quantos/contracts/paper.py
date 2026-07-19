"""Canonical paper-execution value contracts shared by research and execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class FillMethod(str, Enum):
    NEXT_OPEN = "NEXT_OPEN"
    NEXT_CLOSE = "NEXT_CLOSE"
    VWAP = "VWAP"


@dataclass
class PaperFill:
    fill_id: str = field(default_factory=lambda: str(uuid4()))
    order_id: str = ""
    run_id: str = ""
    instrument_id: str = ""
    side: OrderSide = OrderSide.BUY
    quantity: int = 0
    price: float = 0.0
    fill_method: FillMethod = FillMethod.NEXT_CLOSE
    timestamp: int = 0
    commission: float = 0.0
    slippage: float = 0.0


__all__ = ["FillMethod", "OrderSide", "PaperFill"]
