from __future__ import annotations

from services.research.realtime_alpha_discovery import (
    DAY_MS,
    DataAdmissionReport,
    RealtimeAlphaPolicy,
    RealtimeFrame,
)
from tools.liquidation_event_research import build_report


def _frames() -> dict[str, tuple[RealtimeFrame, ...]]:
    result: dict[str, tuple[RealtimeFrame, ...]] = {}
    for index in range(8):
        instrument = f"S{index:02d}USDT"
        liquidation = 1.0 if index == 0 else (-1.0 if index == 1 else 0.0)
        base = 100.0 + index
        future = base * (1.01 if index == 0 else (0.99 if index == 1 else 1.0))
        result[instrument] = (
            RealtimeFrame(
                instrument_id=instrument,
                event_time_ms=0,
                decision_time_ms=0,
                mid_price=base,
                spread_bps=0.0,
                depth_imbalance_5=0.0,
                mark_basis_bps=0.0,
                funding_rate=0.0,
                taker_imbalance_5s=0.0,
                liquidation_imbalance_5s=liquidation,
            ),
            RealtimeFrame(
                instrument_id=instrument,
                event_time_ms=300_000,
                decision_time_ms=300_000,
                mid_price=future,
                spread_bps=0.0,
                depth_imbalance_5=0.0,
                mark_basis_bps=0.0,
                funding_rate=0.0,
                taker_imbalance_5s=0.0,
                liquidation_imbalance_5s=0.0,
            ),
        )
    return result


def _admission(*, formal: bool) -> DataAdmissionReport:
    return DataAdmissionReport(
        status="VALIDATION_READY" if formal else "EXPLORATION_READY",
        quality_status="PASS",
        common_start_ms=0,
        common_end_ms=31 * DAY_MS if formal else 12 * DAY_MS,
        common_span_ms=31 * DAY_MS if formal else 12 * DAY_MS,
        feature_age_ms=0,
        common_instruments=tuple(_frames()),
        missing_features=(),
        thin_features=(),
        reasons=(),
        exploration_allowed=True,
        formal_validation_allowed=formal,
        shadow_admission_allowed=False,
    )


def test_report_keeps_sub_30_day_result_exploratory_and_testnet_blocked() -> None:
    report = build_report(
        _frames(),
        admission=_admission(formal=False),
        dataset_version="a" * 64,
        generated_at_ms=12 * DAY_MS,
        policy=RealtimeAlphaPolicy(
            min_instruments=8,
            min_cross_section=8,
            execution_latency_ms=0,
            max_label_lateness_ms=0,
        ),
    )

    assert report["status"] == "EXPLORATION_ONLY"
    assert report["formal_trial_registered"] is False
    assert report["forward_eligible"] is False
    assert report["testnet_eligible"] is False
    assert report["blocking_reasons"] == ["REALTIME_HISTORY_BELOW_30D"]
    assert [item["mechanism"] for item in report["evaluations"]] == [
        "liquidation_event_exhaustion",
        "liquidation_event_cascade",
    ]
    assert report["safety"] == {
        "diagnostic_only": True,
        "credentials_accessed": False,
        "broker_connectivity": False,
        "order_submission": False,
        "mainnet": False,
    }


def test_even_mature_data_is_not_misreported_as_formally_validated() -> None:
    report = build_report(
        _frames(),
        admission=_admission(formal=True),
        dataset_version="b" * 64,
        generated_at_ms=31 * DAY_MS,
        policy=RealtimeAlphaPolicy(
            min_instruments=8,
            min_cross_section=8,
            execution_latency_ms=0,
            max_label_lateness_ms=0,
        ),
    )

    assert report["status"] == "FORMAL_VALIDATION_REQUIRED"
    assert report["formal_trial_registered"] is False
    assert report["forward_eligible"] is False
    assert report["blocking_reasons"] == [
        "DSR_PBO_WALK_FORWARD_AND_OOS_NOT_RUN"
    ]
