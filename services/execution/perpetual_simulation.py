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
from typing import Any, Generic, TypeVar

from services.execution.authoritative_perpetual_backtest import (
    CapacitySnapshot, FundingPolicySnapshot, HistoricalContractRule,
)
from services.execution.backtest_authority import exact_identity_hash
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
class OrderRuleProfile:
    """Caller-frozen order rules, distinct from position accounting rules.

    This profile is technical input, not a certification of venue provenance.
    Quantity step and minimums come from its explicitly bound contract rule.
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
        for name in ("price_tick", "min_price", "max_price", "market_max_quantity",
                     "limit_max_quantity", "max_notional"):
            _decimal(getattr(self, name), name, positive=True)
        if (self.min_price > self.max_price or self.min_price % self.price_tick != ZERO
                or self.max_price % self.price_tick != ZERO):
            raise ValueError("order rule price bounds must lie on the tick grid")
        if self.minimum_policy not in ("ORIGINAL_ORDER", "OPENED_POSITION"):
            raise ValueError("unknown order rule minimum policy")
        types = tuple(self.supported_order_types)
        if (not types or len(set(types)) != len(types)
                or any(t not in ("MARKET", "LIMIT") for t in types)):
            raise ValueError("unsupported order rule types")
        object.__setattr__(self, "supported_order_types", types)

    def price_reason(self, price: Decimal) -> str | None:
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

@dataclass(frozen=True)
class SimulationContext:
    timestamp: int
    equity: Decimal
    positions: Mapping[str, PositionView]
    quotes: Mapping[str, ExecutableQuote]
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
    profile_items = None if order_rule_profiles is None else tuple(order_rule_profiles)
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
                    or p.market_max_quantity % spec.quantity_step != ZERO
                    or p.limit_max_quantity % spec.quantity_step != ZERO
                    or min(p.market_max_quantity, p.limit_max_quantity) < spec.min_quantity
                    or p.max_notional < spec.min_notional):
                raise ValueError("order rule binding, coverage or quantity bounds are invalid")
    expected_funding = {(p.instrument_id, t) for p in policy_items
                        for t in p.settlement_times if times[0] <= t <= times[-1]}
    actual_funding = [(e.instrument_id, e.timestamp) for f in frame_items
                      for e in f.market_events if isinstance(e, FundingEvent)]
    if set(actual_funding) != expected_funding or len(actual_funding) != len(expected_funding):
        raise ValueError("funding schedule does not match settlement events")
    ledger = LinearPerpetualLedger(tuple(r.spec for r in rule_items), initial_collateral=initial_collateral)
    universe = tuple(sorted(rules))
    quotes: dict[str, ExecutableQuote] = {}
    capacities: dict[str, CapacitySnapshot] = {}
    mark_times: dict[str, int] = {}
    mark_prices: dict[str, Decimal] = {}
    pending: dict[str, OrderState] = {}
    orders: dict[str, OrderState] = {}
    history: list[EquityPoint] = []
    order_events: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    risk_events: list[dict[str, Any]] = []
    peak = initial_collateral

    def checkpoint(timestamp: int, phase: str) -> None:
        nonlocal peak
        equity = ledger.account_equity()
        previous = history[-1].equity if history else None
        peak = max(peak, equity)
        history.append(EquityPoint(timestamp, len(history), equity,
            None if previous is None or previous <= ZERO else equity / previous - ONE,
            (peak-equity)/peak, phase))

    def execution_price(quote: ExecutableQuote, quantity: Decimal) -> Decimal:
        price = (quote.ask_price * (ONE+costs.adverse_slippage_rate) if quantity > ZERO
                 else quote.bid_price * (ONE-costs.adverse_slippage_rate))
        profile = profiles.get(quote.instrument_id)
        if profile is not None:
            steps = (price/profile.price_tick).to_integral_value(
                rounding=ROUND_CEILING if quantity > ZERO else ROUND_FLOOR)
            price = steps*profile.price_tick
        return price

    def reservation_snapshot(*, extra: OrderState | None = None,
                             filling: str | None = None, filled: Decimal = ZERO) -> dict[str, Any]:
        """Conservative cross-only order commitments; never debit ledger cash.

        Opposing orders do not net. Tier increments use the canonical margin
        function, on gross pending quantity plus existing absolute inventory.
        Filled quantities disappear from commitments only in the projected ledger
        or after commit. No hypothetical PnL is credited to available collateral.
        """
        margin = ledger.initial_margin_snapshot()["cross"]
        if not profiles:
            return {"policy": "UNRESERVED_LEGACY_DIAGNOSTIC", "by_order": {},
                    "total_reserved": ZERO, "available": margin["available"]}
        states = []
        for state in pending.values():
            if state.intent.order_id == filling:
                state = replace(state, remaining_quantity=state.remaining_quantity-filled)
            if state.remaining_quantity != ZERO:
                states.append(state)
        if extra is not None:
            states.append(extra)
        reserve: dict[str, Decimal] = {}
        stressed_required = ZERO
        executable_equity = ledger.account_equity()
        for name in universe:
            position = ledger.position(name)
            group = [s for s in states if s.intent.instrument_id == name]
            q = quotes.get(name)
            if q is None or name not in mark_prices:
                continue  # Admission rejects missing quotes before reaching here.
            high, low = execution_price(q, ONE), execution_price(q, -ONE)
            mark = mark_prices[name]
            price = max(mark, high, *(s.intent.limit_price or ZERO for s in group))
            spec = rules[name].spec
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
                reserve[state.intent.order_id] = next_required-required + notional*costs.fee_rate + buffer
                required = next_required
        total = sum(reserve.values(), ZERO)
        available = min(margin["equity"], executable_equity)-max(margin["required"], stressed_required)-total
        return {"policy": RESERVATION_POLICY, "by_order": reserve,
                "total_reserved": total, "available": available}

    def context(timestamp: int) -> SimulationContext:
        positions = {name: ledger.position(name) for name in universe}
        reservation = reservation_snapshot()
        return SimulationContext(timestamp, ledger.account_equity(),
            MappingProxyType({n:p for n,p in positions.items() if p is not None}),
            MappingProxyType(dict(quotes)), MappingProxyType(dict(pending)), _Prefix(history),
            MappingProxyType(reservation["by_order"]), reservation["available"])

    def transition(state: OrderState, timestamp: int, status: str,
                   reason: str | None = None, filled: Decimal = ZERO,
                   price: Decimal | None = None) -> OrderState:
        existing = orders.get(state.intent.order_id)
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
        orders[state.intent.order_id] = updated
        if status in ("OPEN", "PARTIALLY_FILLED"):
            pending[state.intent.order_id] = updated
        else:
            pending.pop(state.intent.order_id, None)
        order_events.append({
            "order_id": state.intent.order_id, "timestamp": timestamp, "status": status,
            "reason": reason, "filled_quantity": canonical_decimal(filled),
            "remaining_quantity": canonical_decimal(updated.remaining_quantity),
            "cumulative_filled_quantity": canonical_decimal(updated.filled_quantity),
            "fill_price": None if price is None else canonical_decimal(price),
            "reservation": _canonical(reservation_snapshot()),
        })
        return updated

    def risk_reason(ctx: SimulationContext, intent: OrderIntent, quantity: Decimal,
                    price: Decimal, *, executing: bool = False) -> str | None:
        name = intent.instrument_id
        old = ctx.positions[name].quantity if name in ctx.positions else ZERO
        reducing = old != ZERO and (old > ZERO) != (quantity > ZERO) and abs(quantity) <= abs(old)
        if intent.reduce_only and not reducing:
            return "REDUCE_ONLY_NO_POSITION" if old == ZERO else "REDUCE_ONLY_WOULD_INCREASE"
        if ctx.equity <= ZERO:
            return "NONPOSITIVE_EQUITY"
        if not reducing:
            if history[-1].drawdown > limits.max_drawdown:
                return "DRAWDOWN_LIMIT"
            # A low mark overstates a short's equity. Value existing inventory
            # at the currently executable side as well, and use the lower
            # equity so inconsistent marks cannot manufacture a risk budget.
            executable_equity = ctx.equity
            for instrument, position in ctx.positions.items():
                q = ctx.quotes.get(instrument)
                if q is None or ctx.timestamp-q.event_time > limits.quote_max_age_ms:
                    return "STALE_OR_MISSING_QUOTE"
                valuation = q.bid_price if position.quantity > ZERO else q.ask_price
                if instrument == name and executing:
                    valuation = (min(valuation, price) if position.quantity > ZERO
                                 else max(valuation, price))
                executable_equity += position.quantity * (valuation-position.mark_price) * rules[instrument].spec.contract_multiplier
            risk_equity = min(ctx.equity, executable_equity)
            if risk_equity <= ZERO:
                return "NONPOSITIVE_EXECUTABLE_EQUITY"
            amounts = {n:p.quantity for n,p in ctx.positions.items()}
            amounts[name] = old + quantity
            # Each side is reserved independently: pending buys and sells must
            # not cancel each other's potential exposure before either fills.
            worst = {}
            for instrument in set(amounts) | {s.intent.instrument_id for s in pending.values()}:
                base = amounts.get(instrument, ZERO)
                buys = sells = ZERO
                if not executing:
                    for state in pending.values():
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
                if q is None or ctx.timestamp-q.event_time > limits.quote_max_age_ms:
                    return "STALE_OR_MISSING_QUOTE"
                p = max(q.bid_price, q.ask_price)
                if instrument == name: p = max(p, price)
                notionals[instrument] = rules[instrument].spec.notional(amount, p)
            if any(n/risk_equity > limits.max_single_weight for n in notionals.values()):
                return "SINGLE_NAME_LIMIT"
            if sum(notionals.values(), ZERO)/risk_equity > limits.max_gross_leverage:
                return "GROSS_EXPOSURE_LIMIT"
        if risk_policy is not None:
            try:
                verdict = risk_policy(ctx, replace(intent, quantity_delta=quantity))
            except Exception:
                return "RISK_POLICY_FAILED"
            if not isinstance(verdict, RiskVerdict):
                return "INVALID_RISK_VERDICT"
            if not verdict.approved:
                return verdict.reason
        return None

    def capacity_for(name: str, timestamp: int) -> CapacitySnapshot | None:
        value = capacities.get(name)
        if value is None or not value.policy.contains_timestamp(timestamp):
            return None
        return value

    def check_projected_fill(before: SimulationContext, intent: OrderIntent,
                             filled: Decimal, price: Decimal, *,
                             minimum_exemption_requires_reduction: bool) -> None:
        # This context comes from the tentative canonical ledger transition;
        # fee, funding, realized PnL and margin are not recomputed here.
        projected = context(before.timestamp)
        old = before.positions.get(intent.instrument_id)
        reducing = (old is not None and (old.quantity > ZERO) != (filled > ZERO)
                    and abs(filled) <= abs(old.quantity))
        reason = risk_reason(before, intent, filled, price, executing=True)
        if minimum_exemption_requires_reduction and not reducing:
            reason = "QUANTITY_RULE"
        executable_equity = projected.equity
        notionals = {}
        for name, position in projected.positions.items():
            quote = projected.quotes.get(name)
            if quote is None or projected.timestamp-quote.event_time > limits.quote_max_age_ms:
                if not reducing:
                    reason = reason or "STALE_OR_MISSING_QUOTE"
                continue
            valuation = quote.bid_price if position.quantity > ZERO else quote.ask_price
            if name == intent.instrument_id:
                valuation = (min(valuation, price) if position.quantity > ZERO
                             else max(valuation, price))
            executable_equity += (position.quantity * (valuation-position.mark_price)
                                  * rules[name].spec.contract_multiplier)
            notional_price = max(quote.bid_price, quote.ask_price, position.mark_price)
            if name == intent.instrument_id:
                notional_price = max(notional_price, price)
            notionals[name] = rules[name].spec.notional(position.quantity, notional_price)
        risk_equity = min(projected.equity, executable_equity)
        drawdown = max(ZERO, (peak-risk_equity)/peak)
        gross = sum(notionals.values(), ZERO)
        checks = {
            "positive_equity": risk_equity > ZERO,
            "drawdown": drawdown <= limits.max_drawdown,
            "single_name": all(n <= risk_equity*limits.max_single_weight for n in notionals.values()),
            "gross_exposure": gross <= risk_equity*limits.max_gross_leverage,
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
        reservation = reservation_snapshot(filling=intent.order_id, filled=filled)
        if profiles and not reducing and reservation["available"] < ZERO:
            reason = reason or "INSUFFICIENT_RESERVED_COLLATERAL"
        risk_events.append({
            "order_id": intent.order_id, "timestamp": before.timestamp,
            "phase": "PROJECTED_FILL", "filled_quantity": filled,
            "marked_equity": projected.equity, "executable_equity": executable_equity,
            "risk_equity": risk_equity, "gross_notional": gross,
            "instrument_notionals": notionals, "drawdown": drawdown,
            "limits": asdict(limits), "checks": checks,
            "margin": ledger.initial_margin_snapshot(),
            "reservation": reservation,
            "reducing": reducing, "approved": reason is None, "reason": reason,
        })
        if reason is not None:
            raise _ProjectedRiskRejected(reason)

    for frame in frame_items:
        now = frame.timestamp
        if not history: checkpoint(now, "INITIAL")
        if any(e.instrument_id not in rules for e in frame.market_events):
            raise ValueError("market event outside the declared universe")
        ledger.process_events(frame.market_events)
        for event in frame.market_events:
            mark_times[event.instrument_id] = now
            mark_prices[event.instrument_id] = event.mark_price
        accepted_quotes: dict[str, ExecutableQuote] = {}
        for q in frame.quotes:
            if q.instrument_id not in rules:
                raise ValueError("quote outside the declared universe")
            if q.venue_id != policies[q.instrument_id].venue_id:
                raise ValueError("quote and funding venues differ")
            profile = profiles.get(q.instrument_id)
            if profile is not None and (profile.price_reason(q.bid_price) or profile.price_reason(q.ask_price)):
                raise ValueError("executable quote violates order rule price grid or bounds")
            prior = quotes.get(q.instrument_id)
            if prior is not None and q.event_time <= prior.event_time:
                # A late observation is retained in the input identity but
                # cannot roll time backwards or replenish consumed liquidity.
                if q.event_time == prior.event_time and q != prior:
                    raise ValueError("conflicting quote at the same venue timestamp")
                continue
            quotes[q.instrument_id] = q
            accepted_quotes[q.instrument_id] = q
        for cap in frame.capacity_snapshots:
            if cap.instrument_id not in rules or cap.policy.venue_id != policies[cap.instrument_id].venue_id:
                raise ValueError("capacity outside the declared venue/universe")
            previous = capacities.get(cap.instrument_id)
            if previous is not None and cap.ingestion_time < previous.ingestion_time:
                raise ValueError("capacity knowledge time moved backwards")
            capacities[cap.instrument_id] = cap
        checkpoint(now, "MARKET")
        depth = {(q.instrument_id, side): size for q in accepted_quotes.values()
                 for side, size in (("BUY", q.ask_quantity), ("SELL", q.bid_quantity))}
        fresh = accepted_quotes
        for state in tuple(pending.values()):
            intent = state.intent
            if intent.expires_at is not None and now >= intent.expires_at:
                transition(state, now, "EXPIRED", "TIME_IN_FORCE")
                continue
            q = fresh.get(intent.instrument_id)
            if q is None or quotes.get(intent.instrument_id) != q:
                continue
            if q.event_time <= state.created_at + limits.order_latency_ms:
                continue
            if now-q.event_time > limits.quote_max_age_ms:
                continue
            if intent.instrument_id not in mark_times or now-mark_times[intent.instrument_id] > limits.quote_max_age_ms:
                transition(state, now, "REJECTED", "STALE_OR_MISSING_MARK")
                continue
            spec = rules[intent.instrument_id].spec
            remaining = state.remaining_quantity
            current = ledger.position(intent.instrument_id)
            if intent.reduce_only:
                if current is None or (current.quantity > ZERO) == (remaining > ZERO):
                    transition(state, now, "REJECTED", "REDUCE_ONLY_NO_POSITION")
                    continue
                remaining = min(abs(remaining), abs(current.quantity)) * (ONE if remaining > ZERO else -ONE)
            side = "BUY" if remaining > ZERO else "SELL"
            px = execution_price(q, remaining)
            profile = profiles.get(intent.instrument_id)
            if profile is not None:
                reason = profile.price_reason(px)
                if reason is None and spec.notional(intent.quantity_delta, px) > profile.max_notional:
                    reason = reason or "MAX_ORDER_NOTIONAL"
                if reason:
                    transition(state, now, "REJECTED", reason)
                    continue
            if intent.limit_price is not None and (
                (remaining > ZERO and px > intent.limit_price)
                or (remaining < ZERO and px < intent.limit_price)):
                continue
            cap = capacity_for(intent.instrument_id, now)
            if cap is None:
                transition(state, now, "REJECTED", "MISSING_CURRENT_CAPACITY")
                continue
            quantity = spec.floor_quantity(min(abs(remaining), depth[(intent.instrument_id, side)]))
            quantity *= ONE if remaining > ZERO else -ONE
            if quantity == ZERO:
                continue
            before_fill = context(now)
            trade = OrderFillEvent(timestamp=now, instrument_id=intent.instrument_id,
                quantity_delta=quantity, execution_price=px, capacity=cap.policy,
                margin_mode=intent.margin_mode,
                isolated_margin=intent.isolated_margin if current is None else None,
                order_id=intent.order_id, fee_rate=costs.fee_rate)
            try:
                fill = ledger.apply_event(trade, trade_guard=lambda candidate:
                    check_projected_fill(before_fill, intent, candidate.filled_quantity, px,
                        minimum_exemption_requires_reduction=state.minimum_exemption_requires_reduction))
            except _ProjectedRiskRejected as exc:
                transition(state, now, "REJECTED", str(exc))
                continue
            except InsufficientMargin:
                transition(state, now, "REJECTED", "INSUFFICIENT_MARGIN")
                continue
            except QuantityRuleViolation:
                transition(state, now, "REJECTED", "QUANTITY_RULE")
                continue
            if fill.filled_quantity == ZERO:
                transition(state, now, "REJECTED", fill.reason or "CAPACITY_REJECTED")
                continue
            depth[(intent.instrument_id, side)] -= abs(fill.filled_quantity)
            done = fill.filled_quantity == state.remaining_quantity
            updated = transition(state, now, "FILLED" if done else "PARTIALLY_FILLED",
                filled=fill.filled_quantity, price=px)
            if intent.reduce_only and ledger.position(intent.instrument_id) is None and not done:
                transition(updated, now, "CANCELLED", "REDUCE_ONLY_REMAINDER")
            checkpoint(now, "FILL")
        ctx = context(now)
        actions = tuple(strategy(ctx))
        action_records = []
        for action in actions:
            if not isinstance(action, (OrderIntent, CancelOrder)):
                raise ValueError("strategy must return only order intents or cancellations")
            action_records.append(asdict(action))
            if isinstance(action, CancelOrder):
                state = pending.get(action.order_id)
                if state is not None:
                    transition(state, now, "CANCELLED", "STRATEGY_CANCEL")
                else:
                    order_events.append({"order_id":action.order_id, "timestamp":now,
                        "status":"CANCEL_REJECTED", "reason":"ORDER_NOT_OPEN",
                        "filled_quantity":"0", "fill_price":None})
                continue
            if action.order_id in orders:
                raise ValueError("duplicate order_id")
            state = OrderState(action, now, action.quantity_delta)
            reason = None
            q = quotes.get(action.instrument_id)
            if action.instrument_id not in rules:
                reason = "UNKNOWN_INSTRUMENT"
            elif action.expires_at is not None and action.expires_at <= now:
                reason = "INVALID_EXPIRY"
            elif q is None or now-q.event_time > limits.quote_max_age_ms:
                reason = "STALE_OR_MISSING_QUOTE"
            elif action.instrument_id not in mark_times or now-mark_times[action.instrument_id] > limits.quote_max_age_ms:
                reason = "STALE_OR_MISSING_MARK"
            elif capacity_for(action.instrument_id, now) is None:
                reason = "MISSING_CURRENT_CAPACITY"
            else:
                current = ledger.position(action.instrument_id)
                px = q.ask_price if action.quantity_delta > ZERO else q.bid_price
                profile = profiles.get(action.instrument_id)
                if profile is not None:
                    order_type = "MARKET" if action.limit_price is None else "LIMIT"
                    bound = profile.market_max_quantity if order_type == "MARKET" else profile.limit_max_quantity
                    px = action.limit_price or execution_price(q, action.quantity_delta)
                    if action.margin_mode != MarginMode.CROSS:
                        reason = "UNSUPPORTED_ORDER_MARGIN_MODE"
                    elif order_type not in profile.supported_order_types:
                        reason = "UNSUPPORTED_ORDER_TYPE"
                    elif profile.price_reason(px):
                        reason = profile.price_reason(px)
                    elif abs(action.quantity_delta) > bound:
                        reason = "MAX_ORDER_QUANTITY"
                    elif rules[action.instrument_id].spec.notional(action.quantity_delta, px) > profile.max_notional:
                        reason = "MAX_ORDER_NOTIONAL"
                try:
                    if reason is None:
                        if profile is not None and profile.minimum_policy == "ORIGINAL_ORDER":
                            reducing = (action.reduce_only and current is not None
                                and (current.quantity > ZERO) != (action.quantity_delta > ZERO)
                                and abs(action.quantity_delta) <= abs(current.quantity))
                            rules[action.instrument_id].spec.validate_quantity(
                                action.quantity_delta, px, reducing=reducing)
                        else:
                            rules[action.instrument_id].spec.validate_trade_quantity(
                                action.quantity_delta, px,
                                current_quantity=ZERO if current is None else current.quantity)
                except QuantityRuleViolation:
                    reason = "QUANTITY_RULE"
                if reason is None:
                    try:
                        rules[action.instrument_id].spec.validate_quantity(action.quantity_delta, px)
                    except QuantityRuleViolation:
                        # An original closing order admitted only by a minimum
                        # exemption must never use it to open inventory later.
                        state = replace(state, minimum_exemption_requires_reduction=True)
                    reason = risk_reason(context(now), action, action.quantity_delta, px)
                    if reason is None and profile is not None and not action.reduce_only:
                        if reservation_snapshot(extra=state)["available"] < ZERO:
                            reason = "INSUFFICIENT_RESERVED_COLLATERAL"
            transition(state, now, "OPEN" if reason is None else "REJECTED", reason)
        decisions.append({
            "timestamp":now, "equity":canonical_decimal(ctx.equity),
            "positions":{n:p.as_canonical_dict() for n,p in ctx.positions.items()},
            "quotes":{n:asdict(q) for n,q in ctx.quotes.items()},
            "history_length":len(ctx.history), "actions":action_records,
            "reserved_collateral": dict(ctx.reserved_collateral),
            "available_collateral": ctx.available_collateral,
        })
    reconciliation = ledger.reconciliation_snapshot()
    if reconciliation.residual != ZERO:
        raise ValueError("perpetual cash-flow reconciliation failed")
    inputs = {
        "schema":SCHEMA, "release":release_identity, "dataset":dataset_identity,
        "strategy":strategy_identity, "initial_collateral":initial_collateral,
        "rules":[r.as_canonical_dict() for r in rule_items],
        "funding_policies":[p.as_canonical_dict() for p in policy_items],
        "frames":[{"timestamp":f.timestamp, "quotes":[asdict(q) for q in f.quotes],
            "market_events":[asdict(e) for e in f.market_events],
            "capacity":[c.as_canonical_dict() for c in f.capacity_snapshots]} for f in frame_items],
        "limits":asdict(limits), "costs":asdict(costs),
        "risk_policy":risk_policy_identity,
        "order_rules": None if profile_items is None else [asdict(p) for p in profile_items],
        "reservation_policy": RESERVATION_POLICY if profiles else "UNRESERVED_LEGACY_DIAGNOSTIC",
    }
    result = {
        "schema_version":SCHEMA, "authoritative":False, "evidence_status":"TECHNICAL_VALIDATION_ONLY",
        "input_hash":exact_identity_hash(inputs, name="perpetual_simulation_inputs"),
        "risk_history_source":"LEDGER_EVENT_REPLAY",
        "execution_model":"AGGRESSIVE_TOP_OF_BOOK_WITH_EMBEDDED_ADVERSE_SLIPPAGE",
        "full_research_risk_9_of_9":False,
        "authority_blockers":["EXTERNAL_SIGNED_RAW_ARCHIVE_AUTHORITY_MISSING",
                              "INDEPENDENT_ECONOMIC_VALIDATION_REQUIRED"]
                             + ([] if profiles else ["ORDER_RULE_PROFILE_MISSING"]),
        "order_rule_profiles": None if profile_items is None else [asdict(p) for p in profile_items],
        "ending_reservation": reservation_snapshot(),
        "ending_state":ledger.state_as_canonical_dict(),
        "reconciliation":reconciliation.as_canonical_dict(),
        "ledger_events":[dict(e) for e in ledger.event_records],
        "orders":[{"order_id":s.intent.order_id, "instrument_id":s.intent.instrument_id,
            "status":s.status, "reason":s.reason, "created_at":s.created_at, "filled_at":s.filled_at,
            "filled_quantity":canonical_decimal(s.filled_quantity),
            "minimum_exemption_requires_reduction":s.minimum_exemption_requires_reduction,
            "remaining_quantity":canonical_decimal(s.remaining_quantity)} for s in orders.values()],
        "order_events":order_events, "decision_events":decisions, "risk_events":risk_events,
        "equity_history":[{"timestamp":p.timestamp,"sequence":p.sequence,"phase":p.phase,
            "equity":canonical_decimal(p.equity),"drawdown":canonical_decimal(p.drawdown),
            "return_since_previous":None if p.return_since_previous is None
            else canonical_decimal(p.return_since_previous)} for p in history],
    }
    result = _canonical(result)
    result["result_hash"] = exact_identity_hash(result, name="perpetual_simulation_result")
    return result
