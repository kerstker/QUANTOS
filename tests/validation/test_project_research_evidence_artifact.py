"""Retained independent reviews, with exact current-population deltas."""
import copy
import os

import pytest

from quantos.contracts.research_evidence_json import canonical, digest
from tests.validation.test_project_research_evidence_authority import (
    project_review as project_review, registered_case as registered_case, signed_project,
)
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments, join


def prepared(fixture):
    from services.validation import project_research_evidence_artifact as artifact
    from services.validation import project_research_evidence_authority as authority
    f, _, snapshot, _ = fixture
    assessment = authority.verify_project_evidence(snapshot, now_ms=f['clock'][0])
    return artifact.prepare(snapshot=snapshot, assessment=assessment)


def with_evaluation(cut, row):
    value = copy.deepcopy(cut)
    value['registry_history']['evaluations'].append({k:row[k] for k in
        ('run_id', 'dataset_version', 'recorded_ts', 'content_hash')})
    value['registry_history']['evaluations'].sort(key=lambda r:r['run_id'])
    value['cut_sha256'] = digest({k:v for k,v in value.items()
        if k not in ('cut_sha256', 'read_identity_sha256')})
    value['read_identity_sha256'] = digest('new-physical-read')
    return value


def test_retained_selected_completion_keeps_other_unfinished_attempts(project_review):
    from services.validation import project_research_evidence_artifact as artifact
    value = prepared(project_review)
    row = artifact.row_fields(value)
    assert artifact.validate_row(row) == value
    assert row['verdict'] == 'INSUFFICIENT'
    assert value['assessment']['projection']['attempt_count'] == 9
    assert value['assessment']['projection']['unfinished_attempt_count'] == 3
    run = value['snapshot']['project_cut']['runs'][value['snapshot']['subject']['invocation_id']]
    assert row['recorded_ts'] >= run['ledger']['history'][value['snapshot']['subject']['native_trial_id']][-1]['at']
    assert value['assessment']['promotion_approved'] is False


def test_unknown_selected_attempt_cannot_be_retained_as_completed(project_review):
    from services.validation import project_research_evidence_artifact as artifact
    from services.validation import project_research_evidence_authority as authority
    f, reader, _, _ = project_review
    cut = reader(); pending = cut['unfinished_runs'][0]
    snapshot, _ = signed_project(cut, pending, now=f['clock'][0])
    assessment = authority.verify_project_evidence(snapshot, now_ms=f['clock'][0])
    with pytest.raises(ValueError, match='SELECTED_NOT_FINALIZED'):
        artifact.prepare(snapshot=snapshot, assessment=assessment)


@pytest.mark.parametrize('which,purpose', [('source_envelope','SOURCE_ATTESTATION'),
                                         ('result_envelope','RESULT_REVIEW')])
def test_signed_review_cannot_claim_it_preceded_the_project_facts(project_review, which, purpose):
    from services.validation import project_research_evidence_artifact as artifact
    from services.validation import project_research_evidence_authority as authority
    f, _, original, sign = project_review
    snapshot = copy.deepcopy(original)
    statement = snapshot[which]['statement']
    statement['issued_at_ms'] = f['clock'][0]-1
    snapshot[which] = sign(purpose, statement)
    if which == 'source_envelope':
        result = snapshot['result_envelope']['statement']
        result['source_envelope_sha256'] = digest(snapshot['source_envelope'])
        snapshot['result_envelope'] = sign('RESULT_REVIEW', result)
    assessment = authority.verify_project_evidence(snapshot, now_ms=f['clock'][0])
    with pytest.raises(ValueError, match='STATEMENT_BEFORE_PROJECT_FACTS'):
        artifact.prepare(snapshot=snapshot, assessment=assessment)


def test_only_check_time_and_physical_observation_may_change_on_retry(project_review):
    from services.validation import project_research_evidence_artifact as artifact
    from services.validation import project_research_evidence_authority as authority
    value = prepared(project_review); changed = copy.deepcopy(value)
    changed['snapshot']['read_identity_sha256'] = digest('second-read')
    changed['snapshot']['project_cut']['read_identity_sha256'] = digest('second-read')
    changed['assessment'] = authority.verify_project_evidence(changed['snapshot'],
        now_ms=value['assessment']['checked_at_ms']+1)
    assert artifact.review_identity(changed) == artifact.review_identity(value)
    assert artifact.row_fields(changed)['run_id'] == artifact.row_fields(value)['run_id']
    assert artifact.row_fields(changed)['content_hash'] != artifact.row_fields(value)['content_hash']


def test_self_review_metadata_is_a_valid_explicit_current_delta(project_review):
    from services.validation import project_research_evidence_artifact as artifact
    value = prepared(project_review); old = value['snapshot']['project_cut']; row = artifact.row_fields(value)
    current = with_evaluation(old, row)
    artifact.verify_current_cut(old, current, review_rows={row['run_id']:row})
    artifact.verify_registry_prefix(value, population=current['registry_population'], history=current['registry_history'])
    assert current['cut_sha256'] != old['cut_sha256']
    assert len(current['registry_history']['evaluations']) == len(old['registry_history']['evaluations'])+1
    assert canonical(value['snapshot']['project_cut']) == canonical(old)


@pytest.mark.parametrize('damage', ['missing-row', 'extra-row', 'metadata', 'body', 'unrelated-review'])
def test_review_only_delta_requires_exact_full_original_rows(project_review, damage):
    from services.validation import project_research_evidence_artifact as artifact
    value = prepared(project_review); old = value['snapshot']['project_cut']; row = artifact.row_fields(value)
    current = with_evaluation(old, row); rows = {row['run_id']:copy.deepcopy(row)}
    if damage == 'missing-row':
        rows.clear()
    elif damage == 'extra-row':
        rows['f'*64] = copy.deepcopy(row)
    elif damage == 'metadata':
        current['registry_history']['evaluations'][-1]['recorded_ts'] += 1
        current['cut_sha256'] = digest({k:v for k,v in current.items() if k not in ('cut_sha256','read_identity_sha256')})
    elif damage == 'body':
        rows[row['run_id']]['evaluation_json'] = '{}'
    else:
        f, _, snapshot, _ = project_review
        other = next(i for i in old['runs'] if i != snapshot['subject']['invocation_id'] and i not in old['unfinished_runs'])
        changed, _ = signed_project(old, other, now=f['clock'][0])
        from services.validation import project_research_evidence_authority as authority
        different = artifact.prepare(snapshot=changed, assessment=authority.verify_project_evidence(changed, now_ms=f['clock'][0]))
        rows[row['run_id']] = artifact.row_fields(different)
    with pytest.raises(ValueError):
        artifact.verify_current_cut(old, current, review_rows=rows)


def test_real_new_attempt_preserves_history_but_invalidates_current_review(project_review):
    from services.validation import project_research_evidence_artifact as artifact
    value = prepared(project_review); f, reader, _, _ = project_review
    f['prepare']('after-review')
    current = reader()
    artifact.verify_registry_prefix(value, population=current['registry_population'], history=current['registry_history'])
    with pytest.raises(ValueError, match='CURRENT_PROJECT_CHANGED'):
        artifact.verify_current_cut(value['snapshot']['project_cut'], current, review_rows={})


@pytest.mark.parametrize('column', ['run_id', 'alpha_id', 'dataset_version', 'recorded_ts', 'verdict', 'content_hash'])
def test_all_original_row_columns_are_bound(project_review, column):
    from services.validation import project_research_evidence_artifact as artifact
    row = artifact.row_fields(prepared(project_review))
    row[column] = row[column]+1 if column == 'recorded_ts' else 'corrupt'
    with pytest.raises(ValueError, match='ROW_BINDING'):
        artifact.validate_row(row)


@pytest.mark.parametrize('stage',['invocation.json','risk-replay.json','outcome.json'])
def test_other_unfinished_run_actual_witness_time_bounds_entire_signed_cut(registered_case,technical_arguments,stage):
    from tests.tools.test_registered_risk_replay import prepare,run
    from tests.research.test_project_risk_replay_reader import gold
    from tools import registered_technical_research_run as tool
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import project_research_evidence_authority as authority
    from services.validation import project_research_evidence_artifact as artifact
    f=registered_case;args=gold(technical_arguments);first=prepare(f,args);run(f,args,first)
    later=prepare(f,args,invocation='8'*64,search_id='later-partial')
    pid=os.fork()
    if pid==0:
        try:
            put=tool.common._put
            before={'invocation.json':'running.json','risk-replay.json':'report.json','outcome.json':'risk-replay.json'}[stage]
            def interrupted(path,*a,**kw):
                result=put(path,*a,**kw)
                if path.name==before:f['clock'][0]+=100
                if path.name==stage:os._exit(71)
                return result
            tool.common._put=interrupted;run(f,args,later)
        except BaseException:
            import traceback;traceback.print_exc();os._exit(98)
        os._exit(99)
    assert join(pid)==71
    cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    assert later['invocation_id'] in cut['unfinished_runs']
    early,_=signed_project(cut,first['invocation_id'],now=f['clock'][0])
    assessed=authority.verify_project_evidence(early,now_ms=f['clock'][0]+100)
    with pytest.raises(ValueError,match='STATEMENT_BEFORE_PROJECT_FACTS'):
        artifact.prepare(snapshot=early,assessment=assessed)
    current,_=signed_project(cut,first['invocation_id'],now=f['clock'][0]+100)
    artifact.prepare(snapshot=current,assessment=authority.verify_project_evidence(current,now_ms=f['clock'][0]+100))
