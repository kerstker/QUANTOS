import math
import pytest
from services.portfolio.constraints import PortfolioConstraints, InfeasibleConstraints
from services.portfolio.constrained_optimizer import min_variance_weights, OptimizerError


def _quad(w, cov):
    n = len(w)
    return sum(w[i] * cov[i][j] * w[j] for i in range(n) for j in range(n))


COV = [[0.04, 0.006, 0.0], [0.006, 0.09, 0.0], [0.0, 0.0, 0.16]]
IDS = ["a", "b", "c"]


def test_respects_budget_and_box():
    c = PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)
    w = min_variance_weights(COV, IDS, c)
    assert sum(w) == pytest.approx(1.0, abs=1e-6)
    assert all(-1e-6 <= x <= 1.0 + 1e-6 for x in w)


def test_min_variance_beats_equal_weight():
    c = PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)
    w = min_variance_weights(COV, IDS, c)
    eq = [1 / 3, 1 / 3, 1 / 3]
    assert _quad(w, COV) <= _quad(eq, COV) + 1e-9


def test_long_short_factor_neutrality():
    loadings = [[1.0], [1.0], [-1.0]]
    c = PortfolioConstraints(lower=-1.0, upper=1.0, budget=0.0, factor_targets={"f": 0.0})
    w = min_variance_weights(COV, IDS, c, loadings=loadings, factor_ids=["f"])
    exposure = sum(w[i] * loadings[i][0] for i in range(3))
    assert exposure == pytest.approx(0.0, abs=1e-5)
    assert sum(w) == pytest.approx(0.0, abs=1e-5)


def test_gross_leverage_cap_enforced():
    c = PortfolioConstraints(lower=-2.0, upper=2.0, budget=0.0, max_gross=1.0)
    w = min_variance_weights(COV, IDS, c)
    assert sum(abs(x) for x in w) <= 1.0 + 1e-5


def test_infeasible_constraints_raise():
    c = PortfolioConstraints(lower=0.0, upper=0.1, budget=1.0)   # 3*0.1 < 1
    with pytest.raises(InfeasibleConstraints):
        min_variance_weights(COV, IDS, c)


def test_non_finite_cov_raises():
    bad = [[float("nan"), 0.0, 0.0], [0.0, 0.09, 0.0], [0.0, 0.0, 0.16]]
    with pytest.raises(ValueError):
        min_variance_weights(bad, IDS, PortfolioConstraints(lower=0.0, upper=1.0))


def test_is_deterministic():
    c = PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)
    assert min_variance_weights(COV, IDS, c) == min_variance_weights(COV, IDS, c)
