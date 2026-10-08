"""Real operator CLI to native maintenance/capture/CAS, with host stand-ins.

UID/sudo/TTY, accepted release and systemd protocol are explicitly synthetic.
Original locks, files, journals, approval parser and transaction run locally.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_feedback_upgrade_transaction import (
    transaction_environment as _transaction_environment,
    functional as _functional,
)
from tools import demo_feedback_upgrade_operator as operator
from tools import gold_strategy_policy_install as identity


@pytest.fixture
def functional(tmp_path, monkeypatch):
    return _functional.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def transaction_environment(functional, tmp_path, monkeypatch, request):
    return _transaction_environment.__wrapped__(functional, tmp_path, monkeypatch, request)


@pytest.fixture
def native_operator(transaction_environment, monkeypatch):
    f = transaction_environment
    # Return the independent fixture's initial paused window to its true old
    # active set; the operator then prepares its own fresh operation/window.
    operator.window.restore(f.root)
    parent = f.tmp_path / 'operator-runs'
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(operator, 'ROOT_PARENT', parent)
    monkeypatch.setattr(operator.maintenance, 'ROOT_PARENT', parent)
    monkeypatch.setattr(operator, '_now', lambda: f.now)
    operator_name = 'synthetic-local-operator'
    monkeypatch.setattr(identity, 'os', SimpleNamespace(
        geteuid=lambda: 0, environ={'SUDO_UID': '1000', 'SUDO_USER': operator_name}))
    monkeypatch.setattr(identity, 'pwd', SimpleNamespace(getpwuid=lambda uid: SimpleNamespace(pw_name=operator_name)))
    monkeypatch.setattr(identity, 'sys', SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True), stdout=SimpleNamespace(isatty=lambda: True)))

    class ConfirmInput:
        def readline(self, limit):
            records = list(parent.glob('*/prepared.json'))
            assert len(records) == 1
            review = json.loads(records[0].read_bytes())
            return 'UPGRADE FEEDBACK ' + review['review_sha256'] + '\n'
    monkeypatch.setattr(operator, 'sys', SimpleNamespace(stdin=ConfirmInput()))
    monkeypatch.setattr(operator, 'select', SimpleNamespace(select=lambda *args: ([1], [], [])))
    f.parent = parent
    publication = json.loads(f.files['research-feedback.json'])
    f.argv = ['--source-commit', 'c' * 40, '--upgrade-reviewed',
        '--research-state-root', publication['research_state_root'],
        '--executor-uid', str(publication['executor_uid']),
        '--research-uid', str(publication['research_uid']),
        '--research-gid', str(publication['research_gid'])]
    return f


@pytest.mark.parametrize('activate', [False, True])
def test_native_operator_cli_installs_and_explicitly_rolls_back(native_operator, activate, capsys):
    f = native_operator
    before = f.local['capture'].capture(source_commit='c' * 40)
    args = f.argv + (['--activate-feedback-timer'] if activate else [])
    assert operator.main(args) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'COMPLETED' and result['services_restored']
    active = {name for name, state in f.manager.states.items() if state == 'active'}
    expected = set(f.active) | ({operator.services.FEEDBACK_UNITS[-1]} if activate else set())
    assert active == expected
    assert all(Path(path).read_bytes() == f.files[name] for name, path in f.targets.items())
    run = operator._base(result['operation_id'])
    original_journal = (run / 'window/services.json').read_bytes()
    approval = (run / 'approval.json').read_bytes()
    assert approval == (run / 'window/feedback-upgrade/approval.json').read_bytes()
    assert f.local['capture'].capture(source_commit='c' * 40) == before
    assert operator.main(['--source-commit', 'c' * 40, '--rollback', result['operation_id']]) == 0
    after = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert after['status'] == 'ROLLED_BACK' and after['services_restored']
    assert f.manager.states == f.original
    assert (run / 'window/services.json').read_bytes() == original_journal
    assert (run / 'approval.json').read_bytes() == approval
    assert all(not Path(path).exists() for path in f.targets.values())
    assert f.local['capture'].capture(source_commit='c' * 40) == before


def test_process_exit_after_outer_approval_resumes_same_native_operation(native_operator, capsys):
    f = native_operator
    pid = os.fork()
    if pid == 0:
        operator.transaction.advance = lambda *a, **kw: os._exit(87)
        operator.main(f.argv)
        os._exit(88)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 87
    f.manager.resume()
    roots = list(f.parent.glob('quantos-feedback-upgrade-*'))
    assert len(roots) == 1
    base = roots[0]
    run = json.loads((base / 'run.json').read_bytes())
    saved = (base / 'approval.json').read_bytes()
    assert not (base / 'window/feedback-upgrade/approval.json').exists()
    assert all(not Path(path).exists() for path in f.targets.values())
    assert operator.main(['--source-commit', 'c' * 40, '--resume', run['operation_sha256']]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'COMPLETED' and result['operation_id'] == run['operation_sha256']
    assert (base / 'approval.json').read_bytes() == saved
    assert (base / 'window/feedback-upgrade/approval.json').read_bytes() == saved
    assert len(list(f.parent.glob('quantos-feedback-upgrade-*'))) == 1


def test_declined_native_operation_remains_recoverable_after_original_restore(native_operator, monkeypatch, capsys):
    import io
    f = native_operator
    monkeypatch.setattr(operator, 'sys', SimpleNamespace(stdin=io.StringIO('DECLINE\n')))
    assert operator.main(f.argv) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'UPGRADE_DECLINED' and result['services_restored']
    base = operator._base(result['operation_id'])
    before = {str(p.relative_to(base)): p.read_bytes() for p in (base / 'window').rglob('*') if p.is_file()}
    calls = list(f.manager.calls)
    assert operator.main(['--source-commit', 'c' * 40, '--rollback', result['operation_id']]) == 0
    again = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert again['status'] == 'CANCELED_UNCHANGED' and again['services_restored']
    assert {str(p.relative_to(base)): p.read_bytes() for p in (base / 'window').rglob('*') if p.is_file()} == before
    assert f.manager.calls == calls


def test_canceled_restore_does_not_trust_terminal_journal_when_old_service_stopped(native_operator, monkeypatch, capsys):
    import io
    f = native_operator
    monkeypatch.setattr(operator, 'sys', SimpleNamespace(stdin=io.StringIO('DECLINE\n')))
    original_phase = operator.window._phase

    def interrupted(root, document, phase):
        if phase == 'RESTORED':
            raise RuntimeError('exit after services journal, before window receipt')
        return original_phase(root, document, phase)
    monkeypatch.setattr(operator.window, '_phase', interrupted)
    assert operator.main(f.argv) == 3
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    base = operator._base(result['operation_id'])
    assert json.loads((base / 'window/services.json').read_bytes())['phase'] == 'RESTORED'
    assert json.loads((base / 'window/window.json').read_bytes())['phase'] == 'RESTORING'
    monkeypatch.setattr(operator.window, '_phase', original_phase)
    f.manager.states[operator.services.UNITS[0]] = 'inactive'
    f.manager.persist()
    calls = list(f.manager.calls)
    assert operator.main(['--source-commit', 'c' * 40, '--rollback', result['operation_id']]) == 3
    recovered = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert recovered['status'] == 'RECOVERY_REQUIRED' and not recovered['services_restored']
    assert f.manager.calls == calls


def test_cancel_process_exit_during_manager_reload_resumes_original_restore(native_operator, monkeypatch, capsys):
    import io
    f = native_operator
    monkeypatch.setattr(operator, 'sys', SimpleNamespace(stdin=io.StringIO('DECLINE\n')))
    original_reload = operator.window.reload_manager
    pid = os.fork()
    if pid == 0:
        def reload_then_exit():
            original_reload()
            states = list(f.parent.glob('*/window/feedback-upgrade/state.json'))
            if states and json.loads(states[0].read_bytes())['phase'] == 'CANCELED_UNCHANGED':
                os._exit(89)
        operator.window.reload_manager = reload_then_exit
        operator.main(f.argv)
        os._exit(90)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 89
    f.manager.resume()
    base, = f.parent.glob('quantos-feedback-upgrade-*')
    run = json.loads((base / 'run.json').read_bytes())
    assert json.loads((base / 'window/window.json').read_bytes())['phase'] == 'RELEASING'
    assert not (base / 'approval.json').exists()
    assert operator.main(['--source-commit', 'c' * 40, '--rollback', run['operation_sha256']]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'CANCELED_UNCHANGED' and result['services_restored']
    assert f.manager.states == f.original
    assert all(not Path(path).exists() for path in f.targets.values())


def test_process_exit_before_outer_prepared_receipt_uses_original_native_review(native_operator, capsys):
    f = native_operator
    pid = os.fork()
    if pid == 0:
        original_write = operator.write_new
        def interrupted(path, raw):
            if path.name == 'prepared.json':
                os._exit(91)
            return original_write(path, raw)
        operator.write_new = interrupted
        operator.main(f.argv)
        os._exit(92)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 91
    f.manager.resume()
    base, = f.parent.glob('quantos-feedback-upgrade-*')
    run = json.loads((base / 'run.json').read_bytes())
    assert not (base / 'prepared.json').exists() and not (base / 'approval.json').exists()
    native_review = (base / 'window/feedback-upgrade/review.json').read_bytes()
    assert operator.main(['--source-commit', 'c' * 40, '--rollback', run['operation_sha256']]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'CANCELED_UNCHANGED' and result['services_restored']
    assert (base / 'window/feedback-upgrade/review.json').read_bytes() == native_review
    assert not (base / 'approval.json').exists()
    assert f.manager.states == f.original


@pytest.mark.parametrize('stopped', [None, 'stream', 'timer'])
def test_preparation_failure_recovery_rechecks_original_live_services(native_operator, monkeypatch, capsys, stopped):
    f = native_operator
    def unavailable(**kwargs):
        raise RuntimeError('consumer capture unavailable before native review')
    monkeypatch.setattr(operator, 'capture_consumer', unavailable)
    assert operator.main(f.argv) == 3
    initial = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert initial['status'] == 'PREPARATION_FAILED' and initial['services_restored']
    base = operator._base(initial['operation_id'])
    assert not (base / 'window/feedback-upgrade').exists()
    assert not (base / 'approval.json').exists()
    assert json.loads((base / 'window/window.json').read_bytes())['phase'] == 'RESTORED'
    if stopped:
        unit = (operator.services.UNITS[0] if stopped == 'stream' else
                next(name for name in f.active if name.endswith('.timer')))
        f.manager.states[unit] = 'inactive'
        f.manager.persist()
    before = {str(p.relative_to(base)): p.read_bytes() for p in (base / 'window').rglob('*') if p.is_file()}
    calls = list(f.manager.calls)
    assert operator.main(['--source-commit', 'c' * 40, '--resume', initial['operation_id']]) == (3 if stopped else 0)
    recovered = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert recovered['services_restored'] is (stopped is None)
    assert recovered['status'] == ('RECOVERY_REQUIRED' if stopped else 'CANCELED_UNCHANGED')
    assert f.manager.calls == calls
    assert {str(p.relative_to(base)): p.read_bytes() for p in (base / 'window').rglob('*') if p.is_file()} == before
