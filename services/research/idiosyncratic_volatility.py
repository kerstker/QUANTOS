"""PIT-safe primitives for a frozen crypto idiosyncratic-volatility test.

The decision at row ``t`` estimates daily residual volatility from completed
rows strictly before ``t``.  Liquidity also uses only completed rows.  Future
prices are consumed solely as labels.  A contract that terminates inside the
holding window is retained and forcibly exited at its final observed close;
non-terminal holes fail closed instead of being confused with delisting.

This module is deterministic and pure: no IO, wall clock, RNG, registry,
lifecycle, broker, credential, or order path.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import statistics
from typing import Literal, Sequence

from services.alpha.panel_backtest import PanelPeriod
from services.research.alpha_expression import FieldPanel


IvolSignalMode = Literal[
    "idiosyncratic", "low_idiosyncratic", "total", "hash"
]
_HASH_SALT = "quantos.crypto-ivol.hash-control.v1"
_DAY_MS = 86_400_000


class IvolDataIntegrityError(ValueError):
    """A selected holding label cannot be constructed without fabrication."""

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


class IvolBreadthError(ValueError):
    """A scheduled decision cannot satisfy the frozen primary breadth."""

    def __init__(self, *, decision_row: int, available: int, required: int) -> None:
        super().__init__(
            f"only {available} complete names for frozen universe {required}"
        )
        self.decision_row = decision_row
        self.available = available
        self.required = required


@dataclass(frozen=True)
class IvolExperimentConfig:
    return_lookback_rows: int = 28
    holding_rows: int = 7
    universe_size: int = 60
    liquid_size: int = 30
    max_terminal_events_per_period: int = 2

    def __post_init__(self) -> None:
        values = (
            self.return_lookback_rows,
            self.holding_rows,
            self.universe_size,
            self.liquid_size,
            self.max_terminal_events_per_period,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise TypeError("experiment dimensions must be integers")
        if any(value <= 0 for value in values[:-1]):
            raise ValueError("experiment dimensions must be positive")
        if self.max_terminal_events_per_period < 0:
            raise ValueError("max terminal events per period cannot be negative")
        if self.liquid_size >= self.universe_size:
            raise ValueError("liquid_size must be smaller than universe_size")


@dataclass(frozen=True)
class IvolDecisionPlan:
    decision_row: int
    lookback_start: int
    lookback_stop: int
    universe_size: int
    ranked_universe: tuple[str, ...]
    trailing_median_adv: tuple[float, ...]
    idiosyncratic_volatility: tuple[float, ...]
    total_volatility: tuple[float, ...]
    eligible_adv: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ForcedExitEvent:
    instrument: str
    decision_row: int
    label_row: int
    exit_row: int
    exit_adv_notional: float | None = None
    decision_time: int | None = None


def _positive(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _validate_panel(panel: FieldPanel) -> tuple[Sequence[Sequence[object]], Sequence[Sequence[object]]]:
    close = panel.matrices.get("close")
    volume = panel.matrices.get("volume")
    rows = len(panel.event_times)
    columns = len(panel.instruments)
    if not panel.instruments or len(set(panel.instruments)) != columns:
        raise ValueError("panel instruments must be non-empty and unique")
    if rows < 2 or any(
        int(panel.event_times[index]) <= int(panel.event_times[index - 1])
        for index in range(1, rows)
    ):
        raise ValueError("panel event_times must be strictly increasing")
    if any(
        int(panel.event_times[index]) - int(panel.event_times[index - 1]) != _DAY_MS
        for index in range(1, rows)
    ):
        raise ValueError("idiosyncratic-volatility panel must use a complete UTC daily grid")
    for name, matrix in (("close", close), ("volume", volume)):
        if matrix is None or len(matrix) != rows or any(len(row) != columns for row in matrix):
            raise ValueError(f"panel {name} matrix must align with its axes")
    return close, volume


def _daily_returns(values: Sequence[object]) -> tuple[float, ...]:
    out = []
    for previous, current in zip(values, values[1:]):
        if not _positive(previous) or not _positive(current):
            raise ValueError("return window contains a missing or invalid close")
        value = float(current) / float(previous) - 1.0
        if not math.isfinite(value) or value <= -1.0:
            raise ValueError("return window contains an invalid return")
        out.append(value)
    return tuple(out)


def _residual_volatility(
    returns: Sequence[float], market: Sequence[float]
) -> float:
    mean_asset = statistics.fmean(returns)
    mean_market = statistics.fmean(market)
    market_var = math.fsum((value - mean_market) ** 2 for value in market)
    beta = (
        0.0
        if market_var <= 1e-24
        else math.fsum(
            (asset - mean_asset) * (factor - mean_market)
            for asset, factor in zip(returns, market, strict=True)
        )
        / market_var
    )
    residuals = tuple(
        asset - mean_asset - beta * (factor - mean_market)
        for asset, factor in zip(returns, market, strict=True)
    )
    return max(statistics.pstdev(residuals), 1e-12)


def build_decision_plan(
    panel: FieldPanel,
    decision_row: int,
    universe_size: int,
    config: IvolExperimentConfig = IvolExperimentConfig(),
) -> IvolDecisionPlan:
    """Build one liquid universe and its residual-volatility cross-section."""

    close, volume = _validate_panel(panel)
    if isinstance(decision_row, bool) or not isinstance(decision_row, int):
        raise TypeError("decision_row must be an integer")
    if isinstance(universe_size, bool) or not isinstance(universe_size, int):
        raise TypeError("universe_size must be an integer")
    if universe_size <= 1:
        raise ValueError("universe_size must be greater than one")
    earliest = config.return_lookback_rows + 1
    if decision_row < earliest or decision_row >= len(panel.event_times):
        raise ValueError("decision_row lacks a complete pre-decision return window")

    close_start = decision_row - config.return_lookback_rows - 1
    adv_start = decision_row - config.return_lookback_rows
    eligible: list[tuple[str, int, float, tuple[float, ...]]] = []
    for column, instrument in enumerate(panel.instruments):
        close_window = [close[row][column] for row in range(close_start, decision_row)]
        if not _positive(close[decision_row][column]) or not all(
            _positive(value) for value in close_window
        ):
            continue
        notionals = []
        complete = True
        for row in range(adv_start, decision_row):
            if not _positive(close[row][column]) or not _positive(volume[row][column]):
                complete = False
                break
            notionals.append(float(close[row][column]) * float(volume[row][column]))
        if not complete:
            continue
        eligible.append(
            (
                instrument,
                column,
                statistics.median(notionals),
                _daily_returns(close_window),
            )
        )
    if len(eligible) < universe_size:
        raise IvolBreadthError(
            decision_row=decision_row,
            available=len(eligible),
            required=universe_size,
        )
    ranked = sorted(eligible, key=lambda item: (-item[2], item[0]))[:universe_size]
    market = tuple(
        statistics.fmean(item[3][row] for item in ranked)
        for row in range(config.return_lookback_rows)
    )
    ivol = tuple(_residual_volatility(item[3], market) for item in ranked)
    total = tuple(max(statistics.pstdev(item[3]), 1e-12) for item in ranked)
    return IvolDecisionPlan(
        decision_row=decision_row,
        lookback_start=close_start,
        lookback_stop=decision_row,
        universe_size=universe_size,
        ranked_universe=tuple(item[0] for item in ranked),
        trailing_median_adv=tuple(float(item[2]) for item in ranked),
        idiosyncratic_volatility=ivol,
        total_volatility=total,
        eligible_adv=tuple(sorted((item[0], float(item[2])) for item in eligible)),
    )


def _hash_signal(instrument: str) -> float:
    digest = hashlib.sha256(f"{_HASH_SALT}:{instrument}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def signal_snapshot(
    panel: FieldPanel,
    plan: IvolDecisionPlan,
    mode: IvolSignalMode,
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...]]:
    """Return aligned instruments, frozen signal, and trailing dollar ADV."""

    del panel  # The immutable plan contains every signal-time input.
    if mode == "idiosyncratic":
        signals = plan.idiosyncratic_volatility
    elif mode == "low_idiosyncratic":
        signals = tuple(-value for value in plan.idiosyncratic_volatility)
    elif mode == "total":
        signals = plan.total_volatility
    elif mode == "hash":
        signals = tuple(_hash_signal(name) for name in plan.ranked_universe)
    else:
        raise ValueError(f"unsupported ivol signal mode: {mode}")
    if not all(math.isfinite(value) for value in signals):
        raise ValueError("ivol signals must be finite")
    return plan.ranked_universe, signals, plan.trailing_median_adv


def find_first_eligible_decision_row(
    panel: FieldPanel,
    config: IvolExperimentConfig = IvolExperimentConfig(),
) -> int:
    _validate_panel(panel)
    for decision_row in range(config.return_lookback_rows + 1, len(panel.event_times)):
        try:
            build_decision_plan(panel, decision_row, config.universe_size, config)
        except ValueError:
            continue
        return decision_row
    raise ValueError("panel never reaches the frozen ivol universe breadth")


def find_contiguous_sample_stop_row(
    panel: FieldPanel,
    start_row: int,
    config: IvolExperimentConfig = IvolExperimentConfig(),
) -> int:
    """Return the exclusive first scheduled primary-breadth failure.

    Once the frozen top-60 research universe becomes incomplete, later rows are
    never cherry-picked back into the sample.  This boundary depends only on
    signal-time data availability and is computed before any return statistic.
    """

    close, _volume = _validate_panel(panel)
    if isinstance(start_row, bool) or not isinstance(start_row, int):
        raise TypeError("start_row must be an integer")
    if start_row < config.return_lookback_rows + 1:
        raise ValueError("start_row lacks a complete trailing return window")
    decision_row = start_row
    while decision_row + config.holding_rows < len(panel.event_times):
        try:
            plan = build_decision_plan(
                panel, decision_row, config.universe_size, config
            )
        except IvolBreadthError:
            return decision_row
        label_row = decision_row + config.holding_rows
        column_by_name = {
            name: index for index, name in enumerate(panel.instruments)
        }
        terminal_count = 0
        for instrument in plan.ranked_universe:
            column = column_by_name[instrument]
            if _positive(close[label_row][column]):
                continue
            if not any(
                _positive(close[row][column])
                for row in range(label_row + 1, len(close))
            ):
                terminal_count += 1
        if terminal_count > config.max_terminal_events_per_period:
            return decision_row
        decision_row += config.holding_rows
    return decision_row


def sample_stop_diagnostics(
    panel: FieldPanel,
    stop_row: int,
    config: IvolExperimentConfig = IvolExperimentConfig(),
) -> dict[str, object]:
    """Explain a frozen exclusive sample boundary without return statistics."""

    close, _volume = _validate_panel(panel)
    try:
        plan = build_decision_plan(panel, stop_row, config.universe_size, config)
    except IvolBreadthError as failure:
        return {
            "reason": "PRIMARY_SIGNAL_BREADTH_FAILURE",
            "available_names": failure.available,
            "required_names": failure.required,
            "terminal_count": 0,
            "terminal_names": (),
        }
    label_row = stop_row + config.holding_rows
    if label_row >= len(panel.event_times):
        return {
            "reason": "PANEL_END_NO_COMPLETE_LABEL",
            "available_names": plan.universe_size,
            "required_names": config.universe_size,
            "terminal_count": 0,
            "terminal_names": (),
        }
    column_by_name = {name: index for index, name in enumerate(panel.instruments)}
    terminal_names = []
    for instrument in plan.ranked_universe:
        column = column_by_name[instrument]
        if _positive(close[label_row][column]):
            continue
        if not any(
            _positive(close[row][column])
            for row in range(label_row + 1, len(close))
        ):
            terminal_names.append(instrument)
    return {
        "reason": (
            "CLUSTERED_TERMINAL_RIGHT_CENSORING"
            if len(terminal_names) > config.max_terminal_events_per_period
            else "CALLER_DEFINED_STOP"
        ),
        "available_names": plan.universe_size,
        "required_names": config.universe_size,
        "terminal_count": len(terminal_names),
        "terminal_names": tuple(sorted(terminal_names)),
    }


def _holding_outcome(
    panel: FieldPanel,
    close: Sequence[Sequence[object]],
    volume: Sequence[Sequence[object]],
    *,
    instrument: str,
    column: int,
    decision_row: int,
    label_row: int,
    fallback_adv: float,
) -> tuple[float, ForcedExitEvent | None]:
    current = close[decision_row][column]
    exact = close[label_row][column]
    if _positive(exact):
        value = float(exact) / float(current) - 1.0
        return value, None
    if any(_positive(close[row][column]) for row in range(label_row + 1, len(close))):
        raise IvolDataIntegrityError(
            reason_code="MISSING_NON_TERMINAL_HOLDING_LABEL",
            instrument=instrument,
            decision_row=decision_row,
            label_row=label_row,
            detail=f"non-terminal data gap for selected instrument {instrument}",
        )
    observed = [
        row
        for row in range(decision_row + 1, label_row + 1)
        if _positive(close[row][column])
    ]
    if not observed:
        raise IvolDataIntegrityError(
            reason_code="NO_POST_DECISION_TRADABLE_EXIT",
            instrument=instrument,
            decision_row=decision_row,
            label_row=label_row,
            detail=f"no post-decision tradable exit for selected instrument {instrument}",
        )
    exit_row = observed[-1]
    exit_adv = fallback_adv
    if _positive(volume[exit_row][column]):
        exit_adv = float(close[exit_row][column]) * float(volume[exit_row][column])
    value = float(close[exit_row][column]) / float(current) - 1.0
    return value, ForcedExitEvent(
        instrument=instrument,
        decision_row=decision_row,
        label_row=label_row,
        exit_row=exit_row,
        exit_adv_notional=exit_adv,
        decision_time=int(panel.event_times[decision_row]),
    )


def build_ivol_periods(
    panel: FieldPanel,
    config: IvolExperimentConfig = IvolExperimentConfig(),
    *,
    mode: IvolSignalMode = "idiosyncratic",
    universe_size: int | None = None,
    start_row: int | None = None,
    stop_row: int | None = None,
) -> tuple[
    tuple[PanelPeriod, ...],
    tuple[IvolDecisionPlan, ...],
    tuple[ForcedExitEvent, ...],
]:
    """Build weekly non-overlapping holdings, retaining terminal contracts."""

    close, volume = _validate_panel(panel)
    selected_size = config.universe_size if universe_size is None else universe_size
    decision_row = (
        find_first_eligible_decision_row(panel, config)
        if start_row is None
        else start_row
    )
    if isinstance(decision_row, bool) or not isinstance(decision_row, int):
        raise TypeError("start_row must be an integer or None")
    if decision_row < config.return_lookback_rows + 1:
        raise ValueError("start_row lacks a complete trailing return window")
    sample_stop = (
        find_contiguous_sample_stop_row(panel, decision_row, config)
        if stop_row is None
        else stop_row
    )
    if isinstance(sample_stop, bool) or not isinstance(sample_stop, int):
        raise TypeError("stop_row must be an integer or None")
    if sample_stop <= decision_row or sample_stop > len(panel.event_times):
        raise ValueError("stop_row must be after start_row and within the panel")
    column_by_name = {name: index for index, name in enumerate(panel.instruments)}
    periods: list[PanelPeriod] = []
    plans: list[IvolDecisionPlan] = []
    forced_events: list[ForcedExitEvent] = []
    forced_exit_adv: dict[str, float] = {}
    previous_instruments: frozenset[str] = frozenset()
    while (
        decision_row < sample_stop
        and decision_row + config.holding_rows < len(panel.event_times)
    ):
        plan = build_decision_plan(panel, decision_row, selected_size, config)
        instruments, signals, adv = signal_snapshot(panel, plan, mode)
        label_row = decision_row + config.holding_rows
        returns = []
        plan_adv = dict(zip(instruments, adv, strict=True))
        for instrument in instruments:
            value, forced = _holding_outcome(
                panel,
                close,
                volume,
                instrument=instrument,
                column=column_by_name[instrument],
                decision_row=decision_row,
                label_row=label_row,
                fallback_adv=plan_adv[instrument],
            )
            if not math.isfinite(value) or value <= -1.0:
                raise IvolDataIntegrityError(
                    reason_code="INVALID_SELECTED_HOLDING_RETURN",
                    instrument=instrument,
                    decision_row=decision_row,
                    label_row=label_row,
                    detail=f"invalid selected holding return for {instrument}",
                )
            returns.append(value)
            if forced is not None:
                forced_events.append(forced)
                forced_exit_adv[instrument] = float(forced.exit_adv_notional or plan_adv[instrument])
        eligible_adv = dict(plan.eligible_adv)
        exiting = previous_instruments - frozenset(instruments)
        missing_exit_adv = sorted(
            instrument
            for instrument in exiting
            if instrument not in eligible_adv and instrument not in forced_exit_adv
        )
        if missing_exit_adv:
            raise IvolDataIntegrityError(
                reason_code="MISSING_DYNAMIC_EXIT_LIQUIDITY",
                instrument=missing_exit_adv[0],
                decision_row=decision_row,
                label_row=label_row,
                detail="missing dynamic-universe liquidation liquidity",
            )
        periods.append(
            PanelPeriod(
                instruments=instruments,
                signals=signals,
                forward_returns=tuple(returns),
                adv_notional=adv,
                event_time=int(panel.event_times[decision_row]),
                exit_adv_notional=tuple(
                    sorted(
                        (
                            instrument,
                            (
                                eligible_adv[instrument]
                                if instrument in eligible_adv
                                else forced_exit_adv[instrument]
                            ),
                        )
                        for instrument in exiting
                    )
                ),
            )
        )
        plans.append(plan)
        previous_instruments = frozenset(instruments)
        decision_row += config.holding_rows
    if not periods:
        raise ValueError("panel has no complete ivol holding period")
    return tuple(periods), tuple(plans), tuple(forced_events)


def adverse_terminal_stress_periods(
    periods: Sequence[PanelPeriod],
    forced_exits: Sequence[ForcedExitEvent],
    *,
    quantile: float,
    adverse_return: float,
) -> tuple[PanelPeriod, ...]:
    """Apply a direction-adverse terminal shock to exposed forced exits."""

    if not 0.0 < quantile <= 0.5:
        raise ValueError("quantile must be in (0, 0.5]")
    if not 0.0 <= adverse_return < 1.0:
        raise ValueError("adverse_return must be in [0, 1)")
    by_time: dict[int, set[str]] = {}
    untimed_rows: dict[int, set[str]] = {}
    for event in forced_exits:
        if event.decision_time is None:
            untimed_rows.setdefault(event.decision_row, set()).add(event.instrument)
        else:
            by_time.setdefault(event.decision_time, set()).add(event.instrument)
    untimed_map: dict[int, set[str]] = {}
    for index, row in enumerate(sorted(untimed_rows)):
        if index < len(periods):
            untimed_map.setdefault(int(periods[index].event_time or index), set()).update(
                untimed_rows[row]
            )
    out = []
    for index, period in enumerate(periods):
        event_time = int(period.event_time or index)
        names = by_time.get(event_time, set()) | untimed_map.get(event_time, set())
        values = list(period.forward_returns)
        if names:
            count = max(1, int(round(len(period.signals) * quantile)))
            order = sorted(
                range(len(period.signals)), key=lambda item: (period.signals[item], item)
            )
            longs, shorts = set(order[-count:]), set(order[:count])
            for cell, instrument in enumerate(period.instruments):
                if instrument not in names:
                    continue
                if cell in longs:
                    values[cell] = max(-0.999999999, values[cell] - adverse_return)
                elif cell in shorts:
                    values[cell] = values[cell] + adverse_return
        out.append(
            PanelPeriod(
                period.instruments,
                period.signals,
                tuple(values),
                period.adv_notional,
                period.event_time,
                period.exit_adv_notional,
            )
        )
    return tuple(out)


def ivol_signal_leakage_ok(
    panel: FieldPanel,
    config: IvolExperimentConfig = IvolExperimentConfig(),
    *,
    mode: IvolSignalMode = "idiosyncratic",
    universe_size: int | None = None,
    start_row: int | None = None,
    stop_row: int | None = None,
) -> bool | None:
    """Prove current signals are invariant to every strictly future cell."""

    selected_size = config.universe_size if universe_size is None else universe_size
    first = (
        find_first_eligible_decision_row(panel, config)
        if start_row is None
        else start_row
    )
    sample_stop = (
        find_contiguous_sample_stop_row(panel, first, config)
        if stop_row is None
        else stop_row
    )
    decision_rows = list(range(first, sample_stop, config.holding_rows))
    if not decision_rows:
        return None
    assessed = False
    for target in sorted({decision_rows[0], decision_rows[len(decision_rows) // 2], decision_rows[-1]}):
        try:
            baseline_plan = build_decision_plan(panel, target, selected_size, config)
            baseline = signal_snapshot(panel, baseline_plan, mode)
            truncated = FieldPanel(
                panel.instruments,
                panel.event_times[: target + 1],
                {
                    name: [list(row) for row in matrix[: target + 1]]
                    for name, matrix in panel.matrices.items()
                },
            )
            observed_plan = build_decision_plan(truncated, target, selected_size, config)
            observed = signal_snapshot(truncated, observed_plan, mode)
        except (TypeError, ValueError):
            return False
        assessed = True
        if observed != baseline:
            return False
    return True if assessed else None


__all__ = [
    "ForcedExitEvent",
    "IvolDataIntegrityError",
    "IvolBreadthError",
    "IvolDecisionPlan",
    "IvolExperimentConfig",
    "adverse_terminal_stress_periods",
    "build_decision_plan",
    "build_ivol_periods",
    "find_first_eligible_decision_row",
    "find_contiguous_sample_stop_row",
    "ivol_signal_leakage_ok",
    "sample_stop_diagnostics",
    "signal_snapshot",
]
