"""Retain three registered queue diagnostics for one immutable public input."""
import argparse
import hashlib
import json
from pathlib import Path

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from services.execution.passive_execution_input import analyze
from services.research.passive_execution_study import PROFILES,prepare_input,run_profile
from tools.perpetual_replay_research import source_identity
from tools.profitability_study import write_new


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
        'action_origin':'IMPORTED_FIXED_ACTIONS_NOT_PRE_EVENT_ATTESTATION',
        'selection':'NONE_WITHOUT_OBSERVED_ORDER_RECEIPTS','economic_eligible':False}
    write_new(output/'preregistration.json',plan)
    try:
        data=load_json_without_duplicate_keys(raw);tape,actions=prepare_input(data)
        trials=[]
        for profile in PROFILES if actions else ():
            write_new(output/f'profile-{profile}.started.json',{'profile_ms':profile,
                'input_sha256':identity,'action_hash':digest(data['actions']),'economic_eligible':False})
            try:
                result=run_profile(tape,actions,profile)
                write_new(output/f'profile-{profile}.json',result)
                trials.append({'profile_ms':profile,'status':'COMPLETED_DIAGNOSTIC',
                    'orders':result['orders'],'fill_count':len(result['fills']),
                    'censored_orders':sum(o['censored'] for o in result['orders']),
                    'events':result['events'][-200:],'fills':result['fills'][-200:],
                    'event_count':len(result['events']),'event_projection':'LAST_200_FULL_TRACE_IN_ARTIFACT'})
            except Exception as exc:
                failure={'profile_ms':profile,'status':'FAILED','reason':type(exc).__name__,'economic_eligible':False}
                write_new(output/f'profile-{profile}.failure.json',failure);trials.append(failure)
        quality=analyze(data['tape'])
        result={'stage':'P5_QUEUE','status':('PUBLIC_INPUT_READY_NO_FIXED_ACTIONS' if not actions else
            'QUEUE_DIAGNOSTICS_COMPLETED' if all(t['status']=='COMPLETED_DIAGNOSTIC' for t in trials) else 'QUEUE_DIAGNOSTICS_WITH_FAILURES'),
            'input_sha256':identity,'input_hash':tape.input_hash,'action_hash':digest(data['actions']),
            'symbol':tape.symbol,'quote':tape.symbol[3:],'start_ms':tape.start_ms,'end_ms':tape.end_ms,
            'quality':quality,'trials':trials,'trials_attempted':len(trials),'source':plan['source'],
            'preregistration_hash':digest(plan),'selected_profile':None,'ledger_applied':False,
            'queue_model_calibrated':False,'venue_receipts_verified':False,'economic_eligible':False,
            'decision':'CONTINUE_CANONICAL_LEDGER_AND_PRIVATE_RECEIPT_CALIBRATION',
            'limitations':['UNOBSERVED_INDIVIDUAL_QUEUE_POSITION','NO_MARKET_IMPACT_SMALL_ORDER_ASSUMPTION',
                'NO_PROFIT_OR_FEE_MEASUREMENT','FIXED_ACTIONS_ONLY','ASSUMED_ACK_LATENCY_NOT_OBSERVED_PRIVATE_RECEIPTS']}
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
