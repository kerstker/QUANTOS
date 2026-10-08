"""Deterministic realtime Alpha discovery primitives.

This module internalizes QUANTOS' realtime research budget: the original four
microstructure families plus a slow cross-sectional state family.  It is
deliberately pure: no network,
filesystem, database, broker, credentials, wall clock, lifecycle promotion, or
order submission.  The scheduled tooling boundary supplies retained PIT frames
and persists the resulting evidence.

The maturity ladder is structural rather than promotional::

    <3d       COLLECTING_DATA
    >=3d      EXPLORATION_READY (diagnostic only)
    >=30d     VALIDATION_READY
    >=90d     SHADOW_DATA_READY

Even ``SHADOW_DATA_READY`` only means the data span may be evaluated by the
separate statistical/risk gates.  It grants no paper, deployment, or live
authority by itself.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, replace
import math
import statistics
from typing import Mapping, Sequence

from services.features.binance_realtime_projection import CORE_FEATURE_IDS


DAY_MS = 86_400_000
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1_000
SLOW_STATE_LOOKBACK_MS = 8 * 60 * 60 * 1_000
SLOW_STATE_MIN_OBSERVATIONS = 768
LABEL_POLICY = (
    "FIRST_KNOWN_ENTRY_AT_OR_AFTER_DECISION_PLUS_LATENCY_"
    "AND_EXIT_AT_OR_AFTER_ENTRY_PLUS_HORIZON"
)
# These versions are part of candidate identity.  The base version is kept
# deliberately separate from feedback variants so an adaptive challenger can
# change signal semantics without rewriting a frozen base definition.
REALTIME_FEEDBACK_SIGNAL_VERSION = "realtime-microstructure.feedback.v1"
REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION = (
    "realtime-microstructure.feedback.signflip.v1"
)


class RealtimeAlphaDiscoveryError(ValueError):
    """A realtime research invariant was violated."""


@dataclass(frozen=True)
class RealtimeAlphaPolicy:
    exploration_min_span_ms: int = 3 * DAY_MS
    validation_min_span_ms: int = 30 * DAY_MS
    shadow_min_span_ms: int = 90 * DAY_MS
    max_feature_age_ms: int = 15 * 60_000
    min_instruments: int = 10
    min_rows_per_feature: int = 1_000
    decision_interval_ms: int = 30_000
    execution_latency_ms: int = 250
    max_label_lateness_ms: int = 30_000
    min_cross_section: int = 8
    min_evaluation_periods: int = 24
    quantile: float = 0.20
    fee_bps: float = 4.0
    slippage_bps: float = 2.0

    def __post_init__(self) -> None:
        integer_fields = (
            "exploration_min_span_ms",
            "validation_min_span_ms",
            "shadow_min_span_ms",
            "max_feature_age_ms",
            "min_instruments",
            "min_rows_per_feature",
            "decision_interval_ms",
            "execution_latency_ms",
            "max_label_lateness_ms",
            "min_cross_section",
            "min_evaluation_periods",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise RealtimeAlphaDiscoveryError(f"{name} must be a non-negative integer")
        if not (
            0 < self.exploration_min_span_ms
            <= self.validation_min_span_ms
            <= self.shadow_min_span_ms
        ):
            raise RealtimeAlphaDiscoveryError("maturity spans are not monotone")
        if self.decision_interval_ms <= 0 or self.min_cross_section < 2:
            raise RealtimeAlphaDiscoveryError("decision interval/cross-section is invalid")
        if not 0.0 < float(self.quantile) <= 0.5:
            raise RealtimeAlphaDiscoveryError("quantile must be in (0, 0.5]")
        for name in ("fee_bps", "slippage_bps"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise RealtimeAlphaDiscoveryError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class FeatureCoverage:
    feature_id: str
    row_count: int
    instruments: frozenset[str]
    first_event_ms: int
    last_event_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.feature_id, str) or not self.feature_id:
            raise RealtimeAlphaDiscoveryError("feature_id is required")
        if type(self.row_count) is not int or self.row_count < 0:
            raise RealtimeAlphaDiscoveryError("row_count is invalid")
        if not self.instruments or any(not item for item in self.instruments):
            raise RealtimeAlphaDiscoveryError("instruments are required")
        if (
            type(self.first_event_ms) is not int
            or type(self.last_event_ms) is not int
            or self.first_event_ms < 0
            or self.last_event_ms < self.first_event_ms
        ):
            raise RealtimeAlphaDiscoveryError("feature event range is invalid")


@dataclass(frozen=True)
class DataAdmissionReport:
    status: str
    quality_status: str
    common_start_ms: int | None
    common_end_ms: int | None
    common_span_ms: int
    feature_age_ms: int | None
    common_instruments: tuple[str, ...]
    missing_features: tuple[str, ...]
    thin_features: tuple[str, ...]
    reasons: tuple[str, ...]
    exploration_allowed: bool
    formal_validation_allowed: bool
    shadow_admission_allowed: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutableDepthCapacityReport:
    status: str
    passed: bool
    evidence_ready: bool
    target_aum_passed: bool
    shadow_sizing_ready: bool
    history_span_ms: int
    feature_age_ms: int | None
    instrument_count: int
    conservative_depth_usd: float | None
    max_supported_aum_usd: float | None
    target_aum_usd: float
    shadow_aum_usd: float | None
    participation_rate: float
    legs_per_side: int
    reasons: tuple[str, ...]
    formal_gate_reasons: tuple[str, ...]
    provenance: str = "PIT_HOURLY_TOP5_EXECUTABLE_DEPTH_P10_V1"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RealtimeHypothesis:
    candidate_id: str
    family_id: str
    mechanism: str
    horizon_ms: int
    required_features: tuple[str, ...]
    economic_hypothesis: str
    falsification_standard: str
    signal_version: str = "realtime-microstructure.v1"
    lookback_ms: int = 0

    def __post_init__(self) -> None:
        if type(self.lookback_ms) is not int or self.lookback_ms < 0:
            raise RealtimeAlphaDiscoveryError("lookback_ms must be non-negative")

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.lookback_ms == 0:
            payload.pop("lookback_ms")
        return payload


@dataclass(frozen=True)
class RealtimeFrame:
    instrument_id: str
    event_time_ms: int
    decision_time_ms: int
    mid_price: float
    spread_bps: float
    depth_imbalance_5: float
    mark_basis_bps: float
    funding_rate: float
    taker_imbalance_5s: float
    liquidation_imbalance_5s: float
    trailing_return_8h: float | None = None
    trailing_realized_vol_8h: float | None = None
    # Hourly, complete Binance public RPI-inclusive 1000-level book.  This is
    # deliberately optional: the main 30-second feature path remains byte-for-
    # byte compatible, while the independent RPI research driver may attach
    # only observations whose knowledge_time is no later than this decision.
    rpi_notional_imbalance_1000: float | None = None
    knowledge_time_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id:
            raise RealtimeAlphaDiscoveryError("instrument_id is required")
        knowledge_time = (
            self.decision_time_ms
            if self.knowledge_time_ms is None
            else self.knowledge_time_ms
        )
        object.__setattr__(self, "knowledge_time_ms", knowledge_time)
        if (
            type(self.event_time_ms) is not int
            or type(self.decision_time_ms) is not int
            or type(knowledge_time) is not int
            or self.event_time_ms < 0
            or not self.event_time_ms <= knowledge_time <= self.decision_time_ms
        ):
            raise RealtimeAlphaDiscoveryError("frame violates PIT time ordering")
        numeric = (
            self.mid_price,
            self.spread_bps,
            self.depth_imbalance_5,
            self.mark_basis_bps,
            self.funding_rate,
            self.taker_imbalance_5s,
            self.liquidation_imbalance_5s,
        )
        if any(not math.isfinite(float(value)) for value in numeric):
            raise RealtimeAlphaDiscoveryError("frame contains a non-finite value")
        optional_numeric = (
            self.trailing_return_8h,
            self.trailing_realized_vol_8h,
            self.rpi_notional_imbalance_1000,
        )
        if any(
            value is not None and not math.isfinite(float(value))
            for value in optional_numeric
        ):
            raise RealtimeAlphaDiscoveryError("frame slow state is non-finite")
        if (
            self.trailing_realized_vol_8h is not None
            and self.trailing_realized_vol_8h < 0
        ):
            raise RealtimeAlphaDiscoveryError("frame slow volatility is negative")
        if (
            self.rpi_notional_imbalance_1000 is not None
            and not -1.0000001 <= self.rpi_notional_imbalance_1000 <= 1.0000001
        ):
            raise RealtimeAlphaDiscoveryError(
                "frame RPI imbalance is outside [-1, 1]"
            )
        if self.mid_price <= 0 or self.spread_bps < 0:
            raise RealtimeAlphaDiscoveryError("frame price/spread is outside its domain")
        for value in (
            self.depth_imbalance_5,
            self.taker_imbalance_5s,
            self.liquidation_imbalance_5s,
        ):
            if not -1.0000001 <= value <= 1.0000001:
                raise RealtimeAlphaDiscoveryError("frame imbalance is outside [-1, 1]")


@dataclass(frozen=True)
class RealtimeHypothesisEvaluation:
    candidate_id: str
    family_id: str
    mechanism: str
    horizon_ms: int
    status: str
    n_periods: int
    mean_breadth: float | None
    mean_ic: float | None
    ic_t_stat: float | None
    gross_sharpe: float
    net_sharpe: float
    holdout_net_sharpe: float
    net_return: float
    max_drawdown: float
    mean_turnover: float
    total_cost: float
    walk_forward_positive_frac: float | None
    spread_regime_positive_frac: float | None
    diagnostic_lead: bool
    label_policy: str
    pressure: Mapping[str, Mapping[str, float]]
    event_times: tuple[int, ...]
    net_returns: tuple[float, ...]

    def as_dict(self, *, include_returns: bool = False) -> dict[str, object]:
        payload = asdict(self)
        if not include_returns:
            payload.pop("event_times")
            payload.pop("net_returns")
        return payload


_MICRO_HORIZONS = (30_000, 120_000, 300_000)
_RELATIVE_VALUE_HORIZONS = (300_000, 3_600_000, 28_800_000)
_SLOW_HORIZONS = (3_600_000, 28_800_000)


def _horizon_label(horizon_ms: int) -> str:
    if horizon_ms % 3_600_000 == 0:
        return f"{horizon_ms // 3_600_000}h"
    if horizon_ms % 60_000 == 0:
        return f"{horizon_ms // 60_000}m"
    return f"{horizon_ms // 1_000}s"


def default_realtime_hypotheses() -> tuple[RealtimeHypothesis, ...]:
    """Return the immutable 30-trial realtime research budget.

    The original 24 definitions remain byte-compatible.  Six additive slow
    candidates consume only causal state derived from the retained mid series.
    """
    mechanisms = (
        (
            "order_flow",
            "flow_continuation",
            _MICRO_HORIZONS,
            ("rt_mid_price", "rt_spread_bps", "rt_depth_imbalance_5", "rt_taker_imbalance_5s"),
            "Aggressive flow continues when same-side depth support remains and the opposing book is depleted.",
            "Net post-latency edge must survive doubled costs, chronological holdout, and symbol removal.",
        ),
        (
            "order_flow",
            "flow_absorption_reversal",
            _MICRO_HORIZONS,
            ("rt_mid_price", "rt_spread_bps", "rt_depth_imbalance_5", "rt_taker_imbalance_5s"),
            "Aggressive flow reverses when it meets opposing displayed depth and is absorbed.",
            "The reversal must disappear when absorption is absent and must remain net positive after crossing costs.",
        ),
        (
            "order_book",
            "book_imbalance_continuation",
            _MICRO_HORIZONS,
            ("rt_mid_price", "rt_spread_bps", "rt_depth_imbalance_5"),
            "Persistent five-level depth imbalance predicts the next executable mid move in tight books.",
            "The effect must survive a post-decision latency and cannot be concentrated in one instrument.",
        ),
        (
            "order_book",
            "wide_spread_imbalance_reversal",
            _MICRO_HORIZONS,
            ("rt_mid_price", "rt_spread_bps", "rt_depth_imbalance_5"),
            "Depth imbalance observed in a wide spread is transient liquidity stress and mean reverts.",
            "The edge must be stronger in wide-spread observations and remain positive after spread costs.",
        ),
        (
            "liquidation",
            "liquidation_cascade",
            _MICRO_HORIZONS,
            ("rt_mid_price", "rt_spread_bps", "rt_depth_imbalance_5", "rt_mark_basis_bps", "rt_liquidation_imbalance_5s"),
            "Forced liquidation flow continues when basis dislocation and depleted liquidity confirm a cascade.",
            "Continuation must fail when liquidation flow is absent and survive liquidation-sparse holdouts.",
        ),
        (
            "liquidation",
            "liquidation_exhaustion",
            _MICRO_HORIZONS,
            ("rt_mid_price", "rt_spread_bps", "rt_depth_imbalance_5", "rt_mark_basis_bps", "rt_liquidation_imbalance_5s"),
            "Extreme forced flow exhausts available sellers or buyers and reverses after the shock.",
            "Reversal must be conditional on a genuine liquidation shock and robust to removing the largest event.",
        ),
        (
            "funding_basis",
            "basis_convergence",
            _RELATIVE_VALUE_HORIZONS,
            ("rt_mid_price", "rt_mark_basis_bps", "rt_funding_rate"),
            "Cross-sectional mark-index basis dislocations converge after costs and funding cash flow.",
            "Convergence must remain after funding attribution and cannot rely on a directional market beta.",
        ),
        (
            "funding_basis",
            "funding_crowding_unwind",
            _RELATIVE_VALUE_HORIZONS,
            ("rt_mid_price", "rt_mark_basis_bps", "rt_funding_rate"),
            "Extreme funding combined with basis crowding predicts relative-value unwind and carry cash flow.",
            "The cash-flow edge must exceed execution costs and remain capacity-feasible across instruments.",
        ),
    )
    result: list[RealtimeHypothesis] = []
    for family, mechanism, horizons, required, hypothesis, falsification in mechanisms:
        for horizon_ms in horizons:
            result.append(
                RealtimeHypothesis(
                    candidate_id=f"rt_{mechanism}_{_horizon_label(horizon_ms)}_v1",
                    family_id=family,
                    mechanism=mechanism,
                    horizon_ms=horizon_ms,
                    required_features=tuple(required),
                    economic_hypothesis=hypothesis,
                    falsification_standard=falsification,
                )
            )
    slow_mechanisms = (
        (
            "cross_sectional_state",
            "slow_cross_sectional_reversal",
            ("rt_mid_price", "rt_spread_bps"),
            "Eight-hour cross-sectional winners mean revert relative to laggards after executable costs.",
            "The reversal must survive chronological holdout, doubled costs, and removal of the most volatile symbols.",
        ),
        (
            "cross_sectional_state",
            "slow_cross_sectional_low_vol",
            ("rt_mid_price", "rt_spread_bps"),
            "Lower realized-volatility instruments earn a relative defensive premium over the next one to eight hours.",
            "The premium must persist outside stress windows and remain positive after turnover and doubled costs.",
        ),
        (
            "funding_basis",
            "funding_crowding_liquidity_filtered",
            (
                "rt_mid_price",
                "rt_spread_bps",
                "rt_depth_imbalance_5",
                "rt_mark_basis_bps",
                "rt_funding_rate",
            ),
            "Funding crowding unwinds are strongest when the book is tight and displayed depth confirms the unwind direction.",
            "The filtered unwind must beat the unfiltered cost burden on holdout data and survive doubled execution costs.",
        ),
    )
    for family, mechanism, required, hypothesis, falsification in slow_mechanisms:
        for horizon_ms in _SLOW_HORIZONS:
            result.append(
                RealtimeHypothesis(
                    candidate_id=f"rt_{mechanism}_{_horizon_label(horizon_ms)}_v1",
                    family_id=family,
                    mechanism=mechanism,
                    horizon_ms=horizon_ms,
                    required_features=tuple(required),
                    economic_hypothesis=hypothesis,
                    falsification_standard=falsification,
                    lookback_ms=SLOW_STATE_LOOKBACK_MS,
                )
            )
    return tuple(result)


def enrich_slow_state(
    frames_by_instrument: Mapping[str, Sequence[RealtimeFrame]],
) -> dict[str, tuple[RealtimeFrame, ...]]:
    """Derive causal eight-hour return and realized-volatility state.

    Only frames at or before each decision participate.  The original frames
    are immutable, and insufficient history remains explicit as ``None``.
    Prefix moments make the derivation linear after the per-series sort.
    """
    enriched: dict[str, tuple[RealtimeFrame, ...]] = {}
    for instrument, raw_frames in sorted(frames_by_instrument.items()):
        frames = sorted(raw_frames, key=lambda item: item.decision_time_ms)
        if any(item.instrument_id != instrument for item in frames):
            raise RealtimeAlphaDiscoveryError(
                "frame instrument does not match its series key"
            )
        times = [item.decision_time_ms for item in frames]
        if len(times) != len(set(times)):
            raise RealtimeAlphaDiscoveryError(
                "frame decision times must be unique per instrument"
            )
        log_returns = [0.0]
        for previous, current in zip(frames, frames[1:]):
            log_returns.append(math.log(current.mid_price / previous.mid_price))
        prefix = [0.0]
        prefix_sq = [0.0]
        for value in log_returns:
            prefix.append(prefix[-1] + value)
            prefix_sq.append(prefix_sq[-1] + value * value)
        rows: list[RealtimeFrame] = []
        for index, frame in enumerate(frames):
            cutoff = frame.decision_time_ms - SLOW_STATE_LOOKBACK_MS
            baseline_index = bisect_right(times, cutoff, 0, index + 1) - 1
            sample_count = index - baseline_index
            if (
                baseline_index < 0
                or sample_count < SLOW_STATE_MIN_OBSERVATIONS
                or frame.decision_time_ms - times[baseline_index]
                < SLOW_STATE_LOOKBACK_MS
            ):
                rows.append(frame)
                continue
            total = prefix[index + 1] - prefix[baseline_index + 1]
            total_sq = prefix_sq[index + 1] - prefix_sq[baseline_index + 1]
            mean = total / sample_count
            variance = max(0.0, total_sq / sample_count - mean * mean)
            rows.append(
                replace(
                    frame,
                    trailing_return_8h=(
                        frame.mid_price / frames[baseline_index].mid_price - 1.0
                    ),
                    trailing_realized_vol_8h=math.sqrt(variance),
                )
            )
        enriched[instrument] = tuple(rows)
    return enriched


def assess_data_admission(
    coverage: Mapping[str, FeatureCoverage],
    *,
    now_ms: int,
    quality_status: str,
    policy: RealtimeAlphaPolicy | None = None,
) -> DataAdmissionReport:
    policy = policy or RealtimeAlphaPolicy()
    if type(now_ms) is not int or now_ms < 0:
        raise RealtimeAlphaDiscoveryError("now_ms must be a non-negative integer")
    required = set(CORE_FEATURE_IDS)
    missing = tuple(sorted(required - set(coverage)))
    if missing:
        return DataAdmissionReport(
            status="BLOCKED_MISSING_FEATURES",
            quality_status=str(quality_status),
            common_start_ms=None,
            common_end_ms=None,
            common_span_ms=0,
            feature_age_ms=None,
            common_instruments=(),
            missing_features=missing,
            thin_features=(),
            reasons=("required realtime feature coverage is missing",),
            exploration_allowed=False,
            formal_validation_allowed=False,
            shadow_admission_allowed=False,
        )
    selected = [coverage[feature_id] for feature_id in CORE_FEATURE_IDS]
    thin = tuple(
        sorted(item.feature_id for item in selected if item.row_count < policy.min_rows_per_feature)
    )
    common_instruments = set(selected[0].instruments)
    for item in selected[1:]:
        common_instruments.intersection_update(item.instruments)
    common_start = max(item.first_event_ms for item in selected)
    common_end = min(item.last_event_ms for item in selected)
    common_span = max(0, common_end - common_start)
    feature_age = max(0, now_ms - common_end)
    reasons: list[str] = []
    if str(quality_status).upper() != "PASS":
        status = "BLOCKED_QUALITY"
        reasons.append("realtime feature materializer quality is not PASS")
    elif thin:
        status = "BLOCKED_THIN_FEATURES"
        reasons.append("one or more realtime features have too few retained observations")
    elif len(common_instruments) < policy.min_instruments:
        status = "BLOCKED_UNIVERSE"
        reasons.append("common realtime universe is below the minimum breadth")
    elif feature_age > policy.max_feature_age_ms:
        status = "BLOCKED_STALE"
        reasons.append("latest common realtime feature is stale")
    elif common_span < policy.exploration_min_span_ms:
        status = "COLLECTING_DATA"
        reasons.append("common history has not reached the three-day exploration floor")
    elif common_span < policy.validation_min_span_ms:
        status = "EXPLORATION_READY"
        reasons.append("diagnostic exploration allowed; formal validation requires 30 days")
    elif common_span < policy.shadow_min_span_ms:
        status = "VALIDATION_READY"
        reasons.append("formal research allowed; forward shadow admission requires 90 days")
    else:
        status = "SHADOW_DATA_READY"
        reasons.append("data maturity met; every statistical and risk gate still applies")
    exploration = status in {"EXPLORATION_READY", "VALIDATION_READY", "SHADOW_DATA_READY"}
    validation = status in {"VALIDATION_READY", "SHADOW_DATA_READY"}
    shadow = status == "SHADOW_DATA_READY"
    return DataAdmissionReport(
        status=status,
        quality_status=str(quality_status),
        common_start_ms=common_start,
        common_end_ms=common_end,
        common_span_ms=common_span,
        feature_age_ms=feature_age,
        common_instruments=tuple(sorted(common_instruments)),
        missing_features=(),
        thin_features=thin,
        reasons=tuple(reasons),
        exploration_allowed=exploration,
        formal_validation_allowed=validation,
        shadow_admission_allowed=shadow,
    )


def assess_executable_depth_capacity(
    hourly_depth_usd: Mapping[str, Sequence[float]],
    *,
    history_span_ms: int,
    feature_age_ms: int | None,
    expected_instruments: Sequence[str],
    target_aum_usd: float = 1_000_000.0,
    participation_rate: float = 0.10,
    quantile: float = 0.20,
    min_history_ms: int = 30 * DAY_MS,
    max_feature_age_ms: int = 15 * 60_000,
    min_instruments: int = 10,
) -> ExecutableDepthCapacityReport:
    """Calibrate conservative top-five-level capacity from retained PIT depth.

    Each instrument is reduced to its trailing 10th percentile hourly mean;
    the universe minimum then binds the cross-sectional book.  This avoids a
    liquid BTC book masking the actual capacity of the weakest selected leg.
    """
    if history_span_ms < 0 or min_history_ms <= 0 or max_feature_age_ms < 0:
        raise RealtimeAlphaDiscoveryError("capacity time policy is invalid")
    if not math.isfinite(float(target_aum_usd)) or target_aum_usd <= 0:
        raise RealtimeAlphaDiscoveryError("target_aum_usd must be positive")
    if not 0 < float(participation_rate) <= 1 or not 0 < float(quantile) <= 0.5:
        raise RealtimeAlphaDiscoveryError("capacity participation/quantile is invalid")
    expected = tuple(sorted(set(str(item) for item in expected_instruments if item)))
    per_instrument: list[float] = []
    for instrument in expected:
        values = sorted(
            float(value)
            for value in hourly_depth_usd.get(instrument, ())
            if not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0
        )
        if not values:
            continue
        index = max(0, math.ceil(len(values) * 0.10) - 1)
        per_instrument.append(values[index])
    instrument_count = len(per_instrument)
    conservative = min(per_instrument) if per_instrument else None
    legs_per_side = max(1, math.floor(max(1, len(expected)) * float(quantile)))
    max_aum = (
        conservative * float(participation_rate) * 2 * legs_per_side
        if conservative is not None
        else None
    )
    evidence_reasons: list[str] = []
    if history_span_ms < min_history_ms:
        evidence_reasons.append("EXECUTABLE_DEPTH_HISTORY_BELOW_30D")
    if feature_age_ms is None or feature_age_ms > max_feature_age_ms:
        evidence_reasons.append("EXECUTABLE_DEPTH_STALE_OR_UNAVAILABLE")
    if instrument_count < min_instruments:
        evidence_reasons.append("EXECUTABLE_DEPTH_UNIVERSE_BELOW_10")
    target_passed = bool(max_aum is not None and max_aum >= float(target_aum_usd))
    shadow_aum = None if max_aum is None else min(10_000.0, max_aum * 0.50)
    shadow_ready = bool(
        not evidence_reasons and shadow_aum is not None and shadow_aum >= 100.0
    )
    formal_reasons = list(evidence_reasons)
    if max_aum is None or max_aum <= 0:
        formal_reasons.append("EXECUTABLE_DEPTH_CAPACITY_NON_POSITIVE")
    reasons = list(evidence_reasons)
    if not target_passed:
        reasons.append("EXECUTABLE_DEPTH_CAPACITY_BELOW_TARGET_AUM")
    if target_passed and not evidence_reasons:
        status = "PASS"
    elif shadow_ready:
        status = "EVIDENCE_READY_LOW_CAPACITY"
    else:
        status = "COLLECTING_OR_BLOCKED"
    return ExecutableDepthCapacityReport(
        status=status,
        passed=target_passed and not evidence_reasons,
        evidence_ready=not evidence_reasons,
        target_aum_passed=target_passed and not evidence_reasons,
        shadow_sizing_ready=shadow_ready,
        history_span_ms=int(history_span_ms),
        feature_age_ms=feature_age_ms,
        instrument_count=instrument_count,
        conservative_depth_usd=conservative,
        max_supported_aum_usd=max_aum,
        target_aum_usd=float(target_aum_usd),
        shadow_aum_usd=shadow_aum,
        participation_rate=float(participation_rate),
        legs_per_side=legs_per_side,
        reasons=tuple(reasons),
        formal_gate_reasons=tuple(formal_reasons),
    )


def _base_signal_score(spec: RealtimeHypothesis, frame: RealtimeFrame) -> float:
    """Evaluate the frozen direction of one realtime mechanism."""
    spread_scale = 1.0 + frame.spread_bps / 10.0
    if spec.mechanism == "flow_continuation":
        alignment = max(0.0, frame.taker_imbalance_5s * frame.depth_imbalance_5)
        return frame.taker_imbalance_5s * (1.0 + alignment) / spread_scale
    if spec.mechanism == "flow_absorption_reversal":
        absorption = max(0.0, -frame.taker_imbalance_5s * frame.depth_imbalance_5)
        return -frame.taker_imbalance_5s * absorption / spread_scale
    if spec.mechanism == "book_imbalance_continuation":
        return frame.depth_imbalance_5 / spread_scale
    if spec.mechanism == "wide_spread_imbalance_reversal":
        wide_spread_weight = frame.spread_bps / (1.0 + frame.spread_bps)
        return -frame.depth_imbalance_5 * wide_spread_weight
    if spec.mechanism == "liquidation_cascade":
        dislocation = 1.0 + abs(frame.mark_basis_bps) / 10.0
        return -frame.liquidation_imbalance_5s * dislocation / spread_scale
    if spec.mechanism == "liquidation_exhaustion":
        shock = abs(frame.liquidation_imbalance_5s)
        dislocation = 1.0 + abs(frame.mark_basis_bps) / 10.0
        return frame.liquidation_imbalance_5s * shock * dislocation / spread_scale
    if spec.mechanism == "basis_convergence":
        return -frame.mark_basis_bps
    if spec.mechanism == "funding_crowding_unwind":
        return -frame.funding_rate * (1.0 + abs(frame.mark_basis_bps))
    if spec.mechanism == "funding_crowding_liquidity_filtered":
        base = -frame.funding_rate * (1.0 + abs(frame.mark_basis_bps))
        confirmation = 1.0 + max(0.0, math.copysign(1.0, base) * frame.depth_imbalance_5)
        return base * confirmation / spread_scale
    if spec.mechanism == "slow_cross_sectional_reversal":
        if frame.trailing_return_8h is None:
            raise RealtimeAlphaDiscoveryError("slow return state is unavailable")
        return -frame.trailing_return_8h
    if spec.mechanism == "slow_cross_sectional_low_vol":
        if frame.trailing_realized_vol_8h is None:
            raise RealtimeAlphaDiscoveryError("slow volatility state is unavailable")
        return -frame.trailing_realized_vol_8h
    if spec.mechanism == "rpi_depth_continuation":
        if frame.rpi_notional_imbalance_1000 is None:
            raise RealtimeAlphaDiscoveryError("RPI depth state is unavailable")
        return frame.rpi_notional_imbalance_1000 / spread_scale
    if spec.mechanism == "rpi_depth_reversal":
        if frame.rpi_notional_imbalance_1000 is None:
            raise RealtimeAlphaDiscoveryError("RPI depth state is unavailable")
        return -frame.rpi_notional_imbalance_1000 / spread_scale
    if spec.mechanism == "rpi_top_divergence":
        if frame.rpi_notional_imbalance_1000 is None:
            raise RealtimeAlphaDiscoveryError("RPI depth state is unavailable")
        return (
            frame.rpi_notional_imbalance_1000 - frame.depth_imbalance_5
        ) / spread_scale
    raise RealtimeAlphaDiscoveryError(f"unknown realtime mechanism: {spec.mechanism}")


def signal_score(spec: RealtimeHypothesis, frame: RealtimeFrame) -> float:
    """Evaluate one causal signal, including registered adaptive variants.

    ``SIGN_FLIP`` is the only structural adaptive variant currently allowed.
    It is a deterministic direction challenger of a frozen base mechanism;
    it changes no data, horizon, cost, or execution authority.
    """
    score = _base_signal_score(spec, frame)
    if spec.signal_version == REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION:
        return -score
    return score


def build_forward_paper_decision(
    spec: RealtimeHypothesis,
    frames: Sequence[RealtimeFrame],
    *,
    previous_weights: Mapping[str, float] | None = None,
    policy: RealtimeAlphaPolicy | None = None,
) -> dict[str, object]:
    """Freeze one causal cross-sectional decision before its outcome exists."""
    policy = policy or RealtimeAlphaPolicy()
    ordered = sorted(frames, key=lambda item: item.instrument_id)
    if len(ordered) < policy.min_cross_section:
        raise RealtimeAlphaDiscoveryError("forward decision breadth is insufficient")
    if len({item.instrument_id for item in ordered}) != len(ordered):
        raise RealtimeAlphaDiscoveryError("forward decision instruments must be unique")
    decision_times = {item.decision_time_ms for item in ordered}
    if len(decision_times) != 1:
        raise RealtimeAlphaDiscoveryError("forward decision frames are not time aligned")
    decision_time = next(iter(decision_times))
    signals = {item.instrument_id: signal_score(spec, item) for item in ordered}
    ranked = sorted(signals, key=lambda item: (signals[item], item))
    count = max(1, int(round(len(ranked) * policy.quantile)))
    if count * 2 > len(ranked):
        raise RealtimeAlphaDiscoveryError("forward decision long/short legs overlap")
    weights: dict[str, float] = {}
    for instrument in ranked[-count:]:
        weights[instrument] = 0.5 / count
    for instrument in ranked[:count]:
        weights[instrument] = -0.5 / count
    prior = {
        str(key): float(value)
        for key, value in (previous_weights or {}).items()
        if float(value) != 0.0
    }
    frame_by_instrument = {item.instrument_id: item for item in ordered}
    missing_exit_marks = sorted(set(prior) - set(frame_by_instrument))
    if missing_exit_marks:
        raise RealtimeAlphaDiscoveryError(
            f"forward decision cannot price exits: {missing_exit_marks}"
        )
    names = set(prior) | set(weights)
    deltas = {
        name: weights.get(name, 0.0) - prior.get(name, 0.0)
        for name in names
    }
    turnover = sum(abs(value) for value in deltas.values())
    return {
        "schema_version": 2,
        "candidate_id": spec.candidate_id,
        "family_id": spec.family_id,
        "mechanism": spec.mechanism,
        "signal_version": spec.signal_version,
        "decision_time_ms": decision_time,
        "entry_target_time_ms": decision_time + policy.execution_latency_ms,
        "exit_target_time_ms": (
            decision_time + policy.execution_latency_ms + spec.horizon_ms
        ),
        # Compatibility alias for schedulers: this is the earliest possible
        # exit.  Realization is based on actual entry knowledge time below.
        "target_time_ms": decision_time + policy.execution_latency_ms + spec.horizon_ms,
        "horizon_ms": spec.horizon_ms,
        "execution_latency_ms": policy.execution_latency_ms,
        "max_label_lateness_ms": policy.max_label_lateness_ms,
        "weights": dict(sorted(weights.items())),
        "previous_weights": dict(sorted(prior.items())),
        "weight_deltas": dict(sorted(deltas.items())),
        "signals": dict(sorted(signals.items())),
        "decision_mid_prices": {
            item.instrument_id: item.mid_price for item in ordered if item.instrument_id in weights
        },
        "turnover": turnover,
        # The executable cost is unknowable at decision time.  Realization
        # computes it from the actual post-latency entry frames.
        "decision_cost": None,
        "cost_policy": "POST_LATENCY_ENTRY_SPREAD_PLUS_FIXED_BPS",
        "fee_bps": policy.fee_bps,
        "slippage_bps": policy.slippage_bps,
        "label_policy": LABEL_POLICY,
        "paper_only": True,
        "promotion_eligible": False,
        "live_allowed": False,
        "order_submission_allowed": False,
    }


def realize_forward_paper_decision(
    decision: Mapping[str, object],
    future_frames: Mapping[str, Sequence[RealtimeFrame]],
) -> dict[str, object]:
    """Realize a frozen decision from distinct post-latency entry/exit marks."""
    try:
        if int(decision["schema_version"]) != 2:
            raise RealtimeAlphaDiscoveryError(
                "legacy forward decision lacks causal entry/exit evidence"
            )
        entry_target = int(decision["entry_target_time_ms"])
        lateness = int(decision["max_label_lateness_ms"])
        horizon = int(decision["horizon_ms"])
        weights = {str(k): float(v) for k, v in dict(decision["weights"]).items()}
        deltas = {
            str(k): float(v) for k, v in dict(decision["weight_deltas"]).items()
        }
        signals = {str(k): float(v) for k, v in dict(decision["signals"]).items()}
        fee_bps = float(decision["fee_bps"])
        slippage_bps = float(decision["slippage_bps"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RealtimeAlphaDiscoveryError("forward decision payload is invalid") from exc
    if not weights or not deltas or set(weights) - set(signals):
        raise RealtimeAlphaDiscoveryError("forward decision evidence is incomplete")
    if set(deltas) - set(future_frames):
        raise RealtimeAlphaDiscoveryError("future marks do not cover every open leg")
    instrument_returns: dict[str, float] = {}
    entry_prices: dict[str, float] = {}
    exit_prices: dict[str, float] = {}
    entry_times: dict[str, int] = {}
    exit_times: dict[str, int] = {}
    entry_spreads: dict[str, float] = {}
    entry_frames: dict[str, RealtimeFrame] = {}
    as_of = entry_target
    for instrument in sorted(deltas):
        marks = sorted(
            tuple(future_frames[instrument]),
            key=lambda item: (int(item.knowledge_time_ms), item.decision_time_ms),
        )
        entry = next(
            (item for item in marks if int(item.knowledge_time_ms) >= entry_target),
            None,
        )
        if entry is None:
            raise RealtimeAlphaDiscoveryError("entry mark precedes the frozen target")
        entry_known = int(entry.knowledge_time_ms)
        if entry_known - entry_target > lateness:
            raise RealtimeAlphaDiscoveryError("entry mark exceeds label lateness")
        entry_frames[instrument] = entry
        entry_prices[instrument] = entry.mid_price
        entry_times[instrument] = entry_known
        entry_spreads[instrument] = entry.spread_bps
        as_of = max(as_of, entry_known)
    fixed_cost = sum(abs(value) for value in deltas.values()) * (
        fee_bps + slippage_bps
    ) / 10_000.0
    spread_cost = sum(
        abs(delta) * entry_frames[instrument].spread_bps / 2.0 / 10_000.0
        for instrument, delta in deltas.items()
    )
    cost = fixed_cost + spread_cost
    for instrument, weight in sorted(weights.items()):
        marks = sorted(
            tuple(future_frames[instrument]),
            key=lambda item: (int(item.knowledge_time_ms), item.decision_time_ms),
        )
        entry = entry_frames[instrument]
        entry_known = int(entry.knowledge_time_ms)
        exit_target = entry_known + horizon
        exit_frame = next(
            (item for item in marks if int(item.knowledge_time_ms) >= exit_target),
            None,
        )
        if exit_frame is None:
            raise RealtimeAlphaDiscoveryError("exit mark precedes the entry-plus-horizon target")
        exit_known = int(exit_frame.knowledge_time_ms)
        if exit_known - exit_target > lateness:
            raise RealtimeAlphaDiscoveryError("exit mark exceeds label lateness")
        if entry.mid_price <= 0 or not math.isfinite(weight):
            raise RealtimeAlphaDiscoveryError("forward decision price/weight is invalid")
        price_return = exit_frame.mid_price / entry.mid_price - 1.0
        funding_cash_flow = -entry.funding_rate * horizon / FUNDING_INTERVAL_MS
        instrument_returns[instrument] = price_return + funding_cash_flow
        exit_prices[instrument] = exit_frame.mid_price
        exit_times[instrument] = exit_known
        as_of = max(as_of, exit_known)
    gross = sum(weights[name] * instrument_returns[name] for name in weights)
    signal_values = [signals[name] for name in sorted(instrument_returns)]
    return_values = [instrument_returns[name] for name in sorted(instrument_returns)]
    realized_ic = _rank_ic(signal_values, return_values)
    return {
        "schema_version": 2,
        "candidate_id": str(decision.get("candidate_id", "")),
        "decision_time_ms": int(decision["decision_time_ms"]),
        "as_of_ms": as_of,
        "entry_knowledge_times_ms": entry_times,
        "exit_knowledge_times_ms": exit_times,
        "entry_mid_prices": entry_prices,
        "exit_mid_prices": exit_prices,
        "entry_spreads_bps": entry_spreads,
        "gross_return": gross,
        "decision_cost": cost,
        "net_return": gross - cost,
        "realized_ic": realized_ic,
        "instrument_returns": instrument_returns,
        "paper_only": True,
        "promotion_eligible": False,
        "live_allowed": False,
        "order_submission_allowed": False,
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + end - 1) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = rank
        cursor = end
    return ranks


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(left_centered, right_centered, strict=True)) / denominator


def _rank_ic(signals: Sequence[float], returns: Sequence[float]) -> float | None:
    return _correlation(_average_ranks(signals), _average_ranks(returns))


def _sharpe(returns: Sequence[float], periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = statistics.pstdev(returns)
    if deviation == 0:
        return 0.0
    return statistics.fmean(returns) / deviation * math.sqrt(periods_per_year)


def _compound(returns: Sequence[float]) -> tuple[float, list[float]]:
    equity = 1.0
    curve: list[float] = []
    for value in returns:
        equity *= 1.0 + value
        curve.append(equity)
    return equity - 1.0, curve


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


def _positive_chunk_fraction(returns: Sequence[float], chunks: int = 4) -> float | None:
    if len(returns) < chunks * 2:
        return None
    positive = 0
    for index in range(chunks):
        lower = len(returns) * index // chunks
        upper = len(returns) * (index + 1) // chunks
        if statistics.fmean(returns[lower:upper]) > 0:
            positive += 1
    return positive / chunks


def evaluate_hypothesis(
    spec: RealtimeHypothesis,
    frames_by_instrument: Mapping[str, Sequence[RealtimeFrame]],
    *,
    policy: RealtimeAlphaPolicy | None = None,
) -> RealtimeHypothesisEvaluation:
    """Evaluate a hypothesis on retained PIT frames with post-latency labels.

    Frames are expected to be the latest complete snapshot in each decision
    bucket.  The evaluator samples non-overlapping decisions at the hypothesis
    horizon, enters on the first price known at or after ``decision + latency``,
    and exits on the first price known after a full horizon from that entry.
    A frame outside the configured lateness tolerance is never consumed.
    """
    policy = policy or RealtimeAlphaPolicy()
    observations: dict[int, list[tuple[str, float, float, float]]] = {}
    step = max(1, math.ceil(spec.horizon_ms / policy.decision_interval_ms))
    for instrument, raw_frames in sorted(frames_by_instrument.items()):
        frames = sorted(raw_frames, key=lambda item: item.decision_time_ms)
        if any(item.instrument_id != instrument for item in frames):
            raise RealtimeAlphaDiscoveryError("frame instrument does not match its series key")
        decision_times = [item.decision_time_ms for item in frames]
        if len(decision_times) != len(set(decision_times)):
            raise RealtimeAlphaDiscoveryError("frame decision times must be unique per instrument")
        known_frames = sorted(
            frames,
            key=lambda item: (int(item.knowledge_time_ms), item.decision_time_ms),
        )
        knowledge_times = [int(item.knowledge_time_ms) for item in known_frames]
        for frame in frames:
            if frame.decision_time_ms % policy.decision_interval_ms != 0:
                continue
            bucket_index = frame.decision_time_ms // policy.decision_interval_ms
            if bucket_index % step != 0:
                continue
            if (
                spec.mechanism == "slow_cross_sectional_reversal"
                and frame.trailing_return_8h is None
            ) or (
                spec.mechanism == "slow_cross_sectional_low_vol"
                and frame.trailing_realized_vol_8h is None
            ) or (
                spec.mechanism.startswith("rpi_")
                and frame.rpi_notional_imbalance_1000 is None
            ):
                continue
            entry_target = frame.decision_time_ms + policy.execution_latency_ms
            entry_index = bisect_left(knowledge_times, entry_target)
            if entry_index >= len(known_frames):
                continue
            entry = known_frames[entry_index]
            if int(entry.knowledge_time_ms) - entry_target > policy.max_label_lateness_ms:
                continue
            exit_target = int(entry.knowledge_time_ms) + spec.horizon_ms
            exit_index = bisect_left(knowledge_times, exit_target, lo=entry_index + 1)
            if exit_index >= len(known_frames):
                continue
            exit_frame = known_frames[exit_index]
            if int(exit_frame.knowledge_time_ms) - exit_target > policy.max_label_lateness_ms:
                continue
            future_return = exit_frame.mid_price / entry.mid_price - 1.0
            funding_cash_flow = -entry.funding_rate * spec.horizon_ms / FUNDING_INTERVAL_MS
            economic_return = future_return + funding_cash_flow
            observations.setdefault(frame.decision_time_ms, []).append(
                (
                    instrument,
                    signal_score(spec, frame),
                    economic_return,
                    entry.spread_bps,
                )
            )

    event_times: list[int] = []
    gross_returns: list[float] = []
    base_costs: list[float] = []
    turnovers: list[float] = []
    spreads: list[float] = []
    ic_values: list[float] = []
    breadths: list[int] = []
    previous_weights: dict[str, float] = {}
    for event_time, rows in sorted(observations.items()):
        if len(rows) < policy.min_cross_section:
            continue
        rows = sorted(rows, key=lambda item: item[0])
        signals = [item[1] for item in rows]
        future_returns = [item[2] for item in rows]
        ic = _rank_ic(signals, future_returns)
        if ic is not None:
            ic_values.append(ic)
        order = sorted(range(len(rows)), key=lambda index: (signals[index], rows[index][0]))
        count = max(1, int(round(len(order) * policy.quantile)))
        short_indices = order[:count]
        long_indices = order[-count:]
        weights: dict[str, float] = {}
        for index in long_indices:
            weights[rows[index][0]] = 0.5 / len(long_indices)
        for index in short_indices:
            name = rows[index][0]
            weights[name] = weights.get(name, 0.0) - 0.5 / len(short_indices)
        gross = sum(weights.get(name, 0.0) * value for name, _, value, _ in rows)
        names = set(previous_weights) | set(weights)
        deltas = {name: weights.get(name, 0.0) - previous_weights.get(name, 0.0) for name in names}
        turnover = sum(abs(value) for value in deltas.values())
        spread_by_name = {name: spread for name, _, _, spread in rows}
        spread_cost = sum(
            abs(delta) * spread_by_name.get(name, 0.0) / 2.0 / 10_000.0
            for name, delta in deltas.items()
        )
        fixed_cost = turnover * (policy.fee_bps + policy.slippage_bps) / 10_000.0
        event_times.append(event_time)
        gross_returns.append(gross)
        base_costs.append(fixed_cost + spread_cost)
        turnovers.append(turnover)
        spreads.append(statistics.fmean(item[3] for item in rows))
        breadths.append(len(rows))
        previous_weights = weights

    if not event_times:
        return RealtimeHypothesisEvaluation(
            candidate_id=spec.candidate_id,
            family_id=spec.family_id,
            mechanism=spec.mechanism,
            horizon_ms=spec.horizon_ms,
            status="INSUFFICIENT_OBSERVATIONS",
            n_periods=0,
            mean_breadth=None,
            mean_ic=None,
            ic_t_stat=None,
            gross_sharpe=0.0,
            net_sharpe=0.0,
            holdout_net_sharpe=0.0,
            net_return=0.0,
            max_drawdown=0.0,
            mean_turnover=0.0,
            total_cost=0.0,
            walk_forward_positive_frac=None,
            spread_regime_positive_frac=None,
            diagnostic_lead=False,
            label_policy=LABEL_POLICY,
            pressure={},
            event_times=(),
            net_returns=(),
        )

    periods_per_year = DAY_MS * 365.0 / spec.horizon_ms
    pressure: dict[str, dict[str, float]] = {}
    default_net: list[float] = []
    for multiplier in (1.0, 1.5, 2.0):
        net = [gross - cost * multiplier for gross, cost in zip(gross_returns, base_costs, strict=True)]
        net_return, curve = _compound(net)
        pressure[f"{multiplier:.1f}x"] = {
            "net_sharpe": _sharpe(net, periods_per_year),
            "net_return": net_return,
            "max_drawdown": _max_drawdown(curve),
            "total_cost": sum(base_costs) * multiplier,
        }
        if multiplier == 1.0:
            default_net = net
    net_return, equity_curve = _compound(default_net)
    holdout_start = max(0, int(len(default_net) * 0.8))
    holdout = default_net[holdout_start:]
    mean_ic = statistics.fmean(ic_values) if ic_values else None
    if len(ic_values) > 1 and statistics.pstdev(ic_values) > 0:
        ic_t_stat = mean_ic / (statistics.pstdev(ic_values) / math.sqrt(len(ic_values)))
    else:
        ic_t_stat = None
    spread_regime_positive_frac: float | None = None
    if len(spreads) >= 4:
        median_spread = statistics.median(spreads)
        regime_returns = (
            [value for value, spread in zip(default_net, spreads, strict=True) if spread <= median_spread],
            [value for value, spread in zip(default_net, spreads, strict=True) if spread > median_spread],
        )
        nonempty = [items for items in regime_returns if items]
        if len(nonempty) == 2:
            spread_regime_positive_frac = sum(
                statistics.fmean(items) > 0 for items in nonempty
            ) / len(nonempty)
    status = (
        "EVALUATED"
        if len(event_times) >= policy.min_evaluation_periods
        else "INSUFFICIENT_OBSERVATIONS"
    )
    worst_pressure = pressure["2.0x"]
    diagnostic_lead = bool(
        status == "EVALUATED"
        and mean_ic is not None
        and mean_ic > 0
        and ic_t_stat is not None
        and ic_t_stat >= 2.0
        and _sharpe(holdout, periods_per_year) > 0
        and worst_pressure["net_return"] > 0
    )
    return RealtimeHypothesisEvaluation(
        candidate_id=spec.candidate_id,
        family_id=spec.family_id,
        mechanism=spec.mechanism,
        horizon_ms=spec.horizon_ms,
        status=status,
        n_periods=len(event_times),
        mean_breadth=statistics.fmean(breadths),
        mean_ic=mean_ic,
        ic_t_stat=ic_t_stat,
        gross_sharpe=_sharpe(gross_returns, periods_per_year),
        net_sharpe=_sharpe(default_net, periods_per_year),
        holdout_net_sharpe=_sharpe(holdout, periods_per_year),
        net_return=net_return,
        max_drawdown=_max_drawdown(equity_curve),
        mean_turnover=statistics.fmean(turnovers),
        total_cost=sum(base_costs),
        walk_forward_positive_frac=_positive_chunk_fraction(default_net),
        spread_regime_positive_frac=spread_regime_positive_frac,
        diagnostic_lead=diagnostic_lead,
        label_policy=LABEL_POLICY,
        pressure=pressure,
        event_times=tuple(event_times),
        net_returns=tuple(default_net),
    )


__all__ = [
    "DAY_MS",
    "DataAdmissionReport",
    "FeatureCoverage",
    "LABEL_POLICY",
    "REALTIME_FEEDBACK_SIGNAL_VERSION",
    "REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION",
    "RealtimeAlphaDiscoveryError",
    "RealtimeAlphaPolicy",
    "RealtimeFrame",
    "RealtimeHypothesis",
    "RealtimeHypothesisEvaluation",
    "assess_data_admission",
    "assess_executable_depth_capacity",
    "build_forward_paper_decision",
    "default_realtime_hypotheses",
    "evaluate_hypothesis",
    "realize_forward_paper_decision",
    "signal_score",
]
