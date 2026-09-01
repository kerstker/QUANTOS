"""Read-only runtime receipt probes for console health views."""

from __future__ import annotations

import json
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
        }

    generated_at_ms = payload.get("generated_at_ms", payload.get("observed_at_ms"))
    age_ms = (
        max(0, now_ms - generated_at_ms)
        if isinstance(generated_at_ms, int) and not isinstance(generated_at_ms, bool)
        else None
    )
    verdict = str(payload.get("verdict", payload.get("status", "UNKNOWN"))).upper()
    fresh = age_ms is not None and age_ms <= max_age_ms
    if fresh and verdict in healthy_verdicts:
        status = "healthy"
    elif verdict == "BREACH":
        status = "down"
    else:
        status = "degraded"
    return {
        "service": service,
        "status": status,
        "latency_ms": None,
        "error_rate": None,
        "probe": f"RUNTIME_RECEIPT:{verdict}:age_ms={age_ms if age_ms is not None else 'UNKNOWN'}",
    }
