"""Exact Demo batch risk projection through the supported pre-trade gate.

Native quantities/volumes/caps are all converted by the same fixed unit policy;
money stays in one quote currency. Both sides can fill independently, so opposite
orders cannot reduce each other's projected risk. This is an offline check over
declared inputs, not approval authentication, settlement or submission authority.
The executor must bind these inputs to its actual account, ledger and policy.
"""
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import json
import math

from quantos.contracts.binance_demo_account import _decimal, _int
from quantos.contracts.portfolio import Position
from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import AccountRiskState, MarketContext, RiskContext, StrategyApproval
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.validation.demo_submission import validate_orders

UNIT_SCALE=1_000_000


def _fields(value,names):
    if type(value) is not dict or set(value)!=set(names.split()):raise ValueError('DEMO_RISK_INPUT_FIELDS')


def _exact(value):return Decimal(_decimal(value))


def _units(value):
    amount=_exact(value)*UNIT_SCALE
    if amount!=amount.to_integral_value() or abs(amount)>2**53-1:raise ValueError('DEMO_RISK_QUANTITY_UNITS')
    return int(amount)


def _cap(value):
    if type(value) not in (float,int,Decimal) or not math.isfinite(value) or value<=0:
        raise ValueError('DEMO_RISK_QUANTITY_CAP')
    return _units(format(Decimal(str(value)),'f'))


def _json_value(value):
    if isinstance(value,Decimal):return format(value,'f')
    if isinstance(value,(set,frozenset)):return sorted(_json_value(v) for v in value)
    if isinstance(value,(tuple,list)):return [_json_value(v) for v in value]
    if isinstance(value,dict):return {k:_json_value(v) for k,v in value.items()}
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(_json_value(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _scaled_limits(native):
    if type(native) is not RiskLimits:raise ValueError('DEMO_RISK_LIMITS')
    # RiskLimits historically validates only a subset of scalar fields. Never
    # let a NaN or negative external limit turn comparison checks into no-ops.
    for value in asdict(native).values():
        if isinstance(value,(int,float)) and (isinstance(value,bool) or not math.isfinite(value) or value<0):
            raise ValueError('DEMO_RISK_LIMITS')
    return replace(native,default_max_position_qty=_cap(native.default_max_position_qty),
                   fat_finger_qty=_cap(native.fat_finger_qty),
                   max_position_qty=tuple((symbol,_cap(cap)) for symbol,cap in native.max_position_qty))


@ledger_decimal_context()
def screen(orders, *, account, market, native_limits, strategy, kill_switch, now_ms,
           recent_order_fingerprints=(), recent_timestamps=(), recent_order_statuses=(), plan_turnover=0.0):
    result={'schema':'DEMO_EXACT_BATCH_RISK_V1','status':'REJECTED','unit_scale':UNIT_SCALE,
            'risk_sha256':None,'checks':[],'subset_checks':[],'reasons':[], 'audit_events':[],
            'input_snapshot':None,
            'batch_turnover_fraction':None,
            'source_verified':False,'submission_enabled':False,'economic_authority':False}
    try:
        orders=json.loads(json.dumps(validate_orders(orders),allow_nan=False))
        account=json.loads(json.dumps(account,allow_nan=False));market=json.loads(json.dumps(market,allow_nan=False))
        if type(plan_turnover) not in (int,float) or not math.isfinite(plan_turnover) or plan_turnover<0:
            raise ValueError('DEMO_RISK_TURNOVER')
        if len(recent_order_fingerprints)>10000 or len(recent_timestamps)>10000 or len(recent_order_statuses)>10000:
            raise ValueError('DEMO_RISK_HISTORY_BUDGET')
        if any(not 0<=_int(t)<=now_ms for t in recent_timestamps):raise ValueError('DEMO_RISK_HISTORY_TIME')
        _fields(account,'account_ref_sha256 quote_asset cash equity day_pnl peak_equity positions')
        _fields(market,'symbol reference_price observed_at_ms daily_volume_base instrument_vol known market_open')
        symbol=orders[0]['symbol'];quote=symbol[3:]
        if account['quote_asset']!=quote or market['symbol']!=symbol:raise ValueError('DEMO_RISK_QUOTE_SCOPE')
        ref=account['account_ref_sha256']
        if not isinstance(ref,str) or len(ref)!=64 or any(c not in 'abcdef0123456789' for c in ref):raise ValueError('DEMO_RISK_ACCOUNT')
        if type(strategy) is not StrategyApproval or type(kill_switch) is not KillSwitch:raise ValueError('DEMO_RISK_GATE_INPUT')
        if not 0<_int(market['observed_at_ms'])<=_int(now_ms):raise ValueError('DEMO_RISK_MARKET_TIME')
        if type(market['known']) is not bool or type(market['market_open']) is not bool:raise ValueError('DEMO_RISK_MARKET_STATUS')
        positions=[];seen=set()
        if type(account['positions']) is not list or len(account['positions'])>2000:raise ValueError('DEMO_RISK_POSITIONS')
        for p in account['positions']:
            _fields(p,'symbol quantity mark_price')
            if not isinstance(p['symbol'],str) or not p['symbol'].endswith(quote) or p['symbol'] in seen:
                raise ValueError('DEMO_RISK_POSITION_QUOTE')
            seen.add(p['symbol']);qty=_units(p['quantity']);price=_exact(p['mark_price'])
            if price<=0:raise ValueError('DEMO_RISK_POSITION_PRICE')
            positions.append(Position(instrument_id=p['symbol'],quantity=qty,market_value=float(_exact(p['quantity'])*price)))
        scaled=_scaled_limits(native_limits)
        acct=AccountRiskState(account_id=ref,positions=tuple(positions),
            **{k:float(_exact(account[k])) for k in ('cash','equity','day_pnl','peak_equity')})
        daily_volume=None if market['daily_volume_base'] is None else float(_exact(market['daily_volume_base'])*UNIT_SCALE)
        volatility=None if market['instrument_vol'] is None else float(_exact(market['instrument_vol']))
        if volatility is not None and volatility<0:raise ValueError('DEMO_RISK_VOLATILITY')
        ctx=RiskContext(account=acct,market=MarketContext(instrument_id=symbol,known=market['known'],
            reference_price=float(_exact(market['reference_price'])/UNIT_SCALE),market_open=market['market_open'],
            price_as_of=market['observed_at_ms'],est_daily_volume=daily_volume,instrument_vol=volatility),
            strategy=strategy,now=now_ms,max_staleness=30000,recent_order_fingerprints=tuple(recent_order_fingerprints),
            recent_timestamps=tuple(recent_timestamps),recent_order_statuses=tuple(recent_order_statuses),plan_turnover=plan_turnover)
        gate=PreTradeRiskGate(scaled,kill_switch=kill_switch)
        binding={'schema':result['schema'],'unit_scale':UNIT_SCALE,'orders':orders,'account':account,'market':market,
                 'native_limits':asdict(native_limits),'strategy':strategy.model_dump(mode='json'),
                 'context':ctx.model_dump(mode='json'),'kill_switch':kill_switch.state.model_dump(mode='json')}
        result['risk_sha256']=_digest(binding)
        result['input_snapshot']=_json_value(binding)

        def check(side,quantity,price,client_id):
            qty=_units(quantity);native_price=_exact(price)
            intent=OrderIntent(instrument_id=symbol,side=OrderSide(side),quantity=qty,order_type=OrderType.LIMIT,
                               limit_price=float(native_price/UNIT_SCALE),strategy_id=strategy.strategy_id)
            decision=gate.evaluate(intent,ctx)
            reasons=[r.value for r in decision.reasons]
            # Exact comparisons are additional backstops to the shared float
            # gate; rounding cannot turn a boundary excess into permission.
            notional=_exact(quantity)*native_price
            cap=Decimal(str(native_limits.max_order_notional))
            if notional>cap:reasons.append('ORDER_SIZE_EXCEEDED')
            return {'client_order_id':client_id,'side':side,'quantity_units':qty,
                    'limit_price_per_unit':format(native_price/UNIT_SCALE,'f'),'notional':format(notional.normalize(),'f'),
                    'reasons':list(dict.fromkeys(reasons))}

        for order in orders:
            row=check(order['side'],order['quantity'],order['price'],order['newClientOrderId'])
            held=acct.position_qty(symbol);delta=_units(order['quantity'])*(1 if order['side']=='BUY' else -1)
            if order['reduceOnly'] and (held==0 or held*delta>=0 or abs(delta)>abs(held)):
                row['reasons'].append('DEMO_REDUCE_ONLY_EXCEEDS_POSITION')
            result['checks'].append(row)
        for side in ('BUY','SELL'):
            group=[o for o in orders if o['side']==side]
            if len(group)<2:continue
            quantity=sum((_exact(o['quantity']) for o in group),Decimal(0))
            # Evaluate all orders on a side filling with the opposing side at
            # zero fills. Maximum price also bounds unsigned notional for sells.
            price=max(_exact(o['price']) for o in group)
            row=check(side,format(quantity,'f'),format(price,'f'),None)
            reduce_quantity=sum((_exact(o['quantity']) for o in group if o['reduceOnly']),Decimal(0))
            if reduce_quantity>Decimal(abs(acct.position_qty(symbol)))/UNIT_SCALE:
                row['reasons'].append('DEMO_REDUCE_ONLY_EXCEEDS_POSITION')
            result['subset_checks'].append(row)
        result['audit_events']=[e.model_dump(mode='json') for e in gate.audit.events]
        result['reasons']=list(dict.fromkeys(reason for row in result['checks']+result['subset_checks'] for reason in row['reasons']))
        if _exact(account['equity'])>0:
            turnover=sum((_exact(o['quantity'])*_exact(o['price']) for o in orders),Decimal(0))/_exact(account['equity'])
            result['batch_turnover_fraction']=format(turnover,'f')
            if turnover+Decimal(str(plan_turnover))>Decimal(str(native_limits.max_turnover)):
                result['reasons']=list(dict.fromkeys([*result['reasons'],'TURNOVER_LIMIT_EXCEEDED']))
        if not result['reasons']:result['status']='RISK_CHECKS_PASSED'
    except Exception:
        result['status']='REJECTED';result['reasons']=list(dict.fromkeys([*result['reasons'],'DEMO_RISK_INPUT_INVALID']))
    return result
