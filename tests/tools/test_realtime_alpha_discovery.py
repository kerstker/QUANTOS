from __future__ import annotations

import json
import hashlib
import sqlite3

import pytest

from quantos.contracts.feature import FeatureValue
from quantos.contracts.hashing import stable_hash
from services.features.binance_realtime_projection import (
    CORE_FEATURE_IDS,
    FEATURE_IDS,
    FEATURE_VERSION,
)
from services.features.value_store import SqliteFeatureValueStore
from services.research.realtime_alpha_discovery import (
    RealtimeAlphaPolicy,
    SLOW_STATE_LOOKBACK_MS,
)
from tools.realtime_alpha_discovery import (
    RealtimeAlphaWorkerError,
    coverage_from_store,
    load_bucketed_frames,
    load_hourly_executable_depth,
    run_cycle,
)
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.alpha_family_registry import AlphaFamilyRegistry
from tools import realtime_alpha_discovery as discovery_module


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
    cache_path = tmp_path / "decision-frames.db"
    cached_first = load_bucketed_frames(
        store_path,
        instruments=sorted(coverage["rt_mid_price"].instruments),
        start_ms=0,
        end_ms=end_ms,
        frame_cache_path=cache_path,
    )
    cached_second = load_bucketed_frames(
        store_path,
        instruments=sorted(coverage["rt_mid_price"].instruments),
        start_ms=0,
        end_ms=end_ms,
        frame_cache_path=cache_path,
    )
    assert cached_first.keys() == cached_second.keys() == frames.keys()
    for instrument in frames:
        assert cached_first[instrument] == cached_second[instrument]
        assert [
            (row.event_time_ms, row.decision_time_ms)
            for row in cached_first[instrument]
        ] == [
            (row.event_time_ms, row.decision_time_ms)
            for row in frames[instrument]
        ]
        for cached, expected in zip(cached_first[instrument], frames[instrument], strict=True):
            assert cached.mid_price == pytest.approx(expected.mid_price)
            assert cached.spread_bps == pytest.approx(expected.spread_bps)
            assert cached.depth_imbalance_5 == pytest.approx(expected.depth_imbalance_5)
            assert cached.mark_basis_bps == pytest.approx(expected.mark_basis_bps)
            assert cached.funding_rate == pytest.approx(expected.funding_rate)
            assert cached.taker_imbalance_5s == pytest.approx(expected.taker_imbalance_5s)
            assert cached.liquidation_imbalance_5s == pytest.approx(expected.liquidation_imbalance_5s)
    capacity = load_hourly_executable_depth(
        store_path,
        instruments=sorted(coverage["rt_mid_price"].instruments),
        start_ms=0,
        end_ms=end_ms,
    )
    assert set(capacity) == set(coverage["rt_mid_price"].instruments)
    assert all(values == (5_000_000.0,) for values in capacity.values())


def test_bucket_loader_materializes_causal_slow_state_with_cache_parity(
    tmp_path,
) -> None:
    store_path = tmp_path / "slow-features.db"
    buckets = SLOW_STATE_LOOKBACK_MS // 30_000 + 2
    end_ms = _write_store(store_path, instruments=2, buckets=buckets)
    instruments = ("S00USDT", "S01USDT")

    uncached = load_bucketed_frames(
        store_path,
        instruments=instruments,
        start_ms=0,
        end_ms=end_ms,
    )
    cached = load_bucketed_frames(
        store_path,
        instruments=instruments,
        start_ms=0,
        end_ms=end_ms,
        frame_cache_path=tmp_path / "slow-decision-frames.db",
    )

    assert cached == uncached
    for instrument in instruments:
        assert uncached[instrument][-1].trailing_return_8h is not None
        assert uncached[instrument][-1].trailing_realized_vol_8h is not None
        assert uncached[instrument][0].trailing_return_8h is None


def test_coverage_reads_compact_series_metadata_not_the_feature_archive(
    tmp_path,
    monkeypatch,
) -> None:
    store_path = tmp_path / "features.db"
    _write_store(store_path, instruments=10, buckets=24)
    original_connect = discovery_module.sqlite3.connect

    def archive_denied_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)

        def authorize(action, arg1, _arg2, _database, _trigger):
            if action == sqlite3.SQLITE_READ and arg1 == "feature_values":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(discovery_module.sqlite3, "connect", archive_denied_connect)

    coverage = coverage_from_store(store_path)

    assert set(coverage) == set(FEATURE_IDS)
    assert all(item.row_count == 240 for item in coverage.values())
    assert all(len(item.instruments) == 10 for item in coverage.values())


def test_coverage_rejects_inconsistent_series_metadata(tmp_path) -> None:
    store_path = tmp_path / "features.db"
    _write_store(store_path, instruments=10, buckets=24)
    connection = sqlite3.connect(store_path)
    connection.execute(
        "UPDATE feature_value_series_metadata SET row_count=row_count+1 "
        "WHERE feature_id=? AND feature_version=? AND instrument_id=?",
        (FEATURE_IDS[0], FEATURE_VERSION, "S00USDT"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(RealtimeAlphaWorkerError, match="metadata is inconsistent"):
        coverage_from_store(store_path)


def test_bucket_cache_refreshes_when_source_advances_within_allowed_lag(tmp_path) -> None:
    store_path = tmp_path / "features.db"
    first_end_ms = _write_store(store_path, instruments=10, buckets=24)
    instruments = tuple(f"S{index:02d}USDT" for index in range(10))
    cache_path = tmp_path / "decision-frames.db"

    load_bucketed_frames(
        store_path,
        instruments=instruments,
        start_ms=0,
        end_ms=first_end_ms,
        frame_cache_path=cache_path,
    )

    next_event_ms = first_end_ms + 30_000
    next_decision_ms = (next_event_ms // 30_000 + 1) * 30_000
    store = SqliteFeatureValueStore(str(store_path), create=False)
    values = []
    for index, instrument in enumerate(instruments):
        rank = -0.9 + index * 0.2
        feature_values = {
            "rt_mid_price": 101.0,
            "rt_spread_bps": 1.0,
            "rt_depth_imbalance_5": rank,
            "rt_mark_basis_bps": rank * 5.0,
            "rt_funding_rate": rank * 0.0001,
            "rt_taker_imbalance_5s": rank,
            "rt_liquidation_imbalance_5s": 0.0,
        }
        values.extend(
            _feature_value(feature_id, instrument, next_event_ms, value)
            for feature_id, value in feature_values.items()
        )
    store.add_many(values, version=FEATURE_VERSION)
    store.close()

    refreshed = load_bucketed_frames(
        store_path,
        instruments=instruments,
        start_ms=0,
        end_ms=first_end_ms + 30_000,
        frame_cache_path=cache_path,
        # Deliberately allow a large cache lag: endpoint coverage, not the
        # lag tolerance alone, must decide whether the live tail is refreshed.
        max_cache_lag_ms=900_000,
    )

    assert all(rows[-1].decision_time_ms == next_decision_ms for rows in refreshed.values())


def test_bucket_loader_selects_latest_complete_snapshot_without_lookahead(tmp_path) -> None:
    store_path = tmp_path / "features.db"
    store = SqliteFeatureValueStore(str(store_path), create=True)
    values = []
    for knowledge_time, mid_price in ((1_000, 100.0), (20_000, 120.0)):
        feature_values = {
            "rt_mid_price": mid_price,
            "rt_spread_bps": 1.0,
            "rt_depth_imbalance_5": 0.1,
            "rt_mark_basis_bps": 0.2,
            "rt_funding_rate": 0.0001,
            "rt_taker_imbalance_5s": 0.3,
            "rt_liquidation_imbalance_5s": 0.4,
        }
        values.extend(
            FeatureValue(
                feature_id=feature_id,
                instrument_id="BTCUSDT",
                event_time=900,
                knowledge_time=knowledge_time,
                value=value,
            )
            for feature_id, value in feature_values.items()
        )
    # A later but incomplete snapshot must not displace the latest complete
    # snapshot in the same decision bucket.
    values.extend(
        FeatureValue(
            feature_id=feature_id,
            instrument_id="BTCUSDT",
            event_time=900,
            knowledge_time=25_000,
            value=999.0,
        )
        for feature_id in CORE_FEATURE_IDS[: len(CORE_FEATURE_IDS) - 1]
    )
    store.add_many(values, version=FEATURE_VERSION)
    store.close()

    frames = load_bucketed_frames(
        store_path,
        instruments=("BTCUSDT",),
        start_ms=0,
        end_ms=20_000,
    )

    assert len(frames["BTCUSDT"]) == 1
    assert frames["BTCUSDT"][0].mid_price == 120.0


def test_bucket_tail_query_does_not_scan_unbounded_feature_history(
    tmp_path,
    monkeypatch,
) -> None:
    store_path = tmp_path / "features.db"
    end_ms = _write_store(store_path, instruments=1, buckets=3_000)
    original_connect = discovery_module.sqlite3.connect

    def budgeted_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        callbacks_remaining = 50

        def progress() -> int:
            nonlocal callbacks_remaining
            callbacks_remaining -= 1
            return int(callbacks_remaining < 0)

        connection.set_progress_handler(progress, 1_000)
        return connection

    monkeypatch.setattr(discovery_module.sqlite3, "connect", budgeted_connect)

    frames = load_bucketed_frames(
        store_path,
        instruments=("S00USDT",),
        start_ms=end_ms - 60_000,
        end_ms=end_ms,
    )

    assert len(frames["S00USDT"]) == 3


def test_cycle_runs_the_fixed_budget_without_paper_or_live_authority(
    tmp_path,
    monkeypatch,
) -> None:
    discovery_module._LAST_RELEASE_RUN_ATTESTATION = None
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
    monkeypatch_commit = "a" * 40
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", monkeypatch_commit)

    report = run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=tmp_path / "registry.db",
        output_path=output_path,
        now_ms=end_ms + 1_000,
        policy=policy,
        persist_trials=False,
    )
    attestation = discovery_module.release_run_attestation()

    assert report["status"] == "RUNNING_EXPLORATORY"
    assert report["autonomy"]["autonomous_search_running"] is True
    assert report["autonomy"]["requires_developer_ai"] is False
    assert report["funnel"]["pre_registered"] == 30
    assert report["funnel"]["evaluated"] == 30
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
        "broker_connectivity": False,
    }
    assert all(row["status"] == "EXPLORATORY_ONLY" for row in report["candidates"])
    assert all("REALTIME_HISTORY_BELOW_30D" in row["gate_reasons"] for row in report["candidates"])
    assert json.loads(output_path.read_text(encoding="utf-8"))["dataset_version"] == report["dataset_version"]
    assert attestation is not None
    assert attestation["complete"] is True
    assert attestation["module"] == "realtime_alpha_discovery"
    assert attestation["release_commit"] == monkeypatch_commit
    assert attestation["input_hashes"]["feature_status"] == stable_hash(
        json.loads(status_path.read_text(encoding="utf-8"))
    )
    assert attestation["input_hashes"]["decision_frame_view"]
    assert attestation["input_hashes"]["executable_depth_view"]
    assert attestation["input_hashes"]["family_registry_authority_before"]
    assert attestation["output_hashes"]["family_registry_authority_after"]
    assert attestation["output_hashes"]["realtime_discovery_report_file"] == (
        hashlib.sha256(output_path.read_bytes()).hexdigest()
    )
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }

    repeated = run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=tmp_path / "registry.db",
        output_path=output_path,
        now_ms=end_ms + 2_000,
        policy=policy,
        persist_trials=False,
    )
    assert repeated["funnel"]["pre_registered"] == 30
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
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "b" * 40)
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
    attestation = discovery_module.release_run_attestation()

    assert report["search_id"]
    assert attestation is not None and attestation["complete"] is True
    assert attestation["input_hashes"]["experiment_evaluation_context"]
    assert attestation["output_hashes"]["experiment_search_artifacts"]
    verified = SqliteExperimentStore(str(experiment_db), create=False)
    starts = verified._conn.execute(
        "SELECT recorded_at FROM search_attempt_events WHERE status='STARTED' ORDER BY event_seq"
    ).fetchall()
    verified.close()
    assert starts[-1][0] == persistence_now


def test_cycle_turns_positive_realized_feedback_into_fresh_oos_challengers(tmp_path) -> None:
    store_path = tmp_path / "features.db"
    end_ms = _write_store(store_path, buckets=80)
    status_path = tmp_path / "feature-status.json"
    status_path.write_text(
        json.dumps({"status": "PASS", "generated_at_ms": end_ms}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.db"
    policy = RealtimeAlphaPolicy(
        exploration_min_span_ms=60_000,
        validation_min_span_ms=3_600_000,
        shadow_min_span_ms=7_200_000,
        max_feature_age_ms=60_000,
        min_rows_per_feature=1,
        min_evaluation_periods=2,
        min_cross_section=8,
    )
    run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=registry_path,
        output_path=tmp_path / "first.json",
        now_ms=end_ms + 1_000,
        policy=policy,
        persist_trials=False,
    )
    parent_id = "rt_liquidation_cascade_2m_v1"
    registry = AlphaFamilyRegistry(str(registry_path), create=False)
    try:
        for index in range(10):
            registry.record_paper_run(
                parent_id,
                run_id=f"realization-{index}",
                status="ENGINEERING_SHADOW_REALIZED",
                metrics={
                    "candidate_id": parent_id,
                    "as_of_ms": end_ms - (9 - index) * 30_000,
                    "net_return": 0.001 + index * 0.00001,
                    "paper_only": True,
                    "promotion_eligible": False,
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
                dataset_version="shadow-v1",
                recorded_at=end_ms + 2_000 + index,
            )
    finally:
        registry.close()

    # The independent formal shadow ledger may share the family registry, but
    # its evidence must never enter the adaptive selector's engineering-only
    # feedback stream.
    registry = AlphaFamilyRegistry(str(registry_path), create=False)
    try:
        registry.record_paper_run(
            parent_id,
            run_id="formal-realization-1",
            status="FORMAL_BLINDED_SHADOW_REALIZED",
            metrics={
                "candidate_id": parent_id,
                "as_of_ms": end_ms + 1_500,
                "net_return": 0.25,
                "paper_only": True,
                "promotion_eligible": False,
                "live_allowed": False,
                "order_submission_allowed": False,
            },
            dataset_version="formal-shadow-v1",
            recorded_at=end_ms + 1_600,
        )
    finally:
        registry.close()

    report = run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=registry_path,
        output_path=tmp_path / "second.json",
        now_ms=end_ms + 20_000,
        policy=policy,
        persist_trials=False,
    )

    assert report["feedback"]["status"] == "ADAPTIVE_EXPLORATION_READY"
    assert report["feedback"]["consumed_realizations"] == 10
    assert report["funnel"]["base_pre_registered"] == 30
    assert report["funnel"]["adaptive_pre_registered"] == 2
    adaptive = [row for row in report["candidates"] if row["adaptive_feedback"]]
    assert {row["candidate_id"] for row in adaptive} == {
        "rt_liquidation_cascade_1m_fb1",
        "rt_liquidation_cascade_4m_fb1",
    }
    assert all(row["parent_candidate_id"] == parent_id for row in adaptive)
    assert all(row["fresh_oos_required"] is True for row in adaptive)
    assert all(
        "ADAPTIVE_FEEDBACK_FRESH_OOS_BELOW_30D" in row["gate_reasons"]
        for row in adaptive
    )
    assert all(row["gate_pass"] is False for row in adaptive)
    assert report["manifest_hash"] == stable_hash(report["hypotheses"])

    adaptive_id = "rt_liquidation_cascade_1m_fb1"
    registry = AlphaFamilyRegistry(str(registry_path), create=False)
    try:
        registry.record_paper_run(
            adaptive_id,
            run_id="adaptive-realization-1",
            status="ENGINEERING_SHADOW_REALIZED",
            metrics={
                "candidate_id": adaptive_id,
                "as_of_ms": end_ms + 21_000,
                "net_return": 0.0005,
                "paper_only": True,
                "promotion_eligible": False,
                "live_allowed": False,
                "order_submission_allowed": False,
            },
            dataset_version="shadow-v1",
            recorded_at=end_ms + 22_000,
        )
    finally:
        registry.close()

    report_after_adaptive_realization = run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=registry_path,
        output_path=tmp_path / "third.json",
        now_ms=end_ms + 30_000,
        policy=policy,
        persist_trials=False,
    )
    adaptive_feedback = next(
        item
        for item in report_after_adaptive_realization["feedback"]["candidates"]
        if item["candidate_id"] == adaptive_id
    )
    assert adaptive_feedback["n_realizations"] == 1
    assert report_after_adaptive_realization["feedback"]["consumed_realizations"] == 11


def test_cycle_rejects_tampered_feedback_registry_before_allocating_trials(tmp_path) -> None:
    discovery_module._LAST_RELEASE_RUN_ATTESTATION = {"complete": True}
    store_path = tmp_path / "features.db"
    end_ms = _write_store(store_path, buckets=4)
    status_path = tmp_path / "feature-status.json"
    status_path.write_text(
        json.dumps({"status": "PASS", "generated_at_ms": end_ms}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.db"
    policy = RealtimeAlphaPolicy(
        exploration_min_span_ms=60_000,
        validation_min_span_ms=3_600_000,
        shadow_min_span_ms=7_200_000,
        max_feature_age_ms=60_000,
        min_rows_per_feature=1,
        min_evaluation_periods=2,
        min_cross_section=8,
    )
    run_cycle(
        source_store=store_path,
        feature_status=status_path,
        registry_db=registry_path,
        output_path=tmp_path / "first.json",
        now_ms=end_ms + 1_000,
        policy=policy,
        persist_trials=False,
    )
    connection = sqlite3.connect(registry_path)
    connection.execute("DROP TRIGGER alpha_family_events_no_update")
    connection.execute(
        "UPDATE alpha_family_events SET payload_json='{}' WHERE event_seq=1"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RealtimeAlphaWorkerError, match="registry chain is invalid"):
        run_cycle(
            source_store=store_path,
            feature_status=status_path,
            registry_db=registry_path,
            output_path=tmp_path / "second.json",
            now_ms=end_ms + 2_000,
            policy=policy,
            persist_trials=False,
        )
    assert discovery_module.release_run_attestation() is None
