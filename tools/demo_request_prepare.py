"""Prepare one installed Demo task using current observations and original risk.

Only the executor identity and private root supervisor pipes are accepted. This
process performs bounded GETs, retains their originals, and emits request input;
it never signs a grant, submits an order or initializes a risk/position store.
"""
import argparse
from contextlib import ExitStack
import json
import os
import sys
import time

from live.execution import demo_authority,demo_current_kill,demo_request_inputs
from live.execution.demo_batch_risk import _digest,_fields
from live.execution.demo_issuer_policy import current_key,require
from live.execution.demo_pre_submit import _retain
from quantos.contracts.killswitch import KillSwitchState
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research import demo_market_inputs
from services.research.demo_execution_origin import load as load_origin
from tools import binance_demo_market_inputs,demo_executor_origin
from tools.demo_execution_session import read_frame
from tools.demo_executor_stream import fixed_account
from tools.demo_issuer import private_pipe,verify_source,MAX_INPUT_BYTES
from tools.demo_resident_recovery import _check_path,_open


def now_ms():return time.time_ns()//1_000_000


def destination(root,name):
    path=root/name;path.mkdir(mode=0o700,exist_ok=True)
    _check_path(path,root.stat().st_uid,directory=True)
    count=size=0
    for entry in path.iterdir():
        _check_path(entry,root.stat().st_uid,directory=False);count+=1;size+=entry.stat().st_size
        require(count<1000 and size<64*1024*1024,'REQUEST_PREPARATION_STORAGE_BUDGET')
    return path


def capture_inputs(*,root,symbol,source_commit):
    origin=demo_executor_origin.run(symbol=symbol,source_commit=source_commit)
    require(origin['status']=='CAPTURED','REQUEST_ORIGIN_UNAVAILABLE')
    # Load the exact returned record, independently of a periodic writer moving
    # latest.json after this capture releases its lock.
    from pathlib import Path
    origin_path=Path(origin['path'])
    require(origin_path.parent==root/'execution-origins'/symbol,'REQUEST_ORIGIN_PATH')
    raw_origin=load_origin(origin_path)
    market=binance_demo_market_inputs.collect_bounded(symbol=symbol,protected=True)
    demo_market_inputs.save(destination(root,'market-inputs-'+symbol),market)
    require(market['status']=='CAPTURED','REQUEST_MARKET_UNAVAILABLE')
    return raw_origin,market


def run(*,source_commit,input_fd=None,output=None):
    channel=sys.stdout if output is None else output
    root=None;task_hash=None;phase='CONFIGURATION';previous=os.umask(0o077)
    try:
        verify_source(source_commit);root,account=fixed_account()
        registry=demo_authority.load_registry_for_executor()
        require(os.geteuid()==os.getegid()==registry['executor_uid'] and
            set(os.getgroups())<={registry['executor_uid']},'REQUEST_PREPARER_IDENTITY')
        fd=sys.stdin.fileno() if input_fd is None else input_fd
        private_pipe(fd);private_pipe(channel.fileno())
        phase='TASK_INPUT'
        raw=read_frame(fd,maximum=MAX_INPUT_BYTES,deadline=time.monotonic()+15)
        bundle=load_json_without_duplicate_keys(raw);_fields(bundle,'task policy')
        task,policy=bundle['task'],bundle['policy'];task_hash=_digest(task);at=now_ms()
        demo_request_inputs.task_check(task,policy=policy,source_commit=source_commit,now_ms=at)
        current_key(policy,registry,at)
        require(policy['account_ref_sha256']==account and
            task['profile']['market_source_sha256']==binance_demo_market_inputs.source_hash(),'REQUEST_CAPTURE_BINDING')
        kill=KillSwitchState.model_validate_json(json.dumps(task['kill_switch']),strict=True)
        demo_current_kill.require_expected(kill,now_ms=at)
        target=destination(root,'request-preparation-results')
        phase='OBSERVATIONS'
        origin,market=capture_inputs(root=root,symbol=task['profile']['symbol'],source_commit=source_commit)
        phase='ORIGINAL_RISK'
        with ExitStack() as stack:
            stores=_open(stack,root);at=now_ms()
            demo_request_inputs.task_check(task,policy=policy,source_commit=source_commit,now_ms=at)
            result=demo_request_inputs.build(orders=task['orders'],origin=origin,market_inputs=market,
                profile=task['profile'],issuer_policy=policy,kill_state=kill,
                **{k:stores[k] for k in ('dispatch','recovery','oms')},now_ms=at)
        result.update(schema='DEMO_REQUEST_PREPARATION_V1',task_sha256=task_hash,source_commit=source_commit)
        phase='RESULT_PERSISTENCE';_retain(target,result)
        print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False),file=channel,flush=True)
        return 0
    except Exception:
        result={'schema':'DEMO_REQUEST_PREPARATION_V1','status':'INPUTS_UNRESOLVED','phase':phase,
            'source_commit':source_commit,'task_sha256':task_hash,'observed_at_ms':now_ms(),
            'submission_enabled':False,'economic_authority':False}
        if root is not None:
            try:_retain(destination(root,'request-preparation-results'),result)
            except Exception:pass
        try:print(json.dumps(result,sort_keys=True,separators=(',',':')),file=channel,flush=True)
        except (OSError,ValueError):pass
        return 3
    finally:os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    args=parser.parse_args(argv)
    return run(source_commit=args.source_commit)


if __name__=='__main__':raise SystemExit(main())
