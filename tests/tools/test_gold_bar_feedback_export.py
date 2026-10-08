from copy import deepcopy
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest


@pytest.mark.parametrize('reject_gap', [False, True])
def test_export_uses_each_original_receipt_window_without_filling_gap(tmp_path, monkeypatch, reject_gap, reported=False, no_fill=False):
    from tools import gold_bar_feedback as feedback, demo_reported_recovery
    from live.execution import demo_pre_submit, demo_position_context
    from services.research import demo_cash_window, demo_execution_journal
    definition = {'definition_hash':'d'*64}
    signal = {'decision_id':'signal','entry_side':'BUY'}
    client = 'QD'+_digest(dict(account_ref_sha256='a'*64, definition_hash='d'*64,
        decision_id='signal', action='OPEN_BUY'))[:32]
    uid = tmp_path.stat().st_uid
    cash_area = tmp_path/'strategy-cash'/('b'*64)
    cash_area.mkdir(parents=True)
    documents = {}
    reservations = []
    sources = []
    journals = {}
    windows = [(100,200)] if no_fill else [(100,200), (14400100,14400200)]
    for index, (start, end) in enumerate(windows):
        rid, mid = str(index+1)*64, str(index+3)*64
        orders = [dict(newClientOrderId=client if index == 0 else 'exit', reduceOnly=bool(index))]
        context = {'account_ref_sha256':'a'*64}
        reservations.append(dict(record_hash=rid, payload=dict(orders=orders, context=context)))
        journal = tmp_path/'journals'/(mid+'.sqlite3')
        journal.parent.mkdir(exist_ok=True)
        journal.write_bytes(b'synthetic')
        journals[journal] = (mid, dict(native={'coverage':dict(start_ms=start,end_ms=end)}))
        proof = dict(orders=orders, context=context)
        proof_hash = _digest(proof)
        plan = dict(
            proposal_sha256=proof_hash, reservation_sha256=rid, status='READY_FOR_OMS_PROJECTION',
            journal_path=str(journal), manifest_hash=mid, journal_tail='f'*64)
        plan_hash = _digest(plan)
        sources.append(dict(reservation_sha256=rid, plan_sha256=plan_hash, manifest_hash=mid))
        documents[tmp_path/'evidence'/(proof_hash+'.json')] = proof
        documents[tmp_path/'receipt-reconciliation'/'plans'/(plan_hash+'.json')] = plan
    for p in tmp_path.rglob('*'):
        p.chmod(0o700 if p.is_dir() else 0o600)
    state = {'reservations':reservations}
    monkeypatch.setattr(demo_pre_submit, '_state', lambda *a: deepcopy(state))
    monkeypatch.setattr(demo_position_context, 'load_context', lambda *a, **kw: dict(quantity='0',sources=sources))
    monkeypatch.setattr(demo_reported_recovery, '_read', lambda path, *a: deepcopy(documents[path]))
    monkeypatch.setattr(demo_cash_window, 'load_capture', lambda _: {'account_ref_sha256':'a'*64})
    monkeypatch.setattr(demo_cash_window, 'assess', lambda _: {'status':'MATCHED_REPORTED_WINDOW'})
    class Journal:
        def __init__(self, path, **kwargs):
            assert kwargs == {'read_only':True}
            self.manifest_hash, self.manifest = journals[path]
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def read(self):
            return [], 'f'*64, None
    monkeypatch.setattr(demo_execution_journal, 'DemoExecutionJournal', Journal)
    def build(**kwargs):
        assert [(b['start_ms'], b['end_ms']) for b in kwargs['batches']] == windows
        if reject_gap:
            raise ValueError('FEEDBACK_CASH_EVIDENCE_UNRESOLVED')
        return {'result_hash':'e'*64, 'synthetic':True}
    monkeypatch.setattr(feedback, 'build', build)
    engine = SimpleNamespace(verified_definition=lambda: definition, entry_signal=lambda _:signal)
    stores = dict(dispatch=object(), recovery=object(), oms=object(), stream=SimpleNamespace(verify=lambda: 'stable'))
    kwargs = dict(engine=engine, entry_decision_ms=100, bundle_sha256='b'*64,
        account='a'*64, source_commit='c'*40, stores=stores, as_of_ms=14401000)
    assert uid == tmp_path.stat().st_uid
    if reported:
        from tools import gold_bar_report_capture, gold_bar_reported_feedback
        calls = []
        def collect(root, **arguments):
            assert not no_fill
            calls.append('capture')
            assert len(arguments['prepared']) == 2
            assert arguments['order_windows'] == [{'start_ms': a, 'end_ms': b} for a, b in windows]
            return {'retained': True}
        def reported_build(**arguments):
            calls.append('build')
            assert arguments['inputs'] == {'retained': True}
            assert len(arguments['prepared']) == 2
            assert arguments['as_of_ms'] >= kwargs['as_of_ms']
            if reject_gap:
                raise ValueError('FEEDBACK_CASH_EVIDENCE_UNRESOLVED')
            return {'result_hash': 'e'*64, 'synthetic': True}
        from tools import gold_bar_cash
        monkeypatch.setattr(gold_bar_cash, 'close_window', lambda *a, **kw: calls.append('close'))
        monkeypatch.setattr(gold_bar_report_capture, 'capture_once', collect)
        monkeypatch.setattr(gold_bar_reported_feedback, 'build', reported_build)
        def operation():
            return feedback.export_completed(tmp_path, bundle={'fixture': True}, **kwargs)
    else:
        def operation():
            return feedback.export_retained(tmp_path, **kwargs)
    if reject_gap:
        with pytest.raises(ValueError, match='EVIDENCE_UNRESOLVED'):
            operation()
        assert not list((tmp_path/'strategy-bar-feedback'/('b'*64)/'research-feedback').iterdir())
    else:
        result = operation()
        assert result['status'] == 'BAR_STRATEGY_FEEDBACK_RETAINED' and result['cash_reconciled'] is True
        assert result['submission_enabled'] is False
        if reported:
            assert calls == (['close'] if no_fill else ['capture', 'build'])


@pytest.mark.parametrize('reject', [False, True])
def test_completed_episode_routes_original_batches_to_reported_account(tmp_path, monkeypatch, reject):
    test_export_uses_each_original_receipt_window_without_filling_gap(
        tmp_path, monkeypatch, reject, reported=True)


def test_unfilled_opening_keeps_original_cash_path(tmp_path, monkeypatch):
    test_export_uses_each_original_receipt_window_without_filling_gap(
        tmp_path, monkeypatch, False, reported=True, no_fill=True)
