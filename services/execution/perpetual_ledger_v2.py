"""Deterministic v2 event ledger for single-currency linear perpetuals.

This module is intentionally independent from the legacy integer spot-like
paper contracts.  It is a pure L2 research primitive: no network, broker,
credential, promotion, or order-submission capability lives here.

Supported scope is deliberately narrow and fail-closed:

* linear USDT- or USDC-margined perpetuals, never a mixed-currency wallet;
* Decimal quantities/prices/cash flows with canonical string serialization;
* explicit exchange quantity rules and load-bearing participation capacity;
* cross and isolated collateral, tiered initial/maintenance margin;
* mark-price liquidation with explicit insurance/ADL inputs;
* settlement events debit the position held immediately before settlement;
* one reconciliation identity spans price PnL and every modeled cash flow.

Inverse contracts, portfolio margin, hedge-mode dual positions, partial
liquidation, and venue bankruptcy-price discovery are not approximated.  They
must be added as separately reviewed contract versions before use.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Rounded,
    localcontext,
)
from enum import Enum
from itertools import groupby
from typing import Callable, Iterator, Mapping, Sequence

from quantos.contracts.decimal_arithmetic import (
    DECIMAL_CONTEXT_VERSION,
    _DECIMAL_CONTEXT_PRECISION,
    _DECIMAL_CONTEXT_EMIN,
    _DECIMAL_CONTEXT_EMAX,
    _new_decimal_context,
    ledger_decimal_context,
    _reviewed_decimal,
    _decimal,
    canonical_decimal,
    _P,
    _R,
)


ZERO = Decimal("0")
ONE = Decimal("1")
LINEAR_USDT_PERPETUAL = "LINEAR_USDT_PERPETUAL"
LINEAR_USDC_PERPETUAL = "LINEAR_USDC_PERPETUAL"
COST_BASIS_POLICY = "EXACT_ENTRY_NOTIONAL_PRORATA_TRUNCATE_RETAIN_REMAINDER_V1"
MARGIN_ADMISSION_POLICY = "INITIAL_FOR_NEW_EXPOSURE_MAINTENANCE_FOR_REDUCTION_V1"
OPENING_STATE_POLICY = "IMPORTED_BALANCES_EXCLUDE_OPENING_UNREALIZED_V1"
REPORTED_CASH_POLICY = "REPORTED_QUOTE_CASH_AFTER_FILLS_V1"
BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID = "BINANCE_USDM.UTC_DAY.v1"

_EXACT_CASH_CONTEXT_PRECISION = 256
_MAX_ECONOMIC_SIGNIFICANT_DIGITS = 64
_MAX_ECONOMIC_ADJUSTED_EXPONENT = 96
_MIN_SETTLEMENT_QUANTUM = Decimal("1e-24")
_MAX_SETTLEMENT_QUANTUM = Decimal("1")
_MAX_SETTLEMENT_UNITS = 10**36 - 1


class PerpetualLedgerError(RuntimeError):
    """Base class for fail-closed v2 ledger errors."""


class UnsupportedContract(PerpetualLedgerError):
    """The requested contract semantics are outside the reviewed v2 scope."""


class QuantityRuleViolation(PerpetualLedgerError):
    """A quantity violates the versioned exchange contract rules."""


class AccountNotionalLimit(QuantityRuleViolation):
    """A proposed new exposure exceeds frozen account admission coverage."""


class MarginConfigurationError(PerpetualLedgerError):
    """Margin brackets or contract metadata are malformed."""


class InsufficientMargin(PerpetualLedgerError):
    """A trade cannot clear the applicable initial-margin requirement."""


class MissingLiquidationBackstop(PerpetualLedgerError):
    """A liquidation cannot be booked without explicit insurance and ADL facts."""


class InsufficientLiquidationBackstop(PerpetualLedgerError):
    """Explicit backstop adjustments still leave the account below zero."""


class EventOrderViolation(PerpetualLedgerError):
    """An event would move the deterministic venue clock backwards."""


class CashQuantumViolation(PerpetualLedgerError):
    """A settlement cash amount cannot be represented by the reviewed exact grid."""


def _positive(value: object, field: str) -> Decimal:
    result = _decimal(value, field)
    if result <= ZERO:
        raise ValueError(f"{field} must be positive")
    return result


def _non_negative(value: object, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < ZERO:
        raise ValueError(f"{field} must be non-negative")
    return result


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("timestamp must be a non-negative integer")
    return value


def _exact_cash_context() -> Context:
    """A bounded context whose reviewed operands cannot round silently."""
    return Context(
        prec=_EXACT_CASH_CONTEXT_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=_DECIMAL_CONTEXT_EMIN,
        Emax=_DECIMAL_CONTEXT_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[
            InvalidOperation,
            DivisionByZero,
            Overflow,
            Inexact,
            Rounded,
        ],
    )


def _exact_operand(value: object, field: str) -> Decimal:
    result = _decimal(value, field)
    digits = len(result.as_tuple().digits)
    adjusted = 0 if result == ZERO else result.adjusted()
    if digits > _MAX_ECONOMIC_SIGNIFICANT_DIGITS:
        raise CashQuantumViolation(
            f"{field} exceeds the reviewed significant-digit bound"
        )
    if abs(adjusted) > _MAX_ECONOMIC_ADJUSTED_EXPONENT:
        raise CashQuantumViolation(
            f"{field} exceeds the reviewed decimal exponent bound"
        )
    return result


def _exact_sum(*values: Decimal, field: str) -> Decimal:
    operands = tuple(_exact_operand(value, field) for value in values)
    try:
        with localcontext(_exact_cash_context()):
            return sum(operands, ZERO)
    except (ArithmeticError, DecimalException) as exc:
        raise CashQuantumViolation(f"{field} cannot be added exactly") from exc


def _exact_product(*values: Decimal, field: str) -> Decimal:
    operands = tuple(_exact_operand(value, field) for value in values)
    try:
        with localcontext(_exact_cash_context()):
            result = ONE
            for operand in operands:
                result *= operand
            return result
    except (ArithmeticError, DecimalException) as exc:
        raise CashQuantumViolation(f"{field} cannot be multiplied exactly") from exc


class MarginMode(str, Enum):
    CROSS = "CROSS"
    ISOLATED = "ISOLATED"


class CapacityAction(str, Enum):
    REJECT = "REJECT"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class MarginBracket:
    notional_floor: Decimal
    notional_cap: Decimal | None
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    maintenance_amount: Decimal = ZERO

    @_reviewed_decimal
    def __post_init__(self) -> None:
        floor = _non_negative(self.notional_floor, "notional_floor")
        cap = (
            None
            if self.notional_cap is None
            else _positive(self.notional_cap, "notional_cap")
        )
        initial = _positive(self.initial_margin_rate, "initial_margin_rate")
        maintenance = _positive(
            self.maintenance_margin_rate, "maintenance_margin_rate"
        )
        amount = _non_negative(self.maintenance_amount, "maintenance_amount")
        if cap is not None and cap <= floor:
            raise MarginConfigurationError("notional_cap must exceed notional_floor")
        if initial > ONE or maintenance > ONE:
            raise MarginConfigurationError("margin rates cannot exceed one")
        if maintenance > initial:
            raise MarginConfigurationError(
                "maintenance margin rate cannot exceed initial margin rate"
            )
        object.__setattr__(self, "notional_floor", floor)
        object.__setattr__(self, "notional_cap", cap)
        object.__setattr__(self, "initial_margin_rate", initial)
        object.__setattr__(self, "maintenance_margin_rate", maintenance)
        object.__setattr__(self, "maintenance_amount", amount)

    def contains(self, notional: Decimal) -> bool:
        return self.notional_floor <= notional and (
            self.notional_cap is None or notional < self.notional_cap
        )

    def as_canonical_dict(self) -> dict[str, str | None]:
        return {
            "notional_floor": canonical_decimal(self.notional_floor),
            "notional_cap": (
                None
                if self.notional_cap is None
                else canonical_decimal(self.notional_cap)
            ),
            "initial_margin_rate": canonical_decimal(self.initial_margin_rate),
            "maintenance_margin_rate": canonical_decimal(
                self.maintenance_margin_rate
            ),
            "maintenance_amount": canonical_decimal(self.maintenance_amount),
        }


@dataclass(frozen=True)
class MarginRequirement:
    bracket: MarginBracket
    notional: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    outside_observed_brackets: bool = False

    def as_canonical_dict(self) -> dict[str, object]:
        result = {
            "bracket": self.bracket.as_canonical_dict(),
            "notional": canonical_decimal(self.notional),
            "initial_margin": canonical_decimal(self.initial_margin),
            "maintenance_margin": canonical_decimal(self.maintenance_margin),
        }
        if self.outside_observed_brackets:
            result["outside_observed_brackets"] = True
        return result


@dataclass(frozen=True)
class ContractSpec:
    instrument_id: str
    contract_type: str
    margin_asset: str
    contract_multiplier: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    liquidation_fee_rate: Decimal
    settlement_quantum: Decimal
    margin_brackets: tuple[MarginBracket, ...]
    new_risk_notional_cap: Decimal | None = None
    margin_overflow_policy: str = "NONE"

    @_reviewed_decimal
    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id.strip():
            raise ValueError("instrument_id must be non-empty")
        margin_assets = {LINEAR_USDT_PERPETUAL: "USDT", LINEAR_USDC_PERPETUAL: "USDC"}
        if self.contract_type not in margin_assets:
            raise UnsupportedContract("only reviewed linear perpetual types are supported")
        if self.margin_asset != margin_assets[self.contract_type]:
            raise UnsupportedContract("margin asset must match the linear contract type")
        multiplier = _positive(self.contract_multiplier, "contract_multiplier")
        step = _positive(self.quantity_step, "quantity_step")
        minimum = _positive(self.min_quantity, "min_quantity")
        min_notional = _positive(self.min_notional, "min_notional")
        liquidation_fee = _non_negative(
            self.liquidation_fee_rate, "liquidation_fee_rate"
        )
        settlement_quantum = _positive(
            self.settlement_quantum, "settlement_quantum"
        )
        if liquidation_fee >= ONE:
            raise ValueError("liquidation_fee_rate must be below one")
        quantum_digits = list(settlement_quantum.as_tuple().digits)
        while len(quantum_digits) > 1 and quantum_digits[-1] == 0:
            quantum_digits.pop()
        if (
            tuple(quantum_digits) != (1,)
            or not _MIN_SETTLEMENT_QUANTUM
            <= settlement_quantum
            <= _MAX_SETTLEMENT_QUANTUM
        ):
            raise MarginConfigurationError(
                "settlement_quantum must be a reviewed base-10 cash quantum "
                "between 1e-24 and 1"
            )
        if minimum % step != ZERO:
            raise MarginConfigurationError("min_quantity must lie on quantity_step")
        brackets = tuple(self.margin_brackets)
        if not brackets:
            raise MarginConfigurationError("at least one margin bracket is required")
        if brackets[0].notional_floor != ZERO:
            raise MarginConfigurationError("first margin bracket must start at zero")
        if brackets[0].maintenance_amount != ZERO:
            raise MarginConfigurationError(
                "first margin bracket maintenance_amount must be zero"
            )
        for index, bracket in enumerate(brackets):
            if bracket.notional_cap is None and index != len(brackets) - 1:
                raise MarginConfigurationError("only the final bracket may have no cap")
            if index:
                previous = brackets[index - 1]
                if previous.notional_cap != bracket.notional_floor:
                    raise MarginConfigurationError(
                        "margin brackets must be ordered and contiguous"
                    )
                if bracket.initial_margin_rate < previous.initial_margin_rate:
                    raise MarginConfigurationError(
                        "initial margin rates cannot decrease across brackets"
                    )
                if bracket.maintenance_margin_rate < previous.maintenance_margin_rate:
                    raise MarginConfigurationError(
                        "maintenance margin rates cannot decrease across brackets"
                    )
                boundary = bracket.notional_floor
                previous_maintenance = (
                    boundary * previous.maintenance_margin_rate
                    - previous.maintenance_amount
                )
                current_maintenance = (
                    boundary * bracket.maintenance_margin_rate
                    - bracket.maintenance_amount
                )
                if current_maintenance != previous_maintenance:
                    raise MarginConfigurationError(
                        "maintenance margin must be continuous across brackets"
                    )
        admission_cap = self.new_risk_notional_cap
        if admission_cap is not None:
            admission_cap = _positive(admission_cap, "new_risk_notional_cap")
        finite_cap = brackets[-1].notional_cap
        if finite_cap is not None:
            if (admission_cap is None or admission_cap > finite_cap
                    or self.margin_overflow_policy != "LAST_BRACKET_RISK_ESTIMATE_V1"):
                raise MarginConfigurationError("finite margin table needs a bounded admission cap and risk estimate policy")
        elif self.margin_overflow_policy != "NONE":
            raise MarginConfigurationError("unbounded margin table cannot declare a finite overflow policy")
        object.__setattr__(self, "new_risk_notional_cap", admission_cap)
        object.__setattr__(self, "contract_multiplier", multiplier)
        object.__setattr__(self, "quantity_step", step)
        object.__setattr__(self, "min_quantity", minimum)
        object.__setattr__(self, "min_notional", min_notional)
        object.__setattr__(self, "liquidation_fee_rate", liquidation_fee)
        object.__setattr__(self, "settlement_quantum", settlement_quantum)
        object.__setattr__(self, "margin_brackets", brackets)

    def cash_units(self, value: Decimal, field: str) -> int:
        """Return exact integer settlement units or fail closed.

        The unit bound leaves fourteen significant digits of headroom inside
        the reviewed 50-digit ledger context even for the finest accepted
        quantum.  Account arithmetic uses Python integer units, so a small
        non-zero cash flow can never disappear next to a larger balance.
        """
        amount = _exact_operand(value, field)
        try:
            with localcontext(_exact_cash_context()):
                units_decimal = amount / self.settlement_quantum
                integral = units_decimal.to_integral_value()
        except (ArithmeticError, DecimalException) as exc:
            raise CashQuantumViolation(
                f"{field} cannot be represented in settlement units"
            ) from exc
        if units_decimal != integral:
            raise CashQuantumViolation(
                f"{field} is off the {canonical_decimal(self.settlement_quantum)} "
                "cash settlement grid"
            )
        units = int(integral)
        if abs(units) > _MAX_SETTLEMENT_UNITS:
            raise CashQuantumViolation(
                f"{field} exceeds the reviewed settlement-unit bound"
            )
        return units

    def cash_from_units(self, units: int, field: str) -> Decimal:
        if isinstance(units, bool) or not isinstance(units, int):
            raise TypeError(f"{field} units must be an integer")
        if abs(units) > _MAX_SETTLEMENT_UNITS:
            raise CashQuantumViolation(
                f"{field} exceeds the reviewed settlement-unit bound"
            )
        try:
            with localcontext(_exact_cash_context()):
                return Decimal(units) * self.settlement_quantum
        except (ArithmeticError, DecimalException) as exc:
            raise CashQuantumViolation(
                f"{field} cannot be constructed exactly"
            ) from exc

    def validate_cash(self, value: Decimal, field: str) -> Decimal:
        return self.cash_from_units(self.cash_units(value, field), field)

    def cash_product(self, *values: Decimal, field: str) -> Decimal:
        return self.validate_cash(_exact_product(*values, field=field), field)

    @_reviewed_decimal
    def notional(self, quantity: Decimal, price: Decimal) -> Decimal:
        qty = _decimal(quantity, "quantity")
        px = _positive(price, "price")
        return _exact_product(
            abs(qty), px, self.contract_multiplier, field="contract_notional"
        )

    @_reviewed_decimal
    def validate_quantity(
        self, quantity: Decimal, price: Decimal, *, reducing: bool = False
    ) -> Decimal:
        qty = _decimal(quantity, "quantity")
        if qty == ZERO:
            raise QuantityRuleViolation("quantity cannot be zero")
        absolute = abs(qty)
        if absolute % self.quantity_step != ZERO:
            raise QuantityRuleViolation("quantity is off the exchange step grid")
        if not reducing and absolute < self.min_quantity:
            raise QuantityRuleViolation("quantity is below min_quantity")
        if not reducing and self.notional(absolute, price) < self.min_notional:
            raise QuantityRuleViolation("quantity is below min_notional")
        return qty

    @_reviewed_decimal
    def validate_trade_quantity(
        self, quantity: Decimal, price: Decimal, *, current_quantity: Decimal = ZERO
    ) -> Decimal:
        """Apply opening minimums only to newly opened inventory, including reversals."""
        current = _decimal(current_quantity, "current_quantity")
        qty = self.validate_quantity(quantity, price, reducing=True)
        if current == ZERO or (current > ZERO) == (qty > ZERO):
            return self.validate_quantity(qty, price)
        if abs(qty) > abs(current):
            self.validate_quantity(
                _exact_sum(current, qty, field="reversal_quantity"), price
            )
        return qty

    @_reviewed_decimal
    def floor_quantity(self, quantity: Decimal) -> Decimal:
        absolute = abs(_decimal(quantity, "quantity"))
        steps = (absolute / self.quantity_step).to_integral_value(
            rounding=ROUND_FLOOR
        )
        return steps * self.quantity_step

    @_reviewed_decimal
    def margin_requirement(self, notional: Decimal) -> MarginRequirement:
        value = _non_negative(notional, "notional")
        bracket = next(
            (candidate for candidate in self.margin_brackets if candidate.contains(value)),
            None,
        )
        if bracket is None:
            if self.margin_overflow_policy != "LAST_BRACKET_RISK_ESTIMATE_V1":
                raise MarginConfigurationError("notional does not map to a margin bracket")
            # A market move must not erase an existing liability. This is an
            # explicitly named research estimate, never unlimited admission or
            # a claim about the venue beyond the observed finite table.
            bracket = self.margin_brackets[-1]
        initial = value * bracket.initial_margin_rate
        maintenance = max(
            ZERO,
            value * bracket.maintenance_margin_rate
            - bracket.maintenance_amount,
        )
        return MarginRequirement(bracket, value, initial, maintenance, not bracket.contains(value))

    def as_canonical_dict(self) -> dict[str, object]:
        result = {
            "decimal_context": DECIMAL_CONTEXT_VERSION,
            "margin_admission_policy": MARGIN_ADMISSION_POLICY,
            "instrument_id": self.instrument_id,
            "contract_type": self.contract_type,
            "margin_asset": self.margin_asset,
            "contract_multiplier": canonical_decimal(self.contract_multiplier),
            "quantity_step": canonical_decimal(self.quantity_step),
            "min_quantity": canonical_decimal(self.min_quantity),
            "min_notional": canonical_decimal(self.min_notional),
            "liquidation_fee_rate": canonical_decimal(
                self.liquidation_fee_rate
            ),
            "settlement_quantum": canonical_decimal(self.settlement_quantum),
            "margin_brackets": [
                bracket.as_canonical_dict() for bracket in self.margin_brackets
            ],
        }
        if self.new_risk_notional_cap is not None:
            result.update(new_risk_notional_cap=canonical_decimal(self.new_risk_notional_cap),
                          margin_overflow_policy=self.margin_overflow_policy)
        return result


@dataclass(frozen=True)
class _ReviewedCapacityPeriodRule:
    rule_id: str
    venue_id: str
    epoch_ms: int
    duration_ms: int
    adv_observation_periods: int

    def bounds(self, period_index: int) -> tuple[int, int]:
        if (
            isinstance(period_index, bool)
            or not isinstance(period_index, int)
            or period_index < 0
        ):
            raise ValueError("capacity period_index must be a non-negative integer")
        start = self.epoch_ms + period_index * self.duration_ms
        return start, start + self.duration_ms


# The period geometry is reviewed code, not caller data.  Adding or changing a
# rule changes capacity semantics and therefore requires a new versioned id.
_REVIEWED_CAPACITY_PERIOD_RULES = {
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID: _ReviewedCapacityPeriodRule(
        rule_id=BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
        venue_id="BINANCE_USDM",
        epoch_ms=0,
        duration_ms=86_400_000,
        adv_observation_periods=30,
    ),
}


@dataclass(frozen=True)
class CapacityPolicy:
    venue_id: str
    window_id: str
    period_rule_id: str
    period_index: int
    adv_notional: Decimal
    max_participation_rate: Decimal
    action: CapacityAction

    @_reviewed_decimal
    def __post_init__(self) -> None:
        if not isinstance(self.venue_id, str) or not self.venue_id.strip():
            raise ValueError("venue_id must be non-empty")
        if not isinstance(self.window_id, str) or not self.window_id.strip():
            raise ValueError("window_id must be non-empty")
        if not isinstance(self.period_rule_id, str) or not self.period_rule_id.strip():
            raise ValueError("period_rule_id must be non-empty")
        try:
            rule = _REVIEWED_CAPACITY_PERIOD_RULES[self.period_rule_id]
        except KeyError as exc:
            raise MarginConfigurationError(
                "capacity period rule is not in the reviewed registry"
            ) from exc
        if rule.venue_id != self.venue_id:
            raise MarginConfigurationError(
                "capacity period rule does not belong to venue_id"
            )
        rule.bounds(self.period_index)
        adv = _positive(self.adv_notional, "adv_notional")
        rate = _positive(self.max_participation_rate, "max_participation_rate")
        if rate > ONE:
            raise ValueError("max_participation_rate cannot exceed one")
        action = (
            self.action
            if isinstance(self.action, CapacityAction)
            else CapacityAction(self.action)
        )
        object.__setattr__(self, "adv_notional", adv)
        object.__setattr__(self, "max_participation_rate", rate)
        object.__setattr__(self, "action", action)

    @property
    def consumption_window_start(self) -> int:
        return _REVIEWED_CAPACITY_PERIOD_RULES[self.period_rule_id].bounds(
            self.period_index
        )[0]

    @property
    def consumption_window_end(self) -> int:
        return _REVIEWED_CAPACITY_PERIOD_RULES[self.period_rule_id].bounds(
            self.period_index
        )[1]

    @property
    def required_adv_window_start(self) -> int:
        rule = _REVIEWED_CAPACITY_PERIOD_RULES[self.period_rule_id]
        return (
            self.consumption_window_start
            - rule.adv_observation_periods * rule.duration_ms
        )

    @property
    def required_adv_window_end(self) -> int:
        return self.consumption_window_start

    @property
    def consumption_period_identity(self) -> tuple[str, str, int]:
        return self.venue_id, self.period_rule_id, self.period_index

    def contains_timestamp(self, timestamp: int) -> bool:
        value = _timestamp(timestamp)
        return self.consumption_window_start <= value < self.consumption_window_end

    @property
    @_reviewed_decimal
    def maximum_notional(self) -> Decimal:
        return _exact_product(
            self.adv_notional,
            self.max_participation_rate,
            field="capacity.maximum_notional",
        )

    def as_canonical_dict(self) -> dict[str, str | int]:
        return {
            "venue_id": self.venue_id,
            "window_id": self.window_id,
            "period_rule_id": self.period_rule_id,
            "period_index": self.period_index,
            "consumption_window_start": self.consumption_window_start,
            "consumption_window_end": self.consumption_window_end,
            "consumption_window_duration_ms": (
                self.consumption_window_end - self.consumption_window_start
            ),
            "consumption_period_epoch_ms": _REVIEWED_CAPACITY_PERIOD_RULES[
                self.period_rule_id
            ].epoch_ms,
            "required_adv_observation_periods": _REVIEWED_CAPACITY_PERIOD_RULES[
                self.period_rule_id
            ].adv_observation_periods,
            "required_adv_window_start": self.required_adv_window_start,
            "required_adv_window_end": self.required_adv_window_end,
            "adv_notional": canonical_decimal(self.adv_notional),
            "max_participation_rate": canonical_decimal(
                self.max_participation_rate
            ),
            "maximum_notional": canonical_decimal(self.maximum_notional),
            "action": self.action.value,
        }


@dataclass(frozen=True)
class LiquidationBackstop:
    insurance_adjustment: Decimal
    adl_adjustment: Decimal

    @_reviewed_decimal
    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "insurance_adjustment",
            _decimal(self.insurance_adjustment, "insurance_adjustment"),
        )
        object.__setattr__(
            self,
            "adl_adjustment",
            _decimal(self.adl_adjustment, "adl_adjustment"),
        )

    def as_canonical_dict(self) -> dict[str, str]:
        return {
            "insurance_adjustment": canonical_decimal(
                self.insurance_adjustment
            ),
            "adl_adjustment": canonical_decimal(self.adl_adjustment),
        }


@dataclass(frozen=True)
class TradeEvent:
    timestamp: int
    instrument_id: str
    quantity_delta: Decimal
    execution_price: Decimal
    margin_mode: MarginMode
    capacity: CapacityPolicy
    isolated_margin: Decimal | None = None
    fee: Decimal = ZERO
    spread_cost: Decimal = ZERO
    impact_cost: Decimal = ZERO

    @_reviewed_decimal
    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        if not isinstance(self.instrument_id, str) or not self.instrument_id:
            raise ValueError("instrument_id must be non-empty")
        quantity = _decimal(self.quantity_delta, "quantity_delta")
        if quantity == ZERO:
            raise QuantityRuleViolation("quantity_delta cannot be zero")
        object.__setattr__(self, "quantity_delta", quantity)
        object.__setattr__(
            self, "execution_price", _positive(self.execution_price, "execution_price")
        )
        mode = (
            self.margin_mode
            if isinstance(self.margin_mode, MarginMode)
            else MarginMode(self.margin_mode)
        )
        object.__setattr__(self, "margin_mode", mode)
        if not isinstance(self.capacity, CapacityPolicy):
            raise TypeError("capacity must be a CapacityPolicy")
        if not self.capacity.contains_timestamp(self.timestamp):
            raise ValueError("trade timestamp lies outside capacity consumption window")
        isolated = (
            None
            if self.isolated_margin is None
            else _positive(self.isolated_margin, "isolated_margin")
        )
        object.__setattr__(self, "isolated_margin", isolated)
        object.__setattr__(self, "fee", _non_negative(self.fee, "fee"))
        object.__setattr__(
            self, "spread_cost", _non_negative(self.spread_cost, "spread_cost")
        )
        object.__setattr__(
            self, "impact_cost", _non_negative(self.impact_cost, "impact_cost")
        )


@dataclass(frozen=True, kw_only=True)
class OrderFillEvent(TradeEvent):
    """Execution slice of an order already admitted by the simulation runtime.

    Opening minimums belong to the parent order. Each fill still enforces the
    quantity grid, capacity, margin and exact cash settlement. The caller owns
    order identity, admission, remaining quantity and terminal-state checks;
    this accounting event is not an order authorization or a live API.
    Costs use actual filled notional, avoiding a rounded partial/request ratio.
    """
    order_id: str
    fee_rate: Decimal
    liquidity_role: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("order_id must be non-empty")
        object.__setattr__(self, "fee_rate", _non_negative(self.fee_rate, "fee_rate"))
        if self.liquidity_role is not None and self.liquidity_role not in ("MAKER", "TAKER"):
            raise ValueError("order fill liquidity role must be MAKER or TAKER")
        if self.fee != ZERO or self.spread_cost != ZERO or self.impact_cost != ZERO:
            raise ValueError("order fill costs require fee_rate and embedded executable prices")


class ReportedFillReplayError(PerpetualLedgerError):
    """The model cannot reproduce a recorded fill exactly; observation remains."""


@dataclass(frozen=True, kw_only=True)
class ReportedFillReplayEvent(TradeEvent):
    """Technical replay of a bound receipt, not venue-fill persistence/admission.

    The caller keeps the original receipt durably before attempting projection,
    validates its parent order and deduplicates trade IDs. A failed model check
    leaves projection unresolved; it never rejects or resizes the venue fact.
    Only exact nonnegative quote-currency commissions are supported here.
    """
    order_id: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("bound parent order required")
        if (not isinstance(self.receipt_sha256, str) or len(self.receipt_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.receipt_sha256)):
            raise ValueError("receipt digest required")
        if self.spread_cost != ZERO or self.impact_cost != ZERO:
            raise ValueError("reported execution prices already contain execution costs")


@dataclass(frozen=True)
class FundingEvent:
    timestamp: int
    instrument_id: str
    funding_rate: Decimal
    mark_price: Decimal
    liquidation_backstop: LiquidationBackstop | None = None

    @_reviewed_decimal
    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        if not isinstance(self.instrument_id, str) or not self.instrument_id:
            raise ValueError("instrument_id must be non-empty")
        rate = _decimal(self.funding_rate, "funding_rate")
        if abs(rate) > Decimal("0.10"):
            raise ValueError("funding_rate is outside the reviewed domain")
        object.__setattr__(self, "funding_rate", rate)
        object.__setattr__(
            self, "mark_price", _positive(self.mark_price, "mark_price")
        )


class ReportedCashReplayError(PerpetualLedgerError):
    """Retain the external observation; this model cannot admit its projection."""


@dataclass(frozen=True, kw_only=True)
class ReportedCashReplayPolicy:
    """Explicit opt-in; default simulation identity and semantics stay unchanged."""
    account_ref_sha256: str
    asset: str

    def __post_init__(self):
        if (type(self.account_ref_sha256) is not str or re.fullmatch('[a-f0-9]{64}',self.account_ref_sha256) is None
            or self.asset not in ('USDT','USDC')):
            raise ValueError('reported cash requires one account namespace and quote')

    def as_canonical_dict(self):
        return {'policy':REPORTED_CASH_POLICY,'account_ref_sha256':self.account_ref_sha256,
                'asset':self.asset,'source_verified':False,'scope':'TECHNICAL_VALIDATION_ONLY'}


@dataclass(frozen=True, kw_only=True)
class ReportedCashFlowEvent:
    """Observed funding or external cash only; never a transfer execution API.

    Commissions and realized price PnL belong to reported fills and cannot be
    posted again from income history. The input journal owns source provenance;
    this ledger deduplicates monetary identity, not transport envelopes.
    """
    timestamp: int
    account_ref_sha256: str
    asset: str
    income_type: str
    transaction_id: str
    amount: Decimal
    instrument_id: str | None
    source_sha256: str

    @_reviewed_decimal
    def __post_init__(self):
        object.__setattr__(self,'timestamp',_timestamp(self.timestamp))
        ReportedCashReplayPolicy(account_ref_sha256=self.account_ref_sha256,asset=self.asset)
        if (self.income_type not in ('FUNDING_FEE','TRANSFER') or type(self.transaction_id) is not str or
            re.fullmatch('[1-9][0-9]{0,18}',self.transaction_id) is None or int(self.transaction_id)>2**63-1 or
            type(self.source_sha256) is not str or re.fullmatch('[a-f0-9]{64}',self.source_sha256) is None):
            raise ValueError('unsupported reported cash identity or category')
        if (self.income_type=='TRANSFER' and self.instrument_id is not None or
            self.income_type=='FUNDING_FEE' and (type(self.instrument_id) is not str or not self.instrument_id)):
            raise ValueError('reported cash instrument scope')
        object.__setattr__(self,'amount',_decimal(self.amount,'reported_cash.amount'))

    def monetary_identity(self):
        return self.income_type+':'+self.transaction_id

    def monetary_sha256(self):
        body={'timestamp':self.timestamp,'account_ref_sha256':self.account_ref_sha256,'asset':self.asset,
            'income_type':self.income_type,'transaction_id':self.transaction_id,'amount':canonical_decimal(self.amount),
            'instrument_id':self.instrument_id}
        return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class MarkEvent:
    timestamp: int
    instrument_id: str
    mark_price: Decimal
    observed_trade_price: Decimal | None = None
    liquidation_backstop: LiquidationBackstop | None = None

    @_reviewed_decimal
    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        if not isinstance(self.instrument_id, str) or not self.instrument_id:
            raise ValueError("instrument_id must be non-empty")
        object.__setattr__(
            self, "mark_price", _positive(self.mark_price, "mark_price")
        )
        observed = (
            None
            if self.observed_trade_price is None
            else _positive(self.observed_trade_price, "observed_trade_price")
        )
        object.__setattr__(self, "observed_trade_price", observed)


LedgerEvent = TradeEvent | FundingEvent | MarkEvent | ReportedCashFlowEvent


@dataclass
class _Position:
    instrument_id: str
    quantity: Decimal
    average_entry_price: Decimal
    mark_price: Decimal
    margin_mode: MarginMode
    isolated_wallet: Decimal = ZERO
    entry_notional: Decimal = ZERO


@dataclass(frozen=True)
class PositionView:
    instrument_id: str
    quantity: Decimal
    average_entry_price: Decimal
    mark_price: Decimal
    margin_mode: MarginMode
    isolated_wallet: Decimal
    entry_notional: Decimal

    def as_canonical_dict(self) -> dict[str, str]:
        return {
            "instrument_id": self.instrument_id,
            "quantity": canonical_decimal(self.quantity),
            "average_entry_price": canonical_decimal(self.average_entry_price),
            "entry_notional": canonical_decimal(self.entry_notional),
            "mark_price": canonical_decimal(self.mark_price),
            "margin_mode": self.margin_mode.value,
            "isolated_wallet": canonical_decimal(self.isolated_wallet),
        }


@dataclass(frozen=True)
class OpeningPosition:
    """Exact inherited basis supplied by a separately identified observation."""
    instrument_id: str
    quantity: Decimal
    entry_notional: Decimal
    mark_price: Decimal
    margin_mode: MarginMode = MarginMode.CROSS
    isolated_wallet: Decimal = ZERO

    @_reviewed_decimal
    def __post_init__(self):
        if not isinstance(self.instrument_id, str) or not self.instrument_id.strip():
            raise ValueError('opening instrument required')
        quantity = _decimal(self.quantity, 'opening quantity')
        if quantity == ZERO: raise ValueError('opening position must be nonzero')
        basis = _positive(self.entry_notional, 'opening entry_notional')
        mark = _positive(self.mark_price, 'opening mark_price')
        isolated = _non_negative(self.isolated_wallet, 'opening isolated_wallet')
        if not isinstance(self.margin_mode, MarginMode): raise ValueError('opening margin mode required')
        if self.margin_mode == MarginMode.CROSS and isolated != ZERO:
            raise ValueError('cross position cannot allocate isolated wallet')
        for name, value in (('quantity', quantity), ('entry_notional', basis),
                            ('mark_price', mark), ('isolated_wallet', isolated)):
            object.__setattr__(self, name, value)

    def as_canonical_dict(self):
        return {'instrument_id': self.instrument_id, 'quantity': canonical_decimal(self.quantity),
                'entry_notional': canonical_decimal(self.entry_notional), 'mark_price': canonical_decimal(self.mark_price),
                'margin_mode': self.margin_mode.value, 'isolated_wallet': canonical_decimal(self.isolated_wallet)}


@dataclass(frozen=True)
class OpeningLedgerState:
    """A technical opening balance, not source attestation or execution authority.

    The observation is inclusive through timestamp; only later events may enter.
    initial_collateral remains TOTAL wallet cash, including isolated allocations.
    """
    timestamp: int
    source_sha256: str
    positions: tuple[OpeningPosition, ...]

    def __post_init__(self):
        _timestamp(self.timestamp)
        if (not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.source_sha256)):
            raise ValueError('opening source digest required')
        if (not isinstance(self.positions, tuple) or len(self.positions) > 2000
                or any(not isinstance(p, OpeningPosition) for p in self.positions)
                or len({p.instrument_id for p in self.positions}) != len(self.positions)):
            raise ValueError('unique immutable opening positions required')

    def as_canonical_dict(self):
        return {'policy': OPENING_STATE_POLICY, 'timestamp': self.timestamp, 'source_sha256': self.source_sha256,
                'positions': [p.as_canonical_dict() for p in sorted(self.positions, key=lambda p: p.instrument_id)]}


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    reason: str | None = None

    def as_canonical_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "requested_quantity": canonical_decimal(self.requested_quantity),
            "filled_quantity": canonical_decimal(self.filled_quantity),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LiquidationRecord:
    timestamp: int
    instrument_id: str
    margin_mode: MarginMode
    mark_price: Decimal
    quantity: Decimal
    realized_price_pnl: Decimal
    liquidation_fee: Decimal
    insurance_adjustment: Decimal
    adl_adjustment: Decimal
    bankruptcy_deficit_allocation: Decimal
    backstop_allocation_basis: str

    def as_canonical_dict(self) -> dict[str, str | int]:
        return {
            "timestamp": self.timestamp,
            "instrument_id": self.instrument_id,
            "margin_mode": self.margin_mode.value,
            "mark_price": canonical_decimal(self.mark_price),
            "quantity": canonical_decimal(self.quantity),
            "realized_price_pnl": canonical_decimal(self.realized_price_pnl),
            "liquidation_fee": canonical_decimal(self.liquidation_fee),
            "insurance_adjustment": canonical_decimal(
                self.insurance_adjustment
            ),
            "adl_adjustment": canonical_decimal(self.adl_adjustment),
            "bankruptcy_deficit_allocation": canonical_decimal(
                self.bankruptcy_deficit_allocation
            ),
            "backstop_allocation_basis": self.backstop_allocation_basis,
        }


@dataclass(frozen=True)
class ReconciliationSnapshot:
    initial_equity: Decimal
    ending_equity: Decimal
    price_pnl: Decimal
    fee: Decimal
    spread: Decimal
    impact: Decimal
    funding: Decimal
    liquidation_fee: Decimal
    insurance_adjustment: Decimal
    adl_adjustment: Decimal

    @property
    @_reviewed_decimal
    def attributed_change(self) -> Decimal:
        return (
            self.price_pnl
            - self.fee
            - self.spread
            - self.impact
            + self.funding
            - self.liquidation_fee
            + self.insurance_adjustment
            + self.adl_adjustment
        )

    @property
    @_reviewed_decimal
    def residual(self) -> Decimal:
        return abs(
            (self.ending_equity - self.initial_equity) - self.attributed_change
        )

    def as_canonical_dict(self) -> dict[str, str]:
        return {
            "initial_equity": canonical_decimal(self.initial_equity),
            "ending_equity": canonical_decimal(self.ending_equity),
            "price_pnl": canonical_decimal(self.price_pnl),
            "fee": canonical_decimal(self.fee),
            "spread": canonical_decimal(self.spread),
            "impact": canonical_decimal(self.impact),
            "funding": canonical_decimal(self.funding),
            "liquidation_fee": canonical_decimal(self.liquidation_fee),
            "insurance_adjustment": canonical_decimal(
                self.insurance_adjustment
            ),
            "adl_adjustment": canonical_decimal(self.adl_adjustment),
            "attributed_change": canonical_decimal(self.attributed_change),
            "residual": canonical_decimal(self.residual),
        }


@dataclass(frozen=True)
class ReportedCashReconciliationSnapshot(ReconciliationSnapshot):
    external_cash_flow: Decimal

    @property
    @_reviewed_decimal
    def attributed_change(self):
        return super().attributed_change+self.external_cash_flow

    def as_canonical_dict(self):
        return {**super().as_canonical_dict(),'reported_cash_policy':REPORTED_CASH_POLICY,
            'external_cash_flow':canonical_decimal(self.external_cash_flow),
            'trading_and_funding_change':canonical_decimal(super().attributed_change)}


class LinearPerpetualLedger:
    """Single-account deterministic event engine for reviewed v2 contracts."""

    _PRIORITY = {FundingEvent: 10, MarkEvent: 20, TradeEvent: 30, OrderFillEvent: 30,
                 ReportedFillReplayEvent: 30, ReportedCashFlowEvent: 40}

    def __init__(
        self, specs: Sequence[ContractSpec], *, initial_collateral: Decimal,
        opening_state: OpeningLedgerState | None = None,
        reported_cash_policy: ReportedCashReplayPolicy | None = None,
    ) -> None:
        items = tuple(specs)
        if not items:
            raise ValueError("at least one ContractSpec is required")
        by_id = {item.instrument_id: item for item in items}
        if len(by_id) != len(items):
            raise ValueError("duplicate instrument_id")
        if len({item.margin_asset for item in items}) != 1:
            raise UnsupportedContract("one ledger requires a single margin asset")
        settlement_quantums = {item.settlement_quantum for item in items}
        if len(settlement_quantums) != 1:
            raise MarginConfigurationError(
                "one cross-margin ledger requires one shared settlement quantum"
            )
        self._specs = by_id
        self._cash_spec = items[0]
        if reported_cash_policy is not None and (
            type(reported_cash_policy) is not ReportedCashReplayPolicy or reported_cash_policy.asset!=items[0].margin_asset):
            raise ReportedCashReplayError('reported cash policy quote mismatch')
        self._reported_cash_policy=reported_cash_policy
        self._reported_cash_seen: dict[str,str]={}
        self._reported_fill_times: dict[str,int]={}
        self._external_cash_flow=ZERO
        self._initial_collateral = self._cash_spec.validate_cash(
            _positive(initial_collateral, "initial_collateral"),
            "initial_collateral",
        )
        self._wallet_balance = self._initial_collateral
        self._positions: dict[str, _Position] = {}
        # Venue marks are market state, not position state.  Retain the latest
        # explicit mark even while flat so a later open cannot replace a known
        # mark with its execution price and understate margin or unrealized P&L.
        self._mark_prices: dict[str, Decimal] = {}
        # A fill is not a venue mark, but it is still load-bearing evidence of
        # the price at which new exposure was acquired.  Until a later explicit
        # venue mark/funding observation arrives, retain every exposure-opening
        # fill price and use the most conservative combination for initial-margin
        # clearance.  This prevents a stale flat-account mark from manufacturing
        # favorable unrealized PnL or understating the filled notional.
        self._pending_initial_margin_prices: dict[str, tuple[Decimal, ...]] = {}
        self._realized_price_pnl = ZERO
        self._fee = ZERO
        self._spread = ZERO
        self._impact = ZERO
        self._funding = ZERO
        self._liquidation_fee = ZERO
        self._insurance_adjustment = ZERO
        self._adl_adjustment = ZERO
        self._events: list[dict[str, object]] = []
        self._liquidations: list[LiquidationRecord] = []
        self._last_event_key: tuple[int, int] | None = None
        self._closed_market_phases: set[tuple[int, int]] = set()
        self._capacity_consumed: dict[tuple[str, str, str, int], Decimal] = {}
        self._capacity_contracts: dict[
            tuple[str, str, str, int], dict[str, str | int]
        ] = {}
        self._capacity_windows: dict[
            tuple[str, str], set[tuple[int, int]]
        ] = {}
        self._opening_state: OpeningLedgerState | None = None
        self._opening_unrealized = ZERO
        if opening_state is not None:
            self._initialize_opening_state(opening_state)
            if reported_cash_policy is not None and any(p.margin_mode!=MarginMode.CROSS for p in self._positions.values()):
                raise ReportedCashReplayError('reported cash replay currently requires cross margin')

    @_reviewed_decimal
    def transition_contract_specs(self, specs: Sequence[ContractSpec], *, timestamp: int) -> None:
        """Apply a reviewed epoch without creating cash, positions or fills.

        The caller binds PIT provenance and venue order rules. Currency, cash
        rounding and contract units cannot change inside this account. Existing
        inventory must remain exactly representable; conversions are never
        inferred. The next market event applies the new maintenance margin.
        """
        timestamp = _timestamp(timestamp)
        items = tuple(specs)
        if (self._reported_cash_policy is not None or not items
                or any(type(item) is not ContractSpec for item in items)):
            raise ValueError('INVALID_CONTRACT_SPEC_TRANSITION')
        updated = {item.instrument_id: item for item in items}
        if len(updated) != len(items) or set(updated) != set(self._specs):
            raise ValueError('CONTRACT_TRANSITION_UNIVERSE_CHANGED')
        if self._last_event_key is None or timestamp <= self._last_event_key[0]:
            raise ValueError('CONTRACT_TRANSITION_TIME_NOT_INCREASING')
        immutable = ('contract_type', 'margin_asset', 'contract_multiplier', 'settlement_quantum')
        for name, spec in updated.items():
            old = self._specs[name]
            if any(getattr(old, key) != getattr(spec, key) for key in immutable):
                raise ValueError('CONTRACT_TRANSITION_CASH_OR_UNITS_CHANGED')
            position = self._positions.get(name)
            if position is not None and abs(position.quantity) % spec.quantity_step != ZERO:
                raise ValueError('CONTRACT_TRANSITION_INVENTORY_NOT_REPRESENTABLE')
        record = dict(event_type='CONTRACT_RULE_TRANSITION', timestamp=timestamp,
            policy='EXACT_REPRESENTABLE_INVENTORY_UNCHANGED_CASH_V1',
            previous=[self._specs[name].as_canonical_dict() for name in sorted(updated)],
            current=[updated[name].as_canonical_dict() for name in sorted(updated)])
        self._specs = updated
        self._events.append(record)
        self._last_event_key = timestamp, 0

    @_reviewed_decimal
    def _initialize_opening_state(self, opening: OpeningLedgerState):
        if (not isinstance(opening, OpeningLedgerState) or self._opening_state is not None
                or self._events or self._positions or self._last_event_key is not None):
            raise ValueError('opening state requires a pristine ledger')
        positions = {}
        for row in opening.positions:
            spec = self._spec(row.instrument_id)
            # An inherited position may be below the current minimum new order;
            # exact quantity grid and cost-basis cash quantum still apply.
            spec.validate_quantity(row.quantity, row.mark_price, reducing=True)
            basis = self._cash(row.entry_notional, 'opening entry_notional')
            isolated = self._cash(row.isolated_wallet, 'opening isolated_wallet')
            positions[row.instrument_id] = _Position(row.instrument_id, row.quantity,
                basis / (abs(row.quantity) * spec.contract_multiplier), row.mark_price,
                row.margin_mode, isolated, basis)
        allocation = self._cash_add(*(p.isolated_wallet for p in positions.values()), field='opening isolated allocation')
        if allocation > self._initial_collateral:
            raise MarginConfigurationError('opening isolated allocation exceeds total wallet')
        unrealized = self._cash_add(*(self._unrealized(p) for p in positions.values()), field='opening unrealized')
        self._cash_add(self._initial_collateral, unrealized, field='opening equity')
        self._positions = positions
        self._mark_prices = {p.instrument_id: p.mark_price for p in opening.positions}
        self._wallet_balance = self._cash_add(self._initial_collateral, -allocation, field='opening cross wallet')
        self._opening_unrealized = unrealized
        self._opening_state = opening
        self._assert_cash_state()

    def _require_after_opening(self, timestamp: int):
        if self._opening_state is not None and timestamp <= self._opening_state.timestamp:
            raise EventOrderViolation('event is not after the inclusive opening cutoff')

    @property
    def liquidations(self) -> tuple[LiquidationRecord, ...]:
        return tuple(self._liquidations)

    @property
    def event_records(self) -> tuple[Mapping[str, object], ...]:
        return tuple(copy.deepcopy(record) for record in self._events)

    def _spec(self, instrument_id: str) -> ContractSpec:
        try:
            return self._specs[instrument_id]
        except KeyError as exc:
            raise UnsupportedContract(f"unknown instrument: {instrument_id}") from exc

    def _cash(self, value: Decimal, field: str) -> Decimal:
        return self._cash_spec.validate_cash(value, field)

    def _cash_add(self, *values: Decimal, field: str) -> Decimal:
        units = sum(
            (self._cash_spec.cash_units(value, field) for value in values),
            0,
        )
        return self._cash_spec.cash_from_units(units, field)

    def _cash_product(self, *values: Decimal, field: str) -> Decimal:
        return self._cash_spec.cash_product(*values, field=field)

    def _pro_rata_cash(
        self,
        total: Decimal,
        weight: Decimal,
        denominator: Decimal,
        *,
        field: str,
    ) -> Decimal:
        total_units = self._cash_spec.cash_units(total, field)
        weight_units = self._cash_spec.cash_units(weight, field)
        denominator_units = self._cash_spec.cash_units(denominator, field)
        if weight_units < 0 or denominator_units <= 0:
            raise CashQuantumViolation(f"{field} has invalid pro-rata weights")
        sign = -1 if total_units < 0 else 1
        allocated_units = (
            sign * (abs(total_units) * weight_units // denominator_units)
        )
        return self._cash_spec.cash_from_units(allocated_units, field)

    def _assert_cash_state(self) -> None:
        for name in (
            "_initial_collateral",
            "_wallet_balance",
            "_realized_price_pnl",
            "_fee",
            "_spread",
            "_impact",
            "_funding",
            "_external_cash_flow",
            "_liquidation_fee",
            "_insurance_adjustment",
            "_adl_adjustment",
        ):
            self._cash(getattr(self, name), name)
        for instrument_id, position in self._positions.items():
            self._cash(
                position.isolated_wallet,
                f"position[{instrument_id}].isolated_wallet",
            )
            self._unrealized(position)

    def position(self, instrument_id: str) -> PositionView | None:
        position = self._positions.get(instrument_id)
        if position is None:
            return None
        return PositionView(
            instrument_id=position.instrument_id,
            quantity=position.quantity,
            average_entry_price=position.average_entry_price,
            mark_price=position.mark_price,
            margin_mode=position.margin_mode,
            isolated_wallet=position.isolated_wallet,
            entry_notional=position.entry_notional,
        )

    def _unrealized(self, position: _Position) -> Decimal:
        return self._unrealized_at(position, position.mark_price)

    def _unrealized_at(self, position: _Position, price: Decimal) -> Decimal:
        spec = self._spec(position.instrument_id)
        direction = ONE if position.quantity > ZERO else -ONE
        return direction * self._cash_add(
            spec.notional(position.quantity, price), -position.entry_notional,
            field=f"unrealized[{position.instrument_id}]")

    @_reviewed_decimal
    def account_equity(self) -> Decimal:
        cross_unrealized = self._cash_add(
            *(
                self._unrealized(position)
                for position in self._positions.values()
                if position.margin_mode == MarginMode.CROSS
            ),
            field="cross_unrealized",
        )
        isolated_equity = self._cash_add(
            *(
                self._cash_add(
                    position.isolated_wallet,
                    self._unrealized(position),
                    field=f"isolated_equity[{position.instrument_id}]",
                )
                for position in self._positions.values()
                if position.margin_mode == MarginMode.ISOLATED
            ),
            field="isolated_equity",
        )
        return self._cash_add(
            self._wallet_balance,
            cross_unrealized,
            isolated_equity,
            field="account_equity",
        )

    def _position_notional(self, position: _Position) -> Decimal:
        return self._spec(position.instrument_id).notional(
            position.quantity, position.mark_price
        )

    def _initial_margin_price_candidates(
        self, position: _Position
    ) -> tuple[Decimal, ...]:
        return (
            position.mark_price,
            *self._pending_initial_margin_prices.get(position.instrument_id, ()),
        )

    def _conservative_initial_margin_unrealized(
        self, position: _Position
    ) -> Decimal:
        return min(
            self._unrealized_at(position, price)
            for price in self._initial_margin_price_candidates(position)
        )

    def _conservative_initial_margin_notional(
        self, position: _Position
    ) -> Decimal:
        spec = self._spec(position.instrument_id)
        return max(
            spec.notional(position.quantity, price)
            for price in self._initial_margin_price_candidates(position)
        )

    def _cross_equity(self) -> Decimal:
        return self._cash_add(
            self._wallet_balance,
            *(
                self._unrealized(position)
                for position in self._positions.values()
                if position.margin_mode == MarginMode.CROSS
            ),
            field="cross_equity",
        )

    def _cross_margin(self, *, maintenance: bool) -> Decimal:
        requirements = (
            self._spec(position.instrument_id).margin_requirement(
                self._position_notional(position)
            )
            for position in self._positions.values()
            if position.margin_mode == MarginMode.CROSS
        )
        return sum(
            (
                item.maintenance_margin if maintenance else item.initial_margin
                for item in requirements
            ),
            ZERO,
        )

    @_reviewed_decimal
    def initial_margin_snapshot(self) -> dict[str, object]:
        """Current conservative margin values, shared by admission and reports."""
        cross_equity = self._cash_add(
            self._wallet_balance,
            *(
                self._conservative_initial_margin_unrealized(position)
                for position in self._positions.values()
                if position.margin_mode == MarginMode.CROSS
            ),
            field="conservative_cross_equity",
        )
        cross_initial_margin = sum(
            (
                self._spec(position.instrument_id)
                .margin_requirement(
                    self._conservative_initial_margin_notional(position)
                )
                .initial_margin
                for position in self._positions.values()
                if position.margin_mode == MarginMode.CROSS
            ),
            ZERO,
        )
        isolated = {}
        for position in self._positions.values():
            if position.margin_mode != MarginMode.ISOLATED:
                continue
            requirement = self._spec(position.instrument_id).margin_requirement(
                self._conservative_initial_margin_notional(position)
            )
            isolated_equity = self._cash_add(
                position.isolated_wallet,
                self._conservative_initial_margin_unrealized(position),
                field=f"conservative_isolated_equity[{position.instrument_id}]",
            )
            isolated[position.instrument_id] = {
                "equity": isolated_equity, "required": requirement.initial_margin,
                "available": isolated_equity-requirement.initial_margin,
            }
        return {"cross": {"equity": cross_equity, "required": cross_initial_margin,
                           "available": cross_equity-cross_initial_margin},
                "isolated": isolated}

    def _assert_initial_margin(self) -> None:
        snapshot = self.initial_margin_snapshot()
        if snapshot["cross"]["available"] < ZERO:
            raise InsufficientMargin("cross initial margin is insufficient")
        for instrument_id, values in snapshot["isolated"].items():
            if values["available"] < ZERO:
                raise InsufficientMargin(
                    f"isolated initial margin is insufficient for {instrument_id}"
                )

    def _liquidation_targets(self) -> tuple[str, ...]:
        targets: list[str] = []
        for instrument_id, position in sorted(self._positions.items()):
            if position.margin_mode != MarginMode.ISOLATED:
                continue
            requirement = self._spec(instrument_id).margin_requirement(
                self._position_notional(position)
            )
            isolated_equity = self._cash_add(
                position.isolated_wallet,
                self._unrealized(position),
                field=f"isolated_equity[{instrument_id}]",
            )
            if isolated_equity <= requirement.maintenance_margin:
                targets.append(instrument_id)
        cross_names = sorted(
            instrument_id
            for instrument_id, position in self._positions.items()
            if position.margin_mode == MarginMode.CROSS
        )
        if cross_names and self._cross_equity() <= self._cross_margin(maintenance=True):
            targets.extend(cross_names)
        return tuple(targets)

    def _apply_average_cost(
        self,
        position: _Position | None,
        quantity_delta: Decimal,
        execution_price: Decimal,
        multiplier: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """Exact cost basis owns PnL; average price is a display quotient only.

        A partial close allocates entry cost in integer settlement units, with
        the remainder retained in the open position. This declared L2 policy
        preserves cash and total round-trip PnL without claiming venue rounding
        equivalence. See COST_BASIS_POLICY in every resulting account state.
        """
        traded_notional = self._cash_product(
            abs(quantity_delta), execution_price, multiplier, field="trade.entry_notional")
        if position is None or position.quantity == ZERO:
            return quantity_delta, execution_price, ZERO, traded_notional
        old_quantity = position.quantity
        if (old_quantity > ZERO) == (quantity_delta > ZERO):
            new_quantity = _exact_sum(
                old_quantity, quantity_delta, field="position_quantity"
            )
            basis = self._cash_add(position.entry_notional, traded_notional, field="entry_notional")
            average = basis / (abs(new_quantity) * multiplier)
            return new_quantity, average, ZERO, basis
        closing = min(abs(old_quantity), abs(quantity_delta))
        direction = ONE if old_quantity > ZERO else -ONE
        # Quantities have their own grid: 0.001 contracts is valid even when
        # cash settles in cents. Allocate integer CASH units by an exact
        # rational QUANTITY ratio, never interpreting quantity as cash.
        closing_num, closing_den = closing.as_integer_ratio()
        quantity_num, quantity_den = abs(old_quantity).as_integer_ratio()
        basis_units = self._cash_spec.cash_units(position.entry_notional, "entry_notional")
        closed_units = basis_units * closing_num * quantity_den // (closing_den * quantity_num)
        closed_basis = self._cash_spec.cash_from_units(closed_units, "closed_entry_notional")
        closing_notional = self._cash_product(closing, execution_price, multiplier,
                                             field="closing_notional")
        realized = direction * self._cash_add(closing_notional, -closed_basis, field="realized_price_pnl")
        new_quantity = _exact_sum(
            old_quantity, quantity_delta, field="position_quantity"
        )
        if new_quantity == ZERO:
            average = ZERO
            basis = ZERO
        elif (new_quantity > ZERO) == (old_quantity > ZERO):
            basis = self._cash_add(position.entry_notional, -closed_basis, field="entry_notional")
            average = basis / (abs(new_quantity) * multiplier)
        else:
            average = execution_price
            basis = self._cash_product(abs(new_quantity), execution_price, multiplier, field="entry_notional")
        return new_quantity, average, realized, basis

    def _event_key(self, event: LedgerEvent) -> tuple[int, int]:
        return event.timestamp, self._PRIORITY[type(event)]

    @contextmanager
    def _transaction(self, trade: TradeEvent | None = None) -> Iterator[None]:
        """Rollback current state and appended records without copying history.

        A trade touches one capacity budget. Historical budget entries are
        immutable and market phases never change them, so only that entry needs
        an undo record. Public history access still returns defensive copies.
        """
        historical = {
            "_events", "_liquidations", "_capacity_consumed", "_capacity_contracts"
        }
        before = copy.deepcopy({
            name: value for name, value in self.__dict__.items()
            if name not in historical
        })
        event_count, liquidation_count = len(self._events), len(self._liquidations)
        capacity_undo = []
        if trade is not None:
            key = (trade.capacity.venue_id, trade.instrument_id,
                   trade.capacity.period_rule_id, trade.capacity.period_index)
            for mapping in (self._capacity_consumed, self._capacity_contracts):
                capacity_undo.append((mapping, key, key in mapping, mapping.get(key)))
        try:
            yield
        except BaseException:
            self.__dict__.update(before)
            del self._events[event_count:]
            del self._liquidations[liquidation_count:]
            for mapping, key, existed, value in capacity_undo:
                if existed:
                    mapping[key] = value
                else:
                    mapping.pop(key, None)
            raise

    @_reviewed_decimal
    def apply_event(
        self, event: LedgerEvent, *,
        trade_guard: Callable[[ExecutionResult], None] | None = None,
    ) -> ExecutionResult | None:
        """Apply an event, optionally checking a simulated trade before commit.

        The guard observes the actual capacity-limited ledger transition. Raising
        rolls back this trade, including costs, capacity and appended history.
        Market/funding observations cannot be vetoed this way. This hook is for
        proposed simulation fills, never for erasing an observed venue fill.
        """
        if type(event) not in self._PRIORITY:
            raise TypeError("unsupported ledger event")
        self._check_reported_event(event)
        if isinstance(event,ReportedCashFlowEvent) and self._reported_cash_duplicate(event):
            return None
        self._require_after_opening(event.timestamp)
        if trade_guard is not None and (
            not isinstance(event, TradeEvent) or isinstance(event, ReportedFillReplayEvent)
            or not callable(trade_guard)
        ):
            raise TypeError("trade_guard requires a trade and a callable")
        with self._transaction(event if isinstance(event, TradeEvent) else None):
            key = self._event_key(event)
            if self._last_event_key is not None and key < self._last_event_key:
                raise EventOrderViolation("event clock or priority moved backwards")
            if (
                isinstance(event, (FundingEvent, MarkEvent, ReportedCashFlowEvent))
                and key in self._closed_market_phases
            ):
                raise EventOrderViolation(
                    "market/settlement phase is already closed for this timestamp"
                )
            self._last_event_key = key
            if isinstance(event,ReportedCashFlowEvent):
                self._apply_reported_cash(event)
                self._check_reported_cash_margin()
                self._closed_market_phases={key}
                return None
            if isinstance(event, TradeEvent):
                result = self._apply_trade(event)
                if self._reported_cash_policy is not None and result.filled_quantity!=ZERO:
                    self._reported_fill_times[event.instrument_id]=event.timestamp
                if trade_guard is not None and result.filled_quantity != ZERO:
                    trade_guard(result)
                return result
            if isinstance(event, FundingEvent):
                self._apply_funding(event)
                self._closed_market_phases = {key}
                return None
            self._apply_mark(event)
            self._closed_market_phases = {key}
            return None

    @_reviewed_decimal
    def process_events(
        self, events: Sequence[LedgerEvent], *,
        phase_observer: Callable[[int, str], None] | None = None,
    ) -> tuple[ExecutionResult | None, ...]:
        """Process one event batch with atomic market/settlement phases.

        All funding observations at one venue timestamp are applied before a
        single account-wide liquidation check; the same is true for mark
        observations.  This removes cross-instrument input-order dependence.
        Trade events retain caller order inside their execution phase.

        Authority callers must submit every same-timestamp observation in one
        call.  ``apply_event`` remains a convenience for genuinely isolated
        events and intentionally cannot infer observations that were omitted.

        The optional observer runs after each complete committed funding/mark
        phase, including its account-wide liquidation check. It records the
        account without splitting instruments or vetoing committed cash flows;
        an observer exception propagates after that phase has committed.
        """
        if phase_observer is not None and not callable(phase_observer):
            raise TypeError("phase_observer must be callable")
        events=tuple(events)
        for event in events:self._check_reported_event(event)
        ordered = sorted(
            enumerate(events),
            key=lambda item: (
                item[1].timestamp,
                self._PRIORITY[type(item[1])],
                (
                    item[1].instrument_id
                    if isinstance(item[1], (FundingEvent, MarkEvent))
                    else ""
                ),
                item[0],
            ),
        )
        results: list[ExecutionResult | None] = []
        for key, group_iter in groupby(
            ordered,
            key=lambda item: (
                item[1].timestamp,
                self._PRIORITY[type(item[1])],
            ),
        ):
            group = tuple(item[1] for item in group_iter)
            if isinstance(group[0],ReportedCashFlowEvent):
                with self._transaction():
                    unseen=[event for event in group if not self._reported_cash_duplicate(event)]
                    if unseen:
                        self._require_after_opening(key[0])
                        if self._last_event_key is not None and key<self._last_event_key or key in self._closed_market_phases:
                            raise EventOrderViolation('reported cash phase moved backwards or is closed')
                        self._last_event_key=key
                        for event in unseen:
                            if not self._reported_cash_duplicate(event):self._apply_reported_cash(event)
                        self._check_reported_cash_margin()
                        self._closed_market_phases={key}
                    results.extend(None for _event in group)
                continue
            self._require_after_opening(key[0])
            if isinstance(group[0], TradeEvent):
                results.extend(self.apply_event(event) for event in group)
                continue

            with self._transaction():
                if self._last_event_key is not None and key < self._last_event_key:
                    raise EventOrderViolation("event clock or priority moved backwards")
                if key in self._closed_market_phases:
                    raise EventOrderViolation(
                        "market/settlement phase is already closed for this timestamp"
                    )
                instruments = [event.instrument_id for event in group]
                if len(instruments) != len(set(instruments)):
                    raise EventOrderViolation(
                        "duplicate market/settlement event for instrument and timestamp"
                    )
                backstops = {
                    event.liquidation_backstop
                    for event in group
                    if event.liquidation_backstop is not None
                }
                if len(backstops) > 1:
                    raise MarginConfigurationError(
                        "same-timestamp events contain conflicting liquidation backstops"
                    )
                backstop = next(iter(backstops), None)
                self._last_event_key = key
                if isinstance(group[0], FundingEvent):
                    for event in group:
                        self._apply_funding(event, defer_liquidation=True)
                else:
                    for event in group:
                        self._apply_mark(event, defer_liquidation=True)
                self._liquidate_if_required(
                    timestamp=key[0],
                    backstop=backstop,
                )
                self._closed_market_phases = {key}
                results.extend(None for _event in group)
            if phase_observer is not None:
                phase_observer(key[0], "FUNDING" if isinstance(group[0], FundingEvent) else "MARK")
        return tuple(results)

    def _check_reported_event(self,event):
        if isinstance(event,ReportedCashFlowEvent):
            if (self._reported_cash_policy is None or event.account_ref_sha256!=self._reported_cash_policy.account_ref_sha256 or
                event.asset!=self._reported_cash_policy.asset):
                raise ReportedCashReplayError('reported cash requires the matching explicit policy')
        if self._reported_cash_policy is not None:
            if isinstance(event,FundingEvent) or isinstance(event,TradeEvent) and type(event) is not ReportedFillReplayEvent:
                raise ReportedCashReplayError('simulated flows cannot enter observed cash replay')
            if isinstance(event,ReportedFillReplayEvent) and event.margin_mode!=MarginMode.CROSS:
                raise ReportedCashReplayError('reported cash replay currently requires cross margin')
            if isinstance(event,MarkEvent) and event.liquidation_backstop is not None:
                raise ReportedCashReplayError('simulated liquidation cash cannot enter observed cash replay')

    def _reported_cash_duplicate(self,event):
        old=self._reported_cash_seen.get(event.monetary_identity())
        if old is not None and old!=event.monetary_sha256():
            raise ReportedCashReplayError('conflicting monetary identity')
        return old is not None

    def _apply_reported_cash(self,event):
        if len(self._reported_cash_seen)>=10000:raise ReportedCashReplayError('reported cash replay budget')
        amount=self._cash(event.amount,'reported_cash.amount')
        if event.income_type=='FUNDING_FEE':
            self._spec(event.instrument_id)
            if self._reported_fill_times.get(event.instrument_id)==event.timestamp:
                raise ReportedCashReplayError('same-millisecond fill and funding order is unresolved')
            position=self._positions.get(event.instrument_id)
            if position is None or position.quantity==ZERO or position.margin_mode!=MarginMode.CROSS:
                raise ReportedCashReplayError('funding lacks a bound cross position; retain raw input')
            self._funding=self._cash_add(self._funding,amount,field='funding')
        else:
            self._external_cash_flow=self._cash_add(self._external_cash_flow,amount,field='external_cash_flow')
        self._wallet_balance=self._cash_add(self._wallet_balance,amount,field='wallet_balance')
        self._reported_cash_seen[event.monetary_identity()]=event.monetary_sha256()
        self._assert_cash_state()
        self._events.append({'event_type':'REPORTED_CASH_FLOW','timestamp':event.timestamp,'policy':REPORTED_CASH_POLICY,
            'income_type':event.income_type,'transaction_id':event.transaction_id,'asset':event.asset,
            'account_ref_sha256':event.account_ref_sha256,'instrument_id':event.instrument_id,
            'amount':canonical_decimal(amount),'source_sha256':event.source_sha256,'monetary_sha256':event.monetary_sha256()})

    def _check_reported_cash_margin(self):
        if self._wallet_balance<ZERO or self._liquidation_targets():
            raise ReportedCashReplayError('reported cash needs unsupported balance or liquidation evidence')

    def _capacity_quantity(
        self, event: TradeEvent, spec: ContractSpec
    ) -> tuple[Decimal, str, str | None, Decimal]:
        budget_key = (
            event.capacity.venue_id,
            event.instrument_id,
            event.capacity.period_rule_id,
            event.capacity.period_index,
        )
        capacity_contract = event.capacity.as_canonical_dict()
        capacity_contract.pop("window_id")
        window_scope = (event.capacity.venue_id, event.instrument_id)
        window_bounds = (
            event.capacity.consumption_window_start,
            event.capacity.consumption_window_end,
        )
        for existing_start, existing_end in self._capacity_windows.get(
            window_scope, set()
        ):
            if (existing_start, existing_end) == window_bounds:
                continue
            if max(existing_start, window_bounds[0]) < min(
                existing_end, window_bounds[1]
            ):
                raise MarginConfigurationError(
                    "overlapping capacity consumption windows are not allowed"
                )
        # Trade timestamps are monotone and must lie inside their window. Only
        # the latest interval can overlap a later trade's interval. Historical
        # budgets remain in the audit maps; this lookup index is bounded.
        self._capacity_windows[window_scope] = {window_bounds}
        prior_contract = self._capacity_contracts.get(budget_key)
        if prior_contract is not None and prior_contract != capacity_contract:
            raise MarginConfigurationError(
                "capacity contract changed inside an existing venue window"
            )
        self._capacity_contracts[budget_key] = capacity_contract
        consumed = self._capacity_consumed.get(budget_key, ZERO)
        maximum_notional = self._cash(
            event.capacity.maximum_notional,
            "capacity.maximum_notional",
        )
        remaining = self._cash_add(
            maximum_notional,
            -consumed,
            field="capacity.remaining_notional",
        )
        if remaining < ZERO:
            raise MarginConfigurationError("capacity accounting exceeded its hard budget")
        requested = event.quantity_delta
        requested_notional = self._cash(
            spec.notional(requested, event.execution_price),
            "capacity.requested_notional",
        )
        if requested_notional <= remaining:
            return requested, "FILLED", None, consumed
        if event.capacity.action == CapacityAction.REJECT:
            return ZERO, "REJECTED", "CAPACITY_EXCEEDED", consumed
        maximum_quantity = remaining / (
            event.execution_price * spec.contract_multiplier
        )
        floored = spec.floor_quantity(maximum_quantity)
        filled_abs = min(abs(requested), floored)
        if filled_abs == ZERO:
            return ZERO, "REJECTED", "CAPACITY_BELOW_MINIMUM", consumed
        signed = filled_abs if requested > ZERO else -filled_abs
        try:
            self._validate_execution_quantity(event, spec, signed)
        except QuantityRuleViolation:
            return ZERO, "REJECTED", "CAPACITY_BELOW_MINIMUM", consumed
        return signed, "PARTIALLY_FILLED", "CAPACITY_PARTIAL_FILL", consumed

    def _validate_execution_quantity(
        self, event: TradeEvent, spec: ContractSpec, quantity: Decimal,
    ) -> None:
        if isinstance(event, (OrderFillEvent, ReportedFillReplayEvent)):
            spec.validate_quantity(quantity, event.execution_price, reducing=True)
        else:
            current = self._positions.get(event.instrument_id)
            spec.validate_trade_quantity(quantity, event.execution_price,
                current_quantity=ZERO if current is None else current.quantity)

    def _apply_trade(self, event: TradeEvent) -> ExecutionResult:
        spec = self._spec(event.instrument_id)
        requested_fee = self._cash(event.fee, "trade.fee")
        requested_spread = self._cash(event.spread_cost, "trade.spread_cost")
        requested_impact = self._cash(event.impact_cost, "trade.impact_cost")
        requested_isolated_margin = (
            None
            if event.isolated_margin is None
            else self._cash(event.isolated_margin, "trade.isolated_margin")
        )
        self._validate_execution_quantity(event, spec, event.quantity_delta)
        filled, status, reason, capacity_consumed_before = self._capacity_quantity(
            event, spec
        )
        if isinstance(event, ReportedFillReplayEvent) and filled != event.quantity_delta:
            raise ReportedFillReplayError("REPORTED_FILL_CAPACITY_MODEL_MISMATCH")
        budget_key = (
            event.capacity.venue_id,
            event.instrument_id,
            event.capacity.period_rule_id,
            event.capacity.period_index,
        )
        if filled == ZERO:
            result = ExecutionResult(
                status=status,
                requested_quantity=event.quantity_delta,
                filled_quantity=ZERO,
                reason=reason,
            )
            self._events.append(
                {
                    "event_type": "TRADE",
                    "timestamp": event.timestamp,
                    "instrument_id": event.instrument_id,
                    **result.as_canonical_dict(),
                    "capacity": event.capacity.as_canonical_dict(),
                    "capacity_consumed_before": canonical_decimal(
                        capacity_consumed_before
                    ),
                    "capacity_consumed_after": canonical_decimal(
                        capacity_consumed_before
                    ),
                }
            )
            return result

        filled_notional = self._cash(
            spec.notional(filled, event.execution_price),
            "capacity.filled_notional",
        )
        capacity_consumed_after = self._cash_add(
            capacity_consumed_before,
            filled_notional,
            field="capacity.consumed_notional",
        )
        if capacity_consumed_after > event.capacity.maximum_notional:
            raise MarginConfigurationError("capacity accounting exceeded its hard budget")
        self._capacity_consumed[budget_key] = capacity_consumed_after

        current = self._positions.get(event.instrument_id)
        if current is not None and current.margin_mode != event.margin_mode:
            raise MarginConfigurationError(
                "margin mode cannot change while a position is open"
            )
        if event.margin_mode == MarginMode.CROSS and event.isolated_margin is not None:
            raise MarginConfigurationError(
                "isolated_margin is invalid for a cross position"
            )

        if current is None:
            if event.margin_mode == MarginMode.ISOLATED:
                if event.isolated_margin is None:
                    raise InsufficientMargin(
                        "isolated positions require explicit isolated_margin"
                    )
                if requested_isolated_margin is None:
                    raise InsufficientMargin(
                        "isolated positions require explicit isolated_margin"
                    )
                if requested_isolated_margin > self._wallet_balance:
                    raise InsufficientMargin(
                        "isolated collateral exceeds the available wallet balance"
                    )
                self._wallet_balance = self._cash_add(
                    self._wallet_balance,
                    -requested_isolated_margin,
                    field="wallet_balance",
                )
                isolated_wallet = requested_isolated_margin
            else:
                isolated_wallet = ZERO
            current_for_cost = None
        else:
            if event.isolated_margin is not None:
                raise MarginConfigurationError(
                    "isolated collateral changes require a separate reviewed event"
                )
            isolated_wallet = current.isolated_wallet
            current_for_cost = current

        new_quantity, new_average, realized, entry_notional = self._apply_average_cost(
            current_for_cost,
            filled,
            event.execution_price,
            spec.contract_multiplier,
        )
        exposure_opened = bool(
            new_quantity != ZERO and (
                current_for_cost is None
                or (new_quantity > ZERO) != (current_for_cost.quantity > ZERO)
                or abs(new_quantity) > abs(current_for_cost.quantity))
        )
        if new_quantity != ZERO and exposure_opened and spec.new_risk_notional_cap is not None:
            reference = max(event.execution_price, self._mark_prices.get(event.instrument_id, event.execution_price))
            if spec.notional(new_quantity, reference) >= spec.new_risk_notional_cap:
                raise AccountNotionalLimit("ACCOUNT_NOTIONAL_LIMIT")
        # TradeEvent costs describe the requested order.  A load-bearing
        # capacity partial fill realizes only the corresponding fraction.
        fill_ratio = abs(filled / event.quantity_delta)
        applied_fee = (
            self._cash_product(filled_notional, event.fee_rate, field="trade.applied_fee")
            if isinstance(event, OrderFillEvent) else
            self._cash_product(requested_fee, fill_ratio, field="trade.applied_fee")
        )
        applied_spread = self._cash_product(
            requested_spread, fill_ratio, field="trade.applied_spread"
        )
        applied_impact = self._cash_product(
            requested_impact, fill_ratio, field="trade.applied_impact"
        )
        total_cost = self._cash_add(
            applied_fee,
            applied_spread,
            applied_impact,
            field="trade.total_cost",
        )
        if event.margin_mode == MarginMode.ISOLATED:
            isolated_wallet = self._cash_add(
                isolated_wallet,
                realized,
                -total_cost,
                field="isolated_wallet",
            )
        else:
            self._wallet_balance = self._cash_add(
                self._wallet_balance,
                realized,
                -total_cost,
                field="wallet_balance",
            )
        self._realized_price_pnl = self._cash_add(
            self._realized_price_pnl,
            realized,
            field="realized_price_pnl",
        )
        self._fee = self._cash_add(self._fee, applied_fee, field="fee")
        self._spread = self._cash_add(
            self._spread, applied_spread, field="spread"
        )
        self._impact = self._cash_add(
            self._impact, applied_impact, field="impact"
        )

        if new_quantity == ZERO:
            if event.margin_mode == MarginMode.ISOLATED:
                if isolated_wallet < ZERO:
                    raise MissingLiquidationBackstop(
                        "isolated close creates a bankruptcy deficit; an explicit "
                        "mark/funding liquidation backstop is required"
                    )
                self._wallet_balance = self._cash_add(
                    self._wallet_balance,
                    isolated_wallet,
                    field="wallet_balance",
                )
            self._positions.pop(event.instrument_id, None)
            self._pending_initial_margin_prices.pop(event.instrument_id, None)
        else:
            if exposure_opened:
                if (
                    current_for_cost is None
                    or (new_quantity > ZERO) != (current_for_cost.quantity > ZERO)
                ):
                    pending_margin_prices = (event.execution_price,)
                else:
                    pending_margin_prices = (
                        *self._pending_initial_margin_prices.get(
                            event.instrument_id, ()
                        ),
                        event.execution_price,
                    )
                self._pending_initial_margin_prices[event.instrument_id] = (
                    tuple(sorted({min(pending_margin_prices), max(pending_margin_prices)}))
                )
            self._positions[event.instrument_id] = _Position(
                instrument_id=event.instrument_id,
                quantity=new_quantity,
                average_entry_price=new_average,
                # An execution price is not a venue mark.  Preserve the last
                # position mark when increasing/reversing, and use retained
                # flat-account market state for a new position.  Execution is
                # only a placeholder when no explicit venue mark exists yet.
                mark_price=(
                    self._mark_prices.get(
                        event.instrument_id, event.execution_price
                    )
                    if current_for_cost is None
                    else current_for_cost.mark_price
                ),
                margin_mode=event.margin_mode,
                isolated_wallet=isolated_wallet,
                entry_notional=entry_notional,
            )
        if exposure_opened:
            self._assert_initial_margin()
        elif self._liquidation_targets() or self._cross_equity() < ZERO:
            # An exit need not replenish initial margin, but its modeled costs
            # may not create insolvency or a maintenance shortfall. Observed
            # venue fills are not submitted through this simulation admission.
            raise InsufficientMargin("reduction cannot leave a maintenance deficit or negative equity")
        self._assert_cash_state()

        result = ExecutionResult(
            status=status,
            requested_quantity=event.quantity_delta,
            filled_quantity=filled,
            reason=reason,
        )
        self._events.append(
            {
                "event_type": "TRADE",
                "timestamp": event.timestamp,
                "instrument_id": event.instrument_id,
                **({"order_id": event.order_id,
                    "quantity_basis": "REPORTED_FILL_TECHNICAL_REPLAY_V1",
                    "receipt_sha256": event.receipt_sha256}
                   if isinstance(event, ReportedFillReplayEvent) else {}),
                **({"order_id": event.order_id,
                    "quantity_basis": "ADMITTED_ORDER_FILL",
                    "fee_rate": canonical_decimal(event.fee_rate),
                    **({"liquidity_role": event.liquidity_role} if event.liquidity_role is not None else {})}
                   if isinstance(event, OrderFillEvent) else {}),
                **result.as_canonical_dict(),
                "execution_price": canonical_decimal(event.execution_price),
                "requested_fee": canonical_decimal(event.fee),
                "requested_spread_cost": canonical_decimal(event.spread_cost),
                "requested_impact_cost": canonical_decimal(event.impact_cost),
                "applied_fee": canonical_decimal(applied_fee),
                "applied_spread_cost": canonical_decimal(applied_spread),
                "applied_impact_cost": canonical_decimal(applied_impact),
                "margin_mode": event.margin_mode.value,
                "margin_admission": "INITIAL" if exposure_opened else "MAINTENANCE_REDUCTION",
                "capacity": event.capacity.as_canonical_dict(),
                "capacity_consumed_before": canonical_decimal(
                    capacity_consumed_before
                ),
                "capacity_consumed_after": canonical_decimal(
                    capacity_consumed_after
                ),
            }
        )
        return result

    def _apply_funding(
        self, event: FundingEvent, *, defer_liquidation: bool = False
    ) -> None:
        self._spec(event.instrument_id)
        if event.liquidation_backstop is not None:
            self._cash(
                event.liquidation_backstop.insurance_adjustment,
                "liquidation.insurance_adjustment",
            )
            self._cash(
                event.liquidation_backstop.adl_adjustment,
                "liquidation.adl_adjustment",
            )
        self._mark_prices[event.instrument_id] = event.mark_price
        self._pending_initial_margin_prices.pop(event.instrument_id, None)
        position = self._positions.get(event.instrument_id)
        pre_settlement_quantity = ZERO if position is None else position.quantity
        cash_flow = ZERO
        if position is not None:
            position.mark_price = event.mark_price
            spec = self._spec(event.instrument_id)
            cash_flow = self._cash_product(
                -position.quantity,
                event.mark_price,
                spec.contract_multiplier,
                event.funding_rate,
                field=f"funding[{event.instrument_id}]",
            )
            if position.margin_mode == MarginMode.ISOLATED:
                position.isolated_wallet = self._cash_add(
                    position.isolated_wallet,
                    cash_flow,
                    field=f"isolated_wallet[{event.instrument_id}]",
                )
            else:
                self._wallet_balance = self._cash_add(
                    self._wallet_balance,
                    cash_flow,
                    field="wallet_balance",
                )
            self._funding = self._cash_add(
                self._funding, cash_flow, field="funding"
            )
            if not defer_liquidation:
                self._liquidate_if_required(
                    timestamp=event.timestamp,
                    backstop=event.liquidation_backstop,
                )
        self._assert_cash_state()
        self._events.append(
            {
                "event_type": "FUNDING_SETTLEMENT",
                "timestamp": event.timestamp,
                "instrument_id": event.instrument_id,
                "pre_settlement_quantity": canonical_decimal(
                    pre_settlement_quantity
                ),
                "funding_rate": canonical_decimal(event.funding_rate),
                "mark_price": canonical_decimal(event.mark_price),
                "cash_flow": canonical_decimal(cash_flow),
            }
        )

    def _apply_mark(self, event: MarkEvent, *, defer_liquidation: bool = False) -> None:
        self._spec(event.instrument_id)
        if event.liquidation_backstop is not None:
            self._cash(
                event.liquidation_backstop.insurance_adjustment,
                "liquidation.insurance_adjustment",
            )
            self._cash(
                event.liquidation_backstop.adl_adjustment,
                "liquidation.adl_adjustment",
            )
        self._mark_prices[event.instrument_id] = event.mark_price
        self._pending_initial_margin_prices.pop(event.instrument_id, None)
        position = self._positions.get(event.instrument_id)
        if position is not None:
            position.mark_price = event.mark_price
            if not defer_liquidation:
                self._liquidate_if_required(
                    timestamp=event.timestamp,
                    backstop=event.liquidation_backstop,
                )
        self._assert_cash_state()
        self._events.append(
            {
                "event_type": "MARK",
                "timestamp": event.timestamp,
                "instrument_id": event.instrument_id,
                "mark_price": canonical_decimal(event.mark_price),
                "observed_trade_price": (
                    None
                    if event.observed_trade_price is None
                    else canonical_decimal(event.observed_trade_price)
                ),
            }
        )

    def _liquidate_if_required(
        self,
        *,
        timestamp: int,
        backstop: LiquidationBackstop | None,
    ) -> None:
        targets = self._liquidation_targets()
        if not targets:
            return
        if backstop is None:
            raise MissingLiquidationBackstop(
                "liquidation requires explicit insurance and ADL adjustments"
            )
        insurance_adjustment = self._cash(
            backstop.insurance_adjustment,
            "liquidation.insurance_adjustment",
        )
        adl_adjustment = self._cash(
            backstop.adl_adjustment,
            "liquidation.adl_adjustment",
        )

        pending_facts: list[tuple[_Position, Decimal, Decimal]] = []
        isolated_deficits: dict[str, Decimal] = {}
        cross_negative_cash_flows: dict[str, Decimal] = {}
        required_backstop = ZERO
        cross_wallet_before = self._wallet_balance
        cross_liquidation_cash_flow = ZERO
        released_isolated_surplus = ZERO
        for instrument_id in targets:
            position = self._positions[instrument_id]
            spec = self._spec(instrument_id)
            realized = self._unrealized(position)
            liquidation_fee = self._cash_product(
                self._position_notional(position),
                spec.liquidation_fee_rate,
                field=f"liquidation_fee[{instrument_id}]",
            )
            if position.margin_mode == MarginMode.ISOLATED:
                remaining = self._cash_add(
                    position.isolated_wallet,
                    realized,
                    -liquidation_fee,
                    field=f"isolated_liquidation_remaining[{instrument_id}]",
                )
                isolated_deficit = max(ZERO, -remaining)
                isolated_deficits[instrument_id] = isolated_deficit
                required_backstop = self._cash_add(
                    required_backstop,
                    isolated_deficit,
                    field="required_backstop",
                )
                released_isolated_surplus = self._cash_add(
                    released_isolated_surplus,
                    max(ZERO, remaining),
                    field="released_isolated_surplus",
                )
                self._wallet_balance = self._cash_add(
                    self._wallet_balance,
                    remaining,
                    field="wallet_balance",
                )
            else:
                cross_cash_flow = self._cash_add(
                    realized,
                    -liquidation_fee,
                    field=f"cross_liquidation_cash_flow[{instrument_id}]",
                )
                cross_negative_cash_flows[instrument_id] = max(
                    ZERO, -cross_cash_flow
                )
                cross_liquidation_cash_flow = self._cash_add(
                    cross_liquidation_cash_flow,
                    cross_cash_flow,
                    field="cross_liquidation_cash_flow",
                )
                self._wallet_balance = self._cash_add(
                    self._wallet_balance,
                    cross_cash_flow,
                    field="wallet_balance",
                )
            self._realized_price_pnl = self._cash_add(
                self._realized_price_pnl,
                realized,
                field="realized_price_pnl",
            )
            self._liquidation_fee = self._cash_add(
                self._liquidation_fee,
                liquidation_fee,
                field="liquidation_fee",
            )
            del self._positions[instrument_id]
            self._pending_initial_margin_prices.pop(instrument_id, None)
            pending_facts.append((position, realized, liquidation_fee))

        cross_post_liquidation_equity = self._cash_add(
            cross_wallet_before,
            cross_liquidation_cash_flow,
            released_isolated_surplus,
            field="cross_post_liquidation_equity",
        )
        cross_required_backstop = max(ZERO, -cross_post_liquidation_equity)
        required_backstop = self._cash_add(
            required_backstop,
            cross_required_backstop,
            field="required_backstop",
        )
        net_backstop = self._cash_add(
            insurance_adjustment,
            adl_adjustment,
            field="liquidation.net_backstop",
        )
        if net_backstop != required_backstop:
            raise InsufficientLiquidationBackstop(
                "liquidation backstop must exactly cover the isolated/cross "
                "bankruptcy deficit; unexplained surplus injection is forbidden"
            )

        deficit_allocations = {
            position.instrument_id: isolated_deficits.get(position.instrument_id, ZERO)
            for position, _realized, _liquidation_fee in pending_facts
        }
        allocation_bases = {
            position.instrument_id: "NO_BANKRUPTCY_DEFICIT_ALLOCATION_V1"
            for position, _realized, _liquidation_fee in pending_facts
        }
        if cross_required_backstop > ZERO:
            weighted_cross_ids = tuple(
                instrument_id
                for instrument_id in sorted(cross_negative_cash_flows)
                if cross_negative_cash_flows[instrument_id] > ZERO
            )
            total_cross_weight = self._cash_add(
                *(
                    cross_negative_cash_flows[instrument_id]
                    for instrument_id in weighted_cross_ids
                ),
                field="cross_deficit_allocation_weight",
            )
            if not weighted_cross_ids or total_cross_weight <= ZERO:
                raise MarginConfigurationError(
                    "cross bankruptcy deficit has no negative liquidation cash "
                    "flow available for deterministic attribution"
                )
            allocated_cross = ZERO
            for index, instrument_id in enumerate(weighted_cross_ids):
                if index == len(weighted_cross_ids) - 1:
                    allocation = self._cash_add(
                        cross_required_backstop,
                        -allocated_cross,
                        field="cross_deficit_allocation",
                    )
                else:
                    allocation = self._pro_rata_cash(
                        cross_required_backstop,
                        cross_negative_cash_flows[instrument_id],
                        total_cross_weight,
                        field="cross_deficit_allocation",
                    )
                    allocated_cross = self._cash_add(
                        allocated_cross,
                        allocation,
                        field="allocated_cross_deficit",
                    )
                deficit_allocations[instrument_id] = allocation
                allocation_bases[instrument_id] = (
                    "CROSS_ACCOUNT_DEFICIT_PRO_RATA_NEGATIVE_CASHFLOW_V1"
                )
        for instrument_id, deficit in isolated_deficits.items():
            if deficit > ZERO:
                allocation_bases[instrument_id] = (
                    "EXACT_ISOLATED_BANKRUPTCY_DEFICIT_V1"
                )

        if self._cash_add(
            *deficit_allocations.values(), field="deficit_allocations"
        ) != required_backstop:
            raise MarginConfigurationError(
                "per-instrument bankruptcy-deficit allocations do not reconcile"
            )

        insurance_allocations = {
            instrument_id: ZERO for instrument_id in deficit_allocations
        }
        adl_allocations = {
            instrument_id: ZERO for instrument_id in deficit_allocations
        }
        positive_allocation_ids = tuple(
            instrument_id
            for instrument_id in sorted(deficit_allocations)
            if deficit_allocations[instrument_id] > ZERO
        )
        if len(pending_facts) == 1:
            only_instrument = pending_facts[0][0].instrument_id
            insurance_allocations[only_instrument] = insurance_adjustment
            adl_allocations[only_instrument] = adl_adjustment
            allocation_bases[only_instrument] = "DIRECT_SINGLE_TARGET_BACKSTOP_V1"
        elif required_backstop == ZERO:
            if (
                insurance_adjustment != ZERO
                or adl_adjustment != ZERO
            ):
                raise MarginConfigurationError(
                    "multi-target zero-deficit liquidation cannot attribute "
                    "offsetting account-level backstop components"
                )
        else:
            if not positive_allocation_ids:
                raise MarginConfigurationError(
                    "non-zero backstop has no per-instrument deficit allocation"
                )
            insurance_allocated = ZERO
            adl_allocated = ZERO
            for index, instrument_id in enumerate(positive_allocation_ids):
                if index == len(positive_allocation_ids) - 1:
                    insurance = self._cash_add(
                        insurance_adjustment,
                        -insurance_allocated,
                        field="insurance_allocation",
                    )
                    adl = self._cash_add(
                        adl_adjustment,
                        -adl_allocated,
                        field="adl_allocation",
                    )
                else:
                    insurance = self._pro_rata_cash(
                        insurance_adjustment,
                        deficit_allocations[instrument_id],
                        required_backstop,
                        field="insurance_allocation",
                    )
                    adl = self._cash_add(
                        deficit_allocations[instrument_id],
                        -insurance,
                        field="adl_allocation",
                    )
                    insurance_allocated = self._cash_add(
                        insurance_allocated,
                        insurance,
                        field="allocated_insurance",
                    )
                    adl_allocated = self._cash_add(
                        adl_allocated,
                        adl,
                        field="allocated_adl",
                    )
                if self._cash_add(
                    insurance, adl, field="instrument_backstop_allocation"
                ) != deficit_allocations[instrument_id]:
                    raise MarginConfigurationError(
                        "per-instrument insurance/ADL allocation does not match "
                        "its bankruptcy deficit"
                    )
                insurance_allocations[instrument_id] = insurance
                adl_allocations[instrument_id] = adl

        if (
            self._cash_add(
                *insurance_allocations.values(), field="insurance_allocations"
            )
            != insurance_adjustment
            or self._cash_add(
                *adl_allocations.values(), field="adl_allocations"
            )
            != adl_adjustment
        ):
            raise MarginConfigurationError(
                "per-instrument insurance/ADL allocations do not reconcile"
            )

        pending_records = [
            LiquidationRecord(
                timestamp=timestamp,
                instrument_id=position.instrument_id,
                margin_mode=position.margin_mode,
                mark_price=position.mark_price,
                quantity=position.quantity,
                realized_price_pnl=realized,
                liquidation_fee=liquidation_fee,
                insurance_adjustment=insurance_allocations[position.instrument_id],
                adl_adjustment=adl_allocations[position.instrument_id],
                bankruptcy_deficit_allocation=deficit_allocations[
                    position.instrument_id
                ],
                backstop_allocation_basis=allocation_bases[position.instrument_id],
            )
            for position, realized, liquidation_fee in pending_facts
        ]
        self._wallet_balance = self._cash_add(
            self._wallet_balance,
            net_backstop,
            field="wallet_balance",
        )
        self._insurance_adjustment = self._cash_add(
            self._insurance_adjustment,
            insurance_adjustment,
            field="insurance_adjustment",
        )
        self._adl_adjustment = self._cash_add(
            self._adl_adjustment,
            adl_adjustment,
            field="adl_adjustment",
        )
        if self.account_equity() < ZERO:
            raise InsufficientLiquidationBackstop(
                "liquidation backstop leaves account equity negative"
            )
        self._assert_cash_state()
        self._liquidations.extend(pending_records)
        self._events.extend(
            {
                "event_type": "LIQUIDATION",
                **record.as_canonical_dict(),
            }
            for record in pending_records
        )

    @_reviewed_decimal
    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        unrealized = self._cash_add(
            *(self._unrealized(position) for position in self._positions.values()),
            field="unrealized_price_pnl",
        )
        price_pnl = self._cash_add(
            self._realized_price_pnl,
            unrealized,
            -self._opening_unrealized,
            field="price_pnl",
        )
        cls=ReconciliationSnapshot if self._reported_cash_policy is None else ReportedCashReconciliationSnapshot
        return cls(
            initial_equity=self._cash_add(self._initial_collateral, self._opening_unrealized, field='opening equity'),
            ending_equity=self.account_equity(),
            price_pnl=price_pnl,
            fee=self._fee,
            spread=self._spread,
            impact=self._impact,
            funding=self._funding,
            liquidation_fee=self._liquidation_fee,
            insurance_adjustment=self._insurance_adjustment,
            adl_adjustment=self._adl_adjustment,
            **({'external_cash_flow':self._external_cash_flow} if self._reported_cash_policy is not None else {}),
        )

    @_reviewed_decimal
    def state_as_canonical_dict(self, *, streaming=False) -> dict[str, object]:
        from quantos.streaming_identity import StreamArray, StreamObject, key_order
        def capacity(mapping, transform):
            def items():
                ordered = mapping.sorted_items() if hasattr(mapping, 'sorted_items') else sorted(
                    mapping.items(), key=lambda item: key_order('|'.join(str(part) for part in item[0])))
                return (('|'.join(str(part) for part in key), transform(value)) for key,value in ordered)
            return StreamObject(items) if streaming else dict(items())
        return {
            **({'reported_cash_policy':self._reported_cash_policy.as_canonical_dict(),
                'reported_cash_identities':dict(sorted(self._reported_cash_seen.items())),
                'reported_fill_times':dict(sorted(self._reported_fill_times.items()))} if self._reported_cash_policy is not None else {}),
            **({'opening_state': self._opening_state.as_canonical_dict(),
                'opening_unrealized': canonical_decimal(self._opening_unrealized)} if self._opening_state is not None else {}),
            "schema": f"quantos.linear-{self._cash_spec.margin_asset.lower()}-perpetual-ledger.v2",
            "decimal_context": DECIMAL_CONTEXT_VERSION,
            "settlement_quantum": canonical_decimal(
                self._cash_spec.settlement_quantum
            ),
            "initial_collateral": canonical_decimal(self._initial_collateral),
            "cost_basis_policy": COST_BASIS_POLICY,
            "margin_admission_policy": MARGIN_ADMISSION_POLICY,
            "wallet_balance": canonical_decimal(self._wallet_balance),
            "account_equity": canonical_decimal(self.account_equity()),
            "positions": {
                instrument_id: self.position(instrument_id).as_canonical_dict()  # type: ignore[union-attr]
                for instrument_id in sorted(self._positions)
            },
            "mark_prices": {
                instrument_id: canonical_decimal(mark_price)
                for instrument_id, mark_price in sorted(self._mark_prices.items())
            },
            "pending_initial_margin_execution_prices": {
                instrument_id: [canonical_decimal(price) for price in prices]
                for instrument_id, prices in sorted(
                    self._pending_initial_margin_prices.items()
                )
            },
            "capacity_consumed": capacity(self._capacity_consumed, canonical_decimal),
            "capacity_period_contracts": capacity(self._capacity_contracts, copy.deepcopy),
            "cashflow_identity": self.reconciliation_snapshot().as_canonical_dict(),
            "liquidations": StreamArray(lambda: (record.as_canonical_dict() for record in self._liquidations))
                if streaming else [record.as_canonical_dict() for record in self._liquidations],
        }


__all__ = [
    "ReportedCashReplayPolicy",
    "ReportedCashFlowEvent",
    "ReportedCashReplayError",
    "ReportedCashReconciliationSnapshot",
    "REPORTED_CASH_POLICY",
    "OpeningPosition",
    "OpeningLedgerState",
    "OPENING_STATE_POLICY",
    "AccountNotionalLimit",
    "MARGIN_ADMISSION_POLICY",
    "BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID",
    "DECIMAL_CONTEXT_VERSION",
    "CapacityAction",
    "CapacityPolicy",
    "CashQuantumViolation",
    "ContractSpec",
    "EventOrderViolation",
    "ExecutionResult",
    "FundingEvent",
    "InsufficientLiquidationBackstop",
    "InsufficientMargin",
    "LINEAR_USDT_PERPETUAL",
    "LINEAR_USDC_PERPETUAL",
    "LinearPerpetualLedger",
    "LiquidationBackstop",
    "LiquidationRecord",
    "MarginBracket",
    "MarginConfigurationError",
    "MarginMode",
    "MarginRequirement",
    "MarkEvent",
    "OrderFillEvent",
    "ReportedFillReplayEvent",
    "ReportedFillReplayError",
    "MissingLiquidationBackstop",
    "PerpetualLedgerError",
    "PositionView",
    "QuantityRuleViolation",
    "ReconciliationSnapshot",
    "TradeEvent",
    "UnsupportedContract",
    "canonical_decimal",
    "ledger_decimal_context",
]
