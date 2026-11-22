"""共享限流感知退避重试(Stage 1.5 运维硬化)。

纯函数、sleep 可注入(测试零真延迟);不含任何网络/交易所特定逻辑。
Hardened to reject negative/boolean `max_retries` (preventing UnboundLocalError)
and negative/non-finite/boolean `base_delay`.
"""
from __future__ import annotations

import math
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RateLimitedError(RuntimeError):
    """交易所明确表示限流(HTTP 429/418 或业务错误码),区别于普通失败。"""


def call_with_backoff(
    fn: Callable[[], T],
    *,
    is_rate_limited: Callable[[T], bool],
    max_retries: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """调用 `fn()` 最多 `max_retries + 1` 次。若 `is_rate_limited(result)` 为真,指数退避
    (`base_delay * 2**attempt`)后重试;耗尽仍限流 -> `raise RateLimitedError`。"""
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError(f"max_retries must be a non-negative integer, got {max_retries!r}")
    if (
        isinstance(base_delay, bool)
        or not isinstance(base_delay, (int, float))
        or not math.isfinite(float(base_delay))
        or float(base_delay) < 0.0
    ):
        raise ValueError(f"base_delay must be finite and >= 0, got {base_delay!r}")

    delay_base = float(base_delay)
    last: T
    for attempt in range(max_retries + 1):
        last = fn()
        if not is_rate_limited(last):
            return last
        if attempt < max_retries:
            sleep(delay_base * (2 ** attempt))
    raise RateLimitedError(f"rate-limited after {max_retries} retries: {last!r}")
