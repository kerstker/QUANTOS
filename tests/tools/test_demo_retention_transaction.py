"""Interrupted retention must preserve every byte and permit explicit rollback."""
import hashlib
import json
from pathlib import Path
import pytest

from tools import demo_retention_transaction as retention


def fixture(tmp_path):
    root = tmp_path / 'snapshot'; root.mkdir(mode=0o700)
    area = root / 'observation-views'; area.mkdir(mode=0o700)
    names = []
    for i in range(3):
        body = json.dumps({'observed_at_ms': i + 1}).encode()
        name = hashlib.sha256(body).hexdigest() + '.json'
        (area / name).write_bytes(body); (area / name).chmod(0o600)
        names.append('observation-views/' + name)
    pointer = area / 'latest.json'
    pointer.write_text(json.dumps({'sha256': Path(names[-1]).stem})); pointer.chmod(0o600)
    return root, names


def test_move_and_rollback_preserve_bytes_and_latest(tmp_path):
    root, names = fixture(tmp_path)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*.json')}
    archive = tmp_path / 'archive'
    retention.prepare(root, archive, names[:2])
    result = retention.apply(root, archive)
    assert result['moved'] == 2
    assert all(not (root / n).exists() for n in names[:2])
    assert (root / names[-1]).exists()
    retention.rollback(root, archive)
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*.json')} == before
    assert retention.rollback(root, archive)['restored'] == 2


def test_crash_between_rename_and_completion_can_roll_back(tmp_path, monkeypatch):
    root, names = fixture(tmp_path); archive = tmp_path / 'archive'
    retention.prepare(root, archive, names[:2])
    real = retention.move
    def crash(source, target):
        real(source, target)
        raise RuntimeError('simulated process interruption')
    monkeypatch.setattr(retention, 'move', crash)
    with pytest.raises(RuntimeError): retention.apply(root, archive)
    monkeypatch.setattr(retention, 'move', real)
    retention.rollback(root, archive)
    assert all((root / n).exists() for n in names)


def test_crash_can_resume_without_double_moving(tmp_path, monkeypatch):
    root, names = fixture(tmp_path); archive = tmp_path / 'archive'
    retention.prepare(root, archive, names[:2])
    real = retention.move
    def crash(source, target):
        real(source, target); raise RuntimeError('crash')
    monkeypatch.setattr(retention, 'move', crash)
    with pytest.raises(RuntimeError): retention.apply(root, archive)
    monkeypatch.setattr(retention, 'move', real)
    assert retention.apply(root, archive)['moved'] == 2


@pytest.mark.parametrize('damage', ['changed', 'symlink', 'conflict'])
def test_damage_rejected_before_any_more_moves(tmp_path, damage):
    root, names = fixture(tmp_path); archive = tmp_path / 'archive'
    retention.prepare(root, archive, names[:2])
    path = root / names[1]
    if damage == 'changed': path.write_bytes(b'{}')
    elif damage == 'symlink': path.unlink(); path.symlink_to(root / names[0])
    else:
        dest = archive / 'records' / names[1]; dest.write_bytes(path.read_bytes()); dest.chmod(0o600)
    with pytest.raises(ValueError): retention.apply(root, archive)
    assert (root / names[0]).exists()


def test_latest_and_out_of_scope_records_never_selected(tmp_path):
    root, names = fixture(tmp_path)
    for i, name in enumerate([names[-1], 'observation-views/latest.json', '../escape.json', 'evidence/' + Path(names[0]).name]):
        with pytest.raises(ValueError): retention.prepare(root, tmp_path / str(i), [name])
        assert not (tmp_path / str(i)).exists()


def test_production_roots_rejected_before_reading():
    with pytest.raises(ValueError, match='ISOLATED_ONLY'):
        retention.prepare(Path('/var/lib/quantos-demo-executor'), Path('/var/tmp/no-create'), [])


def test_process_death_releases_lock_and_allows_recovery(tmp_path):
    import subprocess
    import sys
    root, names = fixture(tmp_path); archive = tmp_path / 'archive'
    retention.prepare(root, archive, names[:2])
    code = '''import os, sys
from tools import demo_retention_transaction as r
original = r.move
def die(a, b):
    original(a, b)
    os._exit(23)
r.move = die
r.apply(sys.argv[1], sys.argv[2])
'''
    result = subprocess.run([sys.executable, '-c', code, str(root), str(archive)], capture_output=True)
    assert result.returncode == 23
    assert sum((root / n).exists() for n in names[:2]) == 1
    retention.rollback(root, archive)
    assert all((root / n).exists() for n in names)


def test_latest_changed_after_preparation_stops_moves(tmp_path):
    root, names = fixture(tmp_path); archive = tmp_path / 'archive'
    retention.prepare(root, archive, names[:2])
    (root / 'observation-views/latest.json').write_text(json.dumps({'sha256': Path(names[0]).stem}))
    with pytest.raises(ValueError, match='LATEST_SELECTED'): retention.apply(root, archive)
    assert all((root / n).exists() for n in names)


def test_corrupted_archived_record_stops_rollback_before_any_moves(tmp_path):
    root, names = fixture(tmp_path); archive = tmp_path / 'archive'
    retention.prepare(root, archive, names[:2]); retention.apply(root, archive)
    (archive / 'records' / names[0]).write_bytes(b'{}')
    with pytest.raises(ValueError, match='RECORD_CHANGED'): retention.rollback(root, archive)
    assert all(not (root / n).exists() for n in names[:2])


def test_atomic_move_does_not_replace_an_existing_destination(tmp_path):
    source = tmp_path / 'source'; target = tmp_path / 'target'
    source.write_bytes(b'old'); target.write_bytes(b'keep')
    with pytest.raises(FileExistsError): retention.move(source, target)
    assert source.read_bytes() == b'old' and target.read_bytes() == b'keep'


def reviewed_fixture(tmp_path):
    root, names = fixture(tmp_path)
    plan = {'schema': 'DEMO_RETENTION_REVIEW_PLAN_V1', 'apply_available': False,
            'directories': {scope: {'candidates': []} for scope in retention.SCOPES}}
    for name in names[:2]:
        raw = (root / name).read_bytes()
        plan['directories']['observation-views']['candidates'].append(
            {'sha256': Path(name).stem, 'bytes': len(raw)})
    plan_path = tmp_path / 'plan.json'; plan_path.write_text(json.dumps(plan)); plan_path.chmod(0o600)
    report = {'schema': 'DEMO_RETENTION_REFERENCE_REVIEW_V2', 'status': 'SCOPED_REVIEW_ONLY',
              'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
              'candidate_files': 2, 'pinned_files': 1, 'pinned_candidates': [names[0]],
              'candidate_reference_closure_checked': True, 'all_references_proven': False,
              'apply_available': False, 'production_changes': False}
    review_path = tmp_path / 'review.json'; review_path.write_text(json.dumps(report)); review_path.chmod(0o600)
    return root, names, plan_path, review_path


def test_reviewed_transaction_preserves_pins_and_lookup_across_rollback(tmp_path):
    root, names, plan, review = reviewed_fixture(tmp_path)
    archive = tmp_path / 'archive'
    before = [(root / n).read_bytes() for n in names]
    retention.prepare_reviewed(root, archive, plan=plan, review=review,
                               expected_review_sha256=hashlib.sha256(review.read_bytes()).hexdigest())
    assert retention.apply(root, archive)['moved'] == 1
    assert (root / names[0]).exists() and not (root / names[1]).exists()
    for name, raw in zip(names, before): assert retention.lookup(root, archive, name) == raw
    retention.rollback(root, archive)
    for name, raw in zip(names, before): assert retention.lookup(root, archive, name) == raw


@pytest.mark.parametrize('damage', ['review_digest', 'plan_digest', 'closure', 'pinned_scope'])
def test_unbound_review_cannot_prepare_moves(tmp_path, damage):
    root, names, plan, review = reviewed_fixture(tmp_path)
    report = json.loads(review.read_bytes())
    if damage == 'plan_digest': report['plan_sha256'] = '0' * 64
    if damage == 'closure': report['candidate_reference_closure_checked'] = False
    if damage == 'pinned_scope': report['pinned_candidates'] = ['evidence/unapproved.json']
    review.write_text(json.dumps(report))
    digest = hashlib.sha256(review.read_bytes()).hexdigest() if damage != 'review_digest' else '0' * 64
    archive = tmp_path / 'archive'
    with pytest.raises(ValueError):
        retention.prepare_reviewed(root, archive, plan=plan, review=review, expected_review_sha256=digest)
    assert not archive.exists() and all((root / n).exists() for n in names)


def test_review_evidence_tampering_blocks_apply(tmp_path):
    root, names, plan, review = reviewed_fixture(tmp_path); archive = tmp_path / 'archive'
    retention.prepare_reviewed(root, archive, plan=plan, review=review,
                               expected_review_sha256=hashlib.sha256(review.read_bytes()).hexdigest())
    (archive / 'review.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='EVIDENCE_DIGEST'): retention.apply(root, archive)
    assert all((root / n).exists() for n in names)


def test_all_candidates_pinned_is_noop_without_transaction_directory(tmp_path):
    root, names, plan, review = reviewed_fixture(tmp_path)
    report = json.loads(review.read_bytes())
    report.update(pinned_files=2, pinned_candidates=names[:2])
    review.write_text(json.dumps(report)); archive = tmp_path / 'archive'
    result = retention.prepare_reviewed(root, archive, plan=plan, review=review,
                                        expected_review_sha256=hashlib.sha256(review.read_bytes()).hexdigest())
    assert result['status'] == 'NO_ELIGIBLE_RECORDS'
    assert not archive.exists() and all((root / n).exists() for n in names)
