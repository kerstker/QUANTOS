from __future__ import annotations

import pytest

from services.research.liquidation_events import (
    FIVE_MINUTES_MS,
    default_liquidation_event_specs,
    evaluate_liquidation_event_spec,
)
from services.research.realtime_alpha_discovery import RealtimeAlphaPolicy, RealtimeFrame


def _frame(
    instrument: str,
    decision_time: int,
    *,
    price: float,
    liquidation: float = 0.0,
    spread_bps: float = 0.0,
) -> RealtimeFrame:
    return RealtimeFrame(
        instrument_id=instrument,
        event_time_ms=decision_time,
        decision_time_ms=decision_time,
        mid_price=price,
        spread_bps=spread_bps,
        depth_imbalance_5=0.0,
        mark_basis_bps=0.0,
        funding_rate=0.01,  # must never be prorated into a 5m event label
        taker_imbalance_5s=0.0,
        liquidation_imbalance_5s=liquidation,
    )


def _history(*, second_event_at: int | None = None):
    instruments = tuple(f"S{index:02d}USDT" for index in range(8))
    times = [0, FIVE_MINUTES_MS]
    if second_event_at is not None:
        times.extend([second_event_at, second_event_at + FIVE_MINUTES_MS])
    result = {instrument: [] for instrument in instruments}
    for decision_time in sorted(set(times)):
        for column, instrument in enumerate(instruments):
            liquidation = 0.0
            if decision_time in {0, second_event_at}:
                liquidation = 1.0 if column == 0 else (-1.0 if column == 1 else 0.0)
            base = 100.0 + column
            price = base
            if decision_time in {FIVE_MINUTES_MS, (second_event_at or -1) + FIVE_MINUTES_MS}:
                if column == 0:
                    price = base * 1.01
                elif column == 1:
                    price = base * 0.99
            result[instrument].append(
                _frame(
                    instrument,
                    decision_time,
                    price=price,
                    liquidation=liquidation,
                )
            )
    return result


def test_manifest_is_exactly_two_fixed_five_minute_event_directions() -> None:
    specs = default_liquidation_event_specs()

    assert [item.mechanism for item in specs] == [
        "liquidation_event_exhaustion",
        "liquidation_event_cascade",
    ]
    assert all(item.horizon_ms == FIVE_MINUTES_MS for item in specs)


def test_all_zero_or_one_sided_liquidation_strictly_abstains() -> None:
    history = _history()
    for rows in history.values():
        rows[0] = _frame(rows[0].instrument_id, 0, price=rows[0].mid_price)
    spec = default_liquidation_event_specs()[0]

    all_zero = evaluate_liquidation_event_spec(spec, history)

    history["S00USDT"][0] = _frame("S00USDT", 0, price=100.0, liquidation=1.0)
    one_sided = evaluate_liquidation_event_spec(spec, history)
    assert all_zero.status == "NO_TWO_SIDED_EVENTS"
    assert one_sided.status == "NO_TWO_SIDED_EVENTS"
    assert all_zero.n_events == one_sided.n_events == 0
    assert all_zero.total_cost == one_sided.total_cost == 0.0


def test_exhaustion_and_cascade_are_opposite_and_charge_independent_round_trip() -> None:
    exhaustion, cascade = default_liquidation_event_specs()
    history = _history()
    policy = RealtimeAlphaPolicy(
        min_cross_section=8,
        execution_latency_ms=0,
        max_label_lateness_ms=0,
        fee_bps=4.0,
        slippage_bps=2.0,
    )

    exhaustion_result = evaluate_liquidation_event_spec(
        exhaustion, history, policy=policy
    )
    cascade_result = evaluate_liquidation_event_spec(cascade, history, policy=policy)

    assert exhaustion_result.n_events == cascade_result.n_events == 1
    assert exhaustion_result.gross_returns == pytest.approx((0.01,))
    assert cascade_result.gross_returns == pytest.approx((-0.01,))
    assert exhaustion_result.base_costs == pytest.approx((0.0012,))
    assert exhaustion_result.net_returns == pytest.approx((0.0088,))
    assert cascade_result.net_returns == pytest.approx((-0.0112,))
    assert exhaustion_result.gross_return == pytest.approx(0.01)
    assert exhaustion_result.mean_gross_return == pytest.approx(0.01)
    assert exhaustion_result.mean_net_return == pytest.approx(0.0088)
    # A deliberately absurd funding rate on every frame must not leak into the
    # five-minute label: no settlement crossing is attested by this contract.
    assert exhaustion_result.gross_returns[0] == pytest.approx(0.01)


def test_event_windows_are_non_overlapping() -> None:
    history = _history(second_event_at=2 * 60_000)
    spec = default_liquidation_event_specs()[0]
    policy = RealtimeAlphaPolicy(
        min_cross_section=8,
        execution_latency_ms=0,
        max_label_lateness_ms=0,
    )

    result = evaluate_liquidation_event_spec(spec, history, policy=policy)

    assert result.n_events == 1
    assert result.event_times == (0,)
