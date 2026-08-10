from __future__ import annotations

import urllib.error

import pytest

from tools import bybit_positioning_fetch as bybit


DAY_MS = 86_400_000
T0 = (1_700_000_000_000 // DAY_MS) * DAY_MS


def _payload(rows, cursor=""):
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {"list": rows, "nextPageCursor": cursor},
    }


def test_parse_positioning_rows_rejects_malformed_and_unfinished_values():
    oi_diag = {}
    oi = bybit.parse_open_interest_rows(
        [
            {"timestamp": str(T0), "openInterest": "123.5"},
            {"timestamp": str(T0), "openInterest": "124.0"},
            {"timestamp": str(T0 + DAY_MS), "openInterest": "nan"},
            {"timestamp": "bad", "openInterest": "1"},
            {"timestamp": str(T0 + 2 * DAY_MS), "openInterest": "-1"},
        ],
        now_ms=T0 + 2 * DAY_MS,
        diagnostics=oi_diag,
    )
    assert oi == {T0: 124.0}
    assert oi_diag == {
        "status": "ACCEPTED",
        "input_rows": 5,
        "accepted_rows": 1,
        "rejected_rows": 3,
        "duplicate_rows": 1,
    }

    ratio_diag = {}
    ratios = bybit.parse_account_ratio_rows(
        [
            {
                "timestamp": str(T0),
                "buyRatio": "0.60",
                "sellRatio": "0.40",
            },
            {
                "timestamp": str(T0 + DAY_MS),
                "buyRatio": "0.50",
                "sellRatio": "0",
            },
            {
                "timestamp": str(T0 + 2 * DAY_MS),
                "buyRatio": "0.70",
                "sellRatio": "0.30",
            },
        ],
        now_ms=T0 + 2 * DAY_MS,
        diagnostics=ratio_diag,
    )
    assert ratios[T0]["buy_ratio"] == 0.6
    assert ratios[T0]["sell_ratio"] == 0.4
    assert ratios[T0]["long_short_ratio"] == pytest.approx(1.5)
    assert ratio_diag["accepted_rows"] == 1
    assert ratio_diag["rejected_rows"] == 2


def test_fetch_daily_positioning_paginates_both_public_endpoints():
    calls = []

    def fake_get(url, _headers):
        calls.append(url)
        is_second = "cursor=" in url
        if "open-interest" in url:
            if is_second:
                return _payload(
                    [
                        {"timestamp": str(T0 + DAY_MS), "openInterest": "101"},
                        {"timestamp": str(T0), "openInterest": "100"},
                    ]
                )
            return _payload(
                [
                    {"timestamp": str(T0 + 3 * DAY_MS), "openInterest": "103"},
                    {"timestamp": str(T0 + 2 * DAY_MS), "openInterest": "102"},
                ],
                "oi-next%3D1",
            )
        if is_second:
            return _payload(
                [
                    {
                        "timestamp": str(T0 + DAY_MS),
                        "buyRatio": "0.51",
                        "sellRatio": "0.49",
                    },
                    {
                        "timestamp": str(T0),
                        "buyRatio": "0.50",
                        "sellRatio": "0.50",
                    },
                ]
            )
        return _payload(
            [
                {
                    "timestamp": str(T0 + 3 * DAY_MS),
                    "buyRatio": "0.53",
                    "sellRatio": "0.47",
                },
                {
                    "timestamp": str(T0 + 2 * DAY_MS),
                    "buyRatio": "0.52",
                    "sellRatio": "0.48",
                },
            ],
            "ratio-next%3D1",
        )

    result = bybit.fetch_daily_positioning(
        "BTC",
        days=10,
        now_ms=T0 + 4 * DAY_MS,
        http_get=fake_get,
        sleep=0,
    )

    assert result["open_interest"] == {
        T0: 100.0,
        T0 + DAY_MS: 101.0,
        T0 + 2 * DAY_MS: 102.0,
        T0 + 3 * DAY_MS: 103.0,
    }
    assert result["long_short_ratio"][T0] == 1.0
    assert result["long_short_ratio"][T0 + 3 * DAY_MS] == pytest.approx(0.53 / 0.47)
    assert len(calls) == 4
    assert all("category=linear" in url and "symbol=BTCUSDT" in url for url in calls)
    assert any("intervalTime=1d" in url for url in calls)
    assert any("period=1d" in url for url in calls)


def test_repeated_cursor_is_rejected_instead_of_looping():
    def fake_get(url, _headers):
        if "open-interest" in url:
            return _payload(
                [{"timestamp": str(T0), "openInterest": "100"}],
                "same-cursor",
            )
        return _payload(
            [
                {
                    "timestamp": str(T0),
                    "buyRatio": "0.50",
                    "sellRatio": "0.50",
                }
            ]
        )

    with pytest.raises(bybit.BybitPositioningFetchError, match="cursor repeated"):
        bybit.fetch_daily_positioning(
            "BTC",
            days=10,
            now_ms=T0 + 4 * DAY_MS,
            http_get=fake_get,
            sleep=0,
            max_pages=3,
        )


def test_transient_tls_failure_is_retried_with_a_bound():
    calls = {"oi": 0, "ratio": 0}

    def fake_get(url, _headers):
        kind = "oi" if "open-interest" in url else "ratio"
        calls[kind] += 1
        if kind == "oi" and calls[kind] == 1:
            raise urllib.error.URLError(TimeoutError("TLS handshake timed out"))
        if kind == "oi":
            return _payload([{"timestamp": str(T0), "openInterest": "100"}])
        return _payload(
            [
                {
                    "timestamp": str(T0),
                    "buyRatio": "0.50",
                    "sellRatio": "0.50",
                }
            ]
        )

    result = bybit.fetch_daily_positioning(
        "BTC",
        days=10,
        now_ms=T0 + DAY_MS,
        http_get=fake_get,
        sleep=0,
        retry_sleep=0,
        max_attempts=2,
    )
    assert result["open_interest"] == {T0: 100.0}
    assert calls == {"oi": 2, "ratio": 1}


def test_http_rate_limit_is_not_retried():
    calls = 0

    def fake_get(url, _headers):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)

    with pytest.raises(urllib.error.HTTPError):
        bybit.fetch_daily_positioning(
            "BTC",
            days=10,
            now_ms=T0 + DAY_MS,
            http_get=fake_get,
            sleep=0,
            retry_sleep=0,
            max_attempts=3,
        )
    assert calls == 1


def test_build_snapshot_reports_intersection_coverage_without_inventing_rows():
    snapshot = bybit.build_snapshot(
        {
            "BTC": {
                "open_interest": {T0: 100.0, T0 + DAY_MS: 101.0},
                "buy_ratio": {T0: 0.5},
                "sell_ratio": {T0: 0.5},
                "long_short_ratio": {T0: 1.0},
            },
            "ETH": {
                "open_interest": {},
                "buy_ratio": {},
                "sell_ratio": {},
                "long_short_ratio": {},
            },
        },
        fetched_at_ms=T0 + 3 * DAY_MS,
    )

    assert snapshot["provenance"] == "REAL_BYBIT_PUBLIC_POSITIONING_HISTORY"
    assert snapshot["tickers"]["BTC"] == {
        "open_interest_days": 2,
        "account_ratio_days": 1,
        "common_days": 1,
        "first_common_date": "2023-11-14",
        "last_common_date": "2023-11-14",
    }
    assert snapshot["tickers"]["ETH"]["first_common_date"] is None
    assert snapshot["fields"] == [
        "open_interest",
        "buy_ratio",
        "sell_ratio",
        "long_short_ratio",
    ]
