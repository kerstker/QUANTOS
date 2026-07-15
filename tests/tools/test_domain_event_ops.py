import json

import pytest

from tools.domain_event_ops import exit_code_for_verdict, run_domain_event_ops


def test_domain_event_ops_runs_drain_before_health_and_records_pass(tmp_path):
    calls = []
    emitted = []

    def drain(**kwargs):
        calls.append(("drain", kwargs))
        return {
            "verdict": "PASS",
            "sources": {"dry_run": {"status": "PASS"}},
            "projection": {"status": "PASS"},
        }

    def health(**kwargs):
        calls.append(("health", kwargs))
        return {
            "verdict": "PASS",
            "alerts": [],
            "alert_delivery": {"n": 0, "pushed": False},
        }

    def emit(alerts, **kwargs):
        emitted.append((list(alerts), kwargs))
        return {"n": len(alerts), "pushed": False}

    output = tmp_path / "ops.json"
    document = run_domain_event_ops(
        now_ms=100,
        event_path=str(tmp_path / "events.db"),
        projection_path=str(tmp_path / "projection.db"),
        drain_output_path=str(tmp_path / "drain.json"),
        health_output_path=str(tmp_path / "health.json"),
        output_path=str(output),
        drain_runner=drain,
        health_runner=health,
        alert_emitter=emit,
    )

    assert [name for name, _ in calls] == ["drain", "health"]
    assert calls[0][1]["now_ms"] == 100
    assert calls[1][1]["now_ms"] == 100
    assert document["verdict"] == "PASS"
    assert document["mode"] == "L2_DRY_RUN_EVENT_OPERATIONS"
    assert document["safety"]["order_submission"] is False
    assert document["safety"]["domain_state_mutation"] is False
    assert emitted == []
    assert json.loads(output.read_text(encoding="utf-8")) == document
    assert exit_code_for_verdict(document["verdict"]) == 0


def test_nonpass_drain_is_alerted_and_health_still_runs(tmp_path):
    calls = []
    emitted = []

    def drain(**kwargs):
        calls.append("drain")
        return {
            "verdict": "PARTIAL",
            "sources": {"missing": {"status": "UNAVAILABLE"}},
            "projection": {"status": "PASS"},
        }

    def health(**kwargs):
        calls.append("health")
        return {"verdict": "PASS", "alerts": []}

    def emit(alerts, **kwargs):
        emitted.extend(alerts)
        return {"n": len(alerts), "pushed": False}

    document = run_domain_event_ops(
        now_ms=200,
        output_path=str(tmp_path / "ops.json"),
        drain_runner=drain,
        health_runner=health,
        alert_emitter=emit,
    )

    assert calls == ["drain", "health"]
    assert document["verdict"] == "PARTIAL"
    assert emitted == ["domain event recovery DOMAIN_EVENT_DRAIN_PARTIAL"]
    assert document["alert_delivery"] == {"n": 1, "pushed": False}
    assert exit_code_for_verdict(document["verdict"]) == 2


def test_drain_exception_is_redacted_and_does_not_block_health(tmp_path):
    health_calls = []
    emitted = []

    def broken_drain(**kwargs):
        raise RuntimeError("credential=must-not-leak")

    def health(**kwargs):
        health_calls.append(kwargs)
        return {"verdict": "PASS", "alerts": []}

    def emit(alerts, **kwargs):
        emitted.extend(alerts)
        return {"n": len(alerts), "pushed": False}

    document = run_domain_event_ops(
        now_ms=300,
        output_path=str(tmp_path / "ops.json"),
        drain_runner=broken_drain,
        health_runner=health,
        alert_emitter=emit,
    )

    encoded = json.dumps(document)
    assert health_calls
    assert document["verdict"] == "BREACH"
    assert document["drain"] == {
        "verdict": "BREACH",
        "status": "EXECUTION_FAILED",
        "error_type": "RuntimeError",
    }
    assert "must-not-leak" not in encoded
    assert emitted == ["domain event recovery DOMAIN_EVENT_DRAIN_EXCEPTION:RuntimeError"]
    assert exit_code_for_verdict(document["verdict"]) == 1


def test_health_exception_is_redacted_after_successful_drain(tmp_path):
    emitted = []

    def drain(**kwargs):
        return {"verdict": "PASS", "sources": {}, "projection": {"status": "PASS"}}

    def broken_health(**kwargs):
        raise OSError("token=must-not-leak")

    def emit(alerts, **kwargs):
        emitted.extend(alerts)
        return {"n": len(alerts), "pushed": False}

    document = run_domain_event_ops(
        now_ms=400,
        output_path=str(tmp_path / "ops.json"),
        drain_runner=drain,
        health_runner=broken_health,
        alert_emitter=emit,
    )

    assert document["verdict"] == "BREACH"
    assert document["health"] == {
        "verdict": "BREACH",
        "status": "EXECUTION_FAILED",
        "error_type": "OSError",
    }
    assert "must-not-leak" not in json.dumps(document)
    assert emitted == ["domain event health DOMAIN_EVENT_HEALTH_EXCEPTION:OSError"]


def test_alert_delivery_failure_is_visible_without_leaking_message(tmp_path):
    def drain(**kwargs):
        return {"verdict": "UNAVAILABLE", "sources": {}, "projection": {}}

    def health(**kwargs):
        return {"verdict": "PASS", "alerts": []}

    def broken_emit(alerts, **kwargs):
        raise ValueError("webhook-secret=must-not-leak")

    document = run_domain_event_ops(
        now_ms=500,
        output_path=str(tmp_path / "ops.json"),
        drain_runner=drain,
        health_runner=health,
        alert_emitter=broken_emit,
    )

    assert document["verdict"] == "BREACH"
    assert document["alert_delivery"] == {
        "status": "BREACH",
        "error_type": "ValueError",
    }
    assert "must-not-leak" not in json.dumps(document)


@pytest.mark.parametrize("value", [-1, True, 1.5, "100"])
def test_domain_event_ops_rejects_invalid_time(value, tmp_path):
    with pytest.raises((TypeError, ValueError)):
        run_domain_event_ops(now_ms=value, output_path=str(tmp_path / "ops.json"))


def test_exit_code_rejects_unknown_verdict():
    with pytest.raises(ValueError, match="unknown domain event ops verdict"):
        exit_code_for_verdict("MAYBE")
