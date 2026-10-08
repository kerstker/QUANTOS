"""Original evaluation rows require all actual reports in their retained cut."""
import json

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tests.tools.test_registered_gold_chunked_result import storage
from tests.validation.test_project_research_evidence_authority import signed_project


@pytest.fixture
def chunked_review(registered_case):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f = registered_case
    plans = [f['prepare'](name, result_storage=storage()) for name in ('one', 'two')]
    for name in ('one', 'two'):
        execute(f, name)
    reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    with reader.evidence() as (cut, reports):
        snapshot, _ = signed_project(cut, plans[0]['invocation_id'], now=f['clock'][0], reports=reports)
        yield f, reader, snapshot, reports


def test_original_artifact_and_row_are_recomputed_from_all_full_reports(chunked_review):
    from services.validation import project_research_evidence_artifact as artifact
    from services.validation import project_research_evidence_authority as authority
    f, _, snapshot, reports = chunked_review
    assessment = authority.verify_project_evidence(snapshot, now_ms=f['clock'][0], reports=reports)
    value = artifact.prepare(snapshot=snapshot, assessment=assessment, reports=reports)
    row = artifact.row_fields(value, reports=reports)
    assert artifact.validate_row(row, reports=reports) == value
    assert artifact.review_identity(value, reports=reports)
    assert row['verdict'] == 'INSUFFICIENT'
    assert value['assessment']['promotion_approved'] is value['assessment']['submission_enabled'] is False
    retained = json.loads(row['evaluation_json'])
    assert retained['snapshot']['project_cut']['attempt_count'] == 6
    assert 'reports' not in retained['snapshot']
    with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
        artifact.validate_row(row)
    selected = snapshot['subject']['invocation_id']
    with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
        artifact.validate_row(row, reports={selected:reports[selected]})


def test_original_review_only_delta_verifies_new_row_against_complete_material(chunked_review):
    from tests.validation.test_project_research_evidence_artifact import with_evaluation
    from services.validation import project_research_evidence_artifact as artifact
    from services.validation import project_research_evidence_authority as authority
    f, _, snapshot, reports = chunked_review
    value = artifact.prepare(snapshot=snapshot,
        assessment=authority.verify_project_evidence(snapshot, now_ms=f['clock'][0], reports=reports),
        reports=reports)
    row = artifact.row_fields(value, reports=reports)
    old = snapshot['project_cut']
    current = with_evaluation(old, row)
    artifact.verify_current_cut(old, current, review_rows={row['run_id']:row}, reports=reports)
    with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
        artifact.verify_current_cut(old, current, review_rows={row['run_id']:row})
