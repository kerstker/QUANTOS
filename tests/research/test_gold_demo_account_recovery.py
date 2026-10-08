"""Gold reported fills reconcile in the original ledger with explicit gaps."""
import json
from copy import deepcopy

import pytest

from services.research.demo_account_recovery import reconcile
from quantos.contracts.binance_demo_account import READ_KINDS, normalize_account_read
from tests.research.test_demo_account_recovery import inputs as btc_inputs
from tests.research.test_binance_order_receipts import receipt


def inputs(tmp_path):
    # Synthetic venue scenario, not a rewritten real account artifact.
    original = btc_inputs(tmp_path)
    body = json.dumps(original).replace('USDC', 'TEMPQUOTE').replace('USDT', 'USDC')
    body = body.replace('TEMPQUOTE', 'USDT').replace('BTCUSDT', 'XAUUSDT')
    value = json.loads(body)
    for origin in (value['opening'], value['closing']):
        origin['schema'] = 'DEMO_GOLD_EXECUTION_ORIGIN_V1'
        rules = next(r['data'] for r in origin['reads'] if r['kind'] == 'rules')
        for read in origin['reads']:
            if read['kind'] in READ_KINDS:
                read['data'] = normalize_account_read(read['kind'], read['data'])
        rules['base_asset'] = 'XAU'
        rules['terms']['contractType'] = 'TRADIFI_PERPETUAL'
    for window in value['windows']:
        window['messages'] = [receipt(json.loads(row['raw_utf8']), row['received_at_ms'])
                              for row in window['messages']]
    return value


def test_gold_reported_open_and_manual_close_use_one_exact_cash_ledger(tmp_path):
    value = inputs(tmp_path)
    before = deepcopy(value)
    report, ledger = reconcile(**value)
    assert report['status'] == 'REPORTED_ACCOUNT_MATCHED', report['issues']
    assert report['symbol'] == 'XAUUSDT'
    assert report['canonical_wallet'] == '4999.6801'
    assert report['wallet_residual'] == '0' and report['final_quantity'] == '0'
    assert report['reported_fee'] == '0.1199' and report['net_after_fees'] == '-0.3199'
    assert report['system_order_ids'] == ['21'] and report['manual_order_ids'] == ['22']
    assert len(report['stream_gaps']) == 2 and not report['stream_coverage_repaired']
    assert not report['automatic_cycle_completed'] and not report['economic_authority']
    assert ledger.state_as_canonical_dict() == report['ledger_state']
    assert value == before


@pytest.mark.parametrize('damage', ['missing_fee', 'cash_difference', 'account_difference'])
def test_gold_unknown_cash_and_unbound_sources_cannot_reconcile(tmp_path, damage):
    value = inputs(tmp_path)
    if damage == 'missing_fee':
        for statement in value['statements']:
            statement['income'] = [r for r in statement['income']
                                   if not (r['incomeType'] == 'COMMISSION' and r['tradeId'] == '2')]
            for read in statement['reads']:
                if read['kind'] == 'income':
                    read['data'] = deepcopy(statement['income'])
    elif damage == 'account_difference':
        value['windows'][1]['account_ref_sha256'] = 'f'*64
    else:
        for read in value['closing']['reads']:
            if read['kind'].startswith('account'):
                asset = next(a for a in read['data']['assets'] if a['asset'] == 'USDT')
                for key in ('walletBalance', 'crossWalletBalance', 'availableBalance', 'marginBalance'):
                    asset[key] = '5000'
    report, ledger = reconcile(**value)
    assert report['status'] == 'UNRESOLVED' and ledger is None
    assert report['issues']
