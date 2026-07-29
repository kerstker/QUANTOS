"""Route-level TCA is deterministic, reconciled and simulation-only."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.execution.simulated_sor import (
    BookLevel,
    SORPolicy,
    VenueBook,
    route_simulated_intent,
)
from services.execution.simulated_tca import (
    RouteExecution,
    SimulatedTCAError,
    build_route_tca,
    verify_route_tca_record,
)


D = Decimal


def _plan(side: OrderSide = OrderSide.BUY):
    intent = OrderIntent(
        instrument_id="BTC-USDT",
        side=side,
        quantity=100,
        order_type=OrderType.MARKET,
        strategy_id="tca-test",
    )
    books = (
        VenueBook(
            venue_id="A",
            observed_at_ms=1_000,
            taker_fee_bps=D("10"),
            latency_ms=2,
            bids=(BookLevel(D("99.5"), 60),),
            asks=(BookLevel(D("100"), 60),),
        ),
        VenueBook(
            venue_id="B",
            observed_at_ms=999,
            taker_fee_bps=D("5"),
            latency_ms=1,
            bids=(BookLevel(D("99.4"), 60),),
            asks=(BookLevel(D("100.1"), 60),),
        ),
    )
    return route_simulated_intent(
        intent,
        books,
        as_of_ms=1_000,
        policy=SORPolicy(
            max_quote_age_ms=10,
            max_venue_fraction=D("0.6"),
            latency_penalty_bps_per_ms=D("0.1"),
        ),
    )


def _executions(plan):
    return tuple(
        RouteExecution(
            slice_index=index,
            venue_id=item.venue_id,
            quantity=item.quantity,
            fill_price=item.raw_price,
        )
        for index, item in enumerate(plan.slices)
    )


def test_builds_reconciled_buy_tca_with_per_venue_contributions() -> None:
    plan = _plan()
    record = build_route_tca(
        plan,
        reference_price=D("100"),
        executions=_executions(plan),
    )

    assert record["simulation_only"] is True
    assert record["filled_quantity"] == 100
    assert record["fill_fraction"] == "1"
    assert [item["venue_id"] for item in record["venues"]] == ["A", "B"]
    assert D(record["realized_all_in_vwap"]) > D(record["realized_raw_vwap"])
    assert D(record["implementation_shortfall_bps"]) > 0
    assert verify_route_tca_record(record) is True


def test_sell_shortfall_is_side_aware() -> None:
    plan = _plan(OrderSide.SELL)
    record = build_route_tca(
        plan,
        reference_price=D("100"),
        executions=_executions(plan),
    )

    assert D(record["realized_all_in_vwap"]) < D(record["realized_raw_vwap"])
    assert D(record["implementation_shortfall_bps"]) > 0
    assert verify_route_tca_record(record) is True


def test_adapter_rejection_is_retained_as_execution_shortfall() -> None:
    plan = _plan()
    record = build_route_tca(
        plan,
        reference_price=D("100"),
        executions=_executions(plan)[:1],
    )

    assert record["routed_quantity"] == 100
    assert record["filled_quantity"] == 60
    assert record["execution_residual_quantity"] == 40
    assert verify_route_tca_record(record) is True


@pytest.mark.parametrize(
    "execution",
    [
        RouteExecution(9, "A", 60, D("100")),
        RouteExecution(0, "WRONG", 60, D("100")),
        RouteExecution(0, "A", 59, D("100")),
    ],
)
def test_execution_must_bind_an_exact_planned_slice(execution) -> None:
    with pytest.raises(SimulatedTCAError):
        build_route_tca(
            _plan(),
            reference_price=D("100"),
            executions=(execution,),
        )


def test_duplicate_slice_execution_fails_closed() -> None:
    execution = _executions(_plan())[0]
    with pytest.raises(SimulatedTCAError, match="cannot be executed twice"):
        build_route_tca(
            _plan(),
            reference_price=D("100"),
            executions=(execution, execution),
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("record_sha256",), "0" * 64),
        (("filled_quantity",), 99),
        (("realized_route_cost",), "0"),
        (("venues", 0, "filled_quantity"), 59),
        (("simulation_only",), False),
    ],
)
def test_tampered_persisted_record_is_rejected(path, value) -> None:
    record = build_route_tca(
        _plan(),
        reference_price=D("100"),
        executions=_executions(_plan()),
    )
    changed = deepcopy(record)
    cursor = changed
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value

    assert verify_route_tca_record(changed) is False


def test_record_is_deterministic() -> None:
    plan = _plan()
    first = build_route_tca(
        plan,
        reference_price=D("100"),
        executions=_executions(plan),
    )
    second = build_route_tca(
        plan,
        reference_price=D("100"),
        executions=tuple(reversed(_executions(plan))),
    )
    assert first == second


def test_shortfall_verification_uses_exact_venue_totals_after_vwap_rounding() -> None:
    """Regression for the real XRP paper-SOR snapshot observed on 2026-07-29."""

    intent = OrderIntent(
        instrument_id="XRPUSDT",
        side=OrderSide.BUY,
        quantity=930,
        order_type=OrderType.MARKET,
        strategy_id="tca-rounding-regression",
    )
    books = (
        VenueBook(
            venue_id="binance_usdm",
            observed_at_ms=1_000,
            taker_fee_bps=D("5"),
            latency_ms=0,
            bids=(BookLevel(D("1.0748"), 930),),
            asks=(BookLevel(D("1.0749"), 372),),
        ),
        VenueBook(
            venue_id="bybit_linear",
            observed_at_ms=1_000,
            taker_fee_bps=D("5.5"),
            latency_ms=0,
            bids=(BookLevel(D("1.0739"), 930),),
            asks=(
                BookLevel(D("1.0740"), 557),
                BookLevel(D("1.0994"), 1),
            ),
        ),
    )
    plan = route_simulated_intent(
        intent,
        books,
        as_of_ms=1_000,
        policy=SORPolicy(
            max_quote_age_ms=10,
            min_fill_fraction=D("1"),
            max_venue_fraction=D("0.60"),
            latency_penalty_bps_per_ms=D("0"),
        ),
    )
    record = build_route_tca(
        plan,
        reference_price=D("1.07485"),
        executions=_executions(plan),
    )

    assert record["filled_quantity"] == 930
    assert record["implementation_shortfall_bps"] == (
        "0.9929464526433045671288967053"
    )
    assert verify_route_tca_record(record) is True
