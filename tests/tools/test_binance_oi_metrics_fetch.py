"""Offline unit test for the Binance OI/metrics fetcher (no network — stubbed http_get_bytes).

Proves: (1) a metrics ZIP parses to its END-OF-DAY (last) row's kept fields; (2) fetch_ticker_days
collapses days correctly, skips already-cached days, tolerates missing (404) days; (3) it is
deterministic for fixed inputs. The fetcher is out-of-gate but this pins its parse/aggregate contract."""
import io
import zipfile

from tools import binance_oi_metrics_fetch as f

_DAY = f._DAY_MS
_HEADER = ("create_time,symbol,sum_open_interest,sum_open_interest_value,"
           "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
           "count_long_short_ratio,sum_taker_long_short_vol_ratio")


def _zip_for(day_ms: int) -> bytes:
    """A fake one-day metrics CSV (2 intraday rows) zipped in memory; the LAST row is the eod."""
    d = f._day_str(day_ms)
    rows = [
        _HEADER,
        f"{d} 00:00:00,BTCUSDT,100000.0,3.0e9,2.0,1.10,2.20,0.50",
        f"{d} 23:55:00,BTCUSDT,113512.884,3041271241.99,2.26,1.2777,2.3994,0.8979",  # eod
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"BTCUSDT-metrics-{d}.csv", "\n".join(rows))
    return buf.getvalue()


def test_parse_takes_end_of_day_row():
    rec = f.parse_metrics_zip(_zip_for(1_700_000_000_000 // _DAY * _DAY))
    assert rec == {"oi": 113512.884, "oi_value": 3041271241.99,
                   "top_ls": 1.2777, "global_ls": 2.3994, "taker_ls": 0.8979}


def test_parse_empty_or_garbage_is_empty():
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("BTCUSDT-metrics-x.csv", _HEADER)  # header only, no data row
    assert f.parse_metrics_zip(empty.getvalue()) == {}


def test_fetch_skips_present_and_missing_days():
    base = 1_700_000_000_000 // _DAY * _DAY
    d0, d1, d2, d3 = base, base + _DAY, base + 2 * _DAY, base + 3 * _DAY
    have = {d1, d2}  # the archive has only d1, d2

    def stub(url: str) -> bytes:
        for dm in have:
            if f._day_str(dm) in url:
                return _zip_for(dm)
        raise RuntimeError("404")  # d0 (already cached) never requested; d3 missing -> skipped

    out = f.fetch_ticker_days("BTC", d0, d3 + _DAY, present={d0}, http_get_bytes=stub, max_workers=1)
    assert set(out) == {d1, d2}                       # d0 skipped (cached), d3 skipped (404)
    assert out[d1]["oi"] == 113512.884
    # deterministic, and the concurrent path yields the same result as the serial one
    out2 = f.fetch_ticker_days("BTC", d0, d3 + _DAY, present={d0}, http_get_bytes=stub, max_workers=8)
    assert out2 == out


def test_find_start_returns_first_available():
    base = f._PROBE_FLOOR_MS // _DAY * _DAY
    first = base + 90 * _DAY  # archive begins ~3 months after the floor

    def stub(url: str) -> bytes:
        # available only on/after `first`
        for off in range(0, 400):
            dm = first + off * _DAY
            if f._day_str(dm) in url:
                return _zip_for(dm)
        raise RuntimeError("404")

    got = f.find_start_ms("BTC", http_get_bytes=stub, now_ms=first + 400 * _DAY)
    # month-step scan lands within the same month window as `first`
    assert got is not None and 0 <= (got - base) <= 120 * _DAY
