"""VenueAdapter Protocol(live/exchange/base.py):runtime_checkable 结构鸭子类型验证。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.2。"""
from live.contracts import VenueOrderResult
from live.exchange.base import VenueAdapter


class _FakeVenue:
    venue_mode = "testnet"

    def place_order(self, order):
        return VenueOrderResult(venue_order_id="1", client_order_id=order.client_order_id,
                                instrument_id=order.instrument_id, status="NEW")

    def get_order(self, instrument_id, client_order_id):
        return None

    def account(self):
        return {}

    def positions(self):
        return {}

    def exchange_filters(self):
        return {}


def test_fake_venue_satisfies_protocol():
    assert isinstance(_FakeVenue(), VenueAdapter)


def test_incomplete_object_does_not_satisfy_protocol():
    class _Half:
        venue_mode = "testnet"

        def place_order(self, order):
            return None

    assert not isinstance(_Half(), VenueAdapter)
