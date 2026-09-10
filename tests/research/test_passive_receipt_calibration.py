"""Receipt fitting is causal in receipt time and measures the complete fill path."""
from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace
import pytest

from services.execution.passive_queue import Action
from services.research.passive_receipt_calibration import (
    validate_calibration, compare_receipts, calibration_score, queue_receipts,
)

HASH='a'*64
ACTIONS=(Action(100,'ORDER','a','BUY',D('1'),D('99')),)
TAPE=SimpleNamespace(symbol='BTCUSDC',start_ms=100,end_ms=300)


def trace(events):
    return {'action_hash':HASH,'symbol':'BTCUSDC',
        'coverage':{'start_ms':100,'end_ms':300,'gaps':[]},
        'provenance':{'kind':'SYNTHETIC_TEST_FIXTURE','declared_source_sha256':'b'*64},
        'orders':[{'order_id':'a','side':'BUY','quantity':'1','limit_price':'99',
            'events':[{'receipt_id':str(i),'venue_ms':venue,'received_ms':received,
                'state':state,'cumulative_filled':filled} for i,(venue,received,state,filled) in enumerate(events)]}]}


def spec(observed):return {'schema':'PASSIVE_RECEIPT_CALIBRATION_V1','cutoff_ms':150,'observed':observed}


def test_same_final_fill_does_not_hide_wrong_partial_fill_path():
    observed=trace([(101,111,'ACK','0'),(121,131,'PARTIALLY_FILLED','0.4'),(151,161,'FILLED','1')])
    simulated=trace([(101,111,'ACK','0'),(121,131,'PARTIALLY_FILLED','0.6'),(151,161,'FILLED','1')])
    validate_calibration(spec(observed),TAPE,ACTIONS,HASH)
    report=compare_receipts(observed,simulated,ACTIONS,start_ms=100,end_ms=200)
    assert report['final_quantity_absolute_error']=='0'
    assert report['matched_event_quantity_absolute_error']=='0.2'
    assert report['quantity_path_error_btc_ms']=='6'
    assert calibration_score(report)>calibration_score(compare_receipts(observed,observed,ACTIONS,start_ms=100,end_ms=200))


def test_prefix_uses_actual_receipt_clock_instead_of_earlier_venue_event():
    observed=trace([(101,111,'ACK','0'),(120,170,'FILLED','1')])
    simulated=trace([(101,111,'ACK','0'),(121,171,'FILLED','1')])
    before=compare_receipts(observed,simulated,ACTIONS,start_ms=100,end_ms=150)
    assert before['observed_events']==before['simulated_events']==1
    assert before['quantity_path_error_btc_ms']=='0'
    future=deepcopy(observed);future['orders'][0]['events'][1]['venue_ms']=130
    assert compare_receipts(future,simulated,ACTIONS,start_ms=100,end_ms=150)==before
    after=compare_receipts(observed,simulated,ACTIONS,start_ms=150,end_ms=200)
    assert after['venue_time_absolute_error_ms']==1
    assert after['receive_time_absolute_error_ms']==1


def test_cancel_rejection_after_fill_is_not_a_new_order_state_or_a_second_fill():
    observed=trace([(101,111,'ACK','0'),(120,130,'FILLED','1'),(140,150,'CANCEL_REJECTED','1')])
    validate_calibration(spec(observed),TAPE,ACTIONS,HASH)
    result=compare_receipts(observed,observed,ACTIONS,start_ms=100,end_ms=200)
    assert result['rows'][0]['observed_state']=='FILLED'
    assert result['rows'][0]['observed_censored'] is False
    assert result['final_quantity_absolute_error']=='0'


@pytest.mark.parametrize('change',['missing_clock','backwards','overfill','duplicate','intent','identity','gap','terminal'])
def test_ambiguous_receipts_or_mixed_actions_are_rejected(change):
    observed=trace([(101,111,'ACK','0'),(120,130,'FILLED','1')])
    events=observed['orders'][0]['events']
    if change=='missing_clock':del events[0]['received_ms']
    elif change=='backwards':events[1]['received_ms']=110
    elif change=='overfill':events[1]['cumulative_filled']='2'
    elif change=='duplicate':events[1]['receipt_id']=events[0]['receipt_id']
    elif change=='intent':observed['orders'][0]['quantity']='2'
    elif change=='identity':observed['action_hash']='c'*64
    elif change=='gap':observed['coverage']['gaps']=[{'start_ms':150,'end_ms':140}]
    else:events.append({'receipt_id':'3','venue_ms':140,'received_ms':150,'state':'ACK','cumulative_filled':'1'})
    with pytest.raises(ValueError):validate_calibration(spec(observed),TAPE,ACTIONS,HASH)


def test_queue_projection_preserves_partial_rejection_and_omits_client_send_events():
    queue={'events':[{'order_id':'a','status':'SENT','client_time_ms':100,'venue_time_ms':None,'client_receive_ms':None,'quantity':None},
        {'order_id':'a','status':'ACK','venue_time_ms':101,'client_receive_ms':111,'quantity':None},
        {'order_id':'a','status':'PARTIALLY_FILLED','venue_time_ms':120,'client_receive_ms':130,'quantity':'0.4'},
        {'order_id':'a','status':'REJECTED','venue_time_ms':140,'client_receive_ms':150,'quantity':None}]}
    source=trace([])
    result=queue_receipts(queue,ACTIONS,source)
    assert [e['state'] for e in result['orders'][0]['events']]==['ACK','PARTIALLY_FILLED','REJECTED']
    assert result['orders'][0]['events'][-1]['cumulative_filled']=='0.4'
    with pytest.raises(ValueError,match='model output'):validate_calibration(spec(result),TAPE,ACTIONS,HASH)
    result['provenance']['kind']='SYNTHETIC_TEST_FIXTURE'
    validate_calibration(spec(result),TAPE,ACTIONS,HASH)
