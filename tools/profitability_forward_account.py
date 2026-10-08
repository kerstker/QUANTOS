"""Recoverable bounded forward-cost study; state is isolated from paper/live ledgers."""
from pathlib import Path
import time
import threading
import numpy as np
from services.research.ai_factory_store import digest
from services.research.profitability_forward_account import make_plan,validate_window,replay_window,SLEEVE_POLICY
from tools.btc_factory_research import verified,seal
from tools.profitability_study import write_new
from tools.profitability_forward_market import capture_market
from tools.profitability_trial_receipts import registered_trial
from tools.perpetual_replay_research import source_identity
from tools.cache_io import atomic_write_json
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock

_LOCK=threading.Lock()


def _market_identity(market):
    return {k:[{'time':r['time'],'rate':str(r['rate']),'mark':str(r['mark'])} for r in v] if k=='funding' else v.tolist() if isinstance(v,np.ndarray) else
            {str(a):str(b) for a,b in v.items()} if k=='daily_volume' else
            str(v) if k=='maximum_quantity' else v for k,v in market.items()}


def advance_cost_account(root,attempt,*,register=False,source_root=None,portfolio=False,sleeves=False,
                         clock=lambda:time.time_ns()//1000000,capture=capture_market):
    if type(attempt) is not int or not 1<=attempt<=1000:raise ValueError('invalid forward attempt')
    if type(sleeves) is not bool or sleeves and portfolio:raise ValueError('one forward construction required')
    root=Path(root);source_root=root if source_root is None else Path(source_root)
    output=root/'data/cache/profitability'/str(attempt)/('portfolio-sleeves-forward' if sleeves else 'portfolio-forward' if portfolio else 'p6')
    if not register and not (output/'plan.json').exists():return None
    output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'worker.lock',process_lock=_LOCK) as acquired:
        if not acquired:return {'stage':'P6','status':'BUSY','economic_eligible':False}
        try:
            now=clock();base=source_root/'data/cache/ai_factory'/str(attempt)
            if portfolio:base=base/'portfolio'
            elif sleeves:base=base/'portfolio-sleeves-v1'
            model=verified(base/'model.json')
            if (model.get('authoritative') is not False or model.get('live_eligible') is not False
                    or model['training_last_label_ms']>=now):raise ValueError('frozen research model required')
            if not (output/'plan.json').exists():
                if not (output/'source.json').exists():write_new(output/'source.json',{'source':source_identity(),'model_hash':model['result_hash']})
                if verified(output/'source.json')['model_hash']!=model['result_hash']:raise ValueError('incomplete registration model changed')
                write_new(output/'plan.json',make_plan(now_ms=now,model_hash=model['result_hash'],
                    **({'policy':SLEEVE_POLICY,'sleeves':{k:model[k] for k in ('components','quarters','baseline_quarters')}} if sleeves else
                       {'policy':'COST_AWARE_PORTFOLIO_AND_INNER_BASELINE'} if portfolio else {})))
            frozen_source=verified(output/'source.json')['source'];current_source=source_identity()
            if any(current_source[k]!=frozen_source[k] for k in ('python_source_sha256','dependency_lock_sha256','python_version','architecture')):
                raise ValueError('frozen forward runner source or environment changed')
            plan=verified(output/'plan.json')
            if plan['model_hash']!=model['result_hash']:raise ValueError('forward frozen model changed')
            paths=sorted((base/'forward/forecasts').glob('*.json'))
            rows=[verified(p) for p in paths if p.stem.isdigit() and plan['start_ms']-300000<=int(p.stem)<plan['end_ms']-300000]
            if len(rows)>336:raise ValueError('duplicate or excessive forecast files')
            readiness=validate_window(plan,rows,as_of_ms=now)
            status={'stage':'P6',**readiness,'plan_hash':plan['result_hash'],'observed_at_ms':now,
                    'cost_adjusted_pnl':None,'actual_venue_costs':None,'authoritative':False}
            completed=sorted((output/'completed').glob('*.json'))
            for path in completed:
                old=verified(path);bindings=old['forecast_bindings']
                current={str(r['slot_ms']):r['result_hash'] for r in rows}
                if any(current.get(slot)!=identity for slot,identity in bindings.items()):
                    raise ValueError('previously evaluated prediction changed or disappeared')
            end=readiness['window_end_ms'];destination=output/'completed'/f'{end}.json'
            if readiness['status']!='READY':
                if completed:status['last_completed']=verified(completed[-1])['summary']
                atomic_write_json(str(output/'status.json'),seal(status));return status
            if destination.exists():
                result=verified(destination)['summary']
                atomic_write_json(str(output/'status.json'),seal(result));return result
            attempts=output/'attempts'/str(end);attempts.mkdir(parents=True,exist_ok=True)
            previous=sorted(attempts.iterdir())
            if len(previous)>=3 or previous and now-int(previous[-1].name)<3600000:
                status['status']='RETRY_BUDGET_EXHAUSTED' if len(previous)>=3 else 'RETRY_BACKOFF'
                atomic_write_json(str(output/'status.json'),seal(status));return status
            trial=attempts/str(now);trial.mkdir()
            write_new(trial/'started.json',{'plan_hash':plan['result_hash'],'window_end_ms':end,'started_ms':now})
            try:
                with registered_trial(output,plan,{'scope':'FORWARD_COST_PREFIX','end':end,'attempt_ms':now}):
                    market,receipts=capture(trial/'capture',start=plan['start_ms'],end=end)
                    market_identity=_market_identity(market)
                    if completed:
                        old=verified(completed[-1]);prior=verified(output/'attempts'/str(old['window_end_ms'])/str(old['attempt_ms'])/'market.json')['market']
                        n=len(prior['times'])
                        if (any(market_identity[k][:n]!=prior[k] for k in ('times','prices','marks'))
                                or [r for r in market_identity['funding'] if r['time']<old['window_end_ms']]!=prior['funding']
                                or any(market_identity['daily_volume'].get(k)!=v for k,v in prior['daily_volume'].items())):
                            raise ValueError('previously evaluated market prefix changed')
                    result=replay_window(plan,rows,market,as_of_ms=now)
                    # Every immutable prefix starts at the original account opening.
                    # Existing account snapshots are never rewritten or reset daily.
                    cases=[]
                    for index,case in enumerate(result['cases']):
                        write_new(trial/f'case-{index}.json',case)
                        item={**{k:v for k,v in case.items() if k!='receipt'},
                              'equity':[{'time':r['time'],'equity':r['equity']} for r in case['receipt']['daily']]}
                        if sleeves:
                            from services.research.ai_factory_sleeves import project_trace
                            item['sleeve_execution']=project_trace(case['receipt']['sleeve_execution'],verified(trial/f'case-{index}.json')['result_hash'])
                        cases.append(item)
                    summary={'stage':'P6','status':result['status'],'window':result['window'],'cases':cases,
                             'observed_at_ms':clock(),'plan_hash':plan['result_hash'],'model_hash':plan['model_hash'],
                             'engine_source_hash':frozen_source['python_source_sha256'],'market_hash':digest(market_identity),
                             'economic_eligible':False,'actual_venue_costs':None,'authoritative':False,
                             'limitations':result['limitations'],'forward_orders_executed':False}
                    (output/'completed').mkdir(exist_ok=True)
                    write_new(trial/'market.json',{'market':market_identity,'source_hash':digest(receipts)})
                # Publish completion only after the canonical trial ledger commits PASSED.
                write_new(destination,{'window_end_ms':end,'attempt_ms':now,'summary':summary,
                          'forecast_bindings':{str(r['slot_ms']):r['result_hash'] for r in rows if r['entry_ms']<end}})
                atomic_write_json(str(output/'status.json'),seal(summary));return summary
            except Exception as exc:
                write_new(trial/'failure.json',{'reason':type(exc).__name__,'detail':str(exc),'economic_eligible':False})
                status.update(status='ERROR',reason=type(exc).__name__)
                atomic_write_json(str(output/'status.json'),seal(status));return status
        except Exception as exc:
            status={"stage":"P6","status":"ERROR","reason":type(exc).__name__,"observed_at_ms":clock(),"economic_eligible":False,"authoritative":False}
            (output/"errors").mkdir(exist_ok=True)
            write_new(output/"errors"/f"{time.time_ns()}.json",status)
            atomic_write_json(str(output/"status.json"),seal(status))
            raise


def run_forward_cost(params):
    from quantos.runtime_paths import runtime_state_root
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('bounded frozen experiment required')
    return {'attempt':params['attempt'],'p6':advance_cost_account(runtime_state_root(),params['attempt'],register=True)}


def main(argv=None):
    """Finite research observer; restarting resumes the same immutable window."""
    import argparse
    import json
    import os
    import signal
    parser=argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--source-root',type=Path)
    parser.add_argument('--attempt',type=int,required=True)
    parser.add_argument('--register',action='store_true')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--interval',type=int,default=60)
    args=parser.parse_args(argv)
    if (not 1<=args.attempt<=1000 or not 30<=args.interval<=300 or
            any(not p.is_absolute() or p.is_symlink() for p in (args.state_root,args.source_root) if p is not None)):
        parser.error('absolute research directories, attempt 1..1000 and interval 30..300 required')
    # This process does not install a service or activate forecast generation.
    os.environ['QUANTOS_STATE_ROOT']=str(args.state_root)
    stop=threading.Event()
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,lambda *_:stop.set())
    while not stop.is_set():
        try:
            status=advance_cost_account(args.state_root,args.attempt,register=args.register,source_root=args.source_root)
            if status is None:
                print(json.dumps({'status':'NOT_REGISTERED','economic_eligible':False}),flush=True);return 1
            print(json.dumps({k:status[k] for k in ('status','observed_at_ms','window_end_ms','reason') if k in status}),flush=True)
            if status['status'] in ('MISSING_FORECASTS','RETRY_BUDGET_EXHAUSTED'):return 1
            if args.once:return 0 if status['status']!='ERROR' else 1
            plan=verified(args.state_root/'data/cache/profitability'/str(args.attempt)/'p6/plan.json')
            if time.time_ns()//1000000>=plan['end_ms']+3*3600000:
                return 0 if status['status']=='FORWARD_FORECAST_ACCOUNT_TECHNICAL_ONLY' else 1
        except Exception as exc:
            print(json.dumps({'status':'ERROR','reason':type(exc).__name__,'economic_eligible':False}),flush=True)
            return 1
        stop.wait(args.interval)
    return 0


if __name__=='__main__':raise SystemExit(main())
