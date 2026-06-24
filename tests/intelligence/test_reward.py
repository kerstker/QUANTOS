import pytest
from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.research import ValidationVerdict
from services.intelligence.reward import evaluation_reward


def _ev(ds=0.0, ns=0.0, verdict=ValidationVerdict.REQUIRES_HUMAN_REVIEW):
    return AlphaEvaluation(alpha_id="x", verdict=verdict, deflated_sharpe=ds, net_sharpe=ns)


def test_reward_is_bounded_unit_interval():
    assert 0.0 <= evaluation_reward(_ev(ds=1.0, ns=10.0, verdict=ValidationVerdict.PASS)) <= 1.0
    assert 0.0 <= evaluation_reward(_ev(ds=0.0, ns=-10.0)) <= 1.0


def test_reward_is_monotone_in_quality_signals():
    assert evaluation_reward(_ev(ds=0.8)) > evaluation_reward(_ev(ds=0.2))
    assert evaluation_reward(_ev(ns=1.0)) > evaluation_reward(_ev(ns=-1.0))


def test_pass_beats_warn_beats_fail_for_equal_metrics():
    p = evaluation_reward(_ev(ds=0.5, ns=0.5, verdict=ValidationVerdict.PASS))
    w = evaluation_reward(_ev(ds=0.5, ns=0.5, verdict=ValidationVerdict.WARN))
    f = evaluation_reward(_ev(ds=0.5, ns=0.5, verdict=ValidationVerdict.FAIL))
    assert p > w > f


def test_none_evaluation_is_zero():
    assert evaluation_reward(None) == 0.0
