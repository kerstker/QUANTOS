"""Frozen post-selection stress and shared-account ensemble diagnostics."""
from __future__ import annotations
from pathlib import Path
import threading
import numpy as np
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import HOUR,fit_predict,hourly_panel,partition
from services.research.ai_factory_store import FactoryStore,digest
from services.research.ai_factory_diagnostics import summarize_returns,search_bootstrap
from tools.btc_factory_research import verified,seal,snapshot
from tools.btc_mechanism_study import load_verified_minutes
from tools.perpetual_alpha_research import load_data
from tools.ai_quant_factory import evaluate_market
from tools.cache_io import atomic_write_json
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.perpetual_replay_research import source_identity

_LOCK=threading.Lock()
SCHEMA='QUANTOS_AI_FACTORY_ASSESSMENT_V1'


def _returns(stats):
    a=np.array([float(r['equity']) for r in stats['equity']])
    return a[1:]/a[:-1]-1


def run_assessment(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:raise ValueError('invalid assessment attempt')
    root=runtime_state_root()/'data/cache';dest=root/'ai_factory'/str(params['attempt'])
    with acquire_runtime_lock(dest/'assessment.lock',process_lock=_LOCK) as acquired:
        if not acquired:raise ValueError('assessment busy')
        if (dest/'assessment.json').exists():return verified(dest/'assessment.json')
        state=FactoryStore.snapshot(root/'ai_factory/factory.db',params['attempt'])
        if state['selected']['state']!='COMPLETED':raise ValueError('completed experiment required')
        plan=verified(dest/'plan.json');model=verified(dest/'model.json');original=verified(dest/'result.json')
        directory=root/'perpetual_alpha/input'
        if digest(snapshot(directory))!=plan['identity']['inputs']['input_hash']:raise ValueError('frozen input changed')
        minutes,_=load_verified_minutes(directory);t,x,y=hourly_panel(minutes);market,*_=load_data(directory=directory)
        keep=(t+300000>=market['start'])&(t+4*HOUR+300000<=market['end']);t,x,y=t[keep],x[keep],y[keep]
        if digest([t.tolist(),x.tolist(),y.tolist()])!=plan['identity']['panel_hash']:
            raise ValueError('assessment panel identity changed')
        train,valid,test=partition(t,t+4*HOUR+300000)
        p=fit_predict(model['spec'],model['training_x'],model['training_y'],x[test])
        variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
        identity={'source':source_identity(),'input_hash':plan['identity']['inputs']['input_hash'],
                  'model_hash':model['result_hash'],'original_result_hash':original['result_hash'],
                  'scope':'POST_SELECTION_OUTER_DIAGNOSTIC','not_used_for_selection':True}
        if (dest/'assessment-plan.json').exists():
            if verified(dest/'assessment-plan.json')!=seal(identity):raise ValueError('assessment identity changed')
        else:atomic_write_json(str(dest/'assessment-plan.json'),seal(identity))
        day=24*HOUR
        common_window=(int((t[test][0]+HOUR+day-1)//day*day+300000),int(t[test][-1]//day*day+300000))
        cases=[]
        # Every case has an immutable receipt. Interrupted cases are resumed by exact identity.
        for slip,fee,delay in ((2,4,0),(5,4,0),(10,4,0),(5,8,0),(10,8,0),(5,4,1)):
            key=f'{slip}-{fee}-{delay}';path=dest/'assessment-cases'/f'{key}.json'
            if path.exists():
                case=verified(path)
                if case['identity_hash']!=digest(identity):raise ValueError('assessment identity changed')
            else:
                shifted=t[test]+delay*HOUR
                stats=evaluate_market(market,model['spec'],shifted,p,cost_aware=True,
                    variance_by_slot={at+delay*HOUR:v for at,v in variance.items()},slip_bps=slip,fee_bps=fee,window=common_window)
                case=seal({'case':key,'slippage_bps':slip,'fee_bps':fee,'delay_hours':delay,
                           'metrics':stats,'identity_hash':digest(identity)})
                path.parent.mkdir(exist_ok=True);atomic_write_json(str(path),case)
            cases.append(case)
        successful=[r for r in state['trials'] if r['status']=='PASSED']
        successful.sort(key=lambda r:(-r['metrics']['score'],r['ordinal']))
        comparison={'status':'INSUFFICIENT_MODELS','authoritative':False}
        if len(successful)>=2:
            # Freeze equal forecast weights before replay. The result cannot promote either model.
            second=successful[1]['spec'];fit=np.r_[train[ -2048:],valid][-2048:]
            q=fit_predict(second,x[fit],y[fit],x[test])
            context={'models':[model['spec'],second],'weights':[.5,.5],'initial_equity':'1500',
                     'maximum_gross_weight':.30,'construction':'FORECAST_ENSEMBLE_SHARED_ACCOUNT',
                     'identity_hash':digest(identity)}
            atomic_write_json(str(dest/'portfolio-context.json'),seal(context))
            replacement=evaluate_market(market,second,t[test],q,cost_aware=True,variance_by_slot=variance,window=common_window)
            combined=evaluate_market(market,model['spec'],t[test],(p+q)/2,cost_aware=True,variance_by_slot=variance,window=common_window)
            base=cases[1]['metrics']
            comparison={'status':'POST_SELECTION_DIAGNOSTIC','context':context,'replace':replacement,'add':combined,
                        'add_net_pnl_delta':combined['net_pnl']-base['net_pnl'],
                        'replace_net_pnl_delta':replacement['net_pnl']-base['net_pnl'],
                        'authoritative':False,'independent_forward_increment':'NOT_ESTABLISHED'}
        histories=[_returns(r['metrics']) for r in successful]
        n=min(map(len,histories)) if histories else 0
        search=search_bootstrap([r[:n] for r in histories],trials_attempted=len(state['trials'])) if n else {'status':'UNKNOWN'}
        result=seal({'schema':SCHEMA,'attempt':params['attempt'],'identity':identity,'cases':cases,
                     'portfolio':comparison,'return_diagnostics':summarize_returns(_returns(cases[1]['metrics'])),
                     'search_diagnostics':search,'authoritative':False,'live_eligible':False,
                     'limitations':['POST_SELECTION_DIAGNOSTIC_ONLY','NO_RESEARCH_AGENT_FEEDBACK','NATURAL_FORWARD_REQUIRED',
                                    'NO_MAKER_QUEUE_OR_MULTI_VENUE_CLAIM']})
        atomic_write_json(str(dest/'assessment.json'),result);return result
