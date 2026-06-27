"""Out-of-gate, READ-ONLY PUBLIC market-data (klines) fetcher — free, no key, deep multi-year history.

Binance's PUBLIC klines endpoint requires no key, no account, and exposes no order capability: it returns
daily OHLCV for hundreds of USDT pairs going back years. This is DATA ONLY — stdlib urllib GET to a public
market-data endpoint (via the shared helper); it imports NO exchange/broker SDK (no ccxt / binance-python)
and makes NO order / account / withdraw call. The L2 safety invariants are about TRADING capability
(orders, credentials, broker connectivity), which reading public prices does not touch — and the governance
boundary test pins exactly that (no trading SDK, no order calls). Network lives HERE (out of the gate); the
research core stays offline / deterministic. There is intentionally no function here that trades; do not add one.
"""
from __future__ import annotations

import time

from services.data.adapters import SourceSpec, normalize_records
from tools.coingecko_fetch import _http_get_json

# public market-data hosts (the .vision domain is Binance's data-only mirror); try in order.
_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
_DAY_MS = 86_400_000

BN_SPEC = SourceSpec(
    name="binance-klines", asset_class="CRYPTO", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "o", "high": "h",
               "low": "l", "close": "c", "volume": "v"},
    knowledge_lag=0,
)


def _get(path: str, http_get=_http_get_json):
    last = None
    for host in _HOSTS:
        try:
            return http_get(host + path, {"accept": "application/json"})
        except Exception as exc:                       # host blocked/unreachable -> try the next
            last = exc
    raise last


def fetch_usdt_symbols(http_get=_http_get_json) -> list:
    """Public exchangeInfo -> base tickers of spot pairs quoted in USDT, status TRADING."""
    info = _get("/api/v3/exchangeInfo", http_get)
    out = []
    for s in (info.get("symbols", []) if isinstance(info, dict) else []):
        if (s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
                and s.get("isSpotTradingAllowed")):
            base = s.get("baseAsset")
            if base and base not in out:
                out.append(base)
    return out


def fetch_klines(symbol: str = "BTCUSDT", *, interval: str = "1d", limit: int = 1000,
                 end_ms=None, http_get=_http_get_json):
    path = f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={int(limit)}"
    if end_ms is not None:
        path += f"&endTime={int(end_ms)}"
    return _get(path, http_get)


def parse_klines(batch, *, instrument_id: str) -> dict:
    """Pure: a klines array -> {day_bucket_ms: row}. Each kline = [openTime, o, h, l, c, v, ...]."""
    out = {}
    if not isinstance(batch, list):
        return out
    for k in batch:
        if not (isinstance(k, (list, tuple)) and len(k) >= 6):
            continue
        try:
            ts = int(k[0])
            o, h, low, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        except (TypeError, ValueError):
            continue
        if c <= 0 or o <= 0:
            continue
        bucket = (ts // _DAY_MS) * _DAY_MS
        out[bucket] = {"sym": instrument_id, "ts": bucket, "o": o, "h": h, "l": low, "c": c, "v": v}
    return out


def fetch_daily_rows(ticker: str = "BTC", *, days: int = 2000, http_get=_http_get_json, now_ms=None) -> list:
    """Paginate public daily klines back ~`days`, snap to UTC day grid, drop the live (today) day.
    Returns canonical bar rows via the SourceSpec. No key. Fail-soft: empty list if the pair is absent."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    symbol = ticker.upper() + "USDT"
    by_bucket, end = {}, now_ms
    for _ in range(8):                                 # up to 8*1000 days — far more than needed
        batch = fetch_klines(symbol, interval="1d", limit=1000, end_ms=end, http_get=http_get)
        if not isinstance(batch, list) or not batch:
            break
        by_bucket.update(parse_klines(batch, instrument_id=ticker))
        if len(batch) < 1000 or len(by_bucket) >= days:
            break                                      # reached start of history / enough
        end = int(batch[0][0]) - 1                     # next page ends just before this batch's oldest
        time.sleep(0.25)
    today = (now_ms // _DAY_MS) * _DAY_MS
    raw = [by_bucket[b] for b in sorted(by_bucket) if b < today]   # drop live day (no lookahead)
    return normalize_records(raw[-days:], BN_SPEC)


def main() -> None:
    import sys
    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows
    import os
    tk = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "crypto_binance.db"), create=True)
    now_ms = int(time.time() * 1000)
    rows = fetch_daily_rows(tk, days=2000, now_ms=now_ms)
    rep = ingest_provider_rows(rows, store, dataset_id=f"bn-{tk}", now=now_ms, time_unit="ms")
    span = (time.strftime("%Y-%m-%d", time.gmtime(rows[0]["event_time"] / 1000)) + " .. " +
            time.strftime("%Y-%m-%d", time.gmtime(rows[-1]["event_time"] / 1000))) if rows else "—"
    print(f"{tk}USDT: {len(rows)} daily bars [{span}], ingested {rep.bars_ingested}")


if __name__ == "__main__":
    main()
