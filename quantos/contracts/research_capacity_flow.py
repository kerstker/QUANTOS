"""Offline capacity exposure from original held, target and order quantities.

This receipt is calculation lineage, never source authentication or authority.
Target portfolio weights remain separate inputs to the other seven risk engines.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context

SCHEMA = "QUANTOS_RESEARCH_CAPACITY_FLOW_V1"
POLICY = "MAX_CURRENT_TARGET_ORDER_NOTIONAL_V1"
MAX_INSTRUMENTS = 1000


def _decimal(value: object, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("capacity flow requires original Decimal values")
    if not value.is_finite() or (positive and value <= 0):
        raise ValueError("capacity flow requires finite quantities and positive valuation")
    # Bound receipt rendering before formatting an attacker-controlled exponent.
    if len(value.as_tuple().digits) > 1000 or abs(value.adjusted()) > 1000:
        raise ValueError("capacity flow Decimal exceeds receipt bounds")
    return value


def build_capacity_flow(*, current_quantities: Mapping[str, Decimal],
                        target_quantities: Mapping[str, Decimal],
                        valuation_prices: Mapping[str, Decimal],
                        contract_multipliers: Mapping[str, Decimal],
                        risk_equity: Decimal) -> dict:
    """Value actual order turnover and both books on the same causal price axis."""
    books = (current_quantities, target_quantities, valuation_prices, contract_multipliers)
    if any(not isinstance(book, Mapping) for book in books):
        raise TypeError("capacity flow books must be mappings")
    keys = set(current_quantities)
    if not 0 < len(keys) <= MAX_INSTRUMENTS or any(type(key) is not str or not key.strip() for key in keys):
        raise ValueError("capacity flow instruments are missing or invalid")
    if any(set(book) != keys for book in books):
        raise ValueError("capacity flow books must cover the same instruments")
    equity = _decimal(risk_equity, positive=True)
    orders, notionals, weights = {}, {}, {}
    with ledger_decimal_context():
        for key in sorted(keys):
            current = _decimal(current_quantities[key])
            target = _decimal(target_quantities[key])
            price = _decimal(valuation_prices[key], positive=True)
            multiplier = _decimal(contract_multipliers[key], positive=True)
            order = target - current
            notional = max(abs(current), abs(target), abs(order)) * price * multiplier
            orders[key] = canonical_decimal(order)
            notionals[key] = canonical_decimal(notional)
            weights[key] = canonical_decimal(notional / equity)
    value = dict(schema=SCHEMA, policy=POLICY, risk_equity=canonical_decimal(equity),
        current_quantities={key: canonical_decimal(current_quantities[key]) for key in sorted(keys)},
        target_quantities={key: canonical_decimal(target_quantities[key]) for key in sorted(keys)},
        valuation_prices={key: canonical_decimal(valuation_prices[key]) for key in sorted(keys)},
        contract_multipliers={key: canonical_decimal(contract_multipliers[key]) for key in sorted(keys)},
        order_quantities=orders, capacity_notionals=notionals, capacity_flow_weights=weights)
    value["receipt_sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest()
    return value


def validated_capacity_weights(target_weights, flow_weights):
    """A supplied flow cannot omit a leg or reduce its target notional.

    None retains the original book-only API. Invalid supplied input raises so
    both original engines reject; it never falls back to the smaller target.
    """
    if flow_weights is None:
        return target_weights
    if not isinstance(target_weights, Mapping) or not isinstance(flow_weights, Mapping):
        raise TypeError("capacity flow weights must be mappings")
    if not 0 < len(target_weights) <= MAX_INSTRUMENTS or set(flow_weights) != set(target_weights):
        raise ValueError("capacity flow weights must exactly cover target instruments")
    result = {}
    for key, target in target_weights.items():
        flow = flow_weights[key]
        if type(key) is not str or not key.strip():
            raise ValueError("capacity flow instrument is invalid")
        if any(type(value) not in (int, float) for value in (target, flow)):
            raise TypeError("capacity flow weights must be finite numbers")
        target, flow = float(target), float(flow)
        if not math.isfinite(target) or not math.isfinite(flow) or flow < abs(target):
            raise ValueError("capacity flow cannot understate target exposure")
        result[key] = flow
    gross = sum(result.values())
    if not math.isfinite(gross) or gross <= 0:
        raise ValueError("capacity flow requires a nonzero actual book or order")
    return result
