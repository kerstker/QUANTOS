import json
from copy import deepcopy

import pytest

from services.data import kraken_fees as fees
from tests.tools.test_kraken_public_capture import capture

NOW=1700000000000


def sample():
    spec=capture()[0]['instrument']
    row={'fee':'0.26','minfee':'0.1','maxfee':'0.4'}
    read={'endpoint':'TradeVolume','params':fees.params(spec),'account_ref_sha256':'a'*64,
          'requested_at_ms':NOW,'received_at_ms':NOW+10,
          'body':json.dumps({'error':[],'result':{'currency':'ZUSD','volume':'1000',
                  'fees':{spec['pair_key']:row},'fees_maker':{spec['pair_key']:dict(row,fee='0.16')}}})}
    return spec,read


def test_class_qualified_fee_query_and_exact_observed_schedule():
    spec,read=sample()
    assert read['params']=={'pair':[{'asset':spec['pair'],'aclass':'equity_pair'}],
                            'fee_schedule':False,'rebase_multiplier':'base'}
    report=fees.assess(read,spec=spec,as_of_ms=NOW+20)
    assert report['taker_or_flat']['fee']=='0.26'
    assert report['maker']['fee']=='0.16'
    assert report['instrument_id']==spec['instrument_id']
    assert report['fee_asset_status']=='UNKNOWN'
    assert report['submission_enabled'] is False


@pytest.mark.parametrize('case',['missing','other_pair','other_class','stale','future','range','nan','duplicate','account','flag_type','missing_rate'])
def test_fee_evidence_must_match_exact_request_and_fresh_product(case):
    spec,read=sample();p=json.loads(read['body'])
    if case=='missing':p['result']['fees']={}
    if case=='other_pair':p['result']['fees']={'UNKNOWN':next(iter(p['result']['fees'].values()))}
    if case=='other_class':read['params']['pair'][0]['aclass']='forex'
    if case=='stale':read['requested_at_ms']-=130000;read['received_at_ms']-=130000
    if case=='future':read['received_at_ms']=NOW+30
    if case=='range':p['result']['fees'][spec['pair_key']]['fee']='0.5'
    if case=='nan':p['result']['fees'][spec['pair_key']]['fee']='NaN'
    if case=='account':read['account_ref_sha256']='unknown'
    if case=='flag_type':read['params']['fee_schedule']=0
    if case=='missing_rate':p['result']['fees'][spec['pair_key']].pop('maxfee')
    read['body']=json.dumps(p)
    if case=='duplicate':read['body']=read['body'].replace('"fee": "0.26"','"fee": "0.26", "fee": "0"')
    with pytest.raises(ValueError):fees.assess(read,spec=spec,as_of_ms=NOW+20)


def test_flat_schedule_does_not_invent_maker_discount():
    spec,read=sample();p=json.loads(read['body']);p['result'].pop('fees_maker');read['body']=json.dumps(p)
    report=fees.assess(read,spec=spec,as_of_ms=NOW+20)
    assert report['maker'] is None
    currency=deepcopy(spec);currency['asset_class']='currency'
    assert fees.params(currency)['pair'][0]['aclass']=='forex'
