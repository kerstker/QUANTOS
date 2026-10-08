"""Bounded three-fold sleeve comparisons using already frozen component forecasts."""
from pathlib import Path
import hashlib
import re
import subprocess
import threading
import time
import numpy as np

from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import HOUR, candidates
from services.research.ai_factory_store import FactoryStore, digest
from services.research.ai_factory_walkforward import folds
from services.research.ai_factory_sleeves import CONSTRUCTION
from tools.ai_factory_portfolio import run_study
from tools.ai_factory_portfolio_verify import verify_portfolio
from tools.ai_factory_sleeve_replay import evaluate_sleeves
from tools.btc_factory_research import verified, seal
from tools.profitability_study import frozen_inputs, write_new
from tools.perpetual_replay_research import ROOT, source_identity
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.cache_io import atomic_write_json

SCHEMA='QUANTOS_SLEEVE_PROCESS_COMPARISON_V1'
_LOCK=threading.Lock()
_PREDICTOR_PATHS=('services/research/ai_factory.py','services/research/ml/stacker.py',
    'services/research/ml/accelerator.py','requirements-ci-linux-aarch64.lock')


def predictor_compatibility(previous):
    current=source_identity()
    if any(previous.get(k)!=current[k] for k in ('dependency_lock_sha256','python_version','architecture')):
        raise ValueError('frozen predictor environment changed')
    commit=previous.get('commit','')
    if not isinstance(commit,str) or not re.fullmatch('[a-f0-9]{40}',commit):
        raise ValueError('frozen predictor source unknown')
    hashes={}
    for name in _PREDICTOR_PATHS:
        old=subprocess.check_output(['git','show',f'{commit}:{name}'],cwd=ROOT,stderr=subprocess.DEVNULL,timeout=10)
        now=(ROOT/name).read_bytes()
        if old!=now:raise ValueError('frozen predictor implementation changed')
        hashes[name]=hashlib.sha256(now).hexdigest()
    return {'status':'MATCHED_IMPLEMENTATION_AND_ENVIRONMENT','files':hashes,
        'previous_source':previous,'current_source':current}


def _summary(result):
    return {k:result[k] for k in ('selected','baseline','decision','comparisons','inner_trials_attempted',
        'model_hash','timings','components')}


def _counts(directory):
    rows=[verified(p) for p in (directory/'cases').glob('*.json')]
    return {'inner_trials_attempted':sum(r['case']['scope']=='INNER' for r in rows),
        'outer_cases_attempted':sum(r['case']['scope']=='OUTER' for r in rows),
        'failed_cases':sum(r['status']=='FAILED' for r in rows)}


def _bindings(directory):
    names=('plan.json','inner-predictions.json','outer-predictions.json','selection.json','result.json')
    return {name:verified(directory/name)['result_hash'] for name in names if (directory/name).exists()}


def verify_comparison(directory):
    directory=Path(directory);plan=verified(directory/'comparison-plan.json')
    if plan.get('schema')!=SCHEMA or plan.get('economic_eligible') is not False:
        raise ValueError('invalid sleeve comparison plan')
    expected=[(fold,group) for fold in range(3) for group in plan['groups']]
    result=verified(directory/'process-comparison.json') if (directory/'process-comparison.json').exists() else None
    rows=[]
    for fold,group in expected:
        path=directory/'rows'/f'{fold}-{group}.json'
        if not path.exists():
            if result:raise ValueError('sleeve comparison terminal row missing')
            continue
        row=verified(path);child=directory/'portfolios'/f'{fold}-{group}'
        if row.get('fold')!=fold or row.get('group')!=group or row.get('plan_hash')!=plan['result_hash']:
            raise ValueError('sleeve comparison row identity changed')
        if _bindings(child)!=row['artifact_hashes']:raise ValueError('sleeve prediction cache binding changed')
        if (child/'plan.json').exists():verify_portfolio(child)
        if row['status']=='COMPLETED' and _summary(verified(child/'result.json'))!=row['portfolio']:
            raise ValueError('sleeve comparison summary changed')
        rows.append(row)
    if result and (result['plan_hash']!=plan['result_hash'] or result['rows']!=rows):
        raise ValueError('sleeve comparison result binding changed')
    return result


def run_cached_comparison(output,upstream,panel,replay,identity):
    output=Path(output);upstream=Path(upstream);output.mkdir(parents=True,exist_ok=True)
    original=verified(upstream/'comparison-plan.json');groups=original.get('groups')
    if groups not in (['baseline','random'],['baseline','random','ai','committee']) or original.get('folds')!=3:
        raise ValueError('unregistered upstream comparison groups')
    t,x,y=panel;t,x,y=np.asarray(t),np.asarray(x),np.asarray(y)
    splits=folds(t,t+4*HOUR+300000);panel_hash=digest([t.tolist(),x.tolist(),y.tolist()])
    plan={'schema':SCHEMA,'identity':identity,'upstream_comparison_hash':original['result_hash'],
        'groups':groups,'folds':3,'panel_hash':panel_hash,'construction':CONSTRUCTION,
        'max_inner_candidates_per_group_fold':16,'outer_cases_per_group_fold':6,
        'prediction_mode':'FROZEN_COMPONENT_REUSE_V1','new_model_call_budget':0,'refit_budget':0,
        'scope':'USED_HISTORY_TECHNICAL_RESEARCH','outer_feedback_allowed':False,'economic_eligible':False}
    path=output/'comparison-plan.json'
    if path.exists():
        if verified(path)!=seal(plan):raise ValueError('sleeve comparison identity changed')
    else:write_new(path,plan)
    finished=verify_comparison(output)
    if finished:return finished
    rows=[];(output/'rows').mkdir(exist_ok=True)
    upstream_identity={k:v for k,v in original.items() if k!='result_hash'}
    for fold,split in enumerate(splits):
        for group in groups:
            path=output/'rows'/f'{fold}-{group}.json';child=output/'portfolios'/f'{fold}-{group}'
            if path.exists():rows.append(verified(path));continue
            atomic_write_json(str(output/'status.json'),seal({'schema':SCHEMA,'status':'RUNNING',
                'fold':fold,'group':group,'completed_groups':len(rows),'total_groups':len(groups)*3,
                'observed_at_ms':time.time_ns()//1000000,'authoritative':False,'economic_eligible':False}))
            stage='UPSTREAM_CONTRACT';started=time.perf_counter()
            row={'fold':fold,'group':group,'plan_hash':digest(plan)}
            try:
                source=upstream/'portfolios'/f'{fold}-{group}';prior=verified(source/'plan.json')
                expected={'comparison':upstream_identity,'fold':fold,'group':group}
                if (prior['identity']!=expected or prior['panel_hash']!=panel_hash
                        or prior['partitions_hash']!=digest([r.tolist() for r in split])
                        or prior['schema']!='QUANTOS_FACTORY_PORTFOLIO_V1'
                        or prior['outer_feedback_allowed'] is not False
                        or not 1<=len(prior['components'])<=3
                        or any(s not in candidates(2) for s in prior['components'])):
                    raise ValueError('upstream component partition contract changed')
                def load(scope,components,fit_rows,target_rows):
                    nonlocal stage
                    stage='INNER_FIT' if scope=='inner' else 'OUTER_COMPARISON'
                    expected_fit=split[0] if scope=='inner' else np.r_[split[0],split[1]][-2048:]
                    expected_target=split[1] if scope=='inner' else split[2]
                    if (components!=prior['components'] or not np.array_equal(fit_rows,expected_fit)
                            or not np.array_equal(target_rows,expected_target)):
                        raise ValueError('frozen prediction rows changed')
                    cached=verified(source/f'{scope}-predictions.json')
                    if cached['plan_hash']!=prior['result_hash']:raise ValueError('upstream prediction plan changed')
                    return {'predictions':cached['predictions'],'source_plan_hash':prior['result_hash'],
                        'source_prediction_hash':cached['result_hash']}
                study=run_study(child,{'comparison_plan_hash':digest(plan),'upstream_plan_hash':prior['result_hash'],
                    'fold':fold,'group':group},prior['components'],panel,replay,split=split,
                    construction=CONSTRUCTION,prediction_loader=load)
                row.update(status='COMPLETED',portfolio=_summary(study))
            except Exception as exc:
                row.update(status='FAILED',stage=stage,reason=type(exc).__name__)
            row.update(**_counts(child),artifact_hashes=_bindings(child),elapsed_ms=round((time.perf_counter()-started)*1000),
                test_start_ms=int(t[split[2][0]]),test_end_ms=int(t[split[2][-1]]))
            write_new(path,row);rows.append(verified(path))
    result={'schema':SCHEMA,'status':'COMPLETED_TECHNICAL_ONLY','attempt':identity['attempt'],
        'plan_hash':digest(plan),'rows':rows,'new_model_calls':0,'refitted_models':0,
        'failed_groups':sum(r['status']!='COMPLETED' or r['failed_cases']>0 for r in rows),'outer_feedback_allowed':False,
        'authoritative':False,'economic_eligible':False,'live_eligible':False,
        'limitations':['USED_HISTORY_NOT_INDEPENDENT_OOS','FROZEN_COMPONENT_REUSE_NOT_NEW_ALPHA_SEARCH',
            'ONE_WAY_NET_ACCOUNT_NOT_HEDGE_MODE','NO_REAL_VENUE_COST_CALIBRATION','NATURAL_FORWARD_NOT_ESTABLISHED']}
    write_new(output/'process-comparison.json',result)
    return verify_comparison(output)


def run_comparison(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('invalid sleeve comparison attempt')
    root=runtime_state_root()/'data/cache';experiment=root/'ai_factory'/str(params['attempt'])
    output=experiment/'process-sleeves-v1';upstream=experiment/'process-v3'
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):raise ValueError('unsafe sleeve comparison path')
    output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'comparison.lock',process_lock=_LOCK) as acquired:
        if not acquired:raise ValueError('sleeve comparison busy')
        if FactoryStore.snapshot(root/'ai_factory/factory.db',params['attempt'])['selected']['state']!='COMPLETED':
            raise ValueError('completed research required')
        previous=verified(upstream/'comparison-plan.json')
        compatibility=predictor_compatibility(previous['source'])
        plan,_,_,t,x,y,market=frozen_inputs(experiment,root/'perpetual_alpha/input')
        if previous['input_hash']!=plan['identity']['inputs']['input_hash']:raise ValueError('comparison input changed')
        variance={int(at):float(row[3]**2*4) for at,row in zip(t,x)}
        return run_cached_comparison(output,upstream,(t,x,y),
            lambda ts,p,fee,slip:evaluate_sleeves(market,p['components'],p['quarters'],ts,p['predictions'],
                variance_by_slot=variance,fee_bps=fee,slip_bps=slip),
            {'attempt':params['attempt'],'source':source_identity(),'predictor_compatibility':compatibility,
                'experiment_plan_hash':plan['result_hash']})
