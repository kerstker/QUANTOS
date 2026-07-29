from __future__ import annotations

import json
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
    assert symbol == "BTCUSDT"
    if parsed.path == "/fapi/v1/symbolAdlRisk":
        return {
            "symbol": symbol,
            "adlRisk": "LOW",
            "updateTime": _NOW - 30 * 60_000,
        }
    if parsed.path == "/futures/data/basis":
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
        return {
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
        "endpoint_weight_per_symbol": 4,
        "configured_max_workers": 1,
        "min_request_interval_ms": 0,
        "cooldown_active": False,
        "last_status_code": None,
        "retry_after_seconds": 0,
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

    repeated = systemic.run_refresh(
        tickers=("BTC",),
        store_path=store_path,
        status_path=status_path,
        archive_root=None,
        http_get=_fake_http,
        now_ms=_NOW,
        max_workers=1,
    )
    assert sum(repeated["store"]["inserted"].values()) == 0
    assert repeated["store"]["row_count"] == 5


def test_refresh_blocks_stale_adl_without_partial_symbol_write(tmp_path):
    def stale_http(url: str):
        payload = _fake_http(url)
        if urlparse(url).path == "/fapi/v1/symbolAdlRisk":
            payload["updateTime"] = _NOW - systemic.MAX_ADL_AGE_MS - 1
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
