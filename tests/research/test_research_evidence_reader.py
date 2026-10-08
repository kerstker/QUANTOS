"""The evidence consumer reads complete native history and governed public pins."""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3

import pytest

from services.research.trial_ledger import TrialLedger, TrialRecord


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def material(tmp_path):
    source = tmp_path / 'source'
    files = {}
    digest = hashlib.sha256()
    for folder in ('quantos', 'services', 'tools'):
        (source / folder).mkdir(parents=True)
        path = source / folder / 'original.py'
        path.write_text('VALUE = 1\n')
        files[path.relative_to(source).as_posix()] = sha(path.read_bytes())
    for name in sorted(files):
        digest.update(name.encode() + b'\0' + (source / name).read_bytes() + b'\0')
    (source / 'requirements-ci-linux-aarch64.lock').write_bytes(b'# isolated lock\n')
    identity = dict(commit='a' * 40, python_source_sha256=digest.hexdigest(),
        dependency_lock_sha256=sha((source / 'requirements-ci-linux-aarch64.lock').read_bytes()),
        python_version=platform.python_version(), architecture=platform.machine())
    archive = tmp_path / 'native'
    archive.mkdir()
    database = archive / 'trials.db'
    tid = '1' * 64
    with TrialLedger(str(database)) as ledger:
        for index, status in enumerate(('PLANNED', 'RUNNING', 'FAILED')):
            ledger.append(TrialRecord(tid, '2' * 64, '3' * 64, '4' * 64, '5' * 64,
                                      status, index, 100 + index))
        history = {tid: [dataclasses.asdict(v) for v in ledger.load(tid).events]}
    bundle = tmp_path / 'evidence'
    bundle.mkdir()
    subject = {'producer_id': 'local-research', 'candidate_id': 'candidate'}
    values = dict(subject=subject, source_envelope={}, result_envelope={},
        source_identity=identity, source_manifest={'schema': 'QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
            'source_identity_sha256': sha(encode(identity)), 'files': files},
        candidate_definition={}, manifest={}, input={}, record={}, dataset_manifest={},
        trial_history=history, evaluation_plan={'trial_ids': [tid]})
    for name, value in values.items():
        (bundle / (name + '.json')).write_bytes(encode(value))
    trust = tmp_path / 'governance' / 'registry.json'
    trust.parent.mkdir()
    trust.write_bytes(encode({'schema': 'QUANTOS_RESEARCH_EVIDENCE_TRUST_V1',
                             'generation': 2, 'producer_ids': ['local-research'], 'keys': {}}))
    for path in tmp_path.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    return dict(bundle=bundle, source=source, database=database, trust=trust,
                identity=identity, values=values, trust_sha=sha(trust.read_bytes()), tid=tid)


def reader(material):
    from services.research.research_evidence_reader import ResearchEvidenceSnapshotReader
    return ResearchEvidenceSnapshotReader(material['bundle'], source_directory=material['source'],
        trial_database=material['database'], trust_registry=material['trust'],
        expected_registry_sha256=material['trust_sha'], minimum_generation=2,
        source_commit=material['identity']['commit'], producer_id='local-research')


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_reads_complete_failed_trial_without_writing_originals(material, tmp_path):
    before = tree_bytes(tmp_path)
    value = reader(material)()
    assert tree_bytes(tmp_path) == before
    assert value['schema'] == 'QUANTOS_RESEARCH_EVIDENCE_SNAPSHOT_V1'
    assert value['originals']['risk_replay'] is None
    assert json.loads(value['originals']['trial_history'])[material['tid']][-1]['status'] == 'FAILED'
    assert value['expected_registry_sha256'] == material['trust_sha']
    assert value == reader(material)()


def test_cannot_omit_failed_or_planned_native_trials(material):
    with TrialLedger(str(material['database']), create=False) as ledger:
        ledger.append(TrialRecord('6' * 64, '7' * 64, '8' * 64, '9' * 64, 'a' * 64,
                                  'PLANNED', 0, 110))
    with pytest.raises(ValueError, match='TRIAL_HISTORY'):
        reader(material)()


@pytest.mark.parametrize('attack', ['symlink', 'hardlink', 'group_write', 'extra', 'duplicate_json'])
def test_material_custody_and_exact_members_reject(material, attack):
    path = material['bundle'] / 'record.json'
    if attack == 'symlink':
        path.unlink(); path.symlink_to(material['bundle'] / 'input.json')
    elif attack == 'hardlink':
        os.link(path, material['bundle'].parent / 'alias.json')
    elif attack == 'group_write':
        path.chmod(0o660)
    elif attack == 'extra':
        (material['bundle'] / 'trust_registry.json').write_text('{}')
    else:
        path.write_bytes(b'{"x":1,"x":2}')
    with pytest.raises(ValueError):
        reader(material)()


@pytest.mark.parametrize('attack', ['source_file', 'added_source', 'cache_source', 'lock', 'identity_bool', 'trust_changed', 'trust_old_generation'])
def test_source_and_trust_are_read_from_originals_and_operator_pin(material, attack):
    if attack == 'source_file':
        (material['source'] / 'tools/original.py').write_text('VALUE = 2\n')
    elif attack == 'added_source':
        (material['source'] / 'tools/omitted.py').write_text('HIDDEN = 1\n')
        (material['source'] / 'tools/omitted.py').chmod(0o600)
    elif attack == 'cache_source':
        directory = material['source'] / 'tools/__pycache__'
        directory.mkdir(mode=0o700)
        (directory / 'omitted.py').write_text('HIDDEN = 1\n')
        (directory / 'omitted.py').chmod(0o600)
    elif attack == 'lock':
        (material['source'] / 'requirements-ci-linux-aarch64.lock').write_text('changed')
    elif attack == 'identity_bool':
        value = dict(material['identity'], python_source_sha256=True)
        (material['bundle'] / 'source_identity.json').write_bytes(encode(value))
    elif attack == 'trust_changed':
        material['trust'].write_bytes(material['trust'].read_bytes() + b'\n')
    else:
        value = json.loads(material['trust'].read_bytes()); value['generation'] = 1
        material['trust'].write_bytes(encode(value))
        material['trust_sha'] = sha(material['trust'].read_bytes())
    with pytest.raises(ValueError):
        reader(material)()


def test_trial_corruption_rejected_by_original_ledger_verifier(material):
    with sqlite3.connect(material['database']) as conn:
        conn.execute("UPDATE trial_heads SET head_hash='broken'")
    with pytest.raises((ValueError, RuntimeError)):
        reader(material)()


def test_consumer_cannot_pin_registry_from_inside_research_materials(material):
    from services.research.research_evidence_reader import ResearchEvidenceSnapshotReader
    with pytest.raises(ValueError, match='TRUST_LOCATION'):
        ResearchEvidenceSnapshotReader(material['bundle'], source_directory=material['source'],
            trial_database=material['database'], trust_registry=material['bundle'] / 'registry.json',
            expected_registry_sha256=material['trust_sha'], minimum_generation=2,
            source_commit=material['identity']['commit'], producer_id='local-research')


def test_wal_copy_reads_committed_native_events_without_creating_original_shm(material, tmp_path):
    # Keep a writer open so committed rows exist in WAL instead of only main DB.
    writer = sqlite3.connect(material['database'])
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('BEGIN')
    writer.execute('SELECT count(*) FROM trial_events').fetchone()
    try:
        with TrialLedger(str(material['database']), create=False) as ledger:
            ledger.append(TrialRecord('6' * 64, '7' * 64, '8' * 64, '9' * 64, 'a' * 64,
                                      'PLANNED', 0, 110))
            history = {tid: [dataclasses.asdict(v) for v in ledger.load(tid).events]
                       for tid in (material['tid'], '6' * 64)}
        assert Path(str(material['database']) + '-wal').stat().st_size > 0
        (material['bundle'] / 'trial_history.json').write_bytes(encode(history))
        (material['bundle'] / 'evaluation_plan.json').write_bytes(encode({'trial_ids': list(history)}))
        before = tree_bytes(tmp_path)
        snapshot = reader(material)()
        assert tree_bytes(tmp_path) == before
        assert set(json.loads(snapshot['originals']['trial_history'])) == set(history)
    finally:
        writer.close()


@pytest.mark.parametrize('attack', ['same_bytes_new_inode', 'late_extra_member', 'late_source_change'])
def test_changes_after_initial_reads_reject_before_snapshot_returns(material, monkeypatch, attack):
    from services.research import research_evidence_reader as module
    original = module._trials

    def changed(*args):
        original(*args)
        if attack == 'same_bytes_new_inode':
            path = material['bundle'] / 'record.json'
            raw = path.read_bytes()
            path.rename(path.with_suffix('.old'))
            path.write_bytes(raw)
            path.chmod(0o600)
            path.with_suffix('.old').unlink()
        elif attack == 'late_extra_member':
            (material['bundle'] / 'new.json').write_text('{}')
        else:
            (material['source'] / 'tools/original.py').write_text('VALUE = 2\n')

    monkeypatch.setattr(module, '_trials', changed)
    with pytest.raises(ValueError, match='CHANGED'):
        reader(material)()


def test_nested_json_bound_is_checked_before_interpreting_material(material):
    value = None
    for _ in range(66):
        value = [value]
    (material['bundle'] / 'record.json').write_bytes(encode(value))
    with pytest.raises(ValueError, match='JSON_BOUND'):
        reader(material)()
