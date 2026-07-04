"""分级授权 mandate(live/governance/mandate.py):PreTradeRiskGate 批准之后的第二层——
限额内 AUTO(机械执行),否则 NEEDS_HUMAN_REVIEW。fail-closed 到底,绝不默认 AUTO。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.6。"""
import pytest

from live.contracts import LiveOrderRequest
from live.governance.mandate import AUTO, NEEDS_HUMAN_REVIEW, Mandate, evaluate_mandate
from quantos.contracts.trading import OrderSide

_M = Mandate(
    mandate_id="m1", venue="binance_testnet", exchange="binance",
    allowed_instruments=frozenset({"BTC", "ETH"}),
    max_single_order_notional=1000.0, max_gross_notional=2000.0, max_net_notional=1500.0,
    per_instrument_max_notional=(("BTC", 800.0),),
    approved_by=("alice",), issued_at=0, expires_at=10_000,
)


def _order(inst, side, qty, cid="c"):
    return LiveOrderRequest(instrument_id=inst, side=side, quantity=qty, client_order_id=cid)


def test_no_mandate_is_needs_review_even_for_empty_batch():
    d = evaluate_mandate([], {}, None, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "mandate_missing" in d.reasons


def test_expired_mandate_needs_review():
    d = evaluate_mandate([], {}, _M, venue="binance_testnet", exchange="binance", now=999_999)
    assert d.tier == NEEDS_HUMAN_REVIEW and "mandate_expired" in d.reasons


def test_venue_mismatch_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="bybit_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "venue_mismatch" in d.reasons


def test_empty_batch_with_valid_mandate_is_auto():
    d = evaluate_mandate([], {}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == AUTO


def test_within_all_limits_is_auto():
    orders = [_order("BTC", OrderSide.BUY, 0.01, "c1"), _order("ETH", OrderSide.SELL, 0.1, "c2")]
    prices = {"BTC": 60000.0, "ETH": 3000.0}   # BTC notional=$600, ETH notional=$300
    d = evaluate_mandate(orders, prices, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == AUTO and d.reasons == ()


def test_instrument_not_in_allowlist_needs_review():
    orders = [_order("SOL", OrderSide.BUY, 1.0)]
    d = evaluate_mandate(orders, {"SOL": 100.0}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("instrument_not_allowed" in r for r in d.reasons)


def test_single_order_notional_exceeded_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.1)]           # 0.1 * 60000 = $6000 > single cap $1000
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("single_order_notional_exceeded" in r for r in d.reasons)


def test_gross_notional_exceeded_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.015, "c1"), _order("ETH", OrderSide.SELL, 0.4, "c2")]
    # BTC $900 + ETH $1200 = $2100 gross > max_gross_notional $2000; both individually under single cap
    d = evaluate_mandate(orders, {"BTC": 60000.0, "ETH": 3000.0}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "gross_notional_exceeded" in d.reasons


def test_net_notional_exceeded_needs_review():
    m = Mandate(mandate_id="m2", venue="binance_testnet", exchange="binance", allowed_instruments=frozenset({"BTC", "ETH"}),
               max_single_order_notional=2000.0, max_gross_notional=5000.0, max_net_notional=500.0)
    orders = [_order("BTC", OrderSide.BUY, 0.01, "c1"), _order("ETH", OrderSide.BUY, 0.1, "c2")]
    # 同向:BTC $600 + ETH $300 = net $900 > max_net $500(gross $900 < max_gross $5000,不触发 gross)
    d = evaluate_mandate(orders, {"BTC": 60000.0, "ETH": 3000.0}, m, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "net_notional_exceeded" in d.reasons


def test_per_instrument_cap_accounts_for_existing_position():
    # 已有 BTC 仓位 0.01(=$600),再买 0.005(=$300)-> 累计 $900 > per-instrument cap $800
    orders = [_order("BTC", OrderSide.BUY, 0.005)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="binance", now=1,
                         current_positions={"BTC": 0.01})
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("per_instrument_cap_exceeded" in r for r in d.reasons)


def test_per_instrument_cap_ok_with_existing_position_when_under_cap():
    orders = [_order("BTC", OrderSide.BUY, 0.001)]        # existing 0.005*60000=$300 + $60 = $360 < $800
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="binance", now=1,
                         current_positions={"BTC": 0.005})
    assert d.tier == AUTO


def test_missing_price_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.01)]
    d = evaluate_mandate(orders, {}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("no_price" in r for r in d.reasons)


def test_evaluation_error_defaults_to_review_not_auto():
    # 批次非空但元素不是合法 LiveOrderRequest(无 .instrument_id)-> 内部 AttributeError
    # -> fail-closed 到 NEEDS_HUMAN_REVIEW,绝不 AUTO(注意:None/[] 都是"空批次"->AUTO 的合法输入,
    # 不是异常输入——这里用非空但畸形的元素才能真正触发求值异常)
    d = evaluate_mandate(["not-a-valid-order"], {}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("evaluation_error" in r for r in d.reasons)


def test_empty_allowed_instruments_rejects_everything():
    m = Mandate(mandate_id="m3", venue="binance_testnet", exchange="binance", allowed_instruments=frozenset(),
               max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9)
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, m, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW


def test_exchange_mismatch_needs_review():
    # 两家交易所的 testnet 都叫 "testnet"——mandate 必须显式签署交易所域,给 Binance 签的
    # mandate 绝不能被 Bybit 误认(2026-07-05)
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="bybit",
                         now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "exchange_mismatch" in d.reasons


def test_exchange_unset_on_mandate_needs_review():
    m = Mandate(mandate_id="m4", venue="binance_testnet", allowed_instruments=frozenset({"BTC"}),
               max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9)
    assert m.exchange == ""                                    # 未签署交易所域
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, m, venue="binance_testnet", exchange="binance",
                         now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "exchange_unset" in d.reasons
