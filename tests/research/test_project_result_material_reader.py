"""Cold original report verification never relies on a retained summary."""
from contextlib import closing

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tests.tools.test_registered_gold_chunked_result import storage
from tests.validation.test_project_research_evidence_authority import signed_project


@pytest.fixture
def original_population(registered_case):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f = registered_case
    plans = [f['prepare'](name, result_storage=storage()) for name in ('one', 'two')]
    for name in ('one', 'two'):
        execute(f, name)
    f['prepare']('not-started')
    population = f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])
    cut = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')()
    return f, plans, cut


def test_report_only_reader_keeps_all_complete_originals_and_no_registry_lease(original_population):
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from quantos.contracts.project_chunked_results import full_report_population
    f, plans, cut = original_population
    before = f['root'].joinpath('registry.db').read_bytes()
    with ProjectResultMaterialReader(f['root']/'project').reports(cut) as reports:
        assert set(reports) == {p['invocation_id'] for p in plans}
        assert cut['attempt_count'] == 9
        full_report_population(cut, reports)
        kept = reports[plans[0]['invocation_id']]
    with pytest.raises(ValueError, match='CLOSED'):
        kept['trials']
    assert f['root'].joinpath('registry.db').read_bytes() == before


def test_original_store_write_retry_and_cold_row_require_the_complete_report_provider(original_population):
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.ports.persistence import ProjectResearchEvidenceArtifactWrite, EvaluationAuditIntent
    from services.validation import project_research_evidence_artifact as artifact
    from services.validation import project_research_evidence_authority as authority
    f, plans, cut = original_population
    provider = ProjectResultMaterialReader(f['root']/'project')
    with provider.reports(cut) as reports:
        snapshot, _ = signed_project(cut, plans[0]['invocation_id'], now=f['clock'][0], reports=reports)
        value = artifact.prepare(snapshot=snapshot,
            assessment=authority.verify_project_evidence(snapshot, now_ms=f['clock'][0], reports=reports),
            reports=reports)
        fields = artifact.row_fields(value, reports=reports)
    intent = EvaluationAuditIntent(event_id='exp-'+fields['run_id'], actor='experiment-registry',
        action='EXPERIMENT_RECORDED', timestamp=fields['recorded_ts'], target=fields['alpha_id'],
        metadata={k:fields[k] for k in ('run_id','content_hash','verdict')})
    write = ProjectResearchEvidenceArtifactWrite(**fields, audit_intent=intent)
    def recheck(retained, before, after, read_rows):
        assert before['population'] == after['population']
        with provider.reports(cut) as reports:
            assert artifact.validate(retained, reports=reports) == value
    callbacks = dict(prepare=lambda view, read_rows:write, recheck=recheck)
    with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
        f['store'].retain_project_research_evidence(**callbacks)
    assert f['store']._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 0
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False,
            project_result_reader=provider)) as store:
        row = store.retain_project_research_evidence(**callbacks)
        assert store.retain_project_research_evidence(**callbacks) == row
        assert store._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
        assert store._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
        assert store._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0] == 0
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True,
            project_result_reader=provider)) as cold:
        assert cold.get_project_research_evidence(row['run_id']) == row
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True)) as missing:
        with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
            missing.get_project_research_evidence(row['run_id'])
    # Even the nonselected report is essential to this historical row.
    blob = next((f['directories']['two']/'archive/report-parts/blobs').iterdir())
    blob.write_bytes(blob.read_bytes()+b' ')
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True,
            project_result_reader=provider)) as corrupt:
        with pytest.raises(ValueError):
            corrupt.get_project_research_evidence(row['run_id'])
