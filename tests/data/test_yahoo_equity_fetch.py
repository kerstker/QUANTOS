"""yahoo_equity_fetch 行构建 —— PIT-vintage 修复(路线图 Rank #21)。

关键不变量:存 RAW 未调价,**不**把今天的 adjclose/close 比例回溯乘进历史 OHLC(那会把未来分红
烘进 knowledge_time=T 的价格 = 非 PIT vintage 前视)。用合成 Yahoo payload(adjclose ≠ close)证明
存的是原始 close;并做一次经规范摄入接缝的 PIT 往返。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from tools.yahoo_equity_fetch import _rows


def _payload():
    # two daily bars; adjclose deliberately != close so a retroactive ratio (adj/close) would scale prices
    return {"chart": {"result": [{
        "timestamp": [1704067200, 1704153600],   # 2024-01-01, 2024-01-02 (UTC)
        "indicators": {
            "quote": [{"open": [100.0, 102.0], "high": [101.0, 103.0], "low": [99.0, 101.0],
                       "close": [100.0, 102.0], "volume": [1000, 1100]}],
            "adjclose": [{"adjclose": [95.0, 96.9]}],   # dividend-adjusted (5% lower) — MUST NOT be baked in
        }}]}}


def test_rows_store_raw_unadjusted_close_not_adjclose_scaled():
    rows = _rows("AAPL", _payload())
    assert len(rows) == 2
    # RAW close preserved (100/102), NOT scaled toward adjclose (which would give ~95/96.9)
    assert rows[0]["close"] == 100.0
    assert rows[1]["close"] == 102.0
    assert rows[0]["open"] == 100.0 and rows[0]["high"] == 101.0 and rows[0]["low"] == 99.0


def test_rows_are_pit_dated_yyyymmdd_with_kt_eq_et():
    rows = _rows("AAPL", _payload())
    assert rows[0]["event_time"] == 20240101 and rows[0]["knowledge_time"] == 20240101
    assert rows[1]["event_time"] == 20240102


def test_rows_skip_bad_bars():
    p = _payload()
    p["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [None, 102.0]   # first bar unusable
    rows = _rows("AAPL", p)
    assert [r["event_time"] for r in rows] == [20240102]


def test_rows_ingest_pit_correctly(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "eq.db"), create=True)
    rows = _rows("AAPL", _payload())
    rep = ingest_provider_rows(rows, store, dataset_id="yahoo-AAPL", now=20240102, time_unit="yyyymmdd")
    assert rep.bars_ingested == 2
    # as-of 2024-01-01 sees only the first bar (no lookahead), and it is the RAW close
    seen = store.as_of("AAPL", as_of_time=20240101)
    assert len(seen) == 1 and seen[0].close == 100.0


def test_rows_ingest_rejects_wrong_time_scale(tmp_path):
    # an epoch-ms event_time must be fail-closed rejected under the yyyymmdd guard (no cross-scale mix)
    store = SqlitePITStore(os.path.join(str(tmp_path), "eq2.db"), create=True)
    bad = [{"instrument_id": "AAPL", "event_time": 1704067200000, "knowledge_time": 1704067200000,
            "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
    rep = ingest_provider_rows(bad, store, dataset_id="yahoo-AAPL", now=20240102, time_unit="yyyymmdd")
    assert rep.bars_ingested == 0 and len(rep.rejected) == 1
