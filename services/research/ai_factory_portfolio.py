"""Finite long-only forecast weights; one downstream cost/risk/position account."""
from itertools import product
import math
import numpy as np

SCHEMA='QUANTOS_FACTORY_PORTFOLIO_V1'
MAX_COMPONENTS=3
MAX_CANDIDATES=16


def candidate_grid(component_ids):
    if (not isinstance(component_ids,list) or not 1<=len(component_ids)<=MAX_COMPONENTS
            or any(type(i) is not int or not 0<=i<72 for i in component_ids)
            or len(set(component_ids))!=len(component_ids)):
        raise ValueError('one to three distinct bounded components required')
    weights=[[0]*len(component_ids)]+[list(w) for w in product(range(5),repeat=len(component_ids)) if sum(w)==4]
    return [{'id':i,'component_ids':list(component_ids),'quarters':w,
             'construction':'HAIRCUT_FORECAST_BLEND_SHARED_ACCOUNT'} for i,w in enumerate(weights)]


def combine_predictions(specs,predictions,quarters):
    p=np.asarray(predictions,dtype=float)
    if (not 1<=len(specs)<=MAX_COMPONENTS or p.ndim!=2 or p.shape[0]!=len(specs) or not p.shape[1]
            or not np.isfinite(p).all() or len(quarters)!=len(specs)
            or any(type(w) is not int or not 0<=w<=4 for w in quarters) or sum(quarters) not in (0,4)):
        raise ValueError('aligned finite predictions and unlevered quarter weights required')
    thresholds=[s['threshold_bps'] for s in specs]
    if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1000 for v in thresholds):
        raise ValueError('invalid component confidence haircut')
    adjusted=np.sign(p)*np.maximum(0,np.abs(p)-np.asarray(thresholds)[:,None]/20000)
    # evaluate_market receives threshold_bps=0: never apply this haircut twice.
    return np.asarray(quarters,dtype=float)@adjusted/4


def select_inner(records):
    if not records or len(records)>MAX_CANDIDATES:raise ValueError('bounded inner trials required')
    usable=[]
    for row in records:
        if row['status']!='PASSED':continue
        metrics=row['metrics'];net=metrics['net_pnl'];drawdown=metrics['drawdown']
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (net,drawdown)) or not 0<=drawdown<=1:
            raise ValueError('invalid net-cost selection metrics')
        usable.append({**row,'selection_score':net-1500*drawdown})
    if not usable:raise ValueError('no completed inner portfolio trials')
    return max(usable,key=lambda r:(r['selection_score'],-sum(w>0 for w in r['spec']['quarters']),-r['spec']['id']))
