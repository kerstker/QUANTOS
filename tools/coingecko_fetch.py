"""Out-of-gate, READ-ONLY CoinGecko market-data fetcher (crypto).

CoinGecko is a DATA provider — it has NO order/trade/account capability. This module
uses ONLY stdlib urllib (no third-party / no exchange SDK), GETs public OHLC, and writes
to the local PIT store via the offline ingestion seam. The API key is a READ-ONLY DATA
key, read at runtime from $COINGECKO_API_KEY or the gitignored key/coinkey.txt — never
hardcoded, logged, or committed. Network happens ONLY in this module's _http_get_json;
the research core and the gate stay fully offline/deterministic (tests inject a stub).

There is intentionally NO function here that places an order, manages an account, or
touches a trading credential. Do not add one — that would breach the L2 safety boundary.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request

DEMO_BASE = "https://api.coingecko.com/api/v3"
PUBLIC_HTTP_TIMEOUT_SECONDS = 30
PUBLIC_HTTP_RETRY_DELAYS_SECONDS = (0.25, 0.75, 1.5)

# CoinGecko OHLC auto-granularity by `days` (ms per candle). days=1 → 30-minute (finest).
_INTERVAL_MS = {1: 30 * 60 * 1000, 7: 4 * 3600 * 1000, 14: 4 * 3600 * 1000,
                30: 4 * 3600 * 1000, 90: 4 * 86400 * 1000, 180: 4 * 86400 * 1000,
                365: 4 * 86400 * 1000}
_DEFAULT_INTERVAL_MS = 4 * 86400 * 1000


def _read_key() -> str:
    """Read-only DATA key from env or the gitignored key/coinkey.txt. Never logged."""
    k = os.environ.get("COINGECKO_API_KEY")
    if k and k.strip():
        return k.strip()
    path = os.path.join(os.getcwd(), "key", "coinkey.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            v = fh.read().strip()
            if v:
                return v
    raise RuntimeError("CoinGecko API key not found (set $COINGECKO_API_KEY or key/coinkey.txt)")


def _http_get_json(url: str, headers: dict) -> object:
    """Read-only JSON GET with bounded transport retries.

    Public-data proxies can reset a TLS tunnel before an HTTP response exists.
    Retry only those transport failures; an explicit HTTP response remains a
    fail-fast provider decision and is never hidden.
    """
    req = urllib.request.Request(url, headers=headers, method="GET")
    for attempt in range(len(PUBLIC_HTTP_RETRY_DELAYS_SECONDS) + 1):
        try:
            with urllib.request.urlopen(  # noqa: S310 (https public data only)
                req, timeout=PUBLIC_HTTP_TIMEOUT_SECONDS
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt >= len(PUBLIC_HTTP_RETRY_DELAYS_SECONDS):
                raise
            time.sleep(PUBLIC_HTTP_RETRY_DELAYS_SECONDS[attempt])
    raise AssertionError("bounded public HTTP retry loop exhausted")


def fetch_ohlc(coin_id: str = "bitcoin", *, vs_currency: str = "usd", days: int = 1,
               http_get=_http_get_json) -> object:
    """GET public OHLC candles. `http_get` is injectable so tests run fully offline."""
    key = _read_key()
    url = f"{DEMO_BASE}/coins/{coin_id}/ohlc?vs_currency={vs_currency}&days={int(days)}"
    return http_get(url, {"x-cg-demo-api-key": key, "accept": "application/json"})


# instrument_id(裸标的名,live/ 全树的约定) -> CoinGecko coin id。仅覆盖当前 mandate universe
# 已知的主流币种(top-100,slug 稳定);新标的加一行即可,未在此映射的标的核价时诚实
# "unverifiable"(见 live/execution/price_check.py),不会误报成"发散"。
SYMBOL_TO_COINGECKO_ID = {
    "AAVE": "aave", "ADA": "cardano", "APT": "aptos", "ATOM": "cosmos",
    "AVAX": "avalanche-2", "BCH": "bitcoin-cash", "BNB": "binancecoin", "BTC": "bitcoin",
    "DOGE": "dogecoin", "DOT": "polkadot", "ETC": "ethereum-classic", "ETH": "ethereum",
    "FIL": "filecoin", "ICP": "internet-computer", "INJ": "injective-protocol",
    "LINK": "chainlink", "LTC": "litecoin", "NEAR": "near", "OP": "optimism",
    "SOL": "solana", "TRX": "tron", "UNI": "uniswap", "XLM": "stellar", "XRP": "ripple",
}


def fetch_simple_prices(instrument_ids, *, vs_currency: str = "usd",
                        http_get=_http_get_json) -> dict:
    """跨源核价用的独立第二价格源(2026-07-05)。GET 一次 `/simple/price` 拿多个币种的
    现价,只对 `SYMBOL_TO_COINGECKO_ID` 里有映射的标的发起查询——未映射的标的直接不在
    返回结果里(调用方视为"unverifiable",fail-soft,不是本函数该报错的场景)。"""
    ids = [SYMBOL_TO_COINGECKO_ID[i] for i in instrument_ids if i in SYMBOL_TO_COINGECKO_ID]
    if not ids:
        return {}
    key = _read_key()
    url = f"{DEMO_BASE}/simple/price?ids={','.join(sorted(set(ids)))}&vs_currencies={vs_currency}"
    data = http_get(url, {"x-cg-demo-api-key": key, "accept": "application/json"})
    id_to_symbol = {v: k for k, v in SYMBOL_TO_COINGECKO_ID.items()}
    out = {}
    for cg_id, row in (data or {}).items():
        sym = id_to_symbol.get(cg_id)
        price = (row or {}).get(vs_currency)
        if sym is not None and isinstance(price, (int, float)):
            out[sym] = float(price)
    return out


def fetch_simple_price_observations(instrument_ids, *, vs_currency: str = "usd",
                                    http_get=_http_get_json) -> dict:
    """Return current CoinGecko prices with provider-supplied observation times.

    Rows without both a positive finite price and ``last_updated_at`` are omitted
    so callers cannot confuse request time with source time.
    """
    ids = [SYMBOL_TO_COINGECKO_ID[i] for i in instrument_ids if i in SYMBOL_TO_COINGECKO_ID]
    if not ids:
        return {}
    key = _read_key()
    url = (f"{DEMO_BASE}/simple/price?ids={','.join(sorted(set(ids)))}"
           f"&vs_currencies={vs_currency}&include_last_updated_at=true")
    data = http_get(url, {"x-cg-demo-api-key": key, "accept": "application/json"})
    if not isinstance(data, dict):
        return {}
    id_to_symbol = {v: k for k, v in SYMBOL_TO_COINGECKO_ID.items()}
    out = {}
    for cg_id, row in data.items():
        if not isinstance(row, dict):
            continue
        symbol = id_to_symbol.get(cg_id)
        price = row.get(vs_currency)
        updated_at = row.get("last_updated_at")
        if symbol is None or isinstance(price, bool) or isinstance(updated_at, bool):
            continue
        if not isinstance(price, (int, float)) or not isinstance(updated_at, (int, float)):
            continue
        price_value = float(price)
        timestamp_seconds = float(updated_at)
        if (not math.isfinite(price_value) or price_value <= 0
                or not math.isfinite(timestamp_seconds) or timestamp_seconds <= 0):
            continue
        out[symbol] = {"price": price_value, "as_of_ms": int(timestamp_seconds * 1000)}
    return out


def parse_ohlc(payload, *, instrument_id: str, interval_ms: int, now_ms=None) -> list:
    """Pure: CoinGecko [[ts_ms, open, high, low, close], ...] -> canonical bar rows.
    event_time = ts (bar open); knowledge_time = ts + interval_ms (knowable at CLOSE,
    PIT-correct). volume = 0.0 (the OHLC endpoint omits it; Bar.volume default 0 is valid).

    Fail-closed / no-lookahead:
      * a NON-list payload (CoinGecko returns an error object on rate-limit/auth even with
        HTTP 200) raises a clear error instead of crashing on int('s') (review P1);
      * malformed candles (short/None/non-numeric) are skipped defensively (review P1);
      * if now_ms is given, any candle whose CLOSE (kt) is after now is DROPPED — the
        trailing in-progress candle is provisional and not yet knowable, so admitting it
        would be lookahead and non-reproducible (its close changes on re-fetch) (review P1)."""
    if not isinstance(payload, list):
        raise ValueError(f"CoinGecko returned a non-list payload (likely an API error): {str(payload)[:200]}")
    rows = []
    for item in payload:
        if not (isinstance(item, (list, tuple)) and len(item) >= 5):
            continue                                       # skip malformed candle
        try:
            ts, o, h, low, c = int(item[0]), float(item[1]), float(item[2]), float(item[3]), float(item[4])
        except (TypeError, ValueError):
            continue
        kt = ts + int(interval_ms)
        if now_ms is not None and kt > now_ms:
            continue                                       # in-progress candle (close not yet realized)
        rows.append({
            "instrument_id": instrument_id, "event_time": ts, "knowledge_time": kt,
            "open": o, "high": h, "low": low, "close": c, "volume": 0.0,
        })
    return rows


def fetch_btc_rows(*, days: int = 1, vs_currency: str = "usd", http_get=_http_get_json, now_ms=None) -> list:
    if now_ms is None:
        now_ms = int(time.time() * 1000)                   # wall clock (out-of-gate; non-deterministic by design)
    payload = fetch_ohlc("bitcoin", vs_currency=vs_currency, days=days, http_get=http_get)
    return parse_ohlc(payload, instrument_id="BTC",
                      interval_ms=_INTERVAL_MS.get(days, _DEFAULT_INTERVAL_MS), now_ms=now_ms)


def main() -> None:
    """Manual out-of-gate run: fetch real BTC OHLC -> local PIT store (gitignored)."""
    import sys
    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    os.makedirs("data/cache", exist_ok=True)
    store = SqlitePITStore("data/cache/crypto.db", create=True)
    now_ms = int(time.time() * 1000)
    rows = fetch_btc_rows(days=days, now_ms=now_ms)         # drops the in-progress trailing candle
    # `now=now_ms` arms reject_if_future as a second guard against any not-yet-knowable bar
    rep = ingest_provider_rows(rows, store, dataset_id="coingecko-btc", now=now_ms, time_unit="ms")
    print(f"BTC days={days}: fetched {len(rows)} candles, ingested {rep.bars_ingested}, "
          f"duplicates {rep.duplicates}, rejected {len(rep.rejected)}")


if __name__ == "__main__":
    main()
