import pytest
from services.research.profitability_decision import decide_registered, POLICY_IDS


def choose(policy, prediction, current=0.):
    return decide_registered(policy, prediction=prediction, current=current, variance=.00001,
                             fee_bps=4, slip_bps=5, funding=0.)


def test_registered_band_avoids_small_new_trades_and_hysteresis_retains_position():
    assert choose('HYSTERESIS_20_5',.001)['target'] == 0
    assert choose('HYSTERESIS_20_5',.001,.25)['target'] is None
    assert choose('HYSTERESIS_20_5',-.0001,.25)['target'] == 0
    assert choose('HYSTERESIS_20_5',.003)['target'] == .25


def test_unregistered_policy_or_nonfinite_input_rejected():
    assert len(POLICY_IDS) == 4
    with pytest.raises(ValueError): choose('SEARCH_MORE',.003)
    with pytest.raises(ValueError): choose('HYSTERESIS_20_5',float('nan'))


def test_risk_reduction_is_not_suppressed_by_holding_band():
    assert choose('HYSTERESIS_20_5',.002,.4)['target'] == 0
