"""A-share daily kline batch fetcher — iFinD datasource CLI (out-of-gate, tools/ only).

Fallback for `tools/ashare_kline_fetch.py` (Wind gateway, quota-throttled). Fetches the
~129 universe stocks missing from the PIT store via the ALREADY-AUTHENTICATED iFinD
plugin CLI (`python3 scripts/ifind_tool.py call --api-name ifind_get_price ...`),
mirrors each raw group CSV to `data/cache/ashare_ifind_raw/`, and ingests bars into
`data/cache/ashare_daily.db` through the same fail-closed ingestion seam
(`services.data.market_provider.ingest_provider_rows`, time_unit="yyyymmdd").

iFinD constraints (verified 2026-07-23):
- max 3 tickers per call, max 3-year range per call → groups of 3 tickers share
  2 range calls: 2021-01-01→2023-12-31 and 2024-01-01→2026-07-23.
- Output CSV columns: open,high,low,close,volume,thscode,time(yyyyMMdd),thsname_cn,...
  NO amt/turnover column — same as the Wind-sourced PIT reality (the canonical Bar
  contract persists OHLCV only; `amt` is a downstream volume×typical-price proxy).

Governance: network/subprocess lives HERE in tools/ only; services/ is untouched.
Read-only market data — no orders, no broker SDK, no credentials.

Idempotent & resumable: missing = universe codes − distinct PIT keys (live check);
progress checkpoint `data/cache/ashare_ifind_progress.json`; ingestion dedups on
(key, event_time, knowledge_time) via INSERT OR IGNORE.

Source-mix caveat (also recorded in the snapshot): Wind-sourced bars are 前复权 from
Wind; iFinD-sourced bars are iFinD forward-adjusted. Adjustment bases may differ, but
each stock's series comes from exactly ONE source, so per-stock return series are
internally consistent; cross-source price LEVELS are not comparable.
"""
from __future__ import annotations

import ast
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from services.data.market_provider import ingest_provider_rows  # noqa: E402
from services.data.persistent_store import SqlitePITStore  # noqa: E402

IFIND_DIR = (r"C:\Users\Administrator\AppData\Roaming\kimi-desktop\daimon-share"
             r"\daimon\runtime\kimi-code\home\plugins\managed\ifind")

UNIVERSE_PATH = os.path.join(REPO_ROOT, "data", "stocks", "universe.json")
DB_PATH = os.path.join(REPO_ROOT, "data", "stocks", "stocks.db")
RAW_DIR = os.path.join(REPO_ROOT, "data", "stocks", "raw", "ifind")
PROGRESS_PATH = os.path.join(REPO_ROOT, "data", "stocks", "ifind_fetch_progress.json")
SNAPSHOT_PATH = os.path.join(REPO_ROOT, "data", "stocks", "snapshots", "klines_snapshot.json")
WIND_PROGRESS_PATH = os.path.join(REPO_ROOT, "data", "stocks", "wind_fetch_progress.json")

# (start_date, end_date) ISO — max 3-year range per call, disjoint, covering the
# full Wind window 2021-01-01 → 2026-07-23.
RANGES = (("2021-01-01", "2023-12-31"), ("2024-01-01", "2026-07-23"))
END_INT = 20260723
BATCH = 3                                # max tickers per iFinD call

CLI_TIMEOUT_S = 120
RETRIES = 2                              # retries AFTER the first attempt
PACE_S = 1.5                             # polite pacing between calls (1-2s)
RETRY_BACKOFF_S = (20.0, 35.0)           # backoff before retry 1 / retry 2
RATELIMIT_BACKOFF_S = 35.0

SNAPSHOT_NOTES = {
    "amt": ("amt (CNY turnover) is NOT persisted in the PIT payload for ANY source "
            "(canonical Bar contract is OHLCV only; iFinD CSV has no amt column). "
            "Downstream amt = volume x typical-price proxy."),
    "source_mix": ("Per-stock single source: 'wind' bars are Wind 前复权, 'ifind' bars "
                   "are iFinD forward-adjusted daily. Adjustment bases may differ, so "
                   "cross-source price LEVELS are not comparable, but each stock's "
                   "series is internally consistent (one source per stock)."),
}


class RateLimitError(RuntimeError):
    """Provider quota exhaustion — back off hard, then keep grinding."""


# --------------------------------------------------------------------------- #
# CLI runner + output parsing
# --------------------------------------------------------------------------- #

def default_ifind_runner(params_json: str) -> str:
    """Run the iFinD plugin CLI for one get_price call; return raw stdout. The ONLY
    subprocess seam in this module — tests inject a fake here. The tool writes the
    full CSV to params.file_path itself; stdout is a python-repr envelope.
    `python3` is resolved via PATH (shutil.which): on Windows CreateProcess would
    otherwise prefer the CALLING interpreter's own directory, which may be a python
    without the plugin's agent-gw dependency."""
    py = shutil.which("python3") or "python3"
    proc = subprocess.run(
        [py, "scripts/ifind_tool.py", "call",
         "--api-name", "ifind_get_price", "--params-json", params_json],
        cwd=IFIND_DIR, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT_S,
    )
    if proc.returncode != 0:
        blob = (proc.stdout or "") + (proc.stderr or "")
        if _is_ratelimit(blob):
            raise RateLimitError(f"ifind cli exit={proc.returncode} (quota): {blob[-200:]!r}")
        raise RuntimeError(f"ifind cli exit={proc.returncode}: {blob[-300:]!r}")
    return proc.stdout


def _is_ratelimit(blob: str) -> bool:
    """True only for genuine quota signals. A bare '429' is NOT sufficient — the
    data_preview embeds CSV rows whose volumes/prices can contain the digits 429."""
    s = blob.lower()
    if ("too many requests" in s or "rate limit" in s or "ratelimit" in s
            or "限流" in blob or "频繁" in blob):
        return True
    return bool(re.search(r"(?:http|status|code|error)[^0-9]{0,12}429\b", s))


def parse_tool_stdout(stdout: str) -> dict:
    """Parse the iFinD CLI stdout -> {"is_success", "total_rows"}. stdout is a
    python-repr dict {'type':'text','text':'<json>'}; the inner JSON carries
    is_success ('...# HTTP get_price OK...') and a data_preview whose header line
    reports '(50 rows here, N rows in total)'. Raises RateLimitError on quota
    messages, RuntimeError on tool-reported failure."""
    blob = stdout or ""
    if _is_ratelimit(blob):
        raise RateLimitError(f"ifind quota: {blob[-200:]!r}")
    payload = None
    try:
        outer = ast.literal_eval(blob.strip())
        if isinstance(outer, dict) and isinstance(outer.get("text"), str):
            payload = json.loads(outer["text"])
    except (ValueError, SyntaxError):
        payload = None
    if payload is None:                          # fallback: maybe stdout IS the json
        payload = json.loads(blob)
    is_success = str(payload.get("is_success", ""))
    total = None
    preview = str(payload.get("data_preview", ""))
    marker = "rows in total)"
    if marker in preview:
        try:
            total = int(preview.rsplit(",", 1)[1].split("rows in total")[0].strip())
        except (ValueError, IndexError):
            total = None
    if is_success and "OK" not in is_success:
        raise RuntimeError(f"ifind tool failure: {is_success[:200]!r}")
    return {"is_success": is_success, "total_rows": total}


def read_ifind_csv(path: str) -> list:
    """Read an iFinD get_price CSV -> list of dict rows (strings), keyed by header.
    Rows without thscode/time are dropped."""
    with open(path, newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f) if r.get("thscode") and r.get("time")]


# --------------------------------------------------------------------------- #
# Batching / canonical conversion
# --------------------------------------------------------------------------- #

def batches_of(items: list, n: int = BATCH) -> list:
    """Split `items` into consecutive chunks of at most n (3 tickers/call)."""
    return [items[i:i + n] for i in range(0, len(items), n)]


def ifind_rows_to_canonical(rows: list, *, end_int: int = END_INT) -> tuple:
    """iFinD CSV rows -> canonical ingest_provider_rows dicts, MATCHING the Wind
    fetcher's convention (tools/ashare_kline_fetch.rows_to_canonical): key=thscode,
    event_time = knowledge_time = time as YYYYMMDD int (EOD-known), OHLCV only — no
    amt in the PIT payload. Dedup by (thscode, time) first-wins; rows dated after
    end_int are DROPPED (future guard). Returns (canonical_rows, dropped_future)."""
    seen = set()
    out, dropped = [], 0
    for r in sorted(rows, key=lambda r: (r["thscode"], r["time"])):
        dedup_key = (r["thscode"], r["time"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        et = int(r["time"])
        if et > end_int:
            dropped += 1
            continue
        out.append({
            "instrument_id": r["thscode"],
            "event_time": et,
            "knowledge_time": et,
            "open": r["open"], "high": r["high"], "low": r["low"],
            "close": r["close"], "volume": r.get("volume", "0"),
        })
    return out, dropped


# --------------------------------------------------------------------------- #
# Fetch one group (3 tickers × 2 range calls), with retries
# --------------------------------------------------------------------------- #

def _raw_path(raw_dir: str, codes: list, start: str, end: str) -> str:
    tag = "_".join(c.replace(".", "") for c in codes)
    return os.path.join(raw_dir, f"ifind_{start.replace('-', '')}_"
                                 f"{end.replace('-', '')}_{tag}.csv")


def fetch_group_rows(codes: list, *, runner=default_ifind_runner,
                     raw_dir: str = RAW_DIR, retries: int = RETRIES,
                     sleep=time.sleep) -> list:
    """Fetch all iFinD CSV rows for a group of ≤3 tickers over both RANGES.
    Each range call writes its raw CSV mirror under raw_dir. Raises after
    exhausted retries."""
    os.makedirs(raw_dir, exist_ok=True)
    all_rows: list = []
    for start, end in RANGES:
        file_path = _raw_path(raw_dir, codes, start, end)
        params = json.dumps({
            "ticker": ",".join(codes),
            "start_date": start,
            "end_date": end,
            "file_path": file_path.replace(os.sep, "/"),
        })
        last = None
        for attempt in range(retries + 1):
            try:
                parse_tool_stdout(runner(params))
                rows = read_ifind_csv(file_path)
                if not rows:
                    raise RuntimeError(f"empty csv for {codes} {start}->{end}")
                all_rows.extend(rows)
                last = None
                break
            except Exception as exc:             # timeout / exit!=0 / parse / empty
                last = exc
                if attempt < retries:
                    sleep(RATELIMIT_BACKOFF_S if isinstance(exc, RateLimitError)
                          else RETRY_BACKOFF_S[attempt])
        if last is not None:
            raise last
        sleep(PACE_S)
    return all_rows


# --------------------------------------------------------------------------- #
# Batch driver (resumable)
# --------------------------------------------------------------------------- #

def _load_progress(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"done": [], "failed": {}}


def _save_progress(path: str, prog: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def pit_keys(store) -> set:
    """Distinct instrument keys already landed in the PIT store (live check)."""
    rows = store._conn.execute("SELECT DISTINCT key FROM bars").fetchall()
    return {r[0] for r in rows}


def missing_codes(universe: list, store) -> list:
    """Universe codes minus codes already present in the PIT store."""
    have = pit_keys(store)
    return [it["code"] for it in universe if it["code"] not in have]


def run_batch(codes: list, store, *, raw_dir=RAW_DIR, progress_path=PROGRESS_PATH,
              runner=default_ifind_runner, sleep=time.sleep,
              max_groups=None, log=print) -> dict:
    """Fetch + ingest every code in `codes` (groups of 3 × 2 range calls).
    Resumable: codes already in the checkpoint's done list are skipped; per-row
    ingestion dedups regardless. `max_groups` caps groups per call (chunked runs
    under a wall-clock budget). On RateLimitError the batch KEEPS GRINDING (the
    retry backoff already waited) — failures are recorded and resumed later.
    Returns {"done", "failed", "stats"}."""
    prog = _load_progress(progress_path)
    done = set(prog["done"])
    failed = dict(prog["failed"])
    stats = {}
    t0 = time.time()
    todo = [c for c in codes if c not in done]
    groups = batches_of(todo)
    if max_groups is not None:
        groups = groups[:max_groups]
    for gi, group in enumerate(groups):
        try:
            rows = fetch_group_rows(group, runner=runner, raw_dir=raw_dir, sleep=sleep)
            canonical, dropped_future = ifind_rows_to_canonical(rows)
            by_code: dict = {}
            for cr in canonical:
                by_code.setdefault(cr["instrument_id"], []).append(cr)
            for code in group:
                crows = by_code.get(code, [])
                if not crows:
                    failed[code] = "no rows in provider output"
                    continue
                rep = ingest_provider_rows(crows, store, dataset_id=f"ashare-{code}",
                                           time_unit="yyyymmdd")
                stats[code] = {
                    "rows": len(crows), "ingested": rep.bars_ingested,
                    "duplicates": rep.duplicates, "rejected": len(rep.rejected),
                    "dropped_future": dropped_future,
                    "date_min": min(c["event_time"] for c in crows),
                    "date_max": max(c["event_time"] for c in crows),
                }
                done.add(code)
                failed.pop(code, None)
        except Exception as exc:
            for code in group:
                if code not in done:
                    failed[code] = f"{type(exc).__name__}: {exc}"[:300]
        prog = {"done": sorted(done), "failed": failed}
        _save_progress(progress_path, prog)
        log(f"[group {gi + 1}/{len(groups)}] {','.join(group)} "
            f"done={len(done)} failed={len(failed)} elapsed={time.time() - t0:.0f}s")
    return {"done": sorted(done), "failed": failed, "stats": stats}


# --------------------------------------------------------------------------- #
# Snapshot (full rewrite, shared format with the Wind fetcher + source tagging)
# --------------------------------------------------------------------------- #

def write_snapshot(store, universe: list, ifind_done, path: str = SNAPSHOT_PATH,
                   fetched_at: str = "") -> dict:
    """Per-stock PIT counts + date coverage + data source + totals -> snapshot JSON.
    Superset of tools/ashare_kline_fetch.write_snapshot: each stock gains
    `source` ('wind' | 'ifind' | 'missing'); top level gains per-source counts and
    honesty notes (amt proxy + source-mix adjustment caveat)."""
    ifind_done = set(ifind_done)
    per_stock = {}
    total_rows = 0
    for item in universe:
        code = item["code"]
        bars = store.fetch_bars_pit(code, -10**18, 10**18)
        if bars:
            per_stock[code] = {
                "rows": len(bars),
                "date_min": min(b.event_time for b in bars),
                "date_max": max(b.event_time for b in bars),
                "source": "ifind" if code in ifind_done else "wind",
            }
            total_rows += len(bars)
        else:
            per_stock[code] = {"rows": 0, "date_min": None, "date_max": None,
                               "source": "missing"}
    n_ifind = sum(1 for v in per_stock.values() if v["source"] == "ifind")
    n_wind = sum(1 for v in per_stock.values() if v["source"] == "wind")
    snap = {"fetched_at": fetched_at,
            "universe_size": len(universe),
            "stocks_with_data": n_ifind + n_wind,
            "wind_stocks": n_wind,
            "ifind_stocks": n_ifind,
            "total_rows": total_rows,
            "notes": SNAPSHOT_NOTES,
            "per_stock": per_stock}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return snap


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-groups", type=int, default=None,
                    help="process at most N groups this call (resumable chunks)")
    ap.add_argument("--snapshot-only", action="store_true",
                    help="skip fetching; only rewrite the snapshot from PIT")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="skip the (slow) full-PIT snapshot rewrite for chunked calls")
    args = ap.parse_args()
    with open(UNIVERSE_PATH, encoding="utf-8") as f:
        universe = json.load(f)["stocks"]
    store = SqlitePITStore(DB_PATH, create=False)
    t0 = time.time()
    result = {"done": [], "failed": {}}
    if not args.snapshot_only:
        codes = missing_codes(universe, store)
        print(f"missing vs PIT: {len(codes)} codes "
              f"({len(batches_of(codes))} groups of <=3)")
        result = run_batch(codes, store, max_groups=args.max_groups)
    prog = _load_progress(PROGRESS_PATH)
    if args.no_snapshot and not args.snapshot_only:
        print(f"\n== chunk finished in {time.time() - t0:.0f}s (snapshot deferred) ==")
        print(f"ifind done: {len(prog['done'])}  failed: {len(prog['failed'])}")
        return
    snap = write_snapshot(store, universe, prog["done"],
                          fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    print(f"\n== finished in {time.time() - t0:.0f}s ==")
    print(f"stocks with data: {snap['stocks_with_data']}/{snap['universe_size']}  "
          f"(wind={snap['wind_stocks']} ifind={snap['ifind_stocks']})  "
          f"total PIT rows: {snap['total_rows']}")
    if prog["failed"]:
        print("FAILURES:")
        for code, reason in sorted(prog["failed"].items()):
            print(f"  {code}: {reason}")


if __name__ == "__main__":
    main()
