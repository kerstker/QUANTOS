import pytest
from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.research import ValidationVerdict
from services.intelligence.reward import evaluation_reward, generalization_gap


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


# --- generalization_gap: the in-sample-vs-out-of-sample overfit signature (search-reward only) ---

def test_generalization_gap_is_low_for_a_stable_series():
    # realistic-vol series (~Sharpe 0.9 annualised), same distribution in both halves -> small gap.
    stable = [0.018, -0.016] * 40  # 80 points
    g = generalization_gap(stable)
    assert g is not None
    assert g < 0.35            # well below a max-overfit (1.0); a stable factor is barely penalised


def test_generalization_gap_is_high_for_an_overfit_series():
    # strong positive drift in the first half, sign-flips negative in the second (the overfit signature).
    first = [0.03, -0.01] * 20    # 40 pts, high in-sample Sharpe
    second = [-0.02, 0.012] * 20  # 40 pts, negative out-of-sample Sharpe
    g = generalization_gap(first + second)
    stable = generalization_gap([0.018, -0.016] * 40)
    assert g is not None
    assert g > stable          # the overfit series is penalised more than the stable one
    assert g > 0.5


def test_generalization_gap_none_when_too_short():
    assert generalization_gap([0.01, -0.01, 0.02]) is None
    assert generalization_gap([]) is None


def test_generalization_gap_bounded_and_deterministic():
    series = [0.02, -0.01, 0.03, -0.02, 0.015, -0.005] * 12
    a = generalization_gap(series)
    b = generalization_gap(list(series))
    assert a == b                      # deterministic
    assert a is None or 0.0 <= a <= 1.0


def test_generalization_gap_handles_zero_variance_conservatively():
    # a perfectly flat half -> Sharpe is non-finite; must not blow up, returns None or a finite value.
    g = generalization_gap([0.01] * 80)
    assert g is None or (0.0 <= g <= 1.0)
