"""Compare fixed-action trajectories; imported logs never certify their own origin."""
import re
from decimal import Decimal
from services.research.profitability_attribution import _amount
from services.execution.perpetual_ledger_v2 import canonical_decimal,ledger_decimal_context


def _orders(trajectory):
    if not re.fullmatch(r'[0-9a-f]{64}',trajectory['action_hash']) or trajectory['symbol'] not in ('BTCUSDC','BTCUSDT'):
        raise ValueError('fixed action and contract identity required')
    rows=trajectory['orders']
    if not isinstance(rows,list) or len(rows)>1000:raise ValueError('bounded order trace required')
    orders={}
    for order in rows:
        identifier=order['id'];quantity=_amount(order['quantity'])
        if not isinstance(identifier,str) or not 1<=len(identifier)<=128 or identifier in orders or quantity<=0 or order['side'] not in ('BUY','SELL'):
            raise ValueError('invalid or duplicate order')
        events=order['events']
        if not isinstance(events,list) or not 1<=len(events)<=10000:raise ValueError('bounded events required')
        previous_at=-1;previous_fill=Decimal(0);terminal=False
        for event in events:
            at=event['at_ms'];filled=_amount(event['filled']);state=event['state']
            if type(at) is not int or at<previous_at or not previous_fill<=filled<=quantity or terminal:
                raise ValueError('noncausal or inconsistent cumulative fill trace')
            if state not in ('SENT','ACK','PARTIAL','CANCEL_SENT','CANCELLED','FILLED','REJECTED'):
                raise ValueError('unknown order state')
            if state=='FILLED' and filled!=quantity or state=='REJECTED' and filled!=0:
                raise ValueError('terminal state conflicts with fill quantity')
            terminal=state in ('CANCELLED','FILLED','REJECTED');previous_at=at;previous_fill=filled
        orders[identifier]=order
    return orders


@ledger_decimal_context()
def compare_trajectories(observed,simulated):
    a=_orders(observed);b=_orders(simulated)
    if observed['action_hash']!=simulated['action_hash'] or observed['symbol']!=simulated['symbol']:
        raise ValueError('same fixed action and contract required')
    paired=sorted(set(a)&set(b));quantity_error=Decimal(0);time_error=0;unmatched_events=0;rows=[]
    for identifier in paired:
        x=a[identifier];y=b[identifier]
        if (x['quantity'],x['side'])!=(y['quantity'],y['side']):raise ValueError('action identity conflicts with order intent')
        error=abs(_amount(x['events'][-1]['filled'])-_amount(y['events'][-1]['filled']));quantity_error+=error
        xe={};ye={}
        for events,target in ((x['events'],xe),(y['events'],ye)):
            counts={}
            for event in events:
                state=event['state'];ordinal=counts.get(state,0);counts[state]=ordinal+1
                target[(state,ordinal)]=event['at_ms']
        shared=set(xe)&set(ye);delta=sum(abs(xe[k]-ye[k]) for k in shared)
        time_error+=delta;unmatched_events+=len(set(xe)^set(ye))
        rows.append({'order_id':identifier,'quantity_absolute_error':canonical_decimal(error),
                     'state_time_absolute_error_ms':delta,'observed_terminal':x['events'][-1]['state'],
                     'simulated_terminal':y['events'][-1]['state']})
    return {'status':'UNVERIFIED_TRAJECTORY_DIAGNOSTIC','action_hash':observed['action_hash'],
            'paired_orders':len(paired),'unmatched_orders':sorted(set(a)^set(b)),
            'unmatched_events':unmatched_events,'quantity_absolute_error':canonical_decimal(quantity_error),
            'state_time_absolute_error_ms':time_error,'rows':rows,'venue_receipts_verified':False,
            'economic_eligible':False,'calibration_objective':'QUANTITY_TIME_STATE_ERROR_NOT_PNL',
            'limitations':['IMPORTED_PROVENANCE_REQUIRES_INDEPENDENT_VERIFICATION','NO_QUEUE_POSITION_INFERENCE']}
