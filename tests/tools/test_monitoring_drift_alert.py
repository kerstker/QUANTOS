from tools.monitoring_drift_alert import run_monitoring_drift_alerts


def test_driver_uses_injected_payload_and_emitter_without_side_effects():
    calls = []

    def emit(alerts, *, context, now_ms):
        calls.append((list(alerts), context, now_ms))
        return {"n": len(alerts), "pushed": False}

    result = run_monitoring_drift_alerts(
        now_ms=123,
        payload={
            "data_provenance": "PARTIAL_ALPHA_DECAY_REAL",
            "monitoring_mode": "READ_ONLY_MONITORING_DRIFT",
            "monitors": [
                {
                    "category": "DATA_DISTRIBUTION_DRIFT",
                    "subject_id": "crypto_daily",
                    "status": "WATCH",
                    "metric": "ks_distance",
                    "drift_score": 0.22,
                    "breach_threshold": 0.35,
                    "provenance": "PIT_DISTRIBUTION_TRAILING_KS_V1",
                }
            ],
        },
        alert_emitter=emit,
    )

    assert result["mode"] == "READ_ONLY_MONITORING_ALERTS"
    assert result["live_authorization_allowed"] is False
    assert result["alert_count"] == 1
    assert calls == [
        (
            ["DATA_DISTRIBUTION_DRIFT crypto_daily WATCH: ks_distance=0.22 (breach_threshold=0.35)"],
            "monitoring_drift",
            123,
        )
    ]
