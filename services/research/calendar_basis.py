"""Causal Binance perpetual/quarterly calendar-basis research primitives.

The module is deterministic and IO-free.  It turns already-observed completed
mark bars and exact settled funding events into paired weekly return periods.
It has no network, credential, broker, promotion, intent, or order path.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from quantos.contracts.cost import CostModel
from services.research.funding_settlement import (
    HOUR_MS,
    MONDAY_UTC_OFFSET_MS,
    SETTLEMENT_INTERVAL_MS,
    SettlementObservation,
    WEEKLY_SETTLEMENT_COUNT,
    WEEK_MS,
)


class CalendarBasisError(ValueError):
    """Calendar-basis evidence is malformed, noncausal, or too thin."""


@dataclass(frozen=True)
class QuarterlyMark:
    contract_id: str
    underlying_id: str
    delivery_time_ms: int
    decision_time_ms: int
    decision_mark_price: float

    def __post_init__(self) -> None:
        for name in ("contract_id", "underlying_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CalendarBasisError(f"{name} must be non-empty")
        if self.contract_id == self.underlying_id:
            raise CalendarBasisError("quarterly contract must differ from its perpetual")
        for name in ("delivery_time_ms", "decision_time_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CalendarBasisError(f"{name} must be a non-negative integer")
        if self.decision_time_ms >= self.delivery_time_ms:
            raise CalendarBasisError("quarterly mark must precede delivery")
        price = self.decision_mark_price
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(float(price))
            or float(price) <= 0.0
        ):
            raise CalendarBasisError(
                "decision_mark_price must be positive and finite"
            )


@dataclass(frozen=True)
class CalendarBasisPeriod:
    instruments: tuple[str, ...]
    signals: tuple[float, ...]
    target_weights: tuple[float, ...]
    forward_returns: tuple[float, ...]
    event_time: int


@dataclass(frozen=True)
class CalendarBasisResult:
    period_returns: tuple[float, ...]
    gross_returns: tuple[float, ...]
    costs: tuple[float, ...]
    turnovers: tuple[float, ...]

    def reconciliation_residual(self) -> float:
        return max(
            (
                abs(gross - cost - net)
                for gross, cost, net in zip(
                    self.gross_returns, self.costs, self.period_returns
                )
            ),
            default=0.0,
        )


def _perpetual_rows(
    history: Mapping[str, Sequence[SettlementObservation]],
) -> dict[str, dict[int, SettlementObservation]]:
    if not isinstance(history, Mapping):
        raise CalendarBasisError("perpetual_history must be a mapping")
    result: dict[str, dict[int, SettlementObservation]] = {}
    for raw_instrument, raw_rows in sorted(history.items()):
        instrument = str(raw_instrument)
        by_time: dict[int, SettlementObservation] = {}
        previous = -1
        for row in tuple(raw_rows):
            if not isinstance(row, SettlementObservation):
                raise CalendarBasisError(
                    "perpetual rows must be SettlementObservation"
                )
            if row.instrument_id != instrument:
                raise CalendarBasisError("perpetual key and row instrument differ")
            if row.settlement_time_ms <= previous:
                raise CalendarBasisError(
                    "perpetual observations must be strictly increasing"
                )
            if row.settlement_time_ms % SETTLEMENT_INTERVAL_MS != 0:
                raise CalendarBasisError(
                    "perpetual settlement is off the eight-hour schedule"
                )
            previous = row.settlement_time_ms
            by_time[row.settlement_time_ms] = row
        if by_time:
            result[instrument] = by_time
    return result


def _quarterly_rows(
    history: Mapping[str, Sequence[QuarterlyMark]],
) -> dict[str, tuple[str, int, dict[int, QuarterlyMark]]]:
    if not isinstance(history, Mapping):
        raise CalendarBasisError("quarterly_history must be a mapping")
    result: dict[str, tuple[str, int, dict[int, QuarterlyMark]]] = {}
    for raw_contract, raw_rows in sorted(history.items()):
        contract = str(raw_contract)
        by_time: dict[int, QuarterlyMark] = {}
        underlying: str | None = None
        delivery: int | None = None
        previous = -1
        for row in tuple(raw_rows):
            if not isinstance(row, QuarterlyMark):
                raise CalendarBasisError("quarterly rows must be QuarterlyMark")
            if row.contract_id != contract:
                raise CalendarBasisError("quarterly key and row contract differ")
            if underlying is None:
                underlying = row.underlying_id
                delivery = row.delivery_time_ms
            elif (
                row.underlying_id != underlying
                or row.delivery_time_ms != delivery
            ):
                raise CalendarBasisError("quarterly contract metadata drifted")
            if row.decision_time_ms <= previous:
                raise CalendarBasisError(
                    "quarterly observations must be strictly increasing"
                )
            previous = row.decision_time_ms
            by_time[row.decision_time_ms] = row
        if by_time and underlying is not None and delivery is not None:
            result[contract] = (underlying, delivery, by_time)
    return result


def _eligible_contract(
    quarterly: Mapping[str, tuple[str, int, Mapping[int, QuarterlyMark]]],
    *,
    underlying: str,
    decision_time: int,
    next_decision_time: int,
) -> tuple[str, QuarterlyMark, QuarterlyMark] | None:
    eligible: list[tuple[int, str, QuarterlyMark, QuarterlyMark]] = []
    for contract, (bound_underlying, delivery, rows) in quarterly.items():
        if bound_underlying != underlying or delivery <= next_decision_time:
            continue
        current = rows.get(decision_time)
        future = rows.get(next_decision_time)
        if current is not None and future is not None:
            eligible.append((delivery, contract, current, future))
    if not eligible:
        return None
    _delivery, contract, current, future = min(eligible)
    return contract, current, future


def build_calendar_basis_periods(
    perpetual_history: Mapping[str, Sequence[SettlementObservation]],
    quarterly_history: Mapping[str, Sequence[QuarterlyMark]],
    *,
    mode: Literal["basis_convergence", "basis_divergence_control"],
    min_underlyings: int = 2,
) -> tuple[CalendarBasisPeriod, ...]:
    """Build fixed, non-overlapping weekly paired relative-value periods."""
    if mode not in {"basis_convergence", "basis_divergence_control"}:
        raise CalendarBasisError("unknown calendar-basis mode")
    if (
        isinstance(min_underlyings, bool)
        or not isinstance(min_underlyings, int)
        or min_underlyings < 1
    ):
        raise CalendarBasisError("min_underlyings must be positive")

    perpetual = _perpetual_rows(perpetual_history)
    quarterly = _quarterly_rows(quarterly_history)
    monday_events = sorted(
        event_time
        for event_time in {
            timestamp for rows in perpetual.values() for timestamp in rows
        }
        if (event_time - MONDAY_UTC_OFFSET_MS) % WEEK_MS == 0
    )
    periods: list[CalendarBasisPeriod] = []
    direction_multiplier = 1.0 if mode == "basis_convergence" else -1.0

    for event_time in monday_events:
        future_event_time = event_time + WEEK_MS
        decision_time = event_time + HOUR_MS
        next_decision_time = future_event_time + HOUR_MS
        cashflow_times = tuple(
            event_time + offset * SETTLEMENT_INTERVAL_MS
            for offset in range(1, WEEKLY_SETTLEMENT_COUNT + 1)
        )
        pairs: list[
            tuple[str, str, float, float, float, float]
        ] = []
        for underlying, rows in sorted(perpetual.items()):
            if event_time not in rows or any(
                timestamp not in rows for timestamp in cashflow_times
            ):
                continue
            selected = _eligible_contract(
                quarterly,
                underlying=underlying,
                decision_time=decision_time,
                next_decision_time=next_decision_time,
            )
            if selected is None:
                continue
            contract, current_quarter, future_quarter = selected
            current_perpetual = rows[event_time]
            future_perpetual = rows[future_event_time]
            basis = (
                float(current_quarter.decision_mark_price)
                / float(current_perpetual.decision_mark_price)
                - 1.0
            )
            funding = sum(
                float(rows[timestamp].settled_rate)
                for timestamp in cashflow_times
            )
            perpetual_return = (
                float(future_perpetual.decision_mark_price)
                / float(current_perpetual.decision_mark_price)
                - 1.0
                - funding
            )
            quarterly_return = (
                float(future_quarter.decision_mark_price)
                / float(current_quarter.decision_mark_price)
                - 1.0
            )
            if not all(
                math.isfinite(value)
                for value in (basis, perpetual_return, quarterly_return)
            ):
                raise CalendarBasisError("calendar-basis return is non-finite")
            pairs.append(
                (
                    underlying,
                    contract,
                    basis,
                    -basis,
                    perpetual_return,
                    quarterly_return,
                )
            )

        if len(pairs) < min_underlyings:
            continue
        per_leg = 1.0 / (2.0 * len(pairs))
        instruments: list[str] = []
        signals: list[float] = []
        weights: list[float] = []
        returns: list[float] = []
        for (
            underlying,
            contract,
            perpetual_signal,
            quarterly_signal,
            perpetual_return,
            quarterly_return,
        ) in pairs:
            pair_direction = (
                1.0
                if perpetual_signal > 0.0
                else (-1.0 if perpetual_signal < 0.0 else 0.0)
            ) * direction_multiplier
            instruments.extend((underlying, contract))
            signals.extend(
                (
                    perpetual_signal * direction_multiplier,
                    quarterly_signal * direction_multiplier,
                )
            )
            weights.extend((pair_direction * per_leg, -pair_direction * per_leg))
            returns.extend((perpetual_return, quarterly_return))
        periods.append(
            CalendarBasisPeriod(
                instruments=tuple(instruments),
                signals=tuple(signals),
                target_weights=tuple(weights),
                forward_returns=tuple(returns),
                event_time=decision_time,
            )
        )

    if not periods:
        raise CalendarBasisError("no causal calendar-basis periods")
    return tuple(periods)


def run_calendar_basis_backtest(
    periods: Sequence[CalendarBasisPeriod],
    *,
    cost: CostModel,
) -> CalendarBasisResult:
    """Replay exact paired target weights with turnover-based linear costs."""
    if not isinstance(cost, CostModel):
        raise TypeError("cost must be CostModel")
    previous: dict[str, float] = {}
    net_returns: list[float] = []
    gross_returns: list[float] = []
    costs: list[float] = []
    turnovers: list[float] = []
    for period in tuple(periods):
        if not isinstance(period, CalendarBasisPeriod):
            raise TypeError("periods must contain CalendarBasisPeriod")
        lengths = {
            len(period.instruments),
            len(period.signals),
            len(period.target_weights),
            len(period.forward_returns),
        }
        if len(lengths) != 1 or not period.instruments:
            raise CalendarBasisError("calendar-basis period fields must align")
        if len(set(period.instruments)) != len(period.instruments):
            raise CalendarBasisError("calendar-basis instruments must be unique")
        if not all(
            math.isfinite(float(value))
            for value in (
                *period.signals,
                *period.target_weights,
                *period.forward_returns,
            )
        ):
            raise CalendarBasisError("calendar-basis period is non-finite")
        if abs(sum(period.target_weights)) > 1e-12:
            raise CalendarBasisError("calendar-basis target is not dollar neutral")
        if sum(abs(value) for value in period.target_weights) > 1.0 + 1e-12:
            raise CalendarBasisError("calendar-basis target exceeds fixed gross")

        target = dict(zip(period.instruments, period.target_weights, strict=True))
        gross_return = sum(
            target[instrument] * float(realized)
            for instrument, realized in zip(
                period.instruments, period.forward_returns, strict=True
            )
        )
        turnover = sum(
            abs(target.get(instrument, 0.0) - previous.get(instrument, 0.0))
            for instrument in sorted(set(target) | set(previous))
        )
        linear_cost = turnover * (cost.fee_bps + cost.slippage_bps) / 10_000.0
        if cost.impact_coef > 0.0:
            raise CalendarBasisError(
                "calendar-basis impact requires real ADV and is not assessed"
            )
        gross_returns.append(gross_return)
        costs.append(linear_cost)
        turnovers.append(turnover)
        net_returns.append(gross_return - linear_cost)
        previous = target
    return CalendarBasisResult(
        period_returns=tuple(net_returns),
        gross_returns=tuple(gross_returns),
        costs=tuple(costs),
        turnovers=tuple(turnovers),
    )


__all__ = [
    "CalendarBasisError",
    "CalendarBasisPeriod",
    "CalendarBasisResult",
    "QuarterlyMark",
    "build_calendar_basis_periods",
    "run_calendar_basis_backtest",
]
