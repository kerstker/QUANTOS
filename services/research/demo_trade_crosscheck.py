"""Offline, per-order comparison; never a replacement for execution evidence.

Statement V1 does not bind an account identity. Agreement here therefore cannot
clear a stream gap, project an OMS event, close a cash ledger, or authorize an
order. Venue and local times remain separate; no latency is inferred.
"""
from decimal import Decimal
import hashlib
import json

from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.research.demo_statement import identity, validate
from services.research.demo_user_stream import normalize_message, _time, _hash, GAP_REASONS


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _native_facts(order):
    return (order['S'], order['ps'], Decimal(order['l']), Decimal(order['L']),
        Decimal(order['n']) if 'n' in order else None, order.get('N'), order['T'],
        order['m'], Decimal(order['rp']) if 'rp' in order else None)


def _rest_facts(trade):
    return (trade['side'], trade['positionSide'], Decimal(trade['qty']), Decimal(trade['price']),
        Decimal(trade['commission']), trade['commissionAsset'], trade['time'], trade['maker'],
        Decimal(trade['realizedPnl']))


@ledger_decimal_context()
def compare_order(statement, *, messages, symbol, venue_order_id, stream_gaps, as_of_ms):
    """Compare all supplied observations for an explicit order, without mutation.

    Callers must retain the supplied window and its original gaps. Missing rows,
    partial REST coverage and a nonterminal private observation remain incomplete.
    This function deliberately offers no caller-controlled authority flag.
    """
    validate(statement); _time(as_of_ms)
    if statement['completed_at_ms'] > as_of_ms:
        raise ValueError('CROSSCHECK_FUTURE_STATEMENT')
    if symbol not in statement['symbols'] or not isinstance(venue_order_id, str) or identity(venue_order_id) != venue_order_id:
        raise ValueError('CROSSCHECK_ORDER_SCOPE_INVALID')
    if not isinstance(messages, list) or len(messages) > 10000:
        raise ValueError('CROSSCHECK_MESSAGE_LIMIT')
    if not isinstance(stream_gaps, list) or len(stream_gaps) > 10000:
        raise ValueError('CROSSCHECK_GAP_LIMIT')
    gaps = []
    for gap in stream_gaps:
        if not isinstance(gap, dict) or not {'at_ms', 'reason'} <= gap.keys() <= {'at_ms', 'reason', 'response_sha256'}:
            raise ValueError('CROSSCHECK_GAP_INVALID')
        if _time(gap['at_ms']) > as_of_ms or gap['reason'] not in GAP_REASONS:
            raise ValueError('CROSSCHECK_GAP_INVALID')
        if 'response_sha256' in gap: _hash(gap['response_sha256'])
        gaps.append(dict(gap))
    native = {}; order_events = []; total_bytes = 0
    for index, row in enumerate(messages):
        if not isinstance(row, dict) or set(row) != {'received_at_ms', 'raw_utf8', 'response_sha256'}:
            raise ValueError('CROSSCHECK_MESSAGE_FIELDS')
        if _time(row['received_at_ms']) > as_of_ms:
            raise ValueError('CROSSCHECK_FUTURE_MESSAGE')
        if not isinstance(row['raw_utf8'], str): raise ValueError('CROSSCHECK_RAW_REQUIRED')
        total_bytes += len(row['raw_utf8'].encode())
        if total_bytes > 2_000_000: raise ValueError('CROSSCHECK_BYTE_LIMIT')
        message = normalize_message(row['raw_utf8'])
        if message['response_sha256'] != row['response_sha256']:
            raise ValueError('CROSSCHECK_MESSAGE_DIGEST')
        if message['event_kind'] != 'ORDER_TRADE_UPDATE':
            raise ValueError('CROSSCHECK_ORDER_EVENT_REQUIRED')
        order = message['payload']['o']
        if order['s'] != symbol or identity(order['i']) != venue_order_id: continue
        order_events.append((row['received_at_ms'], index, order))
        if order['x'] == 'TRADE' and Decimal(order['l']) > 0:
            native.setdefault(identity(order['t']), []).append(order)
    trades = [r for r in statement['trades'] if r['symbol'] == symbol and r['orderId'] == venue_order_id]
    issues = []; fills = []; conflict = False; incomplete = False
    if statement['status'] != 'CAPTURED':
        issues.append('STATEMENT_INCOMPLETE'); incomplete = True
    if not trades:
        issues.append('REST_FILLS_MISSING'); incomplete = True
    for trade in trades:
        observed = native.get(trade['id'], [])
        if not observed:
            native_status = 'MISSING'; incomplete = True
        elif any(any(value is None for value in _native_facts(o)) for o in observed):
            native_status = 'INCOMPLETE'; incomplete = True
        elif any(_native_facts(o) != _rest_facts(trade) for o in observed):
            native_status = 'CONFLICT'; conflict = True
        else:
            native_status = 'MATCHED'
        income = [r for r in statement['income'] if r['incomeType'] == 'COMMISSION'
                  and r['symbol'] == symbol and r['tradeId'] == trade['id']]
        if not income:
            fee_status = 'MISSING'; residual = None; incomplete = True
        elif len(income) != 1 or income[0]['asset'] != trade['commissionAsset']:
            fee_status = 'CONFLICT'; residual = None; conflict = True
        else:
            residual = canonical_decimal(Decimal(trade['commission']) + Decimal(income[0]['income']))
            fee_status = 'MATCHED' if Decimal(residual) == 0 else 'CONFLICT'
            conflict = conflict or fee_status == 'CONFLICT'
        fills.append({'trade_id': trade['id'], 'side': trade['side'], 'position_side': trade['positionSide'],
            'quantity': trade['qty'], 'price': trade['price'], 'venue_trade_ms': trade['time'],
            'commission': trade['commission'], 'commission_asset': trade['commissionAsset'],
            'native_status': native_status, 'fee_status': fee_status, 'fee_residual': residual})
    native_only = sorted(set(native) - {r['id'] for r in trades}, key=int)
    if native_only:
        issues.append('NATIVE_FILLS_WITHOUT_REST'); incomplete = True
    quantity = sum((Decimal(t['qty']) for t in trades), Decimal(0))
    terminal_status = None
    if order_events:
        last = max(order_events, key=lambda e: (e[0], e[1]))[2]
        terminal_status = last['X']
        if last['X'] not in ('FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH'):
            issues.append('NATIVE_ORDER_NOT_TERMINAL'); incomplete = True
        if Decimal(last['z']) != quantity:
            issues.append('TERMINAL_QUANTITY_DIFFERENCE'); conflict = True
    else:
        issues.append('NATIVE_TERMINAL_MISSING'); incomplete = True
    if gaps: issues.append('STREAM_GAPS_RETAINED')
    if any(f['native_status'] != 'MATCHED' for f in fills): issues.append('NATIVE_TRADE_COMPARISON_UNRESOLVED')
    if any(f['fee_status'] != 'MATCHED' for f in fills): issues.append('TRADE_FEE_COMPARISON_UNRESOLVED')
    one_asset = len({t['commissionAsset'] for t in trades}) == 1
    fees = sum((Decimal(t['commission']) for t in trades), Decimal(0))
    fee_residual = (canonical_decimal(sum((Decimal(f['fee_residual']) for f in fills), Decimal(0)))
                    if fills and one_asset and all(f['fee_residual'] is not None for f in fills) else None)
    return {'schema': 'DEMO_TRADE_CROSSCHECK_V1', 'as_of_ms': as_of_ms,
        'status': 'CONFLICT' if conflict else 'INCOMPLETE' if incomplete else
                  'MATCHED_WITH_STREAM_GAPS' if gaps else 'OBSERVATIONS_MATCHED',
        'symbol': symbol, 'venue_order_id': venue_order_id, 'fills': fills,
        'native_only_trade_ids': native_only, 'observed_terminal_status': terminal_status,
        'reported_quantity': canonical_decimal(quantity) if trades else None,
        'reported_commission': canonical_decimal(fees) if one_asset else None,
        'commission_asset': trades[0]['commissionAsset'] if one_asset else None,
        'fee_residual': fee_residual, 'stream_gaps': gaps, 'issues': issues,
        'statement_sha256': _digest(statement), 'messages_sha256': _digest(messages),
        'account_binding': 'UNKNOWN', 'latest_order_query': 'NOT_CHECKED',
        'canonical_ledger_reconciled': False, 'automatic_cycle_completed': False,
        'orders_submitted': False, 'economic_authority': False}
