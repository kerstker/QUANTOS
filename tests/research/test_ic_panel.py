import statistics

from services.research.ic_panel import (aggregate_ic_series, compute_period_ic_series,
                                        signal_composite, signal_from_window_op)


def test_compute_period_ic_series_perfect_positive_rank():
    # period 0: signal exactly ranks with next-period return (perfect positive IC)
    signal = [[1.0, 2.0, 3.0, 4.0], [0.0, 0.0, 0.0, 0.0]]
    price = [[100.0, 100.0, 100.0, 100.0], [101.0, 102.0, 103.0, 104.0]]
    ics = compute_period_ic_series(signal, price)
    assert len(ics) == 1                     # T=2 periods -> 1 usable IC
    assert ics[0] is not None and ics[0] > 0.99


def test_compute_period_ic_series_perfect_negative_rank():
    signal = [[4.0, 3.0, 2.0, 1.0], [0.0, 0.0, 0.0, 0.0]]
    price = [[100.0, 100.0, 100.0, 100.0], [101.0, 102.0, 103.0, 104.0]]
    ics = compute_period_ic_series(signal, price)
    assert ics[0] < -0.99


def test_compute_period_ic_series_insufficient_pairs_is_none():
    # only 3 valid (signal,price) pairs that period -> None (threshold is 4)
    signal = [[1.0, 2.0, 3.0, None], [0.0] * 4]
    price = [[100.0] * 4, [101.0, 102.0, 103.0, 104.0]]
    ics = compute_period_ic_series(signal, price)
    assert ics[0] is None


def test_compute_period_ic_series_skips_bad_price():
    # price[t][i] <= 0 -> that instrument excluded from the pair set that period.
    # Only 3 valid pairs remain (below the 4-pair threshold) -> must be None.
    signal = [[1.0, 2.0, 3.0, 4.0], [0.0] * 4]
    price = [[100.0, 100.0, 0.0, 100.0], [101.0, 102.0, 103.0, 104.0]]
    ics = compute_period_ic_series(signal, price)
    assert ics[0] is None


def test_aggregate_ic_series_below_min_periods_all_none():
    out = aggregate_ic_series([0.1, 0.2, -0.1, 0.3, 0.15])  # only 5 valid < 10 threshold
    assert out["mean_ic"] is None and out["icir"] is None and out["hit_rate"] is None
    assert out["n_periods"] == 5 and out["per_year"] == {}


def test_aggregate_ic_series_known_stats():
    ics = [0.1] * 15 + [-0.1] * 5   # 20 periods, mean positive, some negative
    out = aggregate_ic_series(ics)
    assert out["n_periods"] == 20
    assert abs(out["mean_ic"] - statistics.fmean(ics)) < 1e-9
    assert out["hit_rate"] == 0.75          # 15/20 share the sign of the mean (positive)
    assert out["icir"] is not None and out["t_stat"] is not None


def test_aggregate_ic_series_per_year_grouping():
    # 12 periods in "2020" (event_time ms), 12 in "2021" -> both years >= 10 -> reported
    ms_2020 = 1577836800000   # 2020-01-01
    ms_2021 = 1609459200000   # 2021-01-01
    ics = [0.05] * 12 + [0.20] * 12
    ets = [ms_2020 + i * 86_400_000 for i in range(12)] + [ms_2021 + i * 86_400_000 for i in range(12)]
    out = aggregate_ic_series(ics, event_times=ets)
    assert out["per_year"]["2020"] == 0.05 and out["per_year"]["2021"] == 0.2


class _FakePanel:
    def __init__(self, close, instruments, event_times):
        self.matrices = {"close": close}
        self.instruments = instruments
        self.event_times = event_times


def test_signal_from_window_op_momentum_matches_manual_calc():
    # 2 instruments, 5 periods; momentum window=2 needs >=2 real closes
    close = [[100.0, 50.0], [102.0, 51.0], [104.0, 52.0], [None, 53.0], [108.0, 54.0]]
    panel = _FakePanel(close, ["A", "B"], [1, 2, 3, 4, 5])
    out = signal_from_window_op(panel, "momentum", 2)
    assert out[0] == [None, None]                          # window=2 needs 2 real closes; only 1 so far
    assert round(out[1][0], 6) == round(102.0 / 100.0 - 1.0, 6)   # A: mom(2) at t=1
    assert out[3][0] is None                                # gap day: no reading, stays None
    # B never gaps: at t=3, hist=[50,51,52,53] (4 real closes) -> window=2 uses closes[-2]=52
    assert round(out[3][1], 6) == round(53.0 / 52.0 - 1.0, 6)


def test_signal_from_window_op_skips_gap_without_padding_history():
    # A has a gap at t=3 (None); the window at t=4 must use [100,102,104,108] (skip the gap),
    # i.e. momentum(2) at t=4 = 108/104 - 1, NOT 108/None.
    close = [[100.0], [102.0], [104.0], [None], [108.0]]
    panel = _FakePanel(close, ["A"], [1, 2, 3, 4, 5])
    out = signal_from_window_op(panel, "momentum", 2)
    assert round(out[4][0], 6) == round(108.0 / 104.0 - 1.0, 6)


def test_signal_composite_requires_all_subsignals_present():
    # 4 instruments so cross-sectional z-score has >=3 valid; instrument D never gets enough
    # history for momentum(3) -> composite for D stays None even once dist_from_high(2) exists.
    close = [
        [100.0, 50.0, 10.0, 5.0],
        [101.0, 51.0, 10.5, 5.1],
        [103.0, 49.0, 11.0, None],
    ]
    panel = _FakePanel(close, ["A", "B", "C", "D"], [1, 2, 3])
    out = signal_composite(panel, [("dist_from_high", 2), ("momentum", 3)])
    assert out[2][3] is None            # D missing a close at t=2 -> excluded that period
    assert out[2][0] is not None         # A has both sub-signals at t=2 -> composite present
