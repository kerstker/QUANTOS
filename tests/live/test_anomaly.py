"""Phase 5 item 2 (dim10):盘中权益异常检测器。纯函数、fail-closed、默认惰性(max_jump_pct None ->
生产字节等价,直到用户签一个阈值)。满足验收"实时监控抓到注入的异常"(在 mechanism 层:注入一个
合成跳变 + 传入显式阈值 -> anomalous=True)。异常只**加告警**,绝不 arm kill-switch / 减仓 / 碰 order 路径。"""
import pytest

from live.execution.anomaly import AnomalyReport, detect_equity_anomaly


def test_disabled_by_default_is_inert():
    # max_jump_pct None(默认)-> 无论跳变多大都不告警(生产路径字节等价,直到用户签数字)
    r = detect_equity_anomaly(prior_equity=100.0, current_equity=10.0)  # -90% 但检测器关
    assert isinstance(r, AnomalyReport)
    assert r.anomalous is False and r.reasons == ()


def test_missing_history_ignored_when_disabled():
    r = detect_equity_anomaly(prior_equity=None, current_equity=100.0)
    assert r.anomalous is False        # 关闭时连缺历史都惰性(签数字前零告警)


def test_equity_jump_down_detected_when_enabled():
    r = detect_equity_anomaly(prior_equity=100.0, current_equity=66.0, max_jump_pct=0.15)
    assert r.anomalous is True
    assert any("equity_jump" in x for x in r.reasons)
    assert r.jump_pct == pytest.approx(-0.34)


def test_equity_jump_up_detected_when_enabled():
    r = detect_equity_anomaly(prior_equity=100.0, current_equity=140.0, max_jump_pct=0.15)
    assert r.anomalous is True
    assert r.jump_pct == pytest.approx(0.40)


def test_no_anomaly_when_within_threshold():
    r = detect_equity_anomaly(prior_equity=100.0, current_equity=105.0, max_jump_pct=0.15)
    assert r.anomalous is False and r.reasons == ()
    assert r.jump_pct == pytest.approx(0.05)


def test_fail_closed_on_missing_or_bad_history_when_enabled():
    for prior in (None, 0.0, -5.0, float("nan"), float("inf")):
        r = detect_equity_anomaly(prior_equity=prior, current_equity=100.0, max_jump_pct=0.15)
        assert r.anomalous is True
        assert "insufficient_history" in r.reasons


def test_fail_closed_on_invalid_current_when_enabled():
    r = detect_equity_anomaly(prior_equity=100.0, current_equity=float("nan"), max_jump_pct=0.15)
    assert r.anomalous is True
    assert "invalid_current_equity" in r.reasons
