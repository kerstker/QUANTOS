"""Original production consumer retains and cold-verifies complete Gold reports."""
import json
from contextlib import closing

import pytest

from quantos.contracts.research_evidence_json import canonical, digest
from tests.research.test_project_chunked_review_request import exported as exported
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.research.test_registered_research_review_job import independent_response


@pytest.mark.parametrize('exported', [None, 'source', 'source-budgeted','source-net-capacity-budgeted','source-net-causality-budgeted','source-net-forward-budgeted','source-net-maintenance-budgeted'], indirect=True)
def test_production_signed_consumer_recovery_and_cold_read_use_all_original_reports(exported, monkeypatch):
    from tools import registered_research_review_job as review
    from tools import registered_research_job as producer
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.project_result_material_reader import ProjectResultMaterialReader

    f, plans, _, packet, _ = exported
    population = f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])
    prepared = review.prepare_review_request({'native_invocation_id':plans[0]['invocation_id']})
    params = {'review_request_id':prepared['review_request_id']}
    packet = review.read_request(params['review_request_id'])
    trust = f['root']/'independent-governance/registry.json'
    independent_response(packet, review.response_directory(params['review_request_id']), trust)
    registry = json.loads(trust.read_bytes())
    registry['producer_ids'] = ['REGISTERED_GOLD_COLLECTOR_V1']
    registry['keys']['fixture']['evidence_kinds'] = ['BACKTEST', 'FORWARD']
    trust.write_bytes(canonical(registry))
    trust.chmod(0o444)
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_TRUST_FILE', str(trust))
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_TRUST_SHA256', digest(trust.read_bytes()))
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_MIN_GENERATION', '1')

    result = review.consume_review(params)
    assert result['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert result['qualification'] == 'INSUFFICIENT'
    assert all(result[key] is False for key in ('economic_authority', 'promotion_approved', 'submission_enabled'))
    assert review.recover_review(params)['review_id'] == result['review_id']
    _, database = producer.workspace_paths()
    with closing(SqliteExperimentStore(str(database), create=False, read_only=True,
            project_result_reader=ProjectResultMaterialReader(f['root']/'project'))) as cold:
        cold_row=cold.get_project_research_evidence(result['review_id'])
        assert cold_row['verdict'] == 'INSUFFICIENT'
        if 'net_forward_policy' in plans[0]['forward_design']['risk_policy']:
            retained=json.loads(cold_row['evaluation_json'])['assessment']['projection']
            model=retained['execution_risk_review']['account_risk_inputs']['model_inputs']
            expected=packet['projection']['execution_risk_review']['account_risk_inputs']['model_inputs']
            assert retained['schema']==('QUANTOS_PROJECT_RESEARCH_FACTS_V17' if 'maintenance_policy' in plans[0]['forward_design']['risk_policy'] else 'QUANTOS_PROJECT_RESEARCH_FACTS_V16')
            assert model==expected and model['net_forward_review']['status']=='INSUFFICIENT'
            assert model['facts']['paper_confidence']['value'] is None
            if 'maintenance_policy' in plans[0]['forward_design']['risk_policy']:
                assert model['maintenance_review']['status']=='INSUFFICIENT'
                assert model['facts']['decay_flag']['value'] is None
                assert model['maintenance_review']['lifecycle_changed'] is False
        assert cold._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
        assert cold._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
        assert cold._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0] == 0
    # A nonselected full report is required by both recovery and cold integrity.
    blob = next((f['directories']['two']/'archive/report-parts/blobs').iterdir())
    original = blob.read_bytes()
    try:
        blob.write_bytes(original+b' ')
        with pytest.raises(ValueError):
            review.recover_review(params)
        with closing(SqliteExperimentStore(str(database), create=False, read_only=True,
                project_result_reader=ProjectResultMaterialReader(f['root']/'project'))) as cold:
            with pytest.raises(ValueError):
                cold.get_project_research_evidence(result['review_id'])
    finally:
        blob.write_bytes(original)
    if packet['request']['schema'] == 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V3':
        # Historical signed integrity includes every original input, even from
        # a run that was not selected by the review subject.
        blob = next((f['directories']['two']/'input-parts/blobs').iterdir())
        original = blob.read_bytes()
        try:
            blob.write_bytes(original+b' ')
            with pytest.raises(ValueError):
                review.recover_review(params)
            with closing(SqliteExperimentStore(str(database), create=False, read_only=True,
                    project_result_reader=ProjectResultMaterialReader(f['root']/'project'))) as cold:
                with pytest.raises(ValueError):
                    cold.get_project_research_evidence(result['review_id'])
        finally:
            blob.write_bytes(original)
