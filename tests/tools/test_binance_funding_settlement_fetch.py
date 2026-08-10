from __future__ import annotations

import pytest
from urllib.error import URLError

from services.research.funding_settlement import HOUR_MS
from tools.binance_funding_settlement_fetch import (
    FundingSettlementFetchError,
    build_settlement_series,
    fetch_funding_events,
    fetch_mark_hours,
)
import tools.binance_funding_settlement_fetch as fetcher


def test_fetch_preserves_exact_funding_events_and_completed_post_hour_mark() -> None:
    funding_payload = [
        {"fundingTime": 8 * HOUR_MS + 5, "fundingRate": "0.00020"},
        {"fundingTime": 16 * HOUR_MS, "fundingRate": "0.00030"},
    ]
    mark_payload = [
        [8 * HOUR_MS, "100", "102", "99", "101", "0", 9 * HOUR_MS - 1],
        [16 * HOUR_MS, "110", "112", "109", "111", "0", 17 * HOUR_MS - 1],
    ]

    def http_get(url: str):
        if "fundingRate" in url:
            return funding_payload
        if "markPriceKlines" in url:
            return mark_payload
        raise AssertionError(url)

    events = fetch_funding_events(
        "BTCUSDT", 0, 24 * HOUR_MS, http_get=http_get
    )
    marks = fetch_mark_hours("BTCUSDT", 0, 24 * HOUR_MS, http_get=http_get)
    series = build_settlement_series("BTCUSDT", events, marks)

    assert [row["settlement_time_ms"] for row in series] == [8 * HOUR_MS, 16 * HOUR_MS]
    assert series[0]["source_funding_time_ms"] == 8 * HOUR_MS + 5
    assert series[0]["settled_rate"] == pytest.approx(0.00020)
    assert series[0]["decision_time_ms"] == 9 * HOUR_MS
    assert series[0]["decision_mark_price"] == 101.0


def test_fetch_fails_closed_on_event_jitter_or_missing_post_hour_mark() -> None:
    with pytest.raises(FundingSettlementFetchError, match="hour boundary"):
        build_settlement_series(
            "BTCUSDT",
            [(8 * HOUR_MS + 60_001, 0.0001)],
            {8 * HOUR_MS: (9 * HOUR_MS - 1, 101.0)},
        )

    with pytest.raises(FundingSettlementFetchError, match="post-settlement mark"):
        build_settlement_series(
            "BTCUSDT",
            [(8 * HOUR_MS, 0.0001)],
            {},
        )


def test_fetch_rejects_malformed_or_non_monotonic_public_rows() -> None:
    def malformed(_url: str):
        return [{"fundingTime": 1, "fundingRate": "nan"}]

    with pytest.raises(FundingSettlementFetchError, match="funding row"):
        fetch_funding_events("BTCUSDT", 0, HOUR_MS, http_get=malformed)

    def reversed_rows(_url: str):
        return [
            {"fundingTime": 8 * HOUR_MS, "fundingRate": "0.0001"},
            {"fundingTime": 0, "fundingRate": "0.0001"},
        ]

    with pytest.raises(FundingSettlementFetchError, match="strictly increasing"):
        fetch_funding_events("BTCUSDT", 0, 9 * HOUR_MS, http_get=reversed_rows)


def test_default_public_get_retries_transient_network_failure(monkeypatch) -> None:
    attempts = 0

    def flaky(_url: str, _headers: dict[str, str]):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise URLError("transient")
        return {"ok": True}

    monkeypatch.setattr(fetcher, "_http_get_json", flaky)
    monkeypatch.setattr(fetcher.time, "sleep", lambda _seconds: None)

    assert fetcher._default_http_get("https://example.invalid") == {"ok": True}
    assert attempts == 3
