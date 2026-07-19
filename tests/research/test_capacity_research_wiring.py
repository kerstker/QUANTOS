"""capacity research withholds estimates for extreme liquidity dispersion."""
from tools.capacity_research import _band_for

_GROSS = [0.01, 0.02, -0.005, 0.015]


def test_band_for_fail_closed_on_extreme_dispersion():
    r = _band_for("x", _GROSS, [[(0.5, 3.7e15)], [(0.5, 2.0e4)], [], []])
    assert r["data_quality"] == "EXTREME_VOLUME_DISPERSION"
    assert r["aum_low"] is None and r["participation_cap_aum"] is None


def test_band_for_ok_on_clean_units():
    r = _band_for("x", _GROSS, [[(0.5, 1e6)], [(0.5, 3e6)], [], []])
    assert r["data_quality"] == "OK"
    assert "calibrated_range" in r          # 正常路径照常出敏感性/校准区间
