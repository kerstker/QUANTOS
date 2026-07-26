"""tools/binance_rest_gap_fill.py 离线测试 —— 全程注入假 transport,零网络。

钉死 2026-07-26 对真实端点实测的语义:
  * markPriceKlines openTime = 日界(日首),close 与归档逐位一致;最后一根是当日未收盘 bar,必须丢。
  * openInterestHist / globalLongShortAccountRatio / topLongShortPositionRatio 的 5m 记录按
    WINDOW END 标时间 → 归档 eod 行 = timestamp == day + 1d(次日 00:00)那条。
  * takerlongshortRatio 按 WINDOW START 标时间 → eod 行 = timestamp == day + 1d - 5m(当日 23:55)。
  * PIT:只接受已收盘日(day < 当前 UTC 日);超过 REST 保留期(30 天)的日不请求。
"""
from __future__ import annotations

from urllib.error import URLError

import pytest

from services.data.raw_archive import load_archived_response, summarize_archive
from tools import binance_rest_gap_fill as gf

_DAY = 86_400_000
_T0 = (1_700_000_000_000 // _DAY) * _DAY
_NOW = _T0 + 3000 * _DAY                     # 日对齐锚点:"今天"(未收盘)
_TODAY = (_NOW // _DAY) * _DAY
_YESTERDAY = _TODAY - _DAY


def _mk_row(day, close=100.0):
    return [day, str(close - 1), str(close + 1), str(close - 2), str(close),
            "0", day + _DAY - 1]


# --- parse_mark_klines(纯函数)---

def test_parse_mark_klines_drops_unclosed_today_and_keeps_closed_days():
    payload = [_mk_row(_YESTERDAY - _DAY, 98.0), _mk_row(_YESTERDAY, 99.0),
               _mk_row(_TODAY, 101.0)]
    out = gf.parse_mark_klines(payload, now_ms=_NOW)
    assert out == {_YESTERDAY - _DAY: 98.0, _YESTERDAY: 99.0}


def test_parse_mark_klines_rejects_bad_rows():
    with pytest.raises(gf.RestGapFillError):
        gf.parse_mark_klines({"not": "a list"}, now_ms=_NOW)
    with pytest.raises(gf.RestGapFillError):
        gf.parse_mark_klines([[_YESTERDAY, "1", "2"]], now_ms=_NOW)      # too few columns
    with pytest.raises(gf.RestGapFillError):
        bad = _mk_row(_YESTERDAY)
        bad[6] = _YESTERDAY + _DAY                     # close_time does not close the UTC day
        gf.parse_mark_klines([bad], now_ms=_NOW)
    with pytest.raises(gf.RestGapFillError):
        gf.parse_mark_klines([_mk_row(_YESTERDAY, close=-5.0)], now_ms=_NOW)
    with pytest.raises(gf.RestGapFillError):
        bad = _mk_row(_YESTERDAY)
        bad[2] = "1.0"                                  # high < max(open, close, low)
        gf.parse_mark_klines([bad], now_ms=_NOW)


# --- fillable_days(PIT/保留期闸门)---

def test_fillable_days_keeps_only_closed_days_within_retention():
    days = [_TODAY, _YESTERDAY, _TODAY - 31 * _DAY, _TODAY - 30 * _DAY]
    assert gf.fillable_days(days, now_ms=_NOW) == [_TODAY - 30 * _DAY, _YESTERDAY]
    with pytest.raises(gf.RestGapFillError):
        gf.fillable_days([_YESTERDAY + 1000], now_ms=_NOW)


# --- fetch_mark_days_rest(假 transport)---

def test_fetch_mark_days_rest_fills_archives_and_replays(tmp_path):
    calls = []

    def http_get(url, headers):
        calls.append(url)
        return [_mk_row(_YESTERDAY, 99.5), _mk_row(_TODAY, 101.0)]

    archive = str(tmp_path / "raw")
    failures = []
    out = gf.fetch_mark_days_rest(
        "BTC", [_YESTERDAY, _TODAY], now_ms=_NOW, http_get=http_get,
        archive_root=archive, observed_at_ms=_NOW, failure_sink=failures,
    )
    assert out == {_YESTERDAY: 99.5}                    # 今日未收盘,绝不入
    assert failures == []
    assert len(calls) == 1 and "markPriceKlines" in calls[0]
    archived = load_archived_response(
        archive, dataset_id=gf.REST_DATASET_MARK, instrument_id="BTC",
        event_time_ms=_YESTERDAY,
    )
    assert archived is not None
    assert gf.parse_mark_rest_day(archived["payload"], _YESTERDAY) == 99.5
    lineage = summarize_archive(archive)
    assert lineage["status"] == "PASS"
    assert lineage["datasets"] == {gf.REST_DATASET_MARK: 1}
    assert lineage["representations"] == ["CANONICAL_JSON_UTF8"]


def test_fetch_mark_days_rest_records_not_found_and_transient(tmp_path):
    def http_get(url, headers):
        return [_mk_row(_TODAY, 101.0)]                 # 只有未收盘日 → 目标日缺失

    failures = []
    out = gf.fetch_mark_days_rest(
        "BTC", [_YESTERDAY], now_ms=_NOW, http_get=http_get, failure_sink=failures,
    )
    assert out == {}
    assert failures[0]["kind"] == "NOT_FOUND"

    def down(url, headers):
        raise URLError("connection reset")

    failures = []
    out = gf.fetch_mark_days_rest(
        "BTC", [_YESTERDAY], now_ms=_NOW, http_get=down, failure_sink=failures,
    )
    assert out == {}
    assert failures[0]["kind"] == "TRANSIENT"


# --- OI eod 行选择 + 记录构造 ---

def _oi_rows(day):
    """四个端点各自一天的 5m 假行;eod 行带可识别值,另放 decoy 行验证 max-ts 选择。"""
    oi_eod = {"symbol": "BTCUSDT", "sumOpenInterest": "113512.884",
              "sumOpenInterestValue": "3041271241.99", "timestamp": day + _DAY}
    oi_decoy = dict(oi_eod, sumOpenInterest="1.0", timestamp=day + _DAY - 300_000)
    gls_eod = {"symbol": "BTCUSDT", "longShortRatio": "2.3994", "timestamp": day + _DAY}
    top_eod = {"symbol": "BTCUSDT", "longShortRatio": "1.2777", "timestamp": day + _DAY}
    tak_eod = {"buySellRatio": "0.8979", "buyVol": "1", "sellVol": "2",
               "timestamp": day + _DAY - 300_000}
    tak_decoy = dict(tak_eod, buySellRatio="9.9", timestamp=day)
    return {
        "openInterestHist": [oi_decoy, oi_eod],
        "globalLongShortAccountRatio": [gls_eod],
        "topLongShortPositionRatio": [top_eod],
        "takerlongshortRatio": [tak_decoy, tak_eod],
    }


def test_eod_row_respects_per_endpoint_timestamp_convention():
    rows = _oi_rows(_YESTERDAY)
    oi_row = gf._eod_row(rows["openInterestHist"], day=_YESTERDAY, convention="end")
    assert oi_row["timestamp"] == _YESTERDAY + _DAY     # window-end:eod = 次日 00:00
    tak_row = gf._eod_row(rows["takerlongshortRatio"], day=_YESTERDAY, convention="start")
    assert tak_row["timestamp"] == _YESTERDAY + _DAY - 300_000  # window-start:eod = 当日 23:55
    assert gf._eod_row([], day=_YESTERDAY, convention="end") is None


def test_build_oi_record_maps_fields_and_validates():
    rows = _oi_rows(_YESTERDAY)
    eod = {
        "openInterestHist": rows["openInterestHist"][-1],
        "globalLongShortAccountRatio": rows["globalLongShortAccountRatio"][0],
        "topLongShortPositionRatio": rows["topLongShortPositionRatio"][0],
        "takerlongshortRatio": rows["takerlongshortRatio"][-1],
    }
    rec = gf.build_oi_record(eod, day=_YESTERDAY)
    assert rec == {"oi": 113512.884, "oi_value": 3041271241.99,
                   "top_ls": 1.2777, "global_ls": 2.3994, "taker_ls": 0.8979}
    with pytest.raises(gf.RestGapFillError):
        gf.build_oi_record({"openInterestHist": eod["openInterestHist"]}, day=_YESTERDAY)
    with pytest.raises(gf.RestGapFillError):
        bad = dict(eod)
        bad["takerlongshortRatio"] = dict(eod["takerlongshortRatio"], buySellRatio="0")
        gf.build_oi_record(bad, day=_YESTERDAY)


def test_fetch_oi_days_rest_fills_archives_and_replays(tmp_path):
    rows_by_endpoint = _oi_rows(_YESTERDAY)

    def http_get(url, headers):
        for endpoint, _conv in gf._OI_ENDPOINTS:
            if endpoint in url:
                return rows_by_endpoint[endpoint]
        raise AssertionError(f"unexpected url {url}")

    archive = str(tmp_path / "raw")
    failures = []
    out = gf.fetch_oi_days_rest(
        "BTC", [_YESTERDAY], now_ms=_NOW, http_get=http_get,
        archive_root=archive, observed_at_ms=_NOW, failure_sink=failures, sleep=0,
    )
    assert set(out) == {_YESTERDAY}
    assert out[_YESTERDAY]["global_ls"] == 2.3994
    assert out[_YESTERDAY]["taker_ls"] == 0.8979
    assert failures == []
    archived = load_archived_response(
        archive, dataset_id=gf.REST_DATASET_OI, instrument_id="BTC",
        event_time_ms=_YESTERDAY,
    )
    assert archived is not None
    assert gf.parse_oi_rest_day(archived["payload"], _YESTERDAY) == out[_YESTERDAY]


def test_fetch_oi_days_rest_skips_day_with_missing_endpoint_row():
    rows_by_endpoint = _oi_rows(_YESTERDAY)
    rows_by_endpoint["takerlongshortRatio"] = []        # taker 当日无行 → 整日不填

    def http_get(url, headers):
        for endpoint, _conv in gf._OI_ENDPOINTS:
            if endpoint in url:
                return rows_by_endpoint[endpoint]
        raise AssertionError(url)

    failures = []
    out = gf.fetch_oi_days_rest(
        "BTC", [_YESTERDAY], now_ms=_NOW, http_get=http_get,
        failure_sink=failures, sleep=0,
    )
    assert out == {}
    assert failures[0]["kind"] == "NOT_FOUND"
    assert "takerlongshortRatio" in failures[0]["message"]
