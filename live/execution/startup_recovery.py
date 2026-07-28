"""Fail-closed startup recovery choreography (727 §13.5 / Phase 12).

This module is an orchestration boundary, not a venue client.  Every external
operation is injected so tests can prove the ordering and the production
driver can choose the appropriate MEXC/Binance/Bybit read-only methods.  A
missing step, an exception, a false health result, or a reconciliation drift
stops the sequence and makes ``ready_for_new_orders`` false.

The MEXC spot adapter intentionally has no user-stream implementation today;
passing no ``establish_user_stream`` callback therefore produces a visible
``USER_STREAM_NOT_READY`` blocker instead of claiming Phase 8 is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from live.execution.reconciliation import preflight_check


class StartupRecoveryError(RuntimeError):
    """A startup recovery dependency is malformed or cannot be verified."""


@dataclass(frozen=True)
class StartupStep:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class StartupRecoveryReport:
    """Serializable result of one startup recovery attempt."""

    ready_for_new_orders: bool
    steps: tuple[StartupStep, ...]
    blockers: tuple[str, ...]
    snapshot_counts: Mapping[str, int]
    reconciliation: Optional[Mapping[str, Any]] = None

    @property
    def status(self) -> str:
        return "READY" if self.ready_for_new_orders else "NOT_READY"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "ready_for_new_orders": self.ready_for_new_orders,
            "steps": [
                {"name": step.name, "passed": step.passed, "detail": step.detail}
                for step in self.steps
            ],
            "blockers": list(self.blockers),
            "snapshot_counts": dict(self.snapshot_counts),
            "reconciliation": dict(self.reconciliation)
            if self.reconciliation is not None else None,
        }


def _truth(value: object) -> bool:
    """Only literal ``True`` is a successful health/chain answer."""
    return value is True


def _call_step(steps: list[StartupStep], blockers: list[str], name: str,
               callback: Optional[Callable[[], object]], *, missing: str,
               require_exact_true: bool = False) -> object:
    if callback is None:
        steps.append(StartupStep(name, False, missing))
        blockers.append(missing)
        return None
    try:
        value = callback()
    except Exception as exc:
        detail = f"{name} failed: {type(exc).__name__}"
        steps.append(StartupStep(name, False, detail))
        blockers.append(detail)
        return None
    if value is False:
        detail = f"{name} returned false"
        steps.append(StartupStep(name, False, detail))
        blockers.append(detail)
        return None
    if require_exact_true and value is not True:
        detail = f"{name} was not explicitly verified"
        steps.append(StartupStep(name, False, detail))
        blockers.append(detail)
        return None
    steps.append(StartupStep(name, True, "verified"))
    return value


def run_startup_recovery(
    *,
    acquire_leader_lease: Optional[Callable[[], object]],
    sync_clock: Optional[Callable[[], object]],
    load_rules_and_fees: Optional[Callable[[], object]],
    fetch_account: Optional[Callable[[], object]],
    fetch_open_orders: Optional[Callable[[], object]],
    fetch_recent_trades: Optional[Callable[[], object]],
    rebuild_local_ledger: Optional[Callable[[object, object, object], object]],
    verify_event_chain: Optional[Callable[[], object]],
    reconcile: Optional[Callable[[Mapping[str, float], Mapping[str, float]], Mapping[str, Any]]] = None,
    ledger_positions: Optional[Mapping[str, float]] = None,
    venue_positions: Optional[Mapping[str, float]] = None,
    establish_user_stream: Optional[Callable[[], object]] = None,
    data_health: Optional[Callable[[], object]] = None,
    account_health: Optional[Callable[[], object]] = None,
    tolerance: float = 1e-6,
) -> StartupRecoveryReport:
    """Run the ordered startup sequence and return a hard gate for new orders.

    The callback return values are deliberately not persisted in the report
    except for safe counts and reconciliation numbers; raw balances, fills,
    and venue payloads must remain in their owner stores.  Once a step fails,
    later steps are not run because their prerequisites are no longer trusted.
    """
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise ValueError("tolerance must be a non-negative number")
    steps: list[StartupStep] = []
    blockers: list[str] = []
    counts: dict[str, int] = {}

    def stop_if_blocked() -> bool:
        return bool(blockers)

    leader = _call_step(
        steps, blockers, "leader_lease", acquire_leader_lease,
        missing="LEADER_LEASE_NOT_ACQUIRED",
    )
    if stop_if_blocked():
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)

    clock = _call_step(steps, blockers, "time_sync", sync_clock,
                       missing="TIME_SYNC_NOT_VERIFIED")
    if stop_if_blocked():
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    rules = _call_step(steps, blockers, "rules_and_fees", load_rules_and_fees,
                       missing="RULES_AND_FEES_NOT_LOADED")
    if stop_if_blocked():
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    account = _call_step(steps, blockers, "balances", fetch_account,
                         missing="ACCOUNT_SNAPSHOT_NOT_AVAILABLE")
    if stop_if_blocked():
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    open_orders = _call_step(steps, blockers, "open_orders", fetch_open_orders,
                             missing="OPEN_ORDERS_NOT_AVAILABLE")
    if stop_if_blocked():
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    recent_trades = _call_step(steps, blockers, "recent_trades", fetch_recent_trades,
                               missing="RECENT_TRADES_NOT_AVAILABLE")
    if stop_if_blocked():
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)

    counts["open_orders"] = len(open_orders) if hasattr(open_orders, "__len__") else 0
    counts["recent_trades"] = len(recent_trades) if hasattr(recent_trades, "__len__") else 0
    if rebuild_local_ledger is None:
        steps.append(StartupStep("rebuild_local_ledger", False, "LOCAL_LEDGER_REBUILDER_MISSING"))
        blockers.append("LOCAL_LEDGER_REBUILDER_MISSING")
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    try:
        rebuilt = rebuild_local_ledger(account, open_orders, recent_trades)
    except Exception as exc:
        detail = f"rebuild_local_ledger failed: {type(exc).__name__}"
        steps.append(StartupStep("rebuild_local_ledger", False, detail))
        blockers.append(detail)
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    if not isinstance(rebuilt, Mapping):
        detail = "rebuild_local_ledger returned a non-mapping position snapshot"
        steps.append(StartupStep("rebuild_local_ledger", False, detail))
        blockers.append(detail)
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    steps.append(StartupStep("rebuild_local_ledger", True, "verified"))
    local_positions = dict(ledger_positions if ledger_positions is not None else rebuilt)
    venue_snapshot = dict(venue_positions if venue_positions is not None else {})

    if verify_event_chain is None:
        steps.append(StartupStep("event_chain", False, "EVENT_CHAIN_VERIFIER_MISSING"))
        blockers.append("EVENT_CHAIN_VERIFIER_MISSING")
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    try:
        chain_ok = verify_event_chain()
    except Exception as exc:
        detail = f"event_chain failed: {type(exc).__name__}"
        steps.append(StartupStep("event_chain", False, detail))
        blockers.append(detail)
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    if not _truth(chain_ok):
        steps.append(StartupStep("event_chain", False, "EVENT_CHAIN_INVALID"))
        blockers.append("EVENT_CHAIN_INVALID")
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    steps.append(StartupStep("event_chain", True, "verified"))

    recon_fn = reconcile or (lambda ledger, venue: preflight_check(
        ledger, venue, tolerance=float(tolerance)))
    try:
        reconciliation = recon_fn(local_positions, venue_snapshot)
    except Exception as exc:
        detail = f"reconciliation failed: {type(exc).__name__}"
        steps.append(StartupStep("reconciliation", False, detail))
        blockers.append(detail)
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    if not isinstance(reconciliation, Mapping):
        detail = "RECONCILIATION_REPORT_INVALID"
        steps.append(StartupStep("reconciliation", False, detail))
        blockers.append(detail)
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts)
    if reconciliation.get("in_sync") is not True or reconciliation.get("proceed") is not True:
        steps.append(StartupStep("reconciliation", False, "RECONCILIATION_REQUIRED"))
        blockers.append("RECONCILIATION_REQUIRED")
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts,
                                     reconciliation=reconciliation)
    steps.append(StartupStep("reconciliation", True, "verified"))

    stream = _call_step(steps, blockers, "user_stream", establish_user_stream,
                        missing="USER_STREAM_NOT_READY", require_exact_true=True)
    if stop_if_blocked():
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts,
                                     reconciliation=reconciliation)
    data_ok = _call_step(steps, blockers, "data_health", data_health,
                         missing="DATA_HEALTH_NOT_VERIFIED", require_exact_true=True)
    if stop_if_blocked() or not _truth(data_ok):
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts,
                                     reconciliation=reconciliation)
    account_ok = _call_step(steps, blockers, "account_health", account_health,
                            missing="ACCOUNT_HEALTH_NOT_VERIFIED", require_exact_true=True)
    if stop_if_blocked() or not _truth(account_ok):
        return StartupRecoveryReport(False, tuple(steps), tuple(blockers), counts,
                                     reconciliation=reconciliation)

    return StartupRecoveryReport(True, tuple(steps), tuple(), counts,
                                 reconciliation=reconciliation)


__all__ = [
    "StartupRecoveryError",
    "StartupRecoveryReport",
    "StartupStep",
    "run_startup_recovery",
]
