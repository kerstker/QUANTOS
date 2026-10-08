"""Pure, PIT-safe primitives for a frozen distance-selected pairs experiment.

Formation selects liquid instruments and disjoint pairs using only completed
formation rows.  Trading signals may read the fixed formation statistics and
the current row; the next row is used only as the return label.  There is no IO,
RNG, wall clock, registry mutation, lifecycle transition, or execution path.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import math
import statistics
from typing import Literal

from quantos.contracts.panel import PanelPeriod
from services.research.alpha_expression import FieldPanel


SelectionMode = Literal["distance", "hash"]
Direction = Literal["convergence", "continuation"]
_HASH_SALT = "quantos.pairs.hash-control.v1"
_EPS = 1e-12


@dataclass(frozen=True)
class PairExperimentConfig:
    formation_rows: int = 180
    trade_rows: int = 90
    universe_size: int = 24
    pair_count: int = 10

    def __post_init__(self) -> None:
        for name in ("formation_rows", "trade_rows", "universe_size", "pair_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.universe_size < 2 * self.pair_count:
            raise ValueError("universe_size must support pair_count disjoint pairs")


@dataclass(frozen=True)
class PairStatistic:
    left: str
    right: str
    left_base: float
    right_base: float
    spread_mean: float
    spread_std: float
    formation_distance: float

    @property
    def pair_id(self) -> str:
        return f"{self.left}~{self.right}"


@dataclass(frozen=True)
class PairCyclePlan:
    formation_start: int
    formation_stop: int
    trade_start: int
    trade_stop: int
    selection: str
    liquid_universe: tuple[str, ...]
    pairs: tuple[tuple[str, str], ...]
    statistics: tuple[PairStatistic, ...]


def _validate_panel(panel: FieldPanel) -> tuple[list, list]:
    if not isinstance(panel, FieldPanel):
        raise TypeError("panel must be a FieldPanel")
    close = panel.matrices.get("close")
    volume = panel.matrices.get("volume")
    if close is None or volume is None:
        raise ValueError("panel requires close and volume matrices")
    rows, cols = len(panel.event_times), len(panel.instruments)
    if len(close) != rows or len(volume) != rows:
        raise ValueError("panel matrix row count mismatch")
    if any(len(row) != cols for row in close) or any(len(row) != cols for row in volume):
        raise ValueError("panel matrix column count mismatch")
    return close, volume


def _positive(value) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _nonnegative(value) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def formation_liquid_universe(
    panel: FieldPanel,
    formation_start: int,
    config: PairExperimentConfig,
) -> tuple[str, ...]:
    """Rank complete formation names by formation-only median dollar volume."""
    close, volume = _validate_panel(panel)
    stop = formation_start + config.formation_rows
    if formation_start < 0 or stop > len(panel.event_times):
        raise ValueError("formation window is outside panel")
    ranked: list[tuple[float, str]] = []
    for col, instrument in enumerate(panel.instruments):
        notionals = []
        complete = True
        for row in range(formation_start, stop):
            px, vol = close[row][col], volume[row][col]
            if not _positive(px) or not _nonnegative(vol):
                complete = False
                break
            notionals.append(float(px) * float(vol))
        if complete:
            ranked.append((statistics.median(notionals), instrument))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if len(ranked) < config.universe_size:
        raise ValueError(
            "insufficient complete formation instruments: "
            f"need {config.universe_size}, got {len(ranked)}"
        )
    return tuple(instrument for _adv, instrument in ranked[: config.universe_size])


def select_distance_pairs(
    normalized_paths: dict[str, tuple[float, ...]], pair_count: int
) -> tuple[tuple[str, str], ...]:
    """Greedily select disjoint pairs by summed squared normalized-path distance."""
    if isinstance(pair_count, bool) or not isinstance(pair_count, int) or pair_count <= 0:
        raise ValueError("pair_count must be a positive integer")
    names = sorted(normalized_paths)
    if len(names) < 2 * pair_count:
        raise ValueError("insufficient instruments for disjoint pairs")
    lengths = {len(normalized_paths[name]) for name in names}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) < 2:
        raise ValueError("normalized paths must share a length of at least two")
    ranked = []
    for left, right in itertools.combinations(names, 2):
        lp, rp = normalized_paths[left], normalized_paths[right]
        if not all(math.isfinite(float(v)) for v in (*lp, *rp)):
            raise ValueError("normalized paths must be finite")
        distance = math.fsum((float(a) - float(b)) ** 2 for a, b in zip(lp, rp))
        ranked.append((distance, left, right))
    ranked.sort()
    used: set[str] = set()
    selected = []
    for _distance, left, right in ranked:
        if left in used or right in used:
            continue
        selected.append((left, right))
        used.update((left, right))
        if len(selected) == pair_count:
            return tuple(selected)
    raise ValueError("could not construct requested disjoint distance pairs")


def select_hash_pairs(
    instruments: tuple[str, ...], pair_count: int
) -> tuple[tuple[str, str], ...]:
    """Outcome-independent control: greedily pair by fixed salted SHA-256 order."""
    names = sorted(set(instruments))
    if len(names) < 2 * pair_count:
        raise ValueError("insufficient instruments for disjoint hash pairs")
    ranked = []
    for left, right in itertools.combinations(names, 2):
        digest = hashlib.sha256(
            f"{_HASH_SALT}:{left}:{right}".encode("utf-8")
        ).hexdigest()
        ranked.append((digest, left, right))
    ranked.sort()
    used: set[str] = set()
    selected = []
    for _digest, left, right in ranked:
        if left in used or right in used:
            continue
        selected.append((left, right))
        used.update((left, right))
        if len(selected) == pair_count:
            return tuple(selected)
    raise ValueError("could not construct requested disjoint hash pairs")


def build_cycle_plan(
    panel: FieldPanel,
    formation_start: int,
    config: PairExperimentConfig = PairExperimentConfig(),
    *,
    selection: SelectionMode = "distance",
) -> PairCyclePlan:
    """Freeze one cycle's universe, pairs, and statistics from formation only."""
    if selection not in ("distance", "hash"):
        raise ValueError("selection must be 'distance' or 'hash'")
    close, _volume = _validate_panel(panel)
    liquid = formation_liquid_universe(panel, formation_start, config)
    col_by_name = {name: i for i, name in enumerate(panel.instruments)}
    stop = formation_start + config.formation_rows
    normalized: dict[str, tuple[float, ...]] = {}
    for name in liquid:
        col = col_by_name[name]
        base = float(close[formation_start][col])
        normalized[name] = tuple(float(close[row][col]) / base for row in range(formation_start, stop))
    pairs = (
        select_distance_pairs(normalized, config.pair_count)
        if selection == "distance"
        else select_hash_pairs(liquid, config.pair_count)
    )
    pair_stats = []
    for left, right in pairs:
        spreads = tuple(a - b for a, b in zip(normalized[left], normalized[right]))
        std = statistics.pstdev(spreads)
        if not math.isfinite(std) or std <= _EPS:
            raise ValueError(f"degenerate formation spread for pair {left}~{right}")
        pair_stats.append(
            PairStatistic(
                left=left,
                right=right,
                left_base=float(close[formation_start][col_by_name[left]]),
                right_base=float(close[formation_start][col_by_name[right]]),
                spread_mean=statistics.fmean(spreads),
                spread_std=std,
                formation_distance=math.fsum(value * value for value in spreads),
            )
        )
    return PairCyclePlan(
        formation_start=formation_start,
        formation_stop=stop,
        trade_start=stop,
        trade_stop=stop + config.trade_rows,
        selection=selection,
        liquid_universe=liquid,
        pairs=pairs,
        statistics=tuple(pair_stats),
    )


def _signal_snapshot(
    panel: FieldPanel,
    plan: PairCyclePlan,
    row: int,
    direction: Direction,
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...]]:
    if direction not in ("convergence", "continuation"):
        raise ValueError("direction must be 'convergence' or 'continuation'")
    close, volume = _validate_panel(panel)
    if row < plan.trade_start or row >= plan.trade_stop or row >= len(panel.event_times):
        raise ValueError("signal row is outside the cycle trade window")
    col_by_name = {name: i for i, name in enumerate(panel.instruments)}
    sign = -1.0 if direction == "convergence" else 1.0
    ids, signals, adv = [], [], []
    for stat in plan.statistics:
        li, ri = col_by_name[stat.left], col_by_name[stat.right]
        lp, rp = close[row][li], close[row][ri]
        lv, rv = volume[row][li], volume[row][ri]
        if not _positive(lp) or not _positive(rp) or not _nonnegative(lv) or not _nonnegative(rv):
            raise ValueError(f"missing trade close or volume for pair {stat.pair_id}")
        spread = float(lp) / stat.left_base - float(rp) / stat.right_base
        zscore = (spread - stat.spread_mean) / stat.spread_std
        ids.append(stat.pair_id)
        signals.append(sign * zscore)
        adv.append(min(float(lp) * float(lv), float(rp) * float(rv)))
    return tuple(ids), tuple(signals), tuple(adv)


def build_pair_periods(
    panel: FieldPanel,
    config: PairExperimentConfig = PairExperimentConfig(),
    *,
    selection: SelectionMode = "distance",
    direction: Direction = "convergence",
    start_row: int = 0,
) -> tuple[tuple[PanelPeriod, ...], tuple[PairCyclePlan, ...]]:
    """Build non-overlapping formation/trade cycles with one-day labels."""
    close, _volume = _validate_panel(panel)
    periods: list[PanelPeriod] = []
    plans: list[PairCyclePlan] = []
    cycle_rows = config.formation_rows + config.trade_rows
    if isinstance(start_row, bool) or not isinstance(start_row, int) or start_row < 0:
        raise ValueError("start_row must be a non-negative integer")
    formation_start = start_row
    while formation_start + cycle_rows < len(panel.event_times):
        plan = build_cycle_plan(panel, formation_start, config, selection=selection)
        col_by_name = {name: i for i, name in enumerate(panel.instruments)}
        for row in range(plan.trade_start, plan.trade_stop):
            ids, signals, adv = _signal_snapshot(panel, plan, row, direction)
            returns = []
            for stat in plan.statistics:
                li, ri = col_by_name[stat.left], col_by_name[stat.right]
                lp0, rp0 = close[row][li], close[row][ri]
                lp1, rp1 = close[row + 1][li], close[row + 1][ri]
                if not all(_positive(value) for value in (lp0, rp0, lp1, rp1)):
                    raise ValueError(f"missing trade close or label for pair {stat.pair_id}")
                pair_return = 0.5 * (
                    float(lp1) / float(lp0) - 1.0
                    - (float(rp1) / float(rp0) - 1.0)
                )
                returns.append(pair_return)
            periods.append(
                PanelPeriod(
                    instruments=ids,
                    signals=signals,
                    forward_returns=tuple(returns),
                    adv_notional=adv,
                    event_time=int(panel.event_times[row]),
                )
            )
        plans.append(plan)
        formation_start += cycle_rows
    if not periods:
        raise ValueError("panel has no complete formation plus trade cycle")
    return tuple(periods), tuple(plans)


def pair_signal_leakage_ok(
    panel: FieldPanel,
    config: PairExperimentConfig = PairExperimentConfig(),
    *,
    selection: SelectionMode = "distance",
    start_row: int = 0,
) -> bool | None:
    """Machine-check current signals are invariant to strictly-future inputs."""
    try:
        _periods, plans = build_pair_periods(
            panel,
            config,
            selection=selection,
            direction="convergence",
            start_row=start_row,
        )
    except ValueError:
        return None
    assessed = False
    for plan in plans:
        targets = sorted({plan.trade_start, plan.trade_stop - 1})
        for target in targets:
            base = _signal_snapshot(panel, plan, target, "convergence")
            truncated = FieldPanel(
                panel.instruments,
                panel.event_times[: target + 1],
                {
                    name: [list(row) for row in matrix[: target + 1]]
                    for name, matrix in panel.matrices.items()
                },
            )
            try:
                observed = _signal_snapshot(truncated, plan, target, "convergence")
            except (TypeError, ValueError):
                return False
            assessed = True
            if observed != base:
                return False
    return True if assessed else None


def find_first_eligible_formation_start(
    panel: FieldPanel,
    config: PairExperimentConfig = PairExperimentConfig(),
) -> int:
    """Earliest row whose formation window has the frozen complete universe.

    This is a data-readiness rule only: it never reads a trade label or return.
    The returned start must still leave one complete trade cycle.
    """
    _validate_panel(panel)
    last = len(panel.event_times) - config.formation_rows - config.trade_rows - 1
    for start in range(max(0, last + 1)):
        try:
            formation_liquid_universe(panel, start, config)
        except ValueError:
            continue
        return start
    raise ValueError("no eligible formation window leaves a complete trade cycle")


__all__ = [
    "PairCyclePlan",
    "PairExperimentConfig",
    "PairStatistic",
    "build_cycle_plan",
    "build_pair_periods",
    "formation_liquid_universe",
    "find_first_eligible_formation_start",
    "pair_signal_leakage_ok",
    "select_distance_pairs",
    "select_hash_pairs",
]
