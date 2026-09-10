"""Frozen public-input queue comparison; no profit or venue calibration selection."""
from decimal import Decimal
import re
from services.execution.passive_queue import Action, Depth, Trade, replay_queue
from services.execution.passive_execution_input import compile_tape

PROFILES = (0, 25, 100)


def decode_actions(rows, tape):
    if not isinstance(rows,list) or len(rows)>200:
        raise ValueError('at most 200 fixed actions required')
    actions=[];seen=set();last=tape.start_ms
    for row in rows:
        if not isinstance(row,dict):raise ValueError('fixed action object required')
        kind=row.get('kind')
        expected={'timestamp','kind','order_id'}
        if kind=='ORDER':expected|={'side','quantity','price'}
        if set(row)-{'expires_ms'}!=expected or kind=='CANCEL' and 'expires_ms' in row:
            raise ValueError('fixed post-only action fields required')
        values={**row}
        for name in ('quantity','price'):
            if name in row:
                raw=row[name]
                if not isinstance(raw,str) or len(raw)>64 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',raw):
                    raise ValueError('exact positive decimal strings required')
                values[name]=Decimal(raw)
        action=Action(**values)
        if not last<=action.timestamp<=tape.end_ms:raise ValueError('actions outside joint window or out of order')
        if kind=='ORDER':
            if action.order_id in seen:raise ValueError('duplicate fixed order')
            seen.add(action.order_id)
            tape.visible_book(action.timestamp)
        last=action.timestamp;actions.append(action)
    return tuple(actions)


def prepare_input(data):
    if not isinstance(data,dict) or set(data)!={'tape','actions'}:
        raise ValueError('raw tape and fixed actions required')
    tape=compile_tape(data['tape'])
    return tape,decode_actions(data['actions'],tape)


def run_profile(tape,actions,profile):
    if type(profile) is not int or profile not in PROFILES:
        raise ValueError('registered queue profile required')
    market=[Depth(b.transaction_ms,b.bids,b.asks,b.bootstrap) for b in tape.books]
    market.extend(Trade(t.transaction_time_ms,t.aggregate_id,t.price,t.quantity,
        'BUY' if t.aggressor_side=='SELL' else 'SELL',t.received_at_ms) for t in tape.trades)
    # Each stream is ordered already. The kernel resolves cross-stream ties by
    # its explicit policy, never by socket receipt order.
    return replay_queue(market,actions,start_ms=tape.start_ms,end_ms=tape.end_ms,
        bid_floor=tape.bid_floor,ask_ceiling=tape.ask_ceiling,
        order_latency_ms=profile,cancel_latency_ms=profile,ack_latency_ms=profile)
