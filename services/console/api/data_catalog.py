"""Read-only data-catalog HTTP route."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter

from services.console.api.common import short_hash as _short_hash
from services.console.api.data_port import data_catalog_runtime


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


def _retained_fx_dataset() -> Dict | None:
    """Return the versioned FX source node used by the persisted feature cache."""
    root = Path(__file__).resolve().parents[3]
    manifest_path = root / "evidence" / "datasets" / "fx_pit_tradable_v1.manifest.json"
    bundle_path = root / "evidence" / "datasets" / "fx_pit_tradable_v1.jsonl.gz"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("bundle_ref") != "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"
            or not bundle_path.is_file()
            or type(manifest.get("row_count")) is not int
            or not isinstance(manifest.get("bundle_sha256"), str)
        ):
            return None
        return {
            "dataset_id": "fx_pit_tradable_v1",
            "name": "Retained FX PIT bundle",
            "domain": "FX_MARKET_DATA",
            "version": "fx_pit_tradable_v1",
            "snapshot_id": "fx-pit-tradable-v1",
            "row_count": manifest["row_count"],
            "coverage": 1.0,
            # This is a historical, offline artifact; never present it as fresh market data.
            "freshness_hours": 999999.0,
            "quality_score": 1.0,
            "point_in_time_safe": True,
            "owner": "FX Research",
            "storage_uri": "artifact://evidence/datasets/fx_pit_tradable_v1.jsonl.gz",
            "content_hash": manifest["bundle_sha256"],
        }
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _data_catalog_payload() -> Dict:
    data_port = data_catalog_runtime.require()

    # DATASETS upgrade to REAL when the on-disk PIT store is present. FEATURES upgrade independently
    # when the offline bitemporal feature cache is present; both paths fail closed in a fresh checkout.
    real_datasets = _enumerate_real_datasets()
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

    real_features = getattr(data_port, "enumerate_real_features", lambda: [])()
    feature_provenance = "REAL_PIT_FEATURE_CACHE" if real_features else "SYNTHETIC_DETERMINISTIC"
    feature_store_mode = "PERSISTED_PIT_FEATURE_CACHE" if real_features else "OFFLINE_POINT_IN_TIME_RESEARCH"
    if real_features:
        fx_dataset = _retained_fx_dataset()
        if fx_dataset is not None:
            if real_datasets:
                datasets.append(fx_dataset)
            else:
                # A checkout may retain the FX evidence without the optional crypto cache.
                # Prefer the one real source node to an unrelated synthetic catalog.
                datasets = [fx_dataset]
                datasets_provenance = "REAL_PIT_DATASETS"
            datasets.sort(key=lambda dataset: dataset["dataset_id"])
            for dataset in datasets:
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
        # The fallback features are illustrative/synthetic. When datasets are REAL their hardcoded source ids
        # may not exist, so attach each fallback feature to a real dataset node to keep the graph connected.
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

    if real_features:
        features = [dict(feature) for feature in real_features]

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
        "feature_store_mode": feature_store_mode,
        "data_backend": data_port.data_backend,
        # REAL_PIT_DATASETS describes dataset provenance; feature provenance is reported separately below.
        # SYNTHETIC_DETERMINISTIC is retained for a fresh checkout without the corresponding artifacts.
        "data_provenance": datasets_provenance,
        "datasets_real": datasets_provenance == "REAL_PIT_DATASETS",
        "features_synthetic": not bool(real_features),
        "feature_provenance": feature_provenance,
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
