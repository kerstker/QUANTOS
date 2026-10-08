import json
import os
import time

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_demo_supervisor import child
from tests.live.test_gold_demo_issuer import case as case


@pytest.fixture(autouse=True)
def isolated_executor_root(tmp_path,monkeypatch):
    from tools import demo_operating_bundle as installer
    from tests.tools.gold_controller_fixture import map_host_custody
    map_host_custody(tmp_path, monkeypatch)
    executor=tmp_path/'isolated-executor';executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer,'EXECUTOR',executor)


@pytest.mark.parametrize('feedback', [None, 'WAITING_STRATEGY_CASH_WINDOW', 'STRATEGY_FEEDBACK_RETAINED', 'BAR_STRATEGY_FEEDBACK_RETAINED'])
@pytest.mark.parametrize('tamper', [False, True])
def test_controller_receives_bound_retained_decision_over_real_pipe(tamper, feedback, tmp_path, monkeypatch):
    from tools import gold_strategy_controller as controller
    bundle = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit='c'*40,
                  observation_sha256='a'*64, trial_manifest_sha256='b'*64)
    decision = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit='c'*40,
        bundle_sha256=_digest(bundle), account_ref_sha256='d'*64,
        status='DECISION_REQUIRES_APPROVAL', submission_enabled=False, economic_authority=False)
    result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
        source_commit='c'*40, bundle_sha256=_digest(bundle), decision_sha256=_digest(decision),
        decision=decision, submission_enabled=False, economic_authority=False)
    if feedback:
        decision['status'] = 'STRATEGY_EPISODE_SETTLED'
        result['decision_sha256'] = _digest(decision)
        result['feedback'] = dict(status=feedback, feedback_sha256='1'*64, artifact_sha256='2'*64,
                                  submission_enabled=False, economic_authority=False)
    if tamper: result['decision']['bundle_sha256'] = 'e'*64
    process = child('import json,sys\n'+f'assert json.loads(sys.stdin.buffer.readline())=={bundle!r}\n'+
                    f'print({json.dumps(json.dumps(result))},flush=True)\n')
    try:
        if tamper:
            with pytest.raises(ValueError):
                controller.exchange(process, bundle, account='d'*64, deadline=time.monotonic()+5)
        else:
            assert controller.exchange(process, bundle, account='d'*64, deadline=time.monotonic()+5) == result
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=2)
        process.stdin.close(); process.stdout.close()
    # Exercise the actual controller persistence path with an isolated child;
    # systemd and production configuration/ownership are explicit substitutes.
    process = child('import json,sys\n'+f'assert json.loads(sys.stdin.buffer.readline())=={bundle!r}\n'+
                    f'print({json.dumps(json.dumps(result))},flush=True)\n')
    monkeypatch.setattr(controller.supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(controller.supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(controller, 'observation_configuration', lambda _: ({'executor_uid':os.getuid()}, {'account_ref_sha256':'d'*64}))
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json', lambda _: json.dumps(bundle).encode())
    monkeypatch.setattr(controller.demo_task, 'preparation_command', lambda **kw: ['tools.demo_request_prepare'])
    monkeypatch.setattr(controller.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(controller.subprocess, 'run', lambda *a, **kw: None)
    delivered = []
    delivery_value = dict(status='RESEARCH_FEEDBACK_DELIVERED', feedback_sha256='1'*64,
                         cycle_sha256=_digest(bundle), source_verified=False,
                         submission_enabled=False, economic_authority=False)
    def deliver(**kwargs):
        delivered.append(kwargs)
        return delivery_value
    monkeypatch.setattr(controller, 'deliver_feedback', deliver)
    actual = controller.run(source_commit='c'*40)
    saved = list((tmp_path/'strategy-decisions').iterdir())
    if tamper:
        assert actual['status'] == 'INPUTS_UNRESOLVED' and saved == []
    else:
        expected = dict(result)
        if feedback in ('STRATEGY_FEEDBACK_RETAINED', 'BAR_STRATEGY_FEEDBACK_RETAINED'):
            assert len(delivered) == 1 and delivered[0]['cycle'] == _digest(bundle)
            expected['research_feedback'] = delivery_value
            receipt_files = list((tmp_path/'strategy-feedback-deliveries').iterdir())
            assert len(receipt_files) == 1 and json.loads(receipt_files[0].read_bytes()) == delivery_value
        else:
            assert delivered == []
        assert actual == expected and len(saved) == 1
        assert json.loads(saved[0].read_bytes()) == result
    assert not (tmp_path/'tasks').exists() and not (tmp_path/'claims').exists()


@pytest.mark.parametrize('bar', [False, True])
@pytest.mark.parametrize('damage', [None, 'cycle', 'feedback', 'authority', 'exit'])
def test_strategy_publication_uses_separate_process_and_checks_receipt(monkeypatch, damage, bar):
    from types import SimpleNamespace
    from tools import gold_strategy_controller as controller
    exported = dict(status='BAR_STRATEGY_FEEDBACK_RETAINED' if bar else 'STRATEGY_FEEDBACK_RETAINED', feedback_sha256='1'*64,
        artifact_sha256='2'*64, path='/untrusted/ignored', submission_enabled=False, economic_authority=False)
    receipt = dict(status='RESEARCH_FEEDBACK_DELIVERED', cycle_sha256='a'*64,
        feedback_sha256='1'*64, source_verified=False, submission_enabled=False, economic_authority=False)
    if damage == 'cycle': receipt['cycle_sha256'] = 'b'*64
    if damage == 'feedback': receipt['feedback_sha256'] = '3'*64
    if damage == 'authority': receipt['economic_authority'] = True
    def child(command, **kwargs):
        assert ('--bar' if bar else '--strategy') in command and 'tools.demo_feedback_publish' in command
        assert '/untrusted/ignored' not in command
        assert str(kwargs['cwd']) == '/opt/quantos/releases/'+'c'*40
        assert kwargs['stdin'] == controller.subprocess.DEVNULL
        return SimpleNamespace(returncode=3 if damage == 'exit' else 0, stdout=json.dumps(receipt).encode())
    monkeypatch.setattr(controller.subprocess, 'run', child)
    if damage:
        with pytest.raises(ValueError, match='STRATEGY_FEEDBACK_DELIVERY'):
            controller.deliver_feedback(source_commit='c'*40, cycle='a'*64, exported=exported)
    else:
        assert controller.deliver_feedback(source_commit='c'*40, cycle='a'*64, exported=exported) == receipt


@pytest.mark.parametrize('change_after_first,fresh,continuous', [(False,False,False), (True,False,False), (False,True,False), (False,True,True), (True,True,True), (False,True,'stop')])
def test_controller_warms_once_and_retains_two_decisions_from_one_process(tmp_path, monkeypatch, change_after_first, fresh, continuous):
    from tools import gold_strategy_controller as controller
    bundle = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit='c'*40,
                  observation_sha256='a'*64, trial_manifest_sha256='b'*64)
    if fresh:
        bundle.pop('observation_sha256')
        bundle.update(schema='GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1', plan={'synthetic_pipe_fixture':True})
    warm = dict(schema='GOLD_STRATEGY_WARMUP_INPUT_V1', source_commit='c'*40,
                trial_manifest_sha256='b'*64)
    warm_result = dict(schema='GOLD_STRATEGY_WARMUP_RESULT_V1', status='STRATEGY_PREPARED_IN_PROCESS',
        source_commit='c'*40, bundle_sha256=_digest(warm), strategy_definition_sha256='e'*64,
        submission_enabled=False, economic_authority=False)
    decision = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit='c'*40,
        bundle_sha256=_digest(bundle), account_ref_sha256='d'*64, strategy_definition_sha256='e'*64,
        status='HOLDING', submission_enabled=False, economic_authority=False)
    result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED', source_commit='c'*40,
        bundle_sha256=_digest(bundle), decision_sha256=_digest(decision), decision=decision,
        submission_enabled=False, economic_authority=False)
    program = 'import json,sys\n'
    messages = [(warm,warm_result), (bundle,result)] + ([] if fresh and not continuous else [(bundle,result)])
    for request, response in messages:
        program += f'assert json.loads(sys.stdin.buffer.readline())=={request!r}\n'
        program += f'print({json.dumps(json.dumps(response))},flush=True)\n'
    process = child(program)
    monkeypatch.setattr(controller.supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(controller.supervisor, '_root_private_directory', lambda _: None)
    checks = []; policy_epoch = [0]
    def configuration(_):
        checks.append(1)
        saved = tmp_path/'strategy-decisions'
        changed = change_after_first and saved.exists() and any(saved.iterdir())
        return {'executor_uid':os.getuid()}, {'account_ref_sha256':('f' if changed else 'd')*64,
                    'synthetic_policy_epoch':policy_epoch[0]}
    monkeypatch.setattr(controller, 'observation_configuration', configuration)
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json', lambda _: json.dumps(bundle).encode())
    monkeypatch.setattr(controller.demo_task, 'preparation_command',
                        lambda **kw: ['tools.demo_request_prepare', 'RuntimeMaxSec=155s'])
    launches = []
    monkeypatch.setattr(controller.subprocess, 'Popen', lambda command, **kw: (launches.append(command) or process))
    monkeypatch.setattr(controller.subprocess, 'run', lambda *a, **kw: None)
    received = []
    def on_decision(value):
        received.append(value)
        assert list((tmp_path/'strategy-decisions').iterdir())
        policy_epoch[0] += 1  # Independent policy may rotate between decisions.
        return continuous != 'stop'
    actual = controller.run(source_commit='c'*40, ticks=1 if fresh and not continuous else 2,
        **({'on_decision':on_decision, 'interval_seconds':0} if continuous else {}))
    if change_after_first:
        assert actual['status'] == 'INPUTS_UNRESOLVED'
    elif fresh and not continuous:
        assert actual == result
    else:
        assert actual['status'] == 'BOUNDED_SESSION_COMPLETE'
        assert len(actual['results']) == (1 if continuous == 'stop' else 2)
    assert len(launches) == 1 and launches[0][-2:] == ['--frames', '2' if fresh and not continuous else '3']
    if continuous:
        assert received == ([result] if change_after_first or continuous == 'stop' else [result, result])
    assert len(list((tmp_path/'strategy-decisions').iterdir())) == 1
    assert process.returncode is not None
    if not change_after_first and continuous != 'stop': assert process.returncode == 0


def test_observation_survives_policy_expiry_but_execution_does_not(case, tmp_path, monkeypatch):
    from tools import gold_strategy_controller as controller
    _, _, _, registry, policy, _ = case
    # Map this fixture's executor UID at the filesystem boundary only.
    # The real registry keeps its independently isolated actor UIDs.
    from tools import gold_feedback_legacy
    original_path_check = gold_feedback_legacy._check_path
    monkeypatch.setattr(gold_feedback_legacy, '_check_path',
        lambda path, uid, **kw: original_path_check(path, os.getuid() if uid == registry['executor_uid'] else uid, **kw))
    supervisor = controller.supervisor
    for name in ('requests', 'claims', 'results'):
        (tmp_path/name).mkdir(exist_ok=True)
    documents = {supervisor.EXECUTOR_REGISTRY: registry, supervisor.ISSUER_REGISTRY: registry,
                 supervisor.ISSUER_POLICY: policy}
    monkeypatch.setattr(supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(supervisor.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(supervisor, 'verify_source', lambda _: None)
    monkeypatch.setattr(supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(supervisor, '_read_root_owned_json', lambda p: json.dumps(documents[p]).encode())
    monkeypatch.setattr(supervisor, 'now_ms', lambda: policy['valid_until_ms']+1)
    source = policy['executor_release_commit']
    assert controller.observation_configuration(source) == (registry, policy)
    with pytest.raises(ValueError, match='ISSUER_POLICY_EXPIRED'):
        supervisor.configuration(source)
    bundle = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit=source,
                  observation_sha256='a'*64, trial_manifest_sha256='b'*64)
    documents[tmp_path/'strategy-runtime.json'] = bundle
    decision = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit=source,
        bundle_sha256=_digest(bundle), account_ref_sha256=policy['account_ref_sha256'],
        status='HOLDING', submission_enabled=False, economic_authority=False)
    result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
        source_commit=source, bundle_sha256=_digest(bundle), decision_sha256=_digest(decision),
        decision=decision, submission_enabled=False, economic_authority=False)
    process = child('import json,sys\n'+f'assert json.loads(sys.stdin.buffer.readline())=={bundle!r}\n'+
        f'print({json.dumps(json.dumps(result))},flush=True)\n')
    monkeypatch.setattr(controller.demo_task, 'preparation_command', lambda **kw: ['tools.demo_request_prepare'])
    launches = []
    monkeypatch.setattr(controller.subprocess, 'Popen', lambda *a, **kw: (launches.append(1) or process))
    monkeypatch.setattr(controller.subprocess, 'run', lambda *a, **kw: None)
    assert controller.run(source_commit=source) == result
    assert len(launches) == 1 and process.returncode == 0
    assert len(list((tmp_path/'strategy-decisions').iterdir())) == 1
    # The original submission entrypoint remains on the current-key check and
    # cannot launch any additional child or claim an order after expiry.
    refused = supervisor.run(source_commit=source, request_sha256='a'*64, purpose='submission')
    assert refused['status'] == 'INSPECT_ORIGINAL_ATTEMPT' and refused['claim_retained'] is False
    assert launches == [1] and list((tmp_path/'claims').iterdir()) == []
    # Observation still binds the exact release and matching identity mirrors.
    with pytest.raises(ValueError, match='SUPERVISOR_POLICY_RELEASE'):
        controller.observation_configuration('e'*40)
    documents[supervisor.ISSUER_REGISTRY] = {**registry, 'deployment_sha256': 'f'*64}
    with pytest.raises(ValueError, match='SUPERVISOR_REGISTRY_MIRROR'):
        controller.observation_configuration(source)
