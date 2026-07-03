"""抓一个流动美股大盘宇宙的 10 年日线(Yahoo v8 chart,浏览器 UA + 礼貌延时)填进 equity_daily.db(PIT store)。
网络在 tools/(出 gate);services/ 保持无网络。走既有规范摄入接缝 `ingest_provider_rows`(与 crypto/FX fetcher
同一条 fail-closed 归一化路径,`time_unit="yyyymmdd"` 拒绝错量纲 event_time),不直接构造 Bar。

**PIT-vintage(路线图 Rank #21):存 RAW 未调价。** 早先版本把「今天的 `adjclose/close` 比例」回溯乘进历史
OHLC → 相当于把未来分红悄悄烘进 knowledge_time=T 的价格(非 PIT vintage 的前视脚印)。现在只存 Yahoo `quote`
的原始 OHLC(Yahoo 已按拆股调整、但**未**按分红调整),knowledge_time=event_time=YYYYMMDD(EOD 可知)。
完整分红/总收益调整是**读取时**的 PIT-correct 步骤(经 `services/data/corporate_actions`,路线图 Rank #23),
不在抓取时烘进库。provenance=`YAHOO_V8_QUOTE_RAW`。

诚实边界:① 用**当前**大盘成分 → 有幸存者偏差(退市/掉出未算),显式标注;真 survivorship-free 需历史成分
(路线图 Rank #17,灌 universe_store)。② Yahoo `quote.close` 是 source 侧拆股调整、非分红调整——如实标注,
不冒充完整总收益价。Yahoo 是公开研究用法(与已有 crypto/FX fetcher 同类);不绕过任何 bot 墙。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore

_DB = os.path.join("data", "cache", "equity_daily.db")
_SNAP = os.path.join("data", "cache", "equity_universe_snapshot.json")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
_DELAY = 1.2
_RANGE = "10y"

# 流动美股大盘宇宙(~90,跨板块;当前成分=有幸存者偏差,诚实标注)
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "ORCL", "CRM",
    "ADBE", "CSCO", "ACN", "INTC", "AMD", "QCOM", "TXN", "IBM",
    "JPM", "BAC", "WFC", "GS", "MS", "C", "AXP", "BLK", "SPGI", "SCHW",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY", "AMGN",
    "XOM", "CVX", "COP", "SLB", "EOG",
    "WMT", "PG", "KO", "PEP", "COST", "MCD", "NKE", "HD", "LOW", "SBUX", "TGT",
    "CAT", "BA", "HON", "UNP", "GE", "MMM", "DE", "LMT", "RTX",
    "V", "MA", "PYPL",
    "DIS", "CMCSA", "NFLX", "T", "VZ", "TMUS",
    "LIN", "SHW", "NEE", "DUK", "SO", "AMT", "PLD",
]


def _fetch(sym: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={_RANGE}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f"{sym} HTTP {r.status}")
        return json.load(r)


def _rows(sym: str, j: dict) -> list:
    """Canonical provider rows with RAW unadjusted quote OHLC (no retroactive adjclose ratio)."""
    res = j["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    o, h, lo, c, v = q["open"], q["high"], q["low"], q["close"], q.get("volume")
    rows = []
    for i in range(len(ts)):
        oi, hi, li, ci = o[i], h[i], lo[i], c[i]
        if None in (oi, hi, li, ci) or ci <= 0 or oi <= 0 or hi <= 0 or li <= 0:
            continue
        yyyymmdd = int(time.strftime("%Y%m%d", time.gmtime(ts[i])))
        rows.append({"instrument_id": sym, "event_time": yyyymmdd, "knowledge_time": yyyymmdd,
                     "open": oi, "high": hi, "low": li, "close": ci,          # RAW — no adjclose scaling
                     "volume": float(v[i]) if (v and v[i] is not None) else 0.0})
    return rows


def main() -> None:
    if os.path.exists(_DB):
        os.remove(_DB)
    store = SqlitePITStore(_DB, create=True)
    now_yyyymmdd = int(time.strftime("%Y%m%d", time.gmtime()))     # future-data guard (kt > now -> reject)
    ok, total_bars, failed = [], 0, []
    for sym in UNIVERSE:
        try:
            rows = _rows(sym, _fetch(sym))
            if len(rows) < 100:
                failed.append((sym, f"only {len(rows)} rows"))
                continue
            rep = ingest_provider_rows(rows, store, dataset_id=f"yahoo-{sym}",
                                       now=now_yyyymmdd, time_unit="yyyymmdd")
            ok.append(sym)
            total_bars += rep.bars_ingested
            print(f"  {sym:6s} {rep.bars_ingested} bars · {rows[0]['event_time']}-{rows[-1]['event_time']}"
                  f" (rejected {len(rep.rejected)})", flush=True)
        except Exception as exc:
            failed.append((sym, f"{type(exc).__name__}: {exc}"))
            print(f"  {sym:6s} FAIL: {exc}", flush=True)
        time.sleep(_DELAY)
    json.dump({"instruments": sorted(ok), "n": len(ok), "total_bars": total_bars,
               "range": _RANGE, "provenance": "YAHOO_V8_QUOTE_RAW",
               "price_note": "Yahoo quote OHLC:source 侧拆股调整、未按分红调整;完整总收益调整是读取时 PIT 步骤(corporate_actions, Rank #23)",
               "survivorship_note": "当前大盘成分,有幸存者偏差(退市/掉出未算);真 survivorship-free 需历史成分(Rank #17)",
               "failed": failed}, open(_SNAP, "w", encoding="utf-8"), indent=2)
    print(f"=== 股票日线填库完成 · {len(ok)}/{len(UNIVERSE)} 只 · {total_bars} bars -> {_DB} ===", flush=True)
    if failed:
        print(f"  失败 {len(failed)}: {[s for s, _ in failed]}", flush=True)
    if not ok:
        sys.exit(1)   # fail-closed:一只都没抓到


if __name__ == "__main__":
    main()
