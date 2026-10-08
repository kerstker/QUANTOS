import ast
import json
from pathlib import Path

from tools import binance_realtime_drift_alerts as alerts


def _feature_status(
    *,
    generated_at_ms: int = 10_000,
    drift_status: str = "BLOCK",
) -> dict:
    return {
        "schema_version": 2,
        "generated_at_ms": generated_at_ms,
        "status": "PASS",
        "batch": {"chain_valid": True},
        "drift": {
            "status": drift_status,
            "status_counts": {
                "PASS": 25,
                "WARN": 23,
                "BLOCK": 50,
            },
            "policy": {
                "calibration": "EMPIRICALLY_CALIBRATED_PIT_V1",
                "calibration_id": "a" * 64,
            },
        },
    }


def test_alert_worker_opens_delivers_deduplicates_and_recovers(tmp_path):
    feature_path = tmp_path / "feature.json"
    feature_path.write_text(json.dumps(_feature_status()), encoding="utf-8")
    delivered = []

    def fake_emit(items, **kwargs):
        delivered.append((items, kwargs))
        return {"n": len(items), "pushed": False}

    kwargs = {
        "feature_status_path": str(feature_path),
        "alert_store_path": str(tmp_path / "alerts.db"),
        "alert_status_path": str(tmp_path / "alert_status.json"),
        "operator_ledger_path": str(tmp_path / "operator.jsonl"),
        "emit_fn": fake_emit,
    }
    opened = alerts.execute(now_ms=20_000, **kwargs)
    duplicate = alerts.execute(now_ms=21_000, **kwargs)
    feature_path.write_text(
        json.dumps(_feature_status(drift_status="PASS")),
        encoding="utf-8",
    )
    recovered = alerts.execute(now_ms=22_000, **kwargs)

    assert opened["incident"]["transition"] == "OPENED"
    assert opened["delivery"]["local_status"] == "DELIVERED"
    assert opened["delivery"]["external_status"] == "UNCONFIGURED"
    assert duplicate["incident"]["transition"] is None
    assert duplicate["incident"]["deduplicated"] is True
    assert duplicate["incident"]["delivery_retry"] is False
    assert recovered["incident"]["transition"] == "RECOVERED"
    assert recovered["status"] == "HEALTHY"
    assert recovered["journal"]["event_count"] == 4
    assert recovered["journal"]["chain_valid"] is True
    assert len(delivered) == 2


def test_alert_worker_fail_closed_for_stale_or_uncalibrated_status(tmp_path):
    feature_path = tmp_path / "feature.json"
    status = _feature_status(generated_at_ms=1)
    status["drift"]["policy"]["calibration_id"] = None
    feature_path.write_text(json.dumps(status), encoding="utf-8")
    result = alerts.execute(
        now_ms=1_000_000,
        feature_status_path=str(feature_path),
        alert_store_path=str(tmp_path / "alerts.db"),
        alert_status_path=str(tmp_path / "status.json"),
        operator_ledger_path=str(tmp_path / "operator.jsonl"),
        emit_fn=lambda items, **kwargs: {"n": len(items), "pushed": False},
    )
    assert result["current"]["status"] == "BLOCK"
    assert result["current"]["reason"] == "feature_status_stale"
    assert result["incident"]["transition"] == "OPENED"


def test_failed_local_delivery_retries_until_success(tmp_path):
    feature_path = tmp_path / "feature.json"
    feature_path.write_text(json.dumps(_feature_status()), encoding="utf-8")
    attempts = []

    def flaky_emit(items, **kwargs):
        attempts.append(items)
        if len(attempts) == 1:
            raise OSError("operator ledger unavailable")
        return {"n": len(items), "pushed": False}

    kwargs = {
        "feature_status_path": str(feature_path),
        "alert_store_path": str(tmp_path / "alerts.db"),
        "alert_status_path": str(tmp_path / "status.json"),
        "operator_ledger_path": str(tmp_path / "operator.jsonl"),
        "emit_fn": flaky_emit,
    }
    failed = alerts.execute(now_ms=20_000, **kwargs)
    retried = alerts.execute(now_ms=21_000, **kwargs)
    settled = alerts.execute(now_ms=22_000, **kwargs)

    assert failed["delivery"]["local_status"] == "FAILED"
    assert retried["incident"]["delivery_retry"] is True
    assert retried["delivery"]["local_status"] == "DELIVERED"
    assert settled["incident"]["deduplicated"] is True
    assert settled["delivery"]["attempted"] is False
    assert len(attempts) == 2


def test_alert_worker_has_no_agent_or_execution_authority():
    source = Path(alerts.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        module.startswith(
            (
                "services.intelligence",
                "services.alpha",
                "services.execution",
                "services.trading",
                "live",
            )
        )
        for module in imports
    )
    for token in ("submit_order", "place_order", "approve_alpha"):
        assert token not in source
