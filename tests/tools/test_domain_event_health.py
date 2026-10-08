import json

from tools.domain_event_health import run_event_bus_health


def _health(verdict: str) -> dict:
    alerts = []
    if verdict != "PASS":
        alerts = [
            {
                "code": "CONSUMER_LAG_EVENTS_EXCEEDED",
                "observed": 12,
                "severity": "CRITICAL",
                "threshold": 10,
            }
        ]
    return {
        "schema_version": 1,
        "verdict": verdict,
        "checked_at_ms": 100,
        "group_id": "domain-event-projection-v1",
        "retention_mode": "APPEND_ONLY_ALERT_ONLY",
        "alerts": alerts,
    }


def test_health_driver_persists_snapshot_and_emits_structured_alert(tmp_path):
    delivered = []

    def fake_reader(**kwargs):
        assert kwargs["now_ms"] == 100
        assert kwargs["path"] == "events.db"
        return _health("BREACH")

    def fake_emit(alerts, **kwargs):
        delivered.extend(alerts)
        assert kwargs == {"context": "domain_event_bus", "now_ms": 100}
        return {"n": len(alerts), "pushed": False}

    output = tmp_path / "health.json"
    document = run_event_bus_health(
        now_ms=100,
        event_path="events.db",
        output_path=str(output),
        health_reader=fake_reader,
        alert_emitter=fake_emit,
    )

    assert document["verdict"] == "BREACH"
    assert document["alert_delivery"] == {"n": 1, "pushed": False}
    assert delivered == [
        "domain event bus CONSUMER_LAG_EVENTS_EXCEEDED: observed=12 threshold=10"
    ]
    assert json.loads(output.read_text(encoding="utf-8")) == document


def test_health_driver_keeps_pass_snapshot_silent(tmp_path):
    delivered = []

    def fake_emit(alerts, **kwargs):
        delivered.extend(alerts)
        return {"n": len(alerts), "pushed": False}

    document = run_event_bus_health(
        now_ms=200,
        event_path="events.db",
        output_path=str(tmp_path / "health.json"),
        health_reader=lambda **kwargs: _health("PASS"),
        alert_emitter=fake_emit,
    )

    assert delivered == []
    assert document["alert_delivery"]["n"] == 0

