"""Fixed-path, bounded offline perpetual replay for the guarded research runner."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from decimal import Decimal, ROUND_CEILING
from quantos.runtime_paths import runtime_state_root
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_public_replay import prepare_public_replay, unique_json_object
from services.execution.perpetual_results import fixed_frequency_equity
from services.execution.perpetual_simulation import OrderIntent, SimulationCostModel, run_linear_perpetual_simulation

ROOT=Path(__file__).resolve().parents[1]
CAPTURE=Path('data/cache/perpetual_replay/capture-v2.json')
OUTPUT=Path('data/cache/perpetual_replay/results')


def source_identity():
    paths=sorted(p for folder in ('services','quantos','tools') for p in (ROOT/folder).rglob('*.py'))
    digest=hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    lock=ROOT/'requirements-ci-linux-aarch64.lock'
    return {'commit':commit,'python_source_sha256':digest.hexdigest(),
            'dependency_lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),
            'python_version':platform.python_version(),'architecture':platform.machine()}


def replay_capture(capture, *, release_identity, strategy_id=1, frequency_ms=1000):
    if type(strategy_id) is not int or strategy_id not in (0,1,2): raise ValueError('invalid strategy_id')
    if type(frequency_ms) is not int or not 1<=frequency_ms<=3_600_000: raise ValueError('invalid frequency_ms')
    prepared=prepare_public_replay(capture)
    start=prepared['frames'][0].timestamp
    end=prepared['frames'][-1].timestamp
    spec=prepared['contract_rules'][0].spec
    def strategy(ctx):
        if strategy_id==0 or ctx.timestamp!=start: return ()
        q=ctx.quotes['BTCUSDT']
        price=q.ask_price if strategy_id==1 else q.bid_price
        units=(spec.min_notional*Decimal('1.1')/(price*spec.quantity_step)).to_integral_value(rounding=ROUND_CEILING)
        quantity=max(spec.min_quantity,units*spec.quantity_step)
        return (OrderIntent('entry','BTCUSDT',quantity if strategy_id==1 else -quantity),)
    result=run_linear_perpetual_simulation(release_identity=release_identity,
        dataset_identity=prepared['manifest'],strategy_identity={'version':'MINIMUM_LOT_HOLD_V1','strategy_id':strategy_id},
        contract_rules=prepared['contract_rules'],funding_policies=prepared['funding_policies'],
        order_rule_profiles=prepared['order_rule_profiles'],frames=prepared['frames'],strategy=strategy,
        initial_collateral=Decimal('10000'),costs=SimulationCostModel(fee_rate=Decimal('.0005')))
    engine_result_hash=result.pop('result_hash')
    result['engine_result_hash']=engine_result_hash
    result['release_identity']=release_identity
    result['dataset_manifest']=prepared['manifest']
    result['fixed_frequency']=fixed_frequency_equity(result['equity_history'],start_ms=start,end_ms=end,frequency_ms=frequency_ms)
    result['research_configuration']={'strategy_id':strategy_id,'frequency_ms':frequency_ms,
        'initial_collateral':'10000','fee_rate':'.0005','adverse_slippage_rate':'0',
        'fee_policy':'EXPLICIT_UNCALIBRATED_SIMULATION_RATE'}
    result['result_hash']=exact_identity_hash(result,name='public_replay_result')
    return result


def run_replay(params):
    if set(params)-{'strategy_id','frequency_ms'}: raise ValueError('unknown replay parameters')
    state=runtime_state_root()
    capture_path=state/CAPTURE
    output_path=state/OUTPUT
    if capture_path.is_symlink() or capture_path.stat().st_size>10_000_000: raise ValueError('invalid frozen capture file')
    capture=json.loads(capture_path.read_text(),object_pairs_hook=unique_json_object)
    result=replay_capture(capture,release_identity=source_identity(),**params)
    encoded=json.dumps(result,sort_keys=True,allow_nan=False,indent=2)+'\n'
    output_path.mkdir(parents=True,exist_ok=True)
    artifact=output_path/(result['result_hash']+'.json')
    try:
        with artifact.open('x') as handle: handle.write(encoded)
    except FileExistsError:
        if artifact.is_symlink() or artifact.read_text()!=encoded:
            raise ValueError('conflicting immutable replay artifact')
    print('Technical replay artifact: '+str(artifact))
    return result


if __name__=='__main__':
    result=run_replay({})
    print(json.dumps({'result_hash':result['result_hash'],'evidence_status':result['evidence_status'],
                     'orders':len(result['orders']),'residual':result['reconciliation']['residual']}))
