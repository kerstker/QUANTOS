"""Candidate model facts come from verified originals, never healthy defaults."""

from copy import deepcopy
from decimal import Decimal as D, localcontext

import pytest


def context():
    from quantos.contracts.research_statistical_review import SCOPED_GOLD_REPORT_SCHEMA
    selected = dict(invocation_id='a' * 64, native_trial_id='b' * 64, config_hash='c' * 64)
    cut = dict(cut_sha256='d' * 64, attempt_count=4, unique_definition_count=4)
    subject = dict(selected)
    report = dict(schema=SCOPED_GOLD_REPORT_SCHEMA, selected_attempt=deepcopy(selected),
        project_cut_sha256=cut['cut_sha256'], as_of_ms=1000,
        population=dict(attempt_count=4, unique_definition_count=4),
        metrics=dict(dsr='0.8', pbo='0.2', n_trials=4), reasons=[])
    return cut, subject, dict(report=report), 'e' * 64


def build(cut=None, subject=None, statistics=None, *, native_result=None, checked_at=1000):
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    c, s, v, result = context()
    return from_verified_statistics(project_cut=c if cut is None else cut,
        subject=s if subject is None else subject,
        verified_statistics=v if statistics is None else statistics,
        candidate_id='net_portfolio', checked_at_ms=checked_at,
        native_result_hash=result if native_result is None else native_result)


def test_actual_dsr_pbo_and_original_population_are_bound_to_one_candidate():
    value = build()
    p = value.projection
    assert p['candidate_id'] == 'net_portfolio'
    assert p['attempt_count'] == 4 and p['unique_definition_count'] == 4
    assert p['facts']['validation_confidence']['value'] == '0.8'
    assert p['facts']['overfit_flag']['value'] is False
    assert p['facts']['age_days']['value'] == '0'
    assert p['scope'] == 'CANDIDATE_SNAPSHOT_REVIEW_ONLY_NOT_PAST_PRETRADE'
    assert value.engine_metadata is None
    assert p['economic_authority'] is p['submission_enabled'] is False


def test_missing_forward_maintenance_and_leakage_evidence_stays_unknown():
    value = build()
    for name in ('paper_confidence', 'decay_flag', 'leakage_flag'):
        assert value.projection['facts'][name]['status'] == 'UNAVAILABLE'
        assert value.projection['facts'][name]['value'] is None
    assert value.projection['missing_fields'] == ['decay_flag', 'leakage_flag', 'paper_confidence']
    assert value.engine_metadata is None


def test_global_dsr_unavailable_never_reuses_a_local_or_successful_claim():
    cut, subject, stats, _ = context()
    stats['report']['metrics']['dsr'] = None
    stats['report']['metrics']['pbo'] = None
    stats['report']['statistical_decision'] = 'PASS'
    value = build(cut, subject, stats)
    assert value.projection['facts']['validation_confidence']['value'] is None
    assert value.projection['facts']['overfit_flag']['value'] is None


def test_explicit_original_overfit_finding_is_not_erased_by_other_missing_fields():
    cut, subject, stats, _ = context()
    stats['report']['metrics']['pbo'] = '0.7'
    value = build(cut, subject, stats)
    assert value.projection['facts']['overfit_flag']['value'] is True
    assert value.projection['known_failure_fields'] == ['overfit_flag']
    assert value.engine_metadata is None


@pytest.mark.parametrize('damage', ['cut', 'trial', 'config', 'invocation', 'count', 'future',
    'bool', 'nan', 'range', 'native_hash'])
def test_unbound_future_or_invalid_model_statistics_fail_closed(damage):
    cut, subject, stats, native = context()
    checked = 1000
    if damage == 'cut':
        stats['report']['project_cut_sha256'] = 'f' * 64
    elif damage in ('trial', 'config', 'invocation'):
        field = {'trial': 'native_trial_id', 'config': 'config_hash', 'invocation': 'invocation_id'}[damage]
        stats['report']['selected_attempt'][field] = 'f' * 64
    elif damage == 'count':
        stats['report']['metrics']['n_trials'] = 3
    elif damage == 'future':
        checked = 999
    elif damage == 'bool':
        stats['report']['metrics']['dsr'] = True
    elif damage == 'nan':
        stats['report']['metrics']['pbo'] = 'NaN'
    elif damage == 'range':
        stats['report']['metrics']['dsr'] = '1.01'
    else:
        native = 'not-a-hash'
    with pytest.raises(ValueError):
        build(cut, subject, stats, native_result=native, checked_at=checked)


@pytest.mark.parametrize('precision', [6, 28, 80])
def test_model_facts_and_hashes_ignore_ambient_decimal_precision(precision):
    expected = build().projection
    with localcontext() as c:
        c.prec = precision
        actual = build().projection
    assert actual == expected


def test_later_consumption_does_not_rewrite_signed_snapshot_age():
    assert build(checked_at=100000).projection == build().projection


def test_original_project_subject_needs_no_invented_candidate_field():
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    cut, subject, statistics, native = context()
    assert 'candidate_id' not in subject
    value = from_verified_statistics(project_cut=cut, subject=subject,
        candidate_id='net_portfolio', verified_statistics=statistics,
        checked_at_ms=1000, native_result_hash=native)
    assert 'candidate_id' not in subject
    assert value.projection['candidate_id'] == 'net_portfolio'


def assert_original_model_binding(facts, cut, statistics, candidate_id):
    """Check actual recomputed/exported materials, not a parallel model claim."""
    from quantos.contracts.research_statistical_review import digest
    from quantos.contracts.decimal_arithmetic import canonical_decimal
    risk = facts['execution_risk_review']['account_risk_inputs']
    model = risk['model_inputs']
    report = statistics['report']
    assert model['candidate_id'] == candidate_id
    assert model['project_cut_sha256'] == cut['cut_sha256']
    assert model['attempt_count'] == cut['attempt_count']
    assert model['statistical_report_sha256'] == digest(report) == facts['statistical_review']['report_sha256']
    assert model['statistical_evidence_sha256'] == digest(statistics) == facts['statistical_review']['evidence_sha256']
    for name in ('invocation_id', 'native_trial_id', 'config_hash'):
        assert model[name] == report['selected_attempt'][name]
    dsr = report['metrics']['dsr']
    assert model['facts']['validation_confidence']['value'] == (None if dsr is None else canonical_decimal(D(str(dsr))))
    assert model['snapshot_as_of_ms'] == report['as_of_ms']
    assert model['scope'] == 'CANDIDATE_SNAPSHOT_REVIEW_ONLY_NOT_PAST_PRETRADE'
    for name in ('paper_confidence', 'decay_flag', 'leakage_flag'):
        assert model['facts'][name]['status'] == 'UNAVAILABLE' and model['facts'][name]['value'] is None
    assert risk['source_groups']['alpha_metadata']['status'] == 'PARTIAL_ORIGINAL_FACTS'
    gate = facts['execution_risk_review']['nine_engine_replay']['engines']['model_alpha']
    assert gate['model_inputs_sha256'] == model['projection_sha256']
    assert gate['invocations'] == 0 and gate['original_report'] is None
    assert gate['missing_fields'] == model['missing_fields']
    assert model['economic_authority'] is model['submission_enabled'] is False
    if model['schema'] in ('QUANTOS_GOLD_MODEL_RISK_INPUTS_V2','QUANTOS_GOLD_MODEL_RISK_INPUTS_V3','QUANTOS_GOLD_MODEL_RISK_INPUTS_V4'):
        causal=model['causality_review']
        assert causal['native_result_hash']==model['native_result_hash']
        assert causal['policy_sha256']==model['causality_policy_sha256']
        assert causal['decision_count']==causal['assessed_decisions']>0
        assert causal['component_evaluations']==causal['decision_count']*(3 if candidate_id=='net_portfolio' else 1)
        assert causal['unassessed_components']==0 and causal['status']=='PASS'
        assert causal['synthetic_future_probes_are_market_evidence'] is False
        assert model['facts']['leakage_flag']['value'] is None
        assert 'OOS_AND_SEARCH_SELECTION_LEAKAGE_NOT_YET_ASSESSED' in model['limitations']

    if model['schema'] in ('QUANTOS_GOLD_MODEL_RISK_INPUTS_V3','QUANTOS_GOLD_MODEL_RISK_INPUTS_V4'):
        forward=model['net_forward_review']
        assert facts['schema']==('QUANTOS_PROJECT_RESEARCH_FACTS_V17' if model['schema']=='QUANTOS_GOLD_MODEL_RISK_INPUTS_V4' else 'QUANTOS_PROJECT_RESEARCH_FACTS_V16')
        assert forward['status']=='INSUFFICIENT' and forward['paper_confidence'] is None
        assert forward['project_cut_sha256']==cut['cut_sha256']
        assert forward['snapshot_as_of_ms']==report['as_of_ms']
        assert forward['native_result_hash']==model['native_result_hash']
        assert forward['policy_sha256']==model['net_forward_policy_sha256']
        assert forward['completed_active_periods']==0 and len(forward['episodes'])==2
        assert all(e['original_cash_identity_sha256'] for e in forward['episodes'])
        assert forward['independent_verification'] is forward['economic_authority'] is forward['submission_enabled'] is False


def causal_context():
    from tests.research.test_gold_candidate_causality import inputs, policy
    from services.research.gold_candidate_causality import assess_candidate
    cut, subject, statistics, native = context()
    values, rows = inputs()
    declared = policy()
    review = assess_candidate(bars=values,decision_rows=rows,candidate_id='net_portfolio',
        policy=declared,subject=subject,native_result_hash=native,
        market_history_policy='GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1')
    statistics['report']['as_of_ms']=rows[-1]['timestamp']+1
    return dict(project_cut=cut,subject=subject,candidate_id='net_portfolio',
        verified_statistics=statistics,checked_at_ms=statistics['report']['as_of_ms'],
        native_result_hash=native,causality_policy=declared,causality_review=review)


def test_signal_causality_pass_does_not_claim_selection_or_oos_leakage_is_clear():
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    arguments=causal_context()
    result=from_verified_statistics(**arguments)
    p=result.projection
    assert p['schema']=='QUANTOS_GOLD_MODEL_RISK_INPUTS_V2'
    assert p['causality_review']==arguments['causality_review']
    assert p['facts']['leakage_flag']['value'] is None
    assert 'OOS_AND_SEARCH_SELECTION_LEAKAGE_NOT_YET_ASSESSED' in p['limitations']
    assert result.engine_metadata is None


def test_native_future_leak_is_known_failure_even_without_forward_or_maintenance(monkeypatch):
    from services.research.gold_bar_candidates import CausalGoldBarEvaluator
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    original=CausalGoldBarEvaluator.evaluate
    def future(self, *, candidate, as_of_ms):
        value=original(self,candidate=candidate,as_of_ms=as_of_ms)
        if self._pending:value['entry_side']='BUY'
        return value
    monkeypatch.setattr(CausalGoldBarEvaluator,'evaluate',future)
    result=from_verified_statistics(**causal_context())
    assert result.projection['facts']['leakage_flag']['value'] is True
    assert result.projection['known_failure_fields']==['leakage_flag']
    assert result.engine_metadata is None


@pytest.mark.parametrize('damage',['native','candidate','trial','hash','policy','future'])
def test_causality_originals_cannot_be_substituted_or_backdated(damage):
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    from quantos.streaming_identity import exact_stream_identity_hash
    arguments=causal_context()
    report=arguments['causality_review']
    if damage=='native':report['native_result_hash']='f'*64
    elif damage=='candidate':report['candidate_id']='channel'
    elif damage=='trial':report['native_trial_id']='f'*64
    elif damage=='policy':report['policy_sha256']='f'*64
    elif damage=='future':arguments['checked_at_ms']=report['actual_last_decision_ms']-1
    else:report['decision_count']+=1
    if damage!='hash':
        report['review_sha256']=exact_stream_identity_hash({k:v for k,v in report.items() if k!='review_sha256'})
    with pytest.raises(ValueError):from_verified_statistics(**arguments)
