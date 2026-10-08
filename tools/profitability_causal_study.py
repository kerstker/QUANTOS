"""Evaluate one frozen prospective feature window through the existing V2 ledger."""
from pathlib import Path
from decimal import Decimal
import numpy as np

from services.research.ai_factory_store import digest
from services.research.ai_factory import HOUR
from tools.btc_factory_research import verified
from tools.profitability_forward_market import capture_market
from tools.profitability_forward_account import _market_identity
from tools.profitability_information_study import evaluate_information_panel
from tools.profitability_study import write_new

DAY=86400000


def capture_window(output,*,start,end,quote='USDC',capture=capture_market):
    """Bounded ten-day chunks; duplicate boundaries must agree, never silently replace."""
    if quote not in ('USDC','USDT') or type(start) is not int or type(end) is not int or not 0<end-start<=22*DAY:
        raise ValueError('fixed three-chunk research window required')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    chunks=[]
    for at in range(start,end,10*DAY):
        market,receipts=capture(output/str(at),start=at,end=min(end,at+10*DAY),quote=quote)
        write_new(output/f'{at}.json',{'market':_market_identity(market),'receipts_hash':digest(receipts)})
        chunks.append(market)
    minutes={};funding={};volumes={}
    for market in chunks:
        for at,price,mark in zip(market['times'],market['prices'],market['marks']):
            at=int(at);value=(float(price),float(mark))
            if at in minutes and minutes[at]!=value:raise ValueError('public minute revision across chunks')
            minutes[at]=value
        for r in market['funding']:
            if r['time'] in funding and funding[r['time']]!=r:raise ValueError('public funding revision across chunks')
            funding[r['time']]=r
        for day,value in market['daily_volume'].items():
            if day in volumes and volumes[day]!=value:raise ValueError('public capacity revision across chunks')
            volumes[day]=value
    times=sorted(minutes)
    if times!=list(range(start,end+60000,60000)):raise ValueError('incomplete joint minute window')
    return {'times':np.asarray(times,dtype=np.int64),'prices':np.asarray([minutes[t][0] for t in times]),
            'marks':np.asarray([minutes[t][1] for t in times]),'start':start,'end':end,
            'funding':[funding[t] for t in sorted(funding)],'daily_volume':volumes,'maximum_quantity':Decimal('50'),'quote':quote}


def run_collected_studies(output,plan,collected,*,capture=capture_window):
    """Pre-registered features, post-outcome fitting; never label these preissued forecasts."""
    quote=collected['quote']
    if quote not in ('USDC','USDT') or collected['status']!='READY_FOR_REGISTERED_RESEARCH' or collected['ready_rows']!=504:
        raise ValueError('complete causal single-currency panel required; no currency conversion')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write_new(output/'started.json',{'plan_hash':plan['result_hash'],'panel_hash':digest(collected),'economic_eligible':False})
    try:
        t=np.asarray(collected['times'],dtype=np.int64);x=np.asarray(collected['price_features'])
        market=capture(output/'market',start=int(t[0])+300000,end=int(t[-1])+4*HOUR+300000,quote=quote)
        if market.get('quote','USDC')!=quote:raise ValueError('market settlement currency mismatch')
        write_new(output/'market.json',{'market':_market_identity(market)})
        indices=np.searchsorted(market['times'],np.r_[t+300000,t+4*HOUR+300000])
        if not np.array_equal(market['times'][indices],np.r_[t+300000,t+4*HOUR+300000]):raise ValueError('exact mature labels required')
        prices=market['prices'][indices];y=prices[len(t):]/prices[:len(t)]-1
        write_new(output/'labels.json',{'times':t.tolist(),'labels':y.tolist(),'panel_hash':digest(collected),
                  'prediction_evidence':'NONE_FEATURE_COLLECTION_ONLY','economic_eligible':False})
        results={}
        for stage,key in [('P3','basis_features'),('P4','information_features')]:
            destination=output/stage;destination.mkdir()
            prior_trials=sum(1 for _ in output.parent.glob('study*/P*/inner-*.started.json'))
            registration={'stage':stage,'created_at_ms':plan['registered_ms'],'p0_hash':plan['p0_hash'],
                          'causal_plan_hash':plan['result_hash'],'quote':quote,'source':verified(output.parent/'source.json')['source'],
                          'prior_input_revision_trials':prior_trials,'total_candidate_budget':plan['total_candidate_budget'],
                          'specs':plan[stage.lower()+'_specs'],
                          'cost_cases':plan['cost_cases'],'model':plan['model'],'threshold_bps':20,
                          'selection':plan['selection'],'history':'PROSPECTIVE_FEATURES_POST_OUTCOME_RESEARCH_NOT_ISSUED_PREDICTIONS',
                          'continuation':'STOP_IF_BASELINE_SELECTED_NO_ACTIVITY_OR_ANY_COST_PAIR_WORSE','economic_eligible':False}
            write_new(destination/'preregistration.json',registration)
            evaluate_information_panel(destination,registration,t,x,y,np.asarray(collected[key]),market,
                                       information_hash=digest(collected),trials=[])
            results[stage]=verified(destination/'result.json')
        result={'status':'COMPLETED_TECHNICAL_ONLY','quote':quote,'results':results,'plan_hash':plan['result_hash'],
                'prediction_evidence':'NONE_FEATURE_COLLECTION_ONLY','economic_eligible':False,'authoritative':False}
        write_new(output/'result.json',result);return result
    except Exception as exc:
        write_new(output/'failure.json',{'reason':type(exc).__name__,'detail':str(exc),'economic_eligible':False})
        raise
