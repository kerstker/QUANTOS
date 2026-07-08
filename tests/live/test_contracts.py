"""live/contracts.py 值类型:frozen、字段完整性。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.1;
LIMIT 扩展:docs/superpowers/specs/2026-07-06-limit-order-execution-design.md §1。"""
import math

import pytest

from live.contracts import ExchangeFilters, LiveOrderRequest, VenueOrderResult, VenuePosition
from quantos.contracts.trading import OrderSide


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


# --- LIMIT 契约扩展(2026-07-06 战线②):默认路径字节不变,LIMIT 构造期 fail-closed 校验 ---

def test_default_construction_is_market_with_no_limit_fields():
    # 不传新字段 == 改动前的完全同一构造调用,default 值必须恰好是 MARKET/None/False
    o = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1")
    assert o.order_type == "MARKET" and o.limit_price is None and o.post_only is False


def test_market_order_construction_still_succeeds_explicitly():
    o = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1", order_type="MARKET")
    assert o.limit_price is None


@pytest.mark.parametrize("bad_price", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_limit_requires_finite_positive_price(bad_price):
    with pytest.raises(ValueError):
        LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1", order_type="LIMIT", limit_price=bad_price)


def test_limit_with_valid_price_constructs():
    o = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1", order_type="LIMIT", limit_price=60000.0)
    assert o.order_type == "LIMIT" and o.limit_price == 60000.0


def test_limit_with_post_only_constructs():
    o = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1", order_type="LIMIT", limit_price=60000.0,
                         post_only=True)
    assert o.post_only is True


def test_market_rejects_limit_price():
    with pytest.raises(ValueError):
        LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1", order_type="MARKET", limit_price=60000.0)


def test_market_rejects_post_only():
    with pytest.raises(ValueError):
        LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1", order_type="MARKET", post_only=True)


def test_unknown_order_type_rejected():
    with pytest.raises(ValueError):
        LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                         client_order_id="c1", order_type="STOP_LIMIT")
