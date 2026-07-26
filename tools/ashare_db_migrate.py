"""Migrate the scattered A-share data into the consolidated stock database.

Builds `data/stocks/stocks.db` from `data/cache/ashare_daily.db`:
  - bars            -- identical PIT schema (key, event_time, knowledge_time,
                       payload_json) so SqlitePITStore reads the new DB directly
  - universe        -- one row per stock (code, name, mcap_yi, adv_yi, source)
  - daily_prices    -- flat convenience table for direct SQL analytics
  - meta            -- provenance key/values

Idempotent: drops and rebuilds the derived tables, copies bars with
INSERT OR REPLACE. Network-free; operates on local files only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DB = os.environ.get(
    "ASHARE_SRC_DB", os.path.join(REPO, "data", "stocks", "stocks.db")
)
DST_DB = os.path.join(REPO, "data", "stocks", "stocks.db")
UNIVERSE = os.path.join(REPO, "data", "stocks", "universe.json")
KLINES_SNAPSHOT = os.path.join(REPO, "data", "stocks", "snapshots", "klines_snapshot.json")


def build() -> None:
    os.makedirs(os.path.dirname(DST_DB), exist_ok=True)
    universe = json.load(open(UNIVERSE, encoding="utf-8"))
    snap = json.load(open(KLINES_SNAPSHOT, encoding="utf-8"))
    per_stock = snap.get("per_stock", {})
    if isinstance(per_stock, list):
        per_stock = {s["code"]: s for s in per_stock}
    source_of = {code: meta.get("source", "unknown") for code, meta in per_stock.items()}

    src = sqlite3.connect(SRC_DB)
    dst = sqlite3.connect(DST_DB)
    dst.executescript(
        """
        CREATE TABLE IF NOT EXISTS bars (
            key TEXT NOT NULL,
            event_time INTEGER NOT NULL,
            knowledge_time INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (key, event_time, knowledge_time)
        );
        CREATE INDEX IF NOT EXISTS ix_bars_key_kt ON bars (key, knowledge_time);
        DROP TABLE IF EXISTS universe;
        CREATE TABLE universe (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mcap_yi REAL,
            adv_yi REAL,
            source TEXT NOT NULL,
            n_bars INTEGER,
            first_date INTEGER,
            last_date INTEGER
        );
        DROP TABLE IF EXISTS daily_prices;
        CREATE TABLE daily_prices (
            code TEXT NOT NULL,
            trade_date INTEGER NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (code, trade_date)
        );
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        """
    )
    # copy bars via ATTACH for speed (skipped when SRC == DST: bars already live)
    if os.path.abspath(SRC_DB) != os.path.abspath(DST_DB):
        dst.execute("ATTACH DATABASE ? AS src", (SRC_DB,))
        dst.execute("INSERT OR REPLACE INTO bars SELECT key, event_time, knowledge_time, payload_json FROM src.bars")
        dst.commit()
        dst.execute("DETACH DATABASE src")

    n_bars = 0
    price_rows = []
    for (key, event_time, payload) in src.execute(
        "SELECT key, event_time, payload_json FROM bars ORDER BY key, event_time"
    ):
        p = json.loads(payload)
        price_rows.append((
            key, int(event_time), p.get("open"), p.get("high"), p.get("low"),
            p.get("close"), p.get("volume"),
        ))
        n_bars += 1
    dst.executemany(
        "INSERT OR REPLACE INTO daily_prices VALUES (?,?,?,?,?,?,?)", price_rows
    )

    span = {
        k: (lo, hi, n) for k, lo, hi, n in src.execute(
            "SELECT key, min(event_time), max(event_time), count(*) FROM bars GROUP BY key"
        )
    }
    for s in universe.get("stocks", []):
        code = s["code"]
        lo, hi, n = span.get(code, (None, None, 0))
        dst.execute(
            "INSERT OR REPLACE INTO universe VALUES (?,?,?,?,?,?,?,?)",
            (code, s.get("name", ""), s.get("mcap_yi"), s.get("adv_yi"),
             source_of.get(code, "unknown"), n, lo, hi),
        )
    dst.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?,?)",
        [
            ("migrated_from", os.path.relpath(SRC_DB, REPO)),
            ("migrated_at", time.strftime("%Y-%m-%dT%H:%M:%S")),
            ("n_bars", str(n_bars)),
            ("n_stocks", str(len(span))),
            ("universe_filter", universe.get("filter", "")),
            ("source_note", "wind 120 / ifind 129 前复权; amt 为下游代理; 详见 snapshots/klines_snapshot.json notes"),
        ],
    )
    dst.commit()
    # verify
    b = dst.execute("SELECT count(*) FROM bars").fetchone()[0]
    u = dst.execute("SELECT count(*) FROM universe").fetchone()[0]
    p = dst.execute("SELECT count(*) FROM daily_prices").fetchone()[0]
    dst.close(); src.close()
    print(f"stocks.db: bars={b} universe={u} daily_prices={p}")
    assert b == n_bars and p == n_bars, "migration count mismatch"
    print("OK")


if __name__ == "__main__":
    sys.exit(build())
