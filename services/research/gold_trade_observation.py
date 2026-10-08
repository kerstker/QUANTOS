"""Offline external fill facts, never a cash ledger or trading authority.

The normalized format is deliberately not a guessed Binance CSV schema.
Reported realized PnL is a venue assertion, not reconstructed strategy profit.
"""
from decimal import Decimal, localcontext
import hashlib
import json
import re

MAX_BYTES = 8_000_000
MAX_FILLS = 20_000
HEADER = {'schema', 'venue', 'environment', 'account_ref', 'symbol',
          'start_ms', 'end_ms', 'exported_at_ms', 'fills'}
FIELDS = {'trade_id', 'order_id', 'time_ms', 'position_side', 'side', 'price',
          'quantity', 'commission', 'commission_asset', 'reported_realized_pnl'}
NUMBER = re.compile(r'-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,18})?\Z')
IDENTIFIER = re.compile(r'(?:0|[1-9][0-9]{0,29})\Z')


class ObservationError(ValueError):
    """Fixed error codes; never echo private input."""


def _require(condition, code):
    if not condition:
        raise ObservationError(code)


def _time(value):
    _require(type(value) is int and 0 <= value <= 2**63-1, 'INVALID_TIME')
    return value


def _amount(value, positive=False):
    _require(isinstance(value, str) and NUMBER.fullmatch(value) is not None,
             'INVALID_DECIMAL')
    result = Decimal(value)
    _require(not positive or result > 0, 'NONPOSITIVE_AMOUNT')
    return result


def _text_amount(value):
    if value == 0:
        return '0'
    result = format(value, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def load_document(raw):
    """Decode a bounded UTF-8 JSON document, rejecting ambiguous keys."""
    _require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'INVALID_INPUT_SIZE')

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result

    def reject_constant(_):
        raise ObservationError('INVALID_JSON_CONSTANT')

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ObservationError('INVALID_JSON_DOCUMENT') from None


def normalize_binance_trades(document):
    """Adapt saved userTrades rows; declared source, never live authentication.

    The envelope owns account/window identity. It is not an API request and must
    contain no headers or credentials. Original bytes remain with the operator.
    Fills alone cannot establish whether a human or an API originated an order.
    """
    _require(isinstance(document, dict) and set(document) == HEADER, 'INVALID_HEADER')
    _require(document['schema'] == 'GOLD_BINANCE_USER_TRADES_V1', 'UNSUPPORTED_SOURCE')
    result = {**document, 'schema': 'GOLD_EXTERNAL_FILLS_V1', 'fills': []}
    # Validate envelope independently, including when no trades were returned.
    assess(result)
    rows = document['fills']
    _require(isinstance(rows, list) and len(rows) <= MAX_FILLS, 'INVALID_FILL_COUNT')
    required = {'symbol', 'id', 'orderId', 'time', 'positionSide', 'side',
                'price', 'qty', 'quoteQty', 'commission', 'commissionAsset',
                'realizedPnl', 'buyer', 'maker'}
    optional = {'pair', 'marginAsset', 'baseQty'}
    seen = {}
    for row in rows:
        _require(isinstance(row, dict) and required <= row.keys() <= required | optional,
                 'INVALID_NATIVE_FILL_FIELDS')
        _require(row['symbol'] == 'XAUUSDT' and row.get('pair', 'XAUUSDT') == 'XAUUSDT'
                 and row.get('marginAsset', 'USDT') == 'USDT', 'UNSUPPORTED_SOURCE')
        for key in ('id', 'orderId'):
            _require(type(row[key]) is int and 0 <= row[key] <= 2**63-1,
                     'INVALID_IDENTIFIER')
        _require(type(row['maker']) is bool and type(row['buyer']) is bool
                 and row['side'] in ('BUY', 'SELL')
                 and row['buyer'] == (row['side'] == 'BUY'), 'INVALID_NATIVE_SIDE')
        _amount(row['quoteQty'], positive=True)
        _require(_amount(row.get('baseQty', '0')) == 0, 'INVALID_NATIVE_BASE_QUANTITY')
        # Do not discard conflicting metadata merely because the summary omits it.
        previous = seen.get(row['id'])
        _require(previous is None or previous == row, 'CONFLICTING_NATIVE_TRADE_ID')
        seen[row['id']] = row
        result['fills'].append({'trade_id': str(row['id']), 'order_id': str(row['orderId']),
            'time_ms': row['time'], 'position_side': row['positionSide'], 'side': row['side'],
            'price': row['price'], 'quantity': row['qty'], 'commission': row['commission'],
            'commission_asset': row['commissionAsset'],
            'reported_realized_pnl': row['realizedPnl']})
    assess(result)
    return result


def assess(document):
    """Summarize a declared account/window; never assert source authenticity."""
    _require(isinstance(document, dict) and set(document) == HEADER, 'INVALID_HEADER')
    d = document
    _require(d['schema'] == 'GOLD_EXTERNAL_FILLS_V1' and d['venue'] == 'BINANCE'
             and d['environment'] == 'MAINNET' and d['symbol'] == 'XAUUSDT',
             'UNSUPPORTED_SOURCE')
    _require(isinstance(d['account_ref'], str) and
             re.fullmatch(r'[0-9a-f]{64}', d['account_ref']) is not None,
             'INVALID_ACCOUNT_REFERENCE')
    start, end, exported = (_time(d[k]) for k in ('start_ms','end_ms','exported_at_ms'))
    _require(start < end <= exported, 'INVALID_WINDOW')
    _require(isinstance(d['fills'], list) and len(d['fills']) <= MAX_FILLS,
             'INVALID_FILL_COUNT')
    unique = {}
    duplicates = 0
    for source in d['fills']:
        _require(isinstance(source, dict) and set(source) == FIELDS, 'INVALID_FILL_FIELDS')
        row = dict(source)
        for key in ('trade_id','order_id'):
            _require(isinstance(row[key], str) and IDENTIFIER.fullmatch(row[key]) is not None,
                     'INVALID_IDENTIFIER')
        _require(start <= _time(row['time_ms']) < end, 'FILL_OUTSIDE_WINDOW')
        _require(row['position_side'] in ('BOTH','LONG','SHORT') and
                 row['side'] in ('BUY','SELL'), 'INVALID_SIDE')
        _require(isinstance(row['commission_asset'], str) and
                 re.fullmatch(r'[A-Z][A-Z0-9]{0,19}', row['commission_asset']) is not None,
                 'INVALID_ASSET')
        for key in ('price','quantity','commission','reported_realized_pnl'):
            row[key] = _text_amount(_amount(row[key], positive=key in ('price','quantity')))
        previous = unique.get(row['trade_id'])
        if previous is not None:
            _require(previous == row, 'CONFLICTING_TRADE_ID')
            duplicates += 1
        else:
            unique[row['trade_id']] = row
    fees, sides = {}, {}
    # 24 integer + 18 fraction digits, plus bounded sums: 64 is exact here.
    with localcontext() as context:
        context.prec = 64
        pnl = Decimal(0)
        for row in unique.values():
            asset = row['commission_asset']
            fees[asset] = fees.get(asset, Decimal(0)) + Decimal(row['commission'])
            pnl += Decimal(row['reported_realized_pnl'])
            side = row['position_side']
            sides[side] = sides.get(side, 0) + 1
    facts = {k: v for k, v in d.items() if k != 'fills'}
    facts['fills'] = sorted(unique.values(), key=lambda row: (row['time_ms'], int(row['trade_id'])))
    return {'schema': 'GOLD_EXTERNAL_FILL_OBSERVATION_V1',
        'status': 'EXTERNAL_FILL_FACTS_ONLY' if unique else 'NO_FILL_FACTS',
        'venue': d['venue'], 'environment': d['environment'], 'symbol': d['symbol'],
        'account_ref': d['account_ref'], 'start_ms': start, 'end_ms': end,
        'exported_at_ms': exported, 'facts_sha256': _hash(facts),
        'unique_fills': len(unique), 'duplicate_fills': duplicates,
        'commission_by_asset': {k: _text_amount(v) for k,v in sorted(fees.items())},
        'fill_counts_by_position_side': dict(sorted(sides.items())),
        'reported_realized_pnl_usdt': _text_amount(pnl),
        'history_complete': False, 'source_authenticated': False,
        'net_pnl_usdt': None, 'account_return': None, 'strategy_pnl': None,
        'authoritative': False,
        'limitations': ['DECLARED_EXTERNAL_FACTS_NOT_AUTHENTICATED',
            'NO_COMPLETE_CASH_FLOW_OR_INITIAL_POSITION', 'NO_FUNDING_RECONCILIATION',
            'NO_STRATEGY_ATTRIBUTION', 'NO_CROSS_ASSET_FEE_CONVERSION']}
