"""Unit tests for tools/ashare_ifind_fetch.py — FAKE runner, NO real iFinD calls."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from services.data.persistent_store import SqlitePITStore  # noqa: E402
from tools import ashare_ifind_fetch as f  # noqa: E402

CODES = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ",
         "000005.SZ", "000006.SZ", "000007.SZ"]


def _csv_text(rows):
    head = "open,high,low,close,volume,thscode,time,thsname_cn,thsname_en,currency\n"
    return head + "".join(
        f"{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']},"
        f"{r['thscode']},{r['time']},X,NA,CNY\n" for r in rows)


def _row(code, yyyymmdd, close="10.2"):
    return {"open": "10.0", "high": "10.5", "low": "9.5", "close": close,
            "volume": "1000", "thscode": code, "time": str(yyyymmdd)}


def _stdout_ok(n_rows):
    inner = json.dumps({
        "is_success": "ifind, ['# HTTP get_price OK', 'ticker: x']",
        "data_preview": f"open,high,...(50 rows here, {n_rows} rows in total)",
    })
    return repr({"type": "text", "text": inner})


def make_fake_runner(rows_by_range, calls):
    """Fake runner: writes the CSV for (tickers, range) to params.file_path and
    returns a success envelope. rows_by_range: {(start_date,end_date): [rows]} —
    the same rows are emitted for every requested ticker (with thscode rewritten)."""
    def runner(params_json):
        params = json.loads(params_json)
        calls.append(params)
        tickers = params["ticker"].split(",")
        key = (params["start_date"], params["end_date"])
        rows = [dict(r, thscode=t) for t in tickers for r in rows_by_range[key]]
        with open(params["file_path"], "w", encoding="utf-8", newline="") as fh:
            fh.write(_csv_text(rows))
        return _stdout_ok(len(rows))
    return runner


@pytest.fixture()
def store(tmp_path):
    return SqlitePITStore(str(tmp_path / "test.db"), create=True)


# --------------------------------------------------------------------------- #
# stdout parsing
# --------------------------------------------------------------------------- #

def test_parse_tool_stdout_success_and_total():
    out = f.parse_tool_stdout(_stdout_ok(2171))
    assert "OK" in out["is_success"]
    assert out["total_rows"] == 2171


def test_parse_tool_stdout_ratelimit_and_failure():
    with pytest.raises(f.RateLimitError):
        f.parse_tool_stdout("... Too many requests ...")
    with pytest.raises(f.RateLimitError):
        f.parse_tool_stdout("API_CALL_ERROR - HTTP 429 quota exceeded")
    with pytest.raises(RuntimeError, match="ifind tool failure"):
        f.parse_tool_stdout(repr({"type": "text", "text": json.dumps(
            {"is_success": "ifind, ['# HTTP get_price FAIL: bad ticker']",
             "data_preview": ""})}))


def test_parse_tool_stdout_429_in_csv_data_is_not_ratelimit():
    """Regression: volume/price digits '429' inside the data_preview must NOT be
    read as an HTTP 429 quota error (real incident 2026-07-23)."""
    inner = json.dumps({
        "is_success": "ifind, ['# HTTP get_price OK']",
        "data_preview": "open,high,low,close,volume,thscode,time\\n"
                        "31.92,43.82,31.27,41.28,4296600.0,688592.SH,20230818",
    })
    out = f.parse_tool_stdout(repr({"type": "text", "text": inner}))
    assert "OK" in out["is_success"]


# --------------------------------------------------------------------------- #
# batching / ranges / canonical conversion
# --------------------------------------------------------------------------- #

def test_batches_of_3():
    groups = f.batches_of(CODES)
    assert [len(g) for g in groups] == [3, 3, 1]
    assert [c for g in groups for c in g] == CODES     # order preserved


def test_ranges_split_covers_window_max_3y_disjoint():
    assert len(f.RANGES) == 2
    assert f.RANGES[0] == ("2021-01-01", "2023-12-31")
    assert f.RANGES[1] == ("2024-01-01", "2026-07-23")
    for start, end in f.RANGES:
        assert int(end[:4]) - int(start[:4]) <= 2      # ≤ 3-year span per call
    assert f.RANGES[0][1] < f.RANGES[1][0]             # disjoint


def test_ifind_rows_to_canonical_dedup_and_future_guard():
    rows = [_row("000001.SZ", 20240102), _row("000001.SZ", 20240102, close="99"),
            _row("000001.SZ", 20240103), _row("000001.SZ", 20260724),  # future
            _row("000002.SZ", 20240102)]
    canonical, dropped = f.ifind_rows_to_canonical(rows)
    assert dropped == 1
    assert len(canonical) == 3                          # dedup (code,date) first-wins
    c0 = [c for c in canonical if c["instrument_id"] == "000001.SZ"
          and c["event_time"] == 20240102][0]
    assert c0["close"] == "10.2"                        # first wins
    assert c0["knowledge_time"] == 20240102
    assert set(c0) == {"instrument_id", "event_time", "knowledge_time",
                       "open", "high", "low", "close", "volume"}   # no amt


# --------------------------------------------------------------------------- #
# fetch group + ingestion (fake runner)
# --------------------------------------------------------------------------- #

def test_fetch_group_rows_two_calls_per_group(tmp_path):
    calls = []
    rows_by_range = {("2021-01-01", "2023-12-31"): [_row("X", 20210104)],
                     ("2024-01-01", "2026-07-23"): [_row("X", 20240102)]}
    runner = make_fake_runner(rows_by_range, calls)
    rows = f.fetch_group_rows(CODES[:3], runner=runner, raw_dir=str(tmp_path),
                              sleep=lambda s: None)
    assert len(calls) == 2                              # 3 tickers share 2 range calls
    assert all(c["ticker"] == ",".join(CODES[:3]) for c in calls)
    assert len(rows) == 2 * 3                           # 2 ranges × 3 tickers
    assert len(list(tmp_path.glob("*.csv"))) == 2       # raw mirrors


def test_ingest_dedup_second_ingest_all_duplicates(store):
    rows = [_row("000001.SZ", 20240102), _row("000001.SZ", 20240103)]
    canonical, _ = f.ifind_rows_to_canonical(rows)
    from services.data.market_provider import ingest_provider_rows
    rep1 = ingest_provider_rows(canonical, store, dataset_id="ashare-000001.SZ",
                                time_unit="yyyymmdd")
    rep2 = ingest_provider_rows(canonical, store, dataset_id="ashare-000001.SZ",
                                time_unit="yyyymmdd")
    assert rep1.bars_ingested == 2
    assert rep2.bars_ingested == 0 and rep2.duplicates == 2
    assert len(store.fetch_bars("000001.SZ", 0, 10**18)) == 2


# --------------------------------------------------------------------------- #
# batch driver: resume-skip + source tagging
# --------------------------------------------------------------------------- #

def _universe(codes):
    return [{"code": c, "name": c, "mcap_yi": 1.0, "adv_yi": 1.0} for c in codes]


def test_run_batch_resume_skip(store, tmp_path):
    # 000001.SZ already in PIT (live check), 000002.SZ already done in checkpoint
    from services.data.market_provider import ingest_provider_rows
    canonical, _ = f.ifind_rows_to_canonical([_row("000001.SZ", 20240102)])
    ingest_provider_rows(canonical, store, dataset_id="ashare-000001.SZ",
                         time_unit="yyyymmdd")
    prog_path = str(tmp_path / "progress.json")
    f._save_progress(prog_path, {"done": ["000002.SZ"], "failed": {}})
    universe = _universe(CODES[:4])
    todo = f.missing_codes(universe, store)
    assert todo == CODES[1:4]                           # 000001.SZ skipped (in PIT)
    calls = []
    rows_by_range = {(s, e): [_row("X", int(s[:4] + "0104"))] for s, e in f.RANGES}
    runner = make_fake_runner(rows_by_range, calls)
    result = f.run_batch(todo, store, raw_dir=str(tmp_path / "raw"),
                         progress_path=prog_path, runner=runner,
                         sleep=lambda s: None, log=lambda m: None)
    requested = {t for c in calls for t in c["ticker"].split(",")}
    assert "000002.SZ" not in requested                 # checkpoint done → skipped
    assert requested == {"000003.SZ", "000004.SZ"}
    assert set(result["done"]) == {"000002.SZ", "000003.SZ", "000004.SZ"}


def test_run_batch_records_failure_and_keeps_going(store, tmp_path):
    def bad_runner(params_json):
        params = json.loads(params_json)
        if "000001.SZ" in params["ticker"]:
            raise RuntimeError("boom")
        return make_fake_runner({(params["start_date"], params["end_date"]):
                                 [_row("X", 20240102)]}, [])(params_json)
    result = f.run_batch(CODES[:4], store, raw_dir=str(tmp_path / "raw"),
                         progress_path=str(tmp_path / "p.json"), runner=bad_runner,
                         sleep=lambda s: None, log=lambda m: None)
    assert set(result["failed"]) == set(CODES[:3])      # group 1 failed wholesale
    assert result["done"] == ["000004.SZ"]              # group 2 still processed


def test_snapshot_source_tagging(store, tmp_path):
    from services.data.market_provider import ingest_provider_rows
    # wind-sourced stock: in PIT, not in ifind done set
    wind_rows, _ = f.ifind_rows_to_canonical([_row("000001.SZ", 20240102)])
    ingest_provider_rows(wind_rows, store, dataset_id="ashare-000001.SZ",
                         time_unit="yyyymmdd")
    # ifind-sourced stock: in PIT AND in ifind done set
    ifind_rows, _ = f.ifind_rows_to_canonical([_row("000002.SZ", 20240102)])
    ingest_provider_rows(ifind_rows, store, dataset_id="ashare-000002.SZ",
                         time_unit="yyyymmdd")
    snap = f.write_snapshot(store, _universe(CODES[:3]), {"000002.SZ"},
                            path=str(tmp_path / "snap.json"), fetched_at="now")
    ps = snap["per_stock"]
    assert ps["000001.SZ"]["source"] == "wind"
    assert ps["000002.SZ"]["source"] == "ifind"
    assert ps["000003.SZ"]["source"] == "missing" and ps["000003.SZ"]["rows"] == 0
    assert snap["wind_stocks"] == 1 and snap["ifind_stocks"] == 1
    assert snap["stocks_with_data"] == 2 and snap["total_rows"] == 2
    assert "amt" in snap["notes"] and "source_mix" in snap["notes"]
