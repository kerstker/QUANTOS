"""Concrete read-only risk adapter for the console composition root."""
from __future__ import annotations

import dataclasses
import os
from typing import Any, Mapping, Sequence

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS
from quantos.contracts.killswitch import KillSwitchState
from services.monitoring.audit_store import DurableAuditLog
from services.monitoring.health import build_snapshot
from services.risk.pre_trade.checks import CHECK_REGISTRY
from services.risk.pre_trade.kill_switch import KillSwitch
from services.risk.research_risk.domain.gate import RiskApprovalGate
from services.risk.research_risk.domain.models import RiskConfig, RiskRun


class DefaultRiskConsolePort:
    def risk_decision(
        self,
        alpha_id: str,
        returns: Sequence[float],
        equity_points: Sequence[Mapping[str, float]],
        capacity: float,
    ) -> dict[str, Any]:
        weights = {f"{alpha_id}-{index}": 0.1 for index in range(10)}
        capacity_cny = {name: capacity for name in weights}
        alpha_returns = {
            name: [value * (0.8 + index * 0.04) for value in returns[:90]]
            for index, name in enumerate(weights)
        }
        metadata = [
            {
                "validation_confidence": 0.78,
                "paper_confidence": 0.72,
                "age_days": 45,
                "decay_flag": False,
                "overfit_flag": False,
                "leakage_flag": False,
            }
        ]
        run = RiskRun(portfolio_id=f"portfolio-{alpha_id}", config=RiskConfig())
        decision = RiskApprovalGate().evaluate(
            run=run,
            returns=list(returns[:120]),
            weights=weights,
            equity_curve=[point["equity"] for point in equity_points],
            capacity_cny=capacity_cny,
            alpha_returns=alpha_returns,
            regime_labels=[
                "normal" if index % 5 else "stress"
                for index in range(len(returns[:120]))
            ],
            alpha_metadata=metadata,
            daily_volume_cny=1_000_000_000,
            total_aum=100_000_000,
        )
        stress_report = run.gate_reports.get("stress")
        return {
            "run_id": run.run_id,
            "portfolio_id": run.portfolio_id,
            "decision": decision.decision.value,
            "gates_passed": decision.gates_passed,
            "gates_total": decision.gates_total,
            "gate_results": run.gate_results,
            "reasons": decision.reasons,
            "reproducibility_hash": run.reproducibility_hash,
            "stress": (
                dataclasses.asdict(stress_report)
                if stress_report is not None
                else None
            ),
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
            audit = DurableAuditLog(audit_db)
            snapshot = build_snapshot(
                timestamp=0, audit_log=audit, kill_switch=kill_switch
            )
            return {
                "provenance": "REAL_MONITORING_SNAPSHOT",
                "audit_chain_valid": snapshot.audit_chain_valid,
                "kill_switch_active": snapshot.kill_switch_active,
                "healthy": snapshot.healthy,
                "anomalies": list(snapshot.anomalies),
                "audit_events": len(audit.events),
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
