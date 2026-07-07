"""capacity_research 驱动器把 volume-units fail-closed 门真正接进 engine(P2-2)。证明:混单位的
turnover 经 _band_for 会 fail-closed 返回 UNRELIABLE(即驱动器确实向 capacity_band 传了 volume_units_max_ratio)。"""
from tools.capacity_research import _band_for

_GROSS = [0.01, 0.02, -0.005, 0.015]


def test_band_for_fail_closed_on_mixed_units():
    # BTC $3.7Q vs EOS $20k 混单位 -> 驱动器拒绝出容量数
    r = _band_for("x", _GROSS, [[(0.5, 3.7e15)], [(0.5, 2.0e4)], [], []])
    assert r["data_quality"] == "UNRELIABLE_VOLUME_UNITS"
    assert r["aum_low"] is None and r["participation_cap_aum"] is None


def test_band_for_ok_on_clean_units():
    r = _band_for("x", _GROSS, [[(0.5, 1e6)], [(0.5, 3e6)], [], []])
    assert r["data_quality"] == "OK"
    assert "calibrated_range" in r          # 正常路径照常出敏感性/校准区间
