import hashlib
import json
import os
import time
import pytest
from concurrent.futures import ThreadPoolExecutor

from tests.live.test_gold_demo_request_session import case, NOW
from tests.tools.test_gold_forward_review import captured
from tests.tools.test_gold_strategy_entry import origin_at


@pytest.mark.parametrize('pilot_enrolled', [False, True])
def test_worker_retains_exact_decision_before_pipe_delivery(case, tmp_path, monkeypatch, pilot_enrolled, request):
    from tools import gold_strategy_worker as worker
    _, values, _, registry, _, _ = case
    with monkeypatch.context() as clock:
        clock.setattr(time, 'time_ns', lambda: (NOW-4011)*1000000)
        observation, trial, _ = captured(tmp_path, monkeypatch)
    from tools.gold_strategy_publish import publish
    root = tmp_path/'executor'
    observation_id = hashlib.sha256((observation/'result.json').read_bytes()).hexdigest()
    trial_id = hashlib.sha256((trial/'manifest.json').read_bytes()).hexdigest()
    published = publish(observation, strategy_trial=trial, output_directory=root, source_commit='c'*40)
    bundle = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit='c'*40,
                  observation_sha256=observation_id, trial_manifest_sha256=trial_id)
    assert published['runtime'] == bundle
    if pilot_enrolled:
        from tests.live.test_demo_pre_submit import stores
        replacement_stores = stores(root)
        for store in replacement_stores:
            request.addfinalizer(store.close)
        values = dict(values, **dict(zip(('dispatch', 'recovery', 'oms'), replacement_stores)))
    uid = root.stat().st_uid
    monkeypatch.setattr(worker, 'verify_source', lambda _: None)
    monkeypatch.setattr(worker, 'fixed_account', lambda: (root, 'a'*64))
    monkeypatch.setattr(worker.demo_authority, 'load_registry_for_executor', lambda: dict(registry, executor_uid=uid))
    monkeypatch.setattr(worker.os, 'geteuid', lambda: uid)
    monkeypatch.setattr(worker.os, 'getegid', lambda: uid)
    monkeypatch.setattr(worker.os, 'getgroups', lambda: [uid])
    monkeypatch.setattr(worker, 'private_pipe', lambda _: None)
    monkeypatch.setattr(worker, 'require_custody', lambda: root)
    monkeypatch.setattr(worker, '_open', lambda *a: values)
    clock = [NOW+10]
    monkeypatch.setattr(worker, 'now_ms', lambda: clock[0])
    origin = origin_at(NOW)
    monkeypatch.setattr(worker, 'fixed_account', lambda: (root, origin['account_ref_sha256']))
    if pilot_enrolled:
        from live.execution import demo_position_context as position
        monkeypatch.setattr(position, '_ROOT', root)
        monkeypatch.setattr(position, '_custody', lambda *a, **k: None)
        (root/'gold-pilot-request.json').write_text(json.dumps(dict(
            schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1', source_commit='c'*40,
            account_ref_sha256=origin['account_ref_sha256'], duration_seconds=86400,
            max_loss_fraction='0.05', submission_enabled=False)))
    captures = []
    monkeypatch.setattr(worker, 'capture_origin', lambda **kw: (captures.append(kw) or origin))
    worker.strategy_inputs(root, bundle, source_commit='c'*40, uid=uid)

    def call():
        read, write = os.pipe(); out_read, out_write = os.pipe()
        def feed():
            with os.fdopen(write, 'wb') as channel: channel.write(json.dumps(bundle).encode()+b'\n')
        def drain():
            with os.fdopen(out_read, 'rb') as channel: return channel.read()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                feeder = pool.submit(feed); result = pool.submit(drain)
                with os.fdopen(out_write, 'w') as output:
                    code = worker.run(source_commit='c'*40, input_fd=read, output=output)
                feeder.result(timeout=3)
                return code, json.loads(result.result(timeout=3))
        finally: os.close(read)

    code, first = call()
    assert code == 0 and first['decision']['status'] == 'DECISION_REQUIRES_APPROVAL', first
    if pilot_enrolled:
        config = json.loads((root/'gold-pilot'/'configuration.json').read_bytes())
        assert config['started_at_ms'] == NOW+10
        assert first['decision']['pilot_control']['status'] == 'ACTIVE'
    path = root/'strategy-decisions'/(first['decision_sha256']+'.json')
    assert json.loads(path.read_bytes()) == first['decision']
    assert first['decision']['draft']['origin_sha256'] == worker._digest(origin)
    assert values['dispatch'].snapshot() == [] and values['oms'].orders() == []
    code, again = call()
    assert code == 0 and again == first
    assert len(list((root/'strategy-decisions').iterdir())) == 1
    prepared_type = worker.PreparedStrategy
    preparations = []
    def prepare_once(*args, **kwargs):
        preparations.append(1)
        return prepared_type(*args, **kwargs)
    monkeypatch.setattr(worker, 'PreparedStrategy', prepare_once)
    read, write = os.pipe(); out_read, out_write = os.pipe()
    warmup = dict(schema='GOLD_STRATEGY_WARMUP_INPUT_V1', source_commit='c'*40,
                  trial_manifest_sha256=trial_id)
    def client():
        results = []
        with os.fdopen(write, 'wb') as send, os.fdopen(out_read, 'rb') as receive:
            # Strict request/response framing: never pipeline two requests into
            # the original one-frame reader or bypass its framing checks.
            for message in (warmup, bundle, bundle):
                send.write(json.dumps(message).encode()+b'\n'); send.flush()
                results.append(json.loads(receive.readline()))
                if len(results) == 1:
                    assert len(captures) == 2
        return results
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(client)
            with os.fdopen(out_write, 'w') as output:
                code = worker.serve(source_commit='c'*40, frames=3, input_fd=read, output=output)
            replies = result.result(timeout=3)
    finally: os.close(read)
    assert code == 0 and replies[0]['status'] == 'STRATEGY_PREPARED_IN_PROCESS'
    assert replies[1:] == [first, first] and preparations == [1]
    assert len(captures) == 4 and len(list((root/'strategy-decisions').iterdir())) == 1
    with monkeypatch.context() as settled_tick:
        settled_tick.setattr(worker.gold_strategy_cycle, 'prepare', lambda *a, **kw: dict(
            schema='GOLD_STRATEGY_CYCLE_DECISION_V1', status='STRATEGY_EPISODE_SETTLED',
            submission_enabled=False, economic_authority=False, cash_reconciled=False))
        code, settled = call()
        assert code == 0 and settled['feedback']['status'] == 'WAITING_STRATEGY_CASH_WINDOW'
        assert settled['decision']['cash_reconciled'] is False
    retain = worker._retain
    def delayed_persistence(*args, **kwargs):
        result = retain(*args, **kwargs)
        clock[0] = first['decision']['draft']['valid_until_ms']
        return result
    monkeypatch.setattr(worker, '_retain', delayed_persistence)
    code, expired = call()
    assert code == 3 and expired['phase'] == 'HANDOFF_DEADLINE' and 'decision' not in expired
    assert values['dispatch'].snapshot() == []
    # Changing a retained source under the same content-addressed input cannot
    # reach account capture or manufacture a replacement decision.
    (root/'strategy-observations'/observation_id/'result.json').write_text('{}')
    code, failed = call()
    assert code == 3 and failed['phase'] == 'STRATEGY_INPUT'
    assert len(captures) == 6 and 'decision' not in failed
