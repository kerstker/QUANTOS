"""Run each imported revision once and preserve preceding failures independently."""
from quantos.runtime_paths import runtime_state_root
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from services.research.ai_factory_store import FactoryStore,encode
from tools.btc_factory_research import verified
from tools.profitability_information_study import run_p3,run_p4
from tools.profitability_trajectory_import import import_trajectories
from tools.profitability_calibration_study import calibrate

STAGES={'basis':'p3_study','information':'p4','trajectory':'p5','calibration':'p5_calibration'}


def run_inputs(cache,output,attempt,directory,*,execution_only,input_kind=None,revision=None):
    history=FactoryStore.profitability_inputs(cache/'ai_factory/factory.db',attempt)
    latest={r['kind']:r for r in FactoryStore.profitability_inputs(cache/'ai_factory/factory.db',attempt,include_data=True,input_kind=input_kind,revision=revision)}
    results={}
    for kind,stage in STAGES.items():
        if (kind in ('trajectory','calibration'))!=execution_only or input_kind is not None and kind!=input_kind:continue
        version=latest.get(kind)
        if version:
            identity=version['input_hash'];source=output/'input-files'/f'{identity}.json'
            source.parent.mkdir(parents=True,exist_ok=True)
            body=encode(version['data'])
            if source.exists():
                if source.is_symlink() or source.read_text()!=body:raise ValueError('immutable input materialization changed')
            else:
                with source.open('x') as stream:stream.write(body)
            destination=output/'inputs'/kind/identity
        else:
            source=cache/'profitability_inputs'/str(attempt)/f'{kind}.json';destination=output/stage
        if not source.exists():continue
        if (destination/'result.json').exists():
            results[stage]=verified(destination/'result.json');continue
        if destination.exists():
            results[stage]={'status':'PREVIOUS_ATTEMPT_INCOMPLETE_OR_FAILED','economic_eligible':False};continue
        destination.parent.mkdir(parents=True,exist_ok=True)
        try:
            prior=sum(len(list((output/'inputs'/kind/r['input_hash']).glob('inner-*.started.json'))) for r in history if r['kind']==kind and (not version or r['revision']<version['revision']))
            if version:prior+=len(list((output/stage).glob('inner-*.started.json')))
            if kind=='basis':result=run_p3(output/'p0',source,directory,destination,prior_trials_attempted=prior)
            elif kind=='information':result=run_p4(output/'p0',source,directory,destination,prior_trials_attempted=prior)
            elif kind=='trajectory':result=import_trajectories(source,destination)
            else:result=calibrate(source,destination)
            results[stage]=result
        except Exception as exc:
            results[stage]={'status':'FAILED','reason':type(exc).__name__,'economic_eligible':False}
    return results


def run_input_version(params):
    if (set(params)!={'attempt','input_kind','revision'} or any(type(v) is not int for v in params.values())
            or not 1<=params['attempt']<=1000 or not 0<=params['input_kind']<=3 or not 1<=params['revision']<=3):
        raise ValueError('bounded registered input version required')
    attempt=params['attempt'];kind=tuple(STAGES)[params['input_kind']];cache=runtime_state_root()/'data/cache'
    rows=FactoryStore.profitability_inputs(cache/'ai_factory/factory.db',attempt,include_data=True,input_kind=kind,revision=params['revision'])
    if len(rows)!=1:raise ValueError('registered input version unavailable')
    output=cache/'profitability'/str(attempt);output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'study.lock') as acquired:
        if not acquired:raise ValueError('profitability study busy')
        if kind in ('basis','information') and not (output/'p0/result.json').exists():
            return {'attempt':attempt,STAGES[kind]:{'status':'WAITING_P0','economic_eligible':False}}
        return {'attempt':attempt,**run_inputs(cache,output,attempt,cache/'perpetual_alpha/input',
            execution_only=kind in ('trajectory','calibration'),input_kind=kind,revision=params['revision'])}
