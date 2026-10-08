"""Periodic checkpoint work retains attempts and never promotes a partial backup."""
import json
import os
from pathlib import Path

import pytest

from tools import demo_checkpoint_cycle as cycle
from tools import demo_namespace_checkpoint as checkpoint
from services.research.demo_user_stream import UserStreamJournal

SOURCE = 'c' * 40
SECRET = 'isolated-periodic-backup-test-passphrase'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    # Tiny fixtures model an adequately sized backup volume; CI need not own
    # the production worst-case reserve. The low-space path has its own test.
    monkeypatch.setattr(cycle.shutil, 'disk_usage', lambda p: SimpleNamespace(free=200 * 1024**3))
    previous = os.umask(0o077)
    try:
        source = tmp_path / 'source'
        source.mkdir()
        journal = UserStreamJournal(source / 'binance_demo_user_stream.db', account_ref_sha256='a' * 64)
        journal.record('SESSION_START', at_ms=1000, source_sha256=SOURCE.ljust(64, 'c'))
        journal.__exit__()
        spool = tmp_path / 'spool'
        backup = tmp_path / 'backup'
        spool.mkdir(); backup.mkdir()
        yield source, spool, backup
    finally:
        os.umask(previous)


def capture(workspace):
    source, spool, _ = workspace
    return cycle.capture_cycle(source, spool, namespace='research', source_commit=SOURCE,
                               observation_prefix=True)


def seal(workspace, capture_id, **kwargs):
    _, spool, backup = workspace
    return cycle.seal_cycle(spool / capture_id, backup, expected_uid=os.getuid(),
                            namespace='research', passphrase=SECRET, **kwargs)


def test_natural_cycle_capture_seal_restore_and_idempotent_delivery(workspace):
    captured = capture(workspace)
    assert captured['status'] == 'CAPTURED'
    result = seal(workspace, captured['cycle_id'])
    assert result['status'] == 'VERIFIED' and result['encrypted']
    assert not result['source_verified'] and not result['cross_namespace_atomic']
    original = (workspace[2] / captured['cycle_id'] / 'checkpoint.enc').read_bytes()
    repeated = seal(workspace, captured['cycle_id'])
    assert repeated == result
    assert (workspace[2] / captured['cycle_id'] / 'checkpoint.enc').read_bytes() == original
    summary = cycle.summarize(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research',
                              now_ms=result['completed_at_ms'], rpo_seconds=86400)
    assert summary['status'] == 'CURRENT' and summary['verified_cycles'] == 1
    assert summary['pending_cycles'] == summary['failed_cycles'] == 0
    assert not summary['submission_enabled'] and not summary['economic_authority']


def test_failed_capture_and_next_success_both_remain_visible(workspace, monkeypatch):
    original = checkpoint.create_checkpoint
    monkeypatch.setattr(checkpoint, 'create_checkpoint', lambda *a, **kw: (_ for _ in ()).throw(OSError('private-detail')))
    failed = capture(workspace)
    assert failed['status'] == 'FAILED' and failed['reason_code'] == 'CHECKPOINT_UNCLASSIFIED'
    assert 'private-detail' not in json.dumps(failed)
    monkeypatch.setattr(checkpoint, 'create_checkpoint', original)
    captured = capture(workspace)
    result = seal(workspace, captured['cycle_id'])
    summary = cycle.summarize(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research',
                              now_ms=result['completed_at_ms'], rpo_seconds=86400)
    assert summary['failed_cycles'] == 1 and summary['verified_cycles'] == 1
    assert (workspace[1] / failed['cycle_id'] / 'capture-result.json').is_file()


def test_interruption_after_cipher_publication_requires_fresh_restore(workspace, monkeypatch):
    captured = capture(workspace)
    original = cycle._write_json
    def interrupt(path, value):
        if path.name == 'seal-result.json':
            raise OSError('interrupted final publication')
        return original(path, value)
    monkeypatch.setattr(cycle, '_write_json', interrupt)
    with pytest.raises(OSError):
        seal(workspace, captured['cycle_id'])
    output = workspace[2] / captured['cycle_id']
    assert (output / 'checkpoint.enc').is_file() and not (output / 'seal-result.json').exists()
    monkeypatch.setattr(cycle, '_write_json', original)
    restored = []
    restore = checkpoint.restore_checkpoint
    def observe(*a, **kw):
        restored.append(kw['expected_sha256'])
        return restore(*a, **kw)
    monkeypatch.setattr(checkpoint, 'restore_checkpoint', observe)
    result = seal(workspace, captured['cycle_id'])
    assert result['status'] == 'VERIFIED' and result['recovered_after_interruption']
    assert len(restored) == 1


def test_modified_capture_or_existing_cipher_cannot_become_success(workspace):
    captured = capture(workspace)
    archive = workspace[1] / captured['cycle_id'] / 'capture.zip'
    archive.write_bytes(archive.read_bytes() + b'changed')
    with pytest.raises(ValueError):
        seal(workspace, captured['cycle_id'])
    assert not (workspace[2] / captured['cycle_id'] / 'seal-result.json').exists()


def test_missing_state_and_pending_capture_never_count_as_backup(workspace):
    captured = capture(workspace)
    summary = cycle.summarize(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research',
                              now_ms=captured['completed_at_ms'], rpo_seconds=86400)
    assert summary['status'] == 'NO_VERIFIED_BACKUP' and summary['pending_cycles'] == 1
    assert summary['last_verified_at_ms'] is None
    interrupted = workspace[1] / ('1789279999999-' + 'b' * 12)
    interrupted.mkdir()
    cycle._write_json(interrupted / 'capture-start.json', dict(captured, cycle_id=interrupted.name, status='STARTED'))
    summary = cycle.summarize(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research',
                              now_ms=captured['completed_at_ms'], rpo_seconds=86400)
    assert summary['interrupted_cycles'] == 1 and summary['status'] == 'NO_VERIFIED_BACKUP'


def test_rpo_measures_source_capture_age_and_preserves_old_failure(workspace):
    captured = capture(workspace)
    result = seal(workspace, captured['cycle_id'])
    summary = cycle.summarize(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research',
                              now_ms=captured['started_at_ms'] + 86400001, rpo_seconds=86400)
    assert summary['status'] == 'STALE' and summary['last_verified_at_ms'] == result['completed_at_ms']
    assert summary['recovery_point_at_ms'] == captured['started_at_ms']


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public'])
def test_capture_metadata_custody_checked_before_reading(workspace, kind):
    captured = capture(workspace)
    path = workspace[1] / captured['cycle_id'] / 'capture-result.json'
    if kind == 'symlink':
        target = path.with_name('elsewhere.json'); path.rename(target); path.symlink_to(target)
    elif kind == 'hardlink':
        os.link(path, path.with_name('elsewhere.json'))
    else:
        path.chmod(0o644)
    with pytest.raises(ValueError):
        seal(workspace, captured['cycle_id'])


def test_full_spool_keeps_original_history_and_refuses_more_work(workspace, monkeypatch):
    first = capture(workspace)
    monkeypatch.setattr(cycle, 'MAX_CYCLES', 1)
    with pytest.raises(ValueError, match='CYCLE_CAPACITY'):
        capture(workspace)
    assert (workspace[1] / first['cycle_id'] / 'capture.zip').exists()


def test_same_spool_cannot_run_overlapping_capture(workspace):
    with cycle._lock(workspace[1], os.getuid()):
        with pytest.raises(ValueError, match='CYCLE_ALREADY_RUNNING'):
            capture(workspace)


def test_successful_cipher_changed_after_sealing_is_rejected(workspace):
    captured = capture(workspace)
    seal(workspace, captured['cycle_id'])
    path = workspace[2] / captured['cycle_id'] / 'checkpoint.enc'
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='CYCLE_SEALED_BINDING'):
        seal(workspace, captured['cycle_id'])


@pytest.mark.parametrize('free', [0, 3 * checkpoint.MAX_BYTES + 1024**3])
def test_disk_reserve_failure_is_recorded_without_reading_source(workspace, monkeypatch, free):
    from types import SimpleNamespace
    monkeypatch.setattr(cycle.shutil, 'disk_usage', lambda p: SimpleNamespace(free=free))
    monkeypatch.setattr(checkpoint, 'create_checkpoint', lambda *a, **kw: pytest.fail('source read after space failure'))
    result = capture(workspace)
    assert result['status'] == 'FAILED' and result['reason_code'] == 'CYCLE_DISK_RESERVE'
    assert not (workspace[1] / result['cycle_id'] / 'capture.zip').exists()


def test_pending_cycle_preserves_failure_then_resumes_and_skips_verified(workspace, monkeypatch):
    captured = capture(workspace)
    original = checkpoint.seal_checkpoint
    monkeypatch.setattr(checkpoint, 'seal_checkpoint', lambda *a, **kw: (_ for _ in ()).throw(OSError('private-failure')))
    result = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research', passphrase=SECRET)
    assert result['status'] == 'PARTIAL' and result['failed_cycles'] == 1
    assert 'private-failure' not in json.dumps(result)
    monkeypatch.setattr(checkpoint, 'seal_checkpoint', original)
    resumed = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research', passphrase=SECRET)
    assert resumed['status'] == 'PROCESSED' and resumed['attempted_cycles'] == 1
    repeated = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research', passphrase=SECRET)
    assert repeated['attempted_cycles'] == 0
    summary = cycle.summarize(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research',
                              now_ms=cycle._now(), rpo_seconds=86400)
    assert summary['seal_failures'] == 1 and summary['verified_cycles'] == 1
    assert (workspace[1] / captured['cycle_id'] / 'capture.zip').exists()


def test_cycle_capture_cli_has_no_secret_resolver(workspace, monkeypatch, capsys):
    monkeypatch.setattr(checkpoint, 'resolve_backup_passphrase', lambda **kw: pytest.fail('capture attempted secret access'))
    assert cycle.main(['capture', '--source-dir', str(workspace[0]), '--source-commit', SOURCE,
                       '--namespace', 'research', '--spool', str(workspace[1]), '--observation-prefix']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'CAPTURED'


def test_rendered_periodic_units_keep_capture_and_secret_separate():
    from scripts.render_demo_checkpoint_units import render
    units = render(release_commit=SOURCE, namespace='research', source_uid=1000,
                   source_dir='/home/operator/research/state/data/cache')
    capture_unit = units['quantos-demo-checkpoint-research-capture.service']
    seal_unit = units['quantos-demo-checkpoint-research-seal.service']
    assert 'User=1000' in capture_unit and 'LoadCredential=' not in capture_unit
    assert 'User=0' in seal_unit and 'LoadCredential=l2-backup-passphrase:' in seal_unit
    assert 'verify-release --source-commit ' + SOURCE in capture_unit
    assert 'verify-release --source-commit ' + SOURCE in seal_unit
    assert '--pending' in seal_unit and 'PrivateNetwork=true' in seal_unit
    assert 'CapabilityBoundingSet=CAP_DAC_READ_SEARCH\n' in seal_unit
    assert 'CapabilityBoundingSet=\n' in capture_unit
    assert 'OnUnitInactiveSec=12h' in units['quantos-demo-checkpoint-research-capture.timer']
    assert 'OnUnitInactiveSec=15min' in units['quantos-demo-checkpoint-research-seal.timer']
    for body in units.values():
        assert '[Install]' not in body
        assert 'Restart=always' not in body


@pytest.mark.parametrize('source_dir', ['/var/lib/quantos-demo-issuer', '/tmp/path\nExecStart=/bin/sh', '/home/operator/../key'])
def test_unit_renderer_rejects_wrong_or_unquoted_source_scope(source_dir):
    from scripts.render_demo_checkpoint_units import render
    with pytest.raises(ValueError):
        render(release_commit=SOURCE, namespace='research', source_uid=1000, source_dir=source_dir)


def test_capture_archive_validation_failure_has_a_bounded_retry_history(workspace, monkeypatch):
    captured = capture(workspace)
    archive = workspace[1] / captured['cycle_id'] / 'capture.zip'
    archive.write_bytes(archive.read_bytes() + b'changed')
    monkeypatch.setattr(cycle, 'MAX_SEAL_ATTEMPTS', 2)
    for _ in range(2):
        result = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research', passphrase=SECRET)
        assert result['failed_cycles'] == 1
    final = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research', passphrase=SECRET)
    assert final['attempted_cycles'] == 0 and final['exhausted_cycles'] == 1
    assert len(list((workspace[2] / captured['cycle_id']).glob('seal-failure-*.json'))) == 2


def test_capture_interrupted_before_first_metadata_is_visible(workspace):
    interrupted = workspace[1] / ('1789279999999-' + 'b' * 12)
    interrupted.mkdir()
    summary = cycle.summarize(workspace[1], workspace[2], expected_uid=os.getuid(), namespace='research',
                              now_ms=cycle._now(), rpo_seconds=86400)
    assert summary['interrupted_cycles'] == 1 and summary['status'] == 'NO_VERIFIED_BACKUP'


def test_small_capture_seals_below_global_worst_case_reserve(workspace, monkeypatch):
    from types import SimpleNamespace
    captured = capture(workspace)
    monkeypatch.setattr(cycle.shutil, 'disk_usage', lambda p: SimpleNamespace(free=4 * 1024**3))
    result = seal(workspace, captured['cycle_id'])
    assert result['status'] == 'VERIFIED'
    assert (workspace[1] / captured['cycle_id'] / 'capture.zip').exists()


def test_disk_shortage_is_retained_once_and_can_recover_without_reset(workspace, monkeypatch):
    from types import SimpleNamespace
    captured = capture(workspace)
    monkeypatch.setattr(cycle.shutil, 'disk_usage', lambda p: SimpleNamespace(free=0))
    real_seal = checkpoint.seal_checkpoint
    monkeypatch.setattr(checkpoint, 'seal_checkpoint', lambda *a, **kw: pytest.fail('encrypt without reserve'))
    for _ in range(cycle.MAX_SEAL_ATTEMPTS + 2):
        result = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(),
                                    namespace='research', passphrase=SECRET)
        assert result['failed_cycles'] == 1 and result['exhausted_cycles'] == 0
    folder = workspace[2] / captured['cycle_id']
    retained = {p.name: p.read_bytes() for p in folder.glob('seal-failure-*.json')}
    assert len(retained) == 1
    monkeypatch.setattr(cycle.shutil, 'disk_usage', lambda p: SimpleNamespace(free=4 * 1024**3))
    monkeypatch.setattr(checkpoint, 'seal_checkpoint', real_seal)
    assert seal(workspace, captured['cycle_id'])['status'] == 'VERIFIED'
    assert all((folder / name).read_bytes() == body for name, body in retained.items())


def test_legacy_space_exhaustion_does_not_prevent_verified_retry(workspace, monkeypatch):
    from types import SimpleNamespace
    captured = capture(workspace)
    folder = workspace[2] / captured['cycle_id']; folder.mkdir(mode=0o700)
    for i in range(cycle.MAX_SEAL_ATTEMPTS):
        cycle._write_json(folder / f'seal-failure-{i:032x}.json', dict(
            schema=cycle.SCHEMA, cycle_id=captured['cycle_id'], namespace='research',
            source_commit=SOURCE, status='FAILED', started_at_ms=captured['started_at_ms'],
            completed_at_ms=captured['completed_at_ms'], reason_code='CYCLE_DISK_RESERVE', **cycle.SAFETY))
    retained = {p.name: p.read_bytes() for p in folder.iterdir()}
    monkeypatch.setattr(cycle.shutil, 'disk_usage', lambda p: SimpleNamespace(free=4 * 1024**3))
    result = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(),
                                namespace='research', passphrase=SECRET)
    assert result['attempted_cycles'] == 1 and result['exhausted_cycles'] == 0
    assert result['records'][0]['status'] == 'VERIFIED'
    assert all((folder / name).read_bytes() == body for name, body in retained.items())


def test_reserve_accounts_for_expansion_not_just_compressed_bytes(workspace, monkeypatch):
    import zipfile
    archive = workspace[1] / 'compressible.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr('large.json', b'0' * (40 * 1024**2))
    required = cycle._seal_reserve(archive, os.getuid())
    assert required >= 40 * 1024**2 + 3 * archive.stat().st_size + 1024**3
    monkeypatch.setattr(checkpoint, 'MAX_BYTES', 1)
    with pytest.raises(checkpoint.CheckpointError, match='CHECKPOINT_ZIP_SIZE'):
        cycle._seal_reserve(archive, os.getuid())


def test_space_failures_do_not_refund_corrupt_archive_attempts(workspace, monkeypatch):
    from types import SimpleNamespace
    captured = capture(workspace)
    monkeypatch.setattr(cycle.shutil, 'disk_usage', lambda p: SimpleNamespace(free=0))
    with pytest.raises(checkpoint.CheckpointError, match='CYCLE_DISK_RESERVE'):
        seal(workspace, captured['cycle_id'])
    archive = workspace[1] / captured['cycle_id'] / 'capture.zip'
    archive.write_bytes(archive.read_bytes() + b'changed')
    monkeypatch.setattr(cycle, 'MAX_SEAL_ATTEMPTS', 2)
    for _ in range(2):
        result = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(),
                                    namespace='research', passphrase=SECRET)
        assert result['records'][0]['reason_code'] == 'CYCLE_CAPTURE_DIGEST'
    result = cycle.seal_pending(workspace[1], workspace[2], expected_uid=os.getuid(),
                                namespace='research', passphrase=SECRET)
    assert result['exhausted_cycles'] == 1 and result['attempted_cycles'] == 0


def test_failure_exemption_rejects_rebound_or_insecure_metadata(workspace):
    captured = capture(workspace)
    folder = workspace[2] / captured['cycle_id']; folder.mkdir(mode=0o700)
    path = folder / ('seal-failure-' + 'a' * 32 + '.json')
    cycle._write_json(path, dict(schema=cycle.SCHEMA, cycle_id=captured['cycle_id'],
        namespace='issuer', source_commit=SOURCE, status='FAILED',
        reason_code='CYCLE_DISK_RESERVE', **cycle.SAFETY))
    with pytest.raises(checkpoint.CheckpointError, match='CYCLE_FAILURE_BINDING'):
        seal(workspace, captured['cycle_id'])
