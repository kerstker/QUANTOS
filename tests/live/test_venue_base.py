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


def test_binance_adapter_satisfies_protocol():
    from live.exchange.binance_futures import BinanceFuturesAdapter
    c = BinanceFuturesAdapter(api_key="k", api_secret="s", base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=lambda *a, **kw: (200, b"{}"))
    assert isinstance(c, VenueAdapter)


def test_bybit_adapter_satisfies_protocol():
    from live.exchange.bybit_futures import BybitFuturesAdapter
    c = BybitFuturesAdapter(api_key="k", api_secret="s", base_url=BybitFuturesAdapter.TESTNET_BASE,
                            http_request=lambda *a, **kw: (200, b"{}"))
    assert isinstance(c, VenueAdapter)
