"""Read-only realtime feature and alert context for console Agent views."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Dict


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_FEATURE_STATUS = (
    _PROJECT_ROOT / "data" / "cache" / "binance_realtime_feature_status.json"
)
_ALERT_STATUS = (
    _PROJECT_ROOT
    / "data"
    / "cache"
    / "binance_realtime_drift_alert_status.json"
)


def _latest(candidates: list[Path]) -> Dict:
    statuses = []
    for path in dict.fromkeys(candidates):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict) and isinstance(
            candidate.get("generated_at_ms"), int
        ):
            statuses.append(candidate)
    return (
        max(statuses, key=lambda item: item["generated_at_ms"])
        if statuses
        else {}
    )


def realtime_agent_input_payload() -> Dict:
    feature_candidates: list[Path] = []
    alert_candidates: list[Path] = []
    state_root = os.getenv("QUANTOS_STATE_ROOT", "").strip()
    if state_root:
        cache = Path(state_root) / "data" / "cache"
        feature_candidates.append(
            cache / "binance_realtime_feature_status.json"
        )
        alert_candidates.append(
            cache / "binance_realtime_drift_alert_status.json"
        )
    feature_candidates.append(_FEATURE_STATUS)
    alert_candidates.append(_ALERT_STATUS)
    status = _latest(feature_candidates)
    alert_status = _latest(alert_candidates)

    generated_at_ms = status.get("generated_at_ms")
    now_ms = int(time.time() * 1_000)
    age_ms = (
        max(0, now_ms - generated_at_ms)
        if isinstance(generated_at_ms, int)
        else None
    )
    fresh = age_ms is not None and age_ms <= 10 * 60_000
    features = status.get("features", {})
    drift = status.get("drift", {})
    batch = status.get("batch", {})
    return {
        "status": (
            "AVAILABLE"
            if fresh and status.get("status") == "PASS"
            else "STALE"
            if status
            else "UNAVAILABLE"
        ),
        "fresh": fresh,
        "age_ms": age_ms,
        "source": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "feature_version": features.get("feature_version"),
        "feature_count": len(features.get("feature_ids", [])),
        "instrument_count": features.get("instrument_count", 0),
        "retained_value_count": features.get("retained_value_count", 0),
        "drift_status": drift.get("status"),
        "drift_status_counts": drift.get("status_counts", {}),
        "drift_calibration": drift.get("policy", {}).get("calibration"),
        "drift_calibration_id": drift.get("policy", {}).get(
            "calibration_id"
        ),
        "drift_warn_ks": drift.get("policy", {}).get("warn_ks"),
        "drift_block_ks": drift.get("policy", {}).get("block_ks"),
        "batch_chain_valid": bool(batch.get("chain_valid", False)),
        "alerting": {
            "status": alert_status.get("status", "UNAVAILABLE"),
            "current_severity": alert_status.get("current", {}).get(
                "status"
            ),
            "local_delivery": alert_status.get("delivery", {}).get(
                "local_status"
            ),
            "external_delivery": alert_status.get("delivery", {}).get(
                "external_status"
            ),
            "independent_worker": bool(
                alert_status.get("delivery", {}).get(
                    "independent_worker", False
                )
            ),
            "journal_chain_valid": bool(
                alert_status.get("journal", {}).get("chain_valid", False)
            ),
            "event_count": alert_status.get("journal", {}).get(
                "event_count", 0
            ),
        },
        "consumption_mode": "READ_ONLY_DIAGNOSTIC_CONTEXT",
        "automatic_action": False,
        "promotion_authority": False,
    }


__all__ = ["realtime_agent_input_payload"]
