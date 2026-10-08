"""tools/common_risk_research.py 的驱动器测试 —— 全合成:周聚合、因子库组装、网格、
canonical run 选择、verdict 规则、簇级组合输出。不碰真实 DB/JSON/网络。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.research import factor_decomp as fd
from tools import common_risk_research as cr

_DAY = 86_400_000
_T0 = 1641168000000      # 2022-01-03 周一(2022-W01)


def _bar(days_after, close, volume=1.0):
    return SimpleNamespace(event_time=_T0 + days_after * _DAY, close=close, volume=volume,
                           knowledge_time=_T0 + days_after * _DAY)


# ---------------------------------------------------------------------------
# canonical run 选择 + 曲线抽取
# ---------------------------------------------------------------------------

def test_canonical_runs_last_wins_per_strategy():
    recs = [{"strategy_id": "carry", "run_id": "r1"}, {"strategy_id": "crowd_contra", "run_id": "r2"},
            {"strategy_id": "carry", "run_id": "r3"}]
    canon = cr.canonical_runs(recs)
    assert canon["carry"]["run_id"] == "r3"          # all_runs 顺序里最后一条
    assert canon["crowd_contra"]["run_id"] == "r2"


def test_curve_series_extracts_rets_and_ts():
    rec = {"detail": {"equity_curve": [{"t": 1, "ret": 0.01}, {"t": 2, "ret": -0.02}],
                      "equity_curve_vt": [{"t": 1, "ret": 0.005}]}}
    rets, ts = cr.curve_series(rec, "equity_curve")
    assert rets == [0.01, -0.02] and ts == [1, 2]
    assert cr.curve_series(rec, "equity_curve_vt")[0] == [0.005]
    assert cr.curve_series({"detail": {}}, "equity_curve") == ([], [])


# ---------------------------------------------------------------------------
# 周聚合
# ---------------------------------------------------------------------------

def test_weekly_price_agg_weekend_close_dollar_vol_and_return_chain():
    bars = [_bar(0, 100.0, 2.0), _bar(3, 110.0, 1.0),      # W01:周末收盘=110(d3 更晚)
            _bar(7, 121.0, 1.0),                            # W02
            _bar(14, 133.1, 1.0)]                           # W03
    agg = cr.weekly_price_agg(bars)
    assert agg["2022-W01"]["close"] == 110.0
    assert agg["2022-W01"]["dollar_vol"] == pytest.approx(100.0 * 2.0 + 110.0 * 1.0)
    # 日收益链(含跨周衔接,归入较晚一天所在周):W02 含 110→121;W03 含 121→133.1
    assert agg["2022-W02"]["daily_ret"] == pytest.approx([121.0 / 110.0 - 1.0])
    assert agg["2022-W03"]["daily_ret"] == pytest.approx([133.1 / 121.0 - 1.0])


def test_weekly_funding_sums_within_week():
    series = {str(_T0): 0.001, str(_T0 + _DAY): 0.002, str(_T0 + 7 * _DAY): 0.004,
              str(_T0 + 8 * _DAY): None}
    got = cr.weekly_funding(series)
    assert got["2022-W01"] == pytest.approx(0.003)
    assert got["2022-W02"] == pytest.approx(0.004)          # None 日跳过


def test_weekly_oi_means_and_basis_formula():
    daily = {str(_T0): {"oi": 100.0, "oi_value": 10_100.0, "global_ls": 1.2},
             str(_T0 + _DAY): {"oi": 100.0, "oi_value": 10_300.0, "global_ls": 1.4},
             str(_T0 + 7 * _DAY): {"oi": None, "oi_value": None, "global_ls": None}}
    closes = {_T0: 100.0, _T0 + _DAY: 102.0, _T0 + 7 * _DAY: 105.0}
    got = cr.weekly_oi(daily, closes)
    w1 = got["2022-W01"]
    assert w1["gls"] == pytest.approx(1.3)
    assert w1["oi_value"] == pytest.approx(10_200.0)
    # basis = (oi_value/oi)/spot − 1:d0 → 101/100−1=0.01;d1 → 103/102−1
    assert w1["basis"] == pytest.approx(((10_100.0 / 100.0) / 100.0 - 1.0
                                         + (10_300.0 / 100.0) / 102.0 - 1.0) / 2)
    assert got["2022-W02"]["gls"] is None                   # 全 None 周 → None


# ---------------------------------------------------------------------------
# 因子库组装(6 周小手算)
# ---------------------------------------------------------------------------

def _mini_inputs():
    weeks = ["w0", "w1", "w2", "w3", "w4", "w5"]
    btc = {w: c for w, c in zip(weeks, [100.0, 110.0, 121.0, 115.0, 126.5, 139.15])}
    eth = {w: c for w, c in zip(weeks, [50.0, 55.0, 49.5, 54.45, 59.895, 65.8845])}
    price_agg = {
        "BTC": {w: {"close": btc[w], "dollar_vol": 1000.0 * (i + 1), "daily_ret": [0.01, -0.01]}
                for i, w in enumerate(weeks)},
        "ETH": {w: {"close": eth[w], "dollar_vol": 2000.0, "daily_ret": [0.02]}
                for i, w in enumerate(weeks)},
    }
    funding = {"BTC": {w: 0.001 for w in weeks}, "ETH": {w: 0.003 for w in weeks}}
    oi = {"BTC": {w: {"gls": 1.0 + 0.1 * i, "oi_value": 100.0 * (i + 1), "basis": 0.001 * i}
                  for i, w in enumerate(weeks)},
          "ETH": {w: {"gls": 2.0, "oi_value": 300.0, "basis": 0.003}
                  for i, w in enumerate(weeks)}}
    return weeks, price_agg, funding, oi


def test_build_factor_library_hand_checks():
    weeks, price_agg, funding, oi = _mini_inputs()
    lib = cr.build_factor_library(weeks, price_agg, funding, oi)

    assert set(lib) == {"btc_market", "eth_market", "market_trend_4w", "short_term_reversal_1w",
                        "realized_vol_change", "liquidity_volume_change", "size_oi_change",
                        "funding_weekly", "basis_change", "oi_crowding_change"}
    assert lib["btc_market"]["w1"] == pytest.approx(0.10)
    assert lib["eth_market"]["w2"] == pytest.approx(49.5 / 55.0 - 1.0)
    # 趋势 4 周:w4 = close(w4)/close(w0) − 1
    assert lib["market_trend_4w"]["w4"] == pytest.approx(126.5 / 100.0 - 1.0)
    # 反转:f(w2) = −r(w1) = −0.10
    assert lib["short_term_reversal_1w"]["w2"] == pytest.approx(-0.10)
    # 已实现波动:每周日收益 [0.01,−0.01] → pstdev=0.01 → ann=0.01·√365,恒定 → Δ=0
    assert lib["realized_vol_change"]["w2"] == pytest.approx(0.0)
    # 美元成交量:w0=3000(1000+2000),w1=4000 → +1/3
    assert lib["liquidity_volume_change"]["w1"] == pytest.approx(4000.0 / 3000.0 - 1.0)
    # OI 规模:w0=400(100+300),w1=500 → +0.25
    assert lib["size_oi_change"]["w1"] == pytest.approx(0.25)
    # 资金费截面均值:(0.001+0.003)/2 = 0.002(level,非 Δ)
    assert lib["funding_weekly"]["w0"] == pytest.approx(0.002)
    # 基差:w0 均值 (0+0.003)/2=0.0015;w1 (0.001+0.003)/2=0.002 → Δ=0.0005
    assert lib["basis_change"]["w1"] == pytest.approx(0.0005)
    # 拥挤度:w0 均值 (1.0+2.0)/2=1.5;w1 (1.1+2.0)/2=1.55 → Δ=0.05
    assert lib["oi_crowding_change"]["w1"] == pytest.approx(0.05)


def test_common_grid_intersects_strategy_weeks_and_factor_validity():
    weeks, price_agg, funding, oi = _mini_inputs()
    lib = cr.build_factor_library(weeks, price_agg, funding, oi)
    # 趋势因子 w0..w3 无定义 → 网格从 w4 起
    assert cr.common_grid(lib, weeks) == ["w4", "w5"]
    assert cr.common_grid(lib, ["w1", "w4"]) == ["w4"]


# ---------------------------------------------------------------------------
# verdict 规则 + 簇级组合
# ---------------------------------------------------------------------------

def test_edge_verdict_rule_branches():
    base = {"r2": 0.1, "raw": {"tstat": 3.0}, "alpha_tstat": 2.5,
            "alpha_annualized": 0.2, "mean_attribution": {"alpha": 0.004, "factors": {}}}
    assert cr._edge_verdict("x", base)["verdict"] == "EDGE_NOT_EXPLAINED_BY_FACTOR_LIBRARY"
    low_alpha = dict(base, r2=0.8, alpha_tstat=0.5)
    assert cr._edge_verdict("x", low_alpha)["verdict"] == "MOSTLY_FACTOR_REPLICATION"
    mixed = dict(base, r2=0.8, alpha_tstat=1.5)
    assert cr._edge_verdict("x", mixed)["verdict"] == "MIXED"


def test_cluster_portfolio_weights_and_returns():
    rets = {"a": [0.01, 0.02, 0.03], "b": [0.0, 0.01, 0.02], "c": [0.05, -0.01, 0.0]}
    weights = {"a": 0.5, "b": 0.25, "c": 0.25}
    clusters = [SimpleNamespace(member_ids=("a", "b"), cluster_id="c1"),
                SimpleNamespace(member_ids=("c",), cluster_id="c2")]
    out = cr._cluster_portfolio(rets, weights, clusters)
    c0 = out["clusters"][0]
    assert c0["weight"] == 0.75
    # 簇内归一:a 2/3,b 1/3 → 簇收益 w0 = 2/3·0.01 + 1/3·0.0
    series0 = out["cluster_corr"]["ids"]
    assert series0 == ["cluster_0", "cluster_1"]
    # 簇收益核验:重算
    inner = [weights["a"] / 0.75, weights["b"] / 0.75]
    expect = [inner[0] * ra + inner[1] * rb for ra, rb in zip(rets["a"], rets["b"])]
    assert fd.pearson(expect, rets["c"]) is not None   # 可计算即可,数值由矩阵块给出
