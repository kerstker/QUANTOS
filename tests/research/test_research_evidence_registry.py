"""Local SQLite receipts; ephemeral signatures do not install research authority."""
from __future__ import annotations

import os
import json
from contextlib import closing

import pytest

from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.trial_ledger import TrialLedger
from tests.research.test_research_evidence_reader_integration import (
    frozen_source as frozen_source,
    native_reader as native_reader,
)


@pytest.fixture
def native_registry(tmp_path, monkeypatch, request):
    path = tmp_path / 'registry.sqlite3'
    store = SqliteExperimentStore(str(path), create=True)
    registry = ExperimentRegistry(store)
    store.begin_search_attempt(search_id='native', search_kind='FORWARD', source='isolated-research',
                               dataset_version='xau-native', recorded_at=0)
    native_append = TrialLedger.append
    profile = getattr(request, 'param', 'v2')
    provenance_mapping = {}

    def register_before_native_trial(ledger, record):
        if record.status == 'PLANNED':
            store.register_search_candidates('native', [record.exact_definition_id], registered_at=record.at)
            if profile != 'v2':
                from services.research.trial_provenance import trial_provenance_for_config
                original_attempt = trial_provenance_for_config(record.exact_definition_id,
                    research_session_id='native', actor_id='isolated-producer', recorded_at=record.at)
                store.record_trial_provenance(original_attempt)
                provenance_mapping[original_attempt.trial_id] = record.trial_id
        return native_append(ledger, record)

    monkeypatch.setattr(TrialLedger, 'append', register_before_native_trial)
    reader, subject, now, materials, trust = request.getfixturevalue('native_reader')
    # Actual native trial registration precedes review. The historical market
    # window is not the clock at which this new review is signed.
    from tests.validation.test_research_evidence_authority import signed_case
    from tests.validation.test_research_result_projection import encode, digest
    from services.research.research_evidence_reader import ResearchEvidenceSnapshotReader
    now = max(now, max(row['at'] for rows in materials['history'].values() for row in rows)+1000)
    original = reader()
    snapshot, sign = signed_case(dict(subject=subject, originals=original['originals'], now_ms=now))
    for name in ('source_envelope', 'result_envelope'):
        (reader.material / (name+'.json')).write_bytes(encode(snapshot[name]))
    trust.write_bytes(encode(snapshot['trust_registry']))
    reader = ResearchEvidenceSnapshotReader(reader.material, source_directory=reader.source,
        trial_database=reader.trials, trust_registry=trust, expected_registry_sha256=digest(trust.read_bytes()),
        minimum_generation=reader.minimum_generation, source_commit=reader.source_commit, producer_id=reader.producer_id)
    mapping = {tid: rows[0]['exact_definition_id'] for tid, rows in materials['history'].items()}
    if profile == 'v4-c2':
        store._ensure_triple_columns()
    kwargs = dict(read_snapshot=reader, expected_subject=subject, search_id='native',
        config_hash=mapping[subject['native_run_id']], native_trial_mapping=mapping,
        provenance_trial_mapping=provenance_mapping, clock_ms=lambda: now)
    yield dict(store=store, registry=registry, path=path, kwargs=kwargs, reader=reader,
               subject=subject, now=now, materials=materials, trust=trust, sign=sign, snapshot=snapshot)
    store.close()


def upgrade(f):
    population = f['store'].research_evidence_population()
    return f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])


@pytest.fixture
def technical_registry(tmp_path, monkeypatch, frozen_source):
    from services.research.research_evidence_reader import ResearchEvidenceSnapshotReader
    from tests.execution import test_authoritative_perpetual_backtest as native
    from tests.validation.test_research_result_projection import _technical, encode, digest
    from tests.validation.test_research_evidence_authority import signed_case
    source, identity, files = frozen_source
    (tmp_path/'registry').mkdir(mode=0o700)
    store = SqliteExperimentStore(str(tmp_path/'registry'/'technical-registry.db'), create=True)
    store.begin_search_attempt(search_id='technical',search_kind='BACKTEST',source='isolated-research',
        dataset_version='native-technical',recorded_at=0)
    original_run, original_producer = native._run, native.run_authoritative_linear_perpetual_v2

    def registered_producer(**kwargs):
        # _technical has already converted and natively validated XAU inputs.
        # Register its exact preimage before the actual runner executes.
        candidate = json.loads(json.dumps(kwargs['alpha_definition'],default=str))
        store.register_search_candidates('technical',[digest(candidate)],registered_at=0)
        return original_producer(**kwargs)

    def pinned_run(**kwargs):
        adapted = native.run_authoritative_linear_perpetual_v2

        def pinned(**values):
            return adapted(**dict(values,release_identity=identity))

        monkeypatch.setattr(native,'run_authoritative_linear_perpetual_v2',pinned)
        return original_run(**kwargs)

    monkeypatch.setattr(native,'run_authoritative_linear_perpetual_v2',registered_producer)
    monkeypatch.setattr(native,'_run',pinned_run)
    v = _technical(tmp_path,monkeypatch)
    originals = dict(v['raw'],source_identity=encode(identity),
        source_manifest=encode(dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',
            source_identity_sha256=digest(encode(identity)),files=files)),
        candidate_definition=encode(v['candidate']),dataset_manifest=encode(v['dataset']),
        trial_history=encode(v['history']),risk_replay=None,
        evaluation_plan=encode(dict(schema='QUANTOS_RESEARCH_EVALUATION_PLAN_V1',candidate_id=v['candidate_id'],
            evidence_kind=v['kind'],registered_at_ms=v['start']-1,start_ms=v['start'],end_ms=v['end'],
            frequency_ms=1000,trial_ids=list(v['history']))))
    subject = dict(schema='QUANTOS_RESEARCH_EVIDENCE_SUBJECT_V1',purpose='CANDIDATE_RESEARCH_REVIEW',
        evidence_kind=v['kind'],native_adapter=v['adapter'],producer_id='isolated-research',
        candidate_id=v['candidate_id'],market='XAUUSDT',native_run_id=v['run_id'])
    for field, role in (('candidate_definition_sha256','candidate_definition'),('source_identity_sha256','source_identity'),
        ('original_record_sha256','record'),('original_manifest_sha256','manifest'),('dataset_manifest_sha256','dataset_manifest'),
        ('trial_history_sha256','trial_history'),('evaluation_plan_sha256','evaluation_plan')):
        subject[field] = digest(originals[role])
    now = v['end']+1000
    snapshot, _ = signed_case(dict(subject=subject,originals=originals,now_ms=now))
    material, governance = tmp_path/'materials', tmp_path/'governance'
    material.mkdir(mode=0o700); governance.mkdir(mode=0o700)
    for name, raw in originals.items():
        if raw is not None:
            (material/(name+'.json')).write_bytes(raw)
    for name in ('subject','source_envelope','result_envelope'):
        (material/(name+'.json')).write_bytes(encode(snapshot[name]))
    trust = governance/'registry.json'
    trust.write_bytes(encode(snapshot['trust_registry']))
    for path in tmp_path.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    reader = ResearchEvidenceSnapshotReader(material,source_directory=source,trial_database=v['ledger_path'],
        trust_registry=trust,expected_registry_sha256=digest(trust.read_bytes()),minimum_generation=1,
        source_commit=identity['commit'],producer_id='isolated-research')
    mapping = {tid:rows[0]['exact_definition_id'] for tid,rows in v['history'].items()}
    kwargs = dict(read_snapshot=reader,expected_subject=subject,search_id='technical',
        config_hash=digest(v['candidate']),native_trial_mapping=mapping,provenance_trial_mapping={},clock_ms=lambda:now)
    yield dict(store=store,registry=ExperimentRegistry(store),kwargs=kwargs,reader=reader,subject=subject,materials=v,now=now)
    store.close()


def test_technical_native_run_is_distinct_from_trial_identity(technical_registry):
    f = technical_registry
    assert f['subject']['native_run_id'] not in f['kwargs']['native_trial_mapping']
    upgrade(f)
    row = f['registry'].record_research_evidence(**f['kwargs'])
    assert row['verdict'] == 'INSUFFICIENT'
    receipt = json.loads(row['evaluation_json'])
    assert receipt['assessment']['projection']['metrics']['annualized_sharpe'] is None
    assert f['registry'].get_research_evidence(row['run_id'])['current_admission_verified'] is False


def test_technical_trial_id_cannot_replace_native_run_identity(technical_registry):
    f = technical_registry
    upgrade(f)
    subject = dict(f['subject'],native_run_id=next(iter(f['kwargs']['native_trial_mapping'])))
    with pytest.raises(ValueError,match='SNAPSHOT_SUBJECT'):
        f['registry'].record_research_evidence(**dict(f['kwargs'],expected_subject=subject))
    assert not f['store'].search_evaluations('technical')


def test_native_producer_fixed_reader_atomic_receipt_and_cold_open(native_registry):
    f = native_registry
    before = f['store'].all_trial_hashes()
    upgrade(f)
    row = f['registry'].record_research_evidence(**f['kwargs'])
    assert row['verdict'] == 'INSUFFICIENT'
    assert f['store'].all_trial_hashes() == before
    assert len(f['store'].search_evaluations('native')) == 1
    assert len(f['store'].pending_evaluation_audits()) == 1
    with closing(SqliteExperimentStore(str(f['path']), read_only=True)) as cold:
        receipt = ExperimentRegistry(cold).get_research_evidence(row['run_id'])
        assert receipt['evaluation_json'] == row['evaluation_json']
        assert receipt['current_admission_verified'] is False
        assert receipt['promotion_approved'] is False


def test_new_artifact_requires_explicit_schema_upgrade(native_registry):
    f = native_registry
    with pytest.raises(ValueError, match='VERSION_5'):
        f['registry'].record_research_evidence(**f['kwargs'])
    assert f['store']._conn.execute('PRAGMA user_version').fetchone()[0] == 2
    assert not f['store'].search_evaluations('native')


def test_rejected_independent_review_retained_without_authority(native_registry):
    from tests.validation.test_research_result_projection import encode
    from services.validation.selection_authority import selection_authority_admitted
    f = native_registry
    statement = dict(f['snapshot']['result_envelope']['statement'], review_verdict='REJECTED')
    (f['reader'].material/'result_envelope.json').write_bytes(encode(f['sign']('RESULT_REVIEW',statement)))
    upgrade(f)
    row = f['registry'].record_research_evidence(**f['kwargs'])
    assert row['verdict'] == 'REJECTED'
    assert selection_authority_admitted(requested=True, authoritative_backtest_record=row) is False
    assert f['registry'].preflight_research_evidence(row['run_id'],read_snapshot=f['reader'],
        clock_ms=lambda:f['now'])['promotion_approved'] is False


@pytest.mark.parametrize('change', ['unmapped-config', 'wrong-definition', 'omitted-native'])
def test_full_registry_population_and_exact_mapping_required(native_registry, change):
    f = native_registry
    kwargs = dict(f['kwargs'], native_trial_mapping=dict(f['kwargs']['native_trial_mapping']))
    if change == 'unmapped-config':
        f['store'].add_trials(['e' * 64], first_seen_ts=0)
    elif change == 'wrong-definition':
        keys = list(kwargs['native_trial_mapping'])
        kwargs['native_trial_mapping'][keys[0]] = kwargs['native_trial_mapping'][keys[-1]]
    else:
        kwargs['native_trial_mapping'].pop(next(iter(kwargs['native_trial_mapping'])))
    upgrade(f)
    with pytest.raises(ValueError, match='POPULATION|MAPPING'):
        f['registry'].record_research_evidence(**kwargs)
    assert not f['store'].search_evaluations('native')


@pytest.mark.parametrize('phase', ['after_evaluation', 'after_link', 'after_audit', 'before_commit'])
def test_original_atomic_transaction_rolls_back_all_three_writes(native_registry, monkeypatch, phase):
    f = native_registry
    upgrade(f)

    def fail(stage):
        if stage == phase:
            raise RuntimeError('injected original transaction failure')

    monkeypatch.setattr(f['store'], '_evaluation_write_checkpoint', fail)
    with pytest.raises(RuntimeError, match='injected'):
        f['registry'].record_research_evidence(**f['kwargs'])
    assert not f['store']._conn.execute('SELECT * FROM evaluations').fetchall()
    assert not f['store'].search_evaluations('native')
    assert not f['store'].pending_evaluation_audits()


def test_retry_keeps_original_receipt_and_audit_timestamp(native_registry):
    f = native_registry
    upgrade(f)
    first = f['registry'].record_research_evidence(**f['kwargs'])
    second = f['registry'].record_research_evidence(**dict(f['kwargs'], clock_ms=lambda: f['now']+1))
    assert first == second
    assert len(f['store'].search_evaluations('native')) == 1
    assert len(f['store'].pending_evaluation_audits()) == 1


def test_idempotent_retry_after_original_search_heartbeat(native_registry):
    f = native_registry
    upgrade(f)
    original = f['registry'].record_research_evidence(**f['kwargs'])
    f['store'].heartbeat_search_attempt('native', recorded_at=f['now']+1)
    retry = f['registry'].record_research_evidence(**dict(f['kwargs'],clock_ms=lambda:f['now']+2))
    assert retry == original
    assert len(f['store'].search_evaluations('native')) == len(f['store'].pending_evaluation_audits()) == 1


def test_historical_getter_is_not_current_admission(native_registry):
    f = native_registry
    upgrade(f)
    row = f['registry'].record_research_evidence(**f['kwargs'])
    f['trust'].write_bytes(f['trust'].read_bytes()+b'\n')
    assert f['registry'].get_research_evidence(row['run_id'])['current_admission_verified'] is False
    with pytest.raises(ValueError, match='TRUST_PIN'):
        f['registry'].preflight_research_evidence(row['run_id'],
            read_snapshot=f['reader'], clock_ms=lambda: f['now']+1)


@pytest.mark.parametrize('kind', ['version', 'extra-table', 'authority'])
def test_plain_upgrade_refuses_unknown_or_family_authority(tmp_path, kind):
    store = SqliteExperimentStore(str(tmp_path/'guard.db'), create=True)
    try:
        population = store.research_evidence_population()
        if kind == 'version':
            store._conn.execute('PRAGMA user_version=3')
        elif kind == 'extra-table':
            store._conn.execute('CREATE TABLE family_genesis(value TEXT)')
        else:
            store._expected_authority_instance_id = 'configured-independent-authority'
        with pytest.raises(ValueError, match='SCHEMA|FAMILY|VERSION'):
            store.upgrade_research_evidence_schema(expected_version=2,
                expected_population_sha256=population['sha256'])
    finally:
        store.close()


@pytest.mark.parametrize('native_registry', ['v2', 'v4', 'v4-c2'], indirect=True)
def test_all_physical_profiles_keep_old_rows_and_new_receipt(native_registry):
    from tests.research.test_experiment_registry import _evaluation
    f = native_registry
    f['registry'].record_evaluation(_evaluation(), dataset_version='legacy-data', recorded_ts=0)
    old_rows = [tuple(row) for row in f['store']._conn.execute('SELECT * FROM evaluations')]
    catalog = [tuple(row) for row in f['store']._conn.execute('SELECT * FROM sqlite_schema ORDER BY name')]
    upgrade(f)
    assert [tuple(row) for row in f['store']._conn.execute('SELECT * FROM sqlite_schema ORDER BY name')] == catalog
    value = f['registry'].record_research_evidence(**f['kwargs'])
    with closing(SqliteExperimentStore(str(f['path']), read_only=True)) as cold:
        assert ExperimentRegistry(cold).get_research_evidence(value['run_id'])['verdict'] == 'INSUFFICIENT'
        assert tuple(cold._conn.execute('SELECT * FROM evaluations WHERE run_id=?', (old_rows[0][0],)).fetchone()) == old_rows[0]
        assert len(cold._conn.execute('PRAGMA table_info(evaluations)').fetchall()) == 7


@pytest.mark.parametrize('native_registry', ['v4'], indirect=True)
@pytest.mark.parametrize('change', ['missing-map', 'wrong-map', 'additional-attempt'])
def test_original_attempt_population_cannot_be_hidden(native_registry, change):
    from services.research.trial_provenance import trial_provenance_for_config
    f = native_registry
    args = dict(f['kwargs'], provenance_trial_mapping=dict(f['kwargs']['provenance_trial_mapping']))
    keys = list(args['provenance_trial_mapping'])
    if change == 'missing-map':
        args['provenance_trial_mapping'].pop(keys[0])
    elif change == 'wrong-map':
        args['provenance_trial_mapping'][keys[0]] = args['provenance_trial_mapping'][keys[-1]]
    else:
        f['store'].record_trial_provenance(trial_provenance_for_config(f['kwargs']['config_hash'],
            research_session_id='earlier-independent-attempt', actor_id='isolated-producer', recorded_at=0))
    upgrade(f)
    with pytest.raises(ValueError, match='MAPPING'):
        f['registry'].record_research_evidence(**args)
    assert not f['store'].search_evaluations('native')


@pytest.mark.parametrize('native_registry', ['v2', 'v4'], indirect=True)
def test_other_search_attempt_cannot_hide_behind_deduplicated_config(native_registry):
    f = native_registry
    original_configs = f['store'].all_trial_hashes()
    f['store'].begin_search_attempt(search_id='second-attempt',search_kind='FORWARD',source='isolated-research',
        dataset_version='xau-native',recorded_at=f['now']+1)
    f['store'].register_search_candidates('second-attempt',[f['kwargs']['config_hash']],registered_at=f['now']+2)
    assert f['store'].all_trial_hashes() == original_configs
    upgrade(f)
    with pytest.raises(ValueError,match='UNMAPPED|MAPPING'):
        f['registry'].record_research_evidence(**dict(f['kwargs'],clock_ms=lambda:f['now']+3))
    assert not f['store'].search_evaluations('native')


@pytest.mark.parametrize('phase', ['after_evaluation', 'after_link', 'after_audit', 'before_commit'])
def test_actual_process_exit_does_not_leave_partial_three_record_transaction(native_registry, phase):
    f = native_registry
    upgrade(f)
    child = os.fork()
    if child == 0:
        f['store']._evaluation_write_checkpoint = lambda stage: os._exit(71) if stage == phase else None
        f['registry'].record_research_evidence(**f['kwargs'])
        os._exit(99)
    assert os.waitpid(child, 0)[1] == 71 << 8
    with closing(SqliteExperimentStore(str(f['path']), read_only=True)) as cold:
        assert cold.research_evidence_population()['sha256'] == f['store'].research_evidence_population()['sha256']
        for table in ('evaluations', 'search_evaluations', 'evaluation_audit_outbox'):
            assert cold._conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert f['registry'].record_research_evidence(**f['kwargs'])['verdict'] == 'INSUFFICIENT'


@pytest.mark.parametrize('phase', ['research_evidence_before_version', 'research_evidence_after_version'])
def test_actual_upgrade_exit_is_old_version_with_unchanged_rows(native_registry, phase):
    f = native_registry
    before = f['store'].research_evidence_population()
    child = os.fork()
    if child == 0:
        f['store']._migration_checkpoint = lambda stage: os._exit(72) if stage == phase else None
        upgrade(f)
        os._exit(99)
    assert os.waitpid(child, 0)[1] == 72 << 8
    with closing(SqliteExperimentStore(str(f['path']), read_only=True)) as cold:
        assert cold._schema_version() == 2
        assert cold.research_evidence_population() == before
    upgrade(f)


@pytest.mark.parametrize('change', ['original', 'expired', 'rollback', 'population'])
def test_commit_rechecks_actual_originals_clock_and_population(native_registry, monkeypatch, change):
    f = native_registry
    upgrade(f)
    clock = [f['now']]

    def mutate(stage):
        if stage != 'before_commit':
            return
        if change == 'original':
            path = f['reader'].material/'record.json'
            path.write_bytes(path.read_bytes()+b'\n')
        elif change == 'expired':
            clock[0] += 86_400_001
        elif change == 'rollback':
            clock[0] -= 1
        else:
            f['store']._conn.execute('INSERT INTO trials VALUES (?,?)', ('e'*64, 0))

    monkeypatch.setattr(f['store'], '_evaluation_write_checkpoint', mutate)
    with pytest.raises(ValueError):
        f['registry'].record_research_evidence(**dict(f['kwargs'], clock_ms=lambda: clock[0]))
    assert not f['store'].search_evaluations('native')
    assert not f['store']._conn.execute('SELECT * FROM evaluations').fetchall()


def test_upgrade_population_compare_and_swap(native_registry):
    f = native_registry
    before = f['store'].research_evidence_population()
    f['store'].add_trials(['a'*64], first_seen_ts=0)
    with pytest.raises(ValueError, match='POPULATION_CAS'):
        f['store'].upgrade_research_evidence_schema(expected_version=2, expected_population_sha256=before['sha256'])
    assert f['store']._schema_version() == 2


def test_expected_subject_cannot_attach_to_another_candidate(native_registry):
    f = native_registry
    upgrade(f)
    other = next(v for v in f['kwargs']['native_trial_mapping'].values() if v != f['kwargs']['config_hash'])
    with pytest.raises(ValueError, match='MAPPING_CANDIDATE'):
        f['registry'].record_research_evidence(**dict(f['kwargs'], config_hash=other))


def test_current_preflight_rejects_new_registry_trial(native_registry):
    f = native_registry
    upgrade(f)
    row = f['registry'].record_research_evidence(**f['kwargs'])
    f['store'].add_trials(['a'*64], first_seen_ts=f['now'])
    assert f['registry'].get_research_evidence(row['run_id'])['current_admission_verified'] is False
    with pytest.raises(ValueError, match='POPULATION_CHANGED'):
        f['registry'].preflight_research_evidence(row['run_id'], read_snapshot=f['reader'], clock_ms=lambda:f['now'])


def test_same_identity_new_independent_review_cannot_replace_receipt(native_registry):
    from services.research.ports.persistence import ImmutableArtifactError
    from services.research.research_evidence_reader import ResearchEvidenceSnapshotReader
    from tests.validation.test_research_evidence_authority import signed_case
    from tests.validation.test_research_result_projection import encode, digest
    f = native_registry
    upgrade(f)
    before = f['registry'].record_research_evidence(**f['kwargs'])
    old_snapshot = f['reader']()
    new, _ = signed_case(dict(subject=f['subject'], originals=old_snapshot['originals'], now_ms=f['now']+1))
    for name in ('source_envelope', 'result_envelope'):
        (f['reader'].material/(name+'.json')).write_bytes(encode(new[name]))
    f['trust'].write_bytes(encode(new['trust_registry']))
    old = f['reader']
    fresh_reader = ResearchEvidenceSnapshotReader(old.material, source_directory=old.source,
        trial_database=old.trials, trust_registry=old.trust,
        expected_registry_sha256=digest(old.trust.read_bytes()), minimum_generation=old.minimum_generation,
        source_commit=old.source_commit, producer_id=old.producer_id)
    with pytest.raises(ImmutableArtifactError, match='conflicts'):
        f['registry'].record_research_evidence(**dict(f['kwargs'],read_snapshot=fresh_reader,clock_ms=lambda:f['now']+1))
    assert f['store'].get_research_evidence(before['run_id']) == before


def test_actual_family_genesis_and_sealed_catalog_cannot_take_plain_upgrade(tmp_path):
    from services.research.experiment_registry import family_schema_v3_activation as activation
    from tests.research.test_family_schema_v3_activation import _inputs
    path = tmp_path/'family.db'
    store = SqliteExperimentStore(str(path), create=True)
    old = store.research_evidence_population()
    activation.activate_family_schema_v3(store._conn, _inputs())
    before = path.read_bytes()
    with pytest.raises(ValueError, match='VERSION'):
        store.upgrade_research_evidence_schema(expected_version=2,expected_population_sha256=old['sha256'])
    assert path.read_bytes() == before
    store.close()


def test_c2_original_catalog_rows_survive_explicit_upgrade(tmp_path):
    from services.alpha.catalog import AlphaCatalog
    from quantos.contracts.alpha import AlphaDefinition
    path = tmp_path/'c2.db'
    store = SqliteExperimentStore(str(path), create=True)
    registry = ExperimentRegistry(store)
    catalog = AlphaCatalog()
    catalog.register(AlphaDefinition(alpha_id='draft-only', name='draft-only', factor_ids=('f1',)))
    registry.save(catalog, now=1)
    assert store._schema_version() == 4
    old = tuple(tuple(row) for row in store._conn.execute('SELECT * FROM alpha_state'))
    old_population = store.research_evidence_population()
    store.upgrade_research_evidence_schema(expected_version=4,expected_population_sha256=old_population['sha256'])
    store.close()
    with closing(SqliteExperimentStore(str(path),read_only=True)) as cold:
        assert cold.research_evidence_population() == old_population
        assert tuple(tuple(row) for row in cold._conn.execute('SELECT * FROM alpha_state')) == old


def test_cold_read_rejects_receipt_type_alias_after_valid_outer_rehash(native_registry):
    from services.validation import research_evidence_artifact as artifact
    f = native_registry
    upgrade(f)
    row = f['registry'].record_research_evidence(**f['kwargs'])
    value = artifact.decode(row['evaluation_json'].encode())
    value['assessment']['promotion_approved'] = 0
    changed = dict(row, evaluation_json=artifact.canonical(value).decode())
    changed['content_hash'] = artifact.digest(b'QUANTOS_RESEARCH_EVIDENCE_ARTIFACT_CONTENT_V1\0'+artifact.canonical(value))
    with pytest.raises(ValueError, match='ASSESSMENT'):
        artifact.validate_row(changed)


def test_size_bound_precedes_large_shared_string_expansion():
    from services.validation import research_evidence_artifact as artifact
    shared = '\x00' * (1024**2)
    with pytest.raises(ValueError, match='BOUND'):
        artifact.canonical([shared]*20)


@pytest.mark.parametrize('change', ['expired', 'rollback'])
def test_current_preflight_final_population_must_not_outlive_review(native_registry, monkeypatch, change):
    f = native_registry
    upgrade(f)
    row = f['registry'].record_research_evidence(**f['kwargs'])
    clock = [f['now']]
    original = f['store'].research_evidence_population
    reads = []

    def read_population():
        population = original()
        reads.append(True)
        if len(reads) == 2:
            clock[0] = f['now'] + 86_400_001 if change == 'expired' else f['now']-1
        return population

    monkeypatch.setattr(f['store'], 'research_evidence_population', read_population)
    with pytest.raises(ValueError, match='EXPIRED|ROLLBACK'):
        f['registry'].preflight_research_evidence(row['run_id'], read_snapshot=f['reader'], clock_ms=lambda:clock[0])


@pytest.mark.parametrize('profile', ['v2', 'v4', 'v4-c2'])
def test_original_catalog_save_after_upgrade_and_cold_reopen(tmp_path, profile):
    from services.alpha.catalog import AlphaCatalog
    from quantos.contracts.alpha import AlphaDefinition
    path = tmp_path/'catalog-write.db'
    store = SqliteExperimentStore(str(path), create=True)
    if profile != 'v2':
        store.trial_provenance_head()  # Original explicit provenance capability path.
    if profile == 'v4-c2':
        store._ensure_triple_columns()
    population = store.research_evidence_population()
    store.upgrade_research_evidence_schema(expected_version=store._schema_version(),
        expected_population_sha256=population['sha256'])
    registry = ExperimentRegistry(store)
    catalog = AlphaCatalog()
    catalog.register(AlphaDefinition(alpha_id='draft-only',name='draft-only',factor_ids=('f1',)))
    registry.save(catalog, now=1)
    assert store._schema_version() == 5
    assert store._evidence_profile() == ('EXACT_V2' if profile == 'v2' else 'EXACT_V4_C2')
    before = tuple(tuple(r) for r in store._conn.execute('SELECT * FROM alpha_state'))
    store.close()
    with closing(SqliteExperimentStore(str(path),read_only=True)) as cold:
        cold.research_evidence_population()
        restored = AlphaCatalog()
        ExperimentRegistry(cold).load_into(restored)
        assert len(restored.snapshot().alphas) == 1
        assert tuple(tuple(r) for r in cold._conn.execute('SELECT * FROM alpha_state')) == before
