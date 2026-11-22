"""动态回撤状态机 + 可解释风险乘数(Profit Maximization spec §10 W6,纯核)。

把 spec §10 的 `risk_multiplier = min(vol_target, drawdown, liquidity, correlation,
degradation, venue_health, capacity)` 落成一台**纯函数**状态机 + 分量组合器,供
live-track 驱动器(`tools/live_track_advance.py`)按运行周期缩放部署资本:

- **状态机** NORMAL / CAUTION / DEFENSIVE / RECOVERY / HALT:
  - CAUTION:回撤首次越过 caution 阈值 -> 降杠杆(状态天花板 0.5);
  - DEFENSIVE:回撤越过 defensive 阈值、或相关冲击(correlation_shock=True)-> 深降
    (天花板 0.25,驱动器端实际跳批,见驱动器 bridge 注释);
  - RECOVERY:回撤回到 caution 之下**且**通过双重门(时间 AND 表现,缺一不可):
    ① 表现门:最近 K 次连续观测的回撤都 < caution(对**当前峰值**计算——历史观测的
       真实峰值只会 ≤ 当前峰值,故这是对历史回撤的保守上界,宁可慢放不可快放);
    ② 稳定门:最近 M 次观测没有创新低(严格新低:观测值 < 此前窗口最小值)。
    进入后**慢速再升杠杆**(ramp:ceiling = min(recovery_max_ceiling, ramp_step ×
    已处 RECOVERY 观测数)),绝不瞬时满杠杆;RECOVERY 持续 recovery_exit_observations
    次观测且双门始终成立才回 NORMAL(天花板 1.0)。RECOVERY 中创新高回撤/创新低 ->
    立即退回 CAUTION/DEFENSIVE(升级永远优先于恢复)。
  - HALT:回撤越过 halt 硬阈值 / 数据异常(data_anomaly) / 场地事故(venue_incident)
    -> 本轮零新增风险(天花板 0.0)。**本状态机不复制 kill-switch**:kill-switch 才是
    闩锁式、需人工复位的全局 halt(风控越限分支早已接它);HALT 只承诺"本驱动器本轮
    不开新风险"(跳批)。HALT 条件消失后不闩锁,但也**绝不直接回 NORMAL**——与任何
    回撤后状态一样,必须经 CAUTION -> 双门 -> RECOVERY -> ramp 爬回。
- **风险乘数**:每个分量是纯函数 -> `ComponentReading{value∈[0,1], status: OK|UNKNOWN,
  reason}`;`combine_risk_multiplier` 取所有分量与状态天花板的 min(钳进 [0,1])。
  **UNKNOWN 分量解析为保守默认 0.5**(`unknown_component_default`,工程默认)——缺证据
  不放行满配,也不假装知道。`binding` 列出取得 min 的分量名(含 "state_ceiling"),
  `explain` 给全量可 JSON 序列化的审计字典。**任何模型/信号输出都无法把乘数抬过 1
  或抬过状态天花板**(min 语义 + [0,1] 钳制)——硬风险上限永不被覆盖。

所有阈值是 **UNCALIBRATED 工程默认**(caution 5% / defensive 10% / halt 20% / K=3 /
M=3 / ramp 0.1→0.5 / exit 6 次观测 / UNKNOWN=0.5)——上线前需用户签署的校准流程,
与 `_MAX_DRAWDOWN_PCT` 等既有工程默认同一口径(非业务风险容忍度)。注意 halt 20% 与
风控监控 `evaluate_risk_state` 的 max_drawdown 0.20 一致:驱动器里 dd≥20% 会先走
kill-switch + 紧急减仓早退,本机的 HALT 实际主要覆盖数据异常/场地事故路径(分层,
不冲突)。

观测语义:一次 `update()` = 一次权益观测(驱动器一周度运行 = 一次;ops API preview
轮询也算一次,与 equity jsonl 既有「preview 也落观测」口径一致——K/M 的次数语义随
调度节奏,阈值 UNCALIBRATED 已如实标注)。`equity_window` = 最近若干次历史观测
(**不含**本次,本函数把 current equity 追加在尾)。

纯函数、无 IO/网络/墙钟/RNG——快照持久化/文件读取全在调用方(驱动器 bridge)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from services.execution.backtest_authority import (
    LEGACY_INVALIDATION_CODE,
    is_authoritative_backtest_record,
)


class DrawdownState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    RECOVERY = "RECOVERY"
    HALT = "HALT"


_STATE_RANK = {
    DrawdownState.NORMAL: 0,
    DrawdownState.RECOVERY: 1,
    DrawdownState.CAUTION: 2,
    DrawdownState.DEFENSIVE: 3,
    DrawdownState.HALT: 4,
}


@dataclass(frozen=True)
class DrawdownThresholds:
    """全部 **UNCALIBRATED 工程默认**(非业务风险容忍度,见模块 docstring)。"""
    caution_drawdown: float = 0.05
    defensive_drawdown: float = 0.10
    halt_drawdown: float = 0.20
    # RECOVERY 双重门:K 次连续观测回撤 < caution(表现)∧ M 次观测无创新低(时间/稳定)
    recovery_consecutive_observations: int = 3
    recovery_stabilization_observations: int = 3
    # 慢速再升杠杆:ceiling = min(recovery_max_ceiling, recovery_ramp_step × 第 n 次 RECOVERY 观测)
    recovery_ramp_step: float = 0.1
    recovery_max_ceiling: float = 0.5
    # 第 6 次 RECOVERY 观测(ramp 已在 0.5 封顶一轮)且双门仍成立 -> 回 NORMAL(满杠杆恢复完毕)
    recovery_exit_observations: int = 6
    # 状态天花板(spec §10 映射表默认)
    caution_ceiling: float = 0.5
    defensive_ceiling: float = 0.25
    # UNKNOWN 分量的保守默认(缺证据不放满配)
    unknown_component_default: float = 0.5

    def __post_init__(self) -> None:
        for name in ("caution_drawdown", "defensive_drawdown", "halt_drawdown"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise ValueError(f"{name} must be a finite number, got {v!r}")
        if not (0.0 < self.caution_drawdown <= self.defensive_drawdown <= self.halt_drawdown):
            raise ValueError("drawdown thresholds must satisfy 0 < caution <= defensive <= halt")
        for name in ("caution_ceiling", "defensive_ceiling", "recovery_max_ceiling",
                     "recovery_ramp_step", "unknown_component_default"):
            v = getattr(self, name)
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(float(v)) or not (0.0 <= float(v) <= 1.0)):
                raise ValueError(f"{name} must be in [0,1], got {v}")
        for name in ("recovery_consecutive_observations", "recovery_stabilization_observations",
                     "recovery_exit_observations"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ValueError(f"{name} must be a positive int, got {v!r}")

    def as_dict(self) -> Dict[str, float]:
        return {
            "caution_drawdown": self.caution_drawdown,
            "defensive_drawdown": self.defensive_drawdown,
            "halt_drawdown": self.halt_drawdown,
            "recovery_consecutive_observations": self.recovery_consecutive_observations,
            "recovery_stabilization_observations": self.recovery_stabilization_observations,
            "recovery_ramp_step": self.recovery_ramp_step,
            "recovery_max_ceiling": self.recovery_max_ceiling,
            "recovery_exit_observations": self.recovery_exit_observations,
            "caution_ceiling": self.caution_ceiling,
            "defensive_ceiling": self.defensive_ceiling,
            "unknown_component_default": self.unknown_component_default,
            "calibration": "UNCALIBRATED_ENGINEERING_DEFAULTS",
        }


def _positive_finite(x: object) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(float(x)) and float(x) > 0)


def state_ceiling(state: DrawdownState, *, observations_in_recovery: int = 0,
                  thresholds: Optional[DrawdownThresholds] = None) -> float:
    """spec §10 状态 -> 乘数天花板映射表(默认 NORMAL 1.0 / CAUTION 0.5 / DEFENSIVE 0.25 /
    RECOVERY min(0.5, slow ramp) / HALT 0.0)。RECOVERY 的 ramp 从第 1 次观测起算
    (0.1, 0.2, ...),绝不瞬时满杠杆。"""
    th = thresholds or DrawdownThresholds()
    if state is DrawdownState.NORMAL:
        return 1.0
    if state is DrawdownState.CAUTION:
        return th.caution_ceiling
    if state is DrawdownState.DEFENSIVE:
        return th.defensive_ceiling
    if state is DrawdownState.HALT:
        return 0.0
    # RECOVERY:慢速再升杠杆
    n = max(1, int(observations_in_recovery))
    return min(th.recovery_max_ceiling, th.recovery_ramp_step * n)


# ---------------------------------------------------------------------------
# 恢复双门(纯窗口计算;window 已含本次观测,由 update() 追加)
# ---------------------------------------------------------------------------

def consecutive_below_caution_count(series: Sequence[float], *, peak_equity: float,
                                    caution_drawdown: float) -> int:
    """从最新观测往回数,连续多少次观测的回撤 < caution。回撤对**当前峰值**计算——历史观测
    当时的峰值只会 ≤ 当前峰值,所以这是历史回撤的保守上界(宁可慢放,不可快放)。"""
    if (not _positive_finite(peak_equity)
            or isinstance(caution_drawdown, bool)
            or not isinstance(caution_drawdown, (int, float))
            or not math.isfinite(float(caution_drawdown))):
        return 0
    peak = float(peak_equity)
    caution = float(caution_drawdown)
    count = 0
    for eq in reversed(series):
        if not _positive_finite(eq):
            break
        dd_i = max(0.0, (peak - float(eq)) / peak)
        if dd_i < caution:
            count += 1
        else:
            break
    return count


def observations_since_new_low(series: Sequence[float]) -> int:
    """距上次**严格新低**(观测值 < 此前窗口最小值)有多少次观测。窗口内从未新低 ->
    len(series)。窗口的第一观测是参照点(窗口外历史不可知,如实标注:这是窗口相对口径)。"""
    running_min: Optional[float] = None
    last_new_low = -1
    for i, eq in enumerate(series):
        if not _positive_finite(eq):
            # Corrupted/non-positive observation breaks stabilization fail-closed.
            last_new_low = i
            continue
        val = float(eq)
        if running_min is not None and val < running_min:
            last_new_low = i
        running_min = val if running_min is None else min(running_min, val)
    return len(series) - 1 - last_new_low


# ---------------------------------------------------------------------------
# 控制器快照(调用方持久化;纯值,可 JSON 序列化)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ControllerSnapshot:
    """跨运行持久的最小机器记忆:前一状态 + 已连续处于 RECOVERY 的观测数(ramp 用)。

    诚实边界:快照损坏/丢失 -> `snapshot_from_dict` 重新初始化为 NORMAL——这**不是**
    fail-open:当前 dd 越限的状态(CAUTION/DEFENSIVE/HALT)每次都由本次权益/峰值现算,
    不依赖记忆;唯一依赖记忆的是 RECOVERY 的 ramp 进度,丢掉它只会**更慢**恢复杠杆
    (保守方向)。"""
    state: DrawdownState = DrawdownState.NORMAL
    observations_in_recovery: int = 0
    reason: str = "initialized"
    updated_at: int = 0


def snapshot_to_dict(snapshot: ControllerSnapshot) -> Dict[str, object]:
    return {"schema_version": 1, "state": snapshot.state.value,
            "observations_in_recovery": int(snapshot.observations_in_recovery),
            "reason": snapshot.reason, "updated_at": int(snapshot.updated_at)}


def snapshot_from_dict(payload: object) -> ControllerSnapshot:
    """容错反序列化:任何畸形 -> 全新 NORMAL 快照(安全性见 ControllerSnapshot docstring)。"""
    if not isinstance(payload, dict):
        return ControllerSnapshot(reason="snapshot_missing_reinitialized")
    try:
        state = DrawdownState(str(payload.get("state")))
    except ValueError:
        return ControllerSnapshot(reason="snapshot_corrupt_reinitialized")
    obs = payload.get("observations_in_recovery", 0)
    if not isinstance(obs, int) or isinstance(obs, bool) or obs < 0:
        obs = 0
    updated_at = payload.get("updated_at", 0)
    if not isinstance(updated_at, int) or isinstance(updated_at, bool):
        updated_at = 0
    reason = payload.get("reason")
    return ControllerSnapshot(state=state, observations_in_recovery=obs,
                              reason=str(reason) if reason is not None else "restored",
                              updated_at=updated_at)


@dataclass(frozen=True)
class DrawdownDecision:
    state: DrawdownState
    reason: str
    drawdown_pct: float
    state_ceiling: float
    snapshot: ControllerSnapshot          # 调用方应持久化的新快照
    explain: Dict[str, object]            # 可 JSON 序列化的全量审计字典


class DrawdownController:
    """纯状态机:`update()` 消费一次权益观测 -> 新状态 + 原因 + 新快照。无 IO/墙钟。"""

    def __init__(self, *, snapshot: Optional[ControllerSnapshot] = None,
                 thresholds: Optional[DrawdownThresholds] = None) -> None:
        self._snapshot = snapshot or ControllerSnapshot()
        self._thresholds = thresholds or DrawdownThresholds()

    @property
    def snapshot(self) -> ControllerSnapshot:
        return self._snapshot

    def update(self, *, equity: float, peak_equity: float, now: int,
               equity_window: Sequence[float] = (),
               correlation_shock: Optional[bool] = None,
               data_anomaly: bool = False,
               venue_incident: bool = False) -> DrawdownDecision:
        th = self._thresholds
        prev = self._snapshot

        # fail-closed:无法确认安全 = 越限同 posture(镜像 risk_monitor 的 peak_equity_unknown)
        if not _positive_finite(equity):
            return self._finish(DrawdownState.HALT, f"invalid_current_equity:{equity!r}",
                                0.0, prev, now, equity_window=(),
                                equity=equity, peak_equity=peak_equity,
                                correlation_shock=correlation_shock,
                                data_anomaly=data_anomaly, venue_incident=venue_incident)
        if not _positive_finite(peak_equity):
            return self._finish(DrawdownState.HALT, "peak_equity_unknown", 0.0, prev, now,
                                equity_window=(), equity=equity, peak_equity=peak_equity,
                                correlation_shock=correlation_shock,
                                data_anomaly=data_anomaly, venue_incident=venue_incident)

        equity = float(equity)
        peak = float(peak_equity)
        dd = max(0.0, (peak - equity) / peak)
        series: List[float] = [float(v) for v in equity_window if _positive_finite(v)]
        series.append(equity)

        # 回撤隐含档位
        if dd >= th.halt_drawdown:
            implied, reasons = DrawdownState.HALT, [
                f"drawdown {dd:.4f} >= halt {th.halt_drawdown}"]
        elif dd >= th.defensive_drawdown:
            implied, reasons = DrawdownState.DEFENSIVE, [
                f"drawdown {dd:.4f} >= defensive {th.defensive_drawdown}"]
        elif dd >= th.caution_drawdown:
            implied, reasons = DrawdownState.CAUTION, [
                f"drawdown {dd:.4f} >= caution {th.caution_drawdown}"]
        else:
            implied, reasons = DrawdownState.NORMAL, [f"drawdown {dd:.4f} within caution"]

        # 硬覆盖:数据异常 / 场地事故 -> HALT(spec §10);相关冲击 -> 至少 DEFENSIVE
        if data_anomaly:
            implied, reasons = DrawdownState.HALT, reasons + ["data_anomaly"]
        if venue_incident:
            implied, reasons = DrawdownState.HALT, reasons + ["venue_incident"]
        if implied is not DrawdownState.HALT and correlation_shock is True \
                and _STATE_RANK[implied] < _STATE_RANK[DrawdownState.DEFENSIVE]:
            implied = DrawdownState.DEFENSIVE
            reasons.append("correlation_shock")

        if implied is DrawdownState.HALT:
            return self._finish(DrawdownState.HALT, "; ".join(reasons), dd, prev, now,
                                equity_window=series[:-1],
                                equity=equity, peak_equity=peak_equity,
                                correlation_shock=correlation_shock,
                                data_anomaly=data_anomaly, venue_incident=venue_incident)
        if implied is DrawdownState.DEFENSIVE:
            return self._finish(DrawdownState.DEFENSIVE, "; ".join(reasons), dd, prev, now,
                                equity_window=series[:-1],
                                equity=equity, peak_equity=peak_equity,
                                correlation_shock=correlation_shock,
                                data_anomaly=data_anomaly, venue_incident=venue_incident)
        if implied is DrawdownState.CAUTION:
            return self._finish(DrawdownState.CAUTION, "; ".join(reasons), dd, prev, now,
                                equity_window=series[:-1],
                                equity=equity, peak_equity=peak_equity,
                                correlation_shock=correlation_shock,
                                data_anomaly=data_anomaly, venue_incident=venue_incident)

        # 回撤回到 caution 之下(CLEAR):只有从回撤态爬回来才需要双门
        if prev.state is DrawdownState.NORMAL:
            return self._finish(DrawdownState.NORMAL, reasons[0], dd, prev, now,
                                equity_window=series[:-1],
                                equity=equity, peak_equity=peak_equity,
                                correlation_shock=correlation_shock,
                                data_anomaly=data_anomaly, venue_incident=venue_incident)

        below = consecutive_below_caution_count(series, peak_equity=peak,
                                                caution_drawdown=th.caution_drawdown)
        since_low = observations_since_new_low(series)
        gate_performance = below >= th.recovery_consecutive_observations
        gate_stabilized = since_low >= th.recovery_stabilization_observations

        if gate_performance and gate_stabilized:
            obs = prev.observations_in_recovery + 1 if prev.state is DrawdownState.RECOVERY else 1
            if prev.state is DrawdownState.RECOVERY and obs >= th.recovery_exit_observations:
                return self._finish(
                    DrawdownState.NORMAL,
                    f"recovery_complete: {obs} observations with both gates held "
                    f"(ramp reached {th.recovery_max_ceiling}, full re-lever allowed)",
                    dd, prev, now, equity_window=series[:-1],
                    equity=equity, peak_equity=peak_equity,
                    correlation_shock=correlation_shock,
                    data_anomaly=data_anomaly, venue_incident=venue_incident,
                    observations_in_recovery=0)
            return self._finish(
                DrawdownState.RECOVERY,
                f"recovery_gates_met: dd<caution for {below} consecutive observations "
                f"(>={th.recovery_consecutive_observations}), no new low for {since_low} "
                f"(>={th.recovery_stabilization_observations}); slow re-risk ramp",
                dd, prev, now, equity_window=series[:-1],
                equity=equity, peak_equity=peak_equity,
                correlation_shock=correlation_shock,
                data_anomaly=data_anomaly, venue_incident=venue_incident,
                observations_in_recovery=obs)
        return self._finish(
            DrawdownState.CAUTION,
            f"recovery_gates_pending: performance_gate={gate_performance} "
            f"({below}/{th.recovery_consecutive_observations} consecutive below caution), "
            f"stabilization_gate={gate_stabilized} "
            f"({since_low}/{th.recovery_stabilization_observations} observations since new low)",
            dd, prev, now, equity_window=series[:-1],
            equity=equity, peak_equity=peak_equity,
            correlation_shock=correlation_shock,
            data_anomaly=data_anomaly, venue_incident=venue_incident)

    def _finish(self, state: DrawdownState, reason: str, drawdown_pct: float,
                prev: ControllerSnapshot, now: int, *, equity: object, peak_equity: object,
                equity_window: Sequence[float],
                correlation_shock: Optional[bool], data_anomaly: bool, venue_incident: bool,
                observations_in_recovery: int = 0) -> DrawdownDecision:
        ceiling = state_ceiling(state, observations_in_recovery=observations_in_recovery,
                                thresholds=self._thresholds)
        snapshot = ControllerSnapshot(state=state,
                                      observations_in_recovery=observations_in_recovery,
                                      reason=reason, updated_at=int(now))
        self._snapshot = snapshot
        explain: Dict[str, object] = {
            "now": int(now),
            "equity": equity if _positive_finite(equity) else None,
            "peak_equity": peak_equity if _positive_finite(peak_equity) else None,
            "drawdown_pct": drawdown_pct,
            "previous_state": prev.state.value,
            "state": state.value,
            "reason": reason,
            "inputs": {
                "correlation_shock": correlation_shock,
                "data_anomaly": bool(data_anomaly),
                "venue_incident": bool(venue_incident),
                "window_size": len(equity_window),
            },
            "recovery": {
                "observations_in_recovery": observations_in_recovery,
                "consecutive_below_caution": (
                    consecutive_below_caution_count(
                        list(equity_window) + ([float(equity)] if _positive_finite(equity) else []),
                        peak_equity=float(peak_equity),
                        caution_drawdown=self._thresholds.caution_drawdown)
                    if _positive_finite(peak_equity) and _positive_finite(equity) else None),
                "observations_since_new_low": (
                    observations_since_new_low(
                        [float(v) for v in equity_window if _positive_finite(v)]
                        + ([float(equity)] if _positive_finite(equity) else []))
                    if _positive_finite(equity) else None),
            },
            "state_ceiling": ceiling,
            "thresholds": self._thresholds.as_dict(),
        }
        return DrawdownDecision(state=state, reason=reason, drawdown_pct=drawdown_pct,
                                state_ceiling=ceiling, snapshot=snapshot, explain=explain)


# ---------------------------------------------------------------------------
# 风险乘数:分量 + min 组合(spec §10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComponentReading:
    """一个乘数分量的读数。`value` 恒在 [0,1](构造即钳制);status="UNKNOWN" 时 value 是
    保守默认(`unknown_component_default`,缺证据不放满配),reason 必须如实说明为什么未知。"""
    name: str
    value: float
    status: str                      # "OK" | "UNKNOWN"
    reason: str

    def __post_init__(self) -> None:
        if self.status not in ("OK", "UNKNOWN"):
            raise ValueError(f"status must be OK|UNKNOWN, got {self.status!r}")
        if (isinstance(self.value, bool)
                or not isinstance(self.value, (int, float))
                or not math.isfinite(float(self.value))):
            raise ValueError(f"component value must be a finite number, got {self.value!r}")
        object.__setattr__(self, "value", min(1.0, max(0.0, float(self.value))))

    def as_dict(self) -> Dict[str, object]:
        return {"value": self.value, "status": self.status, "reason": self.reason}


def drawdown_component(drawdown_pct: float,
                       thresholds: Optional[DrawdownThresholds] = None) -> ComponentReading:
    """回撤分量:回撤越深值越低(与状态天花板同一档梯,双处钉死同一保守语义)。"""
    th = thresholds or DrawdownThresholds()
    if (isinstance(drawdown_pct, bool)
            or not isinstance(drawdown_pct, (int, float))
            or not math.isfinite(float(drawdown_pct))):
        return ComponentReading(
            "drawdown",
            0.0,
            "UNKNOWN",
            f"invalid drawdown_pct {drawdown_pct!r} — fail-closed halt",
        )
    dd = max(0.0, float(drawdown_pct))
    if dd >= th.halt_drawdown:
        return ComponentReading("drawdown", 0.0, "OK", f"drawdown {dd:.4f} >= halt")
    if dd >= th.defensive_drawdown:
        return ComponentReading("drawdown", th.defensive_ceiling, "OK",
                                f"drawdown {dd:.4f} >= defensive")
    if dd >= th.caution_drawdown:
        return ComponentReading("drawdown", th.caution_ceiling, "OK",
                                f"drawdown {dd:.4f} >= caution")
    return ComponentReading("drawdown", 1.0, "OK", f"drawdown {dd:.4f} within caution")


def correlation_component(correlation_shock: Optional[bool],
                          thresholds: Optional[DrawdownThresholds] = None) -> ComponentReading:
    """相关冲击分量:True -> 深降(与 DEFENSIVE 同档);False -> 1.0;None(W3 读者给不出
    意见:制品缺失/畸形/status!=OK)-> UNKNOWN 保守默认。"""
    th = thresholds or DrawdownThresholds()
    if correlation_shock is True:
        return ComponentReading("correlation", th.defensive_ceiling, "OK",
                                "correlation shock detected")
    if correlation_shock is False:
        return ComponentReading("correlation", 1.0, "OK", "no correlation shock")
    return ComponentReading("correlation", th.unknown_component_default, "UNKNOWN",
                            "no usable W3 correlation-shock reading (artifact "
                            "absent/malformed/status!=OK) — fail-closed conservative")


def venue_health_component(available: Optional[int], total: Optional[int],
                           thresholds: Optional[DrawdownThresholds] = None
                           ) -> Tuple[ComponentReading, bool]:
    """场地健康分量 + 事故标志。输入 = 本轮 venue 只读探测(book_ticker)成功数/目标数
    (账户读在驱动器里若失败早已崩溃,到不了这里;时钟同步 fail-soft 不报失败,不作证据)。
    total=0(无目标)-> UNKNOWN(无从判断,不谎称健康);available=0 且有目标 -> 0.0 +
    incident=True(整源失联 = 场地事故,状态机 HALT);部分成功 -> 按可得比例给值(失联
    标的同时被跨源核价冻结,乘数再按数据可得性同比收缩)。"""
    th = thresholds or DrawdownThresholds()
    if (total is None or isinstance(total, bool)
            or not isinstance(total, int) or total <= 0):
        return ComponentReading("venue_health", th.unknown_component_default, "UNKNOWN",
                                "no target instruments — venue health not judgable"), False
    if available is None or isinstance(available, bool) or not isinstance(available, int):
        avail = 0
    else:
        avail = max(0, min(available, total))
    if avail <= 0:
        return ComponentReading("venue_health", 0.0, "OK",
                                f"all {total} venue book reads failed — venue incident"), True
    ratio = avail / float(total)
    if avail < total:
        return ComponentReading("venue_health", ratio, "OK",
                                f"partial venue book reads: {avail}/{total} available"), False
    return ComponentReading("venue_health", 1.0, "OK",
                            f"all {total} venue book reads available"), False


# ---------------------------------------------------------------------------
# 实盘制品读者(W3 相关冲击 / W1 live-vs-replay 衰退,2026-07-25 接真制品)
# ---------------------------------------------------------------------------

#: W1 衰退制品最大可信年龄(7 天,UNCALIBRATED 工程默认——制品由 tools/live_degradation.py
#: 按需重跑,超过一周的证据不再代表现行执行漏损,fail-closed 回退 UNKNOWN)。
DEGRADATION_ARTIFACT_MAX_AGE_MS = 7 * 24 * 3600 * 1000


def correlation_shock_from_artifact(payload: object) -> Optional[bool]:
    """W3 相关冲击制品(evidence/common_risk_decomposition.json)纯读者。真 schema:
    payload["correlation_shock"] = {"status": "OK"|"INSUFFICIENT_DATA",
    "in_shock_now": bool, "current": {"week","mean_abs_corr"}, "rolling": [...],
    "threshold": float, ...}(26 周滚动 mean|ρ| > 全样本均值+0.25 绝对边际,UNCALIBRATED;
    制品本身无 generated_at 时间戳——研究驱动器周更,新鲜度由消费方调度节奏担保)。
    status=="OK" 且 in_shock_now 严格 bool -> 返回之;制品/块缺失、status 非 OK、
    标志非 bool -> None(无意见 -> 分量 UNKNOWN 保守默认)。"""
    if not isinstance(payload, dict):
        return None
    block = payload.get("correlation_shock")
    if not isinstance(block, dict) or block.get("status") != "OK":
        return None
    flag = block.get("in_shock_now")
    if flag is True:
        # A legacy positive alarm may only reduce risk, so retaining it is
        # fail-safe even though the old profitability model is invalidated.
        return True
    if flag is not False:
        return None
    authority_record = payload.get("authoritative_backtest_record")
    # A quiet reading relaxes the UNKNOWN multiplier from 0.5 to 1.0 and is
    # therefore selection-bearing.  Never accept that direction from an old
    # self-described artifact.
    return (
        False
        if isinstance(authority_record, dict)
        and is_authoritative_backtest_record(authority_record)
        else None
    )


def _degradation_band_value(ratio: float, th: DrawdownThresholds) -> float:
    """live_net/replay_net 比值 -> 乘数分档(**UNCALIBRATED 工程默认**,非业务风险容忍度):
    ≥0.7 -> 1.0(衰退不构成实质漏损;ratio>1 即 live 跑赢 replay 同样 1.0——乘数只缩不放,
    绝不因此放大);0.4–0.7 -> caution 档(中等衰退减半);<0.4 -> defensive 档(严重衰退)。"""
    if ratio >= 0.7:
        return 1.0
    if ratio >= 0.4:
        return th.caution_ceiling
    return th.defensive_ceiling


def degradation_component_from_artifact(payload: object, strategy_id: str, venue: str,
                                        *, now_ms: int,
                                        thresholds: Optional[DrawdownThresholds] = None
                                        ) -> ComponentReading:
    """衰退分量(W1 实盘制品 evidence/live_degradation.json:live vs replay 净收益漏损,
    schema_version 1)。真 schema:{"generated_at_ms": int, "reports": [{"strategy_id",
    "venue", "live_degradation_ratio": float|null, "degradation_ratio_reason": str|null,
    "significance": {"level","reason"}, ...}]}。判定(全 fail-closed):
      - 制品缺失/非 dict -> UNKNOWN;
      - generated_at_ms 缺失/畸形 -> UNKNOWN(新鲜度无从确认,不放满配);
      - 制品年龄 > 7 天(与注入 now_ms 比)-> UNKNOWN(过期);
      - reports 中无 (strategy_id, venue) 条目 -> UNKNOWN(该策略×场地无实盘漏损证据);
      - ratio 为 null(sign-flip / replay≈0 地板:比值数学上无意义)-> UNKNOWN,**绝不把
        无意义比值翻成数字**(reason 原样带上 degradation_ratio_reason);
      - significance.level == "SMALL_SAMPLE"(制品自标样本太小、不构成显著性断言)->
        UNKNOWN;level 非可识别的 "DESCRIPTIVE_ONLY" -> UNKNOWN(未识别标签不放行);
      - 可信 ratio -> `_degradation_band_value` 分档(≥0.7→1.0 / 0.4–0.7→0.5 / <0.4→0.25,
        UNCALIBRATED)。
    2026-07-24 的前向兼容暂定 schema({"strategies": {...}} 猜版)已随真制品落地删除——
    猜版从未匹配现实,留着只会让手工构造的假载荷驱动乘数。"""
    th = thresholds or DrawdownThresholds()

    def _unknown(reason: str) -> ComponentReading:
        return ComponentReading("degradation", th.unknown_component_default, "UNKNOWN", reason)

    if not isinstance(payload, dict):
        return _unknown("degradation artifact absent or unreadable — fail-closed conservative")
    generated = payload.get("generated_at_ms")
    if not (isinstance(generated, (int, float)) and not isinstance(generated, bool)
            and math.isfinite(float(generated))):
        return _unknown("degradation artifact has no usable generated_at_ms — freshness "
                        "unverifiable, fail-closed conservative")
    age_ms = int(now_ms) - int(generated)
    if age_ms < 0:
        return _unknown(f"degradation artifact timestamp is in the future ({age_ms} ms) "
                        "— fail-closed conservative")
    if age_ms > DEGRADATION_ARTIFACT_MAX_AGE_MS:
        return _unknown(f"degradation artifact stale: age {age_ms} ms > "
                        f"{DEGRADATION_ARTIFACT_MAX_AGE_MS} ms (7d, UNCALIBRATED) "
                        "— fail-closed conservative")
    reports = payload.get("reports")
    row: Optional[dict] = None
    if isinstance(reports, list):
        for entry in reports:
            if (isinstance(entry, dict) and entry.get("strategy_id") == strategy_id
                    and entry.get("venue") == venue):
                row = entry
                break
    if row is None:
        return _unknown(f"no degradation report for {strategy_id}@{venue} — "
                        "fail-closed conservative")
    if payload.get("backtest_reference_status") == LEGACY_INVALIDATION_CODE:
        return ComponentReading(
            "degradation",
            th.defensive_ceiling,
            "UNKNOWN",
            f"{LEGACY_INVALIDATION_CODE}: replay economics are not usable for deploy scaling",
        )
    ratio = row.get("live_degradation_ratio")
    if ratio is None:
        return _unknown("degradation ratio meaningless "
                        f"({row.get('degradation_ratio_reason') or 'null ratio'}) — "
                        "not translated into a number")
    if not (isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
            and math.isfinite(float(ratio))):
        return _unknown(f"malformed live_degradation_ratio {ratio!r} — fail-closed conservative")
    significance = row.get("significance")
    level = significance.get("level") if isinstance(significance, dict) else None
    if level == "SMALL_SAMPLE":
        return _unknown("artifact's own significance label is SMALL_SAMPLE — ratio is "
                        f"descriptive-only, not acted on ({significance.get('reason')})")
    if level != "DESCRIPTIVE_ONLY":
        return _unknown(f"unrecognized significance level {level!r} — fail-closed conservative")
    authority_record = payload.get("authoritative_backtest_record")
    if not (
        isinstance(authority_record, dict)
        and is_authoritative_backtest_record(authority_record)
    ):
        return ComponentReading(
            "degradation",
            th.defensive_ceiling,
            "UNKNOWN",
            "degradation replay lacks independently admitted V2 backtest authority — "
            "legacy ratio ignored and defensive ceiling applied",
        )
    value = _degradation_band_value(float(ratio), th)
    return ComponentReading(
        "degradation", value, "OK",
        f"live/replay net ratio {float(ratio):.4f} -> band {value} "
        "(UNCALIBRATED mapping; DESCRIPTIVE_ONLY evidence)")


def unknown_component(name: str, reason: str,
                      thresholds: Optional[DrawdownThresholds] = None) -> ComponentReading:
    """暂无实盘制品的分量(vol_target/liquidity/capacity 等)-> UNKNOWN 保守默认。"""
    th = thresholds or DrawdownThresholds()
    return ComponentReading(name, th.unknown_component_default, "UNKNOWN", reason)


@dataclass(frozen=True)
class MultiplierResult:
    multiplier: float                # ∈ [0,1],min(分量, 状态天花板)
    binding: Tuple[str, ...]         # 取得 min 的分量名("state_ceiling" 含状态天花板)
    state_ceiling: float
    explain: Dict[str, object]


def combine_risk_multiplier(components: Sequence[ComponentReading], *,
                            state: DrawdownState,
                            observations_in_recovery: int = 0,
                            thresholds: Optional[DrawdownThresholds] = None) -> MultiplierResult:
    """spec §10:multiplier = min(所有分量, 状态天花板),钳进 [0.1]——**只缩不放**,
    任何模型输出都无法把乘数抬过 1 或抬过状态天花板(硬风险上限永不被覆盖)。"""
    th = thresholds or DrawdownThresholds()
    ceiling = state_ceiling(state, observations_in_recovery=observations_in_recovery,
                            thresholds=th)
    values = [c.value for c in components] + [ceiling]
    multiplier = min(1.0, max(0.0, min(values)))
    binding: List[str] = [c.name for c in components if c.value == multiplier]
    if ceiling == multiplier:
        binding.append("state_ceiling")
    explain: Dict[str, object] = {
        "multiplier": multiplier,
        "binding": list(binding),
        "state": state.value,
        "state_ceiling": ceiling,
        "components": {c.name: c.as_dict() for c in components},
        "unknown_default": th.unknown_component_default,
        "thresholds": th.as_dict(),
    }
    return MultiplierResult(multiplier=multiplier, binding=tuple(binding),
                            state_ceiling=ceiling, explain=explain)
