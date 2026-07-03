"""Rank #10c — tail-aware (CVaR) objective option on the constrained optimizer.

Pure min-variance is blind to the fat left tail RE-2 found (Hill 2.4-2.7). An optional
Rockafellar-Uryasev empirical-CVaR penalty (+ρ·CVaR_α over a caller-supplied scenario
matrix, folded in as a tail subgradient) tilts weight away from assets whose tail loss
the variance term cannot see. Backward-compatible: no scenarios / zero penalty == unchanged."""
import math
import pytest

from services.portfolio.constraints import PortfolioConstraints
from services.portfolio.constrained_optimizer import min_variance_weights


IDS = ["risky", "safe"]
COV = [[0.04, 0.0], [0.0, 0.04]]          # equal variance, uncorrelated -> min-var is ~50/50
# 'risky' carries two catastrophic crash scenarios that variance (symmetric) cannot see;
# 'safe' never crashes. Same sample variance, very different tail.
SCEN = [[-0.50, 0.00], [-0.50, 0.00]] + [[0.01, 0.01]] * 8


def _c():
    return PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)


def test_cvar_penalty_tilts_away_from_the_fat_tail_asset():
    base = min_variance_weights(COV, IDS, _c())
    tilted = min_variance_weights(COV, IDS, _c(), cvar_scenarios=SCEN, cvar_penalty=3.0, cvar_alpha=0.8)
    assert tilted[0] < base[0] - 1e-3          # less weight on the crash-prone asset
    assert sum(tilted) == pytest.approx(1.0, abs=1e-5)
    assert all(-1e-6 <= x <= 1.0 + 1e-6 for x in tilted)   # still feasible (budget/box)


def test_zero_penalty_is_identical_to_pure_min_variance():
    base = min_variance_weights(COV, IDS, _c())
    with_zero = min_variance_weights(COV, IDS, _c(), cvar_scenarios=SCEN, cvar_penalty=0.0)
    assert with_zero == base                    # penalty off -> byte-identical (backward compat)


def test_none_scenarios_is_identical_to_pure_min_variance():
    base = min_variance_weights(COV, IDS, _c())
    assert min_variance_weights(COV, IDS, _c(), cvar_scenarios=None, cvar_penalty=3.0) == base


def test_cvar_is_deterministic():
    a = min_variance_weights(COV, IDS, _c(), cvar_scenarios=SCEN, cvar_penalty=3.0, cvar_alpha=0.8)
    b = min_variance_weights(COV, IDS, _c(), cvar_scenarios=SCEN, cvar_penalty=3.0, cvar_alpha=0.8)
    assert a == b


def test_non_finite_scenarios_raise():
    bad = [[float("nan"), 0.0]] + [[0.01, 0.01]] * 9
    with pytest.raises(ValueError):
        min_variance_weights(COV, IDS, _c(), cvar_scenarios=bad, cvar_penalty=1.0)


def test_wrong_width_scenarios_raise():
    bad = [[0.01, 0.01, 0.01]] * 10             # 3 cols for a 2-asset book
    with pytest.raises(ValueError):
        min_variance_weights(COV, IDS, _c(), cvar_scenarios=bad, cvar_penalty=1.0)


def test_cvar_penalty_respects_the_gross_leverage_cap():
    c = PortfolioConstraints(lower=-2.0, upper=2.0, budget=0.0, max_gross=1.0)
    w = min_variance_weights(COV, IDS, c, cvar_scenarios=SCEN, cvar_penalty=3.0, cvar_alpha=0.8)
    assert sum(abs(x) for x in w) <= 1.0 + 1e-5
