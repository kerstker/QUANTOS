"""Original window/backend and temporary native files; no host systemd operations.

The fixture substitutes systemd's external process protocol and host root/release
identity only. Original service journal, hashes, inhibitor files and locks run.
"""
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import demo_maintenance_window as window
from tools import demo_maintenance_services as services
from tools import demo_maintenance_inhibit as inhibit
from tools import demo_supervisor as supervisor

_REAL_LEASE = supervisor.controller_lease
from tests.tools.test_demo_feedback_upgrade import materials


class NativeManager:
    def __init__(self, root):
        self.root = root
        self.states = {name: 'inactive' for name in services.ALLOWED_UNITS}
        self.states[services.UNITS[0]] = 'active'
        self.states[services.UNITS[-1]] = 'active'
        self.loaded = {}
        self.calls = []
        self.failure = None

    def files(self, name):
        fragment = self.root / 'units' / name
        paths = sorted((self.root / 'runtime' / (name + '.d')).glob('*.conf'))
        return fragment, paths

    def reload(self):
        self.calls.append('reload')
        if self.failure == 'reload':
            raise RuntimeError('reload interrupted')
        self.loaded = {name: (str(fragment) if fragment.exists() else '',
                             [str(p) for p in paths])
                       for name in services.ALLOWED_UNITS
                       for fragment, paths in [self.files(name)]}
        self.persist()

    def persist(self):
        (self.root / 'manager.json').write_text(json.dumps({'states': self.states, 'loaded': self.loaded}))

    def resume(self):
        value = json.loads((self.root / 'manager.json').read_bytes())
        self.states, self.loaded = value['states'], value['loaded']

    def run(self, args, **kwargs):
        if args[0] == '/usr/bin/busctl':
            unit = next(name for name in services.ALLOWED_UNITS
                        if ('/org/freedesktop/systemd1/unit/' + ''.join(c if c.isalnum() else '_' + format(ord(c), '02x') for c in name)) in args)
            paths = self.loaded[unit][1]
            data = [['ConditionPathExists', False, False, str(inhibit.CONTROL / 'allow-start'), 0]] \
                if str(inhibit.dropin(unit)) in paths else []
            return SimpleNamespace(stdout=json.dumps({'type': 'a(sbbsi)', 'data': data}).encode())
        assert args[0] == '/usr/bin/systemctl', args
        if args[1] == 'daemon-reload':
            self.reload()
            return SimpleNamespace(stdout='')
        action, name = args[1:3]
        if action in {'start', 'stop'}:
            self.calls.append((action, name))
            if self.failure == (action, name):
                raise RuntimeError('service interrupted')
            if action == 'start':
                assert str(inhibit.dropin(name)) not in self.loaded[name][1]
            self.states[name] = 'active' if action == 'start' else 'inactive'
            self.persist()
            return SimpleNamespace(stdout='')
        assert action == 'show'
        fragment, paths = self.loaded[name]
        current_fragment, current_paths = self.files(name)
        changed = fragment != (str(current_fragment) if current_fragment.exists() else '') or paths != [str(p) for p in current_paths]
        return SimpleNamespace(stdout='\n'.join([
            'LoadState=' + ('loaded' if fragment else 'not-found'),
            'ActiveState=' + self.states[name], 'ControlGroup=', 'FragmentPath=' + fragment,
            'DropInPaths=' + ' '.join(paths), 'NeedDaemonReload=' + ('yes' if changed else 'no')]))


@pytest.fixture
def native_window(tmp_path, monkeypatch, existing=False):
    (tmp_path / 'units').mkdir(mode=0o700)
    (tmp_path / 'runtime').mkdir(mode=0o755)
    (tmp_path / 'config').mkdir(mode=0o700)
    for name in services.UNITS:
        (tmp_path / 'units' / name).write_text('[Unit]\nDescription=' + name + '\n')
        (tmp_path / 'units' / name).chmod(0o644)
    if existing:
        for name, raw in materials('c' * 40).items():
            p = tmp_path / ('units' if name.endswith(('.service', '.timer')) else 'config') / name
            p.write_bytes(raw)
            p.chmod(0o644)
    manager = NativeManager(tmp_path)
    if existing:
        manager.states[services.FEEDBACK_UNITS[-1]] = 'active'
    manager.reload()
    monkeypatch.setattr(services.subprocess, 'run', manager.run)
    monkeypatch.setattr(inhibit, 'SYSTEM', tmp_path / 'runtime')
    monkeypatch.setattr(inhibit, 'CONTROL', tmp_path / 'control')
    monkeypatch.setattr(inhibit, 'OPERATOR_UID', os.getuid())
    native_lstat = Path.lstat

    def metadata(path, *args, **kwargs):
        info = native_lstat(path, *args, **kwargs)
        if path in tmp_path.parents:
            values = list(info)
            values[0], values[4] = 0o40755, os.getuid()
            return os.stat_result(values)
        return info
    monkeypatch.setattr(Path, 'lstat', metadata)
    native_path = services.Path

    class RootUnitPath(type(Path())):
        def stat(self, *args, **kwargs):
            info = super().stat(*args, **kwargs)
            if tmp_path / 'units' in self.parents:
                values = list(info)
                values[4] = 0
                return os.stat_result(values)
            return info
    monkeypatch.setattr(services, 'Path', RootUnitPath)
    monkeypatch.setattr(services, 'AUTOMATION_PROFILE', tmp_path / 'config' / 'feedback-automation.json')
    import live.execution.demo_authority as authority
    monkeypatch.setattr(authority, '_read_root_owned_json', lambda p: native_path(p).read_bytes())

    class RootOS:
        def __getattr__(self, name):
            return getattr(os, name)
        def geteuid(self):
            return 0
    monkeypatch.setattr(services, 'os', RootOS())
    monkeypatch.setattr(window, 'reload_manager', manager.reload)
    monkeypatch.setattr(window, 'boot_id', lambda: 'a' * 32)
    root = tmp_path / 'window'
    window.prepare(root, 'c' * 64)
    original = dict(manager.states)
    window.pause(root)
    return SimpleNamespace(root=root, manager=manager, original=original,
                           tmp_path=tmp_path, files=materials())


def test_original_native_window_rejects_new_scope_without_transition(native_window):
    f = native_window
    for name, raw in f.files.items():
        (f.tmp_path / ('units' if name.endswith(('.service', '.timer')) else 'config') / name).write_bytes(raw)
    f.manager.reload()
    with pytest.raises(ValueError, match='UNIT_SCOPE_CHANGED'):
        window.restore(f.root)
    assert not any(call[0] == 'start' for call in f.manager.calls if isinstance(call, tuple))


def test_prepared_transition_prevents_ordinary_restore_even_before_files_change(native_window):
    f = native_window
    area = f.root / 'feedback-upgrade'
    area.mkdir(mode=0o700)
    with pytest.raises(ValueError, match='FEEDBACK_UPGRADE_RECOVERY_REQUIRED'):
        window.restore(f.root)
    assert all(state == 'inactive' for state in f.manager.states.values())


def test_transaction_contract_is_available():
    module = importlib.import_module('tools.demo_feedback_upgrade_transaction')
    assert all(callable(getattr(module, name)) for name in ('prepare', 'advance', 'query', 'cancel_prepared'))


# Reuse the original compiled functional-cycle records. Its account/order worker
# is isolated; preflight itself is the real pure consumer, never a PASS stub.
from tests.tools.test_demo_feedback_upgrade_preflight import functional as _functional_fixture  # noqa: E402


@pytest.fixture
def functional(tmp_path, monkeypatch):
    return _functional_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def transaction_environment(functional, tmp_path, monkeypatch, request):
    from tests.tools.test_demo_feedback_upgrade_consumer_capture import local_capture
    local = local_capture.__wrapped__(functional, monkeypatch)
    # C fixture maps its root caller; contain that mapping to the reader module
    # so original private maintenance files still use the actual test owner.
    monkeypatch.setattr(os, 'geteuid', lambda: os.getuid())
    class CaptureOS:
        def __getattr__(self, name): return getattr(os, name)
        def geteuid(self): return 0
    monkeypatch.setattr(local['capture'], 'os', CaptureOS())
    monkeypatch.setattr(supervisor, 'controller_lease', _REAL_LEASE)
    f = native_window.__wrapped__(tmp_path, monkeypatch, existing=getattr(request, "param", False))
    tx = importlib.import_module('tools.demo_feedback_upgrade_transaction')
    ft = tx.files_tx
    targets = {name: str(tmp_path / ('units' if name.endswith(('.service', '.timer')) else 'config') / name)
               for name in ft.TARGETS}
    monkeypatch.setattr(ft, 'TARGETS', targets)
    monkeypatch.setattr(ft, '_root', lambda: None)
    monkeypatch.setattr(ft, 'OWNER_UID', os.getuid())
    monkeypatch.setattr(ft, 'OWNER_GID', os.getgid())
    check = ft._protected
    host = {(p.stat().st_dev, p.stat().st_ino) for p in tmp_path.parents}
    def custody(info, *, directory, maximum=ft.MAX_INTENT):
        view = SimpleNamespace(**{k: getattr(info, k) for k in ('st_mode', 'st_nlink', 'st_size')},
                               st_uid=os.getuid())
        if directory and (info.st_dev, info.st_ino) in host:
            view.st_mode &= ~0o022
        check(view, directory=directory, maximum=maximum)
    monkeypatch.setattr(ft, '_protected', custody)
    monkeypatch.setattr(tx.operator, '_admit', lambda source: None if source == 'c' * 40 else
                        (_ for _ in ()).throw(ValueError('UNACCEPTED_RELEASE')))
    class RootOS:
        def __getattr__(self, name): return getattr(os, name)
        def getresuid(self): return (0, 0, 0)
    monkeypatch.setattr(tx.operator, 'os', RootOS())
    # Original controller lease uses the functional fixture's private directory;
    # its host UID admission is synthetic, while the actual flock is retained.
    monkeypatch.setattr(window.processes, 'observe', lambda **kw: {'status': 'NO_SCOPED_PROCESS_OBSERVED'})
    monkeypatch.setattr(tx, '_now', lambda: functional['now_ms'])
    f.tx, f.ft, f.targets = tx, ft, targets
    f.files = functional['args']['files']
    f.evidence = functional['evidence']
    f.now = functional['now_ms']
    monkeypatch.setattr(supervisor, 'now_ms', lambda: f.now)
    f.local = local
    f.snapshot = local['capture'].capture(source_commit='c' * 40)
    f.active = [name for name, state in f.original.items() if state == 'active']
    f.journal = (f.root / 'services.json').read_bytes()
    return f


def prepared(f, *, active=None):
    f.captured = f.ft.capture()
    f.review = f.tx.prepare(f.root, source_commit='c' * 40, operation_sha256='c' * 64,
        files=f.files, captured=f.captured, approved_active=f.active if active is None else active,
        consumer_snapshot=f.snapshot)
    f.approval = dict(schema=f.tx.APPROVAL, purpose=f.tx.PURPOSE,
        review_sha256=f.review['review_sha256'], operation_sha256='c' * 64, source_commit='c' * 40,
        operator_id='synthetic-local-operator', approved_at_ms=f.now,
        service_activation_authorized=True, submission_enabled=False, economic_authority=False)
    return f


def advance(f, action='apply'):
    return f.tx.advance(f.root, expected_digest=f.review['review_sha256'], action=action, approval=f.approval)


def test_native_42_to_44_restores_only_explicit_activity_and_keeps_original_journal(transaction_environment):
    f = prepared(transaction_environment)
    assert f.review['consumer_preflight']['timer_compatible'] is True
    with pytest.raises(ValueError, match='FEEDBACK_UPGRADE_RECOVERY_REQUIRED'):
        window.restore(f.root)
    result = advance(f)
    assert result['status'] == 'COMPLETED'
    assert (f.root / 'services.json').read_bytes() == f.journal
    assert set(services.configured_units()) == set(services.ALLOWED_UNITS)
    assert {n for n, s in f.manager.states.items() if s == 'active'} == set(f.active)
    assert all(f.manager.states[n] == 'inactive' for n in services.FEEDBACK_UNITS)
    assert all(Path(path).read_bytes() == f.files[name] for name, path in f.targets.items())
    assert advance(f) == result
    with pytest.raises(ValueError, match='FEEDBACK_UPGRADE_RECOVERY_REQUIRED'):
        window.restore(f.root)


def test_native_cancel_without_approval_allows_original_restore(transaction_environment):
    f = prepared(transaction_environment)
    result = f.tx.cancel_prepared(f.root, expected_digest=f.review['review_sha256'])
    assert result['status'] == 'CANCELED_UNCHANGED'
    assert not (f.root / 'feedback-upgrade' / 'approval.json').exists()
    assert all(not Path(path).exists() for path in f.targets.values())
    window.restore(f.root)
    assert f.manager.states == f.original


def test_only_separately_approved_new_pair_is_started(transaction_environment):
    f = transaction_environment
    prepared(f, active=[*f.active, *services.FEEDBACK_UNITS])
    assert advance(f)['status'] == 'COMPLETED'
    assert all(f.manager.states[n] == 'active' for n in services.FEEDBACK_UNITS)


@pytest.mark.parametrize('damage', ['wrong_scope', 'new_purpose', 'economic', 'future', 'other_operation'])
def test_invalid_approval_never_publishes_or_releases(transaction_environment, damage):
    f = prepared(transaction_environment)
    if damage == 'wrong_scope': f.approval['review_sha256'] = 'b' * 64
    elif damage == 'new_purpose': f.approval['purpose'] = 'OLD_MAINTENANCE'
    elif damage == 'economic': f.approval['economic_authority'] = True
    elif damage == 'future': f.approval['approved_at_ms'] += 1
    else: f.approval['operation_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='FEEDBACK_UPGRADE_APPROVAL'):
        advance(f)
    assert all(not Path(path).exists() for path in f.targets.values())
    assert all(f.manager.states[n] == 'inactive' for n in services.UNITS)


@pytest.mark.parametrize('transaction_environment', [True], indirect=True)
def test_native_44_to_44_keeps_original_active_timer_and_old_journal(transaction_environment):
    f = prepared(transaction_environment)
    assert len(f.review['old_scope']) == 44
    assert advance(f)['status'] == 'COMPLETED'
    assert (f.root / 'services.json').read_bytes() == f.journal
    assert {n for n, v in f.manager.states.items() if v == 'active'} == set(f.active)


def fork_crash(f, monkeypatch, phase, action='apply'):
    """Actual fork exits after a durable boundary, with a bounded parent wait."""
    import signal
    import time
    native_phase = f.tx._phase
    pid = os.fork()
    if pid == 0:
        def crash(area, review, current):
            native_phase(area, review, current)
            if current == phase:
                os._exit(77)
        f.tx._phase = crash
        try:
            advance(f, action)
        except BaseException:
            os._exit(78)
        os._exit(79)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            assert os.waitstatus_to_exitcode(status) == 77, (phase, status)
            f.manager.resume()
            return
        time.sleep(.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    pytest.fail('bounded child did not reach durable boundary')


@pytest.mark.parametrize('phase', ['INHIBITING', 'APPLYING', 'VERIFYING', 'RELEASING', 'RESTORING', 'COMPLETED'])
def test_native_crash_resumes_same_operation_without_rewriting_old_scope(transaction_environment, monkeypatch, phase):
    f = prepared(transaction_environment)
    fork_crash(f, monkeypatch, phase)
    assert f.tx.query(f.root, expected_digest=f.review['review_sha256'])['status'] == phase
    assert advance(f)['status'] == 'COMPLETED'
    assert (f.root / 'services.json').read_bytes() == f.journal
    assert {n for n, v in f.manager.states.items() if v == 'active'} == set(f.active)


@pytest.mark.parametrize('phase', ['ROLLBACK_PAUSING', 'ROLLING_BACK', 'ROLLBACK_RELEASING', 'ROLLBACK_RESTORING', 'ROLLED_BACK'])
def test_native_rollback_can_itself_restart_and_never_reapply(transaction_environment, monkeypatch, phase):
    f = prepared(transaction_environment)
    fork_crash(f, monkeypatch, 'VERIFYING')
    fork_crash(f, monkeypatch, phase, 'rollback')
    assert advance(f, 'rollback')['status'] == 'ROLLED_BACK'
    assert all(not Path(path).exists() for path in f.targets.values())
    assert f.manager.states == f.original
    with pytest.raises(ValueError, match='ROLLBACK|TERMINAL'):
        advance(f)
    window.restore(f.root)
    assert f.manager.states == f.original


@pytest.mark.parametrize('when', ['before_reload', 'after_remove', 'after_first_start'])
def test_native_interruption_between_service_actions_recovers(transaction_environment, monkeypatch, when):
    f = prepared(transaction_environment)
    fired = False
    if when == 'before_reload':
        original = window.reload_manager
        def fail():
            nonlocal fired
            if not fired:
                fired = True
                raise RuntimeError('interrupt')
            original()
        monkeypatch.setattr(window, 'reload_manager', fail)
    elif when == 'after_remove':
        original = inhibit.remove
        def fail(*args, **kw):
            nonlocal fired
            result = original(*args, **kw)
            if not fired:
                fired = True
                raise RuntimeError('interrupt')
            return result
        monkeypatch.setattr(inhibit, 'remove', fail)
    else:
        original = services.SystemdBackend.change
        def fail(self, action, name):
            nonlocal fired
            result = original(self, action, name)
            if action == 'start' and not fired:
                fired = True
                raise RuntimeError('interrupt')
            return result
        monkeypatch.setattr(services.SystemdBackend, 'change', fail)
    with pytest.raises(RuntimeError, match='interrupt'):
        advance(f)
    assert advance(f)['status'] == 'COMPLETED'
    assert (f.root / 'services.json').read_bytes() == f.journal


@pytest.mark.parametrize('damage', ['original_unit', 'dropin', 'process', 'lease', 'approval', 'cancel_approved'])
def test_confirm_gap_changes_fail_before_file_mutation(transaction_environment, monkeypatch, damage):
    f = prepared(transaction_environment)
    if damage == 'original_unit':
        p = f.tmp_path / 'units' / services.UNITS[0]
        p.write_text('changed original configuration')
    elif damage == 'dropin':
        p = inhibit.SYSTEM / (services.UNITS[0] + '.d') / 'foreign.conf'
        p.write_text('[Unit]\nDescription=foreign\n')
        p.chmod(0o644)
        f.manager.reload()
    elif damage == 'process':
        monkeypatch.setattr(window.processes, 'observe', lambda **kw: {'status': 'SCOPED_PROCESSES_PRESENT'})
    elif damage == 'approval':
        fork_crash(f, monkeypatch, 'INHIBITING')
        f.approval['operator_id'] = 'another-confirmation'
    elif damage == 'cancel_approved':
        fork_crash(f, monkeypatch, 'INHIBITING')
        with pytest.raises(ValueError, match='CANCEL_FORBIDDEN'):
            f.tx.cancel_prepared(f.root, expected_digest=f.review['review_sha256'])
        return
    if damage == 'lease':
        with f.tx.supervisor.controller_lease():
            with pytest.raises(ValueError, match='CONTROLLER_MAINTENANCE_BUSY'):
                advance(f)
    else:
        with pytest.raises(ValueError):
            advance(f)
    assert all(not Path(path).exists() for path in f.targets.values())
    assert not any(call[0] == 'start' for call in f.manager.calls if isinstance(call, tuple))


def test_prepare_reentry_preserves_displayed_digest_despite_later_clock(transaction_environment, monkeypatch):
    f = prepared(transaction_environment)
    first = dict(f.review)
    monkeypatch.setattr(f.tx, '_now', lambda: f.now + 1000)
    assert f.tx.prepare(f.root, source_commit='c' * 40, operation_sha256='c' * 64,
        files=f.files, captured=f.captured, approved_active=f.active,
        consumer_snapshot=f.snapshot) == first


def test_prepare_review_written_state_missing_resumes_original_digest(transaction_environment, monkeypatch):
    f = transaction_environment
    original = f.tx._phase
    def fail(area, review, phase):
        if phase == 'PREPARED':
            raise RuntimeError('after review publication')
        original(area, review, phase)
    monkeypatch.setattr(f.tx, '_phase', fail)
    with pytest.raises(RuntimeError, match='after review publication'):
        prepared(f)
    stored = json.loads((f.root / 'feedback-upgrade' / 'review.json').read_bytes())
    monkeypatch.setattr(f.tx, '_phase', original)
    monkeypatch.setattr(f.tx, '_now', lambda: f.now + 1000)
    result = f.tx.prepare(f.root, source_commit='c' * 40, operation_sha256='c' * 64,
        files=f.files, captured=f.captured, approved_active=f.active, consumer_snapshot=f.snapshot)
    assert result['review_sha256'] == f.tx._digest(stored)
    assert not (f.root / 'feedback-upgrade' / 'approval.json').exists()


def test_completed_upgrade_can_explicitly_restore_original_configuration_and_activity(transaction_environment):
    f = transaction_environment
    prepared(f, active=[*f.active, services.FEEDBACK_UNITS[-1]])
    assert advance(f)['status'] == 'COMPLETED'
    assert f.manager.states[services.FEEDBACK_UNITS[-1]] == 'active'
    assert advance(f, 'rollback')['status'] == 'ROLLED_BACK'
    assert all(not Path(path).exists() for path in f.targets.values())
    assert f.manager.states == f.original
    assert (f.root / 'services.json').read_bytes() == f.journal


def test_new_fragment_cannot_be_published_before_all_44_inhibitors_exist(transaction_environment, monkeypatch):
    f = prepared(transaction_environment)
    original = f.ft._publish_target
    checks = []
    def observed(*args, **kwargs):
        token = json.loads((f.root / 'window.json').read_bytes())['inhibitor_sha256']
        for name in services.ALLOWED_UNITS:
            assert inhibit._existing(inhibit.dropin(name), inhibit._payload(token))
        assert set(f.manager.states.values()) == {'inactive'}
        checks.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(f.ft, '_publish_target', observed)
    assert advance(f)['status'] == 'COMPLETED'
    assert len(checks) == 4


def test_foreign_dropin_on_absent_feedback_pair_is_not_normalized(transaction_environment):
    f = prepared(transaction_environment)
    parent = inhibit.SYSTEM / (services.FEEDBACK_UNITS[0] + '.d')
    parent.mkdir(mode=0o755)
    (parent / 'foreign.conf').write_text('[Unit]\nDescription=foreign\n')
    (parent / 'foreign.conf').chmod(0o644)
    with pytest.raises(ValueError):
        advance(f)
    assert all(not Path(path).exists() for path in f.targets.values())


def append_delivered(f):
    from tools import demo_functional_cycle as cycle
    from tools.demo_operator_setup import digest
    original = f.evidence['initial_result']
    receipt = dict(status='RESEARCH_FEEDBACK_DELIVERED', feedback_sha256='f' * 64,
        cycle_sha256=digest(f.evidence['installation']), source_commit='c' * 40,
        completion_sha256=cycle.completion_digest(original), observed_at_ms=f.now,
        source_verified=False, submission_enabled=False, economic_authority=False)
    return cycle._retain(f.local['area'] / 'feedback-results', receipt)


def started_child(f, *, replace_original=False):
    import signal
    import time
    original = services.SystemdBackend.change
    pid = os.fork()
    if pid == 0:
        def start(self, action, name):
            result = original(self, action, name)
            if action == 'start' and name == services.FEEDBACK_UNITS[-1]:
                append_delivered(f)
                if replace_original:
                    path = f.local['paths']['ISSUER_POLICY']
                    other = path.with_name('same-bytes-replacement')
                    other.write_bytes(path.read_bytes()); other.chmod(0o600); other.replace(path)
                os._exit(77)
            return result
        services.SystemdBackend.change = start
        try:
            advance(f)
        except BaseException:
            import traceback
            (f.tmp_path / 'child-error.txt').write_text(traceback.format_exc())
            os._exit(78)
        os._exit(79)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            assert os.waitstatus_to_exitcode(status) == 77, (status,
                (f.tmp_path / 'child-error.txt').read_text() if (f.tmp_path / 'child-error.txt').exists() else '')
            f.manager.resume()
            return
        time.sleep(.01)
    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
    pytest.fail('bounded child did not reach actual feedback start')


@pytest.mark.parametrize('action', ['apply', 'rollback'])
def test_real_start_receipt_append_then_process_exit_can_resume_or_rollback(transaction_environment, action):
    f = transaction_environment
    prepared(f, active=[*f.active, services.FEEDBACK_UNITS[-1]])
    original_digest = f.review['review_sha256']
    started_child(f)
    assert json.loads((f.root / 'feedback-upgrade' / 'state.json').read_bytes())['phase'] == 'RESTORING'
    with pytest.raises(ValueError, match='CAPTURE_CHANGED'):
        f.local['capture'].recheck(f.snapshot, source_commit='c' * 40)
    assert advance(f, action)['status'] == ('COMPLETED' if action == 'apply' else 'ROLLED_BACK')
    assert f.tx._digest(json.loads((f.root / 'feedback-upgrade' / 'review.json').read_bytes())) == original_digest
    assert (f.root / 'services.json').read_bytes() == f.journal
    assert len(list((f.local['area'] / 'feedback-results').iterdir())) == len(f.evidence['feedback_results']) + 1
    if action == 'rollback':
        assert all(not Path(path).exists() for path in f.targets.values())
        assert f.manager.states == f.original


def test_started_receipt_does_not_allow_original_policy_inode_replacement(transaction_environment):
    f = transaction_environment
    prepared(f, active=[*f.active, services.FEEDBACK_UNITS[-1]])
    started_child(f, replace_original=True)
    with pytest.raises(ValueError, match='CAPTURE_CHANGED'):
        advance(f)
    with pytest.raises(ValueError, match='CAPTURE_CHANGED'):
        advance(f, 'rollback')


def test_receipt_append_before_start_boundary_cannot_change_approval_inputs(transaction_environment):
    f = prepared(transaction_environment)
    append_delivered(f)
    with pytest.raises(ValueError, match='CAPTURE_CHANGED'):
        advance(f)
    assert all(not Path(path).exists() for path in f.targets.values())


@pytest.mark.parametrize('boundary', ['after_inhibitor_removal', 'before_final_receipt'])
def test_native_process_exit_between_final_service_boundary_and_receipt(transaction_environment, boundary):
    import signal
    import time
    f = prepared(transaction_environment)
    pid = os.fork()
    if pid == 0:
        if boundary == 'after_inhibitor_removal':
            original = inhibit.remove
            def die(*args, **kwargs):
                original(*args, **kwargs)
                os._exit(77)
            inhibit.remove = die
        else:
            original = f.tx._phase
            def die(area, review, phase):
                if phase == 'COMPLETED':
                    os._exit(77)
                original(area, review, phase)
            f.tx._phase = die
        try:
            advance(f)
        except BaseException:
            os._exit(78)
        os._exit(79)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            assert os.waitstatus_to_exitcode(status) == 77
            break
        time.sleep(.01)
    else:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
        pytest.fail('bounded child did not reach service boundary')
    f.manager.resume()
    assert advance(f)['status'] == 'COMPLETED'
    assert (f.root / 'services.json').read_bytes() == f.journal


@pytest.mark.parametrize('phase', ['VERIFYING', 'RELEASING'])
def test_restarted_file_identity_drift_is_rejected_before_inhibitor_release(transaction_environment, monkeypatch, phase):
    f = prepared(transaction_environment)
    fork_crash(f, monkeypatch, phase)
    path = Path(f.targets['research-feedback.json'])
    replacement = path.with_name('same-bytes-new-inode')
    replacement.write_bytes(path.read_bytes()); replacement.chmod(path.stat().st_mode & 0o777)
    replacement.replace(path)
    with pytest.raises(ValueError, match='FEEDBACK_FILES_'):
        advance(f)
    token = json.loads((f.root / 'window.json').read_bytes())['inhibitor_sha256']
    assert all(inhibit._existing(inhibit.dropin(name), inhibit._payload(token))
               for name in services.ALLOWED_UNITS)
    assert set(f.manager.states.values()) == {'inactive'}


@pytest.mark.parametrize('damage', ['same_bytes_inode', 'delete'])
def test_completed_advance_remembers_receipt_prefix_it_already_observed(transaction_environment, damage):
    f = transaction_environment
    prepared(f, active=[*f.active, services.FEEDBACK_UNITS[-1]])
    assert advance(f)['status'] == 'COMPLETED'
    receipt = append_delivered(f)
    assert advance(f)['status'] == 'COMPLETED'  # Actual original API observes the append.
    if damage == 'same_bytes_inode':
        other = receipt.with_name('new-inode')
        other.write_bytes(receipt.read_bytes()); other.chmod(0o600); other.replace(receipt)
    else:
        receipt.unlink()
    with pytest.raises(ValueError, match='CAPTURE_CHANGED|CONSUMER_WITNESS'):
        advance(f)


def witness_head(f):
    path = f.root / 'feedback-upgrade' / f.tx.WITNESS_HEAD
    return json.loads(path.read_bytes())


@pytest.mark.parametrize('boundary', ['record_before_head', 'head_before_return'])
@pytest.mark.parametrize('action', ['apply', 'rollback'])
def test_fsynced_receipt_witness_crash_resumes_without_forgetting_identity(
        transaction_environment, boundary, action):
    import signal
    import time
    f = transaction_environment
    prepared(f, active=[*f.active, services.FEEDBACK_UNITS[-1]])
    assert advance(f)['status'] == 'COMPLETED'
    append_delivered(f)
    original = services._save
    pid = os.fork()
    if pid == 0:
        def die(path, value):
            if path.parent.name == f.tx.AREA and path.name == f.tx.WITNESS_HEAD and value['index'] == 1:
                if boundary == 'record_before_head':
                    os._exit(77)
                original(path, value)
                os._exit(77)
            return original(path, value)
        services._save = die
        try:
            advance(f)
        except BaseException:
            import traceback
            (f.tmp_path / 'witness-child-error.txt').write_text(traceback.format_exc())
            os._exit(78)
        os._exit(79)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            assert os.waitstatus_to_exitcode(status) == 77, (status,
                (f.tmp_path / 'witness-child-error.txt').read_text()
                if (f.tmp_path / 'witness-child-error.txt').exists() else '')
            break
        time.sleep(.01)
    else:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
        pytest.fail('bounded child did not reach witness write boundary')
    f.manager.resume()
    location = f.root / 'feedback-upgrade' / f.tx.WITNESSES
    first = (location / 'witness-001.json').read_bytes()
    if boundary == 'record_before_head':
        with pytest.raises(ValueError, match='CONSUMER_WITNESS_RECOVERY_REQUIRED'):
            f.tx.query(f.root, expected_digest=f.review['review_sha256'])
        assert witness_head(f)['index'] == 0
    assert advance(f, action)['status'] == ('COMPLETED' if action == 'apply' else 'ROLLED_BACK')
    assert witness_head(f)['index'] == 1
    assert (location / 'witness-001.json').read_bytes() == first
    assert json.loads((f.root / 'feedback-upgrade' / 'review.json').read_bytes())['consumer_snapshot'] == f.snapshot


@pytest.mark.parametrize('damage', ['truncated_tail', 'missing_tail', 'missing_middle', 'head_rollback', 'fork_file'])
def test_consumer_witness_corruption_never_falls_back_to_initial_review(transaction_environment, monkeypatch, damage):
    f = prepared(transaction_environment)
    assert advance(f)['status'] == 'COMPLETED'
    initial_head = witness_head(f)
    append_delivered(f)
    advance(f)
    f.now += 1
    monkeypatch.setattr(f.tx, '_now', lambda: f.now)
    append_delivered(f)
    advance(f)
    assert witness_head(f)['index'] == 2
    location = f.root / 'feedback-upgrade' / f.tx.WITNESSES
    if damage == 'truncated_tail':
        (location / 'witness-002.json').write_bytes(b'{')
    elif damage == 'missing_tail':
        (location / 'witness-002.json').unlink()
    elif damage == 'missing_middle':
        (location / 'witness-001.json').unlink()
    elif damage == 'head_rollback':
        services._save(f.root / 'feedback-upgrade' / f.tx.WITNESS_HEAD, initial_head)
    else:
        (location / 'witness-002-fork.json').write_bytes((location / 'witness-002.json').read_bytes())
        (location / 'witness-002-fork.json').chmod(0o600)
    before = {p.name: p.read_bytes() for p in location.iterdir()}
    with pytest.raises(ValueError):
        advance(f)
    assert {p.name: p.read_bytes() for p in location.iterdir()} == before


def test_query_does_not_register_new_receipts_or_modify_private_state(transaction_environment):
    f = prepared(transaction_environment)
    advance(f)
    receipt = append_delivered(f)
    area = f.root / 'feedback-upgrade'
    before = {str(p.relative_to(area)): p.read_bytes() for p in area.rglob('*') if p.is_file()}
    result = f.tx.query(f.root, expected_digest=f.review['review_sha256'])
    assert result['consumer_observation_recorded'] is False
    assert {str(p.relative_to(area)): p.read_bytes() for p in area.rglob('*') if p.is_file()} == before
    assert witness_head(f)['index'] == 0
    advance(f)
    assert witness_head(f)['index'] == 1
    receipt.unlink()
    with pytest.raises(ValueError, match='CAPTURE_CHANGED'):
        f.tx.query(f.root, expected_digest=f.review['review_sha256'])


def test_changed_approval_cannot_advance_consumer_observation_witness(transaction_environment):
    f = prepared(transaction_environment)
    advance(f)
    append_delivered(f)
    f.approval['operator_id'] = 'different-local-confirmation'
    with pytest.raises(ValueError, match='APPROVAL_CHANGED'):
        advance(f)
    assert witness_head(f)['index'] == 0


@pytest.mark.parametrize('action', ['apply', 'rollback'])
def test_empty_genesis_directory_after_real_exit_resumes_before_start_marker(transaction_environment, action):
    import signal
    import time
    f = prepared(transaction_environment)
    original = f.tx._put
    pid = os.fork()
    if pid == 0:
        def die(path, value):
            if path.name == 'witness-000.json' and path.parent.name == f.tx.WITNESSES:
                os._exit(77)
            return original(path, value)
        f.tx._put = die
        try:
            advance(f)
        except BaseException:
            os._exit(78)
        os._exit(79)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            assert os.waitstatus_to_exitcode(status) == 77
            break
        time.sleep(.01)
    else:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
        pytest.fail('bounded child did not reach initial witness boundary')
    f.manager.resume()
    area = f.root / 'feedback-upgrade'
    assert list((area / f.tx.WITNESSES).iterdir()) == []
    assert not (area / 'start-allowed.json').exists()
    assert advance(f, action)['status'] == ('COMPLETED' if action == 'apply' else 'ROLLED_BACK')
    assert witness_head(f)['index'] == 0


def test_old_restore_checks_latest_witness_after_rollback(transaction_environment):
    f = prepared(transaction_environment)
    advance(f)
    receipt = append_delivered(f)
    advance(f)
    advance(f, 'rollback')
    receipt.unlink()
    with pytest.raises(ValueError, match='RECOVERY_REQUIRED'):
        window.restore(f.root)


def test_missing_start_marker_after_completed_cannot_bypass_retained_history(transaction_environment):
    f = prepared(transaction_environment)
    advance(f)
    (f.root / 'feedback-upgrade' / 'start-allowed.json').unlink()
    with pytest.raises(ValueError, match='START_BOUNDARY|CONSUMER_WITNESS'):
        advance(f)


@pytest.mark.parametrize('action', ['apply', 'rollback'])
@pytest.mark.parametrize('damage', ['stopped_stream', 'stopped_timer', 'core_fingerprint'])
def test_terminal_advance_cannot_claim_restored_services_after_actual_state_drift(
        transaction_environment, action, damage):
    f = prepared(transaction_environment)
    advance(f)
    if action == 'rollback':
        advance(f, action)
    if damage == 'core_fingerprint':
        path = f.tmp_path / 'units' / services.UNITS[0]
        path.write_text('[Unit]\nDescription=changed terminal configuration\n')
    else:
        name = services.UNITS[0] if damage == 'stopped_stream' else services.UNITS[-1]
        f.manager.run(['/usr/bin/systemctl', 'stop', name])
    calls = list(f.manager.calls)
    with pytest.raises(ValueError, match='RESTORE|CONFIGURATION'):
        advance(f, action)
    assert f.manager.calls == calls  # A terminal check must never silently start again.


def test_terminal_check_preserves_original_completed_oneshot_convention(transaction_environment):
    f = transaction_environment
    prepared(f, active=[*f.active, services.FEEDBACK_UNITS[0]])
    advance(f)
    f.manager.run(['/usr/bin/systemctl', 'stop', services.FEEDBACK_UNITS[0]])
    calls = list(f.manager.calls)
    assert advance(f)['status'] == 'COMPLETED'
    assert f.manager.calls == calls


@pytest.mark.parametrize('action', ['apply', 'rollback'])
def test_fsynced_head_temporary_crash_recovers_without_forgetting_receipt(transaction_environment, action):
    import signal
    import time
    f = prepared(transaction_environment)
    assert advance(f)['status'] == 'COMPLETED'
    receipt = append_delivered(f)
    area = f.root / 'feedback-upgrade'
    head_path = area / f.tx.WITNESS_HEAD
    original = os.replace
    pid = os.fork()
    if pid == 0:
        def die(source, target, *args, **kwargs):
            if Path(target) == head_path:
                os._exit(77)  # Original _save has already flushed and fsynced its temp.
            return original(source, target, *args, **kwargs)
        os.replace = die
        try:
            advance(f)
        except BaseException:
            os._exit(78)
        os._exit(79)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            assert os.waitstatus_to_exitcode(status) == 77
            break
        time.sleep(.01)
    else:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
        pytest.fail('bounded child did not reach fsynced head temporary boundary')
    f.manager.resume()
    location = area / f.tx.WITNESSES
    retained = (location / 'witness-001.json').read_bytes()
    temporaries = {p: p.read_bytes() for p in head_path.parent.glob('.maintenance-*')}
    assert len(temporaries) == 1
    assert advance(f, action)['status'] == ('COMPLETED' if action == 'apply' else 'ROLLED_BACK')
    assert witness_head(f)['index'] == 1
    assert (location / 'witness-001.json').read_bytes() == retained
    assert all(path.read_bytes() == raw for path, raw in temporaries.items())
    receipt.unlink()
    with pytest.raises(ValueError, match='CAPTURE_CHANGED'):
        advance(f, action)
