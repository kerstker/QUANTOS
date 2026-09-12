"""Observed cash uses the canonical ledger without inventing profit or fees."""
from dataclasses import replace
from decimal import Decimal as D,localcontext

import pytest

from services.execution.perpetual_ledger_v2 import (
    LinearPerpetualLedger,ReportedCashReplayPolicy,ReportedCashFlowEvent,ReportedCashReplayError,
    ReportedFillReplayEvent,FundingEvent,MarkEvent,MarginMode,CashQuantumViolation,LiquidationBackstop,
)
from tests.execution.test_perpetual_ledger_v2 import _spec,_capacity,_trade

ACCOUNT='a'*64


def ledger(**kwargs):
    return LinearPerpetualLedger([replace(_spec(),settlement_quantum=D('0.00000001'))],initial_collateral=D('1000'),
        reported_cash_policy=ReportedCashReplayPolicy(account_ref_sha256=ACCOUNT,asset='USDT'),**kwargs)


def flow(at,amount, *,kind='TRANSFER',identity='1',**changes):
    return ReportedCashFlowEvent(timestamp=at,account_ref_sha256=ACCOUNT,asset='USDT',income_type=kind,
        transaction_id=identity,amount=D(amount),instrument_id='BTCUSDT' if kind=='FUNDING_FEE' else None,
        source_sha256='b'*64,**changes)


def fill(at=1,quantity='1',price='100',fee='.10'):
    return ReportedFillReplayEvent(timestamp=at,instrument_id='BTCUSDT',quantity_delta=D(quantity),execution_price=D(price),
        margin_mode=MarginMode.CROSS,capacity=_capacity(),fee=D(fee),order_id='observed-order',receipt_sha256='c'*64)


def test_fills_funding_and_external_cash_have_one_identity_without_duplicate_commission():
    book=ledger();book.apply_event(fill());book.apply_event(flow(2,'-0.02',kind='FUNDING_FEE'))
    book.apply_event(flow(3,'200',identity='2'));book.apply_event(fill(4,'-1','110','.11'))
    snapshot=book.reconciliation_snapshot().as_canonical_dict()
    assert snapshot['ending_equity']=='1209.77' and snapshot['fee']=='0.21'
    assert snapshot['funding']=='-0.02' and snapshot['external_cash_flow']=='200'
    assert snapshot['trading_and_funding_change']=='9.77' and snapshot['attributed_change']=='209.77'
    assert snapshot['residual']=='0'
    assert book.state_as_canonical_dict()['reported_cash_policy']['source_verified'] is False


def test_old_mode_and_old_canonical_shape_do_not_silently_accept_new_cash_semantics():
    book=LinearPerpetualLedger([_spec()],initial_collateral=D('1000'))
    before=book.state_as_canonical_dict()
    assert 'reported_cash_policy' not in before and 'external_cash_flow' not in before['cashflow_identity']
    with pytest.raises(ReportedCashReplayError):book.apply_event(flow(1,'200'))
    assert book.state_as_canonical_dict()==before


def test_repeated_observation_after_later_events_is_idempotent_and_conflicts_roll_back():
    book=ledger();event=flow(1,'200');book.apply_event(event);book.apply_event(MarkEvent(2,'BTCUSDT',D('100')))
    before=book.state_as_canonical_dict();events=book.event_records
    book.apply_event(replace(event,source_sha256='d'*64))
    assert book.state_as_canonical_dict()==before and book.event_records==events
    with pytest.raises(ReportedCashReplayError):book.apply_event(replace(event,amount=D('201')))
    assert book.state_as_canonical_dict()==before and book.event_records==events


@pytest.mark.parametrize('change',[{'account_ref_sha256':'d'*64},{'asset':'USDC'},
    {'income_type':'COMMISSION'},{'income_type':'REALIZED_PNL'},{'instrument_id':'BTCUSDT'}])
def test_foreign_scope_or_already_accounted_cash_categories_cannot_enter(change):
    book=ledger();before=book.state_as_canonical_dict()
    with pytest.raises((ValueError,ReportedCashReplayError)):
        book.apply_event(replace(flow(1,'1'),**change))
    assert book.state_as_canonical_dict()==before


def test_cash_replay_mode_cannot_mix_simulated_funding_or_trades_into_observed_results():
    for event in (_trade(1,'1','100'),FundingEvent(1,'BTCUSDT',D('.001'),D('100'))):
        book=ledger();before=book.state_as_canonical_dict()
        with pytest.raises(ReportedCashReplayError):book.apply_event(event)
        with pytest.raises(ReportedCashReplayError):book.process_events([event])
        assert book.state_as_canonical_dict()==before


def test_same_time_cash_group_is_atomic_and_quantum_failures_do_not_leave_partial_cash():
    book=ledger();book.apply_event(fill())
    book.process_events([flow(2,'-1100',identity='1'),flow(2,'1100',identity='2')])
    assert book.reconciliation_snapshot().external_cash_flow==0
    before=book.state_as_canonical_dict()
    with pytest.raises(CashQuantumViolation):book.process_events([flow(3,'1',identity='3'),flow(3,'.000000001',identity='4')])
    assert book.state_as_canonical_dict()==before


def test_unmodelled_debit_and_funding_without_bound_position_stay_unresolved():
    book=ledger()
    with pytest.raises(ReportedCashReplayError):book.apply_event(flow(1,'1',kind='FUNDING_FEE'))
    book.apply_event(fill());before=book.state_as_canonical_dict()
    with pytest.raises(ReportedCashReplayError):book.apply_event(flow(2,'-999',identity='2'))
    assert book.state_as_canonical_dict()==before


def test_ambient_decimal_settings_cannot_change_observed_cash_totals():
    first=ledger();first.process_events([flow(1,'0.00000001'),flow(2,'123.45678901',identity='2')])
    with localcontext() as context:
        context.prec=5
        second=ledger();second.process_events([flow(1,'0.00000001'),flow(2,'123.45678901',identity='2')])
    assert first.state_as_canonical_dict()==second.state_as_canonical_dict()


def test_declared_liquidation_backstop_cannot_manufacture_cash_in_observed_replay():
    book=ledger();book.apply_event(fill());before=book.state_as_canonical_dict()
    event=MarkEvent(2,'BTCUSDT',D('100'),liquidation_backstop=LiquidationBackstop(D('0'),D('0')))
    with pytest.raises(ReportedCashReplayError):book.apply_event(event)
    with pytest.raises(ReportedCashReplayError):book.process_events([event])
    assert book.state_as_canonical_dict()==before


def test_funding_and_fill_in_same_millisecond_require_an_explicit_ordering_source():
    book=ledger();book.apply_event(fill());before=book.state_as_canonical_dict()
    with pytest.raises(ReportedCashReplayError):book.apply_event(flow(1,'-.01',kind='FUNDING_FEE'))
    assert book.state_as_canonical_dict()==before


def test_event_iterator_is_not_consumed_by_preflight_before_replay():
    old=LinearPerpetualLedger([_spec()],initial_collateral=D('1000'))
    old.process_events(iter([_trade(1,'1','100')]))
    assert old.position('BTCUSDT').quantity==1
    observed=ledger();observed.process_events(iter([flow(1,'200')]))
    assert observed.reconciliation_snapshot().external_cash_flow==200
