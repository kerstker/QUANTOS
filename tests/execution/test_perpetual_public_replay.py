"""Frozen public-data boundary: causal clocks, corruption, gaps and missing rules."""
from copy import deepcopy
import base64
import hashlib
import json
import pytest
from services.execution.backtest_authority import exact_identity_hash

DAY=86_400_000
START=20_000*DAY+10_000


def sealed(doc):
    doc=deepcopy(doc)
    def bind(value):
        if isinstance(value,dict):
            if 'kind' in value and 'payload' in value:
                raw=json.dumps(value['payload']).encode()
                value['response_base64']=base64.b64encode(raw).decode()
                value['response_sha256']=hashlib.sha256(raw).hexdigest()
            else:
                for child in value.values(): bind(child)
        elif isinstance(value,list):
            for child in value: bind(child)
    bind(doc)
    doc.pop('capture_id',None)
    doc['capture_id']=exact_identity_hash(doc,name='public_replay_capture')
    return doc


def capture_fixture():
    def record(kind,t,payload):
        raw=json.dumps(payload).encode()
        return {'kind':kind,'requested_at':t-1,'received_at':t,'endpoint':'/fapi/v1/fixture','payload':payload,
                'response_base64':base64.b64encode(raw).decode(),'response_sha256':hashlib.sha256(raw).hexdigest()}
    symbol={'symbol':'BTCUSDT','contractType':'PERPETUAL','marginAsset':'USDT','status':'TRADING',
            'orderTypes':['MARKET','LIMIT'],'filters':[
                {'filterType':'PRICE_FILTER','minPrice':'0.1','maxPrice':'100000','tickSize':'0.1'},
                {'filterType':'LOT_SIZE','minQty':'0.001','maxQty':'100','stepSize':'0.001'},
                {'filterType':'MARKET_LOT_SIZE','minQty':'0.001','maxQty':'10','stepSize':'0.001'},
                {'filterType':'MIN_NOTIONAL','notional':'5'}]}
    day=START//DAY*DAY
    candles=[[t,'100','100','100','100','10000',t+DAY-1,'1000000',1,'0','0','0']
             for t in range(day-30*DAY,day,DAY)]
    document={'schema_version':'BINANCE_PUBLIC_REPLAY_CAPTURE_V2','symbol':'BTCUSDT',
        'max_gap_ms':30_000,'declared_interval_ms':1000,
        'bootstrap':{'exchange':record('exchange',START-100,{'symbols':[symbol]}),
            'candles':record('candles',START-80,candles),
            'funding':record('funding',START-60,[{'symbol':'BTCUSDT','fundingTime':day,'fundingRate':'0.0001'}])},
        'samples':[{'mark':record('mark',START+i*1000+1,{'symbol':'BTCUSDT','time':START+i*1000,
                       'markPrice':'100','nextFundingTime':day+8*3_600_000}),
                    'depth':record('depth',START+i*1000+2,{'T':START+i*1000,'E':START+i*1000,
                       'lastUpdateId':i+1,'bids':[['99.9','1']],'asks':[['100.1','1']]})}
                   for i in range(4)],
        'exchange_after':record('exchange',START+4000,{'symbols':[symbol]})}
    return sealed(document)


def prepare(doc):
    from services.execution.perpetual_public_replay import prepare_public_replay
    return prepare_public_replay(doc)


def test_frozen_capture_builds_causal_frames_and_exact_adv():
    p=prepare(capture_fixture())
    assert len(p['frames'])==4
    assert p['frames'][0].quotes[0].event_time==START
    assert p['frames'][0].timestamp==START+2
    assert str(p['frames'][0].capacity_snapshots[0].policy.adv_notional)=='1000000'
    assert p['manifest']['authority']=='TECHNICAL_VALIDATION_ONLY'


def test_mutated_bytes_are_rejected_before_parsing():
    d=capture_fixture();d['samples'][0]['mark']['payload']['markPrice']='101'
    with pytest.raises(ValueError,match='capture identity'): prepare(d)


def test_raw_response_mismatch_is_rejected_even_with_rehashed_envelope():
    d=capture_fixture();d['bootstrap']['exchange']['response_sha256']='0'*64
    d.pop('capture_id');d['capture_id']=exact_identity_hash(d,name='public_replay_capture')
    with pytest.raises(ValueError,match='raw public response'): prepare(d)


@pytest.mark.parametrize('mutation', ['late_bootstrap','future_quote','old_mark','duplicate_level','missing_filter','changed_rules','missing_day','out_of_order'])
def test_invalid_or_unsupported_public_evidence_is_fail_closed(mutation):
    d=capture_fixture()
    if mutation=='late_bootstrap': d['bootstrap']['candles']['received_at']=START+5000
    elif mutation=='future_quote': d['samples'][0]['depth']['payload']['T']=START+1000
    elif mutation=='old_mark': d['samples'][0]['mark']['payload']['time']=START-40_000
    elif mutation=='duplicate_level': d['samples'][0]['depth']['payload']['bids']*=2
    elif mutation=='missing_filter': d['bootstrap']['exchange']['payload']['symbols'][0]['filters'].pop()
    elif mutation=='changed_rules': d['exchange_after']['payload']['symbols'][0]['filters'][0]['tickSize']='1'
    elif mutation=='missing_day': d['bootstrap']['candles']['payload'].pop()
    elif mutation=='out_of_order': d['samples'].reverse()
    with pytest.raises(ValueError): prepare(sealed(d))
