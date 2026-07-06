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
    a = client_order_id("crowd_contra", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015)
    b = client_order_id("crowd_contra", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015)
    assert a == b


def test_client_order_id_differs_on_any_input_change():
    base = client_order_id("crowd_contra", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015)
    assert client_order_id("carry_oi_majors", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015) != base
    assert client_order_id("crowd_contra", 1_700_000_000_001, "BTC", OrderSide.BUY, 0.015) != base
    assert client_order_id("crowd_contra", 1_700_000_000_000, "ETH", OrderSide.BUY, 0.015) != base


def test_client_order_id_fits_binance_limit():
    cid = client_order_id("crowd_contra", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015)
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
    assert o.client_order_id == client_order_id("crowd_contra", 1_700_000_000_000, "BTC",
                                                OrderSide.BUY, o.quantity)


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


# --- client_order_id 必须区分"同一决策的不同增量"(2026-07-05 双所真实调仓暴露的关键 bug) ---
# 旧键 (strategy, decided_ts, instrument) 下,调仓批与首批同 ID:Binance 重复执行但账本
# INSERT OR IGNORE 静默吞掉记账(仓位漂移),Bybit 直接被交易所去重没执行还误报成功。

def test_client_order_id_differs_for_different_quantities():
    from live.execution.filters import client_order_id
    from quantos.contracts.trading import OrderSide
    a = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0)
    b = client_order_id("s", 1000, "BTC", OrderSide.BUY, 2.5)
    assert a != b                                   # 不同增量 = 不同订单,绝不能同 ID


def test_client_order_id_differs_for_different_sides():
    from live.execution.filters import client_order_id
    from quantos.contracts.trading import OrderSide
    a = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0)
    b = client_order_id("s", 1000, "BTC", OrderSide.SELL, 1.0)
    assert a != b


def test_client_order_id_stable_for_identical_order():
    from live.execution.filters import client_order_id
    from quantos.contracts.trading import OrderSide
    a = client_order_id("s", 1000, "BTC", OrderSide.BUY, 0.015)
    b = client_order_id("s", 1000, "BTC", OrderSide.BUY, 0.015)
    assert a == b                                   # 同一订单重试仍幂等


def test_client_order_id_keeps_decision_prefix_for_parity_linking():
    from live.execution.filters import client_order_id, decision_prefix
    from quantos.contracts.trading import OrderSide
    p = decision_prefix("s", 1000, "BTC")
    a = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0)
    b = client_order_id("s", 1000, "BTC", OrderSide.BUY, 2.5)
    assert a.startswith(p) and b.startswith(p)      # 同一决策的所有订单共享前缀(对账归因用)
    assert not client_order_id("s", 1000, "ETH", OrderSide.BUY, 1.0).startswith(p)


def test_client_order_id_fits_exchange_limits_even_with_slice_suffix():
    from live.execution.filters import client_order_id
    from quantos.contracts.trading import OrderSide
    cid = client_order_id("crowd_contra", 1_783_036_800_000, "DOGE", OrderSide.SELL, 4811.123456)
    assert len(cid) <= 32                            # Binance 上限 36,留 -sNN 切单后缀余量


# --- LIMIT 扩展(2026-07-06 战线②):limit_price 参与订单身份,重定价 = 不同订单 ---
# golden 值钉死:limit_price=None(默认)时输出必须与改动前逐字节相同——已在途的 MARKET
# 幂等链(真实测试网历史订单)绝不能因为这次改动漂移。

def test_client_order_id_market_golden_unchanged():
    cid = client_order_id("crowd_contra", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015)
    assert cid == "4c88b54744c798c567c1457cbbd8b969"


def test_client_order_id_limit_price_none_matches_market_call():
    # limit_price=None(默认参数)与不传该参数完全同一输出——不是"新的默认行为",是同一行为
    a = client_order_id("crowd_contra", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015)
    b = client_order_id("crowd_contra", 1_700_000_000_000, "BTC", OrderSide.BUY, 0.015,
                        limit_price=None)
    assert a == b


def test_client_order_id_limit_reprice_differs_from_market():
    market = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0)
    limit = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0, limit_price=60000.0)
    assert market != limit


def test_client_order_id_differs_for_different_limit_prices():
    a = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0, limit_price=60000.0)
    b = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0, limit_price=60100.0)
    assert a != b                                    # 改价重发不能被交易所当重试去重


def test_client_order_id_same_limit_price_is_idempotent():
    a = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0, limit_price=60000.0)
    b = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0, limit_price=60000.0)
    assert a == b                                    # 同一限价重试仍幂等


def test_client_order_id_limit_still_shares_decision_prefix():
    from live.execution.filters import decision_prefix
    p = decision_prefix("s", 1000, "BTC")
    cid = client_order_id("s", 1000, "BTC", OrderSide.BUY, 1.0, limit_price=60000.0)
    assert cid.startswith(p)
