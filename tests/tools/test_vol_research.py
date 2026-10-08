"""tools/vol_research.py 离线测试 —— 纯辅助函数(前向收益对齐、非重叠抽样、期限结构序列、
因子拼装)用合成数据手算验证;regime/主流程走真实库,不在此测。无网络。"""
from __future__ import annotations

import math

import pytest

from tools import vol_research as vr

_DAY = vr._DAY
_T0 = (1_700_000_000_000 // _DAY) * _DAY


def _grid(n):
    return [_T0 + i * _DAY for i in range(n)]


def test_fwd_log_return_alignment_and_guards():
    px = {_T0: 100.0, _T0 + _DAY: 110.0}
    assert vr._fwd_log_return(px, _T0, 1) == pytest.approx(math.log(1.1))
    assert vr._fwd_log_return(px, _T0 + _DAY, 1) is None        # 缺终点
    assert vr._fwd_log_return({_T0: 0.0, _T0 + _DAY: 1.0}, _T0, 1) is None  # 非正


def test_screen_factor_h7_is_non_overlapping():
    grid = _grid(40)
    factor = {t: float(i) for i, t in enumerate(grid)}
    px = {t: 100.0 * (1.01 ** i) for i, t in enumerate(grid)}   # 恒定 +1%/日
    out = vr.screen_factor(grid, factor, px, 7)
    # 非重叠起点 0,7,14,21,28(35 之后不足 7 天)→ 5 对;全部同收益同序因子 → 完全正相关
    assert out["n"] == 5
    h1 = vr.screen_factor(grid, factor, px, 1)
    assert h1["n"] == 39                                       # h1 全样本


def test_screen_factor_small_n_is_honest_none():
    grid = _grid(5)
    out = vr.screen_factor(grid, {t: 1.0 for t in grid},
                           {t: 100.0 for t in grid}, 1)
    assert out["spearman"] is None and out["n"] == 4           # n<10 → None


def test_build_term_series_picks_near_and_far():
    grid = _grid(3)
    perp = {t: 100.0 for t in grid}
    near_exp = _T0 + 30 * _DAY                                  # dte 30(>=14)
    far_exp = _T0 + 90 * _DAY                                   # dte 90(>=30+21)
    too_near = _T0 + 5 * _DAY                                   # dte 5(<14)→ 不可作近端
    dated = [
        (too_near, {t: 101.0 for t in grid}),
        (near_exp, {t: 100.0 * (1 + 0.10 * 30 / 365) for t in grid}),   # +10% ann
        (far_exp, {t: 100.0 * (1 + 0.20 * 90 / 365) for t in grid}),    # +20% ann
    ]
    out = vr.build_term_series(grid, perp, dated)
    row = out[_T0]
    assert row["near"] == near_exp and row["far"] == far_exp    # 跳过 dte 5 的
    assert row["basis"] == pytest.approx(0.10, abs=1e-9)
    assert row["slope"] == pytest.approx(0.10, abs=1e-9)


def test_build_term_series_missing_far_gives_none_slope():
    grid = _grid(2)
    perp = {t: 100.0 for t in grid}
    dated = [(_T0 + 30 * _DAY, {t: 101.0 for t in grid})]
    out = vr.build_term_series(grid, perp, dated)
    assert out[_T0]["basis"] is not None and out[_T0]["slope"] is None
    assert vr.build_term_series(grid, perp, []) == {}


def test_factor_series_shapes_and_none_propagation():
    grid = _grid(40)
    dvol = {t: 50.0 + (i % 5) for i, t in enumerate(grid)}
    px = {t: 100.0 * (1.001 ** i) for i, t in enumerate(grid)}
    factors = vr.factor_series_for_underlying(grid, dvol, px, {}, [])
    assert set(factors) == {"dvol_level", "dvol_change_7d", "iv_rv_spread_30d",
                            "dvol_zscore_30d", "fut_basis_ann", "fut_term_slope"}
    assert factors["dvol_level"][grid[0]] == pytest.approx(0.50)
    assert factors["dvol_change_7d"][grid[6]] is None           # 暖机 None
    assert factors["dvol_change_7d"][grid[7]] == pytest.approx(
        dvol[grid[7]] - dvol[grid[0]])
    assert factors["iv_rv_spread_30d"][grid[29]] is None        # RV 暖机
    assert factors["iv_rv_spread_30d"][grid[30]] is not None
    assert factors["fut_basis_ann"] == {} and factors["fut_term_slope"] == {}


def test_dvol_labels_for_weeks_uses_latest_known_and_none_before_start():
    dvol = {_T0: 40.0, _T0 + 7 * _DAY: 60.0, _T0 + 14 * _DAY: 80.0}
    weeks = [_T0 - 3 * _DAY, _T0, _T0 + 8 * _DAY, _T0 + 14 * _DAY]
    labels, vals = vr._dvol_labels_for_weeks(dvol, weeks)
    assert vals == [None, 40.0, 60.0, 80.0]                     # 开始前 None;周内取最新已知
    assert labels[0] is None and labels[1] == "low" and labels[3] == "high"
