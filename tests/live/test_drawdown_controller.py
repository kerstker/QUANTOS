"""W6 动态回撤状态机 + 可解释风险乘数(spec §10)测试。

两层(与仓库既有惯例一致):
1. 纯核 live/execution/drawdown_controller.py —— 全部状态迁移(含 RECOVERY 双门:
   只时间不够 / 只表现不够 / 两者兼具才恢复)、相关冲击/数据异常/场地事故覆盖、
   乘数 min 组合 + UNKNOWN 保守默认 + explain 完整性、快照序列化。
2. 驱动器接线(tools/live_track_advance.py 的可测桥 drawdown_control_decision /
   apply_risk_multiplier / _equity_window_from_history)——main() 是网络胶水不单测
   (tests/live/test_live_track_advance.py 顶部 docstring 惯例),接线顺序用源码钉子锁。
"""
import json
import os

import pytest

from live.execution.drawdown_controller import (
    ComponentReading, DEGRADATION_ARTIFACT_MAX_AGE_MS, DrawdownController, DrawdownState,
    DrawdownThresholds, _degradation_band_value, combine_risk_multiplier,
    consecutive_below_caution_count,
    correlation_component, correlation_shock_from_artifact,
    degradation_component_from_artifact, drawdown_component,
    observations_since_new_low, snapshot_from_dict, snapshot_to_dict, state_ceiling,
    unknown_component, venue_health_component)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC = open(os.path.join(_ROOT, "tools", "live_track_advance.py"), encoding="utf-8").read()

PEAK = 100.0


def _ctrl(snapshot=None, thresholds=None):
    return DrawdownController(snapshot=snapshot, thresholds=thresholds)


# ---------------------------------------------------------------------------
# 状态机:回撤阈值档位
# ---------------------------------------------------------------------------

def test_normal_stays_normal_below_caution():
    d = _ctrl().update(equity=96.0, peak_equity=PEAK, now=1)     # dd 4% < 5%
    assert d.state is DrawdownState.NORMAL
    assert d.state_ceiling == 1.0
    assert d.snapshot.state is DrawdownState.NORMAL


def test_caution_at_first_threshold():
    d = _ctrl().update(equity=95.0, peak_equity=PEAK, now=1)     # dd 5% = caution
    assert d.state is DrawdownState.CAUTION
    assert d.state_ceiling == 0.5
    assert "caution" in d.reason


def test_defensive_at_second_threshold():
    c = _ctrl()
    c.update(equity=94.0, peak_equity=PEAK, now=1)               # CAUTION
    d = c.update(equity=90.0, peak_equity=PEAK, now=2)           # dd 10% -> DEFENSIVE
    assert d.state is DrawdownState.DEFENSIVE
    assert d.state_ceiling == 0.25


def test_halt_at_hard_threshold():
    d = _ctrl().update(equity=80.0, peak_equity=PEAK, now=1)     # dd 20% -> HALT
    assert d.state is DrawdownState.HALT
    assert d.state_ceiling == 0.0


def test_escalation_never_needs_gates():
    # 升级(CLEAR 态直接跌入 DEFENSIVE/HALT)不走恢复双门,立即生效
    c = _ctrl()
    d = c.update(equity=85.0, peak_equity=PEAK, now=1)
    assert d.state is DrawdownState.DEFENSIVE
    d = c.update(equity=75.0, peak_equity=PEAK, now=2)
    assert d.state is DrawdownState.HALT


# ---------------------------------------------------------------------------
# 覆盖输入:数据异常 / 场地事故 / 相关冲击
# ---------------------------------------------------------------------------

def test_data_anomaly_halts_regardless_of_drawdown():
    d = _ctrl().update(equity=99.9, peak_equity=PEAK, now=1, data_anomaly=True)
    assert d.state is DrawdownState.HALT
    assert "data_anomaly" in d.reason


def test_venue_incident_halts_regardless_of_drawdown():
    d = _ctrl().update(equity=99.9, peak_equity=PEAK, now=1, venue_incident=True)
    assert d.state is DrawdownState.HALT
    assert "venue_incident" in d.reason


def test_correlation_shock_forces_defensive():
    d = _ctrl().update(equity=99.0, peak_equity=PEAK, now=1, correlation_shock=True)
    assert d.state is DrawdownState.DEFENSIVE
    assert "correlation_shock" in d.reason


def test_correlation_shock_none_is_no_opinion_not_shock():
    # W3 制品未落地 -> None:不能当成"有冲击"(那会天天 DEFENSIVE),也不能当"无冲击"
    # 放行满配(分量 UNKNOWN 保守默认,见下面乘数测试)
    d = _ctrl().update(equity=99.0, peak_equity=PEAK, now=1, correlation_shock=None)
    assert d.state is DrawdownState.NORMAL


def test_correlation_shock_does_not_weaken_halt():
    d = _ctrl().update(equity=70.0, peak_equity=PEAK, now=1, correlation_shock=True)
    assert d.state is DrawdownState.HALT          # 升级优先:shock 不会把 HALT 降成 DEFENSIVE


def test_invalid_inputs_fail_closed_halt():
    for bad_equity in (0.0, -5.0, float("nan"), float("inf"), None, "x"):
        assert _ctrl().update(equity=bad_equity, peak_equity=PEAK, now=1).state \
            is DrawdownState.HALT
    assert _ctrl().update(equity=99.0, peak_equity=0.0, now=1).state is DrawdownState.HALT
    assert _ctrl().update(equity=99.0, peak_equity=float("nan"), now=1).state \
        is DrawdownState.HALT


# ---------------------------------------------------------------------------
# RECOVERY 双门:时间 AND 表现,缺一不可
# ---------------------------------------------------------------------------

def _drive_to_caution(c):
    c.update(equity=93.0, peak_equity=PEAK, now=1)               # dd 7% -> CAUTION


def test_recovery_performance_gate_alone_is_insufficient():
    # 表现门满足(尾部 3 次观测回撤都 < caution),但稳定门不满足(最近 M=3 次内有创新低)
    c = _ctrl()
    _drive_to_caution(c)
    # 窗口相对口径:谷底 93 已滑出尾窗(窗口参照点 96)。series=[96,97,95.5,97]:尾部
    # 96/95.5/97 三次 dd 全 <5% -> gate1 过;但 95.5<96 是窗口内严格新低、距今仅 1 次
    # 观测(< M=3)-> gate2 不过 -> 停在 CAUTION,不进 RECOVERY
    d = c.update(equity=97.0, peak_equity=PEAK, now=2, equity_window=[96.0, 97.0, 95.5])
    assert d.state is DrawdownState.CAUTION
    assert "recovery_gates_pending" in d.reason
    assert "stabilization_gate=False" in d.reason


def test_recovery_time_gate_alone_is_insufficient():
    # 稳定门满足(窗口参照点 93 之后无新低,距今 ≥3 次),但表现门不满足(尾部 3 次里
    # 94.5 那次回撤 5.5% 仍 >= caution,连续计数被打断 -> 只有 1/3)
    c = _ctrl()
    _drive_to_caution(c)
    d = c.update(equity=99.0, peak_equity=PEAK, now=2, equity_window=[93.0, 98.0, 94.5])
    assert d.state is DrawdownState.CAUTION
    assert "recovery_gates_pending" in d.reason
    assert "performance_gate=False" in d.reason


def test_recovery_requires_both_gates_then_recovers():
    c = _ctrl()
    _drive_to_caution(c)
    d = c.update(equity=97.0, peak_equity=PEAK, now=2, equity_window=[93.0])
    assert d.state is DrawdownState.CAUTION                      # 1/3 below
    d = c.update(equity=98.0, peak_equity=PEAK, now=3, equity_window=[93.0, 97.0])
    assert d.state is DrawdownState.CAUTION                      # 2/3 below
    d = c.update(equity=98.5, peak_equity=PEAK, now=4, equity_window=[93.0, 97.0, 98.0])
    # gate1: 98.5/98/97 连续 3 次 dd<5% ✓;gate2: 93 之后无新低(97>93,98>97,98.5>98)✓
    assert d.state is DrawdownState.RECOVERY
    assert d.snapshot.observations_in_recovery == 1


def test_recovery_insufficient_history_cannot_pass_gates():
    # 窗口太短(不足 K/M 次观测)-> fail-closed:恢复被挡,停在 CAUTION
    c = _ctrl()
    _drive_to_caution(c)
    d = c.update(equity=99.0, peak_equity=PEAK, now=2, equity_window=[])  # series 只有本次
    assert d.state is DrawdownState.CAUTION
    assert "recovery_gates_pending" in d.reason


def test_recovery_slow_ramp_no_instant_full_relever():
    th = DrawdownThresholds()
    c = _ctrl()
    _drive_to_caution(c)
    window = [93.0]
    d = None
    # 连续爬升:进入 RECOVERY 后 ramp 0.1 -> 0.5 封顶,第 6 次观测才回 NORMAL(满杠杆)
    ceilings = []
    for i, eq in enumerate([97.0, 98.0, 98.5, 99.0, 99.2, 99.4, 99.6, 99.8]):
        d = c.update(equity=eq, peak_equity=PEAK, now=2 + i, equity_window=window)
        window.append(eq)
        ceilings.append((d.state, d.state_ceiling))
    assert ceilings[0] == (DrawdownState.CAUTION, 0.5)           # 1/3
    assert ceilings[1] == (DrawdownState.CAUTION, 0.5)           # 2/3
    assert ceilings[2][0] is DrawdownState.RECOVERY
    assert ceilings[2][1] == pytest.approx(0.1)                  # ramp 起步,绝不瞬时满杠杆
    assert ceilings[3][1] == pytest.approx(0.2)
    assert ceilings[4][1] == pytest.approx(0.3)
    assert ceilings[5][1] == pytest.approx(0.4)
    assert ceilings[6][1] == pytest.approx(th.recovery_max_ceiling)   # 0.5 封顶
    assert ceilings[7] == (DrawdownState.NORMAL, 1.0)            # 双门持续成立 -> 满杠杆恢复


def test_new_low_knocks_recovery_back_to_caution():
    c = _ctrl()
    _drive_to_caution(c)
    window = [93.0]
    for i, eq in enumerate([97.0, 98.0, 98.5]):
        d = c.update(equity=eq, peak_equity=PEAK, now=2 + i, equity_window=window)
        window.append(eq)
    assert d.state is DrawdownState.RECOVERY
    # 回撤没 spike(dd 4.5% < caution),但相对窗口创新低(95.5 < 96)-> 稳定门立刻
    # 不满足 -> 退回 CAUTION,ramp 进度清零(不依赖 dd 越限的纯稳定门路径)
    d = c.update(equity=95.5, peak_equity=PEAK, now=5,
                 equity_window=[96.0, 97.0, 98.0, 98.5])
    assert d.state is DrawdownState.CAUTION
    assert "stabilization_gate=False" in d.reason
    assert d.snapshot.observations_in_recovery == 0


def test_drawdown_spike_during_recovery_escalates_immediately():
    c = _ctrl()
    _drive_to_caution(c)
    window = [93.0]
    for i, eq in enumerate([97.0, 98.0, 98.5]):
        d = c.update(equity=eq, peak_equity=PEAK, now=2 + i, equity_window=window)
        window.append(eq)
    assert d.state is DrawdownState.RECOVERY
    d = c.update(equity=89.0, peak_equity=PEAK, now=5, equity_window=window)   # dd 11%
    assert d.state is DrawdownState.DEFENSIVE                    # 升级优先于恢复


def test_halt_never_returns_directly_to_normal():
    c = _ctrl()
    d = c.update(equity=75.0, peak_equity=PEAK, now=1)           # HALT (dd 25%)
    assert d.state is DrawdownState.HALT
    d = c.update(equity=99.0, peak_equity=PEAK, now=2)           # 回撤消失
    assert d.state is DrawdownState.CAUTION                      # 不闩锁,但绝不直接 NORMAL
    # 仍需双门 + ramp 爬回
    window = [99.0]
    for i, eq in enumerate([99.1, 99.2]):
        d = c.update(equity=eq, peak_equity=PEAK, now=3 + i, equity_window=window)
        window.append(eq)
    assert d.state is DrawdownState.RECOVERY
    assert d.state_ceiling == pytest.approx(0.1)


def test_halt_clears_when_anomaly_clears_but_keeps_dd_based_state():
    c = _ctrl()
    c.update(equity=94.0, peak_equity=PEAK, now=1, data_anomaly=True)   # HALT(异常覆盖)
    d = c.update(equity=94.0, peak_equity=PEAK, now=2, data_anomaly=False)
    assert d.state is DrawdownState.CAUTION                      # 回到 dd 隐含档位


# ---------------------------------------------------------------------------
# 窗口辅助函数(双门的构件)
# ---------------------------------------------------------------------------

def test_consecutive_below_caution_counts_from_tail_and_breaks():
    assert consecutive_below_caution_count([96.0, 97.0, 98.0], peak_equity=PEAK,
                                           caution_drawdown=0.05) == 3
    # 94(dd 6%)打断:从尾部数只有 2 次
    assert consecutive_below_caution_count([96.0, 94.0, 97.0, 98.0], peak_equity=PEAK,
                                           caution_drawdown=0.05) == 2


def test_observations_since_new_low_strict_less_than():
    assert observations_since_new_low([100.0, 94.0, 88.0]) == 0          # 本次就是新低
    assert observations_since_new_low([100.0, 88.0, 95.0]) == 1
    assert observations_since_new_low([100.0, 88.0, 95.0, 96.0, 97.0]) == 3
    assert observations_since_new_low([100.0, 100.0, 100.0]) == 3        # 平盘不创新低
    assert observations_since_new_low([90.0, 95.0, 96.0]) == 3           # 窗口前低不可知


# ---------------------------------------------------------------------------
# 快照序列化
# ---------------------------------------------------------------------------

def test_snapshot_round_trip():
    snap = _ctrl().update(equity=94.0, peak_equity=PEAK, now=123).snapshot
    restored = snapshot_from_dict(snapshot_to_dict(snap))
    assert restored.state is DrawdownState.CAUTION
    assert restored.updated_at == 123
    json.dumps(snapshot_to_dict(snap))                               # 可 JSON 序列化


def test_snapshot_corrupt_reinitializes_safely():
    # 损坏/畸形 -> 全新 NORMAL:安全论证见 ControllerSnapshot docstring(越限状态每次现算,
    # 唯一依赖记忆的 RECOVERY ramp 丢失只会更慢恢复 = 保守方向)
    for bad in (None, "garbage", {"state": "NOPE"}, {"state": 1}, 42):
        snap = snapshot_from_dict(bad)
        assert snap.state is DrawdownState.NORMAL
        assert snap.observations_in_recovery == 0


def test_thresholds_reject_nonsense():
    with pytest.raises(ValueError):
        DrawdownThresholds(caution_drawdown=0.10, defensive_drawdown=0.05)   # 倒挂
    with pytest.raises(ValueError):
        DrawdownThresholds(caution_ceiling=1.5)
    with pytest.raises(ValueError):
        DrawdownThresholds(recovery_consecutive_observations=0)


def test_state_ceiling_mapping_table():
    assert state_ceiling(DrawdownState.NORMAL) == 1.0
    assert state_ceiling(DrawdownState.CAUTION) == 0.5
    assert state_ceiling(DrawdownState.DEFENSIVE) == 0.25
    assert state_ceiling(DrawdownState.HALT) == 0.0
    assert state_ceiling(DrawdownState.RECOVERY, observations_in_recovery=1) == pytest.approx(0.1)
    assert state_ceiling(DrawdownState.RECOVERY, observations_in_recovery=5) == pytest.approx(0.5)
    assert state_ceiling(DrawdownState.RECOVERY, observations_in_recovery=99) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 风险乘数:分量 + min 组合
# ---------------------------------------------------------------------------

def test_component_reading_clamps_into_unit_interval():
    assert ComponentReading("x", 1.7, "OK", "r").value == 1.0
    assert ComponentReading("x", -0.2, "OK", "r").value == 0.0
    with pytest.raises(ValueError):
        ComponentReading("x", 0.5, "WEIRD", "r")


def test_drawdown_component_ladder():
    assert drawdown_component(0.0).value == 1.0
    assert drawdown_component(0.06).value == 0.5
    assert drawdown_component(0.12).value == 0.25
    assert drawdown_component(0.25).value == 0.0
    assert drawdown_component(0.06).status == "OK"


def test_correlation_component_tri_state():
    assert correlation_component(True).value == 0.25
    assert correlation_component(False).value == 1.0
    unknown = correlation_component(None)
    assert unknown.status == "UNKNOWN" and unknown.value == 0.5     # 保守默认,不假装知道


def test_venue_health_component_ratio_and_incident():
    reading, incident = venue_health_component(14, 14)
    assert reading.value == 1.0 and reading.status == "OK" and incident is False
    reading, incident = venue_health_component(10, 14)
    assert reading.value == pytest.approx(10 / 14) and incident is False
    reading, incident = venue_health_component(0, 14)
    assert reading.value == 0.0 and incident is True                 # 整源失联 = 场地事故
    reading, incident = venue_health_component(0, 0)
    assert reading.status == "UNKNOWN" and incident is False         # 无目标:不谎称健康


# ---------------------------------------------------------------------------
# 实盘制品读者:W3 相关冲击 + W1 衰退(真 schema,2026-07-25 接线)
# ---------------------------------------------------------------------------

_NOW = 1_000_000


def _w1_payload(*, generated_at_ms=_NOW, ratio=0.85, level="DESCRIPTIVE_ONLY",
                reason=None, strategy="crowd_contra", venue="binance"):
    """真 W1 schema(schema_version 1,镜像 evidence/live_degradation.json 的相关键)。"""
    report = {"strategy_id": strategy, "venue": venue,
              "live_degradation_ratio": ratio,
              "degradation_ratio_reason": reason,
              "significance": {"level": level, "reason": "test"}}
    return {"artifact": "LIVE_DEGRADATION", "schema_version": 1,
            "generated_at_ms": generated_at_ms, "reports": [report]}


def _w3_payload(*, in_shock_now=False, status="OK"):
    """真 W3 schema(镜像 evidence/common_risk_decomposition.json 的 correlation_shock 块)。"""
    return {"provenance": "REAL_COMMON_RISK_DECOMPOSITION",
            "correlation_shock": {"status": status, "in_shock_now": in_shock_now,
                                  "current": {"week": "2026-W29", "mean_abs_corr": 0.144},
                                  "threshold": 0.3205, "window": 26, "margin": 0.25}}


def test_correlation_shock_from_artifact_real_w3_schema():
    assert correlation_shock_from_artifact(_w3_payload(in_shock_now=False)) is None
    assert correlation_shock_from_artifact(_w3_payload(in_shock_now=True)) is True


def test_correlation_shock_from_artifact_malformed_is_none():
    for bad in (None, "garbage", [], {}, {"correlation_shock": "nope"},
                _w3_payload(in_shock_now=False, status="INSUFFICIENT_DATA"),
                {"correlation_shock": {"status": "OK", "in_shock_now": 1}},
                {"correlation_shock": {"status": "OK", "in_shock_now": "true"}},
                {"correlation_shock": {"status": "OK"}}):
        assert correlation_shock_from_artifact(bad) is None, bad


def test_degradation_component_absent_or_malformed_is_unknown():
    # 制品缺失/非 dict/无 reports -> UNKNOWN 保守默认,不崩
    for bad in (None, "garbage", [], {"reports": "nope"}):
        r = degradation_component_from_artifact(bad, "crowd_contra", "binance", now_ms=_NOW)
        assert r.status == "UNKNOWN" and r.value == 0.5
    # 无 generated_at_ms -> 新鲜度无从确认 -> UNKNOWN(fail-closed)
    r = degradation_component_from_artifact({"reports": []}, "crowd_contra", "binance",
                                            now_ms=_NOW)
    assert r.status == "UNKNOWN"


def test_degradation_component_stale_artifact_is_unknown():
    payload = _w1_payload(generated_at_ms=_NOW - DEGRADATION_ARTIFACT_MAX_AGE_MS - 1)
    r = degradation_component_from_artifact(payload, "crowd_contra", "binance", now_ms=_NOW)
    assert r.status == "UNKNOWN" and "stale" in r.reason
    # 恰好 7 天和未来时间戳通过 freshness，但仍因缺独立 V2 authority 而 UNKNOWN。
    fresh_edge = _w1_payload(generated_at_ms=_NOW - DEGRADATION_ARTIFACT_MAX_AGE_MS)
    assert degradation_component_from_artifact(fresh_edge, "crowd_contra", "binance",
                                               now_ms=_NOW).status == "UNKNOWN"
    future = _w1_payload(generated_at_ms=_NOW + 60_000)
    assert degradation_component_from_artifact(future, "crowd_contra", "binance",
                                               now_ms=_NOW).status == "UNKNOWN"


def test_degradation_component_requires_strategy_venue_report():
    payload = _w1_payload(venue="binance")
    # venue 不匹配 / strategy 不在案 -> UNKNOWN(该策略×场地无实盘漏损证据)
    assert degradation_component_from_artifact(payload, "crowd_contra", "mexc",
                                               now_ms=_NOW).status == "UNKNOWN"
    assert degradation_component_from_artifact(payload, "carry_oi_majors", "binance",
                                               now_ms=_NOW).status == "UNKNOWN"


def test_degradation_component_null_ratio_never_becomes_a_number():
    # sign-flip / replay≈0 地板:比值数学上无意义 -> UNKNOWN,绝不翻成数字
    payload = _w1_payload(ratio=None, reason="sign_flip_ratio_meaningless")
    r = degradation_component_from_artifact(payload, "crowd_contra", "binance", now_ms=_NOW)
    assert r.status == "UNKNOWN" and r.value == 0.5
    assert "sign_flip_ratio_meaningless" in r.reason


def test_degradation_component_malformed_ratio_is_unknown():
    for bad in ("0.8", float("nan"), float("inf"), True):
        r = degradation_component_from_artifact(_w1_payload(ratio=bad),
                                                "crowd_contra", "binance", now_ms=_NOW)
        assert r.status == "UNKNOWN", bad


def test_degradation_component_small_sample_and_unrecognized_levels_are_unknown():
    # 制品自标 SMALL_SAMPLE(样本太小、不构成显著性断言)-> 不消费比值
    r = degradation_component_from_artifact(_w1_payload(ratio=0.1, level="SMALL_SAMPLE"),
                                            "crowd_contra", "binance", now_ms=_NOW)
    assert r.status == "UNKNOWN"
    # 未识别的 significance 标签 -> fail-closed UNKNOWN
    r = degradation_component_from_artifact(_w1_payload(ratio=0.1, level="SOME_FUTURE_LEVEL"),
                                            "crowd_contra", "binance", now_ms=_NOW)
    assert r.status == "UNKNOWN"


def test_degradation_component_ratio_bands_uncalibrated():
    # Retained pure mapping is testable, but the artifact consumer cannot reach
    # it without independently admitted V2 authority.
    for ratio, expected in ((1.5, 1.0), (0.85, 1.0), (0.7, 1.0),
                            (0.55, 0.5), (0.4, 0.5), (0.39, 0.25), (0.0, 0.25), (-1.2, 0.25)):
        assert _degradation_band_value(ratio, DrawdownThresholds()) == pytest.approx(expected)


def test_legacy_degradation_ratio_never_drives_deploy_scaling():
    for ratio in (1.5, 0.85, 0.55, 0.1):
        reading = degradation_component_from_artifact(
            _w1_payload(ratio=ratio),
            "crowd_contra",
            "binance",
            now_ms=_NOW,
        )
        assert reading.status == "UNKNOWN"
        assert reading.value == DrawdownThresholds().defensive_ceiling
        assert "authority" in reading.reason


def test_combine_min_wins_and_binding_is_named():
    components = [
        ComponentReading("a", 0.9, "OK", "ra"),
        ComponentReading("b", 0.6, "OK", "rb"),
        unknown_component("c", "no artifact"),
    ]
    result = combine_risk_multiplier(components, state=DrawdownState.NORMAL)
    assert result.multiplier == 0.5                                  # UNKNOWN 默认 0.5 取得 min
    assert result.binding == ("c",)
    assert result.state_ceiling == 1.0


def test_combine_state_ceiling_binds_below_all_components():
    components = [ComponentReading("a", 1.0, "OK", "ra"),
                  ComponentReading("b", 0.9, "OK", "rb")]
    result = combine_risk_multiplier(components, state=DrawdownState.CAUTION)
    assert result.multiplier == 0.5
    assert "state_ceiling" in result.binding
    result = combine_risk_multiplier(components, state=DrawdownState.DEFENSIVE)
    assert result.multiplier == 0.25
    result = combine_risk_multiplier(components, state=DrawdownState.HALT)
    assert result.multiplier == 0.0


def test_combine_never_exceeds_one_and_never_negative():
    components = [ComponentReading("a", 1.0, "OK", "ra")]
    assert combine_risk_multiplier(components, state=DrawdownState.NORMAL).multiplier == 1.0
    # 乘数是 ≤1 的 scaler:任何分量组合都无法把资本抬过硬上限(spec §10 红线的机器保证)
    for st in DrawdownState:
        m = combine_risk_multiplier(components, state=st,
                                    observations_in_recovery=3).multiplier
        assert 0.0 <= m <= 1.0


def test_combine_explain_is_complete_and_serializable():
    components = [drawdown_component(0.0), correlation_component(None),
                  unknown_component("vol_target", "none")]
    result = combine_risk_multiplier(components, state=DrawdownState.RECOVERY,
                                     observations_in_recovery=2)
    ex = result.explain
    assert set(ex["components"]) == {"drawdown", "correlation", "vol_target"}
    for name, reading in ex["components"].items():
        assert set(reading) == {"value", "status", "reason"}
        assert reading["reason"]                                     # 每个分量都有可解释原因
    assert ex["state"] == "RECOVERY" and ex["state_ceiling"] == pytest.approx(0.2)
    assert ex["unknown_default"] == 0.5
    assert ex["thresholds"]["calibration"] == "UNCALIBRATED_ENGINEERING_DEFAULTS"
    assert result.binding
    json.dumps(ex)                                                   # 审计字典可 JSON 序列化


def test_decision_explain_is_serializable():
    d = _ctrl().update(equity=94.0, peak_equity=PEAK, now=7, equity_window=[100.0, 96.0],
                       correlation_shock=None)
    json.dumps(d.explain)
    assert d.explain["inputs"]["window_size"] == 2
    assert d.explain["previous_state"] == "NORMAL"


# ---------------------------------------------------------------------------
# 驱动器接线:可测桥(main() 网络胶水不单测,同 test_live_track_advance 惯例)
# ---------------------------------------------------------------------------

def _history(tmp_path, rows):
    path = tmp_path / "eq.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for ts, eq in rows:
            fh.write(json.dumps({"ts": ts, "equity": eq, "source": "live_track"}) + "\n")
    return str(path)


def _bridge(tmp_path, *, equity, peak=PEAK, now=1, books=None, valid=True,
            strategy="crowd_contra", venue="binance",
            degradation_path=None, common_risk_path=None):
    from tools.live_track_advance import drawdown_control_decision
    return drawdown_control_decision(
        equity=equity, peak_equity=peak, peak_state_valid=valid,
        venue_books=books if books is not None else {"BTC": {"bid": 1, "ask": 2}},
        now=now, state_path=str(tmp_path / "dd.json"),
        equity_history_path=str(tmp_path / "eq.jsonl"), strategy_id=strategy, venue=venue,
        # 默认指向不存在的 tmp 文件:制品缺席路径保持 hermetic(与本机 evidence/ 是否已有
        # 真制品无关);"制品在场"路径由调用方显式传真 schema 临时文件。
        degradation_path=degradation_path or str(tmp_path / "absent_degradation.json"),
        common_risk_path=common_risk_path or str(tmp_path / "absent_common_risk.json"))


def test_equity_window_reads_tail_and_drops_current_row(tmp_path):
    from tools.live_track_advance import _equity_window_from_history
    path = _history(tmp_path, [(i, 100.0 + i) for i in range(1, 6)])
    # drop_ts=5(本轮刚 append 的当前观测)-> 窗口不含它;limit 截尾
    assert _equity_window_from_history(path, limit=3, drop_ts=5) == [102.0, 103.0, 104.0]
    assert _equity_window_from_history(path, limit=99, drop_ts=None) == \
        [101.0, 102.0, 103.0, 104.0, 105.0]


def test_equity_window_fail_soft_on_missing_or_corrupt(tmp_path):
    from tools.live_track_advance import _equity_window_from_history
    assert _equity_window_from_history(str(tmp_path / "absent.jsonl"), limit=5) == []
    path = tmp_path / "bad.jsonl"
    path.write_text('{"ts": 1, "equity": 100.0}\nnot-json\n{"ts": 2, "equity": -5}\n'
                    '{"ts": 3, "equity": 101.0}\n', encoding="utf-8")
    # 坏行跳过;非正权益跳过(append_equity_observation 本就不接收,这里纵深防御)
    assert _equity_window_from_history(str(path), limit=5) == [100.0, 101.0]


def test_apply_risk_multiplier_scales_down_and_never_up():
    from tools.live_track_advance import apply_risk_multiplier
    assert apply_risk_multiplier(1000.0, 0.5) == 500.0
    assert apply_risk_multiplier(1000.0, 0.0) == 0.0
    assert apply_risk_multiplier(1000.0, 1.0) == 1000.0
    assert apply_risk_multiplier(1000.0, 1.7) == 1000.0      # 钳 1:绝不放大超过 mandate 口径
    assert apply_risk_multiplier(1000.0, -0.3) == 0.0        # 钳 0


def test_bridge_normal_run_unknown_components_cap_multiplier_at_half(tmp_path):
    _history(tmp_path, [(1, 100.0)])
    out = _bridge(tmp_path, equity=100.0, now=1)
    assert out["state"] == "NORMAL" and out["skip_reason"] is None
    # W3/衰退/vol/liquidity/capacity 全部 UNKNOWN -> 保守默认 0.5 取得 min(缺证据不放满配)
    assert out["multiplier"] == 0.5
    for name in ("vol_target", "liquidity", "correlation", "degradation", "capacity"):
        assert out["components"][name]["status"] == "UNKNOWN"
    assert out["components"]["venue_health"]["status"] == "OK"
    assert out["snapshot_persisted"] is True
    snap = json.load(open(tmp_path / "dd.json", encoding="utf-8"))
    assert snap["state"] == "NORMAL"


def test_bridge_caution_scales_deploy_no_skip(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 94.0)])
    out = _bridge(tmp_path, equity=94.0, now=2)
    assert out["state"] == "CAUTION"
    assert out["skip_reason"] is None                        # CAUTION 降杠杆,不跳批
    assert out["multiplier"] == 0.5                          # state ceiling 0.5 与 UNKNOWN 并列
    assert "state_ceiling" in out["binding"]


def test_bridge_defensive_skips_batch(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 88.0)])
    out = _bridge(tmp_path, equity=88.0, now=2)
    assert out["state"] == "DEFENSIVE"
    assert out["skip_reason"] is not None
    assert "drawdown_controller_defensive" in out["skip_reason"]
    assert "reduce-only" in out["skip_reason"]               # 诚实说明:管线无 reduce-only,跳批代替
    assert out["multiplier"] == 0.25


def test_bridge_halt_skips_batch_with_zero_multiplier(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 79.0)])
    out = _bridge(tmp_path, equity=79.0, now=2)
    assert out["state"] == "HALT"
    assert "drawdown_controller_halt" in out["skip_reason"]
    assert out["multiplier"] == 0.0


def test_bridge_peak_state_invalid_is_data_anomaly_halt(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    out = _bridge(tmp_path, equity=99.5, now=2, valid=False)
    assert out["state"] == "HALT"
    assert "data_anomaly" in out["reason"]


def test_bridge_venue_incident_all_books_failed_is_halt(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    out = _bridge(tmp_path, equity=99.5, now=2, books={"BTC": None, "ETH": None})
    assert out["state"] == "HALT"
    assert "venue_incident" in out["reason"]
    assert out["venue_incident"] is True


def test_bridge_partial_book_failure_scales_venue_health_without_halt(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    out = _bridge(tmp_path, equity=99.5, now=2,
                  books={"BTC": {"bid": 1, "ask": 2}, "ETH": None})
    assert out["state"] == "NORMAL"
    assert out["venue_incident"] is False
    assert out["components"]["venue_health"]["value"] == 0.5  # 1/2 可得 -> 同比收缩


def test_bridge_degradation_artifact_real_schema(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    # 缺失 -> UNKNOWN,不崩(与制品缺席时行为逐位一致)
    out = _bridge(tmp_path, equity=99.5, now=2,
                  degradation_path=str(tmp_path / "absent.json"))
    assert out["components"]["degradation"]["status"] == "UNKNOWN"
    assert out["components"]["degradation"]["value"] == 0.5
    # 真 W1 schema 在场且新鲜:ratio 0.3 (DESCRIPTIVE_ONLY) -> 0.25 成为乘数约束
    deg = tmp_path / "deg.json"
    deg.write_text(json.dumps(_w1_payload(generated_at_ms=2, ratio=0.3)), encoding="utf-8")
    out = _bridge(tmp_path, equity=99.5, now=2, degradation_path=str(deg))
    assert out["components"]["degradation"]["status"] == "UNKNOWN"
    assert out["components"]["degradation"]["value"] == 0.25
    assert out["multiplier"] == 0.25
    assert "degradation" in out["binding"]


def test_bridge_degradation_report_matched_on_strategy_and_venue(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    deg = tmp_path / "deg.json"
    deg.write_text(json.dumps(_w1_payload(generated_at_ms=2, ratio=0.3, venue="binance")),
                   encoding="utf-8")
    # 报告在 binance:venue=mexc -> 无该策略×场地证据 -> UNKNOWN;venue=binance -> 消费
    out = _bridge(tmp_path, equity=99.5, now=2, venue="mexc", degradation_path=str(deg))
    assert out["components"]["degradation"]["status"] == "UNKNOWN"
    out = _bridge(tmp_path, equity=99.5, now=2, venue="binance", degradation_path=str(deg))
    assert out["components"]["degradation"]["value"] == 0.25


def test_bridge_stale_or_null_ratio_degradation_falls_back_to_unknown(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    deg = tmp_path / "deg.json"
    deg.write_text(json.dumps(_w1_payload(generated_at_ms=2, ratio=0.3)), encoding="utf-8")
    # 制品年龄 8 天(> 7d 守门)-> UNKNOWN,不消费过期衰退证据
    eight_days_ms = 8 * 24 * 3600 * 1000
    out = _bridge(tmp_path, equity=99.5, now=2 + eight_days_ms, degradation_path=str(deg))
    assert out["components"]["degradation"]["status"] == "UNKNOWN"
    assert "stale" in out["components"]["degradation"]["reason"]
    # ratio null(sign-flip)-> UNKNOWN,绝不把无意义比值翻成数字
    deg.write_text(json.dumps(_w1_payload(generated_at_ms=2, ratio=None,
                                          reason="sign_flip_ratio_meaningless")),
                   encoding="utf-8")
    out = _bridge(tmp_path, equity=99.5, now=2, degradation_path=str(deg))
    assert out["components"]["degradation"]["status"] == "UNKNOWN"


def test_bridge_correlation_shock_quiet_artifact_gives_ok_reading(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    w3 = tmp_path / "w3.json"
    w3.write_text(json.dumps(_w3_payload(in_shock_now=False)), encoding="utf-8")
    out = _bridge(tmp_path, equity=99.5, now=2, common_risk_path=str(w3))
    assert out["state"] == "NORMAL"
    assert out["components"]["correlation"]["value"] == 0.5
    assert out["components"]["correlation"]["status"] == "UNKNOWN"
    # 其余 UNKNOWN 分量仍把乘数压在 0.5(接上真制品不等于放任满配)
    assert out["multiplier"] == 0.5


def test_bridge_correlation_shock_artifact_forces_defensive_and_skips(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    w3 = tmp_path / "w3.json"
    w3.write_text(json.dumps(_w3_payload(in_shock_now=True)), encoding="utf-8")
    out = _bridge(tmp_path, equity=99.5, now=2, common_risk_path=str(w3))
    assert out["state"] == "DEFENSIVE"
    assert "correlation_shock" in out["reason"]
    assert out["skip_reason"] is not None                  # DEFENSIVE 跳批(管线无 reduce-only)
    assert out["components"]["correlation"]["value"] == 0.25
    assert out["multiplier"] == 0.25


def test_bridge_malformed_w3_artifact_falls_back_to_unknown(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 99.5)])
    w3 = tmp_path / "w3.json"
    w3.write_text(json.dumps({"correlation_shock": {"status": "INSUFFICIENT_DATA"}}),
                  encoding="utf-8")
    out = _bridge(tmp_path, equity=99.5, now=2, common_risk_path=str(w3))
    assert out["state"] == "NORMAL"                        # 无意见 ≠ 有冲击
    assert out["components"]["correlation"]["status"] == "UNKNOWN"
    assert out["multiplier"] == 0.5


def test_bridge_recovery_sequence_persists_across_runs(tmp_path):
    _history(tmp_path, [(1, 100.0)])
    _bridge(tmp_path, equity=100.0, now=1)
    with open(tmp_path / "eq.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 2, "equity": 93.0}) + "\n")
    out = _bridge(tmp_path, equity=93.0, now=2)
    assert out["state"] == "CAUTION"
    for now, eq in ((3, 97.0), (4, 98.0), (5, 98.5)):
        with open(tmp_path / "eq.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now, "equity": eq}) + "\n")
        out = _bridge(tmp_path, equity=eq, now=now)
    assert out["state"] == "RECOVERY"                        # 快照跨调用持久化,双门在第三次通过
    assert out["multiplier"] == pytest.approx(0.1)           # ramp 起步
    snap = json.load(open(tmp_path / "dd.json", encoding="utf-8"))
    assert snap["observations_in_recovery"] == 1


def test_bridge_output_is_json_serializable_status_payload(tmp_path):
    _history(tmp_path, [(1, 100.0), (2, 94.0)])
    out = _bridge(tmp_path, equity=94.0, now=2)
    json.dumps(out)                                          # 整 dict 直接进 status JSON
    for key in ("state", "reason", "drawdown_pct", "multiplier", "binding", "state_ceiling",
                "skip_reason", "components", "explain", "snapshot_persisted"):
        assert key in out


def test_multiplier_never_raises_deploy_above_mandate_cap(tmp_path):
    # spec §10 红线:任何模型输出都不能覆盖硬风险上限——对所有状态,乘后 deploy 恒 ≤
    # 既有 mandate 口径 min(capital_base, 0.95*max_gross)
    from tools.live_track_advance import apply_risk_multiplier
    _history(tmp_path, [(1, 100.0), (2, 79.0)])
    mandate_cap_deploy = min(100.0, 0.95 * 10_000.0)
    for equity in (100.0, 94.0, 88.0, 79.0):
        out = _bridge(tmp_path, equity=equity, now=2)
        assert apply_risk_multiplier(mandate_cap_deploy, out["multiplier"]) \
            <= mandate_cap_deploy + 1e-12


# ---------------------------------------------------------------------------
# 源码结构钉子(main() 是网络胶水不可离线跑,镜像 tests/live/test_strategy_wiring.py)
# ---------------------------------------------------------------------------

def test_driver_wires_drawdown_controller_before_deploy_and_submit():
    assert "drawdown = drawdown_control_decision(" in _SRC
    assert 'deploy_aum = apply_risk_multiplier(deploy_aum, drawdown["multiplier"])' in _SRC
    i_equity = _SRC.index("peak_state = record_and_validate_peak_equity(")
    i_ctrl = _SRC.index("drawdown = drawdown_control_decision(")
    i_scale = _SRC.index('deploy_aum = apply_risk_multiplier(deploy_aum, drawdown["multiplier"])')
    i_submit = _SRC.index("result = submit_live_batch(")
    # 顺序:权益/峰值 -> 控制器 -> 乘数缩放 deploy -> 批次提交
    assert i_equity < i_ctrl < i_scale < i_submit
    # 乘数乘在 mandate 上限口径**之后**(只缩不放,硬上限永不被覆盖)
    i_mandate = _SRC.index("deploy_aum = min(capital_base, 0.95 * mandate.max_gross_notional)")
    assert i_mandate < i_scale


def test_driver_wires_real_artifacts_into_drawdown_components():
    # W3/W1 真制品接进 bridge(2026-07-25):路径常量、纯读者、状态机真读数、
    # strategy×venue + 注入 now 的衰退分量、call site 传 venue
    assert '_COMMON_RISK_PATH = os.path.join("evidence", "common_risk_decomposition.json")' in _SRC
    assert "correlation_shock_from_artifact" in _SRC
    assert "correlation_shock=correlation_shock" in _SRC
    assert "now_ms=now" in _SRC
    assert "strategy_id=strategy_id, venue=venue_arg" in _SRC


def test_driver_wires_halt_defensive_batch_skip_and_status_key():
    assert 'skip_reason = skip_reason or drawdown["skip_reason"]' in _SRC
    assert '"drawdown_control": drawdown' in _SRC                # status JSON 加法键
    i_ctrl = _SRC.index("drawdown = drawdown_control_decision(")
    i_skip = _SRC.index('skip_reason = skip_reason or drawdown["skip_reason"]')
    assert i_ctrl < i_skip


def test_driver_does_not_invent_reduce_only_order_path():
    # DEFENSIVE 的裁定语义 = 跳批(见 drawdown_control_decision docstring):管线仍没有、
    # 也不许为本控制器新增 reduce-only 下单路径(reduce-only 只属于 emergency 保留通道;
    # gate 的 reduce-only 豁免是平仓单过流动性门的既有机制,与下单路径无关)
    pipeline_src = open(os.path.join(_ROOT, "live", "pipeline.py"), encoding="utf-8").read()
    assert "reduce_only" not in pipeline_src


def test_driver_drawdown_state_path_is_account_level_per_venue():
    from tools.live_track_advance import _VENUES, _venue_paths
    for venue, cfg in _VENUES.items():
        paths = _venue_paths(cfg["suffix"], strategy_id="stratX")
        default_paths = _venue_paths(cfg["suffix"])
        # 账户概念、per-venue 共享:绝不按策略拆(与 risk_state/day_state 同一规则)
        assert paths["drawdown_state"] == default_paths["drawdown_state"], venue
        assert "live_drawdown_state" in paths["drawdown_state"]
