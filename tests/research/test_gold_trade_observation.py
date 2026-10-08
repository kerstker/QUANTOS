"""External fill facts must not become invented account or strategy returns."""
import copy
import json
from decimal import localcontext

import pytest

from services.research.gold_trade_observation import assess, load_document, ObservationError


def document():
    return {'schema': 'GOLD_EXTERNAL_FILLS_V1', 'venue': 'BINANCE',
        'environment': 'MAINNET', 'account_ref': 'a'*64, 'symbol': 'XAUUSDT',
        'start_ms': 1000, 'end_ms': 2000, 'exported_at_ms': 3000,
        'fills': [{'trade_id': '123', 'order_id': '456', 'time_ms': 1500,
            'position_side': 'BOTH', 'side': 'BUY', 'price': '4000.10',
            'quantity': '0.01', 'commission': '0.0200005',
            'commission_asset': 'USDT', 'reported_realized_pnl': '0'}]}


def native_document():
    d = document()
    d['schema'] = 'GOLD_BINANCE_USER_TRADES_V1'
    d['fills'] = [{'symbol': 'XAUUSDT', 'id': 123, 'orderId': 456,
        'time': 1500, 'positionSide': 'BOTH', 'side': 'BUY',
        'price': '4000.10', 'qty': '0.01', 'quoteQty': '40.001',
        'commission': '0.0200005', 'commissionAsset': 'USDT',
        'realizedPnl': '0', 'buyer': True, 'maker': False}]
    return d


def test_native_binance_fills_map_without_mutation_or_manual_attribution():
    from services.research.gold_trade_observation import normalize_binance_trades
    d = native_document()
    original = copy.deepcopy(d)
    normalized = normalize_binance_trades(d)
    assert normalized == document()
    assert d == original
    assert assess(normalized)['history_complete'] is False
    assert assess(normalized)['strategy_pnl'] is None


@pytest.mark.parametrize('field,value', [('symbol', 'BTCUSDT'), ('id', True),
    ('orderId', -1), ('buyer', False), ('maker', 'false'),
    ('qty', 0.01), ('marginAsset', 'BTC'), ('baseQty', '1'),
    ('pair', 'BTCUSDT'), ('secret', 'private-value')])
def test_native_rejects_scope_type_and_conflicting_metadata(field, value):
    from services.research.gold_trade_observation import normalize_binance_trades
    d = native_document()
    d['fills'][0][field] = value
    with pytest.raises(ObservationError) as exc:
        normalize_binance_trades(d)
    assert 'private-value' not in str(exc.value)


def test_native_duplicate_metadata_conflict_is_not_hidden_by_projection():
    from services.research.gold_trade_observation import normalize_binance_trades
    d = native_document()
    d['fills'].append(dict(d['fills'][0], maker=True))
    with pytest.raises(ObservationError, match='CONFLICTING_NATIVE_TRADE_ID'):
        normalize_binance_trades(d)


def test_native_optional_unified_fields_and_duplicate_replay():
    from services.research.gold_trade_observation import normalize_binance_trades
    d = native_document()
    d['fills'][0].update(pair='XAUUSDT', marginAsset='USDT', baseQty='0')
    d['fills'].append(dict(d['fills'][0]))
    assert assess(normalize_binance_trades(d))['duplicate_fills'] == 1


def test_partial_facts_never_claim_complete_account_or_strategy_performance():
    r = assess(document())
    assert r['unique_fills'] == 1
    assert r['commission_by_asset'] == {'USDT': '0.0200005'}
    assert r['history_complete'] is False
    assert r['account_return'] is None and r['strategy_pnl'] is None
    assert r['authoritative'] is False


def test_repeated_fill_is_idempotent_but_conflict_rejected():
    d = document()
    d['fills'].append(copy.deepcopy(d['fills'][0]))
    assert assess(d)['unique_fills'] == 1
    assert assess(d)['duplicate_fills'] == 1
    d['fills'][1]['quantity'] = '0.02'
    with pytest.raises(ObservationError, match='CONFLICTING_TRADE_ID'):
        assess(d)


def test_fees_keep_their_currency_and_hedged_sides_remain_separate():
    d = document()
    d['fills'][0]['position_side'] = 'LONG'
    second = dict(d['fills'][0], trade_id='124', position_side='SHORT',
                  side='SELL', commission='0.0001', commission_asset='BNB',
                  reported_realized_pnl='-2.5')
    d['fills'].append(second)
    r = assess(d)
    assert r['commission_by_asset'] == {'BNB': '0.0001', 'USDT': '0.0200005'}
    assert r['reported_realized_pnl_usdt'] == '-2.5'
    assert r['fill_counts_by_position_side'] == {'LONG': 1, 'SHORT': 1}
    assert r['net_pnl_usdt'] is None


@pytest.mark.parametrize('field,value', [('price','NaN'), ('price','Infinity'),
    ('quantity','0'), ('quantity',0.01), ('time_ms',True), ('time_ms',2000),
    ('trade_id','00123'), ('position_side','NET'), ('side','sell'),
    ('commission_asset','usdt'), ('reported_realized_pnl','1e9')])
def test_malformed_or_outside_half_open_window_rejected(field, value):
    d = document()
    d['fills'][0][field] = value
    with pytest.raises(ObservationError):
        assess(d)


@pytest.mark.parametrize('field,value', [('symbol','XAUTUSDT'),
    ('environment','DEMO'), ('venue','OTHER'), ('start_ms',True),
    ('end_ms',1000), ('exported_at_ms',1999), ('account_ref','account-email')])
def test_wrong_contract_environment_and_invalid_identity_rejected(field,value):
    d = document()
    d[field] = value
    with pytest.raises(ObservationError):
        assess(d)


def test_unknown_fields_and_duplicate_json_keys_fail_without_echoing_content():
    d = document()
    d['secret'] = 'private-value'
    with pytest.raises(ObservationError) as e:
        assess(d)
    assert 'private-value' not in str(e.value)
    with pytest.raises(ObservationError):
        load_document(b'{"schema":1,"schema":2}')


def test_precision_is_independent_of_callers_decimal_context_and_input_order():
    d = document()
    d['fills'].append(dict(d['fills'][0], trade_id='124', commission='0.000000000000000001'))
    with localcontext() as ctx:
        ctx.prec = 3
        r = assess(d)
    assert r['commission_by_asset']['USDT'] == '0.020000500000000001'
    d['fills'].reverse()
    assert assess(d)['facts_sha256'] == r['facts_sha256']


def test_empty_is_no_facts_not_zero_profit():
    d = document()
    d['fills'] = []
    r = assess(load_document(json.dumps(d).encode()))
    assert r['status'] == 'NO_FILL_FACTS'
    assert r['net_pnl_usdt'] is None
