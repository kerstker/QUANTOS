"""BybitFuturesAdapter(live/exchange/bybit_futures.py):V5 统一账户签名 REST 客户端。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.3 延伸(§10 Bybit 补充)。

全部注入假 http transport——零真网络。诚实标注:响应字段名/端点路径依 Bybit V5 公开文档研究
假设,**尚未对真测试网响应验证**(与 binance_futures.py 首版同样的诚实边界;这次带着"同步 ACK
不是终态"的教训——get_order 从第一天就走轮询确认,mirror 已修复的 Binance 模式)。
"""
import hashlib
import hmac
import json

import pytest

from live.exchange.bybit_futures import BybitFuturesAdapter
from quantos.contracts.config import SafetyConfig, SafetyViolation
from quantos.contracts.trading import OrderSide

_KEY, _SECRET = "test-bybit-key", "test-bybit-secret"


class _FakeTransport:
    """记录每次调用;按 path 返回预置的 (status, json_body),或一个序列(逐次消费,超出重复最后一个
    ——用于测试限流重试后成功的场景)。"""

    def __init__(self, responses):
        self.responses = responses          # {path: (status, dict) | list[(status, dict)]}
        self.calls = []
        self._path_counts = {}

    def __call__(self, method, url, *, body=None, headers=None):
        path = url.split("?")[0].replace(BybitFuturesAdapter.TESTNET_BASE, "")
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        entry = self.responses.get(path, (404, {"retCode": 404, "retMsg": "not found"}))
        if isinstance(entry, list):
            idx = min(self._path_counts.get(path, 0), len(entry) - 1)
            self._path_counts[path] = self._path_counts.get(path, 0) + 1
            status, payload = entry[idx]
        else:
            status, payload = entry
        return status, json.dumps(payload).encode()


def _client(responses=None, **kw):
    t = _FakeTransport(responses or {})
    c = BybitFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                            base_url=BybitFuturesAdapter.TESTNET_BASE,
                            http_request=t, clock=lambda: 1_700_000_000.0, **kw)
    return c, t


def _verify_signature(sign_str: str, expected_sig: str) -> bool:
    return expected_sig == hmac.new(_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()


# --- construction / mainnet gating (mirrors BinanceFuturesAdapter) ---

def test_mainnet_construction_refused_at_l2_default():
    with pytest.raises(SafetyViolation):
        BybitFuturesAdapter(api_key=_KEY, api_secret=_SECRET, base_url=BybitFuturesAdapter.MAINNET_BASE)


def test_mainnet_construction_refused_even_with_partial_flags():
    cfg = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True)
    assert cfg.live_trading_armed is False
    with pytest.raises(SafetyViolation):
        BybitFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                           base_url=BybitFuturesAdapter.MAINNET_BASE, safety_config=cfg)


def test_unknown_base_url_rejected():
    with pytest.raises(ValueError):
        BybitFuturesAdapter(api_key=_KEY, api_secret=_SECRET, base_url="https://evil.example.com")


def test_testnet_construction_succeeds_and_sets_venue_mode():
    c, _ = _client()
    assert c.venue_mode == "testnet"


# --- signature: header-based, NOT query-embedded (核心差异 vs Binance) ---

def test_signed_get_uses_bapi_headers_not_query_signature():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [{"totalWalletBalance": "1000.0",
             "totalAvailableBalance": "900.0", "totalEquity": "1000.0"}]}, "time": 1700000000000}
    c, t = _client({"/v5/account/wallet-balance": (200, resp)})
    c.account()
    call = t.calls[0]
    headers = call["headers"]
    assert headers["X-BAPI-API-KEY"] == _KEY
    assert headers["X-BAPI-TIMESTAMP"] == "1700000000000"
    assert headers["X-BAPI-RECV-WINDOW"] == "5000"
    assert "signature" not in call["url"]                     # 签名不在 query/body,在 header
    query = call["url"].split("?", 1)[1] if "?" in call["url"] else ""
    sign_str = "1700000000000" + _KEY + "5000" + query
    assert _verify_signature(sign_str, headers["X-BAPI-SIGN"])


def test_unsigned_public_call_has_no_bapi_headers():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": []}, "time": 1700000000000}
    c, t = _client({"/v5/market/instruments-info": (200, resp)})
    c.exchange_filters()
    headers = t.calls[0]["headers"] or {}
    assert "X-BAPI-SIGN" not in headers


# --- exchange_filters ---

def test_exchange_filters_parses_instruments_info():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"symbol": "BTCUSDT", "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001",
                                                "minNotionalValue": "5"},
         "priceFilter": {"tickSize": "0.1"}},
    ]}, "time": 1700000000000}
    c, _ = _client({"/v5/market/instruments-info": (200, resp)})
    filters = c.exchange_filters()
    assert filters["BTC"].step_size == pytest.approx(0.001)
    assert filters["BTC"].min_qty == pytest.approx(0.001)
    assert filters["BTC"].min_notional == pytest.approx(5.0)
    assert filters["BTC"].tick_size == pytest.approx(0.1)


def test_exchange_filters_skips_symbol_missing_lot_size_filter():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"symbol": "WEIRDUSDT", "priceFilter": {"tickSize": "0.1"}},   # 缺 lotSizeFilter
    ]}, "time": 1700000000000}
    c, _ = _client({"/v5/market/instruments-info": (200, resp)})
    assert c.exchange_filters() == {}


def test_exchange_filters_ignores_non_usdt_symbols():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"symbol": "BTCUSDC", "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001",
                                                "minNotionalValue": "5"}},
    ]}, "time": 1700000000000}
    c, _ = _client({"/v5/market/instruments-info": (200, resp)})
    assert c.exchange_filters() == {}


# --- account / positions (Bybit 用 side Buy/Sell + positionIdx,非 positionAmt 符号) ---

def test_account_returns_raw_wallet_balance():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"totalWalletBalance": "1000.0", "totalAvailableBalance": "900.0", "totalEquity": "1050.0"}]},
        "time": 1700000000000}
    c, _ = _client({"/v5/account/wallet-balance": (200, resp)})
    raw = c.account()
    assert raw["result"]["list"][0]["totalWalletBalance"] == "1000.0"


def test_positions_normalizes_one_way_mode():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.010", "avgPrice": "50000.0", "positionIdx": 0},
        {"symbol": "ETHUSDT", "side": "Sell", "size": "1.500", "avgPrice": "3000.0", "positionIdx": 0},
        {"symbol": "SOLUSDT", "side": "", "size": "0", "avgPrice": "0", "positionIdx": 0},   # 空仓
    ]}, "time": 1700000000000}
    c, _ = _client({"/v5/position/list": (200, resp)})
    pos = c.positions()
    assert set(pos) == {"BTC", "ETH"}
    assert pos["BTC"].quantity == pytest.approx(0.010)          # Buy -> 正
    assert pos["ETH"].quantity == pytest.approx(-1.500)         # Sell -> 负


def test_positions_nets_hedge_mode_legs_instead_of_overwriting():
    # positionIdx 1=多头腿/2=空头腿(即便本系统假设 one-way,防御性地处理账户被切到 hedge mode 的情形,
    # 与 Binance 的 hedge-mode 修复同一防御姿态)
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.010", "avgPrice": "50000.0", "positionIdx": 1},
        {"symbol": "BTCUSDT", "side": "Sell", "size": "0.020", "avgPrice": "51000.0", "positionIdx": 2},
    ]}, "time": 1700000000000}
    c, _ = _client({"/v5/position/list": (200, resp)})
    pos = c.positions()
    assert pos["BTC"].quantity == pytest.approx(0.010 - 0.020)   # 净敞口 = 两腿相加


def test_positions_fully_hedged_nets_to_zero_is_dropped():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.010", "avgPrice": "50000.0", "positionIdx": 1},
        {"symbol": "BTCUSDT", "side": "Sell", "size": "0.010", "avgPrice": "50000.0", "positionIdx": 2},
    ]}, "time": 1700000000000}
    c, _ = _client({"/v5/position/list": (200, resp)})
    assert "BTC" not in c.positions()


# --- place_order ---

def test_place_order_one_way_mode_posts_json_body():
    resp = {"retCode": 0, "retMsg": "OK",
           "result": {"orderId": "abc123", "orderLinkId": "cid-1"}, "time": 1700000000000}
    c, t = _client({"/v5/order/create": (200, resp)})
    order_result = c.place_order(_order("BTC", OrderSide.BUY, 0.015, "cid-1"))
    call = t.calls[0]
    assert call["method"] == "POST"
    body = json.loads(call["body"])
    assert body["symbol"] == "BTCUSDT" and body["side"] == "Buy" and body["orderType"] == "Market"
    assert body["qty"] == "0.015" and body["orderLinkId"] == "cid-1"
    assert body["category"] == "linear" and body["positionIdx"] == 0
    assert order_result.venue_order_id == "abc123" and order_result.client_order_id == "cid-1"
    assert order_result.instrument_id == "BTC"


def test_place_order_sell_side_maps_to_bybit_casing():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"orderId": "x", "orderLinkId": "c"},
           "time": 1700000000000}
    c, t = _client({"/v5/order/create": (200, resp)})
    c.place_order(_order("ETH", OrderSide.SELL, 3.0, "c"))
    body = json.loads(t.calls[0]["body"])
    assert body["side"] == "Sell" and body["qty"] == "3"        # 整数量不带小数点


def test_place_order_nonzero_retcode_raises():
    resp = {"retCode": 10001, "retMsg": "params error", "result": {}, "time": 1700000000000}
    c, _ = _client({"/v5/order/create": (200, resp)})
    with pytest.raises(RuntimeError):
        c.place_order(_order("BTC", OrderSide.BUY, 0.01, "c"))


def test_place_order_non_2xx_http_status_raises():
    c, _ = _client({"/v5/order/create": (500, {"retCode": 0, "retMsg": "gateway error"})})
    with pytest.raises(RuntimeError):
        c.place_order(_order("BTC", OrderSide.BUY, 0.01, "c"))


# --- get_order: 先查未结(realtime),再查历史(history)——mirror 已修复的"轮询确认终态"原则 ---

def test_get_order_found_in_realtime_unfilled():
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"orderId": "1", "orderLinkId": "c1", "symbol": "BTCUSDT", "orderStatus": "New",
         "cumExecQty": "0", "avgPrice": "0"}]}, "time": 1700000000000}
    c, t = _client({"/v5/order/realtime": (200, resp)})
    r = c.get_order("BTC", "c1")
    assert r.status == "NEW" and r.filled_qty == 0.0            # Bybit 原样 "New" 必须归一化
    assert t.calls[0]["url"].split("?")[0].endswith("/v5/order/realtime")


def test_get_order_falls_back_to_history_when_not_in_realtime():
    realtime_empty = {"retCode": 0, "retMsg": "OK", "result": {"list": []}, "time": 1700000000000}
    history_hit = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"orderId": "1", "orderLinkId": "c1", "symbol": "BTCUSDT", "orderStatus": "Filled",
         "cumExecQty": "0.015", "avgPrice": "62443.8"}]}, "time": 1700000000000}
    c, t = _client({"/v5/order/realtime": (200, realtime_empty),
                    "/v5/order/history": (200, history_hit)})
    r = c.get_order("BTC", "c1")
    assert r.status == "FILLED" and r.filled_qty == pytest.approx(0.015)   # 归一化后的规范状态
    assert len(t.calls) == 2                                    # 先 realtime 后 history


def test_get_order_not_found_in_either_returns_none():
    empty = {"retCode": 0, "retMsg": "OK", "result": {"list": []}, "time": 1700000000000}
    c, _ = _client({"/v5/order/realtime": (200, empty), "/v5/order/history": (200, empty)})
    assert c.get_order("BTC", "ghost") is None


def test_get_order_nonzero_retcode_raises():
    resp = {"retCode": 10001, "retMsg": "params error", "result": {}, "time": 1700000000000}
    c, _ = _client({"/v5/order/realtime": (200, resp)})
    with pytest.raises(RuntimeError):
        c.get_order("BTC", "c1")


# --- 状态归一化:Bybit 原样状态("Filled"/"Cancelled"…)必须映射到流水线的规范集合 ---
# (否则 live/pipeline.py 的 _TERMINAL_ORDER_STATUSES 永远匹配不上,订单永远"确认不了"——
#  这是接 Bybit 驱动器前必须钉死的既有缺口)

@pytest.mark.parametrize("raw,canonical", [
    ("Created", "NEW"), ("New", "NEW"), ("Untriggered", "NEW"), ("Triggered", "NEW"),
    ("PartiallyFilled", "PARTIALLY_FILLED"),
    ("Filled", "FILLED"),
    ("Cancelled", "CANCELED"), ("PartiallyFilledCanceled", "CANCELED"),
    ("Rejected", "REJECTED"),
    ("Deactivated", "EXPIRED"),
])
def test_get_order_normalizes_bybit_statuses_to_canonical(raw, canonical):
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"orderId": "1", "orderLinkId": "c1", "symbol": "BTCUSDT", "orderStatus": raw,
         "cumExecQty": "0", "avgPrice": "0"}]}, "time": 1700000000000}
    c, _ = _client({"/v5/order/realtime": (200, resp)})
    assert c.get_order("BTC", "c1").status == canonical


def test_get_order_unknown_status_passes_through_honestly():
    # 未来 Bybit 新增的未知状态:原样透传(诚实),流水线自然不会把它当终态确认——安全默认
    resp = {"retCode": 0, "retMsg": "OK", "result": {"list": [
        {"orderId": "1", "orderLinkId": "c1", "symbol": "BTCUSDT", "orderStatus": "SomethingNew2027",
         "cumExecQty": "0", "avgPrice": "0"}]}, "time": 1700000000000}
    c, _ = _client({"/v5/order/realtime": (200, resp)})
    assert c.get_order("BTC", "c1").status == "SomethingNew2027"


# --- cancel_order(OMS 补全) ---

def test_cancel_order_posts_cancel_and_ack_is_not_treated_as_terminal():
    # Bybit 撤单 ACK 只回 {orderId, orderLinkId},不带最终状态——ACK 只表示"请求被受理",
    # 不保证已撤成。诚实返回非终态,调用方必须 get_order 回查权威状态。
    resp = {"retCode": 0, "retMsg": "OK", "result": {"orderId": "1", "orderLinkId": "c1"},
           "time": 1700000000000}
    c, t = _client({"/v5/order/cancel": (200, resp)})
    r = c.cancel_order("BTC", "c1")
    body = json.loads(t.calls[0]["body"])
    assert body == {"category": "linear", "symbol": "BTCUSDT", "orderLinkId": "c1"}
    assert r is not None
    assert r.status not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


def test_cancel_order_not_exists_or_too_late_returns_none():
    # retCode 110001 = "order not exists or too late to cancel" —— 语义化 None(撤单与成交
    # 赛跑的正常路径:订单可能已成交),不是异常;调用方回查 get_order 定论
    resp = {"retCode": 110001, "retMsg": "order not exists or too late to cancel",
           "result": {}, "time": 1700000000000}
    c, _ = _client({"/v5/order/cancel": (200, resp)})
    assert c.cancel_order("BTC", "ghost") is None


def test_cancel_order_other_retcode_raises():
    resp = {"retCode": 10001, "retMsg": "params error", "result": {}, "time": 1700000000000}
    c, _ = _client({"/v5/order/cancel": (200, resp)})
    with pytest.raises(RuntimeError):
        c.cancel_order("BTC", "c1")


def test_no_withdraw_or_transfer_methods_exist():
    c, _ = _client()
    for forbidden in ("withdraw", "transfer", "create_withdrawal"):
        assert not hasattr(c, forbidden)


# --- 2026-07-04 运维硬化:限流退避 + 时钟偏移校正 ---

def test_http_429_retries_with_backoff_then_succeeds():
    sleeps = []
    resp = {"/v5/account/wallet-balance": [
        (429, {"retCode": 0, "retMsg": "too many"}),
        (200, {"retCode": 0, "retMsg": "OK", "result": {"list": [
            {"totalWalletBalance": "1000.0"}]}, "time": 1700000000000}),
    ]}
    c, t = _client(resp, sleep=sleeps.append, max_retries=5, retry_base_delay=1.0)
    c.account()
    assert len(t.calls) == 2
    assert sleeps == [1.0]


def test_retcode_10006_business_rate_limit_also_triggers_backoff():
    resp = {"/v5/account/wallet-balance": [
        (200, {"retCode": 10006, "retMsg": "Too many visits!", "result": {}, "time": 1700000000000}),
        (200, {"retCode": 0, "retMsg": "OK", "result": {"list": [
            {"totalWalletBalance": "1000.0"}]}, "time": 1700000000000}),
    ]}
    c, t = _client(resp, sleep=lambda s: None)
    c.account()
    assert len(t.calls) == 2


def test_rate_limit_exhausted_retries_raises_rate_limited_error():
    from live.exchange.retry import RateLimitedError
    resp = {"/v5/account/wallet-balance": (429, {"retCode": 0, "retMsg": "too many"})}
    c, _ = _client(resp, sleep=lambda s: None, max_retries=2)
    with pytest.raises(RateLimitedError):
        c.account()


def test_non_rate_limit_business_error_does_not_retry():
    resp = {"/v5/account/wallet-balance": (200, {"retCode": 10001, "retMsg": "params error",
                                                  "result": {}, "time": 1700000000000})}
    c, t = _client(resp, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.account()
    assert len(t.calls) == 1


def test_sync_clock_applies_offset_to_subsequent_signed_requests():
    c, t = _client({
        "/v5/market/time": (200, {"retCode": 0, "retMsg": "OK", "result": {},
                                  "time": 1_700_000_010_000}),      # 服务器比本机快 10s
        "/v5/account/wallet-balance": (200, {"retCode": 0, "retMsg": "OK",
                                             "result": {"list": [{"totalWalletBalance": "1"}]},
                                             "time": 1700000000000}),
    })
    offset = c.sync_clock()
    assert offset == 10_000
    c.account()
    assert t.calls[-1]["headers"]["X-BAPI-TIMESTAMP"] == "1700000010000"


def test_sync_clock_failure_is_fail_soft_keeps_prior_offset():
    c, _ = _client({"/v5/market/time": (500, {"retCode": 0, "retMsg": "unavailable"})})
    offset = c.sync_clock()
    assert offset == 0


def _order(inst, side, qty, cid):
    from live.contracts import LiveOrderRequest
    return LiveOrderRequest(instrument_id=inst, side=side, quantity=qty, client_order_id=cid)


# --- reduce_only(紧急减仓通道的交易所侧第二重保证) ---

def test_place_order_reduce_only_passes_flag_to_exchange():
    from live.contracts import LiveOrderRequest
    resp = {"retCode": 0, "retMsg": "OK", "result": {"orderId": "x", "orderLinkId": "c"},
           "time": 1700000000000}
    c, t = _client({"/v5/order/create": (200, resp)})
    c.place_order(LiveOrderRequest(instrument_id="BTC", side=OrderSide.SELL, quantity=0.01,
                                   client_order_id="c", reduce_only=True))
    body = json.loads(t.calls[0]["body"])
    assert body["reduceOnly"] is True


def test_place_order_default_omits_reduce_only_param():
    from live.contracts import LiveOrderRequest
    resp = {"retCode": 0, "retMsg": "OK", "result": {"orderId": "x", "orderLinkId": "c"},
           "time": 1700000000000}
    c, t = _client({"/v5/order/create": (200, resp)})
    c.place_order(LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                                   client_order_id="c"))
    assert "reduceOnly" not in json.loads(t.calls[0]["body"])


# --- Demo Trading 主机(2026-07-05:testnet 水龙头失灵,Bybit 官方主推的模拟环境) ---

def test_demo_base_construction_succeeds_zero_real_capital_class():
    # api-demo 与 testnet 同为零真实资本的模拟环境(V5 协议完全一致,只换主机)——
    # venue_mode 同为 "testnet"(mandate venue 域口径一致),不受 mainnet 门禁管辖
    c = BybitFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                            base_url=BybitFuturesAdapter.DEMO_BASE,
                            http_request=lambda *a, **kw: (200, b"{}"))
    assert c.venue_mode == "testnet"
    assert c.venue_name == "bybit"


def test_demo_base_is_not_mainnet():
    assert BybitFuturesAdapter.DEMO_BASE != BybitFuturesAdapter.MAINNET_BASE
    # mainnet 依旧结构性拒绝(demo 的加入绝不放宽真钱门禁)
    with pytest.raises(SafetyViolation):
        BybitFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                            base_url=BybitFuturesAdapter.MAINNET_BASE)
