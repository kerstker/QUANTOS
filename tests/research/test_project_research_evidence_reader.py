"""Real independent native ledgers and the original registry, including failures."""
import dataclasses
import json
import os
from pathlib import Path

import pytest

from quantos.contracts import registered_research_run as contract
from quantos.contracts.gold_trial_identity import gold_trial_definition_id
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.registered_research_registration import (
    expected_provenance, initial_archive_manifest, native_trial_identity, registration_evidence,
)
from services.research.trial_ledger import TrialLedger, TrialRecord
from services.research.trial_provenance import trial_provenance_for_config
from tests.research.test_research_evidence_reader import material as material


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(contract.canonical(value))
    path.chmod(0o600)


@pytest.fixture
def project(tmp_path, material):
    root = tmp_path / 'project'
    root.mkdir(mode=0o700)
    (root / 'runs').mkdir(mode=0o700)
    database = tmp_path / 'catalog.db'
    store = SqliteExperimentStore(str(database), create=True)
    database.chmod(0o600)
    value = dict(root=root, database=database, store=store, source=material['identity'])
    yield value
    store.close()


def prepare(f, number, *, register=True, status='FAILED'):
    invocation = f'{number:064x}'
    source = f['source']
    inputs = {'retained_public_input': ['same', 1]}
    input_hash = contract.digest(inputs)
    definition = gold_trial_definition_id(input_sha256=input_hash, candidate='channel', source=source)
    trial = dict(candidate_id='channel', config_hash=definition,
        native_trial_id=contract.native_trial_id(invocation, 'channel', definition),
        provenance_trial_id=f'search-{number}:' + definition, alpha_id=definition,
        family_id='f' * 64, validation_policy_digest='e' * 64,
        definition_preimage=dict(input_sha256=input_hash, candidate='channel', source=source))
    plan = dict(schema=contract.SCHEMA, invocation_id=invocation, adapter=contract.GOLD,
        search=dict(search_id=f'search-{number}', search_kind='FORWARD', source='native-test',
                    dataset_version='xau-frozen', recorded_at_ms=100),
        actor_id='automated-native-test', input_sha256=input_hash, source_identity=source,
        trials=[trial], submission_enabled=False, economic_authority=False)
    contract.validate_plan(plan)
    directory = f['root'] / 'runs' / invocation
    write(directory / 'plan.json', plan)
    write(directory / 'input.json', inputs)
    if register:
        store = f['store']
        store.begin_search_attempt(**{k:v for k,v in plan['search'].items() if k != 'recorded_at_ms'}, recorded_at=100)
        store.register_search_candidates(plan['search']['search_id'], [definition], registered_at=100)
        store.record_trial_provenance(expected_provenance(plan, trial))
        receipt = registration_evidence(plan, ExperimentRegistry(store))
        write(directory/'registration.json', receipt)
    if status is not None:
        database = directory / 'archive' / 'trials.db'
        database.parent.mkdir(mode=0o700)
        write(database.parent/'input.json', inputs)
        with TrialLedger(str(database)) as ledger:
            statuses = ['PLANNED'] if status == 'PLANNED' else ['PLANNED', 'RUNNING', status]
            for index, item in enumerate(statuses):
                if index == 2:
                    outcome = dict(schema='QUANTOS_REGISTERED_RESEARCH_OUTCOME_V1',
                        plan_sha256=contract.digest(plan), status=status, error_type='ValueError',
                        report_sha256=None, completed_at_ms=112)
                    write(directory/'outcome.json', outcome)
                ledger.append(TrialRecord(trial['native_trial_id'], definition, trial['family_id'],
                    definition, trial['validation_policy_digest'], item, index, 110 + index))
                if item == 'RUNNING':
                    write(database.parent/'running.json', dict(schema='GOLD_BAR_RUNNING_ARCHIVE_V1',
                        manifest=initial_archive_manifest(plan), identities=[native_trial_identity(trial)]))
                    write(directory/'invocation.json', dict(schema='QUANTOS_REGISTERED_RESEARCH_INVOCATION_V1',
                        plan_sha256=contract.digest(plan), registration_sha256=contract.digest(receipt), started_at_ms=111))
        if status == 'FAILED':
            write(database.parent/'manifest.json', dict(initial_archive_manifest(plan),
                                                        terminal='FAILED', error_type='ValueError'))
            store.finish_search_attempt(plan['search']['search_id'], status='FAILED', recorded_at=112)
        database.chmod(0o600)
    return plan, directory


def reader(f):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    return ProjectResearchEvidenceReader(f['root'], registry_database=f['database'])


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_repeat_config_in_two_searches_keeps_both_original_global_chains(project):
    first, first_dir = prepare(project, 1)
    second, second_dir = prepare(project, 2, status='PLANNED')
    before = contents(project['root'])
    cut = reader(project)()
    assert contents(project['root']) == before
    assert cut['population_coverage'] == 'COMPLETE'
    assert cut['attempt_count'] == 2
    assert cut['unique_definition_count'] == 1
    assert len(cut['registry_population']['provenance']) == 2
    assert cut['runs'][first['invocation_id']]['ledger']['event_count'] == 3
    assert cut['runs'][second['invocation_id']]['ledger']['event_count'] == 1
    for plan, directory in ((first, first_dir), (second, second_dir)):
        with TrialLedger(str(directory / 'archive/trials.db'), create=False, read_only=True) as ledger:
            original = [dataclasses.asdict(row) for row in ledger.load(plan['trials'][0]['native_trial_id']).events]
        retained = cut['runs'][plan['invocation_id']]['ledger']['history'][plan['trials'][0]['native_trial_id']]
        assert original == retained
        assert retained[0]['global_seq'] == 1
    assert [row['status'] for row in cut['attempts']] == ['FAILED', 'PLANNED']
    assert cut['unfinished_attempts'] == [second['trials'][0]['native_trial_id']]
    assert cut['submission_enabled'] is False
    assert cut['economic_authority'] is False


def test_pre_registration_plan_is_retained_without_inventing_rows(project):
    plan, directory = prepare(project, 1, register=False, status=None)
    before = contents(project['root'])
    cut = reader(project)()
    assert cut['attempts'][0]['status'] == 'NOT_REGISTERED'
    assert cut['attempts'][0]['candidate_seq'] is None
    assert cut['registry_population']['trials'] == []
    assert cut['unique_definition_count'] == 1
    assert contents(project['root']) == before
    assert not (directory / 'archive').exists()
    assert cut['unfinished_attempts'] == [plan['trials'][0]['native_trial_id']]


def test_old_unmapped_registry_attempt_is_visible_and_not_complete(project):
    prepare(project, 1)
    project['store'].begin_search_attempt(search_id='old-unmapped', search_kind='FORWARD',
        source='old-producer', dataset_version='old-data', recorded_at=120)
    project['store'].register_search_candidates('old-unmapped', ['b'*64], registered_at=120)
    cut = reader(project)()
    assert cut['population_coverage'] == 'UNMAPPED'
    assert cut['unmapped']['search_candidates'] == [2]
    assert cut['unmapped']['definitions'] == ['b'*64]


def test_search_with_no_candidates_is_preserved(project):
    prepare(project, 1)
    project['store'].begin_search_attempt(search_id='empty-failure', search_kind='FORWARD',
        source='native-test', dataset_version='xau-frozen', recorded_at=120)
    project['store'].finish_search_attempt('empty-failure', status='FAILED', recorded_at=121)
    cut = reader(project)()
    events = [row for row in cut['registry_history']['events'] if row['search_id'] == 'empty-failure']
    assert [row['status'] for row in events] == ['STARTED', 'FAILED']
    assert cut['unmapped']['searches'] == ['empty-failure']


@pytest.mark.parametrize('attack', ['symlink_run', 'symlink_database', 'hardlink', 'extra_file', 'hidden_directory'])
def test_project_roster_and_file_custody_are_not_caller_selected(project, attack):
    _, directory = prepare(project, 1)
    if attack == 'symlink_run':
        (project['root']/'runs'/('d'*64)).symlink_to(directory, target_is_directory=True)
    elif attack == 'symlink_database':
        path = directory/'archive/trials.db'
        target = directory.parent.parent/'outside.db'
        path.rename(target); path.symlink_to(target)
    elif attack == 'hardlink':
        os.link(directory/'plan.json', directory.parent.parent/'alias.json')
    elif attack == 'extra_file':
        write(directory/'unmanaged.json', {})
    else:
        (project['root']/'runs'/'.hidden').mkdir()
    with pytest.raises(ValueError): reader(project)()


def test_deleted_run_cannot_shrink_original_attempt_population(project):
    import shutil
    prepare(project, 1)
    _, directory = prepare(project, 2)
    shutil.rmtree(directory)
    cut = reader(project)()
    assert cut['population_coverage'] == 'UNMAPPED'
    assert cut['unmapped']['search_candidates'] == [2]
    assert len(cut['registry_population']['provenance']) == 2
    assert cut['attempt_count'] == 2
    assert cut['mapped_attempt_count'] == 1


def test_changed_self_consistent_plan_fails_original_provenance_binding(project):
    plan, directory = prepare(project, 1)
    plan['actor_id'] = 'different-automated-actor'
    write(directory/'plan.json', plan)
    with pytest.raises(ValueError, match='PROVENANCE'): reader(project)()


def test_ledger_with_unplanned_native_trial_rejects(project):
    _, directory = prepare(project, 1)
    with TrialLedger(str(directory/'archive/trials.db'), create=False) as ledger:
        ledger.append(TrialRecord('a'*64, 'b'*64, 'c'*64, 'd'*64, 'e'*64, 'PLANNED', 0, 200))
    with pytest.raises(ValueError, match='NATIVE_TRIAL'): reader(project)()


def test_ledger_append_during_last_read_is_detected(project, monkeypatch):
    from services.research.research_evidence_reader import _ReadSet
    plan, directory = prepare(project, 1, status='PLANNED')
    original = _ReadSet.recheck
    def append_then_check(reads):
        row = plan['trials'][0]
        with TrialLedger(str(directory/'archive/trials.db'), create=False) as ledger:
            ledger.append(TrialRecord(row['native_trial_id'], row['alpha_id'], row['family_id'],
                row['config_hash'], row['validation_policy_digest'], 'RUNNING', 1, 120))
        return original(reads)
    monkeypatch.setattr(_ReadSet, 'recheck', append_then_check)
    with pytest.raises(ValueError, match='CHANGED'): reader(project)()


def running_trial(f):
    plan, directory = prepare(f, 1, status='PLANNED')
    row = plan['trials'][0]
    with TrialLedger(str(directory/'archive/trials.db'), create=False) as ledger:
        ledger.append(TrialRecord(row['native_trial_id'], row['alpha_id'], row['family_id'],
            row['config_hash'], row['validation_policy_digest'], 'RUNNING', 1, 120))
    return plan, directory


def test_original_validation_receipts_are_in_the_semantic_project_cut(project):
    plan, directory = running_trial(project)
    before = reader(project)()
    with TrialLedger(str(directory/'archive/trials.db'), create=False) as ledger:
        ledger.claim_validation_stage(plan['trials'][0]['native_trial_id'], 'cheap',
            owner='original-validator', now=130, lease_seconds=5, panel_digest='1'*64, policy_digest='2'*64)
        expected = dict(zip(('event_count', 'head_hash'), ledger._c.execute(
            'SELECT event_count,head_hash FROM validation_receipt_heads').fetchone()))
    after = reader(project)()
    assert before['cut_sha256'] != after['cut_sha256']
    retained = after['runs'][plan['invocation_id']]['ledger']
    assert retained['validation_receipt_head'] == expected
    assert len(retained['validation_receipts']) == 1
    assert retained['validation_receipts'][0]['owner'] == 'original-validator'


def test_receipt_row_budget_precedes_original_full_materialization(project, monkeypatch):
    from services.research import project_research_evidence_reader as module
    plan, directory = running_trial(project)
    with TrialLedger(str(directory/'archive/trials.db'), create=False) as ledger:
        for index in range(4):
            ledger.claim_validation_stage(plan['trials'][0]['native_trial_id'], 'cheap',
                owner='original-validator', now=130+index*6, lease_seconds=5,
                panel_digest='1'*64, policy_digest='2'*64)
    monkeypatch.setattr(module, 'MAX_ROWS', 1)
    monkeypatch.setattr(TrialLedger, 'verify', lambda _:pytest.fail('large receipt set entered native materialization'))
    with pytest.raises(ValueError, match='RECEIPT.*BOUND'): reader(project)()


def test_directory_budget_does_not_materialize_the_entire_roster(project, monkeypatch):
    from services.research import project_research_evidence_reader as module
    for number in range(1, 6): prepare(project, number, register=False, status=None)
    target = project['root']/'runs'
    original = os.listdir
    def forbid_full_roster(path):
        actual = Path(os.readlink(f'/proc/self/fd/{path}')) if isinstance(path, int) else Path(path)
        if actual == target:
            pytest.fail('directory bound was checked after full listdir materialization')
        return original(path)
    monkeypatch.setattr(module, 'MAX_FILES', 2)
    monkeypatch.setattr(os, 'listdir', forbid_full_roster)
    with pytest.raises(ValueError, match='DIRECTORY_BOUND'): reader(project)()
