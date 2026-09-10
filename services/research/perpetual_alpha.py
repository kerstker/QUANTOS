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
           maximum_quantity=D('50'),taker_fee_bps=D('4'),decision_interval_ms=DAY,
           target_weight=D('.25'),max_position_weight=D('.30'),initial_margin=D(1),
           maintenance_margin=D('.5'),price_stop=None,trade_lows=None,trade_highs=None,
           max_holding_ms=None,context_decision=None,quote='USDC',
           target_policy='DISCRETE_V1',execution_observer=None):
    if quote not in ('USDC','USDT'):raise ValueError('unsupported research settlement currency')
    if target_policy not in ('DISCRETE_V1','SLEEVE_NET_V1'):raise ValueError('unregistered target policy')
    if target_policy=='SLEEVE_NET_V1' and (not stop or target_weight!=D('.25') or max_position_weight!=D('.30')):
        raise ValueError('fixed sleeve risk budget required')
    if execution_observer is not None and not callable(execution_observer):raise ValueError('invalid execution observer')
    symbol='BTC'+quote
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
    for value in (taker_fee_bps,target_weight,max_position_weight,initial_margin,maintenance_margin):
        if not isinstance(value,D) or not value.is_finite() or value<=0:
            raise ValueError('invalid research execution parameter')
    if maintenance_margin>initial_margin or initial_margin>1 or taker_fee_bps>100:
        raise ValueError('invalid margin/fee scenario')
    if type(decision_interval_ms) is not int or decision_interval_ms<=0 or DAY%decision_interval_ms:
        raise ValueError('invalid decision interval')
    if max_holding_ms is not None and (type(max_holding_ms) is not int or max_holding_ms<=0 or max_holding_ms%MINUTE):
        raise ValueError('invalid holding period')
    if price_stop is not None:
        if not isinstance(price_stop,D) or not price_stop.is_finite() or not 0<price_stop<1:
            raise ValueError('invalid protective stop')
        trade_lows=np.asarray(trade_lows,dtype=float);trade_highs=np.asarray(trade_highs,dtype=float)
        if (trade_lows.shape!=prices.shape or trade_highs.shape!=prices.shape
                or not np.isfinite(trade_lows).all() or not np.isfinite(trade_highs).all()
                or (trade_lows<=0).any() or (trade_lows>prices).any() or (trade_highs<prices).any()):
            raise ValueError('invalid protective stop observations')
    if start>=end or (end-start)%DAY or start%DAY!=5*MINUTE:
        raise ValueError('invalid evaluation clock')
    lo,hi=map(int,np.searchsorted(times,[start,end]))
    if hi>=len(times) or times[lo]!=start or times[hi]!=end or (np.diff(times[lo:hi+1])!=MINUTE).any():
        raise ValueError('missing evaluation minute')
    spec=ContractSpec(symbol,f'LINEAR_{quote}_PERPETUAL',quote,D(1),D('.001'),D('.001'),
                      D(50),D('.0125'),D('1e-24'),(MarginBracket(D(0),None,initial_margin,maintenance_margin,D(0)),))
    ledger=LinearPerpetualLedger((spec,),initial_collateral=D(1500))
    events=[]
    for at in range(start,end,decision_interval_ms):heapq.heappush(events,(at,1,'decision',None))
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
    protective=None
    protective_cursor=lo
    episode=0
    peak=1500.;max_dd=0.;exposure_sum=0.;observed=0

    def qty():
        position=ledger.position(symbol)
        return D(0) if position is None else position.quantity

    def notify_execution(at,status,reason):
        if execution_observer is not None:
            from types import MappingProxyType
            execution_observer(at,MappingProxyType({'status':status,'reason':reason,
                'quantity':canonical_decimal(qty()),'equity':canonical_decimal(ledger.account_equity()),
                'halted':halted is not None}))

    def latch(at):
        nonlocal halted,pending_exit
        if stop and halted is None and ledger.account_equity()<=D(1350):
            halted=at
            pending_exit=(at//MINUTE+1)*MINUTE
            if pending_exit<=end:heapq.heappush(events,(pending_exit,2,'exit',None))

    def observe(left,right):
        nonlocal peak,max_dd,exposure_sum,observed
        if right<=left:return
        position=ledger.position(symbol)
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
        nonlocal protective,protective_cursor,episode
        index=int(np.searchsorted(times,at))
        if index>=len(times) or times[index]!=at:raise ValueError('execution quote unavailable')
        reference=D(str(prices[index]));current=qty();equity=ledger.account_equity()
        desired=(D(str(abs(weight)))*equity/reference/spec.quantity_step).to_integral_value(rounding=ROUND_FLOOR)*spec.quantity_step
        if weight<0:desired=-desired
        delta=desired-current
        if delta==0:
            notify_execution(at,'NO_NET_CHANGE',reason);return
        if abs(delta)>maximum_quantity:
            orders.append({'time':at,'status':'REJECTED_MAX_QUANTITY','quantity':str(delta)})
            notify_execution(at,'REJECTED_MAX_QUANTITY',reason);return
        price=reference*(1+D(slip_bps)/10000 if delta>0 else 1-D(slip_bps)/10000)
        price=(price/D('.1')).to_integral_value(rounding=ROUND_CEILING if delta>0 else ROUND_FLOOR)*D('.1')
        if abs(delta)*price<spec.min_notional:
            orders.append({'time':at,'status':'SKIPPED_MIN_NOTIONAL','quantity':str(delta)})
            notify_execution(at,'SKIPPED_MIN_NOTIONAL',reason);return
        volume=daily_volume.get(at//DAY)
        if not isinstance(volume,D) or not volume.is_finite() or volume<=0:raise ValueError('completed volume unavailable')
        capacity=CapacityPolicy('BINANCE_USDM',str(at//DAY),BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
                                at//DAY,volume,D('.01'),CapacityAction.REJECT)
        fee=abs(delta)*price*taker_fee_bps/10000
        before=ledger.account_equity()
        # Post-fill risk check uses the canonical transaction rollback, never a
        # second PnL implementation. Oversized new risk is an explicit rejection.
        def guard(result):
            if result.filled_quantity!=delta:raise _TradeRejected('CAPACITY')
            after=ledger.account_equity()
            increased=abs(qty())>abs(current) or qty()*current<0
            if increased and (after<D(1350) and stop or after<=0 or abs(qty())*D(str(marks[index]))>max_position_weight*after):
                raise _TradeRejected('POST_FILL_RISK')
        try:
            outcome=ledger.apply_event(TradeEvent(at,symbol,delta,price,MarginMode.CROSS,capacity,fee=fee),trade_guard=guard)
        except _TradeRejected as exc:
            orders.append({'time':at,'status':'REJECTED_'+str(exc),'equity_before':str(before)})
            notify_execution(at,'REJECTED_'+str(exc),reason);return
        # A capacity rejection returns a zero-fill receipt and deliberately does
        # not call the post-fill guard. Only committed fills belong in this list.
        if outcome.filled_quantity==0:
            orders.append({'time':at,'status':'REJECTED_CAPACITY','quantity':canonical_decimal(delta),
                'reason':outcome.reason,'equity_before':canonical_decimal(before)})
            notify_execution(at,'REJECTED_CAPACITY',reason);return
        if outcome.status!='FILLED' or outcome.filled_quantity!=delta:
            raise ValueError('unexpected canonical execution outcome')
        fills.append({'time':at,'quantity':canonical_decimal(delta),'price':canonical_decimal(price),
                      'fee':canonical_decimal(fee),'reference':canonical_decimal(reference),'reason':reason})
        orders.append({'time':at,'status':'FILLED','quantity':canonical_decimal(delta)})
        notify_execution(at,'FILLED',reason)
        if current==0 and qty()!=0:
            episode+=1
            if price_stop is not None:
                protective=price*(1-price_stop if qty()>0 else 1+price_stop)
                protective_cursor=index
            if max_holding_ms is not None and at+max_holding_ms<=end:
                heapq.heappush(events,(at+max_holding_ms,2,'timeout',episode))
        elif qty()==0:
            protective=None
        latch(at)

    while events:
        event=heapq.heappop(events)
        at,priority,kind,payload=event
        # Only completed minute extrema may trigger a protective exit. Schedule
        # its following open before processing later decisions/funding events.
        if protective is not None and qty()!=0:
            closed_right=min(int(np.searchsorted(times,at-MINUTE,side='right')),hi)
            if closed_right>protective_cursor:
                observed_prices=trade_lows if qty()>0 else trade_highs
                segment=observed_prices[protective_cursor:closed_right]
                hits=np.flatnonzero(segment<=float(protective) if qty()>0 else segment>=float(protective))
                if len(hits):
                    triggered=int(times[protective_cursor+int(hits[0])])+MINUTE
                    protective=None
                    heapq.heappush(events,event)
                    heapq.heappush(events,(triggered,2,'price_stop',episode))
                    continue
                protective_cursor=closed_right
        right=int(np.searchsorted(times,at,side='left' if kind=='funding' else 'right'))
        right=min(right,hi+1)
        position=ledger.position(symbol)
        quantity=qty()
        interrupted=False
        if cursor<right and quantity:
            values=float(ledger.account_equity())+float(quantity)*(marks[cursor:right]-float(position.mark_price))
            critical=values<=abs(float(quantity))*marks[cursor:right]*float(maintenance_margin)+1e-8
            if stop and halted is None:critical|=values<=1350+1e-8
            segment_start=cursor
            for offset in np.flatnonzero(critical):
                index=segment_start+int(offset)
                observe(cursor,index+1)
                cursor=index+1
                ledger.apply_event(MarkEvent(int(times[index]),symbol,D(str(marks[index]))))
                observations.append({'time':int(times[index]),'equity':canonical_decimal(ledger.account_equity())})
                old=halted;latch(int(times[index]))
                if old is None and halted is not None:
                    heapq.heappush(events,event);interrupted=True;break
        if interrupted:continue
        if cursor<right:
            observe(cursor,right)
            ledger.apply_event(MarkEvent(int(times[right-1]),symbol,D(str(marks[right-1]))))
            cursor=right
        if kind=='funding':
            ledger.apply_event(FundingEvent(at,symbol,payload['rate'],payload['mark']))
            observations.append({'time':at,'equity':canonical_decimal(ledger.account_equity())})
            latch(at)
        elif kind in ('decision','end'):
            notify_execution(at,'LEDGER_STATE',kind)
            if (at-start)%DAY==0 or kind=='end':
                daily.append({'time':at,'equity':canonical_decimal(ledger.account_equity()),
                              'quantity':canonical_decimal(qty()),'mark':str(marks[int(np.searchsorted(times,at))])})
            if kind=='end':break
            if context_decision is None:
                weight=decision(at,qty()!=0)
            else:
                from types import MappingProxyType
                equity=ledger.account_equity()
                context=MappingProxyType({'quantity':canonical_decimal(qty()),'equity':canonical_decimal(equity),
                    'mark':str(marks[int(np.searchsorted(times,at))]),'halted':halted is not None,
                    'pending_exit_ms':pending_exit})
                weight=context_decision(at,context)
            if weight is not None and (not np.isfinite(weight) or
                    (D(str(weight)) not in (-target_weight,D(0),target_weight) if target_policy=='DISCRETE_V1'
                     else abs(D(str(weight)))>target_weight)):
                raise ValueError('invalid bounded target')
            decisions.append({'time':at,'target':weight,'actual_quantity':canonical_decimal(qty()),'halted':halted is not None})
            if halted is not None:
                if pending_exit is None:apply_trade(at,0.,'LATCHED_EXIT')
            elif weight is not None:apply_trade(at,weight,'DAILY_TARGET')
        elif kind=='exit':
            pending_exit=None
            apply_trade(at,0.,'LOSS_BUDGET_NEXT_QUOTE')
        elif kind in ('price_stop','timeout') and payload==episode and qty()!=0:
            apply_trade(at,0.,'PROTECTIVE_STOP_NEXT_QUOTE' if kind=='price_stop' else 'HOLDING_TIMEOUT')
        equity=float(ledger.account_equity());max_dd=max(max_dd,1-equity/peak);peak=max(peak,equity)
    reconciliation=ledger.reconciliation_snapshot().as_canonical_dict()
    if reconciliation['residual']!='0':raise ValueError('canonical reconciliation failed')
    return {'cash_model':f'CANONICAL_LINEAR_{quote}_PERPETUAL','reconciliation':reconciliation,
            'daily':daily,'fills':fills,'orders':orders,'decisions':decisions,'funding_observations':observations,
            'halted_at':halted,'minute_max_drawdown':max_dd,'average_exposure':exposure_sum/observed,
            'mark_observations':observed,'authoritative':False,'live_eligible':False}
