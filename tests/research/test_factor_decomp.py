"""services/research/factor_decomp.py 的纯核测试:因子数学手算核对、OLS/alpha t、残差正交性、
相关矩阵、聚类确定性 + 反链式、breadth 手算、滚动相关冲击、以及端到端「假分散」场景
(名字不同、共享未建模因子 → 原始不相关但残差完全相关,必须被聚类抓住)。全合成、确定性。"""
from __future__ import annotations

import math
import statistics

import pytest

from services.research import factor_decomp as fd


# ---------------------------------------------------------------------------
# 工具:Sylvester-Hadamard 正交列(精确正交的确定性序列,用于精确相关断言)
# ---------------------------------------------------------------------------

def _hadamard(n: int) -> list:
    h = [[1.0]]
    while len(h) < n:
        h = [r + r for r in h] + [r + [-x for x in r] for r in h]
    return [[h[i][j] for i in range(n)] for j in range(n)]   # 按列返回


_H64 = _hadamard(64)          # 64 列互相正交(第 0 列为常数列)
_F1, _G, _E3 = _H64[1], _H64[2], _H64[3]   # 三根非恒定正交列,均值均 0


def _approx(x, ref, tol=1e-9):
    return x == pytest.approx(ref, abs=tol)


# ---------------------------------------------------------------------------
# ISO 周 + 对齐
# ---------------------------------------------------------------------------

def test_iso_week_known_dates():
    assert fd.iso_week(1640995200000) == "2021-W52"   # 2022-01-01(周六)属 2021 第 52 周
    assert fd.iso_week(1641168000000) == "2022-W01"   # 2022-01-03(周一)
    assert fd.iso_week(1642636800000) == "2022-W03"   # 2022-01-20


def test_align_weekly_intersects_and_dedups_first():
    day = 86_400_000
    t0 = 1641168000000        # 2022-01-03 (W01)
    a = ([0.1, 0.2, 0.3], [t0, t0 + 7 * day, t0 + 14 * day])
    b = ([1.0, 2.0], [t0, t0 + 14 * day])                    # 缺 W02
    c = ([9.9, 5.0, 6.0], [t0, t0 + 7 * day, t0 + 14 * day])  # W01 同周单值
    out = fd.align_weekly({"a": a, "b": b, "c": c})
    assert out["weeks"] == ["2022-W01", "2022-W03"]
    assert out["data"]["a"] == [0.1, 0.3]
    assert out["data"]["b"] == [1.0, 2.0]
    # 同周多值取首个
    dup = ([1.0, 2.0], [t0, t0 + day])
    out2 = fd.align_weekly({"x": dup, "y": ([7.0], [t0])})
    assert out2["data"]["x"] == [1.0]


# ---------------------------------------------------------------------------
# 因子原语手算
# ---------------------------------------------------------------------------

def test_weekly_returns_hand_check_and_no_cross_gap_fill():
    closes = {"w0": 100.0, "w1": 110.0, "w3": 99.0}   # w2 缺
    got = fd.weekly_returns(closes, ["w0", "w1", "w2", "w3"])
    assert got == {"w1": pytest.approx(0.10)}          # w3 不跨 w2 缺口补


def test_trailing_return_hand_check():
    closes = {"w0": 100.0, "w1": 100.0, "w2": 121.0, "w3": 100.0}
    got = fd.trailing_return(closes, ["w0", "w1", "w2", "w3"], 2)
    assert got["w2"] == pytest.approx(0.21)
    assert got["w3"] == pytest.approx(0.0)
    assert "w1" not in got


def test_reversal_lag_is_negated_prior_week_return():
    closes = {"w0": 100.0, "w1": 110.0, "w2": 121.0}
    got = fd.reversal_lag(closes, ["w0", "w1", "w2"])
    # r(w1)=0.10,r(w2)=0.10;f_t=−r_{t−1} → f(w2)=−r(w1)=−0.10;f(w1) 需要 r(w0),不存在 → 缺
    assert got == {"w2": pytest.approx(-0.10)}


def test_pct_change_and_diff_maps():
    vals = {"w0": 50.0, "w1": 55.0, "w2": 44.0}
    weeks = ["w0", "w1", "w2"]
    assert fd.pct_change_map(vals, weeks) == {"w1": pytest.approx(0.10),
                                              "w2": pytest.approx(-0.20)}
    assert fd.diff_map(vals, weeks) == {"w1": pytest.approx(5.0), "w2": pytest.approx(-11.0)}


def test_annualized_vol_hand_check():
    got = fd.annualized_vol([0.01, -0.01])
    assert got == pytest.approx(0.01 * math.sqrt(365))
    assert fd.annualized_vol([0.01]) is None
    assert fd.annualized_vol([0.01, None, float("nan")]) is None


def test_cross_sectional_mean_and_sum_skip_missing():
    per_coin = {"A": {"w1": 2.0, "w2": 4.0}, "B": {"w1": 4.0}, "C": {"w2": None}}
    weeks = ["w1", "w2", "w3"]
    assert fd.cross_sectional_mean(per_coin, weeks) == {"w1": pytest.approx(3.0),
                                                        "w2": pytest.approx(4.0)}
    assert fd.cross_sectional_sum(per_coin, weeks) == {"w1": pytest.approx(6.0),
                                                       "w2": pytest.approx(4.0)}


# ---------------------------------------------------------------------------
# OLS 因子模型
# ---------------------------------------------------------------------------

def test_fit_factor_model_exact_recovery():
    x1 = [float(v) for v in _F1]
    x2 = [float(v) for v in _G]
    y = [1.5 + 2.0 * a - 3.0 * b for a, b in zip(x1, x2)]
    m = fd.fit_factor_model(y, [x1, x2], ["f1", "f2"])
    assert m.intercept == pytest.approx(1.5)
    assert m.betas == (pytest.approx(2.0), pytest.approx(-3.0))
    assert m.r2 == pytest.approx(1.0)
    assert max(abs(r) for r in m.residual) < 1e-9
    # 近完美拟合 → σ²≈0 → 标准误≈0 → t 统计爆炸或 None(不编造 ∞;fp 残差 rarely 精确 0)
    assert all(t is None or abs(t) > 1e6 for t in m.tstats)


def test_fit_factor_model_residual_orthogonal_to_factors_and_mean_zero():
    x1 = [float(v) for v in _F1]
    x2 = [float(v) for v in _G]
    y = [0.3 + 0.8 * a + 0.2 * b + 0.1 * c for a, b, c in zip(x1, x2, _E3)]  # 含未建模分量
    m = fd.fit_factor_model(y, [x1, x2], ["f1", "f2"])
    assert abs(sum(m.residual)) < 1e-9
    for col in (x1, x2):
        assert abs(sum(r * x for r, x in zip(m.residual, col))) < 1e-8
    assert m.r2 == pytest.approx((0.8 ** 2 + 0.2 ** 2) / (0.8 ** 2 + 0.2 ** 2 + 0.1 ** 2), abs=1e-9)


def test_fit_factor_model_alpha_tstat_matches_manual_demeaned_case():
    # 因子零均值 → 截距=mean(y),其 t = mean(y)/√(σ²/n)(单变量手算可核)
    x = [float(v) for v in _F1]                          # 均值 0
    eps = [0.5 * float(v) for v in _G]                   # 与 x 正交
    y = [0.02 + 1.0 * xi + e for xi, e in zip(x, eps)]
    m = fd.fit_factor_model(y, [x], ["f"])
    n = len(y)
    sigma2 = sum(r * r for r in m.residual) / (n - 2)
    expected_t = statistics.fmean(y) / math.sqrt(sigma2 / n)
    assert m.intercept == pytest.approx(0.02)
    assert m.tstats[0] == pytest.approx(expected_t)
    # beta 的 t:se=√(σ²/Σx²)
    assert m.tstats[1] == pytest.approx(1.0 / math.sqrt(sigma2 / sum(xi * xi for xi in x)))


def test_fit_factor_model_fail_closed():
    with pytest.raises(ValueError):
        fd.fit_factor_model([1.0, 2.0], [[1.0, 2.0]], ["f"])           # n<=k+1
    with pytest.raises(ValueError):
        fd.fit_factor_model([1.0] * 10, [[1.0] * 9], ["f"])            # 形状不齐
    with pytest.raises(ValueError):
        fd.fit_factor_model([1.0] * 5, [[float("nan")] * 5], ["f"])    # 非有限 fail-closed


def test_standardized_betas_and_mean_attribution_identity():
    x1 = [float(v) for v in _F1]
    y = [0.01 + 2.0 * xi + 0.3 * e for xi, e in zip(x1, _G)]
    m = fd.fit_factor_model(y, [x1], ["f1"])
    sb = fd.standardized_betas(m, [x1], y)
    assert sb["f1"] == pytest.approx(2.0 * statistics.pstdev(x1) / statistics.pstdev(y))
    attr = fd.mean_attribution(m, [x1], y)
    assert attr["alpha"] + sum(attr["factors"].values()) == pytest.approx(attr["raw_mean"])
    # 因子零均值 → 全部均值归因都进 alpha
    assert attr["factors"]["f1"] == pytest.approx(0.0)
    assert attr["alpha"] == pytest.approx(attr["raw_mean"])


def test_mean_tstat():
    assert fd.mean_tstat([]) == {"n": 0, "mean_weekly": None, "annualized": None, "tstat": None}
    assert fd.mean_tstat([0.01] * 10)["tstat"] is None                 # 零方差 → None
    got = fd.mean_tstat([0.01, -0.01, 0.02, 0.0])
    assert got["n"] == 4
    assert got["annualized"] == pytest.approx(got["mean_weekly"] * 52)


# ---------------------------------------------------------------------------
# 相关矩阵 + 聚类
# ---------------------------------------------------------------------------

def test_pearson_and_correlation_matrix():
    a, b = [1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]
    assert fd.pearson(a, b) == pytest.approx(1.0)
    assert fd.pearson(a, [-v for v in b]) == pytest.approx(-1.0)
    assert fd.pearson([1.0] * 4, b) is None                            # 零方差 → None
    mat = fd.correlation_matrix({"b": b, "a": a, "c": [1.0, -1.0, 1.0, -1.0]})
    assert mat["ids"] == ["a", "b", "c"]
    assert mat["matrix"][0][1] == pytest.approx(1.0)
    assert mat["matrix"][1][0] == mat["matrix"][0][1]                  # 对称
    assert all(mat["matrix"][i][i] == 1.0 for i in range(3))


def test_agglomerative_cluster_deterministic_and_groups_correlated_pair():
    a = [float(v) for v in _F1]
    b = [0.9 * v + 0.1 * w for v, w in zip(_F1, _G)]   # corr(a,b)=0.9
    c = [float(v) for v in _E3]                        # 与两者正交
    series = {"a": a, "b": b, "c": c}
    c1 = fd.agglomerative_cluster(series, rho_cut=0.5, min_overlap=2)
    c2 = fd.agglomerative_cluster(series, rho_cut=0.5, min_overlap=2)
    assert [c.cluster_id for c in c1] == [c.cluster_id for c in c2]    # 同输入→同簇
    assert sorted(tuple(sorted(cc.member_ids)) for cc in c1) == [("a", "b"), ("c",)]
    # intra_corr 与本模块 correlation_matrix 一致(cluster_alphas 内部 Pearson 同式)
    mat = fd.correlation_matrix(series)["matrix"]
    ab = mat[0][1]
    cluster_ab = next(cc for cc in c1 if len(cc.member_ids) == 2)
    assert cluster_ab.intra_corr == pytest.approx(abs(ab))


def test_agglomerative_cluster_complete_linkage_blocks_chaining():
    # A=h2;B=0.8h2+0.6h3 → AB=0.8;C=h3 → BC=0.6,AC=0。单链会经 B 把 C 桥接进来(0.6≥0.5),
    # complete-linkage(min cross-pair=min(0, 0.6)=0)必须拒绝 → {A,B},{C}。
    a = [float(v) for v in _F1]
    b = [0.8 * v + 0.6 * w for v, w in zip(_F1, _G)]
    c = [float(v) for v in _G]
    clusters = fd.agglomerative_cluster({"a": a, "b": b, "c": c}, rho_cut=0.5, min_overlap=2)
    assert sorted(tuple(sorted(cc.member_ids)) for cc in clusters) == [("a", "b"), ("c",)]


def test_agglomerative_cluster_negative_corr_counts_as_same_exposure():
    a = [float(v) for v in _F1]
    d = [-float(v) for v in _F1]                       # corr = −1,|ρ|=1 → 同一底层敞口
    clusters = fd.agglomerative_cluster({"a": a, "d": d}, rho_cut=0.5, min_overlap=2)
    assert len(clusters) == 1
    assert sorted(clusters[0].member_ids) == ["a", "d"]


# ---------------------------------------------------------------------------
# effective breadth
# ---------------------------------------------------------------------------

def test_effective_breadth_hand_checks():
    w2 = {"a": 0.5, "b": 0.5}
    assert fd.effective_breadth(w2, [("a", "b")])["effective_breadth"] == pytest.approx(1.0)
    assert fd.effective_breadth(w2, [("a",), ("b",)])["effective_breadth"] == pytest.approx(2.0)
    w = {"a": 0.75, "b": 0.25}
    got = fd.effective_breadth(w, [("a",), ("b",)])
    assert got["effective_breadth"] == pytest.approx(1.0 / (0.75 ** 2 + 0.25 ** 2))
    assert got["cluster_weights"] == [pytest.approx(0.75), pytest.approx(0.25)]
    w4 = {s: 0.25 for s in "abcd"}
    assert fd.effective_breadth(w4, [(s,) for s in "abcd"])["effective_breadth"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# 滚动相关冲击
# ---------------------------------------------------------------------------

def test_rolling_mean_abs_corr_hand_check():
    series = {"a": [1.0, 2.0, 1.0, 2.0], "b": [1.0, 2.0, 1.0, 2.0]}
    got = fd.rolling_mean_abs_corr(series, 2)
    assert got[0] is None
    assert all(v == pytest.approx(1.0) for v in got[1:])


def test_correlation_shock_flags_regime_shift():
    # 前 30 周不相关(F1 vs G),后 30 周完全同向 → 后段 rolling mean|ρ| 冲过阈值
    a = [float(v) for v in _H64[1][:30]] + [float(v) for v in _H64[1][30:60]]
    b = [float(v) for v in _H64[2][:30]] + [float(v) for v in _H64[1][30:60]]
    weeks = [f"w{i}" for i in range(60)]
    out = fd.correlation_shock({"a": a, "b": b}, weeks, window=26, margin=0.25)
    assert out["status"] == "OK"
    assert out["n_shock_weeks"] > 0
    assert out["in_shock_now"] is True            # 末段完全相关
    assert out["threshold"] == pytest.approx(out["full_sample_mean_abs_corr"] + 0.25)


def test_correlation_shock_quiet_when_correlation_constant():
    a = [float(v) for v in _F1]
    b = [0.9 * v + 0.1 * w for v, w in zip(_F1, _G)]
    weeks = [f"w{i}" for i in range(64)]
    out = fd.correlation_shock({"a": a, "b": b}, weeks, window=26, margin=0.25)
    assert out["n_shock_weeks"] == 0
    assert out["in_shock_now"] is False


def test_correlation_shock_insufficient_data():
    out = fd.correlation_shock({"a": [0.1, 0.2], "b": [0.1, 0.2]}, ["w0", "w1"], window=26)
    assert out["status"] == "INSUFFICIENT_DATA"
    assert out["in_shock_now"] is False


# ---------------------------------------------------------------------------
# 端到端:假分散场景(§7 的存在理由)
# ---------------------------------------------------------------------------

def test_decompose_catches_hidden_shared_factor_fake_diversification():
    # s2 = F1 + G,s3 = −F1 + G:G 是未建模公共因子 → raw corr(s2,s3)=0(看似分散),
    # 残差都=G → 残差 corr=1(同一底层敞口)。s1 = E3 与两者全正交(纯独立 alpha)。
    f1 = [float(v) for v in _F1]
    g = [float(v) for v in _G]
    rets = {"s1_orthogonal": [float(v) for v in _E3],
            "s2": [a + b for a, b in zip(f1, g)],
            "s3": [-a + b for a, b in zip(f1, g)]}
    out = fd.decompose_strategies(rets, {"market": f1}, min_overlap=2, shock_window=8)

    # 因子模型手算:s2/s3 的 beta=±1、R²=0.5(var 平分)、alpha≈0;s1 与市场正交 → R²=0
    assert out["strategies"]["s2"]["betas"]["market"] == pytest.approx(1.0)
    assert out["strategies"]["s3"]["betas"]["market"] == pytest.approx(-1.0)
    assert out["strategies"]["s2"]["r2"] == pytest.approx(0.5)
    assert out["strategies"]["s1_orthogonal"]["r2"] == pytest.approx(0.0)
    assert abs(out["strategies"]["s2"]["alpha_weekly"]) < 1e-12

    # 原始相关:s2·s3 = 0(假分散);残差相关:s2·s3 = 1(被抓住)
    ids = out["raw_corr"]["ids"]
    i2, i3 = ids.index("s2"), ids.index("s3")
    assert out["raw_corr"]["matrix"][i2][i3] == pytest.approx(0.0, abs=1e-12)
    assert out["residual_corr"]["matrix"][i2][i3] == pytest.approx(1.0)

    raw_clusters = sorted(tuple(sorted(c.member_ids)) for c in out["clusters_raw"])
    res_clusters = sorted(tuple(sorted(c.member_ids)) for c in out["clusters_residual"])
    assert raw_clusters == [("s1_orthogonal",), ("s2",), ("s3",)]        # 原始口径被骗过
    assert res_clusters == [("s1_orthogonal",), ("s2", "s3")]            # 残差口径抓住

    # breadth:等权 1/3 → raw 3.0(假);残差 1/((1/3)²+(2/3)²)=1.8(真)
    w = {s: 1.0 / 3 for s in rets}
    assert fd.effective_breadth(w, out["clusters_raw"])["effective_breadth"] == pytest.approx(3.0)
    assert fd.effective_breadth(w, out["clusters_residual"])["effective_breadth"] == pytest.approx(1.8)


def test_decompose_fail_closed_on_bad_input():
    with pytest.raises(ValueError):
        fd.decompose_strategies({"a": [0.1, float("nan")]}, {"f": [0.1, 0.2]})
    with pytest.raises(ValueError):
        fd.decompose_strategies({"a": [0.1, 0.2], "b": [0.1]}, {"f": [0.1, 0.2]})
    with pytest.raises(ValueError):
        fd.decompose_strategies({"a": [0.1, 0.2]}, {"f": [0.1]})
    with pytest.raises(ValueError):
        fd.decompose_strategies({}, {"f": [0.1]})
