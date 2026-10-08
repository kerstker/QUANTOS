"""Actual receive clocks, frozen windows and quote isolation for new information."""
import copy
import numpy as np
import pytest

from services.research.profitability_causal_information import make_plan, observation, panel

HOUR=3600000


def receipts(slot,quote='USDC'):
    rows=[]
    for i in range(25):
        at=slot-(25-i)*HOUR
        rows.append([at,'100','101','99',str(100+i),'1',at+HOUR-1,'100','1','1','60'])
    result={'hourly':{'payload':rows,'received_ms':slot+1000}}
    for kind,price in [('spot',100),('perpetual',101),('index',100.5)]:
        result[kind]={'payload':[[slot-120000,0,0,0,str(price),0,slot-60001],
                                 [slot-60000,0,0,0,str(price+.1),0,slot-1]],'received_ms':slot+1000}
    result['funding']={'payload':[{'symbol':'BTC'+quote,'fundingTime':slot-8*HOUR,'fundingRate':'.0001'},
                                  {'symbol':'BTC'+quote,'fundingTime':slot,'fundingRate':'.0002'}], 'received_ms':slot+1000}
    result['oi']={'payload':{'symbol':'BTC'+quote,'time':slot+500,'openInterest':'1000'},'received_ms':slot+1000}
    return result


def test_snapshot_retains_actual_receive_clock_and_separate_currency():
    slot=48*HOUR
    a=observation(receipts(slot),slot=slot,quote='USDC',issued_ms=slot+2000)
    assert a['available_ms']==slot+1000 and a['issued_ms']==slot+2000
    assert a['entry_ms']==slot+300000 and len(a['basis'])==4
    assert a['funding_event_ms']==slot and a['funding_interval_ms']==8*HOUR
    assert not a['prediction_issued'] and not a['economic_eligible']
    with pytest.raises(ValueError,match='currency'):observation(receipts(slot),slot=slot,quote='USDT',issued_ms=slot+2000)


@pytest.mark.parametrize('change', ['late','future_oi','stale_oi','funding_gap','future_receive','missing_price','bad_hour_clock'])
def test_rejects_backdating_incomplete_or_changed_clock(change):
    slot=48*HOUR;data=receipts(slot);issued=slot+2000
    if change=='late':issued=slot+300000
    if change=='future_oi':data['oi']['payload']['time']=slot+3000
    if change=='stale_oi':data['oi']['payload']['time']=slot-600000
    if change=='funding_gap':data['funding']['payload'][0]['fundingTime']-=HOUR
    if change=='future_receive':data['spot']['received_ms']=slot+3000
    if change=='missing_price':data['index']['payload'].pop()
    if change=='bad_hour_clock':data['hourly']['payload'][0][0]+=1
    with pytest.raises(ValueError):observation(data,slot=slot,quote='USDC',issued_ms=issued)


def test_plan_fixed_before_window_and_panel_never_backfills_missing_slots():
    plan=make_plan(now_ms=47*HOUR+1,p0_hash='a'*64)
    assert plan['warmup_ms']==48*HOUR and plan['samples']==504
    row=observation(receipts(48*HOUR),slot=48*HOUR,quote='USDC',issued_ms=48*HOUR+2000)
    result=panel(plan,[row],quote='USDC',as_of_ms=49*HOUR)
    assert result['status']=='COLLECTING' and result['ready_rows']==0
    assert result['missing_slots']==[]
    result=panel(plan,[row],quote='USDC',as_of_ms=50*HOUR)
    assert result['missing_slots']==[49*HOUR]
    with pytest.raises(ValueError):panel({**plan,'samples':12},[],quote='USDC',as_of_ms=50*HOUR)


def test_panel_rejects_duplicate_receipts_and_mixed_quotes():
    plan=make_plan(now_ms=47*HOUR+1,p0_hash='a'*64)
    row=observation(receipts(48*HOUR),slot=48*HOUR,quote='USDC',issued_ms=48*HOUR+2000)
    for rows in ([row,row],[{**row,'quote':'USDT'}]):
        with pytest.raises(ValueError):panel(plan,rows,quote='USDC',as_of_ms=49*HOUR)


def test_panel_does_not_treat_collected_features_as_preissued_predictions():
    plan=make_plan(now_ms=47*HOUR+1,p0_hash='a'*64);rows=[]
    for slot in range(plan['warmup_ms'],plan['end_slot_ms']+HOUR,HOUR):
        rows.append(observation(receipts(slot),slot=slot,quote='USDC',issued_ms=slot+2000))
    result=panel(plan,rows,quote='USDC',as_of_ms=plan['mature_ms'])
    assert result['status']=='READY_FOR_REGISTERED_RESEARCH' and result['ready_rows']==504
    assert np.asarray(result['price_features']).shape==(504,5)
    assert np.asarray(result['basis_features']).shape==(504,6)
    assert np.asarray(result['information_features']).shape==(504,6)
    assert result['prediction_evidence']=='NONE_FEATURE_COLLECTION_ONLY'
    changed=copy.deepcopy(rows);changed[-1]['available_ms']=changed[-1]['entry_ms']
    with pytest.raises(ValueError):panel(plan,changed,quote='USDC',as_of_ms=plan['mature_ms'])
