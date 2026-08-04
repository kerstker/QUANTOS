"""Conservative integer-lot projection for fractional crypto contracts.

The core dry-run/SOR contracts intentionally use integer quantities.  This
adapter keeps that invariant while making one integer unit equal to the
exchange's public ``step_size`` rather than one whole base asset.  Prices are
scaled to quote currency per lot, so notional, fees, risk limits and TCA remain
dimensionally correct.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Mapping

from quantos.contracts.hashing import stable_hash
from services.execution.simulated_sor import BookLevel, VenueBook


class LotBookError(ValueError):
    """A lot specification or native book cannot be represented safely."""


@dataclass(frozen=True)
class LotSpec:
    ticker: str
    venue_symbol: str
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        if (
            not self.ticker
            or self.ticker.endswith("USDT")
            or self.venue_symbol != f"{self.ticker}USDT"
        ):
            raise LotBookError("lot identity is invalid")
        for name, value in (
            ("step_size", self.step_size),
            ("min_qty", self.min_qty),
            ("min_notional", self.min_notional),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise LotBookError(f"{name} must be a finite positive Decimal")
        if self.min_qty < self.step_size:
            raise LotBookError("minimum quantity is smaller than one lot")

    @property
    def min_lots(self) -> int:
        return int(
            (self.min_qty / self.step_size).to_integral_value(rounding=ROUND_CEILING)
        )

    @property
    def content_hash(self) -> str:
        return stable_hash(self.as_dict())

    def as_dict(self) -> dict[str, str]:
        return {
            "ticker": self.ticker,
            "venue_symbol": self.venue_symbol,
            "step_size": str(self.step_size),
            "min_qty": str(self.min_qty),
            "min_notional": str(self.min_notional),
        }


def _scale_levels(
    levels: tuple[BookLevel, ...],
    *,
    step_size: Decimal,
) -> tuple[BookLevel, ...]:
    """Split proven whole-base depth into exchange-step lots conservatively.

    The source book has already accumulated fractional native depth into whole
    units at the worst contributing price.  Cumulative conversion here avoids
    losing valid remainder between levels while never exceeding that proven
    native depth.
    """
    result: list[BookLevel] = []
    cumulative_native = Decimal("0")
    emitted_lots = 0
    for level in levels:
        cumulative_native += Decimal(level.quantity)
        total_lots = int(
            (cumulative_native / step_size).to_integral_value(rounding=ROUND_FLOOR)
        )
        quantity = total_lots - emitted_lots
        if quantity > 0:
            result.append(
                BookLevel(price=level.price * step_size, quantity=quantity)
            )
            emitted_lots += quantity
    if not result:
        raise LotBookError("native book has no executable lot depth")
    return tuple(result)


def scale_book_to_lots(book: VenueBook, spec: LotSpec) -> VenueBook:
    """Return a dimensionally equivalent book priced and sized per integer lot."""
    return VenueBook(
        venue_id=book.venue_id,
        observed_at_ms=book.observed_at_ms,
        taker_fee_bps=book.taker_fee_bps,
        latency_ms=book.latency_ms,
        bids=_scale_levels(book.bids, step_size=spec.step_size),
        asks=_scale_levels(book.asks, step_size=spec.step_size),
    )


def scale_snapshot_to_lots(
    books: Mapping[str, tuple[VenueBook, VenueBook]],
    prices: Mapping[str, float],
    specs: Mapping[str, LotSpec],
) -> tuple[dict[str, tuple[VenueBook, VenueBook]], dict[str, float]]:
    """Scale one complete ticker snapshot; missing/extra identities fail closed."""
    if set(books) != set(prices) or set(books) != set(specs):
        raise LotBookError("book, price and lot-spec universes differ")
    scaled_books: dict[str, tuple[VenueBook, VenueBook]] = {}
    scaled_prices: dict[str, float] = {}
    for ticker in sorted(books):
        spec = specs[ticker]
        native_price = Decimal(str(prices[ticker]))
        if not native_price.is_finite() or native_price <= 0:
            raise LotBookError(f"native reference price is invalid for {ticker}")
        scaled_books[ticker] = tuple(
            scale_book_to_lots(book, spec) for book in books[ticker]
        )
        scaled_prices[ticker] = float(native_price * spec.step_size)
    return scaled_books, scaled_prices


__all__ = [
    "LotBookError",
    "LotSpec",
    "scale_book_to_lots",
    "scale_snapshot_to_lots",
]
