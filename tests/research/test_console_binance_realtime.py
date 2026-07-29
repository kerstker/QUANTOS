import json

from services.console.api import data_catalog


def test_systemic_rate_limit_telemetry_reaches_console_payload(
    tmp_path,
    monkeypatch,
) -> None:
    now_ms = 9_000_000_000_000
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)
    status = {
        "generated_at_ms": now_ms - 1_000,
        "verdict": "PASS",
        "complete": True,
        "coverage": {"requested": 14, "complete": 14, "failed": 0},
        "errors": {},
        "store": {"chain_valid": True, "row_count": 42},
        "request_rate_limit": {
            "planned_ip_weight": 56,
            "endpoint_weight_per_symbol": 4,
            "configured_max_workers": 1,
            "min_request_interval_ms": 250,
            "cooldown_active": False,
            "last_status_code": None,
            "retry_after_seconds": 0,
            "max_used_weights": {"X-MBX-USED-WEIGHT-1M": 66},
        },
    }
    (cache / "binance_systemic_risk_status.json").write_text(
        json.dumps(status),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(data_catalog.time, "time", lambda: now_ms / 1_000)

    payload = data_catalog._binance_realtime_payload()

    assert payload["systemic_risk"]["fresh"] is True
    assert payload["systemic_risk"]["request_rate_limit"] == {
        "planned_ip_weight": 56,
        "endpoint_weight_per_symbol": 4,
        "configured_max_workers": 1,
        "min_request_interval_ms": 250,
        "cooldown_active": False,
        "last_status_code": None,
        "retry_after_seconds": 0,
        "max_used_weights": {"X-MBX-USED-WEIGHT-1M": 66},
    }
