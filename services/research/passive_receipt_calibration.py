"""Dual-clock passive receipt diagnostics. Imported provenance never certifies itself.

Receipt time controls the calibration partition and cumulative-fill path. Venue
time is a separate error on paired states, never a replacement for availability.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context

SCHEMA='PASSIVE_RECEIPT_CALIBRATION_V1'
STATES={'ACK','PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED','EXPIRED','CANCEL_REJECTED'}
TERMINAL={'FILLED','REJECTED','CANCELLED','EXPIRED'}
ZERO=Decimal('0')


def _time(value):
    if type(value) is not int or not 0<=value<=8640000000000000:raise ValueError('exact receipt clock required')
    return value


def _amount(value):
    if not isinstance(value,str) or len(value)>64 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',value):
        raise ValueError('exact nonnegative receipt quantity required')
    return Decimal(value)


def _trace(document,actions,symbol,action_hash):
    if (not isinstance(document,dict) or set(document)!={'action_hash','symbol','coverage','provenance','orders'}
            or document['action_hash']!=action_hash or not re.fullmatch('[0-9a-f]{64}',action_hash)
            or document['symbol']!=symbol or symbol not in ('BTCUSDC','BTCUSDT')):
        raise ValueError('same frozen actions and contract required')
    coverage=document['coverage'];source=document['provenance']
    if not isinstance(coverage,dict) or set(coverage)!={'start_ms','end_ms','gaps'}:
        raise ValueError('explicit receipt coverage required')
    start,end=_time(coverage['start_ms']),_time(coverage['end_ms'])
    if not start<end or end-start>150000:raise ValueError('bounded receipt coverage required')
    if (not isinstance(source,dict) or set(source)!={'kind','declared_source_sha256'}
            or source['kind'] not in ('IMPORTED_PRIVATE_RECEIPTS','SYNTHETIC_TEST_FIXTURE','MODEL_DIAGNOSTIC')
            or not isinstance(source['declared_source_sha256'],str)
            or not re.fullmatch('[0-9a-f]{64}',source['declared_source_sha256'])):
        raise ValueError('declared receipt provenance required')
    gaps=coverage['gaps'];previous=start
    if not isinstance(gaps,list) or len(gaps)>200:raise ValueError('bounded receipt gaps required')
    for gap in gaps:
        if not isinstance(gap,dict) or set(gap)!={'start_ms','end_ms'}:raise ValueError('gap clocks required')
        left,right=_time(gap['start_ms']),_time(gap['end_ms'])
        if not previous<=left<right<=end:raise ValueError('nonoverlapping ordered gaps within coverage required')
        previous=right
    fixed={a.order_id:a for a in actions if a.kind=='ORDER'}
    rows=document['orders'];result={};seen=set();count=0
    if not isinstance(rows,list) or len(rows)>200:raise ValueError('bounded receipt order list required')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'order_id','side','quantity','limit_price','events'}:
            raise ValueError('receipt intent binding required')
        name=row['order_id'];action=fixed.get(name)
        if (action is None or name in result or row['side']!=action.side
                or _amount(row['quantity'])!=action.quantity or _amount(row['limit_price'])!=action.price):
            raise ValueError('receipt intent conflicts with fixed action')
        events=row['events'];last_receive=start;last_venue=0;filled=ZERO;terminal=False;acked=False
        if not isinstance(events,list):raise ValueError('receipt event list required')
        for e in events:
            count+=1
            if count>10000:raise ValueError('receipt event budget exceeded')
            if not isinstance(e,dict) or set(e)!={'receipt_id','venue_ms','received_ms','state','cumulative_filled'}:
                raise ValueError('both venue and actual receive clocks required')
            receipt=e['receipt_id'];venue=_time(e['venue_ms']);received=_time(e['received_ms'])
            state=e['state'];quantity=_amount(e['cumulative_filled'])
            if (not isinstance(receipt,str) or not 1<=len(receipt)<=128 or receipt in seen
                    or state not in STATES or not max(action.timestamp,last_venue)<=venue<=received
                    or not last_receive<=received<=end or not filled<=quantity<=action.quantity):
                raise ValueError('duplicate, noncausal or inconsistent receipt')
            if terminal and state!='CANCEL_REJECTED':raise ValueError('order receipt after terminal state')
            if state=='ACK' and (acked or quantity!=ZERO or filled!=ZERO):raise ValueError('invalid acknowledgement')
            if state=='PARTIALLY_FILLED' and not filled<quantity<action.quantity:raise ValueError('partial fill must advance quantity')
            if state=='FILLED' and (quantity!=action.quantity or quantity<=filled):raise ValueError('full fill must complete quantity')
            if state not in ('PARTIALLY_FILLED','FILLED') and quantity!=filled:
                raise ValueError('non-fill receipt cannot change filled quantity')
            seen.add(receipt);last_receive=received;last_venue=venue;filled=quantity
            acked|=state=='ACK';terminal|=state in TERMINAL
        result[name]=row
    return result


def validate_calibration(spec,tape,actions,action_hash):
    try:
        if not isinstance(spec,dict) or set(spec)!={'schema','cutoff_ms','observed'} or spec['schema']!=SCHEMA:
            raise ValueError('versioned passive calibration contract required')
        cutoff=_time(spec['cutoff_ms']);observed=spec['observed']
        _trace(observed,actions,tape.symbol,action_hash)
        coverage=observed['coverage']
        if not tape.start_ms==coverage['start_ms']<cutoff<coverage['end_ms']<=tape.end_ms:
            raise ValueError('calibration and held-out receipt windows must lie inside frozen tape')
        if observed['provenance']['kind']=='MODEL_DIAGNOSTIC':raise ValueError('model output is not an imported observation')
        return deepcopy(spec)
    except (KeyError,TypeError,AttributeError,ArithmeticError) as exc:
        raise ValueError('malformed passive receipt calibration input') from exc


@ledger_decimal_context()
def queue_receipts(queue,actions,observed):
    """Project full model events before the console truncates them."""
    rows={a.order_id:{'order_id':a.order_id,'side':a.side,'quantity':canonical_decimal(a.quantity),
        'limit_price':canonical_decimal(a.price),'events':[]} for a in actions if a.kind=='ORDER'}
    amounts={name:ZERO for name in rows};end=observed['coverage']['end_ms']
    for i,e in enumerate(queue['events']):
        if e['status'] in ('SENT','CANCEL_SENT'):continue
        if e['order_id'] not in rows:
            # A cancel for an unknown fixed order is not a new order.
            continue
        name=e['order_id']
        if e['status'] in ('PARTIALLY_FILLED','FILLED'):amounts[name]+=_amount(e['quantity'])
        if e['client_receive_ms']>end:continue
        rows[name]['events'].append({'receipt_id':f'model:{i}','venue_ms':e['venue_time_ms'],
            'received_ms':e['client_receive_ms'],'state':e['status'],
            'cumulative_filled':canonical_decimal(amounts[name])})
    return {'action_hash':observed['action_hash'],'symbol':observed['symbol'],
        'coverage':{**observed['coverage'],'gaps':[]},
        'provenance':{'kind':'MODEL_DIAGNOSTIC','declared_source_sha256':hashlib.sha256(
            json.dumps(queue,sort_keys=True,allow_nan=False).encode()).hexdigest()},'orders':list(rows.values())}


def _advance(events,index,at,quantity,state):
    while index<len(events) and events[index]['received_ms']<=at:
        event=events[index];quantity=_amount(event['cumulative_filled'])
        if event['state']!='CANCEL_REJECTED':state=event['state']
        index+=1
    return index,quantity,state


@ledger_decimal_context()
def compare_receipts(observed,simulated,actions,*,start_ms,end_ms):
    """Whole-account continuation; tail reports retain their inherited state."""
    a=_trace(observed,actions,observed['symbol'],observed['action_hash'])
    b=_trace(simulated,actions,observed['symbol'],observed['action_hash'])
    _time(start_ms);_time(end_ms)
    if not start_ms<end_ms or any(not r['coverage']['start_ms']<=start_ms<end_ms<=r['coverage']['end_ms'] for r in (observed,simulated)):
        raise ValueError('shared nonempty receipt window required')
    include_start=start_ms==observed['coverage']['start_ms']
    def inside(at):return (at>=start_ms if include_start else at>start_ms) and at<=end_ms
    totals={'final_quantity_absolute_error':ZERO,'matched_event_quantity_absolute_error':ZERO,
        'quantity_path_error_btc_ms':ZERO,'increment_quantity_absolute_error':ZERO,
        'carry_quantity_absolute_error':ZERO,'venue_time_absolute_error_ms':0,
        'receive_time_absolute_error_ms':0,'state_mismatch_duration_ms':0,
        'observed_events':0,'simulated_events':0,'matched_events':0,'unmatched_events':0,'paired_orders':0}
    rows=[];unmatched=[];unobserved=[]
    for action in (x for x in actions if x.kind=='ORDER' and x.timestamp<=end_ms):
        name=action.order_id;xe=a.get(name,{}).get('events',[]);ye=b.get(name,{}).get('events',[])
        xe=[e for e in xe if e['received_ms']<=end_ms];ye=[e for e in ye if e['received_ms']<=end_ms]
        xwindow=[e for e in xe if inside(e['received_ms'])];ywindow=[e for e in ye if inside(e['received_ms'])]
        totals['observed_events']+=len(xwindow);totals['simulated_events']+=len(ywindow)
        totals['paired_orders']+=bool(xwindow and ywindow)
        if bool(xwindow)!=bool(ywindow):unmatched.append(name)
        if not xe:unobserved.append(name)
        matched=[]
        for events in (xe,ye):
            counters={};by_state={}
            for e in events:
                key=(e['state'],counters.get(e['state'],0));counters[e['state']]=key[1]+1
                if inside(e['received_ms']):by_state[key]=e
            matched.append(by_state)
        left,right=matched;shared=left.keys()&right.keys()
        totals['matched_events']+=len(shared);totals['unmatched_events']+=len(left.keys()^right.keys())
        event_error=sum((abs(_amount(left[k]['cumulative_filled'])-_amount(right[k]['cumulative_filled'])) for k in shared),ZERO)
        venue_error=sum(abs(left[k]['venue_ms']-right[k]['venue_ms']) for k in shared)
        receive_error=sum(abs(left[k]['received_ms']-right[k]['received_ms']) for k in shared)
        totals['matched_event_quantity_absolute_error']+=event_error
        totals['venue_time_absolute_error_ms']+=venue_error;totals['receive_time_absolute_error_ms']+=receive_error
        ix,qx,sx=_advance(xe,0,start_ms,ZERO,None);iy,qy,sy=_advance(ye,0,start_ms,ZERO,None)
        carry_x,carry_y=qx,qy;path=[];area=ZERO;mismatch=0;previous=start_ms
        moments=sorted({start_ms,end_ms,*[e['received_ms'] for e in xe+ye if e['received_ms']>start_ms],
            *([action.timestamp] if start_ms<action.timestamp<end_ms else [])})
        for at in moments:
            area+=abs(qx-qy)*(at-previous)
            observed_state=sx or ('SENT' if action.timestamp<=previous else 'NOT_SENT')
            simulated_state=sy or ('SENT' if action.timestamp<=previous else 'NOT_SENT')
            mismatch+=(at-previous)*(observed_state!=simulated_state)
            ix,qx,sx=_advance(xe,ix,at,qx,sx);iy,qy,sy=_advance(ye,iy,at,qy,sy)
            path.append({'received_ms':at,'observed_filled':canonical_decimal(qx),'simulated_filled':canonical_decimal(qy)})
            previous=at
        final_error=abs(qx-qy);increment_error=abs((qx-carry_x)-(qy-carry_y))
        totals['final_quantity_absolute_error']+=final_error;totals['quantity_path_error_btc_ms']+=area
        totals['carry_quantity_absolute_error']+=abs(carry_x-carry_y)
        totals['increment_quantity_absolute_error']+=increment_error;totals['state_mismatch_duration_ms']+=mismatch
        rows.append({'order_id':name,'side':action.side,'quantity':canonical_decimal(action.quantity),
            'observed_state':sx or 'SENT','simulated_state':sy or 'SENT',
            'observed_censored':sx not in TERMINAL,'simulated_censored':sy not in TERMINAL,
            'final_quantity_absolute_error':canonical_decimal(final_error),
            'quantity_path_error_btc_ms':canonical_decimal(area),
            'venue_time_absolute_error_ms':venue_error,'receive_time_absolute_error_ms':receive_error,
            'path':path})
    gaps=[g for g in observed['coverage']['gaps'] if g['start_ms']<=end_ms and g['end_ms']>start_ms]
    return {'status':'IMPORTED_RECEIPT_DIAGNOSTIC','start_ms':start_ms,'end_ms':end_ms,
        **{k:canonical_decimal(v) if isinstance(v,Decimal) else v for k,v in totals.items()},
        'unmatched_orders':unmatched,'unobserved_orders':unobserved,'submitted_orders':len(rows),'rows':rows,
        'coverage_gaps':gaps,'venue_receipts_verified':False,'economic_eligible':False,
        'partition_clock':'ACTUAL_CLIENT_RECEIPT_TIME','quantity_path_unit':'BTC_MILLISECONDS',
        'state_pairing':'STATE_OCCURRENCE_ORDINAL','quantity_path_includes_inherited_state':True}


def calibration_score(report):
    return (len(report['unmatched_orders']),Decimal(report['final_quantity_absolute_error']),
        Decimal(report['quantity_path_error_btc_ms']),Decimal(report['matched_event_quantity_absolute_error']),
        report['unmatched_events'],report['receive_time_absolute_error_ms'],report['venue_time_absolute_error_ms'])


def summary_report(report,*,rows=0):
    result={k:v for k,v in report.items() if k!='rows'}
    result['order_rows_total']=len(report['rows']);result['row_projection']='FIRST_20_ORDERS_LAST_200_PATH_POINTS_FULL_REPORT_IN_ARTIFACT'
    result['rows']=[{**r,'path':r['path'][-200:],'path_points_total':len(r['path'])} for r in report['rows'][:rows]]
    return result
