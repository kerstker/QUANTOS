"""Actual idle worker/private-pipe closure retains prior account history.

The process/pipe and original storage/observation algorithms run normally.
Systemd, OS role transitions, source acceptance and account transport are the
same explicit isolated boundaries as the settlement integration fixture.
"""
import json
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

from tests.live.test_gold_pilot_closure_integration import ForkWorker
from tools.demo_executor_origin import run as original_origin_run
from tests.tools.gold_controller_fixture import map_host_custody
from tests.tools.test_gold_pilot_closure import (
    idle_empty_case as idle_empty_case, opening_case as opening_case,
    idle_btc_case as idle_btc_case, idle_case as idle_case,
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, enrollment_case as enrollment_case,
)


@pytest.mark.parametrize('kind', ['empty', 'btc', 'legacy'])
def test_idle_original_history_closes_through_controller_worker_and_cold_restart(request, tmp_path, monkeypatch, kind):
    from tools import gold_strategy_controller as controller, gold_strategy_worker as worker
    from tools import gold_strategy_tick as tick, gold_pilot_run as runner
    from tools import demo_operating_bundle as installer, gold_pilot_closure_controller as terminal
    from tools.gold_feedback_recovery import FeedbackOwner
    from tools.gold_pilot_retention_store import canonical
    from live.execution.demo_pre_submit import _state
    f = request.getfixturevalue('idle_btc_case' if kind == 'btc' else 'idle_empty_case')
    if kind == 'legacy':
        # This no-execution branch never loads a strategy. Only the installed
        # legacy runtime shape is varied; enrollment and native empty ledgers
        # remain original, with no native retention marker to remove or relabel.
        f.base_runtime = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit=f.source_commit,
            trial_manifest_sha256=f.base_runtime['trial_manifest_sha256'], observation_sha256='e'*64)
        from tools import demo_executor_origin
        # The opening fixture isolates run() for its earlier opening tests.
        # This path must use the original collector and its actual 16 GETs.
        monkeypatch.setattr(demo_executor_origin, 'run', original_origin_run)
    f.controller_root = tmp_path/'idle-controller'
    f.controller_root.mkdir(mode=0o700)
    monkeypatch.setattr(os, 'geteuid', os.getuid)
    monkeypatch.setattr(os, 'getegid', os.getuid)
    monkeypatch.setattr(os, 'getgroups', lambda: [os.getuid()])
    map_host_custody(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, 'EXECUTOR', f.root)
    monkeypatch.setattr(controller.supervisor, 'STATE', f.controller_root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(worker, 'now_ms', lambda: f.clock[0])
    registry = dict(executor_uid=f.executor_uid)
    monkeypatch.setattr(controller, 'observation_configuration',
        lambda source: (registry, dict(account_ref_sha256=f.account)))
    # Isolate the worker's OS launch identity only. The partition reader keeps
    # the fixture's original complete registry and its ordinary validation.
    monkeypatch.setattr(worker, 'demo_authority', SimpleNamespace(load_registry_for_executor=lambda: registry))
    monkeypatch.setattr(worker, 'verify_source', lambda source: None if source == f.source_commit else pytest.fail('source'))
    monkeypatch.setattr(worker, 'fixed_account', lambda: (f.root, f.account))
    monkeypatch.setattr(worker, 'require_custody', lambda: f.root)
    def private(fd):
        info = os.fstat(fd)
        assert stat.S_ISFIFO(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
    monkeypatch.setattr(worker, 'private_pipe', private)
    from tools.gold_pilot_install import _read_target
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json',
        lambda path: _read_target(path, uid=os.getuid()))
    path = f.controller_root/'strategy-runtime.json'
    path.write_bytes(canonical(f.base_runtime)); path.chmod(0o600)
    children = []
    def launch(command, **kwargs):
        assert command[0] == 'systemd-run' and 'RuntimeMaxSec=450s' in command
        assert command[command.index('-m')+1] == 'tools.gold_strategy_worker'
        child = ForkWorker(f.source_commit); children.append(child)
        return child
    monkeypatch.setattr(terminal.subprocess, 'Popen', launch)
    monkeypatch.setattr(terminal.subprocess, 'run', lambda command, **kw:
        subprocess.CompletedProcess(command, 0) if command[:2] == ['systemctl', 'stop'] else pytest.fail('unexpected process'))
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: pytest.fail('idle published feedback'))
    monkeypatch.setattr(controller, '_run_observation', lambda **kw: pytest.fail('idle captured a new strategy'))
    before = _state(**f.stores)
    f.clock[0] = max(f.clock[0], f.window['ends_at_ms']+1000)
    with FeedbackOwner() as owner:
        found = tick.run_loop(source_commit=f.source_commit, require_pilot=True, feedback_owner=owner)
        assert found['status'] == 'PILOT_CLOSED_WITHOUT_EXECUTION', found
        observation = found['ticks'][0]['observation']
        assert 'feedback' not in observation and 'research_feedback' not in observation
        assert observation['original_pilot_window'] == f.window
        assert observation['decision']['execution_history_empty'] is (kind != 'btc')
        assert observation['idle_evidence']['observed_equity'] == ('4200' if kind == 'btc' else '5000')
        view = owner.discover(current_runtime=f.base_runtime, terminal=True)
        assert view['status'] == 'IDLE_CLOSURE'
        assert view['closure']['status'] == 'COMPLETE' and view['closure']['result'] == observation
        if kind == 'legacy':
            assert view['controller_checkpoint'] is view['executor_checkpoint'] is None
            assert view['closure']['claim'] is None
            assert len(list((f.root/'strategy-decisions').glob('*.json'))) == 1
        else:
            assert view['controller_checkpoint']['frames'] == view['executor_checkpoint']['frames'] == 1
            assert sum(json.loads(raw)['status'] == 'STRATEGY_EPISODE_SETTLED' for raw, in
                owner.retention._feedback_source.conn.execute("SELECT payload FROM records WHERE kind='decision'")) == 0
        owner.release()
        assert _state(**f.stores) == before
    complete = runner.run(source_commit=f.source_commit, max_seconds=60)
    assert complete['pilot_complete'] is True and complete['status'] == 'PILOT_STOPPED', complete
    assert len(children) == 2 and all(child.returncode == 0 for child in children)
    assert _state(**f.stores) == before
    with FeedbackOwner() as owner:
        owner.bind(root=f.controller_root, base_runtime=f.base_runtime, source_commit=f.source_commit,
            account=f.account, registry=registry)
        view = owner.discover(current_runtime=f.base_runtime, terminal=True)
        if kind == 'legacy':
            assert view['closure']['result'] == observation
            assert len(list((f.root/'strategy-decisions').glob('*.json'))) == 1
        else:
            assert view['executor_checkpoint']['frames'] == 1
