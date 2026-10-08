"""抓 FRED 市场型日频宏观序列(无修正、EOD 可知 → 真 PIT)填进 fundamental_store。网络在 tools/(出 gate);
services/ 保持无网络。市场序列 knowledge_time=event_time。经济发布类(CPI/GDP)不抓——免费 fredgraph 只给最新修正值
(前视),需 ALFRED vintage。fail-closed:HTTP≠200/空/全缺失 → raise、非零退出。"""
from __future__ import annotations

import csv
import io
import os
import sys
import urllib.request

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore

_DB = os.path.join("data", "cache", "macro_fred.db")
_COSD = "1990-01-01"
_SERIES = ["VIXCLS", "DGS10", "T10Y2Y", "BAMLH0A0HYM2", "DTWEXBGS", "DFII10"]


def _fetch_csv(series_id: str) -> str:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={_COSD}"
    req = urllib.request.Request(url, headers={"User-Agent": "quantos-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"FRED {series_id} HTTP {resp.status}")
        body = resp.read().decode("utf-8")
    if not body.strip() or "\n" not in body:
        raise RuntimeError(f"FRED {series_id} empty body")
    return body


def _ingest(series_id: str, body: str, store: SqliteFundamentalStore) -> tuple:
    added = seen = 0
    for row in csv.DictReader(io.StringIO(body)):
        vals = list(row.values())
        date_s, val_s = vals[0], vals[1]              # observation_date, <series>
        if not date_s or val_s in (".", "", None):
            continue
        try:
            v = float(val_s)
        except ValueError:
            continue
        yyyymmdd = int(date_s.replace("-", ""))
        seen += 1
        fv = FeatureValue(feature_id=series_id, instrument_id="MACRO",
                          event_time=yyyymmdd, knowledge_time=yyyymmdd, value=v)  # 市场序列 EOD 可知
        if store.add(fv):
            added += 1
    if seen == 0:
        raise RuntimeError(f"FRED {series_id} all-missing")
    return added, seen


def main() -> None:
    if os.path.exists(_DB):
        os.remove(_DB)
    store = SqliteFundamentalStore(_DB, create=True)
    total = 0
    for s in _SERIES:
        body = _fetch_csv(s)
        added, seen = _ingest(s, body, store)
        total += added
        rows = store.as_of(s, "MACRO", 99999999)
        span = f"{rows[0].event_time}-{rows[-1].event_time}" if rows else "-"
        print(f"  {s:14s} added {added}/{seen} · span {span}", flush=True)
    print(f"=== FRED 宏观填库完成 · {len(_SERIES)} 序列 · {total} 行 -> {_DB} ===", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FRED fetch FAILED (fail-closed): {exc}", file=sys.stderr)
        sys.exit(1)
