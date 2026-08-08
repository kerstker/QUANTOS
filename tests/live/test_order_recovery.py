"""MEXC/venue-neutral unknown-order recovery invariants (727 §13.4)."""

import pytest

from live.contracts import VenueOrderResult
from live.execution.order_recovery import (
    RecoveryState,
    SqliteOrderRecoveryStore,
    resolve_unknown_orders,
)
from live.execution.oms import OmsAuthority
from live.execution.order_event_store import SqliteOrderEventStore
from live.execution.position_ledger import LiveLedger
from quantos.contracts.trading import OrderSide
from services.risk.pre_trade.kill_switch import KillSwitch


def _register(store, *, cid="cid-1", deployment="alpha-a"):
    return store.register_unknown(
        client_order_id=cid,
        deployment_id=deployment,
        instrument_id="BTC",
        side="BUY",
        quantity=0.01,
        now_ms=100,
    )


class _FoundVenue:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_order(self, instrument_id, client_order_id):
        self.calls.append((instrument_id, client_order_id))
        return self.result


class _BrokenVenue:
    def get_order(self, instrument_id, client_order_id):
        raise TimeoutError("transport timeout")


def test_unknown_is_durable_and_blocks_deployment_until_resolved(tmp_path):
    path = str(tmp_path / "recovery.db")
    store = SqliteOrderRecoveryStore(path, create=True)
    first = _register(store)
    assert first.state is RecoveryState.ORDER_SUBMISSION_UNKNOWN
    assert store.deployment_blocked("alpha-a") is True
    assert store.client_submission_permitted(
        deployment_id="alpha-a", client_order_id="cid-1"
    ) is False
    store.close()

    reopened = SqliteOrderRecoveryStore(path)
    assert reopened.chain_valid() is True
    assert reopened.latest("cid-1").state is RecoveryState.ORDER_SUBMISSION_UNKNOWN
    assert reopened.deployment_blocked("alpha-a") is True
    reopened.close()


def test_found_terminal_order_is_taken_over_and_not_retried(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    _register(store)
    venue = _FoundVenue(VenueOrderResult(
        venue_order_id="venue-1", client_order_id="cid-1", instrument_id="BTC",
        status="FILLED", filled_qty=0.01, avg_price=60000.0,
    ))

    result = resolve_unknown_orders(store, venue, now_ms=200)
    assert result["outcomes"][0]["outcome"] == "RESOLVED"
    assert store.latest("cid-1").state is RecoveryState.RESOLVED
    assert store.deployment_blocked("alpha-a") is False
    # A resolved deterministic id is spent forever; a replay cannot make a
    # second venue order through this recovery gate.
    assert store.client_submission_permitted(
        deployment_id="alpha-a", client_order_id="cid-1"
    ) is False


def test_terminal_recovery_journals_and_projects_before_resolving(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    _register(store)
    event_store = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)
    ledger = LiveLedger(str(tmp_path / "ledger.db"), create=True)
    oms = OmsAuthority(event_store, ledger, recovery_store=store)
    oms.register_submission(
        client_order_id="cid-1",
        instrument_id="BTC",
        side=OrderSide.BUY,
        order_quantity=0.01,
        strategy_id="alpha-a",
        order_type="MARKET",
        limit_price=None,
        observed_at_ms=100,
        command_event_id="command-cid-1",
    )
    venue = _FoundVenue(VenueOrderResult(
        venue_order_id="venue-1", client_order_id="cid-1", instrument_id="BTC",
        status="FILLED", filled_qty=0.01, avg_price=60000.0,
    ))

    result = resolve_unknown_orders(
        store, venue, now_ms=200, oms_authority=oms,
    )

    assert result["outcomes"][0]["outcome"] == "RESOLVED"
    assert store.latest("cid-1").state is RecoveryState.RESOLVED
    assert ledger.all_positions() == {"BTC": pytest.approx(0.01)}
    assert event_store.row_count() == 2
    ledger.close()
    event_store.close()
    store.close()


def test_recovery_cannot_resolve_when_oms_has_no_prejournaled_order(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    _register(store)
    event_store = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)
    ledger = LiveLedger(str(tmp_path / "ledger.db"), create=True)
    oms = OmsAuthority(event_store, ledger, recovery_store=store)
    venue = _FoundVenue(VenueOrderResult(
        venue_order_id="venue-1", client_order_id="cid-1", instrument_id="BTC",
        status="FILLED", filled_qty=0.01, avg_price=60000.0,
    ))

    result = resolve_unknown_orders(
        store, venue, now_ms=200, oms_authority=oms,
    )

    assert result["outcomes"][0]["outcome"] == "OMS_PROJECTION_BLOCKED"
    assert store.latest("cid-1").state is RecoveryState.FOUND
    assert store.deployment_blocked("alpha-a") is True
    assert event_store.row_count() == 0
    assert ledger.orders() == []
    ledger.close()
    event_store.close()
    store.close()


def test_confirmed_absence_requires_explicit_retry_authorization(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    _register(store)
    venue = _FoundVenue(None)

    result = resolve_unknown_orders(store, venue, now_ms=200)
    assert result["outcomes"][0]["outcome"] == "CONFIRMED_ABSENT"
    assert store.latest("cid-1").state is RecoveryState.CONFIRMED_ABSENT
    assert store.client_submission_permitted(
        deployment_id="alpha-a", client_order_id="cid-1"
    ) is False

    store.authorize_retry("cid-1", now_ms=300)
    assert store.retry_permitted(
        deployment_id="alpha-a", client_order_id="cid-1"
    ) is True
    assert store.client_submission_permitted(
        deployment_id="alpha-a", client_order_id="cid-1"
    ) is True


def test_query_failure_arms_kill_switch_and_keeps_deployment_blocked(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    _register(store)
    kill_switch = KillSwitch()

    result = resolve_unknown_orders(
        store, _BrokenVenue(), now_ms=200, kill_switch=kill_switch,
    )
    assert result["outcomes"][0]["outcome"] == "KILL_SWITCHED"
    assert store.latest("cid-1").state is RecoveryState.KILL_SWITCHED
    assert store.deployment_blocked("alpha-a") is True
    assert kill_switch.is_active() is True


def test_query_failure_without_kill_switch_remains_explicitly_indeterminate(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    _register(store)

    result = resolve_unknown_orders(store, _BrokenVenue(), now_ms=200)
    assert result["outcomes"][0]["outcome"] == "INDETERMINATE"
    assert store.latest("cid-1").state is RecoveryState.INDETERMINATE
    assert store.deployment_blocked("alpha-a") is True


def test_same_id_different_payload_is_a_conflict(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    _register(store)
    with pytest.raises(Exception):
        store.register_unknown(
            client_order_id="cid-1", deployment_id="alpha-a",
            instrument_id="BTC", side="SELL", quantity=0.01, now_ms=101,
        )
