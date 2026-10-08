"""Original signed project snapshots hold every report through consumption."""
import copy
from contextlib import closing, contextmanager

import pytest

from quantos.contracts.research_evidence_json import canonical, digest
from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tests.tools.test_registered_gold_chunked_result import storage
from tests.research.test_research_evidence_reader_integration import frozen_source as frozen_source
from tests.validation.test_project_research_evidence_authority import signed_project


@pytest.fixture
def complete_snapshot(registered_case, frozen_source):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.research.project_research_evidence_snapshot import ProjectResearchEvidenceSnapshotReader
    f = registered_case
    source, identity, files = frozen_source
    f['arguments']['release_identity'] = copy.deepcopy(identity)
    plans = [f['prepare'](name, result_storage=storage()) for name in ('one', 'two')]
    for name in ('one', 'two'):
        execute(f, name)
    population = f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])
    cut_reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    manifest = dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1', source_identity_sha256=digest(identity), files=files)
    with cut_reader.evidence() as (cut, reports):
        snapshot, _ = signed_project(cut, plans[0]['invocation_id'], now=f['clock'][0],
            source_manifest=manifest, reports=reports)
    material = f['root']/'materials'
    material.mkdir(mode=0o700)
    for name in ('subject', 'source_manifest', 'source_envelope', 'result_envelope'):
        path = material/(name+'.json')
        path.write_bytes(canonical(snapshot[name]))
        path.chmod(0o600)
    governance = f['root']/'governance'
    governance.mkdir(mode=0o700)
    trust = governance/'registry.json'
    trust.write_bytes(canonical(snapshot['trust_registry']))
    trust.chmod(0o600)
    reader = ProjectResearchEvidenceSnapshotReader(material, project_root=f['root']/'project',
        registry_database=f['root']/'registry.db', source_directory=source, source_commit=identity['commit'],
        producer_id='isolated-native', trust_registry=trust, expected_registry_sha256=digest(trust.read_bytes()),
        minimum_generation=1)
    return f, reader, snapshot


def test_original_snapshot_context_holds_reports_and_checks_source_after_consumption(complete_snapshot):
    from services.validation import project_research_evidence_authority as authority
    f, reader, snapshot = complete_snapshot
    with reader.evidence_retained(snapshot['project_cut'], read_review_rows=lambda ids:{}) as (observed, reports):
        assert observed['current_cut']['attempt_count'] == 6
        verified = authority.verify_project_evidence(observed['snapshot'], now_ms=f['clock'][0], reports=reports)
        assert verified['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
        assert verified['qualification'] == 'INSUFFICIENT'
        kept = next(iter(reports.values()))
    with pytest.raises(ValueError, match='CLOSED'):
        kept['trials']
    with pytest.raises(ValueError, match='CHANGED'):
        with reader.evidence_retained(snapshot['project_cut'], read_review_rows=lambda ids:{}):
            blob = next((f['directories']['two']/'archive/report-parts/blobs').iterdir())
            blob.write_bytes(blob.read_bytes())


def test_original_registry_context_persists_recovers_and_checks_current_full_cut(complete_snapshot):
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from services.research.experiment_registry.registry import ExperimentRegistry
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f, reader, snapshot = complete_snapshot
    provider = ProjectResultMaterialReader(f['root']/'project')
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False,
            project_result_reader=provider)) as store:
        registry = ExperimentRegistry(store)
        options = dict(reviewed_cut=snapshot['project_cut'], read_snapshot_context=reader.evidence_retained,
            expected_subject=snapshot['subject'], clock_ms=lambda:f['clock'][0])
        row = registry.record_project_research_evidence(**options)
        f['clock'][0] += 1
        assert registry.record_project_research_evidence(**options) == row
        assert store._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
        assert store._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
        assert store._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0] == 0
        current = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')()
        assert current['attempt_count'] == 6 and len(current['registry_history']['evaluations']) == 1
        verified = registry.preflight_project_research_evidence(row['run_id'],
            read_snapshot_context=reader.evidence_retained, clock_ms=lambda:f['clock'][0])
        assert verified['qualification'] == 'INSUFFICIENT'
        assert verified['promotion_approved'] is verified['submission_enabled'] is False
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True,
            project_result_reader=provider)) as cold:
        assert cold.get_project_research_evidence(row['run_id']) == row


def test_expiry_while_closing_full_material_context_prevents_the_original_write(complete_snapshot):
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from services.research.experiment_registry.registry import ExperimentRegistry
    from services.research.experiment_registry.store import SqliteExperimentStore
    f, reader, snapshot = complete_snapshot
    @contextmanager
    def late(cut, **kwargs):
        with reader.evidence_retained(cut, **kwargs) as materials:
            yield materials
        f['clock'][0] = snapshot['result_envelope']['statement']['expires_at_ms']+1
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False,
            project_result_reader=ProjectResultMaterialReader(f['root']/'project'))) as store:
        with pytest.raises(ValueError, match='EXPIRED'):
            ExperimentRegistry(store).record_project_research_evidence(reviewed_cut=snapshot['project_cut'],
                read_snapshot_context=late, expected_subject=snapshot['subject'], clock_ms=lambda:f['clock'][0])
        assert store._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 0
        assert store._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 0


@pytest.mark.parametrize('damage', ['expiry', 'nonselected-same-byte-rewrite'])
def test_final_material_recheck_rolls_back_the_pending_original_rows(complete_snapshot, damage):
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from services.research.experiment_registry.registry import ExperimentRegistry
    from services.research.experiment_registry.store import SqliteExperimentStore
    f, reader, snapshot = complete_snapshot
    pending = []
    @contextmanager
    def changed(cut, **kwargs):
        with reader.evidence_retained(cut, **kwargs) as materials:
            yield materials
            if store._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0]:
                assert store._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
                pending.append(True)
                if damage == 'expiry':
                    f['clock'][0] = snapshot['result_envelope']['statement']['expires_at_ms']+1
                else:
                    blob = next((f['directories']['two']/'archive/report-parts/blobs').iterdir())
                    blob.write_bytes(blob.read_bytes())
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False,
            project_result_reader=ProjectResultMaterialReader(f['root']/'project'))) as store:
        with pytest.raises(ValueError, match='EXPIRED' if damage == 'expiry' else 'CHANGED'):
            ExperimentRegistry(store).record_project_research_evidence(reviewed_cut=snapshot['project_cut'],
                read_snapshot_context=changed, expected_subject=snapshot['subject'], clock_ms=lambda:f['clock'][0])
        assert pending
        assert store._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 0
        assert store._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 0
