"""A-share micro-cap universe collector via Wind agent-gw CLI.

Wind search/analytics responses only expose a ~30-row ``data_preview`` CSV, so
this collector paginates ascending by market cap: each query asks for
``总市值{lo}亿到{hi}亿`` sorted ascending, we read the visible rows, advance
``lo`` to just above the largest seen cap, and repeat until we have enough
names or hit the cap ceiling. Network access lives in tools/ by governance;
this script shells out to the Wind CLI (node) and never imports network libs.

Outputs:
  data/cache/ashare_universe.json  -- full collected universe + metadata
  data/cache/ashare_universe.csv   -- flat CSV (code,name,mcap_yi,adv_yi)
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WIND_CLI_DIR = Path(
    r"C:\Users\Administrator\AppData\Roaming\kimi-desktop\daimon-share\daimon"
    r"\runtime\kimi-code\home\plugins\managed\wind-allskill\skills\wind-mcp-skill"
)
OUT_JSON = REPO / "data" / "cache" / "ashare_universe.json"
OUT_CSV = REPO / "data" / "cache" / "ashare_universe.csv"

TARGET_COUNT = 220
CAP_FLOOR = 20.0      # 亿 — micro-cap segment floor
CAP_CEIL = 60.0       # 亿 — stop paginating past this
MIN_ADV_YI = 0.5      # 近1月日均成交额 >= 5000万
MAX_PAGES = 40


def wind_query(question: str) -> list[dict]:
    """One analytics_data call; returns parsed preview rows (may be ~30)."""
    cmd = [
        "node", "scripts/cli.mjs", "call",
        "analytics_data", "get_financial_data",
        json.dumps({"question": question}, ensure_ascii=False),
    ]
    proc = subprocess.run(
        cmd, cwd=str(WIND_CLI_DIR), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"wind cli failed: {proc.stdout[:400]} {proc.stderr[:400]}")
    payload = json.loads(proc.stdout)
    if payload.get("isError"):
        raise RuntimeError(f"wind error: {str(payload)[:400]}")
    text = payload["content"][0]["text"]
    # The text is a nested repr/JSON blob; robustly regex out the data_preview
    # CSV (quotes may be escaped at one or two levels), then unescape.
    import re
    m = re.search(r'data_preview\\?":\s*\\?"(.*?)\\?"\s*[,\\}]', text, re.S)
    if not m:
        return []
    csv_text = m.group(1)
    for _ in range(3):  # peel up to 3 layers of escaping
        if "\\n" not in csv_text and "\\\"" not in csv_text:
            break
        csv_text = csv_text.replace("\\\\n", "\n").replace("\\n", "\n").replace('\\"', '"')
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    return rows


def collect() -> list[dict]:
    universe: dict[str, dict] = {}
    lo = CAP_FLOOR
    pages = 0
    while len(universe) < TARGET_COUNT and lo < CAP_CEIL and pages < MAX_PAGES:
        pages += 1
        q = (
            f"查询沪深A股总市值{lo:.2f}亿到{CAP_CEIL:.0f}亿且近一月日均成交额大于5000万元"
            f"且非ST的股票,返回Wind代码、证券简称、总市值、近一月日均成交额,按总市值升序"
        )
        try:
            rows = wind_query(q)
        except Exception as exc:  # noqa: BLE001 — log and retry once
            print(f"[page {pages}] query failed: {exc}; retrying in 5s", flush=True)
            time.sleep(5)
            rows = wind_query(q)
        new = 0
        max_cap = lo
        for r in rows:
            code = (r.get("Wind代码") or "").strip()
            name = (r.get("证券简称") or "").strip()
            if not code or "ST" in name.upper():
                continue
            try:
                mcap = float(r.get("总市值1") or "nan")
                adv = float(r.get("近1月日均成交额") or "nan")
            except ValueError:
                continue
            if mcap != mcap or adv != adv or adv < MIN_ADV_YI:
                continue
            max_cap = max(max_cap, mcap)
            if code not in universe:
                universe[code] = {"code": code, "name": name,
                                  "mcap_yi": mcap, "adv_yi": adv}
                new += 1
        print(f"[page {pages}] lo={lo:.2f} rows={len(rows)} new={new} "
              f"total={len(universe)} max_cap={max_cap:.2f}", flush=True)
        if new == 0:
            break  # no progress — avoid infinite loop
        lo = max_cap + 0.001
        time.sleep(1.5)  # be polite to the gateway
    return sorted(universe.values(), key=lambda d: d["mcap_yi"])


def main() -> int:
    stocks = collect()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({
        "source": "WIND_AGENT_GW analytics_data.get_financial_data",
        "filter": "沪深A股 总市值20-60亿 近1月日均成交额>5000万 非ST, 按总市值升序分页",
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(stocks),
        "stocks": stocks,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "name", "mcap_yi", "adv_yi"])
        for s in stocks:
            w.writerow([s["code"], s["name"], s["mcap_yi"], s["adv_yi"]])
    print(f"DONE: {len(stocks)} stocks -> {OUT_JSON} / {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
