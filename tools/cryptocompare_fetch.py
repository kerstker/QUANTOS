"""Out-of-gate, READ-ONLY CryptoCompare market-data fetcher — DAILY OHLCV over MULTI-YEAR history.

CryptoCompare is a DATA AGGREGATOR (like CoinGecko — NOT an exchange): it has no order / trade /
account capability. Its free public `/data/v2/histoday` returns daily OHLCV up to ~2000 days, which
reaches the OUT-OF-REGIME sample (incl. the 2022 bear) that CoinGecko's 365-day demo cap cannot. We
use ONLY stdlib urllib (via the shared helper; no third-party / no exchange SDK), GET public data,
and map it through the declarative data adapter into canonical bars. The optional API key is a
READ-ONLY DATA key read at runtime from $CRYPTOCOMPARE_API_KEY or the gitignored key/cryptocompare.txt
— never hardcoded, logged, or committed (keyless calls also work, just rate-limited).

Network lives ONLY here (out of the gate); the research core and the gate stay fully offline /
deterministic (tests inject a stub). There is intentionally NO function here that submits an order,
manages an account, or touches a trading credential. Do not add one — it would breach the L2 boundary.
"""
from __future__ import annotations

import os
import time

from services.data.adapters import SourceSpec, normalize_records
from tools.coingecko_fetch import _http_get_json

CC_BASE = "https://min-api.cryptocompare.com/data/v2"
_DAY_MS = 86_400_000

# histoday -> canonical daily bars: real OHLC + volumeto (quote-ccy / USD volume).
# knowledge_lag=0: a daily bar's close is knowable at that day's close (PIT; the live day is dropped).
CC_SPEC = SourceSpec(
    name="cryptocompare-histoday", asset_class="CRYPTO", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "o", "high": "h",
               "low": "l", "close": "c", "volume": "v"},
    knowledge_lag=0,
)


def _read_key() -> str:
    """Read-only DATA key from env or gitignored key/cryptocompare.txt; '' if absent (keyless
    public calls still work, just rate-limited). Never logged."""
    k = os.environ.get("CRYPTOCOMPARE_API_KEY")
    if k and k.strip():
        return k.strip()
    path = os.path.join(os.getcwd(), "key", "cryptocompare.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            v = fh.read().strip()
            if v:
                return v
    return ""


def fetch_histoday(fsym: str = "BTC", *, tsym: str = "USD", limit: int = 2000,
                   http_get=_http_get_json) -> object:
    """GET public daily OHLCV (up to `limit` days). http_get is injectable so tests run offline."""
    url = f"{CC_BASE}/histoday?fsym={fsym}&tsym={tsym}&limit={int(limit)}"
    headers = {"accept": "application/json"}
    key = _read_key()
    if key:
        headers["authorization"] = f"Apikey {key}"
    return http_get(url, headers)


def parse_histoday(payload, *, instrument_id: str, now_ms=None) -> list:
    """Pure: CryptoCompare {Response, Data:{Data:[{time,open,high,low,close,volumeto}]}} -> canonical
    daily bar rows (via the SourceSpec). `time` (unix SECONDS, UTC midnight) is snapped to the day
    grid (ms). Fail-closed on a non-Success / malformed payload; skips pre-listing padding rows
    (CryptoCompare pads with 0s) and any future bar; drops the trailing LIVE (today) bar."""
    if not (isinstance(payload, dict) and payload.get("Response") == "Success"
            and isinstance(payload.get("Data"), dict)
            and isinstance(payload["Data"].get("Data"), list)):
        raise ValueError(f"CryptoCompare returned an unexpected payload: {str(payload)[:200]}")
    raw = []
    for it in payload["Data"]["Data"]:
        if not isinstance(it, dict):
            continue
        try:
            ts = int(it["time"]) * 1000
            o, h, low, c = float(it["open"]), float(it["high"]), float(it["low"]), float(it["close"])
            v = float(it.get("volumeto", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if c <= 0 or o <= 0:
            continue                                       # pre-listing padding (zeros) -> skip
        if now_ms is not None and ts > now_ms:
            continue
        bucket = (ts // _DAY_MS) * _DAY_MS
        raw.append({"sym": instrument_id, "ts": bucket, "o": o, "h": h, "l": low, "c": c, "v": v})
    raw.sort(key=lambda r: r["ts"])
    if raw and now_ms is not None:
        today = (now_ms // _DAY_MS) * _DAY_MS
        if raw[-1]["ts"] >= today:                         # drop the still-forming current day
            raw = raw[:-1]
    return normalize_records(raw, CC_SPEC)


def fetch_daily_rows(fsym: str = "BTC", ticker: str = "", *, limit: int = 2000,
                     http_get=_http_get_json, now_ms=None) -> list:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    payload = fetch_histoday(fsym, limit=limit, http_get=http_get)
    return parse_histoday(payload, instrument_id=ticker or fsym, now_ms=now_ms)


def main() -> None:
    import sys
    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "crypto_history.db"), create=True)
    now_ms = int(time.time() * 1000)
    rows = fetch_daily_rows("BTC", "BTC", limit=limit, now_ms=now_ms)
    rep = ingest_provider_rows(rows, store, dataset_id="cc-hist-BTC", now=now_ms, time_unit="ms")
    span = (time.strftime("%Y-%m-%d", time.gmtime(rows[0]["event_time"] / 1000)) + " .. " +
            time.strftime("%Y-%m-%d", time.gmtime(rows[-1]["event_time"] / 1000))) if rows else "—"
    print(f"BTC histoday limit={limit}: {len(rows)} daily bars [{span}], ingested {rep.bars_ingested}")


if __name__ == "__main__":
    main()
