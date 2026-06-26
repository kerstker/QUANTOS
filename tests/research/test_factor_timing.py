"""Factor timing (tests-first).

A factor itself goes through good and bad stretches; time exposure to its OWN recent health
(trailing Sharpe). weight[t] uses only returns STRICTLY before t (PIT). 0 while a factor is
sick (trailing health <= 0) — don't keep betting a losing factor. Pure, deterministic.
"""
from services.research.factor_timing import factor_timing_weights


def test_sick_factor_gets_zero_weight():
    w = factor_timing_weights([-0.02] * 30, lookback=10)
    assert all(x == 0.0 for x in w[10:])


def test_healthy_factor_gets_positive_weight():
    w = factor_timing_weights([0.02] * 30, lookback=10)
    assert all(x > 0.0 for x in w[10:])


def test_pit_warmup_is_zero_and_length_matches():
    w = factor_timing_weights([0.01] * 20, lookback=10)
    assert len(w) == 20 and all(x == 0.0 for x in w[:10])


def test_weights_in_unit_range():
    import statistics
    rets = [0.03, -0.01, 0.02, 0.04, -0.02, 0.05, 0.01, 0.03] * 4
    w = factor_timing_weights(rets, lookback=8)
    assert all(0.0 <= x <= 1.0 for x in w)
