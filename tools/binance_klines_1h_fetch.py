"""Out-of-gate, READ-ONLY PUBLIC 1H (hourly) klines fetcher — the hourly sibling of binance_klines_fetch.py.

Same public market-data endpoint (no key, no account, no order capability), stdlib urllib GET only, NO
exchange/broker SDK and NO order/account/withdraw call — the governance boundary test pins exactly that.
This exists so the IC panel / OOS gates can FALSIFY-or-confirm a 1H edge on real hourly bars; it does NOT
assume a 1H edge exists (the repo's own holding-period evidence points the other way — crowd_contra h1
t=0.62 -> h14 3.35, edge grows with holding period, so 1H is likely weaker). Data only; do not add trading.

**The one deliberate deviation from the daily fetcher: knowledge_lag = 1 hour (kt = CLOSE, not open).**
The daily fetcher uses knowledge_lag=0 (kt=open), tolerable because daily research aligns as_of to day
boundaries. Intraday as_of grids land ON the hour open, so kt=open would let as_of=open(H) read a bar that
does not close until H+1h > as_of — a 1-hour lookahead per bar that inflates intraday IC. kt=close fixes it;
reject_if_future is the second line (a still-running hour's kt is in the future -> ingest drops it).
Writes to a SEPARATE db (crypto_binance_1h.db, dataset_id bn1h-*) — never touches the daily crypto_binance.db.
"""
from __future__ import annotations

import time

from services.data.adapters import (SourceSpec, VolumeSemantics, VolumeUnit,
                                    normalize_records)
from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       archive_json_response)
from tools.binance_klines_fetch import (_parse_klines_for_interval,
                                        fetch_klines)

_HOUR_MS = 3_600_000

BN1H_SPEC = SourceSpec(
    name="binance-klines-1h", asset_class="CRYPTO", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "o", "high": "h",
               "low": "l", "close": "c", "volume": "v"},
    knowledge_lag=_HOUR_MS,     # kt = open + 1h = CLOSE (deliberate; see module docstring)
    volume_unit=VolumeUnit.BASE_ASSET,
    volume_semantics=VolumeSemantics.PER_BAR,
)


def parse_klines_1h(batch, *, instrument_id: str, diagnostics=None) -> dict:
    """Pure: a klines array -> {hour_bucket_ms: row}. Each kline = [openTime, o, h, l, c, v, ...].
    Snaps openTime to the UTC hour grid (mirror of parse_klines but on _HOUR_MS instead of _DAY_MS)."""
    return _parse_klines_for_interval(
        batch,
        instrument_id=instrument_id,
        interval_ms=_HOUR_MS,
        diagnostics=diagnostics,
    )


def fetch_hourly_rows(ticker: str = "BTC", *, hours: int = 2160,
                      http_get=None, now_ms=None, archive_root=None,
                      observed_at_ms=None) -> list:
    """Paginate public 1H klines back ~`hours` (default 90 days), snap to the UTC hour grid, drop the live
    (currently-open) hour. Returns canonical bar rows via BN1H_SPEC (kt=close). No key. Fail-soft: empty
    list if the pair is absent. `http_get` defaults to the shared stdlib-urllib helper used by fetch_klines."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    symbol = ticker.upper() + "USDT"
    kw = {} if http_get is None else {"http_get": http_get}
    by_bucket, end = {}, now_ms
    for _ in range(max(1, (hours // 1000) + 2)):        # ~ enough pages to cover `hours` (1000 bars/page)
        source_uris = []
        batch = fetch_klines(
            symbol, interval="1h", limit=1000, end_ms=end,
            source_sink=source_uris, **kw,
        )
        diagnostics = {}
        parsed = parse_klines_1h(
            batch, instrument_id=ticker, diagnostics=diagnostics
        )
        overlap = len(set(by_bucket).intersection(parsed))
        if overlap:
            diagnostics["duplicate_rows"] += overlap
            diagnostics["accepted_rows"] -= overlap
        if archive_root is not None:
            archive_json_response(
                root=archive_root,
                payload=batch,
                source_uri=source_uris[-1],
                provider="Binance public spot klines",
                dataset_id="spot_klines_1h",
                instrument_id=ticker,
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics=diagnostics,
            )
        if not isinstance(batch, list) or not batch:
            break
        by_bucket.update(parsed)
        if len(batch) < 1000 or len(by_bucket) >= hours:
            break
        end = int(batch[0][0]) - 1
        time.sleep(0.25)
    live_hour = (now_ms // _HOUR_MS) * _HOUR_MS
    raw = [by_bucket[b] for b in sorted(by_bucket) if b < live_hour]   # drop live hour (no lookahead)
    return normalize_records(raw[-hours:], BN1H_SPEC)


def main() -> None:
    import os
    import sys

    from services.data.market_provider import ingest_provider_rows
    from services.data.persistent_store import SqlitePITStore
    tk = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 2160     # default ~90 days (narrow-universe first)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "crypto_binance_1h.db"), create=True)
    now_ms = int(time.time() * 1000)
    rows = fetch_hourly_rows(
        tk,
        hours=hours,
        now_ms=now_ms,
        archive_root=DEFAULT_RAW_ARCHIVE,
        observed_at_ms=now_ms,
    )
    rep = ingest_provider_rows(
        rows,
        store,
        dataset_id=f"bn1h-{tk}",
        now=now_ms,
        time_unit="ms",
        strict=True,
        expected_interval=_HOUR_MS,
        expected_end=(now_ms // _HOUR_MS) * _HOUR_MS - _HOUR_MS,
    )
    span = (time.strftime("%Y-%m-%d %H:00", time.gmtime(rows[0]["event_time"] / 1000)) + " .. " +
            time.strftime("%Y-%m-%d %H:00", time.gmtime(rows[-1]["event_time"] / 1000))) if rows else "—"
    print(f"{tk}USDT 1h: {len(rows)} hourly bars [{span}], ingested {rep.bars_ingested}")


if __name__ == "__main__":
    main()
