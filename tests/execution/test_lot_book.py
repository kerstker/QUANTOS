from decimal import Decimal

import pytest

from services.execution.lot_book import (
    LotBookError,
    LotSpec,
    scale_book_to_lots,
    scale_snapshot_to_lots,
)
from services.execution.simulated_sor import BookLevel, VenueBook


def _book() -> VenueBook:
    return VenueBook(
        venue_id="binance_usdm",
        observed_at_ms=100,
        taker_fee_bps=Decimal("5"),
        latency_ms=2,
        bids=(
            BookLevel(price=Decimal("60000"), quantity=1),
            BookLevel(price=Decimal("59900"), quantity=2),
        ),
        asks=(
            BookLevel(price=Decimal("60100"), quantity=1),
            BookLevel(price=Decimal("60200"), quantity=2),
        ),
    )


def _spec() -> LotSpec:
    return LotSpec(
        ticker="BTC",
        venue_symbol="BTCUSDT",
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("50"),
    )


def test_scales_native_depth_and_price_to_integer_exchange_lots() -> None:
    scaled = scale_book_to_lots(_book(), _spec())

    assert scaled.bids[0] == BookLevel(price=Decimal("60.000"), quantity=1000)
    assert scaled.asks[0] == BookLevel(price=Decimal("60.100"), quantity=1000)
    assert sum(level.quantity for level in scaled.asks) == 3000
    assert scaled.asks[0].price * scaled.asks[0].quantity == Decimal("60100.000")


def test_snapshot_requires_exact_identity_and_scales_reference_price() -> None:
    book = _book()
    books, prices = scale_snapshot_to_lots(
        {"BTC": (book, book)},
        {"BTC": 60050.0},
        {"BTC": _spec()},
    )

    assert prices == {"BTC": 60.05}
    assert books["BTC"][0].asks[0].quantity == 1000

    with pytest.raises(LotBookError, match="universes differ"):
        scale_snapshot_to_lots({"BTC": (book, book)}, {}, {"BTC": _spec()})


def test_lot_spec_rejects_invalid_contract_identity() -> None:
    with pytest.raises(LotBookError, match="identity"):
        LotSpec(
            ticker="BTCUSDT",
            venue_symbol="BTCUSDT",
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("50"),
        )


def test_minimum_quantity_rounds_up_to_whole_exchange_lots() -> None:
    spec = LotSpec(
        ticker="BTC",
        venue_symbol="BTCUSDT",
        step_size=Decimal("0.003"),
        min_qty=Decimal("0.010"),
        min_notional=Decimal("50"),
    )

    assert spec.min_lots == 4
