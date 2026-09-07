"""Bounded, resumable hourly BTC research factory. Technical evidence only."""
from __future__ import annotations
from decimal import Decimal as D
from pathlib import Path
import numpy as np
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import HOUR,candidates,fit_predict,hourly_panel,partition,targets,validate_config
from services.research.ai_factory_store import FactoryStore,digest
from services.research.trial_ledger import TrialLedger
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.btc_factory_research import _event,_trial,seal,verified,snapshot
from tools.cache_io import atomic_write_json
from tools.ai_factory_provider import propose

SCHEMA='QUANTOS_AI_FACTORY_V1'
POLICY={'schema':SCHEMA,'split':[.65,.82],'purge_hours':4,'entry_delay_minutes':5,
        'seed':17,'max_training_rows':2048,'fee_bps':4,'slippage_bps':5,'stop':True}




def execute(store,attempt,panel,evaluate,identity,output,proposal=propose):
    session=store.get(attempt);config=validate_config(session['config']);dest=Path(output)/str(attempt)
    dest.mkdir(parents=True,exist_ok=True)
    t,x,y=panel
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
                if previous.status=='PLANNED':_event(ledger,record,'RUNNING')
                if previous.status in ('RUNNING','PLANNED'):_event(ledger,record,'FAILED')
                store.record_trial(attempt,ordinal,{'ordinal':ordinal,'status':'FAILED','candidate_id':None,
                    'reason':'INTERRUPTED','trial_id':definition});continue
            _event(ledger,record,'PLANNED');_event(ledger,record,'RUNNING')
            picked=None;stage='PROPOSAL'
            try:
                remaining=[c['id'] for c in candidates() if c['id'] not in [r['candidate_id'] for r in existing]]
                receipt={'candidate_id':remaining[0],'model':'deterministic_baseline'}
                if config['mode']=='ai':
                    history=[{'candidate_id':r['candidate_id'],'score':r.get('metrics',{}).get('score'),'status':r['status']}
                             for r in existing if r['candidate_id'] is not None]
                    receipt=proposal(config['objective'],remaining,history)
                picked=receipt['candidate_id']
                if type(picked) is not int or picked not in remaining:raise ValueError('invalid candidate proposal')
                stage='MODEL_FIT'
                spec=candidates()[picked];p=fit_predict(spec,x[train],y[train],x[valid])
                stage='LEDGER_REPLAY'
                stats=evaluate(spec,t[valid],p)
                if not np.isfinite(stats['score']):raise ValueError('nonfinite trial score')
                result={'ordinal':ordinal,'candidate_id':picked,'spec':spec,'status':'PASSED',
                        'metrics':stats,'proposal':receipt,'rmse':float(np.sqrt(np.mean((p-y[valid])**2))),
                        'trial_id':definition}
            except Exception as exc:
                # Exception messages can include remote payloads or paths; retain a bounded error code.
                result={'ordinal':ordinal,'candidate_id':picked,'status':'FAILED','reason':stage+'_'+type(exc).__name__,'trial_id':definition}
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
            'authoritative':False,'live_eligible':False})
        atomic_write_json(str(dest/'model.json'),artifact)
        result={'selected':spec,'inner':best['metrics'],'outer':outer,'model_hash':artifact['result_hash'],
                'outer_rmse':float(np.sqrt(np.mean((p-y[test])**2))), 'train_rows':len(train),'validation_rows':len(valid),
                'test_rows':len(test),'authoritative':False,'live_eligible':False,'schema':SCHEMA,
                'limitations':['TECHNICAL_VALIDATION_ONLY','HISTORICAL_RECEIVE_TIME_UNKNOWN','CURRENT_VENUE_RULE_SCENARIO',
                               'OUTER_NOT_AVAILABLE_TO_PROPOSAL_MODEL','NATURAL_FORWARD_NOT_ESTABLISHED']}
        atomic_write_json(str(dest/'result.json'),seal(result));store.update(attempt,'COMPLETED',result)
        return store.get(attempt)


def evaluate_market(market,spec,t,p):
    from services.research.perpetual_alpha import replay
    from tools.perpetual_alpha_research import metrics
    day=24*HOUR
    weights=dict(zip((t+300000).tolist(),targets(p,spec['threshold_bps'])))
    # The canonical ledger retains its whole-day reconciliation contract.
    start=int((t[0]+day-1)//day*day+300000)
    end=int(t[-1]//day*day+300000)
    if end<=start:raise ValueError('insufficient whole-day evaluation window')
    result=replay(**{**market,'start':start,'end':end},decision=lambda at,held:weights.get(at,0.),
                  slip_bps=D(5),stop=True,decision_interval_ms=HOUR)
    stats=metrics(result)
    return {'score':stats['net_pnl']-1500*stats['max_drawdown'], 'net_pnl':stats['net_pnl'],
            'drawdown':stats['max_drawdown'],'fees':stats['fees'],'funding':stats['funding'],
            'fills':stats['fills'],'equity':result['daily'],'start_ms':start,'end_ms':end}


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
                minutes,_=load_verified_minutes(directory);panel=hourly_panel(minutes)
                market,*_=load_data(directory=directory)
                # Respect the canonical replay's supported market window.
                t,x,y=panel;keep=(t+300000>=market['start'])&(t+4*HOUR+300000<=market['end'])
                panel=t[keep],x[keep],y[keep]
                def evaluate(spec,t,p):
                    return evaluate_market(market,spec,t,p)
                result=execute(store,params['attempt'],panel,evaluate,identity,output)
                if result['state']=='FAILED':raise ValueError('research factory failed; inspect retained session receipts')
                return result
            except Exception as exc:
                if store.get(params['attempt'])['state']!='FAILED':
                    store.update(params['attempt'],'FAILED',{'reason':type(exc).__name__,'authoritative':False})
                raise
