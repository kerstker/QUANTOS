"""services/research/vol_factors.py 测试 —— 手算值 + 边界(短序列/dte/NaN)。纯离线。"""
from __future__ import annotations

import math

import pytest

from services.research import vol_factors as vf


# --- dvol_level / dvol_change ---

def test_dvol_level_converts_percent_to_decimal():
    assert vf.dvol_level([54.21, None, float("nan"), 38.0]) == [0.5421, None, None, 0.38]


def test_dvol_change_is_vol_points_with_warmup_none():
    out = vf.dvol_change([50.0, 52.0, 49.0, 55.0], 2)
    assert out == [None, None, -1.0, 3.0]
    assert vf.dvol_change([50.0, None, 52.0], 1) == [None, None, None]
    with pytest.raises(ValueError):
        vf.dvol_change([1.0], 0)


# --- realized_vol / iv_rv_spread(手算)---

def test_realized_vol_hand_computed():
    # closes: 100, 110, 99, 108.9 -> log rets ln(1.1), ln(0.9), ln(1.1)
    closes = [100.0, 110.0, 99.0, 108.9]
    rv = vf.realized_vol(closes, 2)
    assert rv[:2] == [None, None]
    r1, r2 = math.log(1.1), math.log(0.9)
    sd = math.sqrt(((r1 - (r1 + r2) / 2) ** 2 + (r2 - (r1 + r2) / 2) ** 2) / 2)
    assert rv[2] == pytest.approx(sd * math.sqrt(365) * 100.0)
    r3 = math.log(1.1)
    sd2 = math.sqrt(((r2 - (r2 + r3) / 2) ** 2 + (r3 - (r2 + r3) / 2) ** 2) / 2)
    assert rv[3] == pytest.approx(sd2 * math.sqrt(365) * 100.0)


def test_realized_vol_gap_yields_none_not_short_window():
    rv = vf.realized_vol([100.0, None, 99.0, 108.9], 2)
    assert rv == [None, None, None, None]      # 窗口内有缺口 → None,不退化成 1 收益


def test_iv_rv_spread_aligns_and_propagates_none():
    closes = [100.0, 110.0, 99.0]
    dvol = [50.0, 51.0, 52.0]
    spread = vf.iv_rv_spread(dvol, closes, 2)
    rv2 = vf.realized_vol(closes, 2)[2]
    assert spread[:2] == [None, None]
    assert spread[2] == pytest.approx(52.0 - rv2)
    with pytest.raises(ValueError):
        vf.iv_rv_spread([1.0], closes, 2)      # 长度不一致 fail-closed


# --- zscore ---

def test_zscore_trailing_window_hand_computed():
    out = vf.zscore([1.0, 2.0, 3.0, 4.0], 3)
    assert out[:2] == [None, None]
    mu, sd = 2.0, math.sqrt(2 / 3)             # pstdev(1,2,3)
    assert out[2] == pytest.approx((3.0 - mu) / sd)
    mu2, sd2 = 3.0, math.sqrt(2 / 3)           # pstdev(2,3,4)
    assert out[3] == pytest.approx((4.0 - mu2) / sd2)


def test_zscore_flat_window_and_nan_are_none():
    assert vf.zscore([5.0, 5.0, 5.0], 2)[-1] is None          # pstdev==0
    assert vf.zscore([1.0, float("nan"), 3.0], 2)[2] is None  # 窗口有效值不足
    with pytest.raises(ValueError):
        vf.zscore([1.0], 1)


def test_zscore_is_truncation_invariant():
    full = vf.zscore([3.0, 1.0, 4.0, 1.0, 5.0, 9.0], 3)
    part = vf.zscore([3.0, 1.0, 4.0, 1.0], 3)
    assert part == full[:4]                                   # 只用过去+当前(PIT)


# --- annualized_basis / term_structure_slope ---

def test_annualized_basis_hand_computed():
    # dated 5% 升水,90 天 -> 0.05 * 365/90
    assert vf.annualized_basis(100.0, 105.0, 90) == pytest.approx(0.05 * 365 / 90)
    assert vf.annualized_basis(105.0, 100.0, 30) == pytest.approx((100 / 105 - 1) * 365 / 30)


def test_annualized_basis_guards():
    assert vf.annualized_basis(100.0, 105.0, 0) is None       # dte=0(到期日)
    assert vf.annualized_basis(100.0, 105.0, -3) is None
    assert vf.annualized_basis(0.0, 105.0, 90) is None        # 非正价格
    assert vf.annualized_basis(100.0, -1.0, 90) is None
    assert vf.annualized_basis(float("nan"), 105.0, 90) is None
    assert vf.annualized_basis(None, 105.0, 90) is None


def test_term_structure_slope_hand_computed_and_guards():
    # near +10% ann,far +20% ann -> slope +0.10
    p, near, far = 100.0, 100.0 * (1 + 0.10 * 30 / 365), 100.0 * (1 + 0.20 * 90 / 365)
    assert vf.term_structure_slope(p, near, far, 30, 90) == pytest.approx(0.10, abs=1e-12)
    assert vf.term_structure_slope(p, near, far, 90, 30) is None    # dte 次序反了
    assert vf.term_structure_slope(p, near, far, 30, 30) is None    # dte 相等
    assert vf.term_structure_slope(p, 0.0, far, 30, 90) is None     # 非正价格


# --- bs_delta / skew_25d ---

def test_bs_delta_atm_call_near_half_put_near_minus_half():
    d_call = vf.bs_delta(100.0, 100.0, 0.25, 0.50, True)
    d_put = vf.bs_delta(100.0, 100.0, 0.25, 0.50, False)
    assert d_call == pytest.approx(0.5497, abs=1e-3)          # N(0.125)
    assert d_put == pytest.approx(d_call - 1.0)               # put-call delta 恒等
    assert 0.5 < d_call < 0.6


def test_bs_delta_guards():
    assert vf.bs_delta(100.0, 100.0, 0.0, 0.5, True) is None  # 到期
    assert vf.bs_delta(100.0, 100.0, 0.25, 0.0, True) is None  # 零 vol
    assert vf.bs_delta(0.0, 100.0, 0.25, 0.5, True) is None
    assert vf.bs_delta(100.0, -1.0, 0.25, 0.5, False) is None
    assert vf.bs_delta(float("nan"), 100.0, 0.25, 0.5, True) is None


def _entry(strike, delta, iv, is_call):
    return {"strike": strike, "delta": delta, "mark_iv": iv, "is_call": is_call}


def test_skew_25d_picks_nearest_delta_rows():
    entries = [
        _entry(90000, -0.30, 60.0, False),
        _entry(95000, -0.24, 58.0, False),    # 最接近 -0.25
        _entry(105000, 0.26, 52.0, True),     # 最接近 +0.25
        _entry(110000, 0.10, 50.0, True),
    ]
    out = vf.skew_25d(entries)
    assert out["put_strike"] == 95000 and out["call_strike"] == 105000
    assert out["skew"] == pytest.approx(58.0 - 52.0)
    assert out["n_entries"] == 4


def test_skew_25d_missing_side_is_honest_none():
    only_puts = [_entry(95000, -0.25, 58.0, False)]
    out = vf.skew_25d(only_puts)
    assert out["skew"] is None and out["put_iv"] == 58.0 and out["call_iv"] is None
    far = [_entry(95000, -0.60, 58.0, False), _entry(105000, 0.26, 52.0, True)]
    out2 = vf.skew_25d(far)
    assert out2["put_iv"] is None and out2["skew"] is None    # 超 tolerance 不硬凑
    bad = [{"strike": 1, "delta": None, "mark_iv": float("nan"), "is_call": True}]
    assert vf.skew_25d(bad)["n_entries"] == 0


# --- ts_ic_stats / per_year_ics ---

def test_ts_ic_stats_perfect_rank_and_small_n():
    pairs = [(float(i), float(i)) for i in range(12)]         # 完全单调
    out = vf.ts_ic_stats(pairs)
    assert out["spearman"] == pytest.approx(1.0) and out["pearson"] == pytest.approx(1.0)
    assert out["t_stat"] is None                              # |r|=1 无法导出 t(诚实)
    small = vf.ts_ic_stats([(1.0, 2.0), (2.0, 1.0), (3.0, 3.0)])
    assert small == {"pearson": None, "spearman": None, "t_stat": None, "n": 3}


def test_ts_ic_stats_drops_invalid_pairs_and_sign():
    pairs = [(1.0, 5.0), (2.0, 4.0), (3.0, 3.0), (4.0, 2.0), (5.0, 1.0),
             (None, 9.0), (float("nan"), 1.0), (7.0, None),
             (6.0, 0.35), (7.0, 0.45), (8.0, 0.3), (9.0, 0.2), (10.0, 0.1)]
    out = vf.ts_ic_stats(pairs)
    assert out["n"] == 10
    assert out["spearman"] is not None and out["spearman"] < -0.9
    assert out["t_stat"] is not None and out["t_stat"] < 0


def test_per_year_ics_buckets_and_min_n():
    day = 86_400_000
    t0 = 1_609_459_200_000                                   # 2021-01-01
    pairs = [(float(i % 7), float(i % 7)) for i in range(20)]
    times = [t0 + i * day * 30 for i in range(20)]            # 跨 2021-2022
    out = vf.per_year_ics(pairs, times)
    assert set(out) == {"2021", "2022"}
    assert out["2021"] is not None and out["2021"] > 0.9
    with pytest.raises(ValueError):
        vf.per_year_ics(pairs, times[:3])
