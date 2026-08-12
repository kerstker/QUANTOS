"""Resumable retrospective backfill for governed Binance book-depth v2 data.

Each completed UTC day is one atomic 14-symbol unit.  The worker delegates all
network and checksum handling to ``binance_book_depth_fetch`` and only skips a
day after independently confirming the exact 14 x 24 aligned authority grid.
Historical rows retain fetch-time knowledge and never gain forward credit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from collections import Counter
from collections.abc import Callable, Sequence

from services.data.binance_book_depth_archive import DATASET, DAY_MS, HOUR_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from tools.binance_book_depth_fetch import (
    DEFAULT_RAW_ARCHIVE,
    DEFAULT_STORE,
    refresh_book_depth_days,
)
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


DEFAULT_STATUS = str(
    Path("data") / "cache" / "binance_book_depth_backfill_status.json"
)
UNIVERSE = tuple(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS)


def _day_complete(store_path: str, day_ms: int, universe: Sequence[str]) -> bool:
    path = Path(store_path)
    if not path.is_file():
        return False
    store = BinanceSystemicStore(str(path))
    try:
        rows = store.range_observations(
            DATASET,
            start_event_time_ms=day_ms + 1,
            end_event_time_ms=day_ms + DAY_MS + 1,
            instruments=universe,
        )
    finally:
        store.close()
    expected_times = {
        day_ms + hour * HOUR_MS for hour in range(1, 25)
    }
    counts = Counter(str(row.get("instrument_id")) for row in rows)
    return (
        len(rows) == len(universe) * 24
        and set(counts) == set(universe)
        and all(counts[instrument] == 24 for instrument in universe)
        and {int(row["event_time_ms"]) for row in rows} == expected_times
        and all(row.get("external_price_alignment") == "PASS" for row in rows)
        and all(row.get("dataset") == DATASET for row in rows)
    )


def run_backfill(
    *,
    days: int,
    closed_day_ms: int,
    observed_at_ms: int,
    store_path: str = DEFAULT_STORE,
    archive_root: str = DEFAULT_RAW_ARCHIVE,
    status_path: str = DEFAULT_STATUS,
    universe: Sequence[str] = UNIVERSE,
    max_workers: int = 4,
    refresh: Callable[..., dict[str, object]] = refresh_book_depth_days,
) -> dict[str, object]:
    if (
        isinstance(days, bool)
        or not isinstance(days, int)
        or days < 1
        or days > 365
        or isinstance(closed_day_ms, bool)
        or not isinstance(closed_day_ms, int)
        or closed_day_ms < 0
        or closed_day_ms % DAY_MS
    ):
        raise ValueError("backfill window is invalid")
    selected = tuple(str(value).upper().strip() for value in universe)
    if len(selected) != 14 or len(set(selected)) != 14:
        raise ValueError("backfill requires the fixed 14-symbol universe")
    selected_days = tuple(
        closed_day_ms - offset * DAY_MS for offset in reversed(range(days))
    )
    completed = skipped = inserted_hours = 0
    processed_days: list[int] = []
    for day_ms in selected_days:
        if _day_complete(store_path, day_ms, selected):
            skipped += 1
            processed_days.append(day_ms)
            continue
        try:
            report = refresh(
                selected,
                (day_ms,),
                observed_at_ms=observed_at_ms,
                store_path=store_path,
                archive_root=archive_root,
                max_workers=max_workers,
            )
            if report.get("external_price_alignment") != "PASS":
                raise RuntimeError("backfill day lacks external price alignment")
            if not _day_complete(store_path, day_ms, selected):
                raise RuntimeError("backfill day is not an exact authority grid")
        except Exception as exc:
            failure = {
                "schema_version": 1,
                "generated_at_ms": observed_at_ms,
                "status": "FAIL",
                "mode": "L2_RETROSPECTIVE_BOOK_DEPTH_BACKFILL",
                "dataset": DATASET,
                "requested_days": days,
                "completed_days": completed,
                "skipped_complete_days": skipped,
                "failed_day_ms": day_ms,
                "failure_type": type(exc).__name__,
                "formal_forward_credit": False,
                "safety": _safety(),
            }
            atomic_write_json(status_path, failure, indent=2)
            raise
        completed += 1
        inserted_hours += int(report["inserted_hours"])
        processed_days.append(day_ms)
        atomic_write_json(
            status_path,
            _progress_report(
                observed_at_ms=observed_at_ms,
                requested_days=days,
                completed_days=completed,
                skipped_days=skipped,
                inserted_hours=inserted_hours,
                processed_days=processed_days,
                status="RUNNING",
            ),
            indent=2,
        )
    result = _progress_report(
        observed_at_ms=observed_at_ms,
        requested_days=days,
        completed_days=completed,
        skipped_days=skipped,
        inserted_hours=inserted_hours,
        processed_days=processed_days,
        status="PASS",
    )
    atomic_write_json(status_path, result, indent=2)
    return result


def _safety() -> dict[str, bool]:
    return {
        "paper_only": True,
        "credentials_accessed": False,
        "broker_connectivity": False,
        "order_submission": False,
        "live_trading": False,
    }


def _progress_report(
    *,
    observed_at_ms: int,
    requested_days: int,
    completed_days: int,
    skipped_days: int,
    inserted_hours: int,
    processed_days: Sequence[int],
    status: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at_ms": observed_at_ms,
        "status": status,
        "mode": "L2_RETROSPECTIVE_BOOK_DEPTH_BACKFILL",
        "dataset": DATASET,
        "requested_days": requested_days,
        "completed_days": completed_days,
        "skipped_complete_days": skipped_days,
        "inserted_hours": inserted_hours,
        "processed_day_count": len(processed_days),
        "first_processed_day_ms": min(processed_days) if processed_days else None,
        "last_processed_day_ms": max(processed_days) if processed_days else None,
        "formal_forward_credit": False,
        "safety": _safety(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill governed Binance book-depth v2 retrospective history"
    )
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    now_ms = int(time.time() * 1000)
    # The newest archive can lag UTC close.  A historical study deliberately
    # anchors one extra day back; daily forward collection covers the frontier.
    closed_day = (now_ms // DAY_MS) * DAY_MS - 2 * DAY_MS
    result = run_backfill(
        days=args.days,
        closed_day_ms=closed_day,
        observed_at_ms=now_ms,
        store_path=args.store,
        archive_root=args.archive_root,
        status_path=args.status,
        max_workers=args.workers,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_STATUS", "UNIVERSE", "main", "run_backfill"]
