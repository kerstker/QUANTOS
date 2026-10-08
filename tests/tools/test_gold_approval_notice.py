"""Retained approval notices across the real pipe and controller lease.

Only synthetic signed Scope, worker observations and private temporary state
are used. No installed state, credentials, systemd, network or orders.
"""

from copy import deepcopy
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_scope_decision import (
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, decision as decision,
    with_original_window, retain_worker_original, isolated_retention_custody,
    isolated_executor_custody,
)
from tools import gold_pilot_run as pilot
from tools import gold_strategy_controller as controller
from tools import gold_strategy_tick as tick


@pytest.fixture
def runtime(decision, tmp_path, monkeypatch):
    state = tmp_path / 'controller'
    state.mkdir(mode=0o700)
    bundle = deepcopy(decision['runtime'])
    observed = deepcopy(decision['result'])
    observed['decision'].update(pilot_control=deepcopy(observed['decision']['draft']['pilot_control']),
                                position_flat=True, execution_history_empty=True)
    observed['decision_sha256'] = _digest(observed['decision'])
    observed=with_original_window(decision,observed)
    (state / 'strategy-runtime.json').write_text(json.dumps(bundle))
    config = deepcopy((decision['registry'], decision['scope']))
    clock = [0.0]
    processes = []
    value = SimpleNamespace(state=state, bundle=bundle, observed=observed, config=config,
                            clock=clock, processes=processes, exit_code=0, extra_output=False)
    from tools import demo_operating_bundle as installer
    monkeypatch.setattr(installer,'EXECUTOR',decision['root'])
    isolated_retention_custody(decision['root'],decision['registry'],tmp_path,monkeypatch)
    isolated_executor_custody(decision, tmp_path, monkeypatch)
    original_popen = subprocess.Popen

    def private(path):
        path.relative_to(tmp_path)
        assert not path.is_symlink()

    def read(path):
        private(path)
        return path.read_bytes()

    def launch(command, **kwargs):
        assert command[0] == 'tools.gold_strategy_worker'
        frames = int(command[command.index('--frames') + 1])
        # This wire fixture replays one durable original in multiple frames;
        # no fake frame can bypass the native executor retention reader.
        retain_worker_original(decision,value.observed,tmp_path=tmp_path,monkeypatch=monkeypatch)
        program = '''import json,os,select,sys,time
def read_frame():
 data=bytearray();deadline=time.monotonic()+5
 while b'\\n' not in data:
  remaining=deadline-time.monotonic()
  assert remaining>0 and select.select([sys.stdin.fileno()],[],[],remaining)[0]
  chunk=os.read(sys.stdin.fileno(),65536)
  assert chunk and len(data)+len(chunk)<=65536
  data.extend(chunk)
 assert data.endswith(b'\\n') and data.count(b'\\n')==1
 return json.loads(data)
'''
        program += f'for _ in range({frames}):\n'
        program += f' assert read_frame() == {bundle!r}\n'
        program += f' print({json.dumps(json.dumps(value.observed))},flush=True)\n'
        if value.extra_output:
            program += 'print("unexpected trailing frame",flush=True)\n'
        program += f'sys.exit({value.exit_code})\n'
        process = original_popen([sys.executable, '-I', '-B', '-u', '-c', program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        processes.append(process)
        return process

    def system_stop(command, **kwargs):
        assert command[:2] == ['systemctl', 'stop']
        return SimpleNamespace(returncode=0)

    fake_time = SimpleNamespace(monotonic=lambda: clock[0],
        time_ns=lambda: (decision['at'] + round(clock[0] * 1000)) * 1_000_000,
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(pilot, 'time', fake_time)
    monkeypatch.setattr(tick, 'time', fake_time)
    monkeypatch.setattr(controller.supervisor, 'STATE', state)
    monkeypatch.setattr(controller.supervisor, '_root_private_directory', private)
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json', read)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: fake_time.time_ns() // 1_000_000)
    monkeypatch.setattr(controller, 'observation_configuration', lambda _: deepcopy(config))
    monkeypatch.setattr(controller.demo_task, 'preparation_command',
                        lambda **kw: ['tools.demo_request_prepare', 'RuntimeMaxSec=155s'])
    monkeypatch.setattr(controller.subprocess, 'Popen', launch)
    monkeypatch.setattr(controller.subprocess, 'run', system_stop)
    monkeypatch.setattr(tick.dispatcher, 'run', lambda **kw: pytest.fail('unexpected dispatch'))
    yield value
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        for channel in (process.stdin, process.stdout):
            channel.close()


def check_notice(runtime, notice):
    assert notice['schema'] == 'GOLD_STRATEGY_APPROVAL_NOTICE_V1'
    assert notice['status'] == 'INDEPENDENT_APPROVAL_REQUIRED'
    assert notice['source_commit'] == 'c' * 40
    assert notice['worker_exit_verified'] is True
    assert notice['controller_shared_lease_released'] is True
    assert runtime.processes[-1].poll() == 0
    with controller.supervisor.controller_lease(maintenance=True):
        from tools.gold_pilot_retention import read_controller_approval,read_controller_result
        account=runtime.config[1]['account_ref_sha256']
        request = json.loads(read_controller_approval(runtime.state,notice['stored_request_sha256'],
            source_commit='c'*40,account=account))
        assert notice['stored_request_sha256'] == _digest(request)
        assert notice['stored_request_sha256'] != request['request_sha256']
        assert notice['request_sha256'] == request['request_sha256']
        assert notice['result_sha256'] == request['result_sha256'] == _digest(runtime.observed)
        assert notice['valid_until_ms'] == request['valid_until_ms']
        assert notice['constraints_sha256'] == request['constraints_sha256']
        assert notice['budget_group_binding_sha256'] == request['budget_group_binding_sha256']
        assert request['budget_group'] == runtime.config[1]['budget_group']
        saved = json.loads(read_controller_result(runtime.state,_digest(runtime.observed),
            source_commit='c'*40,account=account))
        assert saved == runtime.observed
    assert all(notice[key] is False for key in ('approval_granted', 'submission_enabled', 'economic_authority'))
    assert not (runtime.state / 'tasks').exists()
    assert not (runtime.state / 'task-claims').exists()


def retained_approval_count(runtime):
    from tools.gold_pilot_retention import _reader
    with _reader(runtime.state,source_commit='c'*40,
            account=runtime.config[1]['account_ref_sha256']) as reader:
        return reader.conn.execute("SELECT COUNT(*) FROM records WHERE kind='approval'").fetchone()[0]


def test_pilot_cli_flushes_real_request_notice_before_dispatch_wait(runtime, monkeypatch):
    rows = []
    dispatches = []

    def output(body, *, flush=False):
        row = json.loads(body)
        if row['schema'] == 'GOLD_STRATEGY_APPROVAL_NOTICE_V1':
            assert flush is True
            check_notice(runtime, row)
        rows.append(row)

    def dispatch(**kwargs):
        dispatches.append(kwargs)
        assert rows and rows[0]['schema'] == 'GOLD_STRATEGY_APPROVAL_NOTICE_V1'
        return dict(status='WAITING_INDEPENDENT_APPROVAL', task_sha256=None,
                    retry_submission=False, submission_enabled=False, economic_authority=False)

    monkeypatch.setattr(pilot, 'print', output, raising=False)
    monkeypatch.setattr(tick.dispatcher, 'run', dispatch)
    assert pilot.main(['--source-commit', 'c' * 40, '--execute-approved', '--max-seconds', '1',
                       '--interval-seconds', '1']) == 3
    assert dispatches and rows[0]['schema'] == 'GOLD_STRATEGY_APPROVAL_NOTICE_V1'
    assert sum(row['schema'] == 'GOLD_STRATEGY_APPROVAL_NOTICE_V1' for row in rows) == 1
    assert rows[-1]['last_session']['status'] == 'OBSERVED_ACTION_REQUIRES_INDEPENDENT_APPROVAL'
    assert rows[-1]['last_session']['ticks'][0]['observation'] == runtime.observed
    assert runtime.clock[0] < 5


@pytest.mark.parametrize('ticks', [1, 2])
def test_notice_follows_successful_final_frame_before_decision_callback(runtime, ticks):
    notices = []
    calls = []

    def notice(row):
        check_notice(runtime, row)
        notices.append(row)

    def handoff(observed):
        calls.append(observed)
        assert len(notices) == int(len(calls) == ticks)
        return True

    result = controller.run(source_commit='c' * 40, ticks=ticks, on_decision=handoff,
                            on_approval_request=notice)
    assert result['status'] in ('DECISION_RETAINED', 'BOUNDED_SESSION_COMPLETE')
    assert len(calls) == ticks and len(notices) == 1


def test_early_multiframe_stop_never_announces_an_unlocked_install_window(runtime):
    notices = []
    result = controller.run(source_commit='c' * 40, ticks=2, on_decision=lambda _: False,
                            on_approval_request=notices.append)
    assert result['status'] == 'BOUNDED_SESSION_COMPLETE'
    assert retained_approval_count(runtime)==1
    assert notices == []


def test_pilot_no_signal_emits_no_approval_notice(runtime):
    runtime.observed['decision']['status'] = 'NO_ENTRY_SIGNAL'
    runtime.observed['decision_sha256'] = _digest(runtime.observed['decision'])
    rows = []
    result = pilot.run(source_commit='c' * 40, execute_approved=True, max_seconds=1,
                       interval_seconds=1, on_progress=rows.append)
    assert result['last_session']['status'] == 'OBSERVATION_COMPLETE'
    assert [row['schema'] for row in rows] == ['GOLD_PILOT_SUPERVISOR_PROGRESS_V1']
    assert not (runtime.state / 'strategy-approval-requests').exists()
    assert retained_approval_count(runtime)==0


def test_notice_callback_failure_never_proceeds_to_dispatch(runtime):
    def failed(_):
        raise ValueError('fixture journal unavailable')
    result = tick.run_loop(source_commit='c' * 40, execute_approved=True, require_pilot=True,
                           on_approval_request=failed)
    assert result['status'] == 'OBSERVATION_REQUIRES_ATTENTION'
    assert result['ticks'][0]['observation']['phase'] == 'INDEPENDENT_APPROVAL_NOTICE'
    assert retained_approval_count(runtime)==1
    assert not (runtime.state / 'tasks').exists()


@pytest.mark.parametrize('failure', ['exit', 'trailing_output', 'persistence'])
def test_failed_worker_or_request_retention_emits_no_notice(runtime, monkeypatch, failure):
    if failure == 'exit':
        runtime.exit_code = 3
    elif failure == 'trailing_output':
        runtime.extra_output = True
    else:
        def failed(*args, **kwargs):
            raise ValueError('fixture retention failure')
        monkeypatch.setattr(controller, 'retain_approval_request', failed)
    notices = []
    result = controller.run(source_commit='c' * 40, on_approval_request=notices.append)
    assert result['status'] == 'INPUTS_UNRESOLVED'
    assert notices == []
    assert not (runtime.state / 'tasks').exists()


def maintenance_wait(**changes):
    return dict(dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1', status='WAITING_CONTROLLER_MAINTENANCE',
        phase='CONTROLLER_LEASE', source_commit='c' * 40, task_sha256=None,
        retry_submission=False, submission_enabled=False, economic_authority=False), **changes)


@pytest.fixture
def waiting(monkeypatch):
    clock = [1000]
    observed = dict(status='DECISION_RETAINED', decision=dict(status='DECISION_REQUIRES_APPROVAL',
        execution_constraints={'valid_until_ms': 1250}))
    calls = []
    responses = []
    monkeypatch.setattr(tick, 'time', SimpleNamespace(monotonic=lambda: clock[0] / 1000,
        time_ns=lambda: clock[0] * 1_000_000,
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + round(seconds * 1000))))

    def dispatch(**kwargs):
        assert clock[0] < observed['decision']['execution_constraints']['valid_until_ms']
        calls.append(kwargs)
        result = responses.pop(0) if responses else maintenance_wait()
        if result.get('status') == 'WAITING_CONTROLLER_MAINTENANCE':
            result.setdefault('result_sha256', _digest(observed))
        return result

    monkeypatch.setattr(tick.dispatcher, 'run', dispatch)
    return SimpleNamespace(clock=clock, observed=observed, calls=calls, responses=responses)


def test_exact_preclaim_maintenance_wait_can_reach_original_approved_dispatch(waiting):
    waiting.responses.extend([maintenance_wait(), {'status': 'WAITING_INDEPENDENT_APPROVAL'},
                              {'status': 'EXECUTOR_RESULT_RECORDED'}])
    result = tick.handle_observation(waiting.observed, source_commit='c' * 40, execute_approved=True)
    assert result['status'] == 'APPROVED_TASK_HANDOFF_COMPLETE'
    assert len(waiting.calls) == 3
    assert all(call['result_sha256'] == _digest(waiting.observed) for call in waiting.calls)


@pytest.mark.parametrize('until', [1000, 1250, 61000])
def test_maintenance_wait_obeys_original_deadline_and_thirty_second_budget(waiting, until):
    waiting.observed['decision']['execution_constraints']['valid_until_ms'] = until
    result = tick.handle_observation(waiting.observed, source_commit='c' * 40, execute_approved=True)
    assert result['status'] == 'OBSERVED_ACTION_REQUIRES_INDEPENDENT_APPROVAL'
    assert waiting.clock[0] == min(until, 31000)
    assert bool(waiting.calls) is (until > 1000)
    assert result['execution']['task_sha256'] is None


@pytest.mark.parametrize('changes', [
    {'phase': 'ORIGINAL_TASK_MATERIALIZATION'}, {'task_sha256': 'a' * 64},
    {'retry_submission': True}, {'submission_enabled': True}, {'economic_authority': True},
    {'status': 'INPUTS_UNRESOLVED'}, {'phase': None},
    {'schema': 'UNKNOWN'}, {'source_commit': 'd' * 40}, {'result_sha256': 'e' * 64},
])
def test_ambiguous_or_postclaim_errors_are_not_retried(waiting, changes):
    waiting.responses.append(maintenance_wait(**changes))
    result = tick.handle_observation(waiting.observed, source_commit='c' * 40, execute_approved=True)
    assert result['status'] == 'DISPATCH_REQUIRES_ATTENTION'
    assert len(waiting.calls) == 1 and waiting.clock[0] == 1000
