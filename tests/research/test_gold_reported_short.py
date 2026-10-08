"""Synthetic owned short receipts use the existing exact account ledger."""
from copy import deepcopy
import json

import pytest

from services.research.demo_account_recovery import reconcile
from tests.research.test_gold_demo_account_recovery import inputs
from tests.research.test_binance_order_receipts import receipt


def short_inputs(tmp_path):
    value = inputs(tmp_path)
    def flip(row):
        if isinstance(row, list):
            return [flip(v) for v in row]
        if isinstance(row, dict):
            changed = {k: flip(v) for k, v in row.items()}
            if 'side' in changed:
                changed['side'] = {'BUY': 'SELL', 'SELL': 'BUY'}[changed['side']]
            if changed.get('realizedPnl') == '-0.2':
                changed['realizedPnl'] = '0.2'
            if changed.get('incomeType') == 'REALIZED_PNL':
                changed['income'] = '0.2'
            return changed
        return row
    value['statements'] = flip(value['statements'])
    value['system_orders'][0]['side'] = 'SELL'
    for i, window in enumerate(value['windows']):
        original = window['messages'][0]
        event = json.loads(original['raw_utf8'])
        event['o']['S'] = 'SELL' if i == 0 else 'BUY'
        if i:
            event['o']['rp'] = '0.2'
            event['o']['c'] = 'QDsystemclose'
        window['messages'] = [receipt(event, original['received_at_ms'])]
    value['system_orders'].append(dict(value['system_orders'][0], side='BUY', type='MARKET',
        price='0', reduceOnly=True, newClientOrderId='QDsystemclose'))
    for read in value['closing']['reads']:
        if read['kind'].startswith('account'):
            for asset in read['data']['assets']:
                if asset['asset'] == 'USDT':
                    for k in ('walletBalance', 'crossWalletBalance', 'availableBalance', 'marginBalance'):
                        asset[k] = '5000.0801'
    return value


def test_owned_short_open_and_buy_close_reconcile(tmp_path):
    value = short_inputs(tmp_path)
    before = deepcopy(value)
    report, ledger = reconcile(**value)
    assert report['status'] == 'REPORTED_ACCOUNT_MATCHED', report['issues']
    assert report['canonical_wallet'] == '5000.0801' and report['wallet_residual'] == '0'
    assert report['reported_realized_pnl'] == '0.2' and report['reported_fee'] == '0.1199'
    assert report['final_quantity'] == '0' and report['manual_order_ids'] == []
    assert report['system_order_ids'] == ['21', '22']
    assert ledger.state_as_canonical_dict() == report['ledger_state'] and value == before
    assert report['stream_coverage_repaired'] is False and report['economic_authority'] is False


@pytest.mark.parametrize('short', [False, True])
def test_reduce_only_cannot_open_either_direction(tmp_path, short):
    value = short_inputs(tmp_path) if short else inputs(tmp_path)
    value['system_orders'][0]['reduceOnly'] = True
    row = value['windows'][0]['messages'][0]
    event = json.loads(row['raw_utf8'])
    event['o']['R'] = True
    value['windows'][0]['messages'] = [receipt(event, row['received_at_ms'])]
    report, ledger = reconcile(**value)
    assert report['status'] == 'UNRESOLVED' and ledger is None
    assert report['issues'] == ['ACCOUNT_RECOVERY_REDUCTION_EXCEEDS_POSITION']


@pytest.mark.parametrize('damage', ['oversized_close', 'unbound_close'])
def test_short_close_preserves_quantity_and_original_order_checks(tmp_path, damage):
    value = short_inputs(tmp_path)
    if damage == 'unbound_close':
        value['system_orders'].pop()
        expected = 'ACCOUNT_RECOVERY_EXTERNAL_OPENING_UNSUPPORTED'
    else:
        value['system_orders'][1]['quantity'] = '0.003'
        for statement in value['statements']:
            for row in statement['trades']:
                if str(row['id']) == '2':
                    row.update(qty='0.003', quoteQty='179.7')
            for read in statement['reads']:
                if isinstance(read['data'], list):
                    for row in read['data']:
                        if 'orderId' in row and str(row['id']) == '2':
                            row.update(qty='0.003', quoteQty='179.7')
        row = value['windows'][1]['messages'][0]
        event = json.loads(row['raw_utf8'])
        event['o'].update(q='0.003', l='0.003', z='0.003')
        value['windows'][1]['messages'] = [receipt(event, row['received_at_ms'])]
        expected = 'ACCOUNT_RECOVERY_REDUCTION_EXCEEDS_POSITION'
    report, ledger = reconcile(**value)
    assert report['status'] == 'UNRESOLVED' and ledger is None
    assert report['issues'] == [expected]
