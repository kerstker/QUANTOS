from __future__ import annotations

import json
from io import BytesIO
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from services.data.binance_systemic_store import BinanceSystemicStore
from tools import binance_systemic_risk as systemic


_NOW = 1_800_000_000_000


def _fake_http(url: str):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    symbol = query.get("symbol", query.get("pair", [""]))[0]
    if parsed.path == "/fapi/v1/symbolAdlRisk":
        assert symbol == ""
        return [
            {
                "symbol": "BTCUSDT",
                "adlRisk": "LOW",
                "updateTime": _NOW - 30 * 60_000,
            }
        ]
    if parsed.path == "/futures/data/basis":
        assert symbol == "BTCUSDT"
        assert query["contractType"] == ["PERPETUAL"]
        assert query["period"] == ["1h"]
        return [
            {
                "indexPrice": "99",
                "contractType": "PERPETUAL",
                "basisRate": "0.010101",
                "futuresPrice": "100",
                "annualizedBasisRate": "",
                "basis": "1",
                "pair": symbol,
                "timestamp": _NOW - 2 * systemic.HOUR_MS,
            },
            {
                "indexPrice": "100",
                "contractType": "PERPETUAL",
                "basisRate": "0.005",
                "futuresPrice": "100.5",
                "annualizedBasisRate": "0.0438",
                "basis": "0.5",
                "pair": symbol,
                "timestamp": _NOW - systemic.HOUR_MS,
            },
        ]
    if parsed.path == "/fapi/v1/constituents":
        assert symbol == "BTCUSDT"
        return {
            "symbol": symbol,
            "time": _NOW - 60_000,
            "constituents": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "price": "100",
                    "weight": "0.6",
                },
                {
                    "exchange": "coinbase",
                    "symbol": "BTC-USD",
                    "price": "101",
                    "weight": "0.4",
                },
            ],
        }
    if parsed.path == "/fapi/v1/insuranceBalance":
        assert symbol == ""
        return [
            {
                "symbols": ["BTCUSDT", "ETHUSDT"],
                "assets": [
                    {
                        "asset": "USDT",
                        "marginBalance": "123456.7",
                        "updateTime": _NOW - 6 * systemic.HOUR_MS,
                    },
                    {
                        "asset": "BTC",
                        "marginBalance": "2.5",
                        "updateTime": _NOW - 6 * systemic.HOUR_MS,
                    },
                ],
            }
        ]
    raise AssertionError(url)


def test_refresh_backfills_and_persists_all_systemic_surfaces(tmp_path):
    store_path = str(tmp_path / "systemic.db")
    status_path = str(tmp_path / "status.json")
    status = systemic.run_refresh(
        tickers=("BTC",),
        store_path=store_path,
        status_path=status_path,
        archive_root=str(tmp_path / "raw"),
        http_get=_fake_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "PASS"
    assert status["coverage"] == {"requested": 1, "complete": 1, "failed": 0}
    assert status["store"]["inserted"] == {
        "adl_risk": 1,
        "basis_1h": 2,
        "index_constituents": 1,
        "insurance_balance": 1,
    }
    assert status["store"]["row_count"] == 5
    assert status["store"]["chain_valid"] is True
    assert status["latest"]["BTC"]["adl_risk"] == "LOW"
    assert status["latest"]["BTC"]["basis_rate"] == 0.005
    assert status["latest"]["BTC"]["constituent_count"] == 2
    assert status["latest"]["BTC"]["insurance_asset_count"] == 2
    assert status["request_rate_limit"] == {
        "planned_ip_weight": 4,
        "planned_request_count": 4,
        "shared_endpoint_weight": 2,
        "endpoint_weight_per_symbol": 2,
        "basis_refresh_budget": 4,
        "basis_refresh_tickers": ["BTC"],
        "configured_max_workers": 1,
        "min_request_interval_ms": 0,
        "cooldown_active": False,
        "last_status_code": None,
        "retry_after_seconds": 0,
        "rate_limited_endpoint": None,
        "ban_until_ms": None,
        "max_used_weights": {},
    }
    assert json.loads((tmp_path / "status.json").read_text())["complete"] is True

    store = BinanceSystemicStore(store_path)
    try:
        assert store.latest("basis_1h", "BTCUSDT")["annualized_basis_rate"] == 0.0438
        assert store.latest("index_constituents", "BTCUSDT")[
            "price_dispersion_bps"
        ] == pytest.approx(99.6015936255)
    finally:
        store.close()

    repeated_calls: list[str] = []

    def repeated_http(url: str):
        repeated_calls.append(url)
        return _fake_http(url)

    repeated = systemic.run_refresh(
        tickers=("BTC",),
        store_path=store_path,
        status_path=status_path,
        archive_root=None,
        http_get=repeated_http,
        now_ms=_NOW,
        max_workers=1,
    )
    assert sum(repeated["store"]["inserted"].values()) == 0
    assert repeated["store"]["row_count"] == 5
    assert repeated["request_rate_limit"]["basis_refresh_tickers"] == []
    assert "/futures/data/basis" not in {
        urlparse(url).path for url in repeated_calls
    }


def test_refresh_fetches_adl_and_insurance_once_for_the_universe(tmp_path):
    calls: list[str] = []

    def multi_http(url: str):
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        symbol = query.get("symbol", query.get("pair", [""]))[0]
        if parsed.path == "/fapi/v1/symbolAdlRisk":
            assert symbol == ""
            return [
                {
                    "symbol": item,
                    "adlRisk": "LOW",
                    "updateTime": _NOW - 30 * 60_000,
                }
                for item in ("BTCUSDT", "ETHUSDT")
            ]
        if parsed.path == "/fapi/v1/insuranceBalance":
            assert symbol == ""
            return [
                {
                    "symbols": ["BTCUSDT", "ETHUSDT"],
                    "assets": [
                        {
                            "asset": "USDT",
                            "marginBalance": "123456.7",
                            "updateTime": _NOW - 6 * systemic.HOUR_MS,
                        }
                    ],
                }
            ]
        if parsed.path == "/fapi/v1/constituents":
            return {
                "symbol": symbol,
                "time": _NOW - 60_000,
                "constituents": [
                    {
                        "exchange": "binance",
                        "symbol": symbol,
                        "price": "100",
                        "weight": "1",
                    }
                ],
            }
        if parsed.path == "/futures/data/basis":
            return [
                {
                    "indexPrice": "100",
                    "contractType": "PERPETUAL",
                    "basisRate": "0.005",
                    "futuresPrice": "100.5",
                    "annualizedBasisRate": "0.0438",
                    "basis": "0.5",
                    "pair": symbol,
                    "timestamp": _NOW - systemic.HOUR_MS,
                }
            ]
        raise AssertionError(url)

    status = systemic.run_refresh(
        tickers=("BTC", "ETH"),
        store_path=str(tmp_path / "systemic.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=multi_http,
        now_ms=_NOW,
        max_workers=1,
    )

    paths = [urlparse(url).path for url in calls]
    assert status["verdict"] == "PASS"
    assert paths.count("/fapi/v1/symbolAdlRisk") == 1
    assert paths.count("/fapi/v1/insuranceBalance") == 1
    assert paths.count("/fapi/v1/constituents") == 2
    assert paths.count("/futures/data/basis") == 2
    assert status["request_rate_limit"]["planned_ip_weight"] == 6


def test_refresh_uses_response_receipt_time_for_pit_knowledge(tmp_path):
    receipt_time_ms = _NOW + 2 * 60_000

    def delayed_http(url: str):
        payload = _fake_http(url)
        if urlparse(url).path == "/fapi/v1/constituents":
            payload["time"] = _NOW + 60_000
        return payload

    store_path = str(tmp_path / "systemic.db")
    status = systemic.run_refresh(
        tickers=("BTC",),
        store_path=store_path,
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=delayed_http,
        now_ms=_NOW,
        max_workers=1,
        knowledge_clock_ms=lambda: receipt_time_ms,
    )

    assert status["verdict"] == "PASS"
    store = BinanceSystemicStore(store_path)
    try:
        row = store.latest("index_constituents", "BTCUSDT")
        assert row["event_time_ms"] == _NOW + 60_000
        assert row["knowledge_time_ms"] == receipt_time_ms
    finally:
        store.close()


def test_refresh_blocks_exchange_time_after_response_receipt(tmp_path):
    receipt_time_ms = _NOW + 2 * 60_000

    def future_http(url: str):
        payload = _fake_http(url)
        if urlparse(url).path == "/fapi/v1/constituents":
            payload["time"] = receipt_time_ms + 1
        return payload

    status = systemic.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "systemic.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=future_http,
        now_ms=_NOW,
        max_workers=1,
        knowledge_clock_ms=lambda: receipt_time_ms,
    )

    assert status["verdict"] == "BLOCK"
    assert "stale or in the future" in status["errors"]["BTC"]["message"]


def test_refresh_blocks_when_shared_snapshot_omits_a_requested_symbol(tmp_path):
    def missing_http(url: str):
        parsed = urlparse(url)
        if parsed.path == "/fapi/v1/symbolAdlRisk":
            return []
        return _fake_http(url)

    status = systemic.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "systemic.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=missing_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "BLOCK"
    assert status["coverage"] == {"requested": 1, "complete": 0, "failed": 1}
    assert "shared ADL snapshot is missing BTCUSDT" in status["errors"]["BTC"][
        "message"
    ]


def test_basis_refresh_selects_only_closed_periods_oldest_first():
    starts = {
        "BTC": _NOW - 2 * systemic.HOUR_MS,
        "ETH": _NOW - systemic.HOUR_MS,
        "SOL": _NOW,
        "BNB": _NOW - 3 * systemic.HOUR_MS,
    }

    selected = systemic._basis_refresh_tickers(
        starts,
        observed_at_ms=_NOW,
        budget=2,
    )

    assert selected == ("BNB", "BTC")


def test_refresh_blocks_stale_adl_without_partial_symbol_write(tmp_path):
    def stale_http(url: str):
        payload = _fake_http(url)
        if urlparse(url).path == "/fapi/v1/symbolAdlRisk":
            payload[0]["updateTime"] = _NOW - systemic.MAX_ADL_AGE_MS - 1
        return payload

    status = systemic.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "systemic.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=stale_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "BLOCK"
    assert status["coverage"]["complete"] == 0
    assert "stale or in the future" in status["errors"]["BTC"]["message"]
    assert status["store"]["row_count"] == 0


def test_refresh_blocks_invalid_constituent_weight_sum(tmp_path):
    def invalid_http(url: str):
        payload = _fake_http(url)
        if urlparse(url).path == "/fapi/v1/constituents":
            payload["constituents"][0]["weight"] = "0.2"
        return payload

    status = systemic.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "systemic.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=invalid_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "BLOCK"
    assert "weights do not sum to one" in status["errors"]["BTC"]["message"]
    assert status["store"]["row_count"] == 0


def test_basis_parser_drops_open_hour_and_rejects_nonfinite_values():
    payload = _fake_http(
        "https://fapi.binance.com/futures/data/basis?"
        "pair=BTCUSDT&contractType=PERPETUAL&period=1h"
    )
    payload.append(
        {
            **payload[-1],
            "timestamp": _NOW,
            "basisRate": "999",
        }
    )
    rows = systemic.parse_basis_rows(
        "BTCUSDT", [payload], knowledge_time_ms=_NOW
    )
    assert len(rows) == 2
    filtered = systemic.parse_basis_rows(
        "BTCUSDT",
        [payload],
        knowledge_time_ms=_NOW,
        min_event_time_ms=int(rows[-1]["event_time_ms"]),
    )
    assert [row["event_time_ms"] for row in filtered] == [
        rows[-1]["event_time_ms"]
    ]

    payload[0]["basisRate"] = "NaN"
    with pytest.raises(systemic.BinanceSystemicRiskError, match="not finite"):
        systemic.parse_basis_rows(
            "BTCUSDT", [payload], knowledge_time_ms=_NOW
        )


def test_adl_middle_is_preserved_and_normalized_to_medium():
    observation = systemic.parse_adl_risk(
        "XRPUSDT",
        {
            "symbol": "XRPUSDT",
            "adlRisk": "MIDDLE",
            "updateTime": _NOW - 30 * 60_000,
        },
        knowledge_time_ms=_NOW,
    )

    assert observation["raw_rating"] == "MIDDLE"
    assert observation["rating"] == "MEDIUM"


def test_insurance_snapshot_uses_receipt_identity_and_preserves_source_time():
    observation = systemic.parse_insurance_balance(
        "BTCUSDT",
        {
            "symbols": ["BTCUSDT"],
            "assets": [
                {
                    "asset": "USDT",
                    "marginBalance": "12.5",
                    "updateTime": _NOW - 1_000,
                }
            ],
        },
        knowledge_time_ms=_NOW,
    )

    assert observation["event_time_ms"] == _NOW
    assert observation["knowledge_time_ms"] == _NOW
    assert observation["source_update_time_ms"] == _NOW - 1_000


def test_append_accepts_pre_raw_rating_equivalent_without_rewrite(tmp_path):
    store = BinanceSystemicStore(str(tmp_path / "systemic.db"), create=True)
    try:
        current = systemic.parse_adl_risk(
            "BTCUSDT",
            {
                "symbol": "BTCUSDT",
                "adlRisk": "LOW",
                "updateTime": _NOW - 30 * 60_000,
            },
            knowledge_time_ms=_NOW,
        )
        legacy = dict(current)
        legacy.pop("raw_rating")
        store.append(legacy)

        assert systemic._append_observation(store, current) is False
        assert store.row_count() == 1
        assert store.chain_valid() is True
    finally:
        store.close()


def test_rate_limiter_honors_retry_after_and_stops_further_network_calls():
    calls = []
    now = [100.0]

    def banned(url: str):
        calls.append(url)
        raise urllib.error.HTTPError(
            url,
            418,
            "auto-banned",
            {"Retry-After": "7"},
            None,
        )

    limited = systemic._RateLimitedHttpGet(
        banned,
        min_interval_seconds=0,
        clock=lambda: now[0],
        sleep=lambda _seconds: None,
    )

    with pytest.raises(systemic.BinanceSystemicRateLimitError) as first:
        limited("https://fapi.binance.com/first")
    with pytest.raises(systemic.BinanceSystemicRateLimitError) as second:
        limited("https://fapi.binance.com/second")

    assert first.value.status_code == second.value.status_code == 418
    assert first.value.retry_after_seconds == second.value.retry_after_seconds == 7
    assert calls == ["https://fapi.binance.com/first"]
    assert limited.telemetry()["cooldown_active"] is True
    assert limited.telemetry()["rate_limited_endpoint"] == "/first"


def test_rate_limiter_uses_body_ban_deadline_when_header_is_missing():
    now_ms = 1_800_000_000_000
    deadline_ms = now_ms + 15 * 60_000
    error = urllib.error.HTTPError(
        "https://fapi.binance.com/futures/data/basis",
        418,
        "auto-banned",
        {},
        BytesIO(
            json.dumps(
                {
                    "code": -1003,
                    "msg": f"Way too much request weight used; IP banned until {deadline_ms}.",
                }
            ).encode()
        ),
    )

    retry_after, ban_until_ms = systemic._rate_limit_deadline(
        error,
        now_ms=now_ms,
    )

    assert retry_after == 15 * 60
    assert ban_until_ms == deadline_ms


def test_refresh_obeys_persisted_ban_without_network_calls(tmp_path):
    calls: list[str] = []
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "provider": "BINANCE_PUBLIC_USDM",
                "request_rate_limit": {
                    "last_status_code": 418,
                    "rate_limited_endpoint": "/futures/data/basis",
                    "ban_until_ms": _NOW + 60_000,
                },
            }
        )
    )

    status = systemic.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "systemic.db"),
        status_path=str(status_path),
        archive_root=None,
        http_get=lambda url: calls.append(url),
        now_ms=_NOW,
        max_workers=1,
    )

    assert calls == []
    assert status["verdict"] == "BLOCK"
    assert status["request_rate_limit"]["cooldown_active"] is True
    assert status["request_rate_limit"]["ban_until_ms"] == _NOW + 60_000
    assert "/futures/data/basis" in status["errors"]["BTC"]["message"]


def test_rate_limiter_retains_maximum_binance_used_weight_headers():
    responses = iter(
        (
            systemic._HttpResult(
                {"ok": 1},
                {"X-MBX-USED-WEIGHT-1M": 12},
            ),
            systemic._HttpResult(
                {"ok": 2},
                {"X-MBX-USED-WEIGHT-1M": 9, "X-MBX-USED-WEIGHT-1D": 30},
            ),
        )
    )
    limited = systemic._RateLimitedHttpGet(
        lambda _url: next(responses),
        min_interval_seconds=0,
    )

    assert limited("first") == {"ok": 1}
    assert limited("second") == {"ok": 2}
    assert limited.telemetry()["max_used_weights"] == {
        "X-MBX-USED-WEIGHT-1D": 30,
        "X-MBX-USED-WEIGHT-1M": 12,
    }
