"""Fractional Demo quantities, independent fills and existing risk controls."""
from copy import deepcopy
from dataclasses import replace
from decimal import localcontext

import pytest

from live.execution import demo_batch_risk as risk
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import StrategyApproval
from services.risk.pre_trade.kill_switch import KillSwitch
from tests.live.test_demo_submission_verification import material,NOW


def inputs():
    return dict(account={'account_ref_sha256':'a'*64,'quote_asset':'USDC','cash':'5000','equity':'5000',
        'day_pnl':'0','peak_equity':'5000','positions':[]},
        market={'symbol':'BTCUSDC','reference_price':'60000','observed_at_ms':NOW,'daily_volume_base':'10',
                'instrument_vol':'0.2','known':True,'market_open':True},
        native_limits=RiskLimits(allowed_instruments=frozenset({'BTCUSDC'}),allowed_accounts=frozenset({'a'*64}),
            default_max_position_qty=0.003,fat_finger_qty=0.003,max_order_notional=200,max_gross_exposure=200,
            max_net_exposure=200,daily_loss_limit=20),
        strategy=StrategyApproval(strategy_id='demo-engineering-fixture',approved=True,approval_id='external-fixture'),
        kill_switch=KillSwitch(),now_ms=NOW)


def test_native_fractional_limits_and_volume_use_the_same_gate_units():
    orders=material()[2];result=risk.screen(orders,**inputs())
    assert result['status']=='RISK_CHECKS_PASSED' and result['submission_enabled'] is False
    assert result['unit_scale']==1000000
    assert result['checks'][0]['quantity_units']==2000
    assert result['checks'][0]['limit_price_per_unit']=='0.06'
    assert result['checks'][0]['notional']=='120'


def test_native_position_cap_does_not_become_a_million_coin_limit():
    values=inputs();values['native_limits']=replace(values['native_limits'],default_max_position_qty=0.001)
    result=risk.screen(material()[2],**values)
    assert result['status']=='REJECTED'
    assert 'POSITION_LIMIT_EXCEEDED' in result['reasons']


def test_independent_fill_subsets_cannot_hide_behind_netting_a_batch():
    orders=material()[2]
    orders.append(dict(orders[0],newClientOrderId='QDtest000002',side='SELL'))
    orders.append(dict(orders[0],newClientOrderId='QDtest000003'))
    result=risk.screen(orders,**inputs())
    assert result['status']=='REJECTED' and result['subset_checks']
    assert any(row['side']=='BUY' and row['quantity_units']==4000 for row in result['subset_checks'])


def test_existing_positions_are_scaled_and_reduce_only_cannot_flip_them():
    values=inputs();values['account']['positions']=[{'symbol':'BTCUSDC','quantity':'0.001','mark_price':'60000'}]
    orders=material()[2];orders[0].update(side='SELL',reduceOnly=True)
    result=risk.screen(orders,**values)
    assert result['status']=='REJECTED' and 'DEMO_REDUCE_ONLY_EXCEEDS_POSITION' in result['reasons']
    orders[0]['quantity']='0.001'
    assert risk.screen(orders,**values)['status']=='RISK_CHECKS_PASSED'


def test_strategy_freshness_loss_and_liquidity_keep_original_gate_rejections():
    for change,reason in [('strategy','STRATEGY_NOT_APPROVED'),('freshness','MARKET_CLOSED'),
                          ('loss','DAILY_LOSS_LIMIT_EXCEEDED'),('liquidity','LIQUIDITY_LIMIT_EXCEEDED')]:
        values=inputs()
        if change=='strategy':values['strategy']=StrategyApproval(strategy_id='demo-engineering-fixture')
        elif change=='freshness':values['market']['observed_at_ms']=NOW-30001
        elif change=='loss':values['account']['day_pnl']='-20'
        else:values['market']['daily_volume_base']=None
        result=risk.screen(material()[2],**values)
        assert result['status']=='REJECTED' and reason in result['reasons']


def test_inexact_quantity_future_market_and_cross_quote_inputs_are_rejected():
    for change in ('quantity','future','quote'):
        orders=material()[2];values=inputs()
        if change=='quantity':orders[0]['quantity']='0.0000001'
        elif change=='future':values['market']['observed_at_ms']=NOW+1
        else:values['account']['positions']=[{'symbol':'BTCUSDT','quantity':'0.001','mark_price':'60000'}]
        assert risk.screen(orders,**values)['status']=='REJECTED'


def test_non_finite_limit_cannot_even_be_constructed():
    """2026-09-20 审计 P0-1:NaN 限额此前**能构造成功**,只靠 demo_batch_risk
    _scaled_limits 的外部兜底才在 screen 阶段被拒——限额本身是 fail-open 的,任何
    绕过那条兜底的调用方都会拿到一个静默失效的护栏(`notional > NaN` 恒为 False)。

    RiskLimits.__post_init__ 现在直接拒绝非有限值,失败位置更早、也更靠近源头:
    连一个带 NaN 限额的 gate 都构造不出来,而不是先构造出一个再指望下游发现。
    这个断言比原来的 'screen 返回 REJECTED' **更强**,不是放宽。
    """
    values=inputs()
    with pytest.raises(ValueError):
        replace(values['native_limits'],max_order_notional=float('nan'))


def test_result_identity_binds_limits_and_exact_order_bytes():
    orders=material()[2];values=inputs()
    original=risk.screen(orders,**values)
    changed=deepcopy(orders);changed[0]['quantity']='0.0020'
    assert risk.screen(changed,**values)['risk_sha256']!=original['risk_sha256']
    values['native_limits']=replace(values['native_limits'],max_order_notional=190)
    assert risk.screen(orders,**values)['risk_sha256']!=original['risk_sha256']


def test_decimal_context_and_combined_reduce_only_orders_cannot_relax_checks():
    orders=material()[2];values=inputs()
    orders[0]['price']='59999.9'
    original=risk.screen(orders,**values)
    with localcontext() as ctx:
        ctx.prec=4
        assert risk.screen(orders,**values)==original
    values['account']['positions']=[{'symbol':'BTCUSDC','quantity':'0.001','mark_price':'60000'}]
    orders[0].update(side='SELL',quantity='0.001',reduceOnly=True)
    orders.append(dict(orders[0],newClientOrderId='QDtest000002'))
    result=risk.screen(orders,**values)
    assert result['status']=='REJECTED' and 'DEMO_REDUCE_ONLY_EXCEEDS_POSITION' in result['reasons']


def test_kill_switch_and_negative_turnover_cannot_silently_pass():
    from quantos.contracts.killswitch import KillSwitchState
    values=inputs();values['kill_switch']=KillSwitch(KillSwitchState(tripped=True))
    assert 'KILL_SWITCH_ACTIVE' in risk.screen(material()[2],**values)['reasons']
    values=inputs();values['plan_turnover']=-1
    assert risk.screen(material()[2],**values)['status']=='REJECTED'


def test_opposing_orders_still_consume_the_whole_turnover_budget():
    values=inputs();values['native_limits']=replace(values['native_limits'],max_turnover=0.04)
    orders=material()[2];orders.append(dict(orders[0],side='SELL',newClientOrderId='QDtest000002'))
    result=risk.screen(orders,**values)
    assert result['status']=='REJECTED' and 'TURNOVER_LIMIT_EXCEEDED' in result['reasons']
