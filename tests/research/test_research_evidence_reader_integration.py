"""Original Gold archive -> fixed files/ledger reader -> signed review consumer.

The source is a real isolated Git clone and keys are ephemeral test identities.
This proves software composition, never an installed independent authority.
"""
import hashlib
import os
from pathlib import Path
import platform
import subprocess

import pytest

from services.research.research_evidence_reader import ResearchEvidenceSnapshotReader
from services.research.trial_ledger import TrialLedger, TrialRecord
from services.validation.research_evidence_authority import consume_evidence, validate_assessment
from tests.validation.test_research_evidence_authority import signed_case
from tests.validation.test_research_result_projection import _gold, digest, encode


@pytest.fixture(scope='module')
def frozen_source(tmp_path_factory):
    destination = tmp_path_factory.mktemp('evidence-source') / 'source'
    original = Path(__file__).resolve().parents[2]
    subprocess.run(['git', 'clone', '--quiet', '--no-local', str(original), str(destination)],
                   check=True, capture_output=True)
    files = {}
    accumulator = hashlib.sha256()
    for path in sorted(p for folder in ('services', 'quantos', 'tools')
                       for p in (destination / folder).rglob('*.py')):
        name = path.relative_to(destination).as_posix()
        raw = path.read_bytes()
        files[name] = digest(raw)
        accumulator.update(name.encode() + b'\0' + raw + b'\0')
    for path in (destination, *destination.rglob('*')):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o022)
    identity = dict(commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=destination,
        text=True).strip(), python_source_sha256=accumulator.hexdigest(),
        dependency_lock_sha256=digest((destination / 'requirements-ci-linux-aarch64.lock').read_bytes()),
        python_version=platform.python_version(), architecture=platform.machine())
    return destination, identity, files


@pytest.fixture
def native_reader(tmp_path, monkeypatch, frozen_source):
    from tools import gold_bar_definition as definition
    source, identity, files = frozen_source
    monkeypatch.setattr(definition, 'ROOT', source)
    v = _gold(tmp_path, monkeypatch, source_identity=identity)
    originals = dict(v['raw'], source_identity=encode(identity),
        source_manifest=encode(dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
            source_identity_sha256=digest(encode(identity)), files=files)),
        candidate_definition=encode(v['candidate']), dataset_manifest=encode(v['dataset']),
        trial_history=encode(v['history']), risk_replay=None,
        evaluation_plan=encode(dict(schema='QUANTOS_RESEARCH_EVALUATION_PLAN_V1',
            candidate_id=v['candidate_id'], evidence_kind=v['kind'], registered_at_ms=v['start']-1,
            start_ms=v['start'], end_ms=v['end'], frequency_ms=1000, trial_ids=list(v['history']))))
    subject = dict(schema='QUANTOS_RESEARCH_EVIDENCE_SUBJECT_V1', purpose='CANDIDATE_RESEARCH_REVIEW',
        evidence_kind=v['kind'], native_adapter=v['adapter'], producer_id='isolated-research',
        candidate_id=v['candidate_id'], market='XAUUSDT', native_run_id=v['run_id'])
    for field, role in (('candidate_definition_sha256', 'candidate_definition'),
        ('source_identity_sha256', 'source_identity'), ('original_record_sha256', 'record'),
        ('original_manifest_sha256', 'manifest'), ('dataset_manifest_sha256', 'dataset_manifest'),
        ('trial_history_sha256', 'trial_history'), ('evaluation_plan_sha256', 'evaluation_plan')):
        subject[field] = digest(originals[role])
    now = v['end'] + 1000
    snapshot, _ = signed_case(dict(subject=subject, originals=originals, now_ms=now))
    bundle = tmp_path / 'materials'
    bundle.mkdir(mode=0o700)
    for name, raw in originals.items():
        if raw is not None:
            (bundle / (name + '.json')).write_bytes(raw)
    for name in ('subject', 'source_envelope', 'result_envelope'):
        (bundle / (name + '.json')).write_bytes(encode(snapshot[name]))
    governance = tmp_path / 'governance'
    governance.mkdir(mode=0o700)
    trust = governance / 'registry.json'
    trust.write_bytes(encode(snapshot['trust_registry']))
    for path in tmp_path.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    reader = ResearchEvidenceSnapshotReader(bundle, source_directory=source,
        trial_database=v['ledger_path'], trust_registry=trust, expected_registry_sha256=digest(trust.read_bytes()),
        minimum_generation=1, source_commit=identity['commit'], producer_id='isolated-research')
    return reader, subject, now, v, trust


def _original_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_native_reader_and_two_signature_consumer_preserve_insufficient_and_originals(native_reader, tmp_path):
    reader, subject, now, _, _ = native_reader
    before = _original_bytes(tmp_path)
    assessment = consume_evidence(read_snapshot=reader, expected_subject=subject, clock_ms=lambda: now)
    assert assessment['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert assessment['qualification'] == 'INSUFFICIENT'
    assert assessment['promotion_approved'] is assessment['submission_enabled'] is False
    assert assessment['projection']['evidence_kind'] == 'FORWARD'
    assert assessment['projection']['metrics']['annualized_sharpe'] is None
    assert _original_bytes(tmp_path) == before


def test_persisted_assessment_cannot_survive_governance_key_replacement(native_reader):
    reader, subject, now, _, trust = native_reader
    assessment = consume_evidence(read_snapshot=reader, expected_subject=subject, clock_ms=lambda: now)
    trust.write_bytes(trust.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='TRUST_PIN'):
        validate_assessment(assessment, read_snapshot=reader, expected_subject=subject, clock_ms=lambda: now+1)


def test_new_native_trial_after_signature_cannot_be_hidden_from_consumption(native_reader):
    reader, subject, now, v, _ = native_reader
    with TrialLedger(str(v['ledger_path']), create=False) as ledger:
        ledger.append(TrialRecord('1' * 64, '2' * 64, '3' * 64, '4' * 64, '5' * 64,
                                  'PLANNED', 0, now))
    os.chmod(v['ledger_path'], 0o600)
    with pytest.raises(ValueError, match='TRIAL_HISTORY'):
        consume_evidence(read_snapshot=reader, expected_subject=subject, clock_ms=lambda: now)
