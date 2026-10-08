"""Declared cash facts and aggregate cross-checks, never a second PnL ledger.

Amounts are signed balance changes, including negative paid commissions and
positive rebates. Record IDs must be unique within the normalized document;
source adapters must preserve their native identity before creating those IDs.
"""
from decimal import Context, Decimal, localcontext
import re

from services.research.gold_trade_observation import (
    HEADER as FILL_HEADER, MAX_FILLS, IDENTIFIER, assess as assess_fills,
    _require, _amount, _text_amount, _time, _hash,
)

HEADER = (FILL_HEADER - {'fills'}) | {'events'}
FIELDS = {'record_id', 'time_ms', 'kind', 'asset', 'amount', 'symbol'}
KINDS = ('COMMISSION', 'REALIZED_PNL', 'FUNDING', 'TRANSFER', 'ADJUSTMENT')


def assess(document):
    _require(isinstance(document, dict) and set(document) == HEADER, 'INVALID_CASH_HEADER')
    d = document
    _require(d['schema'] == 'GOLD_EXTERNAL_CASH_V1', 'INVALID_CASH_SCHEMA')
    # Reuse the same account, instrument, environment and window contract.
    identity = {k: v for k, v in d.items() if k != 'events'}
    assess_fills(identity | {'schema': 'GOLD_EXTERNAL_FILLS_V1', 'fills': []})
    _require(isinstance(d['events'], list) and len(d['events']) <= MAX_FILLS, 'INVALID_EVENT_COUNT')
    unique, duplicates = {}, 0
    for source in d['events']:
        _require(isinstance(source, dict) and set(source) == FIELDS, 'INVALID_CASH_FIELDS')
        row = dict(source)
        _require(isinstance(row['record_id'], str) and IDENTIFIER.fullmatch(row['record_id']) is not None,
                 'INVALID_RECORD_ID')
        _require(d['start_ms'] <= _time(row['time_ms']) < d['end_ms'], 'CASH_OUTSIDE_WINDOW')
        _require(row['kind'] in KINDS, 'INVALID_CASH_KIND')
        _require(row['symbol'] == ('XAUUSDT' if row['kind'] in KINDS[:3] else None),
                 'INVALID_CASH_SCOPE')
        _require(isinstance(row['asset'], str) and re.fullmatch(r'[A-Z][A-Z0-9]{0,19}', row['asset']) is not None,
                 'INVALID_CASH_ASSET')
        row['amount'] = _text_amount(_amount(row['amount']))
        previous = unique.get(row['record_id'])
        if previous is not None:
            _require(previous == row, 'CONFLICTING_CASH_ID')
            duplicates += 1
        else:
            unique[row['record_id']] = row
    totals = {}
    with localcontext(Context(prec=64)):
        for row in unique.values():
            bucket = totals.setdefault(row['kind'], {})
            bucket[row['asset']] = bucket.get(row['asset'], Decimal(0)) + Decimal(row['amount'])
    facts = identity | {'events': sorted(unique.values(), key=lambda row: (row['time_ms'], int(row['record_id'])))}
    return identity | {
        'schema': 'GOLD_EXTERNAL_CASH_OBSERVATION_V1',
        'status': 'EXTERNAL_CASH_FACTS_ONLY' if unique else 'NO_CASH_FACTS',
        'facts_sha256': _hash(facts), 'unique_events': len(unique), 'duplicate_events': duplicates,
        'amounts_by_kind_asset': {kind: {asset: _text_amount(value) for asset, value in sorted(bucket.items())}
                                for kind, bucket in sorted(totals.items())},
        'history_complete': False, 'source_authenticated': False, 'authoritative': False,
        'net_pnl': None, 'limitations': ['DECLARED_FACTS_ONLY', 'NO_INITIAL_BALANCE_OR_POSITION',
            'NO_HISTORY_COMPLETENESS_PROOF', 'NO_CROSS_ASSET_CONVERSION', 'NO_STRATEGY_ATTRIBUTION']}


def _check(fill_value, cash_value, *, commission=False):
    result = {'status': 'MISSING_CASH_CATEGORY' if cash_value is None else 'MISSING_FILL_CATEGORY',
              'fill_cost' if commission else 'fill_reported': fill_value,
              'cash_change': cash_value, 'difference': None}
    if fill_value is not None and cash_value is not None:
        with localcontext(Context(prec=64)):
            # Commissions in fills are costs; cash entries are signed changes.
            difference = Decimal(cash_value) + Decimal(fill_value) if commission else Decimal(cash_value) - Decimal(fill_value)
        result.update(status='MATCHING_DECLARED_TOTALS' if difference == 0 else 'MISMATCH',
                      difference=_text_amount(difference))
    return result


def reconcile(fills, cash):
    f, c = assess_fills(fills), assess(cash)
    for key in ('venue', 'environment', 'symbol', 'account_ref', 'start_ms', 'end_ms'):
        _require(f[key] == c[key], 'CASH_FILL_SCOPE_MISMATCH')
    totals = c['amounts_by_kind_asset']
    commissions = totals.get('COMMISSION', {})
    pnl = totals.get('REALIZED_PNL', {})
    return {'schema': 'GOLD_CASH_FILL_CROSSCHECK_V1', 'status': 'DECLARED_TOTALS_ONLY',
        'fills_facts_sha256': f['facts_sha256'], 'cash_facts_sha256': c['facts_sha256'],
        'commission_checks': {asset: _check(f['commission_by_asset'].get(asset), commissions.get(asset), commission=True)
                              for asset in sorted(set(f['commission_by_asset']) | set(commissions))},
        'realized_pnl_check': _check(f['reported_realized_pnl_usdt'] if f['unique_fills'] else None, pnl.get('USDT')),
        'uncompared_realized_pnl_by_asset': {asset: value for asset, value in pnl.items() if asset != 'USDT'},
        'funding_by_asset': totals.get('FUNDING', {}),
        'non_trade_cash_by_kind': {kind: totals[kind] for kind in ('TRANSFER', 'ADJUSTMENT') if kind in totals},
        'net_pnl': None, 'history_complete': False, 'source_authenticated': False, 'authoritative': False,
        'limitations': ['MATCHING_TOTALS_DO_NOT_PROVE_COMPLETE_OR_IDENTICAL_EVENTS',
            'CASH_AND_FILL_AMOUNTS_ARE_NOT_ADDED_TOGETHER', 'FUNDING_NOT_INDEPENDENTLY_RECONCILED',
            'NO_INITIAL_BALANCE_OR_POSITION', 'NO_STRATEGY_ATTRIBUTION']}
