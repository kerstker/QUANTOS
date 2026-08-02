"""A-share valuation/fundamentals snapshot fetcher — Wind gateway CLI (tools/ only).

Fetches a STRUCTURED current-cross-section valuation snapshot for the 249-stock
micro-cap universe via `get_stock_price_indicators` (逐字 indexes from the Wind
indicator library) and lands it in `data/stocks/stocks.db` table
`valuation_snapshot`. Complements the daily bars (stocks.db `bars`) with the
fundamentals layer: PE(TTM/LYR), PB(/LF), total market cap, dividend yield.

Governance: network/subprocess lives HERE in tools/ only; services/ untouched.
READ-ONLY market data — no orders, no broker SDK, gateway holds credentials.

Idempotent & resumable:
- `valuation_snapshot` keyed by code (INSERT OR REPLACE) — reruns overwrite;
- progress file records completed codes; a re-run skips them unless --refresh.

Honest scope notes:
- This is a CURRENT cross-section (as-of fetch date), NOT a PIT time series of
  valuation; point-in-time valuation history would need per-date snapshots and
  is an explicit follow-up. Factor research may join this on the LATEST date
  only (cross-sectional screens), never as a historical series.
- Deep fundamentals (ROE / revenue & profit growth / balance-sheet items) come
  from the NL `get_stock_fundamentals` endpoint and are the NEXT layer
  (per-stock prose; needs careful parsing) — not landed here.
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

SKILL_DIR = (r"C:\Users\Administrator\AppData\Roaming\kimi-desktop\daimon-share"
             r"\daimon\runtime\kimi-code\home\plugins\managed\wind-allskill"
             r"\skills\wind-mcp-skill")

UNIVERSE_PATH = os.path.join(REPO_ROOT, "data", "stocks", "universe.json")
DB_PATH = os.path.join(REPO_ROOT, "data", "stocks", "stocks.db")
PROGRESS_PATH = os.path.join(REPO_ROOT, "data", "stocks", "valuation_fetch_progress.json")
SNAPSHOT_PATH = os.path.join(REPO_ROOT, "data", "stocks", "snapshots", "valuation_snapshot.json")

# 逐字抄自 Wind 指标库(indicators.md);括号/口径一字不差。
INDEXES = "中文简称,最新成交价,总市值2,市盈率(TTM),市盈率(LYR),市净率,市净率(LF),股息率"
FIELD_MAP = {
    "中文简称": "name",
    "最新成交价": "close",
    "总市值2": "mcap",          # 含限售股口径,与 universe.mcap_yi 一致
    "市盈率(TTM)": "pe_ttm",
    "市盈率(LYR)": "pe_lyr",
    "市净率": "pb",
    "市净率(LF)": "pb_lf",
    "股息率": "dividend_yield",
}

CLI_TIMEOUT_S = 60
RETRIES = 2
PACE_S = 2.0
RATELIMIT_SLEEP_S = 35.0

DDL = """
CREATE TABLE IF NOT EXISTS valuation_snapshot (
  code TEXT PRIMARY KEY,
  asof TEXT NOT NULL,            -- YYYY-MM-DD fetch date (current cross-section)
  name TEXT, close REAL, mcap REAL,
  pe_ttm REAL, pe_lyr REAL, pb REAL, pb_lf REAL, dividend_yield REAL,
  raw_json TEXT NOT NULL
);
"""


class RateLimitError(RuntimeError):
    pass


def default_cli_runner(args_json: str) -> str:
    proc = subprocess.run(
        ["node", "scripts/cli.mjs", "call", "stock_data",
         "get_stock_price_indicators", args_json],
        cwd=SKILL_DIR, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT_S,
    )
    if proc.returncode != 0:
        blob = (proc.stdout or "") + (proc.stderr or "")
        if "Too many requests" in blob or "NETWORK_ERROR" in blob:
            raise RateLimitError(f"cli exit={proc.returncode} (quota)")
        raise RuntimeError(f"cli exit={proc.returncode}: {(proc.stderr or proc.stdout)[-300:]!r}")
    return proc.stdout


def parse_indicator_row(stdout: str) -> dict:
    """CLI stdout -> one valuation row {field: value}. Raises on error envelopes."""
    obj = json.loads(stdout)
    if obj.get("ok") is False:
        err = obj.get("error") or {}
        msg = f"{err.get('code')}: {str(err.get('agent_action'))[:200]}"
        if "Too many requests" in msg or err.get("code") == "NETWORK_ERROR":
            raise RateLimitError(msg)
        raise RuntimeError(f"cli error: {msg}")
    if obj.get("isError"):
        raise RuntimeError(f"cli isError: {stdout[:300]!r}")
    text = obj["content"][0]["text"]
    try:
        inner = ast.literal_eval(text)
        payload = json.loads(inner["text"]) if isinstance(inner, dict) else json.loads(text)
    except (ValueError, SyntaxError):
        payload = json.loads(text)
    preview = payload.get("data_preview") or ""
    rows = [r for r in csv.DictReader(io.StringIO(preview)) if r.get("wind_code")]
    if not rows:
        raise RuntimeError(f"no data row in preview: {preview[:200]!r}")
    raw = rows[0]
    out: dict = {}
    for zh, key in FIELD_MAP.items():
        v = raw.get(zh)
        if key == "name":
            out[key] = v
        else:
            try:
                out[key] = float(v) if v not in (None, "", "--") else None
            except ValueError:
                out[key] = None
    return out


def load_universe_codes() -> list[str]:
    with open(UNIVERSE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else data.get("universe") or data.get("stocks") or []
    codes = []
    for e in entries:
        code = e.get("code") if isinstance(e, dict) else str(e)
        if code:
            codes.append(code)
    return codes


def ensure_table(con: sqlite3.Connection) -> None:
    con.executescript(DDL)
    con.commit()


def fetch_all(*, codes: list[str], refresh: bool, asof: str,
              cli_runner=default_cli_runner, sleep=time.sleep,
              db_path: str = DB_PATH, progress_path: str = PROGRESS_PATH,
              snapshot_path: str = SNAPSHOT_PATH) -> dict:
    progress = {}
    if os.path.exists(progress_path) and not refresh:
        with open(progress_path, encoding="utf-8") as f:
            progress = json.load(f)
    done = set(progress.get("done", []))

    con = sqlite3.connect(db_path)
    ensure_table(con)
    stats = {"ok": 0, "skipped": 0, "failed": {}}
    try:
        for i, code in enumerate(codes):
            if code in done:
                stats["skipped"] += 1
                continue
            args = json.dumps({"windcode": code, "indexes": INDEXES}, ensure_ascii=False)
            row = None
            for attempt in range(RETRIES + 1):
                try:
                    row = parse_indicator_row(cli_runner(args))
                    break
                except RateLimitError:
                    sleep(RATELIMIT_SLEEP_S)
                except (RuntimeError, subprocess.TimeoutExpired) as e:
                    if attempt >= RETRIES:
                        stats["failed"][code] = str(e)[:200]
                    else:
                        sleep(5.0)
            if row is None:
                continue
            con.execute(
                "INSERT OR REPLACE INTO valuation_snapshot "
                "(code, asof, name, close, mcap, pe_ttm, pe_lyr, pb, pb_lf, dividend_yield, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (code, asof, row.get("name"), row.get("close"), row.get("mcap"),
                 row.get("pe_ttm"), row.get("pe_lyr"), row.get("pb"), row.get("pb_lf"),
                 row.get("dividend_yield"), json.dumps(row, ensure_ascii=False)))
            con.commit()
            done.add(code)
            stats["ok"] += 1
            if (i + 1) % 10 == 0:
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump({"done": sorted(done), "asof": asof}, f)
                print(f"[{i + 1}/{len(codes)}] ok={stats['ok']} failed={len(stats['failed'])}", flush=True)
            sleep(PACE_S)
    finally:
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump({"done": sorted(done), "asof": asof}, f)
        n_db = con.execute("SELECT COUNT(*) FROM valuation_snapshot").fetchone()[0]
        con.close()

    snapshot = {
        "artifact": "ASHARE_VALUATION_SNAPSHOT",
        "asof": asof, "universe_size": len(codes),
        "fetched": stats["ok"], "skipped": stats["skipped"],
        "failed": stats["failed"], "rows_in_db": n_db,
        "fields": list(FIELD_MAP.values()),
        "scope_note": "current cross-section only, NOT a PIT valuation history",
    }
    os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return snapshot


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 只(试跑)")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔指定代码")
    ap.add_argument("--refresh", action="store_true", help="忽略断点全部重抓")
    ap.add_argument("--asof", type=str, default=time.strftime("%Y-%m-%d"),
                    help="快照日期(默认今天)")
    args = ap.parse_args()
    codes = args.codes.split(",") if args.codes else load_universe_codes()
    if args.limit:
        codes = codes[: args.limit]
    snap = fetch_all(codes=codes, refresh=args.refresh, asof=args.asof)
    print(json.dumps(snap, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
