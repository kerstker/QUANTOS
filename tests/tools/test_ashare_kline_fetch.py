"""Unit tests for tools/ashare_kline_fetch.py — FAKE cli runner, NO real node calls."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from services.data.persistent_store import SqlitePITStore  # noqa: E402
from tools import ashare_kline_fetch as f  # noqa: E402

CODE = "002858.SZ"


def _csv_rows(dates):
    return [{"trade_date": d, "wind_code": CODE, "open": "10.0", "high": "10.5",
             "low": "9.5", "close": "10.2", "volume": "1000", "amt": "10200"}
            for d in dates]


def _csv_text(rows):
    head = "trade_date,wind_code,open,high,low,close,volume,amt\n"
    return head + "".join(
        f"{r['trade_date']},{r['wind_code']},{r['open']},{r['high']},{r['low']},"
        f"{r['close']},{r['volume']},{r['amt']}\n" for r in rows)


def _stdout_for(csv_path, total):
    notice_path = (
        csv_path.replace(os.sep, chr(92) * 2)
        if os.name == "nt"
        else csv_path
    )
    inner = json.dumps({
        "is_success": "wind, Wind historical price fetched for 1 ticker(s)",
        "data_preview": "trade_date,wind_code,...(truncated)",
        "notice": (f"Detailed and complete data saved to {notice_path}. "
                   f"Total records: {total}."),
    })
    repr_text = repr({"type": "text", "text": inner})
    return json.dumps({"content": [{"type": "text", "text": repr_text}]})


def _write_csv(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text(_csv_text(rows), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# stdout / notice parsing
# --------------------------------------------------------------------------- #

def test_parse_cli_stdout_extracts_notice_path(tmp_path):
    csv_path = _write_csv(tmp_path, "wind_get_price_x.csv", _csv_rows(["2026-07-23"]))
    out = f.parse_cli_stdout(_stdout_for(csv_path, 1))
    assert out["total_records"] == 1
    assert out["csv_path"] is not None
    assert out["csv_path"].endswith("wind_get_price_x.csv")
    assert "\\\\" not in out["csv_path"]          # backslashes unescaped
    rows = f.read_kline_csv(out["csv_path"])
    assert rows[0]["close"] == "10.2"


def test_parse_cli_stdout_ratelimit_and_error_envelopes():
    with pytest.raises(f.RateLimitError):
        f.parse_cli_stdout(json.dumps({
            "ok": False,
            "error": {"code": "NETWORK_ERROR",
                      "agent_action": "API_CALL_ERROR - Too many requests."}}))
    with pytest.raises(RuntimeError, match="cli error"):
        f.parse_cli_stdout(json.dumps({
            "ok": False, "error": {"code": "BAD_PARAMS", "agent_action": "x"}}))
    # missing notice path -> csv_path None (fetch_stock_rows turns it into an error)
    out = f.parse_cli_stdout(json.dumps({"content": [{"type": "text", "text": repr(
        {"type": "text", "text": json.dumps({"is_success": "ok", "notice": "no path"})})}]}))
    assert out["csv_path"] is None


# --------------------------------------------------------------------------- #
# merge / canonical rows / future guard
# --------------------------------------------------------------------------- #

def test_merge_rows_dedups_by_trade_date():
    a = _csv_rows(["2026-07-21", "2026-07-22"])
    b = _csv_rows(["2026-07-22", "2026-07-23"])
    merged = f.merge_rows(a, b)
    assert [r["trade_date"] for r in merged] == ["2026-07-21", "2026-07-22", "2026-07-23"]


def test_rows_to_canonical_drops_future_and_sets_bitemporal():
    rows = _csv_rows(["2026-07-22", "2026-07-23", "2026-07-24"])
    canonical, dropped = f.rows_to_canonical(CODE, rows, end_int=20260723)
    assert dropped == 1
    assert len(canonical) == 2
    r = canonical[0]
    assert r["instrument_id"] == CODE
    assert r["event_time"] == 20260722
    assert r["knowledge_time"] == r["event_time"]       # EOD-known
    assert r["close"] == "10.2"


# --------------------------------------------------------------------------- #
# ingestion dedup + PIT reads
# --------------------------------------------------------------------------- #

def test_ingestion_dedups_on_rerun(tmp_path):
    store = SqlitePITStore(str(tmp_path / "t.db"), create=True)
    rows = _csv_rows(["2026-07-21", "2026-07-22", "2026-07-23"])
    canonical, _ = f.rows_to_canonical(CODE, rows)
    from services.data.market_provider import ingest_provider_rows
    rep1 = ingest_provider_rows(canonical, store, dataset_id="ashare-x",
                                time_unit="yyyymmdd")
    rep2 = ingest_provider_rows(canonical, store, dataset_id="ashare-x",
                                time_unit="yyyymmdd")
    assert rep1.bars_ingested == 3
    assert rep2.bars_ingested == 0 and rep2.duplicates == 3
    bar = store.latest_as_of(CODE, 99999999)
    assert bar.event_time == 20260723 and abs(bar.close - 10.2) < 1e-9
    assert len(store.as_of(CODE, 20260722)) == 2        # PIT: as-of Wed excludes Thu


# --------------------------------------------------------------------------- #
# batch: resume-skip + failure recording (fake runner)
# --------------------------------------------------------------------------- #

def _universe(codes):
    return [{"code": c} for c in codes]


def test_run_batch_resume_skip_and_failures(tmp_path):
    store = SqlitePITStore(str(tmp_path / "t.db"), create=True)
    progress = str(tmp_path / "progress.json")
    raw_dir = str(tmp_path / "raw")
    calls = []

    def runner(args_json):
        code = json.loads(args_json)["windcode"]
        calls.append(code)
        if code == "BAD.SZ":
            raise RuntimeError("boom")
        csv_path = _write_csv(tmp_path, f"{code.replace('.', '_')}.csv",
                              _csv_rows(["2026-07-22", "2026-07-23"]))
        return _stdout_for(csv_path, 2)

    uni = _universe(["A.SZ", "BAD.SZ", "B.SZ"])
    res1 = f.run_batch(uni, store, raw_dir=raw_dir, progress_path=progress,
                       runner=runner, sleep=lambda s: None)
    assert res1["done"] == ["A.SZ", "B.SZ"]
    assert "BAD.SZ" in res1["failed"]
    assert os.path.exists(os.path.join(raw_dir, "A.SZ.csv"))

    # resume: done codes are skipped, BAD retried (3 attempts = 1 + 2 retries)
    calls.clear()
    res2 = f.run_batch(uni, store, raw_dir=raw_dir, progress_path=progress,
                       runner=runner, sleep=lambda s: None)
    assert calls and all(c == "BAD.SZ" for c in calls)
    assert res2["done"] == ["A.SZ", "B.SZ"]


def test_run_batch_ratelimit_aborts_chunk(tmp_path):
    store = SqlitePITStore(str(tmp_path / "t.db"), create=True)

    def runner(args_json):
        raise f.RateLimitError("NETWORK_ERROR: Too many requests")

    res = f.run_batch(_universe(["A.SZ", "B.SZ", "C.SZ"]), store,
                      raw_dir=str(tmp_path / "raw"),
                      progress_path=str(tmp_path / "progress.json"),
                      runner=runner, sleep=lambda s: None)
    assert res["aborted_ratelimit"] is True
    assert res["done"] == [] and len(res["failed"]) == 1   # stopped after first


def test_fetch_stock_rows_paginates_when_history_truncated(tmp_path):
    # first call returns only 2026 rows (history "truncated") -> yearly pagination
    pages = {}

    def runner(args_json):
        a = json.loads(args_json)
        key = (a["begin_date"], a["end_date"])
        if key == ("20210101", "20260723"):
            rows = _csv_rows(["2026-07-22", "2026-07-23"])      # truncated
        else:
            year = key[0][:4]
            rows = _csv_rows([f"{year}-06-30"])
            pages[key] = True
        p = _write_csv(tmp_path, f"p_{len(pages)}_{key[0]}.csv", rows)
        return _stdout_for(p, len(rows))

    rows = f.fetch_stock_rows(CODE, runner=runner, sleep=lambda s: None)
    dates = [r["trade_date"] for r in rows]
    assert dates == sorted(set(dates))
    assert any(d.startswith("2021") for d in dates)             # pagination kicked in
    assert len(pages) >= 5                                      # yearly ranges queried
