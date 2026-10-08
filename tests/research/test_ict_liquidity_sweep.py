from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services.research.ict_liquidity_sweep import (
    IctLiquiditySweepConfig,
    SweepFrame,
    detect_liquidity_sweeps,
    session_bucket,
    signal_for_event,
)


CADENCE = 30_000


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def _frame(
    index: int,
    *,
    start_ms: int,
    price: float = 100.0,
    spread: float = 10.0,
    taker: float = 0.0,
    depth_imbalance: float = 0.0,
    depth: float = 100_000.0,
) -> SweepFrame:
    event_time = start_ms + index * CADENCE
    return SweepFrame(
        instrument_id="BTCUSDT",
        event_time_ms=event_time,
        decision_time_ms=event_time + 10,
        mid_price=price,
        spread_bps=spread,
        taker_imbalance_5s=taker,
        depth_imbalance_5=depth_imbalance,
        executable_depth_5_usd=depth,
    )


def _high_sweep(*, absorbed: bool = True) -> tuple[SweepFrame, ...]:
    # 11:00 UTC is 07:00 New York during DST, inside the frozen primary session.
    breach_ms = _ms(datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc))
    start = breach_ms - 120 * CADENCE
    rows = [_frame(index, start_ms=start) for index in range(120)]
    rows.append(
        _frame(
            120,
            start_ms=start,
            price=100.25,
            taker=0.80 if absorbed else 0.10,
            depth_imbalance=0.70,
            depth=100_000.0,
        )
    )
    rows.append(
        _frame(
            121,
            start_ms=start,
            price=99.95,
            taker=-0.10,
            depth_imbalance=-0.50 if absorbed else -0.10,
            depth=125_000.0,
        )
    )
    return tuple(rows)


def _low_sweep() -> tuple[SweepFrame, ...]:
    breach_ms = _ms(datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc))
    start = breach_ms - 120 * CADENCE
    rows = [_frame(index, start_ms=start) for index in range(120)]
    rows.append(
        _frame(
            120,
            start_ms=start,
            price=99.75,
            taker=-0.80,
            depth_imbalance=-0.70,
            depth=100_000.0,
        )
    )
    rows.append(
        _frame(
            121,
            start_ms=start,
            price=100.05,
            taker=0.10,
            depth_imbalance=0.50,
            depth=125_000.0,
        )
    )
    return tuple(rows)


def test_config_and_frame_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="below one frame"):
        IctLiquiditySweepConfig(execution_latency_ms=30_000)
    with pytest.raises(ValueError, match="must not overlap"):
        IctLiquiditySweepConfig(placebo_session_hours=((2, 3),))
    with pytest.raises(ValueError, match="imbalance"):
        SweepFrame(
            "BTCUSDT",
            1,
            1,
            100.0,
            1.0,
            1.1,
            0.0,
            1_000.0,
        )


def test_session_clock_is_dst_safe_and_placebo_is_disjoint() -> None:
    ny = ZoneInfo("America/New_York")
    winter = _ms(datetime(2026, 1, 15, 7, 30, tzinfo=ny))
    summer = _ms(datetime(2026, 8, 15, 7, 30, tzinfo=ny))
    placebo = _ms(datetime(2026, 8, 15, 14, 30, tzinfo=ny))
    other = _ms(datetime(2026, 8, 15, 12, 30, tzinfo=ny))

    assert session_bucket(winter) == "PRIMARY"
    assert session_bucket(summer) == "PRIMARY"
    assert session_bucket(placebo) == "PLACEBO"
    assert session_bucket(other) == "OTHER"


def test_high_sweep_reclaim_creates_causal_short_absorption_event() -> None:
    event, = detect_liquidity_sweeps(_high_sweep())

    assert event.side == "HIGH"
    assert event.reversal_direction == -1
    assert event.session_bucket == "PRIMARY"
    assert event.aggressive_flow_confirmed is True
    assert event.displayed_absorption_confirmed is True
    assert event.absorption_confirmed is True
    assert event.decision_time_ms > event.confirmation_event_time_ms
    assert event.depth_replenishment_ratio == pytest.approx(1.25)


def test_low_sweep_reclaim_creates_causal_long_absorption_event() -> None:
    event, = detect_liquidity_sweeps(_low_sweep())

    assert event.side == "LOW"
    assert event.reversal_direction == 1
    assert event.absorption_confirmed is True


def test_candidate_controls_share_event_without_post_hoc_rescue() -> None:
    absorbed, = detect_liquidity_sweeps(_high_sweep())
    sweep_only, = detect_liquidity_sweeps(_high_sweep(absorbed=False))

    assert signal_for_event(absorbed, mode="session_absorption_reversal") == -1
    assert signal_for_event(absorbed, mode="all_day_absorption_reversal") == -1
    assert signal_for_event(absorbed, mode="session_sweep_only_reversal") == -1
    assert signal_for_event(absorbed, mode="session_absorption_continuation") == 1
    assert (
        signal_for_event(absorbed, mode="placebo_session_absorption_reversal")
        == 0
    )
    assert sweep_only.absorption_confirmed is False
    assert signal_for_event(sweep_only, mode="session_absorption_reversal") == 0
    assert signal_for_event(sweep_only, mode="session_sweep_only_reversal") == -1


def test_future_rows_cannot_change_an_already_confirmed_event() -> None:
    prefix = _high_sweep()
    first, = detect_liquidity_sweeps(prefix)
    last = prefix[-1]
    future = tuple(
        SweepFrame(
            instrument_id="BTCUSDT",
            event_time_ms=last.event_time_ms + index * CADENCE,
            decision_time_ms=last.decision_time_ms + index * CADENCE,
            mid_price=500.0 if index == 40 else 99.95,
            spread_bps=10.0,
            taker_imbalance_5s=1.0,
            depth_imbalance_5=-1.0,
            executable_depth_5_usd=125_000.0,
        )
        for index in range(1, 45)
    )

    extended = detect_liquidity_sweeps(prefix + future)

    assert extended[0] == first


def test_gap_in_formation_window_fails_closed_without_event() -> None:
    rows = list(_high_sweep())
    broken = rows[60]
    rows[60] = SweepFrame(
        instrument_id=broken.instrument_id,
        event_time_ms=broken.event_time_ms + 1,
        decision_time_ms=broken.decision_time_ms + 1,
        mid_price=broken.mid_price,
        spread_bps=broken.spread_bps,
        taker_imbalance_5s=broken.taker_imbalance_5s,
        depth_imbalance_5=broken.depth_imbalance_5,
        executable_depth_5_usd=broken.executable_depth_5_usd,
    )

    assert detect_liquidity_sweeps(rows) == ()


def test_equal_level_with_zero_spread_is_not_a_breach() -> None:
    rows = list(_high_sweep())
    breach = rows[120]
    rows[120] = _frame(
        120,
        start_ms=breach.event_time_ms - 120 * CADENCE,
        price=100.0,
        spread=0.0,
        taker=1.0,
    )

    assert detect_liquidity_sweeps(rows) == ()


def test_invalid_mode_and_mixed_instrument_series_are_rejected() -> None:
    event, = detect_liquidity_sweeps(_high_sweep())
    with pytest.raises(ValueError, match="unsupported"):
        signal_for_event(event, mode="bad")  # type: ignore[arg-type]

    rows = list(_high_sweep())
    row = rows[-1]
    rows[-1] = SweepFrame(
        "ETHUSDT",
        row.event_time_ms,
        row.decision_time_ms,
        row.mid_price,
        row.spread_bps,
        row.taker_imbalance_5s,
        row.depth_imbalance_5,
        row.executable_depth_5_usd,
    )
    with pytest.raises(ValueError, match="one instrument"):
        detect_liquidity_sweeps(rows)


def test_nonmonotone_knowledge_clock_is_rejected() -> None:
    rows = list(_high_sweep())
    row = rows[-2]
    rows[-2] = SweepFrame(
        row.instrument_id,
        row.event_time_ms,
        rows[-1].decision_time_ms + 1,
        row.mid_price,
        row.spread_bps,
        row.taker_imbalance_5s,
        row.depth_imbalance_5,
        row.executable_depth_5_usd,
    )

    with pytest.raises(ValueError, match="decision times"):
        detect_liquidity_sweeps(rows)
