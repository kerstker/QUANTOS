"""Dataset versioning with reproducible content hashing (Phase 2, CT-3).

Identical data -> identical content_hash (order-independent), so a DatasetVersion
is a stable, auditable fingerprint of exactly what a backtest consumed.
"""
from __future__ import annotations

from typing import Iterable

from quantos.contracts.data import DatasetVersion
from quantos.contracts.hashing import short_hash


def content_hash_of(records: Iterable) -> str:
    """Order-independent hash of a set of pydantic records."""
    dumps = sorted(short_hash(r.model_dump(mode="json")) for r in records)
    return short_hash(dumps)


def build_dataset_version(
    dataset_id: str,
    version: str,
    records: Iterable,
    created_at: int,
    source: str = "",
    point_in_time: bool = True,
) -> DatasetVersion:
    records = list(records)
    return DatasetVersion(
        dataset_id=dataset_id,
        version=version,
        created_at=created_at,
        row_count=len(records),
        content_hash=content_hash_of(records),
        source=source,
        point_in_time=point_in_time,
    )
