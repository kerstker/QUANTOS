"""增量数据刷新(tools/live_data_refresh)可离线测的核心:水位、缺口、合并、staleness、fail-soft。
网络部分注入 fake fetch;不触真网。设计:docs/engineering/specs/2026-07-04-forward-track-live-design.md §2.2。"""
import json
import sqlite3
import threading
import time
from urllib.error import URLError

from tools.live_data_refresh import (_gap_days, merge_funding_days, price_watermarks,
                                     _normalize_child_result, _run_sources_with_deadlines,
                                     build_refresh_alerts,
                                     oi_quality_failures, refresh_funding, refresh_oi,
                                     refresh_prices, run_refresh, staleness_days)

_DAY = 86_400_000
_T0 = (1_780_000_000_000 // _DAY) * _DAY          # 对齐日网格的真实纪元毫秒


def _oi_record():
    return {"oi": 100.0, "oi_value": 1000.0, "top_ls": 1.1,
            "global_ls": 1.2, "taker_ls": 0.9}


def _deadline_test_worker(source, tickers, now_ms, result_queue):
    if source.startswith("hang"):
        time.sleep(30)
    if source == "crash":
        raise RuntimeError("child crashed")
    result_queue.put({
        "source": source,
        "ok": True,
        "report": {
            "source": source,
            "failed": [],
            "failure_details": [],
            "failed_tickers": [],
            "quality_failed": [],
            "watermark_after": now_ms,
        },
    })


def _seed_price_db(path, tickers, last_day):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
                 "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
                 "PRIMARY KEY (key, event_time, knowledge_time))")
    for tk in tickers:
        for d in range(3):
            t = last_day - d * _DAY
            conn.execute("INSERT INTO bars VALUES (?,?,?,?)",
                         (tk, t, t, json.dumps({"instrument_id": tk, "event_time": t,
                                                "open": 1, "high": 1, "low": 1, "close": 1,
                                                "volume": 1, "knowledge_time": t})))
    conn.commit()
    conn.close()


def test_gap_days_and_watermarks(tmp_path):
    db = str(tmp_path / "px.db")
    _seed_price_db(db, ["BTC", "ETH"], _T0)
    wm = price_watermarks(db)
    assert wm == {"BTC": _T0, "ETH": _T0}
    now = _T0 + 8 * _DAY + 3600_000
    assert _gap_days(_T0, now) == 8
    assert price_watermarks(str(tmp_path / "missing.db")) == {}       # 不存在 → 空(诚实)


def test_refresh_prices_skips_fresh_and_fails_soft(tmp_path):
    db = str(tmp_path / "px.db")
    now = _T0 + 8 * _DAY
    _seed_price_db(db, ["BTC", "ETH"], _T0)
    # BTC/ETH 落后 8 天 → 都该刷;BTC 的 fetch 抛异常 → fail-soft 记录,ETH 照常
    calls = []

    def fake_fetch(tk, *, days, now_ms):
        calls.append((tk, days))
        if tk == "BTC":
            raise RuntimeError("boom")
        return [{"instrument_id": tk, "event_time": _T0 + (i + 1) * _DAY, "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0, "volume": 1.0, "knowledge_time": _T0 + (i + 1) * _DAY}
                for i in range(7)]

    rep = refresh_prices(["BTC", "ETH"], now_ms=now, fetch=fake_fetch, db_path=db, sleep=0)
    assert [c[0] for c in calls] == ["BTC", "ETH"]
    assert calls[0][1] >= 8                                           # 缺口 + pad
    assert rep["refreshed"] == 1 and len(rep["failed"]) == 1 and "BTC" in rep["failed"][0]
    assert rep["failed_tickers"] == ["BTC"]
    assert rep["failure_details"][0]["kind"] == "SOURCE_ERROR"
    assert rep["ingested_rows"] == 7
    assert price_watermarks(db)["ETH"] == _T0 + 7 * _DAY              # 水位前移
    # 再跑一轮:ETH 已新鲜(gap<=1)则跳过 —— 幂等
    rep2 = refresh_prices(["ETH"], now_ms=_T0 + 8 * _DAY, fetch=fake_fetch, db_path=db, sleep=0)
    assert rep2["skipped"] == 1 and rep2["refreshed"] == 0


def test_refresh_prices_quarantines_gapped_batch_without_advancing_watermark(tmp_path):
    db = str(tmp_path / "px.db")
    now = _T0 + 5 * _DAY
    _seed_price_db(db, ["BTC"], _T0)

    def gapped_fetch(tk, *, days, now_ms):
        return [
            {"instrument_id": tk, "event_time": _T0 + _DAY,
             "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
             "volume": 1.0, "knowledge_time": _T0 + _DAY},
            {"instrument_id": tk, "event_time": _T0 + 3 * _DAY,
             "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
             "volume": 1.0, "knowledge_time": _T0 + 3 * _DAY},
        ]

    rep = refresh_prices(["BTC"], now_ms=now, fetch=gapped_fetch, db_path=db, sleep=0)
    assert rep["refreshed"] == 0
    assert rep["ingested_rows"] == 0
    assert rep["quality_failed"] == ["BTC"]
    assert rep["failure_details"][0]["kind"] == "QUALITY"
    assert len(rep["failed"]) == 1 and "completeness" in rep["failed"][0]
    assert price_watermarks(db)["BTC"] == _T0


def test_merge_funding_days_never_overwrites():
    existing = {str(_T0): 0.001}
    n = merge_funding_days(existing, {_T0: 999.0, _T0 + _DAY: 0.002})
    assert n == 1
    assert existing[str(_T0)] == 0.001                                # 历史事实不改写
    assert existing[str(_T0 + _DAY)] == 0.002


def test_refresh_funding_merges_and_reports(tmp_path):
    fp = str(tmp_path / "fund.json")
    json.dump({"BTC": {str(_T0): 0.001}}, open(fp, "w"))
    now = _T0 + 5 * _DAY

    def fake_fund(tk, *, days, now_ms):
        return {_T0 + i * _DAY: 0.0001 for i in range(1, 5)}

    rep = refresh_funding(["BTC"], now_ms=now, fetch=fake_fund, fund_path=fp, sleep=0)
    assert rep["refreshed"] == 1 and rep["new_days"] == 4
    saved = json.load(open(fp))
    assert len(saved["BTC"]) == 5
    assert rep["watermark_after"] == _T0 + 4 * _DAY


def test_refresh_funding_surfaces_fetch_failure_by_ticker(tmp_path):
    fp = str(tmp_path / "fund.json")
    original = {"BTC": {str(_T0): 0.001}}
    json.dump(original, open(fp, "w"))

    def failed_fetch(tk, *, days, now_ms):
        raise URLError("offline")

    rep = refresh_funding(
        ["BTC"],
        now_ms=_T0 + 5 * _DAY,
        fetch=failed_fetch,
        fund_path=fp,
        sleep=0,
    )

    assert rep["failed_tickers"] == ["BTC"]
    assert rep["failure_details"][0]["kind"] == "TRANSIENT"
    assert json.load(open(fp)) == original


def test_refresh_funding_fetches_with_bounded_concurrency(tmp_path):
    fp = str(tmp_path / "fund.json")
    original = {
        "BTC": {str(_T0): 0.001},
        "ETH": {str(_T0): 0.002},
    }
    json.dump(original, open(fp, "w"))
    rendezvous = threading.Barrier(2, timeout=1.0)

    def concurrent_fetch(tk, *, days, now_ms):
        rendezvous.wait()
        return {_T0 + i * _DAY: 0.0001 for i in range(1, 5)}

    rep = refresh_funding(
        ["BTC", "ETH"],
        now_ms=_T0 + 5 * _DAY,
        fetch=concurrent_fetch,
        fund_path=fp,
        sleep=0,
        max_workers=2,
    )

    assert rep["failed_tickers"] == []
    assert rep["refreshed"] == 2
    assert rep["new_days"] == 8
    saved = json.load(open(fp))
    assert list(saved) == ["BTC", "ETH"]
    assert len(saved["BTC"]) == len(saved["ETH"]) == 5


def test_refresh_funding_quarantines_gap_without_mutating_history(tmp_path):
    fp = str(tmp_path / "fund.json")
    original = {"BTC": {str(_T0): 0.001}}
    json.dump(original, open(fp, "w"))
    now = _T0 + 5 * _DAY

    def gapped_fund(tk, *, days, now_ms):
        return {_T0 + _DAY: 0.0001, _T0 + 3 * _DAY: 0.0002}

    rep = refresh_funding(["BTC"], now_ms=now, fetch=gapped_fund, fund_path=fp, sleep=0)
    assert rep["refreshed"] == 0 and rep["new_days"] == 0
    assert rep["quality_failed"] == ["BTC"]
    assert len(rep["failed"]) == 1 and "completeness" in rep["failed"][0]
    assert json.load(open(fp)) == original


def test_refresh_funding_quarantines_nonfinite_rate(tmp_path):
    fp = str(tmp_path / "fund.json")
    original = {"BTC": {str(_T0): 0.001}}
    json.dump(original, open(fp, "w"))
    now = _T0 + 2 * _DAY

    def invalid_fund(tk, *, days, now_ms):
        return {_T0 + _DAY: float("nan")}

    rep = refresh_funding(["BTC"], now_ms=now, fetch=invalid_fund, fund_path=fp, sleep=0)
    assert rep["quality_failed"] == ["BTC"]
    assert len(rep["failed"]) == 1 and "non-finite funding rate" in rep["failed"][0]
    assert json.load(open(fp)) == original


def test_staleness_summary_delisted_does_not_poison(tmp_path):
    db = str(tmp_path / "px.db")
    # BTC 新鲜、DEADCOIN 退市 800 天:survivorship-free 库的正常形态——
    # freshest 必须是 1(源还在更新),worst=800 与 n_stale=1 只是信息,不是"整源停更"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
                 "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
                 "PRIMARY KEY (key, event_time, knowledge_time))")
    conn.execute("INSERT INTO bars VALUES (?,?,?,?)", ("BTC", _T0, _T0, "{}"))
    conn.execute("INSERT INTO bars VALUES (?,?,?,?)",
                 ("DEADCOIN", _T0 - 800 * _DAY, _T0 - 800 * _DAY, "{}"))
    conn.commit()
    conn.close()
    fp = str(tmp_path / "fund.json")
    json.dump({"BTC": {str(_T0 - 2 * _DAY): 0.001}}, open(fp, "w"))
    oip = str(tmp_path / "oi.json")
    json.dump({"_meta": {"BTC": {"start": 0, "through": _T0 - 5 * _DAY}},
               "data": {"BTC": {str(_T0 - 5 * _DAY): _oi_record()}}}, open(oip, "w"))
    s = staleness_days(_T0 + _DAY, db_path=db, fund_path=fp, oi_path=oip)
    assert s["prices"] == {"freshest_days": 1, "worst_days": 801, "n_stale": 1, "n_total": 2}
    assert s["funding"]["freshest_days"] == 3 and s["funding"]["n_total"] == 1
    assert s["oi_metrics"]["freshest_days"] == 6
    s2 = staleness_days(_T0, db_path=str(tmp_path / "no.db"), fund_path=str(tmp_path / "no.json"),
                        oi_path=str(tmp_path / "no2.json"))
    for src in ("prices", "funding", "oi_metrics"):                   # 缺源 → None(诚实)
        assert s2[src]["freshest_days"] is None and s2[src]["n_total"] == 0


def test_staleness_summary_can_scope_forward_universe_without_erasing_archive(tmp_path):
    db = str(tmp_path / "px.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
                 "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
                 "PRIMARY KEY (key, event_time, knowledge_time))")
    conn.execute("INSERT INTO bars VALUES (?,?,?,?)", ("BTC", _T0, _T0, "{}"))
    conn.execute("INSERT INTO bars VALUES (?,?,?,?)",
                 ("DEADCOIN", _T0 - 800 * _DAY, _T0 - 800 * _DAY, "{}"))
    conn.commit()
    conn.close()
    fp = str(tmp_path / "fund.json")
    json.dump({"BTC": {str(_T0 - 2 * _DAY): 0.001},
               "DEADCOIN": {str(_T0 - 800 * _DAY): 0.001}}, open(fp, "w"))
    oip = str(tmp_path / "oi.json")
    json.dump({"data": {
        "BTC": {str(_T0 - 5 * _DAY): _oi_record()},
        "DEADCOIN": {str(_T0 - 800 * _DAY): _oi_record()},
    }}, open(oip, "w"))

    archive = staleness_days(_T0 + _DAY, db_path=db, fund_path=fp, oi_path=oip)
    scoped = staleness_days(
        _T0 + _DAY, db_path=db, fund_path=fp, oi_path=oip,
        active_tickers=["BTC"],
    )
    assert archive["prices"]["n_total"] == 2
    assert scoped["prices"] == {
        "freshest_days": 1, "worst_days": 1, "n_stale": 0, "n_total": 1
    }
    assert scoped["funding"]["n_total"] == 1
    assert scoped["oi_metrics"]["n_total"] == 1


def test_oi_quality_uses_actual_recent_ticker_data_not_meta(tmp_path):
    path = str(tmp_path / "oi.json")
    days = {str(_T0 - offset * _DAY): _oi_record() for offset in range(8)}
    json.dump({"_meta": {"BTC": {"through": _T0 + 100 * _DAY}},
               "data": {"BTC": days}}, open(path, "w"))
    assert oi_quality_failures(["BTC"], now_ms=_T0 + _DAY, oi_path=path) == []


def test_oi_quality_rejects_recent_gap_invalid_value_and_stale_ticker(tmp_path):
    path = str(tmp_path / "oi.json")
    good = {str(_T0 - offset * _DAY): _oi_record() for offset in range(8)}
    gapped = {key: dict(value) for key, value in good.items()}
    del gapped[str(_T0 - 2 * _DAY)]
    invalid = {key: dict(value) for key, value in good.items()}
    invalid[str(_T0)]["global_ls"] = float("nan")
    stale = {str(_T0 - (10 + offset) * _DAY): _oi_record() for offset in range(8)}
    json.dump({"_meta": {}, "data": {"GAP": gapped, "BAD": invalid, "STALE": stale}},
              open(path, "w"))
    assert oi_quality_failures(
        ["GAP", "BAD", "STALE", "MISSING"], now_ms=_T0 + _DAY, oi_path=path
    ) == ["BAD", "GAP", "MISSING", "STALE"]


def test_oi_staleness_reports_each_ticker_not_only_global_max(tmp_path):
    path = str(tmp_path / "oi.json")
    json.dump({"_meta": {}, "data": {
        "FRESH": {str(_T0): _oi_record()},
        "STALE": {str(_T0 - 10 * _DAY): _oi_record()},
    }}, open(path, "w"))
    summary = staleness_days(
        _T0 + _DAY,
        db_path=str(tmp_path / "missing.db"),
        fund_path=str(tmp_path / "missing.json"),
        oi_path=path,
    )["oi_metrics"]
    assert summary == {"freshest_days": 1, "worst_days": 11, "n_stale": 1, "n_total": 2}


def test_refresh_oi_surfaces_quality_failures_from_written_cache(tmp_path):
    path = str(tmp_path / "oi.json")

    def fake_refresh(tickers):
        json.dump({"_meta": {}, "data": {
            "BTC": {str(_T0): {"oi": 100.0}},
        }}, open(path, "w"))

    report = refresh_oi(
        ["BTC"], now_ms=_T0 + _DAY, fetch=fake_refresh, oi_path=path
    )
    assert report["watermark_before"] is None
    assert report["watermark_after"] == _T0
    assert report["quality_failed"] == ["BTC"]


def test_source_process_deadline_terminates_worker_and_blocks_full_universe():
    started = time.monotonic()
    reports = _run_sources_with_deadlines(
        {"ok": ["BTC"], "hang_source": ["ETH", "SOL"]},
        now_ms=_T0,
        # Windows spawn imports the full test module in each child. Keep the
        # non-target worker budget above cold-start jitter under the full gate;
        # the hanging source still has the 200ms deadline this test exercises.
        source_timeouts={"ok": 5.0, "hang_source": 0.2},
        global_timeout_s=5.0,
        worker_target=_deadline_test_worker,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 6.0
    assert reports[0]["source"] == "ok"
    assert reports[0]["outcome"] == "PASS"
    timed_out = reports[1]
    assert timed_out["outcome"] == "BUDGET_EXCEEDED"
    assert timed_out["budget_scope"] == "SOURCE"
    assert timed_out["failed_tickers"] == ["ETH", "SOL"]
    assert timed_out["failure_details"][0]["kind"] == "BUDGET_EXCEEDED"


def test_global_deadline_terminates_every_unfinished_source():
    started = time.monotonic()
    reports = _run_sources_with_deadlines(
        {"hang_one": ["BTC"], "hang_two": ["ETH"]},
        now_ms=_T0,
        source_timeouts={"hang_one": 5.0, "hang_two": 5.0},
        global_timeout_s=0.2,
        worker_target=_deadline_test_worker,
    )

    assert time.monotonic() - started < 3.0
    assert {report["outcome"] for report in reports} == {"BUDGET_EXCEEDED"}
    assert {report["budget_scope"] for report in reports} == {"GLOBAL"}


def test_source_process_crash_is_structured_and_fail_closed():
    reports = _run_sources_with_deadlines(
        {"crash": ["BTC", "ETH"]},
        now_ms=_T0,
        source_timeouts={"crash": 2.0},
        global_timeout_s=2.0,
        worker_target=_deadline_test_worker,
    )
    crashed = reports[0]
    assert crashed["outcome"] == "CRASHED"
    assert crashed["failed_tickers"] == ["BTC", "ETH"]
    assert crashed["failure_details"][0]["kind"] == "SOURCE_CRASHED"


def test_not_found_archive_probe_is_warning_when_cached_quality_passes():
    report = _normalize_child_result(
        "perp_mark_prices",
        ["BTC"],
        {
            "source": "perp_mark_prices",
            "ok": True,
            "report": {
                "source": "perp_mark_prices",
                "failed": ["BTC: archive not published"],
                "failure_details": [{
                    "ticker": "BTC",
                    "kind": "NOT_FOUND",
                    "error_type": "HTTPError",
                    "message": "archive not published",
                }],
                "failed_tickers": [],
                "quality_failed": [],
            },
        },
        duration_ms=10,
    )
    assert report["outcome"] == "PASS"
    assert report["failed"] == []
    assert report["failure_details"] == []
    assert report["warnings"] == ["BTC: NOT_FOUND: archive not published"]
    assert report["warning_details"][0]["kind"] == "NOT_FOUND"


def test_refresh_alerts_include_budget_and_consecutive_failure_breach():
    status = {
        "reports": [{
            "source": "funding",
            "outcome": "BUDGET_EXCEEDED",
            "budget_scope": "SOURCE",
        }],
    }
    slo = {
        "violations": [{
            "code": "CONSECUTIVE_FAILURES",
            "source": "funding",
            "observed": 3,
            "threshold": 3,
        }],
    }
    alerts = build_refresh_alerts(status, slo)
    assert alerts == [
        "refresh funding exceeded SOURCE wall-clock budget",
        "refresh funding failed 3 consecutive runs (threshold 3)",
    ]


def test_run_refresh_persists_history_slo_and_atomic_status(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"instruments": ["BTC"]}), encoding="utf-8")
    stale = {
        source: {"freshest_days": 1, "worst_days": 1, "n_stale": 0, "n_total": 1}
        for source in ("prices", "funding", "perp_mark_prices", "oi_metrics", "mexc_btc")
    }

    def fake_runner(source_tickers, **kwargs):
        return [
            {
                "source": source,
                "outcome": "PASS",
                "duration_ms": 1,
                "failed": [],
                "failure_details": [],
                "failed_tickers": [],
                "quality_failed": [],
                "watermark_after": _T0,
            }
            for source in source_tickers
        ]

    delivered = []

    def fake_alert(alerts, **kwargs):
        delivered.extend(alerts)
        return {"n": len(alerts), "pushed": False}

    status_path = str(tmp_path / "status.json")
    slo_path = str(tmp_path / "slo.json")
    status = run_refresh(
        now_ms=_T0,
        snapshot_path=str(snapshot),
        history_path=str(tmp_path / "history.db"),
        status_path=status_path,
        slo_status_path=slo_path,
        event_bus_path=str(tmp_path / "events.db"),
        event_projection_path=str(tmp_path / "projection.db"),
        source_runner=fake_runner,
        staleness_reader=lambda now_ms: stale,
        archive_summary=lambda root: {"verdict": "PASS", "aggregate_hash": "abc"},
        alert_emitter=fake_alert,
    )

    assert status["outcome"] == "PASS"
    assert status["history"]["chain_valid"] is True
    assert status["history"]["run_count"] == 1
    assert status["slo"]["verdict"] == "INSUFFICIENT_HISTORY"
    assert status["event_delivery"]["dispatch"] == {
        "dispatched": 1,
        "pending": 0,
    }
    assert status["event_delivery"]["projection"]["projected"] == 1
    assert delivered == []
    assert json.load(open(status_path, encoding="utf-8"))["history"]["entry_seq"] == 1
    assert json.load(open(slo_path, encoding="utf-8"))["provenance"] == "REAL_REFRESH_HISTORY"


# --- MEXC 现货 BTC(第 5 源,2026-07 MEXC 对接 1.1)---

from tools.live_data_refresh import refresh_mexc_btc


def _mexc_rows(start_day, n):
    return [{"instrument_id": "BTC", "event_time": start_day + i * _DAY,
             "knowledge_time": start_day + i * _DAY,
             "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
            for i in range(n)]


def test_refresh_mexc_btc_ingests_and_writes_snapshot_then_skips(tmp_path):
    db = str(tmp_path / "crypto_mexc.db")
    snap = str(tmp_path / "mexc_btc_klines.json")
    now = _T0 + 5 * _DAY
    calls = []

    def fake_fetch(tk, *, days, now_ms):
        calls.append((tk, days))
        return _mexc_rows(_T0 + 2 * _DAY, 3)          # .. _T0+4*_DAY = expected_end

    rep = refresh_mexc_btc(now_ms=now, fetch=fake_fetch, db_path=db, snapshot_path=snap)
    assert rep["refreshed"] == 1 and rep["ingested_rows"] == 3
    assert rep["watermark_after"] == _T0 + 4 * _DAY
    assert calls and calls[0][0] == "BTC" and calls[0][1] >= 8   # 空库首跑:全量缺口 + pad
    payload = json.load(open(snap, encoding="utf-8"))
    assert payload["provenance"] == "REAL_MEXC_PUBLIC_KLINES"
    assert payload["rows"] == 3
    assert payload["first_event_time"] == _T0 + 2 * _DAY
    assert payload["last_event_time"] == _T0 + 4 * _DAY
    # 幂等:水位只落后 1 天(未收盘当根,PIT 本来就丢)→ 跳过,不再抓
    rep2 = refresh_mexc_btc(now_ms=now, fetch=fake_fetch, db_path=db, snapshot_path=snap)
    assert rep2["skipped"] == 1 and rep2["refreshed"] == 0
    assert len(calls) == 1


def test_refresh_mexc_btc_fails_soft_and_still_writes_empty_snapshot(tmp_path):
    db = str(tmp_path / "missing.db")
    snap = str(tmp_path / "snap.json")

    def boom(tk, *, days, now_ms):
        raise RuntimeError("offline")
    rep = refresh_mexc_btc(now_ms=_T0 + 5 * _DAY, fetch=boom, db_path=db, snapshot_path=snap)
    assert rep["refreshed"] == 0 and rep["failed_tickers"] == ["BTC"]
    payload = json.load(open(snap, encoding="utf-8"))
    assert payload["rows"] == 0 and payload["first_event_time"] is None   # 诚实空快照


def test_staleness_days_includes_mexc_btc_source(tmp_path):
    out = staleness_days(_T0, db_path=str(tmp_path / "no.db"),
                         fund_path=str(tmp_path / "no.json"),
                         oi_path=str(tmp_path / "no2.json"),
                         mexc_db_path=str(tmp_path / "no3.db"))
    assert "mexc_btc" in out and out["mexc_btc"]["n_total"] == 0


def test_run_refresh_includes_mexc_btc_in_source_tickers_and_scope(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"instruments": ["BTC"]}), encoding="utf-8")
    captured = {}

    def fake_runner(source_tickers, **kwargs):
        captured["source_tickers"] = dict(source_tickers)
        return [{"source": s, "outcome": "PASS", "duration_ms": 1, "failed": [],
                 "failure_details": [], "failed_tickers": [], "quality_failed": [],
                 "watermark_after": _T0} for s in source_tickers]

    status = run_refresh(
        now_ms=_T0, snapshot_path=str(snapshot),
        history_path=str(tmp_path / "h.db"), status_path=str(tmp_path / "s.json"),
        slo_status_path=str(tmp_path / "slo.json"),
        event_bus_path=str(tmp_path / "e.db"),
        event_projection_path=str(tmp_path / "p.db"),
        source_runner=fake_runner,
        staleness_reader=lambda now_ms: {},
        archive_summary=lambda root: {},
        alert_emitter=lambda alerts, **kw: {"n": 0},
    )
    assert captured["source_tickers"]["mexc_btc"] == ["BTC"]
    assert status["staleness_scope"]["mexc_btc"] == ["BTC"]
    assert status["budgets"]["source_timeout_seconds"]["mexc_btc"] > 0
