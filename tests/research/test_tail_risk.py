from services.research.realism.tail_risk import (historical_var_cvar, hill_tail_index,
                                                lower_tail_dependence)


def test_var_le_cvar():
    rs = [0.01, -0.02, 0.03, -0.05, 0.01, -0.10, 0.02, -0.01, 0.00, -0.07]
    vc = historical_var_cvar(rs, alpha=0.90)
    assert vc["cvar"] >= vc["var"] > 0                # CVaR(尾均损)>= VaR(尾分位)


def test_fatter_tail_smaller_hill_index():
    import random
    rng = random.Random(0)
    normal = [rng.gauss(0, 1) for _ in range(2000)]
    heavy = [rng.gauss(0, 1) * (5 if rng.random() < 0.05 else 1) for _ in range(2000)]
    a_norm = hill_tail_index(normal, tail_q=0.05)
    a_heavy = hill_tail_index(heavy, tail_q=0.05)
    assert a_heavy < a_norm                           # 更肥的尾 → 更小尾指数


def test_lower_tail_dependence_bounds_and_comonotone():
    x = [float(i) for i in range(100)]
    assert lower_tail_dependence(x, x, q=0.10) == 1.0     # 完全同向 → 1
    assert lower_tail_dependence(x, x[::-1], q=0.10) == 0.0  # 完全反向 → 0
    assert lower_tail_dependence([1.0], [1.0]) is None    # 太短 → None
