from services.research.realism.impact import AlmgrenChrissImpact
from services.research.realism.capacity_curve import (capacity_band, capacity_curve,
                                                      net_return_series_at_aum,
                                                      participation_cap_aum)

# 4 期毛收益,每期一名换手 frac=0.5,ADV=$1e6
_GROSS = [0.01, 0.02, -0.005, 0.015]
_TURN = [[(0.5, 1e6)]] * 4
_IMP = AlmgrenChrissImpact(temp_coef=10.0, perm_coef=5.0)


def test_zero_turnover_means_net_equals_gross():
    net = net_return_series_at_aum(_GROSS, [[]] * 4, 1e6, _IMP)
    assert net == _GROSS


def test_larger_aum_costs_more_net_is_lower():
    small = net_return_series_at_aum(_GROSS, _TURN, 1e5, _IMP)
    big = net_return_series_at_aum(_GROSS, _TURN, 1e8, _IMP)
    assert sum(big) < sum(small) <= sum(_GROSS)      # 冲击单调侵蚀


def test_curve_net_sharpe_monotonic_non_increasing_in_aum():
    curve = capacity_curve(_GROSS, _TURN, [1e5, 1e6, 1e7, 1e8], _IMP, ppy=52)
    sh = [pt["net_sharpe"] for pt in curve]
    assert all(sh[i] >= sh[i + 1] - 1e-9 for i in range(len(sh) - 1))


def test_capacity_band_lo_le_hi():
    band = capacity_band(_GROSS, _TURN, [1e5, 1e6, 1e7, 1e8, 1e9],
                         impact_coef_lo_hi=(5.0, 20.0), ppy=52, sharpe_floor=0.3)
    lo, hi = band["aum_low"], band["aum_high"]
    if lo is not None and hi is not None:
        assert lo <= hi                               # 激进系数 → 容量更小 = 下界


def test_participation_cap_binds_capacity():
    # frac=0.5, adv=$1e6, 10% 参与率上限 → 单名可交易 $1e5 = 0.5*AUM ⟹ AUM 上限 = $2e5
    assert participation_cap_aum(_TURN, max_participation=0.10) == 0.10 * (1e6 / 0.5)
    # 参与率上限极紧 → band 的 binding 容量应被它压到参与率上限,而非冲击容量
    band = capacity_band(_GROSS, _TURN, [1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11],
                         impact_coef_lo_hi=(5.0, 20.0), max_participation=0.10)
    assert band["aum_high"] == band["participation_cap_aum"]   # 参与率是更紧的绑定


def test_participation_cap_none_when_no_turnover():
    assert participation_cap_aum([[], []], max_participation=0.10) is None


def test_execution_horizon_scales_capacity_linearly():
    # 铺开 5 天执行 → 单日参与率摊薄 → 参与率容量 ×5
    c1 = participation_cap_aum(_TURN, max_participation=0.10, execution_horizon=1)
    c5 = participation_cap_aum(_TURN, max_participation=0.10, execution_horizon=5)
    assert c5 == c1 * 5
    # 参与率翻倍也线性
    assert participation_cap_aum(_TURN, max_participation=0.20, execution_horizon=1) == c1 * 2


# --- volume-dispersion fail-closed guard: extreme liquidity spread withholds capacity ---

_GRID = [1e5, 1e6, 1e7, 1e8, 1e9]


def test_capacity_band_default_has_no_volume_guard_and_new_key():
    band = capacity_band(_GROSS, _TURN, _GRID, (5, 20))
    assert band["provenance"] == "UNCALIBRATED_PRINCIPLED"
    assert band["data_quality"] == "OK"


def test_capacity_band_guard_passes_moderate_dispersion():
    turnover = [[(0.5, 1e6)], [(0.5, 3e6)]]
    band = capacity_band(_GROSS, turnover, _GRID, (5, 20), volume_dispersion_max_ratio=1e6)
    assert band["data_quality"] == "OK"


def test_capacity_band_guard_fail_closed_on_extreme_dispersion():
    turnover = [[(0.5, 3.7e15)], [(0.5, 2.0e4)]]
    band = capacity_band(_GROSS, turnover, _GRID, (5, 20), volume_dispersion_max_ratio=1e6)
    assert band["provenance"] == "EXTREME_VOLUME_DISPERSION"
    assert band["data_quality"] == "EXTREME_VOLUME_DISPERSION"
    assert band["aum_low"] is None and band["aum_high"] is None
    assert band["participation_cap_aum"] is None


def test_capacity_band_guard_ignores_zero_adv():
    turnover = [[(0.5, 1e6), (0.5, 0.0)], [(0.5, 3e6)]]
    band = capacity_band(_GROSS, turnover, _GRID, (5, 20), volume_dispersion_max_ratio=1e6)
    assert band["data_quality"] == "OK"
