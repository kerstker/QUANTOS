"""Re-run bounded baseline/random/available-AI search inside three chronological folds."""
from pathlib import Path
import time
import threading
import numpy as np
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import HOUR,candidates,candidate_pool,fit_predict,hourly_panel
from services.research.ai_factory_walkforward import folds
from services.research.ai_factory_store import FactoryStore,digest
from services.research.trial_ledger import TrialLedger
from tools.ai_factory_provider import provider_status,propose
from tools.ai_quant_factory import evaluate_market
from tools.ai_factory_portfolio import run_study
from tools.btc_mechanism_study import load_verified_minutes
from tools.perpetual_alpha_research import load_data
from tools.perpetual_replay_research import source_identity
from tools.btc_factory_research import verified,seal,snapshot,_event,_trial
from tools.cache_io import atomic_write_json
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock

_LOCK=threading.Lock()


def run_comparison(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:raise ValueError('invalid process comparison attempt')
    root=runtime_state_root()/'data/cache';experiment=root/'ai_factory'/str(params['attempt']);dest=experiment/'process-v2'
    dest.mkdir(parents=True,exist_ok=True);path=dest/'process-comparison.json'
    with acquire_runtime_lock(dest/'comparison.lock',process_lock=_LOCK) as acquired:
        if not acquired:raise ValueError('process comparison busy')
        if path.exists():return verified(path)
        state=FactoryStore.snapshot(root/'ai_factory/factory.db',params['attempt'])['selected']
        if state['state']!='COMPLETED':raise ValueError('completed research required')
        plan=verified(experiment/'plan.json');directory=root/'perpetual_alpha/input'
        if digest(snapshot(directory))!=plan['identity']['inputs']['input_hash']:raise ValueError('frozen input changed')
        minutes,_=load_verified_minutes(directory);t,x,y=hourly_panel(minutes);market,*_=load_data(directory=directory)
        keep=(t+300000>=market['start'])&(t+4*HOUR+300000<=market['end']);t,x,y=t[keep],x[keep],y[keep]
        if digest([t.tolist(),x.tolist(),y.tolist()])!=plan['identity']['panel_hash']:raise ValueError('comparison panel changed')
        pool=candidate_pool(state['config']);space={c['id']:c for c in pool}
        budget=state['config']['budget'];groups=['baseline','random']
        if state['config']['mode']=='ai' and provider_status()['status']=='CONFIGURED_UNVERIFIED':groups.append('ai')
        identity={'source':source_identity(),'input_hash':plan['identity']['inputs']['input_hash'],
                  'budget_per_group_per_fold':budget,'groups':groups,'folds':3,'seed':17,'scope':'POST_SELECTION_PROCESS_DIAGNOSTIC',
                  'portfolio_max_components':3,'portfolio_max_inner_candidates':16,'portfolio_outer_cases':6}
        if 'search_plan' in state['config']:identity['search_plan']=state['config']['search_plan']
        frozen=dest/'comparison-plan.json'
        if frozen.exists() and verified(frozen)!=seal(identity):raise ValueError('comparison identity changed')
        if not frozen.exists():atomic_write_json(str(frozen),seal(identity))
        rows=[];variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
        with TrialLedger(str(dest/'comparison-trials.db')) as ledger:
            ledger.verify()
            for fold,(train,valid,test) in enumerate(folds(t,t+4*HOUR+300000)):
                for group in groups:
                    history=[];order=list(space) if group=='baseline' else np.random.default_rng(17+fold).permutation(list(space)).tolist()
                    for ordinal in range(budget):
                        definition=digest({'identity':identity,'fold':fold,'group':group,'ordinal':ordinal})
                        record={'trial_id':definition,'alpha_id':digest(group),'family_id':digest('PROCESS_COMPARISON'),
                                'exact_definition_id':definition,'validation_policy_digest':digest(identity)}
                        receipt_path=dest/'comparison-cases'/f'{definition}.json';receipt_path.parent.mkdir(exist_ok=True)
                        previous=_trial(ledger,definition)
                        if receipt_path.exists():
                            receipt=verified(receipt_path)
                            if previous is None:raise ValueError('comparison receipt missing trial registration')
                            if previous.status=='RUNNING':_event(ledger,record,receipt['status'])
                            elif previous.status!=receipt['status']:raise ValueError('comparison receipt status mismatch')
                        elif previous is not None:
                            if previous.status not in ('PLANNED','RUNNING'):
                                raise ValueError('comparison terminal receipt missing')
                            if previous.status=='PLANNED':_event(ledger,record,'RUNNING')
                            if previous.status in ('PLANNED','RUNNING'):_event(ledger,record,'FAILED')
                            receipt=seal({'status':'FAILED','reason':'INTERRUPTED','candidate_id':None,'trial_id':definition})
                            atomic_write_json(str(receipt_path),receipt)
                        else:
                            _event(ledger,record,'PLANNED');_event(ledger,record,'RUNNING');picked=None;started=time.perf_counter()
                            try:
                                picked=order[ordinal]
                                if group=='ai':
                                    remaining=[c['id'] for c in pool if c['id'] not in [r['candidate_id'] for r in history]]
                                    picked=propose(state['config']['objective'],remaining,[{'candidate_id':r['candidate_id'],'score':r.get('score'),'status':r['status']} for r in history if r['candidate_id'] is not None],**({'search_plan':state['config']['search_plan']} if 'search_plan' in state['config'] else {}))['candidate_id']
                                spec=space[picked];p=fit_predict(spec,x[train],y[train],x[valid])
                                prediction_hash=digest([t[valid].tolist(),p.tolist(),spec['threshold_bps']])
                                if 'search_plan' in state['config'] and any(r.get('prediction_hash')==prediction_hash for r in history):raise ValueError('duplicate inner prediction')
                                stats=evaluate_market(market,spec,t[valid],p,cost_aware=True,variance_by_slot=variance)
                                receipt=seal({'status':'PASSED','candidate_id':picked,'score':stats['score'],'trial_id':definition,
                                              'net_pnl':stats['net_pnl'],**({'prediction_hash':prediction_hash} if 'search_plan' in state['config'] else {}),'elapsed_seconds':time.perf_counter()-started})
                            except Exception as exc:receipt=seal({'status':'FAILED','candidate_id':picked,'reason':type(exc).__name__,'trial_id':definition})
                            atomic_write_json(str(receipt_path),receipt);_event(ledger,record,receipt['status'])
                        history.append(receipt)
                    successful=[r for r in history if r['status']=='PASSED']
                    if not successful:
                        rows.append({'fold':fold,'group':group,'status':'FAILED','trials':history});continue
                    best=max(successful,key=lambda r:(r['score'],-r['candidate_id']));spec=space[best['candidate_id']]
                    component_ids=list(dict.fromkeys(r['candidate_id'] for r in sorted(successful,key=lambda r:(-r['score'],r['candidate_id']))))[:3]
                    portfolio=run_study(dest/'portfolios'/f'{fold}-{group}',
                        {'comparison':identity,'fold':fold,'group':group},[space[i] for i in component_ids],(t,x,y),
                        lambda ts,ps,fee,slip:evaluate_market(market,{'threshold_bps':0},ts,ps,cost_aware=True,
                            variance_by_slot=variance,fee_bps=fee,slip_bps=slip),fit=fit_predict,split=(train,valid,test))
                    portfolio_summary={k:portfolio[k] for k in ('selected','baseline','decision','comparisons','inner_trials_attempted','model_hash','timings')}
                    portfolio_summary['scope']='USED_HISTORY_TECHNICAL_RESEARCH'
                    outer_path=dest/f'comparison-outer-{fold}-{group}.json';started_path=outer_path.with_suffix('.started.json')
                    if outer_path.exists():outer=verified(outer_path)['metrics']
                    elif started_path.exists():
                        rows.append({'fold':fold,'group':group,'status':'OUTER_INTERRUPTED','trials':history});continue
                    else:
                        atomic_write_json(str(started_path),seal({'selected':spec,'identity_hash':digest(identity)}))
                        fit=np.r_[train,valid][-2048:];p=fit_predict(spec,x[fit],y[fit],x[test])
                        outer=evaluate_market(market,spec,t[test],p,cost_aware=True,variance_by_slot=variance)
                        atomic_write_json(str(outer_path),seal({'metrics':outer,'identity_hash':digest(identity)}))
                    rows.append({'fold':fold,'group':group,'status':'COMPLETED','selected':spec,'trials':history,
                                 'portfolio':portfolio_summary,'test_start_ms':int(t[test[0]]),'test_end_ms':int(t[test[-1]]),'outer':outer})
        result=seal({'schema':'QUANTOS_AI_FACTORY_PROCESS_COMPARISON_V2','attempt':params['attempt'],'identity':identity,'rows':rows,
                     'ai_group_status':'EXECUTED' if 'ai' in groups else 'NOT_CONFIGURED','authoritative':False,
                     'limitations':['HISTORICAL_POST_SELECTION_DIAGNOSTIC','LLM_PRETRAINING_KNOWLEDGE_UNKNOWN','NO_ECONOMIC_ADMISSION']})
        atomic_write_json(str(path),result);return result
