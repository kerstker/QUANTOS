from copy import deepcopy

import pytest

from tools.demo_operator_setup import digest
from services.execution.backtest_authority import exact_identity_hash


def document():
    definition = dict(schema='GOLD_BAR_RESEARCH_DEFINITION_V1', candidate='channel', symbol='XAUUSDT',
        authoritative=False, economic_eligible=False, submission_enabled=False, selected_for_execution=False)
    definition['definition_hash'] = exact_identity_hash(definition, name='gold_bar_research_definition')
    cash = dict(schema='DEMO_EXECUTION_RESEARCH_FEEDBACK_V1', environment='BINANCE_USDM_DEMO',
        scope='TECHNICAL_REVIEW_ONLY', strategy_id='signed', symbol='XAUUSDT', account_ref_sha256='a'*64,
        dispatch_evidence_sha256='b'*64, cash_capture_sha256='c'*64, cash_report_sha256='d'*64,
        available_at_ms=2000, source_commit='c'*40, orders=[dict(filled_quantity='0',terminal_status='EXPIRED')],
        positions={}, position_flat=True, cash_reconciliation={}, canonical_wallet='1000',wallet_residual='0',
        candidate_attribution='UNRESOLVED',research_trigger_enabled=False,strategy_pnl=None,
        source_verified=False,coverage_verified=False,economic_eligible=False,submission_enabled=False,
        **{k:'e'*64 for k in ('reservation_sha256','proposal_sha256','resolution_plan_sha256','manifest_hash','journal_tail')})
    cash['result_hash'] = digest(cash)
    value = dict(schema='GOLD_BAR_CASH_RESEARCH_FEEDBACK_V1', environment='BINANCE_USDM_DEMO',scope='TECHNICAL_REVIEW_ONLY',
        definition=definition,definition_hash=definition['definition_hash'],entry_decision_ms=1000,
        entry_client_order_id='QD'+'f'*32,bar_signal_sha256='f'*64,cash_feedback=cash,cash_feedback_sha256=cash['result_hash'],
        candidate_attribution='BAR_DEFINITION_BOUND_TO_SIGNED_CONSTRAINTS',source_verified=False,coverage_verified=False,
        economic_eligible=False,submission_enabled=False,research_trigger_enabled=False,strategy_pnl=None)
    return dict(value,result_hash=digest(value))


@pytest.mark.parametrize('damage', [None,'cash','definition','future','authority'])
def test_bar_publication_validates_nested_cash_and_definition(damage):
    from tools import demo_feedback_publish as publish
    value = document()
    if damage == 'cash':
        value['cash_feedback']['wallet_residual'] = '1'
    elif damage == 'definition':
        value['definition']['candidate'] = 'trend'
    elif damage == 'future':
        value['cash_feedback']['available_at_ms'] = 4000
        value['cash_feedback']['result_hash'] = digest({k:v for k,v in value['cash_feedback'].items() if k != 'result_hash'})
        value['cash_feedback_sha256'] = value['cash_feedback']['result_hash']
    elif damage == 'authority':
        value['economic_eligible'] = True
    value['result_hash'] = digest({k:v for k,v in value.items() if k != 'result_hash'})
    kwargs = dict(source_commit='c'*40,feedback_sha256=value['result_hash'],now=3000)
    if damage:
        with pytest.raises(ValueError):
            publish.validate_bar_export(value, **kwargs)
    else:
        assert publish.validate_bar_export(value, **kwargs) == value


def test_bar_publication_is_separate_immutable_namespace(tmp_path):
    from tools import demo_feedback_publish as publish
    value = document()
    root = tmp_path/'research'
    publish.publish(root, value, bar=True)
    publish.publish(root, value, bar=True)
    target = root/'data/cache/ai_factory/bar-execution-feedback'/(value['result_hash']+'.json')
    assert target.exists() and not (root/'data/cache/ai_factory/execution-feedback').exists()
    changed = deepcopy(value)
    changed['entry_decision_ms'] += 1
    with pytest.raises(ValueError, match='DESTINATION_CONFLICT'):
        publish.publish(root, changed, bar=True)


def test_bar_run_drops_identity_before_writing_research(tmp_path, monkeypatch):
    from tools import demo_feedback_publish as publish
    value = document()
    events = []
    monkeypatch.setattr(publish, 'verify_source', lambda _: None)
    monkeypatch.setattr(publish.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(publish, 'configuration', lambda _: dict(executor_uid=1001,
        research_uid=1002,research_gid=1002,research_state_root=str(tmp_path)))
    def read(**kwargs):
        assert kwargs['bar'] is True and kwargs['strategy'] is False
        events.append('read')
        return value
    monkeypatch.setattr(publish, 'read_export', read)
    monkeypatch.setattr(publish.time, 'time_ns', lambda: 3000*1000000)
    monkeypatch.setattr(publish.custody, 'drop_identity', lambda *a: events.append('drop'))
    def write(*args, **kwargs):
        assert events == ['read','drop'] and kwargs == {'bar':True}
        events.append('write')
    monkeypatch.setattr(publish, 'publish', write)
    result = publish.run(source_commit='c'*40,cycle='a'*64,artifact_sha256=digest(value),
        feedback_sha256=value['result_hash'],bar=True)
    assert events == ['read','drop','write'] and result['status'] == 'RESEARCH_FEEDBACK_DELIVERED'
