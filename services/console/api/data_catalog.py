"""Read-only data-catalog HTTP route."""

from __future__ import annotations

import statistics
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


def _data_catalog_payload() -> Dict:
    data_port = data_catalog_runtime.require()

    # DATASETS upgrade to REAL: enumerate the on-disk PIT store (crypto_daily.db) — real content hash,
    # row_count, coverage, PIT-safety. FEATURES / lineage stay SYNTHETIC (no persisted FeatureRegistry yet)
    # and are honestly marked. Fail-closed to the synthetic dataset fallback when no db is present (CI).
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
