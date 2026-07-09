"""Cross-check between the platform's TWO independent capacity implementations (2026-07-10, gap
audit rank #5): services/alpha/capacity.py::capacity_notional (a closed-form participation-cap /
impact-budget-cap formula, wired into the promotion evaluator/ranker) and services/research/
realism/capacity_curve.py::capacity_band (a full Almgren-Chriss net-Sharpe-erosion AUM-grid search,
wired into the standalone tools/capacity_research.py diagnostic). Both claim to use "the same
sqrt-participation basis" — before this test, nothing ever checked that claim; the two numbers
could silently drift apart with no code path catching it (a reader looking at capacity_adj_sharpe
on the AlphaZoo page vs a capacity-band diagnostic could see two very different stories about the
same alpha's deployable size).

These are genuinely DIFFERENT metrics by design (a fixed-bps-cost threshold vs a Sharpe-floor-
crossing point that depends on the underlying gross edge), so this does NOT assert numeric
equality — it asserts they land in the same rough neighborhood under MATCHED inputs (same ADV,
same impact coefficient, participation unconstrained so both isolate pure impact-budget capacity),
catching a genuine methodological drift (a units error, an inverted formula, a sign flip) without
overclaiming precision the two formulas were never designed to share."""
import math

import pytest

from services.alpha.capacity import capacity_notional
from services.research.backtest_engine.costs import CostModel
from services.research.realism.capacity_curve import capacity_band
from services.research.realism.impact import AlmgrenChrissImpact

_ADV = 1e7
_COEF = 10.0   # bps at 100% participation — matches AlmgrenChrissImpact's own class default


def test_capacity_notional_matches_impact_budget_bps_definition():
    """Sanity-pin capacity.py's own closed form before cross-checking against capacity_curve.py."""
    cap = capacity_notional(_ADV, CostModel(impact_coef=_COEF), impact_budget_bps=10.0,
                            max_participation=1.0)
    assert cap == pytest.approx(_ADV * (10.0 / _COEF) ** 2)


def test_capacity_notional_and_capacity_band_agree_within_an_order_of_magnitude():
    """Matched inputs (same ADV, same temp-only impact coefficient, participation unconstrained
    on both sides) — the two independently-implemented capacity engines must land within a
    generous but bounded multiplicative range of each other, not orders of magnitude apart."""
    cap_closed_form = capacity_notional(
        _ADV, CostModel(impact_coef=_COEF), impact_budget_bps=10.0, max_participation=1.0)

    n_periods = 104
    # gross edge tuned so a ~10bps/period impact cost is a comparable-magnitude drag against it
    # (an edge orders of magnitude bigger than the cost threshold would push capacity_band's
    # Sharpe-erosion crossing far beyond any impact-budget comparison — a property of THIS test's
    # chosen edge, not a general claim that the two formulas always agree at any edge size).
    gross = [0.0015 + 0.001 * math.sin(i * 0.7) for i in range(n_periods)]
    turnover = [[(1.0, _ADV)] for _ in range(n_periods)]   # one aggregate book-level leg/period
    aum_grid = [1e5, 3e5, 1e6, 2e6, 3e6, 5e6, 8e6, 1e7, 1.3e7, 2e7, 3e7, 1e8]

    band = capacity_band(
        gross, turnover, aum_grid, (_COEF, _COEF), ppy=52, sharpe_floor=0.3,
        max_participation=1.0,
        base_impact=AlmgrenChrissImpact(temp_coef=_COEF, perm_coef=0.0, temp_exponent=0.5))

    cap_curve_search = band["impact_aum_low"]   # pure impact-only critical AUM (participation unconstrained)
    assert cap_curve_search is not None and cap_curve_search > 0
    assert cap_closed_form > 0

    ratio = cap_curve_search / cap_closed_form
    assert 0.1 <= ratio <= 10.0, (
        f"capacity.py={cap_closed_form:,.0f} vs capacity_curve.py={cap_curve_search:,.0f} "
        f"(ratio={ratio:.2f}) — outside the same-order-of-magnitude band; a genuine drift "
        f"between the two 'same basis' capacity engines, not just their inherent methodological "
        f"difference (fixed-bps threshold vs Sharpe-erosion crossing).")


def test_capacity_notional_and_capacity_band_both_scale_up_with_larger_adv():
    """Directional consistency check: doubling ADV should roughly double BOTH capacity numbers
    (a basic sanity property either engine could silently violate under a units bug)."""
    def _closed_form(adv):
        return capacity_notional(adv, CostModel(impact_coef=_COEF), impact_budget_bps=10.0,
                                 max_participation=1.0)

    def _curve_search(adv):
        n_periods = 104
        gross = [0.0015 + 0.001 * math.sin(i * 0.7) for i in range(n_periods)]
        turnover = [[(1.0, adv)] for _ in range(n_periods)]
        aum_grid = [x * (adv / _ADV) for x in
                   (1e5, 3e5, 1e6, 2e6, 3e6, 5e6, 8e6, 1e7, 1.3e7, 2e7, 3e7, 1e8)]
        band = capacity_band(
            gross, turnover, aum_grid, (_COEF, _COEF), ppy=52, sharpe_floor=0.3,
            max_participation=1.0,
            base_impact=AlmgrenChrissImpact(temp_coef=_COEF, perm_coef=0.0, temp_exponent=0.5))
        return band["impact_aum_low"]

    cap_1x_closed, cap_2x_closed = _closed_form(_ADV), _closed_form(_ADV * 2)
    cap_1x_curve, cap_2x_curve = _curve_search(_ADV), _curve_search(_ADV * 2)

    assert cap_2x_closed == pytest.approx(cap_1x_closed * 2)
    assert cap_2x_curve == pytest.approx(cap_1x_curve * 2, rel=0.05)
