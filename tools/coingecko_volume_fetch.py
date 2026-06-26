"""Out-of-gate, READ-ONLY CoinGecko market_chart fetcher — real price + REAL volume.

The /ohlc endpoint we already use carries NO volume. CoinGecko /coins/{id}/market_chart
returns prices AND total_volumes (24h rolling traded volume in the vs_currency) — a genuine
liquidity dimension. We map them through the declarative data adapter (services.data.adapters
SourceSpec) into canonical bars carrying REAL volume, ready for the offline ingest seam.

Read-only DATA only: stdlib urllib via the shared helpers in coingecko_fetch (no SDK, no
order/account/withdraw, no credential beyond the read-only data key). Network lives here,
out of the gate; the research core stays offline/deterministic.

Note on semantics (honest): market_chart's volume is the 24h ROLLING traded volume observed
at each timestamp (a liquidity LEVEL), not a per-bar traded volume. open/high/low are set to
the observed price (market_chart gives price only); close = that price. Good for liquidity /
dollar-volume factors; not an OHLC range.
"""
from __future__ import annotations

import time

from services.data.adapters import SourceSpec, normalize_records
from tools.coingecko_fetch import DEMO_BASE, _read_key, _http_get_json

# market_chart -> canonical bars: price becomes o/h/l/c, total_volume becomes volume.
# knowledge_lag=0: a spot price/volume observed at ts is knowable at ts (PIT-correct).
MC_SPEC = SourceSpec(
    name="coingecko-market-chart", asset_class="CRYPTO", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "px", "high": "px",
               "low": "px", "close": "px", "volume": "vol"},
    knowledge_lag=0,
)


def fetch_market_chart(coin_id: str = "bitcoin", *, vs_currency: str = "usd", days: int = 30,
                       http_get=_http_get_json) -> object:
    """GET public market_chart (prices + total_volumes). http_get injectable for offline tests."""
    key = _read_key()
    url = f"{DEMO_BASE}/coins/{coin_id}/market_chart?vs_currency={vs_currency}&days={int(days)}"
    return http_get(url, {"x-cg-demo-api-key": key, "accept": "application/json"})


_HOUR_MS = 3_600_000


def parse_market_chart(payload, *, instrument_id: str, now_ms=None, bucket_ms: int = _HOUR_MS) -> list:
    """Pure: market_chart {prices:[[ts,px]], total_volumes:[[ts,vol]]} -> canonical bar rows
    (via the SourceSpec). event_time is SNAPPED to a common grid (`bucket_ms`, default 1h) so
    different coins' points land on the SAME period keys — otherwise each coin's slightly-offset
    hourly stamps never align and every cross-section collapses to one name (breadth 0). Volume
    is looked up by the original stamp. Fail-closed on a non-dict/error payload; drops any future
    point and the trailing LIVE bucket (its 24h volume is provisional / non-reproducible)."""
    if not (isinstance(payload, dict) and isinstance(payload.get("prices"), list)
            and isinstance(payload.get("total_volumes"), list)):
        raise ValueError(f"market_chart returned an unexpected payload: {str(payload)[:200]}")
    vol_by_ts = {}
    for item in payload["total_volumes"]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                vol_by_ts[int(item[0])] = float(item[1])
            except (TypeError, ValueError):
                continue
    by_bucket = {}                                   # bucket -> row (last point in the bucket wins)
    for item in payload["prices"]:
        if not (isinstance(item, (list, tuple)) and len(item) >= 2):
            continue
        try:
            ts, px = int(item[0]), float(item[1])
        except (TypeError, ValueError):
            continue
        if now_ms is not None and ts > now_ms:
            continue
        bucket = (ts // bucket_ms) * bucket_ms if bucket_ms > 0 else ts
        by_bucket[bucket] = {"sym": instrument_id, "ts": bucket, "px": px, "vol": vol_by_ts.get(ts, 0.0)}
    raw = [by_bucket[b] for b in sorted(by_bucket)]
    if raw and now_ms is not None:
        raw = raw[:-1]                              # drop the trailing LIVE bucket (provisional)
    return normalize_records(raw, MC_SPEC)


def fetch_volume_rows(coin_id: str = "bitcoin", ticker: str = "BTC", *, days: int = 30,
                      http_get=_http_get_json, now_ms=None) -> list:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    payload = fetch_market_chart(coin_id, days=days, http_get=http_get)
    return parse_market_chart(payload, instrument_id=ticker, now_ms=now_ms)


def main() -> None:
    import os
    import sys
    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "crypto_volume.db"), create=True)
    now_ms = int(time.time() * 1000)
    rows = fetch_volume_rows("bitcoin", "BTC", days=days, now_ms=now_ms)
    rep = ingest_provider_rows(rows, store, dataset_id="coingecko-mc-btc", now=now_ms, time_unit="ms")
    vols = [r["volume"] for r in rows]
    print(f"BTC market_chart days={days}: {len(rows)} points, ingested {rep.bars_ingested}, "
          f"rejected {len(rep.rejected)}")
    if vols:
        print(f"  REAL 24h volume (USD) range: {min(vols):,.0f} .. {max(vols):,.0f}")


if __name__ == "__main__":
    main()
