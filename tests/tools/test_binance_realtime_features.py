import ast
import hashlib
from pathlib import Path

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from services.features.value_store import SqliteFeatureValueStore
from services.features.binance_realtime_projection import CORE_FEATURE_IDS, FEATURE_IDS
from tools import binance_realtime_features as realtime


def _snapshot(instrument_id: str, index: int) -> dict:
    observed = 10_000 + index * 10
    price = 100.0 + index * 0.01
    return {
        "schema_version": 1,
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "venue": "binance_usdm",
        "instrument_id": instrument_id,
        "event_time_ms": observed - 1,
        "knowledge_time_ms": observed,
        "observed_at_ms": observed,
        "quality": {
            "complete_core": True,
            "missing_channels": [],
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
        "book": {"mid": price, "spread_bps": 1.0 + index * 0.001},
        "depth20": {
            "bids": [
                [str(price - 0.01 - level * 0.01), str(100 + level + index)]
                for level in range(5)
            ],
            "asks": [
                [str(price + 0.01 + level * 0.01), str(110 + level + index)]
                for level in range(5)
            ],
        },
        "mark": {
            "mark_price": price + 0.01,
            "index_price": price,
            "funding_rate": 0.0001 + index * 0.0000001,
        },
        "trade_window": {
            "buy_taker_notional": 100 + index,
            "sell_taker_notional": 90 + index,
        },
        "liquidation_window": {
            "long_liquidation_notional": index % 3,
            "short_liquidation_notional": index % 2,
        },
    }


def test_execute_materializes_complete_drift_ready_feature_cache(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "a" * 40)
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        for index in range(300):
            for ticker in ("AAA", "BBB"):
                source.append_snapshot(_snapshot(f"{ticker}USDT", index))
        incomplete = _snapshot("AAAUSDT", 301)
        incomplete["quality"]["complete_core"] = False
        incomplete["quality"]["missing_channels"] = ["depth20"]
        source.append_snapshot(incomplete)
    finally:
        source.close()

    feature_path = tmp_path / "features.db"
    status_path = tmp_path / "status.json"
    result = realtime.execute(
        now_ms=20_000,
        source_store_path=str(source_path),
        feature_store_path=str(feature_path),
        batch_store_path=str(tmp_path / "batches.db"),
        status_path=str(status_path),
        source_row_limit=601,
    )
    attestation = realtime.release_run_attestation()
    assert result["status"] == "PASS"
    assert result["features"]["instrument_count"] == 2
    assert result["features"]["new_value_count"] == 2 * 300 * len(FEATURE_IDS)
    assert result["drift"]["series_count"] == 2 * len(CORE_FEATURE_IDS)
    assert result["drift"]["structurally_ready"] is True
    assert result["drift"]["calibration"]["status"] == "READY"
    assert result["drift"]["calibration"]["calibration_id"]
    assert result["drift"]["policy"]["calibration"] == (
        "EMPIRICALLY_CALIBRATED_PIT_V1"
    )
    assert result["drift"]["policy"]["warn_ks"] < (
        result["drift"]["policy"]["block_ks"]
    )
    assert result["features"]["incremental_projected_value_count"] == (
        2 * 300 * len(FEATURE_IDS)
    )
    assert result["features"]["skipped_existing_snapshot_count"] == 0
    assert result["drift"]["history"]["max_observations_per_series"] == 240
    assert result["drift"]["history"]["loaded_observation_count"] == (
        2 * len(CORE_FEATURE_IDS) * 240
    )
    assert result["runtime"]["elapsed_ms"] >= 0
    assert result["source"]["chain_verification"]["mode"] == "FULL_BOOTSTRAP"
    assert result["source"]["chain_verification"]["verified_suffix_rows"] == 601
    assert result["batch"]["chain_valid"] is True
    assert result["agent_input"]["automatic_action"] is False
    assert attestation is not None
    assert attestation["complete"] is True
    assert attestation["module"] == "binance_realtime_features"
    assert attestation["release_commit"] == "a" * 40
    assert attestation["input_hashes"]["public_source_snapshot_bundle"] == (
        result["source"]["snapshot_digest"]
    )
    assert attestation["input_hashes"]["feature_batch_authority_before"]
    assert attestation["input_hashes"]["drift_feature_view"]
    assert attestation["output_hashes"]["feature_batch_authority_after"]
    assert attestation["output_hashes"]["feature_projection_frontier"]
    assert attestation["output_hashes"]["realtime_feature_status_file"] == (
        hashlib.sha256(status_path.read_bytes()).hexdigest()
    )
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }

    store = SqliteFeatureValueStore(str(feature_path))
    try:
        rows = store.as_of(
            "rt_mid_price",
            realtime.FEATURE_VERSION,
            "AAAUSDT",
            20_000,
        )
        assert len(rows) == 300
    finally:
        store.close()

    repeated = realtime.execute(
        now_ms=20_001,
        source_store_path=str(source_path),
        feature_store_path=str(feature_path),
        batch_store_path=str(tmp_path / "batches.db"),
        status_path=str(tmp_path / "status.json"),
        source_row_limit=601,
    )
    assert repeated["features"]["new_value_count"] == 0
    assert repeated["features"]["incremental_projected_value_count"] == 0
    assert repeated["features"]["eligible_source_snapshot_count"] == 0
    assert repeated["features"]["skipped_existing_snapshot_count"] == 600
    assert repeated["batch"]["new_batch"] is False
    assert repeated["source"]["chain_verification"]["mode"] == (
        "INCREMENTAL_FROM_TRUSTED_BATCH"
    )
    assert repeated["source"]["chain_verification"]["verified_suffix_rows"] == 0
    periodic = realtime._trusted_source_checkpoint(
        str(tmp_path / "batches.db"),
        now_ms=20_000 + realtime.FULL_CHAIN_AUDIT_INTERVAL_MS,
    )
    assert periodic["verification_mode"] == "FULL_PERIODIC_AUDIT"
    assert periodic["checkpoint_seq"] == 0


def test_execute_clears_stale_attestation_before_failed_run(
    tmp_path,
    monkeypatch,
):
    realtime._LAST_RELEASE_RUN_ATTESTATION = {"complete": True}
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        source.append_snapshot(_snapshot("AAAUSDT", 1))
    finally:
        source.close()

    with pytest.raises(
        realtime.BinanceRealtimeFeatureError,
        match="missing instruments",
    ):
        realtime.execute(
            now_ms=20_000,
            source_store_path=str(source_path),
            feature_store_path=str(tmp_path / "features.db"),
            batch_store_path=str(tmp_path / "batches.db"),
            status_path=str(tmp_path / "status.json"),
            source_row_limit=1,
        )

    assert realtime.release_run_attestation() is None


def test_incremental_projection_replays_from_minimum_feature_watermark(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        for index in range(300):
            for ticker in ("AAA", "BBB"):
                source.append_snapshot(_snapshot(f"{ticker}USDT", index))
    finally:
        source.close()

    feature_path = tmp_path / "features.db"
    feature_store = SqliteFeatureValueStore(str(feature_path), create=True)
    try:
        # Simulate a crash/legacy partial write: the latest mid exists, while
        # the other seven features do not.  The minimum watermark must remain
        # absent so the complete source history is replayed safely.
        feature_store.add(
            realtime.project_snapshot(_snapshot("AAAUSDT", 299))[0],
            version=realtime.FEATURE_VERSION,
        )
    finally:
        feature_store.close()

    result = realtime.execute(
        now_ms=20_000,
        source_store_path=str(source_path),
        feature_store_path=str(feature_path),
        batch_store_path=str(tmp_path / "batches.db"),
        status_path=str(tmp_path / "status.json"),
        source_row_limit=600,
    )

    assert result["features"]["watermarks_before"]["AAAUSDT"] is None
    assert result["features"]["eligible_source_snapshot_count"] == 600
    assert result["features"]["new_value_count"] == (
        2 * 300 * len(FEATURE_IDS) - 1
    )


def test_source_snapshot_selection_keeps_incomplete_restart_rows_as_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        complete = _snapshot("AAAUSDT", 1)
        source.append_snapshot(complete)
        incomplete = _snapshot("BBBUSDT", 1)
        incomplete["quality"]["complete_core"] = False
        incomplete["quality"]["missing_channels"] = ["mark"]
        source.append_snapshot(incomplete)

        with pytest.raises(
            realtime.BinanceRealtimeFeatureError,
            match="missing instruments",
        ):
            realtime._source_snapshots(
                source,
                limit=2,
                checkpoint_seq=0,
                checkpoint_row_hash="",
            )

        assert source.row_count() == 2
        assert source.chain_valid() is True
    finally:
        source.close()


def test_realtime_feature_worker_has_no_network_or_execution_surface():
    source = Path(realtime.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        str(node.module).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imports.isdisjoint({"urllib", "requests", "websocket", "ccxt", "binance"})
    for token in ("api_key", "api_secret", "submit_order", "place_order"):
        assert token not in source
