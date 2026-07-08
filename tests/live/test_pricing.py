"""live/execution/pricing.py:LIMIT 定价——盘口锚 + 工程容忍带 + 按 tick 取整。设计:
docs/superpowers/specs/2026-07-06-limit-order-execution-design.md §2;
批判修正(2026-07-06):锚校验必须防"成功但退化"的响应,不只防网络失败。

纯函数、无网络/墙钟——全部输入通过参数注入。"""
import math

import pytest

from live.contracts import ExchangeFilters, LiveOrderRequest
from live.execution.pricing import (LimitPricing, limit_price_for, resolve_anchor,
                                    round_price_to_tick, to_limit_orders)
from quantos.contracts.trading import OrderSide

_FILT = ExchangeFilters(instrument_id="BTC", step_size=0.001, min_qty=0.001,
                       min_notional=5.0, tick_size=0.1)


def _order(inst="BTC", side=OrderSide.BUY, qty=0.015, cid="c1"):
    return LiveOrderRequest(instrument_id=inst, side=side, quantity=qty, client_order_id=cid)


# --- round_price_to_tick ---

def test_round_price_to_tick_buy_floors():
    assert round_price_to_tick(100.27, 0.1, OrderSide.BUY) == pytest.approx(100.2)


def test_round_price_to_tick_sell_ceils():
    assert round_price_to_tick(100.23, 0.1, OrderSide.SELL) == pytest.approx(100.3)


def test_round_price_to_tick_none_tick_is_passthrough():
    assert round_price_to_tick(100.2345, None, OrderSide.BUY) == pytest.approx(100.2345)


def test_round_price_to_tick_zero_tick_is_passthrough():
    assert round_price_to_tick(100.2345, 0.0, OrderSide.SELL) == pytest.approx(100.2345)


def test_round_price_to_tick_absorbs_float_noise():
    # 0.1 步长下的经典浮点噪声(0.3/0.1 因二进制浮点略小于 3)不应被 floor 多砍一格
    assert round_price_to_tick(0.3, 0.1, OrderSide.BUY) == pytest.approx(0.3)
    assert round_price_to_tick(0.3, 0.1, OrderSide.SELL) == pytest.approx(0.3)


def test_round_price_to_tick_buy_never_exceeds_original():
    # BUY 向下取整绝不出比意图更高的买价
    r = round_price_to_tick(100.29, 0.1, OrderSide.BUY)
    assert r <= 100.29 + 1e-9


def test_round_price_to_tick_sell_never_below_original():
    r = round_price_to_tick(100.21, 0.1, OrderSide.SELL)
    assert r >= 100.21 - 1e-9


# --- resolve_anchor: 结构校验 + 容忍度门(批判必须修改①:防"成功但退化"的响应) ---

def test_resolve_anchor_uses_book_ticker_mid_when_valid():
    anchor, source = resolve_anchor({"bid": 59990.0, "ask": 60010.0}, decision_price=60000.0)
    assert anchor == pytest.approx(60000.0) and source == "book_ticker"


def test_resolve_anchor_none_book_falls_back_to_decision_price():
    anchor, source = resolve_anchor(None, decision_price=60000.0)
    assert anchor == pytest.approx(60000.0) and source == "decision_price_fallback"


def test_resolve_anchor_no_book_and_no_decision_price_is_no_anchor():
    anchor, source = resolve_anchor(None, decision_price=None)
    assert anchor is None and source == "no_anchor"


def test_resolve_anchor_crossed_book_falls_back():
    # 交叉盘(ask < bid)——形状正确内容错误的退化响应,不是网络失败,必须同样被拒
    anchor, source = resolve_anchor({"bid": 60010.0, "ask": 59990.0}, decision_price=60000.0)
    assert source == "decision_price_fallback" and anchor == pytest.approx(60000.0)


def test_resolve_anchor_zero_or_negative_price_falls_back():
    anchor, source = resolve_anchor({"bid": 0.0, "ask": 60000.0}, decision_price=60000.0)
    assert source == "decision_price_fallback"
    anchor2, source2 = resolve_anchor({"bid": -1.0, "ask": 60000.0}, decision_price=60000.0)
    assert source2 == "decision_price_fallback"


def test_resolve_anchor_extreme_deviation_from_decision_price_falls_back():
    # "天价 mid":盘口结构合法(bid<ask,双正),但与决策价偏离远超容忍度——testnet/demo
    # 流动性稀薄场景下的典型退化数据,不能被当作可信锚
    anchor, source = resolve_anchor({"bid": 119990.0, "ask": 120010.0}, decision_price=60000.0)
    assert source == "decision_price_fallback" and anchor == pytest.approx(60000.0)


def test_resolve_anchor_within_deviation_tolerance_is_accepted():
    # 偏离在容忍度内(默认 5%)—— 60000*1.02 = 61200,仍应接受盘口锚
    anchor, source = resolve_anchor({"bid": 61190.0, "ask": 61210.0}, decision_price=60000.0)
    assert source == "book_ticker" and anchor == pytest.approx(61200.0)


def test_resolve_anchor_custom_deviation_threshold():
    anchor, source = resolve_anchor({"bid": 62000.0, "ask": 62000.0}, decision_price=60000.0,
                                    max_deviation_pct=0.10)
    assert source == "book_ticker"          # 62000/60000-1 ≈ 3.3% < 10% 自定义阈值


def test_resolve_anchor_malformed_book_missing_fields_falls_back():
    anchor, source = resolve_anchor({"bid": None, "ask": 60000.0}, decision_price=60000.0)
    assert source == "decision_price_fallback"


def test_resolve_anchor_no_decision_price_skips_deviation_check_but_keeps_structural_checks():
    # 决策价缺失时无法比较偏离,但结构校验(交叉盘/零价)仍然生效
    anchor, source = resolve_anchor({"bid": 100.0, "ask": 200.0}, decision_price=None)
    assert source == "book_ticker" and anchor == pytest.approx(150.0)
    anchor2, source2 = resolve_anchor({"bid": 200.0, "ask": 100.0}, decision_price=None)
    assert anchor2 is None and source2 == "no_anchor"          # 交叉盘且无决策价可回退


# --- limit_price_for ---

def test_limit_price_for_buy_adds_band_then_rounds():
    p = limit_price_for(OrderSide.BUY, anchor=60000.0, band_pct=0.002, tick_size=0.1)
    assert p == pytest.approx(60120.0)                # 60000*1.002 = 60120.0


def test_limit_price_for_sell_subtracts_band_then_rounds():
    p = limit_price_for(OrderSide.SELL, anchor=60000.0, band_pct=0.002, tick_size=0.1)
    assert p == pytest.approx(59880.0)


def test_limit_price_for_none_anchor_is_none():
    assert limit_price_for(OrderSide.BUY, anchor=None, band_pct=0.002, tick_size=0.1) is None


def test_limit_price_for_non_finite_or_nonpositive_anchor_is_none():
    assert limit_price_for(OrderSide.BUY, anchor=float("nan"), band_pct=0.002, tick_size=0.1) is None
    assert limit_price_for(OrderSide.BUY, anchor=0.0, band_pct=0.002, tick_size=0.1) is None
    assert limit_price_for(OrderSide.BUY, anchor=-100.0, band_pct=0.002, tick_size=0.1) is None


# --- to_limit_orders: never silently falls back to MARKET (critique 明确要求) ---

def test_to_limit_orders_converts_when_anchor_present():
    adjusted = [_order()]
    limit_orders, dropped = to_limit_orders(
        adjusted, anchors={"BTC": 60000.0}, filters={"BTC": _FILT}, pricing=LimitPricing(),
        strategy_id="s", decided_ts=1000)
    assert dropped == []
    assert len(limit_orders) == 1
    o = limit_orders[0]
    assert o.order_type == "LIMIT"
    assert o.limit_price == pytest.approx(round_price_to_tick(60000.0 * 1.002, 0.1, OrderSide.BUY))
    assert o.quantity == 0.015                          # 数量不受定价影响


def test_to_limit_orders_drops_without_anchor_never_silent_market_fallback():
    adjusted = [_order(inst="GHOST", cid="c-ghost")]
    limit_orders, dropped = to_limit_orders(
        adjusted, anchors={}, filters={"BTC": _FILT}, pricing=LimitPricing(),
        strategy_id="s", decided_ts=1000)
    assert limit_orders == []
    assert dropped == [("GHOST", "no_limit_anchor")]
    assert all(o.order_type != "MARKET" for o in limit_orders)   # 输出集合里不存在任何 MARKET 单


def test_to_limit_orders_produces_new_client_order_id_distinct_from_market():
    adjusted = [_order()]
    limit_orders, _ = to_limit_orders(
        adjusted, anchors={"BTC": 60000.0}, filters={"BTC": _FILT}, pricing=LimitPricing(),
        strategy_id="s", decided_ts=1000)
    assert limit_orders[0].client_order_id != adjusted[0].client_order_id


def test_to_limit_orders_post_only_flows_through_from_pricing():
    adjusted = [_order()]
    limit_orders, _ = to_limit_orders(
        adjusted, anchors={"BTC": 60000.0}, filters={"BTC": _FILT},
        pricing=LimitPricing(post_only=True), strategy_id="s", decided_ts=1000)
    assert limit_orders[0].post_only is True


def test_to_limit_orders_missing_filter_uses_no_tick_rounding():
    adjusted = [_order(inst="XRP", cid="c-xrp")]
    limit_orders, dropped = to_limit_orders(
        adjusted, anchors={"XRP": 0.6}, filters={}, pricing=LimitPricing(),
        strategy_id="s", decided_ts=1000)
    assert dropped == []
    assert limit_orders[0].limit_price == pytest.approx(0.6 * 1.002)


def test_to_limit_orders_is_fail_soft_across_batch():
    adjusted = [_order(inst="BTC", cid="c-btc"), _order(inst="GHOST", cid="c-ghost")]
    limit_orders, dropped = to_limit_orders(
        adjusted, anchors={"BTC": 60000.0}, filters={"BTC": _FILT}, pricing=LimitPricing(),
        strategy_id="s", decided_ts=1000)
    assert len(limit_orders) == 1 and limit_orders[0].instrument_id == "BTC"
    assert dropped == [("GHOST", "no_limit_anchor")]


def test_limit_pricing_default_band_is_20bps():
    assert LimitPricing().band_pct == pytest.approx(0.002)


def test_limit_pricing_default_post_only_is_false():
    assert LimitPricing().post_only is False
