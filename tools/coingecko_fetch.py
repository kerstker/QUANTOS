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
import os
import urllib.request

DEMO_BASE = "https://api.coingecko.com/api/v3"

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
    """The ONLY network call in the codebase. Read-only GET, JSON. timeout-bounded."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:   # noqa: S310 (https public data only)
        return json.loads(resp.read().decode("utf-8"))


def fetch_ohlc(coin_id: str = "bitcoin", *, vs_currency: str = "usd", days: int = 1,
               http_get=_http_get_json) -> object:
    """GET public OHLC candles. `http_get` is injectable so tests run fully offline."""
    key = _read_key()
    url = f"{DEMO_BASE}/coins/{coin_id}/ohlc?vs_currency={vs_currency}&days={int(days)}"
    return http_get(url, {"x-cg-demo-api-key": key, "accept": "application/json"})


def parse_ohlc(payload, *, instrument_id: str, interval_ms: int) -> list:
    """Pure: CoinGecko [[ts_ms, open, high, low, close], ...] -> canonical bar rows.
    event_time = ts (bar open); knowledge_time = ts + interval_ms (knowable at CLOSE,
    PIT-correct). volume = 0.0 (the OHLC endpoint omits it; Bar.volume default 0 is valid)."""
    rows = []
    for item in payload:
        ts, o, h, low, c = int(item[0]), float(item[1]), float(item[2]), float(item[3]), float(item[4])
        rows.append({
            "instrument_id": instrument_id, "event_time": ts, "knowledge_time": ts + int(interval_ms),
            "open": o, "high": h, "low": low, "close": c, "volume": 0.0,
        })
    return rows


def fetch_btc_rows(*, days: int = 1, vs_currency: str = "usd", http_get=_http_get_json) -> list:
    payload = fetch_ohlc("bitcoin", vs_currency=vs_currency, days=days, http_get=http_get)
    return parse_ohlc(payload, instrument_id="BTC", interval_ms=_INTERVAL_MS.get(days, _DEFAULT_INTERVAL_MS))


def main() -> None:
    """Manual out-of-gate run: fetch real BTC OHLC -> local PIT store (gitignored)."""
    import sys
    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    os.makedirs("data/cache", exist_ok=True)
    store = SqlitePITStore("data/cache/crypto.db", create=True)
    rows = fetch_btc_rows(days=days)
    rep = ingest_provider_rows(rows, store, dataset_id="coingecko-btc")
    print(f"BTC days={days}: fetched {len(rows)} candles, ingested {rep.bars_ingested}, "
          f"duplicates {rep.duplicates}, rejected {len(rep.rejected)}")


if __name__ == "__main__":
    main()
