"""Read-only recovery status never invents completed projection or cash closure."""
from copy import deepcopy

import pytest

from services.research import demo_oms_status as status
import hashlib
import json

NOW=1800000000000


def inputs():
    prepared={'reservation_sha256':'a'*64,'proposal_sha256':'b'*64,
        'orders':[{'newClientOrderId':'QDtest000001','quantity':'0.002'}]}
    plan={'schema':'DEMO_OMS_RECEIPT_RESOLUTION_V1','status':'READY_FOR_OMS_PROJECTION','issues':[],
        'reservation_sha256':'a'*64,'proposal_sha256':'b'*64,'import_sha256':'c'*64,
        'manifest_hash':'d'*64,'journal_tail':'e'*64,'resolution_at_ms':NOW,
        'submission_enabled':False,'source_verified':False,'economic_authority':False,'cash_reconciled':False,
        'orders':[{'client_order_id':'QDtest000001','venue_order_id':'9007199254740993',
            'terminal_status':'FILLED','filled_quantity':'0.002','query_record_sha256':'f'*64,
            'query_native_status':'FILLED','events':[{'received_at_ms':NOW-5,'filled_quantity':'0.002'}]}]}
    return prepared,plan


def result(plan):
    identity=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return {'schema':plan['schema'],'status':'OMS_TERMINAL_PROJECTED','plan_sha256':identity,
        'orders':1,'cash_reconciled':False,'economic_authority':False,'submission_enabled':False}


def test_pending_interrupted_and_projected_are_distinct_and_do_not_claim_cash(tmp_path):
    prepared,plan=inputs()
    pending=status.build_status(prepared,plan,observed_at_ms=NOW+1)
    assert pending['phase']=='AWAITING_PROJECTION' and pending['projected_orders'] is None
    broken=status.build_status(prepared,plan,observed_at_ms=NOW+2,interrupted=True)
    assert broken['phase']=='PROJECTION_INTERRUPTED' and broken['projected_orders'] is None
    done=status.build_status(prepared,plan,observed_at_ms=NOW+3,result=result(plan))
    assert done['phase']=='OMS_TERMINAL_PROJECTED' and done['projected_orders']==1
    assert done['cash_reconciled'] is False and done['authoritative'] is False
    assert done['orders'][0]['venue_order_id']=='9007199254740993'
    path=status.save(tmp_path,done);before=path.read_bytes()
    assert status.load_latest(tmp_path)==done and status.save(tmp_path,done)==path
    assert path.read_bytes()==before
    path.write_bytes(before.replace(b'0.002',b'0.003'))
    with pytest.raises(ValueError):status.load_latest(tmp_path)


def test_incomplete_batch_remains_unknown_even_when_some_orders_match():
    prepared,plan=inputs();plan.update(status='UNRESOLVED',orders=[],issues=['ORIGINAL_ID_QUERY_MISSING'],resolution_at_ms=None)
    raw=status.build_status(prepared,plan,observed_at_ms=NOW)
    assert raw['phase']=='INPUTS_UNRESOLVED' and raw['matched_orders']==0 and raw['order_count']==1
    assert raw['projected_orders'] is None and raw['issues']==['ORIGINAL_ID_QUERY_MISSING']
    with pytest.raises(ValueError):status.build_status(prepared,plan,observed_at_ms=NOW,result=result(plan))


@pytest.mark.parametrize('failure',['plan_hash','batch','future','cash','missing_row','wrong_quantity','interrupted_result'])
def test_unbound_or_contradictory_completion_is_rejected(failure):
    prepared,plan=inputs();outcome=result(plan);kwargs={}
    if failure=='plan_hash':outcome['plan_sha256']='0'*64
    elif failure=='batch':prepared['reservation_sha256']='0'*64
    elif failure=='future':plan['resolution_at_ms']=NOW+2;outcome=result(plan)
    elif failure=='cash':outcome['cash_reconciled']=True
    elif failure=='missing_row':plan['orders']=[];outcome=result(plan)
    elif failure=='wrong_quantity':plan['orders'][0]['filled_quantity']='0.003';outcome=result(plan)
    else:kwargs['interrupted']=True
    with pytest.raises(ValueError):status.build_status(prepared,plan,observed_at_ms=NOW+1,result=outcome,**kwargs)


def test_reloaded_status_rejects_fabricated_projection_count_and_authority():
    prepared,plan=inputs();raw=status.build_status(prepared,plan,observed_at_ms=NOW,result=result(plan))
    for key,value in [('projected_orders',0),('cash_reconciled',True),('source_verified',True),('order_count',2),('phase','FAKE')]:
        bad=deepcopy(raw);bad[key]=value
        with pytest.raises(ValueError):status.validate(bad)
