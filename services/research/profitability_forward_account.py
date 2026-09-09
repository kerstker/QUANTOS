"""Preregistered forward forecast replay on the unique ledger, never venue PnL."""
import re
import math
from decimal import Decimal
from services.research.ai_factory import HOUR
from services.research.ai_factory_store import digest
from services.research.profitability_forward import forward_summary
from services.research.profitability_attribution import attribute_replay
from services.research.perpetual_alpha import replay

DAY=24*HOUR
PORTFOLIO_POLICY='COST_AWARE_PORTFOLIO_AND_INNER_BASELINE'


def make_plan(*,now_ms,model_hash,policy='IMMUTABLE_FORECAST_TARGET_WEIGHT'):
    if type(now_ms) is not int or now_ms<0 or not isinstance(model_hash,str) or not re.fullmatch('[0-9a-f]{64}',model_hash):
        raise ValueError('valid clock and frozen model required')
    start=(now_ms//DAY+1)*DAY+300000
    if policy not in ('IMMUTABLE_FORECAST_TARGET_WEIGHT',PORTFOLIO_POLICY):raise ValueError('unknown forward account policy')
    return {'schema':'QUANTOS_FORWARD_ACCOUNT_PLAN_V1','created_at_ms':now_ms,
            'start_ms':start,'end_ms':start+14*DAY,'model_hash':model_hash,
            'policy':policy,'baseline':'INNER_SINGLE_AND_CASH_SAME_WINDOW' if policy==PORTFOLIO_POLICY else 'CASH_SAME_WINDOW',
            'initial_equity':'1500','cost_cases':[[4,5],[4,10],[8,5]],
            'missing_prediction_policy':'BLOCK_ACCOUNT_REPLAY_NO_BACKFILL',
            'economic_eligible':False,'actual_venue_costs':None}


def _verified(document):
    content={k:v for k,v in document.items() if k!='result_hash'}
    if document.get('result_hash')!=digest(content):raise ValueError('immutable receipt identity mismatch')
    return content


def validate_window(plan,forecasts,*,as_of_ms):
    plain={k:v for k,v in plan.items() if k!='result_hash'}
    if 'result_hash' in plan:_verified(plan)
    if plain!=make_plan(now_ms=plain['created_at_ms'],model_hash=plain['model_hash'],policy=plain['policy']):
        raise ValueError('forward registration was altered')
    if type(as_of_ms) is not int or as_of_ms<plan['created_at_ms'] or len(forecasts)>4096:
        raise ValueError('bounded forward window required')
    days=max(0,min(14,(as_of_ms-3*HOUR-plan['start_ms'])//DAY))
    end=plan['start_ms']+max(1,days)*DAY
    expected=set(range(plan['start_ms']-300000,end-300000,HOUR));selected=[];seen=set()
    for row in forecasts:
        _verified(row);slot=row['slot_ms']
        if slot not in expected:continue
        if slot in seen or row['model_hash']!=plan['model_hash']:raise ValueError('duplicate or changed forecast model')
        compatible=row['target_weight'] in (-.25,0,.25)
        if plan['policy']==PORTFOLIO_POLICY:
            compatible=(row['target_weight'] is None and row.get('decision_policy')=='DISCRETE_COST_RISK_V1'
                and type(row.get('baseline_prediction')) in (float,int) and math.isfinite(row['baseline_prediction'])
                and len(row.get('features',[]))==5
                and all(type(v) in (float,int) and math.isfinite(v) for v in row['features']) and row['features'][3]>=0)
        if (row['entry_ms']!=slot+300000 or row['exit_ms']!=slot+300000+4*HOUR
                or row['issued_at_ms']<plan['created_at_ms'] or not compatible
                or not re.fullmatch('[0-9a-f]{64}',row['input_hash'])):
            raise ValueError('forecast not compatible with registered policy')
        seen.add(slot);selected.append(row)
    forward_summary(selected,[],as_of_ms=as_of_ms)
    # Include the final prediction's four-hour horizon, not only the accounting close.
    mature_at=end+3*HOUR
    status='WAITING_MATURITY' if as_of_ms<mature_at else 'MISSING_FORECASTS' if seen!=expected else 'READY'
    return {'status':status,'missing_slots':sorted(s for s in expected-seen if s+300000<=as_of_ms),
            'pending_slots':sorted(s for s in expected-seen if s+300000>as_of_ms),'mature_at_ms':mature_at,
            'forecast_count':len(selected),'window_start_ms':plan['start_ms'],'window_end_ms':end,
            'mature_days':days,'planned_days':14,
            'economic_eligible':False}


def replay_window(plan,forecasts,market,*,as_of_ms):
    readiness=validate_window(plan,forecasts,as_of_ms=as_of_ms)
    if readiness['status']!='READY':raise ValueError('complete mature forward window required')
    end=readiness['window_end_ms']
    weights={r['entry_ms']:r['target_weight'] for r in forecasts if plan['start_ms']<=r['entry_ms']<end}
    if market['start']>plan['start_ms'] or market['end']<end:raise ValueError('market coverage missing')
    cases=[]
    by_time={r['entry_ms']:r for r in forecasts if r['entry_ms'] in weights}
    names=('CASH','FROZEN_MODEL','SINGLE_BASELINE') if plan['policy']==PORTFOLIO_POLICY else ('CASH','FROZEN_MODEL')
    for name in names:
        for fee,slip in plan['cost_cases']:
            context_decision=None
            if plan['policy']==PORTFOLIO_POLICY:
                context_decision=portfolio_decision(by_time,market,name,fee,slip)
            receipt=replay(**{**market,'start':plan['start_ms'],'end':end},
                           decision=lambda at,held:weights[at] if name=='FROZEN_MODEL' else 0.,
                           context_decision=context_decision,
                           decision_interval_ms=HOUR,slip_bps=Decimal(slip),taker_fee_bps=Decimal(fee),stop=True)
            cases.append({'model':name,'fee_bps':fee,'slippage_bps':slip,
                          'attribution':attribute_replay(receipt),'receipt':receipt})
    return {'stage':'P6','status':'FORWARD_FORECAST_ACCOUNT_TECHNICAL_ONLY','cases':cases,
            'plan_hash':digest(plan),'forecast_hashes':[r['result_hash'] for r in forecasts if r['entry_ms'] in weights],
            'window':readiness,'economic_eligible':False,'actual_venue_costs':None,
            'policy_selected_before_window':True,'forward_orders_executed':False,
            'limitations':['SIMULATED_EXECUTION_OF_PREISSUED_FORECASTS','UNCALIBRATED_COSTS_AND_RULES',
                          'NOT_REALIZED_VENUE_ACCOUNT_PNL','SHORT_WINDOWS_ARE_NOT_STABILITY_EVIDENCE']}


def portfolio_decision(by_time,market,name,fee,slip):
    """Same frozen utility as historical portfolio selection, using preissued inputs."""
    from services.research.ai_factory_decision import decide
    import numpy as np
    def decision(at,context):
        if name=='CASH':return 0.
        row=by_time[at]
        settled=[float(r['rate']) for r in market['funding'] if r['time']<at][-9:]
        if len(settled)<3:raise ValueError('insufficient settled funding history')
        equity=float(context['equity'])
        if equity<=0:return 0.
        prediction=row['baseline_prediction'] if name=='SINGLE_BASELINE' else row['prediction']
        return decide(prediction=prediction,variance=row['features'][3]**2*4,
            current=float(context['quantity'])*float(context['mark'])/equity,
            fee_bps=fee,slip_bps=slip,funding=float(np.asarray(settled).mean())*.5)['target']
    return decision
