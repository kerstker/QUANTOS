"""共享限流感知退避重试(Stage 1.5 运维硬化)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §12。

回答 2026-07-04 对抗式审查记录在案的运维缺口:之前 `live/exchange/{binance,bybit}_futures.py`
把非 2xx 响应(含限流 429/418)一视同仁转成通用 `RuntimeError`,没有退避、没有区分"限流"(应该
等一等再试)与"真错误"(不该无脑重试)。纯函数、sleep 可注入(测试零真延迟);不含任何网络/交易所
特定逻辑——两个 venue adapter 各自定义自己的 `is_rate_limited` 判定(HTTP 状态码/业务错误码不同),
本文件只提供退避骨架。
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RateLimitedError(RuntimeError):
    """交易所明确表示限流(HTTP 429/418 或业务错误码),区别于普通失败——调用方/驱动器可以据此
    采取不同策略(比如放弃这一批、告警,而不是当成随机的提交错误)。"""


def call_with_backoff(fn: Callable[[], T], *, is_rate_limited: Callable[[T], bool],
                      max_retries: int = 3, base_delay: float = 1.0,
                      sleep: Callable[[float], None] = time.sleep) -> T:
    """调用 `fn()` 最多 `max_retries + 1` 次。若 `is_rate_limited(result)` 为真,指数退避
    (`base_delay * 2**attempt`)后重试;耗尽仍限流 -> `raise RateLimitedError`(附最后一次结果,
    不吞掉信息)。`is_rate_limited` 为假的任何结果(包括非限流的失败,如 500)原样直接返回,
    交给调用方按既有逻辑处理——本函数只管"要不要因为限流而等一等再试",不改变其它错误语义。"""
    last: T
    for attempt in range(max_retries + 1):
        last = fn()
        if not is_rate_limited(last):
            return last
        if attempt < max_retries:
            sleep(base_delay * (2 ** attempt))
    raise RateLimitedError(f"rate-limited after {max_retries} retries: {last!r}")
