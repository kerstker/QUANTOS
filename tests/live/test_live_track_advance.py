"""tools/live_track_advance.py 里可离线测的纯逻辑(gate_limits/make_ctx_factory/PreviewVenue)。
main() 本身是薄胶水(数据刷新+真实 venue I/O),不单测——同 tools/forward_track_advance.py 的
既有惯例(核心逻辑在被测模块里,驱动器用真实运行验证)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §0 延伸。"""
import pytest

from live.contracts import LiveOrderRequest, VenueOrderResult
from quantos.contracts.trading import OrderSide
from tools.live_track_advance import PreviewVenue, gate_limits, make_ctx_factory

_UNIT_SCALE = 1_000_000


class _FakeIntent:
    def __init__(self, instrument_id):
        self.instrument_id = instrument_id


def test_gate_limits_bare_quantity_fields_are_effectively_unlimited():
    limits = gate_limits(["BTC", "ETH"], gross_cap=10000.0, net_cap=1500.0,
                        single_order_cap=2500.0, daily_loss_cap=500.0)
    # 多资产 book 里裸数量阈值不该成为真正的约束(见模块 docstring:不同币单价差好几个数量级)
    assert limits.fat_finger_qty > 10 ** 10
    assert limits.default_max_position_qty > 10 ** 10


def test_gate_limits_notional_fields_come_from_params():
    limits = gate_limits(["BTC"], gross_cap=10000.0, net_cap=1500.0,
                        single_order_cap=2500.0, daily_loss_cap=500.0)
    assert limits.max_gross_exposure == 10000.0
    assert limits.max_net_exposure == 1500.0
    assert limits.max_order_notional == 2500.0
    assert limits.daily_loss_limit == 500.0
    assert limits.allowed_instruments == frozenset({"BTC"})


def test_make_ctx_factory_scales_reference_price_by_unit_scale():
    factory = make_ctx_factory({"BTC": 60000.0}, aum=100_000.0, now=1_700_000_000_000)
    ctx = factory(_FakeIntent("BTC"))
    assert ctx.market.reference_price == pytest.approx(60000.0 / _UNIT_SCALE)
    assert ctx.market.known is True
    assert ctx.account.equity == 100_000.0


def test_make_ctx_factory_handles_missing_price_honestly():
    factory = make_ctx_factory({}, aum=100_000.0, now=1_700_000_000_000)
    ctx = factory(_FakeIntent("GHOST"))
    assert ctx.market.known is False
    assert ctx.market.reference_price is None


# --- PreviewVenue ---

class _FakeRealVenue:
    venue_mode = "testnet"

    def __init__(self):
        self.place_order_calls = []

    def place_order(self, order):
        self.place_order_calls.append(order)
        return VenueOrderResult(venue_order_id="REAL-SHOULD-NEVER-HAPPEN",
                                client_order_id=order.client_order_id,
                                instrument_id=order.instrument_id, status="FILLED",
                                filled_qty=order.quantity, avg_price=100.0)

    def get_order(self, instrument_id, client_order_id):
        return "should-not-be-called-in-preview"

    def exchange_filters(self):
        return {"BTC": "real-filters"}

    def account(self):
        return {"totalWalletBalance": "5000.0"}

    def positions(self):
        return {"BTC": "real-position"}


def test_preview_venue_never_calls_real_place_order():
    real = _FakeRealVenue()
    preview = PreviewVenue(real)
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                             client_order_id="c1")
    result = preview.place_order(order)
    assert real.place_order_calls == []                      # 真 venue 从未被调用
    assert preview.would_submit == [order]
    assert result.status == "PREVIEW_NOT_SUBMITTED"
    assert result.filled_qty == 0.0


def test_preview_venue_get_order_returns_none_not_real_call():
    preview = PreviewVenue(_FakeRealVenue())
    assert preview.get_order("BTC", "c1") is None             # 不透传真实 venue(预览从未真提交)


def test_preview_venue_readonly_methods_pass_through_to_real_venue():
    real = _FakeRealVenue()
    preview = PreviewVenue(real)
    assert preview.exchange_filters() == {"BTC": "real-filters"}
    assert preview.account() == {"totalWalletBalance": "5000.0"}
    assert preview.positions() == {"BTC": "real-position"}
    assert preview.venue_mode == "testnet"
