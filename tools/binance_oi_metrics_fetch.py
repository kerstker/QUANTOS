"""Out-of-gate fetcher for Binance USD-M futures **metrics** (open interest + positioning) history.

The funding-carry edge is established; this pulls a SECOND, orthogonal-candidate data source — perp
OPEN INTEREST and LONG/SHORT positioning — so the research core can test whether any positioning factor
adds incremental edge over carry. The live REST `futures/data/*` endpoints only retain ~30 days, but the
**Binance Data Vision archive** publishes a daily metrics ZIP per symbol going back years:

    https://data.binance.vision/data/futures/um/daily/metrics/<SYMBOL>/<SYMBOL>-metrics-<YYYY-MM-DD>.zip

Each ZIP is one CSV of 5-minute intraday snapshots (288/day) with columns:
    create_time, symbol, sum_open_interest, sum_open_interest_value,
    count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
    count_long_short_ratio, sum_taker_long_short_vol_ratio

We collapse each UTC day to its END-OF-DAY snapshot (the last 5-min row), which is knowable at the end of
day t — the same point-in-time convention as a daily close. Forward returns use t -> t+1, so a signal built
from day-t metrics is PIT-safe by construction.

Read-only DATA only: stdlib `urllib` GET of public archive ZIPs, no key, NO exchange/broker SDK, NO order
or transfer calls. Out-of-gate (governance scanner scopes to quantos/+services/); the boundary is still
machine-enforced by tests/governance/test_crypto_data_boundary.py. Wall-clock/network are allowed here.

Cache (resumable): data/cache/crypto_oi_metrics.json
    {"_meta": {ticker: {"start": day_ms, "through": day_ms}},
     "data": {ticker: {day_ms_str: {"oi","oi_value","top_ls","global_ls","taker_ls"}}}}
Re-running only fetches days not already cached. `main()` writes a snapshot alongside it.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.request
import zipfile

_DAY_MS = 86_400_000
_HOSTS = ["https://data.binance.vision"]
_CACHE = os.path.join("data", "cache", "crypto_oi_metrics.json")
_SNAP = os.path.join("data", "cache", "crypto_oi_metrics_snapshot.json")

# the liquid majors that (a) exist in our funding universe and (b) have a metrics archive — positioning
# data is only meaningful/reliable for liquid names, so we deliberately do NOT chase the small-cap tail.
DEFAULT_TICKERS = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
                   "LTC", "BCH", "TRX", "ATOM", "NEAR", "APT", "OP", "FIL", "ETC", "UNI",
                   "AAVE", "INJ", "XLM", "ICP"]

# end-of-day fields we keep (column index in the metrics CSV, 0-based)
_COLS = {"oi": 2, "oi_value": 3, "top_ls": 5, "global_ls": 6, "taker_ls": 7}
_PROBE_FLOOR_MS = 1_598_918_400_000  # 2020-09-01 UTC — no metrics archive predates this


def _http_get_bytes(url: str) -> bytes:
    """The only network primitive: read-only GET of a public archive ZIP, timeout-bounded."""
    last = None
    for host in _HOSTS:
        full = url if url.startswith("http") else host + url
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "quantos-research"}, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (https public data only)
                return resp.read()
        except Exception as exc:  # 404 (missing day) or transient — caller decides
            last = exc
    raise last if last is not None else RuntimeError("no host")


def _day_str(day_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day_ms / 1000))


def metrics_url(ticker: str, day_ms: int) -> str:
    sym = ticker.upper() + "USDT"
    return f"/data/futures/um/daily/metrics/{sym}/{sym}-metrics-{_day_str(day_ms)}.zip"


def parse_metrics_zip(raw: bytes) -> dict:
    """Parse one daily metrics ZIP -> the END-OF-DAY (last) row's kept fields. {} if empty/garbage.

    CSV parsed by hand (no `csv` import) — the schema is fixed and comma-clean. We take the LAST data
    row = the day's final 5-min snapshot (eod), which is what is knowable at the day's close."""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = [n for n in zf.namelist() if n.endswith(".csv")]
    if not names:
        return {}
    text = zf.open(names[0]).read().decode("utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {}
    last = lines[-1].split(",")
    need = max(_COLS.values())
    if len(last) <= need:
        return {}
    out = {}
    for name, idx in _COLS.items():
        try:
            out[name] = float(last[idx])
        except (ValueError, IndexError):
            out[name] = None
    return out


def find_start_ms(ticker: str, *, http_get_bytes=_http_get_bytes, now_ms=None) -> int | None:
    """Coarse forward month-scan for the earliest available metrics day (one-time per ticker)."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    day = (_PROBE_FLOOR_MS // _DAY_MS) * _DAY_MS
    end = (now_ms // _DAY_MS) * _DAY_MS
    while day < end:
        try:
            http_get_bytes(metrics_url(ticker, day))
            return day
        except Exception:
            day += 30 * _DAY_MS  # month-ish step until first hit
    return None


def find_end_ms(ticker: str, *, http_get_bytes=_http_get_bytes, now_ms=None) -> int | None:
    """Coarse backward month-scan for the LAST available metrics day — bounds DELISTED coins so we
    do not fetch thousands of post-delisting 404 days. For a live coin this returns ~today."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    day = (now_ms // _DAY_MS) * _DAY_MS - _DAY_MS
    floor = (_PROBE_FLOOR_MS // _DAY_MS) * _DAY_MS
    while day > floor:
        try:
            http_get_bytes(metrics_url(ticker, day))
            return day
        except Exception:
            day -= 30 * _DAY_MS
    return None


def fetch_ticker_days(ticker: str, start_ms: int, end_ms: int, present: set, *,
                      http_get_bytes=_http_get_bytes, sleep: float = 0.0, max_workers: int = 12) -> dict:
    """Fetch every UTC day in [start_ms, end_ms) not already in `present`. Skips missing (404) days.
    Returns {day_ms: {fields}}. PIT-safe: each day collapses to its eod snapshot.

    The Data Vision archive has high per-request (TLS-handshake) latency, so days are fetched
    CONCURRENTLY via a stdlib thread pool — `concurrent.futures` adds no network/trade/credential
    surface, it only overlaps the wait. Result is a dict, so worker ordering is irrelevant and the
    output is deterministic for fixed inputs. max_workers<=1 runs the serial path (used by tests)."""
    days = [d for d in range(((start_ms // _DAY_MS) * _DAY_MS), ((end_ms // _DAY_MS) * _DAY_MS), _DAY_MS)
            if d not in present]

    def _one(day):
        try:
            rec = parse_metrics_zip(http_get_bytes(metrics_url(ticker, day)))
            return (day, rec or None)
        except Exception:
            return (day, None)  # missing day or transient; leave a gap (panel handles None)

    out = {}
    if max_workers <= 1:
        for day in days:
            d, rec = _one(day)
            if rec:
                out[d] = rec
            if sleep:
                time.sleep(sleep)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for d, rec in ex.map(_one, days):
                if rec:
                    out[d] = rec
    return out


def _load_cache() -> dict:
    if os.path.exists(_CACHE):
        return json.load(open(_CACHE, encoding="utf-8"))
    return {"_meta": {}, "data": {}}


def main(tickers=None, *, sleep: float = 0.08, bounded: bool = False) -> None:
    """`bounded=True` probes each ticker's LAST available day (find_end_ms) and stops there — use it
    for DELISTED coins so we don't fetch thousands of post-delisting 404 days. Live coins are unaffected."""
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    tickers = tickers or DEFAULT_TICKERS
    cache = _load_cache()
    meta, data = cache.setdefault("_meta", {}), cache.setdefault("data", {})
    now_ms = int(time.time() * 1000)
    today_ms = (now_ms // _DAY_MS) * _DAY_MS  # drop the live (today) day -> PIT-safe

    for ti, ticker in enumerate(tickers):
        sym_days = data.setdefault(ticker, {})
        present = {int(k) for k in sym_days}
        start = meta.get(ticker, {}).get("start")
        if start is None:
            start = find_start_ms(ticker, now_ms=now_ms)
            if start is None:
                print(f"[{ti+1}/{len(tickers)}] {ticker}: no metrics archive found")
                meta[ticker] = {"start": None, "through": None}
                continue
        end_ms = today_ms
        if bounded:  # delisted coin: bound the fetch to its last available day + a small buffer
            last = find_end_ms(ticker, now_ms=now_ms)
            if last is not None:
                end_ms = min(today_ms, last + 35 * _DAY_MS)
        fresh = fetch_ticker_days(ticker, start, end_ms, present, sleep=sleep)
        for k, v in fresh.items():
            sym_days[str(k)] = v
        all_days = sorted(int(k) for k in sym_days)
        meta[ticker] = {"start": start, "through": all_days[-1] if all_days else None}
        json.dump(cache, open(_CACHE, "w", encoding="utf-8"))  # checkpoint each ticker (resumable)
        print(f"[{ti+1}/{len(tickers)}] {ticker}: +{len(fresh)} new, {len(sym_days)} total days"
              f" ({_day_str(all_days[0])}..{_day_str(all_days[-1])})" if all_days else "")

    covered = {tk: len(d) for tk, d in data.items() if d}
    json.dump({"generated_at": int(time.time()), "provider": "Binance Data Vision futures metrics (perp)",
               "fields": list(_COLS), "n_tickers": len(covered),
               "days_per_ticker": covered}, open(_SNAP, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_CACHE} ({len(covered)} tickers) and {_SNAP}")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args or None)
