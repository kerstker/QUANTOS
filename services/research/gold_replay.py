"""Pure historical gold signal recomputation, without filesystem or execution.

A replay is not prospective signal delivery, source authentication or a fill.
"""
from dataclasses import asdict
from decimal import Decimal

from services.research.gold_signal import Rule, Zone, GoldSignal
from services.research.gold_trade_windows import build_window, WindowError


def replay(document, *, cursor_factory):
    required={'schema','start_ms','rule','zone','decision_times_ms','packets'}
    if not isinstance(document,dict) or set(document)!=required or document['schema']!='GOLD_SIGNAL_REPLAY_INPUT_V1':
        raise ValueError('INVALID_REPLAY_SCHEMA')
    start=document['start_ms']; times=document['decision_times_ms']
    if (type(start) is not int or start<0 or not isinstance(times,list) or not 1<=len(times)<=1000
            or any(type(t) is not int or t<0 for t in times)
            or any(b<=a for a,b in zip(times,times[1:]))):
        raise ValueError('INVALID_DECISION_SCHEDULE')
    r,z=document['rule'],document['zone']
    if (not isinstance(r,dict) or set(r)!={'window_ms','max_age_ms','sell_threshold','confirm_windows'}
            or not isinstance(z,dict) or set(z)!={'lower','upper','registered_at_ms','expires_at_ms'}
            or not all(isinstance(v,str) for v in (r['sell_threshold'],z['lower'],z['upper']))):
        raise ValueError('INVALID_RULE_SCHEMA')
    rule=Rule(**dict(r,sell_threshold=Decimal(r['sell_threshold'])))
    zone=Zone(**dict(z,lower=Decimal(z['lower']),upper=Decimal(z['upper'])))
    signal=GoldSignal(rule,zone)
    result={'schema':'GOLD_SIGNAL_REPLAY_V1','status':'RESEARCH_SIGNALS_ONLY',
        'rule_id':signal.rule_id,'records':[],'net_pnl':None,'authoritative':False,
        'limitations':['LOCAL_AGGREGATE_STREAM_ONLY','NO_COMEX_DATA','NO_FILL_SIMULATION',
            'NO_ACCOUNT_LINK','NO_PROFITABILITY_EVIDENCE','UNAUTHENTICATED_INPUT']}
    for decision in times:
        try:
            frame=build_window(document['packets'],start_ms=start,end_ms=start+rule.window_ms,
                               as_of_ms=decision,max_age_ms=rule.max_age_ms,cursor_factory=cursor_factory)
        except WindowError as exc:
            result.update(status='INPUT_INCOMPLETE',failed_window_start_ms=start,reason=str(exc))
            return result
        record=signal.observe(frame,decision_ms=decision)
        result['records'].append({'window':asdict(frame),'decision':record})
        if record['state']=='DATA_UNKNOWN':
            result.update(status='INPUT_INCOMPLETE',failed_window_start_ms=start,
                          reason=record['reason'])
            return result
        start+=rule.window_ms
    return result
