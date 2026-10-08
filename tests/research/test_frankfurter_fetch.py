"""FX fetcher (Frankfurter): offline parse correctness + PIT row shape. No network (stubbed payload)."""
from tools.frankfurter_fetch import _ymd_to_ms, fetch_fx_rows, parse_fx

_PAYLOAD = {
    "base": "USD",
    "rates": {
        "2024-01-03": {"EUR": 0.91, "JPY": 143.0, "GBP": 0.79},
        "2024-01-02": {"EUR": 0.90, "JPY": 142.0, "GBP": 0.78},  # out of order on purpose
    },
}


def test_ymd_to_ms_is_utc_midnight():
    assert _ymd_to_ms("1970-01-01") == 0
    assert _ymd_to_ms("1970-01-02") == 86_400_000
    assert _ymd_to_ms("2024-01-03") == 1_704_240_000_000   # 2024-01-03T00:00:00Z


def test_parse_fx_close_is_usd_price_and_sorted():
    rows = parse_fx(_PAYLOAD)
    # 2 days x 3 currencies = 6 rows, ascending event_time
    assert len(rows) == 6
    ets = [r["event_time"] for r in rows]
    assert ets == sorted(ets)
    eur0 = next(r for r in rows if r["instrument_id"] == "EUR" and r["event_time"] == _ymd_to_ms("2024-01-02"))
    assert eur0["close"] == 1.0 / 0.90                      # USD price of 1 EUR
    assert eur0["open"] == eur0["high"] == eur0["low"] == eur0["close"]   # single reference rate/day
    assert eur0["volume"] == 0.0
    assert eur0["knowledge_time"] == eur0["event_time"]     # known end-of-day, PIT


def test_parse_fx_skips_nonpositive_and_bad():
    bad = {"base": "USD", "rates": {"2024-01-02": {"EUR": 0.9, "XXX": None, "YYY": 0.0, "ZZZ": -1.0}}}
    ids = {r["instrument_id"] for r in parse_fx(bad)}
    assert ids == {"EUR"}                                   # None / 0 / negative dropped (fail-soft)


def test_fetch_fx_rows_wires_through_a_stub():
    rows = fetch_fx_rows("2024-01-02", "2024-01-03", http_get=lambda url: _PAYLOAD)
    assert len(rows) == 6 and all(r["close"] > 0 for r in rows)
