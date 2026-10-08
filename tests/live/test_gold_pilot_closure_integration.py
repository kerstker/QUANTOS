"""Original AUTO cash through controller/private pipe/terminal executor.

Systemd and OS role transitions use a fork with real private pipes. The worker,
original account capture fixture, ledger reads, stop and both retention stores
run normally. This does not establish deployed custody or venue behavior.
"""
import json
import os
import select
import signal
import stat
import subprocess
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_feedback_recovery_fixture import (
    case as case, original_evidence as original_evidence, completed_bar_feedback as completed_bar_feedback,
)
from tests.tools.test_gold_pilot_closure import configure_closure
from tests.tools.gold_controller_fixture import map_host_custody
from tests.live.gold_legacy_closure_fixture import (
    legacy_case as legacy_case, completed_legacy_feedback as completed_legacy_feedback,
)


class ForkWorker:
    def __init__(self, source):
        reader, writer = os.pipe()
        out_reader, out_writer = os.pipe()
        self.returncode = None
        self.pid = os.fork()
        if self.pid == 0:
            os.close(writer); os.close(out_reader)
            try:
                from tools.gold_strategy_worker import run
                with os.fdopen(out_writer, 'w') as output:
                    code = run(source_commit=source, input_fd=reader, output=output)
                os.close(reader)
                os._exit(code)
            except BaseException:
                os._exit(91)
        os.close(reader); os.close(out_writer)
        self.stdin = os.fdopen(writer, 'wb', buffering=0)
        self.stdout = os.fdopen(out_reader, 'rb', buffering=0)
        self.pidfd = os.pidfd_open(self.pid)

    def poll(self):
        if self.returncode is None:
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                self.returncode = os.waitstatus_to_exitcode(status)
                os.close(self.pidfd)
        return self.returncode

    def wait(self, timeout=None):
        if self.poll() is None:
            if not select.select([self.pidfd], [], [], timeout)[0]:
                raise subprocess.TimeoutExpired('isolated-terminal-worker', timeout)
            self.poll()
        return self.returncode

    def terminate(self):
        if self.poll() is None:
            signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)

    def kill(self):
        if self.poll() is None:
            signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)


@pytest.mark.parametrize('kind,previous', [('bar',0), ('bar',1), ('bar',2), ('legacy',0)])
def test_delivered_native_cash_closes_via_original_worker_after_expiry(request, tmp_path, monkeypatch, kind, previous):
    from tools import gold_strategy_controller as controller, gold_strategy_worker as worker
    from tools import gold_strategy_tick as tick, gold_pilot_run as runner, gold_bar_rotation
    from tools import demo_operating_bundle as installer, gold_pilot_closure_controller as terminal
    from tools.gold_feedback_recovery import FeedbackOwner
    from tools.gold_pilot_retention_store import canonical
    from live.execution.demo_pre_submit import _state
    f = request.getfixturevalue('completed_bar_feedback' if kind == 'bar' else 'completed_legacy_feedback')
    configure_closure(f, monkeypatch)
    if kind == 'legacy':
        f.controller_root = tmp_path/'legacy-controller'
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
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: pytest.fail('republished completed feedback'))
    diagnostics = []
    read_frame = controller.supervisor.read_frame
    def checked_frame(*args, **kwargs):
        raw = read_frame(*args, **kwargs)
        row = json.loads(raw)
        if row.get('status') != 'DECISION_RETAINED':
            diagnostics.append(row)
        return raw
    monkeypatch.setattr(controller.supervisor, 'read_frame', checked_frame)
    finish = controller.finish_recovered_pilot
    def checked_finish(**kwargs):
        try:
            return finish(**kwargs)
        except Exception as exc:
            diagnostics.append((type(exc).__name__, str(exc)))
            raise
    monkeypatch.setattr(controller, 'finish_recovered_pilot', checked_finish)
    receipt = dict(status='RESEARCH_FEEDBACK_DELIVERED', cycle_sha256=_digest(f.base_runtime),
        feedback_sha256=f.exported['feedback_sha256'], source_verified=False,
        submission_enabled=False, economic_authority=False)
    before = _state(**f.stores)
    with FeedbackOwner() as owner:
        owner.bind(root=f.controller_root, base_runtime=f.base_runtime, source_commit=f.source_commit,
            account=f.account, registry=registry)
        if kind == 'legacy':
            # Legacy executor owns only the original decision. This is the
            # original controller's result write, not a fabricated executor copy.
            owner.retention.retain(f.result)
        delivered = owner.recover(current_runtime=f.base_runtime, deliver=lambda **kw: receipt)
        current = (gold_bar_rotation.advance(root=f.controller_root, base=f.base_runtime, account=f.account,
            current=f.base_runtime, result=delivered) if previous else f.base_runtime)
        if previous == 2:
            from tests.tools.test_gold_feedback_retention_readers import _native_no_entry_after_settlement
            no_entry = _native_no_entry_after_settlement(f, current)
            owner.retention.retain(no_entry); owner.release()
            current = gold_bar_rotation.advance(root=f.controller_root, base=f.base_runtime,
                account=f.account, current=current, result=no_entry)
        monkeypatch.setattr(terminal.subprocess, 'Popen', launch)
        monkeypatch.setattr(terminal.subprocess, 'run', lambda command, **kw:
            subprocess.CompletedProcess(command, 0) if command[:2] == ['systemctl', 'stop'] else pytest.fail('unexpected process'))
        f.clock[0] = f.result['original_pilot_window']['ends_at_ms']+1000
        found = tick.run_loop(source_commit=f.source_commit, require_pilot=True, feedback_owner=owner)
        assert found['status'] == 'PILOT_CLOSED_WITH_FEEDBACK', (found, diagnostics)
        observation = found['ticks'][0]['observation']
        assert observation['decision'] == f.result['decision']
        assert observation['research_feedback'] == receipt
        view = owner.discover(current_runtime=current, terminal=True)
        assert view['result_bytes'] == f.result_bytes
        assert view['closure']['status'] == 'COMPLETE'
        if kind == 'bar':
            assert view['claim'] == f.claim
            assert view['controller_checkpoint']['frames'] == view['executor_checkpoint']['frames'] == 4+(previous == 2)
            assert sum(json.loads(raw)['status'] == 'STRATEGY_EPISODE_SETTLED' for raw, in
                owner.retention._feedback_source.conn.execute("SELECT payload FROM records WHERE kind='decision'")) == 1
        else:
            assert view['claim'] is view['closure']['claim'] is None
            assert observation['pilot_closure']['original_result_provenance'] == 'CONTROLLER_ORIGINAL'
            assert sum(json.loads(path.read_bytes())['status'] == 'STRATEGY_EPISODE_SETTLED'
                for path in (f.root/'strategy-decisions').glob('*.json')) == 1
        owner.release()
        assert _state(**f.stores) == before
        if kind == 'bar':
            assert gold_bar_rotation.load(root=f.controller_root, base=f.base_runtime, account=f.account) == current
    completed = runner.run(source_commit=f.source_commit, max_seconds=60)
    assert completed['pilot_complete'] is True and completed['status'] == 'PILOT_STOPPED', completed
    assert len(children) == 2 and all(child.returncode == 0 for child in children)
    assert _state(**f.stores) == before
    if kind == 'bar':
        assert gold_bar_rotation.load(root=f.controller_root, base=f.base_runtime, account=f.account) == current
