"""Deterministic multi-venue routing primitive for research and dry-run use.

This module computes an inert route plan from already-supplied order-book
snapshots.  It performs no I/O, imports no venue SDK, holds no credentials, and
cannot submit an order.  The returned plan is always marked ``simulation_only``.

The objective is side-aware all-in price:

* BUY: raw ask + taker fee + configured latency-risk penalty (lower is better)
* SELL: raw bid - taker fee - configured latency-risk penalty (higher is better)

Venue concentration, quote freshness, limit prices, depth and a minimum fill
ratio are load-bearing constraints.  Inputs and tie-breaks are deterministic so
the same snapshots always produce the same route plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Iterable

from quantos.contracts.trading import OrderIntent, OrderSide, OrderType


_BPS = Decimal("10000")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class RoutingError(ValueError):
    """Raised when a routing request or market snapshot is structurally invalid."""


class RouteStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.price, Decimal) or not self.price.is_finite() or self.price <= 0:
            raise RoutingError("book level price must be a finite positive Decimal")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise RoutingError("book level quantity must be a positive integer")


@dataclass(frozen=True)
class VenueBook:
    venue_id: str
    observed_at_ms: int
    taker_fee_bps: Decimal
    latency_ms: int
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.venue_id, str) or not self.venue_id.strip():
            raise RoutingError("venue_id must be non-empty")
        if type(self.observed_at_ms) is not int or self.observed_at_ms < 0:
            raise RoutingError("observed_at_ms must be a non-negative integer")
        if (
            not isinstance(self.taker_fee_bps, Decimal)
            or not self.taker_fee_bps.is_finite()
            or self.taker_fee_bps < 0
        ):
            raise RoutingError("taker_fee_bps must be a finite non-negative Decimal")
        if type(self.latency_ms) is not int or self.latency_ms < 0:
            raise RoutingError("latency_ms must be a non-negative integer")
        if not isinstance(self.bids, tuple) or not isinstance(self.asks, tuple):
            raise RoutingError("bids and asks must be tuples")
        if any(
            self.bids[index].price <= self.bids[index + 1].price
            for index in range(len(self.bids) - 1)
        ):
            raise RoutingError("bids must be strictly descending by price")
        if any(
            self.asks[index].price >= self.asks[index + 1].price
            for index in range(len(self.asks) - 1)
        ):
            raise RoutingError("asks must be strictly ascending by price")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise RoutingError("venue book must not be crossed or locked")


@dataclass(frozen=True)
class SORPolicy:
    max_quote_age_ms: int
    min_fill_fraction: Decimal = _ONE
    max_venue_fraction: Decimal = _ONE
    latency_penalty_bps_per_ms: Decimal = _ZERO

    def __post_init__(self) -> None:
        if type(self.max_quote_age_ms) is not int or self.max_quote_age_ms < 0:
            raise RoutingError("max_quote_age_ms must be a non-negative integer")
        for name, value in (
            ("min_fill_fraction", self.min_fill_fraction),
            ("max_venue_fraction", self.max_venue_fraction),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or not (_ZERO < value <= _ONE):
                raise RoutingError(f"{name} must be a finite Decimal in (0, 1]")
        penalty = self.latency_penalty_bps_per_ms
        if not isinstance(penalty, Decimal) or not penalty.is_finite() or penalty < 0:
            raise RoutingError(
                "latency_penalty_bps_per_ms must be a finite non-negative Decimal"
            )


@dataclass(frozen=True)
class RouteSlice:
    venue_id: str
    quantity: int
    raw_price: Decimal
    all_in_price: Decimal
    fee_amount: Decimal
    latency_penalty_amount: Decimal
    quote_age_ms: int


@dataclass(frozen=True)
class SimulatedRoutePlan:
    instrument_id: str
    side: OrderSide
    requested_quantity: int
    routed_quantity: int
    residual_quantity: int
    status: RouteStatus
    slices: tuple[RouteSlice, ...]
    excluded_venues: tuple[tuple[str, str], ...]
    expected_raw_notional: Decimal
    expected_all_in_notional: Decimal
    expected_fees: Decimal
    expected_latency_penalty: Decimal
    simulation_only: bool = True


@dataclass(frozen=True)
class _Candidate:
    venue_id: str
    raw_price: Decimal
    all_in_price: Decimal
    available_quantity: int
    fee_rate: Decimal
    latency_penalty_rate: Decimal
    quote_age_ms: int
    level_index: int


def _required_fill(quantity: int, fraction: Decimal) -> int:
    return int((Decimal(quantity) * fraction).to_integral_value(rounding=ROUND_CEILING))


def _venue_cap(quantity: int, fraction: Decimal) -> int:
    return int((Decimal(quantity) * fraction).to_integral_value(rounding=ROUND_FLOOR))


def _empty_plan(
    intent: OrderIntent,
    excluded: tuple[tuple[str, str], ...],
) -> SimulatedRoutePlan:
    return SimulatedRoutePlan(
        instrument_id=intent.instrument_id,
        side=intent.side,
        requested_quantity=intent.quantity,
        routed_quantity=0,
        residual_quantity=intent.quantity,
        status=RouteStatus.REJECTED,
        slices=(),
        excluded_venues=excluded,
        expected_raw_notional=_ZERO,
        expected_all_in_notional=_ZERO,
        expected_fees=_ZERO,
        expected_latency_penalty=_ZERO,
    )


def _candidates_for_book(
    intent: OrderIntent,
    book: VenueBook,
    *,
    quote_age_ms: int,
    policy: SORPolicy,
) -> list[_Candidate]:
    levels = book.asks if intent.side == OrderSide.BUY else book.bids
    fee_rate = book.taker_fee_bps / _BPS
    latency_rate = Decimal(book.latency_ms) * policy.latency_penalty_bps_per_ms / _BPS
    candidates: list[_Candidate] = []
    for index, level in enumerate(levels):
        if intent.order_type == OrderType.LIMIT:
            assert intent.limit_price is not None
            limit = Decimal(str(intent.limit_price))
            if intent.side == OrderSide.BUY and level.price > limit:
                continue
            if intent.side == OrderSide.SELL and level.price < limit:
                continue
        adjustment = level.price * (fee_rate + latency_rate)
        all_in = (
            level.price + adjustment
            if intent.side == OrderSide.BUY
            else level.price - adjustment
        )
        candidates.append(
            _Candidate(
                venue_id=book.venue_id,
                raw_price=level.price,
                all_in_price=all_in,
                available_quantity=level.quantity,
                fee_rate=fee_rate,
                latency_penalty_rate=latency_rate,
                quote_age_ms=quote_age_ms,
                level_index=index,
            )
        )
    return candidates


def route_simulated_intent(
    intent: OrderIntent,
    books: Iterable[VenueBook],
    *,
    as_of_ms: int,
    policy: SORPolicy,
) -> SimulatedRoutePlan:
    """Return a deterministic, non-executable multi-venue route plan.

    Stale books and books without executable side depth are excluded.  Future
    snapshots and duplicate venue IDs fail closed.  If available liquidity does
    not satisfy ``min_fill_fraction``, no slices are returned.
    """

    if type(as_of_ms) is not int or as_of_ms < 0:
        raise RoutingError("as_of_ms must be a non-negative integer")
    if not isinstance(intent, OrderIntent):
        raise RoutingError("intent must be an OrderIntent")
    if not isinstance(policy, SORPolicy):
        raise RoutingError("policy must be an SORPolicy")

    materialized = tuple(books)
    venue_ids = [book.venue_id for book in materialized]
    if len(venue_ids) != len(set(venue_ids)):
        raise RoutingError("duplicate venue_id in routing snapshot")

    excluded: list[tuple[str, str]] = []
    candidates: list[_Candidate] = []
    for book in sorted(materialized, key=lambda item: item.venue_id):
        if book.observed_at_ms > as_of_ms:
            raise RoutingError(f"future quote for venue {book.venue_id}")
        age = as_of_ms - book.observed_at_ms
        if age > policy.max_quote_age_ms:
            excluded.append((book.venue_id, "STALE_QUOTE"))
            continue
        venue_candidates = _candidates_for_book(
            intent,
            book,
            quote_age_ms=age,
            policy=policy,
        )
        if not venue_candidates:
            excluded.append((book.venue_id, "NO_EXECUTABLE_DEPTH"))
            continue
        candidates.extend(venue_candidates)

    if not candidates:
        return _empty_plan(intent, tuple(excluded))

    if intent.side == OrderSide.BUY:
        candidates.sort(
            key=lambda item: (
                item.all_in_price,
                item.raw_price,
                item.venue_id,
                item.level_index,
            )
        )
    else:
        candidates.sort(
            key=lambda item: (
                -item.all_in_price,
                -item.raw_price,
                item.venue_id,
                item.level_index,
            )
        )

    cap = _venue_cap(intent.quantity, policy.max_venue_fraction)
    if cap <= 0:
        return _empty_plan(intent, tuple(excluded) + (("*", "VENUE_CAP_ZERO"),))

    remaining = intent.quantity
    by_venue: dict[str, int] = {}
    provisional: list[RouteSlice] = []
    for candidate in candidates:
        venue_remaining = cap - by_venue.get(candidate.venue_id, 0)
        take = min(remaining, candidate.available_quantity, venue_remaining)
        if take <= 0:
            continue
        raw_notional = candidate.raw_price * take
        provisional.append(
            RouteSlice(
                venue_id=candidate.venue_id,
                quantity=take,
                raw_price=candidate.raw_price,
                all_in_price=candidate.all_in_price,
                fee_amount=raw_notional * candidate.fee_rate,
                latency_penalty_amount=raw_notional * candidate.latency_penalty_rate,
                quote_age_ms=candidate.quote_age_ms,
            )
        )
        by_venue[candidate.venue_id] = by_venue.get(candidate.venue_id, 0) + take
        remaining -= take
        if remaining == 0:
            break

    routed = intent.quantity - remaining
    if routed < _required_fill(intent.quantity, policy.min_fill_fraction):
        return _empty_plan(
            intent,
            tuple(excluded) + (("*", "INSUFFICIENT_LIQUIDITY"),),
        )

    slices = tuple(provisional)
    raw_notional = sum(
        (item.raw_price * item.quantity for item in slices),
        start=_ZERO,
    )
    all_in_notional = sum(
        (item.all_in_price * item.quantity for item in slices),
        start=_ZERO,
    )
    fees = sum((item.fee_amount for item in slices), start=_ZERO)
    latency = sum((item.latency_penalty_amount for item in slices), start=_ZERO)
    return SimulatedRoutePlan(
        instrument_id=intent.instrument_id,
        side=intent.side,
        requested_quantity=intent.quantity,
        routed_quantity=routed,
        residual_quantity=remaining,
        status=RouteStatus.COMPLETE if remaining == 0 else RouteStatus.PARTIAL,
        slices=slices,
        excluded_venues=tuple(excluded),
        expected_raw_notional=raw_notional,
        expected_all_in_notional=all_in_notional,
        expected_fees=fees,
        expected_latency_penalty=latency,
    )


def validate_simulated_route_plan(
    plan: SimulatedRoutePlan,
    intent: OrderIntent,
) -> tuple[str, ...]:
    """Validate a route plan before another subsystem consumes it.

    Dataclasses are intentionally easy to serialize, so a caller could
    construct or alter one without using ``route_simulated_intent``.  The
    dry-run pipeline uses this independent consistency check as its fail-closed
    boundary.
    """

    if not isinstance(plan, SimulatedRoutePlan):
        return ("ROUTE_PLAN_TYPE_INVALID",)
    if not isinstance(intent, OrderIntent):
        return ("ROUTE_INTENT_TYPE_INVALID",)
    reasons: list[str] = []
    if plan.simulation_only is not True:
        reasons.append("ROUTE_NOT_SIMULATION_ONLY")
    if plan.instrument_id != intent.instrument_id:
        reasons.append("ROUTE_INSTRUMENT_MISMATCH")
    if plan.side != intent.side:
        reasons.append("ROUTE_SIDE_MISMATCH")
    if plan.requested_quantity != intent.quantity:
        reasons.append("ROUTE_REQUESTED_QUANTITY_MISMATCH")
    if (
        type(plan.routed_quantity) is not int
        or type(plan.residual_quantity) is not int
        or plan.routed_quantity < 0
        or plan.residual_quantity < 0
        or plan.routed_quantity + plan.residual_quantity
        != plan.requested_quantity
    ):
        reasons.append("ROUTE_QUANTITY_ACCOUNTING_INVALID")

    raw_notional = _ZERO
    all_in_notional = _ZERO
    fees = _ZERO
    latency = _ZERO
    slice_quantity = 0
    for item in plan.slices:
        if not isinstance(item, RouteSlice):
            reasons.append("ROUTE_SLICE_TYPE_INVALID")
            continue
        if not isinstance(item.venue_id, str) or not item.venue_id.strip():
            reasons.append("ROUTE_SLICE_VENUE_INVALID")
        if type(item.quantity) is not int or item.quantity <= 0:
            reasons.append("ROUTE_SLICE_QUANTITY_INVALID")
            continue
        monetary = (
            item.raw_price,
            item.all_in_price,
            item.fee_amount,
            item.latency_penalty_amount,
        )
        if any(
            not isinstance(value, Decimal) or not value.is_finite()
            for value in monetary
        ):
            reasons.append("ROUTE_SLICE_MONEY_INVALID")
            continue
        if (
            item.raw_price <= 0
            or item.all_in_price <= 0
            or item.fee_amount < 0
            or item.latency_penalty_amount < 0
        ):
            reasons.append("ROUTE_SLICE_MONEY_INVALID")
            continue
        if type(item.quote_age_ms) is not int or item.quote_age_ms < 0:
            reasons.append("ROUTE_SLICE_AGE_INVALID")
        raw = item.raw_price * item.quantity
        all_in = item.all_in_price * item.quantity
        adjustment = item.fee_amount + item.latency_penalty_amount
        expected_all_in = (
            raw + adjustment if intent.side == OrderSide.BUY else raw - adjustment
        )
        if all_in != expected_all_in:
            reasons.append("ROUTE_SLICE_ALL_IN_IDENTITY_INVALID")
        raw_notional += raw
        all_in_notional += all_in
        fees += item.fee_amount
        latency += item.latency_penalty_amount
        slice_quantity += item.quantity

    if slice_quantity != plan.routed_quantity:
        reasons.append("ROUTE_SLICE_QUANTITY_MISMATCH")
    if (
        plan.expected_raw_notional != raw_notional
        or plan.expected_all_in_notional != all_in_notional
        or plan.expected_fees != fees
        or plan.expected_latency_penalty != latency
    ):
        reasons.append("ROUTE_TOTALS_MISMATCH")
    if plan.status == RouteStatus.COMPLETE and plan.residual_quantity != 0:
        reasons.append("ROUTE_COMPLETE_WITH_RESIDUAL")
    elif plan.status == RouteStatus.PARTIAL and (
        plan.routed_quantity <= 0 or plan.residual_quantity <= 0
    ):
        reasons.append("ROUTE_PARTIAL_STATE_INVALID")
    elif plan.status == RouteStatus.REJECTED and (
        plan.routed_quantity != 0 or plan.slices
    ):
        reasons.append("ROUTE_REJECTED_WITH_ALLOCATION")
    elif not isinstance(plan.status, RouteStatus):
        reasons.append("ROUTE_STATUS_INVALID")
    return tuple(dict.fromkeys(reasons))
