"""One-shot gold research intent adapter for the existing V2 simulator.

No ledger or profitability calculations live here. The simulator alone admits
orders, fills, charges costs and updates positions. This is not a live callback.
"""
from decimal import Decimal
import hashlib
import json
import re

from services.execution.perpetual_simulation import OrderIntent, CancelOrder, SimulationContext
from services.research.gold_signal import ExitPlan, exit_reason


class GoldV2Strategy:
    @classmethod
    def from_signal(cls, signal, **plan):
        if not (isinstance(signal,dict) and signal.get('schema')=='GOLD_RESEARCH_SIGNAL_V1'
                and signal.get('symbol')=='XAUUSDT' and signal.get('state')=='SIGNALLED'
                and signal.get('intent')=='LONG_RESEARCH_SIGNAL'
                and signal.get('authoritative') is False and signal.get('entry_price') is None
                and signal.get('decision_ms')==signal.get('earliest_execution_after_ms')
                and set(plan)=={'quantity','stop','target','entry_timeout_ms','holding_ms','max_quote_age_ms'}):
            raise ValueError('CONFIRMED_GOLD_RESEARCH_SIGNAL_REQUIRED')
        return cls(signal_time_ms=signal['decision_ms'],signal_id=signal.get('decision_id'),**plan)

    def __init__(self, *, signal_time_ms, signal_id, quantity, stop, target,
                 entry_timeout_ms, holding_ms, max_quote_age_ms):
        if not (type(signal_time_ms) is int and 0<=signal_time_ms<=2**63-1
                and isinstance(signal_id,str) and re.fullmatch('[0-9a-f]{64}',signal_id)
                and all(type(v) is int and 0<v<=2**63-1
                        for v in (entry_timeout_ms,holding_ms,max_quote_age_ms))
                and all(type(v) is Decimal and v.is_finite() and v>0
                        for v in (quantity,stop,target)) and stop<target):
            raise ValueError('INVALID_GOLD_SIMULATION_PLAN')
        self.signal_time=signal_time_ms
        self.quantity,self.stop,self.target=quantity,stop,target
        self.entry_timeout,self.holding,self.max_age=entry_timeout_ms,holding_ms,max_quote_age_ms
        self.identity={'schema':'GOLD_SINGLE_SIGNAL_V2_ADAPTER_V1','signal_id':signal_id,
            'signal_time_ms':signal_time_ms,'quantity':str(quantity),'stop':str(stop),
            'target':str(target),'entry_timeout_ms':entry_timeout_ms,'holding_ms':holding_ms,
            'max_quote_age_ms':max_quote_age_ms,'scope':'TECHNICAL_RESEARCH_ONLY'}
        self.plan_id=hashlib.sha256(json.dumps(self.identity,sort_keys=True).encode()).hexdigest()
        self.entry_id='gold-entry-'+self.plan_id
        self.exit_id='gold-exit-'+self.plan_id
        self.state='WAITING'
        self.opened_at=None
        self.events=[]
        self.last_time=None

    def _record(self, now, reason):
        self.events.append({'timestamp':now,'state':self.state,'reason':reason,'plan_id':self.plan_id})

    def __call__(self, ctx: SimulationContext):
        if not isinstance(ctx,SimulationContext):
            raise ValueError('SIMULATION_CONTEXT_REQUIRED')
        now=ctx.timestamp
        if self.last_time is not None and now<=self.last_time:
            raise ValueError('STRATEGY_TIME_NOT_INCREASING')
        self.last_time=now
        position=ctx.positions.get('XAUUSDT')
        amount=position.quantity if position else Decimal(0)
        if amount<0 or (self.state in ('WAITING','DONE') and amount!=0):
            raise ValueError('UNEXPECTED_GOLD_POSITION')
        if self.state=='DONE':
            return ()
        q=ctx.quotes.get('XAUUSDT')
        fresh=q is not None and 0<=now-q.event_time<=self.max_age
        if self.state=='WAITING':
            if now<self.signal_time:
                return ()
            if now>self.signal_time:
                self.state='DONE'; self._record(now,'MISSED_SIGNAL_TIME'); return ()
            if ctx.open_orders or not fresh or not self.stop<q.ask_price<self.target:
                self.state='DONE'; self._record(now,'ENTRY_CONTEXT_REJECTED'); return ()
            self.state='ENTRY_REQUESTED'; self._record(now,'ENTRY_INTENT')
            return (OrderIntent(self.entry_id,'XAUUSDT',self.quantity,
                                expires_at=now+self.entry_timeout),)
        if amount==0:
            if self.entry_id in ctx.open_orders or self.exit_id in ctx.open_orders:
                return ()
            self.state='DONE'
            self._record(now,'POSITION_CLOSED' if self.opened_at is not None else 'NO_ENTRY_FILL')
            return ()
        if self.opened_at is None:
            # In this simulator fills precede each strategy callback. This is the
            # first observed partial fill, not the earlier signal timestamp.
            self.opened_at=now
            self.state='HOLDING'; self._record(now,'POSITION_OBSERVED')
        if self.state=='EXIT_REQUESTED':
            if self.exit_id not in ctx.open_orders:
                raise ValueError('EXIT_TERMINATED_WITH_POSITION_REMAINING')
            return ()
        if not fresh:
            self._record(now,'EXIT_DATA_UNKNOWN')
            return ()
        plan=ExitPlan(self.opened_at,self.stop,self.target,self.opened_at+self.holding)
        reason=exit_reason(plan,bid=q.bid_price,event_ms=q.event_time,received_ms=now,
                           decision_ms=now,max_age_ms=self.max_age)
        if reason not in ('STOP','TARGET','TIMEOUT'):
            return ()
        actions=[]
        if self.entry_id in ctx.open_orders:
            actions.append(CancelOrder(self.entry_id))
        actions.append(OrderIntent(self.exit_id,'XAUUSDT',-amount,reduce_only=True))
        self.state='EXIT_REQUESTED'; self._record(now,reason)
        return tuple(actions)
