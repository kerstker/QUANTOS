"""live/execution/market_stats.py —— 从面板 volume/close 算真实逐标的 ADV / 已实现波动喂 gate(Phase 4 子项1,
P1-5 深化)。纯函数、无 IO/网络。fail-closed:缺/非有限/窗口不足一律 None(gate 据此拒该标的,绝不 fail-open 大数)。"""
import math

from live.execution.market_stats import average_daily_volume_units, realized_volatility

_US = 1_000_000   # UNIT_SCALE


def test_adv_median_times_unit_scale():
    # 中位数 base 量 × UNIT_SCALE(缩放口径:gate 的 intent.quantity 也是缩放整数,比值才是真实参与率)
    assert average_daily_volume_units([10.0, 20.0, 30.0], unit_scale=_US, window=3) == 20.0 * _US


def test_adv_uses_trailing_window_only():
    # window=2 只取最后两个 -> median([20,30])=25
    assert average_daily_volume_units([10.0, 20.0, 30.0], unit_scale=_US, window=2) == 25.0 * _US


def test_adv_fail_closed_on_empty_or_nonpositive_or_short():
    assert average_daily_volume_units([], unit_scale=_US, window=3) is None
    assert average_daily_volume_units([0.0, -1.0], unit_scale=_US, window=3) is None   # 全≤0
    assert average_daily_volume_units([float("nan")], unit_scale=_US, window=3) is None
    assert average_daily_volume_units(None, unit_scale=_US, window=3) is None


def test_realized_vol_of_known_series():
    # 常数序列 -> 收益全 0 -> σ=0(有效,非 None)
    assert realized_volatility([100.0, 100.0, 100.0], window=3) == 0.0
    # 非平凡序列 -> 正 σ
    v = realized_volatility([100.0, 110.0, 99.0, 105.0], window=4)
    assert v is not None and v > 0.0


def test_realized_vol_annualize_scales_by_sqrt():
    daily = realized_volatility([100.0, 110.0, 99.0, 105.0], window=4, annualize=1.0)
    ann = realized_volatility([100.0, 110.0, 99.0, 105.0], window=4, annualize=365.0)
    assert ann == daily * math.sqrt(365.0)


def test_realized_vol_fail_closed_on_insufficient_or_nonfinite():
    assert realized_volatility([100.0], window=4) is None                 # <2 点
    assert realized_volatility([100.0, float("inf")], window=4) is None    # 非有限
    assert realized_volatility([], window=4) is None
