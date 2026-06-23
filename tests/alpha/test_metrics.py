"""Phase 6 step 3 — series-level alpha metrics: correct ICIR from a series,
t-stat = ICIR*sqrt(T), None-period handling, thin-coverage fail-closed gate."""
import math
import statistics

import pytest

from services.alpha import metrics


def test_icir_is_series_mean_over_std_and_tstat_is_icir_sqrt_T():
    series = [0.05, 0.02, 0.08, -0.01, 0.04, 0.03, 0.06, 0.01, 0.05, 0.02, 0.07, 0.03]
    expected_icir = statistics.fmean(series) / statistics.pstdev(series)
    assert metrics.icir(series) == expected_icir
    T = len(series)
    assert metrics.ic_tstat(series) == pytest.approx(expected_icir * math.sqrt(T))
    # two-sided p-value is in (0,1] and shrinks as t grows
    assert 0.0 < metrics.ic_pvalue(series) <= 1.0


def test_none_and_nonfinite_periods_are_dropped():
    series = [0.05, None, 0.02, float("nan"), 0.08, 0.04]
    valid = [0.05, 0.02, 0.08, 0.04]
    assert metrics.series_metrics(series, min_periods=3)["n_periods"] == 4
    assert metrics.icir(series) == statistics.fmean(valid) / statistics.pstdev(valid)


def test_hit_rate_and_autocorr():
    assert metrics.hit_rate([0.1, -0.2, 0.3, 0.4]) == 0.75
    # strongly trending series has positive lag-1 autocorr
    assert metrics.ic_autocorr([1, 2, 3, 4, 5, 6]) > 0.5


def test_thin_coverage_is_flagged_failclosed():
    # too few periods
    m = metrics.series_metrics([0.05, 0.02, 0.03], min_periods=12)
    assert m["coverage_ok"] is False
    # enough periods but breadth too thin
    series = [0.03] * 12
    m2 = metrics.series_metrics(series, breadth_per_period=[5] * 12, min_periods=12, min_breadth=10)
    assert m2["coverage_ok"] is False
    # enough of both -> ok
    m3 = metrics.series_metrics(series, breadth_per_period=[40] * 12, min_periods=12, min_breadth=10)
    assert m3["coverage_ok"] is True


def test_degenerate_series_returns_none_not_inf():
    assert metrics.icir([0.02, 0.02, 0.02]) is None      # zero std -> undefined, not inf
    assert metrics.ic_tstat([0.02, 0.02]) is None
    assert metrics.icir([0.02]) is None


def test_ic_pvalue_uses_student_t_not_normal_at_small_T():
    # at small T the normal approximation overstates significance (review finding);
    # the Student-t p-value must be strictly LARGER (heavier tails, more conservative)
    from statistics import NormalDist
    series = [0.05, 0.02, 0.06, 0.01, 0.07]   # T=5 -> df=4
    t = metrics.ic_tstat(series)
    normal_p = 2.0 * (1.0 - NormalDist().cdf(abs(t)))
    p = metrics.ic_pvalue(series)
    assert 0.0 < p <= 1.0
    assert p > normal_p * 5      # df=4, |t|~4: t-p ~0.015 vs normal-p ~5e-5 (orders of magnitude)


def test_ic_pvalue_matches_known_t_distribution_value():
    # _betai-based two-sided t p-value, checked against a tabulated value:
    # t = 2.776 at df = 4 is the 0.05 two-sided critical point
    import math as _m
    from services.alpha.metrics import _betai
    df, t = 4, 2.776
    p = _betai(df / 2.0, 0.5, df / (df + t * t))
    assert abs(p - 0.05) < 1e-3
