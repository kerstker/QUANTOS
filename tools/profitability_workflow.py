"""Whitelisted registered profitability research workflow; no deployment or live execution path."""
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import FactoryStore
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.btc_factory_research import verified
from tools.profitability_study import run_p0
from tools.profitability_position_study import run_p1
from tools.profitability_timing_study import run_p2
from tools.profitability_market_capture import capture_basis
from tools.profitability_information_study import run_p4
from tools.profitability_trajectory_import import import_trajectories


def run_profitability(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('invalid profitability attempt')
    attempt=params['attempt'];cache=runtime_state_root()/'data/cache'
    selected=FactoryStore.snapshot(cache/'ai_factory/factory.db',attempt)['selected']
    if selected is None or selected['state']!='COMPLETED':raise ValueError('completed frozen experiment required')
    output=cache/'profitability'/str(attempt);output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'study.lock') as acquired:
        if not acquired:raise ValueError('profitability study busy')
        experiment=cache/'ai_factory'/str(attempt);directory=cache/'perpetual_alpha/input'
        # A partial or failed directory is intentionally not restarted as a new attempt.
        # The original failure stays visible and requires a separately registered study.
        if not (output/'p0/result.json').exists():run_p0(experiment,directory,output/'p0')
        p0=verified(output/'p0/result.json')
        if p0['model_hash']!=verified(experiment/'model.json')['result_hash']:
            raise ValueError('cached study belongs to a different frozen model')
        if p0['decision']!='P1_BOUNDED_INNER_COMPARISON_ALLOWED':return {'attempt':attempt,'p0':p0,'p1':None}
        if not (output/'p1/result.json').exists():run_p1(output/'p0',directory,output/'p1')
        if verified(output/'p1/result.json')['p0_hash']!=p0['result_hash']:
            raise ValueError('cached P1/P0 binding mismatch')
        if not (output/'p2/result.json').exists():run_p2(output/'p0',output/'p1',directory,output/'p2')
        if verified(output/'p2/result.json')['p1_hash']!=verified(output/'p1/result.json')['result_hash']:
            raise ValueError('cached P2/P1 binding mismatch')
        if not (output/'p3/result.json').exists():capture_basis(output/'p3')
        information=cache/'profitability_inputs'/str(attempt)/'information.json'
        trajectory=cache/'profitability_inputs'/str(attempt)/'trajectory.json'
        if information.exists() and not (output/'p4/result.json').exists():run_p4(output/'p0',information,directory,output/'p4')
        if trajectory.exists() and not (output/'p5/result.json').exists():import_trajectories(trajectory,output/'p5')
        return {'attempt':attempt,**{stage:verified(output/stage/'result.json') if (output/stage/'result.json').exists() else None
                                    for stage in ('p0','p1','p2','p3','p4','p5')}}
