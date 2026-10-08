"""Bounded, exact JSON transport for the fixed offline gold cost-replay inputs.

Only the original engine's listed value types are constructible. There is no
pickle, dynamic import, callable, path resolution or network access in decoding.
This transport does not authenticate the declared market/rule evidence.
"""
from dataclasses import fields
from decimal import Decimal
import json

from quantos.contracts.gold_cost_input_limits import MAX_BYTES
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.execution.authoritative_perpetual_backtest import (
    CapacitySnapshot, FundingPolicySnapshot, HistoricalContractRule,
)
from services.execution.perpetual_ledger_v2 import (
    CapacityAction, CapacityPolicy, ContractSpec, FundingEvent, MarginBracket, MarkEvent,
)
from services.execution.perpetual_simulation import (
    ExecutableQuote, OrderRuleProfile, SimulationCostModel, SimulationFrame,
    SimulationLimits, VenueOrderConstraints,
)
from services.execution.venue_order_filters import PriceFilter, QuantityFilter, VenueOrderFilters

MAX_NODES = 500_000
CLASSES = {c.__name__: c for c in (
    CapacitySnapshot, FundingPolicySnapshot, HistoricalContractRule, CapacityPolicy,
    ContractSpec, FundingEvent, MarginBracket, MarkEvent, ExecutableQuote,
    OrderRuleProfile, SimulationCostModel, SimulationFrame, SimulationLimits,
    VenueOrderConstraints, PriceFilter, QuantityFilter, VenueOrderFilters,
)}
REQUIRED = {'document', 'plan', 'release_identity', 'market_identity', 'contract_rules',
            'funding_policies', 'frames', 'initial_collateral', 'costs'}
OPTIONAL = {'order_rule_profiles', 'limits'}


def _arguments(value):
    if type(value) is not dict or not REQUIRED <= value.keys() or value.keys() - REQUIRED - OPTIONAL:
        raise ValueError('GOLD_COST_ARGUMENTS_INVALID')
    return value


def _decimal(value):
    if type(value) is not str or len(value) > 100:
        raise ValueError('GOLD_COST_DECIMAL_INVALID')
    result = Decimal(value)
    if not result.is_finite() or abs(result.as_tuple().exponent) > 100:
        raise ValueError('GOLD_COST_DECIMAL_INVALID')
    return result


def _budget(counter, depth):
    counter[0] += 1
    if depth > 32 or counter[0] > MAX_NODES:
        raise ValueError('GOLD_COST_STRUCTURE_LIMIT')


def _encode(value, counter, depth=0):
    _budget(counter, depth)
    def child(v):
        return _encode(v, counter, depth+1)
    kind = type(value)
    if value is None:
        return ['none']
    if kind in (str, int, bool):
        return [kind.__name__, value]
    if kind is Decimal:
        _decimal(str(value))
        return ['decimal', str(value)]
    if kind is CapacityAction:
        return ['capacity_action', value.value]
    if kind in (list, tuple):
        return [kind.__name__, [child(v) for v in value]]
    if kind is dict:
        if any(type(k) is not str for k in value):
            raise ValueError('GOLD_COST_STRING_KEYS_REQUIRED')
        return ['dict', {k: child(v) for k, v in value.items()}]
    if CLASSES.get(kind.__name__) is kind:
        return ['object', kind.__name__, {f.name: child(getattr(value, f.name)) for f in fields(value)}]
    raise ValueError('GOLD_COST_UNSUPPORTED_VALUE')


def _decode(node, counter, depth=0):
    _budget(counter, depth)
    def child(v):
        return _decode(v, counter, depth+1)
    if type(node) is not list or not node or type(node[0]) is not str:
        raise ValueError('GOLD_COST_TAG_INVALID')
    tag = node[0]
    if tag == 'none' and len(node) == 1:
        return None
    if tag == 'object' and len(node) == 3:
        name, body = node[1:]
        cls = CLASSES.get(name) if type(name) is str else None
        if cls is None or type(body) is not dict or set(body) != {f.name for f in fields(cls)}:
            raise ValueError('GOLD_COST_OBJECT_INVALID')
        return cls(**{k: child(v) for k, v in body.items()})
    if len(node) != 2:
        raise ValueError('GOLD_COST_TAG_INVALID')
    value = node[1]
    primitives = {'str': str, 'int': int, 'bool': bool}
    if tag in primitives and type(value) is primitives[tag]:
        return value
    if tag == 'decimal':
        return _decimal(value)
    if tag == 'capacity_action' and type(value) is str:
        return CapacityAction(value)
    if tag in ('list', 'tuple') and type(value) is list:
        values = [child(v) for v in value]
        return tuple(values) if tag == 'tuple' else values
    if tag == 'dict' and type(value) is dict:
        return {k: child(v) for k, v in value.items()}
    raise ValueError('GOLD_COST_TAG_INVALID')


def dump_inputs(arguments):
    body = {'schema': 'GOLD_COST_INPUT_V1', 'arguments': _encode(_arguments(arguments), [0])}
    raw = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('GOLD_COST_INPUT_TOO_LARGE')
    return raw


def load_inputs(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('GOLD_COST_INPUT_SIZE')
    try:
        body = load_json_without_duplicate_keys(raw)
        if (type(body) is not dict or set(body) != {'schema', 'arguments'}
                or body['schema'] != 'GOLD_COST_INPUT_V1'):
            raise ValueError('GOLD_COST_INPUT_SCHEMA')
        return _arguments(_decode(body['arguments'], [0]))
    except (RecursionError, TypeError, KeyError, ArithmeticError) as exc:
        raise ValueError('GOLD_COST_INPUT_INVALID') from exc
