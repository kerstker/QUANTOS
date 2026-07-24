"""A-share daily kline batch fetcher — Wind gateway CLI (out-of-gate, tools/ only).

Downloads daily klines for the 249-stock micro-cap universe
(`data/cache/ashare_universe.json`) via the ALREADY-AUTHENTICATED Wind gateway CLI
(node scripts/cli.mjs call stock_data get_stock_kline ...), mirrors the full raw CSV
to `data/cache/ashare_klines_raw/{code}.csv`, and ingests the bars into the PIT store
`data/cache/ashare_daily.db` through the shared fail-closed ingestion seam
(`services.data.market_provider.ingest_provider_rows`, time_unit="yyyymmdd").

Governance: network/subprocess lives HERE in tools/ only; services/ is untouched and
never imports requests/urllib/subprocess. Data is READ-ONLY market data — no orders,
no broker SDK, no credentials beyond the gateway's own session.

Idempotent & resumable:
- ingestion dedups on (key, event_time, knowledge_time) via INSERT OR IGNORE;
- a checkpoint file (`data/cache/ashare_klines_progress.json`) records completed codes
  so a re-run skips stocks already fully landed in PIT.

Honest schema note: the canonical `Bar` contract carries OHLCV only — `amt` (CNY
turnover) is NOT persisted in the PIT payload (pydantic drops extras). It is preserved
in the raw CSV mirror for any consumer that needs turnover.
"""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import re
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from services.data.market_provider import ingest_provider_rows  # noqa: E402
from services.data.persistent_store import SqlitePITStore  # noqa: E402

SKILL_DIR = (r"C:\Users\Administrator\AppData\Roaming\kimi-desktop\daimon-share"
             r"\daimon\runtime\kimi-code\home\plugins\managed\wind-allskill"
             r"\skills\wind-mcp-skill")

UNIVERSE_PATH = os.path.join(REPO_ROOT, "data", "cache", "ashare_universe.json")
DB_PATH = os.path.join(REPO_ROOT, "data", "cache", "ashare_daily.db")
RAW_DIR = os.path.join(REPO_ROOT, "data", "cache", "ashare_klines_raw")
PROGRESS_PATH = os.path.join(REPO_ROOT, "data", "cache", "ashare_klines_progress.json")
SNAPSHOT_PATH = os.path.join(REPO_ROOT, "data", "cache", "ashare_klines_snapshot.json")

BEGIN_DATE = "20210101"
END_DATE = "20260723"
END_INT = 20260723

CLI_TIMEOUT_S = 90
RETRIES = 2                      # retries AFTER the first attempt (3 attempts total)
PACE_S = 3.0                     # polite pacing between stocks (gateway rate-limits)
RETRY_SLEEP_S = 5.0
RATELIMIT_SLEEP_S = 35.0         # backoff when the gateway says "Too many requests"


class RateLimitError(RuntimeError):
    """Gateway quota exhaustion ("Too many requests") — back off hard."""

_NOTICE_PATH_RE = re.compile(r"(C:\\+[^\s\"']*?\.csv)", re.IGNORECASE)
_TOTAL_RE = re.compile(r"Total records:\s*(\d+)")


# --------------------------------------------------------------------------- #
# CLI runner + output parsing
# --------------------------------------------------------------------------- #

def default_cli_runner(args_json: str) -> str:
    """Run the Wind gateway CLI for one kline call; return raw stdout (raises on
    timeout / non-zero exit). The ONLY subprocess seam in this module — tests inject
    a fake here. A non-zero exit with a quota message in stdout/stderr is surfaced
    as RateLimitError so the batch can back off instead of burning retries."""
    proc = subprocess.run(
        ["node", "scripts/cli.mjs", "call", "stock_data", "get_stock_kline", args_json],
        cwd=SKILL_DIR, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT_S,
    )
    if proc.returncode != 0:
        blob = (proc.stdout or "") + (proc.stderr or "")
        if "Too many requests" in blob or "NETWORK_ERROR" in blob:
            raise RateLimitError(f"cli exit={proc.returncode} (quota): {blob[-200:]!r}")
        raise RuntimeError(f"cli exit={proc.returncode}: {(proc.stderr or proc.stdout)[-300:]!r}")
    return proc.stdout


def parse_cli_stdout(stdout: str) -> dict:
    """Parse the CLI stdout JSON -> {"notice", "csv_path", "total_records"}.
    stdout: {"content":[{"text": "<python-repr of {'type':'text','text':'<json>'}>"}]}
    The inner JSON carries `data_preview` (truncated) and `notice` with the FULL CSV
    path (backslashes possibly escaped). csv_path is None when absent.
    Raises RateLimitError on gateway quota errors; RuntimeError on other CLI errors."""
    obj = json.loads(stdout)
    if obj.get("ok") is False:                   # gateway-level error envelope
        err = obj.get("error") or {}
        msg = f"{err.get('code')}: {str(err.get('agent_action'))[:200]}"
        if "Too many requests" in msg or err.get("code") == "NETWORK_ERROR":
            raise RateLimitError(msg)
        raise RuntimeError(f"cli error: {msg}")
    if obj.get("isError"):
        raise RuntimeError(f"cli returned isError: {stdout[:300]!r}")
    text = obj["content"][0]["text"]
    payload = None
    try:
        inner = ast.literal_eval(text)          # python-repr of {'type':..,'text':'<json>'}
        if isinstance(inner, dict) and isinstance(inner.get("text"), str):
            payload = json.loads(inner["text"])
    except (ValueError, SyntaxError):
        payload = None
    if payload is None:                          # fallback: maybe the text IS the json
        payload = json.loads(text)
    notice = str(payload.get("notice", ""))
    csv_path = None
    m = _NOTICE_PATH_RE.search(notice)
    if m:
        csv_path = m.group(1).replace("\\\\", "\\")
    tm = _TOTAL_RE.search(notice)
    return {
        "notice": notice,
        "csv_path": csv_path,
        "total_records": int(tm.group(1)) if tm else None,
        "is_success": payload.get("is_success", ""),
    }


def read_kline_csv(path: str) -> list:
    """Read a Wind kline CSV -> list of dict rows (strings), keyed by header."""
    with open(path, newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f) if r.get("trade_date")]


def merge_rows(*row_lists) -> list:
    """Merge kline row lists, dedup by trade_date (first wins), sorted ascending."""
    by_date = {}
    for rows in row_lists:
        for r in rows:
            by_date.setdefault(r["trade_date"], r)
    return [by_date[d] for d in sorted(by_date)]


# --------------------------------------------------------------------------- #
# Fetch one stock (with yearly-pagination fallback)
# --------------------------------------------------------------------------- #

def _year_ranges(begin: str, end: str) -> list:
    y0, y1 = int(begin[:4]), int(end[:4])
    out = []
    for y in range(y0, y1 + 1):
        b = begin if y == y0 else f"{y}0101"
        e = end if y == y1 else f"{y}1231"
        out.append((b, e))
    return out


def fetch_stock_rows(code: str, *, begin: str = BEGIN_DATE, end: str = END_DATE,
                     runner=default_cli_runner, retries: int = RETRIES,
                     sleep=time.sleep) -> list:
    """Fetch full daily kline rows for one windcode. Raises on exhausted retries.

    Primary: one CLI call over [begin,end]; parse the notice's full-CSV path.
    Fallback (notice path missing / empty CSV / history truncated before begin+120d):
    paginate per calendar year and merge."""
    def one_call(b: str, e: str) -> list:
        args = json.dumps({"windcode": code, "begin_date": b, "end_date": e})
        parsed = parse_cli_stdout(runner(args))
        if not parsed["csv_path"]:
            raise RuntimeError(f"no csv path in notice: {parsed['notice'][:160]!r}")
        return read_kline_csv(parsed["csv_path"])

    def with_retries(b: str, e: str) -> list:
        last = None
        for attempt in range(retries + 1):
            try:
                return one_call(b, e)
            except Exception as exc:             # timeout / exit!=0 / parse / missing csv
                last = exc
                if attempt < retries:
                    sleep(RATELIMIT_SLEEP_S if isinstance(exc, RateLimitError)
                          else RETRY_SLEEP_S)
        raise last

    rows = with_retries(begin, end)
    truncated = (not rows) or (rows[0]["trade_date"].replace("-", "") >
                               _shift_days(begin, 120))
    if truncated:
        chunks = [with_retries(b, e) for b, e in _year_ranges(begin, end)]
        merged = merge_rows(*chunks)
        if merged:
            rows = merged
    if not rows:
        raise RuntimeError("empty kline history")
    return rows


def _shift_days(yyyymmdd: str, days: int) -> str:
    import datetime as _dt
    d = _dt.date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    return (d + _dt.timedelta(days=days)).strftime("%Y%m%d")


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #

def rows_to_canonical(code: str, rows: list, *, end_int: int = END_INT) -> tuple:
    """Wind CSV rows -> canonical ingest_provider_rows dicts. event_time =
    knowledge_time = trade_date as YYYYMMDD int (EOD-known). Rows dated after
    end_int are DROPPED (future guard). Returns (canonical_rows, dropped_future)."""
    out, dropped = [], 0
    for r in rows:
        et = int(r["trade_date"].replace("-", ""))
        if et > end_int:
            dropped += 1
            continue
        out.append({
            "instrument_id": code,
            "event_time": et,
            "knowledge_time": et,
            "open": r["open"], "high": r["high"], "low": r["low"],
            "close": r["close"], "volume": r.get("volume", "0"),
        })
    return out, dropped


def write_raw_mirror(code: str, rows: list, raw_dir: str = RAW_DIR) -> str:
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, f"{code}.csv")
    cols = ["trade_date", "wind_code", "open", "high", "low", "close", "volume", "amt"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


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


def run_batch(universe, store, *, raw_dir=RAW_DIR, progress_path=PROGRESS_PATH,
              runner=default_cli_runner, sleep=time.sleep,
              begin=BEGIN_DATE, end=END_DATE, log_every=10, log=print,
              limit=None, abort_on_ratelimit=True) -> dict:
    """Fetch + ingest every stock in `universe` (list of dicts with 'code').
    Resumable via the checkpoint file; per-row ingestion dedups regardless.
    `limit` caps how many NOT-yet-done stocks are processed this call (chunked runs).
    On a gateway RateLimitError the batch ABORTS EARLY (progress is saved) so the
    caller can resume after the quota window resets instead of hammering the gateway.
    Returns {"done", "failed", "stats", "aborted_ratelimit"}."""
    prog = _load_progress(progress_path)
    done = set(prog["done"])
    failed = dict(prog["failed"])
    stats = {}
    t0 = time.time()
    total = len(universe)
    processed_this_call = 0
    aborted_ratelimit = False
    # order: never-attempted stocks FIRST, previously-failed stocks LAST, so a fresh
    # gateway quota window is spent on new data rather than on likely-doomed retries.
    remaining = [it for it in universe if it["code"] not in done]
    remaining.sort(key=lambda it: it["code"] in failed)
    for i, item in enumerate(remaining):
        code = item["code"]
        if code in done:
            continue
        if limit is not None and processed_this_call >= limit:
            break
        processed_this_call += 1
        try:
            rows = fetch_stock_rows(code, begin=begin, end=end,
                                    runner=runner, sleep=sleep)
            write_raw_mirror(code, rows, raw_dir)
            canonical, dropped_future = rows_to_canonical(code, rows,
                                                          end_int=int(end))
            rep = ingest_provider_rows(canonical, store, dataset_id=f"ashare-{code}",
                                       time_unit="yyyymmdd")
            stats[code] = {
                "rows": len(rows), "ingested": rep.bars_ingested,
                "duplicates": rep.duplicates if hasattr(rep, "duplicates") else None,
                "rejected": len(rep.rejected),
                "dropped_future": dropped_future,
                "date_min": rows[0]["trade_date"], "date_max": rows[-1]["trade_date"],
            }
            done.add(code)
            failed.pop(code, None)
        except Exception as exc:
            failed[code] = f"{type(exc).__name__}: {exc}"[:300]
            if isinstance(exc, RateLimitError) and abort_on_ratelimit:
                aborted_ratelimit = True
        prog = {"done": sorted(done), "failed": failed}
        _save_progress(progress_path, prog)
        if aborted_ratelimit:
            log(f"[ratelimit] aborting chunk early at {code}; "
                f"done={len(done)} failed={len(failed)} — resume after quota reset")
            break
        n_processed = i + 1
        if n_processed % log_every == 0 or n_processed == total:
            log(f"[{n_processed}/{total}] done={len(done)} failed={len(failed)} "
                f"elapsed={time.time() - t0:.0f}s")
        sleep(PACE_S)
    return {"done": sorted(done), "failed": failed, "stats": stats,
            "aborted_ratelimit": aborted_ratelimit}


def write_snapshot(store, universe, path: str = SNAPSHOT_PATH,
                   fetched_at: str = "") -> dict:
    """Per-stock PIT counts + date coverage + totals -> snapshot JSON."""
    per_stock = {}
    total_rows = 0
    for item in universe:
        code = item["code"]
        bars = store.fetch_bars(code, -10**18, 10**18)
        if bars:
            per_stock[code] = {
                "rows": len(bars),
                "date_min": min(b.event_time for b in bars),
                "date_max": max(b.event_time for b in bars),
            }
            total_rows += len(bars)
        else:
            per_stock[code] = {"rows": 0, "date_min": None, "date_max": None}
    snap = {"fetched_at": fetched_at, "universe_size": len(universe),
            "stocks_with_data": sum(1 for v in per_stock.values() if v["rows"]),
            "total_rows": total_rows, "per_stock": per_stock}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return snap


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N not-yet-done stocks this call (resumable chunks)")
    ap.add_argument("--snapshot-only", action="store_true",
                    help="skip fetching; only rewrite the snapshot from PIT")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="skip the (slow) full-PIT snapshot rewrite for chunked calls")
    args = ap.parse_args()
    universe = json.load(open(UNIVERSE_PATH, encoding="utf-8"))["stocks"]
    store = SqlitePITStore(DB_PATH, create=True)
    t0 = time.time()
    result = {"done": [], "failed": {}}
    if not args.snapshot_only:
        result = run_batch(universe, store, limit=args.limit)
    else:
        prog = _load_progress(PROGRESS_PATH)
        result = {"done": prog["done"], "failed": prog["failed"]}
    if args.no_snapshot and not args.snapshot_only:
        print(f"\n== call finished in {time.time() - t0:.0f}s (snapshot deferred) ==")
        print(f"stocks done: {len(result['done'])}/{len(universe)}")
        if result["failed"]:
            print("FAILURES SO FAR:")
            for code, reason in sorted(result["failed"].items()):
                print(f"  {code}: {reason}")
        return
    snap = write_snapshot(store, universe,
                          fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    print(f"\n== call finished in {time.time() - t0:.0f}s ==")
    print(f"stocks done: {len(result['done'])}/{len(universe)}  "
          f"total PIT rows: {snap['total_rows']}")
    if result["failed"]:
        print("FAILURES:")
        for code, reason in sorted(result["failed"].items()):
            print(f"  {code}: {reason}")


if __name__ == "__main__":
    main()
