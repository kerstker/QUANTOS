"""Retain three registered queue diagnostics for one immutable public input."""
import argparse
import hashlib
import json
from pathlib import Path

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from services.execution.passive_execution_input import analyze
from services.research.passive_execution_study import PROFILES,prepare_input,prepare_account,run_profile
from services.research.passive_receipt_calibration import validate_calibration
from services.research.binance_order_receipts import native_fee_comparisons,native_summary
from tools.perpetual_replay_research import source_identity
from tools.profitability_study import write_new
from tools.passive_receipt_study import fit_before_cutoff,finish_heldout


def study(source,output):
    source,output=Path(source),Path(output)
    if source.is_symlink() or not source.is_file() or source.stat().st_size>4000000:
        raise ValueError('bounded fixed public input file required')
    output.mkdir(parents=True,exist_ok=False)
    raw=source.read_bytes()
    if len(raw)>4000000:raise ValueError('bounded input required')
    identity=hashlib.sha256(raw).hexdigest()
    plan={'schema':'PASSIVE_QUEUE_STUDY_V1','source':source_identity(),'input_sha256':identity,
        'profiles_ms':list(PROFILES),'queue_policy':'CANCEL_TAIL_SHARED_VOLUME_V1',
        'trade_volume_policy':'BINANCE_NORMAL_QUANTITY_NQ_EXCLUDES_RPI_V1',
        'action_origin':'IMPORTED_FIXED_ACTIONS_NOT_PRE_EVENT_ATTESTATION',
        'selection':'PREFIX_RECEIPT_ERROR_ONLY_IF_IMPORTED_OTHERWISE_NONE',
        'maximum_simulations_with_calibration':6,'economic_eligible':False}
    write_new(output/'preregistration.json',plan)
    try:
        data=load_json_without_duplicate_keys(raw);tape,actions=prepare_input(data)
        account=prepare_account(data,tape)
        if account is not None:
            write_new(output/'account-environment.json',account['manifest'])
        calibration=None;calibration_spec=None
        if 'calibration' in data:
            calibration_spec=validate_calibration(data['calibration'],tape,actions,digest(data['actions']),
                reduce_only_ids=account['reduce_only_ids'],
                account_ref_sha256=data['account'].get('account_terms',{}).get('account_ref_sha256'))
            calibration=fit_before_cutoff(calibration_spec,tape,actions,account,plan['source'],output/'calibration')
        trials=[];simulations={}
        for profile in PROFILES if actions else ():
            write_new(output/f'profile-{profile}.started.json',{'profile_ms':profile,
                'input_sha256':identity,'action_hash':digest(data['actions']),'economic_eligible':False})
            try:
                result=run_profile(tape,actions,profile,account=account,release_identity=plan['source'])
                artifact=dict(result)
                if account is not None:
                    artifact['engine_result_hash']=artifact.pop('result_hash')
                write_new(output/f'profile-{profile}.json',artifact)
                if calibration is not None:simulations[profile]=result
                queue=result['queue'] if account is not None else result
                account_summary=None
                if account is not None:
                    account_summary={k:result[k] for k in ('input_hash','result_hash','reconciliation',
                        'ending_state','ending_reservation','actual_venue_fees','fee_model','position_history_policy')}
                    account_summary.update({'equity_history':result['equity_history'][-200:],
                        'history_count':len(result['equity_history']),
                        'history_projection':'LAST_200_FULL_HISTORY_IN_ARTIFACT',
                        'simulated_fee_rate':data['account'].get('fee_rate'),'ledger_applied':True})
                trials.append({'profile_ms':profile,'status':'COMPLETED_DIAGNOSTIC',
                    'orders':queue['orders'],'fill_count':len(queue['fills']),
                    'censored_orders':sum(o['censored'] for o in queue['orders']),
                    'events':queue['events'][-200:],'fills':queue['fills'][-200:],
                    'event_count':len(queue['events']),'event_projection':'LAST_200_FULL_TRACE_IN_ARTIFACT',
                    'account':account_summary})
            except Exception as exc:
                failure={'profile_ms':profile,'status':'FAILED','reason':type(exc).__name__,'economic_eligible':False}
                write_new(output/f'profile-{profile}.failure.json',failure);trials.append(failure)
        if calibration is not None:
            try:calibration=finish_heldout(calibration,calibration_spec,actions,simulations,output/'calibration')
            except Exception as exc:
                write_new(output/'calibration/heldout.failure.json',{'reason':type(exc).__name__,'economic_eligible':False})
                calibration={**calibration,'status':'HELDOUT_COMPARISON_FAILED','heldout':None}
            if 'native_observations' in calibration_spec:
                native=calibration_spec['native_observations']
                native={**native,'fee_comparisons':native_fee_comparisons(native,simulations,tape,PROFILES)}
                write_new(output/'native-observations.json',native)
                calibration['native_observations']=native_summary(native)
        quality=analyze(data['tape'])
        complete=all(t['status']=='COMPLETED_DIAGNOSTIC' for t in trials)
        status=('PUBLIC_INPUT_READY_NO_FIXED_ACTIONS' if not actions else
            ('QUEUE_ACCOUNT_REPLAYS_COMPLETED' if account is not None else 'QUEUE_DIAGNOSTICS_COMPLETED') if complete else 'QUEUE_DIAGNOSTICS_WITH_FAILURES')
        if calibration:
            if calibration['status'] in ('INCOMPLETE_PREFIX_FITS','HELDOUT_REPLAY_FAILED','HELDOUT_COMPARISON_FAILED') or not complete:
                status='QUEUE_RECEIPT_DIAGNOSTICS_WITH_FAILURES'
            elif calibration['status']=='HELDOUT_ERRORS_RECORDED_NOT_VENUE_ACCEPTANCE':status='QUEUE_RECEIPT_DIAGNOSTICS_COMPLETED'
            else:status='QUEUE_RECEIPT_DIAGNOSTICS_INCONCLUSIVE'
        attempts=len(trials)+(calibration['trials_attempted'] if calibration else 0)
        result={'stage':'P5_QUEUE','status':status,
            'input_sha256':identity,'input_hash':tape.input_hash,'action_hash':digest(data['actions']),
            'symbol':tape.symbol,'quote':tape.symbol[3:],'start_ms':tape.start_ms,'end_ms':tape.end_ms,
            'quality':quality,'trials':trials,'trials_attempted':attempts,
            'full_window_trials_attempted':len(trials),'source':plan['source'],
            'preregistration_hash':digest(plan),'selected_profile':calibration['selected_profile_ms'] if calibration else None,
            'calibration':calibration,'simulations_attempted':attempts,
            'ledger_applied':bool(actions and account is not None and complete),
            'account_environment':account['manifest'] if account is not None else None,
            'queue_model_calibrated':False,'venue_receipts_verified':False,'economic_eligible':False,
            'decision':'CONTINUE_PRIVATE_RECEIPT_CALIBRATION' if account is not None else 'CONTINUE_CANONICAL_LEDGER_AND_PRIVATE_RECEIPT_CALIBRATION',
            'limitations':['UNOBSERVED_INDIVIDUAL_QUEUE_POSITION','NO_MARKET_IMPACT_SMALL_ORDER_ASSUMPTION',
                'SIMULATED_ACCOUNT_COSTS_NOT_ACTUAL_FEES' if account is not None else 'NO_PROFIT_OR_FEE_MEASUREMENT',
                'FIXED_ACTIONS_ONLY','ASSUMED_ACK_LATENCY_NOT_OBSERVED_PRIVATE_RECEIPTS']}
        write_new(output/'result.json',result);return result
    except BaseException as exc:
        write_new(output/'failure.json',{'stage':'P5_QUEUE','reason':type(exc).__name__,
            'input_sha256':identity,'economic_eligible':False})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=study(args.input,args.output)
    print(json.dumps({k:result[k] for k in ('status','symbol','trials_attempted','ledger_applied')}))


if __name__=='__main__':main()
