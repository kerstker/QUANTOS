"""Read-only data-catalog HTTP route."""

from __future__ import annotations

import json
import os
import statistics
import time
from typing import Dict, List

from fastapi import APIRouter

from services.console.api.common import short_hash as _short_hash
from services.console.api.data_port import data_catalog_runtime
from services.data.binance_api_coverage import (
    BinanceApiCoverageError,
    assess_coverage,
)

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
)
_DATA_CHANNEL_INVENTORY = os.path.join(
    _PROJECT_ROOT, "data", "cache", "data_channel_inventory.json"
)
_BINANCE_STREAM_STATUS = "binance_realtime_stream_status.json"
_BINANCE_REST_STATUS = "binance_realtime_market_status.json"
_BINANCE_SYSTEMIC_STATUS = "binance_systemic_risk_status.json"
_BINANCE_RPI_STATUS = "binance_rpi_depth_status.json"
_BINANCE_FEATURE_STATUS = "binance_realtime_feature_status.json"
_BINANCE_DRIFT_ALERT_STATUS = "binance_realtime_drift_alert_status.json"
_BINANCE_API_COVERAGE_CONTRACT = os.path.join(
    _PROJECT_ROOT, "config", "binance_usdm_market_data_coverage.json"
)


def _status_candidates(filename: str) -> list[str]:
    candidates: list[str] = []
    state_root = os.getenv("QUANTOS_STATE_ROOT", "").strip()
    if state_root:
        candidates.append(os.path.join(os.path.abspath(state_root), "data", "cache", filename))
    candidates.append(os.path.join(_PROJECT_ROOT, "data", "cache", filename))
    return list(dict.fromkeys(candidates))


def _read_latest_status(filename: str) -> tuple[Dict | None, str | None]:
    valid: list[tuple[int, Dict, str]] = []
    for path in _status_candidates(filename):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        generated_at_ms = payload.get("generated_at_ms")
        if isinstance(generated_at_ms, int):
            valid.append((generated_at_ms, payload, path))
    if not valid:
        return None, None
    _, payload, path = max(valid, key=lambda item: item[0])
    return payload, path


def _binance_api_coverage_payload() -> Dict:
    try:
        with open(_BINANCE_API_COVERAGE_CONTRACT, encoding="utf-8") as handle:
            contract = json.load(handle)
        if not isinstance(contract, dict):
            raise BinanceApiCoverageError("coverage contract is not an object")
        report = assess_coverage(contract)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        BinanceApiCoverageError,
    ) as exc:
        return {
            "status": "INVALID",
            "catalog_endpoint_count": 0,
            "accounted_endpoint_count": 0,
            "accounted_ratio": 0.0,
            "load_bearing_endpoint_count": 0,
            "gap_count": 0,
            "gaps": [],
            "error_type": type(exc).__name__,
        }
    report["status"] = "VALIDATED_OFFICIAL_CATALOG"
    return report


def _binance_realtime_payload() -> Dict:
    now_ms = int(time.time() * 1_000)
    stream, stream_path = _read_latest_status(_BINANCE_STREAM_STATUS)
    rest, rest_path = _read_latest_status(_BINANCE_REST_STATUS)
    systemic, systemic_path = _read_latest_status(_BINANCE_SYSTEMIC_STATUS)
    rpi, rpi_path = _read_latest_status(_BINANCE_RPI_STATUS)
    features, feature_path = _read_latest_status(_BINANCE_FEATURE_STATUS)
    drift_alerts, drift_alert_path = _read_latest_status(
        _BINANCE_DRIFT_ALERT_STATUS
    )

    def age(payload: Dict | None) -> int | None:
        generated = payload.get("generated_at_ms") if payload else None
        return max(0, now_ms - generated) if isinstance(generated, int) else None

    stream_age = age(stream)
    rest_age = age(rest)
    systemic_age = age(systemic)
    rpi_age = age(rpi)
    feature_age = age(features)
    drift_alert_age = age(drift_alerts)
    stream_fresh = stream_age is not None and stream_age <= 30_000
    rest_fresh = rest_age is not None and rest_age <= 10 * 60_000
    systemic_fresh = (
        systemic_age is not None
        and systemic_age <= 2 * 60 * 60_000
        and bool(systemic and systemic.get("complete"))
    )
    rpi_fresh = (
        rpi_age is not None
        and rpi_age <= 2 * 60 * 60_000
        and bool(rpi and rpi.get("complete"))
    )
    feature_fresh = feature_age is not None and feature_age <= 10 * 60_000
    drift_alert_fresh = (
        drift_alert_age is not None and drift_alert_age <= 10 * 60_000
    )
    stream_coverage = (stream or {}).get("coverage", {})
    rest_coverage = (rest or {}).get("coverage", {})
    effective_streaming = bool(
        stream_fresh
        and stream
        and stream.get("streaming")
        and stream.get("verdict") == "PASS"
    )
    verdict = (
        "PASS"
        if effective_streaming and rest_fresh
        else "STREAM_PASS"
        if effective_streaming
        else "STALE"
        if stream or rest
        else "UNAVAILABLE"
    )
    return {
        "verdict": verdict,
        "checked_at_ms": now_ms,
        "stream": {
            "status_present": stream is not None,
            "effective_streaming": effective_streaming,
            "reported_streaming": bool(stream and stream.get("streaming")),
            "fresh": stream_fresh,
            "age_ms": stream_age,
            "lag_ms": (stream or {}).get("lag_ms"),
            "coverage": stream_coverage,
            "connections": (stream or {}).get("connections", {}),
            "reconnects": (stream or {}).get("reconnects", {}),
            "event_counts": (stream or {}).get("event_counts", {}),
            "rejected_frames": (stream or {}).get("rejected_frames"),
            "chain_valid": (stream or {}).get("store", {}).get("chain_valid"),
            "routes": (stream or {}).get("routes", {}),
            "source_path": stream_path,
        },
        "rest_snapshot": {
            "status_present": rest is not None,
            "fresh": rest_fresh,
            "age_ms": rest_age,
            "verdict": (rest or {}).get("verdict"),
            "coverage": rest_coverage,
            "chain_valid": (rest or {}).get("store", {}).get("chain_valid"),
            "errors": len((rest or {}).get("errors", {})),
            "fields": (rest or {}).get("fields", []),
            "source_path": rest_path,
        },
        "systemic_risk": {
            "status_present": systemic is not None,
            "fresh": systemic_fresh,
            "age_ms": systemic_age,
            "verdict": (systemic or {}).get("verdict"),
            "coverage": (systemic or {}).get("coverage", {}),
            "chain_valid": (systemic or {}).get("store", {}).get(
                "chain_valid"
            ),
            "row_count": (systemic or {}).get("store", {}).get(
                "row_count", 0
            ),
            "row_counts": (systemic or {}).get("store", {}).get(
                "row_counts", {}
            ),
            "inserted": (systemic or {}).get("store", {}).get(
                "inserted", {}
            ),
            "latest": (systemic or {}).get("latest", {}),
            "errors": len((systemic or {}).get("errors", {})),
            "fields": (systemic or {}).get("fields", []),
            "source_path": systemic_path,
        },
        "rpi_depth": {
            "status_present": rpi is not None,
            "fresh": rpi_fresh,
            "age_ms": rpi_age,
            "verdict": (rpi or {}).get("verdict"),
            "coverage": (rpi or {}).get("coverage", {}),
            "chain_valid": (rpi or {}).get("store", {}).get("chain_valid"),
            "row_count": (rpi or {}).get("store", {}).get("row_count", 0),
            "inserted": (rpi or {}).get("store", {}).get("inserted", 0),
            "latest": (rpi or {}).get("latest", {}),
            "errors": len((rpi or {}).get("errors", {})),
            "request_policy": (rpi or {}).get("request_policy", {}),
            "fields": (rpi or {}).get("fields", []),
            "source_path": rpi_path,
        },
        "feature_projection": {
            "status_present": features is not None,
            "fresh": feature_fresh,
            "age_ms": feature_age,
            "status": (features or {}).get("status"),
            "feature_version": (features or {}).get("features", {}).get(
                "feature_version"
            ),
            "feature_count": len(
                (features or {}).get("features", {}).get("feature_ids", [])
            ),
            "instrument_count": (features or {}).get("features", {}).get(
                "instrument_count", 0
            ),
            "retained_value_count": (features or {}).get("features", {}).get(
                "retained_value_count", 0
            ),
            "drift_status": (features or {}).get("drift", {}).get("status"),
            "drift_status_counts": (features or {}).get("drift", {}).get(
                "status_counts", {}
            ),
            "drift_series_count": (features or {}).get("drift", {}).get(
                "series_count", 0
            ),
            "batch_chain_valid": (features or {}).get("batch", {}).get(
                "chain_valid"
            ),
            "calibration": (features or {}).get("drift", {}).get(
                "policy", {}
            ).get("calibration"),
            "calibration_id": (features or {}).get("drift", {}).get(
                "policy", {}
            ).get("calibration_id"),
            "warn_ks": (features or {}).get("drift", {}).get(
                "policy", {}
            ).get("warn_ks"),
            "block_ks": (features or {}).get("drift", {}).get(
                "policy", {}
            ).get("block_ks"),
            "calibration_window_count": (features or {}).get(
                "drift", {}
            ).get("policy", {}).get("window_count", 0),
            "load_bearing": bool(
                (features or {}).get("safety", {}).get("load_bearing", False)
            ),
            "source_path": feature_path,
        },
        "drift_alerting": {
            "status_present": drift_alerts is not None,
            "fresh": drift_alert_fresh,
            "age_ms": drift_alert_age,
            "status": (drift_alerts or {}).get("status"),
            "current_severity": (drift_alerts or {}).get(
                "current", {}
            ).get("status"),
            "reason": (drift_alerts or {}).get("current", {}).get(
                "reason"
            ),
            "transition": (drift_alerts or {}).get("incident", {}).get(
                "transition"
            ),
            "deduplicated": bool(
                (drift_alerts or {}).get("incident", {}).get(
                    "deduplicated", False
                )
            ),
            "local_delivery": (drift_alerts or {}).get(
                "delivery", {}
            ).get("local_status"),
            "external_delivery": (drift_alerts or {}).get(
                "delivery", {}
            ).get("external_status"),
            "independent_worker": bool(
                (drift_alerts or {}).get("delivery", {}).get(
                    "independent_worker", False
                )
            ),
            "event_count": (drift_alerts or {}).get("journal", {}).get(
                "event_count", 0
            ),
            "chain_valid": (drift_alerts or {}).get(
                "journal", {}
            ).get("chain_valid"),
            "source_path": drift_alert_path,
        },
        "api_coverage": _binance_api_coverage_payload(),
        "universe_size": 14,
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "private_stream": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }


def _data_channel_payload() -> Dict:
    """读取只读渠道审计产物；缺失时显式 UNAVAILABLE，不生成占位渠道。"""
    if not os.path.exists(_DATA_CHANNEL_INVENTORY):
        return {"status": "UNAVAILABLE", "channels": [], "summary": {}}
    try:
        with open(_DATA_CHANNEL_INVENTORY, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "INVALID", "channels": [], "summary": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("channels"), list):
        return {"status": "INVALID", "channels": [], "summary": {}}
    return {
        "status": "REAL_COVERAGE_INVENTORY",
        "generated_at_ms": payload.get("generated_at_ms"),
        "channels": payload["channels"],
        "summary": payload.get("summary", {}),
        "honesty": payload.get("honesty", []),
    }


def _enumerate_real_datasets() -> List[Dict]:
    return data_catalog_runtime.require().enumerate_real_datasets()


def _dataset_checks(dataset: Dict) -> List[Dict]:
    return [
        {
            "check_id": f"{dataset['dataset_id']}-coverage",
            "name": "coverage",
            "status": "PASS" if dataset["coverage"] >= 0.94 else "WARN",
            "metric": dataset["coverage"],
            "threshold": 0.94,
        },
        {
            "check_id": f"{dataset['dataset_id']}-freshness",
            "name": "freshness_hours",
            "status": "PASS" if dataset["freshness_hours"] <= 24 else "WARN",
            "metric": dataset["freshness_hours"],
            "threshold": 24,
        },
        {
            "check_id": f"{dataset['dataset_id']}-pit",
            "name": "point_in_time_safe",
            "status": "PASS" if dataset["point_in_time_safe"] else "FAIL",
            "metric": 1 if dataset["point_in_time_safe"] else 0,
            "threshold": 1,
        },
    ]


_SYNTHETIC_CATALOG_DATASETS = [
    {
        "dataset_id": "ds-market-daily-bars",
        "name": "CN A-share daily bars",
        "domain": "MARKET_DATA",
        "version": "2026.06.12.001",
        "snapshot_id": "md-snapshot-2026w20",
        "row_count": 18_420_000,
        "coverage": 0.992,
        "freshness_hours": 3.2,
        "quality_score": 0.981,
        "point_in_time_safe": True,
        "owner": "Market Data",
        "storage_uri": "artifact://data-lake/market/daily-bars/2026w20",
    },
    {
        "dataset_id": "ds-corporate-actions",
        "name": "Corporate actions adjusted events",
        "domain": "REFERENCE_DATA",
        "version": "2026.06.12.001",
        "snapshot_id": "ca-snapshot-2026w20",
        "row_count": 214_800,
        "coverage": 0.987,
        "freshness_hours": 8.5,
        "quality_score": 0.963,
        "point_in_time_safe": True,
        "owner": "Reference Data",
        "storage_uri": "artifact://data-lake/reference/corporate-actions/2026w20",
    },
    {
        "dataset_id": "ds-index-membership",
        "name": "Point-in-time index membership",
        "domain": "REFERENCE_DATA",
        "version": "2026.06.12.001",
        "snapshot_id": "idx-snapshot-2026w20",
        "row_count": 1_322_600,
        "coverage": 0.978,
        "freshness_hours": 12.0,
        "quality_score": 0.956,
        "point_in_time_safe": True,
        "owner": "Reference Data",
        "storage_uri": "artifact://data-lake/reference/index-membership/2026w20",
    },
    {
        "dataset_id": "ds-fundamentals-ttm",
        "name": "Fundamentals TTM normalized",
        "domain": "FUNDAMENTAL_DATA",
        "version": "2026.06.11.002",
        "snapshot_id": "fund-snapshot-2026w20",
        "row_count": 4_860_000,
        "coverage": 0.941,
        "freshness_hours": 22.4,
        "quality_score": 0.927,
        "point_in_time_safe": True,
        "owner": "Fundamental Data",
        "storage_uri": "artifact://data-lake/fundamental/ttm/2026w20",
    },
    {
        "dataset_id": "ds-liquidity-profile",
        "name": "Liquidity and capacity profile",
        "domain": "LIQUIDITY_DATA",
        "version": "2026.06.12.001",
        "snapshot_id": "liq-snapshot-2026w20",
        "row_count": 5_520_000,
        "coverage": 0.969,
        "freshness_hours": 4.1,
        "quality_score": 0.948,
        "point_in_time_safe": True,
        "owner": "Trading Research",
        "storage_uri": "artifact://data-lake/liquidity/profile/2026w20",
    },
]


def _unavailable_data_catalog_payload(reason: str) -> Dict:
    """Return an explicit unavailable state when the composition port is absent.

    Returning the deterministic catalogue here would make a wiring or storage
    outage look healthy to the operator.  The reason is intentionally reduced
    to a stable class label rather than exposing filesystem/driver details.
    """
    return {
        "catalog_mode": "READ_ONLY_DATA_CATALOG",
        "feature_store_mode": "OFFLINE_POINT_IN_TIME_RESEARCH",
        "data_backend": "UNAVAILABLE",
        "data_provenance": "UNAVAILABLE",
        "datasets_real": False,
        "features_synthetic": False,
        "snapshot_present": False,
        "snapshot_id": None,
        "snapshot_ids": [],
        "snapshot_status": "UNAVAILABLE",
        "data_quality": {
            "status": "UNAVAILABLE",
            "provenance": "UNAVAILABLE",
            "snapshot_present": False,
            "snapshot_ids": [],
            "reason": reason,
        },
        "unavailable_reason": reason,
        "summary": {
            "datasets": 0,
            "features": 0,
            "quality_checks": 0,
            "quality_passed": 0,
            "point_in_time_safe_features": 0,
            "lineage_edges": 0,
            "avg_dataset_quality": 0.0,
            "avg_feature_quality": 0.0,
        },
        "datasets": [],
        "features": [],
        "lineage_edges": [],
        "data_channels": _data_channel_payload(),
        "binance_realtime": _binance_realtime_payload(),
        "quality_policy": {
            "requires_dataset_hash": True,
            "requires_event_timestamp": True,
            "requires_asof_join": True,
            "blocks_future_leakage": True,
            "mutable_raw_inputs": False,
        },
    }


def _data_catalog_payload() -> Dict:
    try:
        data_port = data_catalog_runtime.require()
        real_datasets = _enumerate_real_datasets()
    except Exception as exc:
        return _unavailable_data_catalog_payload(type(exc).__name__)

    # DATASETS upgrade to REAL: enumerate the on-disk PIT store (crypto_daily.db) — real content hash,
    # row_count, coverage, PIT-safety. FEATURES / lineage stay SYNTHETIC (no persisted FeatureRegistry yet)
    # and are honestly marked. Fail-closed to the synthetic dataset fallback when no db is present (CI).
    if real_datasets:
        datasets = [dict(d) for d in real_datasets]
        datasets_provenance = "REAL_PIT_DATASETS"
        for dataset in datasets:
            dataset["checks"] = _dataset_checks(dataset)  # content_hash + PIT already real from the bars
    else:
        datasets = [dict(d) for d in _SYNTHETIC_CATALOG_DATASETS]
        datasets_provenance = "SYNTHETIC_DETERMINISTIC"
        for dataset in datasets:
            dataset["content_hash"] = data_port.dataset_content_hash(
                dataset["dataset_id"],
                dataset["version"],
            )
            dataset["point_in_time_safe"] = (
                data_port.verify_point_in_time_safe(dataset["dataset_id"])
            )
            dataset["checks"] = _dataset_checks(dataset)

    feature_specs = [
        (
            "feat-ts-mean-close-5",
            "ts_mean_close_5",
            "PRICE_MOMENTUM",
            "(ts_mean :close 5)",
            "ds-market-daily-bars",
            5,
            ["exp-alpha-1-v001", "exp-alpha-7-v001"],
        ),
        (
            "feat-ts-rank-volume-20",
            "ts_rank_volume_20",
            "LIQUIDITY",
            "(ts_rank :volume 20)",
            "ds-market-daily-bars",
            20,
            ["exp-alpha-2-v002", "exp-alpha-8-v002"],
        ),
        (
            "feat-zscore-returns-10",
            "zscore_returns_10",
            "REVERSAL",
            "(zscore :returns 10)",
            "ds-market-daily-bars",
            10,
            ["exp-alpha-3-v003"],
        ),
        (
            "feat-ts-delta-vwap-30",
            "ts_delta_vwap_30",
            "EXECUTION_PRICE",
            "(ts_delta :vwap 30)",
            "ds-market-daily-bars",
            30,
            ["exp-alpha-4-v001"],
        ),
        (
            "feat-turnover-std-60",
            "ts_std_turnover_60",
            "LIQUIDITY",
            "(ts_std :turnover 60)",
            "ds-liquidity-profile",
            60,
            ["exp-alpha-5-v002"],
        ),
        (
            "feat-rank-decay-amount-90",
            "rank_decay_amount_90",
            "CAPACITY",
            "(rank_decay :amount 90)",
            "ds-liquidity-profile",
            90,
            ["exp-alpha-6-v003"],
        ),
        (
            "feat-index-member-lag",
            "index_member_lag_safe",
            "UNIVERSE",
            "(asof_join :index_member 1)",
            "ds-index-membership",
            1,
            ["exp-alpha-1-v001", "exp-alpha-4-v001"],
        ),
        (
            "feat-fundamental-yield-ttm",
            "fundamental_yield_ttm",
            "FUNDAMENTAL",
            "(point_in_time :earnings_yield_ttm)",
            "ds-fundamentals-ttm",
            120,
            ["exp-alpha-9-v003"],
        ),
    ]
    dataset_quality = {dataset["dataset_id"]: dataset["quality_score"] for dataset in datasets}
    real_dataset_ids = [d["dataset_id"] for d in datasets]
    features = []
    for idx, (feature_id, name, family, expression, dataset_id, lookback_days, experiments) in enumerate(feature_specs):
        # Features are illustrative/synthetic (no persisted FeatureRegistry yet); when datasets are REAL their
        # hardcoded source ids don't exist, so attach each feature to a real dataset node (round-robin) to keep
        # the lineage graph connected. The feature values themselves remain synthetic and are marked as such.
        if dataset_id not in dataset_quality:
            dataset_id = real_dataset_ids[idx % len(real_dataset_ids)]
        quality_score = max(0.0, min(1.0, dataset_quality[dataset_id] - (idx % 3) * 0.006))
        features.append(
            {
                "feature_id": feature_id,
                "name": name,
                "family": family,
                "expression": expression,
                "source_dataset_id": dataset_id,
                "version": f"v{idx % 4 + 1}",
                "owner": "Alpha Research",
                "lookback_days": lookback_days,
                "point_in_time_policy": {
                    "event_timestamp_column": "event_ts",
                    "as_of_join": True,
                    "max_source_lag_days": 1 if lookback_days <= 30 else 2,
                    "leakage_guard": "STRICT_EVENT_TIME_UPPER_BOUND",
                },
                "quality_score": round(quality_score, 3),
                "lineage_hash": _short_hash(feature_id, dataset_id, expression, lookback_days),
                "used_by_experiments": experiments,
            }
        )

    lineage_edges = []
    for feature in features:
        lineage_edges.append(
            {"from": feature["source_dataset_id"], "to": feature["feature_id"], "kind": "DATASET_TO_FEATURE"}
        )
        for experiment_id in feature["used_by_experiments"]:
            lineage_edges.append({"from": feature["feature_id"], "to": experiment_id, "kind": "FEATURE_TO_EXPERIMENT"})

    quality_checks = [check for dataset in datasets for check in dataset["checks"]]
    pit_safe_features = sum(1 for feature in features if feature["point_in_time_policy"]["as_of_join"])
    return {
        "catalog_mode": "READ_ONLY_DATA_CATALOG",
        "feature_store_mode": "OFFLINE_POINT_IN_TIME_RESEARCH",
        "data_backend": data_port.data_backend,
        # REAL_PIT_DATASETS when datasets came from the on-disk PIT store; the feature/lineage block is still
        # synthetic regardless — honest partial. SYNTHETIC_DETERMINISTIC when no db (CI / fresh checkout).
        "data_provenance": datasets_provenance,
        "datasets_real": bool(real_datasets),
        "snapshot_present": bool(real_datasets),
        "snapshot_id": real_datasets[0].get("snapshot_id") if len(real_datasets) == 1 else None,
        "snapshot_ids": [
            str(dataset["snapshot_id"])
            for dataset in real_datasets
            if dataset.get("snapshot_id")
        ],
        "features_synthetic": True,
        "summary": {
            "datasets": len(datasets),
            "features": len(features),
            "quality_checks": len(quality_checks),
            "quality_passed": sum(1 for check in quality_checks if check["status"] == "PASS"),
            "point_in_time_safe_features": pit_safe_features,
            "lineage_edges": len(lineage_edges),
            "avg_dataset_quality": round(statistics.mean(dataset["quality_score"] for dataset in datasets), 3),
            "avg_feature_quality": round(statistics.mean(feature["quality_score"] for feature in features), 3),
        },
        "datasets": datasets,
        "features": features,
        "lineage_edges": lineage_edges,
        "data_channels": _data_channel_payload(),
        "binance_realtime": _binance_realtime_payload(),
        "quality_policy": {
            "requires_dataset_hash": True,
            "requires_event_timestamp": True,
            "requires_asof_join": True,
            "blocks_future_leakage": True,
            "mutable_raw_inputs": False,
        },
    }


router = APIRouter()


@router.get("/api/v1/console/data-catalog")
async def data_catalog() -> Dict:
    payload = _data_catalog_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }
