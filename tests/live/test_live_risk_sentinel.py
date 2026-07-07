"""风险哨兵的异常检测接线种子(Phase 5 item 2)。check_venue 本身要真连 venue(网络),不便单测;
可测种子 = `_last_equity_point`(从持久化权益曲线 jsonl 读上一轮基线,供 detect_equity_anomaly 用)。
检测器本体在 test_anomaly.py 已穷尽测试;此处只钉死"读基线"这段新的文件边界逻辑。"""


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
