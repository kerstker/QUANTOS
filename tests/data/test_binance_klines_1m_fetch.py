"""1m(分钟线)K线抓取器(tools/binance_klines_1m_fetch.py):1H 抓取器的同构变体,唯一关键偏离
——与 1H 相同——是 knowledge_lag=收盘(防日内 as_of=开盘纳入未收盘 bar 的前视);在 1 分钟颗粒度下
这个前视 bug 更致命(逐 bar 交易的策略每根 bar 都会算错可知性)。cadence-shift-1m 纯加法,不改
binance_klines_fetch.py / binance_klines_1h_fetch.py 一行。"""
import os

import pytest

from services.data.persistent_store import SqlitePITStore
from tools.binance_klines_1m_fetch import (BN1M_SPEC, _parse_args, fetch_minute_rows,
                                           parse_klines_1m)

_MIN = 60_000
# minute-aligned realistic ms base (ms-range validation rejects toy timestamps — real bars are ~1.7e12)
_BASE = (1_700_000_000_000 // _MIN) * _MIN


def _kline(open_ms, o, h, low, c, v):
    # Binance kline = [openTime, o, h, l, c, v, closeTime, ...]
    return [open_ms, str(o), str(h), str(low), str(c), str(v), open_ms + _MIN - 1]


def _fake_http(batch):
    def _get(url, headers=None):
        return batch
    return _get


def _counting_http(fn):
    calls = []

    def _get(url, headers=None):
        calls.append(url)
        return fn(url)
    _get.calls = calls
    return _get


def _paged_http(all_klines):
    """Stand-in for the real Binance klines endpoint: honors `endTime`/`limit` from the URL query
    string the way the real API does — eligible = openTime <= endTime, then the most recent
    `limit` of those, returned ascending. Used to prove multi-page pagination actually stitches."""
    def _get(url, headers=None):
        query = url.split("?", 1)[-1]
        end_time = None
        limit = 1000
        for part in query.split("&"):
            if part.startswith("endTime="):
                end_time = int(part.split("=", 1)[1])
            elif part.startswith("limit="):
                limit = int(part.split("=", 1)[1])
        eligible = [k for k in all_klines if end_time is None or k[0] <= end_time]
        eligible = sorted(eligible, key=lambda k: k[0])
        return eligible[-limit:] if limit else eligible
    return _get


# ---------------------------------------------------------------------------
# knowledge_lag — the single most important assertion in this file
# ---------------------------------------------------------------------------

def test_knowledge_lag_is_one_minute_close_not_open():
    # 关键防前视:kt = event_time + 60_000 = 收盘时刻(刻意偏离日频 BN_SPEC.knowledge_lag=0,
    # 与 1H 版本 BN1H_SPEC.knowledge_lag=_HOUR_MS 同一条修复,只是换成分钟粒度)。
    assert BN1M_SPEC.knowledge_lag == _MIN
    assert BN1M_SPEC.knowledge_lag != 0


# ---------------------------------------------------------------------------
# parse_klines_1m
# ---------------------------------------------------------------------------

def test_parse_snaps_to_minute_grid():
    batch = [_kline(_BASE + 17, 1.0, 2.0, 0.5, 1.5, 100.0)]         # ts slightly off the minute grid
    out = parse_klines_1m(batch, instrument_id="BTC")
    assert set(out) == {_BASE}                                     # snapped to the minute bucket
    assert out[_BASE]["c"] == 1.5 and out[_BASE]["sym"] == "BTC"


def test_parse_drops_malformed_and_non_positive_rows():
    good = _kline(_BASE, 1.0, 2.0, 0.5, 1.5, 100.0)
    zero_close = _kline(_BASE + _MIN, 1.0, 2.0, 0.5, 0.0, 100.0)
    short = [_BASE + 2 * _MIN, "1", "2"]                            # too few fields
    out = parse_klines_1m([good, zero_close, short], instrument_id="BTC")
    assert set(out) == {_BASE}


def test_parse_non_list_batch_returns_empty():
    assert parse_klines_1m(None, instrument_id="BTC") == {}
    assert parse_klines_1m({"not": "a list"}, instrument_id="BTC") == {}


# ---------------------------------------------------------------------------
# fetch_minute_rows — pagination stitching
# ---------------------------------------------------------------------------

def test_fetch_stitches_multiple_pages_correctly():
    # 1500 连续分钟 bar,超过单页上限 1000 -> 必须两页拼接才能拿全
    n = 1500
    start_open = _BASE - n * _MIN
    all_klines = [_kline(start_open + i * _MIN, 1 + i * 0.01, 2, 0.5, 1 + i * 0.01, 10)
                 for i in range(n)]
    http = _counting_http(_paged_http(all_klines))
    rows = fetch_minute_rows("BTC", start_ms=start_open, end_ms=_BASE - 1,
                             http_get=http, now_ms=_BASE)
    assert len(rows) == n                                           # 全部 1500 根都拿到,零缺漏零重复
    ets = sorted(r["event_time"] for r in rows)
    assert ets == sorted(set(ets))                                  # 无重复 bucket
    assert ets[0] == start_open and ets[-1] == _BASE - _MIN
    assert len(http.calls) >= 2                                     # 确实分了至少两页,不是单页凑巧对上


# ---------------------------------------------------------------------------
# fetch_minute_rows — no-lookahead / live-minute drop (mirrors the 1H test)
# ---------------------------------------------------------------------------

def test_fetch_discards_the_live_unclosed_minute():
    now = _BASE + 12345                                             # 当前在 _BASE 这根 bar 中途(未收盘)
    batch = [_kline(_BASE - 2 * _MIN, 1, 1, 1, 1, 1), _kline(_BASE - _MIN, 1, 1, 1, 1, 1),
             _kline(_BASE, 1, 1, 1, 1, 1)]                          # _BASE 那根 = 活 bar,必须丢
    rows = fetch_minute_rows("BTC", start_ms=_BASE - 10 * _MIN, end_ms=_BASE,
                             http_get=_fake_http(batch), now_ms=now)
    ets = {r["event_time"] for r in rows}
    assert _BASE not in ets                                         # 活分钟 bar 被丢弃(无前视)
    assert (_BASE - 2 * _MIN) in ets and (_BASE - _MIN) in ets


def test_normalized_rows_have_close_knowledge_time():
    now = _BASE
    batch = [_kline(_BASE - 2 * _MIN, 1, 2, 0.5, 1.5, 100)]
    rows = fetch_minute_rows("BTC", start_ms=_BASE - 10 * _MIN, end_ms=_BASE,
                             http_get=_fake_http(batch), now_ms=now)
    assert rows and rows[0]["knowledge_time"] == rows[0]["event_time"] + _MIN


def test_as_of_at_open_excludes_the_bar_no_lookahead(tmp_path):
    # 端到端无前视:入库一根分钟 bar,as_of=该 bar 开盘时刻必须**排除**它(kt=收盘 > as_of=开盘)
    from services.data.market_provider import ingest_provider_rows
    now = _BASE
    open_m = _BASE - 2 * _MIN
    batch = [_kline(open_m, 1, 2, 0.5, 1.5, 100)]
    rows = fetch_minute_rows("BTC", start_ms=_BASE - 10 * _MIN, end_ms=_BASE,
                             http_get=_fake_http(batch), now_ms=now)
    store = SqlitePITStore(os.path.join(str(tmp_path), "m.db"), create=True)
    rep = ingest_provider_rows(rows, store, dataset_id="bn1m-BTC", now=now, time_unit="ms")
    assert rep.bars_ingested == 1
    # as_of = 开盘时刻:bar 尚未收盘(kt=开盘+1m),不可读出
    assert store.as_of("BTC", as_of_time=open_m) == []
    # as_of = 收盘时刻:此时可知,读得出
    got = store.as_of("BTC", as_of_time=open_m + _MIN)
    assert len(got) == 1
    assert got[0].close == 1.5


# ---------------------------------------------------------------------------
# range validation
# ---------------------------------------------------------------------------

def test_start_after_end_raises():
    with pytest.raises(ValueError):
        fetch_minute_rows("BTC", start_ms=_BASE, end_ms=_BASE - 1_000, http_get=_fake_http([]))


# ---------------------------------------------------------------------------
# boundary conditions: empty response / short single page / API error
# ---------------------------------------------------------------------------

def test_fetch_handles_empty_response():
    rows = fetch_minute_rows("BTC", start_ms=_BASE - 10 * _MIN, end_ms=_BASE,
                             http_get=_fake_http([]), now_ms=_BASE)
    assert rows == []


def test_fetch_stops_after_short_single_page():
    # 一页不足 1000 条(只有 5 条可用历史)-> 只能拿这 5 条,且绝不因为没到 start_ms 就死循环再翻页
    batch = [_kline(_BASE - (i + 1) * _MIN, 1, 1, 1, 1, 1) for i in range(5)]
    http = _counting_http(_fake_http(batch))
    rows = fetch_minute_rows("BTC", start_ms=_BASE - 10_000 * _MIN, end_ms=_BASE - 1,
                             http_get=http, now_ms=_BASE)
    assert len(rows) == 5
    assert len(http.calls) == 1                                     # 短页后立即停止,没有再翻页


def test_fetch_propagates_api_errors_instead_of_silently_returning_empty():
    # 两台 host 都失败时,底层 fetch_klines/_get 会重新抛出最后一个异常——本抓取器不吞掉它、
    # 不假装"没有数据"(那会被误读成"这段历史真的没有 bar"而不是"网络/API 出错了")。持续失败
    # 耗尽重试预算后仍会 raise(不是"重试了就永远不报错")。
    def _raising(url, headers=None):
        raise RuntimeError("simulated API failure")

    with pytest.raises(RuntimeError):
        fetch_minute_rows("BTC", start_ms=_BASE - 10 * _MIN, end_ms=_BASE,
                         http_get=_raising, now_ms=_BASE, sleep=lambda s: None)


# ---------------------------------------------------------------------------
# transient-failure retry (2026-07-09): a single flaky page must not discard
# every page already fetched before it — real runs are 100s of pages long,
# and this environment's network has been observed to blip mid-run.
# ---------------------------------------------------------------------------

def test_fetch_retries_a_transiently_failing_page_then_succeeds():
    # binance_klines_fetch.py::_get already fails over across 2 hosts per outer attempt (calling
    # http_get up to twice) BEFORE this module's own retry loop ever sees an exception -- so
    # "both hosts down" takes 2 http_get calls to surface as one retriable failure. First 4 calls
    # (2 outer retry attempts x 2 hosts each) fail; the 5th (3rd attempt, 1st host) succeeds.
    good = [_kline(_BASE - 2 * _MIN, 1, 2, 0.5, 1.5, 100)]
    calls = {"n": 0}

    def _flaky(url, headers=None):
        calls["n"] += 1
        if calls["n"] < 5:
            raise RuntimeError("simulated transient network error")
        return good

    sleeps = []
    rows = fetch_minute_rows("BTC", start_ms=_BASE - 10 * _MIN, end_ms=_BASE,
                             http_get=_flaky, now_ms=_BASE, sleep=sleeps.append)
    assert len(rows) == 1 and rows[0]["close"] == 1.5     # eventually got the data, not an empty result
    assert calls["n"] == 5                                # 4 failures + 1 success, not silently given up
    assert len(sleeps) == 2                                # backoff slept between the 2 failed OUTER attempts
    assert sleeps[1] > sleeps[0]                            # exponential (increasing) backoff, not fixed


def test_fetch_exhausts_retry_budget_and_raises_the_last_error():
    def _always_raising(url, headers=None):
        raise RuntimeError("persistent network error")

    sleeps = []
    with pytest.raises(RuntimeError, match="persistent network error"):
        fetch_minute_rows("BTC", start_ms=_BASE - 10 * _MIN, end_ms=_BASE,
                         http_get=_always_raising, now_ms=_BASE, sleep=sleeps.append)
    assert len(sleeps) > 0                                  # did retry (not a bare first-failure raise)


def test_fetch_does_not_retry_across_already_completed_pages():
    # page 2 (older) flakes once then succeeds -- page 1 (already fetched successfully) must not
    # be re-requested as a side effect of retrying page 2.
    n = 1500
    start_open = _BASE - n * _MIN
    all_klines = [_kline(start_open + i * _MIN, 1 + i * 0.01, 2, 0.5, 1 + i * 0.01, 10)
                 for i in range(n)]
    paged = _paged_http(all_klines)
    calls = {"n": 0}

    def _flaky_second_page(url, headers=None):
        calls["n"] += 1
        if calls["n"] == 2:                               # the second HTTP call (older page) flakes once
            raise RuntimeError("simulated transient network error")
        return paged(url, headers)

    rows = fetch_minute_rows("BTC", start_ms=start_open, end_ms=_BASE - 1,
                             http_get=_flaky_second_page, now_ms=_BASE, sleep=lambda s: None)
    assert len(rows) == n                                   # still got everything, zero gaps
    ets = sorted(r["event_time"] for r in rows)
    assert ets == sorted(set(ets))                          # zero duplicates despite the retry


# ---------------------------------------------------------------------------
# CLI arg parsing (pure, no network)
# ---------------------------------------------------------------------------

def test_parse_args_defaults_ticker_to_btc():
    parsed = _parse_args(["--start-ms", "123456"])
    assert parsed == {"ticker": "BTC", "start_ms": 123456, "end_ms": None}


def test_parse_args_reads_all_three_flags():
    parsed = _parse_args(["--ticker", "ETH", "--start-ms", "100", "--end-ms", "200"])
    assert parsed == {"ticker": "ETH", "start_ms": 100, "end_ms": 200}


def test_parse_args_requires_start_ms():
    with pytest.raises(SystemExit):
        _parse_args(["--ticker", "BTC"])


def test_parse_args_rejects_unexpected_positional():
    with pytest.raises(SystemExit):
        _parse_args(["BTC", "--start-ms", "1"])
