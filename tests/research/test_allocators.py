"""services/research/allocators.py 的纯核测试 —— 配置器数学手算、约束可行性、回退性质
(低置信 → 精确回退权重)、换手惩罚 λ 单调性、PIT 截断不变(追加极端未来数据 → 历史时点
权重逐位不变)、引擎可行性承重校验、判定规则分支。全合成数据,不碰真实 DB/网络。"""
from __future__ import annotations

import pytest

from services.research import allocators as al

CONS = al.AllocationConstraints(gross=1.0, strategy_cap=0.40, cluster_cap=0.40)
IDS = ("a", "b", "c", "d")
SINGLETONS = {i: i for i in IDS}


def _series(vals: dict, n: int) -> dict:
    """{sid: [周收益 × n]}(默认重复给定序列)。"""
    out = {}
    for sid, v in vals.items():
        reps = (n // len(v)) + 1
        out[sid] = (list(v) * reps)[:n]
    return out


# ---------------------------------------------------------------------------
# 约束投影
# ---------------------------------------------------------------------------

def test_apply_constraints_identity_when_feasible():
    w, diag = al.apply_constraints({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25},
                                   SINGLETONS, CONS)
    assert diag["feasible"] is True
    assert w == pytest.approx({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25})


def test_apply_constraints_strategy_cap_and_waterfill():
    w, diag = al.apply_constraints({"a": 0.9, "b": 0.05, "c": 0.03, "d": 0.02},
                                   SINGLETONS, CONS)
    assert diag["feasible"] is True
    assert sum(w.values()) == pytest.approx(1.0)
    assert all(v <= 0.40 + 1e-9 for v in w.values())
    assert w["a"] == pytest.approx(0.40)          # 帽钉死
    assert w["b"] > 0.05                          # 残差按 headroom 注水


def test_apply_constraints_cluster_cap_hand_check():
    cmap = {"a": "X", "b": "X", "c": "c", "d": "d"}
    w, diag = al.apply_constraints({"a": 0.35, "b": 0.35, "c": 0.30, "d": 0.0},
                                   cmap, CONS)
    # 簇 X:0.7 → 缩到 0.4(a=b=0.2);残差 0.3 按 headroom 注水:c headroom 0.1、d 0.4
    # → c += 0.3*(0.1/0.5)=0.06,d += 0.3*(0.4/0.5)=0.24 → Σ=1.0
    assert diag["feasible"] is True
    assert w["a"] + w["b"] == pytest.approx(0.40)
    assert w["c"] == pytest.approx(0.36)
    assert w["d"] == pytest.approx(0.24)
    assert sum(w.values()) == pytest.approx(1.0)


def test_apply_constraints_infeasible_caps_flagged_not_violated():
    tight = al.AllocationConstraints(gross=1.0, strategy_cap=0.20, cluster_cap=0.20)
    w, diag = al.apply_constraints({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25},
                                   SINGLETONS, tight)
    assert diag["feasible"] is False               # Σmax=0.8 < gross → 诚实标注
    assert sum(w.values()) == pytest.approx(0.8)
    assert all(v <= 0.20 + 1e-9 for v in w.values())


# ---------------------------------------------------------------------------
# 等权 / 逆波动 / 簇风险预算(手算)
# ---------------------------------------------------------------------------

def test_equal_weight_hand_check():
    res = al.equal_weight(IDS, CONS, SINGLETONS)
    assert res["weights"] == pytest.approx({i: 0.25 for i in IDS})
    assert res["diagnostics"]["feasible"] is True


def test_inverse_vol_hand_check_and_fallback():
    # σ_a=0.01, σ_b=0.02(交替 ±) → w_a = (1/0.01)/(1/0.01+1/0.02) = 2/3
    rets = _series({"a": [0.01, -0.01], "b": [0.02, -0.02]}, 30)
    cons = al.AllocationConstraints(gross=1.0, strategy_cap=1.0, cluster_cap=1.0)
    res = al.inverse_vol(rets, 30, lookback=26, min_history=12, constraints=cons)
    assert res["weights"]["a"] == pytest.approx(2.0 / 3.0)
    assert res["weights"]["b"] == pytest.approx(1.0 / 3.0)
    assert res["diagnostics"]["used_fallback"] is False
    # 历史不足 → 等权回退
    res2 = al.inverse_vol(rets, 8, lookback=26, min_history=12, constraints=cons)
    assert res2["weights"] == pytest.approx({"a": 0.5, "b": 0.5})
    assert res2["diagnostics"]["used_fallback"] is True


def test_cluster_risk_parity_two_cluster_hand_check():
    # {a,b} 簇(σ=0.01 各)+ {c} 簇(σ=0.02):簇内逆波动 a=b=0.5;簇间预算 2/3 vs 1/3
    # → a=b=1/3, c=1/3(σ 比 2:1 → 预算 2:1,簇内再半分)
    rets = _series({"a": [0.01, -0.01], "b": [0.01, -0.01], "c": [0.02, -0.02]}, 30)
    cmap = {"a": "AB", "b": "AB", "c": "c"}
    cons = al.AllocationConstraints(gross=1.0, strategy_cap=1.0, cluster_cap=1.0)
    res = al.cluster_risk_parity(cmap, rets, 30, lookback=26, min_history=12, constraints=cons)
    assert res["weights"]["a"] == pytest.approx(1.0 / 3.0)
    assert res["weights"]["b"] == pytest.approx(1.0 / 3.0)
    assert res["weights"]["c"] == pytest.approx(1.0 / 3.0)
    assert res["diagnostics"]["n_clusters"] == 2


def test_cluster_risk_parity_singleton_equals_inverse_vol_modulo_caps():
    # 全单例簇、帽不约束 → 与逆波动同解(W3 簇图现状的诚实注记,机制等价性钉死)
    rets = _series({"a": [0.01, -0.01], "b": [0.02, -0.02], "c": [0.015, -0.015]}, 30)
    cons = al.AllocationConstraints(gross=1.0, strategy_cap=1.0, cluster_cap=1.0)
    iv = al.inverse_vol(rets, 30, lookback=26, min_history=12, constraints=cons)["weights"]
    crp = al.cluster_risk_parity({"a": "a", "b": "b", "c": "c"}, rets, 30,
                                 lookback=26, min_history=12, constraints=cons)["weights"]
    assert crp == pytest.approx(iv)


# ---------------------------------------------------------------------------
# 约束最大夏普
# ---------------------------------------------------------------------------

def test_max_sharpe_picks_dominant_strategy_up_to_cap():
    # a: 均值 0.01/σ 0.01(主导);b/c/d: 均值 ≤0 或高波动 → 帽内全配 a
    rets = {"a": [0.01, 0.02, 0.0, 0.01, 0.02, 0.0] * 10,
            "b": [-0.01, 0.01, -0.02, 0.02, -0.01, 0.0] * 10,
            "c": [0.001, -0.001] * 30,
            "d": [0.05, -0.05, 0.03, -0.04, 0.02, -0.03] * 10}
    res = al.constrained_max_sharpe(rets, 60, lookback=52, min_history=30,
                                    mean_shrink=0.5, grid_step=0.02, constraints=CONS,
                                    cluster_map=SINGLETONS)
    assert res["diagnostics"]["used_fallback"] is False
    assert res["weights"]["a"] == pytest.approx(0.40)       # 帽钉死在主导策略上
    assert sum(res["weights"].values()) == pytest.approx(1.0)
    assert all(v <= 0.40 + 1e-9 for v in res["weights"].values())


def test_max_sharpe_insufficient_history_falls_back_to_equal_weight():
    rets = _series({"a": [0.01], "b": [0.02], "c": [0.015], "d": [0.005]}, 20)
    res = al.constrained_max_sharpe(rets, 20, lookback=52, min_history=30,
                                    constraints=CONS, cluster_map=SINGLETONS)
    assert res["diagnostics"]["used_fallback"] is True
    assert res["diagnostics"]["fallback_reason"] == "insufficient_history"
    assert res["weights"] == pytest.approx({i: 0.25 for i in IDS})


def test_max_sharpe_determinism_bit_identical():
    rets = {"a": [0.01, -0.005, 0.02, -0.01, 0.005] * 12,
            "b": [0.005, 0.01, -0.01, 0.0, 0.015] * 12,
            "c": [-0.01, 0.02, -0.005, 0.01, 0.0] * 12,
            "d": [0.0, 0.01, -0.02, 0.01, 0.005] * 12}
    r1 = al.constrained_max_sharpe(rets, 60, constraints=CONS, cluster_map=SINGLETONS)
    r2 = al.constrained_max_sharpe(rets, 60, constraints=CONS, cluster_map=SINGLETONS)
    assert r1["weights"] == r2["weights"]                    # 逐位相同(无 RNG/迭代初值)


# ---------------------------------------------------------------------------
# 换手惩罚(死区;λ 精确单调)
# ---------------------------------------------------------------------------

def test_turnover_penalized_hand_math():
    prev = {"a": 0.5, "b": 0.5}
    base = {"a": 0.7, "b": 0.3}
    res = al.turnover_penalized(base, prev, 0.05)
    # S=0.4, dead=0.1, beta=0.75 → a=0.5+0.75*0.2=0.65, b=0.35;实现换手=0.3
    assert res["weights"] == pytest.approx({"a": 0.65, "b": 0.35})
    assert res["diagnostics"]["realized_turnover"] == pytest.approx(0.3)
    # 死区内 → 原样持有
    res2 = al.turnover_penalized(base, prev, 0.3)
    assert res2["weights"] == prev
    assert res2["diagnostics"]["realized_turnover"] == pytest.approx(0.0)


def test_turnover_penalized_lambda_monotonicity_exact():
    prev = {"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}
    base = {"a": 0.1, "b": 0.25, "c": 0.35, "d": 0.3}
    turns = [al.turnover_penalized(base, prev, lam)["diagnostics"]["realized_turnover"]
             for lam in (0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5)]
    assert all(b <= a + 1e-12 for a, b in zip(turns, turns[1:]))
    assert turns[0] == pytest.approx(0.7)                    # λ=0 → 全调(S=0.3+0.05+0.15+0.2)
    assert turns[-1] == pytest.approx(0.0)                   # 死区盖住 → 不动


def test_turnover_penalized_initial_entry_not_penalized():
    base = {"a": 0.4, "b": 0.3, "c": 0.3}
    res = al.turnover_penalized(base, {"a": 0.0, "b": 0.0, "c": 0.0}, 0.05)
    assert res["weights"] == base                            # 入场全额,不留现金拖尾
    assert res["diagnostics"]["initial_entry"] is True


def test_turnover_penalized_preserves_feasibility_convexity():
    prev = {"a": 0.40, "b": 0.30, "c": 0.20, "d": 0.10}
    base = {"a": 0.10, "b": 0.40, "c": 0.40, "d": 0.10}
    res = al.turnover_penalized(base, prev, 0.03)
    assert sum(res["weights"].values()) == pytest.approx(1.0)
    assert all(0.0 <= v <= 0.40 + 1e-9 for v in res["weights"].values())


# ---------------------------------------------------------------------------
# regime 条件化(回退性质 = 精确回退权重)
# ---------------------------------------------------------------------------

def test_regime_conditioned_low_confidence_returns_exact_fallback():
    base = {"a": 0.40, "b": 0.35, "c": 0.25}
    fb = {"a": 0.5, "b": 0.3, "c": 0.2}
    res = al.regime_conditioned(base, 0.9, 0.4, fb, confidence_threshold=0.6)
    assert res["weights"] == fb                              # 逐字段精确相等,绝不掺 base
    assert res["diagnostics"]["used_fallback"] is True
    assert res["diagnostics"]["fallback_reason"] == "low_confidence"


def test_regime_conditioned_unknown_stress_returns_exact_fallback():
    res = al.regime_conditioned({"a": 0.6, "b": 0.4}, None, 1.0, {"a": 0.3, "b": 0.7})
    assert res["weights"] == {"a": 0.3, "b": 0.7}
    assert res["diagnostics"]["fallback_reason"] == "stress_unknown"


def test_regime_conditioned_tilt_math_and_asymmetry():
    base = {"a": 0.8, "b": 0.2}
    fb = {"a": 0.2, "b": 0.8}
    # stress=1.0 → τ=(1.0-0.5)*2*max_tilt=0.5 → w = 0.5*base+0.5*fb
    hi = al.regime_conditioned(base, 1.0, 1.0, fb, max_tilt=0.5)
    assert hi["weights"] == pytest.approx({"a": 0.5, "b": 0.5})
    assert hi["diagnostics"]["tilt"] == pytest.approx(0.5)
    # stress=0.75 → τ=0.25
    mid = al.regime_conditioned(base, 0.75, 1.0, fb, max_tilt=0.5)
    assert mid["weights"] == pytest.approx({"a": 0.65, "b": 0.35})
    # stress ≤ 0.5 → 不倾斜(不对称保守:calm 不加注)
    calm = al.regime_conditioned(base, 0.2, 1.0, fb, max_tilt=0.5)
    assert calm["weights"] == base
    assert calm["diagnostics"]["tilt"] == 0.0


# ---------------------------------------------------------------------------
# regime 原语 + PIT 截断不变
# ---------------------------------------------------------------------------

def test_trailing_zscore_and_stress_mapping():
    vals = [1.0] * 30 + [3.0]
    z = al.trailing_zscore(vals, 31, lookback=52, min_history=26)
    assert z is not None and z > 1.0
    assert al.trailing_zscore(vals, 10, lookback=52, min_history=26) is None   # 暖机不足
    assert al.z_to_stress(2.0) == 1.0 and al.z_to_stress(-2.0) == 0.0
    assert al.z_to_stress(0.0) == 0.5 and al.z_to_stress(None) is None
    assert al.tercile_to_stress("high") == 1.0 and al.tercile_to_stress(None) is None


def test_build_regime_series_shift_and_confidence():
    n = 40
    rets = _series({"a": [0.01, -0.01], "b": [0.02, -0.02]}, n)
    vix = [10.0] * (n - 5) + [40.0] * 5                       # 尾部高压
    fund = [0.001, -0.001] * (n // 2)                         # 交替(零均值但 pstdev>0,z 可算)
    dims = [("vix", "tercile", vix), ("fund", "zscore", fund)]
    states = al.build_regime_series(rets, dims, corr_window=6, corr_min_obs=3,
                                    z_lookback=20, z_min_history=10)
    assert len(states) == n
    assert states[0]["confidence"] == 0.0                     # t=0 无任何已完结周
    # t 用 t-1:末尾高压在下一周才进入状态
    assert states[n - 1]["dims"]["vix"] == 1.0
    # t=6(src=5):vix 暖启 'mid' 可用;fund 需 j≥9;shock 需 k≥8 → 仅 1/3 维可知
    assert states[6]["confidence"] == pytest.approx(1.0 / 3.0)
    # t=10(src=9):fund/shock 均已可知 → 3/3
    assert states[10]["confidence"] == pytest.approx(1.0)


def test_build_regime_series_pit_truncation_invariance():
    n = 60
    rets = _series({"a": [0.01, -0.02, 0.015, -0.005], "b": [0.02, -0.01, 0.005, -0.015],
                    "c": [0.005, 0.01, -0.01, 0.0]}, n)
    vix = [12.0 + (i % 7) for i in range(n)]
    fund = [0.0005 * ((i % 5) - 2) for i in range(n)]
    dims = [("vix", "tercile", vix), ("fund", "zscore", fund)]
    states = al.build_regime_series(rets, dims, corr_window=8, corr_min_obs=4,
                                    z_lookback=20, z_min_history=10)
    # 追加极端未来数据(收益爆炸 + VIX 爆炸)→ 历史时点状态逐字段不变
    rets2 = {s: list(r) + [0.9, -0.9, 0.9] for s, r in rets.items()}
    dims2 = [("vix", "tercile", vix + [900.0, 900.0, 900.0]),
             ("fund", "zscore", fund + [9.0, -9.0, 9.0])]
    states2 = al.build_regime_series(rets2, dims2, corr_window=8, corr_min_obs=4,
                                     z_lookback=20, z_min_history=10)
    assert states2[:n] == states


def test_pit_correlation_shock_detects_correlation_regime_change():
    # 前 40 周对冲结构(|ρ| 低),后 12 周三条序列完全同形(ρ=1)→ 冲击
    calm_a = [0.01, -0.01, 0.02, -0.02] * 10
    calm_b = [-0.01, 0.01, -0.02, 0.02] * 10
    calm_c = [0.015, -0.005, -0.015, 0.005] * 10
    storm = [0.03, -0.04, 0.05, -0.02] * 3
    rets = {"a": calm_a + storm, "b": calm_b + storm, "c": calm_c + storm}
    assert al.pit_correlation_shock(rets, 52, window=12, margin=0.25, min_obs=4) is True
    assert al.pit_correlation_shock(rets, 30, window=12, margin=0.25, min_obs=4) is False
    assert al.pit_correlation_shock(rets, 5, window=12, margin=0.25, min_obs=4) is None


# ---------------------------------------------------------------------------
# 走前引擎 + 指标 + vol target + 判定
# ---------------------------------------------------------------------------

def _mini_grid(n=80):
    rets = {"a": [0.010, -0.008, 0.012, -0.004, 0.006, -0.002, 0.008, -0.006] * (n // 8),
            "b": [0.004, 0.002, -0.006, 0.008, -0.002, 0.006, -0.004, 0.002] * (n // 8),
            "c": [0.015, -0.010, 0.005, 0.000, -0.005, 0.010, -0.012, 0.007] * (n // 8),
            "d": [-0.002, 0.006, 0.004, -0.008, 0.010, -0.006, 0.002, 0.000] * (n // 8)}
    weeks = [f"2024-W{k + 1:02d}" for k in range(52)] + [f"2025-W{k + 1:02d}" for k in range(n - 52)]
    return rets, weeks


def test_evaluate_walk_forward_cost_math_and_lock_split():
    rets, weeks = _mini_grid()
    allocators = {"equal_weight": lambda t, prev: (al.equal_weight(sorted(rets), CONS,
                                                                   SINGLETONS)["weights"], {})}
    out = al.evaluate_walk_forward(rets, weeks, allocators, lock_week="2025-W01",
                                   start_idx=10, cost_bps=10.0, cluster_map=SINGLETONS,
                                   constraints=CONS)
    path = out["equal_weight"]["path"]
    # 等权:首周从 0 建仓换手 1.0,之后 0 → 成本只在首周
    assert path[0]["turnover"] == pytest.approx(1.0)
    assert path[0]["cost"] == pytest.approx(0.001)
    assert all(p["turnover"] == pytest.approx(0.0) for p in path[1:])
    # 锁定切分:locked 全部 ≥ 2025-W01,fit 全部 <,两窗相加 = 总路径
    lw = out["equal_weight"]["locked_weeks"]
    assert lw and all(w >= "2025-W01" for w in lw)
    assert out["equal_weight"]["locked"]["n_weeks"] == len(lw)
    assert out["equal_weight"]["fit"]["n_weeks"] + len(lw) == len(path)
    # 净收益手算:net = Σ w·r − cost
    t = 10
    expect_gross = sum(0.25 * rets[s][t] for s in sorted(rets))
    assert path[0]["gross"] == pytest.approx(expect_gross)
    assert path[0]["net"] == pytest.approx(expect_gross - 0.001)


def test_evaluate_walk_forward_feasibility_enforcement_fail_closed():
    rets, weeks = _mini_grid(24)
    bad = {"bad": lambda t, prev: ({"a": 0.9, "b": 0.1}, {})}     # 超帽
    with pytest.raises(ValueError):
        al.evaluate_walk_forward(rets, weeks, bad, lock_week="2025-W01", start_idx=2,
                                 constraints=CONS, cluster_map=SINGLETONS)


def test_allocator_pit_property_weights_unchanged_by_future():
    rets, weeks = _mini_grid()
    t = 70
    fns = {
        "iv": lambda d, k: al.inverse_vol(d, k, constraints=CONS, cluster_map=SINGLETONS)["weights"],
        "crp": lambda d, k: al.cluster_risk_parity(SINGLETONS, d, k, constraints=CONS)["weights"],
        "ms": lambda d, k: al.constrained_max_sharpe(d, k, constraints=CONS,
                                                     cluster_map=SINGLETONS)["weights"],
    }
    rets2 = {s: list(r) + [0.95, -0.95, 0.95, -0.95] for s, r in rets.items()}
    for name, fn in fns.items():
        assert fn(rets, t) == fn(rets2, t), name              # 追加极端未来 → 权重逐位不变


def test_apply_vol_target_scale_and_cost_on_scaled_deltas():
    # 恒定 |r|=0.02 交替 → trailing pstdev=0.02 → scale = (0.10/√52)/0.02 ≈ 0.6935
    path = [{"t": i, "week": f"w{i}", "weights": {"a": 0.5, "b": 0.5},
             "gross": 0.02 if i % 2 == 0 else -0.02, "cost": 0.0, "net": 0.0,
             "turnover": 0.0, "hhi": 0.5, "used_fallback": False} for i in range(40)]
    out = al.apply_vol_target(path, target_ann=0.10, lookback=26, lev_cap=2.0,
                              min_history=26, cost_bps=10.0)
    assert out[25]["vol_target_scale"] == 1.0                  # 暖机期不缩放
    s = out[30]["vol_target_scale"]
    assert s == pytest.approx((0.10 / (52 ** 0.5)) / 0.02, rel=1e-6)
    assert out[30]["weights"]["a"] == pytest.approx(0.5 * s)
    # 缩放到恒定后换手≈0;暖机结束那一跳有缩放换手成本
    assert out[26]["cost"] > 0.0
    assert out[30]["cost"] == pytest.approx(0.0, abs=1e-12)


def test_apply_vol_target_lev_cap_binds_on_calm_series():
    path = [{"t": i, "week": f"w{i}", "weights": {"a": 1.0}, "gross": 0.0001 * (1 if i % 2 else -1),
             "cost": 0.0, "net": 0.0, "turnover": 0.0, "hhi": 1.0,
             "used_fallback": False} for i in range(40)]
    out = al.apply_vol_target(path, target_ann=0.50, lookback=26, lev_cap=2.0,
                              min_history=26, cost_bps=10.0)
    assert out[39]["vol_target_scale"] == 2.0                  # 帽钉死(杠杆诊断口径)


def test_verdict_branches():
    ew = {"net_sharpe": 1.0, "max_drawdown": 0.10}
    good = {"net_sharpe": 1.3, "max_drawdown": 0.12}
    vd = al.verdict({"equal_weight": ew, "x": good})
    assert vd["verdict"] == "ADOPTED-AS-RESEARCH-FINDING" and vd["best_non_ew"] == "x"
    vd2 = al.verdict({"equal_weight": ew, "x": dict(good, net_sharpe=1.05)})
    assert vd2["verdict"] == "EW_REMAINS"                     # margin 不足
    vd3 = al.verdict({"equal_weight": ew, "x": dict(good, max_drawdown=0.20)})
    assert vd3["verdict"] == "EW_REMAINS"                     # 回撤越界
    assert vd3["maxdd_constraint_ok"] is False


def test_window_metrics_and_max_drawdown_hand_check():
    assert al.max_drawdown([0.1, -0.2, 0.05]) == pytest.approx(1 - (1.1 * 0.8) / 1.1)
    assert al.max_drawdown([]) == 0.0
    path = [{"gross": 0.02, "net": 0.018, "turnover": 0.2, "cost": 0.002,
             "hhi": 0.25, "used_fallback": True}] * 10
    m = al.window_metrics(path, list(range(10)))
    assert m["n_weeks"] == 10
    assert m["net_total_return"] == pytest.approx(1.018 ** 10 - 1)
    assert m["mean_weekly_turnover"] == pytest.approx(0.2)
    assert m["ann_turnover"] == pytest.approx(0.2 * 52)
    assert m["total_cost"] == pytest.approx(0.02)
    assert m["fallback_rate"] == pytest.approx(1.0)
    assert m["cluster_hhi_mean"] == pytest.approx(0.25)
    empty = al.window_metrics(path, [])
    assert empty["n_weeks"] == 0 and empty["net_sharpe"] is None
