"""Pure Paper/Testnet decision parity and execution-quality evidence.

The report is diagnostic engineering evidence.  Binance Futures Testnet fills
are never represented as production microstructure evidence or as a real-money
mandate input.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from quantos.contracts.hashing import stable_hash
from quantos.contracts.trading import OrderSide


SCHEMA = "mft-parity.v1"
TESTNET_SCOPE = "TESTNET_MICROSTRUCTURE_NOT_PRODUCTION"
UNCALIBRATED_GATE = "UNCALIBRATED"


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")
    return value


def _time(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite(value: object, *, name: str, positive: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or (positive and float(value) <= 0)
    ):
        qualifier = "positive " if positive else ""
        raise ValueError(f"{name} must be a {qualifier}finite number")
    return float(value)


def _side(value: object) -> OrderSide:
    if not isinstance(value, OrderSide):
        raise TypeError("side must be an OrderSide")
    return value


@dataclass(frozen=True)
class PaperDecisionLeg:
    intent_id: str
    decision_id: str
    client_order_id: str
    decided_at_ms: int
    instrument_id: str
    side: OrderSide
    target_signed_quantity: float
    request_quantity: float
    decision_price: float

    def __post_init__(self) -> None:
        for name in ("intent_id", "decision_id", "client_order_id", "instrument_id"):
            _text(getattr(self, name), name=name)
        _time(self.decided_at_ms, name="decided_at_ms")
        _side(self.side)
        _finite(self.target_signed_quantity, name="target_signed_quantity")
        _finite(self.request_quantity, name="request_quantity", positive=True)
        _finite(self.decision_price, name="decision_price", positive=True)
        if (self.target_signed_quantity > 0) != (self.side is OrderSide.BUY):
            raise ValueError("target_signed_quantity direction must match side")

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.intent_id, self.decision_id, self.client_order_id

    def payload(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "decision_id": self.decision_id,
            "client_order_id": self.client_order_id,
            "decided_at_ms": self.decided_at_ms,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "target_signed_quantity": self.target_signed_quantity,
            "request_quantity": self.request_quantity,
            "decision_price": self.decision_price,
        }


@dataclass(frozen=True)
class TestnetFillObservation:
    fill_id: str
    quantity: float
    price: float
    filled_at_ms: int

    def __post_init__(self) -> None:
        _text(self.fill_id, name="fill_id")
        _finite(self.quantity, name="quantity", positive=True)
        _finite(self.price, name="price", positive=True)
        _time(self.filled_at_ms, name="filled_at_ms")

    def payload(self) -> dict[str, object]:
        return {
            "fill_id": self.fill_id,
            "quantity": self.quantity,
            "price": self.price,
            "filled_at_ms": self.filled_at_ms,
        }


@dataclass(frozen=True)
class TestnetOrderObservation:
    intent_id: str
    decision_id: str
    client_order_id: str
    decided_at_ms: int
    instrument_id: str
    side: OrderSide
    target_signed_quantity: float
    request_quantity: float
    submitted_at_ms: int
    order_status: str
    fills: tuple[TestnetFillObservation, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "intent_id",
            "decision_id",
            "client_order_id",
            "instrument_id",
            "order_status",
        ):
            _text(getattr(self, name), name=name)
        _time(self.decided_at_ms, name="decided_at_ms")
        _time(self.submitted_at_ms, name="submitted_at_ms")
        _side(self.side)
        _finite(self.target_signed_quantity, name="target_signed_quantity")
        _finite(self.request_quantity, name="request_quantity", positive=True)
        if (self.target_signed_quantity > 0) != (self.side is OrderSide.BUY):
            raise ValueError("target_signed_quantity direction must match side")
        if any(not isinstance(fill, TestnetFillObservation) for fill in self.fills):
            raise TypeError("fills must contain TestnetFillObservation values")
        fill_ids = tuple(fill.fill_id for fill in self.fills)
        if len(fill_ids) != len(set(fill_ids)):
            raise ValueError("fills must have unique fill_id values")
        object.__setattr__(self, "fills", tuple(sorted(self.fills, key=lambda item: item.fill_id)))

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.intent_id, self.decision_id, self.client_order_id

    def payload(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "decision_id": self.decision_id,
            "client_order_id": self.client_order_id,
            "decided_at_ms": self.decided_at_ms,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "target_signed_quantity": self.target_signed_quantity,
            "request_quantity": self.request_quantity,
            "submitted_at_ms": self.submitted_at_ms,
            "order_status": self.order_status,
            "fills": [fill.payload() for fill in self.fills],
        }


@dataclass(frozen=True)
class MftParityPolicy:
    policy_id: str
    policy_version: str
    max_target_quantity_diff: float
    max_request_quantity_diff: float
    min_fill_ratio: float
    max_abs_slippage_bps: float
    max_latency_ms: int
    max_position_drift: float
    max_abs_equity_drift: float

    def __post_init__(self) -> None:
        _text(self.policy_id, name="policy_id")
        _text(self.policy_version, name="policy_version")
        for name in (
            "max_target_quantity_diff",
            "max_request_quantity_diff",
            "max_abs_slippage_bps",
            "max_position_drift",
            "max_abs_equity_drift",
        ):
            value = _finite(getattr(self, name), name=name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        ratio = _finite(self.min_fill_ratio, name="min_fill_ratio")
        if not 0 <= ratio <= 1:
            raise ValueError("min_fill_ratio must be between zero and one")
        _time(self.max_latency_ms, name="max_latency_ms")

    def payload(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "max_target_quantity_diff": self.max_target_quantity_diff,
            "max_request_quantity_diff": self.max_request_quantity_diff,
            "min_fill_ratio": self.min_fill_ratio,
            "max_abs_slippage_bps": self.max_abs_slippage_bps,
            "max_latency_ms": self.max_latency_ms,
            "max_position_drift": self.max_position_drift,
            "max_abs_equity_drift": self.max_abs_equity_drift,
            "calibration": UNCALIBRATED_GATE,
        }

    @property
    def policy_hash(self) -> str:
        return stable_hash(self.payload())


def _positions(values: Mapping[str, float], *, name: str) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result: dict[str, float] = {}
    for instrument_id, quantity in values.items():
        result[_text(instrument_id, name=f"{name} instrument")] = _finite(
            quantity, name=f"{name} quantity"
        )
    return dict(sorted(result.items()))


def _optional_finite(value: float | None, *, name: str) -> float | None:
    return None if value is None else _finite(value, name=name)


def _index_unique(values: Sequence[Any], *, label: str) -> tuple[dict, bool]:
    index: dict[tuple[str, str, str], Any] = {}
    duplicate = False
    for value in values:
        if value.identity in index:
            duplicate = True
        else:
            index[value.identity] = value
    return index, duplicate


def _weighted_slippage(order: TestnetOrderObservation, decision_price: float) -> float | None:
    notionals = [fill.quantity * fill.price for fill in order.fills]
    denominator = sum(notionals)
    if denominator <= 0:
        return None
    direction = 1.0 if order.side is OrderSide.BUY else -1.0
    return sum(
        direction * (fill.price - decision_price) / decision_price * 10_000 * notional
        for fill, notional in zip(order.fills, notionals)
    ) / denominator


def build_mft_parity_report(
    *,
    canonical_run_id: str,
    paper_legs: Sequence[PaperDecisionLeg],
    testnet_orders: Sequence[TestnetOrderObservation],
    paper_positions: Mapping[str, float],
    testnet_positions: Mapping[str, float],
    paper_equity: float | None,
    testnet_equity: float | None,
    testnet_fee_total: float | None,
    testnet_funding_total: float | None,
    policy: MftParityPolicy,
    as_of_ms: int,
) -> dict[str, Any]:
    """Build a deterministic parity/TCA report from already captured facts."""

    canonical_run_id = _text(canonical_run_id, name="canonical_run_id")
    as_of_ms = _time(as_of_ms, name="as_of_ms")
    if not isinstance(policy, MftParityPolicy):
        raise TypeError("policy must be an MftParityPolicy")
    if any(not isinstance(item, PaperDecisionLeg) for item in paper_legs):
        raise TypeError("paper_legs must contain PaperDecisionLeg values")
    if not paper_legs:
        raise ValueError("paper_legs must not be empty")
    if any(not isinstance(item, TestnetOrderObservation) for item in testnet_orders):
        raise TypeError("testnet_orders must contain TestnetOrderObservation values")
    paper = tuple(sorted(
        paper_legs,
        key=lambda item: (item.identity, stable_hash(item.payload())),
    ))
    testnet = tuple(sorted(
        testnet_orders,
        key=lambda item: (item.identity, stable_hash(item.payload())),
    ))
    paper_index, duplicate_paper = _index_unique(paper, label="paper")
    testnet_index, duplicate_testnet = _index_unique(testnet, label="testnet")
    paper_position_values = _positions(paper_positions, name="paper_positions")
    testnet_position_values = _positions(testnet_positions, name="testnet_positions")
    paper_equity_value = _optional_finite(paper_equity, name="paper_equity")
    testnet_equity_value = _optional_finite(testnet_equity, name="testnet_equity")
    fee_total = _optional_finite(testnet_fee_total, name="testnet_fee_total")
    funding_total = _optional_finite(testnet_funding_total, name="testnet_funding_total")

    reasons: list[str] = []
    if duplicate_paper:
        reasons.append("DUPLICATE_PAPER_IDENTITY")
    if duplicate_testnet:
        reasons.append("DUPLICATE_TESTNET_IDENTITY")
    if len({item.client_order_id for item in paper}) != len(paper):
        reasons.append("DUPLICATE_PAPER_CLIENT_ORDER_ID")
    if len({item.client_order_id for item in testnet}) != len(testnet):
        reasons.append("DUPLICATE_TESTNET_CLIENT_ORDER_ID")
    fill_ids = [fill.fill_id for order in testnet for fill in order.fills]
    if len(fill_ids) != len(set(fill_ids)):
        reasons.append("DUPLICATE_TESTNET_FILL_ID")
    paper_keys = set(paper_index)
    testnet_keys = set(testnet_index)
    missing = sorted(paper_keys - testnet_keys)
    unexpected = sorted(testnet_keys - paper_keys)
    if missing:
        reasons.append("MISSING_TESTNET_ORDER")
    if unexpected:
        reasons.append("UNEXPECTED_TESTNET_ORDER")

    legs: list[dict[str, Any]] = []
    total_request = 0.0
    total_filled = 0.0
    aggregate_slippage_numerator = 0.0
    aggregate_slippage_notional = 0.0
    latencies: list[int] = []
    target_diffs: list[tuple[str, float]] = []
    request_diffs: list[tuple[str, float]] = []
    for item in paper:
        if item.decided_at_ms > as_of_ms:
            reasons.append(f"PAPER_DECISION_FROM_FUTURE:{item.client_order_id}")
        observed = testnet_index.get(item.identity)
        if observed is None:
            legs.append(
                {
                    **item.payload(),
                    "identity_status": "MISSING_TESTNET",
                    "decision_time_status": "UNKNOWN",
                    "testnet_order_status": None,
                    "target_quantity_diff": None,
                    "request_quantity_diff": None,
                    "filled_quantity": None,
                    "fill_ratio": None,
                    "slippage_bps": None,
                    "first_fill_latency_ms": None,
                }
            )
            continue
        if observed.decided_at_ms != item.decided_at_ms:
            reasons.append(f"DECISION_TIME_MISMATCH:{item.client_order_id}")
        if observed.decided_at_ms > as_of_ms:
            reasons.append(f"TESTNET_DECISION_FROM_FUTURE:{item.client_order_id}")
        if observed.instrument_id != item.instrument_id:
            reasons.append(f"INSTRUMENT_MISMATCH:{item.client_order_id}")
        if observed.side is not item.side:
            reasons.append(f"SIDE_MISMATCH:{item.client_order_id}")
        if observed.submitted_at_ms < item.decided_at_ms:
            reasons.append(f"SUBMISSION_TIME_REGRESSION:{item.client_order_id}")
        if observed.submitted_at_ms > as_of_ms:
            reasons.append(f"SUBMISSION_FROM_FUTURE:{item.client_order_id}")
        target_diff = observed.target_signed_quantity - item.target_signed_quantity
        request_diff = observed.request_quantity - item.request_quantity
        target_diffs.append((item.client_order_id, target_diff))
        request_diffs.append((item.client_order_id, request_diff))
        filled_quantity = sum(fill.quantity for fill in observed.fills)
        fill_ratio = filled_quantity / observed.request_quantity
        if filled_quantity > observed.request_quantity + 1e-12:
            reasons.append(f"OVERFILL:{item.client_order_id}")
        first_fill_latency = None
        if observed.fills:
            first_fill_at = min(fill.filled_at_ms for fill in observed.fills)
            has_regression = any(
                fill.filled_at_ms < item.decided_at_ms for fill in observed.fills
            )
            has_future = any(fill.filled_at_ms > as_of_ms for fill in observed.fills)
            if has_regression:
                reasons.append(f"FILL_TIME_REGRESSION:{item.client_order_id}")
            if has_future:
                reasons.append(f"FILL_FROM_FUTURE:{item.client_order_id}")
            if not has_regression and not has_future:
                first_fill_latency = first_fill_at - item.decided_at_ms
                latencies.append(first_fill_latency)
        slip = _weighted_slippage(observed, item.decision_price)
        if slip is not None:
            fill_notional = sum(fill.quantity * fill.price for fill in observed.fills)
            aggregate_slippage_numerator += slip * fill_notional
            aggregate_slippage_notional += fill_notional
        total_request += observed.request_quantity
        total_filled += filled_quantity
        legs.append(
            {
                **item.payload(),
                "identity_status": "EXACT_MATCH",
                "decision_time_status": (
                    "MATCH" if observed.decided_at_ms == item.decided_at_ms else "MISMATCH"
                ),
                "testnet_order_status": observed.order_status,
                "testnet_submitted_at_ms": observed.submitted_at_ms,
                "target_quantity_diff": target_diff,
                "request_quantity_diff": request_diff,
                "filled_quantity": filled_quantity,
                "fill_ratio": fill_ratio,
                "slippage_bps": slip,
                "first_fill_latency_ms": first_fill_latency,
            }
        )

    position_drift = {
        instrument_id: testnet_position_values.get(instrument_id, 0.0)
        - paper_position_values.get(instrument_id, 0.0)
        for instrument_id in sorted(set(paper_position_values) | set(testnet_position_values))
    }
    equity_drift = (
        None
        if paper_equity_value is None or testnet_equity_value is None
        else testnet_equity_value - paper_equity_value
    )
    aggregate_fill_ratio = total_filled / total_request if total_request > 0 else 0.0
    aggregate_slippage = (
        aggregate_slippage_numerator / aggregate_slippage_notional
        if aggregate_slippage_notional > 0
        else None
    )
    aggregate_latency = max(latencies) if latencies else None

    breaches: list[str] = []
    for client_order_id, difference in target_diffs:
        if abs(difference) > policy.max_target_quantity_diff:
            breaches.append(f"TARGET_QUANTITY_DIFF_BREACHED:{client_order_id}")
    for client_order_id, difference in request_diffs:
        if abs(difference) > policy.max_request_quantity_diff:
            breaches.append(f"REQUEST_QUANTITY_DIFF_BREACHED:{client_order_id}")
    if total_filled > 0 and aggregate_fill_ratio < policy.min_fill_ratio:
        breaches.append("MIN_FILL_RATIO_BREACHED")
    if (
        aggregate_slippage is not None
        and abs(aggregate_slippage) > policy.max_abs_slippage_bps
    ):
        breaches.append("SLIPPAGE_BREACHED")
    if aggregate_latency is not None and aggregate_latency > policy.max_latency_ms:
        breaches.append("LATENCY_BREACHED")
    for instrument_id, drift in position_drift.items():
        if abs(drift) > policy.max_position_drift:
            breaches.append(f"POSITION_DRIFT_BREACHED:{instrument_id}")
    if equity_drift is None:
        breaches.append("EQUITY_DRIFT_UNKNOWN")
    elif abs(equity_drift) > policy.max_abs_equity_drift:
        breaches.append("EQUITY_DRIFT_BREACHED")

    if total_filled == 0 and not (missing or unexpected or duplicate_paper or duplicate_testnet):
        reasons.append("NO_TESTNET_FILLS")
        status = "INSUFFICIENT_EVIDENCE"
        gate_verdict = "INSUFFICIENT_EVIDENCE"
        evidence_status = "INSUFFICIENT_EVIDENCE"
    elif reasons:
        status = "FAIL"
        gate_verdict = "FAIL"
        evidence_status = "SUFFICIENT_TESTNET_EXECUTION_EVIDENCE" if total_filled else "INSUFFICIENT_EVIDENCE"
    elif breaches:
        status = "FAIL"
        gate_verdict = "FAIL"
        evidence_status = "SUFFICIENT_TESTNET_EXECUTION_EVIDENCE"
    else:
        status = "PASS"
        gate_verdict = "PASS"
        evidence_status = "SUFFICIENT_TESTNET_EXECUTION_EVIDENCE"

    unknown_costs = []
    if fee_total is None:
        unknown_costs.append("FEES_UNKNOWN")
    if funding_total is None:
        unknown_costs.append("FUNDING_UNKNOWN")
    input_payload = {
        "canonical_run_id": canonical_run_id,
        "as_of_ms": as_of_ms,
        "paper_legs": [item.payload() for item in paper],
        "testnet_orders": [item.payload() for item in testnet],
        "paper_positions": paper_position_values,
        "testnet_positions": testnet_position_values,
        "paper_equity": paper_equity_value,
        "testnet_equity": testnet_equity_value,
        "testnet_fee_total": fee_total,
        "testnet_funding_total": funding_total,
        "policy": policy.payload(),
    }
    input_hash = stable_hash(input_payload)
    report_id = stable_hash(
        {
            "kind": SCHEMA,
            "canonical_run_id": canonical_run_id,
            "input_hash": input_hash,
            "policy_hash": policy.policy_hash,
        }
    )
    return {
        "schema": SCHEMA,
        "report_id": report_id,
        "canonical_run_id": canonical_run_id,
        "input_hash": input_hash,
        "input_manifest": input_payload,
        "as_of_ms": as_of_ms,
        "status": status,
        "evidence_status": evidence_status,
        "microstructure_scope": TESTNET_SCOPE,
        "identity": {
            "matched": len(paper_keys & testnet_keys),
            "missing_testnet": [list(identity) for identity in missing],
            "unexpected_testnet": [list(identity) for identity in unexpected],
        },
        "reasons": list(dict.fromkeys(reasons)),
        "legs": legs,
        "aggregate": {
            "paper_leg_count": len(paper),
            "testnet_order_count": len(testnet),
            "requested_quantity": total_request,
            "filled_quantity": total_filled,
            "fill_ratio": aggregate_fill_ratio,
            "slippage_bps": aggregate_slippage,
            "first_fill_latency_ms": aggregate_latency,
        },
        "cost_evidence": {
            "status": "INCOMPLETE" if unknown_costs else "COMPLETE",
            "testnet_fee_total": fee_total,
            "testnet_funding_total": funding_total,
            "unknown": unknown_costs,
        },
        "position_drift": position_drift,
        "equity_drift": equity_drift,
        "engineering_gate": {
            "verdict": gate_verdict,
            "calibration": UNCALIBRATED_GATE,
            "is_real_money_mandate": False,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_hash": policy.policy_hash,
            "thresholds": policy.payload(),
            "breaches": breaches,
        },
    }


__all__ = [
    "MftParityPolicy",
    "PaperDecisionLeg",
    "SCHEMA",
    "TESTNET_SCOPE",
    "TestnetFillObservation",
    "TestnetOrderObservation",
    "build_mft_parity_report",
]
