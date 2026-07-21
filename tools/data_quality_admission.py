"""Build a reproducible, read-only admission manifest for forward data artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time

from services.data.admission_store import (
    DataQualityAdmissionDecision,
    DataQualityAdmissionStore,
    DataQualityAdmissionStoreError,
)
from services.data.quality import build_ingestion_report
from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       summarize_archive)
from tools.binance_funding_fetch import FundingDataError, validate_daily_funding
from tools.binance_oi_metrics_fetch import DEFAULT_TICKERS
from tools.binance_perp_mark_fetch import (load_mark_prices,
                                           mark_price_quality_failures,
                                           PUBLICATION_LAG_DAYS)
from tools.cache_io import atomic_write_json
from tools.live_data_refresh import oi_quality_failures

_DAY_MS = 86_400_000
_PRICE = os.path.join("data", "cache", "crypto_perp_mark_prices.json")
_FUNDING = os.path.join("data", "cache", "crypto_funding_rates.json")
_OI = os.path.join("data", "cache", "crypto_oi_metrics.json")
_SNAPSHOT = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_REFRESH = os.path.join("data", "cache", "live_refresh_status.json")
_OUTPUT = os.path.join("evidence", "data_quality_admission.json")
DATA_QUALITY_ADMISSION_DB = os.path.join("data", "cache", "data_quality_admission.db")
_AUTHORITY = DATA_QUALITY_ADMISSION_DB
_RAW_ARCHIVE = DEFAULT_RAW_ARCHIVE
_LINEAGE_BACKFILL = os.path.join("evidence", "raw_data_lineage_backfill.json")
_LINEAGE_LOOKBACK_DAYS = 8
_LINEAGE_MAX_AGE_MS = 7 * _DAY_MS


def _sha256(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: str) -> dict:
    digest = _sha256(path)
    return {
        "path": path.replace("\\", "/"),
        "exists": digest is not None,
        "size_bytes": os.path.getsize(path) if digest is not None else None,
        "sha256": digest,
    }


def _check_prices(path: str, instruments: list[str], *, now_ms: int,
                  expected_end: int) -> dict:
    details = {}
    if not os.path.isfile(path):
        return {
            "passed": False,
            "failed_instruments": list(instruments),
            "instruments": {
                ticker: {"passed": False, "detail": "price artifact missing"}
                for ticker in instruments
            },
        }
    if path.lower().endswith(".json"):
        try:
            prices = load_mark_prices(path)
            failed = set(mark_price_quality_failures(
                instruments,
                now_ms=now_ms,
                cache_path=path,
                publication_lag_days=PUBLICATION_LAG_DAYS,
            ))
            for ticker in instruments:
                times = sorted(prices.get(ticker, {}))
                details[ticker] = {
                    "passed": ticker not in failed,
                    "n_records": len(times),
                    "last_event_time": times[-1] if times else None,
                    "failed_checks": [] if ticker not in failed else ["recent_mark_price_quality"],
                    "detail": "ok" if ticker not in failed else "recent mark-price quality failed",
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            details = {
                ticker: {
                    "passed": False,
                    "detail": f"mark-price artifact unreadable: {type(exc).__name__}: {exc}",
                }
                for ticker in instruments
            }
        failed_instruments = sorted(
            ticker for ticker, detail in details.items() if not detail["passed"]
        )
        return {
            "passed": not failed_instruments,
            "failed_instruments": failed_instruments,
            "instruments": details,
        }
    try:
        uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            for ticker in instruments:
                times = [
                    int(row[0])
                    for row in conn.execute(
                        "SELECT event_time FROM bars WHERE key=? ORDER BY event_time",
                        (ticker,),
                    )
                ]
                report = build_ingestion_report(
                    f"forward-price-{ticker}",
                    {ticker: times},
                    as_of=now_ms,
                    expected_interval=_DAY_MS,
                    expected_end=expected_end,
                )
                failed = [check.name for check in report.checks if not check.passed]
                details[ticker] = {
                    "passed": report.passed,
                    "n_records": len(times),
                    "last_event_time": max(times) if times else None,
                    "failed_checks": failed,
                    "detail": "ok" if not failed else str(failed),
                }
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        details = {
            ticker: {
                "passed": False,
                "detail": f"price artifact unreadable: {type(exc).__name__}: {exc}",
            }
            for ticker in instruments
        }
    failed_instruments = sorted(
        ticker for ticker, detail in details.items() if not detail["passed"]
    )
    return {
        "passed": not failed_instruments,
        "failed_instruments": failed_instruments,
        "instruments": details,
    }


def _check_funding(path: str, instruments: list[str], *, expected_end: int) -> dict:
    details = {}
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("funding artifact root must be an object")
        for ticker in instruments:
            raw = payload.get(ticker, {})
            daily = {int(day): value for day, value in raw.items()} if isinstance(raw, dict) else raw
            try:
                validate_daily_funding(ticker, daily, expected_end=expected_end)
                details[ticker] = {
                    "passed": True,
                    "n_records": len(daily),
                    "last_event_time": max(daily) if daily else None,
                    "detail": "ok",
                }
            except (FundingDataError, TypeError, ValueError) as exc:
                details[ticker] = {
                    "passed": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        details = {
            ticker: {
                "passed": False,
                "detail": f"funding artifact unreadable: {type(exc).__name__}: {exc}",
            }
            for ticker in instruments
        }
    failed_instruments = sorted(
        ticker for ticker, detail in details.items() if not detail["passed"]
    )
    return {
        "passed": not failed_instruments,
        "failed_instruments": failed_instruments,
        "instruments": details,
    }


def _check_oi(path: str, instruments: list[str], *, now_ms: int) -> dict:
    details = {}
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        data = payload.get("data", {})
        failed = set(oi_quality_failures(instruments, now_ms=now_ms, oi_path=path))
        for ticker in instruments:
            raw = data.get(ticker, {}) if isinstance(data, dict) else {}
            times = sorted(int(day) for day in raw) if isinstance(raw, dict) else []
            details[ticker] = {
                "passed": ticker not in failed,
                "n_records": len(times),
                "last_event_time": times[-1] if times else None,
                "detail": "ok" if ticker not in failed else "OI quality/freshness failed",
            }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        details = {
            ticker: {
                "passed": False,
                "detail": f"OI artifact unreadable: {type(exc).__name__}: {exc}",
            }
            for ticker in instruments
        }
    failed_instruments = sorted(
        ticker for ticker, detail in details.items() if not detail["passed"]
    )
    return {
        "passed": not failed_instruments,
        "failed_instruments": failed_instruments,
        "instruments": details,
    }


def _refresh_observation(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        reports = []
        for report in payload.get("reports", []):
            reports.append({
                "source": report.get("source"),
                "failed_count": len(report.get("failed", [])),
                "failed_tickers": sorted(report.get("failed_tickers", [])),
                "failure_details": report.get("failure_details", report.get("failed", [])),
            })
        return {
            "readable": True,
            "run_id": payload.get("run_id"),
            "ran_at": payload.get("ran_at"),
            "reports": reports,
            "raw_lineage": payload.get("raw_lineage"),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "readable": False,
            "detail": f"{type(exc).__name__}: {exc}",
            "reports": [],
        }


def _lineage_backfill_observation(
    path: str,
    *,
    now_ms: int,
    scope: list[str],
    required_days: list[int],
    artifacts: dict,
    raw_lineage: dict,
) -> dict:
    evidence_sha = _sha256(path)
    result = {
        "path": path.replace("\\", "/"),
        "exists": evidence_sha is not None,
        "sha256": evidence_sha,
        "readable": False,
        "passed": False,
        "reasons": [],
    }
    if evidence_sha is None:
        result["reasons"].append("LINEAGE:BACKFILL_EVIDENCE_MISSING")
        return result
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result["reasons"].append(
            f"LINEAGE:BACKFILL_EVIDENCE_UNREADABLE:{type(exc).__name__}"
        )
        return result
    result["readable"] = True
    result["generated_at_ms"] = payload.get("generated_at_ms")
    result["verdict"] = payload.get("verdict")
    result["instrument_scope"] = payload.get("instrument_scope")
    result["required_days"] = payload.get("required_days")

    if payload.get("schema_version") != 1 \
            or payload.get("readiness") != "L2_DRY_RUN_ONLY" \
            or payload.get("purpose") != "RECENT_FORWARD_RAW_LINEAGE_BACKFILL":
        result["reasons"].append("LINEAGE:BACKFILL_SCHEMA_INVALID")
    generated_at = payload.get("generated_at_ms")
    if (isinstance(generated_at, bool) or not isinstance(generated_at, int)
            or generated_at > now_ms or now_ms - generated_at > _LINEAGE_MAX_AGE_MS):
        result["reasons"].append("LINEAGE:BACKFILL_STALE_OR_FUTURE")
    if payload.get("instrument_scope") != scope:
        result["reasons"].append("LINEAGE:BACKFILL_SCOPE_MISMATCH")
    if payload.get("required_days") != required_days:
        result["reasons"].append("LINEAGE:BACKFILL_DAYS_MISMATCH")
    expected_mark_oi_days = [
        int(day) - (PUBLICATION_LAG_DAYS - 1) * _DAY_MS
        for day in required_days
    ]
    expected_source_days = {
        "funding": [int(day) for day in required_days],
        "perp_mark_prices": expected_mark_oi_days,
        "oi_metrics": expected_mark_oi_days,
    }
    if payload.get("source_required_days") != expected_source_days:
        result["reasons"].append("LINEAGE:BACKFILL_SOURCE_DAYS_MISMATCH")
    if payload.get("verdict") != "PASS":
        result["reasons"].append(
            f"LINEAGE:BACKFILL_VERDICT:{payload.get('verdict')}"
        )

    evidence_artifacts = payload.get("artifacts")
    if not isinstance(evidence_artifacts, dict):
        result["reasons"].append("LINEAGE:BACKFILL_ARTIFACTS_MISSING")
    else:
        for name in ("prices", "funding", "oi_metrics"):
            expected_sha = artifacts[name].get("sha256")
            observed = evidence_artifacts.get(name)
            observed_sha = observed.get("sha256") if isinstance(observed, dict) else None
            if not expected_sha or observed_sha != expected_sha:
                result["reasons"].append(
                    f"LINEAGE:BACKFILL_ARTIFACT_MISMATCH:{name}"
                )

    source_reports = payload.get("sources")
    for name in ("perp_mark_prices", "funding", "oi_metrics"):
        report = source_reports.get(name) if isinstance(source_reports, dict) else None
        if (not isinstance(report, dict) or report.get("passed") is not True
                or report.get("matched_records") != report.get("attempted_records")):
            result["reasons"].append(f"LINEAGE:BACKFILL_SOURCE_FAILED:{name}")

    evidence_lineage = payload.get("raw_lineage")
    evidence_aggregate = (
        evidence_lineage.get("aggregate_sha256")
        if isinstance(evidence_lineage, dict) else None
    )
    if evidence_aggregate != raw_lineage.get("aggregate_sha256"):
        result["reasons"].append("LINEAGE:BACKFILL_ARCHIVE_MISMATCH")
    result["passed"] = not result["reasons"]
    return result


def build_data_quality_evidence(*, now_ms: int | None = None,
                                instruments=None,
                                price_path: str = _PRICE,
                                funding_path: str = _FUNDING,
                                oi_path: str = _OI,
                                snapshot_path: str = _SNAPSHOT,
                                refresh_status_path: str = _REFRESH,
                                raw_archive_path: str = _RAW_ARCHIVE,
                                lineage_backfill_path: str = _LINEAGE_BACKFILL,
                                price_basis: str = "PERP_MARK_PRICE") -> dict:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    scope = sorted(set(instruments or DEFAULT_TICKERS))
    expected_end = (now_ms // _DAY_MS) * _DAY_MS - _DAY_MS

    snapshot_missing = []
    try:
        with open(snapshot_path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
        available = set(snapshot.get("instruments", []))
        snapshot_missing = sorted(set(scope) - available)
    except (OSError, ValueError, json.JSONDecodeError):
        snapshot_missing = list(scope)

    prices = _check_prices(price_path, scope, now_ms=now_ms, expected_end=expected_end)
    funding = _check_funding(funding_path, scope, expected_end=expected_end)
    oi = _check_oi(oi_path, scope, now_ms=now_ms)
    sources = {"prices": prices, "funding": funding, "oi_metrics": oi}

    quality_reasons = []
    if snapshot_missing:
        quality_reasons.append(f"SNAPSHOT_SCOPE_MISSING:{snapshot_missing}")
    for name, result in sources.items():
        if not result["passed"]:
            quality_reasons.append(f"QUALITY:{name}:{result['failed_instruments']}")
    quality_verdict = "PASS" if not quality_reasons else "BLOCK"

    artifact_paths = {
        "prices": price_path,
        "funding": funding_path,
        "oi_metrics": oi_path,
        "universe_snapshot": snapshot_path,
        "refresh_status": refresh_status_path,
        "raw_lineage_backfill": lineage_backfill_path,
    }
    artifacts = {name: _artifact(path) for name, path in artifact_paths.items()}

    raw_lineage = summarize_archive(raw_archive_path)
    raw_lineage["path"] = raw_archive_path.replace("\\", "/")
    required_lineage_datasets = sorted({
        "perp_mark_prices" if price_basis == "PERP_MARK_PRICE" else "spot_klines",
        "funding",
        "oi_metrics",
    })
    lineage_reasons = []
    if raw_lineage["status"] == "MISSING":
        lineage_reasons.append("LINEAGE:ARCHIVE_MISSING")
    elif raw_lineage["status"] != "PASS":
        lineage_reasons.append(
            f"LINEAGE:ARCHIVE_INTEGRITY:{raw_lineage['status']}"
        )
    observed_datasets = set(raw_lineage.get("datasets", {}))
    missing_lineage = sorted(set(required_lineage_datasets) - observed_datasets)
    if missing_lineage:
        lineage_reasons.append(f"LINEAGE:MISSING_DATASETS:{missing_lineage}")
    required_lineage_days = [
        expected_end - offset * _DAY_MS
        for offset in reversed(range(_LINEAGE_LOOKBACK_DAYS))
    ]
    lineage_backfill = _lineage_backfill_observation(
        lineage_backfill_path,
        now_ms=int(now_ms),
        scope=scope,
        required_days=required_lineage_days,
        artifacts=artifacts,
        raw_lineage=raw_lineage,
    )
    lineage_reasons.extend(lineage_backfill["reasons"])
    lineage_verdict = "PASS" if not lineage_reasons else "BLOCK"

    block_reasons = list(quality_reasons) + lineage_reasons
    if price_basis != "PERP_MARK_PRICE":
        block_reasons.append(str(price_basis))

    return {
        "schema_version": 2,
        "generated_at_ms": int(now_ms),
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "FORWARD_EVIDENCE_DATA_ADMISSION",
        "instrument_scope": scope,
        "expected_last_closed_day_ms": expected_end,
        "price_basis": price_basis,
        "source_provenance": {
            "prices": "Binance public spot klines" if price_basis == "SPOT_PROXY_FOR_PERP"
                      else "Binance Data Vision USD-M perpetual mark-price klines",
            "funding": "Binance USD-M perpetual funding history",
            "oi_metrics": "Binance Data Vision USD-M futures metrics",
        },
        "artifacts": artifacts,
        "sources": sources,
        "raw_lineage": raw_lineage,
        "required_lineage_datasets": required_lineage_datasets,
        "required_lineage_days": required_lineage_days,
        "lineage_backfill": lineage_backfill,
        "lineage_verdict": lineage_verdict,
        "refresh_observation": _refresh_observation(refresh_status_path),
        "quality_verdict": quality_verdict,
        "verdict": "PASS" if not block_reasons else "BLOCK",
        "block_reasons": block_reasons,
    }


def write_data_quality_evidence(*, output_path: str = _OUTPUT, **kwargs) -> dict:
    evidence = build_data_quality_evidence(**kwargs)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    atomic_write_json(output_path, evidence, indent=2)
    return evidence


def run_data_quality_admission(
    *,
    output_path: str = _OUTPUT,
    authority_path: str = _AUTHORITY,
    recorded_at_ms: int | None = None,
    **kwargs,
) -> tuple[dict, DataQualityAdmissionDecision, bool]:
    """Evaluate once, commit the decision/outbox, then publish its read artifact."""
    evidence = build_data_quality_evidence(**kwargs)
    if recorded_at_ms is None:
        recorded_at_ms = max(
            int(time.time() * 1_000),
            int(evidence["generated_at_ms"]),
        )
    refresh = evidence.get("refresh_observation", {})
    refresh_run_id = refresh.get("run_id") if isinstance(refresh, dict) else None
    causation_id = refresh_run_id if isinstance(refresh_run_id, str) else ""

    store = DataQualityAdmissionStore(authority_path, create=True)
    try:
        decision, inserted = store.record_evidence(
            evidence,
            recorded_at_ms=recorded_at_ms,
            causation_id=causation_id,
        )
    finally:
        store.close()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    atomic_write_json(output_path, evidence, indent=2)
    return evidence, decision, inserted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=_OUTPUT)
    parser.add_argument("--authority-path", default=_AUTHORITY)
    parser.add_argument("--now-ms", type=int)
    parser.add_argument(
        "--price-basis",
        choices=("SPOT_PROXY_FOR_PERP", "PERP_MARK_PRICE"),
        default="PERP_MARK_PRICE",
    )
    args = parser.parse_args(argv)
    try:
        evidence, decision, inserted = run_data_quality_admission(
            output_path=args.output,
            authority_path=args.authority_path,
            now_ms=args.now_ms,
            price_basis=args.price_basis,
        )
    except (DataQualityAdmissionStoreError, OSError, ValueError) as exc:
        print(
            "data-quality admission authority: BREACH "
            f"({type(exc).__name__})"
        )
        return 1
    print(
        f"data-quality admission: {evidence['verdict']} "
        f"(quality={evidence['quality_verdict']}, lineage={evidence['lineage_verdict']}, "
        f"scope={len(evidence['instrument_scope'])})"
    )
    print(
        f"authority: {decision.admission_id} "
        f"({'recorded' if inserted else 'idempotent replay'})"
    )
    print(f"-> {args.output}")
    return 0 if evidence["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
