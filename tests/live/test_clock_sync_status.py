"""时钟同步可见性(P2-14):`binance_futures.py` / `mexc_spot.py` 的 `sync_clock()` 失败此前被
裸 `except Exception: pass` 完全吞掉——运维看不到任何记录,只能等下一层签名请求被交易所拒
(-1021 时间戳偏差)才后知后觉。`bybit_futures.py` 已有修好的模式(2026-07-09,诊断
docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md §有序阻塞清单 #4):
`_try_sync_clock_once() -> Optional[str]` + 公开只读属性 `last_clock_sync_ok`/
`last_clock_sync_error`。本文件把同款可见性移植到 Binance/MEXC 两家(fail-soft 不改:不抛异常、
保留旧偏移;返回值契约恒为 int 偏移量),并钉死边界:

- 失败可见(False + 原因串)、成功可见(True + None)、从未同步过 = None;
- **每次 `sync_clock()` 的调用/重试次数逐位不变**——binance/mexc 不引入 bybit 那层外层重试
  (那是 bybit 自己记录在案的取舍),本 lane 只补可见性。

全部注入假 http transport——零真网络。
"""
import json

import pytest

from live.exchange.binance_futures import BinanceFuturesAdapter
from live.exchange.bybit_futures import BybitFuturesAdapter
from live.exchange.mexc_spot import MexcSpotAdapter
from quantos.contracts.config import SafetyConfig

_KEY, _SECRET = "test-clock-key", "test-clock-secret"
_CLOCK = lambda: 1_700_000_000.0  # noqa: E731
# MEXC 无现货 testnet,可构造的世界只有"Phase 3A 人工 gate 已过"的全开 SafetyConfig。
_ARMED = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True,
                      allow_order_submission=True, allow_ai_direct_trading=False)


class _FakeTransport:
    """记录每次调用;按 path 返回预置的 (status, json_body)(与既有适配器测试同款)。"""

    def __init__(self, responses, base_url):
        self.responses = responses          # {path: (status, dict)}
        self.base_url = base_url
        self.calls = []

    def __call__(self, method, url, *, body=None, headers=None):
        path = url.split("?")[0].replace(self.base_url, "")
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        status, payload = self.responses.get(path, (404, {"msg": "not found"}))
        return status, json.dumps(payload).encode()


def _binance(responses=None, **kw):
    t = _FakeTransport(responses or {}, BinanceFuturesAdapter.TESTNET_BASE)
    c = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=t, clock=_CLOCK, sleep=lambda _s: None, **kw)
    return c, t


def _mexc(responses=None, **kw):
    t = _FakeTransport(responses or {}, MexcSpotAdapter.MAINNET_BASE)
    c = MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                        base_url=MexcSpotAdapter.MAINNET_BASE,
                        http_request=t, clock=_CLOCK, safety_config=_ARMED,
                        sleep=lambda _s: None, **kw)
    return c, t


def _bybit(responses=None, **kw):
    t = _FakeTransport(responses or {}, BybitFuturesAdapter.TESTNET_BASE)
    c = BybitFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                            base_url=BybitFuturesAdapter.TESTNET_BASE,
                            http_request=t, clock=_CLOCK, sleep=lambda _s: None, **kw)
    return c, t


# --- 可见性状态本身(三家 venue 对齐) ---

def test_clock_sync_status_attributes_exist_on_all_real_venue_adapters():
    # bybit 已有;binance/mexc 此前缺失,运维(getattr 探测,见 tools/live_risk_sentinel.py)静默跳过。
    for client in (_binance()[0], _mexc()[0], _bybit()[0]):
        assert client.last_clock_sync_ok is None       # 从未同步过 ≠ 成功 ≠ 失败
        assert client.last_clock_sync_error is None


# --- Binance ---

def test_binance_last_clock_sync_ok_initially_none_before_first_sync():
    c, _ = _binance()
    assert c.last_clock_sync_ok is None
    assert c.last_clock_sync_error is None


def test_binance_sync_clock_success_sets_visible_ok_flag():
    c, _ = _binance({"/fapi/v1/time": (200, {"serverTime": 1_700_000_010_000})})
    offset = c.sync_clock()
    assert offset == 10_000                            # 返值契约不变
    assert c.last_clock_sync_ok is True
    assert c.last_clock_sync_error is None


def test_binance_sync_clock_failure_sets_visible_failure_flag_and_reason():
    # 现状(修复前):裸 except 吞掉,last_clock_sync_ok/last_clock_sync_error 根本不存在
    # -> 运维拿到 AttributeError 或(经 getattr)静默跳过,失败现场无记录。
    c, _ = _binance({"/fapi/v1/time": (500, {"msg": "unavailable"})})
    offset = c.sync_clock()                            # fail-soft:不抛异常
    assert offset == 0
    assert c.last_clock_sync_ok is False
    assert c.last_clock_sync_error is not None and "500" in c.last_clock_sync_error


def test_binance_sync_clock_missing_server_time_field_is_recorded_as_failure():
    c, _ = _binance({"/fapi/v1/time": (200, {"msg": "not the documented shape"})})
    assert c.sync_clock() == 0                         # 缺字段 -> 不改偏移(原语义)
    assert c.last_clock_sync_ok is False
    assert c.last_clock_sync_error is not None and "/fapi/v1/time" in c.last_clock_sync_error


def test_binance_sync_clock_transport_exception_is_caught_and_recorded():
    # 真实根因场景之一:传输层直接抛异常(超时/连接失败),不是 HTTP 状态错误
    def bad_transport(method, url, *, body=None, headers=None):
        raise ConnectionError("boom")
    c = BinanceFuturesAdapter(api_key=_KEY, api_secret=_SECRET,
                              base_url=BinanceFuturesAdapter.TESTNET_BASE,
                              http_request=bad_transport, clock=_CLOCK, sleep=lambda _s: None)
    assert c.sync_clock() == 0                         # 不抛异常,保留旧偏移
    assert c.last_clock_sync_ok is False
    assert c.last_clock_sync_error is not None
    assert "ConnectionError" in c.last_clock_sync_error


def test_binance_sync_clock_failure_keeps_prior_offset_and_only_updates_status():
    c, t = _binance({"/fapi/v1/time": (200, {"serverTime": 1_700_000_010_000})})
    assert c.sync_clock() == 10_000
    t.responses["/fapi/v1/time"] = (500, {"msg": "down"})
    assert c.sync_clock() == 10_000                    # 失败保留旧的非零偏移,绝不清零
    assert c.last_clock_sync_ok is False
    assert c.last_clock_sync_error is not None


def test_binance_sync_clock_call_count_unchanged_by_failure_visibility():
    # 本 lane 只补可见性:每次 sync_clock() 仍恰好打一次 /fapi/v1/time(500 非限流 -> 不重试)。
    c, t = _binance({"/fapi/v1/time": (500, {"msg": "unavailable"})})
    c.sync_clock()
    assert len(t.calls) == 1
    t.responses["/fapi/v1/time"] = (200, {"serverTime": 1_700_000_002_000})
    assert c.sync_clock() == 2_000
    assert len(t.calls) == 2


def test_binance_sync_clock_ok_flag_resets_after_later_failure():
    c, t = _binance({"/fapi/v1/time": (200, {"serverTime": 1_700_000_010_000})})
    c.sync_clock()
    assert c.last_clock_sync_ok is True
    t.responses["/fapi/v1/time"] = (500, {"msg": "down"})
    c.sync_clock()
    assert c.last_clock_sync_ok is False               # 最近一次尝试说了算,不残留旧的 True
    assert c.last_clock_sync_error is not None


# --- MEXC(同款落差,同款修复) ---

def test_mexc_sync_clock_success_sets_visible_ok_flag():
    c, _ = _mexc({"/api/v3/time": (200, {"serverTime": 1_700_000_005_000})})
    assert c.sync_clock() == 5_000
    assert c.last_clock_sync_ok is True
    assert c.last_clock_sync_error is None


def test_mexc_sync_clock_failure_sets_visible_failure_flag_and_reason():
    c, _ = _mexc({"/api/v3/time": (500, {"msg": "boom"})})
    c._clock_offset_ms = 123
    assert c.sync_clock() == 123                       # 既有 fail-soft 契约不变
    assert c.last_clock_sync_ok is False
    assert c.last_clock_sync_error is not None and "500" in c.last_clock_sync_error


def test_mexc_sync_clock_missing_server_time_field_is_recorded_as_failure():
    c, _ = _mexc({"/api/v3/time": (200, {"msg": "not the documented shape"})})
    assert c.sync_clock() == 0
    assert c.last_clock_sync_ok is False
    assert c.last_clock_sync_error is not None and "/api/v3/time" in c.last_clock_sync_error


def test_mexc_sync_clock_transport_exception_is_caught_and_recorded():
    def bad_transport(method, url, *, body=None, headers=None):
        raise TimeoutError("no route")
    c = MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET, base_url=MexcSpotAdapter.MAINNET_BASE,
                        http_request=bad_transport, clock=_CLOCK, safety_config=_ARMED,
                        sleep=lambda _s: None)
    assert c.sync_clock() == 0
    assert c.last_clock_sync_ok is False
    assert c.last_clock_sync_error is not None and "TimeoutError" in c.last_clock_sync_error


def test_mexc_sync_clock_call_count_unchanged_by_failure_visibility():
    c, t = _mexc({"/api/v3/time": (500, {"msg": "boom"})})
    c.sync_clock()
    assert len(t.calls) == 1
    assert t.calls[0]["method"] == "GET"
    assert "signature" not in t.calls[0]["url"]        # 公开端点,不签名
