"""Bounded fixed-action market-by-price queue replay with optional account feedback.

Conservative external cancellation at the queue tail; increases join behind
existing orders. Trades and depth updates share one queue, and each public trade
has one fill budget across all of our orders. This small-order/no-market-impact
counterfactual is an explicit model, not a claim of observed private fills.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from services.execution.perpetual_ledger_v2 import ledger_decimal_context

ZERO = Decimal('0')
MODEL = 'CANCEL_TAIL_SHARED_VOLUME_V1'


def _time(value):
    if type(value) is not int or value < 0:
        raise ValueError('nonnegative integer time required')


def _amount(value, positive=True):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or positive and value == 0:
        raise ValueError('finite positive Decimal required')


def number(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


@dataclass(frozen=True)
class Depth:
    timestamp: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    bootstrap: bool = False
    public_available_ms: int | None = None

    def __post_init__(self):
        _time(self.timestamp)
        if self.public_available_ms is not None:
            _time(self.public_available_ms)
            if self.public_available_ms<self.timestamp:raise ValueError('depth available before venue event')
        if type(self.bootstrap) is not bool:
            raise ValueError('explicit bootstrap flag required')
        for name in ('bids','asks'):
            levels = tuple(tuple(row) for row in getattr(self,name))
            if len(levels)>5000 or any(len(row)!=2 for row in levels):
                raise ValueError('bounded depth required')
            if len({p for p,_ in levels})!=len(levels):
                raise ValueError('duplicate depth price')
            for p,q in levels:
                _amount(p);_amount(q,positive=False)
            object.__setattr__(self,name,levels)


@dataclass(frozen=True)
class Trade:
    timestamp: int
    identity: int
    price: Decimal
    quantity: Decimal
    resting_side: str
    received_ms: int

    def __post_init__(self):
        _time(self.timestamp);_time(self.identity);_time(self.received_ms)
        _amount(self.price);_amount(self.quantity)
        if self.resting_side not in ('BUY','SELL') or self.received_ms<self.timestamp:
            raise ValueError('consistent trade side and receive time required')


@dataclass(frozen=True)
class Action:
    timestamp: int
    kind: str
    order_id: str
    side: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    expires_ms: int | None = None

    def __post_init__(self):
        _time(self.timestamp)
        if not isinstance(self.order_id,str) or not 1<=len(self.order_id)<=100 or not self.order_id.strip():
            raise ValueError('bounded order identity required')
        if self.kind=='ORDER':
            if self.side not in ('BUY','SELL'):
                raise ValueError('order side required')
            _amount(self.quantity);_amount(self.price)
            if self.expires_ms is not None:
                _time(self.expires_ms)
                if self.expires_ms<=self.timestamp:
                    raise ValueError('expiry must follow client send')
        elif self.kind!='CANCEL' or any(v is not None for v in (self.side,self.quantity,self.price,self.expires_ms)):
            raise ValueError('fixed order or cancellation required')


@dataclass(frozen=True)
class AccountingFill:
    filled_quantity: Decimal
    status: str
    reason: str | None = None

    def __post_init__(self):
        _amount(self.filled_quantity,positive=False)
        if self.status not in ('OPEN','PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED'):
            raise ValueError('invalid accounting fill state')


@ledger_decimal_context()
def replay_queue(market, actions, *, start_ms, end_ms, bid_floor, ask_ceiling,
                 order_latency_ms=0, cancel_latency_ms=0, ack_latency_ms=25, max_book_age_ms=1000,
                 _accounting=None):
    _time(start_ms);_time(end_ms);_amount(bid_floor);_amount(ask_ceiling)
    if start_ms>=end_ms or bid_floor>=ask_ceiling:
        raise ValueError('nonempty replay window and book range required')
    for latency in (order_latency_ms,cancel_latency_ms,ack_latency_ms,max_book_age_ms):
        if type(latency) is not int or not 0<=latency<=1000:
            raise ValueError('bounded latency required')
    market,actions=tuple(market),tuple(actions)
    if not 1<=len(market)<=40000 or len(actions)>200:
        raise ValueError('bounded market and action count required')
    if any(type(e) not in (Depth,Trade) for e in market) or any(type(a) is not Action for a in actions):
        raise ValueError('typed immutable events required')
    depth_events=[e for e in market if isinstance(e,Depth)]
    if (not depth_events or not depth_events[0].bootstrap or any(e.bootstrap for e in depth_events[1:])
            or depth_events[0].timestamp>start_ms
            or [e.timestamp for e in depth_events]!=sorted(e.timestamp for e in depth_events)):
        raise ValueError('one ordered book epoch required')
    trades=[e for e in market if isinstance(e,Trade)]
    if (len({e.identity for e in trades})!=len(trades)
            or [(e.timestamp,e.identity) for e in trades]!=sorted((e.timestamp,e.identity) for e in trades)):
        raise ValueError('unique ordered trades required')
    if (any(not start_ms<=a.timestamp<=end_ms for a in actions)
            or [a.timestamp for a in actions]!=sorted(a.timestamp for a in actions)):
        raise ValueError('fixed actions must lie in the declared window')
    ids=[a.order_id for a in actions if a.kind=='ORDER']
    if len(ids)!=len(set(ids)):
        raise ValueError('duplicate order identity')
    # A tie expires first; then trades, depth, arrivals/cancels. Stable action
    # ordinal resolves ties. No adaptive callback can inspect future venue data.
    timeline=[(e.timestamp,0 if isinstance(e,Trade) else 1,i,'MARKET',e) for i,e in enumerate(market)]
    for i,a in enumerate(actions):
        timeline.append((a.timestamp,-2,i,'SEND',a))
        delay=order_latency_ms if a.kind=='ORDER' else cancel_latency_ms
        timeline.append((a.timestamp+delay,3,i,'ARRIVE',a))
        if a.expires_ms is not None:
            timeline.append((a.expires_ms,-1,i,'EXPIRE',a))
    timeline.sort(key=lambda row:row[:3])
    displayed={'BUY':{},'SELL':{}}
    queues={}
    orders={}
    events=[];fills=[];last_depth=None

    def emit(order_id,now,status,reason=None,quantity=None,price=None,client_time=None):
        events.append({'order_id':order_id,'venue_time_ms':None if client_time is not None else now,
            'client_receive_ms':None if client_time is not None else now+ack_latency_ms,
            'client_time_ms':client_time,'status':status,'reason':reason,
            'quantity':None if quantity is None else number(quantity),
            'price':None if price is None else number(price)})

    def remove(order_id):
        for key,nodes in queues.items():
            queues[key]=[node for node in nodes if node[0]!=order_id]

    def reconcile(key,target):
        if key not in queues:
            return
        nodes=queues[key]
        external=sum((q for owner,q in nodes if owner is None),ZERO)
        if target>external:
            nodes.append([None,target-external])
        else:
            reduction=external-target
            for node in reversed(nodes):
                if node[0] is None:
                    take=min(node[1],reduction);node[1]-=take;reduction-=take
                    if not reduction:break
        queues[key]=[node for node in nodes if node[1]>ZERO]

    def finish(state,now,status,reason=None):
        state['status']=status;state['reason']=reason
        remove(state['action'].order_id)
        emit(state['action'].order_id,now,status,reason)

    for now,_,_,kind,item in timeline:
        if now>end_ms:break
        if _accounting is not None:_accounting.advance(now)
        if kind=='SEND':
            if item.kind=='ORDER':
                orders[item.order_id]={'action':item,'remaining':item.quantity,'filled':ZERO,
                    'status':'SENT','reason':None,'arrival_ms':item.timestamp+order_latency_ms,'queue_ahead_at_arrival':None}
            emit(item.order_id,now,'SENT' if item.kind=='ORDER' else 'CANCEL_SENT',client_time=now)
        elif kind=='EXPIRE':
            state=orders[item.order_id]
            if state['status'] in ('OPEN','PARTIALLY_FILLED'):
                if _accounting is not None:_accounting.expire(item.order_id,now)
                finish(state,now,'EXPIRED','TIME_IN_FORCE')
        elif kind=='ARRIVE':
            state=orders.get(item.order_id)
            if item.kind=='CANCEL':
                if _accounting is not None:_accounting.cancel(item.order_id,now)
                if state is not None and state['status'] in ('OPEN','PARTIALLY_FILLED'):
                    finish(state,now,'CANCELLED','CLIENT_CANCEL_ARRIVED')
                else:emit(item.order_id,now,'CANCEL_REJECTED','ORDER_NOT_OPEN')
                continue
            bids,asks=displayed['BUY'],displayed['SELL'];reason=None
            if item.expires_ms is not None and item.expires_ms<=now:reason='EXPIRED_BEFORE_ARRIVAL'
            elif last_depth is None or now-last_depth>max_book_age_ms:reason='STALE_BOOK'
            elif not bids or not asks:reason='UNKNOWN_TOUCH_AFTER_TRADES'
            elif item.side=='BUY' and item.price>=min(asks) or item.side=='SELL' and item.price<=max(bids):reason='POST_ONLY_WOULD_CROSS'
            elif not bid_floor<=item.price<=ask_ceiling:reason='OUTSIDE_KNOWN_BOOK_RANGE'
            elif any(s['status'] in ('OPEN','PARTIALLY_FILLED') and s['action'].side!=item.side
                and (item.price>=s['action'].price if item.side=='BUY' else item.price<=s['action'].price)
                for s in orders.values()):reason='SELF_CROSS_POLICY_UNMODELED'
            if reason:
                if _accounting is not None:_accounting.reject(item.order_id,now,reason)
                finish(state,now,'REJECTED',reason);continue
            if _accounting is not None:
                reason=_accounting.admit(item.order_id,now)
                if reason:
                    finish(state,now,'REJECTED',reason);continue
            key=(item.side,item.price)
            if key not in queues:queues[key]=[[None,displayed[item.side].get(item.price,ZERO)]]
            state['queue_ahead_at_arrival']=sum((q for _,q in queues[key]),ZERO)
            queues[key].append([item.order_id,item.quantity]);state['status']='OPEN'
            emit(item.order_id,now,'ACK')
        elif isinstance(item,Depth):
            for side,levels in (('BUY',item.bids),('SELL',item.asks)):
                for p,q in levels:
                    if q:displayed[side][p]=q
                    else:displayed[side].pop(p,None)
                    reconcile((side,p),q)
            if not displayed['BUY'] or not displayed['SELL'] or max(displayed['BUY'])>=min(displayed['SELL']):
                raise ValueError('incomplete or crossed venue book')
            last_depth=now
        else:
            side=item.resting_side;budget=item.quantity
            better=lambda p:p>item.price if side=='BUY' else p<item.price
            keys=sorted((key for key in queues if key[0]==side and (better(key[1]) or key[1]==item.price)),
                         key=lambda key:key[1],reverse=side=='BUY')
            priority_blocked=False
            for key in keys:
                if priority_blocked:break
                if better(key[1]):reconcile(key,ZERO)
                for node in queues[key]:
                    if budget==ZERO:break
                    offered=min(node[1],budget)
                    receipt=None
                    amount=offered
                    if node[0] is not None and offered and _accounting is not None:
                        receipt=_accounting.fill(node[0],now,key[1],offered)
                        if not isinstance(receipt,AccountingFill) or receipt.filled_quantity>offered:
                            raise ValueError('accounting exceeded the shared trade budget')
                        amount=receipt.filled_quantity
                    node[1]-=amount;budget-=amount
                    if node[0] is not None and amount:
                        state=orders[node[0]];state['remaining']-=amount;state['filled']+=amount
                        state['status']='FILLED' if not state['remaining'] else 'PARTIALLY_FILLED'
                        emit(node[0],now,state['status'],quantity=amount,price=key[1])
                        fills.append({'order_id':node[0],'venue_time_ms':now,'client_receive_ms':now+ack_latency_ms,
                            'source_trade_id':item.identity,'public_trade_received_ms':item.received_ms,
                            'quantity':number(amount),'price':number(key[1]),'side':side})
                    if receipt is not None:
                        state=orders[node[0]]
                        if receipt.status in ('CANCELLED','REJECTED'):
                            state['status']=receipt.status;state['reason']=receipt.reason;node[1]=ZERO
                            emit(node[0],now,receipt.status,receipt.reason)
                        elif amount<offered:
                            # A still-open front order retains priority. A
                            # rejected remainder, above, releases the budget.
                            priority_blocked=True;break
                        elif receipt.status!=state['status']:
                            raise ValueError('queue and ledger order states diverged')
                queues[key]=[node for node in queues[key] if node[1]>ZERO]
            # Reconcile the historical external book separately from our shadow
            # fills; tail cancellation prevents counting a depth decrease twice.
            for p in list(displayed[side]):
                if better(p):displayed[side].pop(p);reconcile((side,p),ZERO)
            remaining=max(ZERO,displayed[side].get(item.price,ZERO)-item.quantity)
            if remaining:displayed[side][item.price]=remaining
            else:displayed[side].pop(item.price,None)
            reconcile((side,item.price),remaining)
        if kind=='MARKET' and _accounting is not None:
            _accounting.observe_book(now,MappingProxyType(dict(displayed['BUY'])),
                                     MappingProxyType(dict(displayed['SELL'])),item)
    if _accounting is not None:_accounting.advance(end_ms)
    result_orders=[]
    for name,state in orders.items():
        a=state['action'];ahead=state['queue_ahead_at_arrival']
        result_orders.append({'order_id':name,'side':a.side,'price':number(a.price),'quantity':number(a.quantity),
            'client_sent_ms':a.timestamp,'arrival_ms':state['arrival_ms'],
            'filled_quantity':number(state['filled']),'remaining_quantity':number(state['remaining']),
            'status':state['status'],'reason':state['reason'],
            'queue_ahead_at_arrival':None if ahead is None else number(ahead),
            'censored':state['status'] in ('SENT','OPEN','PARTIALLY_FILLED')})
    return {'status':'PASSIVE_QUEUE_DIAGNOSTIC_ONLY','model':MODEL,'start_ms':start_ms,'end_ms':end_ms,
        'orders':result_orders,'events':events,'fills':fills,'ledger_applied':_accounting is not None,
        'queue_model_calibrated':False,'venue_receipts_verified':False,'economic_eligible':False,
        'tie_policy':'SEND_EXPIRY_TRADE_DEPTH_ARRIVAL_STABLE_ACTION_ORDER',
        'scope':'FIXED_ACTIONS_NO_ADAPTIVE_CALLBACK_NO_MARKET_IMPACT'}
