from __future__ import annotations

from collections import Counter
from dataclasses import replace
import math

from services.features.binance_realtime_projection import FEATURE_IDS
from services.research.realtime_alpha_discovery import (
    FeatureCoverage,
    REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION,
    SLOW_STATE_LOOKBACK_MS,
    RealtimeAlphaPolicy,
    RealtimeFrame,
    assess_data_admission,
    assess_executable_depth_capacity,
    build_forward_paper_decision,
    default_realtime_hypotheses,
    enrich_slow_state,
    evaluate_hypothesis,
    realize_forward_paper_decision,
    signal_score,
)


DAY_MS = 86_400_000


def _coverage(*, span_days: int, end_ms: int = 100 * DAY_MS):
    start_ms = end_ms - span_days * DAY_MS
    instruments = frozenset(f"S{index:02d}USDT" for index in range(14))
    return {
        feature_id: FeatureCoverage(
            feature_id=feature_id,
            row_count=10_000,
            instruments=instruments,
            first_event_ms=start_ms,
            last_event_ms=end_ms,
        )
        for feature_id in FEATURE_IDS
    }


def test_default_manifest_is_a_fixed_30_trial_family_budget() -> None:
    hypotheses = default_realtime_hypotheses()

    assert len(hypotheses) == 30
    assert len({item.candidate_id for item in hypotheses}) == 30
    assert Counter(item.family_id for item in hypotheses) == {
        "order_flow": 6,
        "order_book": 6,
        "liquidation": 6,
        "funding_basis": 8,
        "cross_sectional_state": 4,
    }
    assert all(item.economic_hypothesis.strip() for item in hypotheses)
    assert all(item.falsification_standard.strip() for item in hypotheses)
    assert all("rt_mid_price" in item.required_features for item in hypotheses)


def test_additive_slow_budget_preserves_original_candidate_identity_shape() -> None:
    hypotheses = default_realtime_hypotheses()
    legacy = hypotheses[:24]
    slow = hypotheses[24:]

    assert all(item.lookback_ms == 0 for item in legacy)
    assert all("lookback_ms" not in item.as_dict() for item in legacy)
    assert all(item.lookback_ms == SLOW_STATE_LOOKBACK_MS for item in slow)
    assert all(
        item.as_dict()["lookback_ms"] == SLOW_STATE_LOOKBACK_MS
        for item in slow
    )


def test_data_admission_progresses_without_weakening_maturity_gates() -> None:
    policy = RealtimeAlphaPolicy(max_feature_age_ms=15 * 60_000)
    now_ms = 100 * DAY_MS + 60_000

    collecting = assess_data_admission(
        _coverage(span_days=2), now_ms=now_ms, quality_status="PASS", policy=policy
    )
    exploratory = assess_data_admission(
        _coverage(span_days=3), now_ms=now_ms, quality_status="PASS", policy=policy
    )
    validation = assess_data_admission(
        _coverage(span_days=30), now_ms=now_ms, quality_status="PASS", policy=policy
    )
    shadow = assess_data_admission(
        _coverage(span_days=90), now_ms=now_ms, quality_status="PASS", policy=policy
    )

    assert collecting.status == "COLLECTING_DATA"
    assert exploratory.status == "EXPLORATION_READY"
    assert exploratory.exploration_allowed is True
    assert exploratory.formal_validation_allowed is False
    assert validation.status == "VALIDATION_READY"
    assert validation.formal_validation_allowed is True
    assert validation.shadow_admission_allowed is False
    assert shadow.status == "SHADOW_DATA_READY"
    assert shadow.shadow_admission_allowed is True


def test_data_admission_fails_closed_on_missing_or_stale_features() -> None:
    policy = RealtimeAlphaPolicy(max_feature_age_ms=15 * 60_000)
    now_ms = 100 * DAY_MS + 60_000
    missing = _coverage(span_days=30)
    missing.pop("rt_depth_imbalance_5")

    missing_report = assess_data_admission(
        missing, now_ms=now_ms, quality_status="PASS", policy=policy
    )
    stale_report = assess_data_admission(
        _coverage(span_days=30, end_ms=99 * DAY_MS),
        now_ms=now_ms,
        quality_status="PASS",
        policy=policy,
    )
    quality_report = assess_data_admission(
        _coverage(span_days=30), now_ms=now_ms, quality_status="BLOCK", policy=policy
    )

    assert missing_report.status == "BLOCKED_MISSING_FEATURES"
    assert "rt_depth_imbalance_5" in missing_report.missing_features
    assert stale_report.status == "BLOCKED_STALE"
    assert quality_report.status == "BLOCKED_QUALITY"
    assert not quality_report.exploration_allowed


def test_capacity_uses_weakest_instrument_and_requires_30_day_history() -> None:
    instruments = tuple(f"S{index:02d}USDT" for index in range(10))
    depth = {instrument: [3_000_000.0] * 100 for instrument in instruments}
    depth[instruments[-1]] = [2_500_000.0] * 100

    mature = assess_executable_depth_capacity(
        depth,
        history_span_ms=30 * DAY_MS,
        feature_age_ms=1_000,
        expected_instruments=instruments,
    )
    collecting = assess_executable_depth_capacity(
        depth,
        history_span_ms=29 * DAY_MS,
        feature_age_ms=1_000,
        expected_instruments=instruments,
    )

    assert mature.passed is True
    assert mature.shadow_sizing_ready is True
    assert mature.conservative_depth_usd == 2_500_000.0
    assert mature.legs_per_side == 2
    assert mature.max_supported_aum_usd == 1_000_000.0
    assert collecting.passed is False
    assert "EXECUTABLE_DEPTH_HISTORY_BELOW_30D" in collecting.reasons

    low_capacity = assess_executable_depth_capacity(
        {instrument: [2_500.0] * 100 for instrument in instruments},
        history_span_ms=30 * DAY_MS,
        feature_age_ms=1_000,
        expected_instruments=instruments,
    )
    assert low_capacity.passed is False
    assert low_capacity.evidence_ready is True
    assert low_capacity.shadow_sizing_ready is True
    assert low_capacity.shadow_aum_usd == 500.0
    assert low_capacity.formal_gate_reasons == ()


def test_capacity_feature_is_separate_from_signal_history_admission() -> None:
    coverage = _coverage(span_days=30)
    coverage.pop("rt_executable_depth_5_usd")

    report = assess_data_admission(
        coverage,
        now_ms=100 * DAY_MS + 60_000,
        quality_status="PASS",
    )

    assert report.formal_validation_allowed is True
    assert "rt_executable_depth_5_usd" not in report.missing_features


def test_forward_paper_decision_is_frozen_before_post_latency_realization() -> None:
    spec = next(
        item for item in default_realtime_hypotheses()
        if item.mechanism == "book_imbalance_continuation" and item.horizon_ms == 30_000
    )
    decision_frames = [
        _frame(
            f"S{index:02d}USDT",
            60_000,
            mid=100.0,
            depth=-0.9 + index * 0.2,
            spread=1.0,
        )
        for index in range(10)
    ]
    decision = build_forward_paper_decision(spec, decision_frames)
    assert decision["decision_time_ms"] == 60_000
    assert decision["target_time_ms"] == 90_250
    assert sum(abs(value) for value in decision["weights"].values()) == 1.0

    too_early = {
        frame.instrument_id: _frame(frame.instrument_id, 90_000, mid=101.0)
        for frame in decision_frames
        if frame.instrument_id in decision["weights"]
    }
    try:
        realize_forward_paper_decision(decision, too_early)
    except ValueError as exc:
        assert "precedes" in str(exc)
    else:
        raise AssertionError("a pre-target mark must never realize forward paper")

    future = {
        instrument: _frame(
            instrument,
            90_250,
            mid=(101.0 if weight > 0 else 99.0),
        )
        for instrument, weight in decision["weights"].items()
    }
    realized = realize_forward_paper_decision(decision, future)
    assert realized["gross_return"] > 0
    assert realized["net_return"] < realized["gross_return"]
    assert realized["live_allowed"] is False


def _frame(
    instrument: str,
    decision_ms: int,
    *,
    mid: float = 100.0,
    taker: float = 0.0,
    depth: float = 0.0,
    liquidation: float = 0.0,
    basis: float = 0.0,
    funding: float = 0.0,
    spread: float = 0.0,
    trailing_return: float | None = None,
    trailing_vol: float | None = None,
) -> RealtimeFrame:
    return RealtimeFrame(
        instrument_id=instrument,
        event_time_ms=max(0, decision_ms - 1),
        decision_time_ms=decision_ms,
        mid_price=mid,
        spread_bps=spread,
        depth_imbalance_5=depth,
        mark_basis_bps=basis,
        funding_rate=funding,
        taker_imbalance_5s=taker,
        liquidation_imbalance_5s=liquidation,
        trailing_return_8h=trailing_return,
        trailing_realized_vol_8h=trailing_vol,
    )


def test_signal_directions_encode_continuation_absorption_and_liquidation() -> None:
    by_mechanism = {
        item.mechanism: item for item in default_realtime_hypotheses() if item.horizon_ms == 30_000
    }
    aligned_flow = _frame("BTCUSDT", 60_000, taker=0.8, depth=0.7, spread=1.0)
    absorbed_flow = _frame("BTCUSDT", 60_000, taker=0.8, depth=-0.7, spread=1.0)
    long_liquidation = _frame(
        "BTCUSDT", 60_000, liquidation=0.9, basis=-12.0, spread=2.0
    )

    assert signal_score(by_mechanism["flow_continuation"], aligned_flow) > 0
    assert signal_score(by_mechanism["flow_absorption_reversal"], absorbed_flow) < 0
    assert signal_score(by_mechanism["liquidation_cascade"], long_liquidation) < 0
    assert signal_score(by_mechanism["liquidation_exhaustion"], long_liquidation) > 0


def test_slow_state_enrichment_is_causal_and_future_invariant() -> None:
    step_ms = 30_000
    observations = SLOW_STATE_LOOKBACK_MS // step_ms + 1
    history = tuple(
        _frame(
            "BTCUSDT",
            index * step_ms,
            mid=100.0 * math.exp(index * 0.0001),
        )
        for index in range(observations)
    )

    enriched = enrich_slow_state({"BTCUSDT": history})["BTCUSDT"]
    assert enriched[-1].trailing_return_8h is not None
    assert enriched[-1].trailing_return_8h > 0
    assert enriched[-1].trailing_realized_vol_8h is not None

    extreme_future = _frame(
        "BTCUSDT",
        history[-1].decision_time_ms + step_ms,
        mid=1_000_000.0,
    )
    with_future = enrich_slow_state(
        {"BTCUSDT": (*history, extreme_future)}
    )["BTCUSDT"]
    assert with_future[:-1] == enriched


def test_slow_cross_sectional_and_filtered_funding_signal_directions() -> None:
    by_mechanism = {
        item.mechanism: item
        for item in default_realtime_hypotheses()
        if item.horizon_ms == 3_600_000
    }
    winner = _frame(
        "BTCUSDT",
        SLOW_STATE_LOOKBACK_MS,
        trailing_return=0.12,
        trailing_vol=0.08,
    )
    quiet = _frame(
        "ETHUSDT",
        SLOW_STATE_LOOKBACK_MS,
        trailing_return=-0.04,
        trailing_vol=0.02,
    )
    tight_aligned = _frame(
        "SOLUSDT",
        SLOW_STATE_LOOKBACK_MS,
        funding=0.001,
        basis=12.0,
        spread=1.0,
        depth=-0.8,
    )
    wide_opposed = _frame(
        "XRPUSDT",
        SLOW_STATE_LOOKBACK_MS,
        funding=0.001,
        basis=12.0,
        spread=20.0,
        depth=0.8,
    )

    assert signal_score(by_mechanism["slow_cross_sectional_reversal"], winner) < 0
    assert signal_score(by_mechanism["slow_cross_sectional_low_vol"], quiet) > signal_score(
        by_mechanism["slow_cross_sectional_low_vol"], winner
    )
    assert abs(
        signal_score(
            by_mechanism["funding_crowding_liquidity_filtered"], tight_aligned
        )
    ) > abs(
        signal_score(
            by_mechanism["funding_crowding_liquidity_filtered"], wide_opposed
        )
    )


def test_sign_flip_challenger_is_exact_directional_inverse() -> None:
    base = next(
        item
        for item in default_realtime_hypotheses()
        if item.mechanism == "basis_convergence" and item.horizon_ms == 3_600_000
    )
    challenger = replace(
        base,
        candidate_id="rt_basis_convergence_1h_signflip_fb1",
        signal_version=REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION,
    )
    frame = _frame("BTCUSDT", 60_000, basis=7.0, funding=0.0002, spread=1.5)

    assert signal_score(challenger, frame) == -signal_score(base, frame)


def test_evaluator_uses_only_post_latency_labels_and_finds_designed_edge() -> None:
    spec = next(
        item
        for item in default_realtime_hypotheses()
        if item.mechanism == "flow_continuation" and item.horizon_ms == 30_000
    )
    policy = RealtimeAlphaPolicy(
        min_cross_section=8,
        min_evaluation_periods=4,
        execution_latency_ms=250,
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    frames: dict[str, list[RealtimeFrame]] = {}
    for symbol_index in range(10):
        instrument = f"S{symbol_index:02d}USDT"
        signal = -0.9 + 0.2 * symbol_index
        rows: list[RealtimeFrame] = []
        for period in range(8):
            decision = period * 60_000
            rows.append(
                _frame(
                    instrument,
                    decision,
                    taker=signal,
                    depth=signal,
                )
            )
            rows.append(
                _frame(
                    instrument,
                    decision + 31_000,
                    mid=100.0 * (1.0 + signal * 0.001),
                    taker=signal,
                    depth=signal,
                )
            )
        frames[instrument] = rows

    result = evaluate_hypothesis(spec, frames, policy=policy)

    assert result.status == "EVALUATED"
    assert result.n_periods == 8
    assert result.mean_ic is not None and result.mean_ic > 0.99
    assert result.net_return > 0
    assert result.label_policy == "FIRST_KNOWN_MID_AT_OR_AFTER_DECISION_PLUS_LATENCY_PLUS_HORIZON"


def test_evaluator_does_not_consume_a_pre_latency_future_price() -> None:
    spec = next(
        item
        for item in default_realtime_hypotheses()
        if item.mechanism == "flow_continuation" and item.horizon_ms == 30_000
    )
    policy = RealtimeAlphaPolicy(
        min_cross_section=2,
        min_evaluation_periods=1,
        execution_latency_ms=1_000,
        max_label_lateness_ms=1_000,
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    frames = {
        instrument: [
            _frame(instrument, 0, taker=signal, depth=signal),
            _frame(instrument, 30_500, mid=100.0 * (1 + signal * 0.01)),
        ]
        for instrument, signal in (("AUSDT", -0.5), ("BUSDT", 0.5))
    }

    result = evaluate_hypothesis(spec, frames, policy=policy)

    assert result.status == "INSUFFICIENT_OBSERVATIONS"
    assert result.n_periods == 0
