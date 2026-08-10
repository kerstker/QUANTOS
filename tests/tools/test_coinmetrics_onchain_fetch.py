from __future__ import annotations

import urllib.error

import pytest

from tools import coinmetrics_onchain_fetch as cm


DAY_MS = 86_400_000
T0 = (1_700_000_000_000 // DAY_MS) * DAY_MS


def _iso(timestamp: int) -> str:
    import time

    return time.strftime("%Y-%m-%dT00:00:00.000000000Z", time.gmtime(timestamp / 1000))


def _payload(rows, next_url=""):
    result = {"data": rows}
    if next_url:
        result["next_page_url"] = next_url
    return result


def test_parse_rows_keeps_completed_finite_positive_metric_values() -> None:
    diagnostics = {}
    series = cm.parse_asset_metric_rows(
        [
            {
                "asset": "btc",
                "time": _iso(T0),
                "AdrActCnt": "100",
                "TxCnt": "200",
            },
            {
                "asset": "btc",
                "time": _iso(T0 + DAY_MS),
                "AdrActCnt": "nan",
                "TxCnt": "210",
            },
            {
                "asset": "eth",
                "time": _iso(T0 + 2 * DAY_MS),
                "AdrActCnt": "300",
                "TxCnt": "400",
            },
            {"asset": "btc", "time": "bad", "AdrActCnt": "1"},
        ],
        metrics=("AdrActCnt", "TxCnt"),
        now_ms=T0 + 2 * DAY_MS,
        diagnostics=diagnostics,
    )

    assert series == {
        "BTC": {
            "AdrActCnt": {T0: 100.0},
            "TxCnt": {T0: 200.0, T0 + DAY_MS: 210.0},
        }
    }
    assert diagnostics == {
        "status": "ACCEPTED",
        "input_rows": 4,
        "accepted_values": 3,
        "rejected_rows": 2,
        "rejected_values": 1,
        "duplicate_values": 0,
    }


def test_fetch_history_follows_only_official_next_page_urls(tmp_path) -> None:
    calls = []
    next_url = (
        "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        "?next_page_token=token-2"
    )

    def fake_get(url, _headers):
        calls.append(url)
        if "next_page_token" in url:
            return _payload(
                [
                    {
                        "asset": "eth",
                        "time": _iso(T0),
                        "AdrActCnt": "300",
                    }
                ]
            )
        return _payload(
            [
                {
                    "asset": "btc",
                    "time": _iso(T0),
                    "AdrActCnt": "100",
                }
            ],
            next_url,
        )

    result = cm.fetch_history(
        assets=("btc", "eth"),
        metrics=("AdrActCnt",),
        days=10,
        now_ms=T0 + DAY_MS,
        http_get=fake_get,
        sleep=0,
        retry_sleep=0,
        archive_root=str(tmp_path),
        observed_at_ms=T0 + DAY_MS,
    )

    assert result["BTC"]["AdrActCnt"] == {T0: 100.0}
    assert result["ETH"]["AdrActCnt"] == {T0: 300.0}
    assert calls[-1] == next_url

    def malicious_get(_url, _headers):
        return _payload([], "https://example.com/steal")

    with pytest.raises(cm.CoinMetricsFetchError, match="untrusted next_page_url"):
        cm.fetch_history(
            assets=("btc",),
            metrics=("AdrActCnt",),
            days=10,
            now_ms=T0 + DAY_MS,
            http_get=malicious_get,
            sleep=0,
            retry_sleep=0,
        )


def test_transient_network_failure_retries_but_http_429_does_not() -> None:
    attempts = 0

    def transient_get(_url, _headers):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.URLError(TimeoutError("TLS timeout"))
        return _payload(
            [{"asset": "btc", "time": _iso(T0), "AdrActCnt": "100"}]
        )

    result = cm.fetch_history(
        assets=("btc",),
        metrics=("AdrActCnt",),
        days=10,
        now_ms=T0 + DAY_MS,
        http_get=transient_get,
        sleep=0,
        retry_sleep=0,
        max_attempts=2,
    )
    assert result["BTC"]["AdrActCnt"] == {T0: 100.0}
    assert attempts == 2

    attempts = 0

    def limited_get(url, _headers):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)

    with pytest.raises(urllib.error.HTTPError):
        cm.fetch_history(
            assets=("btc",),
            metrics=("AdrActCnt",),
            days=10,
            now_ms=T0 + DAY_MS,
            http_get=limited_get,
            sleep=0,
            retry_sleep=0,
            max_attempts=3,
        )
    assert attempts == 1


def test_fetch_rejects_product_catalog_fields_before_network() -> None:
    called = False

    def fake_get(_url, _headers):
        nonlocal called
        called = True
        return _payload([])

    with pytest.raises(cm.CoinMetricsFetchError, match="Community entitlement"):
        cm.fetch_history(
            assets=("btc", "sol"),
            metrics=("AdrActCnt", "FeeTotUSD"),
            days=10,
            now_ms=T0 + DAY_MS,
            http_get=fake_get,
            sleep=0,
            retry_sleep=0,
        )
    assert called is False


def test_default_source_contract_matches_current_community_catalog() -> None:
    assert cm.DEFAULT_ASSETS == (
        "btc", "eth", "bch", "ltc", "doge", "etc", "ada", "xrp", "xlm",
    )
    assert cm.DEFAULT_METRICS == ("AdrActCnt", "TxCnt", "CapMVRVCur")


def test_snapshot_reports_per_metric_coverage() -> None:
    snapshot = cm.build_snapshot(
        {
            "BTC": {
                "AdrActCnt": {T0: 100.0, T0 + DAY_MS: 110.0},
                "TxCnt": {T0: 200.0},
            },
            "ETH": {"AdrActCnt": {}, "TxCnt": {}},
        },
        metrics=("AdrActCnt", "TxCnt"),
        fetched_at_ms=T0 + 2 * DAY_MS,
    )

    assert snapshot["provenance"] == "REAL_COINMETRICS_COMMUNITY_ONCHAIN_HISTORY"
    assert snapshot["assets"]["BTC"]["AdrActCnt"] == {
        "days": 2,
        "first_date": "2023-11-14",
        "last_date": "2023-11-15",
    }
    assert snapshot["assets"]["ETH"]["TxCnt"]["first_date"] is None
    assert snapshot["license_boundary"] == "CC BY-NC 4.0 / non-commercial research only"
