"""Public account inputs must exist before the venue-time replay begins."""
import copy
import hashlib
import json
from decimal import Decimal as D
import pytest

from tests.research.test_passive_execution_input import tape
from services.execution.passive_execution_input import compile_tape
from services.execution.passive_account_environment import prepare_environment, endpoint

DAY=86400000
BASE=40*DAY


def receipt(kind,payload,at=BASE+900):
    raw=json.dumps(payload,separators=(',',':'))
    return {'kind':kind,'endpoint':endpoint(kind,'BTCUSDC',BASE),
        'requested_at':at-10,'received_at':at,'raw_utf8':raw,
        'response_sha256':hashlib.sha256(raw.encode()).hexdigest()}


def fixture():
    raw=tape()
    for key in ('started_at_ms','ended_at_ms'):raw[key]+=BASE
    raw['snapshot']['received_at_ms']+=BASE
    payload=json.loads(raw['snapshot']['raw_utf8'])
    for key in ('E','T'):
        if key in payload:payload[key]+=BASE
    raw['snapshot']['raw_utf8']=json.dumps(payload)
    for row in raw['messages']:
        row['received_at_ms']+=BASE;p=json.loads(row['raw_utf8'])
        for key in ('E','T'):p['data'][key]+=BASE
        row['raw_utf8']=json.dumps(p)
    for row in raw['connections'].values():
        for key in ('started_at_ms','connected_at_ms','closed_at_ms'):row[key]+=BASE
    symbol={'symbol':'BTCUSDC','contractType':'PERPETUAL','quoteAsset':'USDC',
        'marginAsset':'USDC','status':'TRADING','orderTypes':['LIMIT','MARKET'],
        'filters':[{'filterType':'PRICE_FILTER','minPrice':'0.1','maxPrice':'1000000','tickSize':'0.1'},
            {'filterType':'LOT_SIZE','minQty':'0.001','maxQty':'100','stepSize':'0.001'},
            {'filterType':'MARKET_LOT_SIZE','minQty':'0.001','maxQty':'100','stepSize':'0.001'},
            {'filterType':'MIN_NOTIONAL','notional':'1'}]}
    candles=[[t,'100','100','100','100','1',t+DAY-1,'1000000',10,'1','100','0']
        for t in range(BASE-30*DAY,BASE,DAY)]
    env={'schema':'PASSIVE_VENUE_ENVIRONMENT_V1','symbol':'BTCUSDC',
        'before':{k:receipt(k,p) for k,p in {'exchange':{'symbols':[symbol]},'candles':candles,
            'funding':[{'symbol':'BTCUSDC','fundingTime':BASE,'fundingRate':'0.001'}],
            'premium':{'symbol':'BTCUSDC','time':BASE+880,'nextFundingTime':BASE+120000}}.items()},
        'exchange_after':receipt('exchange',{'symbols':[symbol]},BASE+1300)}
    return raw,env


def prepare(raw,env):return prepare_environment(env,raw,compile_tape(raw),max_order_notional=D('1000'))


def test_actual_mark_clock_and_prior_metadata_produce_canonical_frames():
    raw,env=fixture();before=copy.deepcopy((raw,env));out=prepare(raw,env)
    assert (raw,env)==before
    assert out['account_frames'][0].timestamp==BASE+1055
    assert out['account_frames'][0].market_events[0].timestamp==BASE+1055
    assert out['manifest']['mark_clocks'][0]['received_at_ms']==BASE+1057
    assert out['funding_policies'][0].ingestion_time==BASE+900
    assert out['contract_rules'][0].spec.margin_asset=='USDC'
    assert out['order_rule_profiles'][0].price_tick==D('.1')
    assert out['account_frames'][0].capacity_snapshots[0].policy.adv_notional==D('1000000')
    assert out['manifest']['actual_venue_fees'] is None
    assert out['manifest']['venue_margin_tiers_verified'] is False


@pytest.mark.parametrize('change',['late','hash','future_candle','filters','settlement','mixed_quote','endpoint','mark_schedule'])
def test_missing_or_noncausal_public_account_inputs_cannot_be_silently_assumed(change):
    raw,env=fixture()
    if change=='late':env['before']['premium']['received_at']=BASE+1056
    elif change=='hash':env['before']['exchange']['response_sha256']='0'*64
    elif change=='future_candle':
        p=json.loads(env['before']['candles']['raw_utf8']);p[-1][0]+=DAY
        env['before']['candles']=receipt('candles',p)
    elif change=='filters':
        p=json.loads(env['exchange_after']['raw_utf8']);p['symbols'][0]['filters'][0]['tickSize']='1'
        env['exchange_after']=receipt('exchange',p,BASE+1300)
    elif change=='settlement':
        p=json.loads(env['before']['premium']['raw_utf8']);p['nextFundingTime']=BASE+1100
        env['before']['premium']=receipt('premium',p)
    elif change=='mixed_quote':env['symbol']='BTCUSDT'
    elif change=='endpoint':env['before']['funding']['endpoint']='https://example.invalid/'
    else:
        p=json.loads(raw['messages'][2]['raw_utf8']);p['data']['T']+=1000
        raw['messages'][2]['raw_utf8']=json.dumps(p)
    with pytest.raises(ValueError):prepare(raw,env)


@pytest.mark.parametrize('payload',[None,[],{}, {'symbols':[{'symbol':'BTCUSDC'}]}])
def test_malformed_raw_exchange_json_becomes_an_input_validation_error(payload):
    raw,env=fixture();env['before']['exchange']=receipt('exchange',payload)
    with pytest.raises(ValueError):prepare(raw,env)
