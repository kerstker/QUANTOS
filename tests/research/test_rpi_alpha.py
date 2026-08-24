from __future__ import annotations

from dataclasses import replace

import pytest

from services.research.realtime_alpha_discovery import (
    DAY_MS,
    RealtimeAlphaPolicy,
    RealtimeFrame,
    evaluate_hypothesis,
    signal_score,
)
from services.research.rpi_alpha import (
    RPI_FEATURE_ID,
    RPI_V2_LOCKED_OOS_START_MS,
    RPI_V2_PREREGISTERED_AT_MS,
    RpiDepthObservation,
    assess_rpi_data_admission,
    default_rpi_hypotheses,
    enrich_frames_with_rpi,
    locked_oos_observations,
    low_frequency_rpi_plan,
)


HOUR_MS = 3_600_000


def _frame(instrument: str, decision_time_ms: int, *, mid: float = 100.0,
           top_depth: float = 0.0) -> RealtimeFrame:
    return RealtimeFrame(
        instrument_id=instrument,
        event_time_ms=max(0, decision_time_ms - 1),
        decision_time_ms=decision_time_ms,
        mid_price=mid,
        spread_bps=1.0,
        depth_imbalance_5=top_depth,
        mark_basis_bps=0.0,
        funding_rate=0.0,
        taker_imbalance_5s=0.0,
        liquidation_imbalance_5s=0.0,
    )


def _observations(*, span_days: int, end_ms: int = 40 * DAY_MS,
                  instruments: int = 14) -> tuple[RpiDepthObservation, ...]:
    start_ms = end_ms - span_days * DAY_MS
    rows = []
    for index in range(instruments):
        instrument = f"S{index:02d}USDT"
        for stamp in range(start_ms, end_ms + 1, HOUR_MS):
            rows.append(
                RpiDepthObservation(
                    instrument_id=instrument,
                    event_time_ms=stamp,
                    knowledge_time_ms=stamp + 1,
                    notional_imbalance=-0.9 + index * 0.1,
                )
            )
    return tuple(rows)


def test_rpi_manifest_is_a_fixed_three_trial_one_hour_family() -> None:
    hypotheses = default_rpi_hypotheses()

    assert len(hypotheses) == 3
    assert len({item.candidate_id for item in hypotheses}) == 3
    assert {item.horizon_ms for item in hypotheses} == {HOUR_MS}
    assert {item.family_id for item in hypotheses} == {"rpi_liquidity"}
    assert all(RPI_FEATURE_ID in item.required_features for item in hypotheses)
    assert all(item.economic_hypothesis.strip() for item in hypotheses)
    assert all(item.falsification_standard.strip() for item in hypotheses)


def test_rpi_v2_plan_is_fixed_future_locked_and_hash_stable() -> None:
    first = low_frequency_rpi_plan()
    second = low_frequency_rpi_plan()

    assert first == second
    assert first.plan_hash == second.plan_hash
    assert first.registered_at_ms == RPI_V2_PREREGISTERED_AT_MS
    assert first.locked_oos_start_ms == RPI_V2_LOCKED_OOS_START_MS
    assert first.locked_oos_start_ms > first.registered_at_ms
    assert first.origin == "OUTCOME_INFORMED_BY_V1_COST_FAILURE"
    assert first.formal_credit_source == "POST_REGISTRATION_LOCKED_OOS_ONLY"
    assert len(first.hypotheses) == 3
    assert {item.mechanism for item in first.hypotheses} == {
        "rpi_depth_reversal"
    }
    assert {item.horizon_ms for item in first.hypotheses} == {
        4 * HOUR_MS,
        8 * HOUR_MS,
        24 * HOUR_MS,
    }
    assert first.as_dict()["plan_hash"] == first.plan_hash


def test_rpi_v2_locked_oos_filter_excludes_all_preboundary_information() -> None:
    boundary = RPI_V2_LOCKED_OOS_START_MS
    rows = (
        RpiDepthObservation("BTCUSDT", boundary - 2, boundary - 1, 0.1),
        RpiDepthObservation("BTCUSDT", boundary - 1, boundary, 0.2),
        RpiDepthObservation("BTCUSDT", boundary, boundary, 0.3),
        RpiDepthObservation("BTCUSDT", boundary + 1, boundary + 2, 0.4),
    )

    locked = locked_oos_observations(low_frequency_rpi_plan(), rows)

    assert locked == rows[2:]


def test_rpi_observation_rejects_non_pit_or_out_of_domain_values() -> None:
    with pytest.raises(ValueError, match="PIT"):
        RpiDepthObservation("BTCUSDT", 10, 9, 0.0)
    with pytest.raises(ValueError, match="imbalance"):
        RpiDepthObservation("BTCUSDT", 10, 10, 1.1)
    with pytest.raises(ValueError, match="instrument"):
        RpiDepthObservation("", 10, 10, 0.0)


def test_rpi_admission_separates_exploration_from_formal_validation() -> None:
    now_ms = 40 * DAY_MS + 60_000

    collecting = assess_rpi_data_admission(
        _observations(span_days=2), now_ms=now_ms, chain_valid=True
    )
    exploratory = assess_rpi_data_admission(
        _observations(span_days=3), now_ms=now_ms, chain_valid=True
    )
    validation = assess_rpi_data_admission(
        _observations(span_days=30), now_ms=now_ms, chain_valid=True
    )

    assert collecting.status == "COLLECTING_DATA"
    assert collecting.exploration_allowed is False
    assert exploratory.status == "EXPLORATION_READY"
    assert exploratory.exploration_allowed is True
    assert exploratory.formal_validation_allowed is False
    assert validation.status == "VALIDATION_READY"
    assert validation.formal_validation_allowed is True


def test_rpi_admission_fails_closed_on_chain_missing_names_or_staleness() -> None:
    now_ms = 40 * DAY_MS + 4 * HOUR_MS
    rows = _observations(span_days=30)

    assert assess_rpi_data_admission(
        rows, now_ms=now_ms, chain_valid=False
    ).status == "BLOCKED_INTEGRITY"
    assert assess_rpi_data_admission(
        _observations(span_days=30, instruments=13),
        now_ms=now_ms,
        chain_valid=True,
    ).status == "BLOCKED_COVERAGE"
    assert assess_rpi_data_admission(
        rows, now_ms=now_ms, chain_valid=True
    ).status == "BLOCKED_STALE"


def test_rpi_admission_rejects_same_size_universe_substitution() -> None:
    rows = _observations(span_days=30)
    expected = tuple(f"S{index:02d}USDT" for index in range(13)) + (
        "OTHERUSDT",
    )

    report = assess_rpi_data_admission(
        rows,
        now_ms=40 * DAY_MS + 60_000,
        chain_valid=True,
        expected_instruments=expected,
    )

    assert report.status == "BLOCKED_COVERAGE"
    assert "RPI_FIXED_UNIVERSE_MISMATCH" in report.reasons


def test_rpi_enrichment_is_pit_and_future_invariant() -> None:
    frames = {
        "BTCUSDT": (
            _frame("BTCUSDT", 2 * HOUR_MS),
            _frame("BTCUSDT", 3 * HOUR_MS),
        )
    }
    known = (
        RpiDepthObservation("BTCUSDT", HOUR_MS, HOUR_MS + 10, 0.4),
        RpiDepthObservation("BTCUSDT", 2 * HOUR_MS, 2 * HOUR_MS + 10, -0.2),
    )
    future = RpiDepthObservation("BTCUSDT", 4 * HOUR_MS, 4 * HOUR_MS, 0.9)

    base = enrich_frames_with_rpi(frames, known)
    with_future = enrich_frames_with_rpi(frames, (*known, future))

    assert base == with_future
    assert base["BTCUSDT"][0].rpi_notional_imbalance_1000 == 0.4
    assert base["BTCUSDT"][1].rpi_notional_imbalance_1000 == -0.2


def test_rpi_signal_directions_are_frozen_and_distinct() -> None:
    specs = {item.mechanism: item for item in default_rpi_hypotheses()}
    frame = replace(
        _frame("BTCUSDT", HOUR_MS, top_depth=0.8),
        rpi_notional_imbalance_1000=-0.6,
    )

    continuation = signal_score(specs["rpi_depth_continuation"], frame)
    reversal = signal_score(specs["rpi_depth_reversal"], frame)
    divergence = signal_score(specs["rpi_top_divergence"], frame)

    assert continuation < 0
    assert reversal == -continuation
    assert divergence < continuation


def test_rpi_candidate_uses_only_post_latency_future_prices() -> None:
    spec = next(
        item for item in default_rpi_hypotheses()
        if item.mechanism == "rpi_depth_continuation"
    )
    policy = RealtimeAlphaPolicy(
        decision_interval_ms=HOUR_MS,
        execution_latency_ms=250,
        max_label_lateness_ms=HOUR_MS,
        min_cross_section=8,
        min_evaluation_periods=4,
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    frames: dict[str, tuple[RealtimeFrame, ...]] = {}
    for index in range(10):
        instrument = f"S{index:02d}USDT"
        signal = -0.9 + 0.2 * index
        rows = []
        for period in range(8):
            decision = period * 2 * HOUR_MS
            rows.append(
                replace(
                    _frame(instrument, decision, mid=100.0),
                    rpi_notional_imbalance_1000=signal,
                )
            )
            rows.append(
                replace(
                    _frame(
                        instrument,
                        decision + HOUR_MS + 1_000,
                        mid=100.0 * (1.0 + signal * 0.001),
                    ),
                    rpi_notional_imbalance_1000=signal,
                )
            )
        frames[instrument] = tuple(rows)

    result = evaluate_hypothesis(spec, frames, policy=policy)

    assert result.status == "EVALUATED"
    assert result.n_periods == 8
    assert result.mean_ic is not None and result.mean_ic > 0.99
    assert result.net_return > 0
