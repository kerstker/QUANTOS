"""Strict authority-capable runner for linear USDT perpetual backtests.

This is a deliberately narrow V2 boundary, not an adapter around the legacy
dry-run path.  It has no broker, network, credential, persistence, promotion,
or live-order capability.  It validates the engine mechanics and raises when a
load-bearing fact is absent or inconsistent.  Records remain explicitly
``TECHNICAL_VALIDATION_ONLY`` until an external signed raw-archive authority is
wired in; caller-supplied content hashes are lineage, not proof of authenticity.

Reviewed V2 scope:

* one execution per decision at the first strictly later archived venue event,
  with exact event-price binding;
* a reviewed direct-linear signal mapping whose computed target position fixes
  the permitted execution quantity delta;
* one immutable historical contract-rule epoch per instrument and run;
* explicit event/available/ingestion time for every consumed datum;
* event-by-event mark, funding, and trade processing through the Decimal
  linear-USDT perpetual ledger;
* an explicit variable settlement schedule, with no omitted funding events;
* load-bearing reject-on-breach capacity and point-in-time cost calibration;
* nine-engine research risk computed inside this runner from actual current
  ledger equity, positions, conservatively valued target and actual order exposure; the
  historical returns path remains caller-supplied causal evidence and is
  explicitly barred from authority until independently replayed from ledger
  checkpoints;
* exact input manifest identity and an immutable-result hash.

Cross-rule-epoch runs, partial fills, multiple orders per decision, inverse
contracts, portfolio margin, and dual-side hedge mode are rejected rather than
approximated.  They require separately reviewed schema/engine versions.

For strategy-driven order lifecycle simulation, use
``perpetual_simulation.run_linear_perpetual_simulation``. This module retains
the strict archived-replay API; future fills are checked at execution time.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from quantos.contracts.research_capacity_flow import POLICY as CAPACITY_FLOW_POLICY, build_capacity_flow
from services.execution.backtest_authority import (
    CANONICAL_ENGINE_SEMANTICS,
    build_run_manifest_v2,
    exact_identity_hash,
    is_authoritative_backtest_record,
)
from services.execution.perpetual_ledger_v2 import (
    DECIMAL_CONTEXT_VERSION,
    CapacityAction,
    CapacityPolicy,
    ContractSpec,
    ExecutionResult,
    FundingEvent,
    LedgerEvent,
    LinearPerpetualLedger,
    MarginMode,
    MarkEvent,
    TradeEvent,
    canonical_decimal,
    ledger_decimal_context,
)
from services.risk.research_risk.domain.models import RiskConfig
from services.risk.research_risk.dry_run_guard import (
    DryRunResearchRiskInputs,
    evaluate_dry_run_risk,
    full_research_risk_approved,
)


ZERO = Decimal("0")
ONE = Decimal("1")
RESULT_HASH_SCHEMA_VERSION = "TECHNICAL_PERPETUAL_VALIDATION_V2"
TECHNICAL_VALIDATION_STATUS = "TECHNICAL_VALIDATION_ONLY"
DIRECT_LINEAR_TARGET_SCHEMA = "DIRECT_LINEAR_TARGET_QUANTITY_V1"
RISK_MARK_POLICY_VERSION = "quantos.perpetual-risk-mark.v2"
RISK_MARK_MAX_AGE_MS = 60_000
RISK_EQUITY_POLICY_VERSION = "quantos.perpetual-risk-equity.v1"
RISK_EQUITY_DENOMINATOR_POLICY = (
    "MIN_MARK_EQUITY_AND_CONFIRMED_PRICE_FLOOR_EQUITY"
)
RISK_HISTORY_POLICY = "CALLER_SUPPLIED_CAUSAL_SERIES_NOT_LEDGER_REPLAYED"


class AuthoritativeBacktestInputError(ValueError):
    """A required causal/economic fact is absent or internally inconsistent."""


@dataclass(frozen=True)
class _DirectTargetContract:
    signal_field: str
    quantity_scale: Decimal
    max_abs_quantity: Decimal


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value


def _require_identity(value: Any, field: str) -> None:
    if value is None:
        raise AuthoritativeBacktestInputError(f"{field} is required")
    if isinstance(value, (str, bytes, Sequence, Mapping)) and len(value) == 0:
        raise AuthoritativeBacktestInputError(f"{field} must be non-empty")
    exact_identity_hash(value, name=field)


def _decimal(value: object, field: str, *, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{field} must not use binary float")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)  # type: ignore[arg-type]
    except Exception as exc:
        raise TypeError(f"{field} must be Decimal, int, or canonical string") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    if non_negative and result < ZERO:
        raise ValueError(f"{field} must be non-negative")
    return result


def _validate_provenance(
    *, event_time: int, available_time: int, ingestion_time: int, name: str
) -> None:
    event = _timestamp(event_time, f"{name}.event_time")
    available = _timestamp(available_time, f"{name}.available_time")
    ingestion = _timestamp(ingestion_time, f"{name}.ingestion_time")
    if not event <= available <= ingestion:
        raise ValueError(
            f"{name} must satisfy event_time <= available_time <= ingestion_time"
        )


def _risk_config_dict(config: RiskConfig) -> dict[str, Any]:
    return {str(key): value for key, value in sorted(vars(config).items())}


def _full_risk_inputs_dict(inputs: DryRunResearchRiskInputs) -> dict[str, Any]:
    return {
        "capacity_cny": {
            str(key): value for key, value in sorted(inputs.capacity_cny.items())
        },
        "daily_volume_cny": inputs.daily_volume_cny,
        "total_aum": inputs.total_aum,
        "alpha_returns": {
            str(key): tuple(values)
            for key, values in sorted(inputs.alpha_returns.items())
        },
        "regime_labels": tuple(inputs.regime_labels),
        "alpha_metadata": tuple(dict(item) for item in inputs.alpha_metadata),
        "timestamps": (
            None if inputs.timestamps is None else tuple(inputs.timestamps)
        ),
    }


def _expected_risk_source_values(
    evidence: "ResearchRiskEvidence",
) -> dict[str, Any]:
    return {
        "portfolio_returns": evidence.returns,
        "initial_equity": evidence.initial_equity,
        "equity_curve": evidence.equity_curve,
        "timestamps": evidence.timestamps,
        "alpha_returns": {
            str(key): tuple(values)
            for key, values in sorted(evidence.full_inputs.alpha_returns.items())
        },
        "regime_labels": tuple(evidence.full_inputs.regime_labels),
        "alpha_metadata": tuple(
            dict(item) for item in evidence.full_inputs.alpha_metadata
        ),
        "capacity_and_aum": {
            "capacity_cny": {
                str(key): value
                for key, value in sorted(evidence.full_inputs.capacity_cny.items())
            },
            "daily_volume_cny": evidence.full_inputs.daily_volume_cny,
            "total_aum": evidence.full_inputs.total_aum,
        },
    }


def _validate_risk_source_bindings(evidence: "ResearchRiskEvidence") -> None:
    expected_sources = _expected_risk_source_values(evidence)
    if set(evidence.source_records) != set(expected_sources):
        raise ValueError(
            "risk source records must separately cover every nine-engine input"
        )
    for name, expected in expected_sources.items():
        record = evidence.source_records[name]
        if not isinstance(record, CausalDataRecord):
            raise TypeError(f"risk source {name} must be CausalDataRecord")
        if exact_identity_hash(
            record.value, name=f"risk_source[{name}]"
        ) != exact_identity_hash(expected, name=f"risk_expected[{name}]"):
            raise ValueError(f"risk source {name} does not bind the supplied input")
        if record.ingestion_time > evidence.ingestion_time:
            raise ValueError(f"risk source {name} post-dates the aggregate evidence")


@dataclass(frozen=True)
class CausalDataRecord:
    record_id: str
    event_time: int
    available_time: int
    ingestion_time: int
    source_identity: str
    value: Any

    def __post_init__(self) -> None:
        _nonempty(self.record_id, "record_id")
        _nonempty(self.source_identity, "source_identity")
        _validate_provenance(
            event_time=self.event_time,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"data_record[{self.record_id}]",
        )
        # Reject opaque/non-deterministic values at construction time.
        exact_identity_hash(self.value, name=f"data_record[{self.record_id}].value")

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "event_time": self.event_time,
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
            "value": self.value,
        }


@dataclass(frozen=True)
class HistoricalContractRule:
    """One archived rule epoch; V2 refuses to mutate it inside a run."""

    rule_id: str
    spec: ContractSpec
    effective_from: int
    effective_until: int
    observed_time: int
    available_time: int
    ingestion_time: int
    source_identity: str

    def __post_init__(self) -> None:
        _nonempty(self.rule_id, "rule_id")
        _nonempty(self.source_identity, "source_identity")
        if not isinstance(self.spec, ContractSpec):
            raise TypeError("spec must be ContractSpec")
        start = _timestamp(self.effective_from, "effective_from")
        end = _timestamp(self.effective_until, "effective_until")
        if end <= start:
            raise ValueError("effective_until must exceed effective_from")
        _validate_provenance(
            event_time=self.observed_time,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"contract_rule[{self.rule_id}]",
        )

    def covers(self, timestamp: int) -> bool:
        return self.effective_from <= timestamp < self.effective_until

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "spec": self.spec.as_canonical_dict(),
            "effective_from": self.effective_from,
            "effective_until": self.effective_until,
            "observed_time": self.observed_time,
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
        }


@dataclass(frozen=True)
class FundingPolicySnapshot:
    """Archived venue schedule; intervals may vary by instrument and epoch."""

    instrument_id: str
    venue_id: str
    policy_epoch_id: str
    coverage_start: int
    coverage_end: int
    settlement_interval_ms: int
    settlement_times: tuple[int, ...]
    observed_time: int
    available_time: int
    ingestion_time: int
    source_identity: str

    def __post_init__(self) -> None:
        _nonempty(self.instrument_id, "instrument_id")
        _nonempty(self.venue_id, "venue_id")
        _nonempty(self.policy_epoch_id, "policy_epoch_id")
        _nonempty(self.source_identity, "source_identity")
        start = _timestamp(self.coverage_start, "coverage_start")
        end = _timestamp(self.coverage_end, "coverage_end")
        if end <= start:
            raise ValueError("coverage_end must exceed coverage_start")
        interval = _timestamp(self.settlement_interval_ms, "settlement_interval_ms")
        if interval == 0:
            raise ValueError("settlement_interval_ms must be positive")
        settlements = tuple(
            _timestamp(value, "settlement_time") for value in self.settlement_times
        )
        if not settlements:
            raise ValueError(
                "technical V2 requires a non-empty authoritative settlement schedule"
            )
        if settlements != tuple(sorted(set(settlements))):
            raise ValueError("settlement_times must be strictly increasing and unique")
        if any(not start <= value <= end for value in settlements):
            raise ValueError("settlement_time lies outside policy coverage")
        if any(
            right - left != interval
            for left, right in zip(settlements, settlements[1:])
        ):
            raise ValueError("settlement_times do not match the archived venue interval")
        if not (
            settlements[0] - interval < start <= settlements[0]
            and settlements[-1] <= end < settlements[-1] + interval
        ):
            raise ValueError(
                "funding coverage leaves room for an omitted settlement event"
            )
        _validate_provenance(
            event_time=self.observed_time,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"funding_policy[{self.instrument_id}]",
        )

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "venue_id": self.venue_id,
            "policy_epoch_id": self.policy_epoch_id,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "settlement_interval_ms": self.settlement_interval_ms,
            "settlement_times": self.settlement_times,
            "observed_time": self.observed_time,
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
        }


@dataclass(frozen=True)
class ObservedLedgerEvent:
    event: FundingEvent | MarkEvent
    available_time: int
    ingestion_time: int
    source_identity: str
    rule_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.event, (FundingEvent, MarkEvent)):
            raise TypeError("observed market event must be FundingEvent or MarkEvent")
        _nonempty(self.source_identity, "source_identity")
        _nonempty(self.rule_id, "rule_id")
        _validate_provenance(
            event_time=self.event.timestamp,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"market_event[{self.event.instrument_id}]",
        )

    def as_canonical_dict(self) -> dict[str, Any]:
        if isinstance(self.event, FundingEvent):
            event_payload: dict[str, Any] = {
                "event_type": "FUNDING_SETTLEMENT",
                "timestamp": self.event.timestamp,
                "instrument_id": self.event.instrument_id,
                "funding_rate": canonical_decimal(self.event.funding_rate),
                "mark_price": canonical_decimal(self.event.mark_price),
                "liquidation_backstop": (
                    None
                    if self.event.liquidation_backstop is None
                    else self.event.liquidation_backstop.as_canonical_dict()
                ),
            }
        else:
            event_payload = {
                "event_type": "MARK",
                "timestamp": self.event.timestamp,
                "instrument_id": self.event.instrument_id,
                "mark_price": canonical_decimal(self.event.mark_price),
                "observed_trade_price": (
                    None
                    if self.event.observed_trade_price is None
                    else canonical_decimal(self.event.observed_trade_price)
                ),
                "liquidation_backstop": (
                    None
                    if self.event.liquidation_backstop is None
                    else self.event.liquidation_backstop.as_canonical_dict()
                ),
            }
        return {
            "event": event_payload,
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
            "rule_id": self.rule_id,
        }


@dataclass(frozen=True)
class CapacitySnapshot:
    instrument_id: str
    policy: CapacityPolicy
    adv_window_start: int
    adv_window_end: int
    available_time: int
    ingestion_time: int
    source_identity: str

    def __post_init__(self) -> None:
        _nonempty(self.instrument_id, "instrument_id")
        _nonempty(self.source_identity, "source_identity")
        if not isinstance(self.policy, CapacityPolicy):
            raise TypeError("policy must be CapacityPolicy")
        adv_window_start = _timestamp(self.adv_window_start, "adv_window_start")
        adv_window_end = _timestamp(self.adv_window_end, "adv_window_end")
        if adv_window_end <= adv_window_start:
            raise ValueError("ADV observation window must be positive")
        if (
            adv_window_start != self.policy.required_adv_window_start
            or adv_window_end != self.policy.required_adv_window_end
        ):
            raise ValueError(
                "ADV observation window must exactly match the reviewed "
                "capacity-period lookback"
            )
        _validate_provenance(
            event_time=adv_window_end,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"capacity[{self.instrument_id}]",
        )

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "policy": self.policy.as_canonical_dict(),
            "adv_window_start": self.adv_window_start,
            "adv_window_end": self.adv_window_end,
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
        }


@dataclass(frozen=True)
class CostModelSnapshot:
    """Point-in-time calibrated all-in cost rates for one execution."""

    model_identity: str
    calibration_end: int
    available_time: int
    ingestion_time: int
    source_identity: str
    fee_rate: Decimal
    spread_rate: Decimal
    impact_rate: Decimal

    def __post_init__(self) -> None:
        _nonempty(self.model_identity, "model_identity")
        _nonempty(self.source_identity, "source_identity")
        _validate_provenance(
            event_time=self.calibration_end,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"cost_model[{self.model_identity}]",
        )
        for name in ("fee_rate", "spread_rate", "impact_rate"):
            value = _decimal(getattr(self, name), name, non_negative=True)
            if value >= ONE:
                raise ValueError(f"{name} must be below one")
            object.__setattr__(self, name, value)

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "model_identity": self.model_identity,
            "calibration_end": self.calibration_end,
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
            "fee_rate": canonical_decimal(self.fee_rate),
            "spread_rate": canonical_decimal(self.spread_rate),
            "impact_rate": canonical_decimal(self.impact_rate),
        }


@dataclass(frozen=True)
class ExecutableVenueEvent:
    """One archived venue event at which an order could actually execute."""

    timestamp: int
    execution_price: Decimal

    def __post_init__(self) -> None:
        _timestamp(self.timestamp, "executable venue event timestamp")
        price = _decimal(self.execution_price, "executable venue event price")
        if price <= ZERO:
            raise ValueError("executable venue event price must be positive")
        object.__setattr__(self, "execution_price", price)

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "execution_price": canonical_decimal(self.execution_price),
        }


@dataclass(frozen=True)
class ExecutionOpportunitySnapshot:
    """Complete archived executable-event sequence around one decision.

    The sequence is execution data, so its archive receipt may become available
    after the decision.  It is never exposed to the alpha calculation.  The
    runner selects the first event strictly after the decision from this exact
    sequence instead of trusting a caller-provided ``NEXT_OPEN`` label.
    """

    instrument_id: str
    venue_id: str
    coverage_start: int
    coverage_end: int
    executable_events: tuple[ExecutableVenueEvent, ...]
    available_time: int
    ingestion_time: int
    source_identity: str

    def __post_init__(self) -> None:
        _nonempty(self.instrument_id, "instrument_id")
        _nonempty(self.venue_id, "venue_id")
        _nonempty(self.source_identity, "source_identity")
        start = _timestamp(self.coverage_start, "coverage_start")
        end = _timestamp(self.coverage_end, "coverage_end")
        if end <= start:
            raise ValueError("execution opportunity coverage must be positive")
        events = tuple(self.executable_events)
        if any(not isinstance(value, ExecutableVenueEvent) for value in events):
            raise TypeError("executable_events must contain ExecutableVenueEvent")
        times = tuple(value.timestamp for value in events)
        if not times or times != tuple(sorted(set(times))):
            raise ValueError("executable event times must be non-empty and unique")
        if any(not start <= value <= end for value in times):
            raise ValueError("executable event lies outside archive coverage")
        object.__setattr__(self, "executable_events", events)
        _validate_provenance(
            event_time=end,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"execution_opportunities[{self.instrument_id}]",
        )

    @property
    def executable_event_times(self) -> tuple[int, ...]:
        return tuple(value.timestamp for value in self.executable_events)

    def next_after(self, decision_time: int) -> ExecutableVenueEvent:
        try:
            return next(
                value for value in self.executable_events if value.timestamp > decision_time
            )
        except StopIteration as exc:
            raise AuthoritativeBacktestInputError(
                "execution opportunity archive has no event after decision"
            ) from exc

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "venue_id": self.venue_id,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "executable_events": [
                value.as_canonical_dict() for value in self.executable_events
            ],
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
        }


@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str
    timestamp: int
    instrument_id: str
    quantity_delta: Decimal
    execution_price: Decimal
    margin_mode: MarginMode
    isolated_margin: Decimal | None
    rule_id: str
    capacity: CapacitySnapshot
    cost_model: CostModelSnapshot
    opportunity: ExecutionOpportunitySnapshot
    available_time: int
    ingestion_time: int
    source_identity: str
    price_basis: str = "REFERENCE_PRICE"

    def __post_init__(self) -> None:
        _nonempty(self.execution_id, "execution_id")
        _nonempty(self.instrument_id, "instrument_id")
        _nonempty(self.rule_id, "rule_id")
        _nonempty(self.source_identity, "source_identity")
        _validate_provenance(
            event_time=self.timestamp,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name=f"execution[{self.execution_id}]",
        )
        quantity = _decimal(self.quantity_delta, "quantity_delta")
        if quantity == ZERO:
            raise ValueError("quantity_delta cannot be zero")
        price = _decimal(self.execution_price, "execution_price")
        if price <= ZERO:
            raise ValueError("execution_price must be positive")
        mode = (
            self.margin_mode
            if isinstance(self.margin_mode, MarginMode)
            else MarginMode(self.margin_mode)
        )
        isolated = (
            None
            if self.isolated_margin is None
            else _decimal(self.isolated_margin, "isolated_margin")
        )
        if isolated is not None and isolated <= ZERO:
            raise ValueError("isolated_margin must be positive")
        if not isinstance(self.capacity, CapacitySnapshot):
            raise TypeError("capacity must be CapacitySnapshot")
        if self.capacity.instrument_id != self.instrument_id:
            raise ValueError("capacity instrument does not match execution")
        if not self.capacity.policy.contains_timestamp(self.timestamp):
            raise ValueError(
                "execution timestamp lies outside capacity consumption window"
            )
        if not isinstance(self.cost_model, CostModelSnapshot):
            raise TypeError("cost_model must be CostModelSnapshot")
        if self.price_basis not in {"REFERENCE_PRICE", "EXECUTABLE_PRICE"}:
            raise ValueError("execution price_basis must be explicitly recognized")
        if self.price_basis == "EXECUTABLE_PRICE" and (
            self.cost_model.spread_rate != ZERO or self.cost_model.impact_rate != ZERO
        ):
            raise ValueError("executable price already contains embedded spread/impact")
        if not isinstance(self.opportunity, ExecutionOpportunitySnapshot):
            raise TypeError("opportunity must be ExecutionOpportunitySnapshot")
        if self.opportunity.instrument_id != self.instrument_id:
            raise ValueError("execution opportunity instrument does not match execution")
        if self.opportunity.venue_id != self.capacity.policy.venue_id:
            raise ValueError("execution opportunity venue does not match capacity venue")
        object.__setattr__(self, "quantity_delta", quantity)
        object.__setattr__(self, "execution_price", price)
        object.__setattr__(self, "margin_mode", mode)
        object.__setattr__(self, "isolated_margin", isolated)

    def build_trade_event(self, spec: ContractSpec) -> TradeEvent:
        notional = spec.notional(self.quantity_delta, self.execution_price)
        return TradeEvent(
            timestamp=self.timestamp,
            instrument_id=self.instrument_id,
            quantity_delta=self.quantity_delta,
            execution_price=self.execution_price,
            margin_mode=self.margin_mode,
            isolated_margin=self.isolated_margin,
            capacity=self.capacity.policy,
            fee=spec.cash_product(
                notional, self.cost_model.fee_rate, field="execution.fee"
            ),
            spread_cost=spec.cash_product(
                notional, self.cost_model.spread_rate, field="execution.spread"
            ),
            impact_cost=spec.cash_product(
                notional, self.cost_model.impact_rate, field="execution.impact"
            ),
        )

    def as_canonical_dict(self, spec: ContractSpec) -> dict[str, Any]:
        event = self.build_trade_event(spec)
        return {
            "execution_id": self.execution_id,
            "execution_time_ms": self.timestamp,
            "instrument_id": self.instrument_id,
            "quantity_delta": canonical_decimal(self.quantity_delta),
            "execution_price": canonical_decimal(self.execution_price),
            "price_basis": self.price_basis,
            "margin_mode": self.margin_mode.value,
            "isolated_margin": (
                None
                if self.isolated_margin is None
                else canonical_decimal(self.isolated_margin)
            ),
            "rule_id": self.rule_id,
            "capacity": self.capacity.as_canonical_dict(),
            "cost_model": self.cost_model.as_canonical_dict(),
            "execution_opportunity": self.opportunity.as_canonical_dict(),
            "calculated_costs": {
                "fee": canonical_decimal(event.fee),
                "spread": canonical_decimal(event.spread_cost),
                "impact": canonical_decimal(event.impact_cost),
            },
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
        }


@dataclass(frozen=True)
class ResearchRiskEvidence:
    returns: tuple[float, ...]
    initial_equity: float
    equity_curve: tuple[float, ...]
    timestamps: tuple[int, ...]
    full_inputs: DryRunResearchRiskInputs
    config: RiskConfig
    observed_time: int
    available_time: int
    ingestion_time: int
    source_identity: str
    source_records: Mapping[str, CausalDataRecord]

    def __post_init__(self) -> None:
        _nonempty(self.source_identity, "source_identity")
        _validate_provenance(
            event_time=self.observed_time,
            available_time=self.available_time,
            ingestion_time=self.ingestion_time,
            name="research_risk_evidence",
        )
        if not isinstance(self.full_inputs, DryRunResearchRiskInputs):
            raise TypeError("full_inputs must be DryRunResearchRiskInputs")
        if not isinstance(self.config, RiskConfig):
            raise TypeError("config must be RiskConfig")
        if not isinstance(self.source_records, Mapping):
            raise TypeError("source_records must be a mapping")
        if not self.returns or len(self.returns) != len(self.equity_curve):
            raise ValueError("risk returns and equity_curve must be non-empty and aligned")
        if len(self.timestamps) != len(self.returns):
            raise ValueError("risk timestamps must align with returns")
        if any(type(value) is not int or value < 0 for value in self.timestamps):
            raise ValueError("risk timestamps must contain non-negative integers")
        if tuple(sorted(set(self.timestamps))) != self.timestamps:
            raise ValueError("risk timestamps must be strictly increasing")
        if self.timestamps[-1] > self.observed_time:
            raise ValueError("risk evidence cannot precede its source series")
        if (
            isinstance(self.initial_equity, bool)
            or not isinstance(self.initial_equity, (int, float))
            or not math.isfinite(float(self.initial_equity))
            or self.initial_equity <= 0
        ):
            raise ValueError("risk initial_equity must be a finite positive number")
        for field, values in (
            ("returns", self.returns),
            ("equity_curve", self.equity_curve),
        ):
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in values
            ):
                raise ValueError(f"risk {field} must contain finite numbers")
        if any(value <= 0 for value in self.equity_curve):
            raise ValueError("risk equity_curve must stay positive")
        _validate_risk_source_bindings(self)

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "returns": self.returns,
            "initial_equity": self.initial_equity,
            "equity_curve": self.equity_curve,
            "timestamps": self.timestamps,
            "full_inputs": _full_risk_inputs_dict(self.full_inputs),
            "config": _risk_config_dict(self.config),
            "observed_time": self.observed_time,
            "available_time": self.available_time,
            "ingestion_time": self.ingestion_time,
            "source_identity": self.source_identity,
            "source_records": {
                name: record.as_canonical_dict()
                for name, record in sorted(self.source_records.items())
            },
        }


@dataclass(frozen=True)
class DecisionRequest:
    decision_id: str
    timestamp: int
    signal_inputs: tuple[CausalDataRecord, ...]
    execution: ExecutionRequest
    capacity_snapshots: tuple[CapacitySnapshot, ...]
    research_risk: ResearchRiskEvidence

    def __post_init__(self) -> None:
        _nonempty(self.decision_id, "decision_id")
        _timestamp(self.timestamp, "decision timestamp")
        if not self.signal_inputs or any(
            not isinstance(value, CausalDataRecord) for value in self.signal_inputs
        ):
            raise TypeError("signal_inputs must contain causal data records")
        if not isinstance(self.execution, ExecutionRequest):
            raise TypeError("execution must be ExecutionRequest")
        if not self.capacity_snapshots or any(
            not isinstance(value, CapacitySnapshot)
            for value in self.capacity_snapshots
        ):
            raise TypeError("capacity_snapshots must contain capacity evidence")
        if not isinstance(self.research_risk, ResearchRiskEvidence):
            raise TypeError("research_risk must be ResearchRiskEvidence")


@dataclass(frozen=True)
class _ScheduledEvent:
    event: LedgerEvent
    ingestion_time: int
    source_identity: str
    execution_id: str | None = None


def _ensure_causal_at_decision(
    *, ingestion_time: int, decision_time: int, name: str
) -> None:
    if ingestion_time > decision_time:
        raise AuthoritativeBacktestInputError(
            f"{name} was ingested after decision {decision_time}"
        )


def _position_snapshot(
    ledger: LinearPerpetualLedger, instruments: Sequence[str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for instrument_id in sorted(instruments):
        position = ledger.position(instrument_id)
        if position is not None:
            result[instrument_id] = position.as_canonical_dict()
    return result


def _validate_equity_history(evidence: ResearchRiskEvidence, equity: Decimal) -> None:
    last = Decimal(str(evidence.equity_curve[-1]))
    if last != equity:
        raise AuthoritativeBacktestInputError(
            "research risk total history does not end at actual ledger equity"
        )
    previous = evidence.initial_equity
    for index, current in enumerate(evidence.equity_curve):
        if previous <= 0:
            raise AuthoritativeBacktestInputError("risk equity history must stay positive")
        implied = current / previous - 1.0
        if not math.isclose(
            implied,
            float(evidence.returns[index]),
            rel_tol=1e-10,
            abs_tol=1e-12,
        ):
            raise AuthoritativeBacktestInputError(
                "risk returns and equity history are not internally aligned"
            )
        previous = current


def _direct_target_contract(alpha_definition: Any) -> _DirectTargetContract:
    if not isinstance(alpha_definition, Mapping):
        raise AuthoritativeBacktestInputError(
            "technical V2 alpha_definition must be a mapping"
        )
    mapping = alpha_definition.get("position_mapping")
    expected_fields = {
        "schema",
        "signal_field",
        "quantity_scale",
        "max_abs_quantity",
        "rounding",
    }
    if not isinstance(mapping, Mapping) or set(mapping) != expected_fields:
        raise AuthoritativeBacktestInputError(
            "alpha position_mapping must exactly declare the reviewed direct-target contract"
        )
    if mapping.get("schema") != DIRECT_LINEAR_TARGET_SCHEMA:
        raise AuthoritativeBacktestInputError("unsupported alpha position_mapping schema")
    signal_field = _nonempty(mapping.get("signal_field"), "signal_field")
    if alpha_definition.get("expression") != signal_field:
        raise AuthoritativeBacktestInputError(
            "technical V2 supports only a direct declared signal field expression"
        )
    if mapping.get("rounding") != "FLOOR_ABS_TO_CONTRACT_STEP":
        raise AuthoritativeBacktestInputError("unsupported target quantity rounding")
    scale = _decimal(mapping.get("quantity_scale"), "quantity_scale")
    maximum = _decimal(
        mapping.get("max_abs_quantity"), "max_abs_quantity", non_negative=True
    )
    if scale == ZERO or maximum == ZERO:
        raise AuthoritativeBacktestInputError(
            "quantity_scale and max_abs_quantity must be non-zero"
        )
    return _DirectTargetContract(signal_field, scale, maximum)


def _target_quantity_from_signal(
    decision: DecisionRequest,
    execution: ExecutionRequest,
    spec: ContractSpec,
    contract: _DirectTargetContract,
) -> Decimal:
    if len(decision.signal_inputs) != 1:
        raise AuthoritativeBacktestInputError(
            "technical V2 requires exactly one load-bearing target signal"
        )
    signal = decision.signal_inputs[0]
    value = signal.value
    if not isinstance(value, Mapping) or set(value) != {
        "instrument_id",
        contract.signal_field,
    }:
        raise AuthoritativeBacktestInputError(
            "target signal must exactly bind instrument_id and the declared signal field"
        )
    if value.get("instrument_id") != execution.instrument_id:
        raise AuthoritativeBacktestInputError(
            "target signal instrument does not match execution"
        )
    score = _decimal(value.get(contract.signal_field), contract.signal_field)
    raw_target = score * contract.quantity_scale
    clipped = max(-contract.max_abs_quantity, min(contract.max_abs_quantity, raw_target))
    floored = spec.floor_quantity(abs(clipped))
    return -floored if clipped < ZERO else floored


@ledger_decimal_context()
def research_manifest_inputs(*, release_identity, dataset_identity, universe_snapshot,
                             decisions, market_events, contract_rules):
    """Original typed manifest material, with the same native venue/cost rules.

    This projects supplied native objects; it does not validate a run or confer
    authority. The runner remains the caller that checks all causal semantics.
    """
    decision_items = tuple(decisions)
    market_items = tuple(market_events)
    rules_by_instrument = {item.spec.instrument_id: item for item in contract_rules}
    execution_events = [
        decision.execution.as_canonical_dict(
            rules_by_instrument[decision.execution.instrument_id].spec
        )
        for decision in decision_items
    ]
    data_identity = {
        "dataset_identity": dataset_identity,
        "universe_snapshot": universe_snapshot.as_canonical_dict(),
        "signal_inputs": [
            item.as_canonical_dict()
            for decision in decision_items
            for item in decision.signal_inputs
        ],
        "research_risk_inputs": [
            decision.research_risk.as_canonical_dict()
            for decision in decision_items
        ],
        "market_events": [item.as_canonical_dict() for item in market_items],
        "execution_observations": execution_events,
    }
    return {"release_identity": release_identity, "data_identity": data_identity,
            "universe": universe_snapshot.as_canonical_dict()}


@ledger_decimal_context()
def run_authoritative_linear_perpetual_v2(
    *,
    release_identity: Any,
    dataset_identity: Any,
    universe_snapshot: CausalDataRecord,
    alpha_definition: Any,
    initial_collateral: Decimal,
    contract_rules: Sequence[HistoricalContractRule],
    funding_policies: Sequence[FundingPolicySnapshot],
    market_events: Sequence[ObservedLedgerEvent],
    decisions: Sequence[DecisionRequest],
    parameters: Mapping[str, Any],
    random_seed: int,
) -> dict[str, Any]:
    """Run a causal V2 replay or raise without emitting authority evidence."""

    decision_items = tuple(decisions)
    _require_identity(release_identity, "release_identity")
    _require_identity(dataset_identity, "dataset_identity")
    _require_identity(alpha_definition, "alpha_definition")
    _require_identity(parameters, "parameters")
    target_contract = _direct_target_contract(alpha_definition)
    if not decision_items:
        raise AuthoritativeBacktestInputError("at least one decision is required")
    if any(not isinstance(item, DecisionRequest) for item in decision_items):
        raise AuthoritativeBacktestInputError("decisions contain an invalid item")
    decision_times = tuple(item.timestamp for item in decision_items)
    if decision_times != tuple(sorted(set(decision_times))):
        raise AuthoritativeBacktestInputError(
            "decision times must be strictly increasing and unique"
        )
    decision_ids = tuple(item.decision_id for item in decision_items)
    if len(set(decision_ids)) != len(decision_ids):
        raise AuthoritativeBacktestInputError("decision_id must be unique")

    if not isinstance(universe_snapshot, CausalDataRecord):
        raise AuthoritativeBacktestInputError("universe_snapshot is required")
    _ensure_causal_at_decision(
        ingestion_time=universe_snapshot.ingestion_time,
        decision_time=decision_items[0].timestamp,
        name="universe snapshot",
    )
    if not isinstance(universe_snapshot.value, Sequence) or isinstance(
        universe_snapshot.value, (str, bytes)
    ):
        raise AuthoritativeBacktestInputError(
            "universe snapshot value must be a sequence of instruments"
        )
    universe = tuple(str(value) for value in universe_snapshot.value)
    if not universe or universe != tuple(sorted(set(universe))):
        raise AuthoritativeBacktestInputError(
            "universe must be non-empty, unique, and sorted"
        )

    rule_items = tuple(contract_rules)
    if not rule_items or any(
        not isinstance(item, HistoricalContractRule) for item in rule_items
    ):
        raise AuthoritativeBacktestInputError(
            "contract rule evidence is required for every instrument"
        )
    rules_by_instrument = {item.spec.instrument_id: item for item in rule_items}
    if len(rules_by_instrument) != len(rule_items):
        raise AuthoritativeBacktestInputError(
            "V2 permits exactly one contract rule epoch per instrument"
        )
    if set(rules_by_instrument) != set(universe):
        raise AuthoritativeBacktestInputError(
            "contract rules must exactly cover the point-in-time universe"
        )
    rule_ids = {item.rule_id for item in rule_items}
    if len(rule_ids) != len(rule_items):
        raise AuthoritativeBacktestInputError("contract rule_id must be unique")

    policy_items = tuple(funding_policies)
    if not policy_items or any(
        not isinstance(item, FundingPolicySnapshot) for item in policy_items
    ):
        raise AuthoritativeBacktestInputError(
            "funding policy evidence is required for every instrument"
        )
    policies_by_instrument = {item.instrument_id: item for item in policy_items}
    if len(policies_by_instrument) != len(policy_items) or set(
        policies_by_instrument
    ) != set(universe):
        raise AuthoritativeBacktestInputError(
            "funding policies must exactly cover the point-in-time universe"
        )

    first_decision_time = decision_items[0].timestamp
    for rule in rule_items:
        _ensure_causal_at_decision(
            ingestion_time=rule.ingestion_time,
            decision_time=first_decision_time,
            name=f"contract rule {rule.rule_id}",
        )
    for policy in policy_items:
        _ensure_causal_at_decision(
            ingestion_time=policy.ingestion_time,
            decision_time=first_decision_time,
            name=f"funding policy {policy.instrument_id}",
        )
        if not policy.coverage_start <= first_decision_time <= policy.coverage_end:
            raise AuthoritativeBacktestInputError(
                f"funding policy does not cover first decision: {policy.instrument_id}"
            )

    market_items = tuple(market_events)
    if not market_items or any(
        not isinstance(item, ObservedLedgerEvent) for item in market_items
    ):
        raise AuthoritativeBacktestInputError("observed market events are required")
    actual_funding: dict[str, list[int]] = defaultdict(list)
    scheduled: dict[int, list[_ScheduledEvent]] = defaultdict(list)
    for observed in market_items:
        instrument_id = observed.event.instrument_id
        rule = rules_by_instrument.get(instrument_id)
        policy = policies_by_instrument.get(instrument_id)
        if rule is None or policy is None:
            raise AuthoritativeBacktestInputError(
                f"market event references instrument outside universe: {instrument_id}"
            )
        if observed.rule_id != rule.rule_id or not rule.covers(observed.event.timestamp):
            raise AuthoritativeBacktestInputError(
                f"market event lacks matching historical contract rule: {instrument_id}"
            )
        if not policy.coverage_start <= observed.event.timestamp <= policy.coverage_end:
            raise AuthoritativeBacktestInputError(
                f"market event lies outside funding policy coverage: {instrument_id}"
            )
        if isinstance(observed.event, FundingEvent):
            actual_funding[instrument_id].append(observed.event.timestamp)
        scheduled[observed.event.timestamp].append(
            _ScheduledEvent(
                event=observed.event,
                ingestion_time=observed.ingestion_time,
                source_identity=observed.source_identity,
            )
        )
    for instrument_id, policy in policies_by_instrument.items():
        if tuple(sorted(actual_funding.get(instrument_id, ()))) != policy.settlement_times:
            raise AuthoritativeBacktestInputError(
                f"funding schedule mismatch for {instrument_id}: every settlement is required"
            )

    execution_ids: set[str] = set()
    execution_times: list[int] = []
    execution_by_id: dict[str, ExecutionRequest] = {}
    capacity_window_definitions: dict[
        tuple[str, str, str, int], dict[str, Any]
    ] = {}
    capacity_window_bounds: dict[
        tuple[str, str], set[tuple[int, int]]
    ] = {}
    capacity_source_bindings: dict[
        tuple[str, str, str], tuple[int, int, str, int]
    ] = {}
    capacity_observation_end_bindings: dict[
        tuple[str, str, int], tuple[int, str, int]
    ] = {}

    def bind_capacity_snapshot(snapshot: CapacitySnapshot) -> None:
        policy = snapshot.policy
        capacity_key = (
            policy.venue_id,
            snapshot.instrument_id,
            policy.period_rule_id,
            policy.period_index,
        )
        capacity_definition = snapshot.as_canonical_dict()
        policy_definition = dict(capacity_definition["policy"])
        policy_definition.pop("window_id")
        capacity_definition = {
            **capacity_definition,
            "policy": policy_definition,
        }
        window_scope = (policy.venue_id, snapshot.instrument_id)
        window_bounds = (
            policy.consumption_window_start,
            policy.consumption_window_end,
        )
        for existing_start, existing_end in capacity_window_bounds.get(
            window_scope, set()
        ):
            if (existing_start, existing_end) == window_bounds:
                continue
            if max(existing_start, window_bounds[0]) < min(
                existing_end, window_bounds[1]
            ):
                raise AuthoritativeBacktestInputError(
                    "overlapping capacity consumption windows are not allowed"
                )
        capacity_window_bounds.setdefault(window_scope, set()).add(window_bounds)
        previous_capacity_definition = capacity_window_definitions.setdefault(
            capacity_key, capacity_definition
        )
        if previous_capacity_definition != capacity_definition:
            raise AuthoritativeBacktestInputError(
                "one venue capacity period has conflicting PIT definitions"
            )

        source_binding = (
            snapshot.adv_window_start,
            snapshot.adv_window_end,
            policy.period_rule_id,
            policy.period_index,
        )
        source_key = (
            policy.venue_id,
            snapshot.instrument_id,
            snapshot.source_identity,
        )
        previous_source_binding = capacity_source_bindings.setdefault(
            source_key, source_binding
        )
        if previous_source_binding != source_binding:
            raise AuthoritativeBacktestInputError(
                "one ADV source identity cannot reset multiple capacity periods"
            )

        observation_end_binding = (
            snapshot.adv_window_start,
            policy.period_rule_id,
            policy.period_index,
        )
        observation_end_key = (
            policy.venue_id,
            snapshot.instrument_id,
            snapshot.adv_window_end,
        )
        previous_end_binding = capacity_observation_end_bindings.setdefault(
            observation_end_key, observation_end_binding
        )
        if previous_end_binding != observation_end_binding:
            raise AuthoritativeBacktestInputError(
                "one ADV observation end cannot reset multiple capacity periods"
            )

    for index, decision in enumerate(decision_items):
        execution = decision.execution
        if execution.execution_id in execution_ids:
            raise AuthoritativeBacktestInputError("execution_id must be unique")
        execution_ids.add(execution.execution_id)
        if execution.timestamp <= decision.timestamp:
            raise AuthoritativeBacktestInputError(
                "execution event must be strictly after decision event"
            )
        opportunity = execution.opportunity
        if not opportunity.coverage_start <= decision.timestamp < opportunity.coverage_end:
            raise AuthoritativeBacktestInputError(
                "decision lies outside the complete execution-opportunity archive"
            )
        if not any(
            value <= decision.timestamp
            for value in opportunity.executable_event_times
        ):
            raise AuthoritativeBacktestInputError(
                "execution-opportunity archive lacks the event adjacent before decision"
            )
        next_executable = opportunity.next_after(decision.timestamp)
        if (
            next_executable.timestamp != execution.timestamp
            or next_executable.execution_price != execution.execution_price
        ):
            raise AuthoritativeBacktestInputError(
                "execution time/price does not match the first archived "
                "venue-executable event after decision"
            )
        if index + 1 < len(decision_items):
            next_decision = decision_items[index + 1].timestamp
            if execution.timestamp >= next_decision:
                raise AuthoritativeBacktestInputError(
                    "execution must complete before the next decision"
                )
            if execution.ingestion_time > next_decision:
                raise AuthoritativeBacktestInputError(
                    "execution acknowledgement was not ingested before the next decision"
                )
        rule = rules_by_instrument.get(execution.instrument_id)
        policy = policies_by_instrument.get(execution.instrument_id)
        if rule is None or policy is None:
            raise AuthoritativeBacktestInputError(
                "execution instrument is outside the point-in-time universe"
            )
        if execution.rule_id != rule.rule_id or not rule.covers(execution.timestamp):
            raise AuthoritativeBacktestInputError(
                "execution has no matching effective historical contract rule"
            )
        if not policy.coverage_start <= execution.timestamp <= policy.coverage_end:
            raise AuthoritativeBacktestInputError(
                "execution lies outside funding policy coverage"
            )
        if execution.capacity.policy.action != CapacityAction.REJECT:
            raise AuthoritativeBacktestInputError(
                "authoritative V2 supports reject-on-capacity only; partial fill is unreviewed"
            )
        if execution.capacity.policy.venue_id != policy.venue_id:
            raise AuthoritativeBacktestInputError(
                "capacity and funding policy venue identities differ"
            )
        bind_capacity_snapshot(execution.capacity)
        rule.spec.validate_quantity(
            execution.quantity_delta, execution.execution_price, reducing=True
        )
        requested_notional = rule.spec.notional(
            execution.quantity_delta, execution.execution_price
        )
        if requested_notional > execution.capacity.policy.maximum_notional:
            raise AuthoritativeBacktestInputError(
                f"capacity exceeded for {execution.instrument_id}"
            )
        trade_event = execution.build_trade_event(rule.spec)
        scheduled[execution.timestamp].append(
            _ScheduledEvent(
                event=trade_event,
                ingestion_time=execution.ingestion_time,
                source_identity=execution.source_identity,
                execution_id=execution.execution_id,
            )
        )
        execution_times.append(execution.timestamp)
        execution_by_id[execution.execution_id] = execution
    if execution_times != sorted(set(execution_times)):
        raise AuthoritativeBacktestInputError(
            "execution times must be strictly increasing and unique"
        )

    risk_config_hashes = {
        exact_identity_hash(
            _risk_config_dict(decision.research_risk.config), name="risk_config"
        )
        for decision in decision_items
    }
    if len(risk_config_hashes) != 1:
        raise AuthoritativeBacktestInputError(
            "deployment-equivalent risk config cannot change inside a run"
        )

    ledger = LinearPerpetualLedger(
        tuple(item.spec for item in rule_items),
        initial_collateral=_decimal(initial_collateral, "initial_collateral"),
    )
    pending_times = sorted(scheduled)
    pending_index = 0
    processed_times: set[int] = set()
    known_marks: dict[str, tuple[int, Decimal]] = {}
    last_execution_time: dict[str, int] = {}
    last_execution_price: dict[str, Decimal] = {}
    execution_results: list[dict[str, Any]] = []
    execution_risk_contexts: list[dict[str, Any]] = []
    execution_decisions = {
        decision.execution.execution_id: decision for decision in decision_items
    }

    def check_execution_risk(execution: ExecutionRequest) -> None:
        """Reprice permission at execution time, never inside an earlier decision.

        The legacy evidence history remains explicitly caller supplied. The
        current account and order exposure are computed from the replay ledger.
        """
        evidence = execution_decisions[execution.execution_id].research_risk
        positions = {name: ledger.position(name) for name in universe}
        quantities = {
            name: ZERO if position is None else position.quantity
            for name, position in positions.items()
        }
        current_quantities = dict(quantities)
        quantities[execution.instrument_id] += execution.quantity_delta
        names = {name for name, quantity in quantities.items() if quantity != ZERO}
        names.add(execution.instrument_id)
        prices: dict[str, Decimal] = {}
        for name in names:
            prices[name] = max(known_marks[name][1], last_execution_price.get(name, ZERO))
            if name == execution.instrument_id:
                prices[name] = max(prices[name], execution.execution_price)
        equity = ledger.account_equity()
        floored_equity = equity + sum((
            position.quantity * (prices[name] - position.mark_price)
            * rules_by_instrument[name].spec.contract_multiplier
            for name, position in positions.items()
            if position is not None and name in prices
        ), ZERO)
        risk_equity = min(equity, floored_equity)
        if risk_equity <= ZERO:
            raise AuthoritativeBacktestInputError("execution risk equity must be positive")
        weights = {
            name: float(quantities[name] * prices[name]
                        * rules_by_instrument[name].spec.contract_multiplier / risk_equity)
            for name in sorted(names)
        }
        capacity_flow = build_capacity_flow(
            current_quantities={name: current_quantities[name] for name in sorted(names)},
            target_quantities={name: quantities[name] for name in sorted(names)},
            valuation_prices=prices,
            contract_multipliers={name: rules_by_instrument[name].spec.contract_multiplier for name in sorted(names)},
            risk_equity=risk_equity,
        )
        result = evaluate_dry_run_risk(
            list(evidence.returns), weights, list(evidence.equity_curve),
            timestamps=list(evidence.timestamps), config=evidence.config,
            full_inputs=replace(evidence.full_inputs, total_aum=float(risk_equity)),
            require_full_inputs=True,
            capacity_flow_weights={name: float(value) for name, value in capacity_flow["capacity_flow_weights"].items()},
        )
        execution_risk_contexts.append({
            "execution_id": execution.execution_id,
            "execution_time_ms": execution.timestamp,
            "risk_equity": canonical_decimal(risk_equity),
            "target_weights": weights,
            "capacity_flow": capacity_flow,
            "research_risk": result,
        })
        if not full_research_risk_approved(result):
            raise AuthoritativeBacktestInputError(
                "nine-engine research risk rejected at execution: "
                + ",".join(str(value) for value in result.get("reasons", ()))
            )

    def process_timestamp(timestamp: int, *, visible_at: int | None) -> None:
        if timestamp in processed_times:
            return
        batch = scheduled[timestamp]
        if visible_at is not None:
            for item in batch:
                if item.ingestion_time > visible_at:
                    event_name = (
                        "market event"
                        if item.execution_id is None
                        else "execution event"
                    )
                    raise AuthoritativeBacktestInputError(
                        f"{event_name} at {timestamp} was not ingested by decision {visible_at}"
                    )
        # The ledger owns venue phase ordering and applies same-timestamp
        # settlement/mark batches atomically before any trade phase.
        def phase(item: _ScheduledEvent) -> int:
            if isinstance(item.event, FundingEvent):
                return 10
            if isinstance(item.event, MarkEvent):
                return 20
            return 30

        ordered = sorted(batch, key=phase)
        market_batch = [item for item in ordered if not isinstance(item.event, TradeEvent)]
        results = list(ledger.process_events(tuple(item.event for item in market_batch)))
        for item in market_batch:
            known_marks[item.event.instrument_id] = (
                item.event.timestamp, item.event.mark_price
            )
        for item in ordered[len(market_batch):]:
            check_execution_risk(execution_by_id[item.execution_id])
            results.append(ledger.apply_event(item.event))
        for item, result in zip(ordered, results, strict=True):
            if isinstance(item.event, FundingEvent):
                known_marks[item.event.instrument_id] = (
                    item.event.timestamp,
                    item.event.mark_price,
                )
            elif isinstance(item.event, MarkEvent):
                known_marks[item.event.instrument_id] = (
                    item.event.timestamp,
                    item.event.mark_price,
                )
            if item.execution_id is None:
                continue
            if not isinstance(result, ExecutionResult):
                raise AuthoritativeBacktestInputError(
                    "ledger did not return an execution result for a trade"
                )
            execution = execution_by_id[item.execution_id]
            if (
                result.status != "FILLED"
                or result.filled_quantity != execution.quantity_delta
            ):
                raise AuthoritativeBacktestInputError(
                    "authoritative run requires the capacity- and margin-cleared full fill"
                )
            execution_results.append(
                {
                    "execution_id": item.execution_id,
                    **result.as_canonical_dict(),
                }
            )
            last_execution_time[execution.instrument_id] = execution.timestamp
            last_execution_price[execution.instrument_id] = execution.execution_price
        processed_times.add(timestamp)

    decision_contexts: list[dict[str, Any]] = []
    risk_results: list[dict[str, Any]] = []
    weights_by_period: list[dict[str, Any]] = []
    prices_by_period: list[dict[str, Any]] = []
    for decision in decision_items:
        while pending_index < len(pending_times) and pending_times[pending_index] <= decision.timestamp:
            process_timestamp(pending_times[pending_index], visible_at=decision.timestamp)
            pending_index += 1

        for signal in decision.signal_inputs:
            _ensure_causal_at_decision(
                ingestion_time=signal.ingestion_time,
                decision_time=decision.timestamp,
                name=f"signal input {signal.record_id}",
            )
        execution = decision.execution
        rule = rules_by_instrument[execution.instrument_id]
        _ensure_causal_at_decision(
            ingestion_time=rule.ingestion_time,
            decision_time=decision.timestamp,
            name=f"contract rule {rule.rule_id}",
        )
        _ensure_causal_at_decision(
            ingestion_time=execution.capacity.ingestion_time,
            decision_time=decision.timestamp,
            name=f"capacity snapshot {execution.instrument_id}",
        )
        _ensure_causal_at_decision(
            ingestion_time=execution.cost_model.ingestion_time,
            decision_time=decision.timestamp,
            name=f"cost model {execution.cost_model.model_identity}",
        )
        _ensure_causal_at_decision(
            ingestion_time=decision.research_risk.ingestion_time,
            decision_time=decision.timestamp,
            name="research risk evidence",
        )
        for name, source_record in decision.research_risk.source_records.items():
            _ensure_causal_at_decision(
                ingestion_time=source_record.ingestion_time,
                decision_time=decision.timestamp,
                name=f"research risk source {name}",
            )

        capacities = {
            item.instrument_id: item for item in decision.capacity_snapshots
        }
        if len(capacities) != len(decision.capacity_snapshots):
            raise AuthoritativeBacktestInputError(
                "decision capacity snapshots contain duplicate instruments"
            )
        for item in capacities.values():
            bind_capacity_snapshot(item)
            _ensure_causal_at_decision(
                ingestion_time=item.ingestion_time,
                decision_time=decision.timestamp,
                name=f"capacity snapshot {item.instrument_id}",
            )
            if item.policy.action != CapacityAction.REJECT:
                raise AuthoritativeBacktestInputError(
                    "risk capacity must use the same load-bearing reject policy"
                )
        if capacities.get(execution.instrument_id) != execution.capacity:
            raise AuthoritativeBacktestInputError(
                "execution capacity differs from decision risk capacity"
            )

        current_positions = {
            instrument_id: ledger.position(instrument_id)
            for instrument_id in universe
        }
        current_execution_quantity = (
            ZERO
            if current_positions[execution.instrument_id] is None
            else current_positions[execution.instrument_id].quantity
        )
        signal_target_quantity = _target_quantity_from_signal(
            decision,
            execution,
            rule.spec,
            target_contract,
        )
        expected_quantity_delta = signal_target_quantity - current_execution_quantity
        if execution.quantity_delta != expected_quantity_delta:
            raise AuthoritativeBacktestInputError(
                "execution quantity does not move the actual position to the "
                "alpha-derived target"
            )
        prospective_quantities = {
            instrument_id: (
                ZERO if position is None else position.quantity
            )
            for instrument_id, position in current_positions.items()
        }
        prospective_quantities[execution.instrument_id] = signal_target_quantity
        risk_instruments = {
            instrument_id
            for instrument_id, quantity in prospective_quantities.items()
            if quantity != ZERO
        }
        # Keep the explicit closing target. Liquidity/capacity separately value
        # the original held book and actual order, including a zero final target.
        risk_instruments.add(execution.instrument_id)
        if set(capacities) != risk_instruments:
            raise AuthoritativeBacktestInputError(
                "capacity snapshots must exactly cover actual target positions"
            )

        equity = ledger.account_equity()
        if equity <= ZERO:
            raise AuthoritativeBacktestInputError(
                "actual ledger equity must be positive at decision"
            )
        decision_marks: dict[str, str] = {}
        risk_valuation_prices: dict[str, str] = {}
        risk_valuation_price_values: dict[str, Decimal] = {}
        for instrument_id in sorted(risk_instruments):
            mark = known_marks.get(instrument_id)
            if mark is None or mark[0] > decision.timestamp:
                raise AuthoritativeBacktestInputError(
                    f"causal decision mark is missing for {instrument_id}"
                )
            latest_execution = last_execution_time.get(instrument_id)
            if prospective_quantities[instrument_id] != ZERO:
                if latest_execution is not None and mark[0] <= latest_execution:
                    raise AuthoritativeBacktestInputError(
                        "strictly post-execution causal risk mark is missing for "
                        f"{instrument_id}"
                    )
                if decision.timestamp - mark[0] > RISK_MARK_MAX_AGE_MS:
                    raise AuthoritativeBacktestInputError(
                        f"causal risk mark exceeds freshness SLA for {instrument_id}"
                    )
            decision_marks[instrument_id] = canonical_decimal(mark[1])
            spec = rules_by_instrument[instrument_id].spec
            valuation_price = mark[1]
            confirmed_execution_price = last_execution_price.get(instrument_id)
            if confirmed_execution_price is not None:
                valuation_price = max(valuation_price, confirmed_execution_price)
            # Only already confirmed executions may influence this decision.
            # The new order is repriced separately when the replay reaches its
            # execution timestamp, before any account mutation for that fill.
            risk_valuation_prices[instrument_id] = canonical_decimal(valuation_price)
            risk_valuation_price_values[instrument_id] = valuation_price

        # A low mark is conservative for a long position but inflates equity for
        # a short position.  Applying confirmed-execution floors only to target
        # notionals would therefore still let a corrupted-low short mark enlarge
        # the denominator and hide a new concentration breach.  Revalue every
        # current position with the same reviewed valuation prices and take the
        # lower equity before computing any risk weight.
        floor_valued_equity = equity
        for instrument_id, position in current_positions.items():
            if position is None:
                continue
            valuation_price = risk_valuation_price_values[instrument_id]
            spec = rules_by_instrument[instrument_id].spec
            floor_valued_equity += (
                position.quantity
                * (valuation_price - position.mark_price)
                * spec.contract_multiplier
            )
        risk_equity = min(equity, floor_valued_equity)
        if risk_equity <= ZERO:
            raise AuthoritativeBacktestInputError(
                "conservative risk equity must be positive at decision"
            )
        target_weights_decimal: dict[str, Decimal] = {}
        for instrument_id in sorted(risk_instruments):
            target_weights_decimal[instrument_id] = (
                prospective_quantities[instrument_id]
                * risk_valuation_price_values[instrument_id]
                * rules_by_instrument[instrument_id].spec.contract_multiplier
                / risk_equity
            )

        evidence = decision.research_risk
        try:
            _validate_risk_source_bindings(evidence)
        except (TypeError, ValueError) as exc:
            raise AuthoritativeBacktestInputError(
                f"research risk source binding changed after construction: {exc}"
            ) from exc
        _validate_equity_history(evidence, equity)
        if evidence.full_inputs.timestamps is None or tuple(
            evidence.full_inputs.timestamps
        ) != evidence.timestamps:
            raise AuthoritativeBacktestInputError(
                "full risk timestamps must be explicit and aligned"
            )
        expected_capacity = {
            instrument_id: capacities[instrument_id].policy.maximum_notional
            for instrument_id in sorted(risk_instruments)
        }
        supplied_capacity = {
            str(key): Decimal(str(value))
            for key, value in evidence.full_inputs.capacity_cny.items()
        }
        if supplied_capacity != expected_capacity:
            raise AuthoritativeBacktestInputError(
                "research risk capacity does not match load-bearing execution capacity"
            )
        expected_daily_volume = sum(
            (
                capacities[instrument_id].policy.adv_notional
                for instrument_id in sorted(risk_instruments)
            ),
            ZERO,
        )
        if Decimal(str(evidence.full_inputs.daily_volume_cny)) != expected_daily_volume:
            raise AuthoritativeBacktestInputError(
                "research risk volume does not match PIT capacity snapshots"
            )
        if Decimal(str(evidence.full_inputs.total_aum)) != risk_equity:
            raise AuthoritativeBacktestInputError(
                "research risk total_aum does not match conservative risk equity"
            )
        target_weights = {
            key: float(value) for key, value in target_weights_decimal.items()
        }
        capacity_flow = build_capacity_flow(
            current_quantities={name: ZERO if current_positions[name] is None else current_positions[name].quantity
                for name in sorted(risk_instruments)},
            target_quantities={name: prospective_quantities[name] for name in sorted(risk_instruments)},
            valuation_prices=risk_valuation_price_values,
            contract_multipliers={name: rules_by_instrument[name].spec.contract_multiplier for name in sorted(risk_instruments)},
            risk_equity=risk_equity,
        )
        risk_result = evaluate_dry_run_risk(
            list(evidence.returns),
            target_weights,
            list(evidence.equity_curve),
            timestamps=list(evidence.timestamps),
            config=evidence.config,
            full_inputs=evidence.full_inputs,
            require_full_inputs=True,
            capacity_flow_weights={name: float(value) for name, value in capacity_flow["capacity_flow_weights"].items()},
        )
        if not full_research_risk_approved(risk_result):
            raise AuthoritativeBacktestInputError(
                "nine-engine research risk rejected the actual target context: "
                + ",".join(str(value) for value in risk_result.get("reasons", ()))
            )

        context = {
            "decision_id": decision.decision_id,
            "decision_time_ms": decision.timestamp,
            "execution_id": execution.execution_id,
            "pretrade_equity": canonical_decimal(equity),
            "floor_valued_equity": canonical_decimal(floor_valued_equity),
            "risk_equity": canonical_decimal(risk_equity),
            "pretrade_positions": _position_snapshot(ledger, universe),
            "decision_marks": decision_marks,
            "risk_valuation_prices": risk_valuation_prices,
            "last_confirmed_execution_times": {
                instrument_id: last_execution_time.get(instrument_id)
                for instrument_id in sorted(risk_instruments)
            },
            "last_confirmed_execution_prices": {
                instrument_id: (
                    None
                    if last_execution_price.get(instrument_id) is None
                    else canonical_decimal(last_execution_price[instrument_id])
                )
                for instrument_id in sorted(risk_instruments)
            },
            "alpha_target_quantity": canonical_decimal(signal_target_quantity),
            "target_quantities": {
                key: canonical_decimal(prospective_quantities[key])
                for key in sorted(risk_instruments)
            },
            "target_weights": {
                key: canonical_decimal(target_weights_decimal[key])
                for key in sorted(target_weights_decimal)
            },
            "capacity_flow": capacity_flow,
            "risk_input_hash": exact_identity_hash(
                evidence.as_canonical_dict(), name="research_risk_input"
            ),
            "research_risk": risk_result,
        }
        decision_contexts.append(context)
        risk_results.append(
            {
                "decision_id": decision.decision_id,
                "result": risk_result,
            }
        )
        weights_by_period.append(
            {
                "decision_time_ms": decision.timestamp,
                "weights": context["target_weights"],
            }
        )
        prices_by_period.append(
            {
                "decision_time_ms": decision.timestamp,
                "decision_marks": decision_marks,
                "execution_price": canonical_decimal(execution.execution_price),
            }
        )

    for timestamp in pending_times[pending_index:]:
        process_timestamp(timestamp, visible_at=None)

    for instrument_id, timestamp in last_execution_time.items():
        terminal_mark = known_marks.get(instrument_id)
        if terminal_mark is None or terminal_mark[0] <= timestamp:
            raise AuthoritativeBacktestInputError(
                f"a post-execution terminal mark is required for {instrument_id}"
            )

    reconciliation = ledger.reconciliation_snapshot()
    if reconciliation.residual != ZERO:
        raise AuthoritativeBacktestInputError(
            "complete perpetual cash-flow identity did not reconcile exactly"
        )

    decision_events = [
        {
            "decision_id": decision.decision_id,
            "decision_time_ms": decision.timestamp,
            "signal_inputs": [
                item.as_canonical_dict() for item in decision.signal_inputs
            ],
            "risk_input_hash": decision_contexts[index]["risk_input_hash"],
        }
        for index, decision in enumerate(decision_items)
    ]
    identity_inputs = research_manifest_inputs(
        release_identity=release_identity, dataset_identity=dataset_identity,
        universe_snapshot=universe_snapshot, decisions=decision_items,
        market_events=market_items, contract_rules=rule_items,
    )
    data_identity = identity_inputs["data_identity"]
    execution_events = data_identity["execution_observations"]
    manifest = build_run_manifest_v2(
        release_identity=release_identity,
        data_identity=data_identity,
        universe=universe_snapshot.as_canonical_dict(),
        alpha_definition=alpha_definition,
        periods=[
            {"decision_id": item.decision_id, "timestamp": item.timestamp}
            for item in decision_items
        ],
        decision_events=decision_events,
        execution_events=execution_events,
        weights_by_period=weights_by_period,
        prices_by_period=prices_by_period,
        cost_model=[item.execution.cost_model.as_canonical_dict() for item in decision_items],
        funding_policy=[item.as_canonical_dict() for item in policy_items],
        venue_rules=[item.as_canonical_dict() for item in rule_items],
        risk_limits=[
            {
                "decision_id": item.decision_id,
                "config": _risk_config_dict(item.research_risk.config),
            }
            for item in decision_items
        ],
        filters=[item.spec.as_canonical_dict() for item in rule_items],
        impact_model=[
            {
                "execution_id": item.execution.execution_id,
                "model_identity": item.execution.cost_model.model_identity,
                "impact_rate": canonical_decimal(item.execution.cost_model.impact_rate),
                "calibration_source": item.execution.cost_model.source_identity,
            }
            for item in decision_items
        ],
        adv_by_period=[
            {
                "decision_id": item.decision_id,
                "capacity": [
                    snapshot.as_canonical_dict()
                    for snapshot in item.capacity_snapshots
                ],
            }
            for item in decision_items
        ],
        parameters=dict(parameters),
        random_seed=random_seed,
    )

    result_hash_payload = {
        "schema_version": RESULT_HASH_SCHEMA_VERSION,
        "run_id": manifest.run_id,
        "engine_policy": {
            "risk_mark_policy_version": RISK_MARK_POLICY_VERSION,
            "risk_mark_max_age_ms": RISK_MARK_MAX_AGE_MS,
            "existing_position_price_floor": "LAST_CONFIRMED_EXECUTION_PRICE",
            "risk_equity_policy_version": RISK_EQUITY_POLICY_VERSION,
            "risk_equity_denominator": RISK_EQUITY_DENOMINATOR_POLICY,
            "risk_history_policy": RISK_HISTORY_POLICY,
            "capacity_flow_policy": CAPACITY_FLOW_POLICY,
        },
        "reconciliation": reconciliation.as_canonical_dict(),
        "ending_state": ledger.state_as_canonical_dict(),
        "ledger_events": [dict(item) for item in ledger.event_records],
        "liquidations": [
            item.as_canonical_dict() for item in ledger.liquidations
        ],
        "decision_contexts": decision_contexts,
        "research_risk_results": risk_results,
        "execution_risk_contexts": execution_risk_contexts,
        "execution_results": execution_results,
    }
    result_hash = exact_identity_hash(
        result_hash_payload, name="authoritative_backtest_result"
    )
    record: dict[str, Any] = {
        "run_id": manifest.run_id,
        "authoritative": False,
        "detail": {
            "run_manifest_v2": manifest.as_dict(),
            "manifest_hash": manifest.manifest_hash,
            "engine_semantics": CANONICAL_ENGINE_SEMANTICS,
            "evidence_status": TECHNICAL_VALIDATION_STATUS,
            "engine_attestations": {
                "causal_available_time": True,
                "strict_next_event_execution": True,
                "decimal_contracts": True,
                "settlement_event_funding": True,
                "historical_venue_rules": True,
                "margin_liquidation_state_machine": True,
                "complete_cashflow_identity": True,
                "deployment_equivalent_risk": False,
                "capacity_is_load_bearing": True,
                "full_research_risk_9_of_9": True,
                "risk_history_ledger_replayed": False,
            },
            "runner_scope": {
                "single_rule_epoch": True,
                "one_execution_per_decision": True,
                "alpha_target_mapping": DIRECT_LINEAR_TARGET_SCHEMA,
                "execution_price_source": "FIRST_ARCHIVED_EXECUTABLE_EVENT",
                "execution_price_basis": "DECLARED_PER_EXECUTION",
                "risk_valuation": (
                    "STRICT_POST_EXECUTION_CAUSAL_MARK_WITH_"
                    "CONFIRMED_EXECUTION_PRICE_FLOORS"
                ),
                "risk_mark_policy_version": RISK_MARK_POLICY_VERSION,
                "risk_mark_max_age_ms": RISK_MARK_MAX_AGE_MS,
                "risk_equity_policy_version": RISK_EQUITY_POLICY_VERSION,
                "risk_equity_denominator": RISK_EQUITY_DENOMINATOR_POLICY,
                "risk_history_policy": RISK_HISTORY_POLICY,
                "capacity_action": "REJECT",
                "decimal_context": DECIMAL_CONTEXT_VERSION,
                "live_or_broker_capability": False,
                "source_authenticity": "EXTERNAL_SIGNED_AUTHORITY_REQUIRED",
            },
            "authority_blockers": [
                "EXTERNAL_SIGNED_RAW_ARCHIVE_AUTHORITY_MISSING",
                "RISK_HISTORY_NOT_INDEPENDENTLY_LEDGER_REPLAYED",
            ],
            "research_risk": risk_results[-1]["result"],
            "research_risk_history": risk_results,
            "result_hash_payload": result_hash_payload,
            "result_hash": result_hash,
        },
    }
    if is_authoritative_backtest_record(record):
        raise AuthoritativeBacktestInputError(
            "technical validation record crossed the authority boundary"
        )
    return record


__all__ = [
    "AuthoritativeBacktestInputError",
    "CapacitySnapshot",
    "CausalDataRecord",
    "CostModelSnapshot",
    "DecisionRequest",
    "DIRECT_LINEAR_TARGET_SCHEMA",
    "ExecutableVenueEvent",
    "ExecutionRequest",
    "ExecutionOpportunitySnapshot",
    "FundingPolicySnapshot",
    "HistoricalContractRule",
    "ObservedLedgerEvent",
    "ResearchRiskEvidence",
    "RESULT_HASH_SCHEMA_VERSION",
    "TECHNICAL_VALIDATION_STATUS",
    "run_authoritative_linear_perpetual_v2",
]
