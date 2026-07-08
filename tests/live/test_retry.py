"""共享限流感知退避重试(live/exchange/retry.py)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §12。纯函数,sleep 可注入
(测试零真延迟)。回答 2026-07-04 对抗式审查记录在案的运维缺口:非 2xx 响应(含限流 429/418)
之前被一视同仁转成通用 RuntimeError,没有退避、没有区分"限流"与"真错误"。"""
import pytest

from live.exchange.retry import RateLimitedError, call_with_backoff


def test_returns_immediately_when_not_rate_limited():
    calls = []

    def fn():
        calls.append(1)
        return (200, {"ok": True})

    result = call_with_backoff(fn, is_rate_limited=lambda r: False, sleep=lambda s: None)
    assert result == (200, {"ok": True})
    assert len(calls) == 1                                    # 没重试


def test_retries_with_exponential_backoff_then_succeeds():
    calls = []
    sleeps = []
    responses = [(429, {}), (429, {}), (200, {"ok": True})]

    def fn():
        return responses[len(calls)] if len(calls) < len(responses) else responses[-1]

    def fn_wrapper():
        r = fn()
        calls.append(r)
        return r

    result = call_with_backoff(fn_wrapper, is_rate_limited=lambda r: r[0] == 429,
                               max_retries=5, base_delay=1.0, sleep=sleeps.append)
    assert result == (200, {"ok": True})
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]                                # 指数退避:base*2^0, base*2^1


def test_raises_rate_limited_error_after_exhausting_retries():
    calls = []

    def fn():
        calls.append(1)
        return (429, {"msg": "too many requests"})

    with pytest.raises(RateLimitedError):
        call_with_backoff(fn, is_rate_limited=lambda r: True, max_retries=2, base_delay=0.1,
                          sleep=lambda s: None)
    assert len(calls) == 3                                     # 首次 + 2 次重试


def test_zero_max_retries_means_single_attempt_no_sleep():
    sleeps = []
    calls = []

    def fn():
        calls.append(1)
        return (429, {})

    with pytest.raises(RateLimitedError):
        call_with_backoff(fn, is_rate_limited=lambda r: True, max_retries=0, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == []


def test_non_rate_limit_failure_passes_through_untouched():
    # is_rate_limited=False 对任何"失败"结果(如非 2xx 但不是限流)都不重试,原样返回给调用方处理
    def fn():
        return (500, {"error": "internal"})

    result = call_with_backoff(fn, is_rate_limited=lambda r: r[0] in (429, 418), sleep=lambda s: None)
    assert result == (500, {"error": "internal"})
