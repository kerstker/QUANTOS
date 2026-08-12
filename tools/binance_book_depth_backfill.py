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
import math
from pathlib import Path
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Sequence

from services.data.binance_book_depth_archive import DATASET, DAY_MS, HOUR_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from tools.binance_book_depth_fetch import (
    BinanceBookDepthQualityRejected,
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
DEFAULT_CONFIRMATION_QUALITY_POLICY = (
    ROOT / "config" / "binance_book_depth_confirmation_quality_policy.json"
)
CONFIRMATION_QUALITY_POLICY_COMMIT = "b0f2be401adcb50545efd88d8b403124af21dbbc"
DEFAULT_CONFIRMATION_CONTRACT_V2 = (
    ROOT / "config" / "binance_book_depth_alpha_confirmation_v2.json"
)
DEFAULT_CONFIRMATION_QUALITY_POLICY_V2 = (
    ROOT / "config" / "binance_book_depth_confirmation_quality_policy_v2.json"
)
CONFIRMATION_V2_COMMIT = "4e3206e87f4cd47b8161a39b97badb2e8a868f85"
DEFAULT_CONFIRMATION_QUALITY_POLICY_V3 = (
    ROOT / "config" / "binance_book_depth_confirmation_quality_policy_v3.json"
)
CONFIRMATION_QUALITY_POLICY_V3_COMMIT = (
    "80bfd5fe1373cffd0ec21c5cd7c42c81c8866d0d"
)
DEFAULT_CONFIRMATION_QUALITY_POLICY_V4 = (
    ROOT / "config" / "binance_book_depth_confirmation_quality_policy_v4.json"
)
CONFIRMATION_QUALITY_POLICY_V4_COMMIT = (
    "1c85c937e4c6b91c1c631263b01cbc01c7e4ab30"
)
UNIVERSE = tuple(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS)


class BookDepthBackfillError(RuntimeError):
    """A contract-bound historical backfill cannot be proven safe to run."""


def _verify_committed_json(
    path: str | Path, *, commit: str, repository_path: str, label: str
) -> tuple[str, dict[str, object]]:
    contract_path = Path(path)
    raw = contract_path.read_bytes()
    try:
        payload = json.loads(raw)
        committed_blob = subprocess.run(
            ["git", "rev-parse", f"{commit}:{repository_path}"],
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
            ["git", "show", f"{commit}:{repository_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise BookDepthBackfillError(
            f"immutable {label} cannot be verified"
        ) from exc
    if not isinstance(payload, dict) or committed_blob != worktree_blob:
        raise BookDepthBackfillError(
            f"{label} differs from immutable commit"
        )
    return hashlib.sha256(committed).hexdigest(), payload


def _verify_confirmation_contract(path: str | Path) -> tuple[str, dict[str, object]]:
    return _verify_committed_json(
        path,
        commit=CONFIRMATION_COMMIT,
        repository_path="config/binance_book_depth_alpha_confirmation.json",
        label="confirmation contract",
    )


def _verify_confirmation_quality_policy(
    path: str | Path,
) -> tuple[str, dict[str, object]]:
    return _verify_committed_json(
        path,
        commit=CONFIRMATION_QUALITY_POLICY_COMMIT,
        repository_path="config/binance_book_depth_confirmation_quality_policy.json",
        label="confirmation quality policy",
    )


def _verify_confirmation_v2_contracts() -> tuple[str, dict, str, dict]:
    contract_sha, contract = _verify_committed_json(
        DEFAULT_CONFIRMATION_CONTRACT_V2,
        commit=CONFIRMATION_V2_COMMIT,
        repository_path="config/binance_book_depth_alpha_confirmation_v2.json",
        label="v2 confirmation contract",
    )
    policy_sha, policy = _verify_committed_json(
        DEFAULT_CONFIRMATION_QUALITY_POLICY_V2,
        commit=CONFIRMATION_V2_COMMIT,
        repository_path="config/binance_book_depth_confirmation_quality_policy_v2.json",
        label="v2 confirmation quality policy",
    )
    return contract_sha, contract, policy_sha, policy


def _verify_confirmation_v3_contracts() -> tuple[str, dict, str, dict]:
    contract_sha, contract = _verify_confirmation_contract(
        DEFAULT_CONFIRMATION_CONTRACT
    )
    policy_sha, policy = _verify_committed_json(
        DEFAULT_CONFIRMATION_QUALITY_POLICY_V3,
        commit=CONFIRMATION_QUALITY_POLICY_V3_COMMIT,
        repository_path=(
            "config/binance_book_depth_confirmation_quality_policy_v3.json"
        ),
        label="v3 confirmation quality policy",
    )
    return contract_sha, contract, policy_sha, policy


def _verify_confirmation_v4_contracts() -> tuple[str, dict, str, dict]:
    contract_sha, contract = _verify_confirmation_contract(
        DEFAULT_CONFIRMATION_CONTRACT
    )
    policy_sha, policy = _verify_committed_json(
        DEFAULT_CONFIRMATION_QUALITY_POLICY_V4,
        commit=CONFIRMATION_QUALITY_POLICY_V4_COMMIT,
        repository_path=(
            "config/binance_book_depth_confirmation_quality_policy_v4.json"
        ),
        label="v4 confirmation quality policy",
    )
    diagnostic_sha, _ = _verify_committed_json(
        ROOT / str(policy["source_quality_diagnostic"]),
        commit=str(policy["source_quality_diagnostic_commit"]),
        repository_path=str(policy["source_quality_diagnostic"]),
        label="v4 source quality diagnostic",
    )
    if diagnostic_sha != policy.get("source_quality_diagnostic_git_blob_sha256"):
        raise BookDepthBackfillError(
            "v4 source quality diagnostic binding is invalid"
        )
    return contract_sha, contract, policy_sha, policy


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


def _confirmation_capacity(
    *,
    store_path: str,
    start_day_ms: int,
    end_exclusive_ms: int,
    universe: Sequence[str],
    horizon_hours: int,
) -> dict[str, object]:
    """Count exact source capacity without reading signals, prices, or returns."""
    store = BinanceSystemicStore(store_path)
    try:
        rows = store.range_observations(
            DATASET,
            start_event_time_ms=start_day_ms + 1,
            end_event_time_ms=end_exclusive_ms + 1,
            instruments=universe,
        )
    finally:
        store.close()
    by_day: dict[int, list[dict[str, object]]] = {}
    by_time: dict[int, set[str]] = {}
    for row in rows:
        event_time_ms = int(row["event_time_ms"])
        day_ms = (event_time_ms - 1) // DAY_MS * DAY_MS
        by_day.setdefault(day_ms, []).append(row)
        by_time.setdefault(event_time_ms, set()).add(str(row["instrument_id"]))
    selected = set(universe)
    complete_days: list[int] = []
    for day_ms in range(start_day_ms, end_exclusive_ms, DAY_MS):
        day_rows = by_day.get(day_ms, [])
        counts = Counter(str(row["instrument_id"]) for row in day_rows)
        expected_times = {day_ms + hour * HOUR_MS for hour in range(1, 25)}
        if (
            len(day_rows) == len(universe) * 24
            and set(counts) == selected
            and all(counts[instrument] == 24 for instrument in universe)
            and {int(row["event_time_ms"]) for row in day_rows} == expected_times
            and all(
                row.get("external_price_alignment") == "PASS"
                and row.get("dataset") == DATASET
                for row in day_rows
            )
        ):
            complete_days.append(day_ms)
    horizon_ms = horizon_hours * HOUR_MS
    decision_times = tuple(
        event_time_ms
        for event_time_ms in sorted(by_time)
        if event_time_ms % horizon_ms == 0
        and by_time[event_time_ms] == selected
        and by_time.get(event_time_ms + horizon_ms) == selected
    )
    return {
        "complete_day_count": len(complete_days),
        "complete_day_ratio": len(complete_days)
        / ((end_exclusive_ms - start_day_ms) // DAY_MS),
        "actual_adjacent_24h_decision_count": len(decision_times),
        "first_capacity_decision_event_time_ms": (
            decision_times[0] if decision_times else None
        ),
        "last_capacity_decision_event_time_ms": (
            decision_times[-1] if decision_times else None
        ),
        "confirmation_outcome_accessed": False,
    }


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
    maximum_quality_excluded_days: int = 0,
    minimum_snapshots_per_archive: int | None = None,
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
    if (
        isinstance(maximum_quality_excluded_days, bool)
        or not isinstance(maximum_quality_excluded_days, int)
        or maximum_quality_excluded_days < 0
        or maximum_quality_excluded_days > days
    ):
        raise ValueError("quality exclusion policy is invalid")
    if (
        minimum_snapshots_per_archive is not None
        and (
            isinstance(minimum_snapshots_per_archive, bool)
            or not isinstance(minimum_snapshots_per_archive, int)
            or minimum_snapshots_per_archive < 1
        )
    ):
        raise ValueError("archive snapshot floor override is invalid")
    selected = tuple(str(value).upper().strip() for value in universe)
    if len(selected) != 14 or len(set(selected)) != 14:
        raise ValueError("backfill requires the fixed 14-symbol universe")
    selected_days = tuple(
        closed_day_ms - offset * DAY_MS for offset in reversed(range(days))
    )
    completed = skipped = inserted_hours = 0
    processed_days: list[int] = []
    quality_exclusions: list[dict[str, object]] = []
    for day_ms in selected_days:
        if _day_complete(store_path, day_ms, selected):
            skipped += 1
            processed_days.append(day_ms)
            continue
        try:
            refresh_kwargs: dict[str, object] = {
                "observed_at_ms": observed_at_ms,
                "store_path": store_path,
                "archive_root": archive_root,
                "max_workers": max_workers,
            }
            if minimum_snapshots_per_archive is not None:
                refresh_kwargs["minimum_snapshots_per_archive"] = (
                    minimum_snapshots_per_archive
                )
            report = refresh(selected, (day_ms,), **refresh_kwargs)
            if report.get("external_price_alignment") != "PASS":
                raise RuntimeError("backfill day lacks external price alignment")
            if not _day_complete(store_path, day_ms, selected):
                raise RuntimeError("backfill day is not an exact authority grid")
        except BinanceBookDepthQualityRejected as exc:
            quality_exclusions.append(
                {"day_ms": day_ms, "failures": [dict(item) for item in exc.failures]}
            )
            if len(quality_exclusions) > maximum_quality_excluded_days:
                failure = _progress_report(
                    observed_at_ms=observed_at_ms,
                    requested_days=days,
                    completed_days=completed,
                    skipped_days=skipped,
                    inserted_hours=inserted_hours,
                    processed_days=processed_days,
                    status="FAIL",
                    quality_exclusions=quality_exclusions,
                    maximum_quality_excluded_days=maximum_quality_excluded_days,
                )
                failure["failed_day_ms"] = day_ms
                failure["failure_type"] = "QUALITY_EXCLUSION_LIMIT_EXCEEDED"
                atomic_write_json(status_path, failure, indent=2)
                raise BookDepthBackfillError(
                    "confirmation quality exclusion limit exceeded"
                ) from exc
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
                    quality_exclusions=quality_exclusions,
                    maximum_quality_excluded_days=maximum_quality_excluded_days,
                ),
                indent=2,
            )
            continue
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
                "quality_excluded_day_count": len(quality_exclusions),
                "quality_exclusions": quality_exclusions,
                "maximum_quality_excluded_days": maximum_quality_excluded_days,
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
                quality_exclusions=quality_exclusions,
                maximum_quality_excluded_days=maximum_quality_excluded_days,
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
        quality_exclusions=quality_exclusions,
        maximum_quality_excluded_days=maximum_quality_excluded_days,
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
    quality_policy: dict[str, object] | None = None,
    quality_policy_commit: str | None = None,
    quality_policy_sha256: str | None = None,
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
    if not isinstance(quality_policy, dict):
        raise BookDepthBackfillError("confirmation quality policy is missing")
    if (
        quality_policy.get("status") != "FROZEN_BEFORE_CONFIRMATION_OUTCOME"
        or quality_policy.get("confirmation_study_id") != contract.get("study_id")
        or quality_policy.get("exclusion_unit")
        != "ENTIRE_UTC_DAY_ALL_14_INSTRUMENTS"
    ):
        raise BookDepthBackfillError("confirmation quality policy is invalid")
    maximum_quality_excluded_days = int(
        quality_policy["maximum_quality_excluded_days"]
    )
    if contract.get("dataset_id") != DATASET:
        raise BookDepthBackfillError("confirmation dataset is invalid")
    window = contract.get("confirmation_window")
    unseen = contract.get("frozen_unseen_window_evidence")
    if not isinstance(window, dict) or not isinstance(unseen, dict):
        raise BookDepthBackfillError("confirmation window evidence is missing")
    start_day_ms = int(window["start_day_ms"])
    end_exclusive_ms = int(window["end_exclusive_ms"])
    day_count = int(window["day_count"])
    policy_schema_version = int(quality_policy.get("schema_version", 1))
    capacity_policy = policy_schema_version in {3, 4}
    minimum_snapshots_per_archive = None
    if capacity_policy:
        terminal = quality_policy.get("terminal_acceptance")
        minimum_complete_ratio = float(
            quality_policy["minimum_complete_day_ratio"]
        )
        minimum_complete_days = int(quality_policy["minimum_complete_days"])
        minimum_decisions = int(quality_policy["minimum_confirmation_decisions"])
        if (
            quality_policy.get("window_day_count") != day_count
            or not 0 < minimum_complete_ratio <= 1
            or minimum_complete_days < math.ceil(day_count * minimum_complete_ratio)
            or minimum_complete_days > day_count
            or minimum_decisions != int(contract["minimum_decisions"])
            or not isinstance(terminal, dict)
            or not terminal
            or not all(value is True for value in terminal.values())
        ):
            raise BookDepthBackfillError(
                "confirmation statistical capacity policy is invalid"
            )
    if policy_schema_version == 4:
        coverage = quality_policy.get("coverage_decision")
        hard_gates = quality_policy.get("unchanged_hard_gates")
        if (
            not isinstance(coverage, dict)
            or coverage.get("daily_total_snapshot_floor_is_admission_gate")
            is not False
            or coverage.get("daily_total_snapshot_count_is_retained_diagnostic")
            is not True
            or coverage.get("all_24_utc_hours_required") is not True
            or coverage.get("minimum_hourly_snapshot_count") != 96
            or coverage.get("minimum_hourly_coverage_ratio") != 0.8
            or not isinstance(hard_gates, dict)
            or hard_gates.get("official_archive_checksum_required") is not True
            or hard_gates.get("exact_symmetric_depth_levels_required") is not True
            or hard_gates.get("maximum_inverted_snapshot_ratio") != 0.002
            or hard_gates.get(
                "all_hours_must_align_to_checksum_verified_mark_prices"
            ) is not True
            or hard_gates.get("maximum_mark_alignment_deviation_ratio") != 0.01
            or hard_gates.get("network_or_timeout_is_excludable") is not False
            or hard_gates.get("checksum_mismatch_is_excludable") is not False
            or hard_gates.get("unexpected_exception_is_excludable") is not False
        ):
            raise BookDepthBackfillError(
                "confirmation estimator-aligned quality policy is invalid"
            )
        minimum_snapshots_per_archive = 1
    schema_version = int(contract.get("schema_version", 1))
    if schema_version == 1:
        relation_valid = (
            window.get("relation_to_exploration")
            == "STRICTLY_EARLIER_NON_OVERLAPPING"
            and end_exclusive_ms
            == int(window["exploration_start_exclusive_ms"])
        )
    elif schema_version == 2:
        relation_valid = (
            window.get("relation_to_prior_confirmation")
            == "STRICTLY_EARLIER_NON_OVERLAPPING"
            and end_exclusive_ms
            == int(window["prior_confirmation_start_exclusive_ms"])
        )
    else:
        relation_valid = False
    if (
        start_day_ms < 0
        or start_day_ms % DAY_MS
        or end_exclusive_ms != start_day_ms + day_count * DAY_MS
        or day_count < 1
        or day_count > 365
        or not relation_valid
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
        maximum_quality_excluded_days=maximum_quality_excluded_days,
        minimum_snapshots_per_archive=minimum_snapshots_per_archive,
    )
    bound_result = {
        **result,
        "mode": "L2_CONTRACT_BOUND_UNSEEN_CONFIRMATION_BACKFILL",
        "contract_study_id": contract["study_id"],
        "contract_commit": contract_commit,
        "contract_sha256": contract_sha256,
        "quality_policy_commit": quality_policy_commit,
        "quality_policy_sha256": quality_policy_sha256,
        "contract_frozen_at_ms": frozen_at_ms,
        "preexisting_confirmation_observations": len(existing),
        "confirmation_window_start_ms": start_day_ms,
        "confirmation_window_end_exclusive_ms": end_exclusive_ms,
    }
    if capacity_policy:
        capacity = _confirmation_capacity(
            store_path=store_path,
            start_day_ms=start_day_ms,
            end_exclusive_ms=end_exclusive_ms,
            universe=selected,
            horizon_hours=int(contract["horizon_hours"]),
        )
        capacity_pass = (
            result["processed_day_count"] == day_count
            and int(capacity["complete_day_count"]) >= minimum_complete_days
            and float(capacity["complete_day_ratio"]) >= minimum_complete_ratio
            and int(capacity["actual_adjacent_24h_decision_count"])
            >= minimum_decisions
        )
        bound_result.update(capacity)
        bound_result["data_capacity_status"] = "PASS" if capacity_pass else "FAIL"
        bound_result["minimum_complete_days"] = minimum_complete_days
        bound_result["minimum_complete_day_ratio"] = minimum_complete_ratio
        bound_result["minimum_confirmation_decisions"] = minimum_decisions
        bound_result["daily_total_snapshot_floor_is_admission_gate"] = (
            minimum_snapshots_per_archive is None
        )
        if not capacity_pass:
            bound_result["status"] = "FAIL"
            bound_result["failure_type"] = (
                "INSUFFICIENT_CONFIRMATION_DATA_CAPACITY"
            )
            atomic_write_json(status_path, bound_result, indent=2)
            raise BookDepthBackfillError(
                "confirmation statistical capacity is insufficient"
            )
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
    quality_exclusions: Sequence[dict[str, object]] = (),
    maximum_quality_excluded_days: int = 0,
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
        "quality_excluded_day_count": len(quality_exclusions),
        "quality_exclusions": list(quality_exclusions),
        "maximum_quality_excluded_days": maximum_quality_excluded_days,
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
    parser.add_argument(
        "--confirmation-contract-v2",
        action="store_true",
        help="Use the exact immutable second confirmation contract and policy",
    )
    parser.add_argument(
        "--confirmation-contract-v3",
        action="store_true",
        help=(
            "Use the first immutable confirmation contract with the frozen "
            "statistical-capacity quality policy"
        ),
    )
    parser.add_argument(
        "--confirmation-contract-v4",
        action="store_true",
        help=(
            "Use the first immutable confirmation contract with the frozen "
            "estimator-aligned quality policy"
        ),
    )
    args = parser.parse_args(argv)
    if sum(
        bool(value)
        for value in (
            args.confirmation_contract,
            args.confirmation_contract_v2,
            args.confirmation_contract_v3,
            args.confirmation_contract_v4,
        )
    ) > 1:
        parser.error("confirmation contract entrypoints are mutually exclusive")
    now_ms = int(time.time() * 1000)
    if args.confirmation_contract_v4:
        contract_sha256, contract, quality_policy_sha256, quality_policy = (
            _verify_confirmation_v4_contracts()
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
            quality_policy=quality_policy,
            quality_policy_commit=CONFIRMATION_QUALITY_POLICY_V4_COMMIT,
            quality_policy_sha256=quality_policy_sha256,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.confirmation_contract_v3:
        contract_sha256, contract, quality_policy_sha256, quality_policy = (
            _verify_confirmation_v3_contracts()
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
            quality_policy=quality_policy,
            quality_policy_commit=CONFIRMATION_QUALITY_POLICY_V3_COMMIT,
            quality_policy_sha256=quality_policy_sha256,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.confirmation_contract_v2:
        contract_sha256, contract, quality_policy_sha256, quality_policy = (
            _verify_confirmation_v2_contracts()
        )
        result = run_contract_backfill(
            contract=contract,
            observed_at_ms=now_ms,
            store_path=args.store,
            archive_root=args.archive_root,
            status_path=args.status,
            max_workers=args.workers,
            contract_commit=CONFIRMATION_V2_COMMIT,
            contract_sha256=contract_sha256,
            quality_policy=quality_policy,
            quality_policy_commit=CONFIRMATION_V2_COMMIT,
            quality_policy_sha256=quality_policy_sha256,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.confirmation_contract:
        contract_sha256, contract = _verify_confirmation_contract(
            args.confirmation_contract
        )
        quality_policy_sha256, quality_policy = _verify_confirmation_quality_policy(
            DEFAULT_CONFIRMATION_QUALITY_POLICY
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
            quality_policy=quality_policy,
            quality_policy_commit=CONFIRMATION_QUALITY_POLICY_COMMIT,
            quality_policy_sha256=quality_policy_sha256,
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
    "CONFIRMATION_QUALITY_POLICY_COMMIT",
    "CONFIRMATION_QUALITY_POLICY_V3_COMMIT",
    "CONFIRMATION_QUALITY_POLICY_V4_COMMIT",
    "CONFIRMATION_V2_COMMIT",
    "DEFAULT_CONFIRMATION_CONTRACT",
    "DEFAULT_CONFIRMATION_CONTRACT_V2",
    "DEFAULT_CONFIRMATION_QUALITY_POLICY",
    "DEFAULT_CONFIRMATION_QUALITY_POLICY_V2",
    "DEFAULT_CONFIRMATION_QUALITY_POLICY_V3",
    "DEFAULT_CONFIRMATION_QUALITY_POLICY_V4",
    "DEFAULT_STATUS",
    "UNIVERSE",
    "main",
    "run_backfill",
    "run_contract_backfill",
]
