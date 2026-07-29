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
from services.data.distribution_drift import (
    DistributionDriftPolicy,
    evaluate_feature_distribution_drift,
)
from services.features.binance_realtime_projection import (
    FEATURE_IDS,
    FEATURE_VERSION,
    MATERIALIZER_VERSION,
    project_snapshot,
)
from services.features.realtime_feature_batch_store import (
    RealtimeFeatureBatchStore,
)
from services.features.value_store import SqliteFeatureValueStore
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


DEFAULT_SOURCE_STORE = os.path.join(
    "data", "cache", "binance_realtime_stream.db"
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
DRIFT_POLICY = DistributionDriftPolicy(
    baseline_window=60,
    recent_window=20,
    min_observations=20,
    warn_ks=0.20,
    block_ks=0.35,
)


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


def _source_snapshots(
    store: BinanceRealtimeStore,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if not store.chain_valid():
        raise BinanceRealtimeFeatureError("Binance realtime source chain is invalid")
    rows = [
        row
        for row in store.recent_rows(limit)
        if row.get("provider") == "BINANCE_PUBLIC_USDM_WEBSOCKET"
    ]
    expected = {f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS}
    observed = {str(row.get("instrument_id", "")).upper() for row in rows}
    missing = sorted(expected - observed)
    if missing:
        raise BinanceRealtimeFeatureError(
            f"realtime source is missing instruments: {missing}"
        )
    return rows


def _drift_snapshot(
    store: SqliteFeatureValueStore,
    *,
    as_of_ms: int,
) -> dict[str, Any]:
    values_by_series: dict[str, list[float]] = {}
    instruments = tuple(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS)
    for instrument_id in instruments:
        for feature_id in FEATURE_IDS:
            series_id = f"{feature_id}@{instrument_id}"
            rows = store.as_of(
                feature_id,
                FEATURE_VERSION,
                instrument_id,
                as_of_ms,
            )
            values_by_series[series_id] = [
                float(row.value)
                for row in rows
                if row.value is not None
            ]
    expected = tuple(sorted(values_by_series))
    aggregate = evaluate_feature_distribution_drift(
        values_by_series,
        expected_features=expected,
        policy=DRIFT_POLICY,
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
            DRIFT_POLICY.baseline_window,
            DRIFT_POLICY.recent_window,
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
            "baseline_window": DRIFT_POLICY.baseline_window,
            "recent_window": DRIFT_POLICY.recent_window,
            "warn_ks": DRIFT_POLICY.warn_ks,
            "block_ks": DRIFT_POLICY.block_ks,
            "calibration": "UNCALIBRATED_PRINCIPLED",
        },
    }


def execute(
    *,
    now_ms: int | None = None,
    source_store_path: str = DEFAULT_SOURCE_STORE,
    feature_store_path: str = DEFAULT_FEATURE_STORE,
    batch_store_path: str = DEFAULT_BATCH_STORE,
    status_path: str = DEFAULT_STATUS,
    source_row_limit: int = SOURCE_ROW_LIMIT,
) -> dict[str, Any]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    source_store = BinanceRealtimeStore(source_store_path, create=False)
    try:
        snapshots = _source_snapshots(source_store, limit=source_row_limit)
    finally:
        source_store.close()
    projected = [
        feature
        for snapshot in snapshots
        for feature in project_snapshot(snapshot)
    ]
    if not projected:
        raise BinanceRealtimeFeatureError("no realtime features were projected")
    source_digest = _sha256(snapshots)
    dataset_version = f"binance-realtime:{source_digest}"
    source_observed_at_ms = max(
        int(snapshot["observed_at_ms"]) for snapshot in snapshots
    )

    Path(feature_store_path).parent.mkdir(parents=True, exist_ok=True)
    feature_store = SqliteFeatureValueStore(feature_store_path, create=True)
    try:
        inserted = feature_store.add_many(
            projected,
            version=FEATURE_VERSION,
            computed_at_ms=now,
            dataset_version=dataset_version,
            materializer_version=MATERIALIZER_VERSION,
            quality_verdict="SOURCE_CHAIN_VERIFIED_COMPLETE_CORE",
        )
        row_count = feature_store.row_count()
        drift = _drift_snapshot(feature_store, as_of_ms=now)
    finally:
        feature_store.close()
    if drift["structurally_ready"] is not True:
        raise BinanceRealtimeFeatureError(
            "realtime feature drift window is structurally incomplete"
        )

    batch_payload = {
        "schema_version": 1,
        "batch_id": _sha256(
            {
                "source_digest": source_digest,
                "feature_version": FEATURE_VERSION,
                "materializer_version": MATERIALIZER_VERSION,
            }
        ),
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
        },
        "projection": {
            "feature_version": FEATURE_VERSION,
            "materializer_version": MATERIALIZER_VERSION,
            "feature_ids": list(FEATURE_IDS),
            "instrument_count": len(FORWARD_MAJOR_TICKERS),
            "projected_value_count": len(projected),
        },
        "drift_sha256": _sha256(drift),
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
    batch_store = RealtimeFeatureBatchStore(batch_store_path, create=True)
    try:
        new_batch = batch_store.append(batch_payload)
        batch_count = batch_store.row_count()
        batch_chain_valid = batch_store.chain_valid()
    finally:
        batch_store.close()
    if not batch_chain_valid:
        raise BinanceRealtimeFeatureError("feature batch chain is invalid")

    status = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": "PASS",
        "mode": "L2_REALTIME_FEATURE_DIAGNOSTIC",
        "source": batch_payload["source"],
        "features": {
            **batch_payload["projection"],
            "new_value_count": inserted,
            "retained_value_count": row_count,
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
        "safety": batch_payload["safety"],
    }
    atomic_write_json(status_path, status)
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
