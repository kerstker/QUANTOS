from __future__ import annotations

import json

from quantos.contracts.feature import FeatureValue
from services.features.binance_realtime_projection import FEATURE_IDS, FEATURE_VERSION
from services.features.value_store import SqliteFeatureValueStore
from services.research.realtime_alpha_discovery import RealtimeAlphaPolicy
from tools.realtime_alpha_discovery import (
    coverage_from_store,
    load_bucketed_frames,
    load_hourly_executable_depth,
    run_cycle,
)
from services.research.experiment_registry.store import SqliteExperimentStore


def _feature_value(feature_id: str, instrument: str, timestamp: int, value: float) -> FeatureValue:
    return FeatureValue(
        feature_id=feature_id,
        instrument_id=instrument,
        event_time=timestamp,
        knowledge_time=timestamp,
        value=value,
    )


def _write_store(path, *, instruments: int = 10, buckets: int = 24) -> int:
    store = SqliteFeatureValueStore(str(path), create=True)
    values = []
    for bucket in range(buckets):
        timestamp = bucket * 30_000 + 29_000
        for index in range(instruments):
            instrument = f"S{index:02d}USDT"
            rank = -0.9 + index * 0.2
            feature_values = {
                "rt_mid_price": 100.0 * (1.0 + rank * bucket * 0.0001),
                "rt_spread_bps": 1.0,
                "rt_depth_imbalance_5": rank,
                "rt_mark_basis_bps": rank * 5.0,
                "rt_funding_rate": rank * 0.0001,
                "rt_taker_imbalance_5s": rank,
                "rt_liquidation_imbalance_5s": rank,
                "rt_executable_depth_5_usd": 5_000_000.0,
            }
            values.extend(
                _feature_value(feature_id, instrument, timestamp, feature_values[feature_id])
                for feature_id in FEATURE_IDS
            )
    inserted = store.add_many(values, version=FEATURE_VERSION)
    store.close()
    assert inserted == len(values)
    return (buckets - 1) * 30_000 + 29_000


def test_coverage_and_bucket_loader_keep_complete_pit_frames(tmp_path) -> None:
    store_path = tmp_path / "features.db"
    end_ms = _write_store(store_path)

    coverage = coverage_from_store(store_path)
    frames = load_bucketed_frames(
        store_path,
        instruments=sorted(coverage["rt_mid_price"].instruments),
        start_ms=0,
        end_ms=end_ms,
    )

    assert set(coverage) == set(FEATURE_IDS)
    assert all(item.row_count == 240 for item in coverage.values())
    assert len(frames) == 10
    assert all(len(rows) == 24 for rows in frames.values())
    assert all(row.event_time_ms <= row.decision_time_ms for rows in frames.values() for row in rows)
    assert all(row.decision_time_ms % 30_000 == 0 for rows in frames.values() for row in rows)
    capacity = load_hourly_executable_depth(
        store_path,
        instruments=sorted(coverage["rt_mid_price"].instruments),
        start_ms=0,
        end_ms=end_ms,
    )
    assert set(capacity) == set(coverage["rt_mid_price"].instruments)
    assert all(values == (5_000_000.0,) for values in capacity.values())


def test_cycle_runs_the_fixed_budget_without_paper_or_live_authority(tmp_path) -> None:
    store_path = tmp_path / "features.db"
    end_ms = _write_store(store_path, buckets=40)
    status_path = tmp_path / "feature-status.json"
    status_path.write_text(
        json.dumps({"status": "PASS", "generated_at_ms": end_ms}),
        encoding="utf-8",
    )
    output_path = tmp_path / "discovery.json"
    policy = RealtimeAlphaPolicy(
        exploration_min_span_ms=60_000,
        validation_min_span_ms=3_600_000,
        shadow_min_span_ms=7_200_000,
        max_feature_age_ms=60_000,
        min_rows_per_feature=1,
        min_evaluation_periods=2,
        min_cross_section=8,
    )

    report = run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=tmp_path / "registry.db",
        output_path=output_path,
        now_ms=end_ms + 1_000,
        policy=policy,
        persist_trials=False,
    )

    assert report["status"] == "RUNNING_EXPLORATORY"
    assert report["autonomy"]["autonomous_search_running"] is True
    assert report["autonomy"]["requires_developer_ai"] is False
    assert report["funnel"]["pre_registered"] == 24
    assert report["funnel"]["evaluated"] == 24
    assert report["funnel"]["formal_passes"] == 0
    assert all("evaluation_status" in row for row in report["candidates"])
    assert any(row["evaluation_status"] == "EVALUATED" for row in report["candidates"])
    assert report["executable_depth_capacity"]["passed"] is False
    assert "EXECUTABLE_DEPTH_HISTORY_BELOW_30D" in report["executable_depth_capacity"]["reasons"]
    assert report["safety"] == {
        "paper_only": True,
        "live_trading_enabled": False,
        "real_broker_enabled": False,
        "allow_order_submission": False,
        "allow_ai_direct_trading": False,
        "promotion_eligible": False,
        "credentials_accessed": False,
    }
    assert all(row["status"] == "EXPLORATORY_ONLY" for row in report["candidates"])
    assert all("REALTIME_HISTORY_BELOW_30D" in row["gate_reasons"] for row in report["candidates"])
    assert json.loads(output_path.read_text(encoding="utf-8"))["dataset_version"] == report["dataset_version"]

    repeated = run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=tmp_path / "registry.db",
        output_path=output_path,
        now_ms=end_ms + 2_000,
        policy=policy,
        persist_trials=False,
    )
    assert repeated["funnel"]["pre_registered"] == 24
    assert repeated["registry"]["chain_valid"] is True


def test_persistent_cycle_uses_persistence_clock_after_expensive_as_of_evaluation(
    tmp_path, monkeypatch
) -> None:
    import tools.realtime_alpha_discovery as discovery_module

    store_path = tmp_path / "features.db"
    end_ms = _write_store(store_path, buckets=1_200)
    status_path = tmp_path / "feature-status.json"
    status_path.write_text(
        json.dumps({"status": "PASS", "generated_at_ms": end_ms}),
        encoding="utf-8",
    )
    experiment_db = tmp_path / "experiment.db"
    experiment_store = SqliteExperimentStore(str(experiment_db), create=True)
    latest_recorded_at = end_ms + 50_000
    experiment_store.begin_search_attempt(
        search_id="prior-search",
        search_kind="test",
        source="tests",
        dataset_version="prior",
        recorded_at=latest_recorded_at,
    )
    experiment_store.finish_search_attempt(
        "prior-search",
        status="COMPLETED",
        recorded_at=latest_recorded_at,
    )
    experiment_store.close()
    persistence_now = end_ms + 100_000
    monkeypatch.setattr(discovery_module.time, "time", lambda: persistence_now / 1_000)
    policy = RealtimeAlphaPolicy(
        exploration_min_span_ms=60_000,
        validation_min_span_ms=3_600_000,
        shadow_min_span_ms=7_200_000,
        max_feature_age_ms=60_000,
        min_rows_per_feature=1,
        min_evaluation_periods=2,
        min_cross_section=8,
    )

    report = run_cycle(
        source_store=store_path,
        feature_status=status_path,
        experiment_db=experiment_db,
        registry_db=tmp_path / "registry.db",
        output_path=tmp_path / "discovery.json",
        now_ms=end_ms + 1_000,
        policy=policy,
        persist_trials=True,
    )

    assert report["search_id"]
    verified = SqliteExperimentStore(str(experiment_db), create=False)
    starts = verified._conn.execute(
        "SELECT recorded_at FROM search_attempt_events WHERE status='STARTED' ORDER BY event_seq"
    ).fetchall()
    verified.close()
    assert starts[-1][0] == persistence_now
