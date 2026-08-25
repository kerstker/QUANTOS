"""Pure, outcome-blind selection for the frozen borrow-pressure experiment."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


BORROW_PERCENTILE_MIN = 0.80
CHEAP_BORROW_PERCENTILE_MAX = 0.20
BORROW_ACCELERATION_MIN = 0.0
FUNDING_RATE_MAX = -0.00005
BASIS_BPS_MAX = -2.0
TRAILING_RETURN_MAX = -0.02
ENTRY_SPREAD_BPS_MAX = 5.0


class BorrowPressureError(ValueError):
    """A purported decision cross-section violates the frozen PIT contract."""


@dataclass(frozen=True)
class BorrowPressureObservation:
    instrument_id: str
    decision_at_ms: int
    borrow_knowledge_time_ms: int
    binance_knowledge_time_ms: int
    average_borrow_rate: float
    estimated_borrow_rate: float
    funding_rate: float
    basis_bps: float
    trailing_24h_return: float
    spread_bps: float


@dataclass(frozen=True)
class BorrowPressureSelection:
    instrument_id: str
    decision_at_ms: int
    borrow_percentile: float
    eligible_modes: tuple[str, ...]
    primary_blockers: tuple[str, ...]


def _validate_number(value: object, *, name: str, non_negative: bool = False) -> float:
    if isinstance(value, bool):
        raise BorrowPressureError(f"{name} is invalid")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BorrowPressureError(f"{name} is not numeric") from exc
    if not math.isfinite(result) or (non_negative and result < 0.0):
        raise BorrowPressureError(f"{name} is outside its domain")
    return result


def _validate_observation(
    item: BorrowPressureObservation,
    *,
    preregistered_at_ms: int,
) -> None:
    if not isinstance(item, BorrowPressureObservation):
        raise BorrowPressureError("cross-section contains an invalid observation")
    if not item.instrument_id or item.instrument_id != item.instrument_id.upper():
        raise BorrowPressureError("instrument_id must be canonical uppercase")
    for value, name in (
        (item.decision_at_ms, "decision_at_ms"),
        (item.borrow_knowledge_time_ms, "borrow_knowledge_time_ms"),
        (item.binance_knowledge_time_ms, "binance_knowledge_time_ms"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BorrowPressureError(f"{name} is invalid")
    if not (
        preregistered_at_ms < item.borrow_knowledge_time_ms <= item.decision_at_ms
        and preregistered_at_ms
        < item.binance_knowledge_time_ms
        <= item.decision_at_ms
    ):
        raise BorrowPressureError("observation violates forward knowledge time")
    _validate_number(
        item.average_borrow_rate,
        name="average_borrow_rate",
        non_negative=True,
    )
    _validate_number(
        item.estimated_borrow_rate,
        name="estimated_borrow_rate",
        non_negative=True,
    )
    _validate_number(item.funding_rate, name="funding_rate")
    _validate_number(item.basis_bps, name="basis_bps")
    _validate_number(item.trailing_24h_return, name="trailing_24h_return")
    _validate_number(item.spread_bps, name="spread_bps", non_negative=True)


def _percentile(values: tuple[float, ...], value: float) -> float:
    below = sum(candidate < value for candidate in values)
    equal = sum(candidate == value for candidate in values)
    return (below + 0.5 * equal) / len(values)


def evaluate_cross_section(
    observations: Iterable[BorrowPressureObservation],
    *,
    preregistered_at_ms: int,
) -> tuple[BorrowPressureSelection, ...]:
    """Classify frozen candidate/control episodes without reading future labels."""
    if (
        isinstance(preregistered_at_ms, bool)
        or not isinstance(preregistered_at_ms, int)
        or preregistered_at_ms < 0
    ):
        raise BorrowPressureError("preregistered_at_ms is invalid")
    rows = tuple(observations)
    if not rows:
        raise BorrowPressureError("decision cross-section is empty")
    for item in rows:
        _validate_observation(item, preregistered_at_ms=preregistered_at_ms)
    instruments = tuple(item.instrument_id for item in rows)
    if len(instruments) != len(set(instruments)):
        raise BorrowPressureError("decision cross-section contains duplicate instruments")
    decision_times = {item.decision_at_ms for item in rows}
    if len(decision_times) != 1:
        raise BorrowPressureError("decision cross-section has mixed decision clocks")
    borrow_values = tuple(float(item.average_borrow_rate) for item in rows)
    selections = []
    for item in sorted(rows, key=lambda value: value.instrument_id):
        percentile = _percentile(borrow_values, float(item.average_borrow_rate))
        high_borrow = percentile >= BORROW_PERCENTILE_MIN
        cheap_borrow = percentile <= CHEAP_BORROW_PERCENTILE_MAX
        accelerating = (
            item.estimated_borrow_rate - item.average_borrow_rate
            >= BORROW_ACCELERATION_MIN
        )
        negative_funding = item.funding_rate <= FUNDING_RATE_MAX
        negative_basis = item.basis_bps <= BASIS_BPS_MAX
        price_pressure = item.trailing_24h_return <= TRAILING_RETURN_MAX
        executable_spread = item.spread_bps <= ENTRY_SPREAD_BPS_MAX
        modes = []
        primary = all(
            (
                high_borrow,
                accelerating,
                negative_funding,
                negative_basis,
                price_pressure,
                executable_spread,
            )
        )
        if primary:
            modes.extend(("PRIMARY", "SIGN_CONTROL"))
        if high_borrow and accelerating and price_pressure and executable_spread:
            modes.append("BORROW_ABLATION_CONTROL")
        if negative_funding and negative_basis and price_pressure and executable_spread:
            modes.append("FUNDING_ABLATION_CONTROL")
        if cheap_borrow and negative_funding and negative_basis and price_pressure and executable_spread:
            modes.append("SCARCITY_CONTROL")
        blockers = tuple(
            name
            for passed, name in (
                (high_borrow, "BORROW_RANK"),
                (accelerating, "BORROW_ACCELERATION"),
                (negative_funding, "NEGATIVE_FUNDING"),
                (negative_basis, "NEGATIVE_BASIS"),
                (price_pressure, "PRICE_PRESSURE"),
                (executable_spread, "EXECUTABLE_SPREAD"),
            )
            if not passed
        )
        selections.append(
            BorrowPressureSelection(
                instrument_id=item.instrument_id,
                decision_at_ms=item.decision_at_ms,
                borrow_percentile=percentile,
                eligible_modes=tuple(modes),
                primary_blockers=blockers,
            )
        )
    return tuple(selections)


__all__ = [
    "BASIS_BPS_MAX",
    "BORROW_ACCELERATION_MIN",
    "BORROW_PERCENTILE_MIN",
    "BorrowPressureError",
    "BorrowPressureObservation",
    "BorrowPressureSelection",
    "CHEAP_BORROW_PERCENTILE_MAX",
    "ENTRY_SPREAD_BPS_MAX",
    "FUNDING_RATE_MAX",
    "TRAILING_RETURN_MAX",
    "evaluate_cross_section",
]
