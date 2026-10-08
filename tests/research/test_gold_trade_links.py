import copy

import pytest

from services.research.gold_trade_observation import assess
from services.research.gold_trade_links import associate, report_digest
from tests.research.test_gold_trade_observation import document
from tests.tools.test_gold_signal_replay import archive
from tools.gold_signal_replay import replay


def inputs():
    fills = document()
    fills.update(end_ms=5000, exported_at_ms=6000)
    fills['fills'][0]['time_ms'] = 3500
    signals = replay(archive())
    annotations = {'schema': 'GOLD_TRADE_LINKS_V1',
        'fills_facts_sha256': assess(fills)['facts_sha256'],
        'replay_sha256': report_digest(signals), 'links': [{
            'trade_id': '123', 'decision_id': signals['records'][1]['decision']['decision_id'],
            'role': 'ENTRY', 'annotated_at_ms': 7000}]}
    return fills, signals, annotations


def test_explicit_label_is_never_execution_or_profitability_proof():
    f, s, a = inputs()
    result = associate(f, s, a, as_of_ms=8000)
    assert result['trades'][0]['status'] == 'DECLARED_LINK'
    assert result['trades'][0]['rule_id'] == s['rule_id']
    assert result['strategy_pnl'] is None and result['authoritative'] is False
    assert result['actor_authenticated'] is False and result['causal_attribution'] is False


def test_close_in_time_without_annotation_remains_unlinked():
    f, s, a = inputs()
    a['links'] = []
    result = associate(f, s, a, as_of_ms=8000)
    assert result['trades'][0]['status'] == 'UNLINKED'
    assert len(result['signals_without_declared_trade']) == 1


@pytest.mark.parametrize('at,status', [(3000,'TRADE_PRECEDES_SIGNAL'),(3010,'SAME_MILLISECOND_UNRESOLVED')])
def test_no_retrospective_time_causality(at, status):
    f, s, a = inputs()
    f['fills'][0]['time_ms'] = at
    a['fills_facts_sha256'] = assess(f)['facts_sha256']
    result = associate(f, s, a, as_of_ms=8000)
    assert result['trades'][0]['status'] == status
    assert result['unique_fills'] == 1


def test_direction_conflict_keeps_the_actual_fill():
    f, s, a = inputs()
    f['fills'][0]['side'] = 'SELL'
    a['fills_facts_sha256'] = assess(f)['facts_sha256']
    assert associate(f, s, a, as_of_ms=8000)['trades'][0]['status'] == 'DIRECTION_CONFLICT'
    a['links'][0]['role'] = 'EXIT'
    assert associate(f, s, a, as_of_ms=8000)['trades'][0]['status'] == 'DECLARED_LINK'


def test_orphan_annotation_is_visible_and_not_counted_as_a_fill():
    f, s, a = inputs()
    a['links'][0]['trade_id'] = '999'
    result = associate(f, s, a, as_of_ms=8000)
    assert result['unique_fills'] == 1
    assert result['orphan_trade_ids'] == ['999']
    assert result['trades'][0]['status'] == 'UNLINKED'


@pytest.mark.parametrize('case', ['binding','future','before_fill','conflict','extra'])
def test_ambiguous_annotations_are_rejected(case):
    f, s, a = inputs()
    if case == 'binding': a['fills_facts_sha256'] = '0'*64
    if case == 'future': a['links'][0]['annotated_at_ms'] = 9000
    if case == 'before_fill': a['links'][0]['annotated_at_ms'] = 3000
    if case == 'conflict': a['links'].append(dict(a['links'][0],role='EXIT'))
    if case == 'extra': a['human_verified'] = True
    with pytest.raises(ValueError): associate(f, s, a, as_of_ms=8000)


def test_duplicate_fills_and_annotations_do_not_double_count():
    f, s, a = inputs()
    f['fills'].append(copy.deepcopy(f['fills'][0]))
    a['links'].append(copy.deepcopy(a['links'][0]))
    result = associate(f, s, a, as_of_ms=8000)
    assert result['unique_fills'] == 1 and len(result['trades']) == 1


def test_non_entry_decision_cannot_be_assigned_as_an_entry_signal():
    f, s, a = inputs()
    a['links'][0]['decision_id'] = s['records'][0]['decision']['decision_id']
    assert associate(f, s, a, as_of_ms=8000)['trades'][0]['status'] == 'UNKNOWN_ENTRY_SIGNAL'


def test_exit_only_declaration_does_not_cover_an_entry_signal():
    f, s, a = inputs()
    f['fills'][0]['side'] = 'SELL'
    a['fills_facts_sha256'] = assess(f)['facts_sha256']
    a['links'][0]['role'] = 'EXIT'
    result = associate(f, s, a, as_of_ms=8000)
    assert result['signals_without_declared_trade'] == []
    assert result['signals_without_declared_entry'] == [a['links'][0]['decision_id']]
    assert result['review_counts'] == {
        'fills_by_status': {'DECLARED_LINK': 1},
        'declared_entry_fills': 0, 'declared_exit_fills': 1,
        'entry_signals': 1, 'signals_with_declared_entry': 0,
        'orphan_annotations': 0}
    row = result['trades'][0]
    assert row['trade_minus_signal_ms'] == 490
    assert row['position_effect'] == 'UNKNOWN'


def test_review_counts_preserve_conflicts_unlinked_and_deduplicate():
    f, s, a = inputs()
    f['fills'].append(copy.deepcopy(f['fills'][0]))
    f['fills'].append(dict(f['fills'][0], trade_id='124', side='SELL'))
    f['fills'].append(dict(f['fills'][0], trade_id='125'))
    a['links'].append(dict(a['links'][0], trade_id='124'))
    a['links'].append(dict(a['links'][0], trade_id='999'))
    a['links'].append(copy.deepcopy(a['links'][0]))
    a['fills_facts_sha256'] = assess(f)['facts_sha256']
    result = associate(f, s, a, as_of_ms=8000)
    counts = result['review_counts']
    assert counts['fills_by_status'] == {'DECLARED_LINK':1, 'DIRECTION_CONFLICT':1, 'UNLINKED':1}
    assert counts['declared_entry_fills'] == counts['signals_with_declared_entry'] == 1
    assert counts['orphan_annotations'] == 1
    assert sum(counts['fills_by_status'].values()) == result['unique_fills'] == 3
    assert result['signals_without_declared_entry'] == []
    assert result['trades'][2]['trade_minus_signal_ms'] is None


def test_time_difference_is_signed_diagnostic_not_execution_latency():
    f, s, a = inputs()
    f['fills'][0]['time_ms'] = 3000
    a['fills_facts_sha256'] = assess(f)['facts_sha256']
    result = associate(f, s, a, as_of_ms=8000)
    assert result['trades'][0]['trade_minus_signal_ms'] == -10
    assert result['review_counts']['signals_with_declared_entry'] == 0
    assert 'NO_EXECUTION_LATENCY_MEASUREMENT' in result['limitations']
