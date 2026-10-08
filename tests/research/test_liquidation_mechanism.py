from __future__ import annotations

import pytest

from services.research.liquidation_mechanism import (
    LiquidationPrint,
    group_closed_liquidation_bursts,
    select_independent_bursts,
)


def _print(
    print_id: str,
    *,
    instrument: str = "BTCUSDT",
    side: str = "Buy",
    received_at_ms: int = 10_000,
    processed_at_ms: int | None = None,
) -> LiquidationPrint:
    return LiquidationPrint(
        instrument=instrument,
        liquidated_position_side=side,
        event_time_ms=received_at_ms - 10,
        received_at_ms=received_at_ms,
        processed_at_ms=(
            received_at_ms + 5 if processed_at_ms is None else processed_at_ms
        ),
        print_id=print_id,
    )


def test_groups_same_instrument_side_and_requires_quiet_period() -> None:
    rows = (
        _print("a", received_at_ms=10_000),
        _print("b", received_at_ms=14_999),
        _print("c", side="Sell", received_at_ms=15_000),
        _print("d", received_at_ms=20_100),
    )

    bursts = group_closed_liquidation_bursts(rows, observed_through_ms=22_100)

    assert [burst.print_ids for burst in bursts] == [("a", "b"), ("c",), ("d",)]
    assert bursts[0].forced_pressure_side == "Sell"
    assert bursts[1].forced_pressure_side == "Buy"
    assert bursts[0].print_count == 2
    assert group_closed_liquidation_bursts(
        rows, observed_through_ms=22_099
    )[-1].print_ids != ("d",)


def test_future_append_cannot_change_fixed_observation_boundary() -> None:
    known = (_print("known", received_at_ms=10_000),)
    future = _print(
        "future",
        received_at_ms=10_100,
        processed_at_ms=20_001,
    )

    first = group_closed_liquidation_bursts(
        known, observed_through_ms=20_000
    )
    appended = group_closed_liquidation_bursts(
        (*known, future), observed_through_ms=20_000
    )

    assert first == appended


def test_lockout_is_per_instrument_regardless_of_liquidation_side() -> None:
    bursts = group_closed_liquidation_bursts(
        (
            _print("btc-1", received_at_ms=10_000),
            _print("eth-1", instrument="ETHUSDT", received_at_ms=11_000),
            _print("btc-2", side="Sell", received_at_ms=20_000),
            _print("btc-3", received_at_ms=1_810_000),
        ),
        observed_through_ms=2_000_000,
    )

    selected = select_independent_bursts(bursts)

    assert [row.print_ids for row in selected] == [
        ("btc-1",),
        ("eth-1",),
        ("btc-3",),
    ]


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"instrument": "btc/usdt"}, "instrument"),
        ({"side": "Long"}, "side"),
        ({"processed_at_ms": 9_999}, "clocks"),
    ],
)
def test_print_validation_fails_closed(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _print("print", **kwargs)
