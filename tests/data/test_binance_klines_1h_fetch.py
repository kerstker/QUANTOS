"""1H(小时线)K线抓取器(tools/binance_klines_1h_fetch.py):日频抓取器的同构变体,唯一关键偏离是
knowledge_lag=收盘(防日内 as_of=开盘纳入未收盘 bar 的前视)。cadence-shift-1h(2026-07-07)纯加法。"""
import os

from services.data.persistent_store import SqlitePITStore
from tools.binance_klines_1h_fetch import (BN1H_SPEC, fetch_hourly_rows, parse_klines_1h)

_H = 3_600_000
# hour-aligned realistic ms base (ms-range validation rejects toy timestamps — real bars are ~1.7e12)
_BASE = (1_700_000_000_000 // _H) * _H


def _kline(open_ms, o, h, low, c, v):
    # Binance kline = [openTime, o, h, l, c, v, closeTime, ...]
    return [open_ms, str(o), str(h), str(low), str(c), str(v), open_ms + _H - 1]


def test_parse_snaps_to_hour_grid():
    batch = [_kline(_BASE + 999, 1.0, 2.0, 0.5, 1.5, 100.0)]      # ts slightly off the hour grid
    out = parse_klines_1h(batch, instrument_id="BTC")
    assert set(out) == {_BASE}                                    # snapped to the hour bucket
    assert out[_BASE]["c"] == 1.5 and out[_BASE]["sym"] == "BTC"


def test_knowledge_lag_is_one_hour_close_not_open():
    # 关键防前视:kt = event_time + 3_600_000 = 收盘时刻(刻意偏离日频 BN_SPEC.knowledge_lag=0)
    assert BN1H_SPEC.knowledge_lag == _H


def _fake_http(batch):
    def _get(url, headers=None):
        return batch
    return _get


def test_fetch_discards_the_live_unclosed_hour():
    now = _BASE + 123456                                          # 当前在 _BASE 这根 bar 中途(未收盘)
    batch = [_kline(_BASE - 2 * _H, 1, 1, 1, 1, 1), _kline(_BASE - _H, 1, 1, 1, 1, 1),
             _kline(_BASE, 1, 1, 1, 1, 1)]                        # _BASE 那根 = 活 bar,必须丢
    rows = fetch_hourly_rows("BTC", hours=10, http_get=_fake_http(batch), now_ms=now)
    ets = {r["event_time"] for r in rows}
    assert _BASE not in ets                                       # 活小时 bar 被丢弃(无前视)
    assert (_BASE - 2 * _H) in ets and (_BASE - _H) in ets


def test_normalized_rows_have_close_knowledge_time():
    now = _BASE
    batch = [_kline(_BASE - 2 * _H, 1, 2, 0.5, 1.5, 100)]
    rows = fetch_hourly_rows("BTC", hours=10, http_get=_fake_http(batch), now_ms=now)
    assert rows and rows[0]["knowledge_time"] == rows[0]["event_time"] + _H


def test_as_of_at_open_excludes_the_bar_no_lookahead(tmp_path):
    # 端到端无前视:入库一根小时 bar,as_of=该 bar 开盘时刻必须**排除**它(kt=收盘 > as_of=开盘)
    from services.data.market_provider import ingest_provider_rows
    now = _BASE
    open_h = _BASE - 2 * _H
    batch = [_kline(open_h, 1, 2, 0.5, 1.5, 100)]
    rows = fetch_hourly_rows("BTC", hours=10, http_get=_fake_http(batch), now_ms=now)
    store = SqlitePITStore(os.path.join(str(tmp_path), "h.db"), create=True)
    ingest_provider_rows(rows, store, dataset_id="bn1h-BTC", now=now, time_unit="ms")
    # as_of = 开盘时刻:bar 尚未收盘(kt=开盘+1h),不可读出
    assert store.as_of("BTC", as_of_time=open_h) == []
    # as_of = 收盘时刻:此时可知,读得出
    assert len(store.as_of("BTC", as_of_time=open_h + _H)) == 1
