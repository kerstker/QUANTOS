"""A stable inventory is not an atomic venue ledger or a new strategy profit."""
from copy import deepcopy

import pytest

from services.research.demo_account_baseline import build_baseline, normalize_response

NOW = 1_800_000_000_000


def payload(kind):
    if kind.startswith('config'):
        return {'canTrade': True, 'dualSidePosition': False, 'multiAssetsMargin': False}
    if kind.startswith('symbols'):
        return [{'symbol': 'BTCUSDT', 'marginType': 'CROSSED', 'leverage': 20}]
    if kind.startswith('account'):
        return {'assets': [{'asset': 'USDT', 'walletBalance': '1000', 'crossWalletBalance': '1000',
            'availableBalance': '990', 'unrealizedProfit': '20', 'marginBalance': '1020'}],
            'positions': [{'symbol': 'BTCUSDT', 'positionSide': 'BOTH', 'positionAmt': '2'}]}
    if kind == 'positions':
        return [{'symbol': 'BTCUSDT', 'positionSide': 'BOTH', 'positionAmt': '2',
            'entryPrice': '100', 'markPrice': '110', 'unRealizedProfit': '20',
            'isolatedWallet': '0', 'marginAsset': 'USDT', 'updateTime': NOW-1000}]
    if kind == 'orders': return []
    raise AssertionError(kind)


def captured():
    from quantos.contracts.binance_demo_account import DemoAccountCapture, AccountRead
    kinds=['config_before','account_before','symbols_before','positions','orders',
           'symbols_after','account_after','config_after']
    return DemoAccountCapture(observed_at_ms=NOW,completed_at_ms=NOW+100,
        status='CAPTURED', reason='CAPTURE_COMPLETE', source_sha256='a'*64,
        reads=[AccountRead(kind=k,started_at_ms=NOW+i*10,received_at_ms=NOW+i*10+1,
            response_sha256=str(i+1)*64,data=normalize_response(k,payload(k))) for i,k in enumerate(kinds)])


def test_complete_inventory_preserves_opening_pnl_and_unverified_source():
    result=build_baseline(captured(),as_of_ms=NOW+200)
    assert result['inventory_status']=='STABLE_OBSERVATIONS'
    assert result['nonzero_positions']==1 and result['flat_account'] is False
    assert result['positions'][0]['estimated_entry_notional']=='200'
    assert result['positions'][0]['basis_pnl_residual']=='0'
    assert result['positions'][0]['ownership']=='PREEXISTING_ACCOUNT_POSITION'
    assert result['assets'][0]['observed_wallet']=='1000'
    assert result['assets'][0]['observed_unrealized']=='20'
    assert result['new_strategy_pnl'] is None and result['atomic_snapshot_verified'] is False
    assert result['submission_enabled'] is False and result['ledger_reconciliation']=='UNKNOWN'


@pytest.mark.parametrize('kind,field,value', [('account_after','walletBalance','999'),
    ('account_after','positionAmt','3'),('positions','positionAmt','1'),
    ('config_after','dualSidePosition',True),('symbols_after','leverage',30)])
def test_mid_capture_changes_are_retained_and_not_treated_as_a_frozen_account(kind,field,value):
    cap=captured().model_dump()
    row=next(r for r in cap['reads'] if r['kind']==kind)
    if field=='walletBalance': row['data']['assets'][0][field]=value
    elif kind.startswith('account'): row['data']['positions'][0][field]=value
    elif kind=='positions' or kind.startswith('symbols'): row['data'][0][field]=value
    else: row['data'][field]=value
    from quantos.contracts.binance_demo_account import DemoAccountCapture
    result=build_baseline(DemoAccountCapture.model_validate(cap),as_of_ms=NOW+200)
    assert result['inventory_status']=='CHANGED_DURING_CAPTURE'
    assert result['new_strategy_pnl'] is None


def test_missing_position_and_missing_margin_asset_are_visible():
    from quantos.contracts.binance_demo_account import DemoAccountCapture
    cap=captured().model_dump()
    next(r for r in cap['reads'] if r['kind']=='positions')['data']=[]
    assert build_baseline(DemoAccountCapture.model_validate(cap),as_of_ms=NOW+200)['inventory_status']=='CHANGED_DURING_CAPTURE'
    cap=captured().model_dump()
    next(r for r in cap['reads'] if r['kind']=='positions')['data'][0]['marginAsset']='USDC'
    assert 'POSITION_MARGIN_ASSET_MISSING' in build_baseline(DemoAccountCapture.model_validate(cap),as_of_ms=NOW+200)['issues']


def test_rounding_difference_is_not_forced_to_match_reported_pnl():
    from quantos.contracts.binance_demo_account import DemoAccountCapture
    cap=captured().model_dump()
    next(r for r in cap['reads'] if r['kind']=='positions')['data'][0]['entryPrice']='100.001'
    result=build_baseline(DemoAccountCapture.model_validate(cap),as_of_ms=NOW+200)
    assert result['positions'][0]['basis_pnl_residual']=='-0.002'
    assert 'ENTRY_BASIS_UNVERIFIED' in result['issues']


def test_normalizer_rejects_duplicate_identity_and_never_retains_unknown_secrets():
    raw=payload('account_before');raw['apiKey']='do-not-persist'
    assert 'apiKey' not in normalize_response('account_before',raw)
    raw['assets'].append(deepcopy(raw['assets'][0]))
    with pytest.raises(ValueError): normalize_response('account_before',raw)
    raw=payload('positions');raw[0]['entryPrice']=100.0
    with pytest.raises(ValueError): normalize_response('positions',raw)


def test_stale_and_future_observations_are_not_current_inventory():
    assert build_baseline(captured(),as_of_ms=NOW+120001)['inventory_status']=='STALE'
    assert build_baseline(captured(),as_of_ms=NOW)['inventory_status']=='DATA_BLOCKED'


def test_demo_symbol_configuration_preserves_han_symbols_and_rejects_control_characters():
    rows = [{'symbol': '测试测试USDT', 'marginType': 'CROSSED', 'leverage': 20}]
    assert normalize_response('symbols_before', rows) == rows
    rows[0]['symbol'] = 'BTC\nUSDT'
    with pytest.raises(ValueError): normalize_response('symbols_before', rows)


@pytest.mark.parametrize('patch', [{'orderId': 0}, {'side': 'UNKNOWN'}, {'status': 'FILLED'},
    {'executedQty': '1.00000000000000000001'}, {'origQty': '0'}, {'clientOrderId': 'bad\nidentity'}])
def test_malformed_open_orders_are_not_admitted(patch):
    row = {'symbol': 'BTCUSDT', 'orderId': 9007199254740993, 'clientOrderId': 'prior-order',
        'positionSide': 'BOTH', 'side': 'BUY', 'status': 'NEW', 'origQty': '1', 'executedQty': '0'}
    assert normalize_response('orders', [row])[0]['orderId'] == '9007199254740993'
    with pytest.raises(ValueError): normalize_response('orders', [row | patch])
