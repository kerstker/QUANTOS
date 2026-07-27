"""BinanceFuturesAdapter(live/exchange/binance_futures.py):签名 REST 客户端。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.3。

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
    """记录每次调用;按 path 返回预置的 (status, json_body),或一个序列(逐次消费,超出重复最后一个
    ——用于测试限流重试后成功的场景)。"""

    def __init__(self, responses):
        self.responses = responses          # {path: (status, dict) | list[(status, dict)]}
        self.calls = []
        self._path_counts = {}

    def __call__(self, method, url, *, body=None, headers=None):
        path = url.split("?")[0].replace(BinanceFuturesAdapter.TESTNET_BASE, "")
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        entry = self.responses.get(path, (404, {"msg": "not found"}))
        if isinstance(entry, list):
            idx = min(self._path_counts.get(path, 0), len(entry) - 1)
            self._path_counts[path] = self._path_counts.get(path, 0) + 1
            status, payload = entry[idx]
        else:
            status, payload = entry
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


def test_positions_nets_hedge_mode_long_and_short_legs_instead_of_overwriting():
    # 2026-07-04 对抗式审查发现 #13:Hedge Mode 下同一 symbol 有 LONG/SHORT 两条记录,
    # 早期实现用 dict 覆盖写入会静默丢弃一侧、把净敞口算错而不报错。
    resp = {"positions": [
        {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.010", "entryPrice": "50000.0"},
        {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.020", "entryPrice": "51000.0"},
    ]}
    c, _ = _client({"/fapi/v2/account": (200, resp)})
    pos = c.positions()
    assert pos["BTC"].quantity == pytest.approx(0.010 - 0.020)   # 净敞口 = 两腿相加,不是覆盖


def test_positions_hedge_mode_legs_that_net_to_zero_are_dropped():
    resp = {"positions": [
        {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.010", "entryPrice": "50000.0"},
        {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.010", "entryPrice": "50000.0"},
    ]}
    c, _ = _client({"/fapi/v2/account": (200, resp)})
    pos = c.positions()
    assert "BTC" not in pos                                       # 完全对冲 = 净敞口 0,同单腿平仓一致


def test_positions_entry_price_from_dominant_leg_by_abs_quantity():
    resp = {"positions": [
        {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.001", "entryPrice": "40000.0"},
        {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.020", "entryPrice": "51000.0"},
    ]}
    c, _ = _client({"/fapi/v2/account": (200, resp)})
    pos = c.positions()
    assert pos["BTC"].entry_price == pytest.approx(51000.0)        # 取绝对量更大那条腿的入场价


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


def test_non_json_response_body_raises_clear_runtime_error_not_json_decode_error():
    # 2026-07-04 对抗式审查发现 #14:反向代理故障/限流页面可能返回非 JSON body。
    class _HtmlTransport:
        def __call__(self, method, url, *, body=None, headers=None):
            return 200, b"<html><body>Service Unavailable</body></html>"

    c = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=_HtmlTransport(), clock=lambda: 1_700_000_000.0)
    with pytest.raises(RuntimeError):     # 不是未处理的 json.JSONDecodeError
        c.account()


def test_no_withdraw_or_transfer_methods_exist():
    c, _ = _client()
    for forbidden in ("withdraw", "transfer", "create_withdrawal"):
        assert not hasattr(c, forbidden)


# --- 2026-07-04 运维硬化:限流退避 + 时钟偏移校正 ---

def test_rate_limited_429_retries_with_backoff_then_succeeds():
    sleeps = []
    resp = {"/fapi/v2/account": [
        (429, {"code": -1003, "msg": "Too many requests"}),
        (429, {"code": -1003, "msg": "Too many requests"}),
        (200, {"totalWalletBalance": "1000.0", "availableBalance": "900.0", "positions": []}),
    ]}
    c, t = _client(resp, sleep=sleeps.append, max_retries=5, retry_base_delay=1.0)
    raw = c.account()
    assert raw["totalWalletBalance"] == "1000.0"
    assert len(t.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limited_418_ip_ban_also_triggers_backoff():
    resp = {"/fapi/v2/account": [
        (418, {"code": -1003, "msg": "IP banned"}),
        (200, {"totalWalletBalance": "1000.0", "availableBalance": "900.0", "positions": []}),
    ]}
    c, t = _client(resp, sleep=lambda s: None)
    c.account()
    assert len(t.calls) == 2


def test_rate_limited_exhausted_retries_raises_rate_limited_error():
    from live.exchange.retry import RateLimitedError
    resp = {"/fapi/v2/account": (429, {"code": -1003, "msg": "Too many requests"})}
    c, _ = _client(resp, sleep=lambda s: None, max_retries=2)
    with pytest.raises(RateLimitedError):
        c.account()


def test_non_rate_limit_error_does_not_retry():
    resp = {"/fapi/v2/account": (400, {"code": -1102, "msg": "bad param"})}
    c, t = _client(resp, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.account()
    assert len(t.calls) == 1                                   # 非限流失败不重试


def test_sync_clock_applies_offset_to_subsequent_signed_requests():
    c, t = _client({
        "/fapi/v1/time": (200, {"serverTime": 1_700_000_010_000}),   # 服务器比本机快 10s
        "/fapi/v2/account": (200, {"totalWalletBalance": "1", "availableBalance": "1", "positions": []}),
    })
    offset = c.sync_clock()
    assert offset == 10_000
    c.account()
    qs = t.calls[-1]["url"].split("?", 1)[1]
    assert "timestamp=1700000010000" in qs                      # 本机时间 + 偏移


def test_sync_clock_failure_is_fail_soft_keeps_prior_offset():
    c, _ = _client({"/fapi/v1/time": (500, {"msg": "unavailable"})})
    offset = c.sync_clock()
    assert offset == 0                                          # 失败保持默认 0,不抛异常打断流程


# --- cancel_order(OMS 补全:超时僵尸单处置的前提) ---

def test_cancel_order_sends_signed_delete_and_parses_result():
    c, t = _client({"/fapi/v1/order": (200, {"orderId": 77, "clientOrderId": "c1",
                                             "status": "CANCELED", "executedQty": "0.001",
                                             "avgPrice": "60000.0"})})
    r = c.cancel_order("BTC", "c1")
    call = t.calls[0]
    assert call["method"] == "DELETE"
    qs = call["url"].split("?", 1)[1]
    assert _verify_signature(qs)
    params = dict(urllib.parse.parse_qsl(qs))
    assert params["symbol"] == "BTCUSDT" and params["origClientOrderId"] == "c1"
    # 撤单响应可能带着已发生的部分成交——按交易所报的真实 executedQty 透传,绝不假设撤单=零成交
    assert r.status == "CANCELED" and r.filled_qty == pytest.approx(0.001)
    assert r.venue_order_id == "77"


def test_cancel_order_unknown_or_already_terminal_returns_none():
    # -2011 CANCEL_REJECTED("Unknown order sent.")= 订单不存在或已终态无法撤——语义化 None,
    # 调用方必须回查 get_order 拿权威状态(撤单与成交赛跑的正常路径),不是异常
    c, _ = _client({"/fapi/v1/order": (400, {"code": -2011, "msg": "Unknown order sent."})})
    assert c.cancel_order("BTC", "ghost") is None


def test_cancel_order_order_does_not_exist_code_also_returns_none():
    c, _ = _client({"/fapi/v1/order": (400, {"code": -2013, "msg": "Order does not exist."})})
    assert c.cancel_order("BTC", "ghost") is None


def test_cancel_order_other_error_raises():
    c, _ = _client({"/fapi/v1/order": (500, {"code": -1000, "msg": "internal error"})})
    with pytest.raises(RuntimeError):
        c.cancel_order("BTC", "c1")


# --- reduce_only(紧急减仓通道的交易所侧第二重保证) ---

def test_place_order_reduce_only_passes_flag_to_exchange():
    from quantos.contracts.trading import OrderSide as _OS
    c, t = _client({"/fapi/v1/order": (200, {"orderId": 1, "clientOrderId": "c1", "status": "NEW",
                                             "executedQty": "0", "avgPrice": "0"})})
    c.place_order(LiveOrderRequest(instrument_id="BTC", side=_OS.SELL, quantity=0.01,
                                   client_order_id="c1", reduce_only=True))
    body = t.calls[0]["body"].decode()
    assert "reduceOnly=true" in body


def test_place_order_default_omits_reduce_only_param():
    # 普通订单绝不带 reduceOnly——保持与已真实联调验证过的请求形状完全一致
    from quantos.contracts.trading import OrderSide as _OS
    c, t = _client({"/fapi/v1/order": (200, {"orderId": 1, "clientOrderId": "c1", "status": "NEW",
                                             "executedQty": "0", "avgPrice": "0"})})
    c.place_order(LiveOrderRequest(instrument_id="BTC", side=_OS.BUY, quantity=0.01,
                                   client_order_id="c1"))
    assert "reduceOnly" not in t.calls[0]["body"].decode()


# --- LIMIT 扩展(2026-07-06 战线②):默认 MARKET 请求形状字节不变 + LIMIT 参数/timeInForce ---

def test_binance_market_request_shape_byte_identical():
    # 回归锁:MARKET 单的 params 必须与改动前完全同键同值(不含 price/timeInForce)
    c, t = _client({"/fapi/v1/order": (200, {"orderId": 999, "clientOrderId": "cid-abc",
                                             "symbol": "BTCUSDT", "status": "NEW",
                                             "executedQty": "0.0", "avgPrice": "0.0"})})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="cid-abc")
    c.place_order(order)
    params = dict(urllib.parse.parse_qsl(t.calls[0]["body"].decode()))
    business_keys = {"symbol", "side", "type", "quantity", "newClientOrderId"}
    assert {k: v for k, v in params.items() if k in business_keys} == {
        "symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
        "quantity": "0.015", "newClientOrderId": "cid-abc"}
    assert "price" not in params and "timeInForce" not in params and "reduceOnly" not in params


def test_binance_limit_params():
    order_resp = {"orderId": 1, "clientOrderId": "c1", "status": "NEW",
                 "executedQty": "0", "avgPrice": "0"}
    c, t = _client({"/fapi/v1/order": (200, order_resp)})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="c1", order_type="LIMIT", limit_price=60123.4)
    c.place_order(order)
    params = dict(urllib.parse.parse_qsl(t.calls[0]["body"].decode()))
    assert params["type"] == "LIMIT"
    assert params["price"] == "60123.4"              # 十进制字符串,无科学计数法
    assert params["timeInForce"] == "GTC"
    assert params["newClientOrderId"] == "c1"


def test_binance_limit_post_only_uses_gtx_time_in_force():
    order_resp = {"orderId": 1, "clientOrderId": "c1", "status": "NEW",
                 "executedQty": "0", "avgPrice": "0"}
    c, t = _client({"/fapi/v1/order": (200, order_resp)})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="c1", order_type="LIMIT", limit_price=60123.4,
                             post_only=True)
    c.place_order(order)
    params = dict(urllib.parse.parse_qsl(t.calls[0]["body"].decode()))
    assert params["timeInForce"] == "GTX"


def test_binance_book_ticker_parses_and_fails_soft():
    resp = {"symbol": "BTCUSDT", "bidPrice": "59990.10", "askPrice": "60010.20",
            "time": 1700000000000}
    c, t = _client({"/fapi/v1/ticker/bookTicker": (200, resp)})
    book = c.book_ticker("BTC")
    assert book == {"bid": pytest.approx(59990.10), "ask": pytest.approx(60010.20),
                    "as_of_ms": 1700000000000}
    assert "signature" not in t.calls[0]["url"]           # 公开端点,不签名


def test_binance_book_ticker_without_source_time_remains_usable_for_limit_anchor():
    resp = {"symbol": "BTCUSDT", "bidPrice": "59990.10", "askPrice": "60010.20"}
    c, _ = _client({"/fapi/v1/ticker/bookTicker": (200, resp)})
    assert c.book_ticker("BTC") == {
        "bid": pytest.approx(59990.10),
        "ask": pytest.approx(60010.20),
    }


def test_binance_book_ticker_non_2xx_fails_soft_returns_none():
    c, _ = _client({"/fapi/v1/ticker/bookTicker": (500, {"msg": "internal error"})})
    assert c.book_ticker("BTC") is None


def test_binance_book_ticker_missing_fields_fails_soft_returns_none():
    c, _ = _client({"/fapi/v1/ticker/bookTicker": (200, {"symbol": "BTCUSDT"})})
    assert c.book_ticker("BTC") is None


def test_binance_book_ticker_non_json_fails_soft_returns_none():
    class _HtmlTransport:
        def __call__(self, method, url, *, body=None, headers=None):
            return 200, b"<html>Service Unavailable</html>"

    c = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=_HtmlTransport(), clock=lambda: 1_700_000_000.0)
    assert c.book_ticker("BTC") is None


# --- depth(SHADOW 订单簿深度,2026-07-27 Phase 7 S1) ---

_DEPTH_RESP = {"lastUpdateId": 1027024,
               "E": 1_700_000_000_123, "T": 1_700_000_000_120,
               "bids": [["49999.5", "0.10"], ["49999.0", "0.20"]],
               "asks": [["50000.5", "0.15"], ["50001.0", "0.30"]]}


def test_depth_parses_levels_and_as_of_prefers_event_time():
    c, _ = _client({"/fapi/v1/depth": (200, _DEPTH_RESP)})
    snap = c.depth("BTC")
    assert snap is not None
    assert snap.source == "binance"
    assert snap.instrument_id == "BTC"
    assert [(l.price, l.size) for l in snap.bids] == [(49999.5, 0.10), (49999.0, 0.20)]
    assert [(l.price, l.size) for l in snap.asks] == [(50000.5, 0.15), (50001.0, 0.30)]
    assert snap.as_of_ms == 1_700_000_000_123         # E(event time)优先


def test_depth_as_of_falls_back_to_transaction_time_then_none():
    resp = dict(_DEPTH_RESP)
    del resp["E"]
    c, _ = _client({"/fapi/v1/depth": (200, resp)})
    assert c.depth("BTC").as_of_ms == 1_700_000_000_120   # 缺 E 回退 T
    resp2 = dict(resp)
    del resp2["T"]
    c2, _ = _client({"/fapi/v1/depth": (200, resp2)})
    assert c2.depth("BTC").as_of_ms is None               # 都没有 -> 诚实 None,绝不自造


def test_depth_is_unsigned_get_with_symbol_and_limit():
    c, t = _client({"/fapi/v1/depth": (200, _DEPTH_RESP)})
    c.depth("ETH", limit=20)
    url = t.calls[0]["url"]
    assert "signature" not in url
    params = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
    assert params == {"symbol": "ETHUSDT", "limit": "20"}


def test_depth_limit_clamped_up_to_nearest_allowed():
    for requested, expected in ((1, "5"), (75, "100"), (101, "500"), (99999, "1000")):
        c, t = _client({"/fapi/v1/depth": (200, _DEPTH_RESP)})
        c.depth("BTC", limit=requested)
        params = dict(urllib.parse.parse_qsl(t.calls[0]["url"].split("?", 1)[1]))
        assert params["limit"] == expected


def test_depth_fail_soft_on_http_error_and_non_json():
    c, _ = _client({"/fapi/v1/depth": (500, {"msg": "boom"})})
    assert c.depth("BTC") is None

    def transport(method, url, *, body=None, headers=None):
        return 200, b"<html>not json</html>"
    c2 = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                               base_url=BinanceFuturesAdapter.TESTNET_BASE,
                               http_request=transport, clock=lambda: 1_700_000_000.0)
    assert c2.depth("BTC") is None


def test_depth_fail_soft_on_malformed_empty_or_non_finite():
    for payload in ({"bids": [], "asks": [["1", "1"]]},
                    {"bids": [["1", "1"]], "asks": []},
                    {"bids": "nope", "asks": [["1", "1"]]},
                    {"asks": [["1", "1"]]},
                    {"bids": [["NaN", "1"]], "asks": [["1", "1"]]},
                    {"bids": [["1", "0"]], "asks": [["1", "1"]]}):
        c, _ = _client({"/fapi/v1/depth": (200, payload)})
        assert c.depth("BTC") is None
