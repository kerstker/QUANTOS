"""Registered inner portfolio search and frozen whole-account outer comparisons."""
from pathlib import Path
import threading
import time
import numpy as np

from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import HOUR,fit_predict,partition
from services.research.ai_factory_store import FactoryStore,digest
from services.research.ai_factory_portfolio import SCHEMA,candidate_grid,combine_predictions,select_inner
from services.research.trial_ledger import TrialLedger
from tools.btc_factory_research import verified,seal,_event,_trial
from tools.profitability_study import write_new,frozen_inputs
from tools.perpetual_replay_research import source_identity
from tools.ai_quant_factory import evaluate_market
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.cache_io import atomic_write_json
from tools.ai_factory_portfolio_verify import verify_portfolio

_LOCK=threading.Lock()
COSTS=((4,5),(4,10),(8,5))


def _case(output,plan,scope,spec,compute):
    case={'scope':scope,'spec':spec};identity=digest({'plan':plan,'case':case})
    path=output/'cases'/f'{identity}.json';path.parent.mkdir(exist_ok=True)
    record={'trial_id':identity,'alpha_id':digest(spec),'family_id':digest([SCHEMA,scope]),
            'exact_definition_id':identity,'validation_policy_digest':digest(plan)}
    with TrialLedger(str(output/'trials.db')) as ledger:
        ledger.verify();previous=_trial(ledger,identity)
        if path.exists():
            value=verified(path)
            if value['trial_id']!=identity or value['case']!=case or previous is None:
                raise ValueError('portfolio trial identity mismatch')
            if previous.status=='RUNNING':_event(ledger,record,value['status'])
            elif previous.status!=value['status']:raise ValueError('portfolio trial status mismatch')
            return value
        if previous is not None:
            if previous.status not in ('PLANNED','RUNNING'):raise ValueError('portfolio terminal receipt missing')
            if previous.status=='PLANNED':_event(ledger,record,'RUNNING')
            _event(ledger,record,'FAILED')
            value={'case':case,'trial_id':identity,'status':'FAILED','reason':'INTERRUPTED'}
        else:
            _event(ledger,record,'PLANNED');_event(ledger,record,'RUNNING');started=time.perf_counter()
            try:
                metrics=compute()
                # Validate selection metrics even on outer cases; no NaN success receipts.
                select_inner([{'status':'PASSED','spec':{'quarters':[0],'id':0},'metrics':metrics}])
                value={'case':case,'trial_id':identity,'status':'PASSED','metrics':metrics}
            except Exception as exc:
                value={'case':case,'trial_id':identity,'status':'FAILED','reason':type(exc).__name__}
            value['elapsed_ms']=round((time.perf_counter()-started)*1000)
            write_new(path,value);_event(ledger,record,value['status']);return verified(path)
        write_new(path,value);return verified(path)


def run_study(output,identity,components,panel,replay,*,fit=fit_predict,split=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    t,x,y=panel;t=np.asarray(t);x=np.asarray(x);y=np.asarray(y)
    train,valid,test=partition(t,t+4*HOUR+300000) if split is None else split
    train,valid,test=(np.asarray(rows) for rows in (train,valid,test))
    for rows in (train,valid,test):
        if (rows.ndim!=1 or len(rows)<32 or not np.issubdtype(rows.dtype,np.integer)
                or rows.min()<0 or rows.max()>=len(t) or (np.diff(rows)<=0).any()):
            raise ValueError('invalid portfolio partitions')
    if (t[train[-1]]+4*HOUR+300000>=t[valid[0]] or t[valid[-1]]+4*HOUR+300000>=t[test[0]]):
        raise ValueError('portfolio partition label leakage')
    train=train[-2048:]
    grid=candidate_grid([s['id'] for s in components])
    plan={'schema':SCHEMA,'identity':identity,'components':components,'candidates':grid,
          'panel_hash':digest([t.tolist(),x.tolist(),y.tolist()]),'costs':[list(c) for c in COSTS],
          'selection':'INNER_NET_PNL_MINUS_1500_DRAWDOWN_CASH_AND_SIMPLE_TIEBREAK',
          'inner_candidate_budget':len(grid),'outer_case_budget':6,'initial_equity':'1500',
          'scope':'USED_HISTORY_TECHNICAL_RESEARCH','outer_feedback_allowed':False,
          'partitions_hash':digest([train.tolist(),valid.tolist(),test.tolist()]),'economic_eligible':False}
    registration=output/'plan.json'
    if registration.exists():
        if verified(registration)!=seal(plan):raise ValueError('portfolio research identity changed')
    else:write_new(registration,plan)
    if (output/'result.json').exists():
        verify_portfolio(output)
        return verified(output/'result.json')
    def progress(stage,count):
        atomic_write_json(str(output/'status.json'),seal({'schema':SCHEMA,'status':'RUNNING','stage':stage,
            'plan_hash':digest(plan),'inner_trials_attempted':count,'observed_at_ms':time.time_ns()//1000000,
            'authoritative':False,'economic_eligible':False}))
    progress('INNER_FIT',0)
    timings={}
    def predictions(scope,fit_rows,target_rows):
        path=output/f'{scope}-predictions.json';started=time.perf_counter()
        if path.exists():
            cached=verified(path)
            if cached['plan_hash']!=digest(plan):raise ValueError('prediction cache identity changed')
            matrix=np.asarray(cached['predictions'])
        else:
            matrix=np.asarray([fit(s,x[fit_rows],y[fit_rows],x[target_rows]) for s in components])
            if matrix.shape!=(len(components),len(target_rows)) or not np.isfinite(matrix).all():
                raise ValueError('invalid component predictions')
            write_new(path,{'plan_hash':digest(plan),'predictions':matrix.tolist()})
        if matrix.shape!=(len(components),len(target_rows)) or not np.isfinite(matrix).all():
            raise ValueError('invalid cached component predictions')
        timings[scope+'_prediction_ms']=round((time.perf_counter()-started)*1000)
        return matrix
    inner_p=predictions('inner',train,valid)
    trials=[]
    for spec in grid:
        value=_case(output,plan,'INNER',spec,lambda s=spec:replay(t[valid],combine_predictions(components,inner_p,s['quarters']),4,5))
        trials.append({**value,'spec':spec})
        progress('INNER_SEARCH',len(trials))
    best=select_inner(trials)
    singles=[r for r in trials if 4 in r['spec']['quarters']]
    baseline=select_inner(singles)
    selected={'plan_hash':digest(plan),'selected':best['spec'],'baseline':baseline['spec'],
              'selection_score':best['selection_score'],'baseline_score':baseline['selection_score'],
              'selected_trial_id':best['trial_id'],'baseline_trial_id':baseline['trial_id'],
              'scope':'INNER_ONLY','economic_eligible':False}
    selection=output/'selection.json'
    if selection.exists():
        if verified(selection)!=seal(selected):raise ValueError('frozen portfolio selection changed')
    else:write_new(selection,selected)
    # No outer fitting or replay occurs before the full selection has been frozen.
    progress('OUTER_COMPARISON',len(trials))
    fit_rows=np.r_[train,valid][-2048:];outer_p=predictions('outer',fit_rows,test)
    artifact={'schema':SCHEMA,'plan_hash':digest(plan),'components':components,'quarters':best['spec']['quarters'],
              'baseline_quarters':baseline['spec']['quarters'],
              'training_x':x[fit_rows].tolist(),'training_y':y[fit_rows].tolist(),
              'training_last_label_ms':int((t[fit_rows]+4*HOUR+300000).max()),
              'decision_policy':'DISCRETE_COST_RISK_V1','replay_threshold_bps':0,
              'authoritative':False,'live_eligible':False,'economic_eligible':False}
    model=output/'model.json'
    if model.exists():
        if verified(model)!=seal(artifact):raise ValueError('frozen portfolio model changed')
    else:write_new(model,artifact)
    outer=[]
    for name,spec in (('portfolio',best['spec']),('single_baseline',baseline['spec'])):
        prediction=combine_predictions(components,outer_p,spec['quarters'])
        for fee,slip in COSTS:
            case={'name':name,'candidate':spec,'fee_bps':fee,'slippage_bps':slip}
            value=_case(output,plan,'OUTER',case,lambda p=prediction,f=fee,s=slip:replay(t[test],p,f,s))
            outer.append({**value,'name':name,'fee_bps':fee,'slippage_bps':slip})
    passed=all(r['status']=='PASSED' for r in outer)
    comparisons=[]
    if passed:
        for i,(fee,slip) in enumerate(COSTS):
            a=outer[i]['metrics'];b=outer[i+3]['metrics']
            comparisons.append({'fee_bps':fee,'slippage_bps':slip,'net_pnl_delta':a['net_pnl']-b['net_pnl'],
                'portfolio_net_pnl':a['net_pnl'],'baseline_net_pnl':b['net_pnl'],
                'portfolio_drawdown':a['drawdown'],'baseline_drawdown':b['drawdown'],
                'portfolio_fills':a['fills'],'baseline_fills':b['fills']})
    decision='STOP_UNRESOLVED_COMPARISON'
    if passed:
        if not any(r['metrics']['fills'] for r in outer[:3]):decision='STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE'
        elif all(c['portfolio_net_pnl']>0 and c['net_pnl_delta']>0 for c in comparisons):decision='NATURAL_FORWARD_REQUIRED'
        else:decision='STOP_NO_STABLE_NET_INCREMENT'
    result={'schema':SCHEMA,'status':'COMPLETED_TECHNICAL_ONLY','decision':decision,'plan_hash':digest(plan),
            'selected':best['spec'],'baseline':baseline['spec'],'components':components,
            'inner_trials':trials,'inner_trials_attempted':len(trials),'outer_cases':outer,'comparisons':comparisons,
            'model_hash':verified(model)['result_hash'],'timings':timings,'quote':'USDC',
            'authoritative':False,'economic_eligible':False,'live_eligible':False,
            'limitations':['USED_HISTORY_NOT_INDEPENDENT_OOS','FORECAST_BLEND_NOT_SEPARATE_TRADING_SLEEVES',
                'NO_REAL_VENUE_COST_CALIBRATION','NATURAL_FORWARD_NOT_ESTABLISHED']}
    write_new(output/'result.json',result);verify_portfolio(output)
    return verified(output/'result.json')


def run_portfolio(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('bounded portfolio experiment required')
    root=runtime_state_root()/'data/cache';experiment=root/'ai_factory'/str(params['attempt']);output=experiment/'portfolio'
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):raise ValueError('unsafe portfolio path')
    output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'worker.lock',process_lock=_LOCK) as acquired:
        if not acquired:raise ValueError('portfolio worker busy')
        state=FactoryStore.snapshot(root/'ai_factory/factory.db',params['attempt'])
        if state['selected']['state']!='COMPLETED':raise ValueError('completed research required')
        trials=sorted((r for r in state['trials'] if r['status']=='PASSED'),key=lambda r:(-r['metrics']['score'],r['ordinal']))[:3]
        if not trials:raise ValueError('no usable inner components')
        plan,_,_,t,x,y,market=frozen_inputs(experiment,root/'perpetual_alpha/input')
        variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
        identity={'source':source_identity(),'experiment_plan_hash':plan['result_hash'],
                  'component_trial_hashes':[digest(r) for r in trials],'scope':'INNER_COMPONENTS_ONLY'}
        def replay(times,prediction,fee,slip):
            return evaluate_market(market,{'threshold_bps':0},times,prediction,cost_aware=True,
                variance_by_slot=variance,fee_bps=fee,slip_bps=slip)
        try:return run_study(output,identity,[r['spec'] for r in trials],(t,x,y),replay)
        except Exception as exc:
            atomic_write_json(str(output/'status.json'),seal({'schema':SCHEMA,'status':'ERROR',
                'reason':type(exc).__name__,'observed_at_ms':time.time_ns()//1000000,
                'authoritative':False,'economic_eligible':False}))
            raise
