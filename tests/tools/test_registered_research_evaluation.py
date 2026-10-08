"""Historical samples with a real pre-execution evaluation commitment."""
import dataclasses
import json

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.validation.test_research_evaluation_plan import core


def declaration(f, arguments, invocation='9'*64):
    from tools import registered_technical_research_run as technical
    from quantos.contracts import registered_research_run as contract
    candidate, config, _ = technical._candidate(arguments)
    value = core()
    value.update(registered_at_ms=f['clock'][0],
        input_sha256=contract.digest(technical.technical_input(arguments)),
        source_identity_sha256=contract.digest(arguments['release_identity']),
        dataset_manifest_sha256=contract.digest(arguments['dataset_identity']),
        cohort=[dict(candidate_id=candidate, config_hash=config,
            native_trial_id=contract.native_trial_id(invocation, candidate, config))])
    value['selection']['candidate_id'] = candidate
    return value


def prepare(f, arguments, evaluation, invocation='9'*64):
    from tools import registered_technical_research_run as technical
    return technical.prepare_technical(arguments, project_root=f['root']/'project',
        search_id='evaluation', producer_id='isolated-native', actor_id='AUTOMATED_TEST',
        dataset_version='native-technical', registered_at_ms=f['clock'][0],
        invocation_id=invocation, evaluation_core=evaluation)


def test_historical_sample_plan_is_committed_before_original_computation(registered_case, technical_arguments, monkeypatch):
    from tools import registered_technical_research_run as technical
    from quantos.contracts.research_evaluation_plan import core_sha256
    from services.research.trial_ledger import TrialLedger
    f = registered_case
    def gold(value):
        if dataclasses.is_dataclass(value):
            return dataclasses.replace(value, **{field.name:gold(getattr(value, field.name))
                for field in dataclasses.fields(value)})
        if type(value) is dict:
            return {gold(k):gold(v) for k,v in value.items()}
        if type(value) in (tuple, list):
            return type(value)(gold(v) for v in value)
        return value.replace('BTCUSDT', 'XAUUSDT') if type(value) is str else value
    technical_arguments = gold(technical_arguments)
    evaluation = declaration(f, technical_arguments)
    assert evaluation['sample']['end_ms'] < evaluation['registered_at_ms']
    plan = prepare(f, technical_arguments, evaluation)
    directory = f['run'].run_directory(f['root']/'project', plan)
    expected = core_sha256(evaluation)
    assert plan['schema'] == 'QUANTOS_REGISTERED_RESEARCH_RUN_V2'
    assert plan['evaluation_core'] == evaluation
    original = technical.native.run_authoritative_linear_perpetual_v2
    calls = []
    def compute(**kwargs):
        saved = json.loads((directory/'plan.json').read_bytes())
        assert saved['evaluation_core'] == evaluation
        with TrialLedger(str(directory/'archive/trials.db'), create=False, read_only=True) as ledger:
            events = ledger.load(plan['trials'][0]['native_trial_id']).events
            assert [e.status for e in events] == ['PLANNED', 'RUNNING']
            assert all(e.validation_policy_digest == expected for e in events)
        assert f['registry'].trial_provenance(plan['trials'][0]['provenance_trial_id'])['record']['artifact_hashes'][0] == f['run'].plan_digest(saved)
        calls.append(1)
        return original(**kwargs)
    monkeypatch.setattr(technical.native, 'run_authoritative_linear_perpetual_v2', compute)
    result = technical.run_technical(technical_arguments, destination=directory,
        registry=f['registry'], clock_ms=lambda:f['clock'][0])
    assert calls == [1]
    assert result['authoritative'] is False
    assert prepare(f, technical_arguments, evaluation) == plan
    assert technical.run_technical(technical_arguments, destination=directory,
        registry=f['registry'], clock_ms=lambda:f['clock'][0]) == result
    assert calls == [1]
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation.project_research_evidence_authority import project_subject, project_facts
    cut = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')()
    subject = project_subject(cut, invocation_id=plan['invocation_id'],
                              native_trial_id=plan['trials'][0]['native_trial_id'])
    projected = project_facts(cut, subject)
    assert projected['native_result_hash'] == result['detail']['result_hash']
    identity = result['detail']['result_hash_payload']['reconciliation']
    assert projected['metrics']['ending_equity'] == identity['ending_equity']
    assert projected['metrics']['reconciliation_residual'] == '0'
    assert projected['evaluation_core']['oos_status'] == 'UNKNOWN'
    assert 'EVALUATION_PLAN_NOT_PREREGISTERED' not in projected['limitations']
    assert projected['promotion_approved'] is projected['submission_enabled'] is False


@pytest.mark.parametrize('field', ['input_sha256', 'source_identity_sha256', 'dataset_manifest_sha256', 'registered_at_ms', 'cohort'])
def test_wrong_evaluation_binding_rejects_before_persisting_any_plan(registered_case, technical_arguments, field):
    f = registered_case
    value = declaration(f, technical_arguments)
    if field == 'registered_at_ms':
        value[field] -= 1
    elif field == 'cohort':
        value[field][0]['native_trial_id'] = '8'*64
    else:
        value[field] = '8'*64
    with pytest.raises(ValueError):
        prepare(f, technical_arguments, value)
    assert not (f['root']/'project').exists()


def test_completed_v1_run_cannot_acquire_a_posthoc_evaluation_plan(registered_case, technical_arguments):
    from tools import registered_technical_research_run as technical
    f = registered_case
    plan = technical.prepare_technical(technical_arguments, project_root=f['root']/'project',
        search_id='evaluation', producer_id='isolated-native', actor_id='AUTOMATED_TEST',
        dataset_version='native-technical', registered_at_ms=f['clock'][0], invocation_id='9'*64)
    directory = f['run'].run_directory(f['root']/'project', plan)
    technical.run_technical(technical_arguments, destination=directory,
        registry=f['registry'], clock_ms=lambda:f['clock'][0])
    before = {p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        prepare(f, technical_arguments, declaration(f, technical_arguments))
    assert {p:p.read_bytes() for p in directory.rglob('*') if p.is_file()} == before


def test_actual_bitcoin_attempt_cannot_be_labelled_for_gold_only_authority(registered_case, technical_arguments):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation.project_research_evidence_authority import project_subject
    f = registered_case
    plan = prepare(f, technical_arguments, declaration(f, technical_arguments))
    cut = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')()
    with pytest.raises(ValueError, match='MARKET'):
        project_subject(cut, invocation_id=plan['invocation_id'], native_trial_id=plan['trials'][0]['native_trial_id'])
