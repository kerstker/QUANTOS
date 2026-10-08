"""Actual pilot/tick/controller pipes and native durable episode rotation.

Source, custody, worker observations and the external feedback publisher are
isolated fixtures. No installed state, credentials, systemd, network or orders.
"""

from copy import deepcopy
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_bar_rotation import bundle
from tools import gold_bar_rotation as rotation
from tools import gold_pilot_run as pilot
from tools import gold_strategy_controller as controller
from tools import gold_strategy_tick as tick


def worker_result(current, status, damage=None):
    decision = dict(
        schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit='c' * 40,
        bundle_sha256=_digest(current), account_ref_sha256='d' * 64,
        strategy_definition_sha256='e' * 64, status=status,
        position_flat=True, execution_history_empty=True,
        pilot_control=dict(status='ACTIVE', configuration_sha256='a' * 64,
                           submission_enabled=False),
        submission_enabled=False, economic_authority=False,
    )
    if damage == 'missing_pilot':
        decision.pop('pilot_control')
    elif damage == 'invalid_pilot':
        decision['pilot_control']['configuration_sha256'] = 'invalid'
    elif damage == 'pilot_submission':
        decision['pilot_control']['submission_enabled'] = True
    elif damage == 'unknown_pilot':
        decision['pilot_control']['status'] = 'UNKNOWN'
    elif damage == 'active_position':
        decision['position_flat'] = False
    elif damage == 'pending_approval':
        decision['status'] = 'DECISION_REQUIRES_APPROVAL'
    elif damage == 'recovery':
        decision['status'] = 'RECOVERY_REQUIRED'
    elif damage == 'stopped':
        decision['pilot_control']['status'] = 'STOPPED'
    result = dict(
        schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
        source_commit='c' * 40, bundle_sha256=_digest(current),
        decision_sha256=_digest(decision), decision=decision,
        submission_enabled=False, economic_authority=False,
    )
    if status == 'STRATEGY_EPISODE_SETTLED' and damage != 'undelivered':
        result['feedback'] = dict(
            status='BAR_STRATEGY_FEEDBACK_RETAINED',
            cash_reconciled=damage != 'unreconciled',
            feedback_sha256='f' * 64, artifact_sha256='b' * 64,
            submission_enabled=False, economic_authority=False,
        )
    return result


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from tools import demo_operating_bundle as installer
    from tests.tools.gold_controller_fixture import map_host_custody
    map_host_custody(tmp_path, monkeypatch)
    # Native control/enrollment is covered separately; this fixture owns pipe I/O.
    from tools import gold_feedback_recovery
    monkeypatch.setattr(gold_feedback_recovery, 'current_control', lambda **kw: (dict(kw['result']['decision']['pilot_control'], ends_at_ms=2**63-1)
        if kw['result']['decision'].get('pilot_control', {}).get('status') == 'ACTIVE'
        else kw['result']['decision'].get('pilot_control')))
    executor=tmp_path/'executor';executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer,'EXECUTOR',executor)
    state = tmp_path / 'controller'
    state.mkdir(mode=0o700)
    base = bundle()
    runtime_path = state / 'strategy-runtime.json'
    runtime_path.write_text(json.dumps(base))
    config = [dict(executor_uid=os.getuid()), dict(account_ref_sha256='d' * 64)]
    plans, seen, processes, published = [], [], [], []
    original_popen = subprocess.Popen

    def private(path):
        path.relative_to(tmp_path)
        assert not path.is_symlink()

    def read(path):
        private(path)
        return path.read_bytes()

    def launch(command, **kwargs):
        assert command[0] == 'tools.gold_strategy_worker'
        assert kwargs['cwd'] == '/'
        current = rotation.load(root=state, base=base, account='d' * 64, read_only=True)
        status, damage = plans.pop(0)
        result = worker_result(current, status, damage)
        # The actual legacy worker persists this decision before stdout handoff.
        from live.execution.demo_pre_submit import _retain
        _retain(executor/'strategy-decisions', result['decision'])
        frames = int(command[command.index('--frames') + 1])
        program = 'import json,sys\n'
        program += f'for _ in range({frames}):\n'
        program += f' assert json.loads(sys.stdin.buffer.readline()) == {current!r}\n'
        program += f' print({json.dumps(json.dumps(result))},flush=True)\n'
        process = original_popen(
            [sys.executable, '-I', '-B', '-u', '-c', program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
        processes.append(process)
        seen.append(current)
        return process

    def system_stop(command, **kwargs):
        assert command[:2] == ['systemctl', 'stop']
        return SimpleNamespace(returncode=0)

    def publish(*, source_commit, cycle, exported):
        assert source_commit == 'c' * 40
        receipt = dict(
            status='RESEARCH_FEEDBACK_DELIVERED', cycle_sha256=cycle,
            feedback_sha256=exported['feedback_sha256'], source_verified=False,
            submission_enabled=False, economic_authority=False,
        )
        published.append(receipt)
        return receipt

    monkeypatch.setattr(controller.supervisor, 'STATE', state)
    monkeypatch.setattr(controller.supervisor, '_root_private_directory', private)
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json', read)
    monkeypatch.setattr(controller, 'observation_configuration', lambda _: deepcopy(tuple(config)))
    monkeypatch.setattr(controller.demo_task, 'preparation_command',
                        lambda **kw: ['tools.demo_request_prepare', 'RuntimeMaxSec=155s'])
    monkeypatch.setattr(controller.subprocess, 'Popen', launch)
    monkeypatch.setattr(controller.subprocess, 'run', system_stop)
    monkeypatch.setattr(controller, 'deliver_feedback', publish)
    monkeypatch.setattr(tick.dispatcher, 'run',
                        lambda **kw: pytest.fail('rotation attempted dispatch'))
    clock = [0.0]
    monkeypatch.setattr(pilot, 'time', SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    ))
    value = SimpleNamespace(state=state, base=base, runtime_path=runtime_path,
                            config=config, plans=plans, seen=seen, published=published,
                            processes=processes)
    yield value
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        for channel in (process.stdin, process.stdout):
            channel.close()


def records(runtime):
    return list((runtime.state / 'strategy-bar-rotation' / _digest(runtime.base)).glob('*.json'))


@pytest.mark.parametrize('status', ['NO_ENTRY_SIGNAL', 'STRATEGY_EPISODE_SETTLED'])
def test_pilot_next_cycle_consumes_durable_next_episode(runtime, status):
    runtime.plans.extend([(status, None), ('HOLDING', None)])
    result = pilot.run(source_commit='c' * 40, max_seconds=2, interval_seconds=1)
    assert result['cycles'] == 2 and result['status'] == 'SUPERVISOR_BUDGET_EXHAUSTED'
    first, following = runtime.seen
    assert following['episode_id'] != first['episode_id']
    assert {k: v for k, v in following.items() if k != 'episode_id'} == {
        k: v for k, v in first.items() if k != 'episode_id'}
    assert rotation.load(root=runtime.state, base=runtime.base, account='d' * 64,
                         read_only=True) == following
    assert len(records(runtime)) == 1
    saved = json.loads(records(runtime)[0].read_bytes())
    assert saved['result']['decision']['strategy_definition_sha256'] == 'e' * 64
    assert saved['result']['decision']['pilot_control']['configuration_sha256'] == 'a' * 64
    assert bool(runtime.published) is (status == 'STRATEGY_EPISODE_SETTLED')
    assert all(process.returncode == 0 for process in runtime.processes)
    assert not (runtime.state / 'tasks').exists()


@pytest.mark.parametrize('damage', [
    'missing_pilot', 'invalid_pilot', 'pilot_submission', 'unknown_pilot',
    'active_position', 'pending_approval', 'recovery', 'stopped',
    'undelivered', 'unreconciled',
])
def test_pilot_keeps_original_episode_when_rotation_is_not_permitted(runtime, damage):
    status = 'STRATEGY_EPISODE_SETTLED' if damage in ('undelivered', 'unreconciled') else 'NO_ENTRY_SIGNAL'
    runtime.plans.append((status, damage))
    result = pilot.run(source_commit='c' * 40, max_seconds=1, interval_seconds=1)
    assert result['cycles'] == 1
    assert records(runtime) == []
    assert rotation.load(root=runtime.state, base=runtime.base, account='d' * 64,
                         read_only=True) == runtime.base
    assert not (runtime.state / 'tasks').exists()


@pytest.mark.parametrize('ticks', [1, 2])
@pytest.mark.parametrize('change', ['runtime', 'registry', 'account'])
def test_stopped_callback_rechecks_original_configuration_before_rotation(runtime, monkeypatch, ticks, change):
    runtime.plans.append(('NO_ENTRY_SIGNAL', None))
    original = tick.handle_observation

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'runtime':
            runtime.runtime_path.write_text(json.dumps(dict(runtime.base, seconds=31)))
        elif change == 'registry':
            runtime.config[0]['executor_uid'] = 2003
        else:
            runtime.config[1]['account_ref_sha256'] = 'a' * 64
        return result

    monkeypatch.setattr(tick, 'handle_observation', changed)
    result = tick.run_loop(source_commit='c' * 40, max_ticks=ticks,
                           interval_seconds=1, require_pilot=True)
    assert result['status'] == 'OBSERVATION_REQUIRES_ATTENTION'
    assert records(runtime) == []


@pytest.mark.parametrize('status', ['NO_ENTRY_SIGNAL', 'STRATEGY_EPISODE_SETTLED'])
def test_early_callback_stops_worker_frames_but_preserves_eligible_rotation(runtime, status):
    runtime.plans.append((status, None))
    result = tick.run_loop(source_commit='c' * 40, max_ticks=2,
                           interval_seconds=1, require_pilot=True)
    assert len(result['ticks']) == 1 and result['tick_budget_exhausted'] is False
    assert len(records(runtime)) == 1
    assert len(runtime.seen) == 1 and runtime.processes[0].poll() is not None
    assert not (runtime.state / 'tasks').exists()
