"""Reported reconciliation can feed research without erasing native stream gaps."""
from copy import deepcopy
import json

import pytest

from services.research.demo_account_recovery import reconcile
from tests.research.test_gold_reported_short import short_inputs
from tests.tools.test_gold_bar_feedback_publish import document
from tools.demo_operator_setup import digest


def reported(tmp_path):
    inputs = short_inputs(tmp_path)
    report, ledger = reconcile(**inputs)
    assert ledger is not None
    value = document()
    del value['cash_feedback'], value['cash_feedback_sha256'], value['result_hash']
    value.update(schema='GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V1', source_commit='c'*40,
        available_at_ms=inputs['statements'][-1]['completed_at_ms'],
        inputs_sha256=digest(inputs), dispatch_evidence_sha256='e'*64,
        reservations=['1'*64, '2'*64], account_report=report,
        account_report_sha256=digest(report), stream_coverage_repaired=False)
    value['result_hash'] = digest(value)
    return value


@pytest.mark.parametrize('damage', [None, 'source', 'future', 'gap_claim', 'wallet',
                                    'ledger', 'manual', 'definition', 'nested_hash'])
def test_reported_publication_keeps_original_report_and_rejects_damage(tmp_path, damage):
    from tools.demo_feedback_publish import validate_bar_export
    value = reported(tmp_path)
    original = deepcopy(value)
    if damage == 'source':
        value['source_commit'] = 'd'*40
    elif damage == 'future':
        value['available_at_ms'] += 1
    elif damage == 'gap_claim':
        value['account_report']['stream_coverage_repaired'] = True
    elif damage == 'wallet':
        value['account_report']['wallet_residual'] = '1'
    elif damage == 'ledger':
        value['account_report']['ledger_state']['wallet_balance'] = '0'
    elif damage == 'manual':
        value['account_report']['manual_order_ids'] = ['999']
    elif damage == 'definition':
        value['definition']['candidate'] = 'unknown'
    elif damage == 'nested_hash':
        value['account_report_sha256'] = '0'*64
    if damage != 'nested_hash':
        value['account_report_sha256'] = digest(value['account_report'])
    value['result_hash'] = digest({k: v for k, v in value.items() if k != 'result_hash'})
    kwargs = dict(source_commit='c'*40, feedback_sha256=value['result_hash'],
                  now=original['available_at_ms'])
    if damage:
        with pytest.raises(ValueError):
            validate_bar_export(value, **kwargs)
    else:
        assert validate_bar_export(value, **kwargs) == original
        assert value['account_report']['stream_gaps']


def test_reported_feedback_publish_consume_once_and_import(tmp_path, monkeypatch):
    from tools import demo_feedback_publish as publisher, gold_bar_feedback_research as research
    from tools.gold_initial_proposal import freeze_proposal
    value = reported(tmp_path)
    publisher.validate_bar_export(value, source_commit='c'*40,
        feedback_sha256=value['result_hash'], now=value['available_at_ms'])
    root = tmp_path/'research'
    publisher.publish(root, value, bar=True)
    monkeypatch.setattr(research, 'runtime_state_root', lambda: root)
    monkeypatch.setattr(research, 'source_identity', lambda: {'commit': 'c'*40})
    monkeypatch.setattr(research.time, 'time_ns', lambda: value['available_at_ms']*1000000)
    calls = []

    def model(objective, remaining, history, **kwargs):
        calls.append(objective)
        assert 'REPORTED_FLAT_ROUNDTRIP_WITH_RETAINED_GAPS' in objective
        assert value['account_report_sha256'] in objective
        assert value['account_report']['canonical_wallet'] not in objective
        return {'candidate_id': 2, 'model': 'fixture'}

    monkeypatch.setattr(research, 'propose', model)
    result = research.run(value['result_hash'])
    assert research.run(value['result_hash']) == result and len(calls) == 1
    proposal, _ = freeze_proposal(result['proposal_directory'], tmp_path/'imported')
    assert proposal['result_hash'] == result['proposal_hash']
    saved = root/'data/cache/ai_factory/bar-feedback-research'/value['result_hash']/'feedback.json'
    assert json.loads(saved.read_text()) == value


def test_reported_publication_preserves_zero_fill_orders(tmp_path):
    from tests.research.test_gold_unfilled_reduction import with_unfilled_reduction
    from tools.demo_feedback_publish import validate_bar_export
    value = reported(tmp_path)
    extra = tmp_path/'unfilled-fixture'
    extra.mkdir()
    report, ledger = reconcile(**with_unfilled_reduction(extra))
    assert ledger is not None
    value['account_report'] = report
    value['account_report_sha256'] = digest(report)
    value['reservations'].append('3'*64)
    value['result_hash'] = digest({k: v for k, v in value.items() if k != 'result_hash'})
    result = validate_bar_export(value, source_commit='c'*40,
        feedback_sha256=value['result_hash'], now=value['available_at_ms'])
    assert result['account_report']['unfilled_system_order_ids'] == ['23']
    assert result['account_report']['fill_count'] == 2


@pytest.mark.parametrize('damage', [None, 'valuation', 'reset', 'policy'])
def test_episode_publication_keeps_unknown_daily_valuation(tmp_path, damage):
    from services.research.demo_account_recovery import reconcile_episode
    from tools.demo_feedback_publish import validate_bar_export
    (tmp_path / 'base').mkdir()
    (tmp_path / 'episode').mkdir()
    value = reported(tmp_path / 'base')
    report, ledger = reconcile_episode(**short_inputs(tmp_path / 'episode'))
    assert ledger is not None
    if damage == 'valuation':
        report['daily_valuation_status'] = 'VERIFIED'
    elif damage == 'reset':
        report['risk_budget_reset'] = True
    elif damage == 'policy':
        report['episode_policy'] = 'RESET_DAILY'
    value['account_report'] = report
    value['account_report_sha256'] = digest(report)
    value['result_hash'] = digest({k: v for k, v in value.items() if k != 'result_hash'})
    def check():
        return validate_bar_export(value, source_commit='c'*40,
            feedback_sha256=value['result_hash'], now=value['available_at_ms'])
    if damage:
        with pytest.raises(ValueError):
            check()
    else:
        assert check() == value
