"""Offline public rule/capacity bootstrap for a short venue-time passive replay.

Public receipts establish observed filters, never private fee or margin tiers.
The bounded adapter refuses funding settlements inside its window: indicative
stream funding rates must not become invented settlement cash flows.
"""
from decimal import Decimal
import hashlib

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.execution.backtest_authority import exact_identity_hash
from services.execution.authoritative_perpetual_backtest import (
    HistoricalContractRule, FundingPolicySnapshot, CapacitySnapshot,
)
from services.execution.perpetual_ledger_v2 import (
    ContractSpec, MarginBracket, MarkEvent, CapacityPolicy, CapacityAction,
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, ledger_decimal_context,
)
from services.execution.perpetual_simulation import OrderRuleProfile, SimulationFrame
from services.execution.passive_execution_input import _mark

DAY_MS=86400000
SCHEMA='PASSIVE_VENUE_ENVIRONMENT_V1'
BASE_URL='https://fapi.binance.com/fapi/v1/'


def endpoint(kind,symbol,day):
    if symbol not in ('BTCUSDC','BTCUSDT') or type(day) is not int or day<30*DAY_MS or day%DAY_MS:
        raise ValueError('supported contract and UTC day required')
    paths={'exchange':'exchangeInfo','premium':f'premiumIndex?symbol={symbol}',
        'funding':f'fundingRate?symbol={symbol}&limit=1',
        'candles':f'klines?symbol={symbol}&interval=1d&startTime={day-30*DAY_MS}&endTime={day-1}&limit=30'}
    if kind not in paths:raise ValueError('unsupported public environment endpoint')
    return BASE_URL+paths[kind]


def _time(value):
    if type(value) is not int or not 0<=value<=8640000000000000:raise ValueError('invalid public time')
    return value


def _positive(value):
    if not isinstance(value,str) or not 1<=len(value)<=64:raise ValueError('exact public decimal required')
    result=Decimal(value)
    if not result.is_finite() or result<=0:raise ValueError('positive finite public amount required')
    return result


def _receipt(row,kind,symbol,day):
    if not isinstance(row,dict) or row.get('kind')!=kind or row.get('endpoint')!=endpoint(kind,symbol,day):
        raise ValueError('public environment receipt identity mismatch')
    requested,received=_time(row['requested_at']),_time(row['received_at'])
    if not 0<=received-requested<=30000:raise ValueError('invalid public response clock')
    raw=row['raw_utf8']
    if not isinstance(raw,str) or len(raw.encode())>(2000000 if kind=='exchange' else 262144):
        raise ValueError('bounded exact response required')
    if hashlib.sha256(raw.encode()).hexdigest()!=row['response_sha256']:
        raise ValueError('raw environment response hash mismatch')
    return load_json_without_duplicate_keys(raw),received


def _symbol(payload,symbol):
    rows=payload.get('symbols')
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('bounded exchange symbol list required')
    selected=[r for r in rows if isinstance(r,dict) and r.get('symbol')==symbol]
    if len(selected)!=1:raise ValueError('one rule observation per contract required')
    result=selected[0]
    if (result.get('contractType')!='PERPETUAL' or result.get('status')!='TRADING'
            or result.get('quoteAsset')!=symbol[3:] or result.get('marginAsset')!=symbol[3:]):
        raise ValueError('unsupported contract rules or quote currency')
    return result


def prepare_environment(environment,raw_tape,tape,*,max_order_notional):
    try:
        return _prepare_environment(environment,raw_tape,tape,max_order_notional=max_order_notional)
    except (KeyError,TypeError,AttributeError,ArithmeticError) as exc:
        raise ValueError('malformed frozen public account environment') from exc


@ledger_decimal_context()
def _prepare_environment(environment,raw_tape,tape,*,max_order_notional):
    if (not isinstance(environment,dict) or set(environment)!={'schema','symbol','before','exchange_after'}
            or environment['schema']!=SCHEMA or environment['symbol']!=tape.symbol
            or set(environment['before'])!={'exchange','premium','funding','candles'}):
        raise ValueError('complete frozen passive account environment required')
    if not isinstance(max_order_notional,Decimal) or not max_order_notional.is_finite() or max_order_notional<=0:
        raise ValueError('explicit maximum simulated order notional required')
    symbol=tape.symbol;day=tape.start_ms//DAY_MS*DAY_MS
    if tape.end_ms//DAY_MS!=day//DAY_MS:raise ValueError('UTC day crossing requires separate capacity evidence')
    records={k:_receipt(v,k,symbol,day) for k,v in environment['before'].items()}
    after,after_time=_receipt(environment['exchange_after'],'exchange',symbol,day)
    venue=_symbol(records['exchange'][0],symbol)
    if _symbol(after,symbol)!=venue or after_time<tape.end_ms:
        raise ValueError('final contract rules changed or do not cover the replay')
    filters={r['filterType']:r for r in venue['filters']}
    if len(filters)!=len(venue['filters']):raise ValueError('duplicate order filter')
    prices,lot,market,notional=(filters[k] for k in ('PRICE_FILTER','LOT_SIZE','MARKET_LOT_SIZE','MIN_NOTIONAL'))
    if any(_positive(lot[k])!=_positive(market[k]) for k in ('stepSize','minQty')):
        raise ValueError('distinct market/limit minimum grids unsupported')
    premium,premium_time=records['premium'];funding,funding_time=records['funding']
    if (premium['symbol']!=symbol or _time(premium['time'])>premium_time
            or premium_time-premium['time']>30000 or not isinstance(funding,list)
            or len(funding)!=1 or funding[0]['symbol']!=symbol):
        raise ValueError('invalid prior funding schedule observation')
    last,next_time=_time(funding[0]['fundingTime']),_time(premium['nextFundingTime'])
    if not last<tape.start_ms<=tape.end_ms<next_time or last>funding_time:
        raise ValueError('funding settlement in sample requires actual settlement evidence')
    marks=[]
    for row in raw_tape['messages']:
        packet=load_json_without_duplicate_keys(row['raw_utf8'])
        if packet['stream'].endswith('@markPrice@1s'):
            mark=_mark(packet['data'],symbol,row['received_at_ms'])
            if mark['next_settlement_ms']!=next_time:raise ValueError('funding schedule changed inside tape')
            if mark['event_time_ms']<=tape.end_ms:
                if marks and mark['event_time_ms']==marks[-1]['event_time_ms']:
                    if {k:v for k,v in mark.items() if k!='received_at_ms'}!={k:v for k,v in marks[-1].items() if k!='received_at_ms'}:
                        raise ValueError('conflicting mark event at same venue time')
                    continue
                marks.append(mark)
    if not marks or marks[0]['event_time_ms']>tape.start_ms:
        raise ValueError('venue mark unavailable for account initialization')
    start=marks[0]['event_time_ms']
    if max(t for _,t in records.values())>start or not last<start:
        raise ValueError('account bootstrap was not known before venue replay')
    if any(b['event_time_ms']-a['event_time_ms']>3000 for a,b in zip(marks,marks[1:])) or tape.end_ms-marks[-1]['event_time_ms']>3000:
        raise ValueError('mark stream gap exceeds registered three-second budget')
    candles,adv_time=records['candles']
    if (not isinstance(candles,list) or len(candles)!=30 or
            any(not isinstance(c,list) or len(c)!=12 for c in candles) or
            [_time(c[0]) for c in candles]!=list(range(day-30*DAY_MS,day,DAY_MS)) or
            any(_time(c[6])!=c[0]+DAY_MS-1 or c[6]>=adv_time for c in candles)):
        raise ValueError('thirty completed UTC candles known before replay required')
    adv=sum((_positive(c[7]) for c in candles),Decimal('0'))/30
    identity=exact_identity_hash(environment,name='passive_public_environment')
    source='public-environment:'+identity;rule_time=records['exchange'][1];rule_id=source+':filters'
    spec=ContractSpec(symbol,'LINEAR_'+symbol[3:]+'_PERPETUAL',symbol[3:],Decimal('1'),
        _positive(lot['stepSize']),_positive(lot['minQty']),_positive(notional['notional']),
        Decimal('.01'),Decimal('1e-24'),
        (MarginBracket(Decimal('0'),None,Decimal('1'),Decimal('.5'),Decimal('0')),))
    rule=HistoricalContractRule(rule_id,spec,rule_time,tape.end_ms+1,
        environment['before']['exchange']['requested_at'],rule_time,rule_time,source)
    types=tuple(t for t in ('MARKET','LIMIT') if t in venue['orderTypes'])
    if 'LIMIT' not in types:raise ValueError('limit orders unsupported by venue')
    profile=OrderRuleProfile(rule_id,rule_id,symbol,'BINANCE_USDM',rule_time,tape.end_ms+1,
        environment['before']['exchange']['requested_at'],rule_time,rule_time,source,
        _positive(prices['tickSize']),_positive(prices['minPrice']),_positive(prices['maxPrice']),
        _positive(market['maxQty']),_positive(lot['maxQty']),max_order_notional,'ORIGINAL_ORDER',types)
    schedule_time=max(premium_time,funding_time)
    policy=FundingPolicySnapshot(symbol,'BINANCE_USDM',source,start,next_time,next_time-last,
        (next_time,),last,schedule_time,schedule_time,source)
    capacity_policy=CapacityPolicy('BINANCE_USDM',source,BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
        day//DAY_MS,adv,Decimal('.01'),CapacityAction.REJECT)
    capacity=CapacitySnapshot(symbol,capacity_policy,day-30*DAY_MS,day,adv_time,adv_time,source)
    frames=tuple(SimulationFrame(m['event_time_ms'],market_events=(MarkEvent(m['event_time_ms'],symbol,Decimal(m['mark_price'])),),
        capacity_snapshots=(capacity,) if i==0 else ()) for i,m in enumerate(marks))
    manifest={'environment_hash':identity,'symbol':symbol,'quote':symbol[3:],
        'mark_clocks':marks,'mark_policy':'VENUE_EVENT_TIME_FIXED_ACTIONS_ONLY',
        'actual_venue_fees':None,'venue_margin_tiers_verified':False,
        'limitations':['UNSIGNED_PUBLIC_SOURCE_RECEIPTS','SIMULATED_IM_100_PERCENT_MM_50_PERCENT_LIQUIDATION_FEE_1_PERCENT',
            'EXPLICIT_SIMULATED_FEE_AND_ORDER_NOTIONAL_LIMIT','NO_FUNDING_SETTLEMENT_IN_WINDOW',
            'PUBLIC_FILTER_EQUALITY_NOT_PROOF_OF_NO_INTERMEDIATE_CHANGE','DYNAMIC_VENUE_FILTERS_NOT_FULLY_MODELED',
            'ONE_PERCENT_COMPLETED_30_DAY_ADV_CAPACITY_POLICY','VENUE_MARKS_NOT_AN_ADAPTIVE_STRATEGY_FEED']}
    return {'contract_rules':(rule,),'order_rule_profiles':(profile,),'funding_policies':(policy,),
        'account_frames':frames,'manifest':manifest}
