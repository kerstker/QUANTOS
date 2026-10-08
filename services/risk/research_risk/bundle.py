"""Strict loader for the complete research-risk evidence bundle.

The paper-order path must not manufacture the five inputs that are not
available at the legacy dry-run seam.  This module is the small, read-only
boundary between an immutable research artifact and that path.  It validates
the artifact's identity against the attested forward decision, rejects
missing or ambiguous fields, and returns ``None`` instead of a partially
populated :class:`DryRunResearchRiskInputs`.

The loader does not fetch data, access credentials, or run a risk engine.  A
separate producer must create a bundle from the same PIT snapshot and record
the resulting nine-engine decision.  Until that producer exists, the status is
explicitly ``PENDING`` and the paper order remains blocked.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from typing import Any

from .dry_run_guard import DryRunResearchRiskInputs


BUNDLE_SCHEMA_VERSION = 1
BUNDLE_PROVENANCE = "REAL_RESEARCH_RISK_INPUT_BUNDLE"
REQUIRED_INPUT_FIELDS = (
    "capacity_cny",
    "daily_volume_cny",
    "total_aum",
    "alpha_returns",
    "regime_labels",
    "alpha_metadata",
)
REQUIRED_METADATA_FIELDS = (
    "age_days",
    "validation_confidence",
    "paper_confidence",
    "decay_flag",
    "overfit_flag",
    "leakage_flag",
)


@dataclass(frozen=True)
class ResearchRiskBundleResult:
    """Result of loading one alpha's immutable risk evidence."""

    inputs: DryRunResearchRiskInputs | None
    status: dict[str, Any]


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _decision_payload(pending: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct the canonical payload hashed by ``record_decision``."""
    try:
        weights = json.loads(str(pending["weights_json"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("attested decision weights are malformed") from exc
    if not isinstance(weights, Mapping) or not weights:
        raise ValueError("attested decision has no weights")
    normalized_weights = {
        str(key): float(value) for key, value in sorted(weights.items(), key=lambda item: str(item[0]))
    }
    if any(not _finite(value) for value in normalized_weights.values()):
        raise ValueError("attested decision contains non-finite weights")
    signals = None
    raw_signals = pending.get("signals_json")
    if raw_signals not in (None, ""):
        try:
            signals = json.loads(str(raw_signals))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("attested decision signals are malformed") from exc
        if not isinstance(signals, Mapping):
            raise ValueError("attested decision signals must be an object")
        signals = {
            str(key): float(value) for key, value in sorted(signals.items(), key=lambda item: str(item[0]))
        }
        if any(not _finite(value) for value in signals.values()):
            raise ValueError("attested decision contains non-finite signals")
    return {
        "alpha_id": str(pending.get("alpha_id", "")),
        "decided_ts": int(pending["decided_ts"]),
        "recorded_at": int(pending["recorded_at"]),
        "weights": normalized_weights,
        "signals": signals,
        "source": str(pending.get("source", "")),
    }


def forward_decision_payload_sha256(pending: Mapping[str, Any]) -> str:
    """Return the digest used to bind a bundle to an attested decision."""
    return _sha256(_decision_payload(pending))


def _load_document(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__
    if not isinstance(value, dict):
        return None, "bundle_root_not_object"
    return value, None


def _base_status(path: str, alpha_id: str) -> dict[str, Any]:
    return {
        "status": "PENDING",
        "provenance": BUNDLE_PROVENANCE,
        "path": os.path.normpath(str(path)),
        "alpha_id": str(alpha_id),
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "missing": [],
        "reasons": [],
    }


def _pending_result(status: dict[str, Any], *reasons: str) -> ResearchRiskBundleResult:
    status["reasons"] = list(dict.fromkeys(str(reason) for reason in reasons))
    status["missing"] = list(
        dict.fromkeys(
            reason.split(":", 1)[1]
            for reason in status["reasons"]
            if reason.startswith("missing:")
        )
    )
    return ResearchRiskBundleResult(inputs=None, status=status)


def load_research_risk_bundle(
    path: str,
    *,
    alpha_id: str,
    pending: Mapping[str, Any],
    expected_dataset_version: str | None = None,
    expected_config_hash: str | None = None,
) -> ResearchRiskBundleResult:
    """Load and validate a per-alpha research-risk bundle.

    The JSON may contain an ``alphas`` mapping (the preferred multi-alpha
    format) or a single ``alpha_id`` row.  All required values and binding
    fields are mandatory.  The loader intentionally treats a missing field as
    pending rather than filling a default.
    """
    status = _base_status(path, alpha_id)
    document, read_error = _load_document(path)
    if document is None:
        status["missing"] = ["bundle_file"]
        return _pending_result(status, f"missing:bundle_file", f"bundle_unavailable:{read_error}")

    if document.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        status["status"] = "BLOCKED"
        return _pending_result(status, "bundle_schema_version_invalid")
    if document.get("provenance") != BUNDLE_PROVENANCE:
        status["status"] = "BLOCKED"
        return _pending_result(status, "bundle_provenance_invalid")
    status["bundle_sha256"] = _sha256(document)

    rows = document.get("alphas")
    if isinstance(rows, Mapping):
        row = rows.get(alpha_id)
    elif document.get("alpha_id") == alpha_id:
        row = document
    else:
        row = None
    if not isinstance(row, Mapping):
        return _pending_result(status, "missing:alpha_entry")

    inputs = row.get("inputs") if isinstance(row.get("inputs"), Mapping) else row
    binding = row.get("binding") if isinstance(row.get("binding"), Mapping) else document.get("binding")
    binding = binding if isinstance(binding, Mapping) else {}
    missing = [field for field in REQUIRED_INPUT_FIELDS if field not in inputs]
    missing.extend(
        field for field in ("alpha_id", "decision_ts", "decision_payload_sha256", "dataset_version", "config_hash")
        if field not in binding
    )
    if missing:
        return _pending_result(status, *(f"missing:{field}" for field in missing))

    try:
        expected_payload_sha = forward_decision_payload_sha256(pending)
    except (TypeError, ValueError, KeyError) as exc:
        status["status"] = "BLOCKED"
        return _pending_result(status, f"decision_binding_invalid:{type(exc).__name__}")
    if binding.get("alpha_id") != alpha_id:
        status["status"] = "BLOCKED"
        return _pending_result(status, "decision_binding_alpha_mismatch")
    try:
        decision_ts = int(pending["decided_ts"])
    except (KeyError, TypeError, ValueError) as exc:
        status["status"] = "BLOCKED"
        return _pending_result(status, f"decision_binding_invalid:{type(exc).__name__}")
    if binding.get("decision_ts") != decision_ts:
        status["status"] = "BLOCKED"
        return _pending_result(status, "decision_binding_timestamp_mismatch")
    if binding.get("decision_payload_sha256") != expected_payload_sha:
        status["status"] = "BLOCKED"
        return _pending_result(status, "decision_binding_digest_mismatch")
    dataset_version = binding.get("dataset_version")
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        status["status"] = "BLOCKED"
        return _pending_result(status, "dataset_version_invalid")
    config_hash = binding.get("config_hash")
    if (
        not isinstance(config_hash, str)
        or len(config_hash) != 64
        or any(character not in "0123456789abcdef" for character in config_hash.lower())
    ):
        status["status"] = "BLOCKED"
        return _pending_result(status, "risk_config_hash_invalid")
    if expected_dataset_version is not None and binding.get("dataset_version") != expected_dataset_version:
        status["status"] = "BLOCKED"
        return _pending_result(status, "dataset_version_mismatch")
    if expected_config_hash is not None and binding.get("config_hash") != expected_config_hash:
        status["status"] = "BLOCKED"
        return _pending_result(status, "risk_config_hash_mismatch")
    status["binding"] = {
        "alpha_id": alpha_id,
        "decision_ts": decision_ts,
        "decision_payload_sha256": expected_payload_sha,
        "dataset_version": dataset_version,
        "config_hash": config_hash,
    }

    reasons: list[str] = []
    capacity = inputs.get("capacity_cny")
    if not isinstance(capacity, Mapping) or not capacity:
        reasons.append("capacity_cny_invalid")
    elif any(not _finite(value) or float(value) <= 0.0 for value in capacity.values()):
        reasons.append("capacity_cny_invalid")
    try:
        decision_weights = _decision_payload(pending)["weights"]
    except (TypeError, ValueError, KeyError):
        decision_weights = {}
    if isinstance(capacity, Mapping) and decision_weights:
        missing_capacity = sorted(
            str(key) for key in decision_weights if key not in capacity
        )
        if missing_capacity:
            reasons.append("capacity_cny_missing:" + ",".join(missing_capacity))
    for name in ("daily_volume_cny", "total_aum"):
        if not _finite(inputs.get(name)) or float(inputs[name]) <= 0.0:
            reasons.append(f"{name}_invalid")

    alpha_returns = inputs.get("alpha_returns")
    if not isinstance(alpha_returns, Mapping) or len(alpha_returns) < 2:
        reasons.append("alpha_returns_need_at_least_two")
    else:
        if alpha_id not in alpha_returns:
            reasons.append("alpha_returns_missing_current_alpha")
        lengths: set[int] = set()
        for key, series in alpha_returns.items():
            if not isinstance(series, Sequence) or isinstance(series, (str, bytes)) or not series:
                reasons.append(f"alpha_returns_invalid:{key}")
                continue
            lengths.add(len(series))
            if any(not _finite(value) for value in series):
                reasons.append(f"alpha_returns_non_finite:{key}")
        if len(lengths) > 1:
            reasons.append("alpha_returns_unaligned")

    labels = inputs.get("regime_labels")
    if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)) or not labels:
        reasons.append("regime_labels_invalid")
    elif any(not isinstance(label, str) or not label.strip() for label in labels):
        reasons.append("regime_labels_invalid")
    elif alpha_returns and isinstance(alpha_returns, Mapping):
        lengths = {len(series) for series in alpha_returns.values() if isinstance(series, Sequence) and not isinstance(series, (str, bytes))}
        if lengths and len(labels) not in lengths:
            reasons.append("regime_labels_unaligned")

    timestamps = inputs.get("timestamps")
    if timestamps is not None:
        if (
            not isinstance(timestamps, Sequence)
            or isinstance(timestamps, (str, bytes))
            or not timestamps
        ):
            reasons.append("timestamps_invalid")
        elif (
            any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in timestamps
            )
            or list(timestamps) != sorted(set(timestamps))
        ):
            reasons.append("timestamps_invalid")
        elif isinstance(labels, Sequence) and len(timestamps) != len(labels):
            reasons.append("timestamps_unaligned")

    metadata = inputs.get("alpha_metadata")
    if not isinstance(metadata, Sequence) or isinstance(metadata, (str, bytes)) or not metadata:
        reasons.append("alpha_metadata_invalid")
    else:
        for index, item in enumerate(metadata):
            if not isinstance(item, Mapping):
                reasons.append(f"alpha_metadata_invalid:{index}")
                continue
            absent = [field for field in REQUIRED_METADATA_FIELDS if field not in item]
            if absent:
                reasons.append(f"alpha_metadata_missing:{index}:{','.join(absent)}")
                continue
            if any(not _finite(item[field]) for field in ("age_days", "validation_confidence", "paper_confidence")):
                reasons.append(f"alpha_metadata_non_finite:{index}")
            elif item["age_days"] < 0 or any(not 0 <= item[field] <= 1
                    for field in ("validation_confidence", "paper_confidence")):
                reasons.append(f"alpha_metadata_range_invalid:{index}")
            if any(type(item[field]) is not bool for field in ("decay_flag", "overfit_flag", "leakage_flag")):
                reasons.append(f"alpha_metadata_flags_invalid:{index}")

    if reasons:
        return _pending_result(status, *reasons)

    if isinstance(rows, Mapping) and isinstance(row.get("inputs"), Mapping):
        status["alpha_entry"] = alpha_id
    status["status"] = "READY"
    status["missing"] = []
    status["reasons"] = []
    return ResearchRiskBundleResult(
        inputs=DryRunResearchRiskInputs(
            capacity_cny={str(key): float(value) for key, value in capacity.items()},
            daily_volume_cny=float(inputs["daily_volume_cny"]),
            total_aum=float(inputs["total_aum"]),
            alpha_returns={
                str(key): tuple(float(value) for value in series)
                for key, series in alpha_returns.items()
            },
            regime_labels=tuple(str(label) for label in labels),
            alpha_metadata=[dict(item) for item in metadata],
            timestamps=(
                tuple(int(value) for value in timestamps)
                if timestamps is not None
                else None
            ),
        ),
        status=status,
    )


__all__ = [
    "BUNDLE_PROVENANCE",
    "BUNDLE_SCHEMA_VERSION",
    "REQUIRED_INPUT_FIELDS",
    "REQUIRED_METADATA_FIELDS",
    "ResearchRiskBundleResult",
    "forward_decision_payload_sha256",
    "load_research_risk_bundle",
]
