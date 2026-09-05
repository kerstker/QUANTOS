"""Bounded minute-observed research using QUANTOS's unique perpetual ledger.

Sparse ledger events plus vector observation between cash-flow events; vector
estimates never authorize a trade or decide a threshold without Decimal replay.
No network, selection authority, paper persistence or broker path.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
import heapq
import numpy as np

from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, CapacityAction, CapacityPolicy,
    ContractSpec, FundingEvent, LinearPerpetualLedger, MarginBracket, MarginMode,
    MarkEvent, TradeEvent, canonical_decimal, ledger_decimal_context,
)

DAY=86400000
MINUTE=60000
SYMBOL='BTCUSDC'


class _TradeRejected(ValueError):
    pass


def hypothesis_target(hypothesis, trailing_values):
    factor,operator,window=hypothesis
    if factor not in ('close_return','settled_funding') or operator not in ('trend','fade') or type(window) is not int or window not in (3,14):
        raise ValueError('unregistered hypothesis')
    values=np.asarray(trailing_values,dtype=float)[-window:]
    if len(values)!=window or not np.isfinite(values).all():
        return None
    value=float(values.mean())
    direction=1 if value>0 else -1 if value<0 else 0
    return .25*direction*(1 if operator=='trend' else -1)


@ledger_decimal_context()
def replay(*,times,prices,marks,funding,start,end,daily_volume,decision,slip_bps,stop,
           maximum_quantity=D('50')):
    times=np.asarray(times,dtype=np.int64)
    prices=np.asarray(prices,dtype=float);marks=np.asarray(marks,dtype=float)
    if (len(times)!=len(prices) or len(times)!=len(marks) or len(times)<2
            or not np.isfinite(prices).all() or not np.isfinite(marks).all()
            or (prices<=0).any() or (marks<=0).any() or (np.diff(times)<=0).any()):
        raise ValueError('invalid aligned perpetual prices/marks')
    if slip_bps not in (2,5,10) or type(stop) is not bool:
        raise ValueError('unregistered cost/risk scenario')
    if not isinstance(maximum_quantity,D) or not maximum_quantity.is_finite() or maximum_quantity<=0:
        raise ValueError('invalid quantity maximum')
    if start>=end or (end-start)%DAY or start%DAY!=5*MINUTE:
        raise ValueError('invalid evaluation clock')
    lo,hi=map(int,np.searchsorted(times,[start,end]))
    if hi>=len(times) or times[lo]!=start or times[hi]!=end or (np.diff(times[lo:hi+1])!=MINUTE).any():
        raise ValueError('missing evaluation minute')
    spec=ContractSpec(SYMBOL,'LINEAR_USDC_PERPETUAL','USDC',D(1),D('.001'),D('.001'),
                      D(50),D('.0125'),D('1e-24'),(MarginBracket(D(0),None,D(1),D('.5'),D(0)),))
    ledger=LinearPerpetualLedger((spec,),initial_collateral=D(1500))
    events=[]
    for at in range(start,end,DAY):heapq.heappush(events,(at,1,'decision',None))
    heapq.heappush(events,(end,3,'end',None))
    previous=-1
    for f in funding:
        if f['time']<=previous:raise ValueError('unordered funding')
        previous=f['time']
        if start<=f['time']<end:heapq.heappush(events,(f['time'],0,'funding',f))
    fills,orders,daily,decisions,observations=[],[],[],[],[]
    cursor=lo
    halted=None
    pending_exit=None
    peak=1500.;max_dd=0.;exposure_sum=0.;observed=0

    def qty():
        position=ledger.position(SYMBOL)
        return D(0) if position is None else position.quantity

    def latch(at):
        nonlocal halted,pending_exit
        if stop and halted is None and ledger.account_equity()<=D(1350):
            halted=at
            pending_exit=(at//MINUTE+1)*MINUTE
            if pending_exit<=end:heapq.heappush(events,(pending_exit,2,'exit',None))

    def observe(left,right):
        nonlocal peak,max_dd,exposure_sum,observed
        if right<=left:return
        position=ledger.position(SYMBOL)
        quantity=qty()
        current=ledger.account_equity()
        reference=D(str(marks[left])) if position is None else position.mark_price
        values=float(current)+float(quantity)*(marks[left:right]-float(reference))
        peaks=np.maximum.accumulate(np.r_[peak,values])[1:]
        max_dd=max(max_dd,float(np.max(1-values/peaks)))
        peak=max(peak,float(values.max()))
        exposure_sum+=float(np.sum(abs(float(quantity))*marks[left:right]/values))
        observed+=right-left

    def apply_trade(at,weight,reason):
        index=int(np.searchsorted(times,at))
        if index>=len(times) or times[index]!=at:raise ValueError('execution quote unavailable')
        reference=D(str(prices[index]));current=qty();equity=ledger.account_equity()
        desired=(D(str(abs(weight)))*equity/reference/spec.quantity_step).to_integral_value(rounding=ROUND_FLOOR)*spec.quantity_step
        if weight<0:desired=-desired
        delta=desired-current
        if delta==0:return
        if abs(delta)>maximum_quantity:
            orders.append({'time':at,'status':'REJECTED_MAX_QUANTITY','quantity':str(delta)});return
        price=reference*(1+D(slip_bps)/10000 if delta>0 else 1-D(slip_bps)/10000)
        price=(price/D('.1')).to_integral_value(rounding=ROUND_CEILING if delta>0 else ROUND_FLOOR)*D('.1')
        if abs(delta)*price<spec.min_notional:
            orders.append({'time':at,'status':'SKIPPED_MIN_NOTIONAL','quantity':str(delta)});return
        volume=daily_volume.get(at//DAY)
        if not isinstance(volume,D) or not volume.is_finite() or volume<=0:raise ValueError('completed volume unavailable')
        capacity=CapacityPolicy('BINANCE_USDM',str(at//DAY),BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
                                at//DAY,volume,D('.01'),CapacityAction.REJECT)
        fee=abs(delta)*price*D('.0004')
        before=ledger.account_equity()
        # Post-fill risk check uses the canonical transaction rollback, never a
        # second PnL implementation. Oversized new risk is an explicit rejection.
        def guard(result):
            if result.filled_quantity!=delta:raise _TradeRejected('CAPACITY')
            after=ledger.account_equity()
            increased=abs(qty())>abs(current) or qty()*current<0
            if increased and (after<D(1350) and stop or after<=0 or abs(qty())*D(str(marks[index]))>D('.30')*after):
                raise _TradeRejected('POST_FILL_RISK')
        try:
            ledger.apply_event(TradeEvent(at,SYMBOL,delta,price,MarginMode.CROSS,capacity,fee=fee),trade_guard=guard)
        except _TradeRejected as exc:
            orders.append({'time':at,'status':'REJECTED_'+str(exc),'equity_before':str(before)});return
        fills.append({'time':at,'quantity':canonical_decimal(delta),'price':canonical_decimal(price),
                      'fee':canonical_decimal(fee),'reference':canonical_decimal(reference),'reason':reason})
        orders.append({'time':at,'status':'FILLED','quantity':canonical_decimal(delta)})
        latch(at)

    while events:
        event=heapq.heappop(events)
        at,priority,kind,payload=event
        right=int(np.searchsorted(times,at,side='left' if kind=='funding' else 'right'))
        right=min(right,hi+1)
        position=ledger.position(SYMBOL)
        quantity=qty()
        interrupted=False
        if cursor<right and quantity:
            values=float(ledger.account_equity())+float(quantity)*(marks[cursor:right]-float(position.mark_price))
            critical=values<=abs(float(quantity))*marks[cursor:right]*.5+1e-8
            if stop and halted is None:critical|=values<=1350+1e-8
            segment_start=cursor
            for offset in np.flatnonzero(critical):
                index=segment_start+int(offset)
                observe(cursor,index+1)
                cursor=index+1
                ledger.apply_event(MarkEvent(int(times[index]),SYMBOL,D(str(marks[index]))))
                observations.append({'time':int(times[index]),'equity':canonical_decimal(ledger.account_equity())})
                old=halted;latch(int(times[index]))
                if old is None and halted is not None:
                    heapq.heappush(events,event);interrupted=True;break
        if interrupted:continue
        if cursor<right:
            observe(cursor,right)
            ledger.apply_event(MarkEvent(int(times[right-1]),SYMBOL,D(str(marks[right-1]))))
            cursor=right
        if kind=='funding':
            ledger.apply_event(FundingEvent(at,SYMBOL,payload['rate'],payload['mark']))
            observations.append({'time':at,'equity':canonical_decimal(ledger.account_equity())})
            latch(at)
        elif kind in ('decision','end'):
            daily.append({'time':at,'equity':canonical_decimal(ledger.account_equity()),
                          'quantity':canonical_decimal(qty()),'mark':str(marks[int(np.searchsorted(times,at))])})
            if kind=='end':break
            weight=decision(at,qty()!=0)
            if weight is not None and (not np.isfinite(weight) or weight not in (-.25,0.,.25)):
                raise ValueError('invalid bounded target')
            decisions.append({'time':at,'target':weight,'actual_quantity':canonical_decimal(qty()),'halted':halted is not None})
            if halted is not None:
                if pending_exit is None:apply_trade(at,0.,'LATCHED_EXIT')
            elif weight is not None:apply_trade(at,weight,'DAILY_TARGET')
        elif kind=='exit':
            pending_exit=None
            apply_trade(at,0.,'LOSS_BUDGET_NEXT_QUOTE')
        equity=float(ledger.account_equity());max_dd=max(max_dd,1-equity/peak);peak=max(peak,equity)
    reconciliation=ledger.reconciliation_snapshot().as_canonical_dict()
    if reconciliation['residual']!='0':raise ValueError('canonical reconciliation failed')
    return {'cash_model':'CANONICAL_LINEAR_USDC_PERPETUAL','reconciliation':reconciliation,
            'daily':daily,'fills':fills,'orders':orders,'decisions':decisions,'funding_observations':observations,
            'halted_at':halted,'minute_max_drawdown':max_dd,'average_exposure':exposure_sum/observed,
            'mark_observations':observed,'authoritative':False,'live_eligible':False}
