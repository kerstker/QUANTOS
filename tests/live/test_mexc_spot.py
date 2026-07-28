"""MexcSpotAdapter(live/exchange/mexc_spot.py):MEXC 现货签名 REST 客户端单测。

全部注入假 http transport——零真网络。诚实标注:响应 JSON 形状依 MEXC 公开 API v3 文档
规格假设,未对真 mainnet 响应验证(见适配器模块 docstring);这里只验证"给定假设的响应
形状,解析/签名/现货语义逻辑正确"。MEXC 无现货 testnet,唯一合法 base_url = MAINNET_BASE,
因此可构造的测试客户端一律用显式全开的 armed SafetyConfig(等价 Phase 3A 人工 gate 已过的
世界);默认/半开 SafetyConfig 一律构造拒绝。
"""
import hashlib
import hmac
import json
import urllib.parse

import pytest

from live.contracts import LiveOrderRequest, VenuePosition
from live.exchange.mexc_spot import MexcSpotAdapter, _format_qty
from live.exchange.retry import RateLimitedError
from quantos.contracts.config import SafetyConfig, SafetyViolation
from quantos.contracts.trading import OrderSide

_KEY, _SECRET = "test-mexc-key", "test-mexc-secret"
_ARMED = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True,
                      allow_order_submission=True, allow_ai_direct_trading=False)
_CLOCK = lambda: 1_700_000_000.0  # noqa: E731


class _FakeTransport:
    """记录每次调用;按 path 返回预置的 (status, json_body),或一个序列(逐次消费,超出重复
    最后一个——用于测试限流重试后成功的场景)。"""

    def __init__(self, responses):
        self.responses = responses          # {path: (status, dict) | list[(status, dict)]}
        self.calls = []
        self._path_counts = {}

    def __call__(self, method, url, *, body=None, headers=None):
        path = url.split("?")[0].replace(MexcSpotAdapter.MAINNET_BASE, "")
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
    c = MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url=MexcSpotAdapter.MAINNET_BASE,
                        http_request=t, clock=_CLOCK, safety_config=_ARMED,
                        sleep=lambda _s: None, **kw)
    return c, t


def _verify_signature(qs_with_sig: str) -> bool:
    qs, _, sig = qs_with_sig.rpartition("&signature=")
    expected = hmac.new(_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return sig == expected


def _account_payload(balances=None):
    return {"balances": balances or [
        {"asset": "BTC", "free": "0.015", "locked": "0.001"},
        {"asset": "ETH", "free": "0", "locked": "0"},
        {"asset": "USDT", "free": "1234.5", "locked": "0"},
    ]}


def _order_payload(**over):
    d = {"symbol": "BTCUSDT", "orderId": 123456789, "clientOrderId": "cid-1",
         "status": "FILLED", "side": "BUY", "type": "MARKET",
         "price": "0", "origQty": "0.015", "executedQty": "0.015",
         "cummulativeQuoteQty": "750.0", "transactTime": 1_700_000_000_000}
    d.update(over)
    return d


class _FakeSocket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)

    def recv(self):
        return next(self.frames)

    def close(self):
        self.closed = True


def test_listen_key_lifecycle_is_api_key_only_and_never_exposes_secret():
    client, transport = _client(
        {"/api/v3/userDataStream": (200, {"listenKey": "listen-test-1"})}
    )
    key = client.create_listen_key()
    client.keepalive_listen_key(key)
    client.close_listen_key(key)

    assert [call["method"] for call in transport.calls] == ["POST", "PUT", "DELETE"]
    assert all(call["headers"]["X-MEXC-APIKEY"] == _KEY for call in transport.calls)
    assert all(_SECRET not in call["url"] for call in transport.calls)
    assert "listenKey=listen-test-1" in transport.calls[1]["url"]


def test_user_stream_reconnects_only_after_rest_backfill_and_closes_key():
    client, transport = _client(
        {"/api/v3/userDataStream": (200, {"listenKey": "listen-test-2"})}
    )
    first = _FakeSocket([None])
    second = _FakeSocket([b"event"])
    sockets = iter([first, second])
    actions = []

    reconnects = client.user_stream(
        ws_connect=lambda url: (actions.append(("connect", url)), next(sockets))[1],
        decode=lambda frame: {"channel": "orders", "raw": frame.decode()},
        on_event=lambda event: (actions.append(("event", event["channel"])), False)[1],
        rest_backfill=lambda adapter: actions.append(("backfill", adapter.venue_name)),
        max_reconnects=1,
    )

    assert reconnects == 1
    assert [action[0] for action in actions] == [
        "connect", "backfill", "connect", "event"
    ]
    assert first.closed is True and second.closed is True
    assert "spot@private.orders.v3.api.pb" in first.sent[0]
    assert [call["method"] for call in transport.calls] == ["POST", "PUT", "DELETE"]


def test_user_stream_fails_closed_after_reconnect_budget():
    client, _ = _client(
        {"/api/v3/userDataStream": (200, {"listenKey": "listen-test-3"})}
    )
    backfills = []
    with pytest.raises(ConnectionError, match="disconnected"):
        client.user_stream(
            ws_connect=lambda _url: _FakeSocket([None]),
            decode=lambda _frame: {},
            on_event=lambda _event: True,
            rest_backfill=lambda _adapter: backfills.append("backfill"),
            max_reconnects=0,
        )
    assert backfills == ["backfill"]


# --- 构造安全 ---

def test_mainnet_construction_refused_at_l2_default():
    with pytest.raises(SafetyViolation):
        MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url=MexcSpotAdapter.MAINNET_BASE)


def test_mainnet_construction_refused_even_with_flags_partially_set():
    cfg = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True)
    assert cfg.live_trading_armed is False
    with pytest.raises(SafetyViolation):
        MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url=MexcSpotAdapter.MAINNET_BASE, safety_config=cfg)


def test_ai_direct_trading_refused_even_if_other_flags_set():
    cfg = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True,
                       allow_order_submission=True, allow_ai_direct_trading=True)
    assert cfg.live_trading_armed is False
    with pytest.raises(SafetyViolation):
        MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url=MexcSpotAdapter.MAINNET_BASE, safety_config=cfg)


def test_unknown_base_url_rejected():
    with pytest.raises(ValueError):
        MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET, base_url="https://evil.example.com")


def test_testnet_like_base_url_rejected_mexc_has_no_spot_testnet():
    # MEXC 无现货 testnet——任何"看起来像测试网"的 base_url 都必须 ValueError,不被误放行
    with pytest.raises(ValueError):
        MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url="https://testnet.mexc.com", safety_config=_ARMED)


def test_armed_construction_succeeds_and_sets_venue_mode_and_name():
    c, _ = _client()
    assert c.venue_mode == "mainnet"
    assert c.venue_name == "mexc"


# --- 时钟同步 ---

def test_sync_clock_updates_offset_from_server_time():
    c, _ = _client({"/api/v3/time": (200, {"serverTime": 1_700_000_005_000})})
    offset = c.sync_clock()
    assert offset == 5_000


def test_sync_clock_fail_soft_keeps_old_offset_on_failure():
    c, _ = _client({"/api/v3/time": (500, {"msg": "boom"})})
    c._clock_offset_ms = 123
    assert c.sync_clock() == 123                          # 失败保留旧偏移,不打断流程


def test_sync_clock_offset_applies_to_signed_timestamp():
    c, t = _client({"/api/v3/time": (200, {"serverTime": 1_700_000_005_000}),
                    "/api/v3/account": (200, _account_payload())})
    c.sync_clock()
    c.account()
    qs = t.calls[-1]["url"].split("?", 1)[1]
    ts = int(dict(urllib.parse.parse_qsl(qs))["timestamp"])
    assert ts == 1_700_000_005_000


# --- 签名 ---

def test_signed_get_uses_mexc_apikey_header_and_valid_hmac():
    c, t = _client({"/api/v3/account": (200, _account_payload())})
    c.account()
    call = t.calls[0]
    assert call["method"] == "GET"
    assert call["headers"]["X-MEXC-APIKEY"] == _KEY
    qs_with_sig = call["url"].split("?", 1)[1]
    assert _verify_signature(qs_with_sig)
    params = dict(urllib.parse.parse_qsl(qs_with_sig))
    assert params["timestamp"] == "1700000000000"
    assert params["recvWindow"] == "5000"


def test_signed_post_body_is_urlencoded_and_valid_hmac():
    c, t = _client({"/api/v3/order": (200, _order_payload())})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="cid-1")
    c.place_order(order)
    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    body = call["body"].decode()
    assert _verify_signature(body)


def test_unsigned_public_call_carries_no_signature():
    c, t = _client({"/api/v3/exchangeInfo": (200, {"symbols": []})})
    c.exchange_info()
    assert "signature" not in t.calls[0]["url"]


def test_retry_re_signs_each_attempt_timestamp_changes():
    # 时钟前进时,429 重试的每次请求 timestamp 都重新算(不复用旧签名串)
    clock_times = [1_700_000_000.0, 1_700_000_002.0, 1_700_000_004.0]
    seen_ts = []

    def transport(method, url, *, body=None, headers=None):
        seen_ts.append(int(dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))["timestamp"]))
        status = 429 if len(seen_ts) < 3 else 200
        return status, json.dumps({} if status == 429 else _account_payload()).encode()

    c = MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url=MexcSpotAdapter.MAINNET_BASE, http_request=transport,
                        clock=lambda: clock_times[min(len(seen_ts), 2)], safety_config=_ARMED,
                        sleep=lambda _s: None)
    c.account()
    assert seen_ts == [1_700_000_000_000, 1_700_000_002_000, 1_700_000_004_000]


# --- 限流重试 ---

def test_429_is_retried_then_succeeds():
    c, t = _client({"/api/v3/account": [(429, {"msg": "too many requests"}),
                                        (200, _account_payload())]})
    raw = c.account()
    assert raw["balances"][0]["asset"] == "BTC"
    assert len(t.calls) == 2


def test_429_exhausted_raises_rate_limited_error():
    c, _ = _client({"/api/v3/account": (429, {"msg": "too many requests"})}, max_retries=2)
    with pytest.raises(RateLimitedError):
        c.account()


# --- exchange_filters / book_ticker ---

_EXCHANGE_INFO = {"symbols": [
    {"symbol": "BTCUSDT", "baseAsset": "BTC", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001", "maxQty": "9000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
    ]},
    {"symbol": "ETHUSDT", "baseAsset": "ETH", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"},
        # 缺 MIN_NOTIONAL -> 诚实丢弃
    ]},
]}


def test_exchange_filters_parses_lot_size_min_notional_and_tick():
    c, _ = _client({"/api/v3/exchangeInfo": (200, _EXCHANGE_INFO)})
    filters = c.exchange_filters()
    assert set(filters) == {"BTC"}                          # ETH 缺 MIN_NOTIONAL,诚实丢弃
    f = filters["BTC"]
    assert f.step_size == pytest.approx(0.00001)
    assert f.min_qty == pytest.approx(0.00001)
    assert f.min_notional == pytest.approx(5.0)
    assert f.tick_size == pytest.approx(0.01)


def test_exchange_filters_uses_base_asset_not_symbol_suffix():
    c, _ = _client({"/api/v3/exchangeInfo": (200, _EXCHANGE_INFO)})
    assert c.exchange_filters()["BTC"].instrument_id == "BTC"


def test_book_ticker_returns_bid_ask():
    c, _ = _client({"/api/v3/ticker/bookTicker": (200, {
        "symbol": "BTCUSDT", "bidPrice": "49999.5", "bidQty": "0.1",
        "askPrice": "50000.5", "askQty": "0.2"})})
    book = c.book_ticker("BTC")
    assert book == {"bid": pytest.approx(49999.5), "ask": pytest.approx(50000.5)}


def test_book_ticker_fail_soft_on_http_error_and_bad_json():
    c, _ = _client({"/api/v3/ticker/bookTicker": (500, {"msg": "boom"})})
    assert c.book_ticker("BTC") is None
    c2, _ = _client({"/api/v3/ticker/bookTicker": (200, {"symbol": "BTCUSDT"})})
    assert c2.book_ticker("BTC") is None                    # 缺字段也 fail-soft None


# --- account / positions(现货语义) ---

def test_positions_maps_free_balances_to_long_only_positions():
    c, _ = _client({"/api/v3/account": (200, _account_payload())})
    pos = c.positions()
    assert pos == {"BTC": VenuePosition(instrument_id="BTC", quantity=pytest.approx(0.015),
                                        entry_price=0.0)}
    assert "USDT" not in pos                                # 现金腿不是风险敞口标的
    assert "ETH" not in pos                                 # 零余额跳过


def test_positions_excludes_locked_only_balances():
    c, _ = _client({"/api/v3/account": (200, _account_payload(
        [{"asset": "LTC", "free": "0", "locked": "5"}]))})
    assert c.positions() == {}                              # 冻结余额不算可支配仓位


def test_positions_never_negative():
    c, _ = _client({"/api/v3/account": (200, _account_payload())})
    assert all(p.quantity > 0 for p in c.positions().values())  # 现货无做空


# --- place_order ---

def test_place_market_order_request_shape_and_result_parsing():
    c, t = _client({"/api/v3/order": (200, _order_payload())})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="cid-1")
    result = c.place_order(order)
    params = dict(urllib.parse.parse_qsl(t.calls[0]["body"].decode()))
    assert params["symbol"] == "BTCUSDT"
    assert params["side"] == "BUY"
    assert params["type"] == "MARKET"
    assert params["quantity"] == "0.015"
    assert params["newClientOrderId"] == "cid-1"
    assert "price" not in params and "timeInForce" not in params
    assert "reduceOnly" not in params                       # 现货无此参数
    assert result.venue_order_id == "123456789"
    assert result.status == "FILLED"
    assert result.filled_qty == pytest.approx(0.015)
    assert result.avg_price == pytest.approx(50000.0)       # 750.0 / 0.015 折算


def test_avg_price_derived_from_cummulative_quote_qty_not_faked():
    c, _ = _client({"/api/v3/order": (200, _order_payload(executedQty="0",
                                                          cummulativeQuoteQty="0"))})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="cid-1")
    result = c.place_order(order)
    assert result.avg_price == 0.0                          # 未成交 avg=0,绝不硬凑


def test_place_limit_order_adds_price_and_time_in_force():
    c, t = _client({"/api/v3/order": (200, _order_payload(type="LIMIT", status="NEW",
                                                          executedQty="0",
                                                          cummulativeQuoteQty="0"))})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="cid-2", order_type="LIMIT", limit_price=49000.0)
    c.place_order(order)
    params = dict(urllib.parse.parse_qsl(t.calls[0]["body"].decode()))
    assert params["type"] == "LIMIT"
    assert params["price"] == "49000"
    assert params["timeInForce"] == "GTC"


def test_place_order_non_2xx_raises_runtime_error():
    c, _ = _client({"/api/v3/order": (400, {"code": -1100, "msg": "Illegal characters"})})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                             client_order_id="cid-1")
    with pytest.raises(RuntimeError, match="place_order failed"):
        c.place_order(order)


# --- reduce_only 现货映射 ---

def test_reduce_only_buy_refused_locally():
    c, t = _client({"/api/v3/order": (200, _order_payload())})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                             client_order_id="cid-ro", reduce_only=True)
    with pytest.raises(RuntimeError, match="SELL-only"):
        c.place_order(order)
    assert t.calls == []                                    # 本地拒绝,绝不提交


def test_reduce_only_sell_within_free_balance_submits():
    c, t = _client({"/api/v3/account": (200, _account_payload()),
                    "/api/v3/order": (200, _order_payload(side="SELL"))})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.SELL, quantity=0.015,
                             client_order_id="cid-ro2", reduce_only=True)
    result = c.place_order(order)
    assert result.status == "FILLED"
    assert t.calls[-1]["method"] == "POST"


def test_reduce_only_sell_exceeding_free_balance_refused_locally():
    c, t = _client({"/api/v3/account": (200, _account_payload()),   # free = 0.015
                    "/api/v3/order": (200, _order_payload(side="SELL"))})
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.SELL, quantity=0.02,
                             client_order_id="cid-ro3", reduce_only=True)
    with pytest.raises(RuntimeError, match="exceeds free balance"):
        c.place_order(order)
    assert all(c_["method"] != "POST" for c_ in t.calls)    # 只查了余额,绝不提交


def test_reduce_only_fail_closed_when_balance_unavailable():
    c, t = _client({"/api/v3/account": (500, {"msg": "boom"})})   # 余额查不到 -> free=0
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.SELL, quantity=0.01,
                             client_order_id="cid-ro4", reduce_only=True)
    with pytest.raises(RuntimeError):
        c.place_order(order)                                # fail-closed:宁可误拒


# --- get_order / cancel_order ---

def test_get_order_returns_result():
    c, _ = _client({"/api/v3/order": (200, _order_payload(status="PARTIALLY_FILLED",
                                                          executedQty="0.005",
                                                          cummulativeQuoteQty="250.0"))})
    result = c.get_order("BTC", "cid-1")
    assert result is not None
    assert result.status == "PARTIALLY_FILLED"
    assert result.filled_qty == pytest.approx(0.005)
    assert result.avg_price == pytest.approx(50000.0)


def test_get_order_not_found_maps_to_none():
    c, _ = _client({"/api/v3/order": (400, {"code": -2013, "msg": "Order does not exist."})})
    assert c.get_order("BTC", "missing") is None


def test_get_order_other_error_raises():
    c, _ = _client({"/api/v3/order": (400, {"code": -1100, "msg": "Illegal characters"})})
    with pytest.raises(RuntimeError, match="get_order failed"):
        c.get_order("BTC", "cid-1")


def test_cancel_order_uses_delete_and_returns_result():
    c, t = _client({"/api/v3/order": (200, _order_payload(status="CANCELED", side="SELL"))})
    result = c.cancel_order("BTC", "cid-1")
    assert t.calls[0]["method"] == "DELETE"
    qs = t.calls[0]["url"].split("?", 1)[1]
    assert _verify_signature(qs)
    params = dict(urllib.parse.parse_qsl(qs))
    assert params["symbol"] == "BTCUSDT"
    assert params["origClientOrderId"] == "cid-1"
    assert result.status == "CANCELED"


def test_cancel_order_partial_fill_preserved_not_zeroed():
    c, _ = _client({"/api/v3/order": (200, _order_payload(status="CANCELED",
                                                          executedQty="0.007",
                                                          cummulativeQuoteQty="350.0"))})
    result = c.cancel_order("BTC", "cid-1")
    assert result.filled_qty == pytest.approx(0.007)        # 撤单≠零成交,按真实数量透传


def test_cancel_order_rejected_codes_map_to_none():
    for code in (-2011, -2013):
        c, _ = _client({"/api/v3/order": (400, {"code": code, "msg": "Unknown order sent."})})
        assert c.cancel_order("BTC", "cid-1") is None


def test_cancel_order_other_error_raises():
    c, _ = _client({"/api/v3/order": (400, {"code": -1100, "msg": "Illegal characters"})})
    with pytest.raises(RuntimeError, match="cancel_order failed"):
        c.cancel_order("BTC", "cid-1")


# --- 其它 ---

def test_format_qty_no_scientific_notation_and_no_trailing_zeros():
    assert _format_qty(0.015) == "0.015"
    assert _format_qty(49000.0) == "49000"
    assert _format_qty(0.000001) == "0.000001"
    assert _format_qty(0.0) == "0"


def test_non_json_body_raises_clear_runtime_error():
    def transport(method, url, *, body=None, headers=None):
        return 502, b"<html>Bad Gateway</html>"
    c = MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url=MexcSpotAdapter.MAINNET_BASE, http_request=transport,
                        clock=_CLOCK, safety_config=_ARMED, sleep=lambda _s: None)
    with pytest.raises(RuntimeError, match="non-JSON response"):
        c.account()


# --- depth(SHADOW 订单簿深度,2026-07-27 Phase 7 S1) ---

_DEPTH_RESP = {"lastUpdateId": 111,
               "bids": [["49999.5", "0.10"], ["49999.0", "0.20"]],
               "asks": [["50000.5", "0.15"], ["50001.0", "0.30"]]}


def test_depth_parses_levels_labels_source_and_as_of_none():
    c, _ = _client({"/api/v3/depth": (200, _DEPTH_RESP)})
    snap = c.depth("BTC")
    assert snap is not None
    assert snap.source == "mexc"
    assert snap.instrument_id == "BTC"
    assert [(l.price, l.size) for l in snap.bids] == [(49999.5, 0.10), (49999.0, 0.20)]
    assert [(l.price, l.size) for l in snap.asks] == [(50000.5, 0.15), (50001.0, 0.30)]
    assert snap.as_of_ms is None                     # MEXC depth 响应不带时间戳,诚实 None


def test_depth_is_unsigned_get_with_symbol_and_limit():
    c, t = _client({"/api/v3/depth": (200, _DEPTH_RESP)})
    c.depth("ETH", limit=20)
    url = t.calls[0]["url"]
    assert "signature" not in url
    params = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
    assert params == {"symbol": "ETHUSDT", "limit": "20"}


def test_depth_limit_clamped_up_to_nearest_allowed():
    for requested, expected in ((1, "5"), (75, "100"), (101, "500"), (99999, "1000")):
        c, t = _client({"/api/v3/depth": (200, _DEPTH_RESP)})
        c.depth("BTC", limit=requested)
        params = dict(urllib.parse.parse_qsl(t.calls[0]["url"].split("?", 1)[1]))
        assert params["limit"] == expected           # 向上取档,绝不少给;超最大档取最大档


def test_depth_fail_soft_on_http_error_and_non_json():
    c, _ = _client({"/api/v3/depth": (500, {"msg": "boom"})})
    assert c.depth("BTC") is None

    def transport(method, url, *, body=None, headers=None):
        return 200, b"<html>not json</html>"
    c2 = MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                         base_url=MexcSpotAdapter.MAINNET_BASE, http_request=transport,
                         clock=_CLOCK, safety_config=_ARMED, sleep=lambda _s: None)
    assert c2.depth("BTC") is None                   # 反向代理 HTML 页也 fail-soft None


def test_depth_fail_soft_on_malformed_or_empty_sides():
    for payload in ({"bids": [], "asks": [["1", "1"]]},          # 空簿
                    {"bids": [["1", "1"]], "asks": []},
                    {"bids": "nope", "asks": [["1", "1"]]},      # 形状不符
                    {"asks": [["1", "1"]]},                      # 缺 bids 键
                    {"bids": [["1", "x"]], "asks": [["1", "1"]]},  # 不可解析档
                    {"bids": [["1", "0"]], "asks": [["1", "1"]]}): # 零量档(快照垃圾)
        c, _ = _client({"/api/v3/depth": (200, payload)})
        assert c.depth("BTC") is None


def test_depth_fail_soft_on_non_finite_level():
    c, _ = _client({"/api/v3/depth": (200, {"bids": [["NaN", "1"]],
                                            "asks": [["1", "1"]]})})
    assert c.depth("BTC") is None                    # 任一非有限档 -> 整簿 None,不硬凑


# --- Phase 8 M1:只读账户端点(open_orders / all_orders / my_trades,2026-07-27) ---

_OPEN_ORDER_ROW = {"symbol": "BTCUSDT", "orderId": 987654, "clientOrderId": "cid-9",
                   "price": "50000.0", "origQty": "0.01", "executedQty": "0.002",
                   "status": "PARTIALLY_FILLED", "side": "BUY", "type": "LIMIT",
                   "transactTime": 1_700_000_000_123}

_TRADE_ROW = {"id": 555, "orderId": 987654, "clientOrderId": "cid-9",
              "symbol": "BTCUSDT", "isBuyer": True, "price": "49999.0",
              "qty": "0.002", "quoteQty": "99.998", "commission": "0",
              "commissionAsset": "BTC", "time": 1_700_000_000_456, "isMaker": False}


def test_open_orders_happy_path_exact_fields():
    c, _ = _client({"/api/v3/openOrders": (200, [_OPEN_ORDER_ROW])})
    rows = c.open_orders()
    assert rows == [{"order_id": "987654", "client_order_id": "cid-9",
                     "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
                     "status": "PARTIALLY_FILLED", "price": 50000.0,
                     "orig_qty": pytest.approx(0.01),
                     "executed_qty": pytest.approx(0.002),
                     "transact_time_ms": 1_700_000_000_123}]


def test_open_orders_signed_shape_and_no_symbol_means_market_wide():
    c, t = _client({"/api/v3/openOrders": (200, [])})
    assert c.open_orders() == []
    call = t.calls[0]
    assert call["method"] == "GET"
    assert call["headers"]["X-MEXC-APIKEY"] == _KEY
    qs_with_sig = call["url"].split("?", 1)[1]
    assert _verify_signature(qs_with_sig)
    params = dict(urllib.parse.parse_qsl(qs_with_sig))
    assert "symbol" not in params                     # 不带 symbol = 全市场挂单
    assert params["timestamp"] == "1700000000000"
    assert params["recvWindow"] == "5000"


def test_open_orders_with_symbol_param():
    c, t = _client({"/api/v3/openOrders": (200, [_OPEN_ORDER_ROW])})
    c.open_orders("BTC")
    qs_with_sig = t.calls[0]["url"].split("?", 1)[1]
    assert _verify_signature(qs_with_sig)
    params = dict(urllib.parse.parse_qsl(qs_with_sig))
    assert params["symbol"] == "BTCUSDT"


def test_open_orders_non_2xx_raises():
    c, _ = _client({"/api/v3/openOrders": (400, {"code": -1100, "msg": "bad"})})
    with pytest.raises(RuntimeError, match="open_orders failed"):
        c.open_orders()


def test_open_orders_malformed_fails_loud():
    # 非 list 载荷 / 行缺 orderId / 数值字段不可解析——对账输入一律 fail-loud,绝不静默跳过
    c, _ = _client({"/api/v3/openOrders": (200, {"code": 0})})
    with pytest.raises(RuntimeError, match="expected list"):
        c.open_orders()
    c2, _ = _client({"/api/v3/openOrders": (200, [{"symbol": "BTCUSDT"}])})
    with pytest.raises(RuntimeError, match="malformed exchange order row"):
        c2.open_orders()
    bad = dict(_OPEN_ORDER_ROW, executedQty="oops")
    c3, _ = _client({"/api/v3/openOrders": (200, [bad])})
    with pytest.raises(RuntimeError, match="malformed exchange row"):
        c3.open_orders()


def test_all_orders_happy_path_and_signed_shape():
    c, t = _client({"/api/v3/allOrders": (200, [dict(_OPEN_ORDER_ROW, status="FILLED",
                                                     executedQty="0.01")])})
    rows = c.all_orders("BTC")
    assert rows[0]["status"] == "FILLED"
    assert rows[0]["order_id"] == "987654"
    qs_with_sig = t.calls[0]["url"].split("?", 1)[1]
    assert _verify_signature(qs_with_sig)
    params = dict(urllib.parse.parse_qsl(qs_with_sig))
    assert params["symbol"] == "BTCUSDT"
    assert params["limit"] == "500"                    # 默认 limit


def test_all_orders_limit_clamped_to_1_1000():
    for requested, expected in ((0, "1"), (-5, "1"), (5000, "1000"), (250, "250")):
        c, t = _client({"/api/v3/allOrders": (200, [])})
        c.all_orders("BTC", limit=requested)
        params = dict(urllib.parse.parse_qsl(t.calls[0]["url"].split("?", 1)[1]))
        assert params["limit"] == expected


def test_all_orders_non_2xx_and_malformed_raise():
    c, _ = _client({"/api/v3/allOrders": (403, {"code": -2015, "msg": "Invalid API-key"})})
    with pytest.raises(RuntimeError, match="all_orders failed"):
        c.all_orders("BTC")
    c2, _ = _client({"/api/v3/allOrders": (200, [dict(_OPEN_ORDER_ROW, status=None)])})
    with pytest.raises(RuntimeError, match="malformed exchange order row"):
        c2.all_orders("BTC")


def test_my_trades_happy_path_exact_fields():
    c, _ = _client({"/api/v3/myTrades": (200, [_TRADE_ROW])})
    rows = c.my_trades("BTC")
    assert rows == [{"trade_id": "555", "order_id": "987654", "client_order_id": "cid-9",
                     "symbol": "BTCUSDT", "side": "BUY", "price": 49999.0,
                     "qty": pytest.approx(0.002),
                     "quote_qty": pytest.approx(99.998),
                     "commission": 0.0, "commission_asset": "BTC",
                     "time_ms": 1_700_000_000_456, "is_maker": False}]


def test_my_trades_signed_shape_and_limit_clamped():
    c, t = _client({"/api/v3/myTrades": (200, [])})
    c.my_trades("BTC", limit=9999)
    call = t.calls[0]
    assert call["headers"]["X-MEXC-APIKEY"] == _KEY
    qs_with_sig = call["url"].split("?", 1)[1]
    assert _verify_signature(qs_with_sig)
    params = dict(urllib.parse.parse_qsl(qs_with_sig))
    assert params == {"symbol": "BTCUSDT", "limit": "1000",
                      "timestamp": "1700000000000", "recvWindow": "5000",
                      "signature": params["signature"]}


def test_my_trades_side_from_is_buyer_and_missing_client_order_id_honest_empty():
    c, _ = _client({"/api/v3/myTrades": (200, [dict(_TRADE_ROW, isBuyer=False),
                                               {k: v for k, v in _TRADE_ROW.items()
                                                if k != "clientOrderId"}])})
    rows = c.my_trades("BTC")
    assert rows[0]["side"] == "SELL"
    assert rows[1]["client_order_id"] == ""            # 字段缺席 -> 诚实 "",不编造


def test_my_trades_non_2xx_and_malformed_raise():
    c, _ = _client({"/api/v3/myTrades": (500, {"msg": "boom"})})
    with pytest.raises(RuntimeError, match="my_trades failed"):
        c.my_trades("BTC")
    c2, _ = _client({"/api/v3/myTrades": (200, [{k: v for k, v in _TRADE_ROW.items()
                                                 if k != "isBuyer"}])})
    with pytest.raises(RuntimeError, match="isBuyer"):
        c2.my_trades("BTC")


# --- Phase 8 M1:exchangeInfo 的 maker/taker commission 解析 ---

_EXCHANGE_INFO_WITH_COMMISSION = {"symbols": [
    {"symbol": "BTCUSDT", "baseAsset": "BTC",
     "makerCommission": "0", "takerCommission": "0",
     "filters": [
         {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
         {"filterType": "MIN_NOTIONAL", "notional": "5"},
     ]},
    {"symbol": "ETHUSDT", "baseAsset": "ETH",          # 无 commission 字段 -> 诚实 None
     "filters": [
         {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"},
         {"filterType": "MIN_NOTIONAL", "notional": "5"},
     ]},
]}


def test_exchange_filters_parses_maker_taker_commission():
    c, _ = _client({"/api/v3/exchangeInfo": (200, _EXCHANGE_INFO_WITH_COMMISSION)})
    f = c.exchange_filters()["BTC"]
    assert f.maker_commission == 0.0                   # 零手续费是 venue 报的事实
    assert f.taker_commission == 0.0
    assert f.step_size == pytest.approx(0.00001)       # 原有字段不回归


def test_exchange_filters_commission_none_when_absent():
    c, _ = _client({"/api/v3/exchangeInfo": (200, _EXCHANGE_INFO_WITH_COMMISSION)})
    f = c.exchange_filters()["ETH"]
    assert f.maker_commission is None                  # 缺席 ≠ 免费,诚实 None
    assert f.taker_commission is None
    # 旧响应形状(无 commission 字段)同样 None,既有构造/测试零漂移
    c2, _ = _client({"/api/v3/exchangeInfo": (200, _EXCHANGE_INFO)})
    assert c2.exchange_filters()["BTC"].maker_commission is None
