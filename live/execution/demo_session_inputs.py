"""Strict external data decoding, never approval issuance or default risk caps.

All existing risk limits and mandate fields must be supplied. Fractional native
quantity caps retain their original units. Labels are declarations; the original
preparation boundary still requires independent signature verification.
"""
from dataclasses import fields
import math
import json

from live.execution.demo_batch_risk import _fields, _scaled_limits
from live.governance.mandate import Mandate
from live.governance.validation_attestation import ValidationAttestation
from quantos.contracts.killswitch import KillSwitchState
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import StrategyApproval
from services.risk.pre_trade.kill_switch import KillSwitch

MAX_INPUT_BYTES = 16_000_000


def _text(value):
    if type(value) is not str or not value or len(value) > 4096:
        raise ValueError('SESSION_TEXT')
    return value


def _number(value, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('SESSION_FINITE_NONNEGATIVE_NUMBER')
    return value


def _names(value):
    if type(value) is not list or len(value) > 2000:
        raise ValueError('SESSION_NAME_LIST')
    names = tuple(_text(v) for v in value)
    if len(set(names)) != len(names):
        raise ValueError('SESSION_DUPLICATE_NAME')
    return names


def _pairs(value):
    if type(value) is not list or len(value) > 2000:
        raise ValueError('SESSION_CAP_LIST')
    result = []
    for row in value:
        if type(row) is not list or len(row) != 2:
            raise ValueError('SESSION_CAP_PAIR')
        result.append((_text(row[0]), _number(row[1])))
    if len({r[0] for r in result}) != len(result):
        raise ValueError('SESSION_DUPLICATE_CAP')
    return tuple(result)


def _limits(value):
    _fields(value, ' '.join(f.name for f in fields(RiskLimits)))
    parsed = dict(value)
    for key in ('allowed_instruments', 'allowed_accounts'):
        parsed[key] = frozenset(_names(value[key]))
    parsed['max_position_qty'] = _pairs(value['max_position_qty'])
    for key in set(parsed) - {'allowed_instruments', 'allowed_accounts', 'max_position_qty'}:
        parsed[key] = _number(value[key], integer=key in {
            'max_orders_per_window', 'rate_window_ms', 'min_cancel_rate_samples'})
    result = RiskLimits(**parsed)
    _scaled_limits(result)
    return result


def _mandate(value):
    _fields(value, ' '.join(f.name for f in fields(Mandate)))
    parsed = dict(value)
    for key in ('mandate_id', 'venue', 'exchange'):
        parsed[key] = _text(value[key])
    parsed['allowed_instruments'] = frozenset(_names(value['allowed_instruments']))
    parsed['approved_by'] = _names(value['approved_by'])
    parsed['per_instrument_max_notional'] = _pairs(value['per_instrument_max_notional'])
    for key in ('max_single_order_notional', 'max_gross_notional', 'max_net_notional', 'issued_at', 'expires_at'):
        parsed[key] = _number(value[key], integer=key in {'issued_at', 'expires_at'})
    return Mandate(**parsed)


def decode(raw, *, now_ms):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT_BYTES:
        raise ValueError('SESSION_INPUT_BUDGET')
    _number(now_ms, integer=True)
    data = load_json_without_duplicate_keys(raw)
    delegated = type(data) is dict and data.get('schema') == 'DEMO_GOLD_DELEGATED_SESSION_INPUT_V1'
    constrained = delegated or type(data) is dict and data.get('schema') == 'DEMO_GOLD_STRATEGY_SESSION_INPUT_V1'
    _fields(data, 'schema orders origin risk mandate attestations'+(' execution_constraints' if constrained else '')
        + (' delegated_admission' if delegated else ''))
    if data['schema'] not in ('DEMO_EXECUTION_SESSION_INPUT_V1','DEMO_GOLD_STRATEGY_SESSION_INPUT_V1',
            'DEMO_GOLD_DELEGATED_SESSION_INPUT_V1'):
        raise ValueError('SESSION_INPUT_SCHEMA')
    risk = data['risk']
    _fields(risk, 'account market native_limits strategy kill_switch plan_turnover')
    _fields(risk['strategy'], 'strategy_id approved approval_id')
    strategy = StrategyApproval.model_validate(risk['strategy'], strict=True)
    _text(strategy.strategy_id); _text(strategy.approval_id)
    _fields(risk['kill_switch'], ' '.join(KillSwitchState.model_fields))
    # Deserialize an expected state, then the ordinary preparation checks it
    # against fresh protected history. No arm/reset/Actor operation is called.
    stop = KillSwitchState.model_validate_json(json.dumps(risk['kill_switch']), strict=True)
    attestations = data['attestations']
    if type(attestations) is not dict or set(attestations) != {strategy.strategy_id}:
        raise ValueError('SESSION_ATTESTATION_SCOPE')
    att = attestations[strategy.strategy_id]
    _fields(att, ' '.join(f.name for f in fields(ValidationAttestation)))
    for key in ('strategy_id', 'kind', 'attested_by'):
        _text(att[key])
    for key in ('reason', 'validation_result_hash'):
        if type(att[key]) is not str or len(att[key]) > 4096:
            raise ValueError('SESSION_ATTESTATION_TEXT')
    _number(att['attested_at'], integer=True)
    extra = {}
    if constrained:
        from services.validation.demo_strategy_constraints import validate
        extra['execution_constraints'] = validate(data['execution_constraints'], orders=data['orders'], now_ms=now_ms)
    if delegated:
        from services.validation.demo_strategy_constraints import delegated_proof_digest
        delegated_proof_digest(data['delegated_admission'])
        extra['delegated_admission'] = data['delegated_admission']
    return {'orders': data['orders'], 'origin': data['origin'], **extra,
        'risk_inputs': {'account': risk['account'], 'market': risk['market'],
            'native_limits': _limits(risk['native_limits']), 'strategy': strategy,
            'kill_switch': KillSwitch(stop), 'plan_turnover': _number(risk['plan_turnover']), 'now_ms': now_ms},
        'mandate': _mandate(data['mandate']),
        'attestations': {strategy.strategy_id: ValidationAttestation(**att)}, 'now_ms': now_ms}
