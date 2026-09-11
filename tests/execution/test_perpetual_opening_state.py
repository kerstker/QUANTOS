"""Inherited positions are opening balances, never fabricated strategy fills."""
from dataclasses import replace
from decimal import Decimal as D
from fractions import Fraction

import pytest

from services.execution.perpetual_ledger_v2 import (
    LinearPerpetualLedger, OpeningLedgerState, OpeningPosition,
    MarginMode, MarkEvent, FundingEvent, TradeEvent, EventOrderViolation,
    InsufficientMargin, QuantityRuleViolation, UnsupportedContract,
)
from tests.execution.test_perpetual_ledger_v2 import _spec, _capacity


def opening(*, quantity='2', basis='200', mark='110', isolated='0'):
    return OpeningLedgerState(timestamp=100, source_sha256='a'*64, positions=(OpeningPosition(
        instrument_id='BTCUSDT', quantity=D(quantity), entry_notional=D(basis), mark_price=D(mark),
        margin_mode=MarginMode.ISOLATED if isolated != '0' else MarginMode.CROSS,
        isolated_wallet=D(isolated)),))


@pytest.mark.parametrize('quantity,unrealized', [('2','20'), ('-2','-20')])
def test_opening_equity_includes_inherited_pnl_without_recording_new_profit(quantity, unrealized):
    ledger = LinearPerpetualLedger([_spec()], initial_collateral=D('1000'), opening_state=opening(quantity=quantity))
    state = ledger.state_as_canonical_dict()
    assert ledger.event_records == () and ledger.liquidations == ()
    assert state['capacity_consumed'] == {} and state['wallet_balance'] == '1000'
    assert state['opening_state']['source_sha256'] == 'a'*64
    r = ledger.reconciliation_snapshot()
    assert r.initial_equity == D('1000')+D(unrealized)
    assert r.ending_equity == r.initial_equity and r.price_pnl == 0 and r.residual == 0


@pytest.mark.parametrize('quantity', ['2','-2'])
def test_future_mark_and_partial_close_have_exact_incremental_cash_identity(quantity):
    ledger = LinearPerpetualLedger([_spec()], initial_collateral=D('1000'), opening_state=opening(quantity=quantity))
    ledger.apply_event(MarkEvent(timestamp=101, instrument_id='BTCUSDT', mark_price=D('120')))
    delta = D('-1') if D(quantity)>0 else D('1')
    ledger.apply_event(TradeEvent(timestamp=102, instrument_id='BTCUSDT', quantity_delta=delta,
        execution_price=D('120'), fee=D('0.12'), margin_mode=MarginMode.CROSS, capacity=_capacity()))
    r = ledger.reconciliation_snapshot()
    assert r.price_pnl == (D('20') if D(quantity)>0 else D('-20'))
    assert r.fee == D('0.12') and r.residual == 0
    assert Fraction(r.ending_equity)-Fraction(r.initial_equity) == Fraction(r.price_pnl)-Fraction(r.fee)
    assert ledger.position('BTCUSDT').entry_notional == D('100')


def test_isolated_wallet_is_allocated_from_total_opening_collateral_once():
    ledger = LinearPerpetualLedger([_spec()], initial_collateral=D('1000'), opening_state=opening(isolated='50'))
    assert ledger.state_as_canonical_dict()['wallet_balance'] == '950'
    assert ledger.account_equity() == D('1020')
    assert ledger.reconciliation_snapshot().residual == 0
    ledger.apply_event(TradeEvent(timestamp=101, instrument_id='BTCUSDT', quantity_delta=D('-2'),
        execution_price=D('110'), fee=D('0'), capacity=_capacity(), margin_mode=MarginMode.ISOLATED))
    assert ledger.account_equity() == D('1020')
    assert ledger.reconciliation_snapshot().price_pnl == 0
    assert ledger.reconciliation_snapshot().residual == 0


@pytest.mark.parametrize('batch', [True, False])
@pytest.mark.parametrize('timestamp', [99, 100])
def test_opening_cutoff_rejects_prior_or_same_time_funding_without_state_mutation(batch, timestamp):
    ledger = LinearPerpetualLedger([_spec()], initial_collateral=D('1000'), opening_state=opening())
    before = ledger.state_as_canonical_dict()
    event = FundingEvent(timestamp=timestamp, instrument_id='BTCUSDT', funding_rate=D('0.01'), mark_price=D('110'))
    with pytest.raises(EventOrderViolation):
        ledger.process_events([event]) if batch else ledger.apply_event(event)
    assert ledger.state_as_canonical_dict() == before and ledger.event_records == ()


def test_seeded_under_initial_margin_position_can_reduce_but_cannot_add_risk():
    seed = opening(quantity='10', basis='1000', mark='95')
    ledger = LinearPerpetualLedger([_spec()], initial_collateral=D('110'), opening_state=seed)
    before = ledger.state_as_canonical_dict()
    with pytest.raises(InsufficientMargin):
        ledger.apply_event(TradeEvent(timestamp=101, instrument_id='BTCUSDT', quantity_delta=D('1'),
            execution_price=D('95'), fee=D('0'), margin_mode=MarginMode.CROSS, capacity=_capacity()))
    assert ledger.state_as_canonical_dict() == before
    ledger.apply_event(TradeEvent(timestamp=102, instrument_id='BTCUSDT', quantity_delta=D('-1'),
        execution_price=D('95'), fee=D('0'), margin_mode=MarginMode.CROSS, capacity=_capacity()))
    assert ledger.position('BTCUSDT').quantity == D('9')
    assert ledger.reconciliation_snapshot().residual == 0


@pytest.mark.parametrize('patch', [dict(quantity=D('0')), dict(entry_notional=D('-1')),
    dict(mark_price=D('NaN')), dict(isolated_wallet=D('1')), dict(quantity=0.1)])
def test_invalid_opening_position_is_rejected(patch):
    with pytest.raises((ValueError, TypeError)):
        replace(opening().positions[0], **patch)


def test_duplicate_positions_and_mismatched_contracts_do_not_seed_a_partial_account():
    seed=opening()
    with pytest.raises(ValueError): replace(seed, positions=seed.positions*2)
    with pytest.raises(UnsupportedContract):
        LinearPerpetualLedger([_spec('ETHUSDT')], initial_collateral=D('1000'), opening_state=seed)
    with pytest.raises(QuantityRuleViolation):
        LinearPerpetualLedger([_spec()], initial_collateral=D('1000'),
            opening_state=replace(seed,positions=(replace(seed.positions[0], quantity=D('0.0001')),)))


def test_flat_legacy_state_shape_is_preserved_and_opening_identity_changes_only_opening_path():
    flat=LinearPerpetualLedger([_spec()], initial_collateral=D('1000')).state_as_canonical_dict()
    assert 'opening_state' not in flat
    first=LinearPerpetualLedger([_spec()], initial_collateral=D('1000'),opening_state=opening()).state_as_canonical_dict()
    second=LinearPerpetualLedger([_spec()],initial_collateral=D('1000'),
        opening_state=replace(opening(),source_sha256='b'*64)).state_as_canonical_dict()
    assert first != second and first['cashflow_identity'] == second['cashflow_identity']


def test_mixed_opening_allocations_future_funding_and_partial_close_share_one_cash_identity():
    first = replace(opening(isolated='50').positions[0], quantity=D('2'))
    second = OpeningPosition('ETHUSDT', D('-1'), D('100'), D('90'))
    seed = OpeningLedgerState(100, 'a'*64, (first, second))
    ledger = LinearPerpetualLedger([_spec(), _spec('ETHUSDT')], initial_collateral=D('1000'), opening_state=seed)
    assert ledger.account_equity() == D('1030')
    ledger.process_events([FundingEvent(101, 'BTCUSDT', D('0.01'), D('110')),
                           FundingEvent(101, 'ETHUSDT', D('0.01'), D('90'))])
    r = ledger.reconciliation_snapshot()
    assert r.funding == D('-1.3') and r.price_pnl == 0 and r.residual == 0
    ledger.apply_event(TradeEvent(timestamp=102, instrument_id='BTCUSDT', quantity_delta=D('-1'),
        execution_price=D('115'), margin_mode=MarginMode.ISOLATED, capacity=_capacity(), fee=D('0.1')))
    r = ledger.reconciliation_snapshot()
    assert Fraction(r.ending_equity) - Fraction(r.initial_equity) == Fraction(r.price_pnl) + Fraction(r.funding) - Fraction(r.fee)
    assert r.residual == 0


def test_opening_does_not_depend_on_ambient_decimal_context_or_allow_rebase():
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec = 3
        ledger = LinearPerpetualLedger([_spec()], initial_collateral=D('1000'), opening_state=opening())
        r = ledger.reconciliation_snapshot()
    assert r.initial_equity == D('1020') and r.residual == 0
    before = ledger.state_as_canonical_dict()
    with pytest.raises(ValueError): ledger._initialize_opening_state(opening())
    assert ledger.state_as_canonical_dict() == before


def test_opening_rejects_overallocated_wallet_and_nonrepresentable_cost_basis():
    from services.execution.perpetual_ledger_v2 import MarginConfigurationError, CashQuantumViolation
    with pytest.raises(MarginConfigurationError):
        LinearPerpetualLedger([_spec()], initial_collateral=D('49'), opening_state=opening(isolated='50'))
    with pytest.raises(CashQuantumViolation):
        LinearPerpetualLedger([_spec()], initial_collateral=D('1000'), opening_state=opening(basis='200.000000000000000000000000000000000000000000000000001'))
