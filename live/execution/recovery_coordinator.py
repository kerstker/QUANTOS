"""Fail-closed recovery barrier before accepting a new execution command.

The coordinator is deliberately observational.  It resolves durable unknown
submissions, replays the event-authoritative OMS, reconciles the account-level
ledger with the venue, and finally evaluates the continuous risk state.  It has
no order-submission capability and never resets the kill switch.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from live.contracts import VenuePosition
from live.execution.oms import OmsAuthority
from live.execution.order_recovery import SqliteOrderRecoveryStore, resolve_unknown_orders
from live.execution.position_ledger import LiveLedger
from live.execution.reconciliation import reconcile_positions
from live.execution.risk_supervisor import RiskSupervisorDecision, RiskSupervisorVerdict
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.hashing import stable_hash
from quantos.contracts.order import TERMINAL_STATES
from services.risk.pre_trade.kill_switch_runtime import arm_and_record
from services.risk.pre_trade.kill_switch_store import (
    KillSwitchStateConflict,
    KillSwitchStateStore,
)


_ACTOR = Actor(actor_id="recovery-coordinator", kind=ActorKind.SYSTEM)


class RecoveryStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    status: RecoveryStatus
    new_command_allowed: bool
    completed_phases: tuple[str, ...]
    reason_codes: tuple[str, ...]
    unknown_order_report: Mapping[str, Any] | None
    oms_states: Mapping[str, str] | None
    reconciliation: Mapping[str, Any] | None
    risk_decision: RiskSupervisorDecision | None
    kill_switch_tripped: bool
    audit_head: str


RiskEvaluator = Callable[
    [Mapping[str, float], Mapping[str, float], int, bool],
    RiskSupervisorDecision,
]


class RecoveryCoordinator:
    """Run the one allowed recovery sequence before new command admission."""

    def __init__(
        self,
        *,
        recovery_store: SqliteOrderRecoveryStore,
        oms_authority: OmsAuthority,
        ledger: LiveLedger,
        venue: object,
        kill_switch_store: KillSwitchStateStore,
        audit: object,
        risk_evaluator: RiskEvaluator,
        deployment_id: str | None = None,
        reconciliation_tolerance: float = 0.0,
    ) -> None:
        if not isinstance(recovery_store, SqliteOrderRecoveryStore):
            raise TypeError("recovery_store must be a SqliteOrderRecoveryStore")
        if not isinstance(oms_authority, OmsAuthority):
            raise TypeError("oms_authority must be an OmsAuthority")
        if not isinstance(ledger, LiveLedger):
            raise TypeError("ledger must be a LiveLedger")
        if not isinstance(kill_switch_store, KillSwitchStateStore):
            raise TypeError("kill_switch_store must be a KillSwitchStateStore")
        if (
            not callable(getattr(audit, "append", None))
            or not hasattr(audit, "events")
            or not hasattr(audit, "head_hash")
        ):
            raise TypeError("audit must implement the append-only audit sink interface")
        if not callable(risk_evaluator):
            raise TypeError("risk_evaluator must be callable")
        if deployment_id is not None and (
            not isinstance(deployment_id, str) or not deployment_id
        ):
            raise ValueError("deployment_id must be non-empty or None")
        if (
            isinstance(reconciliation_tolerance, bool)
            or not isinstance(reconciliation_tolerance, (int, float))
            or not math.isfinite(float(reconciliation_tolerance))
            or reconciliation_tolerance < 0
        ):
            raise ValueError("reconciliation_tolerance must be finite and non-negative")
        self._recovery = recovery_store
        self._oms = oms_authority
        self._ledger = ledger
        self._venue = venue
        self._kill_switch = kill_switch_store
        self._audit_log = audit
        self._risk_evaluator = risk_evaluator
        self._deployment_id = deployment_id
        self._tolerance = float(reconciliation_tolerance)

    def _audit(self, action: str, *, now_ms: int, metadata: Mapping[str, object]) -> None:
        normalized = {str(key): str(value) for key, value in metadata.items()}
        event_id = "recovery-" + stable_hash(
            {
                "action": action,
                "now_ms": now_ms,
                "metadata": normalized,
                "sequence": len(self._audit_log.events),
            }
        )[:32]
        self._audit_log.append(
            event_id=event_id,
            actor="live:recovery_coordinator",
            action=action,
            timestamp=now_ms,
            target=self._deployment_id or "ACCOUNT",
            metadata=normalized,
        )

    def _latch(self, *, now_ms: int, reason: str) -> str | None:
        current = self._kill_switch.load_state()
        if current.tripped:
            return None
        try:
            arm_and_record(self._kill_switch, _ACTOR, reason, at=now_ms)
        except KillSwitchStateConflict:
            if self._kill_switch.load_state().tripped:
                return None
            return "KILL_SWITCH_LATCH_CONFLICT"
        except Exception as exc:
            return f"KILL_SWITCH_LATCH_{type(exc).__name__.upper()}"
        return None

    def _latch_or_raise(self, reason: str, now_ms: int) -> None:
        error = self._latch(now_ms=now_ms, reason=reason)
        if error is not None:
            raise RuntimeError(error)

    def _report(
        self,
        *,
        status: RecoveryStatus,
        phases: list[str],
        reasons: list[str],
        unknown: Mapping[str, Any] | None = None,
        oms_states: Mapping[str, str] | None = None,
        reconciliation: Mapping[str, Any] | None = None,
        risk: RiskSupervisorDecision | None = None,
    ) -> RecoveryReport:
        state = self._kill_switch.load_state()
        return RecoveryReport(
            status=status,
            new_command_allowed=(status is RecoveryStatus.READY and not state.tripped),
            completed_phases=tuple(phases),
            reason_codes=tuple(dict.fromkeys(reasons)),
            unknown_order_report=unknown,
            oms_states=oms_states,
            reconciliation=reconciliation,
            risk_decision=risk,
            kill_switch_tripped=state.tripped,
            audit_head=self._audit_log.head_hash,
        )

    def _manual(
        self,
        *,
        now_ms: int,
        phases: list[str],
        reasons: list[str],
        unknown: Mapping[str, Any] | None = None,
        oms_states: Mapping[str, str] | None = None,
        reconciliation: Mapping[str, Any] | None = None,
        risk: RiskSupervisorDecision | None = None,
    ) -> RecoveryReport:
        latch_error = self._latch(
            now_ms=now_ms,
            reason="RECOVERY_MANUAL_REVIEW:" + ",".join(reasons),
        )
        if latch_error is not None:
            reasons.append(latch_error)
        self._audit(
            "RECOVERY_MANUAL_REVIEW_REQUIRED",
            now_ms=now_ms,
            metadata={"reasons": ",".join(dict.fromkeys(reasons))},
        )
        return self._report(
            status=RecoveryStatus.MANUAL_REVIEW,
            phases=phases,
            reasons=reasons,
            unknown=unknown,
            oms_states=oms_states,
            reconciliation=reconciliation,
            risk=risk,
        )

    def _blocked(
        self,
        *,
        now_ms: int,
        phases: list[str],
        reasons: list[str],
        unknown: Mapping[str, Any],
        oms_states: Mapping[str, str],
        reconciliation: Mapping[str, Any],
        risk: RiskSupervisorDecision,
    ) -> RecoveryReport:
        self._audit(
            "RECOVERY_NEW_COMMAND_BLOCKED",
            now_ms=now_ms,
            metadata={"reasons": ",".join(dict.fromkeys(reasons))},
        )
        return self._report(
            status=RecoveryStatus.BLOCKED,
            phases=phases,
            reasons=reasons,
            unknown=unknown,
            oms_states=oms_states,
            reconciliation=reconciliation,
            risk=risk,
        )

    @staticmethod
    def _venue_positions(venue: object) -> dict[str, float]:
        loader = getattr(venue, "positions", None)
        if not callable(loader):
            raise ValueError("venue positions capability unavailable")
        raw = loader()
        if not isinstance(raw, Mapping):
            raise ValueError("venue positions result is not a mapping")
        result: dict[str, float] = {}
        for instrument_id, value in raw.items():
            if (
                not isinstance(instrument_id, str)
                or not instrument_id
                or not isinstance(value, VenuePosition)
                or value.instrument_id != instrument_id
                or isinstance(value.quantity, bool)
                or not isinstance(value.quantity, (int, float))
                or not math.isfinite(float(value.quantity))
            ):
                raise ValueError("venue position snapshot is malformed")
            result[instrument_id] = float(value.quantity)
        return result

    @staticmethod
    def _ledger_positions(ledger: LiveLedger) -> dict[str, float]:
        raw = ledger.all_positions()
        if not isinstance(raw, Mapping):
            raise ValueError("ledger positions result is not a mapping")
        result: dict[str, float] = {}
        for instrument_id, value in raw.items():
            if (
                not isinstance(instrument_id, str)
                or not instrument_id
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError("ledger position snapshot is malformed")
            result[instrument_id] = float(value)
        return result

    def recover_before_new_command(self, *, now_ms: int) -> RecoveryReport:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        phases: list[str] = []
        reasons: list[str] = []

        # 1. Unknown venue submissions must be resolved before OMS replay.
        try:
            unknown = resolve_unknown_orders(
                self._recovery,
                self._venue,
                now_ms=now_ms,
                deployment_id=self._deployment_id,
                kill_switch_arm=self._latch_or_raise,
                oms_authority=self._oms,
            )
        except Exception as exc:
            phases.append("UNKNOWN_ORDERS")
            reasons.append(f"UNKNOWN_ORDER_RECOVERY_{type(exc).__name__.upper()}")
            return self._manual(now_ms=now_ms, phases=phases, reasons=reasons)
        phases.append("UNKNOWN_ORDERS")
        self._audit(
            "RECOVERY_UNKNOWN_ORDERS_COMPLETE",
            now_ms=now_ms,
            metadata={"outcomes": len(unknown["outcomes"])},
        )
        if unknown["blocking_orders"]:
            reasons.append("UNKNOWN_ORDERS_BLOCKING")
            return self._manual(
                now_ms=now_ms, phases=phases, reasons=reasons, unknown=unknown
            )

        # 2. Rebuild projections only from the append-only OMS event journal.
        try:
            read_models = self._oms.replay()
        except Exception as exc:
            phases.append("OMS_REPLAY")
            reasons.append(f"OMS_REPLAY_{type(exc).__name__.upper()}")
            return self._manual(
                now_ms=now_ms, phases=phases, reasons=reasons, unknown=unknown
            )
        phases.append("OMS_REPLAY")
        oms_states = {
            client_order_id: model.state.value
            for client_order_id, model in sorted(read_models.items())
        }
        self._audit(
            "RECOVERY_OMS_REPLAY_COMPLETE",
            now_ms=now_ms,
            metadata={"orders": len(oms_states)},
        )
        nonterminal = sorted(
            client_order_id
            for client_order_id, model in read_models.items()
            if model.state not in TERMINAL_STATES
        )
        if nonterminal:
            reasons.append("OMS_NONTERMINAL_ORDERS")
            return self._manual(
                now_ms=now_ms,
                phases=phases,
                reasons=reasons,
                unknown=unknown,
                oms_states=oms_states,
            )

        # 3. Reconcile account-level truth; do not repair drift automatically.
        try:
            venue_positions = self._venue_positions(self._venue)
            ledger_positions = self._ledger_positions(self._ledger)
            reconciliation = reconcile_positions(
                ledger_positions,
                venue_positions,
                tolerance=self._tolerance,
            )
        except Exception as exc:
            phases.append("LEDGER_VENUE_RECONCILIATION")
            reasons.append(f"RECONCILIATION_INPUT_{type(exc).__name__.upper()}")
            return self._manual(
                now_ms=now_ms,
                phases=phases,
                reasons=reasons,
                unknown=unknown,
                oms_states=oms_states,
            )
        phases.append("LEDGER_VENUE_RECONCILIATION")
        self._audit(
            "RECOVERY_RECONCILIATION_COMPLETE",
            now_ms=now_ms,
            metadata={
                "in_sync": reconciliation["in_sync"],
                "max_abs_drift": reconciliation["max_abs_drift"],
            },
        )
        if not reconciliation["in_sync"]:
            reasons.append("LEDGER_VENUE_DRIFT")
            return self._manual(
                now_ms=now_ms,
                phases=phases,
                reasons=reasons,
                unknown=unknown,
                oms_states=oms_states,
                reconciliation=reconciliation,
            )

        # 4. Risk is evaluated last and cannot clear a previously latched halt.
        preexisting_trip = self._kill_switch.load_state().tripped
        try:
            risk = self._risk_evaluator(
                venue_positions,
                ledger_positions,
                now_ms,
                preexisting_trip,
            )
            if not isinstance(risk, RiskSupervisorDecision):
                raise TypeError("risk evaluator returned a non-decision")
        except Exception as exc:
            phases.append("RISK_STATE")
            reasons.append(f"RISK_STATE_{type(exc).__name__.upper()}")
            return self._manual(
                now_ms=now_ms,
                phases=phases,
                reasons=reasons,
                unknown=unknown,
                oms_states=oms_states,
                reconciliation=reconciliation,
            )
        phases.append("RISK_STATE")
        self._audit(
            "RECOVERY_RISK_STATE_COMPLETE",
            now_ms=now_ms,
            metadata={"verdict": risk.verdict.value},
        )
        if risk.verdict is not RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK:
            reasons.extend(risk.reasons or (risk.verdict.value,))
            if risk.latch_kill_switch:
                return self._manual(
                    now_ms=now_ms,
                    phases=phases,
                    reasons=reasons,
                    unknown=unknown,
                    oms_states=oms_states,
                    reconciliation=reconciliation,
                    risk=risk,
                )
            return self._blocked(
                now_ms=now_ms,
                phases=phases,
                reasons=reasons,
                unknown=unknown,
                oms_states=oms_states,
                reconciliation=reconciliation,
                risk=risk,
            )
        if preexisting_trip or self._kill_switch.load_state().tripped:
            reasons.append("KILL_SWITCH_LATCHED")
            return self._blocked(
                now_ms=now_ms,
                phases=phases,
                reasons=reasons,
                unknown=unknown,
                oms_states=oms_states,
                reconciliation=reconciliation,
                risk=risk,
            )

        self._audit(
            "RECOVERY_READY_FOR_NEW_COMMAND",
            now_ms=now_ms,
            metadata={"verdict": risk.verdict.value},
        )
        return self._report(
            status=RecoveryStatus.READY,
            phases=phases,
            reasons=reasons,
            unknown=unknown,
            oms_states=oms_states,
            reconciliation=reconciliation,
            risk=risk,
        )


__all__ = ["RecoveryCoordinator", "RecoveryReport", "RecoveryStatus"]
