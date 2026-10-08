"""Actual native statistics, fixed originals and original transactional store."""
import copy
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from quantos.contracts.research_evidence_json import canonical, digest
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.validation.test_project_research_statistical_evidence import build_case, prepare, signed_statistics


@pytest.fixture
def fixed_statistics(registered_case, technical_arguments, monkeypatch):
    from tools import registered_technical_research_run as technical
    from tools.perpetual_replay_research import source_identity
    from services.research.project_research_evidence_snapshot import ProjectResearchEvidenceSnapshotReader
    source = Path(technical.__file__).resolve().parents[1]
    if os.environ.get('QUANTOS_STATISTICS_ISOLATED_SOURCE') != str(source):
        return None
    identity = source_identity()
    arguments = copy.deepcopy(technical_arguments)
    arguments['release_identity'] = identity
    monkeypatch.setattr(technical, 'source_identity', source_identity)
    c = build_case(registered_case, arguments, monkeypatch)
    files = {p.relative_to(source).as_posix(): digest(p.read_bytes())
        for folder in ('services', 'quantos', 'tools') for p in (source/folder).rglob('*.py')}
    manifest = dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
        source_identity_sha256=digest(identity), files=files)
    snapshot, _ = signed_statistics(c, prepare(c), source_manifest=manifest)
    f = c['f']
    material = f['root']/'materials'
    material.mkdir(mode=0o700)
    for key in ('subject', 'source_manifest', 'source_envelope', 'result_envelope', 'statistical_evidence'):
        path = material/(key+'.json')
        path.write_bytes(canonical(snapshot[key]))
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
    population = f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])
    f['clock'][0] += 1
    return c, reader, snapshot, trust


def isolated(tmp_path, name):
    original = Path(__file__).resolve().parents[2]
    destination = tmp_path/'isolated-source'
    subprocess.run(['git', 'clone', '--quiet', '--no-local', '--no-hardlinks',
        str(original), str(destination)], check=True, capture_output=True)
    for folder in ('services', 'quantos', 'tools', 'tests'):
        shutil.copytree(original/folder, destination/folder, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for path in (destination, *destination.rglob('*')):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o022)
    environment = dict(os.environ, QUANTOS_STATISTICS_ISOLATED_SOURCE=str(destination),
        PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        'tests/research/test_project_statistical_evidence_registry.py::'+name],
        cwd=destination, env=environment, text=True, capture_output=True, timeout=1800)
    assert child.returncode == 0, child.stdout+child.stderr


def record(case, **overrides):
    c, reader, snapshot, _ = case
    f = c['f']
    arguments = dict(reviewed_cut=snapshot['project_cut'], read_snapshot=reader.read_retained,
        expected_subject=snapshot['subject'], clock_ms=lambda:f['clock'][0])
    arguments.update(overrides)
    return f['registry'].record_project_research_evidence(**arguments)


def test_statistics_original_reader_store_cold_reopen_and_fresh_preflight(fixed_statistics, tmp_path):
    if fixed_statistics is None:
        return isolated(tmp_path, 'test_statistics_original_reader_store_cold_reopen_and_fresh_preflight')
    from tests.validation.test_project_research_statistics import add_attempt
    c, reader, snapshot, _ = fixed_statistics
    f = c['f']
    originals = {p:p.read_bytes() for p in (f['root']/'project').rglob('*') if p.is_file()}
    materials = {p:p.read_bytes() for p in reader.material.iterdir()}
    events = f['store']._conn.execute('SELECT * FROM search_attempt_events ORDER BY event_seq').fetchall()
    row = record(fixed_statistics)
    retained = json.loads(row['evaluation_json'])
    assert retained['schema'] == 'QUANTOS_PROJECT_RESEARCH_EVIDENCE_ARTIFACT_V3'
    assert retained['snapshot']['statistical_evidence'] == snapshot['statistical_evidence']
    assert row['verdict'] == 'INSUFFICIENT'
    assert f['store']._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
    assert f['store']._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
    assert f['store']._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0] == 0
    assert f['store']._conn.execute('SELECT * FROM search_attempt_events ORDER BY event_seq').fetchall() == events
    assert originals == {p:p.read_bytes() for p in (f['root']/'project').rglob('*') if p.is_file()}
    assert materials == {p:p.read_bytes() for p in reader.material.iterdir()}
    with closing(SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True)) as store:
        historical = ExperimentRegistry(store).get_project_research_evidence(row['run_id'])
        assert historical['content_hash'] == row['content_hash']
        assert historical['current_admission_verified'] is False
        assert store.research_evidence_population()['sha256']
    checked = f['registry'].preflight_project_research_evidence(row['run_id'],
        read_snapshot=reader.read_retained, clock_ms=lambda:f['clock'][0])
    assert checked['projection']['statistical_review']['statistical_decision'] == 'PASS'
    assert checked['projection']['attempt_count'] == 3
    assert 'NO_INDEPENDENT_OOS_PROOF' in checked['reasons']
    assert checked['qualification'] == 'INSUFFICIENT'
    assert checked['promotion_approved'] is checked['submission_enabled'] is False
    add_attempt(c, c['args'][0], number=4, execute=False)
    with pytest.raises(ValueError, match='CURRENT_PROJECT_CHANGED'):
        f['registry'].preflight_project_research_evidence(row['run_id'],
            read_snapshot=reader.read_retained, clock_ms=lambda:f['clock'][0])
    assert f['registry'].get_project_research_evidence(row['run_id'])['content_hash'] == row['content_hash']
    changed = copy.deepcopy(retained)
    changed['snapshot']['statistical_evidence']['report']['metrics']['dsr'] = .123
    raw = canonical(changed)
    # Damage an independent SQLite backup, never the append-only original.
    # Restore its exact trigger definitions so the cold validator must detect
    # the changed original statistics, rather than just a missing SQL trigger.
    damaged = f['root']/'damaged-registry.db'
    with closing(sqlite3.connect(damaged)) as database:
        f['store']._conn.backup(database)
        triggers = database.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='evaluations'").fetchall()
        for name, _ in triggers:
            database.execute('DROP TRIGGER "'+name.replace('"', '""')+'"')
        database.execute('UPDATE evaluations SET evaluation_json=? WHERE run_id=?',
            (raw.decode(), row['run_id']))
        for _, definition in triggers:
            database.execute(definition)
        database.commit()
    with closing(SqliteExperimentStore(str(damaged), create=False, read_only=True)) as store:
        with pytest.raises(ValueError, match='STATISTICAL_REPORT_CHANGED'):
            store.research_evidence_population()
    assert f['store']._conn.execute('SELECT evaluation_json FROM evaluations WHERE run_id=?',
        (row['run_id'],)).fetchone()[0] == row['evaluation_json']


def test_statistics_expiry_at_actual_transaction_checkpoint_rolls_back(fixed_statistics, monkeypatch, tmp_path):
    if fixed_statistics is None:
        return isolated(tmp_path, 'test_statistics_expiry_at_actual_transaction_checkpoint_rolls_back')
    c, _, snapshot, _ = fixed_statistics
    f = c['f']
    def expire(stage):
        if stage == 'after_evaluation':
            f['clock'][0] = snapshot['result_envelope']['statement']['expires_at_ms']+1
    monkeypatch.setattr(f['store'], '_evaluation_write_checkpoint', expire)
    with pytest.raises(ValueError):
        record(fixed_statistics)
    assert f['store']._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 0
    assert f['store']._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 0


def test_statistics_another_source_cannot_select_this_executing_math(fixed_statistics, tmp_path):
    if fixed_statistics is None:
        return isolated(tmp_path, 'test_statistics_another_source_cannot_select_this_executing_math')
    _, reader, _, _ = fixed_statistics
    other = tmp_path/'different-source'
    other.mkdir(mode=0o700)
    for folder in ('services', 'quantos', 'tools'):
        shutil.copytree(reader.source/folder, other/folder,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(reader.source/'requirements-ci-linux-aarch64.lock', other/'requirements-ci-linux-aarch64.lock')
    (other/'requirements-ci-linux-aarch64.lock').chmod(0o600)
    reader.source = other
    with pytest.raises(ValueError, match='STATISTICAL_EXECUTING_SOURCE'):
        record(fixed_statistics)


def test_changed_statistics_during_actual_second_read_cannot_be_retained(fixed_statistics, tmp_path):
    if fixed_statistics is None:
        return isolated(tmp_path, 'test_changed_statistics_during_actual_second_read_cannot_be_retained')
    c, reader, _, _ = fixed_statistics
    f = c['f']
    calls = []
    def changing(cut, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            path = reader.material/'statistical_evidence.json'
            value = json.loads(path.read_bytes())
            value['report']['metrics']['dsr'] = .123
            path.write_bytes(canonical(value))
        return reader.read_retained(cut, **kwargs)
    with pytest.raises(ValueError, match='PROJECT_READ_CHANGED'):
        record(fixed_statistics, read_snapshot=changing)
    assert calls == [1, 1]
    assert f['store']._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 0
    assert f['store']._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 0
