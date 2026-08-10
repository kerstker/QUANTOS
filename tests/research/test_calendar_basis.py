from __future__ import annotations

import pytest

from quantos.contracts.cost import CostModel
from services.research.calendar_basis import (
    CalendarBasisError,
    CalendarBasisPeriod,
    QuarterlyMark,
    build_calendar_basis_periods,
    run_calendar_basis_backtest,
)
from services.research.funding_settlement import (
    HOUR_MS,
    SETTLEMENT_INTERVAL_MS,
    SettlementObservation,
    WEEKLY_SETTLEMENT_COUNT,
    WEEK_MS,
)


MONDAY = 4 * 24 * HOUR_MS


def _perpetual(symbol: str, bases: tuple[float, float]) -> list[SettlementObservation]:
    rows = []
    for index in range(WEEKLY_SETTLEMENT_COUNT + 1):
        event_time = MONDAY + index * SETTLEMENT_INTERVAL_MS
        week = 0 if index < WEEKLY_SETTLEMENT_COUNT else 1
        rows.append(
            SettlementObservation(
                instrument_id=symbol,
                settlement_time_ms=event_time,
                decision_time_ms=event_time + HOUR_MS,
                settled_rate=0.0001,
                decision_mark_price=100.0 * (1.0 + bases[week]),
            )
        )
    return rows


def _quarter(symbol: str, underlying: str, current: float, future: float, *, delivery: int):
    return [
        QuarterlyMark(symbol, underlying, delivery, MONDAY + HOUR_MS, current),
        QuarterlyMark(symbol, underlying, delivery, MONDAY + WEEK_MS + HOUR_MS, future),
    ]


def test_builds_causal_neutral_pair_and_includes_exact_funding() -> None:
    delivery = MONDAY + 4 * WEEK_MS
    periods = build_calendar_basis_periods(
        {
            "BTCUSDT": _perpetual("BTCUSDT", (0.0, 0.01)),
            "ETHUSDT": _perpetual("ETHUSDT", (0.0, -0.01)),
        },
        {
            "BTCUSDT_240628": _quarter("BTCUSDT_240628", "BTCUSDT", 110.0, 108.0, delivery=delivery),
            "ETHUSDT_240628": _quarter("ETHUSDT_240628", "ETHUSDT", 90.0, 92.0, delivery=delivery),
        },
        mode="basis_convergence",
    )

    assert len(periods) == 1
    period = periods[0]
    assert period.event_time == MONDAY + HOUR_MS
    assert period.instruments == (
        "BTCUSDT",
        "BTCUSDT_240628",
        "ETHUSDT",
        "ETHUSDT_240628",
    )
    assert period.signals == pytest.approx((0.1, -0.1, -0.1, 0.1))
    assert period.forward_returns[0] == pytest.approx(0.01 - 21 * 0.0001)
    assert period.forward_returns[1] == pytest.approx(108.0 / 110.0 - 1.0)
    assert period.forward_returns[2] == pytest.approx(-0.01 - 21 * 0.0001)
    assert period.forward_returns[3] == pytest.approx(92.0 / 90.0 - 1.0)


def test_divergence_control_only_reverses_signal() -> None:
    delivery = MONDAY + 4 * WEEK_MS
    kwargs = {
        "perpetual_history": {
            "BTCUSDT": _perpetual("BTCUSDT", (0.0, 0.0)),
            "ETHUSDT": _perpetual("ETHUSDT", (0.0, 0.0)),
        },
        "quarterly_history": {
            "BTCUSDT_240628": _quarter("BTCUSDT_240628", "BTCUSDT", 110.0, 108.0, delivery=delivery),
            "ETHUSDT_240628": _quarter("ETHUSDT_240628", "ETHUSDT", 90.0, 92.0, delivery=delivery),
        },
    }
    convergence = build_calendar_basis_periods(**kwargs, mode="basis_convergence")
    divergence = build_calendar_basis_periods(**kwargs, mode="basis_divergence_control")
    assert divergence[0].signals == pytest.approx(tuple(-x for x in convergence[0].signals))
    assert divergence[0].forward_returns == convergence[0].forward_returns


def test_uses_nearest_contract_that_survives_the_holding_week() -> None:
    safe_delivery = MONDAY + 4 * WEEK_MS
    expiring_delivery = MONDAY + WEEK_MS
    periods = build_calendar_basis_periods(
        {
            "BTCUSDT": _perpetual("BTCUSDT", (0.0, 0.0)),
            "ETHUSDT": _perpetual("ETHUSDT", (0.0, 0.0)),
        },
        {
            "BTCUSDT_OLD": [
                QuarterlyMark(
                    "BTCUSDT_OLD",
                    "BTCUSDT",
                    expiring_delivery,
                    MONDAY + HOUR_MS,
                    120.0,
                )
            ],
            "BTCUSDT_NEW": _quarter("BTCUSDT_NEW", "BTCUSDT", 110.0, 108.0, delivery=safe_delivery),
            "ETHUSDT_NEW": _quarter("ETHUSDT_NEW", "ETHUSDT", 90.0, 92.0, delivery=safe_delivery),
        },
        mode="basis_convergence",
    )
    assert "BTCUSDT_NEW" in periods[0].instruments
    assert "BTCUSDT_OLD" not in periods[0].instruments


def test_missing_funding_settlement_rejects_period_instead_of_imputing() -> None:
    delivery = MONDAY + 4 * WEEK_MS
    btc = _perpetual("BTCUSDT", (0.0, 0.0))
    del btc[10]
    with pytest.raises(CalendarBasisError, match="no causal calendar-basis periods"):
        build_calendar_basis_periods(
            {"BTCUSDT": btc, "ETHUSDT": _perpetual("ETHUSDT", (0.0, 0.0))},
            {
                "BTCUSDT_Q": _quarter("BTCUSDT_Q", "BTCUSDT", 110.0, 108.0, delivery=delivery),
                "ETHUSDT_Q": _quarter("ETHUSDT_Q", "ETHUSDT", 90.0, 92.0, delivery=delivery),
            },
            mode="basis_convergence",
        )


@pytest.mark.parametrize("bad", [0.0, float("nan"), float("inf")])
def test_quarterly_mark_rejects_nonpositive_or_nonfinite_prices(bad: float) -> None:
    with pytest.raises(CalendarBasisError):
        QuarterlyMark("BTCUSDT_Q", "BTCUSDT", MONDAY + 4 * WEEK_MS, MONDAY + HOUR_MS, bad)


def test_paired_backtest_reconciles_and_charges_contract_roll_turnover() -> None:
    periods = (
        CalendarBasisPeriod(
            instruments=("BTCUSDT", "BTC_Q1", "ETHUSDT", "ETH_Q1"),
            signals=(1.0, -1.0, 1.0, -1.0),
            target_weights=(0.25, -0.25, 0.25, -0.25),
            forward_returns=(0.01, 0.0, 0.02, 0.0),
            event_time=1,
        ),
        CalendarBasisPeriod(
            instruments=("BTCUSDT", "BTC_Q2", "ETHUSDT", "ETH_Q2"),
            signals=(1.0, -1.0, 1.0, -1.0),
            target_weights=(0.25, -0.25, 0.25, -0.25),
            forward_returns=(0.01, 0.0, 0.02, 0.0),
            event_time=2,
        ),
    )
    result = run_calendar_basis_backtest(
        periods,
        cost=CostModel(fee_bps=4.0, slippage_bps=2.0, impact_coef=0.0),
    )
    assert result.turnovers == pytest.approx((1.0, 1.0))
    assert result.gross_returns == pytest.approx((0.0075, 0.0075))
    assert result.costs == pytest.approx((0.0006, 0.0006))
    assert result.reconciliation_residual() < 1e-15


def test_paired_backtest_rejects_non_neutral_or_unassessed_impact() -> None:
    malformed = CalendarBasisPeriod(
        instruments=("BTCUSDT", "BTC_Q"),
        signals=(1.0, -1.0),
        target_weights=(0.5, -0.4),
        forward_returns=(0.01, 0.0),
        event_time=1,
    )
    with pytest.raises(CalendarBasisError, match="not dollar neutral"):
        run_calendar_basis_backtest((malformed,), cost=CostModel())

    neutral = CalendarBasisPeriod(
        instruments=("BTCUSDT", "BTC_Q"),
        signals=(1.0, -1.0),
        target_weights=(0.5, -0.5),
        forward_returns=(0.01, 0.0),
        event_time=1,
    )
    with pytest.raises(CalendarBasisError, match="impact"):
        run_calendar_basis_backtest(
            (neutral,),
            cost=CostModel(impact_coef=1.0),
        )
