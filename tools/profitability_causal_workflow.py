"""Finite automatic P3/P4 acquisition and research, isolated from frozen P6 predictions."""
from pathlib import Path
import time

from services.research.profitability_causal_information import make_plan,observation,panel,HOUR
from tools.profitability_causal_capture import capture_information
from tools.profitability_causal_study import run_collected_studies
from tools.profitability_study import write_new
from tools.btc_factory_research import verified,seal
from tools.perpetual_replay_research import source_identity
from tools.cache_io import atomic_write_json
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock


def _study_summary(result):
    if not result:return None
    summary={k:v for k,v in result.items() if k not in ('results','result_hash')}
    summary['results']={stage:{'status':r['status'],'decision':r['decision'],'trials_attempted':r['trials_attempted'],
                       'selected_id':r['selected_id'],'artifact_hash':r['result_hash'],
                       'cases':[{'id':c['id'],'fee_bps':c['fee_bps'],'slippage_bps':c['slippage_bps'],
                                 'metrics':{k:c['metrics'][k] for k in ('net_pnl','fees','funding','fills','drawdown')}} for c in r['cases']]}
                       for stage,r in result.get('results',{}).items()}
    return summary


def _retained_partial(directory):
    partial={'status':'STOP_PREVIOUS_STUDY_FAILED_OR_INTERRUPTED','economic_eligible':False,'results':{}}
    if (directory/'failure.json').exists():partial['reason']=verified(directory/'failure.json')['reason']
    for stage in ('P3','P4'):
        if (directory/stage/'result.json').exists():partial['results'][stage]=verified(directory/stage/'result.json')
    return partial


def _observations(output,plan,quote):
    rows=[]
    for slot in range(plan['warmup_ms'],plan['end_slot_ms']+HOUR,HOUR):
        folder=output/'captures'/str(slot);path=folder/f'{quote}.json'
        if not path.exists():continue
        value=verified(path)
        receipts={kind:{k:v for k,v in verified(folder/f'{quote}-{kind}.json').items() if k!='result_hash'}
                  for kind in ('hourly','spot','perpetual','index','funding','oi')}
        expected=observation(receipts,slot=slot,quote=quote,issued_ms=value['issued_ms'])
        if seal(expected)!=value:raise ValueError('retained observation no longer matches its source receipts')
        rows.append(value)
    return rows


def advance_information(root,attempt,*,register=False,clock=lambda:time.time_ns()//1000000,
                        capture=capture_information,study=run_collected_studies):
    if type(attempt) is not int or not 1<=attempt<=1000:raise ValueError('bounded experiment required')
    base=Path(root)/'data/cache/profitability'/str(attempt);output=base/'causal'
    if not register and not (output/'plan.json').exists():return None
    output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'worker.lock') as acquired:
        if not acquired:return {'status':'BUSY','economic_eligible':False}
        try:
            now=clock();p0=verified(base/'p0/result.json')
            if p0['decision']!='P1_BOUNDED_INNER_COMPARISON_ALLOWED':raise ValueError('reconciled P0 required')
            current=source_identity()
            if not (output/'source.json').exists():write_new(output/'source.json',{'source':current})
            original=verified(output/'source.json')['source']
            if any(current[k]!=original[k] for k in ('python_source_sha256','dependency_lock_sha256','python_version','architecture')):
                raise ValueError('frozen collection implementation or environment changed')
            if not (output/'plan.json').exists():write_new(output/'plan.json',make_plan(now_ms=now,p0_hash=p0['result_hash']))
            plan=verified(output/'plan.json')
            if p0['result_hash']!=plan['p0_hash']:raise ValueError('registered P0 changed')
            # Validate the full contract before doing any network work.
            panel(plan,[],quote='USDC',as_of_ms=now)
            slot=now//HOUR*HOUR;destination=output/'captures'/str(slot)
            if plan['warmup_ms']<=slot<=plan['end_slot_ms'] and now<slot+240000 and not destination.exists():
                capture(destination,slot=slot,clock=clock)
            panels={quote:panel(plan,_observations(output,plan,quote),quote=quote,as_of_ms=clock()) for quote in plan['quotes']}
            if (output/'frozen-panel.json').exists():
                frozen=verified(output/'frozen-panel.json')
                if frozen['plan_hash']!=plan['result_hash'] or frozen['panels']!=panels:raise ValueError('evaluated feature panel changed')
            studies={}
            for quote in plan['quotes']:
                study_directory=output/('study' if quote=='USDC' else 'study-usdt');result=None
                if (study_directory/'result.json').exists():
                    result=verified(study_directory/'result.json')
                    if result['plan_hash']!=plan['result_hash'] or result['quote']!=quote:raise ValueError('study plan or quote changed')
                elif study_directory.exists():result=_retained_partial(study_directory)
                elif panels[quote]['status']=='READY_FOR_REGISTERED_RESEARCH':
                    if not (output/'frozen-panel.json').exists():write_new(output/'frozen-panel.json',{'plan_hash':plan['result_hash'],'panels':panels,'economic_eligible':False})
                    try:result=study(study_directory,plan,panels[quote])
                    except Exception as exc:
                        result=_retained_partial(study_directory)
                        result.update(status='STOP_STUDY_FAILED',reason=type(exc).__name__)
                studies[quote]=_study_summary(result)
            summary=[]
            for quote,values in panels.items():
                summary.append({k:values[k] for k in ('quote','status','ready_rows','required_rows','missing_slots')})
            status={'status':'WINDOW_FINISHED' if now>=plan['mature_ms'] else 'COLLECTING' if now>=plan['warmup_ms'] else 'WAITING_WINDOW',
                    'observed_at_ms':clock(),'plan_hash':plan['result_hash'],'start_ms':plan['warmup_ms'],
                    'mature_ms':plan['mature_ms'],'quotes':summary,'study':studies['USDC'],'usdt_study':studies['USDT'],
                    'trials_attempted':sum(1 for _ in output.glob('study*/P*/inner-*.started.json')),
                    'total_candidate_budget':plan['total_candidate_budget'],
                    'prediction_evidence':'NONE_FEATURE_COLLECTION_ONLY','economic_eligible':False,'authoritative':False,
                    'limitations':['SEPARATE_USDC_USDT_RESEARCH_ACCOUNTS_NO_CURRENCY_CONVERSION','FIXED_COST_AND_VENUE_RULE_SCENARIOS_NOT_ACTUAL_FEES','NO_RETROACTIVE_FEATURES_OR_PREDICTIONS']}
            atomic_write_json(str(output/'status.json'),seal(status));return status
        except Exception as exc:
            status={'status':'ERROR','reason':type(exc).__name__,'detail':str(exc),'observed_at_ms':clock(),'economic_eligible':False,'authoritative':False}
            (output/'errors').mkdir(exist_ok=True)
            write_new(output/'errors'/f'{time.time_ns()}.json',status)
            atomic_write_json(str(output/'status.json'),seal(status))
            raise


def advance_registered_information(root):
    base=Path(root)/'data/cache/profitability'
    attempts=[i for i in range(1,1001) if (base/str(i)/'causal/plan.json').exists()]
    if len(attempts)>8:raise ValueError('at most eight information windows may be scheduled')
    results=[]
    for attempt in attempts:
        try:result=advance_information(root,attempt);results.append({'attempt':attempt,'information_status':result['status']})
        except Exception as exc:results.append({'attempt':attempt,'information_status':'ERROR','reason':type(exc).__name__})
    return results


def run_information(params):
    from quantos.runtime_paths import runtime_state_root
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('bounded frozen experiment required')
    return {'attempt':params['attempt'],'causal':advance_information(runtime_state_root(),params['attempt'],register=True)}


def main(argv=None):
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',type=Path,required=True);parser.add_argument('--attempt',type=int,required=True)
    parser.add_argument('--register',action='store_true');parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv);first=True
    while True:
        result=advance_information(args.state_root,args.attempt,register=args.register and first);first=False
        print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
        if args.once or result is None or result['status']=='WINDOW_FINISHED':return
        time.sleep(60)


if __name__=='__main__':main()
