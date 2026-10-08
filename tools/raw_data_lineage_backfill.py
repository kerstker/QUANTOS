"""Refetch and reconcile recent forward-data responses into the raw archive.

REST gap-fill days (cache records marked REST_GAP_FILL, see tools/binance_rest_gap_fill.py)
cannot be refetched from the Data Vision archive — that is exactly why they are placeholders.
They are verified against the REST source they came from (live refetch + immutable per-day
replay from the raw archive's dedicated REST dataset ids) with the SAME reconciliation bar:
provenance changes the verification source, never the strictness."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       load_archived_response,
                                       load_archived_responses,
                                       summarize_indexed_archive)
from tools.binance_funding_fetch import fetch_daily_funding
from tools.binance_oi_metrics_fetch import (FORWARD_MAJOR_TICKERS,
                                            fetch_ticker_days,
                                            parse_metrics_zip)
from tools.binance_perp_mark_fetch import (fetch_mark_days,
                                           load_mark_prices,
                                           parse_mark_price_zip)
from tools.binance_rest_gap_fill import (PROVENANCE_REST_GAP_FILL,
                                         REST_DATASET_MARK,
                                         REST_DATASET_OI,
                                         fetch_mark_days_rest,
                                         fetch_oi_days_rest,
                                         parse_mark_rest_day,
                                         parse_oi_rest_day)
from tools.cache_io import atomic_write_json
from tools.source_failures import source_failure_detail

_DAY_MS = 86_400_000
_MARK = os.path.join("data", "cache", "crypto_perp_mark_prices.json")
_FUNDING = os.path.join("data", "cache", "crypto_funding_rates.json")
_OI = os.path.join("data", "cache", "crypto_oi_metrics.json")
_OUTPUT = os.path.join("evidence", "raw_data_lineage_backfill.json")
RAW_LINEAGE_BACKFILL_DATASETS = frozenset({
    "funding",
    "oi_metrics",
    REST_DATASET_OI,
    "perp_mark_prices",
    REST_DATASET_MARK,
})


class LineageBackfillError(RuntimeError):
    pass


def _load_object(path: str, *, label: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LineageBackfillError(f"{label} cache is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise LineageBackfillError(f"{label} cache root must be an object")
    return payload


def _artifact(path: str) -> dict:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": path.replace("\\", "/"),
        "size_bytes": os.path.getsize(path),
        "sha256": digest.hexdigest(),
    }


def reanchor_backfill_archive(
    *,
    evidence_path: str = _OUTPUT,
    now_ms: int | None = None,
    archive_root: str | None = None,
    mark_path: str = _MARK,
    funding_path: str = _FUNDING,
    oi_path: str = _OI,
    archive_summary=summarize_indexed_archive,
) -> dict:
    """Refresh only the archive witness of a completed backfill.

    The network reconciliation remains immutable evidence.  This operation is
    permitted only when every retained source report passed, the canonical
    cache artifacts are byte-identical, and the scope is unchanged.  It does
    not invoke any fetcher and therefore cannot make a newly completed full
    archive audit stale again.
    """

    payload = _load_object(evidence_path, label="lineage backfill evidence")
    if (
        payload.get("schema_version") != 1
        or payload.get("readiness") != "L2_DRY_RUN_ONLY"
        or payload.get("purpose") != "RECENT_FORWARD_RAW_LINEAGE_BACKFILL"
    ):
        raise LineageBackfillError("lineage backfill evidence schema is invalid")
    previous_generated_at_ms = payload.get("generated_at_ms")
    if (
        isinstance(previous_generated_at_ms, bool)
        or not isinstance(previous_generated_at_ms, int)
        or previous_generated_at_ms < 0
    ):
        raise LineageBackfillError("lineage backfill evidence time is invalid")
    reanchored_at_ms = int(time.time() * 1_000) if now_ms is None else now_ms
    if (
        isinstance(reanchored_at_ms, bool)
        or not isinstance(reanchored_at_ms, int)
        or reanchored_at_ms < previous_generated_at_ms
    ):
        raise LineageBackfillError("archive reanchor time is invalid")

    scope = payload.get("instrument_scope")
    if (
        not isinstance(scope, list)
        or not scope
        or any(not isinstance(item, str) or not item for item in scope)
        or scope != sorted(set(scope))
    ):
        raise LineageBackfillError("lineage backfill scope is invalid")
    required_days = payload.get("required_days")
    if (
        not isinstance(required_days, list)
        or not required_days
        or any(isinstance(item, bool) or not isinstance(item, int) for item in required_days)
        or required_days != sorted(set(required_days))
    ):
        raise LineageBackfillError("lineage backfill days are invalid")
    expected_lineage_scope = {
        "dataset_ids": sorted(RAW_LINEAGE_BACKFILL_DATASETS),
        "instrument_ids": scope,
    }
    if payload.get("raw_lineage_scope") != expected_lineage_scope:
        raise LineageBackfillError("lineage backfill archive scope is invalid")

    try:
        current_artifacts = {
            "prices": _artifact(mark_path),
            "funding": _artifact(funding_path),
            "oi_metrics": _artifact(oi_path),
        }
    except OSError as exc:
        raise LineageBackfillError(
            "lineage backfill artifact is unreadable"
        ) from exc
    retained_artifacts = payload.get("artifacts")
    if not isinstance(retained_artifacts, dict):
        raise LineageBackfillError("lineage backfill artifacts are missing")
    for name, current in current_artifacts.items():
        retained = retained_artifacts.get(name)
        if not isinstance(retained, dict) or any(
            retained.get(field) != current[field]
            for field in ("path", "size_bytes", "sha256")
        ):
            raise LineageBackfillError(
                f"lineage backfill artifact changed: {name}"
            )

    sources = payload.get("sources")
    if not isinstance(sources, dict):
        raise LineageBackfillError("lineage backfill sources are missing")
    for name in ("perp_mark_prices", "funding", "oi_metrics"):
        source = sources.get(name)
        attempted = source.get("attempted_records") if isinstance(source, dict) else None
        matched = source.get("matched_records") if isinstance(source, dict) else None
        if (
            not isinstance(source, dict)
            or source.get("passed") is not True
            or isinstance(attempted, bool)
            or not isinstance(attempted, int)
            or attempted <= 0
            or matched != attempted
            or source.get("failures") not in ([], ())
            or source.get("failed_tickers") not in ([], ())
        ):
            raise LineageBackfillError(
                f"lineage backfill source did not pass: {name}"
            )

    retained_archive_root = payload.get("archive_root")
    resolved_archive_root = (
        retained_archive_root if archive_root is None else archive_root
    )
    if not isinstance(resolved_archive_root, str) or not resolved_archive_root:
        raise LineageBackfillError("lineage backfill archive root is invalid")
    if (
        archive_root is not None
        and archive_root.replace("\\", "/") != retained_archive_root
    ):
        raise LineageBackfillError("lineage backfill archive root changed")
    try:
        raw_lineage = archive_summary(
            resolved_archive_root,
            dataset_ids=RAW_LINEAGE_BACKFILL_DATASETS,
            instrument_ids=frozenset(scope),
            now_ms=reanchored_at_ms,
        )
    except Exception as exc:
        raise LineageBackfillError("archive summary is unavailable") from exc
    if not isinstance(raw_lineage, dict):
        raise LineageBackfillError("archive summary is invalid")
    required_datasets = {"funding", "oi_metrics", "perp_mark_prices"}
    datasets = raw_lineage.get("datasets")
    missing_datasets = sorted(
        required_datasets - (set(datasets) if isinstance(datasets, dict) else set())
    )
    aggregate_sha256 = raw_lineage.get("aggregate_sha256")
    valid_pass_summary = (
        isinstance(aggregate_sha256, str)
        and len(aggregate_sha256) == 64
        and all(character in "0123456789abcdef" for character in aggregate_sha256)
        and raw_lineage.get("errors") in ([], ())
        and not missing_datasets
    )
    if raw_lineage.get("status") == "PASS" and not valid_pass_summary:
        raise LineageBackfillError("archive summary PASS evidence is invalid")
    block_reasons = []
    if raw_lineage.get("status") != "PASS":
        block_reasons.append(f"ARCHIVE:{raw_lineage.get('status')}")
    if missing_datasets:
        block_reasons.append(f"ARCHIVE_MISSING_DATASETS:{missing_datasets}")

    report = dict(payload)
    report.update(
        {
            "generated_at_ms": reanchored_at_ms,
            "raw_lineage": raw_lineage,
            "verdict": "PASS" if not block_reasons else "BLOCK",
            "block_reasons": block_reasons,
            "archive_witness_reanchor": {
                "mode": "OFFLINE_NO_REFETCH",
                "previous_generated_at_ms": previous_generated_at_ms,
                "reanchored_at_ms": reanchored_at_ms,
            },
        }
    )
    return report


def _equivalent(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(float(left)) and math.isfinite(float(right)) and math.isclose(
            float(left), float(right), rel_tol=1e-12, abs_tol=1e-12
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _equivalent(left[key], right[key]) for key in left
        )
    return left == right


def _strip_provenance(record):
    """Reconciliation compares canonical fields only: the "source" marker is provenance
    metadata, not data (archive rows simply lack it)."""
    if isinstance(record, dict):
        return {key: value for key, value in record.items() if key != "source"}
    return record


def _comparison_failure(ticker: str, day: int, kind: str, message: str) -> dict:
    return {
        "ticker": ticker,
        "day_ms": int(day),
        "kind": kind,
        "error_type": "LineageReconciliationError",
        "message": message,
    }


def _finish_source(report: dict) -> dict:
    report["failures"] = sorted(
        report["failures"],
        key=lambda item: (
            str(item.get("ticker")), int(item.get("day_ms", -1)),
            str(item.get("kind")),
        ),
    )
    report["failed_tickers"] = sorted({
        item["ticker"] for item in report["failures"] if item.get("ticker")
    })
    report["warnings"] = sorted(
        report.get("warnings", []),
        key=lambda item: (
            str(item.get("ticker")), int(item.get("day_ms", -1)),
            str(item.get("kind")),
        ),
    )
    report["passed"] = (
        not report["failures"]
        and report["matched_records"] == report["attempted_records"]
    )
    return report


def _replay_missing(
    *,
    archive_root: str,
    dataset_id: str,
    ticker: str,
    days: list[int],
    observed: dict,
    failures: list[dict],
    parser,
    archive_responses: dict | None = None,
) -> tuple[dict, list[dict], list[dict], int]:
    replayed_days = set()
    replay_failures = []
    for day in days:
        if day in observed:
            continue
        try:
            key = (dataset_id, ticker, day)
            archived = (
                archive_responses.get(key)
                if archive_responses is not None
                else load_archived_response(
                    archive_root,
                    dataset_id=dataset_id,
                    instrument_id=ticker,
                    event_time_ms=day,
                )
            )
            if archived is None:
                continue
            observed[day] = parser(archived["payload"], day)
            replayed_days.add(day)
        except Exception as exc:
            replay_failures.append(_comparison_failure(
                ticker, day, "ARCHIVE_REPLAY_FAILED",
                f"archived response could not be replayed: {type(exc).__name__}: {exc}",
            ))

    warnings = []
    remaining = []
    all_days_replayed = bool(days) and replayed_days == set(days)
    for failure in failures:
        failure_day = failure.get("day_ms")
        if failure_day in replayed_days or (failure_day is None and all_days_replayed):
            warning = dict(failure)
            warning["resolved_by"] = "ARCHIVE_REPLAY"
            warnings.append(warning)
        else:
            remaining.append(failure)
    remaining.extend(replay_failures)
    return observed, remaining, warnings, len(replayed_days)


def _reconcile(report: dict, ticker: str, days: list[int], expected: dict,
               observed: dict) -> None:
    for day in days:
        if day not in expected:
            report["failures"].append(_comparison_failure(
                ticker, day, "CANONICAL_MISSING",
                "canonical cache has no required record",
            ))
        elif day not in observed:
            report["failures"].append(_comparison_failure(
                ticker, day, "REFETCH_MISSING",
                "refetch did not reproduce the canonical record",
            ))
        elif not _equivalent(expected[day], observed[day]):
            report["failures"].append(_comparison_failure(
                ticker, day, "VALUE_MISMATCH",
                "refetched value differs from the canonical cache",
            ))
        else:
            report["matched_records"] += 1


def run_backfill(
    *,
    now_ms: int | None = None,
    instruments=None,
    lookback_days: int = 8,
    archive_root: str = DEFAULT_RAW_ARCHIVE,
    mark_path: str = _MARK,
    funding_path: str = _FUNDING,
    oi_path: str = _OI,
    mark_fetch=fetch_mark_days,
    oi_fetch=fetch_ticker_days,
    funding_fetch=fetch_daily_funding,
    rest_mark_fetch=fetch_mark_days_rest,
    rest_oi_fetch=fetch_oi_days_rest,
    funding_workers: int = 8,
    archive_summary=summarize_indexed_archive,
) -> dict:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if funding_workers <= 0:
        raise ValueError("funding_workers must be positive")
    scope = sorted(set(instruments or FORWARD_MAJOR_TICKERS))
    expected_end = (int(now_ms) // _DAY_MS) * _DAY_MS - _DAY_MS
    days = [
        expected_end - offset * _DAY_MS
        for offset in reversed(range(lookback_days))
    ]
    attempted = len(scope) * len(days)

    mark_cache = load_mark_prices(mark_path)
    mark_raw = _load_object(mark_path, label="mark")
    mark_provenance = mark_raw.get("provenance")
    if not isinstance(mark_provenance, dict):
        mark_provenance = {}
    funding_raw = _load_object(funding_path, label="funding")
    funding_cache = {
        ticker: {int(day): value for day, value in values.items()}
        for ticker, values in funding_raw.items()
        if isinstance(values, dict)
    }
    oi_raw = _load_object(oi_path, label="OI")
    oi_data = oi_raw.get("data")
    if not isinstance(oi_data, dict):
        raise LineageBackfillError("OI cache data must be an object")
    oi_provenance = {
        ticker: {int(day): record.get("source")
                 for day, record in values.items() if isinstance(record, dict)}
        for ticker, values in oi_data.items() if isinstance(values, dict)
    }
    oi_cache = {
        ticker: {int(day): _strip_provenance(value)
                 for day, value in values.items()}
        for ticker, values in oi_data.items()
        if isinstance(values, dict)
    }
    replay_datasets = (
        "perp_mark_prices", REST_DATASET_MARK,
        "oi_metrics", REST_DATASET_OI,
    )
    archive_responses = load_archived_responses(
        archive_root,
        requests={
            (dataset_id, ticker, day)
            for dataset_id in replay_datasets
            for ticker in scope
            for day in days
        },
    )

    mark_report = {
        "source": "perp_mark_prices", "attempted_records": attempted,
        "matched_records": 0, "refetched_records": 0, "replayed_records": 0,
        "rest_refetched_records": 0, "rest_replayed_records": 0,
        "failures": [], "warnings": [],
    }
    for ticker in scope:
        provenance = mark_provenance.get(ticker, {})
        archive_days = [
            day for day in days
            if provenance.get(str(day)) != PROVENANCE_REST_GAP_FILL
        ]
        rest_days = [
            day for day in days
            if provenance.get(str(day)) == PROVENANCE_REST_GAP_FILL
        ]
        observed, failures, warnings, replayed = _replay_missing(
            archive_root=archive_root,
            dataset_id="perp_mark_prices",
            ticker=ticker,
            days=archive_days,
            observed={},
            failures=[],
            parser=lambda payload, day: parse_mark_price_zip(
                payload, expected_day=day
            ),
            archive_responses=archive_responses,
        )
        mark_report["replayed_records"] += replayed
        mark_report["warnings"].extend(warnings)
        missing_archive_days = [day for day in archive_days if day not in observed]
        if missing_archive_days:
            try:
                observed.update(mark_fetch(
                    ticker, missing_archive_days, archive_root=archive_root,
                    observed_at_ms=int(now_ms), failure_sink=failures,
                ))
            except Exception as exc:
                failures.append(source_failure_detail(
                    ticker, exc, phase="lineage_backfill_mark"
                ))
        mark_report["refetched_records"] += len(
            set(missing_archive_days).intersection(observed)
        )
        mark_report["failures"].extend(failures)
        _reconcile(
            mark_report, ticker, archive_days, mark_cache.get(ticker, {}), observed
        )
        if rest_days:
            rest_observed, rest_failures, rest_warnings, rest_replayed = _replay_missing(
                archive_root=archive_root,
                dataset_id=REST_DATASET_MARK,
                ticker=ticker,
                days=rest_days,
                observed={},
                failures=[],
                parser=parse_mark_rest_day,
                archive_responses=archive_responses,
            )
            mark_report["rest_replayed_records"] += rest_replayed
            mark_report["warnings"].extend(rest_warnings)
            missing_rest_days = [day for day in rest_days if day not in rest_observed]
            if missing_rest_days:
                try:
                    rest_observed.update(rest_mark_fetch(
                        ticker, missing_rest_days, now_ms=int(now_ms),
                        archive_root=archive_root, observed_at_ms=int(now_ms),
                        failure_sink=rest_failures,
                    ))
                except Exception as exc:
                    rest_failures.append(source_failure_detail(
                        ticker, exc, phase="lineage_backfill_mark_rest"
                    ))
            mark_report["rest_refetched_records"] += len(
                set(missing_rest_days).intersection(rest_observed)
            )
            mark_report["failures"].extend(rest_failures)
            _reconcile(
                mark_report, ticker, rest_days,
                mark_cache.get(ticker, {}), rest_observed,
            )
    _finish_source(mark_report)

    oi_report = {
        "source": "oi_metrics", "attempted_records": attempted,
        "matched_records": 0, "refetched_records": 0, "replayed_records": 0,
        "rest_refetched_records": 0, "rest_replayed_records": 0,
        "failures": [], "warnings": [],
    }
    for ticker in scope:
        provenance = oi_provenance.get(ticker, {})
        archive_days = [
            day for day in days
            if provenance.get(day) != PROVENANCE_REST_GAP_FILL
        ]
        rest_days = [
            day for day in days
            if provenance.get(day) == PROVENANCE_REST_GAP_FILL
        ]
        observed, failures, warnings, replayed = _replay_missing(
            archive_root=archive_root,
            dataset_id="oi_metrics",
            ticker=ticker,
            days=archive_days,
            observed={},
            failures=[],
            parser=lambda payload, _day: parse_metrics_zip(payload),
            archive_responses=archive_responses,
        )
        oi_report["replayed_records"] += replayed
        oi_report["warnings"].extend(warnings)
        missing_archive_days = [day for day in archive_days if day not in observed]
        if missing_archive_days:
            try:
                fetched = oi_fetch(
                    ticker, missing_archive_days[0],
                    missing_archive_days[-1] + _DAY_MS, set(observed),
                    archive_root=archive_root, observed_at_ms=int(now_ms),
                    failure_sink=failures,
                )
                observed.update({
                    day: fetched[day]
                    for day in missing_archive_days if day in fetched
                })
            except Exception as exc:
                failures.append(source_failure_detail(
                    ticker, exc, phase="lineage_backfill_oi"
                ))
        oi_report["refetched_records"] += len(
            set(missing_archive_days).intersection(observed)
        )
        oi_report["failures"].extend(failures)
        _reconcile(
            oi_report, ticker, archive_days, oi_cache.get(ticker, {}), observed
        )
        if rest_days:
            rest_observed, rest_failures, rest_warnings, rest_replayed = _replay_missing(
                archive_root=archive_root,
                dataset_id=REST_DATASET_OI,
                ticker=ticker,
                days=rest_days,
                observed={},
                failures=[],
                parser=parse_oi_rest_day,
                archive_responses=archive_responses,
            )
            oi_report["rest_replayed_records"] += rest_replayed
            oi_report["warnings"].extend(rest_warnings)
            missing_rest_days = [day for day in rest_days if day not in rest_observed]
            if missing_rest_days:
                try:
                    rest_observed.update(rest_oi_fetch(
                        ticker, missing_rest_days, now_ms=int(now_ms),
                        archive_root=archive_root, observed_at_ms=int(now_ms),
                        failure_sink=rest_failures,
                    ))
                except Exception as exc:
                    rest_failures.append(source_failure_detail(
                        ticker, exc, phase="lineage_backfill_oi_rest"
                    ))
            oi_report["rest_refetched_records"] += len(
                set(missing_rest_days).intersection(rest_observed)
            )
            oi_report["failures"].extend(rest_failures)
            _reconcile(
                oi_report, ticker, rest_days,
                oi_cache.get(ticker, {}), rest_observed,
            )
    _finish_source(oi_report)

    funding_report = {
        "source": "funding", "attempted_records": attempted,
        "matched_records": 0, "refetched_records": 0, "replayed_records": 0,
        "failures": [], "warnings": [],
    }

    def fetch_funding(ticker):
        try:
            return ticker, funding_fetch(
                ticker,
                days=lookback_days + 3,
                now_ms=int(now_ms),
                archive_root=archive_root,
                observed_at_ms=int(now_ms),
            ), None
        except Exception as exc:
            return ticker, {}, source_failure_detail(
                ticker, exc, phase="lineage_backfill_funding"
            )

    with ThreadPoolExecutor(max_workers=funding_workers) as executor:
        funding_results = list(executor.map(fetch_funding, scope))
    for ticker, observed, failure in funding_results:
        funding_report["refetched_records"] += len(set(days).intersection(observed))
        if failure is not None:
            funding_report["failures"].append(failure)
        _reconcile(
            funding_report, ticker, days, funding_cache.get(ticker, {}), observed
        )
    _finish_source(funding_report)

    sources = {
        "perp_mark_prices": mark_report,
        "oi_metrics": oi_report,
        "funding": funding_report,
    }
    lineage_scope = {
        "dataset_ids": sorted(RAW_LINEAGE_BACKFILL_DATASETS),
        "instrument_ids": scope,
    }
    raw_lineage = archive_summary(
        archive_root,
        dataset_ids=RAW_LINEAGE_BACKFILL_DATASETS,
        instrument_ids=frozenset(scope),
    )
    required_datasets = set(sources)
    missing_datasets = sorted(required_datasets - set(raw_lineage.get("datasets", {})))
    block_reasons = [
        f"SOURCE:{name}" for name, report in sources.items() if not report["passed"]
    ]
    if raw_lineage["status"] != "PASS":
        block_reasons.append(f"ARCHIVE:{raw_lineage['status']}")
    if missing_datasets:
        block_reasons.append(f"ARCHIVE_MISSING_DATASETS:{missing_datasets}")
    return {
        "schema_version": 1,
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "RECENT_FORWARD_RAW_LINEAGE_BACKFILL",
        "generated_at_ms": int(now_ms),
        "instrument_scope": scope,
        "required_days": days,
        "archive_root": archive_root.replace("\\", "/"),
        "artifacts": {
            "prices": _artifact(mark_path),
            "funding": _artifact(funding_path),
            "oi_metrics": _artifact(oi_path),
        },
        "sources": sources,
        "raw_lineage_scope": lineage_scope,
        "raw_lineage": raw_lineage,
        "verdict": "PASS" if not block_reasons else "BLOCK",
        "block_reasons": block_reasons,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=_OUTPUT)
    parser.add_argument("--now-ms", type=int)
    parser.add_argument("--lookback-days", type=int, default=8)
    parser.add_argument("--funding-workers", type=int, default=8)
    parser.add_argument(
        "--offline-reanchor",
        action="store_true",
        help="refresh only a byte-identical backfill's archive witness",
    )
    args = parser.parse_args(argv)
    if args.offline_reanchor:
        try:
            report = reanchor_backfill_archive(
                evidence_path=args.output,
                now_ms=args.now_ms,
            )
        except LineageBackfillError as exc:
            print(f"raw-lineage offline reanchor: BLOCK ({exc})")
            return 2
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        atomic_write_json(args.output, report, indent=2)
        print(f"raw-lineage offline reanchor: {report['verdict']}")
        return 0 if report["verdict"] == "PASS" else 2
    report = run_backfill(
        now_ms=args.now_ms,
        lookback_days=args.lookback_days,
        funding_workers=args.funding_workers,
    )
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    atomic_write_json(args.output, report, indent=2)
    print(
        f"raw-lineage backfill: {report['verdict']} "
        f"(scope={len(report['instrument_scope'])}, days={len(report['required_days'])})"
    )
    for name, source in report["sources"].items():
        print(
            f"  {name}: matched={source['matched_records']}/"
            f"{source['attempted_records']} failures={len(source['failures'])}"
        )
    print(f"-> {args.output}")
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
