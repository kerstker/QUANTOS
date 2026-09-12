"""Execution origin binds one actual credential namespace and all pending-order classes."""
import json
from copy import deepcopy

import pytest

from tools import binance_demo_execution_origin as tool
from tests.research.test_demo_account_baseline import payload,NOW
from tests.tools.test_binance_demo_private_check import credentials
from tests.tools.test_binance_demo_statement import transport as terms_transport


def transport(*, conditional=False, isolated=False):
    index=0;calls=[];terms=terms_transport([])
    def get(path, *, params,headers):
        nonlocal index
        kind=tool.READ_PLAN[index][0];index+=1;calls.append((path,params,headers))
        if kind.startswith('clock'):data={'serverTime':NOW}
        elif kind in tool.ACCOUNT_KINDS:
            data=payload(kind)
            if kind.startswith('account'):
                data['assets'].append({'asset':'USDC','walletBalance':'5000','crossWalletBalance':'5000',
                    'availableBalance':'5000','unrealizedProfit':'0','marginBalance':'5000'})
            if kind.startswith('symbols'):
                data.append({'symbol':'BTCUSDC','marginType':'ISOLATED' if isolated else 'CROSSED','leverage':20})
        elif kind=='algo_orders':
            data=[{'algoId':9007199254740993,'clientAlgoId':'existing-stop','algoType':'CONDITIONAL',
                'orderType':'STOP_MARKET','symbol':'BTCUSDC','side':'SELL','positionSide':'BOTH',
                'quantity':'0.001','algoStatus':'NEW','reduceOnly':True,'closePosition':False,
                'createTime':NOW-1000,'updateTime':NOW-1000}] if conditional else []
        elif kind in ('rules','fees','brackets'):
            data=json.loads(terms(path,params=params,headers=headers).decode().replace('USDT','USDC'))
            if kind=='rules':
                data['symbols'][0].update(pricePrecision=1,quantityPrecision=3,baseAssetPrecision=8,quotePrecision=8,
                    orderTypes=['LIMIT','MARKET'],timeInForce=['GTX','IOC','GTC'])
        elif kind=='mark':data={'symbol':'BTCUSDC','markPrice':'60000','indexPrice':'60000','lastFundingRate':'0.0001','nextFundingTime':NOW+3600000,'time':NOW}
        elif kind=='book':data={'symbol':'BTCUSDC','bidPrice':'59999.9','askPrice':'60000.1','bidQty':'2','askQty':'2','time':NOW}
        else:raise AssertionError(kind)
        return json.dumps(data).encode()
    return get,calls


def collect(tmp_path,**options):
    get,calls=transport(**options)
    raw=tool.collect(credential_file=credentials(tmp_path),symbol='BTCUSDC',operator_read=True,get=get,clock_ms=lambda:NOW)
    return raw,calls


def test_origin_default_is_offline_without_credential_access(tmp_path,monkeypatch):
    monkeypatch.setattr(tool,'load_credentials',lambda *a:pytest.fail('credentials touched'))
    raw=tool.collect(credential_file=tmp_path/'absent',symbol='BTCUSDC',get=lambda *a,**kw:pytest.fail('network'))
    assert raw['status']=='NOT_REQUESTED' and not raw['reads']


def test_flat_quote_origin_keeps_other_quote_positions_and_binds_every_private_read(tmp_path):
    raw,calls=collect(tmp_path)
    assert raw['status']=='CAPTURED' and len(calls)==16
    private=[r for r in raw['reads'] if r['account_ref_sha256'] is not None]
    assert private and {r['account_ref_sha256'] for r in private}=={raw['account_ref_sha256']}
    assert any(path=='/fapi/v1/openAlgoOrders' for path,_,_ in calls)
    report=tool.assess(raw,as_of_ms=NOW)
    assert report['status']=='OBSERVED_FLAT_QUOTE' and report['quote_asset']=='USDC'
    assert report['wallet']=='5000' and report['other_quote_positions']==1
    assert report['settlement_rounding']=='UNVERIFIED' and report['submission_enabled'] is False
    assert report['source_verified'] is False and report['strategy_pnl'] is None
    assert 'DO_NOT_RETAIN' not in json.dumps(raw)


@pytest.mark.parametrize('options,reason',[({'conditional':True},'PREEXISTING_ALGO_ORDERS'),({'isolated':True},'TARGET_CROSS_MARGIN_REQUIRED')])
def test_conditions_that_ordinary_flat_inventory_misses_are_blocked(tmp_path,options,reason):
    raw,_=collect(tmp_path,**options)
    report=tool.assess(raw,as_of_ms=NOW)
    assert report['status']=='UNRESOLVED' and reason in report['issues']
    if options.get('conditional'):
        assert next(r for r in raw['reads'] if r['kind']=='algo_orders')['data'][0]['algoId']=='9007199254740993'


def test_identity_and_missing_conditional_coverage_cannot_be_claimed_complete(tmp_path):
    raw,_=collect(tmp_path)
    for change in ('identity','missing','future'):
        bad=deepcopy(raw)
        if change=='identity':bad['reads'][1]['account_ref_sha256']='f'*64
        elif change=='missing':bad['reads']=[r for r in bad['reads'] if r['kind']!='algo_orders']
        else:bad['reads'][-1]['received_at_ms']+=1
        with pytest.raises(ValueError):tool.validate(bad)


def test_failure_keeps_prior_inputs_without_retaining_private_error(tmp_path):
    get,calls=transport()
    def fail(path,**kwargs):
        if path=='/fapi/v1/openAlgoOrders':raise RuntimeError('SECRET must not appear')
        return get(path,**kwargs)
    raw=tool.collect(credential_file=credentials(tmp_path),symbol='BTCUSDC',operator_read=True,get=fail,clock_ms=lambda:NOW)
    assert raw['status']=='FAILED' and raw['reads']
    assert tool.assess(raw,as_of_ms=NOW)['status']=='UNRESOLVED'
    assert 'SECRET' not in json.dumps(raw)
    report=tool.assess(raw,as_of_ms=NOW)
    assert report['quote_positions'] is None and report['other_quote_positions'] is None


def test_rejected_closing_clock_retains_its_hash_and_server_time(tmp_path):
    get,calls=transport()
    def drift(path,**kwargs):
        raw=get(path,**kwargs)
        return json.dumps({'serverTime':NOW+900}).encode() if len(calls)==16 else raw
    raw=tool.collect(credential_file=credentials(tmp_path),symbol='BTCUSDC',operator_read=True,get=drift,clock_ms=lambda:NOW)
    failure=raw['failures'][0]
    assert failure['reason']=='CLOCK_UNRELIABLE' and len(raw['reads'])==15
    assert failure['server_time_ms']==NOW+900 and len(failure['response_sha256'])==64


def test_capture_is_immutable_and_loading_rejects_changed_content(tmp_path):
    from services.research.demo_execution_origin import load,save
    raw,_=collect(tmp_path)
    path=save(tmp_path/'evidence',raw)
    assert load(path)==raw and save(path.parent,raw)==path
    path.write_text(path.read_text().replace('5000','5001'))
    with pytest.raises(ValueError,match='ORIGIN_DIGEST'):load(path)


@pytest.mark.parametrize('change',['failure_kind','empty_account','unknown_algo_side','zero_algo_id'])
def test_origin_rejects_unbound_failure_and_malformed_pending_order(tmp_path,change):
    raw,_=collect(tmp_path,conditional=True)
    if change=='failure_kind':
        raw.update(status='FAILED',reads=raw['reads'][:5],failures=[{'kind':'book','requested_at_ms':NOW,
            'received_at_ms':NOW,'reason':'REQUEST_FAILED','response_sha256':None,'server_time_ms':None}])
    elif change=='empty_account':raw['account_ref_sha256']=None
    else:
        order=next(r for r in raw['reads'] if r['kind']=='algo_orders')['data'][0]
        order['side' if change=='unknown_algo_side' else 'algoId']='INVALID' if change=='unknown_algo_side' else '0'
    with pytest.raises(ValueError):tool.validate(raw)


def test_old_origin_is_observation_only_and_never_execution_ready(tmp_path):
    raw,_=collect(tmp_path)
    report=tool.assess(raw,as_of_ms=NOW+30001)
    assert report['status']=='UNRESOLVED' and 'ORIGIN_STALE' in report['issues']
    assert report['submission_enabled'] is False
    older=tool.assess(raw,as_of_ms=NOW+120001)
    assert 'ACCOUNT_NOT_STABLE' not in older['issues']


def test_public_transport_rejects_headers_and_extra_query_before_network(monkeypatch):
    monkeypatch.setattr(tool.urllib.request,'build_opener',lambda *a:pytest.fail('network'))
    for path,params,headers in [('/fapi/v1/premiumIndex','symbol=BTCUSDC',{'X-MBX-APIKEY':'fake'}),
                                 ('/fapi/v1/time','timestamp=1',{}),('/fapi/v1/order','',{}),
                                 ('/fapi/v1/ticker/bookTicker','symbol=ETHUSDC',{})]:
        with pytest.raises(tool.ProbeFailure):tool.get_response(path,params=params,headers=headers)


def test_empty_target_brackets_do_not_claim_complete_execution_inputs(tmp_path):
    get,_=transport()
    def missing(path,**kwargs):
        raw=get(path,**kwargs)
        return b'[]' if path=='/fapi/v1/leverageBracket' else raw
    raw=tool.collect(credential_file=credentials(tmp_path),symbol='BTCUSDC',operator_read=True,get=missing,clock_ms=lambda:NOW)
    assert raw['status']=='FAILED' and raw['failures'][0]['reason']=='RESPONSE_INVALID'


def test_negative_free_wallet_cannot_be_used_as_a_funded_origin(tmp_path):
    raw,_=collect(tmp_path)
    for read in raw['reads']:
        if read['kind'].startswith('account'):
            asset=next(a for a in read['data']['assets'] if a['asset']=='USDC')
            for name in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[name]='-1'
    report=tool.assess(raw,as_of_ms=NOW)
    assert report['status']=='UNRESOLVED' and 'QUOTE_WALLET_NOT_FREE_AND_FLAT' in report['issues']


def test_v2_retains_order_count_limits_and_v1_evidence_remains_readable(tmp_path):
    get,_=transport()
    def limits(path,**kwargs):
        raw=get(path,**kwargs)
        if path=='/fapi/v1/exchangeInfo':
            data=json.loads(raw);data['symbols'][0]['filters'].append({'filterType':'MAX_NUM_ORDERS','limit':200})
            return json.dumps(data).encode()
        return raw
    raw=tool.collect(credential_file=credentials(tmp_path),symbol='BTCUSDC',operator_read=True,get=limits,clock_ms=lambda:NOW)
    assert raw['schema']=='DEMO_EXECUTION_ORIGIN_V3'
    rules=next(r for r in raw['reads'] if r['kind']=='rules')['data']
    assert rules['order_count_limits']=={'MAX_NUM_ORDERS':200}
    legacy=deepcopy(raw);legacy['schema']='DEMO_EXECUTION_ORIGIN_V1'
    del next(r for r in legacy['reads'] if r['kind']=='rules')['data']['venue_filters']
    del next(r for r in legacy['reads'] if r['kind']=='rules')['data']['order_count_limits']
    assert tool.validate(legacy)==legacy
    del rules['order_count_limits']
    with pytest.raises(ValueError):tool.validate(raw)


def test_new_origin_retains_all_filter_fields_and_rejects_projection_conflicts(tmp_path):
    raw,_=collect(tmp_path)
    assert raw['schema']=='DEMO_EXECUTION_ORIGIN_V3'
    rules=next(r for r in raw['reads'] if r['kind']=='rules')['data']
    assert {f['filterType'] for f in rules['venue_filters']}==set(rules['filter_types'])
    legacy=deepcopy(raw);legacy['schema']='DEMO_EXECUTION_ORIGIN_V2'
    del next(r for r in legacy['reads'] if r['kind']=='rules')['data']['venue_filters']
    assert tool.validate(legacy)==legacy
    lot=next(f for f in rules['venue_filters'] if f['filterType']=='LOT_SIZE')
    lot['stepSize']='1'
    with pytest.raises(ValueError):tool.validate(raw)


def test_control_filter_fields_are_retained_with_no_semantic_upgrade(tmp_path):
    get,_=transport()
    def control(path,**kwargs):
        raw=get(path,**kwargs)
        if path=='/fapi/v1/exchangeInfo':
            data=json.loads(raw);data['symbols'][0]['filters'].append({'filterType':'POSITION_RISK_CONTROL','positionControlSide':'NONE'})
            return json.dumps(data).encode()
        return raw
    raw=tool.collect(credential_file=credentials(tmp_path),symbol='BTCUSDC',operator_read=True,get=control,clock_ms=lambda:NOW)
    rules=next(r for r in raw['reads'] if r['kind']=='rules')['data']
    assert rules['venue_filters'][-1]=={'filterType':'POSITION_RISK_CONTROL','positionControlSide':'NONE'}
    assert tool.assess(raw,as_of_ms=NOW)['source_verified'] is False
