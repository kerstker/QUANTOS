import numpy as np
import pytest
from services.research.ai_factory_diagnostics import summarize_returns, search_bootstrap


def test_serial_dependence_statistics_are_bounded_and_reproducible():
    values=np.random.default_rng(4).normal(0,.01,100)
    a=summarize_returns(values);assert a==summarize_returns(values)
    assert a['count']==100 and a['mean_ci'][0]<=a['mean_ci'][1]
    assert a['authority'] is False and a['method']=='MOVING_BLOCK_BOOTSTRAP_7D'


def test_missing_or_short_history_is_unknown():
    assert summarize_returns([.01]*10)['status']=='INSUFFICIENT_HISTORY'
    with pytest.raises(ValueError):summarize_returns([float('nan')]*50)


def test_search_diagnostic_repeats_selection_with_common_resamples():
    panel=np.random.default_rng(2).normal(0,.01,(3,90))
    a=search_bootstrap(panel,trials_attempted=5)
    assert a['successful_models']==3 and a['trials_attempted']==5
    assert 0<a['family_tail_fraction']<=1
    assert a==search_bootstrap(panel,trials_attempted=5)
    assert a['adaptive_search_certified'] is False
