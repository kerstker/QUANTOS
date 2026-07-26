"""tools/allocator_research.py 的驱动器测试 —— 全合成:周 anchor、双指针取值、regime 输入组装、
W3 簇图加载(fail-closed)、6 配置器绑定、走前集成(确定性 + regime 回退周精确等于逆波动权重)、
单策略参考行、锁定切分边界。不碰真实 DB/JSON/网络。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.research import allocators as al
from tools import allocator_research as ar

_T0 = 1641168000000      # 2022-01-03 周一(2022-W01)
_WEEK = 7 * 86_400_000


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def test_week_anchor_ts_min_across_strategies():
    # 两条策略同周 ts 不同(周一大早 vs 稍晚)→ anchor 取跨策略 min(保守 = 决策不晚于此时)
    series = {"s1": ([0.01, 0.02], [_T0 + 7200_000, _T0 + _WEEK]),
              "s2": ([0.01, 0.02], [_T0, _T0 + _WEEK + 3600_000])}
    anchors = ar.week_anchor_ts(series)
    assert anchors["2022-W01"] == _T0
    assert anchors["2022-W02"] == _T0 + _WEEK


def test_latest_leq_bisect_semantics():
    pairs = [(100, "a"), (200, "b"), (300, "c")]
    assert ar.latest_leq(pairs, 250) == "b"
    assert ar.latest_leq(pairs, 200) == "b"                 # ≤ 含等号
    assert ar.latest_leq(pairs, 99) is None
    assert ar.latest_leq([], 100) is None


def test_build_regime_inputs_assembly():
    weeks = ["2022-W01", "2022-W02"]
    anchors = {w: _T0 + i * _WEEK for i, w in enumerate(weeks)}
    macro = SimpleNamespace(latest_as_of=lambda s, ent, ymd: SimpleNamespace(value=20.0))
    dvol_pairs = [(_T0 - 1000, 55.0)]
    fund = {"2022-W01": 0.001}                                  # W02 缺席 → None
    rv = {"2022-W01": 0.8, "2022-W02": 0.9}
    dims = ar.build_regime_inputs(weeks, anchors, macro, dvol_pairs, fund, rv)
    by_name = {n: (k, v) for n, k, v in dims}
    assert by_name["vix"] == ("tercile", [20.0, 20.0])
    assert by_name["dvol"] == ("tercile", [55.0, 55.0])
    assert by_name["funding"] == ("zscore", [0.001, None])
    assert by_name["realized_vol"] == ("zscore", [0.8, 0.9])


# ---------------------------------------------------------------------------
# W3 簇图加载(fail-closed)
# ---------------------------------------------------------------------------

def test_load_cluster_map_parses_residual_clusters(tmp_path):
    doc = {"clusters": {"residual": [{"cluster_id": "c1", "members": ["a", "b"]},
                                     {"cluster_id": "c2", "members": ["z"]}]}}
    p = tmp_path / "w3.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert ar.load_cluster_map(str(p)) == {"a": "c1", "b": "c1", "z": "c2"}


def test_load_cluster_map_fail_closed_on_missing(tmp_path):
    with pytest.raises(RuntimeError):
        ar.load_cluster_map(str(tmp_path / "nope.json"))
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"clusters": {}}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        ar.load_cluster_map(str(p))


# ---------------------------------------------------------------------------
# 绑定 + 走前集成
# ---------------------------------------------------------------------------

def _synth_grid(n=90):
    rets = {"a": [0.010, -0.008, 0.012, -0.004, 0.006, -0.002, 0.008, -0.006] * (n // 8 + 1),
            "b": [0.004, 0.002, -0.006, 0.008, -0.002, 0.006, -0.004, 0.002] * (n // 8 + 1),
            "c": [0.015, -0.010, 0.005, 0.000, -0.005, 0.010, -0.012, 0.007] * (n // 8 + 1),
            "d": [-0.002, 0.006, 0.004, -0.008, 0.010, -0.006, 0.002, 0.000] * (n // 8 + 1)}
    rets = {s: r[:n] for s, r in rets.items()}
    weeks = [f"2024-W{k + 1:02d}" for k in range(52)] + [f"2025-W{k + 1:02d}" for k in range(n - 52)]
    vix = [12.0 + (i % 5) for i in range(60)] + [35.0] * (n - 60)      # 锁定窗内高压段
    dims = [("vix", "tercile", vix)]
    return rets, weeks, dims


def test_make_allocators_keys_and_smoke():
    rets, weeks, dims = _synth_grid()
    regime = al.build_regime_series(rets, dims, corr_window=8, corr_min_obs=4)
    allocators = ar.make_allocators(rets, {i: i for i in rets}, regime)
    assert sorted(allocators) == ["cluster_risk_parity", "constrained_max_sharpe", "equal_weight",
                                  "inverse_vol", "regime_conditioned_crp", "turnover_penalized_max_sharpe"]
    prev = {i: 0.0 for i in rets}
    for name, fn in allocators.items():
        w, diag = fn(70, prev)
        assert abs(sum(w.values()) - 1.0) < 1e-6, name
        assert all(0.0 <= v <= 0.40 + 1e-9 for v in w.values()), name


def test_driver_evaluation_determinism_bit_identical():
    rets, weeks, dims = _synth_grid()
    cmap = {i: i for i in rets}
    regime = al.build_regime_series(rets, dims, corr_window=8, corr_min_obs=4)
    r1 = al.evaluate_walk_forward(rets, weeks, ar.make_allocators(rets, cmap, regime),
                                  lock_week="2025-W01", start_idx=52, cost_bps=10.0,
                                  cluster_map=cmap, constraints=ar.CONSTRAINTS)
    r2 = al.evaluate_walk_forward(rets, weeks, ar.make_allocators(rets, cmap, regime),
                                  lock_week="2025-W01", start_idx=52, cost_bps=10.0,
                                  cluster_map=cmap, constraints=ar.CONSTRAINTS)
    assert r1 == r2


def test_regime_allocator_fallback_weeks_equal_inverse_vol_weights():
    rets, weeks, dims = _synth_grid()
    cmap = {i: i for i in rets}
    # 前 60 周没有任何 regime 值(全 None)→ 早期周 confidence 0 → 精确回退
    dims_none = [("vix", "tercile", [None] * 90)]
    regime = al.build_regime_series(rets, dims_none, corr_window=8, corr_min_obs=4)
    allocators = ar.make_allocators(rets, cmap, regime)
    out = al.evaluate_walk_forward(rets, weeks, allocators, lock_week="2025-W01",
                                   start_idx=52, cost_bps=10.0, cluster_map=cmap,
                                   constraints=ar.CONSTRAINTS)
    rc_path = out["regime_conditioned_crp"]["path"]
    iv_path = out["inverse_vol"]["path"]
    fb_weeks = [k for k, p in enumerate(rc_path) if p["used_fallback"]]
    assert fb_weeks, "合成网格应至少有一个回退周(全 None 输入 → confidence 不足)"
    for k in fb_weeks:
        assert rc_path[k]["weights"] == iv_path[k]["weights"]     # 回退周逐字段精确相等


def test_single_strategy_path_and_lock_boundary():
    rets, weeks, _ = _synth_grid()
    path = ar.single_strategy_path(rets, weeks, "a", 52)
    assert len(path) == 90 - 52
    assert all(p["cost"] == 0.0 and p["turnover"] == 0.0 for p in path)
    assert path[0]["net"] == pytest.approx(rets["a"][52])
    lock_rel = ar.locked_rel_indices(weeks, 52)
    assert all(weeks[52 + k] >= "2025-W01" for k in lock_rel)
    assert 52 + lock_rel[0] == 52                                    # 边界恰为 2025-W01
