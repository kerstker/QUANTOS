"""A retained terminal zero-fill reduction is not a fabricated trade."""
from copy import deepcopy
import json

import pytest

from services.research.demo_account_recovery import reconcile
from tests.research.test_gold_reported_short import short_inputs
from tests.research.test_binance_order_receipts import receipt


def with_unfilled_reduction(tmp_path):
    value = short_inputs(tmp_path)
    planned = dict(value['system_orders'][1], newClientOrderId='QDunfilled')
    value['system_orders'].insert(1, planned)
    window = deepcopy(value['windows'][1])
    row = window['messages'][0]
    event = json.loads(row['raw_utf8'])
    event['o'].update(c='QDunfilled', i=23, x='EXPIRED', X='EXPIRED', l='0', z='0',
                      L='0', t=0, n='0', rp='0')
    event['E'] -= 10000
    event['T'] -= 10000
    event['o']['T'] -= 10000
    window['start_ms'] -= 10000
    window['end_ms'] -= 10000
    window['gaps'] = []
    window['messages'] = [receipt(event, row['received_at_ms']-10000)]
    value['windows'].insert(1, window)
    return value


@pytest.mark.parametrize('damage', [None, 'nonterminal', 'positive_fill', 'unbound', 'opening', 'fee'])
def test_terminal_zero_fill_reduction_is_retained_without_ledger_event(tmp_path, damage):
    value = with_unfilled_reduction(tmp_path)
    row = value['windows'][1]['messages'][0]
    event = json.loads(row['raw_utf8'])
    if damage == 'nonterminal':
        event['o'].update(x='NEW', X='NEW')
    elif damage == 'positive_fill':
        event['o'].update(l='0.001', z='0.001', x='TRADE')
    elif damage == 'unbound':
        value['system_orders'].pop(1)
    elif damage == 'fee':
        event['o']['n'] = '1'
    elif damage == 'opening':
        event['o']['R'] = False
        value['system_orders'][1]['reduceOnly'] = False
    value['windows'][1]['messages'] = [receipt(event, row['received_at_ms'])]
    original = deepcopy(value)
    report, ledger = reconcile(**value)
    if damage:
        assert report['status'] == 'UNRESOLVED' and ledger is None
    else:
        assert report['status'] == 'REPORTED_ACCOUNT_MATCHED', report['issues']
        assert report['system_order_ids'] == ['21', '22', '23']
        assert report['unfilled_system_order_ids'] == ['23']
        assert report['fill_count'] == 2
        assert report['canonical_wallet'] == '5000.0801' and report['wallet_residual'] == '0'
        assert report['stream_gaps'] and report['stream_coverage_repaired'] is False
    assert value == original
