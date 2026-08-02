"""Independent operational alert worker for Binance realtime feature drift.

The worker consumes only the materializer's signed-by-content status artifact.
It maintains its own append-only incident/delivery chain, deduplicates stable
incidents, and delivers transitions to the operator alert ledger with optional
Telegram egress.  It has no data-materialization, agent, broker, credential,
position, promotion, or order authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SUCCESS,
)
from services.monitoring.realtime_drift_alert_store import (
    RealtimeDriftAlertStore,
)
from tools.alert_sink import emit
from tools.cache_io import atomic_write_json


DEFAULT_FEATURE_STATUS = os.path.join(
    "data", "cache", "binance_realtime_feature_status.json"
)
DEFAULT_ALERT_STORE = os.path.join(
    "data", "cache", "binance_realtime_drift_alerts.db"
)
DEFAULT_ALERT_STATUS = os.path.join(
    "data", "cache", "binance_realtime_drift_alert_status.json"
)
DEFAULT_OPERATOR_LEDGER = os.path.join("data", "cache", "alerts.jsonl")
INCIDENT_KEY = "BINANCE_REALTIME_FEATURE_DRIFT"
MAX_STATUS_AGE_MS = 10 * 60_000
MAX_FUTURE_SKEW_MS = 30_000
_RANK = {"PASS": 0, "WARN": 1, "BLOCK": 2}


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_feature_status(path: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _assess(
    feature_status: dict[str, Any] | None,
    *,
    now_ms: int,
) -> dict[str, Any]:
    if not feature_status:
        return {
            "status": "BLOCK",
            "reason": "feature_status_unavailable",
            "feature_generated_at_ms": None,
            "calibration_id": None,
            "status_counts": {},
        }
    generated = feature_status.get("generated_at_ms")
    if not isinstance(generated, int):
        return {
            "status": "BLOCK",
            "reason": "feature_status_timestamp_invalid",
            "feature_generated_at_ms": None,
            "calibration_id": None,
            "status_counts": {},
        }
    age_ms = now_ms - generated
    drift = feature_status.get("drift", {})
    policy = drift.get("policy", {}) if isinstance(drift, dict) else {}
    calibration = (
        drift.get("calibration", {}) if isinstance(drift, dict) else {}
    )
    calibration_id = (
        policy.get("calibration_id")
        or (
            calibration.get("calibration_id")
            if isinstance(calibration, dict)
            else None
        )
    )
    common = {
        "feature_generated_at_ms": generated,
        "feature_age_ms": age_ms,
        "calibration_id": calibration_id,
        "status_counts": (
            drift.get("status_counts", {})
            if isinstance(drift, dict)
            else {}
        ),
    }
    if age_ms < -MAX_FUTURE_SKEW_MS:
        return {
            "status": "BLOCK",
            "reason": "feature_status_from_future",
            **common,
        }
    if age_ms > MAX_STATUS_AGE_MS:
        return {
            "status": "BLOCK",
            "reason": "feature_status_stale",
            **common,
        }
    if feature_status.get("status") != "PASS":
        return {
            "status": "BLOCK",
            "reason": "feature_materializer_blocked",
            **common,
        }
    if feature_status.get("batch", {}).get("chain_valid") is not True:
        return {
            "status": "BLOCK",
            "reason": "feature_batch_chain_invalid",
            **common,
        }
    if (
        not isinstance(calibration_id, str)
        or not calibration_id
        or policy.get("calibration") != "EMPIRICALLY_CALIBRATED_PIT_V1"
    ):
        return {
            "status": "BLOCK",
            "reason": "feature_drift_calibration_unavailable",
            **common,
        }
    status = drift.get("status")
    if status not in _RANK:
        return {
            "status": "BLOCK",
            "reason": "feature_drift_status_invalid",
            **common,
        }
    return {
        "status": status,
        "reason": (
            "feature_distribution_stable"
            if status == "PASS"
            else "feature_distribution_drift"
        ),
        **common,
    }


def _transition(
    current_status: str,
    previous: dict[str, Any] | None,
) -> str | None:
    previous_status = previous.get("status") if previous else None
    if previous_status == current_status:
        return None
    if current_status == "PASS":
        return "RECOVERED" if previous_status in {"WARN", "BLOCK"} else None
    if previous_status in {None, "PASS"}:
        return "OPENED"
    if _RANK[current_status] > _RANK.get(str(previous_status), -1):
        return "ESCALATED"
    return "DEESCALATED"


def execute(
    *,
    now_ms: int | None = None,
    feature_status_path: str = DEFAULT_FEATURE_STATUS,
    alert_store_path: str = DEFAULT_ALERT_STORE,
    alert_status_path: str = DEFAULT_ALERT_STATUS,
    operator_ledger_path: str = DEFAULT_OPERATOR_LEDGER,
    emit_fn: Callable[..., dict] = emit,
) -> dict[str, Any]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    assessment = _assess(
        _load_feature_status(feature_status_path),
        now_ms=now,
    )
    Path(alert_store_path).parent.mkdir(parents=True, exist_ok=True)
    store = RealtimeDriftAlertStore(alert_store_path, create=True)
    try:
        if not store.chain_valid():
            raise RuntimeError("realtime drift alert chain is invalid")
        previous = store.latest_transition(INCIDENT_KEY)
        latest_before = store.latest_event()
        transition = _transition(str(assessment["status"]), previous)
        delivery_retry = bool(
            transition is None
            and latest_before
            and latest_before.get("event_type") == "DELIVERY_RESULT"
            and latest_before.get("local_status") == "FAILED"
            and previous
            and previous.get("status") == assessment["status"]
        )
        delivery = {
            "attempted": False,
            "local_status": "DEDUPLICATED",
            "external_status": "UNCONFIGURED",
        }
        transition_id = (
            str(previous.get("event_id"))
            if delivery_retry and previous
            else None
        )
        if transition is not None:
            transition_payload = {
                "schema_version": 1,
                "event_id": "",
                "incident_key": INCIDENT_KEY,
                "event_type": transition,
                "severity": assessment["status"],
                "observed_at_ms": now,
                "status": assessment["status"],
                "reason": assessment["reason"],
                "feature_generated_at_ms": assessment.get(
                    "feature_generated_at_ms"
                ),
                "calibration_id": assessment.get("calibration_id"),
                "status_counts": assessment.get("status_counts", {}),
                "safety": {
                    "diagnostic_only": True,
                    "automatic_action": False,
                    "promotion_authority": False,
                    "position_authority": False,
                    "order_submission": False,
                },
            }
            transition_id = _sha256(
                {key: value for key, value in transition_payload.items()
                 if key != "event_id"}
            )
            transition_payload["event_id"] = transition_id
            store.append(transition_payload)

        if transition is not None or delivery_retry:
            message = (
                f"{transition or 'DELIVERY_RETRY'} "
                f"{assessment['status']}: "
                f"{assessment['reason']}; counts="
                f"{_canonical(assessment.get('status_counts', {}))}; "
                f"calibration={assessment.get('calibration_id') or 'NONE'}"
            )
            external_configured = bool(
                os.environ.get("QUANTOS_TG_BOT_TOKEN")
                and os.environ.get("QUANTOS_TG_CHAT_ID")
            )
            try:
                emitted = emit_fn(
                    [message],
                    context="BINANCE_REALTIME_DRIFT",
                    now_ms=now,
                    ledger_path=operator_ledger_path,
                )
                local_status = (
                    "DELIVERED"
                    if int(emitted.get("n", 0)) == 1
                    else "FAILED"
                )
                external_status = (
                    "DELIVERED"
                    if external_configured and emitted.get("pushed") is True
                    else "FAILED"
                    if external_configured
                    else "UNCONFIGURED"
                )
            except Exception:
                local_status = "FAILED"
                external_status = (
                    "FAILED" if external_configured else "UNCONFIGURED"
                )
            delivery = {
                "attempted": True,
                "local_status": local_status,
                "external_status": external_status,
            }
            delivery_payload = {
                "schema_version": 1,
                "event_id": "",
                "incident_key": INCIDENT_KEY,
                "event_type": "DELIVERY_RESULT",
                "severity": assessment["status"],
                "observed_at_ms": now,
                "status": assessment["status"],
                "transition_event_id": transition_id,
                **delivery,
            }
            delivery_payload["event_id"] = _sha256(
                {key: value for key, value in delivery_payload.items()
                 if key != "event_id"}
            )
            store.append(delivery_payload)

        chain_valid = store.chain_valid()
        event_count = store.row_count()
        latest_event = store.latest_event()
    finally:
        store.close()
    if not chain_valid:
        raise RuntimeError("realtime drift alert chain became invalid")

    status = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": (
            "HEALTHY" if assessment["status"] == "PASS" else "ALERTING"
        ),
        "current": assessment,
        "incident": {
            "incident_key": INCIDENT_KEY,
            "transition": transition,
            "transition_event_id": transition_id,
            "delivery_retry": delivery_retry,
            "deduplicated": transition is None and not delivery_retry,
        },
        "delivery": {
            **delivery,
            "independent_worker": True,
            "operator_ledger_path": operator_ledger_path,
        },
        "journal": {
            "event_count": event_count,
            "chain_valid": chain_valid,
            "latest_event_type": (
                latest_event.get("event_type") if latest_event else None
            ),
        },
        "safety": {
            "diagnostic_only": True,
            "load_bearing": False,
            "automatic_action": False,
            "promotion_authority": False,
            "position_authority": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }
    atomic_write_json(alert_status_path, status)
    return status


def main() -> int:
    try:
        status = execute()
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "generated_at_ms": int(time.time() * 1_000),
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "safety": {
                "diagnostic_only": True,
                "automatic_action": False,
                "order_submission": False,
                "live_trading": False,
            },
        }
        atomic_write_json(DEFAULT_ALERT_STATUS, failure)
        print(_canonical(failure))
        return SCHEDULE_EXIT_FAILURE
    print(
        _canonical(
            {
                "status": status["status"],
                "current": status["current"],
                "incident": status["incident"],
                "delivery": status["delivery"],
                "journal": status["journal"],
            }
        )
    )
    if (
        status["delivery"]["attempted"]
        and status["delivery"]["local_status"] != "DELIVERED"
    ):
        return SCHEDULE_EXIT_FAILURE
    return SCHEDULE_EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
