"""Compatibility adapter for authoritative kill-switch persistence.

SQLite is the authority. The historical JSON path remains an atomic mirror for
older read-only tooling and is imported once when no authority database exists.
Corrupt authority always reads as a tripped fail-closed state.
"""
from __future__ import annotations

import os
import tempfile

from quantos.contracts.killswitch import KillSwitchState
from quantos.contracts.events import EventOutboxMessage
from services.risk.pre_trade.events import kill_switch_activated_event
from services.risk.pre_trade.kill_switch_store import (
    KillSwitchStateConflict,
    KillSwitchStateStore,
    kill_switch_authority_path,
)


def kill_switch_db_path(path: str) -> str:
    """Compatibility name for the risk-owned authority-path convention."""
    return kill_switch_authority_path(path)


def _fail_closed_state() -> KillSwitchState:
    return KillSwitchState(
        tripped=True,
        incident_id="corrupt-state-fail-closed",
        reason="kill-switch authoritative state unreadable/corrupt - failing closed",
    )


def _load_legacy_json(path: str) -> KillSwitchState:
    if not os.path.exists(path):
        return KillSwitchState()
    try:
        with open(path, encoding="utf-8") as handle:
            return KillSwitchState.model_validate_json(handle.read())
    except Exception:
        return _fail_closed_state()


def _legacy_activation_outbox(
    state: KillSwitchState,
) -> EventOutboxMessage | None:
    """Rebuild an activation only when the legacy state contains complete facts."""
    if (
        not state.tripped
        or state.armed_by is None
        or state.trip_count < 1
        or not state.incident_id
        or state.reset_at > state.armed_at
    ):
        return None
    event = kill_switch_activated_event(
        state,
        actor=state.armed_by,
        reason=state.reason,
        at_ms=state.armed_at,
    )
    return EventOutboxMessage(
        event=event,
        correlation_id=state.incident_id,
    )


def _write_json_mirror(path: str, state: KillSwitchState) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(state.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_kill_switch_json_mirror(path: str, state: KillSwitchState) -> None:
    """Refresh the compatibility mirror after an authoritative transaction."""
    if not isinstance(state, KillSwitchState):
        raise TypeError("state must be a KillSwitchState")
    _write_json_mirror(path, state)


def open_authoritative_kill_switch_store(path: str) -> KillSwitchStateStore:
    """Open the SQLite authority, importing a legacy JSON state exactly once."""
    database = kill_switch_db_path(path)
    existed = os.path.exists(database)
    store = KillSwitchStateStore(database, create=True)
    if not existed and os.path.exists(path):
        legacy = _load_legacy_json(path)
        store.bootstrap(
            legacy,
            recorded_at=max(legacy.armed_at, legacy.reset_at),
            event_outbox=_legacy_activation_outbox(legacy),
        )
        _write_json_mirror(path, legacy)
    return store


def load_kill_switch_state(path: str) -> KillSwitchState:
    database = kill_switch_db_path(path)
    if os.path.exists(database):
        store = None
        try:
            store = KillSwitchStateStore(database, create=False)
            return store.load_state()
        except Exception:
            return _fail_closed_state()
        finally:
            if store is not None:
                store.close()
    return _load_legacy_json(path)


def save_kill_switch_state(path: str, state: KillSwitchState) -> None:
    """Bootstrap or mirror an exact state; never overwrite authority with a new one."""
    if not isinstance(state, KillSwitchState):
        raise TypeError("state must be a KillSwitchState")
    store = open_authoritative_kill_switch_store(path)
    try:
        current = store.load_state()
        if store.transition_count() == 0:
            store.bootstrap(
                state,
                recorded_at=max(state.armed_at, state.reset_at),
                event_outbox=_legacy_activation_outbox(state),
            )
        elif current != state:
            raise KillSwitchStateConflict(
                "refusing legacy overwrite of existing authoritative state"
            )
        _write_json_mirror(path, state)
    finally:
        store.close()


def save_kill_switch_state_if_tripped(path: str, state: KillSwitchState) -> None:
    """Legacy monotonic helper: an untripped caller can never clear authority."""
    if state.tripped:
        save_kill_switch_state(path, state)


__all__ = [
    "kill_switch_db_path",
    "load_kill_switch_state",
    "open_authoritative_kill_switch_store",
    "save_kill_switch_state",
    "save_kill_switch_state_if_tripped",
    "write_kill_switch_json_mirror",
]
