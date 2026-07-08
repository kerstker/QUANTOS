"""风险哨兵的异常检测接线种子(Phase 5 item 2)。check_venue 本身要真连 venue(网络),不便单测;
可测种子 = `_last_equity_point`(从持久化权益曲线 jsonl 读上一轮基线,供 detect_equity_anomaly 用)。
检测器本体在 test_anomaly.py 已穷尽测试;此处只钉死"读基线"这段新的文件边界逻辑。

审计 §4.4 [P1-R2](2026-07-08):VaR/CVaR/HHI 引擎已建好测过(services/risk/research_risk),但零
live 消费——本文件底部新增 `_daily_equity_closes`/`_returns_from_series`/`compute_risk_snapshot`
三个纯函数的测试,证明这三个数字现在真的算得出来、且被接进 check_venue 的只读状态输出(不进任何
门禁/breach/kill-switch 判断)。"""
import pytest


def test_last_equity_point_reads_most_recent(tmp_path):
    from tools.live_risk_sentinel import _last_equity_point, append_equity_point
    p = str(tmp_path / "eq.jsonl")
    append_equity_point(p, {"ts": 1, "equity": 100.0})
    append_equity_point(p, {"ts": 2, "equity": 120.0})
    assert _last_equity_point(p) == 120.0


def test_last_equity_point_absent_file_is_none(tmp_path):
    from tools.live_risk_sentinel import _last_equity_point
    assert _last_equity_point(str(tmp_path / "nope.jsonl")) is None


def test_last_equity_point_skips_bad_and_non_positive(tmp_path):
    from tools.live_risk_sentinel import _last_equity_point
    p = str(tmp_path / "eq.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"ts":1,"equity":100.0}\n')
        fh.write('not-json\n')
        fh.write('{"ts":2}\n')            # equity 缺失
        fh.write('{"ts":3,"equity":0}\n')  # 非正 -> 不作基线
    assert _last_equity_point(p) == 100.0  # 回退到最近一条有效正 equity


def test_last_equity_point_empty_file_is_none(tmp_path):
    from tools.live_risk_sentinel import _last_equity_point
    p = str(tmp_path / "eq.jsonl")
    open(p, "w").close()
    assert _last_equity_point(p) is None


def test_signed_equity_jump_alert_threshold_is_active():
    # 用户 2026-07-07 签署:权益异常告警阈值 15%(alert-only)。drift-lock:防止意外改回 None/漂移。
    from tools import live_risk_sentinel as s
    assert s._EQUITY_JUMP_ALERT_PCT == 0.15


def test_signed_min_margin_ratio_is_active():
    # 用户 2026-07-07 签署:维持保证金比率地板 2.0(低于即 breach → arm kill-switch)。drift-lock。
    from tools import live_risk_sentinel as s
    assert s._MIN_MARGIN_RATIO == 2.0


# ---------------------------------------------------------------------------
# 审计 §4.4 [P1-R2]:VaR/CVaR/HHI 只读展示接线
# ---------------------------------------------------------------------------

def test_daily_equity_closes_downsamples_to_last_point_per_utc_day(tmp_path):
    from tools.live_risk_sentinel import _daily_equity_closes, append_equity_point
    p = str(tmp_path / "eq.jsonl")
    day1 = 1_700_000_000_000                      # 2023-11-14T22:13:20Z
    append_equity_point(p, {"ts": day1, "equity": 100.0})
    append_equity_point(p, {"ts": day1 + 3_600_000, "equity": 105.0})   # 同一 UTC 日下一小时
    day2 = day1 + 86_400_000                       # 下一 UTC 日
    append_equity_point(p, {"ts": day2, "equity": 110.0})
    # 同一天只留当天最后一条("收盘"),不同天各留一条
    assert _daily_equity_closes(p) == [105.0, 110.0]


def test_daily_equity_closes_skips_bad_rows(tmp_path):
    from tools.live_risk_sentinel import _daily_equity_closes
    p = str(tmp_path / "eq.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"ts": 1700000000000, "equity": 100.0}\n')
        fh.write("not-json\n")
        fh.write('{"ts": 1700000001000}\n')             # equity 缺失
        fh.write('{"ts": 1700000002000, "equity": 0}\n')  # 非正
        fh.write('{"equity": 120.0}\n')                   # ts 缺失
    assert _daily_equity_closes(p) == [100.0]


def test_daily_equity_closes_absent_file_is_empty_list(tmp_path):
    from tools.live_risk_sentinel import _daily_equity_closes
    assert _daily_equity_closes(str(tmp_path / "nope.jsonl")) == []


def test_returns_from_series_basic():
    from tools.live_risk_sentinel import _returns_from_series
    out = _returns_from_series([100.0, 110.0, 99.0])
    assert out == pytest.approx([0.1, -0.1])


def test_returns_from_series_short_series_is_empty():
    from tools.live_risk_sentinel import _returns_from_series
    assert _returns_from_series([]) == []
    assert _returns_from_series([100.0]) == []


def test_compute_risk_snapshot_has_var_cvar_hhi_with_sane_values():
    # 真实调用 research_risk 的 MarketRiskEngine/ConcentrationRiskEngine(不重新发明计算逻辑)
    from tools.live_risk_sentinel import compute_risk_snapshot
    returns = [0.01, -0.02, 0.015, -0.03, 0.02, -0.01, 0.005, -0.025, 0.01, -0.015]
    weights = {"BTC": 0.4, "ETH": -0.3, "SOL": 0.2, "XRP": -0.1}
    snap = compute_risk_snapshot(returns, weights)
    assert {"var_95", "cvar_95", "hhi"} <= set(snap)
    assert snap["var_95"] >= 0.0
    assert snap["cvar_95"] >= 0.0
    assert 0.0 < snap["hhi"] <= 1.0          # 归一化权重(和=1)-> HHI 在 (0,1] 内
    assert snap["n_daily_returns"] == 10
    assert snap["n_positions"] == 4


def test_compute_risk_snapshot_empty_inputs_do_not_crash():
    # 空仓/无历史 -> 引擎自身 fail-closed 默认值(0.0),只读展示不应崩溃也不应臆造数字
    from tools.live_risk_sentinel import compute_risk_snapshot
    snap = compute_risk_snapshot([], {})
    assert snap["var_95"] == 0.0
    assert snap["cvar_95"] == 0.0
    assert snap["hhi"] == 0.0
    assert snap["n_daily_returns"] == 0
    assert snap["n_positions"] == 0


def test_compute_risk_snapshot_is_deterministic():
    from tools.live_risk_sentinel import compute_risk_snapshot
    returns = [0.01, -0.02, 0.015, -0.03, 0.02]
    weights = {"BTC": 0.6, "ETH": -0.4}
    assert compute_risk_snapshot(returns, weights) == compute_risk_snapshot(returns, weights)


def test_position_weights_from_notional_normalizes_by_gross():
    from tools.live_risk_sentinel import _position_weights_from_notional
    notional = {"BTC": 400.0, "ETH": -300.0, "SOL": 200.0, "XRP": -100.0}
    weights = _position_weights_from_notional(notional, gross_notional=1000.0)
    assert weights == pytest.approx({"BTC": 0.4, "ETH": -0.3, "SOL": 0.2, "XRP": -0.1})


def test_position_weights_from_notional_zero_gross_is_empty():
    from tools.live_risk_sentinel import _position_weights_from_notional
    assert _position_weights_from_notional({"BTC": 100.0}, gross_notional=0.0) == {}
    assert _position_weights_from_notional({}, gross_notional=0.0) == {}


# ---------------------------------------------------------------------------
# 诊断 docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md §有序阻塞清单 #4
# (2026-07-09):一次现场 bybit_demo 检查撞到 retCode 10002(时间戳偏差超 recv_window),但
# `sync_clock()` fail-soft 吞掉的失败当时完全没留下记录。`log_clock_sync_status` 把 venue
# 适配器新增的 `last_clock_sync_ok`/`last_clock_sync_error` 写进既有告警台账(`tools/alert_sink
# .emit`,不新开通道)——check_venue 本身仍不便单测(真连网络),这里直接单测这段新逻辑。
# ---------------------------------------------------------------------------

class _FakeVenue:
    def __init__(self, ok, error=None):
        self.last_clock_sync_ok = ok
        self.last_clock_sync_error = error


def test_log_clock_sync_status_emits_alert_when_sync_failed(monkeypatch):
    from tools import live_risk_sentinel as s
    captured = []
    monkeypatch.setattr(s, "emit",
                        lambda alerts, **kw: captured.append((list(alerts), kw)) or {"n": len(alerts)})
    venue = _FakeVenue(False, "TimeoutError: timed out")
    s.log_clock_sync_status("bybit_demo", venue, now=123)
    assert len(captured) == 1
    alerts, kw = captured[0]
    assert len(alerts) == 1
    assert "CLOCK SYNC FAILED" in alerts[0]
    assert "bybit_demo" in alerts[0]
    assert "TimeoutError: timed out" in alerts[0]
    assert kw["context"] == "risk_sentinel"
    assert kw["now_ms"] == 123


def test_log_clock_sync_status_no_alert_when_sync_ok(monkeypatch):
    from tools import live_risk_sentinel as s
    captured = []
    monkeypatch.setattr(s, "emit", lambda alerts, **kw: captured.append(alerts))
    s.log_clock_sync_status("binance", _FakeVenue(True), now=123)
    assert captured == []


def test_log_clock_sync_status_no_alert_when_venue_lacks_attribute(monkeypatch):
    # 防御性:venue 对象没有这个属性(理论上不会发生——本文件底部会钉死 binance/bybit 都有)时
    # 绝不误报告警(None 视为"未知"而非"失败")
    from tools import live_risk_sentinel as s
    captured = []
    monkeypatch.setattr(s, "emit", lambda alerts, **kw: captured.append(alerts))
    class _NoAttrVenue:
        pass
    s.log_clock_sync_status("binance", _NoAttrVenue(), now=123)
    assert captured == []
