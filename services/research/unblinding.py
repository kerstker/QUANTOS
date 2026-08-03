"""预注册揭盲纪律(WP3 W3-1)—— 影子候选何时允许「揭盲」的纯核判定。

设计文档 ``docs/engineering/specs/2026-08-02-wp3-preregistered-unblinding-design.md`。
三条缺口各对应一个判定:

1. **有效独立观察数 ≠ 原始期数**:兑现窗口重叠(决策在上一决策兑现前开出)时两条
   窗口共享价格路径,不是两次独立观察。`effective_independent_periods` 用最早结束
   优先的贪心区间调度取最大互不重叠子集基数——重叠只少计、绝不多计。
2. **regime 覆盖未判定**:8 个前向期可能全落在同一市场 regime。
   `window_regime_labels` 对每个兑现窗口以**截至窗口末端**的 PIT 序列打二维
   (资金费 z 三分位 × 30d 实现波动 z 三分位)标签,`regime_coverage` 判定类数与
   集中度双条件。
3. **揭盲政策预注册 + 版本化**:`UnblindingPolicy` 内容寻址(`content_hash`),
   未来改门槛 = 新政策哈希,可审计。

**揭盲判定只消费计数与覆盖,绝不读收益/IC/PNL**(不以频繁查看结果替代证据——
绩效证据是另一个独立 gate 的事)。fail-closed:任一窗口缺 regime 标签 → SEALED +
REGIME_EVIDENCE_MISSING,绝不猜;窗口为空 → SEALED。纯核:无 IO/RNG/墙钟/网络,
时间全为调用方 int ms。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
import statistics
from typing import Mapping, Optional, Sequence

from quantos.contracts.hashing import stable_hash


UNCALIBRATED = "UNCALIBRATED"
POLICY_VERSION_V1 = "unblinding_policy.v1"

SEALED = "SEALED"
READY_TO_UNBLIND = "READY_TO_UNBLIND"


@dataclass(frozen=True)
class UnblindingPolicy:
    """揭盲门槛。全部阈值 UNCALIBRATED 工程默认(随证据落盘,待校准评审)。"""

    min_periods: int = 8
    min_independent_periods: int = 8
    min_regime_classes: int = 2
    max_single_regime_share: float = 0.75
    policy_version: str = POLICY_VERSION_V1

    def __post_init__(self) -> None:
        for name in ("min_periods", "min_independent_periods", "min_regime_classes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"unblinding policy {name} must be a positive int")
        share = self.max_single_regime_share
        if (
            isinstance(share, bool)
            or not isinstance(share, (int, float))
            or not math.isfinite(float(share))
            or not 0.0 < float(share) <= 1.0
        ):
            raise ValueError("unblinding policy max_single_regime_share must be in (0, 1]")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("unblinding policy policy_version is required")

    def content_hash(self) -> str:
        """内容寻址的政策身份:任一身份字段变化 → 哈希漂移。"""
        return stable_hash(
            {
                "min_periods": self.min_periods,
                "min_independent_periods": self.min_independent_periods,
                "min_regime_classes": self.min_regime_classes,
                "max_single_regime_share": float(self.max_single_regime_share),
                "policy_version": self.policy_version,
            }
        )


@dataclass(frozen=True)
class RealizationWindow:
    """一条 attested 兑现记录的窗口:[decided_ts, as_of_ts](ms)。"""

    decided_ts: int
    as_of_ts: int

    def __post_init__(self) -> None:
        for name in ("decided_ts", "as_of_ts"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"realization window {name} must be a non-negative int")
        if self.as_of_ts <= self.decided_ts:
            raise ValueError("realization window as_of_ts must exceed decided_ts")


def effective_independent_periods(windows: Sequence[RealizationWindow]) -> int:
    """最大互不重叠兑现窗口子集基数(最早结束优先贪心区间调度)。

    确定性、输入序无关;端点相接 `[t0,t1] [t1,t2]` 算**不重叠**。重叠只少计。
    """
    ordered = sorted(windows, key=lambda w: (w.as_of_ts, w.decided_ts))
    for window in ordered:
        if not isinstance(window, RealizationWindow):
            raise TypeError("windows must be RealizationWindow instances")
    count = 0
    last_end = -1
    for window in ordered:
        if window.decided_ts >= last_end:
            count += 1
            last_end = window.as_of_ts
    return count


def expanding_tercile_labels(
    values: Sequence[Optional[float]], min_history: int = 10
) -> list[Optional[int]]:
    """PIT 扩展窗三分位 → 0/1/2;历史不足或值缺失 → None(绝不暖启猜中档)。

    位置 i 的阈值只用 `values[:i+1]` 的有效值(仅过去+当前)——对未来数据追加
    **不变**(截断不变)。
    """
    if type(min_history) is not int or min_history < 1:
        raise ValueError("min_history must be a positive int")
    out: list[Optional[int]] = []
    seen: list[float] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        point = float(value)
        if not math.isfinite(point):
            out.append(None)
            continue
        seen.append(point)
        if len(seen) < min_history:
            out.append(None)
            continue
        ordered = sorted(seen)
        lo, hi = ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]
        out.append(0 if point <= lo else 2 if point >= hi else 1)
    return out


def trailing_zscore(
    values: Sequence[Optional[float]],
    window: int = 30,
    min_history: int = 10,
) -> list[Optional[float]]:
    """逐位置 trailing z:values[i] 相对 [i-window+1, i] 窗口的 z。样本不足/零方差 → None。

    只用 ≤ i 的数据 → 对追加未来数据不变。
    """
    if type(window) is not int or window < 1:
        raise ValueError("window must be a positive int")
    if type(min_history) is not int or min_history < 2:
        raise ValueError("min_history must be >= 2")
    out: list[Optional[float]] = []
    for i, value in enumerate(values):
        if value is None:
            out.append(None)
            continue
        point = float(value)
        if not math.isfinite(point):
            out.append(None)
            continue
        segment = [
            float(item)
            for item in values[max(0, i - window + 1) : i + 1]
            if item is not None and math.isfinite(float(item))
        ]
        if len(segment) < min_history:
            out.append(None)
            continue
        sd = statistics.pstdev(segment)
        if sd <= 0:
            out.append(None)
            continue
        out.append((point - statistics.fmean(segment)) / sd)
    return out


def expanding_zscore(
    values: Sequence[Optional[float]], min_history: int = 3
) -> list[Optional[float]]:
    """逐位置 expanding z:values[i] 相对全部 ≤ i 有效值的 z。样本不足/零方差 → None。"""
    if type(min_history) is not int or min_history < 2:
        raise ValueError("min_history must be >= 2")
    out: list[Optional[float]] = []
    seen: list[float] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        point = float(value)
        if not math.isfinite(point):
            out.append(None)
            continue
        seen.append(point)
        if len(seen) < min_history:
            out.append(None)
            continue
        sd = statistics.pstdev(seen)
        if sd <= 0:
            out.append(None)
            continue
        out.append((point - statistics.fmean(seen)) / sd)
    return out


def trailing_realized_vol(
    returns: Sequence[Optional[float]], window: int = 30
) -> list[Optional[float]]:
    """逐位置 trailing `window` 期实现波动(窗口未满或含缺失 → None,绝不缩短窗口硬算)。"""
    if type(window) is not int or window < 1:
        raise ValueError("window must be a positive int")
    out: list[Optional[float]] = []
    for i in range(len(returns)):
        if i + 1 < window:
            out.append(None)
            continue
        segment = returns[i + 1 - window : i + 1]
        if any(
            item is None or not math.isfinite(float(item)) for item in segment
        ):
            out.append(None)
            continue
        out.append(statistics.pstdev([float(item) for item in segment]))
    return out


def _label_key(label: object) -> str:
    if isinstance(label, (tuple, list)):
        return "-".join(str(part) for part in label)
    return str(label)


def regime_coverage(labels: Sequence[object]) -> dict:
    """逐窗口 regime 标签分布 → {n_classes, distribution, max_share, n_labeled}。

    None 标签不计入分布(缺失由上游 REGIME_EVIDENCE_MISSING 判定,不在此猜)。
    """
    counts: dict[str, int] = {}
    for label in labels:
        if label is None:
            continue
        key = _label_key(label)
        counts[key] = counts.get(key, 0) + 1
    n_labeled = sum(counts.values())
    max_share = max(counts.values()) / n_labeled if n_labeled else 0.0
    return {
        "n_classes": len(counts),
        "distribution": dict(sorted(counts.items())),
        "max_share": max_share,
        "n_labeled": n_labeled,
    }


def evaluate_unblinding(
    *,
    n_periods: int,
    windows: Sequence[RealizationWindow],
    regime_labels: Sequence[object],
    policy: UnblindingPolicy,
) -> dict:
    """揭盲判定(只消费计数与覆盖,绝不读收益/IC)。

    任一条件未满足 → SEALED + 全部未满足项清单(不短路);全过 → READY_TO_UNBLIND。
    """
    if not isinstance(policy, UnblindingPolicy):
        raise TypeError("policy must be an UnblindingPolicy")
    if type(n_periods) is not int or n_periods < 0:
        raise ValueError("n_periods must be a non-negative int")
    realization_windows = tuple(windows)
    labels = tuple(regime_labels)
    if len(labels) != len(realization_windows):
        raise ValueError("regime_labels must align with windows")
    blockers: list[str] = []
    if not realization_windows:
        blockers.append("NO_REALIZATION_WINDOWS")
    if n_periods < policy.min_periods:
        blockers.append("INSUFFICIENT_PERIODS")
    independent = effective_independent_periods(realization_windows)
    if independent < policy.min_independent_periods:
        blockers.append("INSUFFICIENT_INDEPENDENT_PERIODS")
    missing_labels = any(label is None for label in labels)
    if realization_windows and missing_labels:
        blockers.append("REGIME_EVIDENCE_MISSING")
    coverage = regime_coverage(labels)
    if realization_windows and not missing_labels:
        if coverage["n_classes"] < policy.min_regime_classes:
            blockers.append("REGIME_COVERAGE_INSUFFICIENT")
        if coverage["max_share"] > policy.max_single_regime_share:
            blockers.append("REGIME_CONCENTRATION_EXCEEDED")
    return {
        "status": SEALED if blockers else READY_TO_UNBLIND,
        "n_periods": n_periods,
        "independent_periods": independent,
        "regime": coverage,
        "blockers": blockers,
        "policy_hash": policy.content_hash(),
        "policy_version": policy.policy_version,
        "calibration": UNCALIBRATED,
    }


def _sorted_series(ts_value: Mapping[int, float]) -> tuple[list[int], list[float]]:
    points: dict[int, float] = {}
    for ts, value in ts_value.items():
        try:
            stamp = int(ts)
            point = float(value)
        except (TypeError, ValueError):
            continue
        if stamp < 0 or not math.isfinite(point):
            continue
        points[stamp] = point
    stamps = sorted(points)
    return stamps, [points[stamp] for stamp in stamps]


def _last_index_upto(stamps: Sequence[int], as_of_ts: int) -> Optional[int]:
    index = bisect_right(stamps, as_of_ts) - 1
    return index if index >= 0 else None


def window_regime_labels(
    windows: Sequence[RealizationWindow],
    *,
    funding_by_ts: Mapping[int, float],
    prices_by_ts: Mapping[int, float],
    funding_z_window: int = 30,
    funding_z_min_history: int = 10,
    rv_window: int = 30,
    rv_z_min_history: int = 3,
    tercile_min_history: int = 10,
) -> tuple[Optional[tuple[int, int]], ...]:
    """逐兑现窗口的二维 regime 标签(全 PIT,只用 ≤ as_of_ts 的数据)。

    - funding 维:日资金费的 trailing z(`funding_z_window`)→ expanding 三分位;
    - rv 维:日收益 trailing `rv_window` 实现波动的 expanding z → expanding 三分位。

    标签 = (funding_tercile, rv_tercile),≤ 9 类;任一维历史不足/缺数据 → None
    (上游 fail-closed SEALED,绝不猜)。全部窗口/历史参数 UNCALIBRATED 工程默认。
    """
    for window in windows:
        if not isinstance(window, RealizationWindow):
            raise TypeError("windows must be RealizationWindow instances")
    funding_ts, funding_values = _sorted_series(funding_by_ts)
    price_ts, price_values = _sorted_series(prices_by_ts)
    funding_labels = expanding_tercile_labels(
        trailing_zscore(
            funding_values,
            window=funding_z_window,
            min_history=funding_z_min_history,
        ),
        min_history=tercile_min_history,
    )
    returns = [
        None
        if price_values[i - 1] <= 0
        else price_values[i] / price_values[i - 1] - 1.0
        for i in range(1, len(price_values))
    ]
    rv_labels = expanding_tercile_labels(
        expanding_zscore(
            trailing_realized_vol(returns, window=rv_window),
            min_history=rv_z_min_history,
        ),
        min_history=tercile_min_history,
    )
    rv_ts = price_ts[1:]
    out: list[Optional[tuple[int, int]]] = []
    for window in windows:
        funding_index = _last_index_upto(funding_ts, window.as_of_ts)
        rv_index = _last_index_upto(rv_ts, window.as_of_ts)
        funding_label = (
            funding_labels[funding_index] if funding_index is not None else None
        )
        rv_label = rv_labels[rv_index] if rv_index is not None else None
        if funding_label is None or rv_label is None:
            out.append(None)
        else:
            out.append((funding_label, rv_label))
    return tuple(out)


__all__ = [
    "POLICY_VERSION_V1",
    "READY_TO_UNBLIND",
    "SEALED",
    "UNCALIBRATED",
    "RealizationWindow",
    "UnblindingPolicy",
    "effective_independent_periods",
    "evaluate_unblinding",
    "expanding_tercile_labels",
    "expanding_zscore",
    "regime_coverage",
    "trailing_realized_vol",
    "trailing_zscore",
    "window_regime_labels",
]
