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
REST gap-filled placeholder days (publication lag) carry an extra record field
"source": "REST_GAP_FILL"; archive rows have no such field (archive is canonical, see
tools/binance_rest_gap_fill.py for the precedence rule).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.request
import zipfile

from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       archive_response)
from tools.binance_rest_gap_fill import (PROVENANCE_REST_GAP_FILL,
                                         REST_RETENTION_DAYS,
                                         fetch_oi_days_rest)
from tools.cache_io import atomic_write_json
from tools.source_failures import classify_source_failure, source_failure_detail

_DAY_MS = 86_400_000
_HOSTS = ["https://data.binance.vision"]
_CACHE = os.path.join("data", "cache", "crypto_oi_metrics.json")
_SNAP = os.path.join("data", "cache", "crypto_oi_metrics_snapshot.json")
_CACHE_INTRADAY = os.path.join("data", "cache", "crypto_oi_intraday.json")
_SNAP_INTRADAY = os.path.join("data", "cache", "crypto_oi_intraday_snapshot.json")

# the liquid majors that (a) exist in our funding universe and (b) have a metrics archive — positioning
# data is only meaningful/reliable for liquid names, so we deliberately do NOT chase the small-cap tail.
DEFAULT_TICKERS = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
                   "LTC", "BCH", "TRX", "ATOM", "NEAR", "APT", "OP", "FIL", "ETC", "UNI",
                   "AAVE", "INJ", "XLM", "ICP"]

# The forward-paper clock intentionally uses the existing major-book shape,
# rather than making the small-cap tail a prerequisite for evidence.  This is
# a fixed, reviewable L2 scope: the historical model card used MATIC, which is
# no longer in the active cache, so current BCH is the compatible replacement.
# ``DEFAULT_TICKERS`` remains the wider liquid-history universe for research
# and survivorship diagnostics; callers must opt into this narrower scope.
FORWARD_MAJOR_TICKERS = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
    "BCH", "DOT", "LTC", "UNI", "LINK", "ATOM",
)

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


def _metrics_input_rows(raw: bytes) -> int:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = [name for name in zf.namelist() if name.endswith(".csv")]
        if len(names) != 1:
            return 0
        lines = [
            line for line in zf.read(names[0]).decode("utf-8").splitlines()
            if line.strip()
        ]
        return max(0, len(lines) - 1)
    except (OSError, UnicodeError, zipfile.BadZipFile):
        return 0


def _csv_floats(text: str, idx: int) -> list:
    """All finite floats in column `idx` of the metrics CSV (skips header + bad cells)."""
    out = []
    for ln in text.splitlines()[1:]:
        parts = ln.split(",")
        if len(parts) > idx:
            try:
                out.append(float(parts[idx]))
            except ValueError:
                pass
    return out


def parse_metrics_zip_intraday(raw: bytes) -> dict:
    """Parse one daily metrics ZIP -> INTRADAY-DERIVED daily features from all 288 5-min snapshots
    (dynamics the end-of-day value discards). All computed from within-day-t data only -> PIT-safe as a
    daily feature (knowable at the close of day t, used to predict t->t+1). Hand-rolled stats (no
    `statistics` import). {} if too few rows."""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = [n for n in zf.namelist() if n.endswith(".csv")]
    if not names:
        return {}
    text = zf.open(names[0]).read().decode("utf-8")
    oi = _csv_floats(text, _COLS["oi"])
    gls = _csv_floats(text, _COLS["global_ls"])
    tak = _csv_floats(text, _COLS["taker_ls"])
    if len(oi) < 12 or len(gls) < 12:
        return {}

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    oi_mean = _mean(oi) or 1.0
    surge = 0.0
    for a, b in zip(oi, oi[1:]):
        if a > 0:
            surge = max(surge, abs(b - a) / a)
    return {
        "oi_range": (max(oi) - min(oi)) / oi_mean,                 # intraday OI swing (vol of positioning size)
        "oi_trend": (oi[-1] - oi[0]) / oi[0] if oi[0] else 0.0,    # net intraday OI build (+) / unwind (-)
        "gls_trend": gls[-1] - gls[0],                             # net intraday shift in retail positioning
        "gls_range": max(gls) - min(gls),                          # intraday positioning swing
        "taker_mean": _mean(tak),                                  # mean intraday aggressor flow (vs eod print)
        "oi_surge": surge,                                         # largest single 5-min OI jump (flow shock)
    }


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
        except Exception as exc:
            if classify_source_failure(exc) != "NOT_FOUND":
                raise
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
        except Exception as exc:
            if classify_source_failure(exc) != "NOT_FOUND":
                raise
            day -= 30 * _DAY_MS
    return None


def fetch_ticker_days(ticker: str, start_ms: int, end_ms: int, present: set, *,
                      http_get_bytes=_http_get_bytes, sleep: float = 0.0, max_workers: int = 12,
                      parse_fn=parse_metrics_zip, failure_sink=None,
                      archive_root=None, observed_at_ms=None) -> dict:
    """Fetch every UTC day in [start_ms, end_ms) not already in `present`. Skips missing (404) days.
    Returns {day_ms: {fields}}. PIT-safe: each day collapses to its eod snapshot.

    The Data Vision archive has high per-request (TLS-handshake) latency, so days are fetched
    CONCURRENTLY via a stdlib thread pool — `concurrent.futures` adds no network/trade/credential
    surface, it only overlaps the wait. Result is a dict, so worker ordering is irrelevant and the
    output is deterministic for fixed inputs. max_workers<=1 runs the serial path (used by tests)."""
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    days = [d for d in range(((start_ms // _DAY_MS) * _DAY_MS), ((end_ms // _DAY_MS) * _DAY_MS), _DAY_MS)
            if d not in present]

    def _one(day):
        raw = None
        path = metrics_url(ticker, day)
        try:
            raw = http_get_bytes(path)
            rec = parse_fn(raw)
            input_rows = _metrics_input_rows(raw)
            if not rec:
                exc = ValueError("metrics archive parsed to an empty record")
                if archive_root is not None:
                    archive_response(
                        root=archive_root,
                        payload=raw,
                        source_uri=_HOSTS[0] + path,
                        provider="Binance Data Vision USD-M futures metrics",
                        dataset_id="oi_metrics",
                        instrument_id=ticker,
                        observed_at_ms=int(observed_at_ms),
                        representation="HTTP_BODY_BYTES",
                        parse_diagnostics={
                            "status": "REJECTED", "input_rows": input_rows,
                            "accepted_rows": 0,
                            "rejected_rows": max(1, input_rows),
                            "duplicate_rows": 0, "event_time_ms": day,
                            "error_type": type(exc).__name__,
                        },
                    )
                return (day, None, source_failure_detail(
                    ticker, exc, kind="QUALITY", day_ms=day, phase="parse"))
            if archive_root is not None:
                archive_response(
                    root=archive_root,
                    payload=raw,
                    source_uri=_HOSTS[0] + path,
                    provider="Binance Data Vision USD-M futures metrics",
                    dataset_id="oi_metrics",
                    instrument_id=ticker,
                    observed_at_ms=int(observed_at_ms),
                    representation="HTTP_BODY_BYTES",
                    parse_diagnostics={
                        "status": "ACCEPTED", "input_rows": input_rows,
                        "accepted_rows": 1, "rejected_rows": 0,
                        "duplicate_rows": 0, "event_time_ms": day,
                    },
                )
            return (day, rec, None)
        except Exception as exc:
            if archive_root is not None and raw is not None:
                try:
                    archive_response(
                        root=archive_root,
                        payload=raw,
                        source_uri=_HOSTS[0] + path,
                        provider="Binance Data Vision USD-M futures metrics",
                        dataset_id="oi_metrics",
                        instrument_id=ticker,
                        observed_at_ms=int(observed_at_ms),
                        representation="HTTP_BODY_BYTES",
                        parse_diagnostics={
                            "status": "REJECTED",
                            "input_rows": _metrics_input_rows(raw),
                            "accepted_rows": 0, "rejected_rows": 1,
                            "duplicate_rows": 0, "event_time_ms": day,
                            "error_type": type(exc).__name__,
                        },
                    )
                except Exception as archive_exc:
                    exc = archive_exc
            return (day, None, source_failure_detail(
                ticker, exc, day_ms=day, phase="fetch"))

    out = {}
    if max_workers <= 1:
        for day in days:
            d, rec, failure = _one(day)
            if failure is not None and failure_sink is not None:
                failure_sink.append(failure)
            if rec:
                out[d] = rec
            if sleep:
                time.sleep(sleep)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for d, rec, failure in ex.map(_one, days):
                if failure is not None and failure_sink is not None:
                    failure_sink.append(failure)
                if rec:
                    out[d] = rec
    return out


def _load_cache(path) -> dict:
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {"_meta": {}, "data": {}}


def main(tickers=None, *, sleep: float = 0.08, bounded: bool = False,
         parse_fn=parse_metrics_zip, cache_path=_CACHE, snap_path=_SNAP,
         http_get_bytes=_http_get_bytes, archive_root=None, rest_fetch=None,
         observed_at_ms=None) -> dict:
    """`bounded=True` probes each ticker's LAST available day (find_end_ms) and stops there — use it
    for DELISTED coins so we don't fetch thousands of post-delisting 404 days. Live coins are unaffected.
    `parse_fn`/`cache_path` select what to extract per day (eod fields vs intraday-derived features) and
    where to cache — so the intraday feature pull reuses the same fetch infrastructure.
    `rest_fetch` (None = disabled, test-hermetic default) enables latest-days REST gap-fill for the
    Data Vision publication lag (tools/binance_rest_gap_fill.py): after the archive pass, recent
    CLOSED days still absent are filled via Binance futures REST and marked source="REST_GAP_FILL";
    placeholder days are re-attempted from the archive every run and replaced once published
    (archive is canonical — a REST placeholder never overwrites an archive row)."""
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    tickers = tickers or DEFAULT_TICKERS
    cache = _load_cache(cache_path)
    meta, data = cache.setdefault("_meta", {}), cache.setdefault("data", {})
    now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        observed_at_ms = now_ms
    today_ms = (now_ms // _DAY_MS) * _DAY_MS  # drop the live (today) day -> PIT-safe
    failure_details = []
    rest_filled = 0

    for ti, ticker in enumerate(tickers):
        sym_days = data.setdefault(ticker, {})
        # REST_GAP_FILL placeholder days are NOT "present": the archive re-attempts them every
        # run and supersedes the placeholder once Data Vision publishes the day (canonical).
        present = {int(k) for k, rec in sym_days.items()
                   if not (isinstance(rec, dict)
                           and rec.get("source") == PROVENANCE_REST_GAP_FILL)}
        start = meta.get(ticker, {}).get("start")
        if start is None:
            start = find_start_ms(ticker, http_get_bytes=http_get_bytes, now_ms=now_ms)
            if start is None:
                print(f"[{ti+1}/{len(tickers)}] {ticker}: no metrics archive found")
                meta[ticker] = {"start": None, "through": None}
                failure_details.append({
                    "ticker": ticker, "kind": "NOT_FOUND",
                    "error_type": "ArchiveNotFound",
                    "message": "no metrics archive found", "phase": "find_start",
                })
                continue
        end_ms = today_ms
        if bounded:  # delisted coin: bound the fetch to its last available day + a small buffer
            last = find_end_ms(ticker, http_get_bytes=http_get_bytes, now_ms=now_ms)
            if last is not None:
                end_ms = min(today_ms, last + 35 * _DAY_MS)
        fresh = fetch_ticker_days(
            ticker, start, end_ms, present, sleep=sleep, parse_fn=parse_fn,
            http_get_bytes=http_get_bytes, failure_sink=failure_details,
            archive_root=archive_root, observed_at_ms=observed_at_ms)
        for k, v in fresh.items():
            sym_days[str(k)] = v
        if rest_fetch is not None:
            # Latest-days REST gap-fill (publication lag): only CLOSED days inside REST
            # retention that are entirely absent; placeholders never overwrite archive rows.
            rest_floor = (max(int(start), today_ms - REST_RETENTION_DAYS * _DAY_MS)
                          // _DAY_MS) * _DAY_MS
            rest_days = [day for day in range(rest_floor, today_ms, _DAY_MS)
                         if str(day) not in sym_days]
            if rest_days:
                rest_fresh = rest_fetch(
                    ticker, rest_days, now_ms=now_ms,
                    archive_root=archive_root, observed_at_ms=observed_at_ms,
                    failure_sink=failure_details)
                for k, v in rest_fresh.items():
                    if str(k) in sym_days:
                        continue
                    sym_days[str(k)] = dict(v, source=PROVENANCE_REST_GAP_FILL)
                rest_filled += len(rest_fresh)
        all_days = sorted(int(k) for k in sym_days)
        meta[ticker] = {"start": start, "through": all_days[-1] if all_days else None}
        atomic_write_json(cache_path, cache)  # checkpoint each ticker (resumable)
        print(f"[{ti+1}/{len(tickers)}] {ticker}: +{len(fresh)} new, {len(sym_days)} total days"
              f" ({_day_str(all_days[0])}..{_day_str(all_days[-1])})" if all_days else "")

    covered = {tk: len(d) for tk, d in data.items() if d}
    atomic_write_json(
        snap_path,
        {"generated_at": int(time.time()), "provider": "Binance Data Vision futures metrics (perp)",
         "n_tickers": len(covered), "days_per_ticker": covered},
        indent=2,
    )
    print(f"-> wrote {cache_path} ({len(covered)} tickers) and {snap_path}")
    ordered = sorted(
        failure_details,
        key=lambda item: (str(item.get("ticker")), int(item.get("day_ms", -1)),
                          str(item.get("kind"))),
    )
    return {
        "failure_details": ordered,
        "rest_gap_fill_days": rest_filled,
        "failed_tickers": sorted({
            item["ticker"] for item in ordered if item.get("kind") != "NOT_FOUND"
        }),
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--intraday":   # pull INTRADAY-derived daily features instead of eod fields
        main(args[1:] or None, parse_fn=parse_metrics_zip_intraday,
             cache_path=_CACHE_INTRADAY, snap_path=_SNAP_INTRADAY,
             archive_root=DEFAULT_RAW_ARCHIVE)
    else:
        main(args or None, archive_root=DEFAULT_RAW_ARCHIVE,
             rest_fetch=fetch_oi_days_rest)
