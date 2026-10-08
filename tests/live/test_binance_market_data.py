"""BinanceFuturesAdapter 公开行情读取(premium_index / funding_rates,Phase 2 缺口:
此前资金费数据只有离线日级快照)。全部注入假 transport——零真网络。

fail-soft 语义与 `book_ticker` 同型:非 2xx/非 JSON/缺字段/非有限值(NaN/Inf)/
transport 抛异常一律返回 None——它们是定价/诊断输入,不在下单路径上。
"""
import json

import pytest

from live.contracts import FundingRateSnapshot, PremiumIndexSnapshot
from live.exchange.binance_futures import BinanceFuturesAdapter

_KEY, _SECRET = "test-api-key", "test-api-secret"

_PREMIUM_RESP = {
    "symbol": "BTCUSDT",
    "markPrice": "60012.50",
    "indexPrice": "60010.10",
    "lastFundingRate": "0.0001",
    "nextFundingTime": 1700006400000,
    "time": 1700000000000,
}

_FUNDING_RESP = [
    {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1699996800000},
    {"symbol": "BTCUSDT", "fundingRate": "-0.0002", "fundingTime": 1700025600000},
]


class _FakeTransport:
    """记录每次调用;按 path 返回预置的 (status, json_body),或一个序列(逐次消费,超出重复最后一个
    ——用于测试限流重试后成功的场景)。与 tests/live/test_binance_futures_adapter.py 同型。"""

    def __init__(self, responses):
        self.responses = responses          # {path: (status, dict|list) | list[(status, ...)]}
        self.calls = []
        self._path_counts = {}

    def __call__(self, method, url, *, body=None, headers=None):
        path = url.split("?")[0].replace(BinanceFuturesAdapter.TESTNET_BASE, "")
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        entry = self.responses.get(path, (404, {"msg": "not found"}))
        if isinstance(entry, list) and entry and isinstance(entry[0], tuple):
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


class _TimeoutTransport:
    """模拟网络超时——transport 直接抛异常(绝不真发请求)。"""

    def __call__(self, method, url, *, body=None, headers=None):
        raise TimeoutError("simulated network timeout")


class _HtmlTransport:
    """反向代理/限流页面返回非 JSON body。"""

    def __call__(self, method, url, *, body=None, headers=None):
        return 200, b"<html><body>Service Unavailable</body></html>"


# --- premium_index 正常路径 ---

def test_premium_index_parses_fields_and_preserves_ms_epoch_ints():
    c, t = _client({"/fapi/v1/premiumIndex": (200, dict(_PREMIUM_RESP))})
    snap = c.premium_index("BTC")
    assert isinstance(snap, PremiumIndexSnapshot)
    assert snap.instrument_id == "BTC"
    assert snap.mark_price == pytest.approx(60012.50)
    assert snap.index_price == pytest.approx(60010.10)
    assert snap.last_funding_rate == pytest.approx(0.0001)
    assert snap.next_funding_time_ms == 1700006400000 and snap.time_ms == 1700000000000
    assert isinstance(snap.next_funding_time_ms, int) and isinstance(snap.time_ms, int)


def test_premium_index_is_unsigned_get_with_symbol_param():
    c, t = _client({"/fapi/v1/premiumIndex": (200, dict(_PREMIUM_RESP))})
    c.premium_index("ETH")
    call = t.calls[0]
    assert call["method"] == "GET"
    assert call["url"].startswith(BinanceFuturesAdapter.TESTNET_BASE + "/fapi/v1/premiumIndex?")
    assert "symbol=ETHUSDT" in call["url"]
    assert "signature" not in call["url"]                      # 公开端点,不签名
    assert "timestamp" not in call["url"]


# --- funding_rates 正常路径 ---

def test_funding_rates_parses_items_and_preserves_ms_epoch_ints():
    c, _ = _client({"/fapi/v1/fundingRate": (200, list(_FUNDING_RESP))})
    snaps = c.funding_rates("BTC")
    assert isinstance(snaps, tuple) and len(snaps) == 2
    assert all(isinstance(s, FundingRateSnapshot) for s in snaps)
    assert snaps[0].instrument_id == "BTC"
    assert snaps[0].funding_rate == pytest.approx(0.0001)
    assert snaps[0].funding_time_ms == 1699996800000
    assert snaps[1].funding_rate == pytest.approx(-0.0002)
    assert snaps[1].funding_time_ms == 1700025600000
    assert all(isinstance(s.funding_time_ms, int) for s in snaps)


def test_funding_rates_is_unsigned_get_with_symbol_and_default_limit():
    c, t = _client({"/fapi/v1/fundingRate": (200, [])})
    assert c.funding_rates("ETH") == ()
    call = t.calls[0]
    assert call["method"] == "GET"
    assert call["url"].startswith(BinanceFuturesAdapter.TESTNET_BASE + "/fapi/v1/fundingRate?")
    assert "symbol=ETHUSDT" in call["url"]
    assert "limit=100" in call["url"]                          # 默认 limit=100
    assert "signature" not in call["url"]


def test_funding_rates_limit_is_clamped_to_binance_max_1000():
    c, t = _client({"/fapi/v1/fundingRate": (200, [])})
    c.funding_rates("BTC", limit=5000)
    assert "limit=1000" in t.calls[0]["url"]                   # 钳到 Binance 上限
    assert "limit=5000" not in t.calls[0]["url"]


def test_funding_rates_explicit_limit_passed_through_when_within_bounds():
    c, t = _client({"/fapi/v1/fundingRate": (200, [])})
    c.funding_rates("BTC", limit=42)
    assert "limit=42" in t.calls[0]["url"]


# --- fail-soft:非 2xx / 超时 / 非 JSON / 缺字段 / 非有限值 一律 None ---

@pytest.mark.parametrize("method,args,path", [
    ("premium_index", ("BTC",), "/fapi/v1/premiumIndex"),
    ("funding_rates", ("BTC",), "/fapi/v1/fundingRate"),
])
def test_non_2xx_fails_soft_returns_none(method, args, path):
    for status in (400, 500):
        c, _ = _client({path: (status, {"code": -1000, "msg": "error"})})
        assert getattr(c, method)(*args) is None


@pytest.mark.parametrize("method,args", [
    ("premium_index", ("BTC",)),
    ("funding_rates", ("BTC",)),
])
def test_transport_timeout_fails_soft_returns_none(method, args):
    c = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=_TimeoutTransport(), clock=lambda: 1_700_000_000.0)
    assert getattr(c, method)(*args) is None


@pytest.mark.parametrize("method,args", [
    ("premium_index", ("BTC",)),
    ("funding_rates", ("BTC",)),
])
def test_non_json_body_fails_soft_returns_none(method, args):
    c = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=_HtmlTransport(), clock=lambda: 1_700_000_000.0)
    assert getattr(c, method)(*args) is None


@pytest.mark.parametrize("payload", [
    {"symbol": "BTCUSDT"},                                     # 缺全部数值字段
    {"markPrice": "60012.50", "indexPrice": "60010.10",
     "lastFundingRate": "0.0001", "nextFundingTime": 1700006400000},   # 缺 time
    {"markPrice": "not-a-number", "indexPrice": "60010.10",
     "lastFundingRate": "0.0001", "nextFundingTime": 1700006400000,
     "time": 1700000000000},                                   # 不可解析字符串
])
def test_premium_index_missing_or_bad_fields_fails_soft_returns_none(payload):
    c, _ = _client({"/fapi/v1/premiumIndex": (200, payload)})
    assert c.premium_index("BTC") is None


@pytest.mark.parametrize("payload", [
    {"symbol": "BTCUSDT"},                                     # 非数组(错误响应形状)
    [{"symbol": "BTCUSDT", "fundingTime": 1699996800000}],     # 缺 fundingRate
    [{"symbol": "BTCUSDT", "fundingRate": "0.0001"}],          # 缺 fundingTime
    [{"symbol": "BTCUSDT", "fundingRate": "bad", "fundingTime": 1699996800000}],
])
def test_funding_rates_bad_payload_fails_soft_returns_none(payload):
    c, _ = _client({"/fapi/v1/fundingRate": (200, payload)})
    assert c.funding_rates("BTC") is None


# --- NaN/Inf 拒绝 ---

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_premium_index_snapshot_rejects_non_finite_values(bad):
    with pytest.raises(ValueError):
        PremiumIndexSnapshot(instrument_id="BTC", mark_price=bad, index_price=1.0,
                             last_funding_rate=0.0, next_funding_time_ms=1, time_ms=1)


def test_funding_rate_snapshot_rejects_non_finite_rate():
    with pytest.raises(ValueError):
        FundingRateSnapshot(instrument_id="BTC", funding_rate=float("nan"), funding_time_ms=1)


def test_premium_index_non_finite_value_in_response_fails_soft_returns_none():
    # json 层的 NaN token 被 json.loads 解析成 float('nan') -> 契约 __post_init__ 拒绝 -> None
    payload = dict(_PREMIUM_RESP, markPrice=float("nan"))
    c, _ = _client({"/fapi/v1/premiumIndex": (200, payload)})
    assert c.premium_index("BTC") is None


def test_funding_rates_non_finite_value_in_response_fails_soft_returns_none():
    payload = [dict(_FUNDING_RESP[0], fundingRate=float("inf")), _FUNDING_RESP[1]]
    c, _ = _client({"/fapi/v1/fundingRate": (200, payload)})
    assert c.funding_rates("BTC") is None                      # 任一条非有限 -> 整体 None,不硬凑


# --- 429/418 限流退避(与既有公开端点一致的 call_with_backoff 行为) ---

@pytest.mark.parametrize("method,args,path,payload", [
    ("premium_index", ("BTC",), "/fapi/v1/premiumIndex", dict(_PREMIUM_RESP)),
    ("funding_rates", ("BTC",), "/fapi/v1/fundingRate", list(_FUNDING_RESP)),
])
def test_rate_limited_429_retries_with_backoff_then_succeeds(method, args, path, payload):
    sleeps = []
    resp = {path: [
        (429, {"code": -1003, "msg": "Too many requests"}),
        (429, {"code": -1003, "msg": "Too many requests"}),
        (200, payload),
    ]}
    c, t = _client(resp, sleep=sleeps.append, max_retries=5, retry_base_delay=1.0)
    result = getattr(c, method)(*args)
    assert result is not None
    assert len(t.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limited_418_ip_ban_also_triggers_backoff_on_public_endpoint():
    resp = {"/fapi/v1/premiumIndex": [
        (418, {"code": -1003, "msg": "IP banned"}),
        (200, dict(_PREMIUM_RESP)),
    ]}
    c, t = _client(resp, sleep=lambda s: None)
    assert c.premium_index("BTC") is not None
    assert len(t.calls) == 2


def test_rate_limited_exhausted_retries_fails_soft_returns_none():
    # 重试耗尽 -> call_with_backoff raise RateLimitedError -> fail-soft 兜成 None(诊断输入,不打断整批)
    resp = {"/fapi/v1/premiumIndex": (429, {"code": -1003, "msg": "Too many requests"})}
    c, _ = _client(resp, sleep=lambda s: None, max_retries=2)
    assert c.premium_index("BTC") is None
