"""Data-owned read adapter used by the official console composition."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from services.data.catalog import (
    DATA_BACKEND,
    dataset_content_hash,
    verify_point_in_time_safe,
)
from services.data.persistent_store import SqlitePITStore
from services.data.versioning import build_dataset_version


class DefaultDataConsolePort:
    data_backend = DATA_BACKEND

    def __init__(self, crypto_daily_db: Optional[Path] = None) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self._crypto_daily_db = crypto_daily_db or (repo_root / "data" / "cache" / "crypto_daily.db")
        self._real_datasets_cache: Optional[List[Dict]] = None

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


DEFAULT_DATA_CONSOLE_PORT = DefaultDataConsolePort()
