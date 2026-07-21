"""Pure alert extraction for the read-only monitoring snapshot.

The console snapshot is intentionally diagnostic.  This module only turns an
honest ``WATCH``/``BREACH`` monitor row into a deterministic message; it does
not change a gate, a position, a promotion state, or an order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_SYNTHETIC_PROVENANCE = {
    "SYNTHETIC_DETERMINISTIC",
    "SYNTHETIC_FEATURE_VALUES",
    "NO_HONEST_INPUT",
}
_ALERTABLE_CATEGORIES = {
    "ALPHA_DECAY",
    "DATA_DISTRIBUTION_DRIFT",
    "DATA_FRESHNESS",
    "FEATURE_DRIFT",
}


def _is_honest_monitor(monitor: Mapping[str, Any], *, payload_provenance: str) -> bool:
    provenance = str(monitor.get("provenance", ""))
    if provenance in _SYNTHETIC_PROVENANCE:
        return False
    if monitor.get("category") == "ALPHA_DECAY" and payload_provenance != "PARTIAL_ALPHA_DECAY_REAL":
        return False
    return True


def build_monitoring_alerts(payload: Mapping[str, Any]) -> list[str]:
    """Return stable, human-readable alerts for honest non-OK monitor rows.

    Malformed rows and synthetic fallbacks are ignored rather than promoted to
    operational alerts.  The caller owns persistence/delivery (the existing
    append-only alert sink is one such caller).
    """
    monitors = payload.get("monitors", ())
    if not isinstance(monitors, Sequence) or isinstance(monitors, (str, bytes)):
        return []
    payload_provenance = str(payload.get("data_provenance", ""))
    alerts: list[str] = []
    for monitor in monitors:
        if not isinstance(monitor, Mapping):
            continue
        status = str(monitor.get("status", ""))
        if (
            status not in {"WATCH", "BREACH"}
            or str(monitor.get("category", "")) not in _ALERTABLE_CATEGORIES
            or not _is_honest_monitor(
                monitor,
                payload_provenance=payload_provenance,
            )
        ):
            continue
        category = str(monitor.get("category", "UNKNOWN"))
        subject = str(monitor.get("subject_id", "UNKNOWN"))
        metric = str(monitor.get("metric", "drift"))
        score = monitor.get("drift_score", "n/a")
        threshold = monitor.get("breach_threshold", "n/a")
        alerts.append(
            f"{category} {subject} {status}: {metric}={score} "
            f"(breach_threshold={threshold})"
        )
    return alerts


__all__ = ["build_monitoring_alerts"]
