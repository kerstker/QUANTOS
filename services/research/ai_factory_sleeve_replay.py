"""Independent cost-aware policies, then one net position in the canonical ledger."""
from collections import Counter
from decimal import Decimal as D
import math
import numpy as np
from services.research.ai_factory import HOUR
from services.research.ai_factory_decision import decide
from services.research.ai_factory_sleeves import SleeveBook,CONSTRUCTION
from services.research.perpetual_alpha import replay

def replay_sleeves(market,specs,quarters,t,p,*,variance_by_slot=None,fee_bps=4,slip_bps=5,window=None):
    book=SleeveBook([s['id'] for s in specs],quarters)
    times=np.asarray(t);predictions=np.asarray(p,dtype=float)
    if (times.ndim!=1 or not 2<=len(times)<=8192 or not np.issubdtype(times.dtype,np.integer)
            or (np.diff(times)<=0).any() or predictions.shape!=(len(specs),len(times))
            or not np.isfinite(predictions).all()):raise ValueError('bounded aligned sleeve predictions required')
    if any(s['threshold_bps'] not in (20,30) for s in specs):raise ValueError('registered sleeve confidence haircut required')
    if (type(fee_bps) not in (int,float) or type(slip_bps) not in (int,float)
            or not math.isfinite(fee_bps) or fee_bps<=0 or slip_bps not in (2,5,10)):
        raise ValueError('known shared execution costs required')
    day=24*HOUR
    start=int((times[0]+day-1)//day*day+300000);end=int(times[-1]//day*day+300000)
    if window is not None:
        start,end=window
        if start<int(times[0]+300000) or end>int(times[-1]+HOUR+300000):raise ValueError('sleeve window outside predictions')
    if end<=start:raise ValueError('insufficient whole-day sleeve window')
    predictions_by_time={int(at)+300000:predictions[:,i] for i,at in enumerate(times)}
    fund_times=np.asarray([r['time'] for r in market['funding']])
    fund_rates=np.asarray([float(r['rate']) for r in market['funding']])
    decisions=[];executions=[];reason_counts=Counter()

    def observe(at,event):
        before=dict(book.positions);book.execution(event)
        executions.append({'time':at,**dict(event),'positions':book.report()['positions'],
            'internal_reallocation':event['status']=='NO_NET_CHANGE' and before!=book.positions})

    def choose(at,context):
        equity=D(context['equity']);mark=D(context['mark'])
        if sum(book.positions.values(),D(0))!=D(context['quantity']):raise ValueError('sleeve/ledger state mismatch')
        observed=predictions_by_time.get(at)
        if observed is None:raise ValueError('sleeve prediction gap')
        hi=int(np.searchsorted(fund_times,at,side='left'));lo=max(0,hi-9)
        if hi-lo<3:raise ValueError('insufficient settled funding history')
        funding=float(fund_rates[lo:hi].mean())*.5
        variance=(variance_by_slot or {}).get(at-300000,.0004)
        weights=[];choices=[];all_hold=True
        for spec,q,raw in zip(specs,quarters,observed):
            budget=D(q)/4;owned=book.positions[spec['id']]
            if q==0 or equity<=0 or context['halted']:
                weights.append(D(0));choices.append({'component_id':spec['id'],'reason':'DISABLED_OR_RISK_EXIT','target':0.})
                if owned:all_hold=False
                continue
            current=float(owned*mark/(equity*budget))
            adjusted=float(np.sign(raw)*max(0,abs(raw)-spec['threshold_bps']/20000))
            choice=decide(prediction=adjusted,variance=variance,current=current,
                fee_bps=fee_bps,slip_bps=slip_bps,funding=funding)
            desired=owned*mark/equity if choice['target'] is None else D(str(choice['target']))*budget
            cap=budget/4;bounded=min(cap,max(-cap,desired))
            if bounded!=desired:choice={**choice,'reason':'FROZEN_SLEEVE_BUDGET_REDUCTION'}
            # A flat component choosing zero is also unchanged. It must not
            # force a held position through weight -> float -> lot conversion.
            unchanged=(choice['target'] is None and bounded==desired) or (bounded==0 and owned==0)
            all_hold=all_hold and unchanged
            weights.append(bounded);choices.append({'component_id':spec['id'],**choice,'raw_prediction':float(raw)})
            reason_counts[choice['reason']]+=1
        target=book.propose(weights)
        decisions.append({'time':at,**book.report(),'choices':choices,'target':None if all_hold else float(target)})
        return None if all_hold else float(target)

    result=replay(**{**market,'start':start,'end':end},decision=lambda *a:None,context_decision=choose,
        execution_observer=observe,target_policy='SLEEVE_NET_V1',slip_bps=D(str(slip_bps)),
        taker_fee_bps=D(str(fee_bps)),stop=True,decision_interval_ms=HOUR)
    result['sleeve_execution']={'construction':CONSTRUCTION,'ending':book.report(),
        'decisions':decisions,'execution':executions,'authoritative':False,'economic_eligible':False,
        'limitations':['ONE_WAY_NET_ACCOUNT_NOT_HEDGE_MODE','OWNERSHIP_NOT_SEPARATE_PNL_LEDGER',
            'SIMULATED_EXECUTION_NOT_REAL_VENUE_RECEIPTS']}
    return result
