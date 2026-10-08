"""Bounded local forward protocol; clock assertions are not trusted timestamps."""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json

from services.research.gold_signal import GoldSignal, Rule, Zone
from services.research.gold_trade_windows import build_window, WindowError


def _time(v):
    return type(v) is int and 0<=v<=2**63-1


class ForwardObservation:
    def __init__(self, plan, *, registered_at_ms, cursor_factory):
        self._cursor_factory=cursor_factory
        required={'schema','start_ms','rule','zone','decision_times_ms','max_lateness_ms'}
        if not isinstance(plan,dict) or set(plan)!=required or plan['schema']!='GOLD_FORWARD_PLAN_V1':
            raise ValueError('INVALID_FORWARD_PLAN')
        p=deepcopy(plan);r,z=p['rule'],p['zone']
        if not (isinstance(r,dict) and set(r)=={'window_ms','max_age_ms','sell_threshold','confirm_windows'}
                and isinstance(z,dict) and set(z)=={'lower','upper','registered_at_ms','expires_at_ms'}
                and all(isinstance(v,str) and len(v)<=64 for v in (r['sell_threshold'],z['lower'],z['upper']))):
            raise ValueError('INVALID_RULE_SCHEMA')
        self.rule=Rule(**dict(r,sell_threshold=Decimal(r['sell_threshold'])))
        zone=Zone(**dict(z,lower=Decimal(z['lower']),upper=Decimal(z['upper'])))
        self.signal=GoldSignal(self.rule,zone)
        times=p['decision_times_ms'];late=p['max_lateness_ms'];start=p['start_ms']
        if not (_time(registered_at_ms) and _time(start) and _time(late)
                and registered_at_ms<zone.registered_at_ms<=start
                and isinstance(times,list) and 1<=len(times)<=60
                and all(_time(t) for t in times) and late<=self.rule.max_age_ms
                and all(b>a for a,b in zip(times,times[1:]))
                and times[-1]+late-registered_at_ms<=60000
                and all(start+(i+1)*self.rule.window_ms<=t
                    <=start+(i+1)*self.rule.window_ms+self.rule.max_age_ms-late
                    for i,t in enumerate(times))):
            raise ValueError('INVALID_FORWARD_SCHEDULE')
        self.plan=p;self.registered_at_ms=registered_at_ms
        self.plan_id=hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.index=0;self.last_time=registered_at_ms;self.status='OBSERVING'

    @property
    def next_decision_ms(self):
        return self.plan['decision_times_ms'][self.index] if self.status=='OBSERVING' else None

    def advance(self, packets, *, now_ms):
        if self.status!='OBSERVING':raise ValueError('FORWARD_OBSERVATION_TERMINAL')
        if not _time(now_ms) or now_ms<self.last_time:
            self.status='CLOCK_INVALID'
            raise ValueError('FORWARD_CLOCK_REVERSED')
        self.last_time=now_ms
        scheduled=self.next_decision_ms
        if now_ms<scheduled:return None
        result={'plan_id':self.plan_id,'scheduled_at_ms':scheduled,'observed_at_ms':now_ms,
            'decision':None,'window':None,'reason':None,'authoritative':False}
        if now_ms>scheduled+self.plan['max_lateness_ms']:
            result['reason']='MISSED_DECISION_DEADLINE'
        else:
            start=self.plan['start_ms']+self.index*self.rule.window_ms
            try:
                frame=build_window(packets,start_ms=start,end_ms=start+self.rule.window_ms,
                    as_of_ms=now_ms,max_age_ms=self.rule.max_age_ms,cursor_factory=self._cursor_factory)
                result.update(window=asdict(frame),decision=self.signal.observe(frame,decision_ms=now_ms))
                if result['decision']['state']=='DATA_UNKNOWN':result['reason']='SIGNAL_DATA_UNKNOWN'
            except WindowError as exc:result['reason']=str(exc)
        self.index+=1
        if result['reason'] is not None:self.status='INPUT_INCOMPLETE'
        elif self.index==len(self.plan['decision_times_ms']):self.status='COMPLETE_OBSERVATION'
        return result
