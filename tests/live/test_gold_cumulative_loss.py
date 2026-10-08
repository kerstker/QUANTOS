"""The first additive guard uses the existing ledger baseline, not daily PnL."""
from copy import deepcopy

import pytest

from live.execution import demo_position_context as position


@pytest.mark.parametrize('equity,rejected',[('4750.000000000000000001',False),('4750',True),('4749.99',True)])
def test_original_baseline_boundary_ignores_day_reset_and_peak(equity,rejected):
    account={'account_ref_sha256':'a'*64,'equity':equity,'day_pnl':'100','peak_equity':'6000'}
    context={'schema':'DEMO_CANONICAL_POSITION_CONTEXT_V1','symbol':'XAUUSDT',
             'account_ref_sha256':'a'*64,'initial_equity':'5000','day_open_equity':'4700'}
    orders=[{'symbol':'XAUUSDT','reduceOnly':False}]
    original=deepcopy((account,context))
    for _ in range(2):
        if rejected:
            with pytest.raises(position.PositionContextRejected,match='CUMULATIVE_LOSS'):
                position.require_gold_cumulative_loss(orders,account,context)
        else:position.require_gold_cumulative_loss(orders,account,context)
    assert (account,context)==original


def test_reduction_is_not_permission_to_reopen_in_a_mixed_batch():
    context={'schema':'DEMO_CANONICAL_POSITION_CONTEXT_V1','symbol':'XAUUSDT',
             'account_ref_sha256':'a'*64,'initial_equity':'5000'}
    account={'account_ref_sha256':'a'*64,'equity':'4700'}
    reduction={'symbol':'XAUUSDT','reduceOnly':True}
    position.require_gold_cumulative_loss([reduction],account,context)
    with pytest.raises(position.PositionContextRejected,match='CUMULATIVE_LOSS'):
        position.require_gold_cumulative_loss([reduction,{**reduction,'reduceOnly':False}],account,context)


@pytest.mark.parametrize('initial',['NaN','Infinity','0','-1',True])
def test_invalid_baseline_cannot_admit_an_opening(initial):
    context={'schema':'DEMO_CANONICAL_POSITION_CONTEXT_V1','symbol':'XAUUSDT',
             'account_ref_sha256':'a'*64,'initial_equity':initial}
    with pytest.raises(position.PositionContextRejected):
        position.require_gold_cumulative_loss([{'symbol':'XAUUSDT','reduceOnly':False}],
            {'account_ref_sha256':'a'*64,'equity':'5000'},context)
