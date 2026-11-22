"""Post-run sanity validation for the A-share kline PIT store (read-only)."""
import csv
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.data.persistent_store import SqlitePITStore  # noqa: E402
from tools import ashare_kline_fetch as f  # noqa: E402

END_INT = 20260723

universe = json.load(open(f.UNIVERSE_PATH, encoding="utf-8"))["stocks"]
store = SqlitePITStore(f.DB_PATH, create=False)

counts, future_bad, span = {}, [], {}
for item in universe:
    code = item["code"]
    bars = store.fetch_bars(code, -10**18, 10**18)
    counts[code] = len(bars)
    if bars:
        span[code] = (min(b.event_time for b in bars), max(b.event_time for b in bars))
        if span[code][1] > END_INT:
            future_bad.append(code)

print(f"stocks with data: {sum(1 for c in counts.values() if c)}/{len(universe)}")
print(f"total rows: {sum(counts.values())}")
zero = [c for c, n in counts.items() if n == 0]
print(f"zero-row stocks: {len(zero)} {zero[:10]}")
hist = {}
for n in counts.values():
    bucket = "1200+" if n >= 1200 else ("800-1199" if n >= 800 else ("1-799" if n >= 1 else "0"))
    hist[bucket] = hist.get(bucket, 0) + 1
print("row-count histogram:", hist)
print("future-dated bars > 20260723:", future_bad or "NONE")

# spot-check 3 stocks vs raw CSV mirror
random.seed(7)
sample = random.sample([c for c in counts if counts[c] > 100], 3)
for code in sample:
    bars = {b.event_time: b for b in store.fetch_bars(code, -10**18, 10**18)}
    with open(f"{f.RAW_DIR}/{code}.csv", newline="", encoding="utf-8") as fh:
        raw = {int(r["trade_date"].replace("-", "")): float(r["close"])
               for r in csv.DictReader(fh)}
    bad = [(d, bars[d].close, raw[d]) for d in raw
           if d in bars and abs(bars[d].close - raw[d]) > 1e-9]
    latest = store.latest_as_of(code, 99999999)
    print(f"spot-check {code}: pit_rows={len(bars)} raw_rows={len(raw)} "
          f"close_mismatches={len(bad)} latest_as_of={latest.event_time}/{latest.close}"
          + (f" BAD:{bad[:3]}" if bad else " OK"))
print("snapshot exists:", end=" ")
import os
print(os.path.exists(f.SNAPSHOT_PATH))
