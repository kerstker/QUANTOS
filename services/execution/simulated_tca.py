"""Deterministic route-level TCA for simulation-only SOR plans.

The module compares an accepted :class:`SimulatedRoutePlan` with the fills
returned by the dry-run adapter.  It performs no I/O and has no execution
capability.  Persisted records include a canonical SHA-256 digest so readers
can independently reject malformed or arithmetically inconsistent snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
from typing import Mapping, Sequence

from quantos.contracts.trading import OrderSide
from services.execution.simulated_sor import (
    RouteStatus,
    SimulatedRoutePlan,
)


_BPS = Decimal("10000")
_ZERO = Decimal("0")
_ARITHMETIC_TOLERANCE = Decimal("1e-24")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SimulatedTCAError(ValueError):
    """A route-TCA input or retained snapshot is invalid."""


@dataclass(frozen=True)
class RouteExecution:
    """One successfully simulated child fill bound to its planned slice."""

    slice_index: int
    venue_id: str
    quantity: int
    fill_price: Decimal

    def __post_init__(self) -> None:
        if type(self.slice_index) is not int or self.slice_index < 0:
            raise SimulatedTCAError("slice_index must be a non-negative integer")
        if not isinstance(self.venue_id, str) or not self.venue_id.strip():
            raise SimulatedTCAError("venue_id must be non-empty")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise SimulatedTCAError("quantity must be a positive integer")
        if (
            not isinstance(self.fill_price, Decimal)
            or not self.fill_price.is_finite()
            or self.fill_price <= 0
        ):
            raise SimulatedTCAError("fill_price must be a finite positive Decimal")


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _arithmetically_equal(left: Decimal, right: Decimal) -> bool:
    """Compare derived decimal ratios within the active 28-digit context."""
    scale = max(abs(left), abs(right), Decimal("1"))
    return abs(left - right) <= _ARITHMETIC_TOLERANCE * scale


def _decimal(name: str, value: object, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise SimulatedTCAError(f"{name} must be a decimal string")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise SimulatedTCAError(f"{name} must be a decimal string") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise SimulatedTCAError(f"{name} is outside its valid domain")
    return parsed


def _ratio(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) / Decimal(denominator) if denominator else _ZERO


def _vwap(notional: Decimal, quantity: int) -> Decimal:
    return notional / Decimal(quantity) if quantity else _ZERO


def build_route_tca(
    plan: SimulatedRoutePlan,
    *,
    reference_price: Decimal,
    executions: Sequence[RouteExecution],
) -> dict:
    """Build a canonical simulation-only route TCA record.

    Executions may be a subset of planned slices when the dry-run adapter
    rejects a child.  Every execution must bind exactly one unique plan slice;
    overfills, cross-venue substitutions and duplicate slice claims fail.
    """

    if not isinstance(plan, SimulatedRoutePlan):
        raise SimulatedTCAError("plan must be a SimulatedRoutePlan")
    if plan.simulation_only is not True:
        raise SimulatedTCAError("route TCA accepts simulation-only plans")
    if (
        not isinstance(reference_price, Decimal)
        or not reference_price.is_finite()
        or reference_price <= 0
    ):
        raise SimulatedTCAError("reference_price must be a finite positive Decimal")
    materialized = tuple(executions)
    if any(not isinstance(item, RouteExecution) for item in materialized):
        raise SimulatedTCAError("executions must contain RouteExecution values")
    indices = [item.slice_index for item in materialized]
    if len(indices) != len(set(indices)):
        raise SimulatedTCAError("a planned slice cannot be executed twice")

    realized_raw_notional = _ZERO
    realized_route_cost = _ZERO
    filled_quantity = 0
    by_venue: dict[str, dict[str, object]] = {}
    for execution in sorted(materialized, key=lambda item: item.slice_index):
        if execution.slice_index >= len(plan.slices):
            raise SimulatedTCAError("execution references an unknown route slice")
        planned = plan.slices[execution.slice_index]
        if (
            execution.venue_id != planned.venue_id
            or execution.quantity != planned.quantity
        ):
            raise SimulatedTCAError("execution does not match its planned route slice")
        raw = execution.fill_price * execution.quantity
        route_cost = planned.fee_amount + planned.latency_penalty_amount
        realized_raw_notional += raw
        realized_route_cost += route_cost
        filled_quantity += execution.quantity
        venue = by_venue.setdefault(
            execution.venue_id,
            {"filled_quantity": 0, "raw_notional": _ZERO, "route_cost": _ZERO},
        )
        venue["filled_quantity"] = int(venue["filled_quantity"]) + execution.quantity
        venue["raw_notional"] = Decimal(venue["raw_notional"]) + raw
        venue["route_cost"] = Decimal(venue["route_cost"]) + route_cost

    if filled_quantity > plan.routed_quantity:
        raise SimulatedTCAError("executions exceed routed quantity")
    realized_all_in_notional = (
        realized_raw_notional + realized_route_cost
        if plan.side == OrderSide.BUY
        else realized_raw_notional - realized_route_cost
    )
    reference_notional = reference_price * filled_quantity
    signed_shortfall = (
        realized_all_in_notional - reference_notional
        if plan.side == OrderSide.BUY
        else reference_notional - realized_all_in_notional
    )
    shortfall_bps = (
        signed_shortfall / reference_notional * _BPS
        if reference_notional
        else _ZERO
    )
    payload = {
        "schema_version": 1,
        "simulation_only": True,
        "instrument_id": plan.instrument_id,
        "side": plan.side.value,
        "route_status": plan.status.value,
        "requested_quantity": plan.requested_quantity,
        "routed_quantity": plan.routed_quantity,
        "filled_quantity": filled_quantity,
        "routing_residual_quantity": plan.residual_quantity,
        "execution_residual_quantity": plan.requested_quantity - filled_quantity,
        "fill_fraction": str(_ratio(filled_quantity, plan.requested_quantity)),
        "reference_price": str(reference_price),
        "expected_raw_vwap": str(
            _vwap(plan.expected_raw_notional, plan.routed_quantity)
        ),
        "expected_all_in_vwap": str(
            _vwap(plan.expected_all_in_notional, plan.routed_quantity)
        ),
        "realized_raw_vwap": str(_vwap(realized_raw_notional, filled_quantity)),
        "realized_all_in_vwap": str(
            _vwap(realized_all_in_notional, filled_quantity)
        ),
        "expected_route_cost": str(
            plan.expected_fees + plan.expected_latency_penalty
        ),
        "realized_route_cost": str(realized_route_cost),
        "implementation_shortfall_bps": str(shortfall_bps),
        "venues": [
            {
                "venue_id": venue_id,
                "filled_quantity": int(values["filled_quantity"]),
                "raw_notional": str(values["raw_notional"]),
                "route_cost": str(values["route_cost"]),
            }
            for venue_id, values in sorted(by_venue.items())
        ],
    }
    return {**payload, "record_sha256": _digest(payload)}


def verify_route_tca_record(record: object) -> bool:
    """Return ``True`` only for a canonical, internally reconciled TCA record."""

    try:
        if not isinstance(record, Mapping):
            return False
        expected_keys = {
            "schema_version",
            "simulation_only",
            "instrument_id",
            "side",
            "route_status",
            "requested_quantity",
            "routed_quantity",
            "filled_quantity",
            "routing_residual_quantity",
            "execution_residual_quantity",
            "fill_fraction",
            "reference_price",
            "expected_raw_vwap",
            "expected_all_in_vwap",
            "realized_raw_vwap",
            "realized_all_in_vwap",
            "expected_route_cost",
            "realized_route_cost",
            "implementation_shortfall_bps",
            "venues",
            "record_sha256",
        }
        if set(record) != expected_keys:
            return False
        digest = record["record_sha256"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            return False
        payload = {key: record[key] for key in record if key != "record_sha256"}
        if _digest(payload) != digest:
            return False
        if (
            record["schema_version"] != 1
            or record["simulation_only"] is not True
            or not isinstance(record["instrument_id"], str)
            or not record["instrument_id"]
            or record["side"] not in {side.value for side in OrderSide}
            or record["route_status"] not in {status.value for status in RouteStatus}
        ):
            return False
        quantities = [
            record["requested_quantity"],
            record["routed_quantity"],
            record["filled_quantity"],
            record["routing_residual_quantity"],
            record["execution_residual_quantity"],
        ]
        if any(type(value) is not int or value < 0 for value in quantities):
            return False
        requested, routed, filled, routing_residual, execution_residual = quantities
        if (
            requested <= 0
            or routed + routing_residual != requested
            or filled > routed
            or filled + execution_residual != requested
        ):
            return False
        fill_fraction = _decimal("fill_fraction", record["fill_fraction"])
        reference = _decimal("reference_price", record["reference_price"], positive=True)
        expected_raw_vwap = _decimal("expected_raw_vwap", record["expected_raw_vwap"])
        expected_all_in_vwap = _decimal(
            "expected_all_in_vwap", record["expected_all_in_vwap"]
        )
        realized_raw_vwap = _decimal("realized_raw_vwap", record["realized_raw_vwap"])
        realized_all_in_vwap = _decimal(
            "realized_all_in_vwap", record["realized_all_in_vwap"]
        )
        expected_cost = _decimal(
            "expected_route_cost", record["expected_route_cost"]
        )
        realized_cost = _decimal(
            "realized_route_cost", record["realized_route_cost"]
        )
        shortfall = _decimal(
            "implementation_shortfall_bps",
            record["implementation_shortfall_bps"],
        )
        if (
            fill_fraction != _ratio(filled, requested)
            or min(
                expected_raw_vwap,
                expected_all_in_vwap,
                realized_raw_vwap,
                realized_all_in_vwap,
                expected_cost,
                realized_cost,
            )
            < 0
        ):
            return False
        venues = record["venues"]
        if not isinstance(venues, list):
            return False
        venue_ids: list[str] = []
        venue_quantity = 0
        venue_raw = _ZERO
        venue_cost = _ZERO
        for venue in venues:
            if not isinstance(venue, Mapping) or set(venue) != {
                "venue_id",
                "filled_quantity",
                "raw_notional",
                "route_cost",
            }:
                return False
            venue_id = venue["venue_id"]
            quantity = venue["filled_quantity"]
            if (
                not isinstance(venue_id, str)
                or not venue_id
                or type(quantity) is not int
                or quantity <= 0
            ):
                return False
            venue_ids.append(venue_id)
            venue_quantity += quantity
            venue_raw += _decimal("raw_notional", venue["raw_notional"])
            venue_cost += _decimal("route_cost", venue["route_cost"])
        if venue_ids != sorted(set(venue_ids)) or venue_quantity != filled:
            return False
        if (
            not _arithmetically_equal(venue_raw, realized_raw_vwap * filled)
            or venue_cost != realized_cost
        ):
            return False
        realized_all_in = realized_all_in_vwap * filled
        expected_all_in = (
            venue_raw + realized_cost
            if record["side"] == OrderSide.BUY.value
            else venue_raw - realized_cost
        )
        if not _arithmetically_equal(realized_all_in, expected_all_in):
            return False
        reference_notional = reference * filled
        expected_shortfall = (
            (
                realized_all_in - reference_notional
                if record["side"] == OrderSide.BUY.value
                else reference_notional - realized_all_in
            )
            / reference_notional
            * _BPS
            if reference_notional
            else _ZERO
        )
        return _arithmetically_equal(shortfall, expected_shortfall)
    except (ArithmeticError, SimulatedTCAError, TypeError, ValueError):
        return False


__all__ = [
    "RouteExecution",
    "SimulatedTCAError",
    "build_route_tca",
    "verify_route_tca_record",
]
