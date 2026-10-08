from services.research.realism.scenario import (iso_to_ms, max_drawdown_window, window_stats)

_R = [0.02, -0.05, -0.03, 0.04, -0.10, 0.06]
_TS = [iso_to_ms(d) for d in ("2022-05-01", "2022-05-08", "2022-05-15", "2022-11-06",
                              "2022-11-13", "2023-03-12")]


def test_iso_to_ms_is_utc_and_ordered():
    assert iso_to_ms("2022-05-08") > iso_to_ms("2022-05-01")
    assert iso_to_ms("1970-01-01") == 0


def test_window_stats_selects_range():
    ws = window_stats(_R, _TS, iso_to_ms("2022-05-05"), iso_to_ms("2022-05-20"))
    assert ws["n_periods"] == 2                       # 2022-05-08, 2022-05-15
    assert ws["worst_period"] == -0.05


def test_window_stats_empty_when_no_data():
    ws = window_stats(_R, _TS, iso_to_ms("2019-01-01"), iso_to_ms("2019-02-01"))
    assert ws["n_periods"] == 0 and ws["total_return"] == 0.0


def test_max_drawdown_window_finds_peak_to_trough():
    mdd = max_drawdown_window(_R, _TS)
    assert mdd["drawdown"] > 0
    assert mdd["peak_ts"] <= mdd["trough_ts"]
