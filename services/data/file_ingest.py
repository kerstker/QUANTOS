"""Read-only local-CSV ingestion adapter (no network, no broker).

Reads a local CSV of bars, normalizes + rejects future/invalid rows (recorded,
not crashed), dedups via the persisted store, and returns an IngestionReport with
a quality report and a reproducible content hash. The ONLY data path into the
persisted lake at L2."""
from __future__ import annotations

import csv
from dataclasses import dataclass

from .normalization import normalize_bar, reject_if_future
from .quality import build_report
from .versioning import content_hash_of

_BAR_COLS = ("instrument_id", "event_time", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class IngestionReport:
    rows_read: int
    bars_ingested: int
    duplicates: int
    rejected: tuple
    content_hash: str
    quality: object


def ingest_bars_csv(path: str, store, *, knowledge_time_default=None, now=None,
                    dataset_id: str = "ingested", version: str = "") -> IngestionReport:
    rows_read = 0
    valid_bars, rejected = [], []          # valid_bars = all that pass validation (for hash/quality)
    bars_ingested = 0                      # only newly-inserted (dedup-aware)
    duplicates = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for i, raw in enumerate(csv.DictReader(fh)):
            rows_read += 1
            row = {k: v for k, v in raw.items() if v not in ("", None)}
            kt_raw = row.pop("knowledge_time", None)
            if kt_raw is None and knowledge_time_default is None:
                raise ValueError("row has no knowledge_time and no knowledge_time_default supplied")
            try:
                kt = int(kt_raw) if kt_raw is not None else int(knowledge_time_default)
                bar = normalize_bar({k: v for k, v in row.items() if k in _BAR_COLS}, kt)
                if now is not None:
                    reject_if_future(bar, now)
            except Exception as exc:
                rejected.append((i, type(exc).__name__))
                continue
            valid_bars.append(bar)
            if store.contains(bar.instrument_id, bar.event_time, bar.knowledge_time):
                duplicates += 1
            elif store.add(bar.instrument_id, bar):
                bars_ingested += 1
            else:
                duplicates += 1
    return IngestionReport(
        rows_read=rows_read, bars_ingested=bars_ingested, duplicates=duplicates,
        rejected=tuple(rejected),
        # content hash reflects the FILE's valid content (idempotent across re-ingests)
        content_hash=content_hash_of(valid_bars) if valid_bars else "",
        quality=build_report(dataset_id, sorted(b.event_time for b in valid_bars), as_of=now, version=version),
    )
