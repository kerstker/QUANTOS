"""Deterministic research/dry-run multi-venue SOR tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.execution.simulated_sor import (
    BookLevel,
    RouteStatus,
    RoutingError,
    SORPolicy,
    VenueBook,
    route_simulated_intent,
)


D = Decimal


def _intent(
    side: OrderSide,
    quantity: int = 100,
    *,
    limit: float | None = None,
) -> OrderIntent:
    return OrderIntent(
        instrument_id="BTC-USDT",
        side=side,
        quantity=quantity,
        order_type=OrderType.LIMIT if limit is not None else OrderType.MARKET,
        limit_price=limit,
        strategy_id="sor-test",
    )


def _book(
    venue: str,
    *,
    bid: str = "99",
    ask: str = "101",
    bid_qty: int = 100,
    ask_qty: int = 100,
    fee_bps: str = "0",
    latency_ms: int = 0,
    observed_at_ms: int = 1_000,
) -> VenueBook:
    return VenueBook(
        venue_id=venue,
        observed_at_ms=observed_at_ms,
        taker_fee_bps=D(fee_bps),
        latency_ms=latency_ms,
        bids=(BookLevel(D(bid), bid_qty),),
        asks=(BookLevel(D(ask), ask_qty),),
    )


def _policy(**overrides: object) -> SORPolicy:
    values: dict[str, object] = {
        "max_quote_age_ms": 100,
        "min_fill_fraction": D("1"),
        "max_venue_fraction": D("1"),
        "latency_penalty_bps_per_ms": D("0"),
    }
    values.update(overrides)
    return SORPolicy(**values)  # type: ignore[arg-type]


def test_buy_routes_by_all_in_cost_not_raw_price() -> None:
    cheap_raw_high_fee = _book("A", ask="100", fee_bps="20")
    higher_raw_low_fee = _book("B", ask="100.10", fee_bps="0")

    plan = route_simulated_intent(
        _intent(OrderSide.BUY),
        [cheap_raw_high_fee, higher_raw_low_fee],
        as_of_ms=1_000,
        policy=_policy(),
    )

    assert plan.status == RouteStatus.COMPLETE
    assert plan.slices[0].venue_id == "B"
    assert plan.slices[0].all_in_price == D("100.10")
    assert plan.simulation_only is True


def test_sell_routes_by_highest_net_price() -> None:
    high_raw_high_fee = _book("A", bid="100", fee_bps="20")
    lower_raw_low_fee = _book("B", bid="99.90", fee_bps="0")

    plan = route_simulated_intent(
        _intent(OrderSide.SELL),
        [high_raw_high_fee, lower_raw_low_fee],
        as_of_ms=1_000,
        policy=_policy(),
    )

    assert plan.slices[0].venue_id == "B"
    assert plan.expected_all_in_notional == D("9990.00")


def test_splits_across_depth_and_applies_venue_concentration_cap() -> None:
    a = VenueBook(
        venue_id="A",
        observed_at_ms=1_000,
        taker_fee_bps=D("0"),
        latency_ms=0,
        bids=(BookLevel(D("99"), 100),),
        asks=(BookLevel(D("100"), 40), BookLevel(D("101"), 40)),
    )
    b = _book("B", ask="102", ask_qty=100)

    plan = route_simulated_intent(
        _intent(OrderSide.BUY),
        [a, b],
        as_of_ms=1_000,
        policy=_policy(max_venue_fraction=D("0.6")),
    )

    assert plan.status == RouteStatus.COMPLETE
    assert [(item.venue_id, item.quantity) for item in plan.slices] == [
        ("A", 40),
        ("A", 20),
        ("B", 40),
    ]


def test_latency_penalty_can_change_route_order() -> None:
    fast = _book("FAST", ask="100.02", latency_ms=1)
    slow = _book("SLOW", ask="100", latency_ms=50)

    plan = route_simulated_intent(
        _intent(OrderSide.BUY),
        [slow, fast],
        as_of_ms=1_000,
        policy=_policy(latency_penalty_bps_per_ms=D("0.1")),
    )

    assert plan.slices[0].venue_id == "FAST"
    assert plan.expected_latency_penalty > 0


def test_stale_and_non_executable_books_are_excluded() -> None:
    stale = _book("STALE", observed_at_ms=800)
    no_asks = VenueBook(
        venue_id="EMPTY",
        observed_at_ms=1_000,
        taker_fee_bps=D("0"),
        latency_ms=0,
        bids=(BookLevel(D("99"), 10),),
    )
    fresh = _book("FRESH")

    plan = route_simulated_intent(
        _intent(OrderSide.BUY),
        [stale, no_asks, fresh],
        as_of_ms=1_000,
        policy=_policy(),
    )

    assert plan.status == RouteStatus.COMPLETE
    assert plan.excluded_venues == (
        ("EMPTY", "NO_EXECUTABLE_DEPTH"),
        ("STALE", "STALE_QUOTE"),
    )


def test_limit_price_filters_raw_execution_prices() -> None:
    inside = _book("A", ask="100")
    outside = _book("B", ask="101")

    plan = route_simulated_intent(
        _intent(OrderSide.BUY, quantity=50, limit=100.5),
        [outside, inside],
        as_of_ms=1_000,
        policy=_policy(),
    )

    assert plan.status == RouteStatus.COMPLETE
    assert {item.venue_id for item in plan.slices} == {"A"}


def test_insufficient_fill_rejects_atomically_without_route_slices() -> None:
    plan = route_simulated_intent(
        _intent(OrderSide.BUY, quantity=100),
        [_book("A", ask_qty=49)],
        as_of_ms=1_000,
        policy=_policy(min_fill_fraction=D("0.5")),
    )

    assert plan.status == RouteStatus.REJECTED
    assert plan.routed_quantity == 0
    assert plan.residual_quantity == 100
    assert plan.slices == ()
    assert ("*", "INSUFFICIENT_LIQUIDITY") in plan.excluded_venues


def test_partial_route_requires_explicit_minimum_fill_policy() -> None:
    plan = route_simulated_intent(
        _intent(OrderSide.BUY, quantity=100),
        [_book("A", ask_qty=60)],
        as_of_ms=1_000,
        policy=_policy(min_fill_fraction=D("0.5")),
    )

    assert plan.status == RouteStatus.PARTIAL
    assert plan.routed_quantity == 60
    assert plan.residual_quantity == 40


def test_input_order_does_not_change_plan() -> None:
    books = [_book("B", ask="100"), _book("A", ask="100")]
    first = route_simulated_intent(
        _intent(OrderSide.BUY),
        books,
        as_of_ms=1_000,
        policy=_policy(),
    )
    second = route_simulated_intent(
        _intent(OrderSide.BUY),
        reversed(books),
        as_of_ms=1_000,
        policy=_policy(),
    )

    assert first == second
    assert first.slices[0].venue_id == "A"


def test_duplicate_or_future_venue_snapshot_fails_closed() -> None:
    with pytest.raises(RoutingError, match="duplicate venue_id"):
        route_simulated_intent(
            _intent(OrderSide.BUY),
            [_book("A"), _book("A")],
            as_of_ms=1_000,
            policy=_policy(),
        )
    with pytest.raises(RoutingError, match="future quote"):
        route_simulated_intent(
            _intent(OrderSide.BUY),
            [_book("A", observed_at_ms=1_001)],
            as_of_ms=1_000,
            policy=_policy(),
        )


@pytest.mark.parametrize(
    "book",
    [
        lambda: VenueBook(
            venue_id="A",
            observed_at_ms=0,
            taker_fee_bps=D("-1"),
            latency_ms=0,
        ),
        lambda: VenueBook(
            venue_id="A",
            observed_at_ms=0,
            taker_fee_bps=D("0"),
            latency_ms=0,
            bids=(BookLevel(D("101"), 1),),
            asks=(BookLevel(D("100"), 1),),
        ),
        lambda: VenueBook(
            venue_id="A",
            observed_at_ms=0,
            taker_fee_bps=D("0"),
            latency_ms=0,
            asks=(BookLevel(D("101"), 1), BookLevel(D("100"), 1)),
        ),
    ],
)
def test_invalid_market_snapshot_is_rejected(book) -> None:
    with pytest.raises(RoutingError):
        book()
