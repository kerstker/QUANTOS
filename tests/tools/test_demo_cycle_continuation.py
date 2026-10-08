"""A restart recovers the original attempt, never another opening order."""
from copy import deepcopy

import pytest

from tools import demo_functional_cycle as cycle


def test_delayed_receipt_recovers_without_resubmitting_and_completes():
    calls = []
    positions = iter([{'status': 'UNRESOLVED'},
        {'status': 'SETTLED', 'quantity': '0.002', 'fill_count': 1},
        {'status': 'SETTLED', 'quantity': '0', 'fill_count': 2}])
    result = cycle.drive(opening_task={'open': True}, execute=lambda task: calls.append(task),
        settle=lambda: next(positions), prepare_reduce=lambda p, i: {'close': i},
        cash_finish=lambda: {'status': 'MATCHED_REPORTED_INPUTS', 'final_quantity': '0', 'fill_count': 2},
        progress=lambda _: None, recovery_checks=3)
    assert result['status'] == 'DEMO_CYCLE_COMPLETED'
    assert calls == [{'open': True}, {'close': 1}]


def test_restart_after_attempt_claim_queries_instead_of_reopening():
    retained = []
    def checkpoint(value): retained.append(deepcopy(value))
    def crash(_): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        cycle.drive(opening_task={'open': True}, execute=crash, settle=lambda: pytest.fail('settle'),
            prepare_reduce=lambda *a: pytest.fail('reduce'), cash_finish=lambda: {'status': 'UNRESOLVED'},
            progress=lambda _: None, checkpoint=checkpoint)
    assert len(retained[-1]['attempts']) == 1
    sent = []
    result = cycle.drive(opening_task={'open': True}, execute=sent.append,
        settle=iter([{'status': 'SETTLED', 'quantity': '0.002', 'fill_count': 1},
                     {'status': 'SETTLED', 'quantity': '0', 'fill_count': 2}]).__next__,
        prepare_reduce=lambda p, i: {'close': i}, checkpoint=checkpoint, resume=retained[-1],
        cash_finish=lambda: {'status': 'MATCHED_REPORTED_INPUTS', 'final_quantity': '0', 'fill_count': 2},
        progress=lambda _: None)
    assert sent == [{'close': 1}] and result['status'] == 'DEMO_CYCLE_COMPLETED'
    assert result['orders_attempted'] == 2


def test_recovery_only_after_expiry_never_prepares_or_sends_reduction():
    state = {'schema': 'DEMO_CYCLE_CONTINUATION_V1', 'attempts': [{'open': True}]}
    result = cycle.drive(opening_task={'open': True}, execute=lambda _: pytest.fail('send'),
        settle=lambda: {'status': 'SETTLED', 'quantity': '0.002', 'fill_count': 1},
        prepare_reduce=lambda *a: pytest.fail('reduce'), resume=state, allow_new_tasks=False,
        cash_finish=lambda: {'status': 'UNRESOLVED'}, progress=lambda _: None)
    assert result['status'] == 'REDUCTION_AUTHORIZATION_REQUIRED'
    assert result['final_quantity'] == '0.002' and result['orders_attempted'] == 1


def test_resume_after_closing_claim_can_finish_cash_without_any_send():
    state = {'schema': 'DEMO_CYCLE_CONTINUATION_V1', 'attempts': [{'open': True}, {'close': 1}]}
    result = cycle.drive(opening_task={'open': True}, execute=lambda _: pytest.fail('send'),
        settle=lambda: {'status': 'SETTLED', 'quantity': '0', 'fill_count': 2},
        prepare_reduce=lambda *a: pytest.fail('reduce'), resume=state, allow_new_tasks=False,
        cash_finish=lambda: {'status': 'MATCHED_REPORTED_INPUTS', 'final_quantity': '0', 'fill_count': 2},
        progress=lambda _: None)
    assert result['status'] == 'DEMO_CYCLE_COMPLETED'


@pytest.mark.parametrize('state', [
    {'schema': 'DEMO_CYCLE_CONTINUATION_V1', 'attempts': [{'wrong_open': True}]},
    {'schema': 'DEMO_CYCLE_CONTINUATION_V1', 'attempts': [{'open': True}] * 4},
    {'schema': 'DEMO_CYCLE_CONTINUATION_V1', 'attempts': [{'open': True}, {'open': True}]},
])
def test_invalid_resume_is_rejected_before_any_effect(state):
    with pytest.raises(ValueError):
        cycle.drive(opening_task={'open': True}, execute=lambda _: pytest.fail('send'),
            settle=lambda: pytest.fail('query'), prepare_reduce=lambda *a: pytest.fail('reduce'),
            cash_finish=lambda: pytest.fail('cash'), progress=lambda _: None, resume=state)


def test_checkpoint_failure_stops_before_send_and_preserves_recovery_status():
    result = cycle.drive(opening_task={'open': True}, execute=lambda _: pytest.fail('send'),
        settle=lambda: pytest.fail('query'), prepare_reduce=lambda *a: pytest.fail('reduce'),
        cash_finish=lambda: {'status': 'UNRESOLVED'}, progress=lambda _: None,
        checkpoint=lambda _: (_ for _ in ()).throw(OSError('disk full')))
    assert result['status'] == 'RECOVERY_REQUIRED'
    assert result['orders_attempted'] == 0


def test_known_stream_gap_exits_retries_and_reports_evidence_repair_needed():
    calls=[]
    def settle():
        calls.append('query')
        return {'status':'EVIDENCE_REPAIR_REQUIRED','quantity':None,'fill_count':None}
    result=cycle.drive(opening_task={'open':True},execute=lambda _:None,settle=settle,
        prepare_reduce=lambda *a:pytest.fail('reduce across missing evidence'),recovery_checks=3,
        cash_finish=lambda:{'status':'UNRESOLVED'},progress=lambda _:None)
    assert result['status']=='EVIDENCE_REPAIR_REQUIRED' and calls==['query']
