"""Read-only local-CSV ingestion adapter (no network, no broker).

Reads a local CSV of bars, normalizes + rejects future/invalid rows (recorded,
not crashed), dedups via the persisted store, and returns an IngestionReport with
a quality report and a reproducible content hash. The shared `_ingest_records`
core is reused by the real-market-data adapter (market_csv.py)."""
from __future__ import annotations

import csv
from dataclasses import dataclass

from .normalization import normalize_bar, reject_if_future
from .quality import build_report
from .versioning import content_hash_of

_BAR_COLS = ("instrument_id", "event_time", "open", "high", "low", "close", "volume")


class FatalIngestionError(ValueError):
    """配置级接入错误,必须向上抛,不能被吞成 rejected 行
    (例如某行缺 knowledge_time 且未提供默认值)。"""


@dataclass(frozen=True)
class IngestionReport:
    rows_read: int
    bars_ingested: int
    duplicates: int
    rejected: tuple
    content_hash: str
    quality: object


def _ingest_records(records, parse, store, *, now, dataset_id, version, strict: bool = False) -> IngestionReport:
    """共享失败关闭接入核心。`records` 产出 (index, raw);`parse(raw)` 返回
    (canonical_row_dict, knowledge_time),可抛(→ rejected)。parse 抛
    FatalIngestionError 则向上传播(配置错误,非坏行)。canonical_row_dict 含
    instrument_id/event_time/OHLCV,不含 knowledge_time。

    Rank #22:`strict=True` 时,若数据质量报告不通过(重复/乱序/缺失/未来时间戳)→ 抛
    `FatalIngestionError`(fail-closed,承重数据集不得静默接入坏数据)。默认 False = 零回归。
    质量报告的重复-时间戳检查以**单一时间序列**为前提,故 strict 用于单标的接入(逐 fetcher 常态)。"""
    rows_read = 0
    valid_bars, rejected = [], []
    bars_ingested = 0
    duplicates = 0
    for i, raw in records:
        rows_read += 1
        try:
            canonical, kt = parse(raw)
            bar = normalize_bar(canonical, kt)
            if now is not None:
                reject_if_future(bar, now)
        except FatalIngestionError:
            raise
        except Exception as exc:
            rejected.append((i, type(exc).__name__, str(exc)))   # index, class, detail
            continue
        valid_bars.append(bar)
        if store.contains(bar.instrument_id, bar.event_time, bar.knowledge_time):
            duplicates += 1
        elif store.add(bar.instrument_id, bar):
            bars_ingested += 1
        else:
            duplicates += 1
    # Quality report must see the ORIGINAL arrival order, not a sorted copy — sorting first made
    # count_out_of_order structurally always 0 (a dead check; audit P2-1). Duplicate/future/completeness
    # checks are order-independent; ordering is the whole point of feeding the un-sorted series.
    quality = build_report(dataset_id, [b.event_time for b in valid_bars], as_of=now, version=version)
    if strict and not quality.passed:
        failed = [c.name for c in quality.checks if not c.passed]
        raise FatalIngestionError(f"strict ingestion: dataset {dataset_id!r} failed quality checks {failed}")
    return IngestionReport(
        rows_read=rows_read, bars_ingested=bars_ingested, duplicates=duplicates,
        rejected=tuple(rejected),
        content_hash=content_hash_of(valid_bars) if valid_bars else "",
        quality=quality,
    )


def ingest_bars_csv(path: str, store, *, knowledge_time_default=None, now=None,
                    dataset_id: str = "ingested", version: str = "", strict: bool = False) -> IngestionReport:
    def parse(raw):
        row = {k: v for k, v in raw.items() if v not in ("", None)}
        kt_raw = row.pop("knowledge_time", None)
        if kt_raw is None and knowledge_time_default is None:
            raise FatalIngestionError("row has no knowledge_time and no knowledge_time_default supplied")
        kt = int(kt_raw) if kt_raw is not None else int(knowledge_time_default)
        canonical = {k: v for k, v in row.items() if k in _BAR_COLS}
        return canonical, kt

    with open(path, newline="", encoding="utf-8") as fh:
        records = list(enumerate(csv.DictReader(fh)))
    return _ingest_records(records, parse, store, now=now, dataset_id=dataset_id, version=version, strict=strict)
