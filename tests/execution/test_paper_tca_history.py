"""Longitudinal paper TCA is verified, deterministic and simulation-only."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.execution.paper_tca_history import (
    PaperTCAHistoryError,
    build_paper_tca_history,
)
from services.execution.simulated_sor import (
    BookLevel,
    SORPolicy,
    VenueBook,
    route_simulated_intent,
)
from services.execution.simulated_tca import RouteExecution, build_route_tca


def _run(run_id: str, timestamp: int, price: str = "100") -> dict:
    intent = OrderIntent(
        instrument_id="BTC-USDT",
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        strategy_id="paper-tca",
    )
    plan = route_simulated_intent(
        intent,
        (
            VenueBook(
                venue_id="BINANCE_PUBLIC_SIM",
                observed_at_ms=timestamp,
                taker_fee_bps=Decimal("5"),
                latency_ms=1,
                asks=(BookLevel(Decimal(price), 6),),
            ),
            VenueBook(
                venue_id="SECONDARY_SIM",
                observed_at_ms=timestamp,
                taker_fee_bps=Decimal("10"),
                latency_ms=2,
                asks=(BookLevel(Decimal(price) + Decimal("0.1"), 4),),
            ),
        ),
        as_of_ms=timestamp,
        policy=SORPolicy(max_quote_age_ms=0),
    )
    executions = tuple(
        RouteExecution(index, item.venue_id, item.quantity, item.raw_price)
        for index, item in enumerate(plan.slices)
    )
    tca = build_route_tca(
        plan,
        reference_price=Decimal(price),
        executions=executions,
    )
    return {
        "run_id": run_id,
        "strategy_id": "paper-tca",
        "timestamp": timestamp,
        "detail": {
            "routing_snapshot_id": f"public-books:{timestamp}",
            "route_tca_schema_version": 1,
            "route_tca": [tca],
        },
    }


def test_builds_longitudinal_route_and_venue_statistics() -> None:
    result = build_paper_tca_history(
        [_run("second", 2_000, "101"), _run("first", 1_000)]
    )

    assert result["simulation_only"] is True
    assert result["status"] == "LONGITUDINAL_READY"
    assert result["run_count"] == 2
    assert result["record_count"] == 2
    assert result["venue_count"] == 2
    assert [point["run_id"] for point in result["points"]] == ["first", "second"]
    assert {row["venue_id"] for row in result["venues"]} == {
        "BINANCE_PUBLIC_SIM",
        "SECONDARY_SIM",
    }
    assert all(Decimal(row["weighted_shortfall_bps"]) > 0 for row in result["venues"])


def test_non_routed_runs_are_ignored_without_fabricating_zero_tca() -> None:
    result = build_paper_tca_history(
        [{"run_id": "plain", "strategy_id": "x", "timestamp": 1, "detail": {}}]
    )

    assert result["status"] == "NO_ROUTE_TCA"
    assert result["weighted_shortfall_bps"] is None
    assert result["points"] == []


def test_single_timestamp_is_not_claimed_as_longitudinal() -> None:
    result = build_paper_tca_history([_run("one", 1_000)])
    assert result["status"] == "SINGLE_SNAPSHOT"


def test_tampered_retained_tca_fails_closed() -> None:
    run = deepcopy(_run("bad", 1_000))
    run["detail"]["route_tca"][0]["filled_quantity"] = 9

    with pytest.raises(PaperTCAHistoryError, match="failed verification"):
        build_paper_tca_history([run])


def test_projection_is_deterministic() -> None:
    runs = [_run("first", 1_000), _run("second", 2_000, "101")]
    assert build_paper_tca_history(runs) == build_paper_tca_history(list(reversed(runs)))
