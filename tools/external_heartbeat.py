"""Send a bounded QUANTOS L2 health heartbeat to an operator-owned HTTPS sink."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable
import urllib.parse
import urllib.request

from services.monitoring.consumer_group import (
    PROCESS_DECLARED_WORKER_ASSURANCE,
)
from services.governance.external_evidence import (
    ContinuityAssessment,
    ContinuityObservation,
    evaluate_continuity,
)
from tools.cache_io import atomic_write_json


ENDPOINT_ENV = "QUANTOS_HEARTBEAT_URL"
DEFAULT_OUTPUT = os.path.join("data", "cache", "external_heartbeat_status.json")
DEFAULT_SCHEDULER_STATUS = os.path.join(
    "data", "cache", "scheduler_watchdog_status.json"
)
DEFAULT_EVENT_STATUS = os.path.join("data", "cache", "domain_event_health.json")
DEFAULT_BACKUP_STATUS = os.path.join("data", "cache", "l2_backup_status.json")


class ExternalHeartbeatConfigurationError(ValueError):
    """The endpoint could expose credentials or cannot provide HTTPS transport."""


def _load_document(path: str | Path) -> dict | None:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _bounded_int(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _verdict(document: dict | None) -> str:
    if document is None:
        return "UNAVAILABLE"
    value = document.get("verdict")
    return value if value in {"PASS", "PARTIAL", "WARN", "BREACH"} else "UNAVAILABLE"


def build_heartbeat_payload(
    *,
    now_ms: int,
    scheduler_status_path: str | Path,
    event_status_path: str | Path,
    backup_status_path: str | Path,
) -> dict:
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    scheduler = _load_document(scheduler_status_path)
    event = _load_document(event_status_path)
    backup = _load_document(backup_status_path)

    scheduler_summary = {}
    if scheduler is not None and isinstance(scheduler.get("summary"), dict):
        scheduler_summary = {
            key: int(scheduler["summary"].get(key, 0))
            for key in ("PASS", "PARTIAL", "UNAVAILABLE", "BREACH")
            if type(scheduler["summary"].get(key, 0)) is int
            and scheduler["summary"].get(key, 0) >= 0
        }
    alert_signature = scheduler.get("alert_signature") if scheduler else None
    if (
        not isinstance(alert_signature, str)
        or len(alert_signature) != 64
        or any(character not in "0123456789abcdef" for character in alert_signature)
    ):
        alert_signature = ""

    event_integrity = event.get("integrity") if event else None
    if not isinstance(event_integrity, dict):
        event_integrity = {}
    bounded_integrity = {
        key: event_integrity.get(key)
        if event_integrity.get(key) in {True, False, None}
        else None
        for key in (
            "event_chain_valid",
            "consumer_chain_valid",
            "coordination_chain_valid",
        )
    }
    component_verdicts = {
        "scheduler": _verdict(scheduler),
        "event_authority": _verdict(event),
        "backup": _verdict(backup),
    }
    overall_state = "PASS"
    if "BREACH" in component_verdicts.values():
        overall_state = "BREACH"
    elif any(value != "PASS" for value in component_verdicts.values()):
        overall_state = "PARTIAL"

    destination_class = backup.get("destination_class") if backup else None
    if destination_class not in {
        "OFF_HOST_UNC",
        "SEPARATE_VOLUME",
        "LOCAL_ONLY",
    }:
        destination_class = ""

    worker_identity_policy = (
        event.get("worker_identity_policy") if event else None
    )
    if worker_identity_policy != PROCESS_DECLARED_WORKER_ASSURANCE:
        worker_identity_policy = ""

    return {
        "schema_version": 1,
        "system": "QUANTOS",
        "readiness": "L2_DRY_RUN_ONLY",
        "observed_at_ms": now_ms,
        "overall_state": overall_state,
        "components": {
            "scheduler": {
                "verdict": component_verdicts["scheduler"],
                "alert_signature": alert_signature,
                "summary": scheduler_summary,
            },
            "event_authority": {
                "verdict": component_verdicts["event_authority"],
                "generated_at_ms": _bounded_int(
                    event.get("generated_at_ms") if event else None
                ),
                "integrity": bounded_integrity,
                "pending_events": _bounded_int(
                    event.get("pending_events") if event else None
                ),
                "dead_letter_count": _bounded_int(
                    event.get("dead_letter_count") if event else None
                ),
                "worker_identity_policy": worker_identity_policy,
            },
            "backup": {
                "verdict": component_verdicts["backup"],
                "last_success_at_ms": _bounded_int(
                    backup.get("last_success_at_ms") if backup else None
                ),
                "destination_class": (
                    destination_class
                ),
            },
        },
    }


def _validate_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint:
        raise ExternalHeartbeatConfigurationError("heartbeat endpoint is required")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ExternalHeartbeatConfigurationError(
            "heartbeat endpoint must be an absolute HTTPS URL"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ExternalHeartbeatConfigurationError(
            "heartbeat endpoint must not use URL userinfo credentials"
        )
    if parsed.fragment:
        raise ExternalHeartbeatConfigurationError(
            "heartbeat endpoint must not contain a fragment"
        )
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ExternalHeartbeatConfigurationError(
            "heartbeat endpoint must not be loopback"
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if address.is_loopback:
            raise ExternalHeartbeatConfigurationError(
                "heartbeat endpoint must not be loopback"
            )
    return endpoint


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _https_post(url: str, body: bytes, timeout: int) -> int:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "QUANTOS-L2-Heartbeat/1",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        return int(response.status)


def send_external_heartbeat(
    *,
    endpoint: str,
    scheduler_status_path: str | Path,
    event_status_path: str | Path,
    backup_status_path: str | Path,
    output_path: str | Path,
    now_ms: int | None = None,
    timeout_seconds: int = 10,
    sender: Callable[[str, bytes, int], int] = _https_post,
) -> dict:
    resolved_endpoint = _validate_endpoint(endpoint)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive integer")
    observed_at_ms = int(time.time() * 1_000) if now_ms is None else now_ms
    payload = build_heartbeat_payload(
        now_ms=observed_at_ms,
        scheduler_status_path=scheduler_status_path,
        event_status_path=event_status_path,
        backup_status_path=backup_status_path,
    )
    body = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result = {
        "schema_version": 1,
        "verdict": "BREACH",
        "observed_at_ms": observed_at_ms,
        "endpoint_fingerprint": hashlib.sha256(
            resolved_endpoint.encode("utf-8")
        ).hexdigest(),
        "payload_sha256": hashlib.sha256(body).hexdigest(),
        "reported_state": payload["overall_state"],
        "delivery_status": None,
        "error_type": "",
    }
    try:
        status = sender(resolved_endpoint, body, timeout_seconds)
        result["delivery_status"] = status
        if 200 <= status < 300:
            result["verdict"] = "PASS"
        else:
            result["error_type"] = "HTTPStatus"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    atomic_write_json(os.fspath(output_path), result, indent=2)
    return result


def evaluate_heartbeat_continuity(
    observations: tuple[ContinuityObservation, ...],
    *,
    now_ms: int,
    max_gap_ms: int,
    minimum_history_span_ms: int,
    minimum_passes: int,
    clean_lookback_ms: int,
) -> ContinuityAssessment:
    """Evaluate retained heartbeat receipts without contacting the endpoint."""

    return evaluate_continuity(
        observations,
        now_ms=now_ms,
        max_gap_ms=max_gap_ms,
        minimum_history_span_ms=minimum_history_span_ms,
        minimum_passes=minimum_passes,
        clean_lookback_ms=clean_lookback_ms,
    )


def main() -> int:
    endpoint = os.environ.get(ENDPOINT_ENV)
    if not endpoint:
        result = {
            "schema_version": 1,
            "verdict": "UNAVAILABLE",
            "observed_at_ms": int(time.time() * 1_000),
            "endpoint_fingerprint": "",
            "payload_sha256": "",
            "reported_state": "UNAVAILABLE",
            "delivery_status": None,
            "error_type": "ConfigurationUnavailable",
        }
        atomic_write_json(DEFAULT_OUTPUT, result, indent=2)
        print(f"{ENDPOINT_ENV} is not configured", file=sys.stderr)
        return 2
    result = send_external_heartbeat(
        endpoint=endpoint,
        scheduler_status_path=DEFAULT_SCHEDULER_STATUS,
        event_status_path=DEFAULT_EVENT_STATUS,
        backup_status_path=DEFAULT_BACKUP_STATUS,
        output_path=DEFAULT_OUTPUT,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ExternalHeartbeatConfigurationError",
    "build_heartbeat_payload",
    "evaluate_heartbeat_continuity",
    "send_external_heartbeat",
]
