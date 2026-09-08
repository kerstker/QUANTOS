"""Read-only runtime receipt probes for console health views."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict

from quantos.runtime_paths import runtime_state_root


_STATE_ROOT = runtime_state_root()


def receipt_service(
    *,
    service: str,
    filename: str,
    now_ms: int,
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
    }
