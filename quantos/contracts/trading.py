"""Trading domain entities (blueprint §6.6).

Pydantic v2 — the standard for data-bearing domain entities. These are the
contracts a strategy emits (OrderIntent) and the order/fill/report objects that
flow through risk -> paper -> execution. They carry NO live-trading capability;
they are inert data validated at construction.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .order import OrderState
from .rejection import RejectionReason


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderIntent(BaseModel):
    """A strategy's desired action (intent only — never an instruction to a
    broker). Risk decides permission separately (blueprint §5.3)."""
    model_config = ConfigDict(frozen=True)

    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: int = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = Field(default=None, gt=0)
    strategy_id: str = ""

    @model_validator(mode="after")
    def _check_price(self) -> "OrderIntent":
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT order requires a positive limit_price")
        if self.order_type == OrderType.MARKET and self.limit_price is not None:
            raise ValueError("MARKET order must not carry a limit_price")
        return self


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str = Field(min_length=1)
    intent: OrderIntent
    state: OrderState = OrderState.CREATED
    filled_quantity: int = Field(default=0, ge=0)
    avg_fill_price: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _check_fill(self) -> "Order":
        if self.filled_quantity > self.intent.quantity:
            raise ValueError("filled_quantity cannot exceed intent.quantity")
        return self


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)

    fill_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    timestamp: int = 0


class ExecutionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    state: OrderState
    filled_quantity: int = Field(default=0, ge=0)
    reason: RejectionReason | None = None
