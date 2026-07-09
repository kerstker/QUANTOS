"""Out-of-gate, READ-ONLY PUBLIC 1m (minute) klines fetcher — the minute sibling of
binance_klines_1h_fetch.py, itself the hourly sibling of binance_klines_fetch.py.

Same public market-data endpoint (no key, no account, no order capability), same stdlib-urllib
dual-host failover + pagination via the SHARED `fetch_klines()` helper (this file does NOT
reimplement pagination or host failover — it reuses tools.binance_klines_fetch.fetch_klines
exactly like the 1h sibling does). NO exchange/broker SDK, NO order/account/withdraw call — the
governance boundary test (tests/governance/test_crypto_data_boundary.py) pins exactly that.

This exists to let a specific intraday strategy (docs/test/lance_1m_strategy.md, a 1-minute
BTCUSDT mean-reversion idea) be validated against REAL 1-minute bars instead of asserted numbers.
Data only; do not add trading.

**The one deliberate deviation from the daily fetcher, INHERITED from the 1h fetcher and NOT
allowed to regress: knowledge_lag = 1 minute (kt = CLOSE, not open).** The daily fetcher uses
knowledge_lag=0 (kt=open), tolerable because daily research aligns as_of to day boundaries. The
1h fetcher explicitly fixed this for intraday: as_of grids that land ON a bar's open would let
as_of=open(bar) read a bar that has not closed yet (open + 1 interval > as_of) — a lookahead of
one full bar, which inflates intraday IC every single bar. That bug gets WORSE at 1-minute
granularity (a strategy trading every bar is wrong about the knowability of literally every bar
it ever looks at), so this fetcher uses knowledge_lag=_MINUTE_MS (kt=close) exactly like the 1h
fetcher's knowledge_lag=_HOUR_MS — this is not optional, it is the whole point of this module
existing as a distinct file instead of a thin wrapper around the daily one. `reject_if_future`
in the shared ingest seam is the second line of defense (a still-running minute's kt is in the
future -> ingest drops it).

Writes to a SEPARATE db (crypto_binance_1m.db, dataset_id bn1m-*) — never touches the daily
crypto_binance.db or the hourly crypto_binance_1h.db.

**CLI shape deliberately differs from the daily/hourly fetchers**: those default to "N bars back
from now" because a fixed relative window is a reasonable default at day/hour granularity. At
1-minute granularity that default would be enormous (90 days = 129,600 bars vs. 2,160 for 1h) and
usually wrong — a 1m validation almost always wants a SPECIFIC historical window (e.g. the exact
period a strategy claims results over), not "the last N minutes". So this CLI requires an
explicit --start-ms and accepts an optional --end-ms (default: now) instead of a bar-count knob.
"""
from __future__ import annotations

import time

from services.data.adapters import SourceSpec, normalize_records
from tools.binance_klines_fetch import fetch_klines

_MINUTE_MS = 60_000

BN1M_SPEC = SourceSpec(
    name="binance-klines-1m", asset_class="CRYPTO", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "o", "high": "h",
               "low": "l", "close": "c", "volume": "v"},
    knowledge_lag=_MINUTE_MS,   # kt = open + 1m = CLOSE (deliberate; see module docstring — do NOT
                                # revert this to the daily fetcher's knowledge_lag=0)
)


def parse_klines_1m(batch, *, instrument_id: str) -> dict:
    """Pure: a klines array -> {minute_bucket_ms: row}. Each kline = [openTime, o, h, l, c, v, ...].
    Snaps openTime to the UTC minute grid (mirror of parse_klines_1h but on _MINUTE_MS)."""
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
        bucket = (ts // _MINUTE_MS) * _MINUTE_MS
        out[bucket] = {"sym": instrument_id, "ts": bucket, "o": o, "h": h, "l": low, "c": c, "v": v}
    return out


_PAGE_MAX_RETRIES = 4       # up to 5 attempts per page before giving up on the whole fetch
_PAGE_RETRY_BASE_DELAY = 1.0


def _fetch_page_with_retry(symbol: str, *, end_ms: int, sleep, kw: dict):
    """One page of `fetch_klines`, retried with exponential backoff on ANY exception (network
    blips, not just rate-limiting — this endpoint returns no structured rate-limit signal we can
    branch on, unlike the live exchange adapters' `call_with_backoff`, which is why this is a
    separate, simpler exception-retry loop rather than a reuse of `live/exchange/retry.py`).
    A real fetch is 100s of sequential pages (2026-07-09: observed this environment's network
    blip mid-run, discarding every already-successful page before it) — retrying the ONE flaky
    page is far cheaper than losing the whole run. Exhausting the budget re-raises the last error
    (never silently returns something that looks like "no data" — that would be misread as "this
    period genuinely has no bars" instead of "the network failed")."""
    last_exc = None
    for attempt in range(_PAGE_MAX_RETRIES + 1):
        try:
            return fetch_klines(symbol, interval="1m", limit=1000, end_ms=end_ms, **kw)
        except Exception as exc:                        # noqa: BLE001 — deliberately broad: any
            last_exc = exc                               # transport failure should retry, not just
            if attempt < _PAGE_MAX_RETRIES:               # a specific exception type
                sleep(_PAGE_RETRY_BASE_DELAY * (2 ** attempt))
    raise last_exc


def fetch_minute_rows(ticker: str = "BTC", *, start_ms: int, end_ms=None, http_get=None,
                      now_ms=None, sleep=time.sleep) -> list:
    """Paginate public 1m klines over [start_ms, end_ms], snap to the UTC minute grid, drop any
    bar not yet closed as of `now_ms` (no lookahead) and anything outside [start_ms, end_ms].
    Returns canonical bar rows via BN1M_SPEC (kt=close). No key. Fail-soft: empty list if the
    pair/range yields no data. `http_get` defaults to the shared stdlib-urllib helper used by
    `fetch_klines` (dual-host failover lives there, not here — see module docstring). `sleep` is
    injectable (tests pass a recording no-op) — used both for the existing inter-page courtesy
    delay and the new per-page retry backoff below."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms > end_ms:
        raise ValueError(f"start_ms ({start_ms}) must be <= end_ms ({end_ms})")
    symbol = ticker.upper() + "USDT"
    kw = {} if http_get is None else {"http_get": http_get}
    by_bucket, cursor = {}, end_ms
    span_minutes = (end_ms - start_ms) // _MINUTE_MS + 1
    max_pages = max(1, (span_minutes // 1000) + 2)     # ~ enough pages to cover the range (1000 bars/page)
    for _ in range(max_pages):
        batch = _fetch_page_with_retry(symbol, end_ms=cursor, sleep=sleep, kw=kw)
        if not isinstance(batch, list) or not batch:
            break
        by_bucket.update(parse_klines_1m(batch, instrument_id=ticker))
        oldest = int(batch[0][0])
        if len(batch) < 1000 or oldest <= start_ms:
            break                                      # reached start of requested range / short page
        cursor = oldest - 1                             # next page ends just before this batch's oldest
        sleep(0.25)
    live_minute = (now_ms // _MINUTE_MS) * _MINUTE_MS
    raw = [row for b, row in sorted(by_bucket.items())
           if start_ms <= b < live_minute and b <= end_ms]
    return normalize_records(raw, BN1M_SPEC)


def _parse_args(argv):
    """Pure: argv (excluding the program name) -> {ticker, start_ms, end_ms}. `--start-ms` is
    REQUIRED (no relative-window default — see module docstring). `--end-ms` is optional (None
    means "now" once handed to fetch_minute_rows). `--ticker` defaults to BTC."""
    opts = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("--"):
            raise SystemExit(f"unexpected positional argument: {a!r} "
                             "(use --ticker/--start-ms/--end-ms)")
        key = a[2:]
        if i + 1 >= len(argv):
            raise SystemExit(f"missing value for --{key}")
        opts[key] = argv[i + 1]
        i += 2
    if "start-ms" not in opts:
        raise SystemExit("--start-ms is required (epoch ms, UTC) — 1m bar volume is too large to "
                         "default to a relative window; pass an explicit --start-ms (see module "
                         "docstring)")
    return {
        "ticker": opts.get("ticker", "BTC"),
        "start_ms": int(opts["start-ms"]),
        "end_ms": int(opts["end-ms"]) if "end-ms" in opts else None,
    }


def main() -> None:
    import os
    import sys

    from services.data.market_provider import ingest_provider_rows
    from services.data.persistent_store import SqlitePITStore
    parsed = _parse_args(sys.argv[1:])
    tk = parsed["ticker"]
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "crypto_binance_1m.db"), create=True)
    now_ms = int(time.time() * 1000)
    rows = fetch_minute_rows(tk, start_ms=parsed["start_ms"], end_ms=parsed["end_ms"], now_ms=now_ms)
    # strict=True (2026-07-09): this fetch feeds a rigorous strategy validation where the
    # strategy's own doc names gap-induced EMA/volume-ratio skew as a known risk (§11.4) — a
    # silently-accepted gap/duplicate/out-of-order bar would corrupt every indicator computed
    # from it, not just the affected minute. Fail loudly (FatalIngestionError) rather than
    # silently ingesting a compromised series; audit F4 flagged exactly this default-off gap.
    rep = ingest_provider_rows(rows, store, dataset_id=f"bn1m-{tk}", now=now_ms, time_unit="ms",
                               strict=True)
    span = (time.strftime("%Y-%m-%d %H:%M", time.gmtime(rows[0]["event_time"] / 1000)) + " .. " +
            time.strftime("%Y-%m-%d %H:%M", time.gmtime(rows[-1]["event_time"] / 1000))) if rows else "—"
    print(f"{tk}USDT 1m: {len(rows)} minute bars [{span}], ingested {rep.bars_ingested}")


if __name__ == "__main__":
    main()
