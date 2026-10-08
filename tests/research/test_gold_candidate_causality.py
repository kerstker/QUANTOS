"""Actual Gold signal paths must be invariant to unavailable observations."""
from dataclasses import asdict, replace
from decimal import Decimal as D, localcontext

import pytest

from services.research.gold_bar_candidates import CausalGoldBarEvaluator
from tests.research.test_gold_bar_candidates import bars


def inputs(*, nonmonotone=False):
    values = list(bars())
    if nonmonotone:
        values[-2] = replace(values[-2], available_ms=values[-1].available_ms + 1)
    rows = [dict(timestamp=values[-1].available_ms + i) for i in (0, 1, 2)]
    return values, rows


def policy():
    from quantos.contracts.gold_causality_policy import declare
    values, rows = inputs()
    sample = dict(start_ms=rows[0]['timestamp'], end_ms=rows[-1]['timestamp'] + 10,
                  frequency_ms=1)
    return declare(sample=sample, declared_at_ms=sample['start_ms'] - 1)


def assess(values=None, rows=None, *, candidate='net_portfolio', declared=None):
    from services.research.gold_candidate_causality import assess_candidate
    b, r = inputs()
    return assess_candidate(bars=b if values is None else values,
        decision_rows=r if rows is None else rows, candidate_id=candidate,
        policy=policy() if declared is None else declared,
        subject=dict(invocation_id='a'*64, native_trial_id='b'*64, config_hash='c'*64),
        native_result_hash='d'*64,
        market_history_policy='GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1')


@pytest.mark.parametrize('nonmonotone', [False, True])
def test_whole_native_decision_axis_and_all_three_original_components_are_checked(nonmonotone):
    values, rows = inputs(nonmonotone=nonmonotone)
    result = assess(values, rows)
    assert result['status'] == 'PASS'
    assert result['decision_count'] == result['assessed_decisions'] == 3
    assert result['component_evaluations'] == 9 and result['unassessed_components'] == 0
    assert result['component_ids'] == ['channel', 'trend', 'reversion']
    assert result['runtime_path'] == ('ORIGINAL_FULL_HISTORY' if nonmonotone else 'ORIGINAL_CAUSAL_EVALUATOR')
    assert result['leakage_flag'] is False
    assert result['synthetic_future_probes_are_market_evidence'] is False
    assert result['independent_verification'] is result['economic_authority'] is result['submission_enabled'] is False


def test_original_incremental_future_read_is_detected(monkeypatch):
    original = CausalGoldBarEvaluator.evaluate
    def future(self, *, candidate, as_of_ms):
        result = original(self, candidate=candidate, as_of_ms=as_of_ms)
        if self._pending:
            result['entry_side'] = 'BUY' if self._pending[-1].close > D('500') else 'SELL'
        return result
    monkeypatch.setattr(CausalGoldBarEvaluator, 'evaluate', future)
    result = assess()
    assert result['status'] == 'FAIL' and result['leakage_flag'] is True
    assert result['failed_components'] > 0


def test_original_full_history_future_read_is_detected(monkeypatch):
    from services.research import gold_bar_candidates as signals
    original = signals.evaluate
    def future(values, *, candidate, as_of_ms):
        result = original(values, candidate=candidate, as_of_ms=as_of_ms)
        if not isinstance(values, CausalGoldBarEvaluator):
            result['entry_side'] = 'BUY' if values[-1].close > D('500') else 'SELL'
        return result
    monkeypatch.setattr(signals, 'evaluate', future)
    values, rows = inputs(nonmonotone=True)
    assert assess(values, rows)['leakage_flag'] is True


def test_one_failed_calculation_never_disappears_inside_an_otherwise_passed_review(monkeypatch):
    original = CausalGoldBarEvaluator.evaluate
    def fail_one(self, *, candidate, as_of_ms):
        if candidate == 'trend':
            raise ArithmeticError('synthetic unavailable calculation')
        return original(self, candidate=candidate, as_of_ms=as_of_ms)
    monkeypatch.setattr(CausalGoldBarEvaluator, 'evaluate', fail_one)
    result = assess()
    assert result['status'] == 'INSUFFICIENT' and result['leakage_flag'] is None
    assert result['unassessed_components'] == 3


def test_known_leak_is_retained_alongside_an_unassessable_component(monkeypatch):
    original = CausalGoldBarEvaluator.evaluate
    def mixed(self, *, candidate, as_of_ms):
        if candidate == 'trend':
            raise ArithmeticError('synthetic unavailable calculation')
        result = original(self, candidate=candidate, as_of_ms=as_of_ms)
        if candidate == 'channel' and self._pending:
            result['entry_side'] = 'BUY'
        return result
    monkeypatch.setattr(CausalGoldBarEvaluator, 'evaluate', mixed)
    result = assess()
    assert result['status'] == 'FAIL' and result['leakage_flag'] is True
    assert result['unassessed_components'] > 0


def test_single_candidate_original_observation_is_recomputed_not_trusted():
    values, rows = inputs()
    rows[0].update(status='READY', entry_side='WRONG_ORIGINAL_CLAIM')
    result = assess(values, rows, candidate='channel')
    assert result['status'] == 'FAIL'
    assert 'ORIGINAL_SIGNAL_OBSERVATION_MISMATCH' in result['reasons']


@pytest.mark.parametrize('damage', ['decision_budget', 'bar_budget', 'fallback_budget', 'clock', 'boolean'])
def test_budget_and_native_time_integrity_fail_closed_without_truncating_coverage(damage):
    values, rows = inputs(nonmonotone=damage == 'fallback_budget')
    p = policy()
    if damage == 'decision_budget':
        p['budget']['maximum_decisions'] = 2
    elif damage == 'bar_budget':
        p['budget']['maximum_bars'] = 2
    elif damage == 'fallback_budget':
        p['budget']['maximum_fallback_bar_reads'] = 2
    elif damage == 'clock':
        rows[1]['timestamp'] = rows[0]['timestamp']
    else:
        rows[1]['timestamp'] = True
    with pytest.raises(ValueError):
        assess(values, rows, declared=p)


@pytest.mark.parametrize('which', ['bars', 'decisions'])
def test_missing_originals_never_become_a_clean_leakage_flag(which):
    result = assess(values=[] if which == 'bars' else None, rows=[] if which == 'decisions' else None)
    assert result['status'] == 'INSUFFICIENT' and result['leakage_flag'] is None


@pytest.mark.parametrize('precision', [6, 28, 80])
def test_all_math_and_review_hashes_use_the_original_decimal_context(precision):
    expected = assess()
    with localcontext() as context:
        context.prec = precision
        assert assess() == expected


def test_original_report_bar_dictionary_path_preserves_identity():
    values, rows = inputs()
    dictionaries = [{k: str(v) if isinstance(v, D) else v for k, v in asdict(b).items()} for b in values]
    assert assess(dictionaries, rows) == assess(values, rows)


def test_read_only_original_report_views_preserve_bars_and_decision_identity():
    from types import MappingProxyType
    values,rows=inputs()
    dictionaries=[MappingProxyType({k:str(v) if isinstance(v,D) else v for k,v in asdict(b).items()}) for b in values]
    readonly_rows=[MappingProxyType(row) for row in rows]
    assert assess(dictionaries,readonly_rows)==assess(values,rows)


def test_original_causality_port_and_nested_historical_ports_expire_with_owner():
    from services.research.gold_native_source_reader import SourceReports
    from services.validation.project_research_evidence_artifact import retained_reports
    from quantos.contracts.registered_research_run import CHUNKED_GOLD_SCHEMAS, SOURCE_GOLD_SCHEMAS
    owner=SourceReports()
    owner['a'*64]=dict(synthetic_report=True)
    owner.source_inputs['a'*64]=[dict(synthetic_input=True)]
    schema=next(iter(sorted(set(CHUNKED_GOLD_SCHEMAS)-set(SOURCE_GOLD_SCHEMAS))))
    cut=dict(runs={'a'*64:dict(plan=dict(schema=schema),result_material=dict(status='COMPLETE'))})
    first=retained_reports(cut,owner)
    second=retained_reports(cut,first)
    values,rows=inputs()
    arguments=dict(bars=values,decision_rows=rows,candidate_id='net_portfolio',policy=policy(),
        subject=dict(invocation_id='a'*64,native_trial_id='b'*64,config_hash='c'*64),
        native_result_hash='d'*64,market_history_policy='GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1')
    for port in (owner,first,second):
        assert port.review_gold_causality(**arguments)['status']=='PASS'
    owner.close_inputs()
    for port in (owner,first,second):
        with pytest.raises(ValueError,match='CONTEXT_CLOSED'):
            port.review_gold_causality(**arguments)
