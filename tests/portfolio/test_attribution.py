"""组合归因:收益归因(精确可加)、边际风险贡献(一致性)、完整报表、fail-closed。"""
import math
import pytest
from services.portfolio.risk_model import (marginal_risk_contributions, portfolio_variance,
                                            risk_contributions)
from services.portfolio.construction import return_attribution, attribution_report


def test_return_attribution_additive():
    rba = {"a": [0.01, 0.02, -0.01], "b": [0.0, 0.03, 0.01]}
    w = {"a": 0.6, "b": 0.4}
    out = return_attribution(rba, w)
    ra, rb = sum(rba["a"]), sum(rba["b"])
    assert out["contribution"]["a"] == pytest.approx(0.6 * ra)
    assert out["contribution"]["b"] == pytest.approx(0.4 * rb)
    # 精确可加:总和 == 组合区间简单收益
    port_cum = sum(0.6 * rba["a"][t] + 0.4 * rba["b"][t] for t in range(3))
    assert out["total"] == pytest.approx(port_cum)
    assert sum(out["contribution"].values()) == pytest.approx(out["total"])
    assert sum(out["pct"].values()) == pytest.approx(1.0)


def test_return_attribution_missing_series_and_zero_total():
    out = return_attribution({"a": [0.05]}, {"a": 1.0, "b": 0.5})   # b 无序列 → 贡献 0
    assert out["contribution"]["b"] == 0.0
    z = return_attribution({"a": [0.0, 0.0]}, {"a": 1.0})           # total 0 → pct 不除零
    assert z["total"] == 0.0 and z["pct"]["a"] == 0.0


def test_return_attribution_horizon():
    out = return_attribution({"a": [1.0, 2.0, 3.0]}, {"a": 1.0}, horizon=2)
    assert out["total"] == pytest.approx(5.0)                       # 末 2 期 2+3


def test_marginal_risk_contribution_consistency():
    cov = [[0.04, 0.01], [0.01, 0.09]]
    w = [0.5, 0.5]
    mrc = marginal_risk_contributions(w, cov)
    assert mrc[0] == pytest.approx(0.04 * 0.5 + 0.01 * 0.5)
    assert mrc[1] == pytest.approx(0.01 * 0.5 + 0.09 * 0.5)
    assert sum(w[i] * mrc[i] for i in range(2)) == pytest.approx(portfolio_variance(w, cov))
    rc = risk_contributions(w, cov)
    assert all(rc[i] == pytest.approx(w[i] * mrc[i]) for i in range(2))


def _series(n, seed):
    return [0.01 * math.sin(seed + 0.3 * t) for t in range(n)]


def test_attribution_report_shape_and_determinism():
    rba = {"a": _series(120, 1.0), "b": _series(120, 2.0), "c": _series(120, 3.0)}
    w = {"a": 0.5, "b": 0.3, "c": 0.2}
    out = attribution_report(rba, w)
    assert set(out) == {"return_contribution", "pct_return", "risk_contribution_pct", "marginal_risk"}
    assert set(out["risk_contribution_pct"]) == {"a", "b", "c"}
    assert sum(out["risk_contribution_pct"].values()) == pytest.approx(1.0)   # 非零方差
    assert attribution_report(rba, w) == out                       # 确定性


def test_attribution_report_fail_closed_short_overlap():
    rba = {"a": [0.01] * 5, "b": [0.02] * 5}                        # overlap 5 < min_overlap
    with pytest.raises(ValueError):
        attribution_report(rba, {"a": 0.5, "b": 0.5})
