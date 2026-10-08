"""Independent observations must not silently repair a rejected private message."""
from copy import deepcopy
import json

import pytest

from tests.tools.test_binance_demo_statement import NOW, transport
from tests.tools.test_binance_demo_private_check import credentials
from tests.research.test_binance_order_receipts import message, receipt
from tools.binance_demo_statement import collect


def inputs(tmp_path):
    statement = collect(credential_file=credentials(tmp_path), symbols=['BTCUSDT'],
        start_ms=NOW-1000, end_ms=NOW, operator_read=True,
        get=transport([]), clock=lambda: NOW)
    payload = message(NOW-100, 'FILLED', '0.001', '0.001',
                      trade_id=9007199254740993, fee='0.00005', asset='USDT')
    payload['o'].update(s='BTCUSDT', i=21, q='0.001', L='100', p='100', m=False,
                        rp='0', f='IOC')
    return statement, [receipt(payload, NOW-98)]


def compare(statement, messages, **kwargs):
    from services.research.demo_trade_crosscheck import compare_order
    return compare_order(statement, messages=messages, symbol='BTCUSDT', venue_order_id='21',
                         as_of_ms=NOW, stream_gaps=kwargs.pop('stream_gaps', []), **kwargs)


def test_exact_three_way_match_is_read_only_and_does_not_assert_account_or_ledger_authority(tmp_path):
    s, messages = inputs(tmp_path); before = deepcopy((s, messages))
    result = compare(s, messages)
    assert result['status'] == 'OBSERVATIONS_MATCHED'
    assert result['fills'][0]['trade_id'] == '9007199254740993'
    assert result['reported_commission'] == '0.00005'
    assert result['fee_residual'] == '0'
    assert result['account_binding'] == 'UNKNOWN'
    assert result['canonical_ledger_reconciled'] is False
    assert result['automatic_cycle_completed'] is False
    assert result['orders_submitted'] is False
    assert (s, messages) == before


def test_original_stream_gap_survives_even_when_all_observed_fills_match(tmp_path):
    s, messages = inputs(tmp_path)
    gap = {'at_ms': NOW-99, 'reason': 'MESSAGE_REJECTED', 'response_sha256': 'a'*64}
    result = compare(s, messages, stream_gaps=[gap])
    assert result['status'] == 'MATCHED_WITH_STREAM_GAPS'
    assert result['stream_gaps'] == [gap]
    assert result['fills'][0]['native_status'] == 'MATCHED'
    assert 'STREAM_GAPS_RETAINED' in result['issues']


@pytest.mark.parametrize('key,value', [('l','0.002'), ('L','99'), ('S','SELL'),
    ('ps','LONG'), ('m',True), ('n','0.00006'), ('N','USDC'), ('T',NOW-101), ('rp','1')])
def test_conflicting_native_fields_are_not_hidden_by_matching_order_identity(tmp_path, key, value):
    s, messages = inputs(tmp_path)
    p = json.loads(messages[0]['raw_utf8']); p['o'][key] = value
    result = compare(s, [receipt(p, NOW-98)])
    assert result['status'] == 'CONFLICT'
    assert result['fills'][0]['native_status'] == 'CONFLICT'


def test_missing_native_does_not_turn_rest_into_private_receipt(tmp_path):
    s, _ = inputs(tmp_path)
    result = compare(s, [])
    assert result['status'] == 'INCOMPLETE'
    assert result['fills'][0]['native_status'] == 'MISSING'


def test_duplicate_delivery_is_deduplicated_but_same_trade_conflict_is_preserved(tmp_path):
    s, messages = inputs(tmp_path)
    result = compare(s, messages*2)
    assert len(result['fills']) == 1 and result['reported_commission'] == '0.00005'
    p = json.loads(messages[0]['raw_utf8']); p['o']['n'] = '0.00006'
    result = compare(s, messages+[receipt(p, NOW-97)])
    assert result['status'] == 'CONFLICT'


def test_missing_rest_trade_is_not_ignored(tmp_path):
    s, messages = inputs(tmp_path)
    p = json.loads(messages[0]['raw_utf8']); p['o']['t'] += 1
    result = compare(s, messages+[receipt(p, NOW-97)])
    assert result['status'] == 'INCOMPLETE'
    assert result['native_only_trade_ids'] == ['9007199254740994']


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'asset', 'amount', 'symbol'])
def test_income_is_checked_per_trade_without_netting_other_fees(tmp_path, change):
    s, messages = inputs(tmp_path)
    rows = next(r['data'] for r in s['reads'] if r['kind'] == 'income')
    if change == 'missing': rows.clear()
    elif change == 'duplicate': rows.append({**rows[0], 'tranId': '10'})
    elif change == 'asset': rows[0]['asset'] = 'USDC'
    elif change == 'symbol': rows[0]['symbol'] = 'ETHUSDT'
    else: rows[0]['income'] = '-0.00004'
    s['income'] = sorted(deepcopy(rows), key=lambda r: (r['time'], r['incomeType'], r['tranId']))
    result = compare(s, messages)
    assert result['status'] in ('INCOMPLETE', 'CONFLICT')
    assert result['fills'][0]['fee_status'] != 'MATCHED'


def test_changed_digest_future_input_and_later_quantity_conflict_cannot_match(tmp_path):
    s, messages = inputs(tmp_path)
    bad = deepcopy(messages); bad[0]['response_sha256'] = 'f'*64
    with pytest.raises(ValueError): compare(s, bad)
    bad = deepcopy(messages); bad[0]['received_at_ms'] = NOW+1
    with pytest.raises(ValueError): compare(s, bad)
    p = json.loads(messages[0]['raw_utf8']); p['o']['z'] = '0.002'
    assert compare(s, messages+[receipt(p, NOW-97)])['status'] == 'CONFLICT'


def test_no_observations_is_unknown_not_success(tmp_path):
    s, _ = inputs(tmp_path)
    from services.research.demo_trade_crosscheck import compare_order
    result = compare_order(s, messages=[], symbol='BTCUSDT', venue_order_id='22',
                           stream_gaps=[], as_of_ms=NOW)
    assert result['status'] == 'INCOMPLETE' and not result['fills']
