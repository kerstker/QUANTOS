"""Causal forward simulation over the canonical Decimal perpetual ledger.

Strategy callbacks observe a frozen prefix and produce intents, never fills.
Frames carry synchronously observed venue mark/settlement events; delayed venue
settlements need a separate adapter and are not silently moved to receipt time.
Quotes distinguish venue event time from first usable time. Only strictly later
executable quotes can fill an order. This models aggressive top-of-book liquidity,
not passive queue priority. Bid/ask and adverse slippage are embedded in price;
only the explicit fee is debited as a separate cost.

One immutable rule/funding-policy epoch per instrument is supported. Source
authenticity, venue-specific queue calibration and economic promotion remain
external blockers: every result is TECHNICAL_VALIDATION_ONLY.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import copy
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeVar

from services.execution.authoritative_perpetual_backtest import (
    CapacitySnapshot, FundingPolicySnapshot, HistoricalContractRule,
)
from services.execution.backtest_authority import exact_identity_hash
from services.execution.venue_order_filters import VenueOrderFilters
from services.execution.perpetual_ledger_v2 import (
    FundingEvent, InsufficientMargin, LinearPerpetualLedger, MarginMode,
    MarkEvent, OrderFillEvent, PositionView, QuantityRuleViolation,
    canonical_decimal, ledger_decimal_context,
)

ZERO, ONE = Decimal("0"), Decimal("1")
SCHEMA = "LINEAR_PERPETUAL_FORWARD_SIMULATION_V3"
RESERVATION_POLICY = "CROSS_GROSS_PENDING_MARGIN_FEES_ADVERSE_BUFFER_V1"
_T = TypeVar("_T")
_ORDER_TRANSITIONS = {
    "OPEN": frozenset({"PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED", "REJECTED"}),
    "PARTIALLY_FILLED": frozenset({"PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED", "REJECTED"}),
}

class _ProjectedRiskRejected(RuntimeError):
    """Internal transaction abort for an uncommitted simulated fill."""

def _decimal(value: Decimal, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < ZERO or (positive and value == ZERO):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return value

def _time(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value

def _name(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty")

def _canonical(value):
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if isinstance(value, MarginMode):
        return value.value
    return value

@dataclass(frozen=True)
class ExecutableQuote:
    instrument_id: str
    event_time: int
    available_time: int
    bid_price: Decimal
    ask_price: Decimal
    bid_quantity: Decimal
    ask_quantity: Decimal
    venue_id: str = "BINANCE_USDM"

    def __post_init__(self) -> None:
        _name(self.instrument_id, "instrument_id")
        _name(self.venue_id, "venue_id")
        _time(self.event_time, "event_time")
        _time(self.available_time, "available_time")
        if self.available_time < self.event_time:
            raise ValueError("quote is available before its venue event")
        for name in ("bid_price", "ask_price"):
            _decimal(getattr(self, name), name, positive=True)
        for name in ("bid_quantity", "ask_quantity"):
            _decimal(getattr(self, name), name)
        if self.bid_price > self.ask_price:
            raise ValueError("crossed bid/ask quote")

@dataclass(frozen=True)
class SimulationFrame:
    timestamp: int
    quotes: tuple[ExecutableQuote, ...] = ()
    market_events: tuple[MarkEvent | FundingEvent, ...] = ()
    capacity_snapshots: tuple[CapacitySnapshot, ...] = ()

    def __post_init__(self) -> None:
        _time(self.timestamp, "timestamp")
        for name in ("quotes", "market_events", "capacity_snapshots"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if any(not isinstance(q, ExecutableQuote) or q.available_time > self.timestamp
               for q in self.quotes):
            raise ValueError("frame contains an unavailable quote")
        if len({q.instrument_id for q in self.quotes}) != len(self.quotes):
            raise ValueError("duplicate instrument quote in frame")
        if any(type(e) not in (MarkEvent, FundingEvent) or e.timestamp != self.timestamp
               for e in self.market_events):
            raise ValueError("venue market events must belong to the frame timestamp")
        if any(not isinstance(c, CapacitySnapshot) or c.ingestion_time > self.timestamp
               for c in self.capacity_snapshots):
            raise ValueError("frame contains unavailable capacity evidence")
        if len({c.instrument_id for c in self.capacity_snapshots}) != len(self.capacity_snapshots):
            raise ValueError("duplicate capacity snapshot in frame")

@dataclass(frozen=True)
class SimulationCostModel:
    fee_rate: Decimal = ZERO
    adverse_slippage_rate: Decimal = ZERO

    def __post_init__(self) -> None:
        _decimal(self.fee_rate, "fee_rate")
        _decimal(self.adverse_slippage_rate, "adverse_slippage_rate")
        if self.adverse_slippage_rate >= ONE:
            raise ValueError("adverse_slippage_rate must be less than one")

@dataclass(frozen=True)
class SimulationLimits:
    max_gross_leverage: Decimal = ONE
    max_single_weight: Decimal = Decimal("0.15")
    max_drawdown: Decimal = Decimal("0.25")
    quote_max_age_ms: int = 60_000
    order_latency_ms: int = 0

    def __post_init__(self) -> None:
        for name in ("max_gross_leverage", "max_single_weight"):
            _decimal(getattr(self, name), name, positive=True)
        _decimal(self.max_drawdown, "max_drawdown")
        if self.max_drawdown > ONE:
            raise ValueError("max_drawdown must not exceed one")
        _time(self.quote_max_age_ms, "quote_max_age_ms")
        _time(self.order_latency_ms, "order_latency_ms")

@dataclass(frozen=True)
class VenueOrderConstraints:
    """Frozen USD-M public order bounds, not account/venue certification.

    The limit-price band is an admission check. Market protection is checked
    against each executable slice; existing limit orders are not re-priced or
    retrospectively rejected when the mark moves. Pending counts cover only
    this isolated simulation's declared orders.
    """
    percent_price_up: Decimal
    percent_price_down: Decimal
    max_open_orders: int
    market_take_bound: Decimal

    @ledger_decimal_context()
    def __post_init__(self) -> None:
        _decimal(self.percent_price_up, "percent_price_up", positive=True)
        _decimal(self.percent_price_down, "percent_price_down", positive=True)
        _decimal(self.market_take_bound, "market_take_bound")
        if not self.percent_price_down <= ONE <= self.percent_price_up:
            raise ValueError("percent price bounds must include the mark")
        if type(self.max_open_orders) is not int or self.max_open_orders < 1:
            raise ValueError("max_open_orders must be a positive integer")

    def admission_reason(self, quantity: Decimal, limit: Decimal | None,
                         mark: Decimal, pending_count: int) -> str | None:
        if pending_count >= self.max_open_orders:
            return "MAX_OPEN_ORDERS"
        if limit is not None and (
                (quantity > ZERO and limit > mark*self.percent_price_up)
                or (quantity < ZERO and limit < mark*self.percent_price_down)):
            return "PERCENT_PRICE"
        return None

    def market_fill_reason(self, quantity: Decimal, price: Decimal,
                           mark: Decimal) -> str | None:
        if ((quantity > ZERO and price > mark*(ONE+self.market_take_bound))
                or (quantity < ZERO and price < mark*(ONE-self.market_take_bound))):
            return "MARKET_TAKE_BOUND"
        return None


@dataclass(frozen=True)
class OrderRuleProfile:
    """Caller-frozen order rules, distinct from position accounting rules.

    This profile is technical input, not a certification of venue provenance.
    Legacy quantity rules come from its bound contract. A public filter set
    separates original-order grids from the contract's accounting quantity atom.
    """
    rule_id: str
    contract_rule_id: str
    instrument_id: str
    venue_id: str
    effective_from: int
    effective_until: int
    observed_time: int
    available_time: int
    ingestion_time: int
    source_identity: str
    price_tick: Decimal
    min_price: Decimal
    max_price: Decimal
    market_max_quantity: Decimal
    limit_max_quantity: Decimal
    max_notional: Decimal
    minimum_policy: str
    supported_order_types: tuple[str, ...] = ("MARKET", "LIMIT")
    venue_constraints: VenueOrderConstraints | None = None
    venue_filters: VenueOrderFilters | None = None

    @ledger_decimal_context()
    def __post_init__(self) -> None:
        for name in ("rule_id", "contract_rule_id", "instrument_id", "venue_id", "source_identity"):
            _name(getattr(self, name), name)
        for name in ("effective_from", "effective_until", "observed_time", "available_time", "ingestion_time"):
            _time(getattr(self, name), name)
        if self.effective_until <= self.effective_from:
            raise ValueError("order rule epoch is empty")
        if not self.observed_time <= self.available_time <= self.ingestion_time:
            raise ValueError("order rule provenance is not causal")
        for name in ("market_max_quantity", "limit_max_quantity", "max_notional"):
            _decimal(getattr(self, name), name, positive=True)
        if self.venue_filters is None:
            for name in ("price_tick", "min_price", "max_price"):
                _decimal(getattr(self, name), name, positive=True)
            if (self.min_price > self.max_price or self.min_price % self.price_tick != ZERO
                    or self.max_price % self.price_tick != ZERO):
                raise ValueError("order rule price bounds must lie on the tick grid")
        else:
            f = self.venue_filters
            for name in ("price_tick", "min_price", "max_price"):
                _decimal(getattr(self, name), name)
            if not isinstance(f, VenueOrderFilters) or (
                    (self.price_tick, self.min_price, self.max_price, self.market_max_quantity, self.limit_max_quantity)
                    != (f.price.step, f.price.minimum, f.price.maximum, f.market.maximum, f.limit.maximum)):
                raise ValueError("public filter fields disagree with order profile")
            if self.minimum_policy != "ORIGINAL_ORDER":
                raise ValueError("public quantity filters require original order admission")
        if self.minimum_policy not in ("ORIGINAL_ORDER", "OPENED_POSITION"):
            raise ValueError("unknown order rule minimum policy")
        types = tuple(self.supported_order_types)
        if (not types or len(set(types)) != len(types)
                or any(t not in ("MARKET", "LIMIT") for t in types)):
            raise ValueError("unsupported order rule types")
        object.__setattr__(self, "supported_order_types", types)
        if self.venue_constraints is not None and not isinstance(self.venue_constraints, VenueOrderConstraints):
            raise ValueError("invalid venue order constraints")

    def price_reason(self, price: Decimal) -> str | None:
        if self.venue_filters is not None:
            return self.venue_filters.price.reason(price)
        if not self.min_price <= price <= self.max_price:
            return "PRICE_RANGE"
        if price % self.price_tick != ZERO:
            return "PRICE_TICK"
        return None

@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    instrument_id: str
    quantity_delta: Decimal
    limit_price: Decimal | None = None
    reduce_only: bool = False
    expires_at: int | None = None
    margin_mode: MarginMode = MarginMode.CROSS
    isolated_margin: Decimal | None = None

    def __post_init__(self) -> None:
        _name(self.order_id, "order_id")
        _name(self.instrument_id, "instrument_id")
        if (not isinstance(self.quantity_delta, Decimal)
                or not self.quantity_delta.is_finite() or self.quantity_delta == ZERO):
            raise ValueError("quantity_delta must be a nonzero finite Decimal")
        if type(self.reduce_only) is not bool:
            raise ValueError("reduce_only must be boolean")
        if self.limit_price is not None:
            _decimal(self.limit_price, "limit_price", positive=True)
        if self.expires_at is not None:
            _time(self.expires_at, "expires_at")
        object.__setattr__(self, "margin_mode", MarginMode(self.margin_mode))
        if self.isolated_margin is not None:
            _decimal(self.isolated_margin, "isolated_margin", positive=True)
        if self.margin_mode == MarginMode.CROSS and self.isolated_margin is not None:
            raise ValueError("cross orders cannot specify isolated collateral")

@dataclass(frozen=True)
class CancelOrder:
    order_id: str

    def __post_init__(self) -> None:
        _name(self.order_id, "order_id")

@dataclass(frozen=True)
class EquityPoint:
    timestamp: int
    sequence: int
    equity: Decimal
    return_since_previous: Decimal | None
    drawdown: Decimal
    phase: str

class _Prefix(Sequence[_T], Generic[_T]):
    """Immutable-length view of an append-only sequence; O(1) to capture."""
    def __init__(self, values: list[_T]) -> None:
        self.__values, self.__length = values, len(values)

    def __len__(self) -> int:
        return self.__length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self.__values[i] for i in range(*index.indices(self.__length)))
        if index < 0:
            index += self.__length
        if not 0 <= index < self.__length:
            raise IndexError(index)
        return self.__values[index]

@dataclass(frozen=True)
class OrderState:
    intent: OrderIntent
    created_at: int
    remaining_quantity: Decimal
    filled_quantity: Decimal = ZERO
    status: str = "OPEN"
    reason: str | None = None
    filled_at: int | None = None
    minimum_exemption_requires_reduction: bool = False

class _RiskQuote(Protocol):
    """Price view consumed by the shared risk core, independent of feed clocks."""
    instrument_id: str
    venue_id: str
    event_time: int
    bid_price: Decimal
    ask_price: Decimal
    bid_quantity: Decimal
    ask_quantity: Decimal

@dataclass(frozen=True)
class SimulationContext:
    timestamp: int
    equity: Decimal
    positions: Mapping[str, PositionView]
    quotes: Mapping[str, _RiskQuote]
    open_orders: Mapping[str, OrderState]
    history: Sequence[EquityPoint]
    reserved_collateral: Mapping[str, Decimal]
    available_collateral: Decimal

@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.approved) is not bool:
            raise ValueError("approved must be boolean")
        if not self.approved:
            _name(self.reason, "risk rejection reason")

Strategy = Callable[[SimulationContext], Sequence[OrderIntent | CancelOrder]]
RiskPolicy = Callable[[SimulationContext, OrderIntent], RiskVerdict]

class _SimulationAccount:
    """Internal shared order/risk/ledger core; not an agent or broker API."""
    def __init__(self, *, rule_items, rules, policies, profiles, initial_collateral, costs, limits, risk_policy):
        self.rules, self.policies, self.profiles = rules, policies, profiles
        self.initial_collateral, self.costs, self.limits = initial_collateral, costs, limits
        self.risk_policy = risk_policy
        self.ledger = LinearPerpetualLedger(tuple(r.spec for r in rule_items), initial_collateral=self.initial_collateral)
        self.universe = tuple(sorted(self.rules))
        self.quotes: dict[str, _RiskQuote] = {}
        self.capacities: dict[str, CapacitySnapshot] = {}
        self.mark_times: dict[str, int] = {}
        self.mark_prices: dict[str, Decimal] = {}
        self.pending: dict[str, OrderState] = {}
        self.orders: dict[str, OrderState] = {}
        self.history: list[EquityPoint] = []
        self.order_events: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.risk_events: list[dict[str, Any]] = []
        self.peak = self.initial_collateral


    def checkpoint(self, timestamp: int, phase: str) -> None:
        equity = self.ledger.account_equity()
        previous = self.history[-1].equity if self.history else None
        self.peak = max(self.peak, equity)
        self.history.append(EquityPoint(timestamp, len(self.history), equity,
            None if previous is None or previous <= ZERO else equity / previous - ONE,
            (self.peak-equity)/self.peak, phase))

    def execution_price(self, quote: _RiskQuote, quantity: Decimal) -> Decimal:
        price = (quote.ask_price * (ONE+self.costs.adverse_slippage_rate) if quantity > ZERO
                 else quote.bid_price * (ONE-self.costs.adverse_slippage_rate))
        profile = self.profiles.get(quote.instrument_id)
        if profile is not None:
            if profile.venue_filters is not None:
                price = profile.venue_filters.price.adverse_price(price, buy=quantity > ZERO)
            else:
                steps = (price/profile.price_tick).to_integral_value(
                    rounding=ROUND_CEILING if quantity > ZERO else ROUND_FLOOR)
                price = steps*profile.price_tick
        return price

    def reservation_snapshot(self, *, extra: OrderState | None = None,
                             filling: str | None = None, filled: Decimal = ZERO) -> dict[str, Any]:
        """Conservative cross-only order commitments; never debit ledger cash.

        Opposing orders do not net. Tier increments use the canonical margin
        function, on gross pending quantity plus existing absolute inventory.
        Filled quantities disappear from commitments only in the projected ledger
        or after commit. No hypothetical PnL is credited to available collateral.
        """
        margin = self.ledger.initial_margin_snapshot()["cross"]
        if not self.profiles:
            return {"policy": "UNRESERVED_LEGACY_DIAGNOSTIC", "by_order": {},
                    "total_reserved": ZERO, "available": margin["available"]}
        states = []
        for state in self.pending.values():
            if state.intent.order_id == filling:
                state = replace(state, remaining_quantity=state.remaining_quantity-filled)
            if state.remaining_quantity != ZERO:
                states.append(state)
        if extra is not None:
            states.append(extra)
        reserve: dict[str, Decimal] = {}
        stressed_required = ZERO
        executable_equity = self.ledger.account_equity()
        for name in self.universe:
            position = self.ledger.position(name)
            group = [s for s in states if s.intent.instrument_id == name]
            q = self.quotes.get(name)
            if q is None or name not in self.mark_prices:
                continue  # Admission rejects missing quotes before reaching here.
            high, low = self.execution_price(q, ONE), self.execution_price(q, -ONE)
            mark = self.mark_prices[name]
            price = max(mark, high, *(s.intent.limit_price or ZERO for s in group))
            spec = self.rules[name].spec
            quantity = ZERO if position is None else abs(position.quantity)
            required = spec.margin_requirement(spec.notional(quantity, price)).initial_margin
            stressed_required += required
            if position is not None:
                valuation = q.bid_price if position.quantity > ZERO else q.ask_price
                executable_equity += (position.quantity * (valuation-position.mark_price)
                                      * spec.contract_multiplier)
            for state in group:
                if state.intent.reduce_only:
                    reserve[state.intent.order_id] = ZERO
                    continue  # Exits remain available; actual costs still enter the ledger.
                quantity += abs(state.remaining_quantity)
                next_required = spec.margin_requirement(spec.notional(quantity, price)).initial_margin
                notional = spec.notional(state.remaining_quantity, price)
                buy = state.remaining_quantity > ZERO
                entry = high if buy else low
                if state.intent.limit_price is not None:
                    entry = min(entry, state.intent.limit_price) if buy else max(entry, state.intent.limit_price)
                adverse = max(ZERO, entry-mark if buy else mark-entry)
                buffer = abs(state.remaining_quantity)*adverse*spec.contract_multiplier
                reserve[state.intent.order_id] = next_required-required + notional*self.costs.fee_rate + buffer
                required = next_required
        total = sum(reserve.values(), ZERO)
        available = min(margin["equity"], executable_equity)-max(margin["required"], stressed_required)-total
        return {"policy": RESERVATION_POLICY, "by_order": reserve,
                "total_reserved": total, "available": available}

    def context(self, timestamp: int) -> SimulationContext:
        positions = {name: self.ledger.position(name) for name in self.universe}
        reservation = self.reservation_snapshot()
        return SimulationContext(timestamp, self.ledger.account_equity(),
            MappingProxyType({n:p for n,p in positions.items() if p is not None}),
            MappingProxyType(dict(self.quotes)), MappingProxyType(dict(self.pending)), _Prefix(self.history),
            MappingProxyType(reservation["by_order"]), reservation["available"])

    def transition(self, state: OrderState, timestamp: int, status: str,
                   reason: str | None = None, filled: Decimal = ZERO,
                   price: Decimal | None = None) -> OrderState:
        existing = self.orders.get(state.intent.order_id)
        if existing is None:
            if status not in ("OPEN", "REJECTED") or filled != ZERO:
                raise ValueError("invalid initial order state")
        elif existing != state or status not in _ORDER_TRANSITIONS.get(state.status, ()):
            raise ValueError("invalid or stale order transition")
        if filled != ZERO and (
            (filled > ZERO) != (state.remaining_quantity > ZERO)
            or abs(filled) > abs(state.remaining_quantity)
            or status not in ("PARTIALLY_FILLED", "FILLED")
        ):
            raise ValueError("fill violates order quantity conservation")
        remaining = state.remaining_quantity-filled
        if status == "FILLED" and (filled == ZERO or remaining != ZERO):
            raise ValueError("FILLED requires exact completion")
        if status == "PARTIALLY_FILLED" and (filled == ZERO or remaining == ZERO):
            raise ValueError("PARTIALLY_FILLED requires a nonzero remainder")
        updated = replace(state, status=status, reason=reason,
            remaining_quantity=remaining,
            filled_quantity=state.filled_quantity+filled,
            filled_at=timestamp if filled != ZERO else state.filled_at)
        self.orders[state.intent.order_id] = updated
        if status in ("OPEN", "PARTIALLY_FILLED"):
            self.pending[state.intent.order_id] = updated
        else:
            self.pending.pop(state.intent.order_id, None)
        self.order_events.append({
            "order_id": state.intent.order_id, "timestamp": timestamp, "status": status,
            "reason": reason, "filled_quantity": canonical_decimal(filled),
            "remaining_quantity": canonical_decimal(updated.remaining_quantity),
            "cumulative_filled_quantity": canonical_decimal(updated.filled_quantity),
            "fill_price": None if price is None else canonical_decimal(price),
            "reservation": _canonical(self.reservation_snapshot()),
        })
        return updated

    def risk_reason(self, ctx: SimulationContext, intent: OrderIntent, quantity: Decimal,
                    price: Decimal, *, executing: bool = False) -> str | None:
        name = intent.instrument_id
        old = ctx.positions[name].quantity if name in ctx.positions else ZERO
        reducing = old != ZERO and (old > ZERO) != (quantity > ZERO) and abs(quantity) <= abs(old)
        if intent.reduce_only and not reducing:
            return "REDUCE_ONLY_NO_POSITION" if old == ZERO else "REDUCE_ONLY_WOULD_INCREASE"
        if ctx.equity <= ZERO:
            return "NONPOSITIVE_EQUITY"
        if not reducing:
            if self.history[-1].drawdown > self.limits.max_drawdown:
                return "DRAWDOWN_LIMIT"
            # A low mark overstates a short's equity. Value existing inventory
            # at the currently executable side as well, and use the lower
            # equity so inconsistent marks cannot manufacture a risk budget.
            executable_equity = ctx.equity
            for instrument, position in ctx.positions.items():
                q = ctx.quotes.get(instrument)
                if q is None or ctx.timestamp-q.event_time > self.limits.quote_max_age_ms:
                    return "STALE_OR_MISSING_QUOTE"
                valuation = q.bid_price if position.quantity > ZERO else q.ask_price
                if instrument == name and executing:
                    valuation = (min(valuation, price) if position.quantity > ZERO
                                 else max(valuation, price))
                executable_equity += position.quantity * (valuation-position.mark_price) * self.rules[instrument].spec.contract_multiplier
            risk_equity = min(ctx.equity, executable_equity)
            if risk_equity <= ZERO:
                return "NONPOSITIVE_EXECUTABLE_EQUITY"
            amounts = {n:p.quantity for n,p in ctx.positions.items()}
            amounts[name] = old + quantity
            # Each side is reserved independently: pending buys and sells must
            # not cancel each other's potential exposure before either fills.
            worst = {}
            for instrument in set(amounts) | {s.intent.instrument_id for s in self.pending.values()}:
                base = amounts.get(instrument, ZERO)
                buys = sells = ZERO
                if not executing:
                    for state in self.pending.values():
                        if state.intent.order_id == intent.order_id or state.intent.reduce_only:
                            continue
                        if state.intent.instrument_id == instrument:
                            if state.remaining_quantity > ZERO: buys += state.remaining_quantity
                            else: sells += state.remaining_quantity
                worst[instrument] = max(abs(base+buys), abs(base+sells))
            notionals = {}
            for instrument, amount in worst.items():
                if amount == ZERO:
                    continue
                q = ctx.quotes.get(instrument)
                if q is None or ctx.timestamp-q.event_time > self.limits.quote_max_age_ms:
                    return "STALE_OR_MISSING_QUOTE"
                p = max(q.bid_price, q.ask_price)
                if instrument == name: p = max(p, price)
                notionals[instrument] = self.rules[instrument].spec.notional(amount, p)
            if any(n/risk_equity > self.limits.max_single_weight for n in notionals.values()):
                return "SINGLE_NAME_LIMIT"
            if sum(notionals.values(), ZERO)/risk_equity > self.limits.max_gross_leverage:
                return "GROSS_EXPOSURE_LIMIT"
        if self.risk_policy is not None:
            try:
                verdict = self.risk_policy(ctx, replace(intent, quantity_delta=quantity))
            except Exception:
                return "RISK_POLICY_FAILED"
            if not isinstance(verdict, RiskVerdict):
                return "INVALID_RISK_VERDICT"
            if not verdict.approved:
                return verdict.reason
        return None

    def capacity_for(self, name: str, timestamp: int) -> CapacitySnapshot | None:
        value = self.capacities.get(name)
        if value is None or not value.policy.contains_timestamp(timestamp):
            return None
        return value

    def check_projected_fill(self, before: SimulationContext, intent: OrderIntent,
                             filled: Decimal, price: Decimal, *,
                             minimum_exemption_requires_reduction: bool) -> None:
        # This context comes from the tentative canonical ledger transition;
        # fee, funding, realized PnL and margin are not recomputed here.
        projected = self.context(before.timestamp)
        old = before.positions.get(intent.instrument_id)
        reducing = (old is not None and (old.quantity > ZERO) != (filled > ZERO)
                    and abs(filled) <= abs(old.quantity))
        reason = self.risk_reason(before, intent, filled, price, executing=True)
        if minimum_exemption_requires_reduction and not reducing:
            reason = "QUANTITY_RULE"
        executable_equity = projected.equity
        notionals = {}
        for name, position in projected.positions.items():
            quote = projected.quotes.get(name)
            if quote is None or projected.timestamp-quote.event_time > self.limits.quote_max_age_ms:
                if not reducing:
                    reason = reason or "STALE_OR_MISSING_QUOTE"
                continue
            valuation = quote.bid_price if position.quantity > ZERO else quote.ask_price
            if name == intent.instrument_id:
                valuation = (min(valuation, price) if position.quantity > ZERO
                             else max(valuation, price))
            executable_equity += (position.quantity * (valuation-position.mark_price)
                                  * self.rules[name].spec.contract_multiplier)
            notional_price = max(quote.bid_price, quote.ask_price, position.mark_price)
            if name == intent.instrument_id:
                notional_price = max(notional_price, price)
            notionals[name] = self.rules[name].spec.notional(position.quantity, notional_price)
        risk_equity = min(projected.equity, executable_equity)
        drawdown = max(ZERO, (self.peak-risk_equity)/self.peak)
        gross = sum(notionals.values(), ZERO)
        checks = {
            "positive_equity": risk_equity > ZERO,
            "drawdown": drawdown <= self.limits.max_drawdown,
            "single_name": all(n <= risk_equity*self.limits.max_single_weight for n in notionals.values()),
            "gross_exposure": gross <= risk_equity*self.limits.max_gross_leverage,
        }
        if not reducing:
            for check, code in (
                ("positive_equity", "NONPOSITIVE_EXECUTABLE_EQUITY"),
                ("drawdown", "DRAWDOWN_LIMIT"),
                ("single_name", "SINGLE_NAME_LIMIT"),
                ("gross_exposure", "GROSS_EXPOSURE_LIMIT"),
            ):
                if not checks[check]:
                    reason = reason or code
        reservation = self.reservation_snapshot(filling=intent.order_id, filled=filled)
        if self.profiles and not reducing and reservation["available"] < ZERO:
            reason = reason or "INSUFFICIENT_RESERVED_COLLATERAL"
        self.risk_events.append({
            "order_id": intent.order_id, "timestamp": before.timestamp,
            "phase": "PROJECTED_FILL", "filled_quantity": filled,
            "marked_equity": projected.equity, "executable_equity": executable_equity,
            "risk_equity": risk_equity, "gross_notional": gross,
            "instrument_notionals": notionals, "drawdown": drawdown,
            "limits": asdict(self.limits), "checks": checks,
            "margin": self.ledger.initial_margin_snapshot(),
            "reservation": reservation,
            "reducing": reducing, "approved": reason is None, "reason": reason,
        })
        if reason is not None:
            raise _ProjectedRiskRejected(reason)

    def apply_market(self, frame):
        now = frame.timestamp
        if not self.history: self.checkpoint(now, "INITIAL")
        if any(e.instrument_id not in self.rules for e in frame.market_events):
            raise ValueError("market event outside the declared universe")
        self.ledger.process_events(frame.market_events)
        for event in frame.market_events:
            self.mark_times[event.instrument_id] = now
            self.mark_prices[event.instrument_id] = event.mark_price
        accepted_quotes: dict[str, ExecutableQuote] = {}
        for q in frame.quotes:
            if q.instrument_id not in self.rules:
                raise ValueError("quote outside the declared universe")
            if q.venue_id != self.policies[q.instrument_id].venue_id:
                raise ValueError("quote and funding venues differ")
            profile = self.profiles.get(q.instrument_id)
            if profile is not None and (profile.price_reason(q.bid_price) or profile.price_reason(q.ask_price)):
                raise ValueError("executable quote violates order rule price grid or bounds")
            prior = self.quotes.get(q.instrument_id)
            if prior is not None and q.event_time <= prior.event_time:
                # A late observation is retained in the input identity but
                # cannot roll time backwards or replenish consumed liquidity.
                if q.event_time == prior.event_time and q != prior:
                    raise ValueError("conflicting quote at the same venue timestamp")
                continue
            self.quotes[q.instrument_id] = q
            accepted_quotes[q.instrument_id] = q
        for cap in frame.capacity_snapshots:
            if cap.instrument_id not in self.rules or cap.policy.venue_id != self.policies[cap.instrument_id].venue_id:
                raise ValueError("capacity outside the declared venue/universe")
            previous = self.capacities.get(cap.instrument_id)
            if previous is not None and cap.ingestion_time < previous.ingestion_time:
                raise ValueError("capacity knowledge time moved backwards")
            self.capacities[cap.instrument_id] = cap
        self.checkpoint(now, "MARKET")
        return accepted_quotes

    def fill_order(self, state, now, px, maximum_quantity):
        """Commit only the affordable, permitted part of one fill opportunity.

        Return the absolute committed quantity, so a matching model never spends
        shared liquidity on a rejected or capacity-truncated transaction.
        """
        _decimal(maximum_quantity, "maximum_quantity")
        _decimal(px, "execution_price")
        if self.pending.get(state.intent.order_id) != state:
            raise ValueError("fill requires the current open order")
        intent = state.intent
        if intent.instrument_id not in self.mark_times or now-self.mark_times[intent.instrument_id] > self.limits.quote_max_age_ms:
            self.transition(state, now, "REJECTED", "STALE_OR_MISSING_MARK")
            return ZERO
        spec = self.rules[intent.instrument_id].spec
        remaining = state.remaining_quantity
        current = self.ledger.position(intent.instrument_id)
        if intent.reduce_only:
            if current is None or (current.quantity > ZERO) == (remaining > ZERO):
                self.transition(state, now, "REJECTED", "REDUCE_ONLY_NO_POSITION")
                return ZERO
            remaining = min(abs(remaining), abs(current.quantity)) * (ONE if remaining > ZERO else -ONE)
        profile = self.profiles.get(intent.instrument_id)
        if profile is not None:
            reason = profile.price_reason(px)
            if reason is None and profile.venue_constraints is not None and intent.limit_price is None:
                reason = profile.venue_constraints.market_fill_reason(
                    remaining, px, self.mark_prices[intent.instrument_id])
            if reason is None and spec.notional(intent.quantity_delta, px) > profile.max_notional:
                reason = reason or "MAX_ORDER_NOTIONAL"
            if reason:
                self.transition(state, now, "REJECTED", reason)
                return ZERO
        if intent.limit_price is not None and (
            (remaining > ZERO and px > intent.limit_price)
            or (remaining < ZERO and px < intent.limit_price)):
            return ZERO
        cap = self.capacity_for(intent.instrument_id, now)
        if cap is None:
            self.transition(state, now, "REJECTED", "MISSING_CURRENT_CAPACITY")
            return ZERO
        quantity = spec.floor_quantity(min(abs(remaining), maximum_quantity))
        quantity *= ONE if remaining > ZERO else -ONE
        if quantity == ZERO:
            return ZERO
        before_fill = self.context(now)
        trade = OrderFillEvent(timestamp=now, instrument_id=intent.instrument_id,
            quantity_delta=quantity, execution_price=px, capacity=cap.policy,
            margin_mode=intent.margin_mode,
            isolated_margin=intent.isolated_margin if current is None else None,
            order_id=intent.order_id, fee_rate=self.costs.fee_rate)
        try:
            fill = self.ledger.apply_event(trade, trade_guard=lambda candidate:
                self.check_projected_fill(before_fill, intent, candidate.filled_quantity, px,
                    minimum_exemption_requires_reduction=state.minimum_exemption_requires_reduction))
        except _ProjectedRiskRejected as exc:
            self.transition(state, now, "REJECTED", str(exc))
            return ZERO
        except InsufficientMargin:
            self.transition(state, now, "REJECTED", "INSUFFICIENT_MARGIN")
            return ZERO
        except QuantityRuleViolation:
            self.transition(state, now, "REJECTED", "QUANTITY_RULE")
            return ZERO
        if fill.filled_quantity == ZERO:
            self.transition(state, now, "REJECTED", fill.reason or "CAPACITY_REJECTED")
            return ZERO
        done = fill.filled_quantity == state.remaining_quantity
        updated = self.transition(state, now, "FILLED" if done else "PARTIALLY_FILLED",
            filled=fill.filled_quantity, price=px)
        if intent.reduce_only and self.ledger.position(intent.instrument_id) is None and not done:
            self.transition(updated, now, "CANCELLED", "REDUCE_ONLY_REMAINDER")
        self.checkpoint(now, "FILL")
        return abs(fill.filled_quantity)

    def snapshot(self):
        reconciliation = self.ledger.reconciliation_snapshot()
        if reconciliation.residual != ZERO:
            raise ValueError("perpetual cash-flow reconciliation failed")
        return {
            "ending_reservation": self.reservation_snapshot(),
            "ending_state":self.ledger.state_as_canonical_dict(),
            "reconciliation":reconciliation.as_canonical_dict(),
            "ledger_events":[dict(e) for e in self.ledger.event_records],
            "orders":[{"order_id":s.intent.order_id, "instrument_id":s.intent.instrument_id,
                "status":s.status, "reason":s.reason, "created_at":s.created_at, "filled_at":s.filled_at,
                "filled_quantity":canonical_decimal(s.filled_quantity),
                "minimum_exemption_requires_reduction":s.minimum_exemption_requires_reduction,
                "remaining_quantity":canonical_decimal(s.remaining_quantity)} for s in self.orders.values()],
            "order_events":self.order_events, "decision_events":self.decisions, "risk_events":self.risk_events,
            "equity_history":[{"timestamp":p.timestamp,"sequence":p.sequence,"phase":p.phase,
                "equity":canonical_decimal(p.equity),"drawdown":canonical_decimal(p.drawdown),
                "return_since_previous":None if p.return_since_previous is None
                else canonical_decimal(p.return_since_previous)} for p in self.history],
        }

    def apply_actions(self, now, actions, *, ctx=None):
        ctx = self.context(now) if ctx is None else ctx
        action_records = []
        for action in actions:
            if not isinstance(action, (OrderIntent, CancelOrder)):
                raise ValueError("strategy must return only order intents or cancellations")
            action_records.append(asdict(action))
            if isinstance(action, CancelOrder):
                state = self.pending.get(action.order_id)
                if state is not None:
                    self.transition(state, now, "CANCELLED", "STRATEGY_CANCEL")
                else:
                    self.order_events.append({"order_id":action.order_id, "timestamp":now,
                        "status":"CANCEL_REJECTED", "reason":"ORDER_NOT_OPEN",
                        "filled_quantity":"0", "fill_price":None})
                continue
            if action.order_id in self.orders:
                raise ValueError("duplicate order_id")
            state = OrderState(action, now, action.quantity_delta)
            reason = None
            q = self.quotes.get(action.instrument_id)
            if action.instrument_id not in self.rules:
                reason = "UNKNOWN_INSTRUMENT"
            elif action.expires_at is not None and action.expires_at <= now:
                reason = "INVALID_EXPIRY"
            elif q is None or now-q.event_time > self.limits.quote_max_age_ms:
                reason = "STALE_OR_MISSING_QUOTE"
            elif action.instrument_id not in self.mark_times or now-self.mark_times[action.instrument_id] > self.limits.quote_max_age_ms:
                reason = "STALE_OR_MISSING_MARK"
            elif self.capacity_for(action.instrument_id, now) is None:
                reason = "MISSING_CURRENT_CAPACITY"
            else:
                current = self.ledger.position(action.instrument_id)
                px = q.ask_price if action.quantity_delta > ZERO else q.bid_price
                profile = self.profiles.get(action.instrument_id)
                if profile is not None:
                    order_type = "MARKET" if action.limit_price is None else "LIMIT"
                    bound = profile.market_max_quantity if order_type == "MARKET" else profile.limit_max_quantity
                    px = action.limit_price or self.execution_price(q, action.quantity_delta)
                    if action.margin_mode != MarginMode.CROSS:
                        reason = "UNSUPPORTED_ORDER_MARGIN_MODE"
                    elif order_type not in profile.supported_order_types:
                        reason = "UNSUPPORTED_ORDER_TYPE"
                    elif profile.price_reason(px):
                        reason = profile.price_reason(px)
                    elif abs(action.quantity_delta) > bound:
                        reason = "MAX_ORDER_QUANTITY"
                    elif profile.venue_filters is not None and profile.venue_filters.quantity_reason(
                            action.quantity_delta, market=order_type == "MARKET"):
                        reason = profile.venue_filters.quantity_reason(action.quantity_delta, market=order_type == "MARKET")
                    elif self.rules[action.instrument_id].spec.notional(action.quantity_delta, px) > profile.max_notional:
                        reason = "MAX_ORDER_NOTIONAL"
                    if reason is None and profile.venue_constraints is not None:
                        reason = profile.venue_constraints.admission_reason(
                            action.quantity_delta, action.limit_price, self.mark_prices[action.instrument_id],
                            sum(s.intent.instrument_id == action.instrument_id for s in self.pending.values()))
                try:
                    if reason is None:
                        minimum_price = (self.mark_prices[action.instrument_id]
                            if profile is not None and (profile.venue_constraints is not None or profile.venue_filters is not None)
                            and action.limit_price is None else px)
                        if profile is not None and profile.minimum_policy == "ORIGINAL_ORDER":
                            reducing = (action.reduce_only and current is not None
                                and (current.quantity > ZERO) != (action.quantity_delta > ZERO)
                                and abs(action.quantity_delta) <= abs(current.quantity))
                            self.rules[action.instrument_id].spec.validate_quantity(
                                action.quantity_delta, minimum_price, reducing=reducing)
                        else:
                            self.rules[action.instrument_id].spec.validate_trade_quantity(
                                action.quantity_delta, minimum_price,
                                current_quantity=ZERO if current is None else current.quantity)
                except QuantityRuleViolation:
                    reason = "QUANTITY_RULE"
                if reason is None:
                    try:
                        self.rules[action.instrument_id].spec.validate_quantity(action.quantity_delta, minimum_price)
                    except QuantityRuleViolation:
                        # An original closing order admitted only by a minimum
                        # exemption must never use it to open inventory later.
                        state = replace(state, minimum_exemption_requires_reduction=True)
                    reason = self.risk_reason(self.context(now), action, action.quantity_delta, px)
                    if reason is None and profile is not None and not action.reduce_only:
                        if self.reservation_snapshot(extra=state)["available"] < ZERO:
                            reason = "INSUFFICIENT_RESERVED_COLLATERAL"
            self.transition(state, now, "OPEN" if reason is None else "REJECTED", reason)
        self.decisions.append({
            "timestamp":now, "equity":canonical_decimal(ctx.equity),
            "positions":{n:p.as_canonical_dict() for n,p in ctx.positions.items()},
            "quotes":{n:asdict(q) for n,q in ctx.quotes.items()},
            "history_length":len(ctx.history), "actions":action_records,
            "reserved_collateral": dict(ctx.reserved_collateral),
            "available_collateral": ctx.available_collateral,
        })


def _validate_market_environment(rule_items, policy_items, profile_items, frame_items):
    times = tuple(f.timestamp for f in frame_items)
    if not rule_items or any(not isinstance(r, HistoricalContractRule) for r in rule_items):
        raise ValueError("historical contract rules are required")
    rules = {r.spec.instrument_id: r for r in rule_items}
    if len(rules) != len(rule_items):
        raise ValueError("one immutable rule epoch per instrument is supported")
    if any(r.ingestion_time > times[0] or not r.covers(times[0])
           or not r.covers(times[-1]) for r in rule_items):
        raise ValueError("historical rules must be known and cover the full simulation")
    if any(not isinstance(p, FundingPolicySnapshot) for p in policy_items):
        raise ValueError("invalid funding policy")
    policies = {p.instrument_id: p for p in policy_items}
    if len(policies) != len(policy_items) or set(policies) != set(rules):
        raise ValueError("funding policies must exactly cover the universe")
    for p in policy_items:
        if p.ingestion_time > times[0] or not p.coverage_start <= times[0] <= times[-1] <= p.coverage_end:
            raise ValueError("funding policy must be known and cover the simulation")
    profiles = {}
    if profile_items is not None:
        if any(not isinstance(p, OrderRuleProfile) for p in profile_items):
            raise ValueError("invalid order rule profile")
        profiles = {p.instrument_id: p for p in profile_items}
        if len(profiles) != len(profile_items) or set(profiles) != set(rules):
            raise ValueError("order rules must exactly cover the universe")
        for name, p in profiles.items():
            spec = rules[name].spec
            if (p.contract_rule_id != rules[name].rule_id or p.venue_id != policies[name].venue_id
                    or p.ingestion_time > times[0]
                    or not p.effective_from <= times[0] <= times[-1] < p.effective_until
                    or p.max_notional < spec.min_notional):
                raise ValueError("order rule binding, coverage or quantity bounds are invalid")
            if p.venue_filters is not None:
                if (spec.quantity_step != p.venue_filters.quantity_atom
                        or spec.min_quantity != min(p.venue_filters.limit.minimum, p.venue_filters.market.minimum)):
                    raise ValueError("public order filters do not bind the accounting quantity atom")
            elif (p.market_max_quantity % spec.quantity_step != ZERO
                    or p.limit_max_quantity % spec.quantity_step != ZERO
                    or min(p.market_max_quantity, p.limit_max_quantity) < spec.min_quantity):
                raise ValueError("order rule binding, coverage or quantity bounds are invalid")
    expected_funding = {(p.instrument_id, t) for p in policy_items
                        for t in p.settlement_times if times[0] <= t <= times[-1]}
    actual_funding = [(e.instrument_id, e.timestamp) for f in frame_items
                      for e in f.market_events if isinstance(e, FundingEvent)]
    if set(actual_funding) != expected_funding or len(actual_funding) != len(expected_funding):
        raise ValueError("funding schedule does not match settlement events")
    return rules, policies, profiles


@ledger_decimal_context()
def run_linear_perpetual_simulation(
    *, release_identity: Mapping[str, Any], dataset_identity: Mapping[str, Any],
    strategy_identity: Mapping[str, Any], contract_rules: Sequence[HistoricalContractRule],
    funding_policies: Sequence[FundingPolicySnapshot], initial_collateral: Decimal,
    frames: Sequence[SimulationFrame], strategy: Strategy,
    limits: SimulationLimits | None = None, costs: SimulationCostModel | None = None,
    risk_policy: RiskPolicy | None = None,
    risk_policy_identity: Mapping[str, Any] | None = None,
    order_rule_profiles: Sequence[OrderRuleProfile] | None = None,
) -> dict[str, Any]:
    """Run strategy callbacks against a causal account and produce technical evidence."""
    limits = limits or SimulationLimits()
    if costs is None:
        raise ValueError("an explicit cost model is required")
    if not isinstance(limits, SimulationLimits) or not isinstance(costs, SimulationCostModel):
        raise TypeError("invalid simulation configuration")
    if not callable(strategy) or (risk_policy is not None and not callable(risk_policy)):
        raise TypeError("strategy and optional risk_policy must be callable")
    if (risk_policy is None) != (risk_policy_identity is None):
        raise ValueError("a risk policy and its frozen identity must be supplied together")
    if risk_policy_identity is not None:
        if not isinstance(risk_policy_identity, Mapping) or not risk_policy_identity:
            raise ValueError("risk policy identity must be a nonempty mapping")
        exact_identity_hash(risk_policy_identity, name="risk_policy")
    release_identity, dataset_identity, strategy_identity, risk_policy_identity = copy.deepcopy(
        (release_identity, dataset_identity, strategy_identity, risk_policy_identity)
    )
    for name, value in (("release", release_identity), ("dataset", dataset_identity),
                        ("strategy", strategy_identity)):
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{name} identity is required")
        exact_identity_hash(value, name=name)
    frame_items, rule_items, policy_items = tuple(frames), tuple(contract_rules), tuple(funding_policies)
    if not frame_items or any(not isinstance(f, SimulationFrame) for f in frame_items):
        raise ValueError("at least one valid simulation frame is required")
    times = tuple(f.timestamp for f in frame_items)
    if times != tuple(sorted(set(times))):
        raise ValueError("frame timestamps must be strictly increasing")
    profile_items = None if order_rule_profiles is None else tuple(order_rule_profiles)
    rules, policies, profiles = _validate_market_environment(rule_items, policy_items, profile_items, frame_items)
    account = _SimulationAccount(rule_items=rule_items, rules=rules, policies=policies,
        profiles=profiles, initial_collateral=initial_collateral, costs=costs, limits=limits, risk_policy=risk_policy)

    for frame in frame_items:
        now = frame.timestamp
        accepted_quotes = account.apply_market(frame)
        depth = {(q.instrument_id, side): size for q in accepted_quotes.values()
                 for side, size in (("BUY", q.ask_quantity), ("SELL", q.bid_quantity))}
        fresh = accepted_quotes
        for state in tuple(account.pending.values()):
            intent = state.intent
            if intent.expires_at is not None and now >= intent.expires_at:
                account.transition(state, now, "EXPIRED", "TIME_IN_FORCE")
                continue
            q = fresh.get(intent.instrument_id)
            if q is None or account.quotes.get(intent.instrument_id) != q:
                continue
            if q.event_time <= state.created_at + account.limits.order_latency_ms:
                continue
            if now-q.event_time > account.limits.quote_max_age_ms:
                continue
            side = "BUY" if state.remaining_quantity > ZERO else "SELL"
            px = account.execution_price(q, state.remaining_quantity)
            used = account.fill_order(state, now, px, depth[(intent.instrument_id, side)])
            depth[(intent.instrument_id, side)] -= used
        ctx = account.context(now)
        actions = tuple(strategy(ctx))
        account.apply_actions(now, actions, ctx=ctx)
    reconciliation = account.ledger.reconciliation_snapshot()
    if reconciliation.residual != ZERO:
        raise ValueError("perpetual cash-flow reconciliation failed")
    inputs = {
        "schema":SCHEMA, "release":release_identity, "dataset":dataset_identity,
        "strategy":strategy_identity, "initial_collateral":account.initial_collateral,
        "rules":[r.as_canonical_dict() for r in rule_items],
        "funding_policies":[p.as_canonical_dict() for p in policy_items],
        "frames":[{"timestamp":f.timestamp, "quotes":[asdict(q) for q in f.quotes],
            "market_events":[asdict(e) for e in f.market_events],
            "capacity":[c.as_canonical_dict() for c in f.capacity_snapshots]} for f in frame_items],
        "limits":asdict(account.limits), "costs":asdict(account.costs),
        "risk_policy":risk_policy_identity,
        "order_rules": None if profile_items is None else [asdict(p) for p in profile_items],
        "reservation_policy": RESERVATION_POLICY if account.profiles else "UNRESERVED_LEGACY_DIAGNOSTIC",
    }
    result = {
        "schema_version":SCHEMA, "authoritative":False, "evidence_status":"TECHNICAL_VALIDATION_ONLY",
        "input_hash":exact_identity_hash(inputs, name="perpetual_simulation_inputs"),
        "risk_history_source":"LEDGER_EVENT_REPLAY",
        "execution_model":"AGGRESSIVE_TOP_OF_BOOK_WITH_EMBEDDED_ADVERSE_SLIPPAGE",
        "full_research_risk_9_of_9":False,
        "authority_blockers":["EXTERNAL_SIGNED_RAW_ARCHIVE_AUTHORITY_MISSING",
                              "INDEPENDENT_ECONOMIC_VALIDATION_REQUIRED"]
                             + ([] if account.profiles else ["ORDER_RULE_PROFILE_MISSING"]),
        "order_rule_profiles": None if profile_items is None else [asdict(p) for p in profile_items],
        **account.snapshot(),
    }
    result = _canonical(result)
    result["result_hash"] = exact_identity_hash(result, name="perpetual_simulation_result")
    return result
