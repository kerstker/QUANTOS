from __future__ import annotations

from dataclasses import dataclass

import pytest

from live.contracts import VenueOrderResult, VenuePosition
from live.execution.oms import OmsAuthority
from live.execution.order_event_store import (
    OrderEventSource,
    SqliteOrderEventStore,
    create_order_event,
)
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.execution.recovery_coordinator import RecoveryCoordinator, RecoveryStatus
from live.execution.risk_supervisor import RiskSupervisorDecision, RiskSupervisorVerdict
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.hashing import stable_hash
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog
from services.risk.pre_trade.kill_switch_runtime import arm_and_record
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore


NOW = 2_000_000


@dataclass
class _Venue:
    current_positions: dict[str, VenuePosition]
    orders: dict[str, VenueOrderResult]
    query_error: bool = False
    venue_mode: str = "testnet"
    place_calls: int = 0

    def get_order(self, instrument_id: str, client_order_id: str):
        if self.query_error:
            raise TimeoutError("indeterminate venue query")
        return self.orders.get(client_order_id)

    def positions(self):
        return dict(self.current_positions)

    def place_order(self, _request):
        self.place_calls += 1
        raise AssertionError("recovery must never submit an order")


def _decision(verdict: RiskSupervisorVerdict) -> RiskSupervisorDecision:
    return RiskSupervisorDecision(
        verdict=verdict,
        reasons=() if verdict is RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK else ("TEST_BLOCK",),
        latch_kill_switch=verdict is not RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK,
        automatic_action="NONE" if verdict is RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK else "HALT_NEW_RISK",
        evaluated_at_ms=NOW,
        policy_id="test-policy",
        policy_version="1",
        policy_hash="a" * 64,
        risk_breach=None,
        constraint_breach=None,
        reconciliation=None,
    )


def _stack(tmp_path, *, venue: _Venue | None = None,
           risk_verdict=RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK):
    events = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)
    ledger = LiveLedger(str(tmp_path / "ledger.db"), create=True)
    recovery = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    oms = OmsAuthority(events, ledger, recovery_store=recovery)
    kill_switch = KillSwitchStateStore(str(tmp_path / "kill-switch.db"), create=True)
    audit = GlobalAuditLog()
    venue = venue or _Venue({}, {})
    risk_calls: list[tuple] = []

    def evaluate(venue_positions, ledger_positions, now_ms, kill_switch_tripped):
        risk_calls.append((venue_positions, ledger_positions, now_ms, kill_switch_tripped))
        return _decision(risk_verdict)

    coordinator = RecoveryCoordinator(
        recovery_store=recovery,
        oms_authority=oms,
        ledger=ledger,
        venue=venue,
        kill_switch_store=kill_switch,
        audit=audit,
        risk_evaluator=evaluate,
    )
    return coordinator, oms, events, ledger, recovery, kill_switch, audit, venue, risk_calls


def _register(oms: OmsAuthority, *, client_order_id: str = "cid-1") -> None:
    oms.register_submission(
        client_order_id=client_order_id,
        instrument_id="BTC",
        side=OrderSide.BUY,
        order_quantity=1.0,
        strategy_id="hourly-paper",
        order_type="MARKET",
        limit_price=None,
        observed_at_ms=NOW - 10,
        command_event_id=f"command-{client_order_id}",
    )


def test_recovery_runs_fixed_order_and_only_then_allows_new_command(tmp_path) -> None:
    stack = _stack(tmp_path)
    coordinator, _, _, _, _, kill_switch, audit, venue, risk_calls = stack

    report = coordinator.recover_before_new_command(now_ms=NOW)

    assert report.status is RecoveryStatus.READY
    assert report.new_command_allowed
    assert report.completed_phases == (
        "UNKNOWN_ORDERS",
        "OMS_REPLAY",
        "LEDGER_VENUE_RECONCILIATION",
        "RISK_STATE",
    )
    assert not kill_switch.load_state().tripped
    assert len(risk_calls) == 1
    assert venue.place_calls == 0
    assert [event.action for event in audit.events] == [
        "RECOVERY_UNKNOWN_ORDERS_COMPLETE",
        "RECOVERY_OMS_REPLAY_COMPLETE",
        "RECOVERY_RECONCILIATION_COMPLETE",
        "RECOVERY_RISK_STATE_COMPLETE",
        "RECOVERY_READY_FOR_NEW_COMMAND",
    ]
    assert audit.chain_valid()


def test_terminal_unknown_is_recovered_into_oms_before_reconciliation(tmp_path) -> None:
    result = VenueOrderResult(
        venue_order_id="venue-1",
        client_order_id="cid-1",
        instrument_id="BTC",
        status="FILLED",
        filled_qty=1.0,
        avg_price=100.0,
    )
    venue = _Venue({"BTC": VenuePosition("BTC", 1.0)}, {"cid-1": result})
    stack = _stack(tmp_path, venue=venue)
    coordinator, oms, _, ledger, recovery, _, _, _, _ = stack
    _register(oms)
    recovery.register_unknown(
        client_order_id="cid-1",
        deployment_id="hourly-paper",
        instrument_id="BTC",
        side="BUY",
        quantity=1.0,
        now_ms=NOW - 5,
        reason="crash after ACK",
    )

    report = coordinator.recover_before_new_command(now_ms=NOW)

    assert report.status is RecoveryStatus.READY
    assert report.unknown_order_report["outcomes"][0]["outcome"] == "RESOLVED"
    assert report.unknown_order_report["blocking_orders"] == []
    assert ledger.all_positions() == {"BTC": pytest.approx(1.0)}
    assert venue.place_calls == 0


def test_indeterminate_unknown_latches_kill_switch_and_never_runs_later_phases(tmp_path) -> None:
    venue = _Venue({}, {}, query_error=True)
    stack = _stack(tmp_path, venue=venue)
    coordinator, _, _, _, recovery, kill_switch, audit, _, risk_calls = stack
    recovery.register_unknown(
        client_order_id="cid-timeout",
        deployment_id="hourly-paper",
        instrument_id="BTC",
        side="BUY",
        quantity=1.0,
        now_ms=NOW - 1,
        reason="submission timeout",
    )

    report = coordinator.recover_before_new_command(now_ms=NOW)

    assert report.status is RecoveryStatus.MANUAL_REVIEW
    assert not report.new_command_allowed
    assert report.completed_phases == ("UNKNOWN_ORDERS",)
    assert "UNKNOWN_ORDERS_BLOCKING" in report.reason_codes
    assert kill_switch.load_state().tripped
    assert risk_calls == []
    assert venue.place_calls == 0
    assert audit.events[-1].action == "RECOVERY_MANUAL_REVIEW_REQUIRED"


def test_ledger_venue_mismatch_latches_and_stops_before_risk(tmp_path) -> None:
    venue = _Venue({"BTC": VenuePosition("BTC", 1.0)}, {})
    stack = _stack(tmp_path, venue=venue)
    coordinator, _, _, _, _, kill_switch, _, _, risk_calls = stack

    report = coordinator.recover_before_new_command(now_ms=NOW)

    assert report.status is RecoveryStatus.MANUAL_REVIEW
    assert report.completed_phases[-1] == "LEDGER_VENUE_RECONCILIATION"
    assert "LEDGER_VENUE_DRIFT" in report.reason_codes
    assert report.reconciliation["in_sync"] is False
    assert kill_switch.load_state().tripped
    assert risk_calls == []


def test_nonterminal_oms_state_is_manual_and_never_resubmitted(tmp_path) -> None:
    stack = _stack(tmp_path)
    coordinator, oms, _, _, _, kill_switch, _, venue, _ = stack
    _register(oms)

    report = coordinator.recover_before_new_command(now_ms=NOW)

    assert report.status is RecoveryStatus.MANUAL_REVIEW
    assert "OMS_NONTERMINAL_ORDERS" in report.reason_codes
    assert kill_switch.load_state().tripped
    assert venue.place_calls == 0


def test_existing_kill_switch_is_never_auto_reset_by_healthy_recovery(tmp_path) -> None:
    stack = _stack(tmp_path)
    coordinator, _, _, _, _, kill_switch, audit, venue, _ = stack
    arm_and_record(
        kill_switch,
        Actor(actor_id="operator-test", kind=ActorKind.SYSTEM),
        "pre-existing incident",
        at=NOW - 1,
    )

    report = coordinator.recover_before_new_command(now_ms=NOW)

    assert report.status is RecoveryStatus.BLOCKED
    assert "KILL_SWITCH_LATCHED" in report.reason_codes
    assert not report.new_command_allowed
    assert kill_switch.load_state().tripped
    assert venue.place_calls == 0
    assert audit.events[-1].action == "RECOVERY_NEW_COMMAND_BLOCKED"


@pytest.mark.parametrize(
    "crash_point,expected",
    (
        ("before_place", RecoveryStatus.MANUAL_REVIEW),
        ("after_ack", RecoveryStatus.MANUAL_REVIEW),
        ("after_journal", RecoveryStatus.MANUAL_REVIEW),
        ("before_projection", RecoveryStatus.READY),
        ("after_fill", RecoveryStatus.READY),
        ("before_reconcile", RecoveryStatus.MANUAL_REVIEW),
    ),
)
def test_crash_matrix_recovery_has_zero_new_economic_side_effects(
    tmp_path, crash_point, expected
) -> None:
    venue = _Venue({}, {})
    stack = _stack(tmp_path, venue=venue)
    coordinator, oms, events, _, recovery, _, audit, _, _ = stack
    client_order_id = f"cid-{crash_point}"
    _register(oms, client_order_id=client_order_id)

    if crash_point == "after_ack":
        venue.orders[client_order_id] = VenueOrderResult(
            venue_order_id=f"venue-{crash_point}",
            client_order_id=client_order_id,
            instrument_id="BTC",
            status="NEW",
        )
        recovery.register_unknown(
            client_order_id=client_order_id,
            deployment_id="hourly-paper",
            instrument_id="BTC",
            side="BUY",
            quantity=1.0,
            now_ms=NOW - 1,
            reason=crash_point,
        )
    elif crash_point == "after_journal":
        oms.ingest_venue_snapshot(
            client_order_id=client_order_id,
            source=OrderEventSource.REST_POLL,
            source_sequence=1,
            venue_event_id="ack-journaled",
            venue_order_id="venue-after-journal",
            venue_status="NEW",
            cumulative_filled_qty=0.0,
            avg_fill_price=0.0,
            observed_at_ms=NOW - 1,
            venue_payload_hash=stable_hash({"status": "NEW"}),
        )
    elif crash_point == "before_projection":
        # Journal the terminal venue fact directly, leaving the ledger at the
        # command projection.  Recovery must replay this event exactly once.
        events.append(
            create_order_event(
                source=OrderEventSource.REST_POLL,
                source_sequence=1,
                venue_event_id="fill-before-projection",
                client_order_id=client_order_id,
                venue_order_id="venue-before-projection",
                instrument_id="BTC",
                side=OrderSide.BUY,
                order_quantity=1.0,
                strategy_id="hourly-paper",
                order_type="MARKET",
                limit_price=None,
                venue_status="FILLED",
                cumulative_filled_qty=1.0,
                avg_fill_price=100.0,
                observed_at_ms=NOW - 1,
                venue_payload_hash=stable_hash({"status": "FILLED", "qty": 1.0}),
            )
        )
        venue.current_positions["BTC"] = VenuePosition("BTC", 1.0)
    elif crash_point in {"after_fill", "before_reconcile"}:
        oms.ingest_venue_snapshot(
            client_order_id=client_order_id,
            source=OrderEventSource.REST_POLL,
            source_sequence=1,
            venue_event_id=f"fill-{crash_point}",
            venue_order_id=f"venue-{crash_point}",
            venue_status="FILLED",
            cumulative_filled_qty=1.0,
            avg_fill_price=100.0,
            observed_at_ms=NOW - 1,
            venue_payload_hash=stable_hash({"status": "FILLED", "point": crash_point}),
        )
        if crash_point == "after_fill":
            venue.current_positions["BTC"] = VenuePosition("BTC", 1.0)

    report = coordinator.recover_before_new_command(now_ms=NOW)

    assert venue.place_calls == 0
    assert report.status is expected
    assert report.audit_head
    assert audit.chain_valid()
