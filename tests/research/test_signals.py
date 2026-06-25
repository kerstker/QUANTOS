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


def test_all_operators_registered():
    assert set(SIGNAL_OPS) >= {"momentum", "ts_mean", "zscore", "volatility",
                               "ts_rank", "ema_ratio", "dist_from_high", "accel"}


def test_volatility_formula_and_guards():
    import statistics
    f = SIGNAL_OPS["volatility"]
    closes = [10.0, 11.0, 10.0, 12.0]                  # window=3 → 3 returns
    rets = [11 / 10 - 1, 10 / 11 - 1, 12 / 10 - 1]
    assert f(closes, 3) == pytest.approx(statistics.pstdev(rets))
    assert f([10.0, 11.0], 3) is None                  # too short (need window+1)
    assert f([5.0, 5.0, 5.0, 5.0], 3) == pytest.approx(0.0)   # constant → zero vol (valid)


def test_ts_rank_formula():
    f = SIGNAL_OPS["ts_rank"]
    assert f([3.0, 1.0, 2.0, 4.0], 4) == pytest.approx(4 / 4)   # last is max → 1.0
    assert f([3.0, 1.0, 4.0, 2.0], 4) == pytest.approx(2 / 4)   # last beats {1,2} incl self
    assert f([1.0], 2) is None


def test_ema_ratio_formula_and_constant():
    f = SIGNAL_OPS["ema_ratio"]
    assert f([5.0, 5.0, 5.0], 2) == pytest.approx(0.0)         # constant → ratio 0
    a = 2.0 / 3.0
    ema = 10.0
    for c in (12.0, 11.0):
        ema = a * c + (1 - a) * ema
    assert f([10.0, 12.0, 11.0], 2) == pytest.approx(11.0 / ema - 1.0)
    assert f([], 2) is None


def test_dist_from_high_formula():
    f = SIGNAL_OPS["dist_from_high"]
    assert f([10.0, 14.0, 12.0], 3) == pytest.approx(12.0 / 14.0 - 1.0)  # below high
    assert f([10.0, 12.0, 14.0], 3) == pytest.approx(0.0)               # at high
    assert f([10.0], 3) is None


def test_accel_formula_and_short():
    f = SIGNAL_OPS["accel"]
    closes = [10.0, 11.0, 12.0, 15.0]      # window=2: mom_now=15/12-1, mom_prev=12/11-1... via slices
    mom_now = SIGNAL_OPS["momentum"](closes, 2)
    mom_prev = SIGNAL_OPS["momentum"](closes[:-2], 2)
    assert f(closes, 2) == pytest.approx(mom_now - mom_prev)
    assert f([10.0, 11.0, 12.0], 2) is None            # len < 2*window
