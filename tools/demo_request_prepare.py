"""Prepare one installed Demo task using current observations and original risk.

Only the executor identity and private root supervisor pipes are accepted. This
process performs bounded GETs, retains their originals, and emits request input;
it never signs a grant, submits an order or initializes a risk/position store.
"""
import argparse
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import time

from live.execution import demo_authority,demo_current_kill,demo_request_inputs
from live.execution.demo_batch_risk import _digest,_fields
from live.execution.demo_issuer_policy import current_key,require
from live.execution.demo_delegated_inputs import task_context
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
    # These collectors have independent clocks and validation. Keep each
    # collector's internal sequence/timeout unchanged, but do not add four
    # public GET latencies after the complete account snapshot. Both finish
    # before original risk reads either result; no fallback or retry is added.
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = (pool.submit(demo_executor_origin.run, symbol=symbol, source_commit=source_commit),
                   pool.submit(binance_demo_market_inputs.collect_bounded, symbol=symbol, protected=True))
        values = []; failures = []
        for future in pending:
            try: values.append(future.result())
            except Exception as exc: failures.append(exc)
        if failures: raise failures[0]
    origin,market=values
    require(origin['status']=='CAPTURED','REQUEST_ORIGIN_UNAVAILABLE')
    # Load the exact returned record, independently of a periodic writer moving
    # latest.json after this capture releases its lock.
    from pathlib import Path
    origin_path=Path(origin['path'])
    require(origin_path.parent==root/'execution-origins'/symbol,'REQUEST_ORIGIN_PATH')
    raw_origin=load_origin(origin_path)
    demo_market_inputs.save(destination(root,'market-inputs-'+symbol),market)
    require(market['status']=='CAPTURED','REQUEST_MARKET_UNAVAILABLE')
    return raw_origin,market



def retained_delegated_inputs(stack, *, root, task, policy, registry, context):
    """Use the exact local executor frame; never recapture or migrate journals."""
    from live.execution.demo_pre_submit import _preparation_lease, _state
    from tools.gold_delegated_proof import _read_only_stores, _pilot_records
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention

    uid = registry["executor_uid"]
    enabled = context["delegation_context"]["enable"]
    proof = task["delegated_admission"]
    base = enabled["runtime"]
    account = policy["account_ref_sha256"]
    require(base["source_commit"] == task["source_commit"] == proof["source_commit"], "REQUEST_DELEGATED_SOURCE")
    stores = _read_only_stores(stack, root, uid)
    stack.enter_context(_preparation_lease(stores["dispatch"]))
    original = original_context(root, base, uid=uid, account=account)
    require(original is not None and original["configuration"] is not None, "REQUEST_DELEGATED_ENROLLMENT")
    retained = stack.enter_context(
        Retention(
            root, original=original, base_runtime=base, uid=uid, role="executor", read_only=True, verify_history=False
        )
    )
    result_sha256 = _digest(proof["result"])

    def recheck():
        result = load_json_without_duplicate_keys(retained.read("result", result_sha256))
        claim = retained.reference("result", result_sha256)
        decision = load_json_without_duplicate_keys(retained.read_claim(claim, "decision"))
        origin = load_json_without_duplicate_keys(retained.read_claim(claim, "origin"))
        require(
            claim == proof["claim"]
            and retained.binding == proof["capacity"]
            and claim["bundle_sha256"] == _digest(proof["runtime"])
            and result == proof["result"]
            and decision == result["decision"]
            and result["decision_sha256"] == _digest(decision)
            and decision["origin_sha256"] == _digest(origin)
            and origin == proof["origin"]
            and origin["account_ref_sha256"] == account,
            "REQUEST_DELEGATED_RETAINED_FRAME",
        )
        require(_state(**stores) == proof["execution"]["state"], "REQUEST_DELEGATED_STATE_CHANGED")
        require(
            _pilot_records(root, uid) == proof["pilot"]
            and original_context(root, base, uid=uid, account=account) == original,
            "REQUEST_DELEGATED_PILOT_CHANGED",
        )
        return origin

    return recheck(), stores, recheck


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
        delegated=task_context(task,policy=policy,registry=registry,now_ms=at,role='executor')
        demo_request_inputs.task_check(task,policy=policy,source_commit=source_commit,now_ms=at,**delegated)
        from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA, current_template_key
        if delegated and policy.get('schema')==FUTURE_SCHEMA:current_template_key(policy,registry,at)
        else:current_key(policy,registry,at)
        require(policy['account_ref_sha256']==account and
            task['profile']['market_source_sha256']==binance_demo_market_inputs.source_hash(),'REQUEST_CAPTURE_BINDING')
        kill=KillSwitchState.model_validate_json(json.dumps(task['kill_switch']),strict=True)
        demo_current_kill.require_expected(kill,now_ms=at)
        target=destination(root,'request-preparation-results')
        phase='OBSERVATIONS'
        with ExitStack() as stack:
            if delegated:
                origin,stores,recheck=retained_delegated_inputs(stack,root=root,task=task,
                    policy=policy,registry=registry,context=delegated)
                market=binance_demo_market_inputs.collect_bounded(symbol=task['profile']['symbol'],protected=True)
                demo_market_inputs.save(destination(root,'market-inputs-'+task['profile']['symbol']),market)
                require(market['status']=='CAPTURED','REQUEST_MARKET_UNAVAILABLE')
            else:
                origin,market=capture_inputs(root=root,symbol=task['profile']['symbol'],source_commit=source_commit)
                stores=_open(stack,root)
            phase='ORIGINAL_RISK'
            at=now_ms()
            delegated=task_context(task,policy=policy,registry=registry,now_ms=at,role='executor')
            demo_request_inputs.task_check(task,policy=policy,source_commit=source_commit,now_ms=at,**delegated)
            result=demo_request_inputs.build(orders=task['orders'],origin=origin,market_inputs=market,
                profile=task['profile'],issuer_policy=policy,kill_state=kill,
                **{k:stores[k] for k in ('dispatch','recovery','oms')},now_ms=at,
                **({'execution_constraints':task['execution_constraints']} if 'execution_constraints' in task else {}),
                **({'delegated_admission':task['delegated_admission'],**delegated} if delegated else {}))
            if delegated:
                recheck()
                require(demo_authority.load_registry_for_executor()==registry
                    and fixed_account()==(root,account),'REQUEST_DELEGATED_CONFIGURATION_CHANGED')
                final_at=now_ms()
                demo_request_inputs.task_check(task,policy=policy,source_commit=source_commit,now_ms=final_at,
                    **task_context(task,policy=policy,registry=registry,now_ms=final_at,role='executor'))
                demo_current_kill.require_expected(kill,now_ms=final_at)
        result.update(schema='DEMO_REQUEST_PREPARATION_V1',task_sha256=task_hash,source_commit=source_commit)
        phase='RESULT_PERSISTENCE';_retain(target,result)
        if delegated:
            # Proof replay and durable persistence may consume the last signal
            # milliseconds. Check the real clock without replaying again.
            require(at<=final_at<=now_ms()<task['valid_until_ms'],'REQUEST_DELEGATED_DELIVERY_EXPIRED')
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
