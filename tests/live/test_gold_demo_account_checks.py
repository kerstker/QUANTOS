"""Gold observed account terms enter existing risk checks, not order authority."""
from copy import deepcopy

import pytest

from live.execution import demo_position_context as position, demo_pre_submit as submit
from services.research.demo_account_baseline import normalize_response
from tests.tools.test_gold_demo_execution_origin import capture, NOW


def orders():
    return [{'symbol': 'XAUUSDT', 'side': 'BUY', 'type': 'LIMIT', 'timeInForce': 'IOC',
             'quantity': '0.002', 'price': '60000', 'reduceOnly': False,
             'positionSide': 'BOTH', 'newClientOrderId': 'QDgoldtest001', 'newOrderRespType': 'ACK'}]


def capped(value):
    origin, _ = capture()
    for read in origin['reads']:
        if read['kind'].startswith('symbols'):
            read['target_config']['maxNotionalValue'] = value
    return origin


def test_gold_exact_account_cap_and_opposite_orders_do_not_net():
    batch = orders()
    submit._venue_account_cap(batch, capped('120'))
    with pytest.raises(submit.DemoPreparationRejected, match='DEMO_VENUE_NOTIONAL_CAP'):
        submit._venue_account_cap(batch, capped('119.999999999999999999'))
    opposite = [*batch, dict(batch[0], side='SELL'), dict(batch[0], quantity='0.001')]
    with pytest.raises(submit.DemoPreparationRejected, match='DEMO_VENUE_NOTIONAL_CAP'):
        submit._venue_account_cap(opposite, capped('179.999999999999999999'))


def test_gold_unsupported_account_terms_do_not_supply_a_cap():
    origin = capped('120')
    read = next(r for r in origin['reads'] if r['kind'] == 'rules')
    read['data']['base_asset'] = 'BTC'
    with pytest.raises(submit.DemoPreparationRejected, match='DEMO_VENUE_ACCOUNT_TERMS_UNSUPPORTED'):
        submit._venue_account_cap(orders(), origin)


def held():
    origin, _ = capture()
    for read in origin['reads']:
        kind, data = read['kind'], read['data']
        if kind.startswith('account'):
            asset = next(a for a in data['assets'] if a['asset'] == 'USDT')
            asset.update(walletBalance='4999.946', crossWalletBalance='4999.946',
                         availableBalance='4993.946', unrealizedProfit='0', marginBalance='4999.946')
            data['positions'].append({'symbol': 'XAUUSDT', 'positionSide': 'BOTH', 'positionAmt': '0.002'})
            read['data'] = normalize_response(kind, data)
        elif kind == 'positions':
            data.append({'symbol': 'XAUUSDT', 'positionSide': 'BOTH', 'positionAmt': '0.002',
                         'entryPrice': '60000', 'markPrice': '60000', 'unRealizedProfit': '0',
                         'isolatedWallet': '0', 'marginAsset': 'USDT', 'updateTime': NOW-1})
            read['data'] = normalize_response(kind, data)
    # Explicit synthetic context: this tests observation comparison, not provenance
    # reconstruction or the independent authority of an actual completed batch.
    context = {'schema': 'DEMO_CANONICAL_POSITION_CONTEXT_V1',
               'account_ref_sha256': origin['account_ref_sha256'], 'symbol': 'XAUUSDT',
               'available_at_ms': NOW-1, 'day_id': NOW//86400000,
               'cash_flow_policy': 'REPORTED_FILLS_ONLY_CONTINUATION_WALLET_MUST_MATCH_V1',
               'source_verified': False, 'quantity': '0.002', 'canonical_wallet': '4999.946',
               'initial_day_pnl': '0', 'initial_equity': '5000'}
    return origin, context


def test_gold_observed_held_book_matches_exact_wallet_and_position():
    origin, context = held()
    before = deepcopy((origin, context))
    report = position.validate_origin(origin, context, as_of_ms=NOW)
    assert report == {'positions': [{'symbol': 'XAUUSDT', 'quantity': '0.002', 'mark_price': '60000'}],
                      'cash': '4993.946', 'equity': '4999.946', 'day_pnl': '-0.054'}
    assert (origin, context) == before


@pytest.mark.parametrize('change,reason', [
    ('wallet', 'UNEXPLAINED_CASH_CHANGE'), ('quantity', 'POSITION_DIFFERENCE'),
    ('account', 'ORIGIN_BINDING'), ('symbol', 'ORIGIN_BINDING'),
    ('future', 'ORIGIN_BINDING'), ('stale', 'ORIGIN_UNRESOLVED'),
])
def test_gold_held_mismatch_is_not_replaced_by_observed_basis(change, reason):
    origin, context = held()
    at = NOW
    if change == 'wallet':
        context['canonical_wallet'] = '5000'
    elif change == 'quantity':
        context['quantity'] = '0.003'
    elif change == 'account':
        context['account_ref_sha256'] = 'f'*64
    elif change == 'symbol':
        context['symbol'] = 'BTCUSDT'
    elif change == 'future':
        context['available_at_ms'] = NOW
    else:
        at += 30001
    with pytest.raises(position.PositionContextRejected, match=reason):
        position.validate_origin(origin, context, as_of_ms=at)
