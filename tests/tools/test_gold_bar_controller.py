import json
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_demo_supervisor import child


@pytest.mark.parametrize('fresh', [False, True, 'auto'])
@pytest.mark.parametrize('ticks', [1, 2])
@pytest.mark.parametrize('continuous', [False, True])
def test_bar_controller_retains_before_callback_without_legacy_warmup(tmp_path, monkeypatch, ticks, continuous, fresh, rotate=False):
    from tools import gold_strategy_controller as controller
    from tools import demo_operating_bundle as installer
    from tests.tools.gold_controller_fixture import map_host_custody
    map_host_custody(tmp_path, monkeypatch)
    # Pipe fixtures supply current pilot I/O; native recovery tests use real enrollment.
    from tools import gold_feedback_recovery
    monkeypatch.setattr(gold_feedback_recovery, 'current_control', lambda **kw: (dict(kw['result']['decision']['pilot_control'], ends_at_ms=2**63-1)
        if kw['result']['decision'].get('pilot_control', {}).get('status') == 'ACTIVE'
        else kw['result']['decision'].get('pilot_control')))
    executor=tmp_path/'executor';executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer,'EXECUTOR',executor)
    bundle = dict(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1', source_commit='c'*40,
        observation_sha256='a'*64, trial_manifest_sha256='b'*64,
        archive_source_commit='e'*40, entry_decision_ms=123)
    if fresh:
        bundle = dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1', source_commit='c'*40,
            trial_manifest_sha256='b'*64, archive_source_commit='e'*40, history_sha256='a'*64,
            candidate='channel', seconds=30)
    if fresh == 'auto':
        bundle['schema'] = 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
        bundle['episode_id'] = bundle.pop('history_sha256')
    decision = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit='c'*40,
        bundle_sha256=_digest(bundle), account_ref_sha256='d'*64,
        status='HOLDING', submission_enabled=False, economic_authority=False)
    result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
        source_commit='c'*40, bundle_sha256=_digest(bundle), decision_sha256=_digest(decision),
        decision=decision, submission_enabled=False, economic_authority=False)
    program = 'import json,sys\n'
    expected_results = []
    active = dict(bundle)
    for ordinal in range(1, ticks+1):
        current = json.loads(json.dumps(result))
        if rotate:
            from tests.tools.test_gold_bar_rotation import outcome
            from tools.gold_bar_rotation import _next
            current = outcome(active)
        program += f'assert json.loads(sys.stdin.buffer.readline())=={active!r}\n'
        program += f'print({json.dumps(json.dumps(current))},flush=True)\n'
        expected_results.append(current)
        if rotate:
            active = _next(bundle, active, current, 'd'*64, ordinal)
    process = child(program)
    monkeypatch.setattr(controller.supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(controller.supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(controller, 'observation_configuration', lambda _: ({'executor_uid':os.getuid()}, {'account_ref_sha256':'d'*64}))
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json', lambda path: json.dumps(bundle).encode() if path.name == 'strategy-runtime.json' else path.read_bytes())
    monkeypatch.setattr(controller.demo_task, 'preparation_command',
        lambda **kw: ['tools.demo_request_prepare', 'RuntimeMaxSec=155s'])
    launches = []
    monkeypatch.setattr(controller.subprocess, 'Popen', lambda command, **kw: (launches.append(command) or process))
    monkeypatch.setattr(controller.subprocess, 'run', lambda *a, **kw: None)
    received = []
    def callback(value):
        assert list((tmp_path/'strategy-decisions').iterdir())
        received.append(value)
        return True
    try:
        actual = controller.run(source_commit='c'*40, ticks=ticks,
            **({'on_decision':callback} if continuous else {}))
        assert actual['status'] == ('DECISION_RETAINED' if ticks == 1 else 'BOUNDED_SESSION_COMPLETE')
        assert len(launches) == 1
        if ticks > 1 or continuous:
            assert launches[0][-2:] == ['--frames', str(ticks)]
        assert received == (expected_results if continuous else [])
        if rotate:
            from tools.gold_bar_rotation import load
            assert load(root=tmp_path, base=bundle, account='d'*64) == active
        assert process.returncode == 0
        assert not (tmp_path/'tasks').exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()


def test_auto_controller_advances_and_restores_next_episode(tmp_path, monkeypatch):
    test_bar_controller_retains_before_callback_without_legacy_warmup(
        tmp_path, monkeypatch, ticks=2, continuous=True, fresh='auto', rotate=True)
