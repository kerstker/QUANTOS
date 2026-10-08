from copy import deepcopy

import pytest

from services.data.gold_contract_profile import parse_profile, ProfileError


def metadata():
    return {'symbols': [{'symbol': 'XAUUSDT', 'pair': 'XAUUSDT',
        'contractType': 'TRADIFI_PERPETUAL', 'status': 'TRADING', 'baseAsset': 'XAU',
        'quoteAsset': 'USDT', 'marginAsset': 'USDT', 'filters': [
            {'filterType': 'PRICE_FILTER','minPrice':'0.01','maxPrice':'200000','tickSize':'0.01'},
            {'filterType': 'LOT_SIZE','minQty':'0.001','maxQty':'10000','stepSize':'0.001'},
            {'filterType': 'MARKET_LOT_SIZE','minQty':'0.001','maxQty':'1000','stepSize':'0.001'},
            {'filterType': 'MIN_NOTIONAL','notional':'5'}]}]}


def parse(data=None, **kwargs):
    return parse_profile(metadata() if data is None else data,
        requested_at_ms=1000, received_at_ms=1100, as_of_ms=kwargs.get('as_of_ms',1200),
        max_age_ms=kwargs.get('max_age_ms',1000))


def test_exact_filters_identity_and_no_execution_authority():
    p = parse()
    assert p['price']['tick'] == '0.01'
    assert p['limit_quantity']['step'] == '0.001'
    assert p['market_quantity']['max'] == '1000'
    assert p['min_notional'] == '5'
    assert p['available_at_ms'] == 1100
    assert p['execution_eligible'] is False
    assert p['contract_multiplier'] is None


@pytest.mark.parametrize('key,value', [('contractType','PERPETUAL'),('symbol','XAUTUSDT'),
    ('pair','XAUTUSDT'),('marginAsset','USDC'),('status','BREAK')])
def test_wrong_gold_contract_cannot_be_silently_admitted(key,value):
    d=metadata(); d['symbols'][0][key]=value
    with pytest.raises(ProfileError): parse(d)


def test_missing_duplicate_filters_and_symbols_rejected():
    for mode in ('missing','duplicate','symbol'):
        d=metadata(); row=d['symbols'][0]
        if mode=='missing': row['filters'].pop()
        elif mode=='duplicate': row['filters'].append(deepcopy(row['filters'][0]))
        else: d['symbols'].append(deepcopy(row))
        with pytest.raises(ProfileError): parse(d)


@pytest.mark.parametrize('value',['NaN','Infinity','0','-1','1e-2',0.01])
def test_invalid_tick_fails(value):
    d=metadata(); d['symbols'][0]['filters'][0]['tickSize']=value
    with pytest.raises(ProfileError): parse(d)


def test_bad_range_and_non_grid_bounds_rejected():
    for value in ('0.0001','0.0015'):
        d=metadata(); d['symbols'][0]['filters'][1]['maxQty']=value
        with pytest.raises(ProfileError): parse(d)


@pytest.mark.parametrize('args',[{'as_of_ms':1099},{'as_of_ms':2101},
    {'max_age_ms':True},{'max_age_ms':0},{'as_of_ms':True}])
def test_unavailable_or_stale_metadata_fails(args):
    with pytest.raises(ProfileError): parse(**args)


def test_response_digest_changes_when_other_venue_constraints_change():
    d=metadata(); before=parse(d)
    d['symbols'][0]['filters'].append({'filterType':'PERCENT_PRICE','multiplierUp':'1.02'})
    after=parse(d)
    assert before['response_sha256'] != after['response_sha256']
    assert after['unmodeled_filters'] == ['PERCENT_PRICE']
