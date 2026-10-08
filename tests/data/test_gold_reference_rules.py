from copy import deepcopy
import pytest
from services.data.gold_reference_rules import session_at, funding_info

def schedule():
    return {'updateTime':900,'marketSchedules':{'COMMODITY':{'sessions':[
        {'startTime':0,'endTime':1200,'type':'REGULAR'},
        {'startTime':1200,'endTime':5000,'type':'NO_TRADING'}]}}}

def times(now=1200):
    return dict(requested_at_ms=1000,received_at_ms=1100,as_of_ms=now,max_age_ms=1000)

def rates():
    return [{'symbol':'XAUUSDT','adjustedFundingRateCap':'0.00500000',
        'adjustedFundingRateFloor':'-0.00500000','fundingIntervalHours':4,
        'disclaimer':False,'updateTime':900}]

def test_schedule_boundary_does_not_claim_binance_or_comex_is_closed():
    result=session_at(schedule(),**times())
    assert result['reference_session']=='NO_TRADING'
    assert result['binance_order_submission_available'] is None
    assert result['comex_session_open'] is None
    assert result['authoritative'] is False
    assert session_at(schedule(),**times(1199))['reference_session']=='REGULAR'

@pytest.mark.parametrize('mode',['overlap','gap','unknown','future','empty'])
def test_bad_or_uncovered_schedule_rejects(mode):
    d=schedule();s=d['marketSchedules']['COMMODITY']['sessions']
    if mode=='overlap':s[1]['startTime']=1199
    if mode=='gap':s[1]['startTime']=1201
    if mode=='unknown':s[1]['type']='OVERNIGHT'
    if mode=='future':d['updateTime']=1201
    if mode=='empty':s.clear()
    with pytest.raises(ValueError):session_at(d,**times())

@pytest.mark.parametrize('now',[1099,2001])
def test_availability_and_freshness_are_not_inferred_from_session_bounds(now):
    with pytest.raises(ValueError):session_at(schedule(),**times(now))

def test_funding_terms_are_not_realized_cash_or_a_historical_epoch():
    r=funding_info(rates(),**times())
    assert r['interval_hours']==4 and r['cap']=='0.005' and r['floor']=='-0.005'
    assert r['realized_funding_rate'] is None and r['effective_from_ms'] is None
    assert r['source_update_ms']==900 and r['available_at_ms']==1100

@pytest.mark.parametrize('mode',['missing','duplicate','nan','sign','boolean','future'])
def test_invalid_funding_terms_do_not_fall_back_to_eight_hours(mode):
    d=rates()
    if mode=='missing':d.clear()
    if mode=='duplicate':d.append(deepcopy(d[0]))
    if mode=='nan':d[0]['adjustedFundingRateCap']='NaN'
    if mode=='sign':d[0]['adjustedFundingRateFloor']='0.005'
    if mode=='boolean':d[0]['fundingIntervalHours']=True
    if mode=='future':d[0]['updateTime']=1201
    with pytest.raises(ValueError):funding_info(d,**times())
