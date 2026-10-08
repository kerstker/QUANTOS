import copy

import pytest

from services.research import gold_account_snapshot as module


def payload(kind):
    if kind.startswith('config'):
        return {'dualSidePosition': False, 'multiAssetsMargin': False}
    if kind.startswith('account'):
        return {'assets': [{'asset': 'USDT', 'walletBalance': '1000',
            'crossWalletBalance': '1000', 'availableBalance': '900',
            'unrealizedProfit': '1', 'marginBalance': '1001'}],
            'positions': [{'symbol': 'XAUUSDT', 'positionSide': 'BOTH', 'positionAmt': '0.01'}]}
    if kind == 'positions':
        return [{'symbol': 'XAUUSDT', 'positionSide': 'BOTH', 'positionAmt': '0.01',
            'entryPrice': '4000', 'markPrice': '4100', 'unRealizedProfit': '1',
            'isolatedWallet': '0', 'marginAsset': 'USDT', 'updateTime': 900}]
    if kind == 'symbols':
        return [{'symbol': 'XAUUSDT', 'marginType': 'CROSSED', 'leverage': 2}]
    if kind.startswith('orders'):
        return [{'symbol': 'XAUUSDT', 'orderId': 42, 'positionSide': 'BOTH',
            'side': 'SELL', 'status': 'NEW', 'origQty': '0.01', 'executedQty': '0',
            'price': '4200', 'type': 'LIMIT', 'reduceOnly': True}]
    if kind.startswith('algos'):
        return [{'symbol': 'XAUUSDT', 'algoId': 43, 'positionSide': 'BOTH',
            'side': 'SELL', 'algoStatus': 'NEW', 'quantity': '0',
            'orderType': 'STOP_MARKET', 'triggerPrice': '3900', 'price': '0',
            'closePosition': True, 'reduceOnly': False}]
    raise AssertionError(kind)


def capture():
    return {'schema': module.SCHEMA, 'environment': 'BINANCE_USDM_MAINNET',
        'account_ref': 'a'*64, 'started_at_ms': 1000, 'completed_at_ms': 1200,
        'status': 'CAPTURED', 'reason': 'READS_COMPLETE',
        'reads': [{'kind': kind, 'started_at_ms': 1000+i*10,
            'received_at_ms': 1001+i*10, 'response_sha256': 'b'*64,
            'data': module.normalize(kind, payload(kind))}
            for i, kind in enumerate(module.KINDS)]}


def test_snapshot_covers_positions_and_both_order_types_without_authority():
    report = module.assess(capture(), as_of_ms=1300)
    assert report['status'] == 'STABLE_OBSERVATIONS'
    assert report['xau_positions'] == 1
    assert report['ordinary_orders'] == report['conditional_orders'] == 1
    assert report['history_complete'] is False
    assert report['atomic_snapshot_verified'] is False
    assert report['submission_enabled'] is False
    assert report['net_pnl'] is None


@pytest.mark.parametrize('kind,field,value', [
    ('orders_after', 'price', '4300'), ('algos_after', 'triggerPrice', '3800'),
    ('account_after', 'walletBalance', '999'), ('positions', 'positionAmt', '0.02'),
    ('config_after', 'dualSidePosition', True)])
def test_interleaved_changes_require_reconciliation(kind, field, value):
    cap = capture()
    row = next(r for r in cap['reads'] if r['kind'] == kind)
    if kind.startswith('account'):
        row['data']['assets'][0][field] = value
    elif kind.startswith('config'):
        row['data'][field] = value
    else:
        row['data'][0][field] = value
    assert module.assess(cap, as_of_ms=1300)['status'] == 'CHANGED_DURING_CAPTURE'


def test_partial_failed_capture_never_becomes_empty_or_connected_account():
    cap = capture()
    cap.update(status='FAILED', reason='REQUEST_FAILED', reads=cap['reads'][:2])
    report = module.assess(cap, as_of_ms=1300)
    assert report['status'] == 'INCOMPLETE'
    assert report['xau_positions'] is None


def test_stale_and_future_snapshots():
    assert module.assess(capture(), as_of_ms=200000)['status'] == 'STALE'
    with pytest.raises(ValueError):
        module.assess(capture(), as_of_ms=1100)


def test_other_symbols_are_preserved_and_unknown_fields_not_persisted():
    raw = payload('account_before')
    raw['positions'].append({'symbol': 'BTCUSDT', 'positionSide': 'BOTH', 'positionAmt': '1'})
    raw['secret'] = 'private-value'
    result = module.normalize('account_before', raw)
    assert len(result['positions']) == 2
    assert 'private-value' not in str(result)
    raw['assets'].append(copy.deepcopy(raw['assets'][0]))
    with pytest.raises(ValueError):
        module.normalize('account_before', raw)


@pytest.mark.parametrize('field,value', [('orderId', True), ('origQty', '0'),
    ('executedQty', '1'), ('reduceOnly', 'true'), ('side', 'buy')])
def test_invalid_native_orders_rejected(field, value):
    raw = payload('orders_before')
    raw[0][field] = value
    with pytest.raises(ValueError):
        module.normalize('orders_before', raw)


def test_complete_label_cannot_hide_missing_conditionals_or_wrong_environment():
    cap = capture()
    cap['reads'].pop(3)
    with pytest.raises(ValueError):
        module.assess(cap, as_of_ms=1300)
    cap = capture()
    cap['environment'] = 'BINANCE_USDM_DEMO'
    with pytest.raises(ValueError):
        module.assess(cap, as_of_ms=1300)
