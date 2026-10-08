"""Native post-computation reviews in the original registry transaction.

Signatures and producer source identities are isolated fixtures, not installed
authority. Original producers, ledgers, project reader and validators run.
"""
import copy
import os
from contextlib import closing

import pytest

from quantos.contracts.research_evidence_json import canonical
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import EvaluationAuditIntent, ImmutableArtifactError, ExperimentStoreError
from services.validation import project_research_evidence_artifact as artifact
from services.validation import project_research_evidence_authority as authority
from tests.validation.test_project_research_evidence_authority import (
    project_review as project_review, registered_case as registered_case, signed_project,
)
from tests.tools.test_registered_research_run import join


def make_write(value):
    from services.research.ports.persistence import ProjectResearchEvidenceArtifactWrite
    fields = artifact.row_fields(value)
    return ProjectResearchEvidenceArtifactWrite(**fields, audit_intent=EvaluationAuditIntent(
        event_id='exp-'+fields['run_id'], actor='experiment-registry', action='EXPERIMENT_RECORDED',
        timestamp=fields['recorded_ts'], target=fields['alpha_id'],
        metadata={k:fields[k] for k in ('run_id', 'content_hash', 'verdict')}))


@pytest.fixture
def project_store(project_review):
    f, reader, snapshot, _ = project_review
    store = f['store']
    population = store.research_evidence_population()
    store.upgrade_research_evidence_schema(expected_version=store._schema_version(),
        expected_population_sha256=population['sha256'])
    f.update(reader=reader, snapshot=snapshot)
    return f


def callbacks(f, *, snapshot=None, now=None, before_return=None):
    original = copy.deepcopy(f['snapshot'] if snapshot is None else snapshot)
    when = f['clock'][0] if now is None else now
    value = artifact.prepare(snapshot=original,
        assessment=authority.verify_project_evidence(original, now_ms=when))
    def fresh(rows):
        from services.research.project_research_evidence_reader import _ProjectReads
        return f['reader']._capture(_ProjectReads(), registry_view_for=rows.registry_view_for)
    def prepare(view, rows):
        current = fresh(rows)
        assert canonical(view) == canonical(dict(population=current['registry_population'],
                                                 history=current['registry_history']))
        artifact.verify_current_cut(original['project_cut'], current,
            review_rows=rows(artifact.added_review_ids(original['project_cut'], current)))
        return make_write(value)
    def recheck(retained, before, after, rows):
        # Native files are read afresh; only the current writer's own DB uses
        # its exact same-lease view instead of copying an uncommitted journal.
        current = fresh(rows)
        assert canonical(before) == canonical(dict(population=current['registry_population'],
                                                   history=current['registry_history']))
        artifact.verify_current_cut(original['project_cut'], current,
            review_rows=rows(artifact.added_review_ids(original['project_cut'], current)))
        artifact.verify_registry_prefix(retained, population=after['population'], history=after['history'])
        authority.verify_project_evidence(original, now_ms=when)
        if before_return:
            before_return(retained, before, after)
    return dict(prepare=prepare, recheck=recheck)


def retain(f, **kwargs):
    return f['store'].retain_project_research_evidence(**callbacks(f, **kwargs))


def counts(store):
    return tuple(store._conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
        for name in ('evaluations', 'search_evaluations', 'evaluation_audit_outbox'))


def test_terminal_search_review_uses_original_evaluation_and_audit_without_activity_link(project_store):
    f = project_store
    before = f['store'].project_research_registry_view()
    row = retain(f, now=f['clock'][0]+1)
    assert row['recorded_ts'] == f['clock'][0]+1
    assert counts(f['store']) == (1, 0, 1)
    assert row['verdict'] == 'INSUFFICIENT'
    current = f['store'].project_research_registry_view()
    assert current['population'] == before['population']
    assert current['history']['events'] == before['history']['events']
    assert len(current['history']['evaluations']) == 1
    assert f['store'].read_project_research_review_rows([row['run_id']]) == {row['run_id']:row}
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False)) as cold:
        assert cold.get_project_research_evidence(row['run_id']) == row
        assert cold.project_research_registry_view() == current


def test_retry_keeps_original_bytes_time_and_audit_but_rechecks_current_originals(project_store):
    f = project_store
    first = retain(f)
    seen = []
    retry = retain(f, now=f['clock'][0]+1, before_return=lambda *args:seen.append(args))
    assert retry == first and len(seen) == 1
    assert counts(f['store']) == (1, 0, 1)
    assert f['reader']()['registry_history']['evaluations'][0]['content_hash'] == first['content_hash']


@pytest.mark.parametrize('stage', ['after_evaluation', 'after_audit', 'before_commit'])
def test_native_sql_exception_rolls_back_both_rows(project_store, monkeypatch, stage):
    f = project_store
    def fail(actual):
        if actual == stage:
            raise OSError('isolated SQL boundary interruption')
    monkeypatch.setattr(f['store'], '_evaluation_write_checkpoint', fail)
    with pytest.raises(OSError): retain(f)
    assert counts(f['store']) == (0, 0, 0)
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False)) as cold:
        assert cold.project_research_registry_view()['history']['evaluations'] == []


@pytest.mark.parametrize('stage', ['after_evaluation', 'after_audit', 'before_commit', 'after_commit'])
def test_real_process_exit_does_not_leave_partial_or_duplicate_review(project_store, stage):
    f = project_store
    pid = os.fork()
    if pid == 0:
        try:
            if stage == 'after_commit':
                original = f['store']._commit_owned_transaction
                def commit(conn):
                    original(conn)
                    os._exit(71)
                f['store']._commit_owned_transaction = commit
            else:
                def checkpoint(actual):
                    if actual == stage: os._exit(71)
                f['store']._evaluation_write_checkpoint = checkpoint
            retain(f)
            os._exit(99)
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(98)
    assert join(pid) == 71
    assert counts(f['store']) == ((1, 0, 1) if stage == 'after_commit' else (0, 0, 0))
    row = retain(f, now=f['clock'][0]+1)
    assert counts(f['store']) == (1, 0, 1)
    assert row['recorded_ts'] == f['clock'][0]+(stage != 'after_commit')


def test_fresh_recheck_failure_rolls_back_native_evaluation_and_audit(project_store):
    f = project_store
    def fail(*_): raise ValueError('changed original or expired independent review')
    with pytest.raises(ValueError, match='changed original'):
        retain(f, before_return=fail)
    assert counts(f['store']) == (0, 0, 0)


def test_new_native_attempt_preserves_historical_getter_but_rejects_current_reuse(project_store):
    f = project_store
    first = retain(f)
    f['prepare']('new-actual-attempt')
    assert f['store'].get_project_research_evidence(first['run_id']) == first
    with pytest.raises(ValueError, match='CURRENT_PROJECT_CHANGED'):
        retain(f)
    assert counts(f['store']) == (1, 0, 1)


@pytest.mark.parametrize('damage', ['missing-audit', 'wrong-audit', 'id-conflict', 'linked'])
def test_missing_or_conflicting_originals_are_not_repaired(project_store, damage):
    f = project_store
    value = artifact.prepare(snapshot=f['snapshot'],
        assessment=authority.verify_project_evidence(f['snapshot'], now_ms=f['clock'][0]))
    row = artifact.row_fields(value)
    # Actual imported partial rows, without weakening append-only triggers.
    if damage in ('missing-audit', 'id-conflict', 'wrong-audit'):
        if damage == 'id-conflict': row['evaluation_json'] = '{}'
        f['store']._conn.execute('INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)', tuple(row.values()))
        if damage == 'wrong-audit':
            from services.research.experiment_registry.family_schema_v2_semantics import evaluation_audit_hash
            bad = EvaluationAuditIntent(event_id='wrong-review', actor='wrong', action='OTHER',
                timestamp=row['recorded_ts'], target=row['alpha_id'], metadata={})
            sha = evaluation_audit_hash(run_id=row['run_id'], intent=bad,
                evaluation_content_hash=row['content_hash'], prev_record_hash='')
            f['store']._conn.execute('INSERT INTO evaluation_audit_outbox '
                '(run_id,event_id,actor,action,timestamp,target,metadata_json,evaluation_content_hash,prev_record_hash,record_hash) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)', (row['run_id'],bad.event_id,bad.actor,bad.action,bad.timestamp,
                 bad.target,'{}',row['content_hash'],'',sha))
        f['store']._conn.commit()
    else:
        retain(f)
        from quantos.contracts.research_registry_identity import search_evaluation_hash
        binding = dict(search_id=value['search_identity']['search_id'], config_hash=value['config_hash'],
            run_id=row['run_id'], recorded_at=row['recorded_ts'],
            evaluation_content_hash=row['content_hash'], prev_link_hash='')
        f['store']._conn.execute('INSERT INTO search_evaluations '
            '(search_id,config_hash,run_id,recorded_at,evaluation_content_hash,prev_link_hash,link_hash) '
            'VALUES (?,?,?,?,?,?,?)', (*binding.values(),search_evaluation_hash(**binding)))
        f['store']._conn.commit()
    with pytest.raises((ValueError, ImmutableArtifactError, ExperimentStoreError)):
        retain(f)
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False)) as cold:
        with pytest.raises((ValueError, ExperimentStoreError)): cold.get_project_research_evidence(row['run_id'])


def test_read_review_rows_rejects_missing_duplicate_or_nonproject_ids(project_store):
    f = project_store
    row = retain(f)
    for ids in ([row['run_id'], row['run_id']], ['f'*64], 'f'*64):
        with pytest.raises(ValueError): f['store'].read_project_research_review_rows(ids)
    assert f['store'].read_project_research_review_rows([]) == {}


def test_real_lease_capability_is_exact_database_and_expires_with_transaction(project_store):
    f = project_store
    saved = []
    operations = callbacks(f)
    prepare = operations['prepare']
    def probe(view, rows):
        capability = rows.registry_view_for
        saved.append(capability)
        assert capability(f['root']/'registry.db') == view
        with pytest.raises(ValueError, match='PROJECT_REGISTRY_PATH'):
            capability(f['root']/'unrelated.db')
        detached = capability(f['root']/'registry.db')
        detached['history']['events'].clear()
        assert capability(f['root']/'registry.db') == view
        return prepare(view, rows)
    operations['prepare'] = probe
    f['store'].retain_project_research_evidence(**operations)
    with pytest.raises(ExperimentStoreError): saved[0](f['root']/'registry.db')
    assert not hasattr(f['store'].read_project_research_review_rows, 'registry_view_for')


def test_same_review_id_cannot_replace_trust_facts_even_with_valid_native_assessment(project_store):
    f = project_store
    original = retain(f)
    changed = copy.deepcopy(f['snapshot'])
    changed['trust_registry']['generation'] += 1
    from quantos.contracts.research_evidence_json import digest
    changed['expected_registry_sha256'] = digest(changed['trust_registry'])
    value = artifact.prepare(snapshot=changed,
        assessment=authority.verify_project_evidence(changed, now_ms=f['clock'][0]))
    assert artifact.row_fields(value)['run_id'] == original['run_id']
    with pytest.raises(ImmutableArtifactError, match='conflicts'):
        retain(f, snapshot=changed)
    assert f['store'].get_project_research_evidence(original['run_id']) == original


def test_actual_failed_producer_may_retain_diagnostic_review_without_fake_report(registered_case, monkeypatch):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tests.tools.test_registered_research_run import execute
    from tools import gold_bar_experiment as gold
    f = registered_case
    plan = f['prepare']('failed')
    original = gold._execute
    def fail(arguments):
        changed = copy.deepcopy(arguments)
        changed['bar_receipt']['response_sha256'] = '0'*64
        return original(changed)
    monkeypatch.setattr(gold, '_execute', fail)
    with pytest.raises(ValueError): execute(f, 'failed')
    population = f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=4, expected_population_sha256=population['sha256'])
    f['reader'] = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    f['snapshot'], _ = signed_project(f['reader'](), plan['invocation_id'], now=f['clock'][0])
    row = retain(f)
    value = artifact.validate_row(row)
    assert value['assessment']['qualification'] != 'PASS'
    assert len(value['snapshot']['project_cut']['attempts']) == 3
    assert not (f['directories']['failed']/'archive'/'report.json').exists()
    assert counts(f['store']) == (1, 0, 1)


def test_ordinary_evaluation_still_cannot_link_after_search_terminal(project_store):
    from quantos.contracts.alpha import AlphaEvaluation
    from quantos.contracts.research import ValidationVerdict
    from services.research.experiment_registry.evaluation_artifact import prepare_evaluation_artifact
    f = project_store
    selected = f['snapshot']['project_cut']['runs'][f['snapshot']['subject']['invocation_id']]['plan']
    write = prepare_evaluation_artifact(AlphaEvaluation(alpha_id='ordinary-diagnostic',
        verdict=ValidationVerdict.FAIL, reproducibility_hash='original-late-evaluation'),
        selected['search']['dataset_version'], f['clock'][0]+1)
    with pytest.raises(ExperimentStoreError, match='cannot bind'):
        f['store'].insert_evaluation(**vars(write), search_id=selected['search']['search_id'],
            config_hash=selected['trials'][0]['config_hash'])
    assert counts(f['store']) == (0, 0, 0)


def test_ordinary_unlinked_evaluation_is_not_a_review_only_current_delta(project_store):
    from quantos.contracts.alpha import AlphaEvaluation
    from quantos.contracts.research import ValidationVerdict
    from services.research.experiment_registry.evaluation_artifact import prepare_evaluation_artifact
    f = project_store
    row = retain(f)
    write = prepare_evaluation_artifact(AlphaEvaluation(alpha_id='ordinary-diagnostic',
        verdict=ValidationVerdict.FAIL, reproducibility_hash='different-review'),
        'native-xau', f['clock'][0]+1)
    f['store'].insert_evaluation(**vars(write))
    assert f['store'].get_project_research_evidence(row['run_id']) == row
    with pytest.raises(ValueError): retain(f)
    assert counts(f['store']) == (2, 0, 2)


def test_real_report_change_after_sql_before_fresh_read_rolls_back(project_store, monkeypatch):
    f = project_store
    path = f['directories']['one']/'archive'/'report.json'
    raw = path.read_bytes()
    def change(stage):
        if stage == 'after_audit': path.write_bytes(raw+b'\n')
    monkeypatch.setattr(f['store'], '_evaluation_write_checkpoint', change)
    with pytest.raises(ValueError): retain(f)
    assert path.read_bytes() == raw+b'\n'
    assert counts(f['store']) == (0, 0, 0)


def test_original_c2_profile_and_existing_evaluation_survive_review_cold_open(project_store):
    from quantos.contracts.alpha import AlphaEvaluation
    from quantos.contracts.research import ValidationVerdict
    from services.research.experiment_registry.evaluation_artifact import prepare_evaluation_artifact
    f = project_store
    f['store']._ensure_triple_columns()
    write = prepare_evaluation_artifact(AlphaEvaluation(alpha_id='original-diagnostic',
        verdict=ValidationVerdict.FAIL, reproducibility_hash='before-independent-review'),
        'native-xau', f['clock'][0])
    f['store'].insert_evaluation(**vars(write))
    original = dict(f['store']._conn.execute('SELECT * FROM evaluations').fetchone())
    cut = f['reader']()
    f['snapshot'], _ = signed_project(cut, f['snapshot']['subject']['invocation_id'], now=f['clock'][0])
    row = retain(f)
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False)) as cold:
        assert cold._evidence_profile() == 'EXACT_V4_C2'
        assert cold.get_project_research_evidence(row['run_id']) == row
        assert dict(cold._conn.execute('SELECT * FROM evaluations WHERE run_id=?',
            (write.run_id,)).fetchone()) == original
    assert counts(f['store']) == (2, 0, 2)
