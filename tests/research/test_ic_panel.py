import inspect
import statistics

from services.research.ic_panel import (aggregate_ic_series, compute_horizon_ic_series,
                                        compute_period_ic_series, signal_composite,
                                        signal_from_window_op)


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


# --- per-factor 持有期口径:compute_horizon_ic_series(非重叠抽样是唯一口径,无 stride 参数) ---


def _rank_corr(xs, ys):
    """独立的 Spearman 参考实现(与被测代码不同写法,用 statistics.correlation 交叉验证)。"""
    rx = {v: i for i, v in enumerate(sorted(xs))}
    ry = {v: i for i, v in enumerate(sorted(ys))}
    return statistics.correlation([float(rx[v]) for v in xs], [float(ry[v]) for v in ys])


def test_horizon_ic_uses_h_period_return():
    # 4 instruments, T=3, horizon=2: IC at t=0 must use price[2]/price[0]-1 (NOT price[1]).
    signal = [[1.0, 2.0, 3.0, 4.0], [0.0] * 4, [0.0] * 4]
    price = [
        [100.0, 100.0, 100.0, 100.0],
        [999.0, 1.0, 999.0, 1.0],          # decoy: would give a totally different ordering at h=1
        [101.0, 102.0, 103.0, 104.0],
    ]
    ics, starts = compute_horizon_ic_series(signal, price, 2)
    assert starts == [0]
    want = _rank_corr([1.0, 2.0, 3.0, 4.0],
                      [101.0 / 100 - 1, 102.0 / 100 - 1, 103.0 / 100 - 1, 104.0 / 100 - 1])
    assert abs(ics[0] - want) < 1e-12


def test_horizon1_matches_legacy_series_exactly():
    # 向后兼容锚:horizon=1 must reproduce compute_period_ic_series ELEMENTWISE and EXACTLY
    # (same float ops) — the h=1 snapshot numbers are a cross-check asset and must not drift.
    signal = [[1.0, 3.0, 2.0, 5.0, 4.0], [2.0, 1.0, None, 4.0, 3.0],
              [5.0, 4.0, 3.0, 2.0, 1.0], [1.0, 2.0, 3.0, 4.0, 5.0]]
    price = [[100.0, 90.0, 80.0, 70.0, 60.0], [101.0, 89.0, 82.0, 71.0, 59.0],
             [99.0, 91.0, 81.0, 73.0, 61.0], [102.0, 88.0, 83.0, 70.0, 62.0]]
    legacy = compute_period_ic_series(signal, price)
    ics, starts = compute_horizon_ic_series(signal, price, 1)
    assert starts == list(range(len(signal) - 1))
    assert len(ics) == len(legacy)
    for a, b in zip(ics, legacy):
        assert a == b                      # bitwise-identical floats, not approx


def test_horizon_nonoverlap_default_sampling():
    # T=25, h=5 -> start indices 0,5,10,15 (t+h<T); non-overlapping is the ONLY honest sampling
    T, N = 25, 4
    signal = [[float(i + t % 3) for i in range(N)] for t in range(T)]
    price = [[100.0 + t + i for i in range(N)] for t in range(T)]
    ics, starts = compute_horizon_ic_series(signal, price, 5)
    assert starts == [0, 5, 10, 15]
    assert len(ics) == len(starts)


def test_horizon_has_no_stride_parameter():
    # 评审拍板:重叠抽样会让 naive t-stat 虚高——不留 stride 口子(YAGNI + 诚实口径唯一)。
    params = inspect.signature(compute_horizon_ic_series).parameters
    assert set(params) == {"signal_matrix", "price_matrix", "horizon"}


def test_horizon_insufficient_pairs_none():
    # only 3 valid pairs in the sampled period (one None signal) -> None, same threshold as h=1
    signal = [[1.0, 2.0, 3.0, None], [0.0] * 4, [0.0] * 4]
    price = [[100.0] * 4, [1.0] * 4, [101.0, 102.0, 103.0, 104.0]]
    ics, starts = compute_horizon_ic_series(signal, price, 2)
    assert starts == [0] and ics == [None]


def test_horizon_skips_bad_price():
    # p0 <= 0 excludes the instrument; drops to 3 pairs -> None (mirrors the h=1 rule)
    signal = [[1.0, 2.0, 3.0, 4.0], [0.0] * 4, [0.0] * 4]
    price = [[100.0, 100.0, 0.0, 100.0], [1.0] * 4, [101.0, 102.0, 103.0, 104.0]]
    ics, _ = compute_horizon_ic_series(signal, price, 2)
    assert ics == [None]


def test_horizon_per_year_uses_start_indices():
    # aggregate per_year must group by the SAMPLED START's year when fed start-aligned event_times
    ms_2020 = 1577836800000
    ms_2021 = 1609459200000
    week = 7 * 86_400_000
    # 48 periods: 24 in 2020, 24 in 2021; h=2 non-overlap -> 23 sampled starts (t+2<48)
    ets = [ms_2020 + i * week for i in range(24)] + [ms_2021 + i * week for i in range(24)]
    T, N = 48, 5
    signal = [[float((i + t) % 5) for i in range(N)] for t in range(T)]
    price = [[100.0 * (1.02 ** t) + 3.0 * i + (t * i % 7) for i in range(N)] for t in range(T)]
    ics, starts = compute_horizon_ic_series(signal, price, 2)
    sub_ets = [ets[i] for i in starts]
    agg = aggregate_ic_series(ics, event_times=sub_ets)
    # split by hand and compare group means against per_year
    by_year = {}
    for ic, et in zip(ics, sub_ets):
        if ic is None:
            continue
        y = "2020" if et < ms_2021 else "2021"
        by_year.setdefault(y, []).append(ic)
    for y, vs in by_year.items():
        want = round(statistics.fmean(vs), 4) if len(vs) >= 10 else None
        assert agg["per_year"].get(y) == want


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


# --- 驱动器 factor_row(纯组装函数):顶层统计 == by_period['1'](向后兼容锚) ---


def _driver_fixture_panel():
    ms_2020 = 1577836800000
    T, N = 30, 5
    close = [[100.0 * (1.01 ** t) + 5.0 * i + ((t * (i + 2)) % 7) for i in range(N)] for t in range(T)]
    ets = [ms_2020 + t * 86_400_000 for t in range(T)]
    return _FakePanel(close, ["A", "B", "C", "D", "E"], ets)


def _driver_fixture_entry(**extra):
    entry = {"name": "fixture", "universe": "fixture_uni", "thesis": "unit-test only",
             "signal_fn": lambda p: [[float((i * 7 + t * 3) % 11) for i in range(len(p.instruments))]
                                     for t in range(len(p.event_times))]}
    entry.update(extra)
    return entry


def test_factor_row_default_holding_period():
    from tools.ic_panel_research import factor_row
    row = factor_row(_driver_fixture_entry(), _driver_fixture_panel())
    assert row["holding_periods"] == [1]
    assert set(row["by_period"]) == {"1"}


def test_factor_row_top_level_equals_by_period_1():
    from tools.ic_panel_research import factor_row
    row = factor_row(_driver_fixture_entry(holding_periods=[1, 2, 7]), _driver_fixture_panel())
    assert row["holding_periods"] == [1, 2, 7]
    assert set(row["by_period"]) == {"1", "2", "7"}
    for f in ("mean_ic", "ic_std", "icir", "hit_rate", "t_stat", "n_periods", "per_year"):
        assert row[f] == row["by_period"]["1"][f], f"top-level {f} != by_period['1']"
    # h=1 top-level must be the LEGACY code path, bitwise (snapshot numbers are an asset)
    panel = _driver_fixture_panel()
    sig = _driver_fixture_entry()["signal_fn"](panel)
    ics = compute_period_ic_series(sig, panel.matrices["close"])
    legacy = aggregate_ic_series(ics, event_times=panel.event_times[:len(ics)])
    for f in ("mean_ic", "ic_std", "icir", "hit_rate", "t_stat", "n_periods", "per_year"):
        assert row[f] == legacy[f]


def test_factor_row_always_computes_h1_even_if_unlisted():
    # defensive: an entry listing only [7] still needs the h=1 anchor for the top-level stats
    from tools.ic_panel_research import factor_row
    row = factor_row(_driver_fixture_entry(holding_periods=[7]), _driver_fixture_panel())
    assert row["holding_periods"] == [1, 7]
    assert "1" in row["by_period"]


def test_registry_holding_periods_are_valid():
    # every registry entry's optional holding_periods must be a sorted list of ints >= 1 containing 1
    from tools.ic_panel_registry import FACTOR_REGISTRY
    for entry in FACTOR_REGISTRY:
        hps = entry.get("holding_periods")
        if hps is None:
            continue
        assert all(isinstance(h, int) and h >= 1 for h in hps), entry["name"]
        assert hps == sorted(set(hps)), entry["name"]
        assert 1 in hps, entry["name"]
