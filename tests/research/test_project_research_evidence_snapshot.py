"""Actual project DB/WAL, complete source tree and independent trust file reads."""
import copy

import pytest

from quantos.contracts.research_evidence_json import canonical, digest
from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tests.research.test_research_evidence_reader_integration import frozen_source as frozen_source
from tests.validation.test_project_research_evidence_authority import signed_project


@pytest.fixture
def project_snapshot(registered_case, frozen_source):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.research.project_research_evidence_snapshot import ProjectResearchEvidenceSnapshotReader
    f = registered_case
    source, identity, files = frozen_source
    f['arguments']['release_identity'] = copy.deepcopy(identity)
    plans = [f['prepare'](name) for name in ('one', 'two')]
    for name in ('one', 'two'):
        execute(f, name)
    cut_reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    manifest = dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
        source_identity_sha256=digest(identity), files=files)
    snapshot, _ = signed_project(cut_reader(), plans[0]['invocation_id'], now=f['clock'][0], source_manifest=manifest)
    material = f['root']/'materials'; material.mkdir(mode=0o700)
    for key in ('subject', 'source_manifest', 'source_envelope', 'result_envelope'):
        path = material/(key+'.json')
        path.write_bytes(canonical(snapshot[key])); path.chmod(0o600)
    governance = f['root']/'governance'; governance.mkdir(mode=0o700)
    trust = governance/'registry.json'; trust.write_bytes(canonical(snapshot['trust_registry'])); trust.chmod(0o600)
    reader = ProjectResearchEvidenceSnapshotReader(material, project_root=f['root']/'project',
        registry_database=f['root']/'registry.db', source_directory=source, source_commit=identity['commit'],
        producer_id='isolated-native', trust_registry=trust,
        expected_registry_sha256=digest(trust.read_bytes()), minimum_generation=1)
    return f, reader, snapshot, trust, source


def test_fixed_project_consumer_reads_originals_and_never_grants_eligibility(project_snapshot):
    from services.validation.project_research_evidence_authority import consume_project_evidence
    f, reader, snapshot, _, _ = project_snapshot
    before = {p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}
    assessment = consume_project_evidence(read_snapshot=reader, expected_subject=snapshot['subject'],
                                         clock_ms=lambda:f['clock'][0])
    assert assessment['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert assessment['qualification'] == 'INSUFFICIENT'
    assert assessment['projection']['attempt_count'] == 6
    assert assessment['promotion_approved'] is assessment['submission_enabled'] is False
    assert before == {p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}


def test_fixed_project_reader_rejects_real_trust_bytes_replacement(project_snapshot):
    _, reader, _, trust, _ = project_snapshot
    trust.write_bytes(trust.read_bytes()+b'\n')
    with pytest.raises(ValueError, match='TRUST_PIN'):
        reader()


def test_fixed_project_reader_checks_source_files_after_population_read(project_snapshot, monkeypatch):
    from services.research import project_research_evidence_snapshot as module
    _, reader, _, _, source = project_snapshot
    old = module._source
    added = source/'tools'/'new_unlisted.py'
    def changed(*args, **kwargs):
        old(*args, **kwargs)
        added.write_text('# Changed only in the isolated source fixture.\n')
        added.chmod(0o600)
    monkeypatch.setattr(module, '_source', changed)
    try:
        with pytest.raises(ValueError, match='CHANGED'):
            reader()
    finally:
        added.unlink(missing_ok=True)


def test_fixed_project_reader_rejects_new_original_attempt_after_signed_cut(project_snapshot):
    f, reader, _, _, _ = project_snapshot
    f['prepare']('later')
    with pytest.raises(ValueError, match='SUBJECT_BINDING'):
        reader()
