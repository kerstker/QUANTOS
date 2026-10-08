import json
from copy import deepcopy

import pytest

from tools import kraken_fee_observation as tool
from tests.data.test_kraken_fees import sample
from tests.tools.test_kraken_public_capture import capture


def fixture():
    market=capture()[0];spec,read=sample();calls=[]
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            calls.append((endpoint,params));return read['body'].encode()
    now=market['completed_at_ms']+10
    evidence=tool.collect(Reader(),market,clock_ms=lambda:now)
    return market,evidence,now,calls


def test_fee_raw_evidence_replays_and_binds_account_product_and_time():
    market,evidence,now,calls=fixture()
    assert len(calls)==1 and calls[0][0]=='TradeVolume'
    report=tool.replay(evidence,market=market,account_ref='a'*64,as_of_ms=now+10)
    assert report['taker_or_flat']['fee']=='0.26'
    assert report['fee_asset_status']=='UNKNOWN'


@pytest.mark.parametrize('case',['tampered','rehashed_report','account','stale','market'])
def test_fee_replay_cannot_accept_stale_or_changed_evidence(case):
    market,evidence,now,_=fixture();ref='a'*64
    if case in ('tampered','rehashed_report'):
        evidence['report']['taker_or_flat']['fee']='0'
        if case=='rehashed_report':evidence['result_hash']=tool.digest({k:v for k,v in evidence.items() if k!='result_hash'})
    if case=='account':ref='b'*64
    if case=='stale':now+=130000
    if case=='market':market=deepcopy(market);market['result_hash']='b'*64
    with pytest.raises(ValueError):tool.replay(evidence,market=market,account_ref=ref,as_of_ms=now)


def test_invalid_market_is_rejected_before_private_read():
    market=capture()[0]
    class Reader:
        account_ref_sha256='a'*64
        def read(self,*_):pytest.fail('private read must not occur')
    with pytest.raises(ValueError):tool.collect(Reader(),market,clock_ms=lambda:market['completed_at_ms']+130000)
