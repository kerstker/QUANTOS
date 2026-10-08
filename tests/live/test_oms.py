from __future__ import annotations

import pytest

from live.execution.oms import OmsAuthority, OmsConflict, OmsProjectionError
from live.execution.order_event_store import (
    OrderEventSource,
    SqliteOrderEventStore,
)
from live.execution.position_ledger import LiveLedger
from live.execution.order_recovery import (
    RecoveryState,
    SqliteOrderRecoveryStore,
)
from quantos.contracts.hashing import stable_hash
from quantos.contracts.order import OrderState
from quantos.contracts.trading import OrderSide


NOW = 1_000_000


def _oms(tmp_path, suffix: str = ""):
    events = SqliteOrderEventStore(
        str(tmp_path / f"events{suffix}.db"), create=True
    )
    ledger = LiveLedger(str(tmp_path / f"ledger{suffix}.db"), create=True)
    recovery = SqliteOrderRecoveryStore(
        str(tmp_path / f"recovery{suffix}.db"), create=True
    )
    return (
        OmsAuthority(events, ledger, recovery_store=recovery),
        events,
        ledger,
        recovery,
    )


def _register(oms: OmsAuthority, *, quantity: float = 1.0) -> None:
    oms.register_submission(
        client_order_id="cid-1",
        instrument_id="BTC",
        side=OrderSide.BUY,
        order_quantity=quantity,
        strategy_id="hourly-paper",
        order_type="MARKET",
        limit_price=None,
        observed_at_ms=NOW,
        command_event_id="command-cid-1",
    )


def _snapshot(
    oms: OmsAuthority,
    *,
    source: OrderEventSource,
    source_sequence: int,
    venue_event_id: str,
    status: str,
    cumulative: float,
    average: float,
    observed_at_ms: int,
):
    payload = {
        "status": status,
        "executedQty": cumulative,
        "avgPrice": average,
    }
    return oms.ingest_venue_snapshot(
        client_order_id="cid-1",
        source=source,
        source_sequence=source_sequence,
        venue_event_id=venue_event_id,
        venue_order_id="venue-1",
        venue_status=status,
        cumulative_filled_qty=cumulative,
        avg_fill_price=average,
        observed_at_ms=observed_at_ms,
        venue_payload_hash=stable_hash(payload),
    )


def test_partial_fill_cross_source_duplicate_and_final_fill_replay_exactly(tmp_path) -> None:
    oms, events, ledger, recovery = _oms(tmp_path)
    _register(oms)
    _snapshot(
        oms,
        source=OrderEventSource.REST_POLL,
        source_sequence=1,
        venue_event_id="rest-partial",
        status="PARTIALLY_FILLED",
        cumulative=0.4,
        average=100.0,
        observed_at_ms=NOW + 1,
    )
    _snapshot(
        oms,
        source=OrderEventSource.USER_STREAM,
        source_sequence=10,
        venue_event_id="stream-same-partial",
        status="PARTIALLY_FILLED",
        cumulative=0.4,
        average=100.0,
        observed_at_ms=NOW + 2,
    )
    _snapshot(
        oms,
        source=OrderEventSource.REST_POLL,
        source_sequence=2,
        venue_event_id="rest-filled",
        status="FILLED",
        cumulative=1.0,
        average=110.0,
        observed_at_ms=NOW + 3,
    )

    fills = ledger.fills("BTC")
    assert len(fills) == 2
    assert fills[0]["quantity"] == pytest.approx(0.4)
    assert fills[0]["price"] == pytest.approx(100.0)
    assert fills[1]["quantity"] == pytest.approx(0.6)
    assert fills[1]["price"] == pytest.approx((110.0 - 40.0) / 0.6)
    assert ledger.orders()[0]["canonical_state"] == OrderState.FILLED.value
    assert ledger.all_positions() == {"BTC": pytest.approx(1.0)}
    assert events.row_count() == 4

    replay_ledger = LiveLedger(str(tmp_path / "replay-ledger.db"), create=True)
    replay = OmsAuthority(events, replay_ledger, recovery_store=recovery)
    replay.replay()

    assert replay_ledger.orders() == ledger.orders()
    assert replay_ledger.fills("BTC") == fills
    assert replay_ledger.all_positions() == ledger.all_positions()
    replay_ledger.close()
    ledger.close()
    events.close()
    recovery.close()


def test_cancel_fill_race_preserves_incremental_fill_and_cancel_terminal(tmp_path) -> None:
    oms, events, ledger, recovery = _oms(tmp_path)
    _register(oms)
    _snapshot(
        oms,
        source=OrderEventSource.REST_POLL,
        source_sequence=1,
        venue_event_id="partial",
        status="PARTIALLY_FILLED",
        cumulative=0.25,
        average=100.0,
        observed_at_ms=NOW + 1,
    )
    _snapshot(
        oms,
        source=OrderEventSource.REST_POLL,
        source_sequence=2,
        venue_event_id="cancelled-after-more-fill",
        status="CANCELED",
        cumulative=0.40,
        average=105.0,
        observed_at_ms=NOW + 2,
    )

    assert sum(item["quantity"] for item in ledger.fills("BTC")) == pytest.approx(0.4)
    assert ledger.orders()[0]["canonical_state"] == OrderState.CANCELLED.value
    assert ledger.all_positions() == {"BTC": pytest.approx(0.4)}
    ledger.close()
    events.close()
    recovery.close()


def test_terminal_regression_is_journaled_but_never_projected_or_hidden(tmp_path) -> None:
    oms, events, ledger, recovery = _oms(tmp_path)
    _register(oms)
    _snapshot(
        oms,
        source=OrderEventSource.REST_POLL,
        source_sequence=1,
        venue_event_id="filled",
        status="FILLED",
        cumulative=1.0,
        average=100.0,
        observed_at_ms=NOW + 1,
    )

    with pytest.raises(OmsProjectionError, match="illegal order transition"):
        _snapshot(
            oms,
            source=OrderEventSource.USER_STREAM,
            source_sequence=1,
            venue_event_id="late-new",
            status="NEW",
            cumulative=0.0,
            average=0.0,
            observed_at_ms=NOW + 2,
        )

    assert events.row_count() == 3
    assert ledger.orders()[0]["canonical_state"] == OrderState.FILLED.value
    assert len(ledger.unprojected_oms_event_hashes(events.event_hashes())) == 1
    assert recovery.latest("cid-1").state is RecoveryState.ORDER_SUBMISSION_UNKNOWN
    with pytest.raises(OmsProjectionError, match="illegal order transition"):
        oms.replay()
    ledger.close()
    events.close()
    recovery.close()


def test_terminal_snapshot_cannot_change_fill_economics(tmp_path) -> None:
    oms, events, ledger, recovery = _oms(tmp_path)
    _register(oms)
    _snapshot(
        oms,
        source=OrderEventSource.REST_POLL,
        source_sequence=1,
        venue_event_id="cancelled",
        status="CANCELED",
        cumulative=0.2,
        average=100.0,
        observed_at_ms=NOW + 1,
    )

    with pytest.raises(OmsProjectionError, match="terminal order economics changed"):
        _snapshot(
            oms,
            source=OrderEventSource.USER_STREAM,
            source_sequence=1,
            venue_event_id="cancelled-correction",
            status="CANCELED",
            cumulative=0.3,
            average=101.0,
            observed_at_ms=NOW + 2,
        )

    assert sum(item["quantity"] for item in ledger.fills("BTC")) == pytest.approx(0.2)
    assert recovery.deployment_blocked("hourly-paper") is True
    ledger.close()
    events.close()
    recovery.close()


def test_unknown_order_snapshot_is_rejected_before_journal(tmp_path) -> None:
    oms, events, ledger, recovery = _oms(tmp_path)

    with pytest.raises(OmsConflict, match="unknown order"):
        _snapshot(
            oms,
            source=OrderEventSource.REST_POLL,
            source_sequence=1,
            venue_event_id="unknown",
            status="NEW",
            cumulative=0.0,
            average=0.0,
            observed_at_ms=NOW,
        )

    assert events.row_count() == 0
    assert ledger.orders() == []
    ledger.close()
    events.close()
    recovery.close()


def test_projection_crash_leaves_replayable_event_and_never_loses_fill(tmp_path) -> None:
    oms, events, ledger, recovery = _oms(tmp_path)
    _register(oms)
    ledger.close()

    with pytest.raises(OmsProjectionError):
        _snapshot(
            oms,
            source=OrderEventSource.REST_POLL,
            source_sequence=1,
            venue_event_id="filled-after-ledger-crash",
            status="FILLED",
            cumulative=1.0,
            average=100.0,
            observed_at_ms=NOW + 1,
        )

    assert events.row_count() == 2
    recovered_ledger = LiveLedger(str(tmp_path / "recovered-ledger.db"), create=True)
    assert recovery.latest("cid-1").state is RecoveryState.ORDER_SUBMISSION_UNKNOWN
    recovered = OmsAuthority(
        events, recovered_ledger, recovery_store=recovery
    )
    recovered.replay()

    assert recovered_ledger.orders()[0]["canonical_state"] == OrderState.FILLED.value
    assert recovered_ledger.all_positions() == {"BTC": pytest.approx(1.0)}
    recovered_ledger.close()
    events.close()
    recovery.close()


def test_prevenue_command_projection_failure_never_claims_submission_unknown(tmp_path) -> None:
    oms, events, ledger, recovery = _oms(tmp_path)
    ledger.close()

    with pytest.raises(OmsProjectionError):
        _register(oms)

    assert events.row_count() == 1
    assert recovery.latest("cid-1") is None
    recovered_ledger = LiveLedger(str(tmp_path / "command-replay.db"), create=True)
    recovered = OmsAuthority(
        events, recovered_ledger, recovery_store=recovery
    )
    recovered.replay()
    assert recovered_ledger.orders()[0]["status"] == "PENDING_NEW"
    recovered_ledger.close()
    events.close()
    recovery.close()
