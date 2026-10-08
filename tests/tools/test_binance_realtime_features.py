import ast
import hashlib
import json
from pathlib import Path

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from services.data.binance_stream_cursor import StreamCursorStore
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
        "received_at_ms": observed - 1,
        "processed_at_ms": observed,
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


def _write_stream_status(
    path: Path,
    source_path: Path,
    *,
    now_ms: int,
    cursor_verification: dict | None = None,
) -> None:
    source = BinanceRealtimeStore(str(source_path), create=False)
    try:
        source_verification = source.verify_chain_from()
    finally:
        source.close()
    status = {
        "schema_version": 1,
        "generated_at_ms": now_ms,
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "store": {
            "chain_valid": True,
            "chain_verification": {
                **source_verification,
                "verification_mode": "FULL_SUBPROCESS_BOOTSTRAP",
                "bootstrap_verification_mode": "FULL_SUBPROCESS_BOOTSTRAP",
                "verified_at_ms": now_ms,
                "full_verified_at_ms": now_ms,
                "batch_authority": hashlib.sha256(
                    b"test-feature-batch-authority"
                ).hexdigest(),
            },
        },
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "private_stream": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }
    if cursor_verification is not None:
        status["cursor"] = {
            "chain": {
                **cursor_verification,
                "verification_mode": "FULL_SUBPROCESS_BOOTSTRAP",
                "bootstrap_verification_mode": "FULL_SUBPROCESS_BOOTSTRAP",
                "verified_at_ms": now_ms,
                "full_verified_at_ms": now_ms,
            }
        }
    path.write_text(json.dumps(status, sort_keys=True), encoding="utf-8")


def _full_bootstrap_checkpoint() -> dict:
    return {
        "checkpoint_seq": 0,
        "checkpoint_row_hash": "",
        "full_verified_at_ms": None,
        "verification_mode": "FULL_BOOTSTRAP",
    }


def test_resident_attestation_accepts_bounded_refresh_after_worker_start(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA",))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        source.append_snapshot(_snapshot("AAAUSDT", 1))
        cursor = StreamCursorStore(str(source_path), create=True)
        try:
            cursor.observe(
                channel="depth20",
                instrument_id="AAAUSDT",
                first_update_id=1,
                final_update_id=2,
                previous_final_update_id=0,
                event_time_ms=19_998,
                observed_at_ms=19_999,
            )
            cursor_verification = cursor.verify_chain_from()
        finally:
            cursor.close()
        status_path = tmp_path / "stream-status.json"
        status_now_ms = 20_000 + realtime.MAX_STREAM_STATUS_FUTURE_SKEW_MS
        _write_stream_status(
            status_path,
            source_path,
            now_ms=status_now_ms,
            cursor_verification=cursor_verification,
        )

        _, source_verification = realtime._source_chain_verification(
            source,
            limit=1,
            checkpoint=_full_bootstrap_checkpoint(),
            stream_status_path=str(status_path),
            now_ms=20_000,
        )
        cursor_chain_verification = realtime._cursor_chain_verification(
            str(source_path),
            checkpoint=_full_bootstrap_checkpoint(),
            stream_status_path=str(status_path),
            now_ms=20_000,
        )
    finally:
        source.close()

    assert source_verification["basis"] == (
        "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )
    assert cursor_chain_verification is not None
    assert cursor_chain_verification["basis"] == (
        "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )


def test_resident_attestation_rejects_excessive_future_skew(tmp_path):
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        source.append_snapshot(_snapshot("AAAUSDT", 1))
        cursor = StreamCursorStore(str(source_path), create=True)
        try:
            cursor.observe(
                channel="depth20",
                instrument_id="AAAUSDT",
                first_update_id=1,
                final_update_id=2,
                previous_final_update_id=0,
                event_time_ms=19_998,
                observed_at_ms=19_999,
            )
            cursor_verification = cursor.verify_chain_from()
        finally:
            cursor.close()
        status_path = tmp_path / "stream-status.json"
        _write_stream_status(
            status_path,
            source_path,
            now_ms=20_001 + realtime.MAX_STREAM_STATUS_FUTURE_SKEW_MS,
            cursor_verification=cursor_verification,
        )

        with pytest.raises(
            realtime.BinanceRealtimeFeatureError,
            match="source full-audit attestation is unavailable or invalid",
        ):
            realtime._source_chain_verification(
                source,
                limit=1,
                checkpoint=_full_bootstrap_checkpoint(),
                stream_status_path=str(status_path),
                now_ms=20_000,
            )
        with pytest.raises(
            realtime.BinanceRealtimeFeatureError,
            match="cursor full-audit attestation is unavailable or invalid",
        ):
            realtime._cursor_chain_verification(
                str(source_path),
                checkpoint=_full_bootstrap_checkpoint(),
                stream_status_path=str(status_path),
                now_ms=20_000,
            )
    finally:
        source.close()


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
    cursor = StreamCursorStore(str(source_path), create=True)
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="AAAUSDT",
            first_update_id=1,
            final_update_id=2,
            previous_final_update_id=0,
            event_time_ms=19_998,
            observed_at_ms=19_999,
        )
        cursor_verification = cursor.verify_chain_from()
    finally:
        cursor.close()
    stream_status_path = tmp_path / "stream-status.json"
    _write_stream_status(
        stream_status_path,
        source_path,
        now_ms=19_999,
        cursor_verification=cursor_verification,
    )

    feature_path = tmp_path / "features.db"
    status_path = tmp_path / "status.json"
    result = realtime.execute(
        now_ms=20_000,
        source_store_path=str(source_path),
        stream_status_path=str(stream_status_path),
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
    assert result["source"]["chain_verification"]["verified_suffix_rows"] == 0
    assert result["source"]["chain_verification"]["attested_frontier_seq"] == 601
    assert result["source"]["chain_verification"]["basis"] == (
        "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )
    assert result["source"]["cursor_chain_checkpoint"]["verified_seq"] == 1
    assert (
        result["source"]["cursor_chain_verification"]["mode"]
        == "FULL_BOOTSTRAP"
    )
    assert (
        result["source"]["cursor_chain_verification"]["basis"]
        == "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )
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
        stream_status_path=str(stream_status_path),
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
    assert (
        repeated["source"]["cursor_chain_verification"]["mode"]
        == "INCREMENTAL_FROM_TRUSTED_BATCH"
    )
    assert (
        repeated["source"]["cursor_chain_verification"][
            "verified_suffix_events"
        ]
        == 0
    )
    periodic = realtime._trusted_source_checkpoint(
        str(tmp_path / "batches.db"),
        now_ms=20_000 + realtime.FULL_CHAIN_AUDIT_INTERVAL_MS,
    )
    assert periodic["verification_mode"] == "FULL_PERIODIC_AUDIT"
    assert periodic["checkpoint_seq"] == 0
    cursor_periodic = realtime._trusted_cursor_checkpoint(
        str(tmp_path / "batches.db"),
        now_ms=20_000 + realtime.CURSOR_FULL_CHAIN_AUDIT_INTERVAL_MS,
    )
    assert cursor_periodic["verification_mode"] == "FULL_PERIODIC_AUDIT"
    assert cursor_periodic["checkpoint_seq"] == 0


def test_live_execute_publishes_completion_clock_not_start_clock(
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

    stream_status_path = tmp_path / "stream-status.json"
    _write_stream_status(stream_status_path, source_path, now_ms=20_000)

    wall_times = iter((20.0, 1_220.0))
    monkeypatch.setattr(realtime.time, "time", lambda: next(wall_times))

    result = realtime.execute(
        source_store_path=str(source_path),
        stream_status_path=str(stream_status_path),
        feature_store_path=str(tmp_path / "features.db"),
        batch_store_path=str(tmp_path / "batches.db"),
        status_path=str(tmp_path / "status.json"),
        source_row_limit=600,
    )

    assert result["generated_at_ms"] == 1_220_000
    assert result["runtime"]["started_at_ms"] == 20_000
    assert result["runtime"]["completed_at_ms"] == 1_220_000
    assert result["runtime"]["completed_at_ms"] - result["runtime"]["started_at_ms"] == (
        20 * 60_000
    )
    assert set(result["runtime"]["stage_elapsed_ms"]) == {
        "trusted_source_checkpoint",
        "source_snapshot_load",
        "cursor_chain_verification",
        "source_contract_hashing",
        "feature_projection",
        "feature_store_append",
        "drift_history_read",
        "drift_calibration_and_assessment",
        "projection_frontier",
        "batch_journal",
    }
    assert all(
        elapsed >= 0
        for elapsed in result["runtime"]["stage_elapsed_ms"].values()
    )


def test_cursor_journal_without_full_audit_attestation_fails_closed(tmp_path):
    source_path = tmp_path / "source.db"
    cursor = StreamCursorStore(str(source_path), create=True)
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=1,
            final_update_id=2,
            previous_final_update_id=0,
            event_time_ms=99,
            observed_at_ms=100,
        )
    finally:
        cursor.close()

    with pytest.raises(
        realtime.BinanceRealtimeFeatureError,
        match="full-audit attestation",
    ):
        realtime._cursor_chain_verification(
            str(source_path),
            checkpoint={
                "checkpoint_seq": 0,
                "checkpoint_row_hash": "",
                "full_verified_at_ms": None,
                "verification_mode": "FULL_BOOTSTRAP",
            },
            stream_status_path=str(tmp_path / "missing-status.json"),
            now_ms=1_000,
        )


def test_source_chain_uses_fresh_resident_full_audit_for_local_suffix(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        source.append_snapshot(_snapshot("AAAUSDT", 1))
        source.append_snapshot(_snapshot("BBBUSDT", 2))
        head = source.verify_chain_from()
    finally:
        source.close()
    now_ms = 50_000
    status = {
        "schema_version": 1,
        "generated_at_ms": now_ms,
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "store": {
            "chain_valid": True,
            "chain_verification": {
                **head,
                "bootstrap_verification_mode": (
                    "FULL_PERIODIC_SUBPROCESS_AUDIT"
                ),
                "full_verified_at_ms": now_ms,
                "batch_authority": "a" * 64,
            },
        },
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "private_stream": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }
    status_path = tmp_path / "stream-status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    source = BinanceRealtimeStore(str(source_path), create=False)
    try:
        rows, verification = realtime._source_chain_verification(
            source,
            limit=2,
            checkpoint={
                "checkpoint_seq": 0,
                "checkpoint_row_hash": "",
                "full_verified_at_ms": 1,
                "verification_mode": "FULL_PERIODIC_AUDIT",
            },
            stream_status_path=str(status_path),
            now_ms=now_ms,
        )
    finally:
        source.close()

    assert {row["instrument_id"] for row in rows} == {"AAAUSDT", "BBBUSDT"}
    assert verification["anchor_seq"] == head["verified_seq"]
    assert verification["verified_suffix_rows"] == 0
    assert verification["full_verified_at_ms"] == now_ms
    assert verification["basis"] == (
        "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )
    assert verification["stream_status_sha256"] == hashlib.sha256(
        status_path.read_bytes()
    ).hexdigest()


def test_source_chain_adopts_newer_resident_full_audit_before_batch_expiry(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        source.append_snapshot(_snapshot("AAAUSDT", 1))
        batch_anchor = source.verify_chain_from()
        source.append_snapshot(_snapshot("BBBUSDT", 2))
        resident_anchor = source.verify_chain_from()
    finally:
        source.close()

    now_ms = 50_000
    status_path = tmp_path / "stream-status.json"
    _write_stream_status(status_path, source_path, now_ms=now_ms)
    source = BinanceRealtimeStore(str(source_path), create=False)
    try:
        _rows, verification = realtime._source_chain_verification(
            source,
            limit=2,
            checkpoint={
                "checkpoint_seq": batch_anchor["verified_seq"],
                "checkpoint_row_hash": batch_anchor["verified_row_hash"],
                "full_verified_at_ms": now_ms - 1_000,
                "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
            },
            stream_status_path=str(status_path),
            now_ms=now_ms,
        )
    finally:
        source.close()

    assert verification["anchor_seq"] == resident_anchor["verified_seq"]
    assert verification["verified_suffix_rows"] == 0
    assert verification["full_verified_at_ms"] == now_ms
    assert verification["basis"] == (
        "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )


def test_cursor_chain_adopts_newer_resident_full_audit_before_batch_expiry(
    tmp_path,
) -> None:
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        source.append_snapshot(_snapshot("AAAUSDT", 1))
    finally:
        source.close()
    cursor = StreamCursorStore(str(source_path), create=True)
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="AAAUSDT",
            first_update_id=1,
            final_update_id=2,
            previous_final_update_id=0,
            event_time_ms=99,
            observed_at_ms=100,
        )
        batch_anchor = cursor.verify_chain_from()
        cursor.observe(
            channel="depth20",
            instrument_id="AAAUSDT",
            first_update_id=3,
            final_update_id=4,
            previous_final_update_id=2,
            event_time_ms=199,
            observed_at_ms=200,
        )
        resident_anchor = cursor.verify_chain_from()
    finally:
        cursor.close()

    now_ms = 50_000
    status_path = tmp_path / "stream-status.json"
    _write_stream_status(
        status_path,
        source_path,
        now_ms=now_ms,
        cursor_verification=resident_anchor,
    )
    verification = realtime._cursor_chain_verification(
        str(source_path),
        checkpoint={
            "checkpoint_seq": batch_anchor["verified_seq"],
            "checkpoint_row_hash": batch_anchor["verified_row_hash"],
            "full_verified_at_ms": now_ms - 1_000,
            "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
        },
        stream_status_path=str(status_path),
        now_ms=now_ms,
    )

    assert verification is not None
    assert verification["anchor_seq"] == resident_anchor["verified_seq"]
    assert verification["verified_suffix_events"] == 0
    assert verification["full_verified_at_ms"] == now_ms
    assert verification["basis"] == (
        "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )


def test_valid_batch_checkpoint_does_not_require_resident_status(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA",))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        source.append_snapshot(_snapshot("AAAUSDT", 1))
        source_anchor = source.verify_chain_from()
        cursor = StreamCursorStore(str(source_path), create=True)
        try:
            cursor.observe(
                channel="depth20",
                instrument_id="AAAUSDT",
                first_update_id=1,
                final_update_id=2,
                previous_final_update_id=0,
                event_time_ms=99,
                observed_at_ms=100,
            )
            cursor_anchor = cursor.verify_chain_from()
        finally:
            cursor.close()

        checkpoint_time_ms = 49_000
        _rows, source_verification = realtime._source_chain_verification(
            source,
            limit=1,
            checkpoint={
                "checkpoint_seq": source_anchor["verified_seq"],
                "checkpoint_row_hash": source_anchor["verified_row_hash"],
                "full_verified_at_ms": checkpoint_time_ms,
                "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
            },
            stream_status_path=str(tmp_path / "missing-status.json"),
            now_ms=50_000,
        )
    finally:
        source.close()
    cursor_verification = realtime._cursor_chain_verification(
        str(source_path),
        checkpoint={
            "checkpoint_seq": cursor_anchor["verified_seq"],
            "checkpoint_row_hash": cursor_anchor["verified_row_hash"],
            "full_verified_at_ms": checkpoint_time_ms,
            "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
        },
        stream_status_path=str(tmp_path / "missing-status.json"),
        now_ms=50_000,
    )

    assert source_verification["basis"] == "TRUSTED_BATCH_CHECKPOINT_SUFFIX"
    assert source_verification["full_verified_at_ms"] == checkpoint_time_ms
    assert cursor_verification is not None
    assert cursor_verification["basis"] == "TRUSTED_BATCH_CHECKPOINT_SUFFIX"
    assert cursor_verification["full_verified_at_ms"] == checkpoint_time_ms


def test_source_chain_never_falls_back_to_genesis_without_resident_proof(
    tmp_path, monkeypatch
) -> None:
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("feature worker must not start a genesis audit")

    monkeypatch.setattr(realtime, "_source_snapshots", forbidden)
    try:
        with pytest.raises(
            realtime.BinanceRealtimeFeatureError,
            match="source full-audit attestation",
        ):
            realtime._source_chain_verification(
                source,
                limit=1,
                checkpoint={
                    "checkpoint_seq": 0,
                    "checkpoint_row_hash": "",
                    "full_verified_at_ms": None,
                    "verification_mode": "FULL_BOOTSTRAP",
                },
                stream_status_path=str(tmp_path / "missing-status.json"),
                now_ms=1_000,
            )
    finally:
        source.close()

    assert called is False


def test_periodic_full_audit_has_a_distinct_content_addressed_batch_id(
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

    paths = {
        "source_store_path": str(source_path),
        "stream_status_path": str(tmp_path / "stream-status.json"),
        "feature_store_path": str(tmp_path / "features.db"),
        "batch_store_path": str(tmp_path / "batches.db"),
        "status_path": str(tmp_path / "status.json"),
        "source_row_limit": 600,
    }
    _write_stream_status(
        tmp_path / "stream-status.json", source_path, now_ms=20_000
    )
    first = realtime.execute(now_ms=20_000, **paths)
    periodic_now_ms = 20_000 + realtime.FULL_CHAIN_AUDIT_INTERVAL_MS
    _write_stream_status(
        tmp_path / "stream-status.json",
        source_path,
        now_ms=periodic_now_ms,
    )
    periodic = realtime.execute(
        now_ms=periodic_now_ms,
        **paths,
    )

    assert first["batch"]["new_batch"] is True
    assert periodic["batch"]["new_batch"] is True
    assert periodic["batch"]["batch_id"] != first["batch"]["batch_id"]
    assert periodic["source"]["chain_verification"]["mode"] == (
        "FULL_PERIODIC_AUDIT"
    )


def test_execute_persists_newer_resident_audit_before_batch_proof_expires(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        for index in range(300):
            for ticker in ("AAA", "BBB"):
                source.append_snapshot(_snapshot(f"{ticker}USDT", index))
    finally:
        source.close()

    stream_status_path = tmp_path / "stream-status.json"
    batch_store_path = tmp_path / "batches.db"
    paths = {
        "source_store_path": str(source_path),
        "stream_status_path": str(stream_status_path),
        "feature_store_path": str(tmp_path / "features.db"),
        "batch_store_path": str(batch_store_path),
        "status_path": str(tmp_path / "status.json"),
        "source_row_limit": 600,
    }
    _write_stream_status(stream_status_path, source_path, now_ms=20_000)
    first = realtime.execute(now_ms=20_000, **paths)
    _write_stream_status(stream_status_path, source_path, now_ms=21_000)
    renewed = realtime.execute(now_ms=21_000, **paths)
    persisted = realtime._trusted_source_checkpoint(
        str(batch_store_path),
        now_ms=21_001,
    )

    assert first["batch"]["new_batch"] is True
    assert renewed["batch"]["new_batch"] is True
    assert renewed["batch"]["batch_id"] != first["batch"]["batch_id"]
    assert renewed["source"]["chain_verification"]["basis"] == (
        "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    )
    assert persisted["verification_mode"] == "INCREMENTAL_FROM_TRUSTED_BATCH"
    assert persisted["full_verified_at_ms"] == 21_000


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
    stream_status_path = tmp_path / "stream-status.json"
    _write_stream_status(stream_status_path, source_path, now_ms=20_000)

    with pytest.raises(
        realtime.BinanceRealtimeFeatureError,
        match="missing instruments",
    ):
        realtime.execute(
            now_ms=20_000,
            source_store_path=str(source_path),
            stream_status_path=str(stream_status_path),
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
    stream_status_path = tmp_path / "stream-status.json"
    _write_stream_status(stream_status_path, source_path, now_ms=20_000)

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
        stream_status_path=str(stream_status_path),
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
