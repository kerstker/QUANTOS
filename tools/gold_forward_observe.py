"""At most 60 seconds of locally registered public gold signal observation.

No account, credentials, orders, automatic retry or profitability evaluation.
Caller supplies every rule and scheduled time; no default trading parameters.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from websockets.asyncio.client import connect
from services.research.gold_forward import ForwardObservation
from services.data.gold_execution_tape import new_gold_trade_cursor
from services.research.gold_trade_observation import MAX_BYTES, load_document
from tools.gold_public_capture import PublicCapture, CaptureError, URL
from tools.gold_signal_replay import _source_files

ROOT=Path(__file__).resolve().parents[1]


def now_ms():return time.time_ns()//1000000


def encoded(value):
    return json.dumps(value,default=str,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def write_new(path,value):
    with path.open('xb') as file:file.write(encoded(value))


async def observe(plan,directory,*,strategy_trial=None,prepared_strategy=None,protected_network=False):
    if type(protected_network) is not bool:
        raise ValueError('GOLD_FORWARD_NETWORK_MODE')
    connection_options = {}
    if protected_network:
        from live.exchange import demo_network
        connection_options = {'proxy': demo_network.proxy_url(), 'ssl': demo_network.tls_context()}
    definition=None
    if prepared_strategy is not None and strategy_trial is None:
        raise ValueError('GOLD_FORWARD_PREPARED_TRIAL_REQUIRED')
    if strategy_trial is not None:
        from tools.gold_execution_identity import retain_strategy_definition, PreparedStrategy
        if prepared_strategy is not None:
            if type(prepared_strategy) is not PreparedStrategy:
                raise ValueError('GOLD_FORWARD_PREPARED_STRATEGY_TYPE')
            definition=prepared_strategy.definition(strategy_trial)
        else:
            definition=retain_strategy_definition(strategy_trial)
    registered=now_ms()
    if definition is not None:
        identity=definition['research_identity']
        if (identity['available_at_ms']>=registered or type(plan) is not dict
                or plan.get('rule')!=definition['entry_rule']
                or type(plan.get('zone')) is not dict
                or any(plan['zone'].get(k)!=definition['historical_zone'][k]
                       for k in ('lower','upper'))):
            raise ValueError('GOLD_FORWARD_STRATEGY_MISMATCH_OR_FUTURE')
    engine=ForwardObservation(plan,registered_at_ms=registered,cursor_factory=new_gold_trade_cursor)
    directory=Path(directory);directory.mkdir()
    sources=_source_files()
    for name in ('tools/gold_forward_observe.py','tools/gold_public_capture.py','services/research/gold_forward.py'):
        sources[name]=(ROOT/name).read_bytes()
    if definition is not None:
        for name in ('tools/gold_execution_identity.py','tools/gold_ai_origin.py',
                     'services/research/gold_execution_identity.py'):
            sources[name]=(ROOT/name).read_bytes()
    if protected_network:
        for name in ('live/exchange/demo_network.py', 'live/exchange/binance_futures.py'):
            sources[name]=(ROOT/name).read_bytes()
    source_hashes={name:hashlib.sha256(raw).hexdigest() for name,raw in sources.items()}
    registration={'schema':'GOLD_LOCAL_FORWARD_REGISTRATION_V1','plan':engine.plan,
        'plan_id':engine.plan_id,'registered_at_ms':registered,'source_sha256':source_hashes,
        'local_registration_only':True,'authoritative':False}
    if definition is not None:
        registration['strategy_definition']=definition
    if protected_network:
        registration['network_mode']='PROTECTED_DEMO_NETWORK_V1'
    write_new(directory/'registration.json',registration)
    capture=PublicCapture(started_at_ms=registered)
    status='STREAM_INTERRUPTED';count=0
    # Raw events and decisions are flushed as observed, including rejected frames.
    with (directory/'events.jsonl').open('xb') as events, (directory/'decisions.jsonl').open('xb') as decisions:
        def append(file,value):
            file.write(encoded(value)+b'\n');file.flush()
        try:
            seconds=(engine.plan['decision_times_ms'][-1]+engine.plan['max_lateness_ms']-registered)/1000
            async with asyncio.timeout(seconds):
                async with connect(URL,open_timeout=min(10,seconds),close_timeout=1,
                                   max_size=131072,max_queue=32,**connection_options) as socket:
                    while engine.status=='OBSERVING':
                        at=now_ms()
                        row=engine.advance(capture.packets,now_ms=at)
                        if row is not None:
                            completed=now_ms()
                            if completed<at:raise ValueError('FORWARD_CLOCK_REVERSED')
                            row['decision_available_at_ms']=completed
                            if completed>row['scheduled_at_ms']+engine.plan['max_lateness_ms']:
                                row['diagnostic_decision']=row['decision']
                                row['decision']=None
                                row['reason']='COMPUTATION_DEADLINE_EXCEEDED'
                                engine.status='INPUT_INCOMPLETE'
                            append(decisions,row);count+=1
                            continue
                        wait=(engine.next_decision_ms-at)/1000
                        try:raw=await asyncio.wait_for(socket.recv(),timeout=wait)
                        except TimeoutError:continue
                        received=now_ms()
                        if capture.raw_bytes+len(raw.encode('utf-8') if isinstance(raw,str) else raw)>3000000:
                            raise CaptureError('CAPTURE_BUDGET_EXHAUSTED')
                        append(events,{'received_at_ms':received,'raw':raw if isinstance(raw,str) else None})
                        capture.ingest(raw,received_at_ms=received)
                    status=engine.status
        except TimeoutError:status='OBSERVATION_TIMEOUT'
        except CaptureError:status='INVALID_STREAM'
        except ValueError:status='CLOCK_OR_INPUT_INVALID'
        except Exception:status='STREAM_INTERRUPTED'
    unchanged=all((ROOT/name).read_bytes()==raw for name,raw in sources.items())
    if not unchanged:status='SOURCE_CHANGED'
    result={'schema':'GOLD_LOCAL_FORWARD_RESULT_V1','plan_id':engine.plan_id,
        'registration_sha256':hashlib.sha256(encoded(registration)).hexdigest(),
        'status':status,'recorded_decisions':count,'authoritative':False,
        'local_registration_only':True,'net_pnl':None,
        'events_sha256':hashlib.sha256((directory/'events.jsonl').read_bytes()).hexdigest(),
        'decisions_sha256':hashlib.sha256((directory/'decisions.jsonl').read_bytes()).hexdigest(),
        'limitations':['LOCAL_CLOCK_NOT_ATTESTED','NO_GLOBAL_TRIAL_REGISTRATION',
            'NO_FILLS_OR_PROFITABILITY','NO_ACCOUNT_OR_COMEX_DATA','NO_AUTOMATIC_RESUME']}
    if definition is not None:
        result.update(strategy_definition_sha256=definition['result_hash'],
                      submission_enabled=False)
    write_new(directory/'result.json',result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--strategy-trial',type=Path,
                        help='Bind the unchanged entry rules and price levels of a retained AI trial')
    args=parser.parse_args(argv)
    try:
        with args.plan.open('rb') as file:plan=load_document(file.read(MAX_BYTES+1))
        result=asyncio.run(observe(plan,args.output_dir,strategy_trial=args.strategy_trial))
    except (OSError,ValueError,TypeError):
        print(json.dumps({'status':'REJECTED','reason':'INVALID_FORWARD_PLAN_OR_OUTPUT'}));return 2
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='COMPLETE_OBSERVATION' else 2


if __name__=='__main__':raise SystemExit(main())
