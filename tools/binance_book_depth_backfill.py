"""Resumable retrospective backfill for governed Binance book-depth v2 data.

Each completed UTC day is one atomic 14-symbol unit.  The worker delegates all
network and checksum handling to ``binance_book_depth_fetch`` and only skips a
day after independently confirming the exact 14 x 24 aligned authority grid.
Historical rows retain fetch-time knowledge and never gain forward credit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
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
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIRMATION_CONTRACT = (
    ROOT / "config" / "binance_book_depth_alpha_confirmation.json"
)
CONFIRMATION_COMMIT = "7b208c08e4161099b20ebbea8d6a6c76b9521a1b"
UNIVERSE = tuple(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS)


class BookDepthBackfillError(RuntimeError):
    """A contract-bound historical backfill cannot be proven safe to run."""


def _verify_confirmation_contract(path: str | Path) -> tuple[str, dict[str, object]]:
    contract_path = Path(path)
    raw = contract_path.read_bytes()
    repository_path = "config/binance_book_depth_alpha_confirmation.json"
    try:
        payload = json.loads(raw)
        committed_blob = subprocess.run(
            ["git", "rev-parse", f"{CONFIRMATION_COMMIT}:{repository_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        worktree_blob = subprocess.run(
            ["git", "hash-object", "--path", repository_path, str(contract_path)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        committed = subprocess.run(
            ["git", "show", f"{CONFIRMATION_COMMIT}:{repository_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", CONFIRMATION_COMMIT, "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise BookDepthBackfillError(
            "immutable confirmation contract cannot be verified"
        ) from exc
    if not isinstance(payload, dict) or committed_blob != worktree_blob:
        raise BookDepthBackfillError(
            "confirmation contract differs from immutable commit"
        )
    return hashlib.sha256(committed).hexdigest(), payload


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


def run_contract_backfill(
    *,
    contract: dict[str, object],
    observed_at_ms: int,
    store_path: str = DEFAULT_STORE,
    archive_root: str = DEFAULT_RAW_ARCHIVE,
    status_path: str = DEFAULT_STATUS,
    max_workers: int = 4,
    refresh: Callable[..., dict[str, object]] = refresh_book_depth_days,
    contract_commit: str | None = None,
    contract_sha256: str | None = None,
) -> dict[str, object]:
    """Backfill only the immutable unseen confirmation window.

    The pre-registration records both an empty-window observation and the exact
    authority head.  Before the first download, the current store must still
    match both facts.  A resumable retry may proceed only when every existing
    day is a complete post-registration grid whose knowledge time is no earlier
    than the frozen observation time.
    """
    if contract.get("status") != "PRE_REGISTERED_BEFORE_CONFIRMATION_DATA_ACCESS":
        raise BookDepthBackfillError("confirmation contract is not pre-registered")
    if contract.get("dataset_id") != DATASET:
        raise BookDepthBackfillError("confirmation dataset is invalid")
    window = contract.get("confirmation_window")
    unseen = contract.get("frozen_unseen_window_evidence")
    if not isinstance(window, dict) or not isinstance(unseen, dict):
        raise BookDepthBackfillError("confirmation window evidence is missing")
    start_day_ms = int(window["start_day_ms"])
    end_exclusive_ms = int(window["end_exclusive_ms"])
    day_count = int(window["day_count"])
    if (
        start_day_ms < 0
        or start_day_ms % DAY_MS
        or end_exclusive_ms != start_day_ms + day_count * DAY_MS
        or day_count < 1
        or day_count > 365
        or window.get("relation_to_exploration")
        != "STRICTLY_EARLIER_NON_OVERLAPPING"
        or end_exclusive_ms != int(window["exploration_start_exclusive_ms"])
    ):
        raise BookDepthBackfillError("confirmation window is invalid")
    selected = tuple(str(value).upper().strip() for value in contract["universe"])  # type: ignore[index]
    if len(selected) != len(set(selected)) or set(selected) != set(UNIVERSE):
        raise BookDepthBackfillError("confirmation universe is not the fixed 14")
    frozen_at_ms = int(unseen["observed_at_ms"])
    if int(observed_at_ms) <= frozen_at_ms:
        raise BookDepthBackfillError("backfill time does not postdate pre-registration")

    path = Path(store_path)
    if not path.is_file():
        raise BookDepthBackfillError("confirmation requires the frozen authority store")
    store = BinanceSystemicStore(str(path))
    try:
        existing = store.range_observations(
            DATASET,
            start_event_time_ms=start_day_ms + 1,
            end_event_time_ms=end_exclusive_ms + 1,
            instruments=selected,
        )
        if not store.chain_valid():
            raise BookDepthBackfillError("book-depth authority chain is invalid")
        current_head = store.authority_head()
    finally:
        store.close()
    frozen_head = {
        "seq": int(unseen["authority_head_seq"]),
        "row_hash": str(unseen["authority_head_row_hash"]),
    }
    if not existing:
        if int(unseen["dataset_observation_count"]) != 0 or current_head != frozen_head:
            raise BookDepthBackfillError("unseen-window authority binding drifted")
    else:
        if any(int(row["knowledge_time_ms"]) < frozen_at_ms for row in existing):
            raise BookDepthBackfillError("confirmation window was observed before freeze")
        existing_days = {
            (int(row["event_time_ms"]) - 1) // DAY_MS * DAY_MS
            for row in existing
        }
        if any(not _day_complete(store_path, day_ms, selected) for day_ms in existing_days):
            raise BookDepthBackfillError("partial confirmation day blocks resume")

    result = run_backfill(
        days=day_count,
        closed_day_ms=end_exclusive_ms - DAY_MS,
        observed_at_ms=int(observed_at_ms),
        store_path=store_path,
        archive_root=archive_root,
        status_path=status_path,
        universe=selected,
        max_workers=max_workers,
        refresh=refresh,
    )
    bound_result = {
        **result,
        "mode": "L2_CONTRACT_BOUND_UNSEEN_CONFIRMATION_BACKFILL",
        "contract_study_id": contract["study_id"],
        "contract_commit": contract_commit,
        "contract_sha256": contract_sha256,
        "contract_frozen_at_ms": frozen_at_ms,
        "preexisting_confirmation_observations": len(existing),
        "confirmation_window_start_ms": start_day_ms,
        "confirmation_window_end_exclusive_ms": end_exclusive_ms,
    }
    atomic_write_json(status_path, bound_result, indent=2)
    return bound_result


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
    parser.add_argument(
        "--confirmation-contract",
        nargs="?",
        const=str(DEFAULT_CONFIRMATION_CONTRACT),
        help="Use the exact immutable confirmation window instead of latest N days",
    )
    args = parser.parse_args(argv)
    now_ms = int(time.time() * 1000)
    if args.confirmation_contract:
        contract_sha256, contract = _verify_confirmation_contract(
            args.confirmation_contract
        )
        result = run_contract_backfill(
            contract=contract,
            observed_at_ms=now_ms,
            store_path=args.store,
            archive_root=args.archive_root,
            status_path=args.status,
            max_workers=args.workers,
            contract_commit=CONFIRMATION_COMMIT,
            contract_sha256=contract_sha256,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
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


__all__ = [
    "BookDepthBackfillError",
    "CONFIRMATION_COMMIT",
    "DEFAULT_CONFIRMATION_CONTRACT",
    "DEFAULT_STATUS",
    "UNIVERSE",
    "main",
    "run_backfill",
    "run_contract_backfill",
]
