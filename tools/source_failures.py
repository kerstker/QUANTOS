"""Deterministic classification for read-only public-data source failures."""
from __future__ import annotations

from urllib.error import HTTPError, URLError


def classify_source_failure(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        if exc.code == 404:
            return "NOT_FOUND"
        if exc.code in {408, 425, 429} or 500 <= exc.code <= 599:
            return "TRANSIENT"
        return "SOURCE_ERROR"
    if isinstance(exc, ValueError):
        return "QUALITY"
    if isinstance(exc, (URLError, TimeoutError, ConnectionError, OSError)):
        return "TRANSIENT"
    if "404" in str(exc):
        return "NOT_FOUND"
    return "SOURCE_ERROR"


def source_failure_detail(ticker: str, exc: BaseException, *, kind: str | None = None,
                          **context) -> dict:
    detail = {
        "ticker": str(ticker),
        "kind": kind or classify_source_failure(exc),
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    detail.update(context)
    return detail
