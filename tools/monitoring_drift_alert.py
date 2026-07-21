"""Emit honest monitoring-drift alerts through the existing local ledger."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import the official composition root so the read-only monitoring dependencies
# are configured when this module is used as a scheduled CLI driver.
from services.console import main as _console_composition  # noqa: F401
from services.console.api.monitoring_views import _has_real_factors, _monitoring_drift_payload
from services.monitoring.monitoring_alerts import build_monitoring_alerts
from tools.alert_sink import emit


def run_monitoring_drift_alerts(
    *,
    now_ms: int | None = None,
    payload: Mapping[str, Any] | None = None,
    alert_emitter: Callable[..., dict[str, Any]] = emit,
) -> dict[str, Any]:
    """Build and deliver monitoring alerts without changing trading state."""
    if payload is None:
        resolved_payload = dict(_monitoring_drift_payload())
        resolved_payload["data_provenance"] = (
            "PARTIAL_ALPHA_DECAY_REAL" if _has_real_factors() else "SYNTHETIC_DETERMINISTIC"
        )
    else:
        resolved_payload = payload
    alerts = build_monitoring_alerts(resolved_payload)
    observed_at = int(time.time() * 1000) if now_ms is None else int(now_ms)
    delivery = alert_emitter(
        alerts,
        context="monitoring_drift",
        now_ms=observed_at,
    )
    return {
        "schema_version": 1,
        "mode": "READ_ONLY_MONITORING_ALERTS",
        "observed_at_ms": observed_at,
        "data_provenance": str(resolved_payload.get("data_provenance", "UNKNOWN")),
        "monitoring_mode": str(resolved_payload.get("monitoring_mode", "UNKNOWN")),
        "alert_count": len(alerts),
        "alerts": alerts,
        "delivery": delivery,
        "live_authorization_allowed": False,
    }


def main() -> None:
    print(json.dumps(run_monitoring_drift_alerts(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ["run_monitoring_drift_alerts", "main"]
