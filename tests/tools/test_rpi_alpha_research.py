from __future__ import annotations

import json

from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.realtime_alpha_discovery import DAY_MS, RealtimeFrame
from services.research.rpi_alpha import (
    RPI_V2_LOCKED_OOS_START_MS,
    RpiDataset,
    RpiDepthObservation,
)
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.rpi_alpha_research import (
    DEFAULT_FRAME_CACHE,
    run_cycle,
    run_low_frequency_cycle,
)


HOUR_MS = 3_600_000


def _frame(instrument: str, decision: int, *, mid: float,
           signal: float) -> RealtimeFrame:
    return RealtimeFrame(
        instrument_id=instrument,
        event_time_ms=max(0, decision - 1),
        decision_time_ms=decision,
        mid_price=mid,
        spread_bps=1.0,
        depth_imbalance_5=signal / 2.0,
        mark_basis_bps=0.0,
        funding_rate=0.0,
        taker_imbalance_5s=0.0,
        liquidation_imbalance_5s=0.0,
    )


def _dataset(span_days: int = 4) -> RpiDataset:
    end = 10 * DAY_MS
    start = end - span_days * DAY_MS
    rows = []
    for index, ticker in enumerate(FORWARD_MAJOR_TICKERS):
        for stamp in range(start, end + 1, HOUR_MS):
            rows.append(
                RpiDepthObservation(
                    f"{ticker}USDT",
                    stamp,
                    stamp + 1,
                    -0.91 + index * 0.14,
                )
            )
    return RpiDataset(
        observations=tuple(rows),
        authority_hash="a" * 64,
        chain_valid=True,
    )


def _frames(*_args, **_kwargs):
    output = {}
    for index, ticker in enumerate(FORWARD_MAJOR_TICKERS):
        instrument = f"{ticker}USDT"
        signal = -0.91 + index * 0.14
        output[instrument] = tuple(
            _frame(
                instrument,
                period * HOUR_MS,
                mid=100.0 * (1.0 + period * signal * 0.0002),
                signal=signal,
            )
            for period in range(10 * 24 + 2)
        )
    return output


def _empty_experiment_db(path) -> None:
    store = SqliteExperimentStore(str(path), create=True)
    store.close()


def test_rpi_cycle_registers_fixed_trials_but_cannot_formally_pass_before_30d(tmp_path):
    experiment_db = tmp_path / "experiment.db"
    output = tmp_path / "rpi_alpha.json"
    _empty_experiment_db(experiment_db)

    report = run_cycle(
        rpi_store=tmp_path / "unused-rpi.db",
        source_store=tmp_path / "unused-features.db",
        experiment_db=experiment_db,
        output_path=output,
        now_ms=10 * DAY_MS + 60_000,
        rpi_loader=lambda *_args, **_kwargs: _dataset(),
        frame_loader=_frames,
    )

    assert report["status"] == "EXPLORATORY_ONLY"
    assert report["funnel"] == {
        "pre_registered": 3,
        "evaluated": 3,
        "statistical_passes": 0,
        "promotion_eligible": 0,
    }
    assert report["data_admission"]["formal_validation_allowed"] is False
    assert report["global_trial_count"] == 3
    assert all(
        row["statistical_validation_gate_pass"] is False
        for row in report["candidates"]
    )
    assert all(
        "RPI_HISTORY_BELOW_30D" in row["statistical_gate_reasons"]
        for row in report["candidates"]
    )
    assert all(row["promotion_eligible"] is False for row in report["candidates"])
    assert report["remaining_promotion_gates"] == [
        "CAPACITY_NOT_ASSESSED",
        "PORTFOLIO_INCREMENT_NOT_ASSESSED",
        "INDEPENDENT_CHALLENGE_NOT_ASSESSED",
        "SIGNAL_SHADOW_NOT_MATURE",
    ]
    assert report["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_rpi_cycle_blocks_without_exploration_maturity_and_writes_no_trials(tmp_path):
    experiment_db = tmp_path / "experiment.db"
    _empty_experiment_db(experiment_db)

    report = run_cycle(
        rpi_store=tmp_path / "unused-rpi.db",
        source_store=tmp_path / "unused-features.db",
        experiment_db=experiment_db,
        output_path=tmp_path / "rpi_alpha.json",
        now_ms=10 * DAY_MS + 60_000,
        rpi_loader=lambda *_args, **_kwargs: _dataset(span_days=2),
        frame_loader=_frames,
    )

    assert report["status"] == "COLLECTING_DATA"
    assert report["funnel"]["evaluated"] == 0
    assert report["global_trial_count"] == 0


def test_rpi_cycle_replay_does_not_double_count_fixed_trials(tmp_path):
    experiment_db = tmp_path / "experiment.db"
    _empty_experiment_db(experiment_db)
    kwargs = {
        "rpi_store": tmp_path / "unused-rpi.db",
        "source_store": tmp_path / "unused-features.db",
        "experiment_db": experiment_db,
        "output_path": tmp_path / "rpi_alpha.json",
        "now_ms": 10 * DAY_MS + 60_000,
        "rpi_loader": lambda *_args, **_kwargs: _dataset(),
        "frame_loader": _frames,
    }

    first = run_cycle(**kwargs)
    second = run_cycle(**kwargs)

    assert first["global_trial_count"] == 3
    assert second["global_trial_count"] == 3
    assert {row["candidate_id"] for row in first["candidates"]} == {
        row["candidate_id"] for row in second["candidates"]
    }


def test_rpi_v1_uses_dedicated_hourly_frame_cache(tmp_path):
    experiment_db = tmp_path / "experiment.db"
    _empty_experiment_db(experiment_db)
    observed = {}

    def frames(*args, **kwargs):
        observed.update(kwargs)
        return _frames(*args, **kwargs)

    run_cycle(
        rpi_store=tmp_path / "unused-rpi.db",
        source_store=tmp_path / "unused-features.db",
        experiment_db=experiment_db,
        output_path=tmp_path / "rpi_alpha.json",
        now_ms=10 * DAY_MS + 60_000,
        rpi_loader=lambda *_args, **_kwargs: _dataset(),
        frame_loader=frames,
    )

    assert observed["frame_cache_path"] == DEFAULT_FRAME_CACHE


def _future_dataset(span_days: int) -> RpiDataset:
    start = RPI_V2_LOCKED_OOS_START_MS
    end = start + span_days * DAY_MS
    rows = []
    for index, ticker in enumerate(FORWARD_MAJOR_TICKERS):
        for stamp in range(start, end + 1, HOUR_MS):
            rows.append(
                RpiDepthObservation(
                    f"{ticker}USDT",
                    stamp,
                    stamp + 1,
                    -0.91 + index * 0.14,
                )
            )
    return RpiDataset(tuple(rows), "b" * 64, True)


def test_rpi_v2_preregisters_three_trials_without_loading_outcomes(tmp_path):
    experiment_db = tmp_path / "experiment.db"
    _empty_experiment_db(experiment_db)
    calls = []

    def forbidden_frames(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("locked OOS outcomes must not be loaded before maturity")

    kwargs = {
        "rpi_store": tmp_path / "unused-rpi.db",
        "source_store": tmp_path / "unused-features.db",
        "experiment_db": experiment_db,
        "output_path": tmp_path / "rpi_v2.json",
        "now_ms": RPI_V2_LOCKED_OOS_START_MS + 2 * DAY_MS + 60_000,
        "rpi_loader": lambda *_args, **_kwargs: _future_dataset(2),
        "frame_loader": forbidden_frames,
    }

    first = run_low_frequency_cycle(**kwargs)
    second = run_low_frequency_cycle(**kwargs)

    assert calls == []
    assert first["status"] == "LOCKED_OOS_COLLECTING"
    assert first["funnel"] == {
        "pre_registered": 3,
        "evaluated": 0,
        "statistical_passes": 0,
        "promotion_eligible": 0,
    }
    assert first["research_plan"]["plan_hash"] == first["plan_hash"]
    assert first["global_trial_count"] == 3
    assert second["global_trial_count"] == 3
    assert first["preregistration_search_id"] == second["preregistration_search_id"]


def test_rpi_v2_evaluates_only_postregistration_locked_sample(tmp_path):
    experiment_db = tmp_path / "experiment.db"
    _empty_experiment_db(experiment_db)
    observed = {}

    def frames(*args, **kwargs):
        observed.update(kwargs)
        return _frames(*args, **kwargs)

    report = run_low_frequency_cycle(
        rpi_store=tmp_path / "unused-rpi.db",
        source_store=tmp_path / "unused-features.db",
        experiment_db=experiment_db,
        output_path=tmp_path / "rpi_v2.json",
        frame_cache_path=tmp_path / "rpi-hourly.db",
        now_ms=RPI_V2_LOCKED_OOS_START_MS + 4 * DAY_MS + 60_000,
        rpi_loader=lambda *_args, **_kwargs: _future_dataset(4),
        frame_loader=frames,
    )

    assert observed["start_ms"] >= RPI_V2_LOCKED_OOS_START_MS
    assert observed["frame_cache_path"] == tmp_path / "rpi-hourly.db"
    assert report["status"] == "LOCKED_OOS_EXPLORATORY_ONLY"
    assert report["funnel"]["pre_registered"] == 3
    assert report["funnel"]["evaluated"] == 3
    assert report["funnel"]["statistical_passes"] == 0
    assert all(row["promotion_eligible"] is False for row in report["candidates"])
