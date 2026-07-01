from services.research.realism.impact import AlmgrenChrissImpact
from services.research.realism.capacity_curve import (capacity_band, capacity_curve,
                                                      critical_aum, net_return_series_at_aum,
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
