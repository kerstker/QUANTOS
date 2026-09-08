import json

import pytest

from services.console.api import runtime_health


@pytest.mark.parametrize("timestamp", [True, -1, 1001, "1000", None])
def test_invalid_receipt_time_cannot_report_healthy(tmp_path, monkeypatch, timestamp):
    monkeypatch.setattr(runtime_health, "_STATE_ROOT", tmp_path)
    cache = tmp_path / "data/cache"
    cache.mkdir(parents=True)
    (cache / "receipt.json").write_text(json.dumps({"status": "PASS", "generated_at_ms": timestamp}))
    row = runtime_health.receipt_service(
        service="test", filename="receipt.json", now_ms=1000,
        max_age_ms=10000, healthy_verdicts=frozenset({"PASS"}),
    )
    assert row["status"] != "healthy"
    assert row["age_ms"] is None
    assert row["reason_code"] == "INVALID_RECEIPT_TIMESTAMP"


def test_failed_backup_receipt_exposes_reason_without_inventing_latency(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_health, "_STATE_ROOT", tmp_path)
    cache = tmp_path / "data/cache"
    cache.mkdir(parents=True)
    (cache / "receipt.json").write_text(json.dumps({
        "verdict": "BREACH", "observed_at_ms": 900,
        "last_reason_code": "RESTORE_RTO_EXCEEDED",
    }))
    row = runtime_health.receipt_service(
        service="backup", filename="receipt.json", now_ms=1000,
        max_age_ms=10000, healthy_verdicts=frozenset({"PASS"}),
    )
    assert row["status"] == "down"
    assert row["reason_code"] == "RESTORE_RTO_EXCEEDED"
    assert row["age_ms"] == 100
    assert row["observed_at_ms"] == 900
    assert row["latency_ms"] is None
