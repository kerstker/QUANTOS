from services.monitoring.monitoring_alerts import build_monitoring_alerts


def _monitor(**overrides):
    row = {
        "category": "FEATURE_DRIFT",
        "subject_id": "fx_momentum_20",
        "status": "BREACH",
        "metric": "ks_distance",
        "drift_score": 0.41,
        "breach_threshold": 0.35,
        "provenance": "PIT_FEATURE_TRAILING_KS_DRIFT_V1",
    }
    row.update(overrides)
    return row


def test_alerts_include_honest_feature_rows_only():
    payload = {
        "data_provenance": "PARTIAL_ALPHA_DECAY_REAL",
        "monitors": [_monitor(), _monitor(subject_id="synthetic", provenance="SYNTHETIC_FEATURE_VALUES")],
    }
    assert build_monitoring_alerts(payload) == [
        "FEATURE_DRIFT fx_momentum_20 BREACH: ks_distance=0.41 (breach_threshold=0.35)"
    ]


def test_synthetic_alpha_rows_are_not_operational_alerts():
    payload = {
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        "monitors": [_monitor(category="ALPHA_DECAY", provenance="")],
    }
    assert build_monitoring_alerts(payload) == []


def test_synthetic_freshness_and_review_rows_are_not_operational_alerts():
    payload = {
        "monitors": [
            _monitor(category="DATA_FRESHNESS", provenance="SYNTHETIC_DETERMINISTIC"),
            _monitor(category="MODEL_RISK_AGING", provenance=""),
        ]
    }
    assert build_monitoring_alerts(payload) == []


def test_malformed_monitor_payload_fails_closed_to_no_alerts():
    assert build_monitoring_alerts({"monitors": "not-a-list"}) == []
