"""Materialize Binance public WebSocket snapshots into realtime PIT features.

This scheduled L2 worker reads only the local hash-chained public snapshot
store.  It creates an append-only FeatureValue cache, evaluates trailing KS
drift as a non-load-bearing diagnostic, and journals every source-bound batch
in a second hash chain.  It has no credentials, private stream, broker, or
order-submission capability.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SUCCESS,
)
from services.data.binance_realtime_store import BinanceRealtimeStore
from services.data.binance_stream_cursor import (
    StreamCursorStore,
    StreamCursorStoreError,
)
from services.data.distribution_drift import (
    evaluate_feature_distribution_drift,
)
from services.data.distribution_drift_calibration import (
    READY as CALIBRATION_READY,
    DriftCalibrationPolicy,
    DriftCalibrationResult,
    calibrate_distribution_drift,
)
from services.features.binance_realtime_projection import (
    CORE_FEATURE_IDS,
    FEATURE_IDS,
    FEATURE_VERSION,
    MATERIALIZER_VERSION,
    project_snapshot,
)
from services.features.realtime_feature_batch_store import (
    RealtimeFeatureBatchStore,
    trusted_cursor_chain_checkpoint,
    trusted_source_chain_checkpoint,
)
from services.features.value_store import SqliteFeatureValueStore
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


DEFAULT_SOURCE_STORE = os.path.join(
    "data", "cache", "binance_realtime_stream.db"
)
DEFAULT_STREAM_STATUS = os.path.join(
    "data", "cache", "binance_realtime_stream_status.json"
)
DEFAULT_FEATURE_STORE = os.path.join(
    "data", "cache", "binance_realtime_features.db"
)
DEFAULT_BATCH_STORE = os.path.join(
    "data", "cache", "binance_realtime_feature_batches.db"
)
DEFAULT_STATUS = os.path.join(
    "data", "cache", "binance_realtime_feature_status.json"
)
SOURCE_ROW_LIMIT = 3_500
DRIFT_HISTORY_LIMIT = 240
WORKER_VERSION = "binance-realtime-feature-worker.v2"
FULL_CHAIN_AUDIT_INTERVAL_MS = 24 * 60 * 60 * 1_000
CURSOR_FULL_CHAIN_AUDIT_INTERVAL_MS = 24 * 60 * 60 * 1_000
MAX_STREAM_STATUS_AGE_MS = 15 * 60 * 1_000
CALIBRATION_POLICY = DriftCalibrationPolicy(
    baseline_window=60,
    recent_window=20,
    stride=20,
    min_calibration_windows=100,
    warn_quantile=0.95,
    block_quantile=0.99,
)
_LAST_RELEASE_RUN_ATTESTATION: dict[str, Any] | None = None


class BinanceRealtimeFeatureError(RuntimeError):
    """Realtime feature materialization cannot prove complete causal input."""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _feature_input_contract_hash(*, source_row_limit: int) -> str:
    return _sha256(
        {
            "schema_version": 1,
            "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
            "complete_core_required": True,
            "source_row_limit": source_row_limit,
            "full_chain_audit_interval_ms": FULL_CHAIN_AUDIT_INTERVAL_MS,
            "drift_history_limit": DRIFT_HISTORY_LIMIT,
            "universe": [f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS],
            "feature_ids": list(FEATURE_IDS),
            "core_feature_ids": list(CORE_FEATURE_IDS),
            "feature_version": FEATURE_VERSION,
            "materializer_version": MATERIALIZER_VERSION,
            "worker_version": WORKER_VERSION,
            "calibration_policy": {
                "baseline_window": CALIBRATION_POLICY.baseline_window,
                "recent_window": CALIBRATION_POLICY.recent_window,
                "stride": CALIBRATION_POLICY.stride,
                "min_calibration_windows": (
                    CALIBRATION_POLICY.min_calibration_windows
                ),
                "warn_quantile": CALIBRATION_POLICY.warn_quantile,
                "block_quantile": CALIBRATION_POLICY.block_quantile,
            },
        }
    )


def _build_release_run_attestation(
    status: dict[str, Any],
    *,
    input_hashes: dict[str, str],
    output_hashes: dict[str, str],
    output_path: str | Path,
) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT")
    if isinstance(release_commit, str):
        release_commit = release_commit.strip().lower()
    inputs = dict(sorted(input_hashes.items()))
    outputs = dict(sorted(output_hashes.items()))
    outputs["realtime_feature_status_file"] = _sha256_file(output_path)
    safety = status.get("safety")
    safety = safety if isinstance(safety, dict) else {}
    safety_contract = {
        "paper_only": safety.get("paper_only"),
        "live_allowed": safety.get("live_trading"),
        "order_submission_allowed": safety.get("order_submission"),
        "credentials_accessed": safety.get("credentials"),
        "broker_connectivity": safety.get("broker_connectivity"),
    }
    expected_safety = {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }
    source = status.get("source")
    source = source if isinstance(source, dict) else {}
    batch = status.get("batch")
    batch = batch if isinstance(batch, dict) else {}
    complete = bool(
        isinstance(release_commit, str)
        and len(release_commit) == 40
        and all(character in "0123456789abcdef" for character in release_commit)
        and status.get("status") == "PASS"
        and source.get("source_chain_valid") is True
        and batch.get("chain_valid") is True
        and inputs
        and outputs
        and all(_is_sha256(value) for value in inputs.values())
        and all(_is_sha256(value) for value in outputs.values())
        and safety_contract == expected_safety
    )
    return {
        "schema_version": 1,
        "module": "binance_realtime_features",
        "release_commit": release_commit,
        "complete": complete,
        "input_hashes": inputs,
        "output_hashes": outputs,
        "safety": safety_contract,
    }


def release_run_attestation() -> dict[str, Any] | None:
    """Return a detached copy for the immutable L2 launcher."""
    if _LAST_RELEASE_RUN_ATTESTATION is None:
        return None
    return json.loads(
        json.dumps(
            _LAST_RELEASE_RUN_ATTESTATION,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _source_snapshots(
    store: BinanceRealtimeStore,
    *,
    limit: int,
    checkpoint_seq: int,
    checkpoint_row_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_rows, verification = store.verified_recent_rows(
        limit,
        checkpoint_seq=checkpoint_seq,
        checkpoint_row_hash=checkpoint_row_hash,
    )
    rows = [
        row
        for row in source_rows
        if row.get("provider") == "BINANCE_PUBLIC_USDM_WEBSOCKET"
        and isinstance(row.get("quality"), dict)
        and row["quality"].get("complete_core") is True
    ]
    expected = {f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS}
    observed = {str(row.get("instrument_id", "")).upper() for row in rows}
    missing = sorted(expected - observed)
    if missing:
        raise BinanceRealtimeFeatureError(
            f"realtime source is missing instruments: {missing}"
        )
    return rows, verification


def _trusted_source_checkpoint(
    batch_store_path: str,
    *,
    now_ms: int,
) -> dict[str, Any]:
    """Load the source-chain anchor from the verified feature batch journal."""
    try:
        return trusted_source_chain_checkpoint(
            batch_store_path,
            now_ms=now_ms,
            full_chain_audit_interval_ms=CURSOR_FULL_CHAIN_AUDIT_INTERVAL_MS,
        )
    except Exception as exc:
        raise BinanceRealtimeFeatureError("feature batch chain is invalid") from exc


def _trusted_cursor_checkpoint(
    batch_store_path: str,
    *,
    now_ms: int,
) -> dict[str, Any]:
    """Load the cursor-chain anchor from the verified feature batch journal."""
    try:
        return trusted_cursor_chain_checkpoint(
            batch_store_path,
            now_ms=now_ms,
            full_chain_audit_interval_ms=FULL_CHAIN_AUDIT_INTERVAL_MS,
        )
    except Exception as exc:
        raise BinanceRealtimeFeatureError("feature batch chain is invalid") from exc


def _cursor_chain_verification(
    source_store_path: str,
    *,
    checkpoint: dict[str, Any],
    stream_status_path: str,
    now_ms: int | None,
) -> dict[str, Any] | None:
    """Independently verify the cursor suffix before the batch anchors it.

    Snapshot-only fixtures and legacy stores legitimately have no cursor
    journal.  Once the cursor schema exists, however, corruption is a hard
    feature-worker failure rather than an optional diagnostic.
    """
    try:
        store = StreamCursorStore(source_store_path)
    except StreamCursorStoreError as exc:
        if str(exc) == "cursor store schema is missing":
            return None
        raise BinanceRealtimeFeatureError("cursor chain is unreadable") from exc
    validation_now_ms = (
        int(time.time() * 1_000) if now_ms is None else int(now_ms)
    )
    checkpoint_seq = int(checkpoint["checkpoint_seq"])
    checkpoint_row_hash = str(checkpoint["checkpoint_row_hash"])
    basis = "TRUSTED_BATCH_CHECKPOINT_SUFFIX"
    full_verified_at_ms = checkpoint["full_verified_at_ms"]
    attested_frontier_seq: int | None = None
    attested_frontier_hash: str | None = None
    stream_status_sha256: str | None = None
    if checkpoint_seq == 0:
        try:
            status_bytes = Path(stream_status_path).read_bytes()
            status = json.loads(status_bytes.decode("utf-8"))
            if not isinstance(status, dict):
                raise ValueError("stream status must be an object")
            cursor = status.get("cursor") if isinstance(status, dict) else None
            chain = cursor.get("chain") if isinstance(cursor, dict) else None
            bootstrap_mode = (
                chain.get("bootstrap_verification_mode")
                if isinstance(chain, dict)
                else None
            )
            generated_at_ms = status.get("generated_at_ms")
            full_verified_at_ms = chain.get("full_verified_at_ms")
            attested_frontier_seq = chain.get("verified_seq")
            attested_frontier_hash = chain.get("verified_row_hash")
            valid_status = bool(
                status.get("provider") == "BINANCE_PUBLIC_USDM_WEBSOCKET"
                and type(generated_at_ms) is int
                and 0 <= validation_now_ms - generated_at_ms
                < MAX_STREAM_STATUS_AGE_MS
                and isinstance(chain, dict)
                and chain.get("valid") is True
                and bootstrap_mode
                in {
                    "FULL_SUBPROCESS_BOOTSTRAP",
                    "FULL_PERIODIC_SUBPROCESS_AUDIT",
                }
                and type(full_verified_at_ms) is int
                and 0 <= validation_now_ms - full_verified_at_ms
                < CURSOR_FULL_CHAIN_AUDIT_INTERVAL_MS
                and type(attested_frontier_seq) is int
                and attested_frontier_seq >= 0
                and (
                    attested_frontier_hash == ""
                    if attested_frontier_seq == 0
                    else _is_sha256(attested_frontier_hash)
                )
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            valid_status = False
        if not valid_status:
            store.close()
            raise BinanceRealtimeFeatureError(
                "cursor full-audit attestation is unavailable or invalid"
            )
        checkpoint_seq = int(attested_frontier_seq)
        checkpoint_row_hash = str(attested_frontier_hash)
        stream_status_sha256 = hashlib.sha256(status_bytes).hexdigest()
        basis = "STREAM_FULL_AUDIT_ATTESTATION_PLUS_LOCAL_SUFFIX"
    try:
        verification = dict(
            store.verify_chain_from(
                checkpoint_seq=checkpoint_seq,
                checkpoint_row_hash=checkpoint_row_hash,
            )
        )
    finally:
        store.close()
    if verification.get("valid") is not True:
        raise BinanceRealtimeFeatureError("cursor chain is invalid")
    verification["mode"] = checkpoint["verification_mode"]
    verification["basis"] = basis
    verification["full_verified_at_ms"] = full_verified_at_ms
    verification["attested_frontier_seq"] = attested_frontier_seq
    verification["attested_frontier_hash"] = attested_frontier_hash
    verification["stream_status_sha256"] = stream_status_sha256
    return verification


def _projection_watermarks(
    store: SqliteFeatureValueStore,
) -> dict[str, tuple[int, int] | None]:
    """Minimum complete feature cursor per instrument.

    All eight values for a source snapshot are inserted in one transaction.
    Taking the minimum cursor across feature IDs nevertheless makes legacy or
    externally interrupted partial stores self-healing: any lagging series
    forces safe replay through the idempotent insert boundary.
    """
    watermarks: dict[str, tuple[int, int] | None] = {}
    for ticker in FORWARD_MAJOR_TICKERS:
        instrument_id = f"{ticker}USDT"
        cursors = [
            store.latest_cursor(feature_id, FEATURE_VERSION, instrument_id)
            for feature_id in FEATURE_IDS
        ]
        watermarks[instrument_id] = (
            min(cursor for cursor in cursors if cursor is not None)
            if all(cursor is not None for cursor in cursors)
            else None
        )
    return watermarks


def _incremental_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    watermarks: dict[str, tuple[int, int] | None],
) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for snapshot in snapshots:
        instrument_id = str(snapshot.get("instrument_id", "")).upper()
        watermark = watermarks.get(instrument_id)
        cursor = (
            int(snapshot["observed_at_ms"]),
            int(snapshot["event_time_ms"]),
        )
        if watermark is None or cursor > watermark:
            eligible.append(snapshot)
    return eligible


def _feature_values(
    store: SqliteFeatureValueStore,
    *,
    as_of_ms: int,
    limit_per_series: int = DRIFT_HISTORY_LIMIT,
) -> dict[str, list[float]]:
    values_by_series: dict[str, list[float]] = {}
    instruments = tuple(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS)
    for instrument_id in instruments:
        # Capacity is admitted by its own 30-day conservative-depth gate.  It
        # starts accumulating additively and must not invalidate the mature
        # seven-feature drift calibration while its history is still short.
        for feature_id in CORE_FEATURE_IDS:
            series_id = f"{feature_id}@{instrument_id}"
            rows = store.tail_as_of(
                feature_id,
                FEATURE_VERSION,
                instrument_id,
                as_of_ms,
                limit=limit_per_series,
            )
            values_by_series[series_id] = [
                float(row.value)
                for row in rows
                if row.value is not None
            ]
    return values_by_series


def _drift_snapshot(
    values_by_series: dict[str, list[float]],
    *,
    calibration: DriftCalibrationResult,
) -> dict[str, Any]:
    if calibration.status != CALIBRATION_READY:
        raise BinanceRealtimeFeatureError(
            "realtime feature drift calibration is not ready: "
            + ",".join(calibration.reasons)
        )
    expected = tuple(sorted(values_by_series))
    aggregate = evaluate_feature_distribution_drift(
        values_by_series,
        expected_features=expected,
        policy=calibration.drift_policy(),
    )
    result = aggregate.as_dict()
    assessments = result["assessments"]
    assert isinstance(assessments, dict)
    counts = Counter(
        str(item.get("status", "BLOCK"))
        for item in assessments.values()
        if isinstance(item, dict)
    )
    structurally_ready = bool(assessments) and all(
        isinstance(item, dict)
        and item.get("ks_distance") is not None
        and item.get("sample_counts") == [
            calibration.policy.baseline_window,
            calibration.policy.recent_window,
        ]
        for item in assessments.values()
    )
    return {
        **result,
        "series_count": len(assessments),
        "status_counts": {
            "PASS": counts["PASS"],
            "WARN": counts["WARN"],
            "BLOCK": counts["BLOCK"],
        },
        "structurally_ready": structurally_ready,
        "policy": {
            "baseline_window": calibration.policy.baseline_window,
            "recent_window": calibration.policy.recent_window,
            "warn_ks": calibration.warn_ks,
            "block_ks": calibration.block_ks,
            "calibration": calibration.calibration,
            "calibration_id": calibration.calibration_id,
            "provenance": calibration.provenance,
            "window_count": calibration.window_count,
            "series_count": calibration.series_count,
            "held_out_observations_per_series": (
                calibration.held_out_observations_per_series
            ),
            "source_digest": calibration.source_digest,
            "warn_quantile": calibration.policy.warn_quantile,
            "block_quantile": calibration.policy.block_quantile,
        },
        "calibration": calibration.as_dict(),
    }


def execute(
    *,
    now_ms: int | None = None,
    source_store_path: str = DEFAULT_SOURCE_STORE,
    stream_status_path: str = DEFAULT_STREAM_STATUS,
    feature_store_path: str = DEFAULT_FEATURE_STORE,
    batch_store_path: str = DEFAULT_BATCH_STORE,
    status_path: str = DEFAULT_STATUS,
    source_row_limit: int = SOURCE_ROW_LIMIT,
) -> dict[str, Any]:
    global _LAST_RELEASE_RUN_ATTESTATION
    _LAST_RELEASE_RUN_ATTESTATION = None
    started_ns = time.perf_counter_ns()
    stage_started_ns = started_ns
    stage_elapsed_ms: dict[str, int] = {}

    def finish_stage(name: str) -> None:
        nonlocal stage_started_ns
        finished_ns = time.perf_counter_ns()
        stage_elapsed_ms[name] = max(
            0,
            (finished_ns - stage_started_ns) // 1_000_000,
        )
        stage_started_ns = finished_ns

    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    trusted_checkpoint = _trusted_source_checkpoint(
        batch_store_path,
        now_ms=now,
    )
    trusted_cursor_checkpoint = _trusted_cursor_checkpoint(
        batch_store_path,
        now_ms=now,
    )
    finish_stage("trusted_source_checkpoint")
    input_hashes = {
        "feature_batch_authority_before": trusted_checkpoint["batch_authority"],
        "feature_materialization_contract": _feature_input_contract_hash(
            source_row_limit=source_row_limit
        ),
    }
    output_hashes: dict[str, str] = {}
    source_store = BinanceRealtimeStore(source_store_path, create=False)
    try:
        snapshots, chain_verification = _source_snapshots(
            source_store,
            limit=source_row_limit,
            checkpoint_seq=trusted_checkpoint["checkpoint_seq"],
            checkpoint_row_hash=trusted_checkpoint["checkpoint_row_hash"],
        )
    finally:
        source_store.close()
    finish_stage("source_snapshot_load")
    cursor_chain_verification = _cursor_chain_verification(
        source_store_path,
        checkpoint=trusted_cursor_checkpoint,
        stream_status_path=stream_status_path,
        now_ms=(None if now_ms is None else now),
    )
    cursor_chain_checkpoint = (
        None
        if cursor_chain_verification is None
        or int(cursor_chain_verification["verified_seq"]) <= 0
        else {
            "schema_version": 1,
            "verified_seq": int(cursor_chain_verification["verified_seq"]),
            "verified_row_hash": str(
                cursor_chain_verification["verified_row_hash"]
            ),
            "full_verified_at_ms": (
                cursor_chain_verification["full_verified_at_ms"]
            ),
        }
    )
    finish_stage("cursor_chain_verification")
    source_digest = _sha256(snapshots)
    input_hashes["public_source_snapshot_bundle"] = source_digest
    input_hashes["public_source_chain_view"] = _sha256(
        {
            "checkpoint": {
                key: trusted_checkpoint[key]
                for key in (
                    "checkpoint_seq",
                    "checkpoint_row_hash",
                    "full_verified_at_ms",
                    "verification_mode",
                )
            },
            "verification": chain_verification,
        }
    )
    input_hashes["public_cursor_chain_view"] = _sha256(
        {
            "checkpoint": {
                key: trusted_cursor_checkpoint[key]
                for key in (
                    "checkpoint_seq",
                    "checkpoint_row_hash",
                    "full_verified_at_ms",
                    "verification_mode",
                )
            },
            "verification": cursor_chain_verification,
        }
    )
    dataset_version = f"binance-realtime:{source_digest}"
    source_observed_at_ms = max(
        int(snapshot["observed_at_ms"]) for snapshot in snapshots
    )
    finish_stage("source_contract_hashing")

    Path(feature_store_path).parent.mkdir(parents=True, exist_ok=True)
    feature_store = SqliteFeatureValueStore(feature_store_path, create=True)
    try:
        watermarks_before = _projection_watermarks(feature_store)
        eligible_snapshots = _incremental_snapshots(
            snapshots,
            watermarks=watermarks_before,
        )
        projected = [
            feature
            for snapshot in eligible_snapshots
            for feature in project_snapshot(snapshot)
        ]
        finish_stage("feature_projection")
        inserted = feature_store.add_many(
            projected,
            version=FEATURE_VERSION,
            computed_at_ms=now,
            dataset_version=dataset_version,
            materializer_version=MATERIALIZER_VERSION,
            quality_verdict="SOURCE_CHAIN_VERIFIED_COMPLETE_CORE",
        )
        row_count = feature_store.row_count()
        finish_stage("feature_store_append")
        values_by_series = _feature_values(feature_store, as_of_ms=now)
        finish_stage("drift_history_read")
        input_hashes["drift_feature_view"] = _sha256(
            {"as_of_ms": now, "values_by_series": values_by_series}
        )
        calibration = calibrate_distribution_drift(
            values_by_series,
            policy=CALIBRATION_POLICY,
        )
        drift = _drift_snapshot(
            values_by_series,
            calibration=calibration,
        )
        drift["history"] = {
            "policy": "BOUNDED_TRAILING_PIT_V1",
            "max_observations_per_series": DRIFT_HISTORY_LIMIT,
            "loaded_observation_count": sum(
                len(values) for values in values_by_series.values()
            ),
            "series_count": len(values_by_series),
            "read_scope": "TRAILING_ONLY",
            "archive_retention_unchanged": True,
        }
        finish_stage("drift_calibration_and_assessment")
        watermarks_after = _projection_watermarks(feature_store)
        output_hashes["feature_projection_frontier"] = _sha256(
            {
                "schema_version": 1,
                "feature_version": FEATURE_VERSION,
                "materializer_version": MATERIALIZER_VERSION,
                "retained_value_count": row_count,
                "watermarks": {
                    instrument_id: (
                        None
                        if cursor is None
                        else {
                            "knowledge_time_ms": cursor[0],
                            "event_time_ms": cursor[1],
                        }
                    )
                    for instrument_id, cursor in sorted(watermarks_after.items())
                },
            }
        )
        finish_stage("projection_frontier")
    finally:
        feature_store.close()
    if drift["structurally_ready"] is not True:
        raise BinanceRealtimeFeatureError(
            "realtime feature drift window is structurally incomplete"
        )

    batch_payload = {
        "schema_version": 3,
        "identity_scheme": "CONTENT_ADDRESSED_PAYLOAD_V1",
        "observed_at_ms": source_observed_at_ms,
        "source": {
            "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
            "snapshot_count": len(snapshots),
            "snapshot_digest": source_digest,
            "first_observed_at_ms": min(
                int(snapshot["observed_at_ms"]) for snapshot in snapshots
            ),
            "last_observed_at_ms": source_observed_at_ms,
            "source_chain_valid": True,
            "chain_checkpoint": {
                "schema_version": 1,
                "verified_seq": chain_verification["verified_seq"],
                "verified_row_hash": chain_verification["verified_row_hash"],
                "full_verified_at_ms": (
                    now
                    if trusted_checkpoint["checkpoint_seq"] == 0
                    else trusted_checkpoint["full_verified_at_ms"]
                ),
            },
            "cursor_chain_checkpoint": cursor_chain_checkpoint,
        },
        "projection": {
            "worker_version": WORKER_VERSION,
            "feature_version": FEATURE_VERSION,
            "materializer_version": MATERIALIZER_VERSION,
            "feature_ids": list(FEATURE_IDS),
            "instrument_count": len(FORWARD_MAJOR_TICKERS),
            # Source-bound potential count stays stable across crash recovery
            # and idempotent reruns of the same source digest.  Operational
            # incremental counts belong in the replaceable status artifact.
            "projected_value_count": len(snapshots) * len(FEATURE_IDS),
            "projected_value_count_semantics": (
                "SOURCE_POTENTIAL_STABLE_IDENTITY"
            ),
        },
        "drift_sha256": _sha256(drift),
        "calibration": {
            "calibration_id": calibration.calibration_id,
            "source_digest": calibration.source_digest,
            "warn_ks": calibration.warn_ks,
            "block_ks": calibration.block_ks,
            "window_count": calibration.window_count,
            "provenance": calibration.provenance,
            "history_limit_per_series": DRIFT_HISTORY_LIMIT,
        },
        "safety": {
            "diagnostic_only": True,
            "load_bearing": False,
            "private_access": False,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }
    batch_payload["batch_id"] = _sha256(
        {
            "kind": "BINANCE_REALTIME_FEATURE_BATCH_V3",
            "payload_without_batch_id": batch_payload,
        }
    )
    batch_store = RealtimeFeatureBatchStore(batch_store_path, create=True)
    try:
        new_batch = batch_store.append(batch_payload)
        batch_count = batch_store.row_count()
        output_hashes["feature_batch_authority_after"] = (
            batch_store.authority_hash()
        )
        batch_chain_valid = True
    finally:
        batch_store.close()
    finish_stage("batch_journal")
    if not batch_chain_valid:
        raise BinanceRealtimeFeatureError("feature batch chain is invalid")

    completed_at_ms = (
        int(time.time() * 1_000) if now_ms is None else int(now_ms)
    )
    if completed_at_ms < now:
        raise BinanceRealtimeFeatureError(
            "realtime feature completion clock precedes start clock"
        )

    status = {
        "schema_version": 2,
        "generated_at_ms": completed_at_ms,
        "status": "PASS",
        "mode": "L2_REALTIME_FEATURE_DIAGNOSTIC",
        "source": {
            **batch_payload["source"],
            "chain_verification": {
                "mode": trusted_checkpoint["verification_mode"],
                "anchor_seq": chain_verification["anchor_seq"],
                "verified_suffix_rows": chain_verification[
                    "verified_suffix_rows"
                ],
            },
            "cursor_chain_verification": {
                "mode": (
                    "NOT_AVAILABLE_NO_CURSOR_JOURNAL"
                    if cursor_chain_verification is None
                    else cursor_chain_verification["mode"]
                ),
                "anchor_seq": (
                    None
                    if cursor_chain_verification is None
                    else cursor_chain_verification["anchor_seq"]
                ),
                "verified_suffix_events": (
                    None
                    if cursor_chain_verification is None
                    else cursor_chain_verification[
                        "verified_suffix_events"
                    ]
                ),
                "basis": (
                    None
                    if cursor_chain_verification is None
                    else cursor_chain_verification["basis"]
                ),
            },
        },
        "features": {
            **batch_payload["projection"],
            "incremental_projected_value_count": len(projected),
            "new_value_count": inserted,
            "retained_value_count": row_count,
            "eligible_source_snapshot_count": len(eligible_snapshots),
            "skipped_existing_snapshot_count": (
                len(snapshots) - len(eligible_snapshots)
            ),
            "watermark_policy": "MIN_COMPLETE_FEATURE_CURSOR_V1",
            "watermarks_before": {
                instrument_id: (
                    None
                    if cursor is None
                    else {
                        "knowledge_time_ms": cursor[0],
                        "event_time_ms": cursor[1],
                    }
                )
                for instrument_id, cursor in sorted(watermarks_before.items())
            },
        },
        "drift": drift,
        "batch": {
            "batch_id": batch_payload["batch_id"],
            "new_batch": new_batch,
            "batch_count": batch_count,
            "chain_valid": batch_chain_valid,
        },
        "agent_input": {
            "discoverable": True,
            "consumption_mode": "READ_ONLY_DIAGNOSTIC_CONTEXT",
            "automatic_action": False,
            "promotion_authority": False,
        },
        "runtime": {
            "started_at_ms": now,
            "completed_at_ms": completed_at_ms,
            "stage_elapsed_ms": stage_elapsed_ms,
            "elapsed_ms": max(
                0,
                (time.perf_counter_ns() - started_ns) // 1_000_000,
            ),
            "incremental_projection": True,
            "bounded_drift_read": True,
        },
        "safety": {
            "paper_only": True,
            **batch_payload["safety"],
        },
    }
    atomic_write_json(status_path, status)
    _LAST_RELEASE_RUN_ATTESTATION = _build_release_run_attestation(
        status,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        output_path=status_path,
    )
    return status


def main() -> int:
    try:
        status = execute()
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "generated_at_ms": int(time.time() * 1_000),
            "status": "BLOCKED",
            "mode": "L2_REALTIME_FEATURE_DIAGNOSTIC",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "safety": {
                "paper_only": True,
                "diagnostic_only": True,
                "load_bearing": False,
                "private_access": False,
                "credentials": False,
                "broker_connectivity": False,
                "order_submission": False,
                "live_trading": False,
            },
        }
        atomic_write_json(DEFAULT_STATUS, failure)
        print(_canonical(failure))
        return SCHEDULE_EXIT_FAILURE
    print(
        _canonical(
            {
                "status": status["status"],
                "generated_at_ms": status["generated_at_ms"],
                "source_snapshot_count": status["source"]["snapshot_count"],
                "new_value_count": status["features"]["new_value_count"],
                "retained_value_count": status["features"]["retained_value_count"],
                "drift_status": status["drift"]["status"],
                "drift_status_counts": status["drift"]["status_counts"],
                "batch": status["batch"],
                "safety": status["safety"],
            }
        )
    )
    return SCHEDULE_EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
