"""Pipeline integration — built-in signal operators (per-instrument scalar from a
PIT close history + window). PIT/lookahead-safe is the builder's job; here we test
the pure math + fail-soft on short/degenerate history."""
import pytest
from services.research.signals import SIGNAL_OPS


def test_momentum_formula_and_short_history():
    f = SIGNAL_OPS["momentum"]
    assert f([10.0, 11.0, 12.0], 2) == pytest.approx(12.0 / 11.0 - 1)
    assert f([10.0], 2) is None                       # too short


def test_ts_mean_formula():
    f = SIGNAL_OPS["ts_mean"]
    assert f([10.0, 12.0, 14.0], 3) == pytest.approx(14.0 / ((10 + 12 + 14) / 3) - 1)


def test_zscore_formula_and_zero_std():
    import statistics
    f = SIGNAL_OPS["zscore"]
    window = [10.0, 12.0, 8.0, 14.0]
    expected = (window[-1] - statistics.fmean(window)) / statistics.pstdev(window)
    assert f(window, 4) == pytest.approx(expected)
    assert f([5.0, 5.0, 5.0], 3) is None              # zero std -> undefined


def test_unknown_operator_fails_closed():
    with pytest.raises(KeyError):
        SIGNAL_OPS["bogus"]
