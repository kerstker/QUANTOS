from __future__ import annotations

import json

import pytest

from tools.external_heartbeat import (
    ExternalHeartbeatConfigurationError,
    build_heartbeat_payload,
    send_external_heartbeat,
)


def _write_status(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_heartbeat_delivers_only_bounded_health_fields_and_redacts_endpoint(tmp_path):
    scheduler = tmp_path / "scheduler.json"
    event = tmp_path / "event.json"
    backup = tmp_path / "backup.json"
    output = tmp_path / "heartbeat.json"
    _write_status(
        scheduler,
        {
            "verdict": "PARTIAL",
            "alert_signature": "a" * 64,
            "summary": {"PASS": 6, "PARTIAL": 1, "UNAVAILABLE": 1, "BREACH": 0},
            "checks": [{"detail": "must not leave host"}],
        },
    )
    _write_status(
        event,
        {
            "verdict": "PASS",
            "generated_at_ms": 10,
            "integrity": {"event_chain_valid": True},
            "worker_identity_policy": "PROCESS_DECLARED_UNAUTHENTICATED",
            "pending_events": 0,
            "dead_letter_count": 0,
            "alerts": [{"detail": "must not leave host"}],
        },
    )
    _write_status(
        backup,
        {
            "verdict": "PASS",
            "last_success_at_ms": 9,
            "destination_class": "SEPARATE_VOLUME",
            "batch_dir": "must-not-leave-host",
        },
    )
    seen = {}

    def sender(url, body, timeout):
        seen.update(url=url, body=body, timeout=timeout)
        return 204

    endpoint = "https://health.example.invalid/ping/secret-token"
    result = send_external_heartbeat(
        endpoint=endpoint,
        scheduler_status_path=scheduler,
        event_status_path=event,
        backup_status_path=backup,
        output_path=output,
        now_ms=20,
        sender=sender,
    )

    assert result["verdict"] == "PASS"
    assert result["delivery_status"] == 204
    assert result["endpoint_fingerprint"]
    assert endpoint not in json.dumps(result)
    payload = json.loads(seen["body"])
    assert payload == build_heartbeat_payload(
        now_ms=20,
        scheduler_status_path=scheduler,
        event_status_path=event,
        backup_status_path=backup,
    )
    serialized = json.dumps(payload)
    assert "must not leave host" not in serialized
    assert "checks" not in serialized
    assert "alerts" not in serialized
    assert payload["components"]["event_authority"][
        "worker_identity_policy"
    ] == "PROCESS_DECLARED_UNAUTHENTICATED"
    assert json.loads(output.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://health.example.invalid/ping",
        "https://user:password@health.example.invalid/ping",
        "https://health.example.invalid/ping#fragment",
        "https://localhost/ping",
        "https://127.0.0.1/ping",
        "https://[::1]/ping",
        "not-a-url",
    ],
)
def test_heartbeat_rejects_unsafe_endpoint(endpoint, tmp_path):
    with pytest.raises(ExternalHeartbeatConfigurationError):
        send_external_heartbeat(
            endpoint=endpoint,
            scheduler_status_path=tmp_path / "scheduler.json",
            event_status_path=tmp_path / "event.json",
            backup_status_path=tmp_path / "backup.json",
            output_path=tmp_path / "output.json",
            now_ms=1,
            sender=lambda *_: 204,
        )


def test_heartbeat_delivery_failure_records_only_error_type(tmp_path):
    output = tmp_path / "heartbeat.json"

    def sender(_url, _body, _timeout):
        raise TimeoutError("secret endpoint detail")

    result = send_external_heartbeat(
        endpoint="https://health.example.invalid/ping/token",
        scheduler_status_path=tmp_path / "scheduler.json",
        event_status_path=tmp_path / "event.json",
        backup_status_path=tmp_path / "backup.json",
        output_path=output,
        now_ms=1,
        sender=sender,
    )

    assert result["verdict"] == "BREACH"
    assert result["error_type"] == "TimeoutError"
    assert "secret endpoint detail" not in json.dumps(result)


def test_heartbeat_drops_unrecognized_destination_class(tmp_path):
    backup = tmp_path / "backup.json"
    _write_status(
        backup,
        {
            "verdict": "PASS",
            "destination_class": "secret material that must not leave host",
        },
    )
    payload = build_heartbeat_payload(
        now_ms=1,
        scheduler_status_path=tmp_path / "scheduler.json",
        event_status_path=tmp_path / "event.json",
        backup_status_path=backup,
    )
    assert payload["components"]["backup"]["destination_class"] == ""


def test_heartbeat_preserves_posix_separate_filesystem_destination(tmp_path):
    backup = tmp_path / "backup.json"
    _write_status(
        backup,
        {"verdict": "PASS", "destination_class": "SEPARATE_FILESYSTEM"},
    )

    payload = build_heartbeat_payload(
        now_ms=1,
        scheduler_status_path=tmp_path / "scheduler.json",
        event_status_path=tmp_path / "event.json",
        backup_status_path=backup,
    )

    assert (
        payload["components"]["backup"]["destination_class"]
        == "SEPARATE_FILESYSTEM"
    )


def test_heartbeat_drops_unrecognized_worker_identity_policy(tmp_path):
    event = tmp_path / "event.json"
    _write_status(
        event,
        {
            "verdict": "PASS",
            "worker_identity_policy": "secret material that must not leave host",
        },
    )

    payload = build_heartbeat_payload(
        now_ms=1,
        scheduler_status_path=tmp_path / "scheduler.json",
        event_status_path=event,
        backup_status_path=tmp_path / "backup.json",
    )

    assert payload["components"]["event_authority"][
        "worker_identity_policy"
    ] == ""
