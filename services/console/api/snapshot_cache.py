"""Small single-flight cache for expensive read-only console snapshots.

The console runs one API process at L2.  A page transition can legitimately
request the same repository/registry projection several times at once.  Letting
every worker repeat the full scan makes the local reverse proxy time out even
though the authority is healthy.  This cache coalesces concurrent readers and
keeps the completed value only for a short, explicit TTL.

It is deliberately process-local and grants no authority: callers still build
and validate the same read-only payload, and exceptions are never cached.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import Condition
import time
from typing import Generic, TypeVar


T = TypeVar("T")


class SingleFlightTtlCache(Generic[T]):
    """Coalesce one in-flight build and retain its result for ``ttl_seconds``."""

    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise TypeError("ttl_seconds must be numeric")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._condition = Condition()
        self._building = False
        self._has_value = False
        self._value: T | None = None
        self._expires_at = 0.0

    def get(self, builder: Callable[[], T]) -> T:
        if not callable(builder):
            raise TypeError("builder must be callable")
        with self._condition:
            while True:
                if self._has_value and self._clock() < self._expires_at:
                    return self._value  # type: ignore[return-value]
                if not self._building:
                    self._building = True
                    break
                self._condition.wait()

        try:
            value = builder()
        except BaseException:
            with self._condition:
                self._building = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._value = value
            self._has_value = True
            self._expires_at = self._clock() + self._ttl_seconds
            self._building = False
            self._condition.notify_all()
            return value

    def clear(self) -> None:
        """Expire the completed value; an active builder is not interrupted."""
        with self._condition:
            self._has_value = False
            self._value = None
            self._expires_at = 0.0
            self._condition.notify_all()


__all__ = ["SingleFlightTtlCache"]
