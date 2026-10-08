"""Actual bounded descriptor reads under a declared temporary custody mapping."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from live.execution import demo_authority
from tools import demo_supervisor as supervisor
from tools import demo_functional_cycle as functional_cycle
from tools.demo_operator_setup import canonical, digest
from tests.tools.test_demo_feedback_upgrade_preflight import functional as functional


_REAL_PROTECTED = demo_authority._require_protected
_UID = os.geteuid()


@pytest.fixture
def local_capture(functional, monkeypatch):
    from tools import demo_feedback_upgrade_consumer_capture as capture
    from tools import demo_feedback_upgrade_capture as reader
    base = functional['root']
    cfg = base/'configuration'; cfg.mkdir(mode=0o700)
    e = functional['evidence']
    paths = {'EXECUTOR_REGISTRY': cfg/'executor-registry.json',
             'ISSUER_REGISTRY': cfg/'issuer-registry.json', 'ISSUER_POLICY': cfg/'policy.json'}
    for name, field in (('EXECUTOR_REGISTRY', 'executor_registry'),
                        ('ISSUER_REGISTRY', 'issuer_registry'), ('ISSUER_POLICY', 'policy')):
        paths[name].write_bytes(canonical(e[field])); paths[name].chmod(0o600)
        monkeypatch.setattr(supervisor, name, paths[name])
    for path in base.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    base.chmod(0o700)
    # The test-only root/UID mapping retains actual file modes, link counts,
    # inode identity, real no-follow traversal, directory lists and bytes.
    def protected(info, *, directory):
        assert info.st_uid in (_UID, 0)
        view = SimpleNamespace(st_mode=info.st_mode, st_uid=0,
            st_nlink=info.st_nlink, st_size=info.st_size)
        _REAL_PROTECTED(view, directory=directory)
    def parent(path):
        path = Path(path); relative = path.relative_to(base)
        fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            protected(os.fstat(fd), directory=True)
            for part in relative.parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = next_fd
                protected(os.fstat(fd), directory=True)
            return fd
        except BaseException:
            os.close(fd); raise
    monkeypatch.setattr(reader, '_parent', parent)
    monkeypatch.setattr(reader, '_protected', protected)
    monkeypatch.setattr(capture.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(supervisor, 'now_ms', lambda: functional['now_ms'])
    verified = []
    monkeypatch.setattr(capture, 'verify_source', lambda source: verified.append(source))
    # Capture's original protected descriptor reader remains real. Neither the
    # functional consumer nor a worker runs while collecting these records.
    def forbidden(*args, **kwargs): pytest.fail('Capture must not acquire a nested lease or execute')
    monkeypatch.setattr(supervisor, 'controller_lease', forbidden)
    monkeypatch.setattr(functional_cycle, 'Runtime', forbidden)
    return dict(functional, cfg=cfg, paths=paths, verified=verified, capture=capture, reader=reader)


def call(local):
    return local['capture'].capture(source_commit='c'*40)


def test_capture_and_fresh_recheck_match_actual_complete_originals(local_capture):
    local = local_capture
    before = {str(p): p.read_bytes() for p in local['root'].rglob('*') if p.is_file()}
    snapshot = call(local)
    assert set(snapshot) == {'schema', 'consumer_evidence', 'read_identity', 'read_identity_sha256'}
    assert snapshot['schema'] == 'DEMO_FEEDBACK_CONSUMER_CAPTURE_V1'
    assert snapshot['consumer_evidence'] == local['evidence']
    assert len(snapshot['read_identity_sha256']) == 64
    assert local['capture'].recheck(snapshot, source_commit='c'*40) == snapshot
    assert local['verified'] == ['c'*40, 'c'*40]
    from tools.demo_feedback_upgrade_preflight import preflight
    assert preflight(**local['args'], consumer_evidence=snapshot['consumer_evidence'],
        now_ms=local['now_ms'])['timer_compatible'] is True
    assert {str(p): p.read_bytes() for p in local['root'].rglob('*') if p.is_file()} == before
    assert local['calls'] == []


@pytest.mark.parametrize('initially_absent', [False, True])
@pytest.mark.parametrize('delivered', [False, True])
def test_started_recovery_allows_only_valid_receipt_append(local_capture, initially_absent, delivered):
    local = local_capture
    history = local['area']/'feedback-results'
    if initially_absent:
        for path in history.iterdir(): path.unlink()
        history.rmdir()
    snapshot = call(local)
    e = snapshot['consumer_evidence']
    receipt = dict(status='RESEARCH_FEEDBACK_DELIVERED', feedback_sha256='f'*64,
        cycle_sha256=digest(e['installation']), source_commit='c'*40,
        completion_sha256=functional_cycle.completion_digest(e['initial_result']),
        observed_at_ms=e['initial_result']['observed_at_ms']+100,
        source_verified=False, economic_authority=False, submission_enabled=False)
    if not delivered:
        receipt = {name: value for name, value in receipt.items()
                   if name not in ('feedback_sha256', 'source_verified', 'economic_authority')}
        receipt['status'] = 'RESEARCH_FEEDBACK_DELIVERY_FAILED'
    path = functional_cycle._retain(history, receipt)
    with pytest.raises(ValueError, match='CHANGED'):
        local['capture'].recheck(snapshot, source_commit='c'*40)
    fresh = local['capture'].recheck_started(snapshot, source_commit='c'*40)
    assert fresh['consumer_evidence']['feedback_results'][path.name] == receipt
    assert local['capture'].recheck_started(fresh, source_commit='c'*40) == fresh


@pytest.mark.parametrize('damage', ['source', 'cycle', 'completion', 'future', 'authority',
                                    'replace_original', 'delete_original', 'recovery', 'receipt_name'])
def test_started_does_not_relax_existing_records_or_receipt_contract(local_capture, damage):
    local = local_capture; snapshot = call(local); e = local['evidence']
    history = local['area']/'feedback-results'
    receipt = dict(status='RESEARCH_FEEDBACK_DELIVERED', feedback_sha256='f'*64,
        cycle_sha256=digest(e['installation']), source_commit='c'*40,
        completion_sha256=functional_cycle.completion_digest(e['initial_result']),
        observed_at_ms=e['initial_result']['observed_at_ms']+100,
        source_verified=False, economic_authority=False, submission_enabled=False)
    if damage in ('source', 'cycle', 'completion'):
        field = {'source': 'source_commit', 'cycle': 'cycle_sha256', 'completion': 'completion_sha256'}[damage]
        receipt[field] = 'f'*(40 if damage == 'source' else 64)
    elif damage == 'future': receipt['observed_at_ms'] = 8640000000000000
    elif damage == 'authority': receipt['submission_enabled'] = True
    elif damage == 'replace_original':
        old = next(history.iterdir()); body = old.read_bytes(); old.unlink(); old.write_bytes(body); old.chmod(0o600)
    elif damage == 'delete_original': next(history.iterdir()).unlink()
    elif damage == 'recovery':
        functional_cycle._retain(local['area']/'recovery-results', e['initial_result'])
    path = functional_cycle._retain(history, receipt)
    if damage == 'receipt_name': path.rename(history/('b'*64+'.json'))
    with pytest.raises(ValueError): local['capture'].recheck_started(snapshot, source_commit='c'*40)


@pytest.mark.parametrize('change', ['bytes', 'same_bytes_inode', 'history_added', 'history_removed',
                                    'active_pointer', 'private_mode', 'ancestor_inode'])
def test_confirmation_recheck_rejects_changed_original_identity_or_file_set(local_capture, change):
    local = local_capture; snapshot = call(local)
    if change == 'bytes':
        path = local['paths']['ISSUER_POLICY']; path.write_bytes(path.read_bytes()+b'\n')
    elif change == 'same_bytes_inode':
        path = local['paths']['ISSUER_POLICY']; replacement = path.with_name('replacement')
        replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600); os.replace(replacement, path)
    elif change == 'history_added':
        row = deepcopy(local['evidence']['initial_result']); row['observed_at_ms'] += 1
        functional_cycle._retain(local['area']/'recovery-results', row)
    elif change == 'history_removed':
        next((local['area']/'feedback-results').iterdir()).unlink()
    elif change == 'active_pointer':
        (functional_cycle.SETUP/'active.json').write_bytes(b'{}')
    elif change == 'private_mode': local['area'].chmod(0o750)
    else:
        original = local['cfg']; moved = original.with_name('old-configuration')
        original.rename(moved); original.mkdir(mode=0o700)
        for path in moved.iterdir():
            (original/path.name).write_bytes(path.read_bytes()); (original/path.name).chmod(0o600)
    with pytest.raises(ValueError): local['capture'].recheck(snapshot, source_commit='c'*40)


def test_unrelated_parent_mtime_does_not_change_original_identity(local_capture):
    local = local_capture; snapshot = call(local)
    unrelated = local['cfg']/'unrelated-public.json'
    unrelated.write_bytes(b'{}'); unrelated.chmod(0o600)
    assert local['capture'].recheck(snapshot, source_commit='c'*40) == snapshot


@pytest.mark.parametrize('damage', ['symlink_file', 'symlink_parent', 'symlink_history', 'hardlink',
                                    'world_write', 'fifo', 'oversize', 'history_limit', 'extra_entry'])
def test_protected_custody_and_history_budgets_cannot_be_bypassed(local_capture, damage):
    local = local_capture; path = local['paths']['ISSUER_POLICY']
    if damage == 'symlink_file':
        target = path.with_name('target'); path.rename(target); path.symlink_to(target)
    elif damage == 'symlink_parent':
        target = local['cfg'].with_name('redirect'); local['cfg'].rename(target); local['cfg'].symlink_to(target)
    elif damage == 'symlink_history':
        directory = local['area']/'feedback-results'; target = directory.with_name('redirect')
        directory.rename(target); directory.symlink_to(target)
    elif damage == 'hardlink': os.link(path, path.with_name('alias'))
    elif damage == 'world_write': path.chmod(0o666)
    elif damage == 'fifo': path.unlink(); os.mkfifo(path, 0o600)
    elif damage == 'oversize': path.write_bytes(b'x'*65537)
    elif damage == 'extra_entry':
        (local['area']/'feedback-results'/'unexpected.json').write_bytes(b'{}')
    else:
        for index in range(33):
            row = dict(local['evidence']['initial_result'], observed_at_ms=index+1)
            functional_cycle._retain(local['area']/'recovery-results', row)
    with pytest.raises(ValueError): call(local)


def test_second_whole_read_detects_change_during_capture(local_capture, monkeypatch):
    local = local_capture; reader = local['reader']; original = reader._read_target
    calls = 0
    def read(path):
        nonlocal calls
        value = original(path)
        if Path(path) == local['paths']['EXECUTOR_REGISTRY']:
            calls += 1
            if calls == 2:
                target = local['paths']['ISSUER_POLICY']; target.write_bytes(target.read_bytes()+b'\n')
        return value
    monkeypatch.setattr(reader, '_read_target', read)
    with pytest.raises(ValueError, match='CHANGED'): call(local)


def test_missing_initial_result_is_distinct_from_unreadable_and_null(local_capture):
    local = local_capture; path = local['area']/'result.json'
    path.unlink()
    snapshot = call(local)
    assert snapshot['consumer_evidence']['initial_result'] is None
    path.write_text('null'); path.chmod(0o600)
    with pytest.raises(ValueError): call(local)


def test_root_and_accepted_release_are_checked_before_protected_reads(local_capture, monkeypatch):
    local = local_capture; capture = local['capture']
    monkeypatch.setattr(capture.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(local['reader'], '_read_target', lambda path: pytest.fail('read before root/source check'))
    with pytest.raises(ValueError, match='ROOT_REQUIRED'): call(local)
    monkeypatch.setattr(capture.os, 'geteuid', lambda: 0)
    def source(source_commit): raise ValueError('ISOLATED_RELEASE_REJECTED')
    monkeypatch.setattr(capture, 'verify_source', source)
    with pytest.raises(ValueError, match='ISOLATED_RELEASE_REJECTED'): call(local)


def test_successor_uses_original_active_pointer_and_own_cycle_directory(local_capture):
    local = local_capture; e = local['evidence']
    enrollment = 'b'*64
    stage = functional_cycle.SETUP/'cycles'/enrollment
    stage.mkdir(parents=True, mode=0o700)
    stage.parent.chmod(0o700)
    installed = dict(e['installation'], enrollment_sha256=enrollment)
    (stage/'result.json').write_bytes(canonical(installed)); (stage/'result.json').chmod(0o600)
    (stage/'spec.json').write_bytes(canonical(e['spec'])); (stage/'spec.json').chmod(0o600)
    pointer = dict(schema='DEMO_ACTIVE_ENROLLMENT_V1', enrollment_sha256=enrollment, result_sha256=digest(installed))
    (functional_cycle.SETUP/'active.json').write_bytes(canonical(pointer))
    (functional_cycle.SETUP/'active.json').chmod(0o600)
    area = local['state']/'functional-cycle/cycles'/digest(installed)
    area.mkdir(parents=True, mode=0o700)
    area.parent.chmod(0o700)
    (area/'claim.json').write_bytes(canonical(dict(e['claim'], cycle_sha256=digest(installed))))
    (area/'claim.json').chmod(0o600)
    result = dict(e['initial_result'], cycle_sha256=digest(installed))
    (area/'result.json').write_bytes(canonical(result)); (area/'result.json').chmod(0o600)
    snapshot = call(local)
    captured = snapshot['consumer_evidence']
    assert captured['installation'] == installed and captured['initial_result'] == result
    assert captured['feedback_results'] == {}
    pointer['result_sha256'] = 'f'*64
    (functional_cycle.SETUP/'active.json').write_bytes(canonical(pointer))
    with pytest.raises(ValueError, match='ACTIVE_BINDING'): call(local)
