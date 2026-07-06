"""VenueAdapter Protocol(live/exchange/base.py):runtime_checkable 结构鸭子类型验证。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.2。"""
from live.contracts import VenueOrderResult
from live.exchange.base import VenueAdapter


class _FakeVenue:
    venue_mode = "testnet"
    venue_name = "fake"

    def place_order(self, order):
        return VenueOrderResult(venue_order_id="1", client_order_id=order.client_order_id,
                                instrument_id=order.instrument_id, status="NEW")

    def get_order(self, instrument_id, client_order_id):
        return None

    def cancel_order(self, instrument_id, client_order_id):
        return None

    def account(self):
        return {}

    def positions(self):
        return {}

    def exchange_filters(self):
        return {}

    def book_ticker(self, instrument_id):
        return None


def test_fake_venue_satisfies_protocol():
    assert isinstance(_FakeVenue(), VenueAdapter)


def test_incomplete_object_does_not_satisfy_protocol():
    class _Half:
        venue_mode = "testnet"

        def place_order(self, order):
            return None

    assert not isinstance(_Half(), VenueAdapter)


def test_venue_without_cancel_order_does_not_satisfy_protocol():
    # 撤单是协议的一等成员(OMS 超时策略依赖它)——缺了它不算一个完整的 venue
    class _NoCancel:
        venue_mode = "testnet"

        def place_order(self, order):
            return None

        def get_order(self, instrument_id, client_order_id):
            return None

        def account(self):
            return {}

        def positions(self):
            return {}

        def exchange_filters(self):
            return {}

        def book_ticker(self, instrument_id):
            return None

    assert not isinstance(_NoCancel(), VenueAdapter)


def test_venue_without_book_ticker_does_not_satisfy_protocol():
    # book_ticker 是 LIMIT 定价锚的来源(2026-07-06 战线②)——协议加了这个必须成员后,
    # 缺了它的 venue 不再算一个完整实现(批判必须修改:_FakeVenue 等 fake 必须同步)
    class _NoBookTicker:
        venue_mode = "testnet"

        def place_order(self, order):
            return None

        def get_order(self, instrument_id, client_order_id):
            return None

        def cancel_order(self, instrument_id, client_order_id):
            return None

        def account(self):
            return {}

        def positions(self):
            return {}

        def exchange_filters(self):
            return {}

    assert not isinstance(_NoBookTicker(), VenueAdapter)


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


def test_adapters_declare_distinct_venue_names():
    # mandate 交易所域匹配的根基:两个适配器必须自报不同的 venue_name(2026-07-05)
    from live.exchange.binance_futures import BinanceFuturesAdapter
    from live.exchange.bybit_futures import BybitFuturesAdapter
    assert BinanceFuturesAdapter.venue_name == "binance"
    assert BybitFuturesAdapter.venue_name == "bybit"
    assert BinanceFuturesAdapter.venue_name != BybitFuturesAdapter.venue_name
