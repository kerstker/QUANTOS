"""Frozen portfolio intent to an unsigned original Demo task and risk precheck.

An independently installed strategy must bind the entire allocation declaration.
No enrollment, credentials, signing, persistence or submission occurs here.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import re

from live.execution import demo_request_inputs
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require, amount
from services.execution.perpetual_ledger_v2 import ledger_decimal_context, canonical_decimal
from services.research.portfolio_target_intent import resolve_target


def strategy_id(allocation):
    _fields(allocation, 'schema source_commit symbol account_ref_sha256 model_hash capital fee_bps slip_bps ioc_offset_bps')
    require(allocation['schema']=='DEMO_PORTFOLIO_ALLOCATION_V1' and allocation['symbol']=='BTCUSDC',
        'PORTFOLIO_ALLOCATION_SCOPE')
    for name,length in (('source_commit',40),('account_ref_sha256',64),('model_hash',64)):
        require(type(allocation[name]) is str and re.fullmatch('[a-f0-9]{'+str(length)+'}',allocation[name]),
            'PORTFOLIO_ALLOCATION_IDENTITY')
    amount(allocation['capital'])
    for name in ('fee_bps','slip_bps','ioc_offset_bps'):
        require(type(allocation[name]) is int and 0<=allocation[name]<=100,'PORTFOLIO_ALLOCATION_COST')
    return 'portfolio-'+_digest(allocation)[:40]


def _grid(value,minimum,step,rounding):
    require(step>0 and minimum>=0,'PORTFOLIO_VENUE_GRID')
    return minimum+((value-minimum)/step).to_integral_value(rounding=rounding)*step


@ledger_decimal_context()
def build(*,allocation,forecast,funding,origin,market_inputs,profile,issuer_policy,kill_state,
          dispatch,recovery,oms,now_ms):
    sid=strategy_id(allocation)
    demo_request_inputs.profile_check(profile,issuer_policy,market_inputs,now_ms)
    require(profile['strategy']['strategy_id']==sid and profile['strategy']['approved'] is True,
        'PORTFOLIO_ALLOCATION_NOT_APPROVED')
    require(allocation['source_commit']==issuer_policy['executor_release_commit'] and
        allocation['account_ref_sha256']==issuer_policy['account_ref_sha256'] and
        allocation['symbol']==origin['symbol']==profile['symbol'],'PORTFOLIO_ALLOCATION_BINDING')
    account,position=demo_request_inputs.observed_account(origin=origin,profile=profile,
        issuer_policy=issuer_policy,dispatch=dispatch,recovery=recovery,oms=oms,now_ms=now_ms)
    capital=amount(allocation['capital'])
    require(capital<=Decimal(account['equity']),'PORTFOLIO_CAPITAL_EXCEEDS_EQUITY')
    data={r['kind']:r['data'] for r in origin['reads']}
    mark=Decimal(data['mark']['markPrice'])
    held=Decimal('0') if position is None else Decimal(position['quantity'])
    context={'quantity':canonical_decimal(held),'mark':canonical_decimal(mark),
        'equity':canonical_decimal(capital),'observed_at_ms':origin['observed_at_ms'],
        'account_ref_sha256':origin['account_ref_sha256'],'origin_sha256':_digest(origin),
        'account_equity':account['equity'],'allocation_sha256':_digest(allocation)}
    intent=resolve_target(forecast,context,funding,model_hash=allocation['model_hash'],now_ms=now_ms,
        fee_bps=allocation['fee_bps'],slip_bps=allocation['slip_bps'])
    result={'schema':'DEMO_PORTFOLIO_TASK_BUILD_V1','allocation_sha256':_digest(allocation),
        'intent':intent,'task':None,'request_build':None,'status':'NO_CHANGE',
        'submission_enabled':False,'economic_authority':False}
    if intent['target_weight'] is None:
        return result
    filters={f['filterType']:f for f in data['rules']['terms']['filters']}
    lot,tick=filters['LOT_SIZE'],filters['PRICE_FILTER']
    minimum,step=Decimal(lot['minQty']),Decimal(lot['stepSize'])
    raw=Decimal(str(intent['target_weight']))*capital/mark
    magnitude=max(Decimal(0),_grid(abs(raw),minimum,step,ROUND_FLOOR))
    desired=magnitude.copy_sign(raw)
    # A reversal is two separately reconciled decisions, never concurrent legs.
    reversing=held*desired<0
    interim=Decimal(0) if reversing else desired
    delta=interim-held
    result.update(target_quantity=canonical_decimal(desired),current_quantity=canonical_decimal(held))
    if delta==0:
        return result
    side='BUY' if delta>0 else 'SELL'
    reducing=held*delta<0 and abs(delta)<=abs(held)
    quantity=abs(delta)
    require(quantity>=minimum and (quantity-minimum)%step==0,'PORTFOLIO_QUANTITY_GRID')
    book=Decimal(data['book']['askPrice' if side=='BUY' else 'bidPrice'])
    offset=Decimal(allocation['ioc_offset_bps'])/10000
    price=_grid(book*(1+offset if side=='BUY' else 1-offset),Decimal(tick['minPrice']),
        Decimal(tick['tickSize']),ROUND_CEILING if side=='BUY' else ROUND_FLOOR)
    # Original venue and risk checks reject dust or excess; no upward size bump,
    # silent capital clipping, or changes to the approved allocation are allowed.
    identity={'allocation':_digest(allocation),'forecast':forecast['result_hash'],
        'held':canonical_decimal(held),'target':canonical_decimal(interim)}
    order={'symbol':allocation['symbol'],'side':side,'type':'LIMIT','timeInForce':'IOC',
        'quantity':canonical_decimal(quantity),'price':canonical_decimal(price),'reduceOnly':reducing,
        'positionSide':'BOTH','newClientOrderId':'QDP'+_digest(identity)[:30],'newOrderRespType':'ACK'}
    prepared=demo_request_inputs.build(orders=[order],origin=origin,market_inputs=market_inputs,
        profile=profile,issuer_policy=issuer_policy,kill_state=kill_state,
        dispatch=dispatch,recovery=recovery,oms=oms,now_ms=now_ms)
    require(prepared['precheck_status']=='RISK_CHECKS_PASSED','PORTFOLIO_RISK_REJECTED')
    require(prepared['request']['risk']['account']==account and
        prepared['position_context_sha256']==(None if position is None else _digest(position)),
        'PORTFOLIO_ACCOUNT_CHANGED_DURING_PREPARATION')
    task={'schema':'DEMO_REQUEST_TASK_V1','source_commit':allocation['source_commit'],
        'valid_from_ms':now_ms,'valid_until_ms':min(now_ms+30000,intent['valid_until_ms'],issuer_policy['valid_until_ms']),
        'issuer_policy_sha256':_digest(issuer_policy),'profile':profile,
        'kill_switch':kill_state.model_dump(mode='json'),'orders':[order]}
    demo_request_inputs.task_check(task,policy=issuer_policy,source_commit=allocation['source_commit'],now_ms=now_ms)
    result.update(task=task,request_build=prepared,task_sha256=_digest(task),
        status='REDUCE_BEFORE_REVERSE' if reversing else 'TASK_PREPARED')
    return json.loads(json.dumps(result,allow_nan=False))
