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


@pytest.mark.parametrize("writer_offset_ms, expected", [(0, "healthy"), (1000, "degraded")])
def test_live_health_checks_receipt_time_after_monitoring_work(monkeypatch, tmp_path, writer_offset_ms, expected):
    from services.console.api import system_views

    clock = [1000.0]
    monkeypatch.setattr(runtime_health, "_STATE_ROOT", tmp_path)
    monkeypatch.setattr(system_views.time, "time", lambda: clock[0])
    monkeypatch.setattr(system_views, "_ashare_search_runtime_service", lambda: {"service": "ashare-test"})
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)

    def monitoring():
        # A producer publishes while the request is doing other monitoring work.
        clock[0] = 1001.0
        (cache / "bybit_realtime_stream_status.json").write_text(json.dumps({
            "verdict": "PASS", "generated_at_ms": 1001000 + writer_offset_ms,
        }))
        return {"healthy": True}

    monkeypatch.setattr(system_views, "_real_monitoring_snapshot", monitoring)
    payload = system_views._system_health_payload()
    row = next(item for item in payload["services"] if item["service"] == "bybit-public-market-stream")
    assert row["status"] == expected
    if writer_offset_ms:
        assert row["reason_code"] == "INVALID_RECEIPT_TIMESTAMP"
    else:
        assert row["age_ms"] == 0 and row["reason_code"] is None
