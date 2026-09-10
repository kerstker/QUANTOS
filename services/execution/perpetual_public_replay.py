"""Offline conversion of frozen public REST observations into technical frames.

Observation-time mark valuation is explicit: the original exchange/receive clocks
remain in the manifest. This bounded adapter refuses funding settlements inside
its sample, changing filters, day boundaries, missing ADV and unsupported grids.
It cannot certify historical venue margin tiers or account-specific fee schedules.
"""
from __future__ import annotations
from collections.abc import Mapping
import base64
import hashlib
import json
from decimal import Decimal
from services.execution.authoritative_perpetual_backtest import (
    CapacitySnapshot, FundingPolicySnapshot, HistoricalContractRule,
)
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, CapacityAction, CapacityPolicy,
    ContractSpec, MarginBracket, MarkEvent, ledger_decimal_context,
)
from services.execution.perpetual_simulation import ExecutableQuote, OrderRuleProfile, SimulationFrame
from services.execution.public_order_constraints import parse_constraints, constraint_manifest

DAY_MS=86_400_000


def unique_json_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate JSON object key')
        result[key]=value
    return result


def _integer(value):
    if type(value) is not int or value<0: raise ValueError('invalid public timestamp/integer')
    return value


def _decimal(value):
    if not isinstance(value,str): raise ValueError('public decimal must be a string')
    number=Decimal(value)
    if not number.is_finite() or number<=0: raise ValueError('invalid public decimal')
    return number


def _record(record,kind):
    if not isinstance(record,Mapping) or record['kind']!=kind: raise ValueError('wrong source kind')
    requested,received=_integer(record['requested_at']),_integer(record['received_at'])
    if not requested<=received or received-requested>30_000: raise ValueError('invalid receive window')
    raw=base64.b64decode(record['response_base64'],validate=True)
    if (hashlib.sha256(raw).hexdigest()!=record['response_sha256']
            or json.loads(raw,object_pairs_hook=unique_json_object)!=record['payload']):
        raise ValueError('raw public response binding mismatch')
    return record['payload'],received


def _symbol(payload):
    symbols=[s for s in payload['symbols'] if s['symbol']=='BTCUSDT']
    if len(symbols)!=1: raise ValueError('BTCUSDT rules missing or duplicated')
    symbol=symbols[0]
    if symbol['contractType']!='PERPETUAL' or symbol['marginAsset']!='USDT' or symbol['status']!='TRADING':
        raise ValueError('unsupported contract status/type')
    return symbol


@ledger_decimal_context()
def prepare_public_replay(capture: Mapping) -> dict:
    if not isinstance(capture,Mapping): raise ValueError('capture must be an object')
    document=dict(capture)
    digest=document.pop('capture_id',None)
    if digest!=exact_identity_hash(document,name='public_replay_capture'):
        raise ValueError('capture identity mismatch')
    if document['schema_version']!='BINANCE_PUBLIC_REPLAY_CAPTURE_V2' or document['symbol']!='BTCUSDT':
        raise ValueError('unsupported capture schema/symbol')
    samples=document['samples']
    if not isinstance(samples,list) or not 3<=len(samples)<=60: raise ValueError('sample budget invalid')
    max_gap=_integer(document['max_gap_ms'])
    if not 1<=max_gap<=30_000: raise ValueError('gap budget invalid')
    bootstrap=document['bootstrap']
    exchange,rule_time=_record(bootstrap['exchange'],'exchange')
    symbol=_symbol(exchange)
    after,after_time=_record(document['exchange_after'],'exchange')
    if _symbol(after)!=symbol: raise ValueError('order rules changed during capture')
    filters={x['filterType']:x for x in symbol['filters']}
    if len(filters)!=len(symbol['filters']): raise ValueError('duplicate rule filter')
    try:
        prices,lot,market,notional=(filters[k] for k in ('PRICE_FILTER','LOT_SIZE','MARKET_LOT_SIZE','MIN_NOTIONAL'))
        if lot['stepSize']!=market['stepSize'] or lot['minQty']!=market['minQty']:
            raise ValueError('distinct market/limit minimum grids unsupported')
        step,minimum=_decimal(lot['stepSize']),_decimal(lot['minQty'])
        minimum_notional=_decimal(notional['notional'])
    except KeyError as exc: raise ValueError('required order filter missing') from exc
    candles,adv_time=_record(bootstrap['candles'],'candles')
    funding,funding_time=_record(bootstrap['funding'],'funding')
    if len(funding)!=1 or funding[0]['symbol']!='BTCUSDT': raise ValueError('last funding source missing')
    last_settlement=_integer(funding[0]['fundingTime'])
    normalized=[]
    previous_time=None
    previous_mark_time=None
    next_settlement=None
    clock_records=[]
    for sample in samples:
        mark,mark_received=_record(sample['mark'],'mark')
        depth,received=_record(sample['depth'],'depth')
        mark_time=_integer(mark['time'])
        depth_time=_integer(depth['T'])
        if (mark['symbol']!='BTCUSDT' or mark_time>mark_received or depth_time>received
                or mark_received>received or received-mark_time>max_gap or received-depth_time>max_gap
                or previous_time is not None and (received<=previous_time or received-previous_time>max_gap)
                or previous_mark_time is not None and mark_time<previous_mark_time):
            raise ValueError('noncausal, stale or disordered public observation')
        settlement=_integer(mark['nextFundingTime'])
        if next_settlement is not None and settlement!=next_settlement:
            raise ValueError('funding schedule changed in sample')
        next_settlement=settlement
        if not last_settlement<received<settlement:
            raise ValueError('sample crossing a funding settlement is unsupported')
        bid_levels,ask_levels=depth['bids'],depth['asks']
        def levels(rows,buy):
            if not isinstance(rows,list) or not 1<=len(rows)<=1000: raise ValueError('invalid depth size')
            pairs=[(_decimal(r[0]),_decimal(r[1])) for r in rows if isinstance(r,list) and len(r)==2]
            if len(pairs)!=len(rows): raise ValueError('malformed depth level')
            values=[p for p,_ in pairs]
            if values!=sorted(set(values),reverse=buy): raise ValueError('duplicate/disordered depth')
            return pairs[0]
        bid,bid_qty=levels(bid_levels,True)
        ask,ask_qty=levels(ask_levels,False)
        quote=ExecutableQuote('BTCUSDT',depth_time,received,bid,ask,bid_qty,ask_qty)
        normalized.append((received,_decimal(mark['markPrice']),quote))
        clock_records.append({'frame_time':received,'mark_event_time':mark_time,
            'mark_available_time':mark_received,'quote_event_time':depth_time,
            'quote_available_time':received,'depth_update_id':_integer(depth['lastUpdateId'])})
        previous_time,previous_mark_time=received,mark_time
    start,end=normalized[0][0],normalized[-1][0]
    day=start//DAY_MS*DAY_MS
    if end//DAY_MS!=start//DAY_MS or after_time<end:
        raise ValueError('day boundary or final rule observation invalid')
    if max(rule_time,adv_time,funding_time)>start:
        raise ValueError('bootstrap evidence unavailable at first decision')
    expected=list(range(day-30*DAY_MS,day,DAY_MS))
    if (not isinstance(candles,list) or len(candles)!=30
            or [_integer(c[0]) for c in candles]!=expected
            or any(_integer(c[6])!=c[0]+DAY_MS-1 for c in candles)):
        raise ValueError('thirty complete UTC days required for ADV')
    adv=sum((_decimal(c[7]) for c in candles),Decimal('0'))/30
    # Deliberately conservative simulation settings, NOT historical venue tiers.
    spec=ContractSpec('BTCUSDT','LINEAR_USDT_PERPETUAL','USDT',Decimal('1'),step,minimum,
        minimum_notional,Decimal('.01'),Decimal('1e-24'),
        (MarginBracket(Decimal('0'),None,Decimal('1'),Decimal('.5'),Decimal('0')),))
    rule_id='public-filter-observation:'+str(rule_time)
    source='public-capture:'+digest
    contract=HistoricalContractRule(rule_id,spec,rule_time,end+1,
        bootstrap['exchange']['requested_at'],rule_time,rule_time,source)
    types=tuple(t for t in ('MARKET','LIMIT') if t in symbol['orderTypes'])
    profile=OrderRuleProfile(rule_id,rule_id,'BTCUSDT','BINANCE_USDM',rule_time,end+1,
        bootstrap['exchange']['requested_at'],rule_time,rule_time,source,
        _decimal(prices['tickSize']),_decimal(prices['minPrice']),_decimal(prices['maxPrice']),
        _decimal(market['maxQty']),_decimal(lot['maxQty']),Decimal('1000'),'ORIGINAL_ORDER',types,
        parse_constraints(symbol))
    interval=next_settlement-last_settlement
    schedule_available=max(funding_time,clock_records[0]['mark_available_time'])
    policy=FundingPolicySnapshot('BTCUSDT','BINANCE_USDM',source,start,next_settlement,
        interval,(next_settlement,),last_settlement,schedule_available,schedule_available,source)
    capacity_policy=CapacityPolicy('BINANCE_USDM',source,BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
        day//DAY_MS,adv,Decimal('.01'),CapacityAction.REJECT)
    capacity=CapacitySnapshot('BTCUSDT',capacity_policy,day-30*DAY_MS,day,adv_time,adv_time,source)
    frames=tuple(SimulationFrame(t,(q,),(MarkEvent(t,'BTCUSDT',mark),),(capacity,))
                 for t,mark,q in normalized)
    parts={name:exact_identity_hash(value,name='public_part') for name,value in bootstrap.items()}
    manifest={'capture_id':digest,'authority':'TECHNICAL_VALIDATION_ONLY','venue':'BINANCE_USDM',
        'instruments':['BTCUSDT'],'start_ms':start,'end_ms':end,'partition_hashes':parts,
        'observation_clocks':clock_records,'units':{'price':'USDT','quantity':'BASE','volume':'QUOTE_USDT'},
        'gap_policy_ms':max_gap,'rule_policy':'FIRST_OBSERVATION_WITH_UNCHANGED_FINAL_FILTERS',
        'mark_policy':'OBSERVATION_TIME_VALUATION_WITH_ORIGINAL_CLOCKS_RETAINED',
        'venue_order_constraints':constraint_manifest(profile.venue_constraints),
        'limitations':['UNSIGNED_SOURCE_RECEIPTS','VENUE_MARGIN_TIERS_UNAVAILABLE_SIMULATION_IM_100_PERCENT_MM_50_PERCENT',
            'ACCOUNT_FEE_TIER_UNAVAILABLE_EXPLICIT_SIMULATION_COST','NO_FUNDING_SETTLEMENT_IN_SAMPLE',
            'REST_TOP_OF_BOOK_NOT_QUEUE_SIMULATION','DYNAMIC_VENUE_FILTERS_NOT_FULLY_MODELED',
            'FILTER_ENDPOINT_EQUALITY_NOT_PROOF_OF_NO_INTERMEDIATE_RULE_CHANGE']}
    return {'contract_rules':(contract,),'order_rule_profiles':(profile,),
            'funding_policies':(policy,),'frames':frames,'manifest':manifest}
