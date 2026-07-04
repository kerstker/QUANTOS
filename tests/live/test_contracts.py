"""live/contracts.py 值类型:frozen、字段完整性。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.1。"""
import pytest

from live.contracts import ExchangeFilters, VenueOrderResult, VenuePosition


def test_venue_order_result_is_frozen():
    r = VenueOrderResult(venue_order_id="123", client_order_id="abc", instrument_id="BTC",
                         status="FILLED", filled_qty=0.5, avg_price=100.0, raw={"x": 1})
    with pytest.raises((AttributeError, TypeError)):
        r.status = "NEW"


def test_venue_position_fields():
    p = VenuePosition(instrument_id="ETH", quantity=-2.5, entry_price=3000.0)
    assert p.instrument_id == "ETH" and p.quantity == -2.5 and p.entry_price == 3000.0


def test_exchange_filters_fields():
    f = ExchangeFilters(instrument_id="BTC", step_size=0.001, min_qty=0.001,
                        min_notional=5.0, tick_size=0.1)
    assert f.step_size == 0.001 and f.min_notional == 5.0
