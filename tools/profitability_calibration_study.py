"""Three preregistered execution latency profiles, selected only by trace error."""
from pathlib import Path
from decimal import Decimal
import hashlib
import time
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_simulation import SimulationLimits,SimulationCostModel
from services.research.profitability_calibration import PROFILES,decode_actions,action_frames,compare_prefix,calibration_score
from services.research.profitability_fixed_replay import run_fixed_actions
from services.research.profitability_trajectory import compare_trajectories
from services.research.profitability_inputs import validate_input
from tools.profitability_study import write_new
from tools.profitability_trial_receipts import registered_trial
from tools.perpetual_replay_research import source_identity


def calibrate(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if source==output or source in output.parents:raise ValueError('separate calibration output required')
    output.mkdir(parents=True,exist_ok=False)
    plan={'stage':'P5','profiles':list(PROFILES),'created_at_ms':time.time_ns()//1000000,
          'objective':'UNMATCHED_ORDERS_QUANTITY_UNMATCHED_EVENTS_TIME_LEXICOGRAPHIC',
          'selection_scope':'BEFORE_INPUT_CUTOFF_ONLY','source':source_identity(),
          'history':'ALREADY_OBSERVED_TRACE_NOT_NEW_OOS','economic_eligible':False}
    write_new(output/'preregistration.json',plan);trials=[];simulations={}
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_size>4000000:raise ValueError('bounded calibration file required')
        raw=source.read_bytes();data=load_json_without_duplicate_keys(raw);validate_input('calibration',data)
        prepared=prepare_public_replay(data['capture']);actions=decode_actions(data['actions']);cutoff=data['calibration_cutoff_ms']
        identity=hashlib.sha256(raw).hexdigest()
        write_new(output/'input-identity.json',{'input_sha256':identity,'calibration_cutoff_ms':cutoff})
        for index,(order_ms,cancel_ms) in enumerate(PROFILES):
            try:
                with registered_trial(output,{**plan,'input_sha256':identity},{'scope':'TRACE_FIT','profile':index}) as trial_id:
                    result=run_fixed_actions(schedule=actions,cancel_latency_ms=cancel_ms,
                        release_identity=plan['source'],dataset_identity=prepared['manifest'],
                        contract_rules=prepared['contract_rules'],funding_policies=prepared['funding_policies'],
                        order_rule_profiles=prepared['order_rule_profiles'],
                        frames=action_frames(prepared['frames'],actions,cancel_latency_ms=cancel_ms),
                        initial_collateral=Decimal('10000'),costs=SimulationCostModel(fee_rate=Decimal('.0005')),
                        limits=SimulationLimits(order_latency_ms=order_ms))
                    fit=compare_prefix(data['observed'],result['trajectory'],cutoff)
                    write_new(output/f'simulation-{index}.json',result);simulations[index]=result['trajectory']
                row={'profile':index,'order_latency_ms':order_ms,'cancel_latency_ms':cancel_ms,
                     'status':'COMPLETED','fit':fit,'trial_id':trial_id}
            except Exception as exc:row={'profile':index,'order_latency_ms':order_ms,'cancel_latency_ms':cancel_ms,'status':'FAILED','reason':type(exc).__name__}
            write_new(output/f'trial-{index}.json',row);trials.append(row)
        success=[r for r in trials if r['status']=='COMPLETED']
        if not success:raise ValueError('no valid calibration profile')
        best=min(success,key=lambda r:calibration_score(r['fit']))
        write_new(output/'selection.json',{'profile':best['profile'],'trials_attempted':len(trials),'selection_scope':'BEFORE_CUTOFF_ONLY'})
        # Late-event diagnostics are produced only after the selection is frozen.
        def tail(trace):
            rows=[]
            for order in trace['orders']:
                events=[e for e in order['events'] if e['at_ms']>cutoff]
                if events:rows.append({**order,'events':events})
            return {**trace,'orders':rows}
        selected=simulations[best['profile']]
        heldout=compare_trajectories(tail(data['observed']),tail(selected))
        report=compare_trajectories(data['observed'],selected)
        result={'stage':'P5',**report,'input_sha256':identity,'trials':trials,'trials_attempted':len(trials),
                'selected_profile':best['profile'],'heldout':heldout,
                'decision':'INSUFFICIENT_HELDOUT_TRACE' if not heldout['paired_orders'] else 'FROZEN_PROFILE_AWAITS_VENUE_VERIFICATION',
                'calibration_complete':False,'queue_model_calibrated':False,
                'limitations':report['limitations']+prepared['manifest']['limitations']}
        write_new(output/'result.json',result);return result
    except Exception as exc:
        write_new(output/'failure.json',{'stage':'P5','reason':type(exc).__name__,'detail':str(exc),
                  'trials_attempted':len(trials),'economic_eligible':False})
        raise
