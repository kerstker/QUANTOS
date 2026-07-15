"""Isolated offline recovery drill for critical persisted risk state."""
from __future__ import annotations

# ruff: noqa: E402 - support direct execution before repository imports.

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from live.execution.position_ledger import LiveLedger
from live.governance.kill_switch_store import (
    kill_switch_db_path,
    load_kill_switch_state,
    open_authoritative_kill_switch_store,
    write_kill_switch_json_mirror,
)
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.events import EVENT_KILL_SWITCH_ACTIVATED
from quantos.contracts.rejection import RejectionReason
from quantos.contracts.trading import OrderIntent, OrderSide
from services.monitoring.audit_store import DurableAuditLog
from services.risk.pre_trade import (
    AccountRiskState,
    KillSwitch,
    MarketContext,
    PreTradeRiskGate,
    RiskContext,
    RiskLimits,
    StrategyApproval,
)
from services.risk.pre_trade.kill_switch_runtime import arm_and_record
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore


class RiskRecoveryDrillError(RuntimeError):
    """The drill cannot prove complete fail-closed risk-state recovery."""


_ACTIVE_ROOTS = (_REPO_ROOT / "data" / "cache", _REPO_ROOT / "key")
_ARTIFACT_NAMES = (
    "audit.db",
    "kill_switch.db",
    "kill_switch.json",
    "ledger.db",
)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _prepare_drill_dir(
    path: str,
    forbidden_roots: Iterable[Path] = _ACTIVE_ROOTS,
) -> Path:
    raw = Path(path).expanduser().absolute()
    if any(candidate.is_symlink() for candidate in (raw, *raw.parents)):
        raise RiskRecoveryDrillError("drill path must not contain symlinks")
    try:
        resolved = raw.resolve(strict=False)
        forbidden = tuple(Path(root).absolute().resolve(strict=False) for root in forbidden_roots)
    except OSError as exc:
        raise RiskRecoveryDrillError(f"drill path cannot be resolved: {exc}") from exc
    if any(
        _is_within(resolved, root) or _is_within(root, resolved)
        for root in forbidden
    ):
        raise RiskRecoveryDrillError("drill path overlaps active or credential state")
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise RiskRecoveryDrillError("drill directory must be empty or absent")
    else:
        resolved.mkdir(parents=True)
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_hashes(root: Path) -> dict[str, str]:
    return {name: _sha256_file(root / name) for name in _ARTIFACT_NAMES}


def _ledger_summary(ledger: LiveLedger) -> dict[str, Any]:
    return {
        "orders": ledger.orders(),
        "fills": ledger.fills(),
        "positions": ledger.all_positions(),
    }


def _clean_gate_inputs() -> tuple[OrderIntent, RiskContext, RiskLimits]:
    intent = OrderIntent(
        instrument_id="AAA",
        side=OrderSide.BUY,
        quantity=10,
        strategy_id="risk-drill",
    )
    context = RiskContext(
        account=AccountRiskState(
            account_id="drill-account",
            cash=1_000_000.0,
            equity=1_000_000.0,
            peak_equity=1_000_000.0,
        ),
        market=MarketContext(
            instrument_id="AAA",
            known=True,
            reference_price=100.0,
            market_open=True,
            price_as_of=1_000,
            est_daily_volume=1_000_000.0,
            instrument_vol=0.2,
        ),
        strategy=StrategyApproval(
            strategy_id="risk-drill",
            approved=True,
            approval_id="drill-approval",
        ),
        now=1_000,
    )
    limits = RiskLimits(
        allowed_instruments=frozenset({"AAA"}),
        allowed_accounts=frozenset({"drill-account"}),
    )
    return intent, context, limits


def run_risk_recovery_drill(
    *,
    drill_dir: str,
    rto_ms: int,
    observed_at_ms: int,
) -> dict[str, Any]:
    if rto_ms <= 0:
        raise RiskRecoveryDrillError("rto_ms must be positive")
    if observed_at_ms < 0:
        raise RiskRecoveryDrillError("observed_at_ms must be non-negative")

    root = _prepare_drill_dir(drill_dir)
    kill_path = root / "kill_switch.json"
    ledger_path = root / "ledger.db"
    audit_path = root / "audit.db"
    started = time.perf_counter()

    audit = DurableAuditLog(str(audit_path), create=True)
    audit.append(
        event_id="risk-recovery-drill.start",
        actor="risk-recovery-drill",
        action="risk_recovery.start",
        timestamp=observed_at_ms,
        target="isolated-state-bundle",
    )
    actor = Actor(actor_id="risk-recovery-drill", kind=ActorKind.SYSTEM)
    kill_store = open_authoritative_kill_switch_store(str(kill_path))
    try:
        armed_state = arm_and_record(
            kill_store,
            actor,
            "isolated recovery drill halt",
            at=observed_at_ms,
            incident_id="risk-recovery-drill-incident",
        )
        write_kill_switch_json_mirror(str(kill_path), armed_state)
    finally:
        kill_store.close()
    audit.append(
        event_id=f"ks.arm.{armed_state.incident_id}.{observed_at_ms}",
        actor=actor.actor_id,
        action="kill_switch.arm",
        timestamp=observed_at_ms,
        target=armed_state.incident_id,
        metadata={
            "reason": armed_state.reason,
            "scope": armed_state.scope.value,
            "scope_value": armed_state.scope_value,
            "trip_count": str(armed_state.trip_count),
        },
    )

    ledger = LiveLedger(str(ledger_path), create=True)
    ledger.record_submission(
        "drill-order-1",
        "AAA",
        OrderSide.BUY,
        2.0,
        strategy_id="risk-drill",
        submitted_at=observed_at_ms,
    )
    ledger.record_fill(
        "drill-fill-1",
        "drill-order-1",
        "AAA",
        OrderSide.BUY,
        2.0,
        100.0,
        strategy_id="risk-drill",
        filled_at=observed_at_ms + 1,
    )
    ledger_before = _ledger_summary(ledger)
    commitment = audit.seal()
    ledger.close()
    audit.close()
    pre_hashes = _state_hashes(root)

    recovered_state = load_kill_switch_state(str(kill_path))
    recovered_kill_store = KillSwitchStateStore(
        kill_switch_db_path(str(kill_path)),
        create=False,
    )
    try:
        kill_switch_chain_verified = recovered_kill_store.chain_valid()
        pending_kill_events = recovered_kill_store.pending_event_outbox()
        kill_switch_event_recoverable = (
            len(pending_kill_events) == 1
            and pending_kill_events[0]["event"].event_type
            == EVENT_KILL_SWITCH_ACTIVATED
            and pending_kill_events[0]["correlation_id"]
            == "risk-recovery-drill-incident"
        )
    finally:
        recovered_kill_store.close()
    recovered_ledger = LiveLedger(str(ledger_path), create=False)
    recovered_audit = DurableAuditLog(str(audit_path), create=False)
    ledger_after = _ledger_summary(recovered_ledger)
    kill_switch_persisted = (
        recovered_state.tripped
        and recovered_state.incident_id == "risk-recovery-drill-incident"
        and recovered_state.reason == "isolated recovery drill halt"
    )
    intent, context, limits = _clean_gate_inputs()
    decision = PreTradeRiskGate(
        limits,
        kill_switch=KillSwitch(recovered_state),
    ).evaluate(intent, context)
    gate_rejected = decision.reasons == (RejectionReason.KILL_SWITCH_ACTIVE,)
    ledger_recovered = ledger_after == ledger_before
    audit_verified = recovered_audit.verify_against(commitment)
    recovered_ledger.close()
    recovered_audit.close()
    post_hashes = _state_hashes(root)

    corrupt_kill_path = root / "fault-corrupt-kill-switch.json"
    corrupt_kill_path.write_text("{}", encoding="utf-8")
    Path(kill_switch_db_path(str(corrupt_kill_path))).write_bytes(
        b"not-a-sqlite-database"
    )
    corrupt_state = load_kill_switch_state(str(corrupt_kill_path))
    corrupt_failed_closed = corrupt_state.tripped and corrupt_state.is_active

    tampered_kill_path = root / "fault-tampered-kill-switch.json"
    tampered_kill_db = Path(kill_switch_db_path(str(tampered_kill_path)))
    shutil.copy2(kill_path, tampered_kill_path)
    shutil.copy2(Path(kill_switch_db_path(str(kill_path))), tampered_kill_db)
    connection = sqlite3.connect(str(tampered_kill_db))
    try:
        connection.execute("DROP TRIGGER kill_switch_transitions_no_update")
        connection.execute(
            "UPDATE kill_switch_transitions SET state_hash='tampered'"
        )
        connection.commit()
    finally:
        connection.close()
    tampered_kill_state = load_kill_switch_state(str(tampered_kill_path))
    tampered_kill_switch_detected = (
        tampered_kill_state.tripped
        and tampered_kill_state.incident_id == "corrupt-state-fail-closed"
    )

    tampered_audit_path = root / "fault-tampered-audit.db"
    shutil.copy2(audit_path, tampered_audit_path)
    connection = sqlite3.connect(str(tampered_audit_path))
    try:
        connection.execute(
            "UPDATE audit_events SET actor='tampered' "
            "WHERE event_id='risk-recovery-drill.start'"
        )
        connection.commit()
    finally:
        connection.close()
    tampered_audit = DurableAuditLog(str(tampered_audit_path), create=False)
    tampered_detected = not tampered_audit.chain_valid()
    tampered_audit.close()

    elapsed_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    rto_met = elapsed_ms <= rto_ms
    failures = []
    if not kill_switch_persisted:
        failures.append("kill_switch_persistence")
    if not kill_switch_chain_verified:
        failures.append("kill_switch_chain")
    if not kill_switch_event_recoverable:
        failures.append("kill_switch_event_outbox")
    if not gate_rejected:
        failures.append("kill_switch_gate_rejection")
    if not ledger_recovered or pre_hashes != post_hashes:
        failures.append("ledger_recovery")
    if not audit_verified:
        failures.append("audit_recovery")
    if not corrupt_failed_closed:
        failures.append("corrupt_kill_switch")
    if not tampered_kill_switch_detected:
        failures.append("tampered_kill_switch")
    if not tampered_detected:
        failures.append("audit_tamper")
    if not rto_met:
        failures.append("rto")
    if failures:
        raise RiskRecoveryDrillError(
            "risk recovery drill failed: " + ", ".join(failures)
        )

    rejection_code = decision.reasons[0].value if decision.reasons else ""
    return {
        "schema_version": 2,
        "verdict": "PASS",
        "drill_mode": "ISOLATED_OFFLINE",
        "observed_at_ms": observed_at_ms,
        "elapsed_ms": elapsed_ms,
        "rto_ms": rto_ms,
        "rto_met": rto_met,
        "artifact_names": list(_ARTIFACT_NAMES),
        "recovered_state_count": 4,
        "pre_restart_sha256": pre_hashes,
        "post_restart_sha256": post_hashes,
        "ledger_before": ledger_before,
        "ledger_after": ledger_after,
        "kill_switch_persisted": kill_switch_persisted,
        "kill_switch_chain_verified": kill_switch_chain_verified,
        "kill_switch_event_recoverable": kill_switch_event_recoverable,
        "kill_switch_outbox_pending_count": len(pending_kill_events),
        "gate_rejected_while_tripped": gate_rejected,
        "gate_rejection_code": rejection_code,
        "ledger_recovered": ledger_recovered,
        "audit_chain_verified": audit_verified,
        "corrupt_kill_switch_failed_closed": corrupt_failed_closed,
        "tampered_kill_switch_detected": tampered_kill_switch_detected,
        "tampered_audit_detected": tampered_detected,
        "active_state_untouched": True,
    }


def _write_evidence(path: str, result: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, destination)


def run_and_write_evidence(
    *,
    drill_dir: str,
    evidence_path: str,
    rto_ms: int,
    observed_at_ms: int,
) -> dict[str, Any]:
    result = run_risk_recovery_drill(
        drill_dir=drill_dir,
        rto_ms=rto_ms,
        observed_at_ms=observed_at_ms,
    )
    _write_evidence(evidence_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run isolated QUANTOS risk recovery drill"
    )
    parser.add_argument("--drill-dir", required=True)
    parser.add_argument("--rto-ms", type=int, default=300_000)
    parser.add_argument("--observed-at-ms", type=int)
    parser.add_argument(
        "--evidence",
        default=os.path.join("evidence", "risk_recovery_drill_latest.json"),
    )
    args = parser.parse_args()
    observed_at_ms = (
        args.observed_at_ms
        if args.observed_at_ms is not None
        else int(time.time() * 1000)
    )
    result = run_and_write_evidence(
        drill_dir=args.drill_dir,
        evidence_path=args.evidence,
        rto_ms=args.rto_ms,
        observed_at_ms=observed_at_ms,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
