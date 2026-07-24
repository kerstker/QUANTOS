"""Arrival-price evidence and fail-closed TCA wiring tests."""

import pytest

from live.execution.position_ledger import LiveLedger, LiveLedgerConflictError
from live.execution.tca import ArrivalSnapshot, validate_arrival_snapshot
from quantos.contracts.trading import OrderSide

from .test_pipeline import (
    _NOW,
    _PRICES,
    _WIDE_MANDATE,
    _ctx_factory,
    _gate,
    _FakeVenue,
    submit_live_batch,
)
from services.monitoring.audit_log import GlobalAuditLog


def _snapshot(*, source="fake.book_ticker", captured_at_ms=_NOW):
    return ArrivalSnapshot.from_book(
        {"bid": 59_990.0, "ask": 60_010.0, "as_of_ms": captured_at_ms},
        source=source,
        captured_at_ms=captured_at_ms,
    )


def _run_with_snapshots(tmp_path, snapshots, **kwargs):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0,
        current_positions={}, gate=_gate(), ctx_factory=_ctx_factory(), venue=venue,
        mandate=_WIDE_MANDATE, ledger=ledger, audit=audit,
        strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW,
        arrival_snapshots=snapshots, **kwargs,
    )
    return result, ledger, venue, audit


def test_arrival_snapshot_from_book_retains_quote_and_source():
    snapshot = _snapshot()
    assert snapshot.price == pytest.approx(60_000.0)
    assert snapshot.bid == pytest.approx(59_990.0)
    assert snapshot.ask == pytest.approx(60_010.0)
    assert snapshot.as_of_ms == _NOW
    assert snapshot.source == "fake.book_ticker"


@pytest.mark.parametrize(
    "book",
    [
        {},
        {"bid": 0.0, "ask": 1.0},
        {"bid": 2.0, "ask": 1.0},
        {"bid": "bad", "ask": 1.0},
    ],
)
def test_arrival_snapshot_rejects_invalid_book(book):
    with pytest.raises(ValueError):
        ArrivalSnapshot.from_book(book, source="fake", captured_at_ms=_NOW)


def test_arrival_snapshot_validator_is_explicit_about_missing_stale_and_source():
    assert validate_arrival_snapshot(None, now_ms=_NOW) == "arrival_snapshot_missing"
    assert validate_arrival_snapshot(
        _snapshot(captured_at_ms=_NOW - 101), now_ms=_NOW, max_age_ms=100
    ) == "arrival_snapshot_stale"
    assert validate_arrival_snapshot(
        _snapshot(), now_ms=_NOW, expected_source="other.book"
    ) == "arrival_snapshot_source_mismatch"
    assert validate_arrival_snapshot(
        _snapshot(), now_ms=_NOW, max_age_ms=100
    ) is None


def test_arrival_snapshot_validator_rejects_stale_or_future_provider_time():
    stale_provider = ArrivalSnapshot.from_book(
        {"bid": 59_990.0, "ask": 60_010.0, "as_of_ms": _NOW - 101},
        source="fake.book_ticker",
        captured_at_ms=_NOW,
    )
    future_provider = ArrivalSnapshot.from_book(
        {"bid": 59_990.0, "ask": 60_010.0, "as_of_ms": _NOW + 1},
        source="fake.book_ticker",
        captured_at_ms=_NOW,
    )

    assert validate_arrival_snapshot(
        stale_provider, now_ms=_NOW, max_age_ms=100
    ) == "arrival_snapshot_provider_stale"
    assert validate_arrival_snapshot(
        future_provider, now_ms=_NOW, max_age_ms=100
    ) == "arrival_snapshot_provider_time_from_future"


def test_ledger_persists_arrival_snapshot_and_uses_it_for_slippage(tmp_path):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    snapshot = ArrivalSnapshot.from_book(
        {"bid": 99.0, "ask": 101.0, "as_of_ms": _NOW},
        source="fake.book", captured_at_ms=_NOW,
    )
    ledger.record_tca(
        "f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
        decision_price=110.0, fill_price=101.0, quantity=1.0,
        strategy_id="s", recorded_at=_NOW, arrival_snapshot=snapshot,
    )
    row = ledger.tca_records()[0]
    assert row["arrival_price"] == pytest.approx(100.0)
    assert row["arrival_bid"] == pytest.approx(99.0)
    assert row["arrival_ask"] == pytest.approx(101.0)
    assert row["arrival_source"] == "fake.book"
    assert row["slippage_bps"] == pytest.approx(100.0)

    ledger.record_tca(
        "f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
        decision_price=110.0, fill_price=101.0, quantity=1.0,
        strategy_id="s", recorded_at=_NOW + 1, arrival_snapshot=snapshot,
    )
    assert len(ledger.tca_records()) == 1

    with pytest.raises(LiveLedgerConflictError, match="TCA fill_id collision"):
        ledger.record_tca(
            "f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
            decision_price=110.0, fill_price=101.0, quantity=1.0,
            strategy_id="s", recorded_at=_NOW + 2,
            arrival_snapshot=ArrivalSnapshot.from_book(
                {"bid": 98.0, "ask": 100.0, "as_of_ms": _NOW},
                source="fake.book", captured_at_ms=_NOW,
            ),
        )


def test_pipeline_records_snapshot_tca_and_does_not_use_decision_proxy(tmp_path):
    result, ledger, venue, _audit = _run_with_snapshots(
        tmp_path, {"BTC": _snapshot()}, arrival_expected_source="fake.book_ticker"
    )
    assert result.tier == "AUTO"
    assert len(venue.calls) == 1
    row = ledger.tca_records()[0]
    assert row["arrival_source"] == "fake.book_ticker"
    assert row["arrival_price"] == pytest.approx(60_000.0)


def test_pipeline_blocks_missing_snapshot_before_venue_call(tmp_path):
    result, ledger, venue, audit = _run_with_snapshots(
        tmp_path, {}, arrival_expected_source="fake.book_ticker"
    )
    assert result.tier == "NEEDS_HUMAN_REVIEW"
    assert any("arrival_snapshot_missing" in reason for reason in result.mandate_reasons)
    assert venue.calls == []
    assert ledger.tca_records() == []
    assert any(event.action == "LIVE_ARRIVAL_SNAPSHOT_BLOCKED" for event in audit.events)


def test_pipeline_blocks_stale_snapshot_before_venue_call(tmp_path):
    result, _ledger, venue, _audit = _run_with_snapshots(
        tmp_path, {"BTC": _snapshot(captured_at_ms=_NOW - 301_000)},
        arrival_expected_source="fake.book_ticker",
    )
    assert result.tier == "NEEDS_HUMAN_REVIEW"
    assert any("arrival_snapshot_stale" in reason for reason in result.mandate_reasons)
    assert venue.calls == []


def test_pipeline_blocks_mismatched_snapshot_source_before_venue_call(tmp_path):
    result, _ledger, venue, _audit = _run_with_snapshots(
        tmp_path, {"BTC": _snapshot(source="coingecko")},
        arrival_expected_source="fake.book_ticker",
    )
    assert result.tier == "NEEDS_HUMAN_REVIEW"
    assert any("arrival_snapshot_source_mismatch" in reason for reason in result.mandate_reasons)
    assert venue.calls == []

