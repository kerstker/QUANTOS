"""REST gap-fill 归档优先级集成测试(全程假 transport,零网络)——模拟「归档 404 死窗」场景:

1. perp_mark_prices:Data Vision 最新日 404(死窗)→ REST 补占位(REST_GAP_FILL),质量门通过;
   归档发布后占位被归档行覆盖,provenance 翻回 DATA_VISION_ARCHIVE(归档 canonical)。
2. oi_metrics:binance_oi_metrics_fetch.main 同样的死窗补齐 + 归档取代语义(record "source" 标记)。
"""
from __future__ import annotations

import io
import json
import zipfile

from tools import binance_oi_metrics_fetch as oi_fetch
from tools import binance_perp_mark_fetch as mark
from tools import binance_rest_gap_fill as gf

_DAY = 86_400_000
_NOW = 21 * _DAY               # "今天"(未收盘);expected_end = 20 日
_EXPECTED = 20 * _DAY

_HEADER = ("create_time,symbol,sum_open_interest,sum_open_interest_value,"
           "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
           "count_long_short_ratio,sum_taker_long_short_vol_ratio")


def _mark_zip(day_ms, close=100.0):
    date = mark.day_string(day_ms)
    text = (
        "open_time,open,high,low,close,volume,close_time\n"
        f"{day_ms},{close - 1},{close + 1},{close - 2},{close},0,{day_ms + _DAY - 1}\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"BTCUSDT-1d-{date}.csv", text)
    return buf.getvalue()


def _oi_zip(day_ms):
    d = oi_fetch._day_str(day_ms)
    rows = [
        _HEADER,
        f"{d} 00:00:00,BTCUSDT,100000.0,3.0e9,2.0,1.10,2.20,0.50",
        f"{d} 23:55:00,BTCUSDT,113512.884,3041271241.99,2.26,1.2777,2.3994,0.8979",
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"BTCUSDT-metrics-{d}.csv", "\n".join(rows))
    return buf.getvalue()


_REST_OI_RECORD = {"oi": 113512.9, "oi_value": 3041271242.0,
                   "top_ls": 1.278, "global_ls": 2.399, "taker_ls": 0.898}


# --- perp_mark_prices ---

def test_mark_refresh_rest_fills_archive_dead_window(tmp_path):
    """归档死窗:最新已收盘日 404 → REST 补齐并标 REST_GAP_FILL,质量门通过。"""
    path = str(tmp_path / "mark.json")

    def archive_fetch(url):
        for day in range(13, 20):                        # 归档只到 19 日(20 日死窗 404)
            ts = day * _DAY
            if mark.day_string(ts) in url:
                return _mark_zip(ts, close=100.0 + day)
        raise RuntimeError("404")

    rest_calls = []

    def rest_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms, failure_sink):
        rest_calls.append(list(days))
        return {_EXPECTED: 121.0}

    report = mark.refresh_mark_prices(
        ["BTC"], now_ms=_NOW, cache_path=path, http_get_bytes=archive_fetch,
        lookback_days=8, max_workers=1, rest_fetch=rest_fetch,
    )
    assert rest_calls == [[_EXPECTED]]                   # 只有归档缺的那日落 REST
    assert report["rest_gap_fill_days"] == 1
    assert report["failed_tickers"] == []
    assert mark.mark_price_quality_failures(["BTC"], now_ms=_NOW, cache_path=path) == []
    payload = json.load(open(path, encoding="utf-8"))
    assert payload["data"]["BTC"][str(_EXPECTED)] == 121.0
    assert payload["provenance"]["BTC"][str(_EXPECTED)] == gf.PROVENANCE_REST_GAP_FILL
    assert payload["provenance"]["BTC"][str(19 * _DAY)] == gf.PROVENANCE_DATA_VISION_ARCHIVE


def test_mark_refresh_archive_supersedes_rest_placeholder(tmp_path):
    """归档发布后:占位日回归档重试并被归档值覆盖,provenance 翻回 DATA_VISION_ARCHIVE。"""
    path = str(tmp_path / "mark.json")
    seed = {
        "_meta": {"BTC": {"through": _EXPECTED}},
        "data": {"BTC": {str(day * _DAY): 100.0 + day for day in range(13, 20)}
                 | {str(_EXPECTED): 121.0}},
        "provenance": {"BTC": {str(_EXPECTED): gf.PROVENANCE_REST_GAP_FILL}},
    }
    json.dump(seed, open(path, "w"))

    def archive_fetch(url):
        if mark.day_string(_EXPECTED) in url:
            return _mark_zip(_EXPECTED, close=130.0)     # 归档现已发布 20 日
        raise RuntimeError("404")

    def rest_fetch(ticker, days, **kwargs):
        raise AssertionError("归档已补齐,REST 不应再被调用")

    report = mark.refresh_mark_prices(
        ["BTC"], now_ms=_NOW, cache_path=path, http_get_bytes=archive_fetch,
        lookback_days=8, max_workers=1, rest_fetch=rest_fetch,
    )
    assert report["rest_gap_fill_days"] == 0
    payload = json.load(open(path, encoding="utf-8"))
    assert payload["data"]["BTC"][str(_EXPECTED)] == 130.0
    assert payload["provenance"]["BTC"][str(_EXPECTED)] == gf.PROVENANCE_DATA_VISION_ARCHIVE


def test_mark_refresh_rest_fetch_never_overwrites_archive_rows(tmp_path):
    """REST 占位绝不覆盖归档行:rest_fetch 即便返回已有日,缓存值不变。"""
    path = str(tmp_path / "mark.json")

    def archive_fetch(url):
        for day in range(13, 21):
            ts = day * _DAY
            if mark.day_string(ts) in url:
                return _mark_zip(ts, close=100.0 + day)
        raise RuntimeError("404")

    def rest_fetch(ticker, days, **kwargs):
        return {day: 999.0 for day in days}              # 恶意/错位返回也不应生效

    report = mark.refresh_mark_prices(
        ["BTC"], now_ms=_NOW, cache_path=path, http_get_bytes=archive_fetch,
        lookback_days=8, max_workers=1, rest_fetch=rest_fetch,
    )
    assert report["rest_gap_fill_days"] == 0             # 无缺席日 → REST 未被请求
    payload = json.load(open(path, encoding="utf-8"))
    assert payload["data"]["BTC"][str(_EXPECTED)] == 120.0
    assert payload["provenance"]["BTC"][str(_EXPECTED)] == gf.PROVENANCE_DATA_VISION_ARCHIVE


# --- oi_metrics ---

# 现代日期基准:main() 走墙钟需 monkeypatch time.time,zipfile 拒绝 1980 前的时间戳,
# 故不能用 1970 附近的 epoch 小数值。
_OBASE = (1_780_000_000_000 // _DAY) * _DAY


def _oday(i):
    return _OBASE + i * _DAY


def _seed_oi_cache(tmp_path):
    cache = {
        "_meta": {"BTC": {"start": _oday(0), "through": _oday(6)}},
        "data": {"BTC": {
            str(_oday(i)): {"oi": 1.0, "oi_value": 2.0, "top_ls": 1.1,
                            "global_ls": 1.2, "taker_ls": 0.9}
            for i in range(7)
        }},
    }
    path = tmp_path / "oi.json"
    path.write_text(json.dumps(cache), encoding="utf-8")
    return str(path)


def test_oi_main_rest_fills_dead_window_then_archive_supersedes(tmp_path, monkeypatch):
    cache_path = _seed_oi_cache(tmp_path)
    snap_path = str(tmp_path / "snap.json")
    expected = _oday(7)                                   # 最新已收盘日(死窗)
    monkeypatch.setattr(oi_fetch.time, "time", lambda: _oday(8) / 1000)

    def archive_stub_dead_window(url):
        for i in range(7):
            if oi_fetch._day_str(_oday(i)) in url:
                return _oi_zip(_oday(i))
        raise RuntimeError("404")                        # expected 日死窗

    rest_calls = []

    def rest_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms, failure_sink):
        rest_calls.append(list(days))
        return {expected: dict(_REST_OI_RECORD)}

    report = oi_fetch.main(
        ["BTC"], sleep=0, cache_path=cache_path, snap_path=snap_path,
        http_get_bytes=archive_stub_dead_window, rest_fetch=rest_fetch,
    )
    assert rest_calls == [[expected]]
    assert report["rest_gap_fill_days"] == 1
    payload = json.load(open(cache_path, encoding="utf-8"))
    filled = payload["data"]["BTC"][str(expected)]
    assert filled["source"] == gf.PROVENANCE_REST_GAP_FILL
    assert filled["global_ls"] == _REST_OI_RECORD["global_ls"]

    def archive_stub_published(url):
        for i in range(8):                               # 归档现已发布 expected 日
            if oi_fetch._day_str(_oday(i)) in url:
                return _oi_zip(_oday(i))
        raise RuntimeError("404")

    def rest_forbidden(ticker, days, **kwargs):
        raise AssertionError("归档已补齐,REST 不应再被调用")

    report2 = oi_fetch.main(
        ["BTC"], sleep=0, cache_path=cache_path, snap_path=snap_path,
        http_get_bytes=archive_stub_published, rest_fetch=rest_forbidden,
    )
    assert report2["rest_gap_fill_days"] == 0
    payload = json.load(open(cache_path, encoding="utf-8"))
    superseded = payload["data"]["BTC"][str(expected)]
    assert "source" not in superseded                    # 归档行无占位标记(canonical)
    assert {key: superseded[key] for key in _REST_OI_RECORD} == {
        "oi": 113512.884,
        "oi_value": 3041271241.99,
        "top_ls": 1.2777,
        "global_ls": 2.3994,
        "taker_ls": 0.8979,
    }
    # Archive supersession must retain all three causal clocks; replacing a
    # REST placeholder may change provenance/value, never erase time meaning.
    assert superseded["source_event_time_ms"] == expected + _DAY - 300_000
    assert superseded["available_time_ms"] == expected + _DAY
    assert superseded["ingestion_time_ms"] == expected + _DAY
