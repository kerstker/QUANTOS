"""Paired natural-time portfolio observations with a separate frozen cost window."""
from pathlib import Path
import threading
import numpy as np
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import fit_predict,candidates
from services.research.ai_factory_portfolio import SCHEMA as MODEL_SCHEMA,combine_predictions
from services.research.ai_factory_sleeves import SCHEMA as SLEEVE_MODEL_SCHEMA,validate_contract
from services.research.ai_factory_store import digest
from tools.ai_factory_forward import _run,_now,_immutable
from tools.btc_forward_observation import _get
from tools.btc_factory_research import seal,verified
from tools.ai_factory_portfolio_verify import verify_portfolio
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.cache_io import atomic_write_json
from tools.perpetual_replay_research import source_identity
from tools.profitability_forward_account import advance_cost_account

SCHEMA='QUANTOS_PORTFOLIO_FORWARD_V1'
_LOCK=threading.Lock()


def _prediction(model,features,*,sleeves=False):
    components=model['components']
    matrix=np.asarray([fit_predict(s,model['training_x'],model['training_y'],[features]) for s in components])
    if sleeves:
        contract=validate_contract({k:model[k] for k in ('components','quarters','baseline_quarters')})
        if matrix.shape!=(len(components),1) or not np.isfinite(matrix).all():raise ValueError('invalid sleeve forecast vector')
        return {'prediction':float(np.asarray(model['quarters'])@matrix[:,0]/4),
            'threshold_bps':0,'decision_policy':'INDEPENDENT_SLEEVE_COST_RISK_V1',
            'extra':{'baseline_prediction':float(np.asarray(model['baseline_quarters'])@matrix[:,0]/4),
                'component_predictions':matrix[:,0].tolist(),'sleeve_contract_hash':digest(contract),
                'prediction_semantics':'WEIGHTED_RAW_COMPONENT_DIAGNOSTIC_NOT_POSITION_TARGET'}}
    return {'prediction':float(combine_predictions(components,matrix,model['quarters'])[0]),
        'threshold_bps':0,'decision_policy':'DISCRETE_COST_RISK_V1',
        'extra':{'baseline_prediction':float(combine_predictions(components,matrix,model['baseline_quarters'])[0]),
                 'component_predictions':matrix[:,0].tolist(),'prediction_semantics':'COMPONENT_HAIRCUT_APPLIED_ONCE'}}


def run_observation(params,*,root=None,clock=_now,get=_get,sleeves=False):
    if (set(params)!={'attempt','action'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000
            or type(params['action']) is not int or params['action'] not in (0,1,2)):
        raise ValueError('invalid portfolio observation request')
    root=runtime_state_root() if root is None else Path(root)
    if type(sleeves) is not bool:raise ValueError('invalid sleeve observer mode')
    schema='QUANTOS_SLEEVE_FORWARD_V1' if sleeves else SCHEMA
    base=root/'data/cache/ai_factory';experiment=base/str(params['attempt'])/('portfolio-sleeves-v1' if sleeves else 'portfolio');dest=experiment/'forward'
    if any(p.is_symlink() for p in (dest,*dest.parents)):raise ValueError('unsafe portfolio observation path')
    dest.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(dest/'worker.lock',process_lock=_LOCK) as acquired:
        if not acquired:raise ValueError('portfolio observation busy')
        try:
            def load_model(now):
                verify_portfolio(experiment)
                model=verified(experiment/'model.json');result=verified(experiment/'result.json')
                if (model['schema']!=(SLEEVE_MODEL_SCHEMA if sleeves else MODEL_SCHEMA) or result['model_hash']!=model['result_hash']
                        or any(model[k] is not False for k in ('authoritative','live_eligible','economic_eligible'))
                        or model['training_last_label_ms']>=now or any(s not in candidates(2) for s in model['components'])):
                    raise ValueError('portfolio activation identity failed')
                if (dest/'freeze.json').exists() and verified(dest/'freeze.json')['model']['result_hash']!=model['result_hash']:
                    raise ValueError('activated portfolio model changed')
                return model
            source_path=dest/'source.json'
            if params['action']!=2:
                source=source_identity()
                if source_path.exists():
                    old=verified(source_path)['source']
                    if any(source[k]!=old[k] for k in ('python_source_sha256','dependency_lock_sha256','python_version','architecture')):
                        raise ValueError('frozen portfolio observer source changed')
                elif params['action']==1:
                    load_model(clock());_immutable(source_path,{'source':source})
                if (dest/'freeze.json').exists():load_model(clock())
            result=_run(base,dest,params['attempt'],params['action'],clock,get,
                load_model=load_model,predict=(lambda m,f:_prediction(m,f,sleeves=True)) if sleeves else _prediction,schema=schema)
            if params['action']!=2 and (dest/'freeze.json').exists():
                cost=advance_cost_account(root,params['attempt'],register=params['action']==1,portfolio=not sleeves,sleeves=sleeves,clock=clock)
                result=seal({**{k:v for k,v in result.items() if k!='result_hash'},'cost_account_status':cost['status'] if cost else 'NOT_REGISTERED'})
                atomic_write_json(str(dest/'status.json'),result)
            return result
        except Exception as exc:
            result={'schema':schema,'attempt':params['attempt'],'status':'ERROR','reason':type(exc).__name__,
                'observed_at_ms':clock(),'authoritative':False,'account_pnl_computed':False}
            _immutable(dest/'errors'/f'{_now()}.json',result);atomic_write_json(str(dest/'status.json'),seal(result))
            raise


def advance_portfolios(root,*,sleeves=False):
    base=Path(root)/'data/cache/ai_factory';enabled=[];results=[]
    for attempt in range(1,1001):
        path=base/str(attempt)/('portfolio-sleeves-v1' if sleeves else 'portfolio')/'forward/control.json'
        if path.exists() and verified(path)['enabled'] is True:enabled.append(attempt)
    if len(enabled)>8:raise ValueError('at most eight portfolio observers')
    for attempt in enabled:
        try:
            result=run_observation({'attempt':attempt,'action':0},root=root,sleeves=sleeves)
            results.append({'attempt':attempt,'scope':'SLEEVES' if sleeves else 'PORTFOLIO','status':result['status'],
                'observed_at_ms':result['observed_at_ms'],'forward_cost_status':result.get('cost_account_status','NOT_REGISTERED')})
        except Exception as exc:results.append({'attempt':attempt,'scope':'SLEEVES' if sleeves else 'PORTFOLIO','status':'ERROR','reason':type(exc).__name__})
    return results
