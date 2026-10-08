"""Binance USD-M + Bybit USDT perpetual 的同步公开行情快照。

这是实时/纸面执行的补充数据源，不是历史回测数据。每一行同时记录
``observed_at_ms`` 与 ``knowledge_time_ms``，并保留 venue、盘口、资金费、OI
字段；只有在积累足够同步样本并通过 PIT/新鲜度门后，才可以研究跨所 Alpha。
缺失或退化的 venue 不会用另一 venue 的价格填充。
"""
from __future__ import annotations

import json
import math
import os
import time

from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json

_BINANCE = "https://fapi.binance.com"
_BYBIT = "https://api.bybit.com"
_DEFAULT_CACHE = os.path.join("data", "cache", "cross_exchange_snapshot.json")


class CrossExchangeDataError(ValueError):
    """同步快照违反数值、时间或响应契约。"""


def _finite_positive(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CrossExchangeDataError(f"invalid {field}") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise CrossExchangeDataError(f"invalid {field}")
    return number


def _optional_number(value, field: str) -> float | None:
    if value in (None, "", "0", 0):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CrossExchangeDataError(f"invalid {field}") from exc
    if not math.isfinite(number):
        raise CrossExchangeDataError(f"invalid {field}")
    return number


def _book_fields(*, venue: str, ticker: str, bid, ask, observed_at_ms: int,
                 last_price=None, funding_rate=None, open_interest=None,
                 source_uris: tuple[str, ...] = ()) -> dict:
    bid_v = _finite_positive(bid, "bid")
    ask_v = _finite_positive(ask, "ask")
    if ask_v < bid_v:
        raise CrossExchangeDataError("ask is below bid")
    mid = (bid_v + ask_v) / 2.0
    return {
        "schema_version": 1,
        "venue": str(venue),
        "instrument_id": ticker.upper() + "USDT",
        "event_time_ms": int(observed_at_ms),
        "knowledge_time_ms": int(observed_at_ms),
        "observed_at_ms": int(observed_at_ms),
        "bid": bid_v,
        "ask": ask_v,
        "mid": mid,
        "spread_bps": (ask_v - bid_v) / mid * 10_000.0,
        "last_price": _optional_number(last_price, "last_price"),
        "funding_rate": _optional_number(funding_rate, "funding_rate"),
        "open_interest": _optional_number(open_interest, "open_interest"),
        "source_uris": list(source_uris),
    }


def parse_binance_snapshot(book_payload: dict, premium_payload: dict,
                           oi_payload: dict, ticker: str = "BTC", *,
                           observed_at_ms: int) -> dict:
    """纯解析 Binance bookTicker + premiumIndex + openInterest 响应。"""
    if not all(isinstance(p, dict) for p in (book_payload, premium_payload, oi_payload)):
        raise CrossExchangeDataError("Binance snapshot responses must be objects")
    symbol = ticker.upper() + "USDT"
    for payload in (book_payload, premium_payload, oi_payload):
        if payload.get("symbol") not in (None, symbol):
            raise CrossExchangeDataError("Binance snapshot symbol mismatch")
    return _book_fields(
        venue="binance_usdm", ticker=ticker,
        bid=book_payload.get("bidPrice"), ask=book_payload.get("askPrice"),
        observed_at_ms=observed_at_ms,
        last_price=premium_payload.get("markPrice"),
        funding_rate=premium_payload.get("lastFundingRate"),
        open_interest=oi_payload.get("openInterest"),
        source_uris=(
            f"{_BINANCE}/fapi/v1/ticker/bookTicker?symbol={symbol}",
            f"{_BINANCE}/fapi/v1/premiumIndex?symbol={symbol}",
            f"{_BINANCE}/fapi/v1/openInterest?symbol={symbol}",
        ),
    )


def parse_bybit_snapshot(payload: dict, ticker: str = "BTC", *,
                         observed_at_ms: int) -> dict:
    """纯解析 Bybit V5 linear ticker 响应。"""
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        raise CrossExchangeDataError("Bybit ticker response is not successful")
    result = payload.get("result")
    entries = result.get("list") if isinstance(result, dict) else None
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise CrossExchangeDataError("Bybit ticker response has no unique symbol")
    row = entries[0]
    symbol = ticker.upper() + "USDT"
    if row.get("symbol") not in (None, symbol):
        raise CrossExchangeDataError("Bybit ticker symbol mismatch")
    return _book_fields(
        venue="bybit_linear", ticker=ticker,
        bid=row.get("bid1Price"), ask=row.get("ask1Price"),
        observed_at_ms=observed_at_ms,
        last_price=row.get("lastPrice"), funding_rate=row.get("fundingRate"),
        open_interest=row.get("openInterest"),
        source_uris=(f"{_BYBIT}/v5/market/tickers?category=linear&symbol={symbol}",),
    )


def _get(url: str, http_get=_http_get_json):
    return http_get(url, {"accept": "application/json"})


def capture_snapshot(ticker: str = "BTC", *, observed_at_ms: int | None = None,
                     http_get=_http_get_json, archive_root=None) -> tuple[dict, dict]:
    """抓一次两 venue 快照，返回 ``(binance_row, bybit_row)``。

    任意响应坏掉都会抛出并由调用方记录 BLOCK；不会返回半套“同步”结果。
    """
    if observed_at_ms is None:
        observed_at_ms = int(time.time() * 1000)
    symbol = ticker.upper() + "USDT"
    urls = {
        "book": f"{_BINANCE}/fapi/v1/ticker/bookTicker?symbol={symbol}",
        "premium": f"{_BINANCE}/fapi/v1/premiumIndex?symbol={symbol}",
        "oi": f"{_BINANCE}/fapi/v1/openInterest?symbol={symbol}",
        "bybit": f"{_BYBIT}/v5/market/tickers?category=linear&symbol={symbol}",
    }
    payloads = {name: _get(url, http_get) for name, url in urls.items()}
    if archive_root is not None:
        for name, payload in payloads.items():
            archive_json_response(
                root=archive_root, payload=payload, source_uri=urls[name],
                provider="Binance + Bybit public synchronized market snapshot",
                dataset_id="cross_exchange_snapshots", instrument_id=ticker,
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics={"status": "UNPARSED", "input_rows": 1},
            )
    bn = parse_binance_snapshot(payloads["book"], payloads["premium"],
                                 payloads["oi"], ticker,
                                 observed_at_ms=int(observed_at_ms))
    bb = parse_bybit_snapshot(payloads["bybit"], ticker,
                               observed_at_ms=int(observed_at_ms))
    return bn, bb


def append_snapshots(path: str = _DEFAULT_CACHE, *, rows: tuple[dict, ...] | list[dict]) -> dict:
    """按 ``(observed_at_ms, venue, instrument_id)`` 幂等追加快照。"""
    if os.path.exists(path):
        payload = json.load(open(path, encoding="utf-8"))
    else:
        payload = {"schema_version": 1,
                   "provenance": "REAL_CROSS_EXCHANGE_PUBLIC_SNAPSHOT",
                   "snapshots": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("snapshots"), list):
        raise CrossExchangeDataError("cross-exchange cache has invalid root schema")
    existing = {
        (int(row["observed_at_ms"]), str(row["venue"]), str(row["instrument_id"]))
        for row in payload["snapshots"] if isinstance(row, dict)
        and "observed_at_ms" in row and "venue" in row and "instrument_id" in row
    }
    added = 0
    for row in rows:
        if not isinstance(row, dict):
            raise CrossExchangeDataError("snapshot row must be an object")
        key = (int(row["observed_at_ms"]), str(row["venue"]),
               str(row["instrument_id"]))
        if key in existing:
            continue
        payload["snapshots"].append(dict(row))
        existing.add(key)
        added += 1
    payload["snapshots"].sort(key=lambda row: (
        int(row["observed_at_ms"]), str(row["venue"]), str(row["instrument_id"])))
    payload["last_observed_at_ms"] = max(
        (int(row["observed_at_ms"]) for row in payload["snapshots"]), default=None)
    payload["row_count"] = len(payload["snapshots"])
    payload["new_rows"] = added
    atomic_write_json(path, payload)
    return payload


def main() -> None:
    import sys
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    now_ms = int(time.time() * 1000)
    rows = capture_snapshot(ticker, observed_at_ms=now_ms,
                            archive_root=DEFAULT_RAW_ARCHIVE)
    append_snapshots(rows=rows)
    print(f"cross-exchange snapshot: {ticker} {len(rows)} venue rows")


if __name__ == "__main__":
    main()
