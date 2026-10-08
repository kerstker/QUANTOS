"""Capture whole native registered producers, with unchanged original results."""
import copy
import json

import pytest

from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tools import gold_bar_experiment as gold


def reader(f):
    return ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')


def test_real_two_gold_runs_and_a_failed_run_preserve_all_nine_attempts(registered_case, monkeypatch):
    f = registered_case
    first, second = f['prepare']('one'), f['prepare']('two')
    outputs = [execute(f, name) for name in ('one', 'two')]
    failed = f['prepare']('failed')
    original = gold._execute
    def bad_input(arguments):
        actual = copy.deepcopy(arguments)
        actual['bar_receipt']['response_sha256'] = '0'*64
        return original(actual)
    monkeypatch.setattr(gold, '_execute', bad_input)
    with pytest.raises(ValueError): execute(f, 'failed')
    cut = reader(f)()
    assert cut['population_coverage'] == 'COMPLETE'
    assert cut['attempt_count'] == 9
    assert cut['unique_definition_count'] == 3
    assert cut['unfinished_attempts'] == []
    assert len(cut['registry_population']['search_candidates']) == 9
    assert len(cut['registry_population']['provenance']) == 9
    assert all(run['partial'] == [] for run in cut['runs'].values())
    for plan in (first, second, failed):
        run = cut['runs'][plan['invocation_id']]
        assert run['ledger']['event_count'] == 9
        events = [row for rows in run['ledger']['history'].values() for row in rows]
        assert sorted(row['global_seq'] for row in events) == list(range(1, 10))
    assert [a['status'] for a in cut['attempts']].count('FAILED') == 3
    for name, expected in zip(('one', 'two'), outputs):
        report = json.loads((f['directories'][name]/'archive/report.json').read_bytes())
        assert report['result_hash'] == expected['result_hash']
        assert report['economic_eligible'] is False


def test_new_original_search_changes_cut_even_when_definition_count_is_unchanged(registered_case):
    f = registered_case
    f['prepare']('one'); execute(f)
    first = reader(f)()
    f['prepare']('two'); execute(f, 'two')
    second = reader(f)()
    assert first['unique_definition_count'] == second['unique_definition_count'] == 3
    assert first['attempt_count'] == 3 and second['attempt_count'] == 6
    assert first['cut_sha256'] != second['cut_sha256']


def test_real_registration_receipt_tamper_is_not_accepted_as_a_population(registered_case):
    f = registered_case
    f['prepare'](); execute(f)
    path = f['directories']['one']/'registration.json'
    value = json.loads(path.read_bytes())
    value['provenance'] = value['provenance'][:-1]
    path.write_bytes(f['run'].canonical(value))
    with pytest.raises(ValueError, match='REGISTRATION_RECEIPT'): reader(f)()


def test_removing_real_outcome_keeps_original_terminal_ledger_but_marks_unknown(registered_case):
    f = registered_case
    plan = f['prepare'](); execute(f)
    path = f['directories']['one']/'outcome.json'
    path.unlink()
    cut = reader(f)()
    assert cut['runs'][plan['invocation_id']]['partial'] == ['OUTCOME_UNKNOWN']
    assert all(row['status'] == 'PASSED' for row in cut['attempts'])
    assert cut['economic_authority'] is False


def test_gold_and_technical_originals_share_a_project_without_merging_ledgers(registered_case, technical_arguments):
    from tools import registered_technical_research_run as technical
    from quantos.contracts.registered_technical_input import project_manifest_inputs
    import base64
    f = registered_case
    gold_plan = f['prepare'](); execute(f)
    plan = technical.prepare_technical(technical_arguments, project_root=f['root']/'project',
        search_id='technical', producer_id='isolated-native', actor_id='AUTOMATED_TEST',
        dataset_version='native-technical', registered_at_ms=f['clock'][0])
    directory = f['run'].run_directory(f['root']/'project', plan)
    result = technical.run_technical(technical_arguments, destination=directory, registry=f['registry'],
                                    clock_ms=lambda:f['clock'][0])
    repeated = technical.prepare_technical(technical_arguments, project_root=f['root']/'project',
        search_id='technical-repeat', producer_id='isolated-native', actor_id='AUTOMATED_TEST',
        dataset_version='native-technical', registered_at_ms=f['clock'][0])
    second = technical.run_technical(technical_arguments,
        destination=f['run'].run_directory(f['root']/'project', repeated), registry=f['registry'],
        clock_ms=lambda:f['clock'][0])
    assert second == result
    assert repeated['trials'][0]['native_trial_id'] != plan['trials'][0]['native_trial_id']
    value = reader(f)()
    assert value['attempt_count'] == 5
    assert value['unique_definition_count'] == 4
    assert value['population_coverage'] == 'COMPLETE'
    assert value['all_runs_finalized'] is True
    assert value['unfinished_runs'] == []
    assert value['runs'][gold_plan['invocation_id']]['ledger']['event_count'] == 9
    run = value['runs'][plan['invocation_id']]
    assert run['ledger']['event_count'] == 3
    document = json.loads(base64.b64decode(run['originals']['input.json']['bytes_base64']))
    derived = project_manifest_inputs(document, manifest=result['detail']['run_manifest_v2'])
    assert derived['schema'] == 'QUANTOS_TECHNICAL_PERPETUAL_INPUT_V1'
    assert result['authoritative'] is False
    assert result['detail']['engine_attestations']['risk_history_ledger_replayed'] is False
