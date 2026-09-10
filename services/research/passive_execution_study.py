"""Frozen public-input queue comparison; no profit or venue calibration selection."""
from decimal import Decimal
import re
from services.execution.passive_queue import Action, Depth, Trade, replay_queue
from services.execution.passive_execution_input import compile_tape
from services.execution.passive_account_environment import prepare_environment
from services.execution.frozen_account_terms import apply_account_terms
from services.execution.passive_perpetual_simulation import run_passive_perpetual_simulation
from services.execution.perpetual_simulation import (
    OrderIntent, CancelOrder, SimulationCostModel, SimulationLimits,
)
from services.research.passive_receipt_calibration import validate_calibration

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
        if set(row)-{'expires_ms','reduce_only'}!=expected or kind=='CANCEL' and set(row)&{'expires_ms','reduce_only'}:
            raise ValueError('fixed post-only action fields required')
        if 'reduce_only' in row and type(row['reduce_only']) is not bool:raise ValueError('reduce_only must be boolean')
        values={k:v for k,v in row.items() if k!='reduce_only'}
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
    if not isinstance(data,dict) or set(data) not in ({'tape','actions'},{'tape','actions','account'},
            {'tape','actions','account','calibration'}):
        raise ValueError('raw tape and fixed actions required')
    tape=compile_tape(data['tape'])
    actions=decode_actions(data['actions'],tape)
    if 'account' in data:prepare_account(data,tape)
    elif any(r.get('reduce_only') for r in data['actions']):raise ValueError('reduce-only needs a canonical account')
    if 'calibration' in data:
        from services.research.ai_factory_store import digest
        if not any(a.kind=='ORDER' for a in actions):raise ValueError('calibration requires fixed orders')
        validate_calibration(data['calibration'],tape,actions,digest(data['actions']))
    return tape,actions


def prepare_account(data,tape):
    if 'account' not in data:return None
    account=data['account']
    base={'environment','initial_collateral','max_order_notional'}
    if not isinstance(account,dict) or set(account) not in (base|{'fee_rate'},base|{'liquidity_fee_rates'},base|{'account_terms'}):
        raise ValueError('explicit simulated account and public environment required')
    if 'liquidity_fee_rates' in account:
        pair=account['liquidity_fee_rates']
        if not isinstance(pair,dict) or set(pair)!={'maker','taker'}:
            raise ValueError('complete simulated maker/taker fee pair required')
        rate_inputs={'maker_fee_rate':pair['maker'],'taker_fee_rate':pair['taker']}
    elif 'fee_rate' in account:
        rate_inputs={'fee_rate':account['fee_rate']}
    else:
        rate_inputs={}
    values={}
    for key,raw in {**{k:account[k] for k in ('initial_collateral','max_order_notional')},**rate_inputs}.items():
        if not isinstance(raw,str) or len(raw)>64 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',raw):
            raise ValueError('exact simulated amount string required')
        values[key]=Decimal(raw)
    if not (0<values['initial_collateral']<=Decimal('1000000000') and
            all(0<=values[k]<=Decimal('.01') for k in rate_inputs) and
            0<values['max_order_notional']<=values['initial_collateral']):
        raise ValueError('bounded simulated collateral, fee and order notional required')
    environment=prepare_environment(account['environment'],data['tape'],tape,
        max_order_notional=values['max_order_notional'])
    cost_values={k:values[k] for k in rate_inputs}
    if 'account_terms' in account:
        environment,cost_values=apply_account_terms(account['account_terms'],environment,
            symbol=tape.symbol,start_ms=environment['account_frames'][0].timestamp,end_ms=tape.end_ms)
    return {**environment,'initial_collateral':values['initial_collateral'],
        'costs':SimulationCostModel(**cost_values),
        'limits':SimulationLimits(quote_max_age_ms=3000),
        'reduce_only_ids':frozenset(r['order_id'] for r in data['actions'] if r.get('reduce_only'))}


def run_profile(tape,actions,profile,*,account=None,release_identity=None):
    if type(profile) is not int or profile not in PROFILES:
        raise ValueError('registered queue profile required')
    if account is not None:
        fixed=tuple((a.timestamp,CancelOrder(a.order_id) if a.kind=='CANCEL' else
            OrderIntent(a.order_id,tape.symbol,a.quantity if a.side=='BUY' else -a.quantity,
                limit_price=a.price,reduce_only=a.order_id in account['reduce_only_ids'],expires_at=a.expires_ms))
            for a in actions)
        return run_passive_perpetual_simulation(release_identity=release_identity,
            dataset_identity={'raw_tape_hash':tape.input_hash,'environment_hash':account['manifest']['environment_hash']},
            tape=tape,actions=fixed,order_latency_ms=profile,cancel_latency_ms=profile,ack_latency_ms=profile,
            **{k:v for k,v in account.items() if k not in ('manifest','reduce_only_ids')})
    market=[Depth(b.transaction_ms,b.bids,b.asks,b.bootstrap) for b in tape.books]
    if any(t.normal_quantity is None for t in tape.trades):raise ValueError('normal trade quantity required')
    market.extend(Trade(t.transaction_time_ms,t.aggregate_id,t.price,t.normal_quantity,
        'BUY' if t.aggressor_side=='SELL' else 'SELL',t.received_at_ms) for t in tape.trades if t.normal_quantity>0)
    # Each stream is ordered already. The kernel resolves cross-stream ties by
    # its explicit policy, never by socket receipt order.
    return replay_queue(market,actions,start_ms=tape.start_ms,end_ms=tape.end_ms,
        bid_floor=tape.bid_floor,ask_ceiling=tape.ask_ceiling,
        order_latency_ms=profile,cancel_latency_ms=profile,ack_latency_ms=profile)
