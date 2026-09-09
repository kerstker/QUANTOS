"""Bounded, resumable hourly BTC research factory. Technical evidence only."""
from __future__ import annotations
from decimal import Decimal as D
from collections import Counter
from pathlib import Path
import numpy as np
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import HOUR,candidates,candidate_pool,fit_predict,hourly_panel,partition,targets,validate_config
from services.research.ai_factory_store import FactoryStore,digest
from services.research.trial_ledger import TrialLedger
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.btc_factory_research import _event,_trial,seal,verified,snapshot
from tools.cache_io import atomic_write_json
from tools.ai_factory_provider import propose

class DuplicateInnerPrediction(ValueError):
    pass


SCHEMA='QUANTOS_AI_FACTORY_V1'
POLICY={'schema':SCHEMA,'split':[.65,.82],'purge_hours':4,'entry_delay_minutes':5,
        'threshold_role':'PREDICTION_CONFIDENCE_HAIRCUT','decision_policy':'DISCRETE_COST_RISK_V1','seed':17,'max_training_rows':2048,'fee_bps':4,'slippage_bps':5,'stop':True}




def execute(store,attempt,panel,evaluate,identity,output,proposal=propose):
    session=store.get(attempt);config=validate_config(session['config']);dest=Path(output)/str(attempt)
    dest.mkdir(parents=True,exist_ok=True)
    t,x,y=panel
    pool=candidate_pool(config);space={c['id']:c for c in pool}
    bound={'config':config,'policy':POLICY,'inputs':identity,
           'panel_hash':digest([t.tolist(),x.tolist(),y.tolist()])}
    plan=dest/'plan.json'
    if plan.exists():
        if verified(plan)['identity']!=bound:raise ValueError('research identity changed; create a new attempt')
    else:atomic_write_json(str(plan),seal({'identity':bound}))
    if session['state'] in ('COMPLETED','CANCELLED'):return session
    if (dest/'result.json').exists():
        result=verified(dest/'result.json');model=verified(dest/'model.json')
        if model['identity_hash']!=digest(bound) or result['model_hash']!=model['result_hash']:
            raise ValueError('final model identity mismatch')
        store.update(attempt,'COMPLETED',{k:v for k,v in result.items() if k!='result_hash'})
        return store.get(attempt)
    if (dest/'outer-started.json').exists():
        store.update(attempt,'FAILED',{'reason':'OUTER_INTERRUPTED_CREATE_NEW_ATTEMPT','authoritative':False})
        return store.get(attempt)
    train,valid,test=partition(t,t+4*HOUR+300000);train=train[-2048:]
    with TrialLedger(str(Path(output)/'trials.db')) as ledger:
        ledger.verify()
        for ordinal in range(config['budget']):
            existing=store.trials(attempt)
            if ordinal<len(existing):
                if existing[ordinal]['ordinal']!=ordinal:raise ValueError('trial receipt ordinal gap')
                current=ledger.load(existing[ordinal]['trial_id'])
                if current.status=='RUNNING':
                    record={key:getattr(current,key) for key in ('trial_id','alpha_id','family_id','exact_definition_id','validation_policy_digest')}
                    _event(ledger,record,existing[ordinal]['status'])
                elif current.status!=existing[ordinal]['status']:raise ValueError('trial ledger receipt conflict')
                continue
            command=store.get(attempt)['command']
            if command in ('pause','cancel'):
                store.update(attempt,'PAUSED' if command=='pause' else 'CANCELLED');return store.get(attempt)
            store.update(attempt,'RUNNING')
            definition=digest({'bound':bound,'ordinal':ordinal})
            record=dict(trial_id=definition,alpha_id=digest(config['objective']),family_id=digest(SCHEMA),
                        exact_definition_id=definition,validation_policy_digest=digest(POLICY))
            previous=_trial(ledger,definition)
            if previous is not None:
                if previous.status not in ('PLANNED','RUNNING'):
                    raise ValueError('factory terminal receipt missing')
                if previous.status=='PLANNED':_event(ledger,record,'RUNNING')
                if previous.status in ('RUNNING','PLANNED'):_event(ledger,record,'FAILED')
                store.record_trial(attempt,ordinal,{'ordinal':ordinal,'status':'FAILED','candidate_id':None,
                    'reason':'INTERRUPTED','trial_id':definition});continue
            _event(ledger,record,'PLANNED');_event(ledger,record,'RUNNING')
            picked=None;prediction_hash=None;stage='PROPOSAL'
            try:
                remaining=[c['id'] for c in pool if c['id'] not in [r['candidate_id'] for r in existing]]
                receipt={'candidate_id':remaining[0],'model':'deterministic_baseline'}
                if config['mode']=='ai':
                    history=[{'candidate_id':r['candidate_id'],'score':r.get('metrics',{}).get('score'),'status':r['status']}
                             for r in existing if r['candidate_id'] is not None]
                    receipt=proposal(config['objective'],remaining,config.get('search_plan',{}).get('memory',[])+history,**({'search_plan':config['search_plan'],'source_context':identity.get('research_sources',{}).get('records',[])} if 'search_plan' in config else {}))
                proposed=receipt['candidate_id']
                if type(proposed) is not int or proposed not in remaining:raise ValueError('invalid candidate proposal')
                picked=proposed
                stage='MODEL_FIT'
                spec=space[picked];p=fit_predict(spec,x[train],y[train],x[valid])
                if 'search_plan' in config:
                    prediction_hash=digest([t[valid].tolist(),p.tolist(),spec['threshold_bps']])
                    if any(r.get('prediction_hash')==prediction_hash for r in existing):raise DuplicateInnerPrediction('duplicate inner forecast')
                stage='LEDGER_REPLAY'
                stats=evaluate(spec,t[valid],p)
                if not np.isfinite(stats['score']):raise ValueError('nonfinite trial score')
                result={'ordinal':ordinal,'candidate_id':picked,'spec':spec,'status':'PASSED',
                        'metrics':stats,'proposal':receipt,'rmse':float(np.sqrt(np.mean((p-y[valid])**2))),
                        'trial_id':definition}
            except Exception as exc:
                # Exception messages can include remote payloads or paths; retain a bounded error code.
                result={'ordinal':ordinal,'candidate_id':picked,'status':'FAILED','reason':stage+'_'+type(exc).__name__,'trial_id':definition}
                if stage!='PROPOSAL':result.update(proposal=receipt,spec=space[picked])
            if prediction_hash is not None:result['prediction_hash']=prediction_hash
            result['hypothesis']={'hypothesis_id':definition,'family_id':record['family_id'],
                'dataset_hash':bound['inputs'].get('input_hash',bound['panel_hash']),
                'feature_spec_hash':digest(space[picked] if 'search_plan' in config else space[picked]['features']) if picked is not None else None,
                'target_horizon_ms':4*HOUR,'training_label_cutoff_ms':int((t[train]+4*HOUR+300000).max()),
                'evaluation_scope':'INNER_ONLY','evaluation_cutoff_ms':int((t[valid]+4*HOUR+300000).max()),
                'split_plan_hash':digest([train.tolist(),valid.tolist(),test.tolist()]),
                'cost_profile_hash':digest({'fee_bps':4,'slippage_bps':5,'decision_policy':POLICY['decision_policy']}),
                'parent_trial_ids':[r['trial_id'] for r in existing],
                'source_ids':config.get('search_plan',{}).get('source_ids',['buildalpha-llm','aqr-cost']),
                'source_role':'RESEARCH_METHOD_NOT_SIGNAL_PROFITABILITY_EVIDENCE'}
            store.record_trial(attempt,ordinal,result)
            _event(ledger,record,result['status'])
        command=store.get(attempt)['command']
        if command in ('pause','cancel'):
            store.update(attempt,'PAUSED' if command=='pause' else 'CANCELLED');return store.get(attempt)
        successful=[r for r in store.trials(attempt) if r['status']=='PASSED']
        if not successful:
            store.update(attempt,'FAILED',{'reason':'NO_VALID_EXPERIMENT','authoritative':False});return store.get(attempt)
        best=max(successful,key=lambda r:(r['metrics']['score'],-r['ordinal']))
        # Persist before reading held-out labels. Restart cannot repeat an interrupted outer evaluation.
        atomic_write_json(str(dest/'outer-started.json'),seal({'selected':best['spec'],'identity_hash':digest(bound)}))
        store.update(attempt,'OUTER_VALIDATION')
        fit=np.r_[train,valid][-2048:];spec=best['spec']
        p=fit_predict(spec,x[fit],y[fit],x[test]);outer=evaluate(spec,t[test],p)
        artifact=seal({'schema':SCHEMA,'identity_hash':digest(bound),'spec':spec,
            'training_x':x[fit].tolist(),'training_y':y[fit].tolist(),'training_last_label_ms':int((t[fit]+4*HOUR+300000).max()),
            'decision_policy':POLICY['decision_policy'],'authoritative':False,'live_eligible':False})
        atomic_write_json(str(dest/'model.json'),artifact)
        overlaps=[]
        for row in FactoryStore.snapshot(Path(output)/'factory.db')['sessions']:
            prior=row['result'].get('outer',{})
            if row['attempt']!=attempt and prior.get('start_ms') is not None and outer.get('start_ms') is not None:
                if prior['start_ms']<outer['end_ms'] and outer['start_ms']<prior['end_ms']:overlaps.append(row['attempt'])
        result={'outer_overlap_attempts':overlaps,'outer_economic_independence_established':False,
                'selected':spec,'inner':best['metrics'],'outer':outer,'model_hash':artifact['result_hash'],
                'outer_rmse':float(np.sqrt(np.mean((p-y[test])**2))), 'train_rows':len(train),'validation_rows':len(valid),
                'test_rows':len(test),'authoritative':False,'live_eligible':False,'schema':SCHEMA,
                'limitations':['TECHNICAL_VALIDATION_ONLY','HISTORICAL_RECEIVE_TIME_UNKNOWN','CURRENT_VENUE_RULE_SCENARIO',
                               'OUTER_NOT_AVAILABLE_TO_PROPOSAL_MODEL','NATURAL_FORWARD_NOT_ESTABLISHED']}
        atomic_write_json(str(dest/'result.json'),seal(result));store.update(attempt,'COMPLETED',result)
        return store.get(attempt)


def evaluate_market(market,spec,t,p,*,cost_aware=False,variance_by_slot=None,slip_bps=5,fee_bps=4,window=None,position_policy=None,timing_wait=False):
    from services.research.perpetual_alpha import replay
    from tools.perpetual_alpha_research import metrics
    if position_policy is not None:
        from services.research.profitability_decision import POLICY_IDS
        if not cost_aware or position_policy not in POLICY_IDS:raise ValueError('invalid registered policy')
    if type(timing_wait) is not bool or timing_wait and not cost_aware:raise ValueError('invalid timing policy')
    timing=None;timing_trace=[]
    if timing_wait:
        from services.research.profitability_timing import TimingGate
        timing=TimingGate(wait_ms=HOUR)
    day=24*HOUR
    weights=dict(zip((t+300000).tolist(),targets(p,spec['threshold_bps'])))
    # The canonical ledger retains its whole-day reconciliation contract.
    start=int((t[0]+day-1)//day*day+300000)
    end=int(t[-1]//day*day+300000)
    if window is not None:
        start,end=window
        if start<int(t[0]+300000) or end>int(t[-1]+HOUR+300000):raise ValueError('comparison window outside prediction coverage')
    if end<=start:raise ValueError('insufficient whole-day evaluation window')
    trace=[];reason_counts={}
    context_decision=None
    if cost_aware:
        from services.research.ai_factory_decision import decide
        predictions=dict(zip((t+300000).tolist(),p.tolist()))
        fund_times=np.asarray([r['time'] for r in market['funding']])
        fund_rates=np.asarray([float(r['rate']) for r in market['funding']])
        def context_decision(at,context):
            mu=predictions.get(at)
            if mu is None:return 0.
            hi=int(np.searchsorted(fund_times,at,side='left'));lo=max(0,hi-9)
            if hi-lo<3:raise ValueError('insufficient settled funding history')
            funding=float(fund_rates[lo:hi].mean())*.5
            variance=(variance_by_slot or {}).get(at-300000,.0004)
            equity=float(context['equity'])
            if equity<=0:return 0.
            current=float(context['quantity'])*float(context['mark'])/equity
            adjusted_mu=float(np.sign(mu)*max(0,abs(mu)-spec['threshold_bps']/20000))
            inputs=dict(prediction=adjusted_mu,variance=variance,current=current,
                        fee_bps=fee_bps,slip_bps=slip_bps,funding=funding)
            if position_policy is None:
                decision=decide(**inputs)
            else:
                from services.research.profitability_decision import decide_registered
                decision=decide_registered(position_policy,**inputs)
            decision.update(raw_prediction=float(mu),confidence_haircut_bps=spec['threshold_bps']/2)
            reason_counts[decision['reason']]=reason_counts.get(decision['reason'],0)+1
            if len(trace)<128:trace.append({'time':at,**decision})
            if timing is not None:
                # Use observed minute OPEN prices strictly before the decision.
                # No future extrema, current close, or future execution quote enters timing.
                right=int(np.searchsorted(market['times'],at,side='left'))-1
                left=int(np.searchsorted(market['times'],at-HOUR,side='right'))-1
                if min(left,right)<0:raise ValueError('timing signal history unavailable')
                short=float(market['prices'][right]/market['prices'][left]-1)
                timed=timing.decide(at=at,current=current,target=decision['target'],short_signal=short)
                timing_trace.append(timed)
                return timed['target']
            return decision['target']
    result=replay(**{**market,'start':start,'end':end},decision=lambda at,held:weights.get(at,0.),
                  context_decision=context_decision,slip_bps=D(slip_bps),taker_fee_bps=D(fee_bps),
                  stop=True,decision_interval_ms=HOUR)
    stats=metrics(result)
    from services.research.ai_factory_execution_feedback import execution_feedback
    return {'execution_feedback':execution_feedback(result),'score':stats['net_pnl']-1500*stats['max_drawdown'], 'net_pnl':stats['net_pnl'],
            'drawdown':stats['max_drawdown'],'fees':stats['fees'],'funding':stats['funding'],
            'fills':stats['fills'],'equity':result['daily'],'start_ms':start,'end_ms':end,
            'decision_trace':trace,'decision_counts':reason_counts,
            'timing_trace':timing_trace,'timing_pending_at_end':timing.pending if timing else None,
            'order_status_counts':dict(Counter(r['status'] for r in result['orders'])),
            'decision_policy':position_policy or ('DISCRETE_COST_RISK_V1' if cost_aware else 'FIXED_THRESHOLD_V1')}


def run_factory(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:raise ValueError('invalid attempt')
    root=runtime_state_root()/'data/cache';output=root/'ai_factory';output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'worker.lock') as acquired:
        if not acquired:raise ValueError('factory worker busy')
        with FactoryStore(output/'factory.db') as store:
            if store.get(params['attempt'])['state'] in ('COMPLETED','CANCELLED'):
                return store.get(params['attempt'])
            try:
                from tools.btc_mechanism_study import load_verified_minutes
                from tools.perpetual_alpha_research import load_data
                from tools.perpetual_replay_research import source_identity
                store.update(params['attempt'],'VALIDATING_INPUT',{})
                directory=root/'perpetual_alpha/input'
                identity={'source':source_identity(),'input_hash':digest(snapshot(directory))}
                config=store.get(params['attempt'])['config']
                if 'search_plan' in config:
                    from tools.ai_factory_source_capture import capture_sources
                    identity['research_sources']=capture_sources(output/str(params['attempt'])/'sources.json',config['search_plan']['source_ids'])
                minutes,_=load_verified_minutes(directory);panel=hourly_panel(minutes)
                market,*_=load_data(directory=directory)
                # Respect the canonical replay's supported market window.
                t,x,y=panel;keep=(t+300000>=market['start'])&(t+4*HOUR+300000<=market['end'])
                panel=t[keep],x[keep],y[keep]
                def evaluate(spec,t,p):
                    return evaluate_market(market,spec,t,p,cost_aware=True,
                        variance_by_slot={int(at):float(row[3]**2*4) for at,row in zip(panel[0],panel[1])})
                result=execute(store,params['attempt'],panel,evaluate,identity,output)
                if result['state']=='FAILED':raise ValueError('research factory failed; inspect retained session receipts')
                return result
            except Exception as exc:
                if store.get(params['attempt'])['state']!='FAILED':
                    store.update(params['attempt'],'FAILED',{'reason':type(exc).__name__,'authoritative':False})
                raise
