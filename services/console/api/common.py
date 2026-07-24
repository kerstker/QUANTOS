"""Small deterministic helpers shared by console HTTP routers.

The console is a read-only presentation layer, but its payloads still need to
carry enough truth for an operator to tell a real snapshot from a synthetic or
missing one.  The helpers in this module are pure and deliberately do not read
the wall clock or contact external systems.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping


SNAPSHOT_STATUSES = frozenset(
    {"REAL", "SYNTHETIC", "NO_SNAPSHOT", "STALE", "DATA_BLOCKED", "UNAVAILABLE"}
)

_SNAPSHOT_KEYS = frozenset({"snapshot_id", "data_snapshot_id", "dataset_version"})
_UNAVAILABLE_TOKENS = frozenset({"UNAVAILABLE", "ERROR", "BACKEND_UNAVAILABLE"})
_BLOCKED_TOKENS = frozenset({"BLOCK", "BLOCKED", "DATA_BLOCKED", "QUALITY_BLOCKED"})


def _text(value: Any) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def collect_snapshot_ids(payload: Mapping[str, Any]) -> List[str]:
    """Collect durable snapshot identifiers already present in a payload.

    The helper only extracts identifiers supplied by the endpoint.  It never
    fabricates a snapshot id for synthetic data, which is important because a
    generated id would make a mock look like a reproducible data artifact.
    """
    found: List[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in _SNAPSHOT_KEYS and isinstance(child, (str, int)):
                    text = str(child).strip()
                    if text and text not in found:
                        found.append(text)
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(payload)
    return found


def infer_snapshot_status(payload: Mapping[str, Any], provenance: str | None = None) -> str:
    """Return the conservative six-state snapshot status for a console body.

    Explicit endpoint status wins.  Otherwise, status is inferred from the
    endpoint's existing verdict/snapshot markers and provenance.  A synthetic
    body remains ``SYNTHETIC``; an artifact route that explicitly has no
    snapshot is ``NO_SNAPSHOT``.  No state is upgraded to ``REAL`` merely
    because a response is HTTP 200.
    """
    explicit = _text(payload.get("snapshot_status") or payload.get("data_quality_status"))
    if explicit in SNAPSHOT_STATUSES:
        return explicit

    marker = _text(provenance or payload.get("data_provenance"))
    verdict = _text(payload.get("verdict"))
    status = _text(payload.get("status"))
    quality = payload.get("data_quality")
    quality_status = _text(quality.get("status")) if isinstance(quality, Mapping) else ""
    if (
        marker in _UNAVAILABLE_TOKENS
        or verdict in _UNAVAILABLE_TOKENS
        or status in _UNAVAILABLE_TOKENS
        or quality_status in _UNAVAILABLE_TOKENS
    ):
        return "UNAVAILABLE"
    if (
        marker in _BLOCKED_TOKENS
        or verdict in _BLOCKED_TOKENS
        or status in _BLOCKED_TOKENS
        or quality_status in _BLOCKED_TOKENS
    ):
        return "DATA_BLOCKED"
    if (
        "STALE" in marker
        or "STALE" in verdict
        or "STALE" in quality_status
        or payload.get("stale") is True
    ):
        return "STALE"
    if payload.get("snapshot_present") is False or verdict == "NO_SNAPSHOT":
        return "NO_SNAPSHOT"
    if marker == "SYNTHETIC_DETERMINISTIC" or marker.startswith("SYNTHETIC_"):
        return "SYNTHETIC"
    # A non-synthetic marker is the endpoint's own claim of a real/authoritative
    # source.  It is still accompanied by the original marker for auditability.
    return "REAL"


def snapshot_metadata(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Build common read-only provenance metadata without changing payload data."""
    provenance = payload.get("data_provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        provenance = "SYNTHETIC_DETERMINISTIC"
    status = infer_snapshot_status(payload, provenance)
    ids = collect_snapshot_ids(payload) if status not in {"SYNTHETIC", "NO_SNAPSHOT", "UNAVAILABLE"} else []
    snapshot_present = payload.get("snapshot_present")
    if not isinstance(snapshot_present, bool):
        snapshot_present = bool(ids) or status == "REAL"
    existing_quality = payload.get("data_quality")
    quality: Dict[str, Any] = dict(existing_quality) if isinstance(existing_quality, Mapping) else {}
    quality.update(
        {
            "status": status,
            "provenance": provenance,
            "snapshot_present": snapshot_present,
            "snapshot_ids": ids,
            "is_synthetic": status == "SYNTHETIC",
            "is_stale": status == "STALE",
            "is_blocked": status == "DATA_BLOCKED",
            "is_available": status not in {"UNAVAILABLE", "NO_SNAPSHOT"},
        }
    )
    if "datasets_real" in payload:
        quality["datasets_real"] = payload["datasets_real"] is True
    if "features_synthetic" in payload:
        quality["features_synthetic"] = payload["features_synthetic"] is True
        quality["mixed_provenance"] = status == "REAL" and payload["features_synthetic"] is True
    return {
        "snapshot_id": ids[0] if len(ids) == 1 else None,
        "snapshot_ids": ids,
        "snapshot_status": status,
        "data_quality": quality,
    }


def with_snapshot_metadata(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy of an endpoint body with common snapshot fields attached."""
    result = dict(payload)
    metadata = snapshot_metadata(result)
    # Preserve endpoint-specific values other than the common metadata.  The
    # common fields are normalized together so snapshot_id(s), status, and the
    # nested quality block cannot disagree when a router supplied only part of
    # the contract.
    result.setdefault("data_provenance", "SYNTHETIC_DETERMINISTIC")
    result.update(metadata)
    if not isinstance(result.get("data_quality"), Mapping):
        legacy_quality = result.get("data_quality")
        if legacy_quality is not None:
            metadata["data_quality"]["legacy_value"] = legacy_quality
    result["data_quality"] = metadata["data_quality"]
    return result


def short_hash(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
