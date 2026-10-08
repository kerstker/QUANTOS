"""Concrete read-only risk adapter for the console composition root."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Mapping, Sequence

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS
from quantos.contracts.killswitch import KillSwitchState
from services.monitoring.audit_store import DurableAuditLog
from services.monitoring.health import build_snapshot
from services.risk.pre_trade.checks import CHECK_REGISTRY
from services.risk.pre_trade.kill_switch import KillSwitch
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk


class DefaultRiskConsolePort:
    def risk_decision(
        self,
        alpha_id: str,
        returns: Sequence[float],
        equity_points: Sequence[Mapping[str, float]],
        capacity: float,
    ) -> dict[str, Any]:
        """Return a read-only risk snapshot without manufacturing admission.

        The console port does not receive the immutable alpha metadata, PIT
        regime labels, and per-leg capacity inputs required by the complete
        nine-engine gate.  The four-engine result is retained as a diagnostic
        panel, while the authoritative decision stays ``PENDING``/rejected.
        This prevents a UI proxy from being mistaken for a promotion or paper
        order approval.
        """
        weights = {f"{alpha_id}-{index}": 0.1 for index in range(10)}
        returns_used = list(returns[:120])
        equity_curve = [point["equity"] for point in equity_points]
        subset = evaluate_dry_run_risk(
            returns_used,
            weights,
            equity_curve,
            timestamps=list(range(len(returns_used))),
        )
        full = evaluate_dry_run_risk(
            returns_used,
            weights,
            equity_curve,
            timestamps=list(range(len(returns_used))),
            require_full_inputs=True,
        )
        stress_report = dict((subset.get("reports") or {}).get("stress") or {})
        if stress_report:
            stress_report["diagnostic_only"] = True
            stress_report["admission_provenance"] = "REAL_RESEARCH_RISK_FULL_PENDING"
        repro_payload = {
            "alpha_id": alpha_id,
            "weights": weights,
            "returns": returns_used,
            "equity_curve": equity_curve,
            "risk": full,
        }
        reproducibility_hash = hashlib.sha256(
            json.dumps(
                repro_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "run_id": f"risk-pending-{alpha_id}",
            "portfolio_id": f"portfolio-{alpha_id}",
            "decision": full["decision"],
            "status": full["status"],
            "gates_passed": full["gates_passed"],
            "gates_run": full["gates_run"],
            "gates_total": 9,
            "gate_results": dict(full.get("engines_run") or {}),
            "engines_pending": dict(full.get("engines_pending") or {}),
            "engines_skipped": dict(full.get("engines_skipped") or {}),
            "reasons": list(full.get("reasons") or []),
            "provenance": full["provenance"],
            "admission_allowed": False,
            "reproducibility_hash": reproducibility_hash,
            "stress": stress_report or None,
            "diagnostic_subset": subset,
        }

    def monitoring_snapshot(
        self, *, audit_db: str, kill_switch_state: KillSwitchState
    ) -> dict[str, Any]:
        try:
            kill_switch = KillSwitch(state=kill_switch_state)
            if not os.path.exists(audit_db):
                return {
                    "provenance": "NO_DURABLE_AUDIT_CHAIN",
                    "audit_chain_valid": None,
                    "kill_switch_active": kill_switch.is_active(),
                    "healthy": None,
                    "anomalies": [],
                    "audit_events": 0,
                }
            audit = DurableAuditLog(audit_db, create=False, read_only=True)
            try:
                snapshot = build_snapshot(
                    timestamp=0, audit_log=audit, kill_switch=kill_switch
                )
                audit_events = len(audit.events)
            finally:
                audit.close()
            return {
                "provenance": "REAL_MONITORING_SNAPSHOT",
                "audit_chain_valid": snapshot.audit_chain_valid,
                "kill_switch_active": snapshot.kill_switch_active,
                "healthy": snapshot.healthy,
                "anomalies": list(snapshot.anomalies),
                "audit_events": audit_events,
            }
        except Exception:
            return {
                "provenance": "MONITORING_ERROR",
                "audit_chain_valid": False,
                "healthy": False,
                "kill_switch_active": True,
                "anomalies": ["MONITORING_ERROR"],
                "audit_events": 0,
            }

    def safety_primitives(
        self, *, kill_switch_state: KillSwitchState
    ) -> dict[str, Any]:
        kill_switch = KillSwitch(state=kill_switch_state)
        checks = [
            {"name": name, "stage": stage}
            for name, stage, _function in CHECK_REGISTRY
        ]
        config = RESEARCH_SAFE_DEFAULTS
        return {
            "safety_mode": "L2_DRY_RUN_ONLY",
            "kill_switch": {
                "is_active": kill_switch.is_active(),
                "trip_count": kill_switch.state.trip_count,
                "fail_closed": True,
                "human_only_reset": True,
                "auto_reset": False,
            },
            "pre_trade_gate": {
                "checks_registered": len(checks),
                "checks": checks,
                "fail_closed_on_unknown": True,
                "refuses_unsafe_build": True,
            },
            "safety_config": {
                "live_trading_enabled": config.live_trading_enabled,
                "real_broker_enabled": config.real_broker_enabled,
                "allow_order_submission": config.allow_order_submission,
                "allow_ai_direct_trading": config.allow_ai_direct_trading,
                "require_human_approval": config.require_human_approval,
            },
            "live_authorization_allowed": False,
        }


DEFAULT_RISK_CONSOLE_PORT = DefaultRiskConsolePort()


__all__ = ["DEFAULT_RISK_CONSOLE_PORT", "DefaultRiskConsolePort"]
