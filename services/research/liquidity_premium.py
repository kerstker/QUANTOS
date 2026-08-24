"""Pure PIT-safe primitives for one frozen cross-sectional liquidity experiment.

The signal at decision row ``t`` uses only completed rows ``[t-lookback, t)``.
The current close is used only to prove the instrument is executable at ``t``;
the return label is the close at ``t + holding``.  There is no IO, wall clock,
RNG, registry mutation, lifecycle transition, broker, or order path here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import statistics
from typing import Literal

from services.alpha.panel_backtest import PanelPeriod
from services.research.alpha_expression import FieldPanel


LiquiditySignalMode = Literal["illiquidity", "liquidity", "hash"]
_HASH_SALT = "quantos.liquidity-premium.hash-control.v1"


class LiquidityDataIntegrityError(ValueError):
    """A selected historical holding cannot be priced without fabrication."""

    def __init__(
        self,
        *,
        reason_code: str,
        instrument: str,
        decision_row: int,
        label_row: int,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.instrument = instrument
        self.decision_row = decision_row
        self.label_row = label_row


@dataclass(frozen=True)
class LiquidityExperimentConfig:
    lookback_rows: int = 28
    holding_rows: int = 7
    universe_size: int = 48
    large_cap_size: int = 24

    def __post_init__(self) -> None:
        for name in (
            "lookback_rows",
            "holding_rows",
            "universe_size",
            "large_cap_size",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.large_cap_size >= self.universe_size:
            raise ValueError("large_cap_size must be smaller than universe_size")


@dataclass(frozen=True)
class LiquidityDecisionPlan:
    decision_row: int
    lookback_start: int
    lookback_stop: int
    universe_size: int
    ranked_universe: tuple[str, ...]
    trailing_median_adv: tuple[float, ...]
    eligible_adv: tuple[tuple[str, float], ...]


def _validate_panel(panel: FieldPanel) -> tuple[list, list]:
    if not isinstance(panel, FieldPanel):
        raise TypeError("panel must be a FieldPanel")
    close = panel.matrices.get("close")
    volume = panel.matrices.get("volume")
    if close is None or volume is None:
        raise ValueError("panel requires close and volume matrices")
    rows, columns = len(panel.event_times), len(panel.instruments)
    if len(close) != rows or len(volume) != rows:
        raise ValueError("panel matrix row count mismatch")
    if any(len(row) != columns for row in close) or any(
        len(row) != columns for row in volume
    ):
        raise ValueError("panel matrix column count mismatch")
    return close, volume


def _positive(value: object) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _nonnegative(value: object) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def build_decision_plan(
    panel: FieldPanel,
    decision_row: int,
    universe_size: int,
    config: LiquidityExperimentConfig = LiquidityExperimentConfig(),
) -> LiquidityDecisionPlan:
    """Freeze the investable universe from completed trailing dollar volume."""

    close, volume = _validate_panel(panel)
    if isinstance(decision_row, bool) or not isinstance(decision_row, int):
        raise TypeError("decision_row must be an integer")
    if (
        isinstance(universe_size, bool)
        or not isinstance(universe_size, int)
        or universe_size <= 0
    ):
        raise ValueError("universe_size must be a positive integer")
    start = decision_row - config.lookback_rows
    if start < 0 or decision_row >= len(panel.event_times):
        raise ValueError("decision row lacks a complete trailing window")

    ranked: list[tuple[float, str]] = []
    for column, instrument in enumerate(panel.instruments):
        # Current availability is observable at the execution close.  Current
        # volume is deliberately excluded from the trailing estimate.
        if not _positive(close[decision_row][column]):
            continue
        notionals = []
        for row in range(start, decision_row):
            price = close[row][column]
            units = volume[row][column]
            if not _positive(price) or not _nonnegative(units):
                notionals = []
                break
            notional = float(price) * float(units)
            if not math.isfinite(notional) or notional <= 0.0:
                notionals = []
                break
            notionals.append(notional)
        if len(notionals) == config.lookback_rows:
            ranked.append((statistics.median(notionals), instrument))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if len(ranked) < universe_size:
        raise ValueError(
            "insufficient complete trailing-liquidity instruments: "
            f"need {universe_size}, got {len(ranked)}"
        )
    selected = ranked[:universe_size]
    return LiquidityDecisionPlan(
        decision_row=decision_row,
        lookback_start=start,
        lookback_stop=decision_row,
        universe_size=universe_size,
        ranked_universe=tuple(instrument for _adv, instrument in selected),
        trailing_median_adv=tuple(float(adv) for adv, _instrument in selected),
        eligible_adv=tuple(
            sorted((instrument, float(adv)) for adv, instrument in ranked)
        ),
    )


def _hash_score(instrument: str) -> float:
    digest = hashlib.sha256(f"{_HASH_SALT}:{instrument}".encode("utf-8")).digest()
    return int.from_bytes(digest, "big") / float(1 << 256)


def signal_snapshot(
    panel: FieldPanel,
    plan: LiquidityDecisionPlan,
    mode: LiquiditySignalMode,
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...]]:
    """Return a stable cross-section, signal, and exact trailing ADV vector."""

    if mode not in ("illiquidity", "liquidity", "hash"):
        raise ValueError("mode must be 'illiquidity', 'liquidity', or 'hash'")
    _validate_panel(panel)
    if plan.decision_row >= len(panel.event_times):
        raise ValueError("decision plan is outside panel")
    adv_by_instrument = dict(
        zip(
            plan.ranked_universe,
            plan.trailing_median_adv,
            strict=True,
        )
    )
    instruments = tuple(sorted(plan.ranked_universe))
    adv = tuple(adv_by_instrument[instrument] for instrument in instruments)
    if mode == "hash":
        signals = tuple(_hash_score(instrument) for instrument in instruments)
    else:
        direction = -1.0 if mode == "illiquidity" else 1.0
        signals = tuple(direction * math.log(value) for value in adv)
    if not all(math.isfinite(value) for value in signals):
        raise ValueError("liquidity signals must be finite")
    return instruments, signals, adv


def find_first_eligible_decision_row(
    panel: FieldPanel,
    config: LiquidityExperimentConfig = LiquidityExperimentConfig(),
) -> int:
    """Find earliest decision with primary breadth using decision-time data only."""

    _validate_panel(panel)
    for decision_row in range(config.lookback_rows, len(panel.event_times)):
        try:
            build_decision_plan(panel, decision_row, config.universe_size, config)
        except ValueError:
            continue
        return decision_row
    raise ValueError("panel never reaches the frozen liquidity universe breadth")


def build_liquidity_periods(
    panel: FieldPanel,
    config: LiquidityExperimentConfig = LiquidityExperimentConfig(),
    *,
    mode: LiquiditySignalMode = "illiquidity",
    universe_size: int | None = None,
    start_row: int | None = None,
) -> tuple[tuple[PanelPeriod, ...], tuple[LiquidityDecisionPlan, ...]]:
    """Build weekly, non-overlapping decision/holding periods without skipping."""

    close, _volume = _validate_panel(panel)
    selected_size = config.universe_size if universe_size is None else universe_size
    decision_row = (
        find_first_eligible_decision_row(panel, config)
        if start_row is None
        else start_row
    )
    if isinstance(decision_row, bool) or not isinstance(decision_row, int):
        raise TypeError("start_row must be an integer or None")
    if decision_row < config.lookback_rows:
        raise ValueError("start_row lacks a complete trailing window")

    column_by_instrument = {
        instrument: index for index, instrument in enumerate(panel.instruments)
    }
    periods: list[PanelPeriod] = []
    plans: list[LiquidityDecisionPlan] = []
    previous_instruments: frozenset[str] = frozenset()
    while decision_row + config.holding_rows < len(panel.event_times):
        plan = build_decision_plan(panel, decision_row, selected_size, config)
        instruments, signals, adv = signal_snapshot(panel, plan, mode)
        eligible_adv = dict(plan.eligible_adv)
        exiting = previous_instruments - frozenset(instruments)
        missing_exit_adv = sorted(exiting - eligible_adv.keys())
        if missing_exit_adv:
            raise ValueError(
                "missing liquidation ADV for prior selected instruments: "
                + ",".join(missing_exit_adv)
            )
        future_row = decision_row + config.holding_rows
        returns = []
        for instrument in instruments:
            column = column_by_instrument[instrument]
            current = close[decision_row][column]
            future = close[future_row][column]
            if not _positive(current) or not _positive(future):
                raise LiquidityDataIntegrityError(
                    reason_code="MISSING_SELECTED_HOLDING_RETURN",
                    instrument=instrument,
                    decision_row=decision_row,
                    label_row=future_row,
                    detail=(
                        "missing holding return for selected instrument "
                        f"{instrument}"
                    ),
                )
            value = float(future) / float(current) - 1.0
            if not math.isfinite(value) or value <= -1.0:
                raise ValueError(
                    f"invalid holding return for selected instrument {instrument}"
                )
            returns.append(value)
        periods.append(
            PanelPeriod(
                instruments=instruments,
                signals=signals,
                forward_returns=tuple(returns),
                adv_notional=adv,
                event_time=int(panel.event_times[decision_row]),
                exit_adv_notional=tuple(
                    sorted((instrument, eligible_adv[instrument]) for instrument in exiting)
                ),
            )
        )
        plans.append(plan)
        previous_instruments = frozenset(instruments)
        decision_row += config.holding_rows
    if not periods:
        raise ValueError("panel has no complete liquidity holding period")
    return tuple(periods), tuple(plans)


def liquidity_signal_leakage_ok(
    panel: FieldPanel,
    config: LiquidityExperimentConfig = LiquidityExperimentConfig(),
    *,
    mode: LiquiditySignalMode = "illiquidity",
    universe_size: int | None = None,
    start_row: int | None = None,
) -> bool | None:
    """Machine-check signals are invariant to every strictly future input."""

    selected_size = config.universe_size if universe_size is None else universe_size
    try:
        _periods, plans = build_liquidity_periods(
            panel,
            config,
            mode=mode,
            universe_size=selected_size,
            start_row=start_row,
        )
    except (TypeError, ValueError):
        return None
    indices = sorted({0, len(plans) // 2, len(plans) - 1})
    assessed = False
    for index in indices:
        plan = plans[index]
        target = plan.decision_row
        baseline = signal_snapshot(panel, plan, mode)
        truncated = FieldPanel(
            panel.instruments,
            panel.event_times[: target + 1],
            {
                name: [list(row) for row in matrix[: target + 1]]
                for name, matrix in panel.matrices.items()
            },
        )
        try:
            rebuilt = build_decision_plan(
                truncated, target, selected_size, config
            )
            observed = signal_snapshot(truncated, rebuilt, mode)
        except (TypeError, ValueError):
            return False
        assessed = True
        if observed != baseline:
            return False
    return True if assessed else None


__all__ = [
    "LiquidityDataIntegrityError",
    "LiquidityDecisionPlan",
    "LiquidityExperimentConfig",
    "build_decision_plan",
    "build_liquidity_periods",
    "find_first_eligible_decision_row",
    "liquidity_signal_leakage_ok",
    "signal_snapshot",
]
