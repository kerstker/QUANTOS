"""BinanceFuturesAdapter(live/exchange/binance_futures.py):签名 REST 客户端。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.3。

全部注入假 http transport——零真网络。诚实标注:响应 JSON 形状依公开文档规格假设,
未对真测试网响应验证(见 spec §0);这里只验证"给定假设的响应形状,解析/签名逻辑正确"。
"""
import hashlib
import hmac
import json
import urllib.parse

import pytest

from live.contracts import LiveOrderRequest
from live.exchange.binance_futures import BinanceFuturesAdapter
from quantos.contracts.config import SafetyConfig, SafetyViolation
from quantos.contracts.trading import OrderSide

_KEY, _SECRET = "test-api-key", "test-api-secret"


class _FakeTransport:
    """记录每次调用;按 path 返回预置的 (status, json_body)。"""

    def __init__(self, responses):
        self.responses = responses          # {path: (status, dict)}
        self.calls = []

    def __call__(self, method, url, *, body=None, headers=None):
        path = url.split("?")[0].replace(BinanceFuturesAdapter.TESTNET_BASE, "")
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        status, payload = self.responses.get(path, (404, {"msg": "not found"}))
        return status, json.dumps(payload).encode()


def _client(responses=None, **kw):
    t = _FakeTransport(responses or {})
    c = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=t, clock=lambda: 1_700_000_000.0, **kw)
    return c, t


def _verify_signature(qs_with_sig: str) -> bool:
    qs, _, sig = qs_with_sig.rpartition("&signature=")
    expected = hmac.new(_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return sig == expected


def test_mainnet_construction_refused_at_l2_default():
    with pytest.raises(SafetyViolation):
        BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.MAINNET_BASE)


def test_mainnet_construction_refused_even_with_other_flags_partially_set():
    # armed 需要全部四个 live 前置皆真;单独打开一两个仍必须被拒(与 SafetyConfig.live_trading_armed 语义一致)
    cfg = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True)
    assert cfg.live_trading_armed is False
    with pytest.raises(SafetyViolation):
        BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.MAINNET_BASE, safety_config=cfg)


def test_unknown_base_url_rejected():
    with pytest.raises(ValueError):
        BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET, base_url="https://evil.example.com")


def test_testnet_construction_succeeds_and_sets_venue_mode():
    c, _ = _client()
    assert c.venue_mode == "testnet"


def test_exchange_info_is_unsigned_public_call():
    filters_resp = {
        "symbols": [{
            "symbol": "BTCUSDT",
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            ],
        }],
    }
    c, t = _client({"/fapi/v1/exchangeInfo": (200, filters_resp)})
    filters = c.exchange_filters()
    assert "signature" not in t.calls[0]["url"]              # 公开端点不签名
    assert filters["BTC"].step_size == pytest.approx(0.001)
    assert filters["BTC"].min_qty == pytest.approx(0.001)
    assert filters["BTC"].min_notional == pytest.approx(5.0)
    assert filters["BTC"].tick_size == pytest.approx(0.10)


def test_exchange_filters_missing_filter_field_is_skipped_not_crashed():
    # 诚实 fail-soft:某 symbol 缺 MIN_NOTIONAL(不同 symbol 过滤器集合可能不同)→ 跳过该 symbol,不整体崩溃
    resp = {"symbols": [{"symbol": "WEIRDUSDT", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1", "maxQty": "9999"},
    ]}]}
    c, _ = _client({"/fapi/v1/exchangeInfo": (200, resp)})
    filters = c.exchange_filters()
    assert filters == {}                                     # 缺 MIN_NOTIONAL,诚实丢弃不硬凑


def test_account_call_is_signed_with_timestamp_and_api_key_header():
    c, t = _client({"/fapi/v2/account": (200, {"totalWalletBalance": "1000.0",
                                               "availableBalance": "900.0", "positions": []})})
    raw = c.account()
    assert raw["totalWalletBalance"] == "1000.0"
    call = t.calls[0]
    assert call["method"] == "GET"
    qs = call["url"].split("?", 1)[1]
    assert "timestamp=1700000000000" in qs
    assert _verify_signature(qs)
    assert call["headers"]["X-MBX-APIKEY"] == _KEY


def test_positions_normalizes_raw_account_into_venue_positions():
    resp = {"totalWalletBalance": "1000.0", "availableBalance": "900.0", "positions": [
        {"symbol": "BTCUSDT", "positionAmt": "0.010", "entryPrice": "50000.0"},
        {"symbol": "ETHUSDT", "positionAmt": "-1.500", "entryPrice": "3000.0"},
        {"symbol": "SOLUSDT", "positionAmt": "0.000", "entryPrice": "0.0"},   # 空仓,应被过滤
    ]}
    c, _ = _client({"/fapi/v2/account": (200, resp)})
    pos = c.positions()
    assert set(pos) == {"BTC", "ETH"}                         # 零仓不出现(诚实,不塞噪声)
    assert pos["BTC"].quantity == pytest.approx(0.010)
    assert pos["ETH"].quantity == pytest.approx(-1.500)
    assert pos["ETH"].entry_price == pytest.approx(3000.0)


def test_place_order_signs_body_and_parses_result():
    order_resp = {"orderId": 999, "clientOrderId": "cid-abc", "symbol": "BTCUSDT",
                  "status": "NEW", "executedQty": "0.0", "avgPrice": "0.0"}
    c, t = _client({"/fapi/v1/order": (200, order_resp)})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="cid-abc")
    result = c.place_order(order)
    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/fapi/v1/order")             # POST 参数走 body,URL 无 query
    body = call["body"].decode()
    assert _verify_signature(body)
    params = dict(urllib.parse.parse_qsl(body))
    assert params["symbol"] == "BTCUSDT" and params["side"] == "BUY" and params["type"] == "MARKET"
    assert params["quantity"] == "0.015" and params["newClientOrderId"] == "cid-abc"
    assert result.venue_order_id == "999" and result.client_order_id == "cid-abc"
    assert result.status == "NEW" and result.instrument_id == "BTC"


def test_place_order_sell_side_and_whole_number_quantity_formats_without_decimal():
    c, t = _client({"/fapi/v1/order": (200, {"orderId": "1", "clientOrderId": "c",
                                             "symbol": "ETHUSDT", "status": "NEW",
                                             "executedQty": "0", "avgPrice": "0"})})
    order = LiveOrderRequest(instrument_id="ETH", side=OrderSide.SELL, quantity=3.0,
                             client_order_id="c")
    c.place_order(order)
    params = dict(urllib.parse.parse_qsl(t.calls[0]["body"].decode()))
    assert params["side"] == "SELL" and params["quantity"] == "3"       # 整数量不带小数点


def test_place_order_non_2xx_raises():
    c, _ = _client({"/fapi/v1/order": (400, {"code": -1013, "msg": "filter failure"})})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=1.0,
                             client_order_id="c")
    with pytest.raises(RuntimeError):
        c.place_order(order)


def test_format_qty_no_scientific_notation_and_no_trailing_zeros():
    from live.exchange.binance_futures import _format_qty
    assert _format_qty(0.015) == "0.015"
    assert _format_qty(3.0) == "3"
    assert _format_qty(0.00000001) == "0.00000001"
    assert _format_qty(1234.5) == "1234.5"


def test_get_order_found():
    resp = {"orderId": "42", "clientOrderId": "c1", "symbol": "BTCUSDT", "status": "FILLED",
           "executedQty": "1.0", "avgPrice": "50000.0"}
    c, t = _client({"/fapi/v1/order": (200, resp)})
    r = c.get_order("BTC", "c1")
    assert r.status == "FILLED" and r.filled_qty == pytest.approx(1.0)
    assert t.calls[0]["method"] == "GET"


def test_get_order_not_found_returns_none():
    c, _ = _client({"/fapi/v1/order": (400, {"code": -2013, "msg": "Order does not exist."})})
    assert c.get_order("BTC", "ghost") is None


def test_get_order_other_error_raises():
    c, _ = _client({"/fapi/v1/order": (500, {"msg": "internal error"})})
    with pytest.raises(RuntimeError):
        c.get_order("BTC", "c1")


def test_no_withdraw_or_transfer_methods_exist():
    c, _ = _client()
    for forbidden in ("withdraw", "transfer", "create_withdrawal"):
        assert not hasattr(c, forbidden)
