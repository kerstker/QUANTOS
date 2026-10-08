"""Event-gated liquidation research primitives.

The generic realtime cross-sectional evaluator always forms a ranked book,
including when every liquidation value is zero.  These primitives implement
the narrower economic contract for liquidation research: trade only when the
same decision bucket contains genuine forced-flow observations on both sides,
hold for one fixed five-minute window, then return to flat.

This module is deliberately pure.  It has no network, filesystem, database,
credential, broker, lifecycle, or order-submission authority.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict, dataclass
import math
import statistics
from typing import Mapping, Sequence

from services.research.realtime_alpha_discovery import (
    DAY_MS,
    RealtimeAlphaDiscoveryError,
    RealtimeAlphaPolicy,
    RealtimeFrame,
)


FIVE_MINUTES_MS = 5 * 60_000
LIQUIDATION_EVENT_LABEL_POLICY = (
    "TWO_SIDED_NONZERO_LIQUIDATION_THEN_FIRST_KNOWN_ENTRY_AND_EXIT_MIDS_"
    "WITH_INDEPENDENT_ROUND_TRIP_COSTS_AND_NO_PRORATED_FUNDING"
)
_EVENT_EPSILON = 1e-12


@dataclass(frozen=True)
class LiquidationEventSpec:
    candidate_id: str
    mechanism: str
    horizon_ms: int
    economic_hypothesis: str
    falsification_standard: str

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.mechanism:
            raise RealtimeAlphaDiscoveryError("liquidation event identity is required")
        if self.horizon_ms != FIVE_MINUTES_MS:
            raise RealtimeAlphaDiscoveryError("liquidation event horizon is fixed at five minutes")
        if not self.economic_hypothesis or not self.falsification_standard:
            raise RealtimeAlphaDiscoveryError("hypothesis and falsification are required")


@dataclass(frozen=True)
class LiquidationEventEvaluation:
    candidate_id: str
    mechanism: str
    status: str
    horizon_ms: int
    n_events: int
    mean_breadth: float | None
    mean_active_instruments: float | None
    gross_sharpe: float
    gross_return: float
    mean_gross_return: float | None
    net_sharpe: float
    net_t_stat: float | None
    net_return: float
    mean_net_return: float | None
    max_drawdown: float
    positive_event_fraction: float | None
    total_cost: float
    label_policy: str
    pressure: Mapping[str, Mapping[str, float]]
    event_times: tuple[int, ...]
    gross_returns: tuple[float, ...]
    base_costs: tuple[float, ...]
    net_returns: tuple[float, ...]

    def as_dict(self, *, include_returns: bool = False) -> dict[str, object]:
        payload = asdict(self)
        if not include_returns:
            for key in ("event_times", "gross_returns", "base_costs", "net_returns"):
                payload.pop(key)
        return payload


def default_liquidation_event_specs() -> tuple[LiquidationEventSpec, ...]:
    """Return the frozen two-trial, one-horizon liquidation budget."""
    return (
        LiquidationEventSpec(
            candidate_id="rt_liquidation_event_exhaustion_5m_v1",
            mechanism="liquidation_event_exhaustion",
            horizon_ms=FIVE_MINUTES_MS,
            economic_hypothesis=(
                "Simultaneous cross-sectional long and short liquidation shocks exhaust "
                "forced flow, so the liquidated sides reverse over the next five minutes."
            ),
            falsification_standard=(
                "The event-only reversal must survive an independent round trip, doubled "
                "costs, chronological holdout, and removal of the largest event."
            ),
        ),
        LiquidationEventSpec(
            candidate_id="rt_liquidation_event_cascade_5m_v1",
            mechanism="liquidation_event_cascade",
            horizon_ms=FIVE_MINUTES_MS,
            economic_hypothesis=(
                "Simultaneous cross-sectional long and short liquidation shocks continue "
                "as forced flow cascades over the next five minutes."
            ),
            falsification_standard=(
                "The event-only continuation must survive an independent round trip, "
                "doubled costs, chronological holdout, and removal of the largest event."
            ),
        ),
    )


def _compound(returns: Sequence[float]) -> tuple[float, tuple[float, ...]]:
    equity = 1.0
    curve: list[float] = []
    for value in returns:
        equity *= 1.0 + value
        curve.append(equity)
    return equity - 1.0, tuple(curve)


def _max_drawdown(curve: Sequence[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def _event_rate_sharpe(returns: Sequence[float], event_times: Sequence[int]) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = statistics.pstdev(returns)
    elapsed_ms = event_times[-1] - event_times[0]
    if deviation == 0 or elapsed_ms <= 0:
        return 0.0
    events_per_year = len(returns) * DAY_MS * 365.0 / elapsed_ms
    return statistics.fmean(returns) / deviation * math.sqrt(events_per_year)


def _t_stat(returns: Sequence[float]) -> float | None:
    if len(returns) < 2:
        return None
    deviation = statistics.pstdev(returns)
    if deviation == 0:
        return None
    return statistics.fmean(returns) / (deviation / math.sqrt(len(returns)))


def _empty_evaluation(spec: LiquidationEventSpec) -> LiquidationEventEvaluation:
    return LiquidationEventEvaluation(
        candidate_id=spec.candidate_id,
        mechanism=spec.mechanism,
        status="NO_TWO_SIDED_EVENTS",
        horizon_ms=spec.horizon_ms,
        n_events=0,
        mean_breadth=None,
        mean_active_instruments=None,
        gross_sharpe=0.0,
        gross_return=0.0,
        mean_gross_return=None,
        net_sharpe=0.0,
        net_t_stat=None,
        net_return=0.0,
        mean_net_return=None,
        max_drawdown=0.0,
        positive_event_fraction=None,
        total_cost=0.0,
        label_policy=LIQUIDATION_EVENT_LABEL_POLICY,
        pressure={},
        event_times=(),
        gross_returns=(),
        base_costs=(),
        net_returns=(),
    )


def evaluate_liquidation_event_spec(
    spec: LiquidationEventSpec,
    frames_by_instrument: Mapping[str, Sequence[RealtimeFrame]],
    *,
    policy: RealtimeAlphaPolicy | None = None,
) -> LiquidationEventEvaluation:
    """Evaluate one fixed liquidation mechanism on non-overlapping events.

    A qualifying decision must have at least ``min_cross_section`` available
    instruments and at least one strictly positive and one strictly negative
    liquidation observation.  Only the non-zero instruments are traded.  Each
    event enters and exits independently, so every observation pays the full
    round trip and no position leaks across an inactive bucket.
    """
    policy = policy or RealtimeAlphaPolicy()
    if spec.horizon_ms != FIVE_MINUTES_MS:
        raise RealtimeAlphaDiscoveryError("liquidation event horizon is fixed at five minutes")
    if spec.mechanism not in {
        "liquidation_event_exhaustion",
        "liquidation_event_cascade",
    }:
        raise RealtimeAlphaDiscoveryError("unknown liquidation event mechanism")

    normalized: dict[str, tuple[RealtimeFrame, ...]] = {}
    times_by_instrument: dict[str, tuple[int, ...]] = {}
    decision_rows: dict[int, list[RealtimeFrame]] = {}
    for instrument, raw_frames in sorted(frames_by_instrument.items()):
        frames = tuple(sorted(raw_frames, key=lambda item: item.decision_time_ms))
        if any(item.instrument_id != instrument for item in frames):
            raise RealtimeAlphaDiscoveryError("frame instrument does not match its series key")
        times = tuple(item.decision_time_ms for item in frames)
        if len(times) != len(set(times)):
            raise RealtimeAlphaDiscoveryError("frame decision times must be unique per instrument")
        normalized[instrument] = frames
        times_by_instrument[instrument] = times
        for frame in frames:
            decision_rows.setdefault(frame.decision_time_ms, []).append(frame)

    event_times: list[int] = []
    gross_returns: list[float] = []
    base_costs: list[float] = []
    breadths: list[int] = []
    active_counts: list[int] = []
    next_eligible_time = -1
    for decision_time, frames in sorted(decision_rows.items()):
        if decision_time < next_eligible_time or len(frames) < policy.min_cross_section:
            continue
        positive = [
            item for item in frames if item.liquidation_imbalance_5s > _EVENT_EPSILON
        ]
        negative = [
            item for item in frames if item.liquidation_imbalance_5s < -_EVENT_EPSILON
        ]
        if not positive or not negative:
            continue

        rows: list[tuple[RealtimeFrame, RealtimeFrame, RealtimeFrame]] = []
        for frame in positive + negative:
            instrument = frame.instrument_id
            series = normalized[instrument]
            times = times_by_instrument[instrument]
            entry_target = decision_time + policy.execution_latency_ms
            entry_index = bisect_left(times, entry_target)
            exit_target = decision_time + policy.execution_latency_ms + spec.horizon_ms
            exit_index = bisect_left(times, exit_target)
            if entry_index >= len(series) or exit_index >= len(series):
                continue
            entry = series[entry_index]
            exit_frame = series[exit_index]
            if (
                entry.decision_time_ms - entry_target > policy.max_label_lateness_ms
                or exit_frame.decision_time_ms - exit_target
                > policy.max_label_lateness_ms
            ):
                continue
            rows.append((frame, entry, exit_frame))

        positive_rows = [row for row in rows if row[0].liquidation_imbalance_5s > 0]
        negative_rows = [row for row in rows if row[0].liquidation_imbalance_5s < 0]
        if not positive_rows or not negative_rows:
            continue

        direction = 1.0 if spec.mechanism == "liquidation_event_exhaustion" else -1.0
        weights: dict[str, float] = {}
        for frame, _, _ in positive_rows:
            weights[frame.instrument_id] = direction * 0.5 / len(positive_rows)
        for frame, _, _ in negative_rows:
            weights[frame.instrument_id] = -direction * 0.5 / len(negative_rows)

        gross = 0.0
        spread_cost = 0.0
        for frame, entry, exit_frame in rows:
            weight = weights[frame.instrument_id]
            price_return = exit_frame.mid_price / entry.mid_price - 1.0
            gross += weight * price_return
            spread_cost += (
                abs(weight)
                * (entry.spread_bps / 2.0 + exit_frame.spread_bps / 2.0)
                / 10_000.0
            )
        # Gross exposure is exactly one at entry and again at exit.
        fixed_cost = 2.0 * (policy.fee_bps + policy.slippage_bps) / 10_000.0
        event_times.append(decision_time)
        gross_returns.append(gross)
        base_costs.append(fixed_cost + spread_cost)
        breadths.append(len(frames))
        active_counts.append(len(rows))
        next_eligible_time = decision_time + policy.execution_latency_ms + spec.horizon_ms

    if not event_times:
        return _empty_evaluation(spec)

    pressure: dict[str, dict[str, float]] = {}
    default_net: tuple[float, ...] = ()
    for multiplier in (1.0, 2.0):
        net = tuple(
            gross - cost * multiplier
            for gross, cost in zip(gross_returns, base_costs, strict=True)
        )
        total_return, curve = _compound(net)
        pressure[f"{multiplier:.1f}x"] = {
            "net_sharpe": _event_rate_sharpe(net, event_times),
            "net_return": total_return,
            "max_drawdown": _max_drawdown(curve),
            "total_cost": sum(base_costs) * multiplier,
        }
        if multiplier == 1.0:
            default_net = net

    gross_return, _ = _compound(gross_returns)
    net_return, curve = _compound(default_net)
    status = (
        "EVALUATED"
        if len(event_times) >= policy.min_evaluation_periods
        else "INSUFFICIENT_EVENTS"
    )
    return LiquidationEventEvaluation(
        candidate_id=spec.candidate_id,
        mechanism=spec.mechanism,
        status=status,
        horizon_ms=spec.horizon_ms,
        n_events=len(event_times),
        mean_breadth=statistics.fmean(breadths),
        mean_active_instruments=statistics.fmean(active_counts),
        gross_sharpe=_event_rate_sharpe(gross_returns, event_times),
        gross_return=gross_return,
        mean_gross_return=statistics.fmean(gross_returns),
        net_sharpe=_event_rate_sharpe(default_net, event_times),
        net_t_stat=_t_stat(default_net),
        net_return=net_return,
        mean_net_return=statistics.fmean(default_net),
        max_drawdown=_max_drawdown(curve),
        positive_event_fraction=sum(value > 0 for value in default_net) / len(default_net),
        total_cost=sum(base_costs),
        label_policy=LIQUIDATION_EVENT_LABEL_POLICY,
        pressure=pressure,
        event_times=tuple(event_times),
        gross_returns=tuple(gross_returns),
        base_costs=tuple(base_costs),
        net_returns=default_net,
    )


__all__ = [
    "FIVE_MINUTES_MS",
    "LIQUIDATION_EVENT_LABEL_POLICY",
    "LiquidationEventEvaluation",
    "LiquidationEventSpec",
    "default_liquidation_event_specs",
    "evaluate_liquidation_event_spec",
]
