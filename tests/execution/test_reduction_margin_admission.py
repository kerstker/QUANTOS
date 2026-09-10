"""Initial margin admits new risk; a partial exit must remain possible below it."""
from dataclasses import replace
from decimal import Decimal as D
import pytest

from services.execution.perpetual_ledger_v2 import (
    LinearPerpetualLedger, MarginMode, MarkEvent, InsufficientMargin, MissingLiquidationBackstop,
)
from services.execution.perpetual_simulation import OrderIntent, SimulationLimits, SimulationCostModel
from tests.execution.test_finite_account_margin import finite_rule
from tests.execution.test_perpetual_ledger_v2 import _trade
from tests.execution.test_perpetual_order_admission import T, frame, execute


@pytest.mark.parametrize('mode',[MarginMode.CROSS,MarginMode.ISOLATED])
@pytest.mark.parametrize('finite',[False,True])
def test_actual_partial_reduction_keeps_losses_and_fees_without_demanding_new_initial_margin(mode, finite):
    spec=finite_rule().spec
    if not finite:
        spec=replace(spec,margin_brackets=(replace(spec.margin_brackets[0],notional_cap=None),),
                     new_risk_notional_cap=None,margin_overflow_policy='NONE')
    ledger=LinearPerpetualLedger((spec,),initial_collateral=D('30'))
    ledger.apply_event(MarkEvent(0,'BTCUSDT',D('100')))
    ledger.apply_event(_trade(1,'-1','100',margin_mode=mode,
                            isolated_margin='30' if mode==MarginMode.ISOLATED else None))
    ledger.apply_event(MarkEvent(2,'BTCUSDT',D('121')))
    result=ledger.apply_event(_trade(3,'0.1','121',fee='0.0121',margin_mode=mode))
    assert result.status=='FILLED' and ledger.position('BTCUSDT').quantity==D('-0.9')
    assert ledger.account_equity()==D('8.9879')
    assert ledger.reconciliation_snapshot().residual==0
    snapshot=ledger.initial_margin_snapshot()
    margin=snapshot['cross'] if mode==MarginMode.CROSS else snapshot['isolated']['BTCUSDT']
    assert margin['available'] < 0


def test_reduce_only_partial_exit_is_executable_after_price_crosses_account_cap():
    def strategy(ctx):
        if ctx.timestamp==T:return (OrderIntent('entry','BTCUSDT',D('-1')),)
        if ctx.timestamp==T+20:return (OrderIntent('reduce','BTCUSDT',D('.1'),reduce_only=True),)
        return ()
    result=execute((frame(0),frame(10),frame(20,'121'),frame(30,'121')),strategy,
        initial_collateral=D('30'),contract_rules=(finite_rule(),),
        costs=SimulationCostModel(fee_rate=D('.001')),
        limits=SimulationLimits(max_single_weight=D('4'),max_gross_leverage=D('4')))
    assert [o['status'] for o in result['orders']]==['FILLED','FILLED']
    assert result['ending_state']['positions']['BTCUSDT']['quantity']=='-0.9'
    assert result['reconciliation']['ending_equity']=='8.8879'
    assert result['reconciliation']['residual']=='0'


@pytest.mark.parametrize('quantity,fee',[('0.1','8'),('1','10'),('-0.1','0'),('2','0')])
def test_exit_exception_cannot_create_insolvency_maintenance_failure_or_new_exposure(quantity,fee):
    spec=finite_rule().spec
    # Initial/maintenance semantics, independently of the separate account cap.
    spec=replace(spec,margin_brackets=(replace(spec.margin_brackets[0],notional_cap=None),),
                 new_risk_notional_cap=None,margin_overflow_policy='NONE')
    ledger=LinearPerpetualLedger((spec,),initial_collateral=D('30'))
    ledger.apply_event(MarkEvent(0,'BTCUSDT',D('100')))
    ledger.apply_event(_trade(1,'-1','100'))
    ledger.apply_event(MarkEvent(2,'BTCUSDT',D('121')))
    before=ledger.state_as_canonical_dict()
    with pytest.raises(InsufficientMargin):
        ledger.apply_event(_trade(3,quantity,'121',fee=fee))
    assert ledger.state_as_canonical_dict()==before


def test_isolated_exit_cannot_implicitly_borrow_free_cross_cash_to_pay_a_deficit():
    ledger=LinearPerpetualLedger((finite_rule().spec,),initial_collateral=D('100'))
    ledger.apply_event(MarkEvent(0,'BTCUSDT',D('100')))
    ledger.apply_event(_trade(1,'-1','100',margin_mode=MarginMode.ISOLATED,isolated_margin='30'))
    ledger.apply_event(MarkEvent(2,'BTCUSDT',D('121')))
    before=ledger.state_as_canonical_dict()
    with pytest.raises(MissingLiquidationBackstop):
        ledger.apply_event(_trade(3,'1','121',fee='10',margin_mode=MarginMode.ISOLATED))
    assert ledger.state_as_canonical_dict()==before
