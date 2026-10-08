"""Phase 7 step 1 — portfolio risk model: covariance, shrinkage, risk contributions."""
import math

import pytest

from services.portfolio.risk_model import (
    covariance_matrix, percent_risk_contributions, portfolio_variance,
    portfolio_vol, risk_contributions, shrink_covariance,
)


def _series(n, fn):
    return tuple(fn(i) for i in range(n))


def test_covariance_diagonal_is_variance_and_symmetric():
    a = _series(80, lambda i: math.sin(i * 0.3))
    b = _series(80, lambda i: math.cos(i * 0.5))
    ids, cov = covariance_matrix({"a": a, "b": b}, min_overlap=10)
    assert ids == ["a", "b"]
    import statistics
    assert cov[0][0] == pytest.approx(statistics.variance(a))
    assert cov[1][1] == pytest.approx(statistics.variance(b))
    assert cov[0][1] == pytest.approx(cov[1][0])             # symmetric


def test_covariance_fails_closed_below_min_overlap():
    short = tuple(range(20))
    with pytest.raises(ValueError):
        covariance_matrix({"a": short, "b": short}, min_overlap=60)


def test_shrinkage_pulls_off_diagonal_toward_zero_keeps_diagonal():
    cov = [[0.04, 0.03], [0.03, 0.09]]
    shr = shrink_covariance(cov, intensity=0.5)
    assert shr[0][0] == 0.04 and shr[1][1] == 0.09         # diagonal unchanged
    assert shr[0][1] == pytest.approx(0.015) and shr[1][0] == pytest.approx(0.015)  # off-diag halved


def test_portfolio_variance_and_vol_match_manual():
    cov = [[0.04, 0.0], [0.0, 0.09]]
    w = [0.5, 0.5]
    # 0.25*0.04 + 0.25*0.09 = 0.0325
    assert portfolio_variance(w, cov) == pytest.approx(0.0325)
    assert portfolio_vol(w, cov) == pytest.approx(math.sqrt(0.0325))


def test_risk_contributions_sum_to_variance_and_equal_when_symmetric():
    cov = [[0.04, 0.0], [0.0, 0.04]]   # equal vol, uncorrelated
    w = [0.5, 0.5]
    rc = risk_contributions(w, cov)
    assert sum(rc) == pytest.approx(portfolio_variance(w, cov))
    assert rc[0] == pytest.approx(rc[1])                    # equal risk contribution
    assert percent_risk_contributions(w, cov) == pytest.approx([0.5, 0.5])
