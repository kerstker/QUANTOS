"""Bounded execution timing intent; risk reduction always precedes waiting."""
import math


class TimingGate:
    def __init__(self, *, wait_ms):
        if type(wait_ms) is not int or wait_ms != 3600000:raise ValueError('unregistered timing budget')
        self.wait_ms=wait_ms;self.pending=None;self.deadline=None;self.last=-1

    def decide(self, *, at, current, target, short_signal):
        if type(at) is not int or at<=self.last:raise ValueError('strictly increasing decision clock required')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (current,short_signal)):
            raise ValueError('finite observed timing inputs required')
        if target is not None and (type(target) not in (int,float) or target not in (-.25,0.,.25)):
            raise ValueError('unregistered target')
        self.last=at;cancelled=None
        if self.pending is not None and target!=self.pending:
            cancelled=self.pending;self.pending=None;self.deadline=None
        row={'time':at,'requested_target':target,'short_signal':short_signal,'cancelled_target':cancelled}
        if target is None or target==0 or (current*target>0 and abs(target)<=abs(current)):
            self.pending=None;self.deadline=None
            return {**row,'target':target,'reason':'HOLD_OR_IMMEDIATE_REDUCTION','deadline_ms':None}
        if current*target<0:
            self.pending=None;self.deadline=None
            return {**row,'target':0.,'reason':'REVERSAL_CLOSE_IMMEDIATE','deadline_ms':None}
        expired=self.deadline is not None and at>=self.deadline
        if short_signal*target>=0 or expired:
            deadline=self.deadline;self.pending=None;self.deadline=None
            return {**row,'target':target,'reason':'WAIT_TIMEOUT' if expired else 'TIMING_PERMITS', 'deadline_ms':deadline}
        if self.pending is None:self.pending=target;self.deadline=at+self.wait_ms
        return {**row,'target':None,'reason':'WAIT_ADVERSE_SHORT_SIGNAL','deadline_ms':self.deadline}
