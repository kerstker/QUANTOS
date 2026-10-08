import math

from services.research.realism.impact import AlmgrenChrissImpact


def test_impact_zero_when_no_adv_or_no_trade():
    m = AlmgrenChrissImpact()
    assert m.total_cost_notional(0.0, 1e6) == 0.0
    assert m.total_cost_notional(1e5, 0.0) == 0.0
    assert m.total_cost_notional(1e5, -5.0) == 0.0


def test_temporary_follows_sqrt_law():
    m = AlmgrenChrissImpact(temp_coef=10.0, temp_exponent=0.5)
    # 参与率 4x -> 临时冲击 2x(sqrt)
    b1 = m.temporary_bps(1e5, 1e6)
    b4 = m.temporary_bps(4e5, 1e6)
    assert math.isclose(b4 / b1, 2.0, rel_tol=1e-9)


def test_permanent_is_linear_in_participation():
    m = AlmgrenChrissImpact(perm_coef=5.0)
    assert math.isclose(m.permanent_bps(2e5, 1e6) / m.permanent_bps(1e5, 1e6), 2.0, rel_tol=1e-9)


def test_cost_notional_monotonic_in_trade_size():
    m = AlmgrenChrissImpact()
    assert m.total_cost_notional(2e5, 1e6) > m.total_cost_notional(1e5, 1e6) > 0
