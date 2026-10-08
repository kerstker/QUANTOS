"""Phase 6 step 7 — crowding (public-factor R^2, fail-closed) and capacity
(impact-budget / participation, Sharpe haircut)."""
import math

import pytest

from services.alpha.capacity import (
    capacity_adjusted_sharpe, capacity_notional, participation_capacity,
)
from services.alpha.crowding import crowding_score, is_crowded, max_factor_loading
from services.research.backtest_engine.costs import CostModel


def _mom():
    return tuple(math.sin(i * 0.3) for i in range(80))


def _orth():
    return tuple(math.cos(i * 0.55) for i in range(80))


# --- crowding ----------------------------------------------------------------

def test_pure_public_factor_scores_high():
    panel = {"momentum": _mom()}
    assert crowding_score(_mom(), panel, min_overlap=10) > 0.95   # it IS the factor


def test_orthogonal_alpha_scores_low():
    panel = {"momentum": _mom()}
    assert crowding_score(_orth(), panel, min_overlap=10) < 0.3


def test_missing_panel_is_none_not_zero():
    # fail-closed: no panel => undefined => caller routes to human review
    assert crowding_score(_mom(), {}, min_overlap=10) is None
    assert max_factor_loading(_mom(), {}, min_overlap=10) is None
    assert is_crowded(None, crowd_max=0.6) is False     # None is not "uncrowded"


def test_insufficient_overlap_is_none():
    short = tuple(range(20))
    assert crowding_score(short, {"f": tuple(range(20))}, min_overlap=60) is None


# --- capacity ----------------------------------------------------------------

def test_participation_capacity():
    assert participation_capacity(1_000_000.0, 0.1) == 100_000.0


def test_impact_budget_can_bind_below_participation_cap():
    adv = 1_000_000.0
    loose = capacity_notional(adv, CostModel(impact_coef=0), max_participation=0.2)
    tight = capacity_notional(adv, CostModel(impact_coef=200), impact_budget_bps=10.0, max_participation=0.2)
    assert loose == pytest.approx(200_000.0)             # participation cap only
    assert tight < loose                                 # impact budget binds tighter


def test_capacity_haircut_on_sharpe():
    # can only carry half of target AUM -> Sharpe halved
    assert capacity_adjusted_sharpe(2.0, capacity_notional_value=5e6, target_aum=1e7) == pytest.approx(1.0)
    # ample capacity -> unchanged
    assert capacity_adjusted_sharpe(2.0, capacity_notional_value=5e7, target_aum=1e7) == pytest.approx(2.0)
