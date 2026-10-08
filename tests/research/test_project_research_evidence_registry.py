"""Actual fixed source/project/trust reads into the original evaluation store."""
import copy
from contextlib import closing

import pytest

from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tests.research.test_project_research_evidence_snapshot import (
    project_snapshot as project_snapshot, registered_case as registered_case,
    frozen_source as frozen_source,
)


@pytest.fixture
def reviewed_registry(project_snapshot):
    f, reader, snapshot, trust, source = project_snapshot
    population = f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])
    f['clock'][0] += 1
    return f, reader, snapshot, trust, source


def record(case, **overrides):
    f, reader, snapshot, _, _ = case
    kwargs = dict(reviewed_cut=snapshot['project_cut'], read_snapshot=reader.read_retained,
                  expected_subject=snapshot['subject'], clock_ms=lambda:f['clock'][0])
    kwargs.update(overrides)
    return f['registry'].record_project_research_evidence(**kwargs)


def test_completed_search_review_persists_later_without_fabricating_activity(reviewed_registry):
    f, reader, snapshot, _, _ = reviewed_registry
    before = f['store']._conn.execute('SELECT * FROM search_attempt_events ORDER BY event_seq').fetchall()
    row = record(reviewed_registry)
    ended = max(r['recorded_at'] for r in snapshot['project_cut']['registry_history']['events'])
    assert row['recorded_ts'] > ended
    assert row['verdict'] == 'INSUFFICIENT'
    assert f['store']._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0] == 0
    assert f['store']._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
    assert f['store']._conn.execute('SELECT * FROM search_attempt_events ORDER BY event_seq').fetchall() == before
    f['clock'][0] += 1
    assert record(reviewed_registry) == row
    checked = f['registry'].preflight_project_research_evidence(row['run_id'],
        read_snapshot=reader.read_retained, clock_ms=lambda:f['clock'][0])
    assert checked['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert checked['qualification'] == 'INSUFFICIENT'
    assert checked['promotion_approved'] is checked['submission_enabled'] is False
    assert checked['projection']['attempt_count'] == 6
    assert checked['checked_at_ms'] == f['clock'][0]


def test_cold_historical_read_survives_new_attempt_but_fresh_use_rejects(reviewed_registry):
    f, reader, _, _, _ = reviewed_registry
    row = record(reviewed_registry)
    f['prepare']('new-attempt')
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True)) as store:
        historical = ExperimentRegistry(store).get_project_research_evidence(row['run_id'])
        assert historical['content_hash'] == row['content_hash']
        assert historical['current_admission_verified'] is False
    with pytest.raises(ValueError, match='CURRENT_PROJECT_CHANGED'):
        f['registry'].preflight_project_research_evidence(row['run_id'],
            read_snapshot=reader.read_retained, clock_ms=lambda:f['clock'][0])


@pytest.mark.parametrize('stage', ['after_evaluation', 'after_audit', 'before_commit'])
def test_expiry_at_original_write_checkpoint_rolls_back_evaluation_and_audit(reviewed_registry, monkeypatch, stage):
    f, _, snapshot, _, _ = reviewed_registry
    def expire(checkpoint):
        if checkpoint == stage:
            f['clock'][0] = snapshot['result_envelope']['statement']['expires_at_ms']+1
    monkeypatch.setattr(f['store'], '_evaluation_write_checkpoint', expire)
    with pytest.raises(ValueError):
        record(reviewed_registry)
    assert f['store']._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 0
    assert f['store']._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 0


def test_real_new_plan_during_last_read_prevents_commit(reviewed_registry):
    f, reader, _, _, _ = reviewed_registry
    calls = []
    def changing(cut, *, read_review_rows, registry_view_for=None):
        calls.append(1)
        if len(calls) == 3:
            f['prepare']('during-retention')
        return reader.read_retained(cut, read_review_rows=read_review_rows, registry_view_for=registry_view_for)
    with pytest.raises(ValueError, match='CURRENT_PROJECT_CHANGED'):
        record(reviewed_registry, read_snapshot=changing)
    assert len(calls) >= 3
    assert f['store']._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 0


def test_fresh_trust_revocation_cannot_reuse_historical_signature(reviewed_registry):
    from quantos.contracts.research_evidence_json import canonical
    f, reader, snapshot, trust, _ = reviewed_registry
    row = record(reviewed_registry)
    revoked = copy.deepcopy(snapshot['trust_registry'])
    revoked['keys']['test']['status'] = 'REVOKED'
    trust.write_bytes(canonical(revoked))
    with pytest.raises(ValueError, match='TRUST_PIN'):
        f['registry'].preflight_project_research_evidence(row['run_id'],
            read_snapshot=reader.read_retained, clock_ms=lambda:f['clock'][0])
    assert f['registry'].get_project_research_evidence(row['run_id'])['current_admission_verified'] is False
