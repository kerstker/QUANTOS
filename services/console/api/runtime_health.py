"""Read-only runtime receipt probes for console health views."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict

from quantos.runtime_paths import runtime_state_root


_STATE_ROOT = runtime_state_root()


def _restore_profile(receipt: dict) -> dict | None:
    """Only expose bounded diagnostic fields bound to this history projection."""
    head = receipt.get("history_event_hash")
    seq = receipt.get("history_event_seq")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{64}", head) or type(seq) is not int or seq < 1:
        return None
    try:
        with (Path(_STATE_ROOT) / "evidence/l2_backup_latest.json").open("rb") as source:
            raw = source.read(131073)
        if len(raw) > 131072:
            return None
        evidence = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(evidence, dict) or evidence.get("history_event_hash") != head or evidence.get("history_event_seq") != seq:
        return None
    profile = evidence.get("restore_diagnostics")
    if not isinstance(profile, dict) or profile.get("scope") != "RESTORE_PERFORMANCE_DIAGNOSTIC_ONLY":
        return None
    if type(profile.get("total_ms")) is not int or profile["total_ms"] < 0 or type(profile.get("completed")) is not bool:
        return None
    stages = profile.get("stages")
    if not isinstance(stages, list) or len(stages) > 512:
        return None
    allowed = {"ARTIFACT_RESTORE", "DECRYPT_DECOMPRESS_HASH", "MANIFEST_CONTRACT", "SQLITE_INTEGRITY", "HISTORY_SEMANTICS"}
    clean = []
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("stage"), str) or stage["stage"] not in allowed:
            return None
        if type(stage.get("elapsed_ms")) is not int or stage["elapsed_ms"] < 0 or type(stage.get("completed")) is not bool:
            return None
        clean.append({key: stage[key] for key in ("stage", "elapsed_ms", "completed")})
    return {"scope": profile["scope"], "total_ms": profile["total_ms"], "completed": profile["completed"], "stages": clean}


def receipt_service(
    *,
    service: str,
    filename: str,
    now_ms: int | None = None,
    max_age_ms: int,
    healthy_verdicts: frozenset[str],
) -> Dict:
    """Map one immutable runtime receipt to a console service-health row."""

    path = Path(_STATE_ROOT) / "data" / "cache" / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return {
            "service": service,
            "status": "unknown",
            "latency_ms": None,
            "error_rate": None,
            "probe": f"RUNTIME_RECEIPT_MISSING:{filename}",
            "reason_code": "RECEIPT_MISSING",
            "age_ms": None,
            "observed_at_ms": None,
        }

    # Live probes sample the clock after reading the receipt: another process
    # may have published it while earlier monitoring work was still running.
    # Explicit as-of callers retain their fixed reference time.
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    generated_at_ms = payload.get("generated_at_ms", payload.get("observed_at_ms"))
    age_ms = (
        now_ms - generated_at_ms
        if type(generated_at_ms) is int and 0 <= generated_at_ms <= now_ms
        else None
    )
    verdict = str(payload.get("verdict", payload.get("status", "UNKNOWN"))).upper()
    fresh = age_ms is not None and age_ms <= max_age_ms
    if fresh and verdict in healthy_verdicts:
        status = "healthy"
    elif verdict in {"BREACH", "FAIL", "BLOCK"}:
        status = "down"
    else:
        status = "degraded"
    reason = payload.get("reason_code", payload.get("last_reason_code"))
    if not isinstance(reason, str) or not re.fullmatch(r"[A-Z0-9_]{1,96}", reason):
        reason = None
    if age_ms is None:
        reason = "INVALID_RECEIPT_TIMESTAMP"
    elif not fresh and reason is None:
        reason = "STALE_RECEIPT"
    return {
        "service": service,
        "status": status,
        "latency_ms": None,
        "error_rate": None,
        "probe": f"RUNTIME_RECEIPT:{verdict}:age_ms={age_ms if age_ms is not None else 'UNKNOWN'}",
        "reason_code": reason,
        "age_ms": age_ms,
        "observed_at_ms": generated_at_ms if age_ms is not None else None,
        **({"restore_diagnostics": _restore_profile(payload)} if service == "l2-backup-restore" else {}),
    }


def operational_receipt_services(*, now_ms: int | None = None) -> list[Dict]:
    """Project market, recovery and archive receipts without coupling them to HTTP routes."""
    return [
        receipt_service(
            service=service,
            filename=filename,
            now_ms=now_ms,
            max_age_ms=max_age_ms,
            healthy_verdicts=frozenset({"PASS"}),
        )
        for service, filename, max_age_ms in (
            ("binance-public-market-stream", "binance_realtime_stream_status.json", 30_000),
            ("bybit-public-market-stream", "bybit_realtime_stream_status.json", 30_000),
            ("l2-backup-restore", "l2_backup_status.json", 36 * 60 * 60_000),
            ("binance-book-depth-archive", "binance_book_depth_archive_status.json", 36 * 60 * 60_000),
            ("binance-rpi-depth", "binance_rpi_depth_status.json", 2 * 60 * 60_000),
        )
    ]
