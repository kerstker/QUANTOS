"""Frozen prospective feature collection; no retroactive prediction or ledger claims."""
import math
import re
import numpy as np

from services.research.ai_factory import HOUR
from services.research.ai_factory_forward import forward_features
from services.research.ai_factory_store import digest
from services.research.profitability_information import synchronized_basis, basis_change, basis_specs, controlled_specs

SCHEMA='QUANTOS_CAUSAL_INFORMATION_V1'


def make_plan(*,now_ms,p0_hash):
    if type(now_ms) is not int or now_ms<25*HOUR or not isinstance(p0_hash,str) or not re.fullmatch('[0-9a-f]{64}',p0_hash):
        raise ValueError('registered reconciled P0 and current clock required')
    start=(now_ms//HOUR+1)*HOUR
    return {'schema':SCHEMA,'registered_ms':now_ms,'p0_hash':p0_hash,'warmup_ms':start,
            'first_slot_ms':start+HOUR,'end_slot_ms':start+504*HOUR,'samples':504,
            'mature_ms':start+508*HOUR+360000,'quotes':['USDC','USDT'],
            'clock':'RECEIVED_AND_FROZEN_BEFORE_SLOT_PLUS_5_MINUTES_V1',
            'p3_specs':basis_specs(),'p4_specs':controlled_specs(),'cost_cases':[[4,5],[4,10],[8,5]],
            'model':'RIDGE_L2_1_TRAIN_SCALE_ONLY','threshold_bps':20,
            'selection':'INNER_NET_PNL_MINUS_1500_DRAWDOWN_BASELINE_WINS_TIES',
            'partition':'PURGED_65_17_18_PERCENT_FIXED_504_HOURLY_SLOTS',
            'total_candidate_budget':20,'initial_equity_per_quote':'1500',
            'venue_scenario':{'minimum_quantity':'0.001','quantity_step':'0.001','tick_size':'0.1',
                              'minimum_notional':'50','maximum_quantity':'50','actual_venue_rules_certified':False},
            'maximum_collection_attempts_per_slot':1,'outer_feedback_allowed':False,
            'prediction_evidence':'NONE_FEATURE_COLLECTION_ONLY','economic_eligible':False}


def observation(receipts,*,slot,quote,issued_ms):
    if (type(slot) is not int or slot%HOUR or quote not in ('USDC','USDT') or type(issued_ms) is not int
            or not slot<=issued_ms<slot+300000 or set(receipts)!={'hourly','spot','perpetual','index','funding','oi'}):
        raise ValueError('bounded natural decision clock and complete sources required')
    for row in receipts.values():
        if type(row['received_ms']) is not int or not slot<=row['received_ms']<=issued_ms:
            raise ValueError('actual receive clock must precede frozen decision')
    available=max(r['received_ms'] for r in receipts.values())
    bars=receipts['hourly']['payload']
    if any(type(r[0]) is not int or type(r[6]) is not int for r in bars):raise ValueError('integer hourly clock required')
    features=forward_features(bars,slot)
    prices={}
    for kind in ('spot','perpetual','index'):
        data=receipts[kind]['payload']
        if not isinstance(data,list) or len(data)!=2:raise ValueError('two complete synchronized minutes required')
        selected=[]
        for i,r in enumerate(data):
            if (len(r)<7 or type(r[0]) is not int or type(r[6]) is not int
                    or r[0]!=slot-(2-i)*60000 or r[6]!=r[0]+59999):raise ValueError('basis minute clock mismatch')
            selected.append({'market':kind,'base':'BTC','quote':quote,'price':float(r[4]),
                             'event_ms':r[6],'available_ms':receipts[kind]['received_ms']})
        prices[kind]=selected
    previous=synchronized_basis([v[0] for v in prices.values()],decision_ms=issued_ms)
    current=synchronized_basis([v[1] for v in prices.values()],decision_ms=issued_ms)
    change=basis_change(previous,current)
    funding=receipts['funding']['payload'];oi=receipts['oi']['payload']
    if (not isinstance(funding,list) or len(funding)!=2 or any(r['symbol']!='BTC'+quote for r in funding)
            or oi['symbol']!='BTC'+quote):raise ValueError('currency or funding history mismatch')
    a,b=[r['fundingTime'] for r in funding]
    if any(type(t) is not int for t in (a,b,oi['time'])) or not 0<=a<b<=receipts['funding']['received_ms']:
        raise ValueError('settled funding clocks required')
    intervals=[n for n in (HOUR,4*HOUR,8*HOUR) if abs(b-a-n)<=1000]
    if len(intervals)!=1 or available-b>intervals[0]+300000:raise ValueError('funding interval or freshness unknown')
    if not 0<=receipts['oi']['received_ms']-oi['time']<=300000:raise ValueError('current causal OI required')
    rate=float(funding[-1]['fundingRate']);quantity=float(oi['openInterest'])
    if not math.isfinite(rate) or abs(rate)>1 or not math.isfinite(quantity) or not 0<quantity<1e12:
        raise ValueError('invalid funding or OI units')
    return {'schema':SCHEMA,'slot_ms':slot,'quote':quote,'available_ms':available,'issued_ms':issued_ms,
            'entry_ms':slot+300000,'exit_ms':slot+4*HOUR+300000,'price_features':features,
            'basis':[current['basis'],change['basis_change'],change['relative_return'],current['index_premium']],
            'funding':rate,'funding_event_ms':b,'funding_interval_ms':intervals[0],
            'oi':quantity,'oi_event_ms':oi['time'],'source_hash':digest(receipts),
            'prediction_issued':False,'economic_eligible':False}


def panel(plan,observations,*,quote,as_of_ms):
    expected=make_plan(now_ms=plan['registered_ms'],p0_hash=plan['p0_hash'])
    if {k:v for k,v in plan.items() if k!='result_hash'}!=expected or quote not in plan['quotes'] or type(as_of_ms) is not int:
        raise ValueError('frozen information plan changed')
    rows={}
    for row in observations:
        slot=row['slot_ms']
        if (type(slot) is not int or slot%HOUR or slot in rows or row['quote']!=quote
                or not plan['warmup_ms']<=slot<=plan['end_slot_ms']
                or not slot<=row['available_ms']<=row['issued_ms']<slot+300000
                or row['issued_ms']>as_of_ms or row['entry_ms']!=slot+300000 or row['exit_ms']!=slot+4*HOUR+300000):
            raise ValueError('unaligned, duplicate or noncausal information observation')
        if len(row['price_features'])!=5 or len(row['basis'])!=4 or not np.isfinite(row['price_features']+row['basis']).all():
            raise ValueError('invalid frozen features')
        rows[slot]=row
    times=[];x=[];basis=[];information=[]
    for slot in range(plan['first_slot_ms'],plan['end_slot_ms']+HOUR,HOUR):
        if slot not in rows or slot-HOUR not in rows:continue
        r=rows[slot];prior=rows[slot-HOUR]
        if not prior['oi_event_ms']<r['oi_event_ms']:raise ValueError('ordered observed OI required')
        delta=r['oi']/prior['oi']-1;flow=r['price_features'][4];funding=r['funding']*4*HOUR/r['funding_interval_ms']
        times.append(slot);x.append(r['price_features'])
        basis.append([r['price_features'][0],flow,*r['basis']])
        information.append([r['price_features'][0],flow,funding,delta,funding*delta,flow*delta])
    missing=[slot for slot in range(plan['warmup_ms'],min(plan['end_slot_ms'],(as_of_ms-300000)//HOUR*HOUR)+HOUR,HOUR) if slot not in rows]
    status=('STOP_INCOMPLETE_CAUSAL_WINDOW' if missing else 'READY_FOR_REGISTERED_RESEARCH') if as_of_ms>=plan['mature_ms'] else 'COLLECTING'
    return {'status':status,'quote':quote,'ready_rows':len(times),'required_rows':504,'missing_slots':missing,
            'times':times,'price_features':x,'basis_features':basis,'information_features':information,
            'prediction_evidence':'NONE_FEATURE_COLLECTION_ONLY','economic_eligible':False}
