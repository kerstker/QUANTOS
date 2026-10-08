"""Offline conversion of frozen public REST observations into technical frames.

Observation-time mark valuation is explicit: the original exchange/receive clocks
remain in the manifest. The default path refuses crossed funding settlements;
Explicit gold receipt mode models forecast boundaries with receipt embargoes;
multiple observed boundaries require the stable continuous-session input schema.
Cross-day session inputs require received daily ADV evidence. Other paths reject
day boundaries; all paths reject changing filters and unsupported grids.
It cannot certify historical venue margin tiers or account-specific fee schedules.
"""
from __future__ import annotations
from collections.abc import Mapping
import base64
import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from dataclasses import replace
from services.execution.public_funding_observations import load_funding_observations
from services.execution.authoritative_perpetual_backtest import (
    CapacitySnapshot, FundingPolicySnapshot, HistoricalContractRule,
)
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, CapacityAction, CapacityPolicy,
    ContractSpec, MarginBracket, MarkEvent, ledger_decimal_context, canonical_decimal,
)
from services.execution.perpetual_simulation import (
    ExecutableQuote, OrderRuleProfile, SimulationFrame, ForecastFundingPolicySnapshot,
    RollingForecastFundingPolicySnapshot,
)
from services.execution.venue_order_filters import parse_filters
from services.execution.public_order_constraints import parse_constraints, constraint_manifest

DAY_MS=86_400_000


def _daily_adv(candles, day, adv_quantum):
    expected = list(range(day-30*DAY_MS, day, DAY_MS))
    if (not isinstance(candles, list) or len(candles) != 30
            or [_integer(c[0]) for c in candles] != expected
            or any(_integer(c[6]) != c[0]+DAY_MS-1 for c in candles)):
        raise ValueError('thirty complete UTC days required for ADV')
    volumes = [_decimal(c[7]) for c in candles]
    if any(len(v.as_tuple().digits) > 48 or v.adjusted() > 24
           or v.as_tuple().exponent < -24 for v in volumes):
        raise ValueError('public ADV numeric precision exceeds budget')
    exact = sum((Fraction(v) for v in volumes), Fraction(0)) / 30
    return Decimal(canonical_decimal(Decimal(exact // Fraction(adv_quantum)) * adv_quantum))
# Reviewed public contract/clearing definitions, not an account or historical attestation.
GOLD_UNIT_BASIS = {
    'scope': 'ENGINEERING_MAPPING_NOT_HISTORICAL_ATTESTATION',
    'quantity_unit': 'XAU', 'ledger_multiplier': '1',
    'contract_spec_sha256': 'e6cdd2a9d1f65380634a191c60ccee8f78dc1657c21809f5206fd553db3fe014',
    'clearing_procedures_sha256': 'b86bd5d3c3f9812f0f1f6441a38709fc368c13452c22d1355e869d2bd8b4e948',
}


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


def decode_public_gzip(wire):
    """Bound both compressed input and decoded entity before JSON parsing."""
    import gzip
    import io
    if len(wire)>10_000_000:raise ValueError('public response exceeds size budget')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(wire)) as stream:
            raw=stream.read(10_000_001)
    except (OSError,EOFError) as exc:
        raise ValueError('invalid compressed public response') from exc
    if len(raw)>10_000_000:raise ValueError('public response exceeds size budget')
    return raw


def _record(record,kind):
    if not isinstance(record,Mapping) or record['kind']!=kind: raise ValueError('wrong source kind')
    requested,received=_integer(record['requested_at']),_integer(record['received_at'])
    if not requested<=received or received-requested>30_000: raise ValueError('invalid receive window')
    raw=base64.b64decode(record['response_base64'],validate=True)
    wire_fields={'content_encoding','wire_response_base64','wire_response_sha256'}
    if wire_fields.intersection(record):
        if not wire_fields.issubset(record) or record['content_encoding']!='gzip':
            raise ValueError('incomplete public transport binding')
        wire=base64.b64decode(record['wire_response_base64'],validate=True)
        if hashlib.sha256(wire).hexdigest()!=record['wire_response_sha256'] or decode_public_gzip(wire)!=raw:
            raise ValueError('public transport binding mismatch')
    if (hashlib.sha256(raw).hexdigest()!=record['response_sha256']
            or json.loads(raw,object_pairs_hook=unique_json_object)!=record['payload']):
        raise ValueError('raw public response binding mismatch')
    return record['payload'],received


def _symbol(payload, instrument="BTCUSDT"):
    symbols=[s for s in payload['symbols'] if s['symbol']==instrument]
    if len(symbols)!=1: raise ValueError('target rules missing or duplicated')
    symbol=symbols[0]
    expected_type = 'TRADIFI_PERPETUAL' if instrument == 'XAUUSDT' else 'PERPETUAL'
    if instrument == 'XAUUSDT' and any(symbol.get(k) != v for k, v in
            {'pair': 'XAUUSDT', 'baseAsset': 'XAU', 'quoteAsset': 'USDT'}.items()):
        raise ValueError('wrong gold units')
    if symbol['contractType']!=expected_type or symbol['marginAsset']!='USDT' or symbol['status']!='TRADING':
        raise ValueError('unsupported contract status/type')
    return symbol


@ledger_decimal_context()
def prepare_public_replay(capture: Mapping, *, observed_funding_records=None,
                          settlement_window_ms=None) -> dict:
    from quantos.contracts.gold_observation_window import (
        EXTENDED_CAPTURE_SCHEMA, EXTENDED_MAX_SAMPLES, window_closes,
    )
    if not isinstance(capture,Mapping): raise ValueError('capture must be an object')
    document=dict(capture)
    digest=document.pop('capture_id',None)
    if digest!=exact_identity_hash(document,name='public_replay_capture'):
        raise ValueError('capture identity mismatch')
    instrument = document['symbol']
    gold = instrument == 'XAUUSDT'
    observed = observed_funding_records is not None
    if observed:
        if (not gold or type(observed_funding_records) not in (list, tuple)
                or not 1 <= len(observed_funding_records) <= 1000
                or type(settlement_window_ms) is not int or not 1 <= settlement_window_ms <= 1000):
            raise ValueError('INVALID_OBSERVED_FUNDING_MODE')
    elif settlement_window_ms is not None:
        raise ValueError('FUNDING_RECORDS_REQUIRED')
    session_prefix = gold and document['schema_version'] == 'GOLD_PUBLIC_SESSION_PREFIX_V1'
    gap_policy = document.get('observation_gap_policy')
    if gap_policy is not None and (not session_prefix or gap_policy != 'FLAT_ACCOUNT_PAUSE_V1'):
        raise ValueError('INVALID_OBSERVATION_GAP_POLICY')
    observation_gaps = []
    continuous = gold and document['schema_version'] == 'GOLD_PUBLIC_CONTINUOUS_CAPTURE_V1'
    extended = gold and document['schema_version'] == EXTENDED_CAPTURE_SCHEMA
    expected_schema = ('GOLD_PUBLIC_SESSION_PREFIX_V1' if session_prefix else EXTENDED_CAPTURE_SCHEMA if extended else 'GOLD_PUBLIC_CONTINUOUS_CAPTURE_V1' if continuous else
                       'GOLD_PUBLIC_REPLAY_CAPTURE_V1' if gold else 'BINANCE_PUBLIC_REPLAY_CAPTURE_V2')
    if instrument not in ('BTCUSDT', 'XAUUSDT') or document['schema_version'] != expected_schema:
        raise ValueError('unsupported capture schema/symbol')
    if gold and document.get('quantity_basis') != GOLD_UNIT_BASIS:
        raise ValueError('gold quantity basis missing or changed')
    samples=document['samples']
    lower, upper = (3, 86401) if session_prefix else (61, EXTENDED_MAX_SAMPLES) if extended else (61, 240) if continuous else (3, 60)
    if not isinstance(samples,list) or not lower <= len(samples) <= upper: raise ValueError('sample budget invalid')
    closes = ()
    if extended:
        window = document.get('window')
        if (type(window) is not dict or set(window) != {'close_times_ms', 'post_close_ms'}
                or type(window['post_close_ms']) is not int or window['post_close_ms'] != 30000):
            raise ValueError('extended capture window missing or changed')
        closes = window_closes(window['close_times_ms'])
    max_gap=_integer(document['max_gap_ms'])
    if not 1<=max_gap<=30_000: raise ValueError('gap budget invalid')
    bootstrap=document['bootstrap']
    exchange,rule_time=_record(bootstrap['exchange'],'exchange')
    symbol=_symbol(exchange, instrument)
    after,after_time=_record(document['exchange_after'],'exchange')
    if _symbol(after, instrument)!=symbol: raise ValueError('order rules changed during capture')
    filters={x['filterType']:x for x in symbol['filters']}
    if len(filters)!=len(symbol['filters']): raise ValueError('duplicate rule filter')
    try:
        notional=filters['MIN_NOTIONAL']
        venue_filters=parse_filters(symbol)
        step,minimum=venue_filters.quantity_atom,min(venue_filters.limit.minimum,venue_filters.market.minimum)
        minimum_notional=_decimal(notional['notional'])
    except KeyError as exc: raise ValueError('required order filter missing') from exc
    candles,adv_time=_record(bootstrap['candles'],'candles')
    funding,funding_time=_record(bootstrap['funding'],'funding')
    if len(funding)!=1 or funding[0]['symbol']!=instrument: raise ValueError('last funding source missing')
    last_settlement=_integer(funding[0]['fundingTime'])
    normalized=[]
    previous_time=None
    previous_mark_time=None
    next_settlement=None
    clock_records=[]
    first_settlement=None
    rollover=None
    forecast_observations=[]
    for sample in samples:
        mark,mark_received=_record(sample['mark'],'mark')
        depth,received=_record(sample['depth'],'depth')
        mark_time=_integer(mark['time'])
        depth_time=_integer(depth['T'])
        if (mark['symbol']!=instrument or mark_time>mark_received or depth_time>received
                or mark_received>received or received-mark_time>max_gap or received-depth_time>max_gap
                or previous_time is not None and (received<=previous_time
                    or received-previous_time>max_gap and gap_policy is None)
                or previous_mark_time is not None and mark_time<previous_mark_time):
            raise ValueError('noncausal, stale or disordered public observation')
        if previous_time is not None and received-previous_time > max_gap:
            observation_gaps.append(dict(pause_at_ms=previous_time+1, resume_at_ms=received))
        settlement=_integer(mark['nextFundingTime'])
        if first_settlement is None:
            first_settlement = settlement
            forecast_observations.append((settlement, mark_time, mark_received))
        if next_settlement is not None and settlement != next_settlement:
            if not observed:
                raise ValueError('funding schedule changed in sample')
            if mark_time < next_settlement or settlement <= next_settlement:
                raise ValueError('FORECAST_CHANGED_BEFORE_SETTLEMENT')
            rollover = settlement
            forecast_observations.append((settlement, mark_time, mark_received))
        next_settlement = settlement
        if not last_settlement < received or (not observed and received >= settlement):
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
        quote=ExecutableQuote(instrument,depth_time,received,bid,ask,bid_qty,ask_qty)
        normalized.append((received,_decimal(mark['markPrice']),quote))
        clock_records.append({'frame_time':received,'mark_event_time':mark_time,
            'mark_available_time':mark_received,'quote_event_time':depth_time,
            'quote_available_time':received,'depth_update_id':_integer(depth['lastUpdateId'])})
        previous_time,previous_mark_time=received,mark_time
    start,end=normalized[0][0],normalized[-1][0]
    if extended and not (closes[0] - 180000 <= start < closes[0]
                         and closes[-1] + 30000 <= end <= closes[-1] + 60000):
        raise ValueError('extended capture does not cover planned window')
    day=start//DAY_MS*DAY_MS
    daily_receipts = document.get('capacity_observations')
    if daily_receipts is not None and (not session_prefix or type(daily_receipts) is not list
                                      or not 1 <= len(daily_receipts) <= 31):
        raise ValueError('DAILY_CAPACITY_RECEIPTS_INVALID')
    if (end//DAY_MS!=start//DAY_MS and daily_receipts is None) or after_time<end:
        raise ValueError('day boundary or final rule observation invalid')
    if max(rule_time,adv_time,funding_time)>start:
        raise ValueError('bootstrap evidence unavailable at first decision')
    # Floor the capacity limit to the ledger cash grid using exact rationals.
    # Division by 30 can repeat; Decimal context rounding alone is not a cash grid.
    cash_quantum = Decimal('1e-24')
    participation = Decimal('.01')
    adv_quantum = cash_quantum / participation
    adv = _daily_adv(candles, day, adv_quantum)
    # Deliberately conservative simulation settings, NOT historical venue tiers.
    spec=ContractSpec(instrument,'LINEAR_USDT_PERPETUAL','USDT',Decimal('1'),step,minimum,
        minimum_notional,Decimal('.01'),cash_quantum,
        (MarginBracket(Decimal('0'),None,Decimal('1'),Decimal('.5'),Decimal('0')),))
    rule_id='public-filter-observation:'+str(rule_time)
    source=('public-session-bootstrap:'+exact_identity_hash(bootstrap,name='public_session_bootstrap')
            if session_prefix else 'public-capture:'+digest)
    contract=HistoricalContractRule(rule_id,spec,rule_time,end+1,
        bootstrap['exchange']['requested_at'],rule_time,rule_time,source)
    types=tuple(t for t in ('MARKET','LIMIT') if t in symbol['orderTypes'])
    profile=OrderRuleProfile(rule_id,rule_id,instrument,'BINANCE_USDM',rule_time,end+1,
        bootstrap['exchange']['requested_at'],rule_time,rule_time,source,
        venue_filters.price.step,venue_filters.price.minimum,venue_filters.price.maximum,
        venue_filters.market.maximum,venue_filters.limit.maximum,Decimal('1000'),'ORIGINAL_ORDER',types,
        parse_constraints(symbol),venue_filters)
    interval=first_settlement-last_settlement
    schedule_available=max(funding_time,clock_records[0]['mark_available_time'])
    crossed = tuple(t for t, _, _ in forecast_observations if start < t <= end)
    rolling = observed and len(crossed) > 1
    if rolling and not session_prefix:
        raise ValueError('ROLLING_FUNDING_REQUIRES_SESSION_PREFIX')
    if observed:
        if not start < first_settlement <= end or not rolling and rollover is not None and end >= rollover:
            raise ValueError('SINGLE_FORECAST_BOUNDARY_REQUIRED')
        if rolling:
            policy = RollingForecastFundingPolicySnapshot(instrument, 'BINANCE_USDM', source, start, end,
                interval, crossed, last_settlement, schedule_available, schedule_available,
                source, settlement_window_ms=settlement_window_ms,
                forecast_observations=tuple(forecast_observations))
        else:
            policy = ForecastFundingPolicySnapshot(instrument, 'BINANCE_USDM', source, start, end,
                interval, (first_settlement,), last_settlement, schedule_available, schedule_available,
                source, settlement_window_ms=settlement_window_ms)
    else:
        policy=FundingPolicySnapshot(instrument,'BINANCE_USDM',source,start,next_settlement,
            interval,(next_settlement,),last_settlement,schedule_available,schedule_available,source)
    capacity_policy=CapacityPolicy('BINANCE_USDM',source,BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
        day//DAY_MS,adv,participation,CapacityAction.REJECT)
    capacity=CapacitySnapshot(instrument,capacity_policy,day-30*DAY_MS,day,adv_time,adv_time,source)
    frames=tuple(SimulationFrame(t,(q,),(MarkEvent(t,instrument,mark),),(capacity,))
                 for t,mark,q in normalized)
    capacity_bindings = []
    if daily_receipts is not None:
        additions = {}
        prior_day = day
        for record in daily_receipts:
            values, available = _record(record, 'candles')
            next_day = prior_day + DAY_MS
            if not next_day <= record['requested_at'] <= available <= end or available >= next_day+DAY_MS:
                raise ValueError('DAILY_CAPACITY_RECEIPT_CLOCK')
            try:
                next_adv = _daily_adv(values, next_day, adv_quantum)
            except (ValueError, IndexError, TypeError) as exc:
                raise ValueError('DAILY_CAPACITY_WINDOW') from exc
            receipt_id = exact_identity_hash(record, name='public_daily_capacity_receipt')
            provenance = 'public-daily-capacity:'+receipt_id
            next_policy = CapacityPolicy('BINANCE_USDM', provenance,
                BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, next_day//DAY_MS,
                next_adv, participation, CapacityAction.REJECT)
            additions[available] = CapacitySnapshot(instrument, next_policy,
                next_day-30*DAY_MS, next_day, available, available, provenance)
            capacity_bindings.append(dict(day_start_ms=next_day, available_at_ms=available,
                                          receipt_sha256=receipt_id))
            prior_day = next_day
        if prior_day != end//DAY_MS*DAY_MS:
            raise ValueError('DAILY_CAPACITY_COVERAGE')
        # The old capacity expires at midnight. Before the new receipt, the
        # original engine rejects new admission/fills with MISSING_CURRENT_CAPACITY.
        by_time = {f.timestamp: replace(f, capacity_snapshots=())
                   if f.timestamp >= day+DAY_MS else f for f in frames}
        for available, snapshot in additions.items():
            frame = by_time.get(available, SimulationFrame(available))
            by_time[available] = replace(frame, capacity_snapshots=(snapshot,))
        frames = tuple(by_time[t] for t in sorted(by_time))
    funding_bindings = []
    if observed:
        settlements = {}
        for record in observed_funding_records:
            _, available = _record(record, 'funding')
            requested = record['requested_at']
            raw = base64.b64decode(record['response_base64'], validate=True)
            observations = load_funding_observations(raw, start_ms=0, end_ms=requested,
                requested_at_ms=requested, received_at_ms=available)
            funding_bindings.append(dict(record_sha256=exact_identity_hash(record, name='funding_receipt'),
                requested_at_ms=requested, available_at_ms=available,
                response_sha256=record['response_sha256']))
            for item in observations:
                timestamp = item.event.timestamp
                if not start <= timestamp <= end:
                    continue
                prior = settlements.get(timestamp)
                if prior is not None and prior.event != item.event:
                    raise ValueError('CONFLICTING_FUNDING_SETTLEMENT')
                if prior is None or item.available_at_ms < prior.available_at_ms:
                    settlements[timestamp] = item
        if len(settlements) != len(crossed):
            raise ValueError('OBSERVED_SETTLEMENT_COVERAGE' if rolling else 'SINGLE_OBSERVED_SETTLEMENT_REQUIRED')
        matched = []
        for nominal in crossed:
            candidates = [item for item in settlements.values()
                          if nominal <= item.event.timestamp <= nominal + settlement_window_ms]
            if len(candidates) != 1:
                raise ValueError('FUNDING_OUTSIDE_FORECAST_WINDOW')
            item = candidates[0]
            if item.available_at_ms > end:
                raise ValueError('FUNDING_RECEIPT_OUTSIDE_CAPTURE')
            matched.append((nominal, item))
        by_time = {frame.timestamp: frame for frame in frames}
        for nominal, item in matched:
            pause = by_time.get(nominal, SimulationFrame(nominal))
            by_time[nominal] = replace(pause, funding_pause_until=item.available_at_ms)
            frame = by_time.get(item.event.timestamp, SimulationFrame(item.event.timestamp))
            by_time[item.event.timestamp] = replace(frame,
                market_events=(item.event,) + frame.market_events,
                funding_available_at=item.available_at_ms)
        frames = tuple(by_time[t] for t in sorted(by_time))
    if observation_gaps:
        by_time = {frame.timestamp: frame for frame in frames}
        for gap in observation_gaps:
            pause = by_time.get(gap['pause_at_ms'], SimulationFrame(gap['pause_at_ms']))
            by_time[pause.timestamp] = replace(pause, observation_pause_until=gap['resume_at_ms'])
        frames = tuple(by_time[t] for t in sorted(by_time))
    parts={name:exact_identity_hash(value,name='public_part') for name,value in bootstrap.items()}
    manifest={'capture_id':digest,'authority':'TECHNICAL_VALIDATION_ONLY','venue':'BINANCE_USDM',
        'instruments':[instrument],'start_ms':start,'end_ms':end,'partition_hashes':parts,
        'observation_clocks':clock_records,'units':{'price':'USDT','quantity':'BASE','volume':'QUOTE_USDT'},
        'gap_policy_ms':max_gap,'rule_policy':'FIRST_OBSERVATION_WITH_UNCHANGED_FINAL_FILTERS',
        'capacity_quantization': {'policy': 'EXACT_ADV_CAPACITY_FLOOR_V1',
            'cash_quantum': str(cash_quantum), 'participation_rate': str(participation),
            'adv_quantum': str(adv_quantum), 'rounding': 'FLOOR'},
        'mark_policy':'OBSERVATION_TIME_VALUATION_WITH_ORIGINAL_CLOCKS_RETAINED',
        'venue_order_constraints':constraint_manifest(profile.venue_constraints),
        'venue_order_filters':venue_filters.manifest(),
        'limitations':['UNSIGNED_SOURCE_RECEIPTS','VENUE_MARGIN_TIERS_UNAVAILABLE_SIMULATION_IM_100_PERCENT_MM_50_PERCENT',
            'ACCOUNT_FEE_TIER_UNAVAILABLE_EXPLICIT_SIMULATION_COST','NO_FUNDING_SETTLEMENT_IN_SAMPLE',
            'REST_TOP_OF_BOOK_NOT_QUEUE_SIMULATION','DYNAMIC_VENUE_FILTERS_NOT_FULLY_MODELED',
            'FILTER_ENDPOINT_EQUALITY_NOT_PROOF_OF_NO_INTERMEDIATE_RULE_CHANGE']}
    if daily_receipts is not None:
        manifest['capacity_observations'] = capacity_bindings
        manifest['limitations'].append('DAILY_CAPACITY_UNAVAILABLE_UNTIL_ORIGINAL_RECEIPT')
    if gap_policy is not None:
        manifest['observation_gap_policy'] = gap_policy
        manifest['observation_gaps'] = observation_gaps
        manifest['limitations'].append('GAPS_NOT_RECONSTRUCTED_FLAT_ACCOUNT_PAUSE_ONLY')
    if observed:
        manifest['limitations'].remove('NO_FUNDING_SETTLEMENT_IN_SAMPLE')
        manifest['limitations'].extend(['FORECAST_TIMING_BOUND_UNVERIFIED',
            'SINGLE_BOUNDARY_COVERAGE_ASSUMPTION_NOT_VENUE_INTERVAL_ATTESTATION',
            'CANCEL_AND_PAUSE_UNTIL_PUBLIC_RECEIPT_SIMULATION_ONLY'])
        manifest['observed_funding'] = dict(receipts=funding_bindings,
            settlement=item.as_document(), forecast_nominal_ms=first_settlement,
            settlement_window_ms=settlement_window_ms,
            historical_gap_ms=interval, account_cash_verified=False)
        if rolling:
            manifest['limitations'].remove('SINGLE_BOUNDARY_COVERAGE_ASSUMPTION_NOT_VENUE_INTERVAL_ATTESTATION')
            manifest['limitations'].append('ROLLING_FORECAST_COVERAGE_NOT_VENUE_INTERVAL_ATTESTATION')
            manifest['observed_funding'] = dict(receipts=funding_bindings,
                settlements=[dict(forecast_nominal_ms=t, settlement=item.as_document()) for t, item in matched],
                forecasts=policy.as_canonical_dict(), settlement_window_ms=settlement_window_ms,
                historical_gap_ms=interval, account_cash_verified=False)
    if gold:
        manifest['quantity_basis'] = dict(GOLD_UNIT_BASIS)
        manifest['limitations'].append('GOLD_QUANTITY_MAPPING_NOT_HISTORICAL_OR_ACCOUNT_ATTESTATION')
    return {'contract_rules':(contract,),'order_rule_profiles':(profile,),
            'funding_policies':(policy,),'frames':frames,'manifest':manifest}
