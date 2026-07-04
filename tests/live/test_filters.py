"""交易所过滤器 + 冪等 client_order_id(live/execution/filters.py)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.4。"""
import pytest

from live.contracts import ExchangeFilters
from live.execution.filters import apply_exchange_filters, client_order_id, round_to_step
from quantos.contracts.trading import OrderSide

_BTC_FILT = ExchangeFilters(instrument_id="BTC", step_size=0.001, min_qty=0.001,
                           min_notional=5.0, tick_size=0.1)
_LOWPRICE_FILT = ExchangeFilters(instrument_id="XRP", step_size=1.0, min_qty=1.0,
                                min_notional=5.0, tick_size=0.0001)


# --- client_order_id ---

def test_client_order_id_is_deterministic():
    a = client_order_id("crowd_contra", 1_700_000_000_000, "BTC")
    b = client_order_id("crowd_contra", 1_700_000_000_000, "BTC")
    assert a == b


def test_client_order_id_differs_on_any_input_change():
    base = client_order_id("crowd_contra", 1_700_000_000_000, "BTC")
    assert client_order_id("carry_oi_majors", 1_700_000_000_000, "BTC") != base
    assert client_order_id("crowd_contra", 1_700_000_000_001, "BTC") != base
    assert client_order_id("crowd_contra", 1_700_000_000_000, "ETH") != base


def test_client_order_id_fits_binance_limit():
    cid = client_order_id("crowd_contra", 1_700_000_000_000, "BTC")
    assert 0 < len(cid) <= 36
    assert all(c.isalnum() for c in cid)          # 无特殊字符,肯定合法


# --- round_to_step ---

def test_round_to_step_floors_to_grid():
    assert round_to_step(1.2345, 0.001) == pytest.approx(1.234)
    assert round_to_step(1.0, 0.1) == pytest.approx(1.0)
    assert round_to_step(0.0009, 0.001) == pytest.approx(0.0)


def test_round_to_step_handles_whole_number_step():
    assert round_to_step(333.7, 1.0) == pytest.approx(333.0)


def test_round_to_step_zero_step_is_passthrough():
    assert round_to_step(1.2345, 0.0) == pytest.approx(1.2345)


# --- apply_exchange_filters ---

def test_apply_exchange_filters_rounds_and_assigns_client_order_id():
    desired = [("BTC", OrderSide.BUY, 0.0158)]
    adjusted, dropped = apply_exchange_filters(
        desired, {"BTC": _BTC_FILT}, {"BTC": 60000.0},
        strategy_id="crowd_contra", decided_ts=1_700_000_000_000)
    assert dropped == []
    assert len(adjusted) == 1
    o = adjusted[0]
    assert o.instrument_id == "BTC" and o.side == OrderSide.BUY
    assert o.quantity == pytest.approx(0.015)                # 0.0158 -> floor to 0.001 grid
    assert o.client_order_id == client_order_id("crowd_contra", 1_700_000_000_000, "BTC")


def test_apply_exchange_filters_drops_when_no_filter_available():
    adjusted, dropped = apply_exchange_filters(
        [("GHOST", OrderSide.BUY, 10.0)], {}, {"GHOST": 1.0},
        strategy_id="s", decided_ts=1)
    assert adjusted == []
    assert dropped == [("GHOST", "no_filter")]


def test_apply_exchange_filters_drops_below_min_qty_after_rounding():
    # 0.0009 < step 0.001 -> 取整后为 0,低于 min_qty
    adjusted, dropped = apply_exchange_filters(
        [("BTC", OrderSide.BUY, 0.0009)], {"BTC": _BTC_FILT}, {"BTC": 60000.0},
        strategy_id="s", decided_ts=1)
    assert adjusted == []
    assert dropped == [("BTC", "below_min_qty")]


def test_apply_exchange_filters_drops_below_min_notional():
    # 数量够 min_qty 但名义额(qty*price)低于 min_notional
    small_filt = ExchangeFilters(instrument_id="BTC", step_size=0.001, min_qty=0.001,
                                 min_notional=100.0, tick_size=0.1)
    adjusted, dropped = apply_exchange_filters(
        [("BTC", OrderSide.BUY, 0.001)], {"BTC": small_filt}, {"BTC": 60000.0},   # notional=$60
        strategy_id="s", decided_ts=1)
    assert adjusted == []
    assert dropped == [("BTC", "below_min_notional")]


def test_apply_exchange_filters_drops_when_price_missing():
    adjusted, dropped = apply_exchange_filters(
        [("BTC", OrderSide.BUY, 0.01)], {"BTC": _BTC_FILT}, {},   # 无价格
        strategy_id="s", decided_ts=1)
    assert adjusted == []
    assert dropped == [("BTC", "no_price")]


def test_apply_exchange_filters_is_fail_soft_across_batch():
    # 一笔缺过滤器不影响其它笔正常通过
    desired = [("GHOST", OrderSide.BUY, 10.0), ("BTC", OrderSide.SELL, 0.02)]
    adjusted, dropped = apply_exchange_filters(
        desired, {"BTC": _BTC_FILT}, {"BTC": 60000.0, "GHOST": 1.0},
        strategy_id="s", decided_ts=1)
    assert len(adjusted) == 1 and adjusted[0].instrument_id == "BTC"
    assert dropped == [("GHOST", "no_filter")]


def test_apply_exchange_filters_low_price_coin_whole_unit_step():
    desired = [("XRP", OrderSide.BUY, 333.7)]
    adjusted, dropped = apply_exchange_filters(
        desired, {"XRP": _LOWPRICE_FILT}, {"XRP": 0.6},
        strategy_id="s", decided_ts=1)
    assert dropped == []
    assert adjusted[0].quantity == pytest.approx(333.0)
