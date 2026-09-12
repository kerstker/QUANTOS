"""Offline, unverified account observations for bounded technical replay.

No network or credential input is accepted. Raw response hashes bind the
research input, not the identity of the response issuer or fees actually paid.
"""
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
import re

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_ledger_v2 import (
    MarginBracket, MarginConfigurationError, canonical_decimal, ledger_decimal_context,
)

SCHEMA = 'BINANCE_USDM_ACCOUNT_TERMS_V1'
ROUNDING = 'LEVERAGE_RECIPROCAL_CEILING_1E24_V1'
OVERFLOW = 'LAST_BRACKET_RISK_ESTIMATE_V1'
KINDS = {'commission', 'configuration', 'brackets'}


def _fields(value, required, optional=frozenset()):
    if not isinstance(value, dict) or not required <= set(value) <= required | optional:
        raise ValueError('unsupported or incomplete account observation fields')


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('bounded account integer required')
    return value


def _amount(value, *, maximum=Decimal('1000000000000000')):
    if isinstance(value, str):
        if len(value) > 64 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value):
            raise ValueError('exact unsigned account decimal required')
        result = Decimal(value)
    elif type(value) in (int, Decimal):
        result = Decimal(value)
    else:
        raise ValueError('exact account amount required')
    if not result.is_finite() or not 0 <= result <= maximum or result.as_tuple().exponent < -24:
        raise ValueError('account amount outside supported bounds')
    return result


def _response(row, kind):
    _fields(row, {'kind', 'requested_at_ms', 'received_at_ms', 'raw_utf8', 'response_sha256'})
    requested = _integer(row['requested_at_ms'], 0, 8640000000000000)
    received = _integer(row['received_at_ms'], requested, requested+30000)
    raw = row['raw_utf8']
    if row['kind'] != kind or not isinstance(raw, str) or len(raw.encode('utf-8')) > 65536:
        raise ValueError('bounded account response required')
    if hashlib.sha256(raw.encode('utf-8')).hexdigest() != row['response_sha256']:
        raise ValueError('account response digest mismatch')
    # Shared trust parser rejects duplicate keys, nonfinite numbers and invalid
    # Unicode. Parse numeric tokens again as Decimal so JSON numbers never pass
    # through binary floats on the path into the ledger.
    load_json_without_duplicate_keys(raw)
    return json.loads(raw, parse_float=Decimal), requested, received


def _single(payload, symbol):
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError('exactly one account contract observation required')
        payload = payload[0]
    if not isinstance(payload, dict) or payload.get('symbol') != symbol:
        raise ValueError('account contract or quote mismatch')
    return payload


def _rate(leverage):
    # Integer division defines the rounding, independently of Decimal settings.
    return Decimal((10**24+leverage-1)//leverage).scaleb(-24)


@ledger_decimal_context()
def parse_margin_table(table, *, symbol, selected_leverage, selected_cap=None):
    """Shared exact observed table; omitted symbol cap is explicitly not known.

    This parser does not attest the source or authorize new exposure. A caller
    omitting the separate symbol cap must retain that limitation in its output.
    """
    selected = _integer(selected_leverage, 1, 125)
    _fields(table, {'symbol', 'brackets'}, {'notionalCoef'})
    if table['symbol'] != symbol:
        raise ValueError('margin table symbol mismatch')
    if 'notionalCoef' in table and _amount(table['notionalCoef']) != 1:
        raise ValueError('adjusted notional coefficient semantics are unsupported')
    rows = table['brackets']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError('bounded finite account margin table required')
    brackets = []; facts = []; previous_max = 125; eligible_cap = Decimal('0')
    for index, row in enumerate(rows):
        _fields(row, {'bracket', 'initialLeverage', 'notionalFloor', 'notionalCap', 'maintMarginRatio', 'cum'})
        if _integer(row['bracket'], 1, 100) != index+1:
            raise ValueError('ordered account margin brackets required')
        maximum = _integer(row['initialLeverage'], 1, previous_max)
        previous_max = maximum
        bracket = MarginBracket(_amount(row['notionalFloor']), _amount(row['notionalCap']),
            _rate(min(selected, maximum)), _amount(row['maintMarginRatio'], maximum=Decimal('1')), _amount(row['cum']))
        brackets.append(bracket)
        facts.append({**bracket.as_canonical_dict(), 'maximum_initial_leverage': maximum})
        if selected <= maximum: eligible_cap = bracket.notional_cap
    cap = eligible_cap if selected_cap is None else min(_amount(selected_cap), eligible_cap)
    if cap <= 0:
        raise ValueError('selected leverage has no supported positive account capacity')
    return tuple(brackets), facts, cap


@ledger_decimal_context()
def apply_account_terms(terms, environment, *, symbol, start_ms, end_ms):
    """Return a new rule set, bound manifest and ordinary simulation fee rates."""
    try:
        return _apply(terms, environment, symbol=symbol, start_ms=start_ms, end_ms=end_ms)
    except (KeyError, TypeError, ArithmeticError, MarginConfigurationError) as exc:
        raise ValueError('malformed or unsupported frozen account terms') from exc


def _apply(terms, environment, *, symbol, start_ms, end_ms):
    _fields(terms, {'schema', 'symbol', 'account_ref_sha256', 'provenance', 'before', 'after'})
    if (terms['schema'] != SCHEMA or terms['symbol'] != symbol or symbol not in ('BTCUSDC', 'BTCUSDT')
            or terms['provenance'] not in ('SYNTHETIC_TEST_FIXTURE', 'IMPORTED_UNVERIFIED')
            or not isinstance(terms['account_ref_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', terms['account_ref_sha256'])):
        raise ValueError('explicit unverified account source identity required')
    observations = {}; clocks = {}
    for phase in ('before', 'after'):
        _fields(terms[phase], KINDS)
        observations[phase] = {}; clocks[phase] = {}
        for kind in sorted(KINDS):
            payload, requested, received = _response(terms[phase][kind], kind)
            if phase == 'before' and not start_ms-30000 <= requested <= received <= start_ms:
                raise ValueError('account conditions were not available before replay')
            if phase == 'after' and not end_ms <= requested <= received <= end_ms+30000:
                raise ValueError('account after observation does not cover replay')
            observations[phase][kind] = _single(payload, symbol)
            clocks[phase][kind] = {'requested_at_ms': requested, 'received_at_ms': received,
                                 'response_sha256': terms[phase][kind]['response_sha256']}
    if observations['before'] != observations['after']:
        raise ValueError('account conditions changed across replay')
    before = observations['before']
    fees = before['commission']
    _fields(fees, {'symbol', 'makerCommissionRate', 'takerCommissionRate'}, {'rpiCommissionRate'})
    rates = {role+'_fee_rate': _amount(fees[role+'CommissionRate'], maximum=Decimal('.01'))
             for role in ('maker', 'taker')}
    rpi = None if 'rpiCommissionRate' not in fees else canonical_decimal(_amount(fees['rpiCommissionRate'], maximum=Decimal('.01')))
    config = before['configuration']
    _fields(config, {'symbol', 'marginType', 'isAutoAddMargin', 'leverage', 'maxNotionalValue'})
    if config['marginType'] != 'CROSSED' or config['isAutoAddMargin'] is not False:
        raise ValueError('only fixed cross margin simulation is supported')
    selected = _integer(config['leverage'], 1, 125)
    selected_cap = _amount(config['maxNotionalValue'])
    brackets, facts, cap = parse_margin_table(before['brackets'], symbol=symbol,
                                             selected_leverage=selected, selected_cap=selected_cap)
    old_rule = environment['contract_rules'][0]
    spec = replace(old_rule.spec, margin_brackets=tuple(brackets), new_risk_notional_cap=cap,
                   margin_overflow_policy=OVERFLOW)
    identity = exact_identity_hash(terms, name='frozen_account_terms')
    combined = exact_identity_hash({'public': environment['manifest']['environment_hash'], 'account_terms': identity}, name='passive_account_environment')
    source = 'frozen-account-environment:'+combined
    known = max(old_rule.ingestion_time, *(r['received_at_ms'] for r in clocks['before'].values()))
    rule_id = source+':rules'
    common = {'rule_id': rule_id, 'source_identity': source, 'effective_from': known,
              'observed_time': known, 'available_time': known, 'ingestion_time': known}
    rule = replace(old_rule, spec=spec, **common)
    profile = replace(environment['order_rule_profiles'][0], contract_rule_id=rule_id, **common)
    facts = {'schema': SCHEMA, 'input_hash': identity, 'account_ref_sha256': terms['account_ref_sha256'],
        'provenance': terms['provenance'], 'source_verified': False, 'actual_paid_fees': None,
        'symbol': symbol, 'quote': symbol[3:], 'observations': clocks,
        'selected_leverage': selected, 'symbol_config_notional_cap': canonical_decimal(selected_cap),
        'new_risk_notional_cap': canonical_decimal(cap), 'cap_comparison': 'STRICTLY_BELOW',
        'margin_brackets': facts, 'initial_margin_rounding': ROUNDING, 'overflow_policy': OVERFLOW,
        'maker_fee_rate': canonical_decimal(rates['maker_fee_rate']),
        'taker_fee_rate': canonical_decimal(rates['taker_fee_rate']), 'rpi_fee_rate_not_applied': rpi,
        'limitations': ['UNVERIFIED_SOURCE_AND_ACCOUNT_MODE', 'BEFORE_AFTER_EQUALITY_NOT_CONTINUOUS_OBSERVATION',
            'ISOLATED_SINGLE_ASSET_SIMULATION_NO_EXTERNAL_POSITIONS_OR_ORDERS',
            'ACTUAL_FEE_DISCOUNTS_REBATES_AND_CONVERSION_UNKNOWN', 'SIMULATED_LIQUIDATION_FEE_1_PERCENT',
            'TAIL_MARGIN_ESTIMATE_NOT_VENUE_REQUIREMENT']}
    manifest = {**environment['manifest'], 'environment_hash': combined,
        'public_environment_hash': environment['manifest']['environment_hash'], 'account_terms': facts,
        'limitations': [v for v in environment['manifest']['limitations'] if v not in (
            'SIMULATED_IM_100_PERCENT_MM_50_PERCENT_LIQUIDATION_FEE_1_PERCENT',
            'EXPLICIT_SIMULATED_FEE_AND_ORDER_NOTIONAL_LIMIT')]+facts['limitations']}
    return {**environment, 'contract_rules': (rule,), 'order_rule_profiles': (profile,), 'manifest': manifest}, rates
