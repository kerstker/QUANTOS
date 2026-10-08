"""Pure ordinary USD-M snapshot projection; no authentication or execution."""
from decimal import Decimal
import re

from services.research.gold_trade_observation import _amount, _time, _require

SCHEMA = 'GOLD_USDM_ACCOUNT_CAPTURE_V1'
KINDS = ('config_before', 'account_before', 'orders_before', 'algos_before',
         'positions', 'symbols', 'orders_after', 'algos_after', 'account_after', 'config_after')
FAILURES = {'REQUEST_FAILED', 'AUTHENTICATION_REJECTED', 'RATE_LIMITED',
            'RESPONSE_INVALID', 'CLOCK_UNRELIABLE', 'READ_ONLY_PERMISSION_REQUIRED',
            'INTERRUPTED', 'SOURCE_CHANGED', 'CAPTURE_TIMEOUT', 'NETWORK_TIMEOUT',
            'NETWORK_UNAVAILABLE', 'TLS_FAILED', 'REDIRECT_REFUSED',
            'EXCHANGE_UNAVAILABLE', 'REQUEST_REJECTED'}


def _text(value):
    _require(isinstance(value, str) and re.fullmatch(r'[A-Z0-9_\u3400-\u9fff]{1,32}', value),
             'INVALID_TOKEN')
    return value


def _bool(value):
    _require(type(value) is bool, 'INVALID_BOOLEAN')
    return value


def _enum(value, choices):
    _require(isinstance(value, str) and value in choices, 'INVALID_ENUM')
    return value


def _decimal(value):
    _amount(value)
    return value


def _side(value):
    return _enum(value, ('BOTH', 'LONG', 'SHORT'))


def _rows(raw, fields, identity):
    _require(isinstance(raw, list) and len(raw) <= 2000, 'INVALID_ROWS')
    result = []
    for row in raw:
        _require(isinstance(row, dict), 'INVALID_ROW')
        result.append({name: parser(row[name]) for name, parser in fields.items()})
    keys = [tuple(row[k] for k in identity) for row in result]
    _require(len(set(keys)) == len(keys), 'DUPLICATE_IDENTITY')
    return sorted(result, key=lambda row: tuple(row[k] for k in identity))


def normalize(kind, raw):
    """Persist only explicitly selected response fields; retain all symbols."""
    try:
        _require(kind in KINDS, 'UNKNOWN_READ_KIND')
        if kind.startswith('config'):
            return {name: _bool(raw[name]) for name in ('dualSidePosition', 'multiAssetsMargin')}
        if kind.startswith('account'):
            return {'assets': _rows(raw['assets'], {'asset': _text,
                **dict.fromkeys(('walletBalance', 'crossWalletBalance', 'availableBalance',
                                  'unrealizedProfit', 'marginBalance'), _decimal)}, ('asset',)),
                'positions': _rows(raw['positions'], {'symbol': _text, 'positionSide': _side,
                                   'positionAmt': _decimal}, ('symbol', 'positionSide'))}
        if kind == 'positions':
            rows = _rows(raw, {'symbol': _text, 'positionSide': _side, 'marginAsset': _text,
                'updateTime': _time, **dict.fromkeys(('positionAmt', 'entryPrice', 'markPrice',
                'unRealizedProfit', 'isolatedWallet'), _decimal)}, ('symbol', 'positionSide'))
            for row in rows:
                _require(Decimal(row['isolatedWallet']) >= 0 and
                    (Decimal(row['positionAmt']) == 0 or
                     Decimal(row['entryPrice']) > 0 and Decimal(row['markPrice']) > 0), 'INVALID_POSITION')
            return rows
        if kind == 'symbols':
            rows = _rows(raw, {'symbol': _text, 'marginType': _text, 'leverage': _time}, ('symbol',))
            for row in rows:
                _require(row['marginType'] in ('CROSSED', 'ISOLATED') and
                         1 <= row['leverage'] <= 1000, 'INVALID_SYMBOL_CONFIG')
            return rows
        fields = {'symbol': _text, 'positionSide': _side,
                  'side': lambda v: _enum(v, ('BUY', 'SELL')), 'price': _decimal, 'reduceOnly': _bool}
        if kind.startswith('orders'):
            rows = _rows(raw, fields | {'orderId': _time, 'status': _text, 'type': _text,
                'origQty': _decimal, 'executedQty': _decimal}, ('symbol', 'orderId'))
            for row in rows:
                _require(row['orderId'] > 0 and row['status'] in ('NEW', 'PARTIALLY_FILLED', 'PENDING_CANCEL')
                    and Decimal(row['origQty']) > 0 and 0 <= Decimal(row['executedQty']) <= Decimal(row['origQty'])
                    and Decimal(row['price']) >= 0, 'INVALID_OPEN_ORDER')
            return rows
        rows = _rows(raw, fields | {'algoId': _time, 'algoStatus': _text, 'orderType': _text,
            'quantity': _decimal, 'triggerPrice': _decimal, 'closePosition': _bool}, ('symbol', 'algoId'))
        for row in rows:
            _require(row['algoId'] > 0 and row['algoStatus'] == 'NEW'
                and Decimal(row['quantity']) >= 0 and Decimal(row['price']) >= 0
                and Decimal(row['triggerPrice']) >= 0
                and (row['closePosition'] or Decimal(row['quantity']) > 0), 'INVALID_CONDITIONAL_ORDER')
        return rows
    except (KeyError, TypeError, ArithmeticError):
        raise ValueError('RESPONSE_INVALID') from None


def assess(capture, *, as_of_ms):
    _time(as_of_ms)
    keys = {'schema', 'environment', 'account_ref', 'started_at_ms', 'completed_at_ms',
            'status', 'reason', 'reads'}
    _require(isinstance(capture, dict) and set(capture) == keys, 'INVALID_CAPTURE')
    c = capture
    _require(c['schema'] == SCHEMA and c['environment'] == 'BINANCE_USDM_MAINNET', 'INVALID_ENVIRONMENT')
    _require(isinstance(c['account_ref'], str) and re.fullmatch('[a-f0-9]{64}', c['account_ref']), 'INVALID_ACCOUNT_REF')
    start, end = _time(c['started_at_ms']), _time(c['completed_at_ms'])
    _require(start <= end <= as_of_ms and end-start <= 180000, 'INVALID_CAPTURE_CLOCK')
    _require(c['status'] in ('CAPTURED', 'FAILED') and
        (c['reason'] == 'READS_COMPLETE' if c['status'] == 'CAPTURED' else c['reason'] in FAILURES), 'INVALID_CAPTURE_STATUS')
    reads = c['reads']
    _require(isinstance(reads, list) and len(reads) <= len(KINDS), 'INVALID_READS')
    previous = start
    data = {}
    for index, row in enumerate(reads):
        _require(isinstance(row, dict) and set(row) == {'kind', 'started_at_ms', 'received_at_ms',
            'response_sha256', 'data'} and row['kind'] == KINDS[index], 'INVALID_READ_SEQUENCE')
        sent, received = _time(row['started_at_ms']), _time(row['received_at_ms'])
        _require(previous <= sent <= received <= end, 'INVALID_READ_CLOCK')
        previous = received
        _require(isinstance(row['response_sha256'], str) and re.fullmatch('[a-f0-9]{64}', row['response_sha256']), 'INVALID_DIGEST')
        normalized = normalize(row['kind'], row['data'])
        _require(normalized == row['data'], 'NONCANONICAL_READ')
        data[row['kind']] = normalized
    _require(c['status'] != 'CAPTURED' or len(reads) == len(KINDS), 'INCOMPLETE_CAPTURE')
    report = {'schema': 'GOLD_ACCOUNT_SNAPSHOT_V1', 'status': 'INCOMPLETE',
        'read_count': len(reads), 'xau_positions': None, 'ordinary_orders': None,
        'conditional_orders': None, 'other_positions': None,
        'history_complete': False, 'source_authenticated': False, 'atomic_snapshot_verified': False,
        'submission_enabled': False, 'net_pnl': None, 'reconciliation': 'UNKNOWN'}
    if c['status'] == 'CAPTURED':
        stable = all(data[prefix+'_before'] == data[prefix+'_after']
                     for prefix in ('config', 'orders', 'algos'))
        # Mark-to-market changes are expected; compare cash and position quantities.
        def inventory(account):
            return ([(r['asset'], Decimal(r['walletBalance']), Decimal(r['crossWalletBalance']))
                     for r in account['assets']], quantities(account['positions']))
        def quantities(rows):
            return {(r['symbol'], r['positionSide']): Decimal(r['positionAmt'])
                    for r in rows if Decimal(r['positionAmt']) != 0}
        stable = (stable and inventory(data['account_before']) == inventory(data['account_after'])
                  and quantities(data['positions']) == quantities(data['account_after']['positions']))
        report.update(status='STABLE_OBSERVATIONS' if stable else 'CHANGED_DURING_CAPTURE',
            xau_positions=sum(r['symbol'] == 'XAUUSDT' and Decimal(r['positionAmt']) != 0 for r in data['positions']),
            other_positions=sum(r['symbol'] != 'XAUUSDT' and Decimal(r['positionAmt']) != 0 for r in data['positions']),
            ordinary_orders=len(data['orders_after']), conditional_orders=len(data['algos_after']))
    if as_of_ms-start > 120000:
        report['status'] = 'STALE'
    return report
