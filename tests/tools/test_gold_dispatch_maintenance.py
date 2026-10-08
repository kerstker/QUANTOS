"""Real process maintenance leases and bounded original decision consumption.

Controller custody, installed policy, account facts and market prefixes are
explicit fixtures. Directory flock, task materialization and claims stay native.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_bar_strategy_dispatch import bar_dispatch as bar_dispatch, case as case
from tools import gold_strategy_tick as tick
from tools import demo_supervisor as supervisor


@contextmanager
def operator_lease(root):
    """The second process holds the original exclusive directory lease."""
    program = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tools import demo_supervisor as supervisor
root = Path(sys.argv[2])
supervisor.STATE = root
def custody(path):
    assert path == root and path.is_dir() and not path.is_symlink()
supervisor._root_private_directory = custody
with supervisor.controller_lease(maintenance=True):
    print('LOCKED', flush=True)
    assert sys.stdin.readline() == 'release\\n'
print('RELEASED', flush=True)
'''
    errors = tempfile.TemporaryFile(dir=root.parent)
    process = subprocess.Popen([sys.executable, '-I', '-B', '-u', '-c', program,
        str(Path(__file__).resolve().parents[2]), str(root)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=errors)
    def frame(expected):
        try:
            raw = supervisor.read_frame(process.stdout.fileno(), maximum=64,
                deadline=time.monotonic()+5)
            assert raw.strip() == expected
        except Exception as exc:
            errors.seek(0)
            pytest.fail(f'operator lease handshake: {type(exc).__name__}; '
                + errors.read(16384).decode(errors='replace'))
    released = False
    def release():
        nonlocal released
        if not released:
            process.stdin.write(b'release\n'); process.stdin.flush()
            frame(b'RELEASED')
            assert process.wait(timeout=5) == 0
            released = True
    try:
        frame(b'LOCKED')
        yield release
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
        for channel in (process.stdin, process.stdout):
            channel.close()
        errors.close()


def original(f):
    _, result, identity, _ = f['write_runtime']('GOLD_BAR_FRESH_EPISODE_INPUT_V1')
    assert _digest(result) == identity
    return result, identity


def test_operator_lease_waits_before_configuration_or_materialization(bar_dispatch, monkeypatch):
    f = bar_dispatch
    _, identity = original(f)
    configurations = []
    real_configuration = f['tool'].supervisor.configuration
    def configuration(source):
        configurations.append(source)
        return real_configuration(source)
    monkeypatch.setattr(f['tool'].supervisor, 'configuration', configuration)
    before = {p: p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}
    with operator_lease(f['root']) as release:
        waiting = f['tool'].run(source_commit='c'*40, use_approved_policy=True, result_sha256=identity)
        assert waiting['status'] == 'WAITING_CONTROLLER_MAINTENANCE', waiting
        assert waiting['phase'] == 'CONTROLLER_LEASE' and waiting['task_sha256'] is None
        assert waiting['retry_submission'] is waiting['submission_enabled'] is waiting['economic_authority'] is False
        assert configurations == [] and f['prefix_calls'] == []
        assert before == {p: p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}
        release()
        checked = f['tool'].run(source_commit='c'*40, use_approved_policy=True, result_sha256=identity)
    assert checked['status'] == 'TASK_READY', checked
    assert len(list((f['root']/'tasks').iterdir())) == 1
    assert not list((f['root']/'task-claims').iterdir())


@pytest.mark.parametrize('error', [ValueError('OTHER_CUSTODY_FAILURE'), RuntimeError('CONTROLLER_MAINTENANCE_BUSY')])
def test_other_lease_errors_do_not_become_waiting(bar_dispatch, monkeypatch, error):
    f = bar_dispatch
    _, identity = original(f)
    @contextmanager
    def broken():
        raise error
        yield
    monkeypatch.setattr(f['tool'].supervisor, 'controller_lease', broken)
    result = f['tool'].run(source_commit='c'*40, use_approved_policy=True, result_sha256=identity)
    assert result['status'] == 'INPUTS_UNRESOLVED'
    assert not list((f['root']/'tasks').iterdir())


def test_same_error_after_acquiring_lease_is_not_waiting(bar_dispatch, monkeypatch):
    f = bar_dispatch
    _, identity = original(f)
    def changed(source):
        raise ValueError('CONTROLLER_MAINTENANCE_BUSY')
    monkeypatch.setattr(f['tool'].supervisor, 'configuration', changed)
    result = f['tool'].run(source_commit='c'*40, use_approved_policy=True, result_sha256=identity)
    assert result['status'] == 'INPUTS_UNRESOLVED' and result['phase'] == 'CONFIGURATION'
    assert not list((f['root']/'tasks').iterdir())


def test_busy_error_after_original_claim_is_never_retried(bar_dispatch, monkeypatch):
    f = bar_dispatch
    observed, _ = original(f)
    calls = []
    monkeypatch.setattr(tick, 'time', SimpleNamespace(monotonic=lambda: 0.0,
        time_ns=lambda: f['clock'][0]*1_000_000,
        sleep=lambda seconds: pytest.fail('a claimed task must not be retried')))
    def lost(**kwargs):
        calls.append(kwargs)
        f['tool'].supervisor.claim(f['root']/'task-claims', {'task_sha256': kwargs['task_sha256']},
            identity_field='task_sha256')
        raise ValueError('CONTROLLER_MAINTENANCE_BUSY')
    monkeypatch.setattr(f['tool'].demo_task, 'run', lost)
    result = tick.dispatch_retained(observed, source_commit='c'*40)
    assert result['status'] == 'INPUTS_UNRESOLVED' and result['phase'] == 'ORIGINAL_TASK'
    assert len(calls) == 1 and len(list((f['root']/'task-claims').iterdir())) == 1
    assert result['task_sha256'] is not None
    again = tick.dispatch_retained(observed, source_commit='c'*40)
    assert again['status'] == 'QUERY_ONLY' and len(calls) == 1


@pytest.mark.parametrize('during_maintenance', ['release', 'expired', 'changed_policy', 'changed_runtime'])
def test_tick_waits_under_real_operator_lease_and_rechecks_originals(bar_dispatch, monkeypatch, during_maintenance):
    f = bar_dispatch
    observed, identity = original(f)
    at = f['clock'][0]
    calls = []
    native_dispatch = f['tool'].run
    def dispatch(**kwargs):
        calls.append(kwargs)
        assert kwargs['result_sha256'] == identity
        return native_dispatch(**kwargs)
    monkeypatch.setattr(tick.dispatcher, 'run', dispatch)
    with operator_lease(f['root']) as release:
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 1:
                if during_maintenance == 'changed_policy':
                    f['policy']['bindings']['execution_constraints_sha256'] = '0'*64
                elif during_maintenance == 'changed_runtime':
                    path = f['root']/'strategy-runtime.json'
                    runtime = json.loads(path.read_bytes()); runtime['seconds'] += 1
                    path.write_text(json.dumps(runtime))
                if during_maintenance != 'expired':
                    release()
                    f['clock'][0] += 1
                    return
            f['clock'][0] += round(seconds*1000)
        monkeypatch.setattr(tick, 'time', SimpleNamespace(monotonic=lambda: (f['clock'][0]-at)/1000,
            time_ns=lambda: f['clock'][0]*1_000_000, sleep=sleep))
        monkeypatch.setattr(f['tool'].demo_task, 'run', lambda **kw: pytest.fail('test must not send'))
        # Reach native materialization but keep execution off for this fixture.
        def dispatch_without_send(**kwargs):
            kwargs['execute'] = False
            return dispatch(**kwargs)
        monkeypatch.setattr(tick.dispatcher, 'run', dispatch_without_send)
        result = tick.dispatch_retained(observed, source_commit='c'*40)
    assert sleeps
    if during_maintenance == 'release':
        assert result['status'] == 'TASK_READY', result
        assert len(calls) == 2 and len(list((f['root']/'tasks').iterdir())) == 1
    else:
        assert not list((f['root']/'tasks').iterdir())
        assert result['status'] == ('INPUTS_UNRESOLVED' if during_maintenance == 'changed_runtime' else
            'WAITING_INDEPENDENT_APPROVAL' if during_maintenance == 'changed_policy' else 'WAITING_CONTROLLER_MAINTENANCE')
    assert not list((f['root']/'task-claims').iterdir())
    assert f['args']['dispatch'].snapshot() == [] and f['args']['oms'].orders() == []
