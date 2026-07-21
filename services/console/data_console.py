"""Data-owned read adapter used by the official console composition."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Dict, List, Optional

from services.data.catalog import (
    DATA_BACKEND,
    dataset_content_hash,
    verify_point_in_time_safe,
)
from services.data.persistent_store import SqlitePITStore
from services.data.versioning import build_dataset_version
from services.features.registry_store import SqliteFeatureRegistryStore
from services.features.value_store import SqliteFeatureValueStore


class DefaultDataConsolePort:
    data_backend = DATA_BACKEND

    def __init__(self, crypto_daily_db: Optional[Path] = None, feature_store_db: Optional[Path] = None) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self._crypto_daily_db = crypto_daily_db or (repo_root / "data" / "cache" / "crypto_daily.db")
        self._feature_store_db = feature_store_db or (repo_root / "data" / "cache" / "fx_feature_store.db")
        self._real_datasets_cache: Optional[List[Dict]] = None
        self._distribution_cache: Optional[Dict[str, List[float]]] = None
        self._real_features_cache: Optional[List[Dict]] = None
        self._feature_distribution_cache: Optional[Dict[str, List[float]]] = None

    def enumerate_real_datasets(self) -> List[Dict]:
        if self._real_datasets_cache is not None:
            return self._real_datasets_cache
        if not self._crypto_daily_db.exists():
            self._real_datasets_cache = []
            return self._real_datasets_cache

        rows: List[Dict] = []
        try:
            store = SqlitePITStore(str(self._crypto_daily_db), create=False)
            collected = []
            global_max_knowledge_time = 0
            for key in store.keys():
                try:
                    bars = store.fetch_bars(key, 0, 9_999_999_999_999)
                except Exception:
                    continue
                if not bars:
                    continue
                global_max_knowledge_time = max(
                    global_max_knowledge_time,
                    max(bar.knowledge_time for bar in bars),
                )
                collected.append((key, bars))

            for key, bars in collected:
                event_times = [bar.event_time for bar in bars]
                knowledge_times = [bar.knowledge_time for bar in bars]
                span_days = (max(event_times) - min(event_times)) / 86_400_000.0
                coverage = min(1.0, len(bars) / (span_days + 1.0)) if span_days > 0 else 1.0
                pit_safe = all(knowledge >= event for event, knowledge in zip(event_times, knowledge_times))
                freshness_hours = round(
                    max(
                        0.0,
                        (global_max_knowledge_time - max(knowledge_times)) / 3_600_000.0,
                    ),
                    1,
                )
                quality = round(
                    coverage if pit_safe else coverage * 0.5,
                    3,
                )
                version = build_dataset_version(
                    f"crypto_daily.{key}",
                    "crypto_daily",
                    bars,
                    created_at=0,
                )
                rows.append(
                    {
                        "dataset_id": f"crypto_daily.{key}",
                        "name": key,
                        "domain": "CRYPTO_MARKET_DATA",
                        "version": "crypto_daily",
                        "snapshot_id": "crypto_daily-snapshot",
                        "row_count": int(version.row_count),
                        "coverage": round(coverage, 3),
                        "freshness_hours": freshness_hours,
                        "quality_score": quality,
                        "point_in_time_safe": pit_safe,
                        "owner": "Crypto Research",
                        "storage_uri": (f"sqlite://data/cache/crypto_daily.db#{key}"),
                        "content_hash": version.content_hash,
                    }
                )
            rows.sort(key=lambda dataset: dataset["dataset_id"])
        except Exception:
            rows = []
        self._real_datasets_cache = rows
        return rows

    def dataset_content_hash(self, dataset_id: str, version: str) -> str:
        return dataset_content_hash(dataset_id, version)

    def verify_point_in_time_safe(self, dataset_id: str) -> bool:
        return verify_point_in_time_safe(dataset_id)

    def enumerate_distribution_series(self) -> Dict[str, List[float]]:
        """Return real PIT close-to-close series for read-only drift diagnostics.

        The console deliberately exposes dataset return distributions rather than
        inventing feature observations. The feature cache has its own read-only
        adapter below. The latest knowledge time is used as the as-of
        boundary and duplicate event times keep only their latest known bar.
        """
        if self._distribution_cache is not None:
            return {key: list(values) for key, values in self._distribution_cache.items()}
        if not self._crypto_daily_db.exists():
            self._distribution_cache = {}
            return {}

        series: Dict[str, List[float]] = {}
        try:
            store = SqlitePITStore(str(self._crypto_daily_db), create=False)
            for key in store.keys():
                raw = store.fetch_bars(key, 0, 9_999_999_999_999)
                if not raw:
                    continue
                as_of_time = max(bar.knowledge_time for bar in raw)
                latest_by_event = {
                    bar.event_time: bar
                    for bar in store.as_of(key, as_of_time)
                }
                bars = [latest_by_event[t] for t in sorted(latest_by_event)]
                returns: List[float] = []
                for previous, current in zip(bars, bars[1:]):
                    if (
                        previous.close is None
                        or current.close is None
                        or previous.close <= 0
                        or current.close <= 0
                    ):
                        continue
                    value = float(current.close / previous.close - 1.0)
                    if math.isfinite(value):
                        returns.append(value)
                if returns:
                    series[f"crypto_daily.{key}"] = returns
        except Exception:
            series = {}
        self._distribution_cache = series
        return {key: list(values) for key, values in series.items()}

    def enumerate_real_features(self) -> List[Dict]:
        """Read the offline PIT feature cache without recomputing or mutating it."""
        if self._real_features_cache is not None:
            return [dict(feature) for feature in self._real_features_cache]
        if not self._feature_store_db.exists():
            self._real_features_cache = []
            return []

        rows: List[Dict] = []
        registry = None
        values = None
        try:
            registry = SqliteFeatureRegistryStore(str(self._feature_store_db), create=False)
            values = SqliteFeatureValueStore(str(self._feature_store_db), create=False)
            for item in registry.items():
                feature_id = item.definition.feature_id
                version = item.version
                summary = values.summary(feature_id, version)
                if summary["row_count"] <= 0:
                    continue
                source = (
                    item.definition.inputs[0].rsplit(".", 1)[0]
                    if item.definition.inputs
                    else "unknown"
                )
                try:
                    lookback_days = int(feature_id.rsplit("_", 1)[-1])
                except ValueError:
                    lookback_days = 0
                audit = registry.audit(feature_id, version)
                lineage_hash = hashlib.sha256(
                    "|".join((feature_id, source, item.definition.formula, version)).encode("utf-8")
                ).hexdigest()
                rows.append(
                    {
                        "feature_id": feature_id,
                        "name": item.definition.name,
                        "family": "FX_PIT",
                        "expression": item.definition.formula,
                        "source_dataset_id": source,
                        "version": version,
                        "owner": item.owner,
                        "lookback_days": lookback_days,
                        "point_in_time_policy": {
                            "event_timestamp_column": "event_time",
                            "as_of_join": True,
                            "max_source_lag_days": 0,
                            "leakage_guard": "STRICT_EVENT_TIME_UPPER_BOUND",
                        },
                        "quality_score": 1.0 if audit["leakage_passed"] is True else 0.0,
                        "lineage_hash": lineage_hash,
                        "used_by_experiments": [],
                        "materialized_row_count": summary["row_count"],
                        "materialized_instrument_count": summary["instrument_count"],
                        "first_event_time": summary["min_event_time"],
                        "last_event_time": summary["max_event_time"],
                        "content_hash": summary.get("content_sha256"),
                        "cache_provenance": "REAL_PIT_FEATURE_VALUE_CACHE_V1",
                    }
                )
        except Exception:
            rows = []
        finally:
            if registry is not None:
                registry.close()
            if values is not None:
                values.close()
        rows.sort(key=lambda feature: (feature["feature_id"], feature["version"]))
        self._real_features_cache = rows
        return [dict(feature) for feature in rows]

    def enumerate_feature_distribution_series(self) -> Dict[str, List[float]]:
        """Read PIT feature distributions for the diagnostic monitor only."""
        if self._feature_distribution_cache is not None:
            return {key: list(values) for key, values in self._feature_distribution_cache.items()}
        if not self._feature_store_db.exists():
            self._feature_distribution_cache = {}
            return {}

        series: Dict[str, List[float]] = {}
        registry = None
        values = None
        try:
            registry = SqliteFeatureRegistryStore(str(self._feature_store_db), create=False)
            values = SqliteFeatureValueStore(str(self._feature_store_db), create=False)
            for item in registry.items():
                feature_id = item.definition.feature_id
                version = item.version
                if registry.audit(feature_id, version)["leakage_passed"] is not True:
                    continue
                observations = values.distribution_series(feature_id, version)
                if observations:
                    key = feature_id if feature_id not in series else f"{feature_id}@{version}"
                    series[key] = observations
        except Exception:
            series = {}
        finally:
            if registry is not None:
                registry.close()
            if values is not None:
                values.close()
        self._feature_distribution_cache = series
        return {key: list(values) for key, values in series.items()}


DEFAULT_DATA_CONSOLE_PORT = DefaultDataConsolePort()
