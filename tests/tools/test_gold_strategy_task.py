import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from live.execution import demo_request_inputs as builder
from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_strategy_constraints import case, constrained, NOW
from tools import demo_request_prepare as worker, demo_task
from tests.tools.test_demo_supervisor import child
from tools import demo_supervisor as supervisor


def task_for(args):
    return dict(schema='DEMO_GOLD_STRATEGY_REQUEST_TASK_V1', source_commit='c'*40,
        valid_from_ms=NOW, valid_until_ms=NOW+100, issuer_policy_sha256=_digest(args['issuer_policy']),
        profile=args['profile'], orders=args['orders'], kill_switch=args['kill_state'].model_dump(mode='json'),
        execution_constraints=args['execution_constraints'])


def test_original_task_contract_preserves_strategy_constraints(case):
    args = constrained(case); task = task_for(args)
    assert builder.task_check(task, policy=args['issuer_policy'], source_commit='c'*40, now_ms=NOW+10) == task
    built = builder.build(**args)
    demo_task.same_declarations(built['request'], task)
    changed = json.loads(json.dumps(built['request']))
    changed.pop('execution_constraints'); changed['schema'] = 'DEMO_EXECUTION_SESSION_INPUT_V1'
    with pytest.raises(ValueError): demo_task.same_declarations(changed, task)
    with pytest.raises(ValueError):
        builder.task_check(task, policy=args['issuer_policy'], source_commit='c'*40, now_ms=NOW+100)


def test_root_preparation_handoff_accepts_the_exact_strategy_request(case):
    args = constrained(case); task = task_for(args)
    bundle = dict(task=task, policy=args['issuer_policy'])
    result = builder.build(**args)
    result.update(schema='DEMO_REQUEST_PREPARATION_V1', task_sha256=_digest(task))
    process = child('import json,sys\n'+f'assert json.loads(sys.stdin.buffer.readline())=={bundle!r}\n'+
                    f'print({json.dumps(json.dumps(result))},flush=True)\n')
    try:
        actual = demo_task.prepare_exchange(process, bundle, deadline=time.monotonic()+5)
        assert actual == result
        demo_task.same_declarations(actual['request'], task)
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=2)
        process.stdin.close(); process.stdout.close()


def test_original_supervisor_claims_strategy_request_once_before_launch(case, tmp_path, monkeypatch):
    args = constrained(case)
    request = builder.build(**args)['request']
    identity = _digest(request)
    for name in ('claims','results'): (tmp_path/name).mkdir()
    monkeypatch.setattr(supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(supervisor, 'configuration', lambda _: (
        {'executor_uid':2001,'issuer_uid':2002},args['issuer_policy']))
    monkeypatch.setattr(supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(supervisor, 'read_request', lambda _: json.dumps(request).encode())
    monkeypatch.setattr(supervisor, 'now_ms', lambda: NOW+10)
    launches = []
    def launch(*a, **k):
        launches.append(1)
        raise OSError('synthetic child-launch interruption')
    monkeypatch.setattr(supervisor.subprocess, 'Popen', launch)
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k: None)
    first = supervisor.run(source_commit='c'*40, request_sha256=identity, purpose='submission')
    assert first['phase'] == 'HANDOFF' and first['claim_retained'] is True
    supervisor.run(source_commit='c'*40, request_sha256=identity, purpose='submission')
    assert launches == [1]


def test_original_executor_preparer_pipe_handles_strategy_task_and_expiry(case, tmp_path, monkeypatch):
    args = constrained(case); task = task_for(args)
    bundle = dict(task=task, policy=args['issuer_policy'])
    captures = []; clock = [NOW+10]; expire = [False]
    monkeypatch.setattr(worker, 'verify_source', lambda _: None)
    monkeypatch.setattr(worker, 'fixed_account', lambda: (tmp_path,args['issuer_policy']['account_ref_sha256']))
    monkeypatch.setattr(worker.os, 'getegid', lambda: 2001)
    monkeypatch.setattr(worker.os, 'getgroups', lambda: [2001])
    monkeypatch.setattr(worker, 'private_pipe', lambda _: None)
    monkeypatch.setattr(worker, 'now_ms', lambda: clock[0])
    monkeypatch.setattr(worker, '_open', lambda *a: {k:args[k] for k in ('dispatch','recovery','oms')})
    def capture(**kwargs):
        captures.append(kwargs['symbol'])
        if expire[0]: clock[0] = NOW+100
        return args['origin'], args['market_inputs']
    monkeypatch.setattr(worker, 'capture_inputs', capture)
    def call():
        read, write = os.pipe(); out_read, out_write = os.pipe()
        def feed():
            with os.fdopen(write, 'wb') as channel: channel.write(json.dumps(bundle).encode()+b'\n')
        def drain():
            with os.fdopen(out_read,'rb') as channel: return channel.read()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                feeder = pool.submit(feed); result = pool.submit(drain)
                with os.fdopen(out_write,'w') as output:
                    code = worker.run(source_commit='c'*40, input_fd=read, output=output)
                feeder.result(timeout=3)
                return code, json.loads(result.result(timeout=3))
        finally: os.close(read)
    code, result = call()
    assert code == 0 and result['status'] == 'PREPARED_INPUT_ONLY'
    assert result['request']['execution_constraints'] == task['execution_constraints']
    demo_task.same_declarations(result['request'], task)
    expire[0] = True
    code, result = call()
    assert code == 3 and result['status'] == 'INPUTS_UNRESOLVED' and result['phase'] == 'ORIGINAL_RISK'
    assert 'request' not in result and captures == ['XAUUSDT','XAUUSDT']
    assert args['dispatch'].snapshot() == [] and args['oms'].orders() == []
