"""Pure-function tests for the A-share equity_alpha research engine.

Covers: known-window factor values on toy histories; known IC values through the
ic_eval adapter; the strict no-peek composite contract (weights fitted on TRAIN
stats must be bit-identical when TEST-period data is perturbed); the tradability
mask (zero-amt / zero-volume day excluded); basket weights summing to 1; and the
ADV participation-cap math.
"""
from __future__ import annotations

import pytest

from quantos.contracts.data import Bar
from services.research.equity_alpha import (basket, combine, factors, hygiene, ic_eval,
                                            panel as eq_panel)
from services.research.paper_trading.store import PaperTrackStore
from services.research.ports.persistence import PaperTrackStoreError


# ---------------------------------------------------------------------------
# toy fixtures
# ---------------------------------------------------------------------------

class _FakeStore:
    """Minimal fetch_bars store for panel tests (PIT contract: knowledge == event)."""

    def __init__(self, rows):
        self._rows = rows  # {code: [Bar]}

    def fetch_bars(self, instrument_id, start, end):
        return [b for b in self._rows.get(instrument_id, ()) if start <= b.event_time <= end]

    def fetch_bars_pit(self, instrument_id, start, end):
        return [b for b in self.fetch_bars(instrument_id, start, end)
                if b.knowledge_time <= b.event_time]


def _bar(code, d, close, volume=1000.0):
    return Bar(instrument_id=code, open=close, high=close, low=close, close=close,
               volume=volume, event_time=d, knowledge_time=d)


def _toy_panel(n_inst=5, n_days=12, base_dates=None):
    """5 instruments x 12 dates; instrument i closes follow a strictly increasing
    linear path with slope (i+1) -> a known cross-sectional ordering every day."""
    dates = base_dates or list(range(20250102, 20250102 + n_days))
    rows = {}
    for i in range(n_inst):
        code = f"T{i:03d}.SZ"
        rows[code] = [_bar(code, d, 10.0 + (i + 1) * k) for k, d in enumerate(dates)]
    store = _FakeStore(rows)
    universe = {"stocks": [{"code": c} for c in sorted(rows)]}
    codes = eq_panel.universe_codes(universe)
    return eq_panel.build_ashare_panel(store, codes)


# ---------------------------------------------------------------------------
# factor math on known windows
# ---------------------------------------------------------------------------

def test_reversal_5d_known_value():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]  # +50% over 5 steps
    assert factors.reversal_5d(closes, [1] * 6, [1] * 6) == pytest.approx(-0.5)
    assert factors.reversal_5d(closes[:5], [1] * 5, [1] * 5) is None  # too short


def test_momentum_skip_and_price_position():
    closes = list(range(1, 62))  # 61 points: 1..61
    # close[-6]=56, close[-61]=1 -> 55.0
    assert factors.momentum_60d_skip5(closes, [1] * 61, [1] * 61) == pytest.approx(55.0)
    w = [1.0] * 249 + [2.0]
    assert factors.price_position_52w(w, [1] * 250, [1] * 250) == pytest.approx(1.0)
    assert factors.price_position_52w(w[:249], [1] * 249, [1] * 249) is None


def test_volatility_amihud_maxret_known_values():
    closes = [10.0, 11.0, 10.0, 11.0] + [10.0] * 17  # 21 closes
    vol = factors.volatility_20d(closes, [1] * 21, [1] * 21)
    assert vol is not None and vol > 0
    amts = [1e6] * 21
    am = factors.amihud_illiq_20d(closes, [1] * 21, amts)
    assert am == pytest.approx(sum(abs(closes[k] / closes[k - 1] - 1) for k in range(1, 21)) / 20 / 1e6)
    assert factors.max_daily_ret_20d(closes, [1] * 21, amts) == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# panel + tradability mask
# ---------------------------------------------------------------------------

def test_tradability_mask_excludes_zero_amt_day():
    d0, d1, d2 = 20250102, 20250103, 20250106
    rows = {
        "AAA.SZ": [_bar("AAA.SZ", d0, 10.0), _bar("AAA.SZ", d1, 11.0, volume=0.0),
                   _bar("AAA.SZ", d2, 12.0)],
    }
    store = _FakeStore(rows)
    panel, aux = eq_panel.build_ashare_panel(store, ["AAA.SZ"])
    assert aux["tradable"][0][0] is True
    assert aux["tradable"][1][0] is False   # zero volume/amt -> not tradable
    assert aux["tradable"][2][0] is True
    assert aux["amt"][1][0] == 0.0
    assert aux["listed_days"][2][0] == 3


def test_min_listed_days_eligibility():
    panel, aux = _toy_panel()
    elig = eq_panel.eligible_matrix(aux, min_listed_days=60)  # only 12 days of history
    assert not any(any(row) for row in elig)
    elig2 = eq_panel.eligible_matrix(aux, min_listed_days=10)
    assert elig2[10][0] is True and elig2[5][0] is False


# ---------------------------------------------------------------------------
# IC adapter: known rank-correlation values
# ---------------------------------------------------------------------------

def test_ic_known_values_train_test_split():
    # 12 train dates + 12 test dates (aggregate_ic_series needs n >= 10 per split)
    dates = list(range(20241201, 20241201 + 12)) + list(range(20250106, 20250106 + 12))
    panel2, _ = _toy_panel(n_days=24, base_dates=dates)
    T, N = panel2.shape
    # signal[t][i] = i -> perfectly rank-correlated with forward returns
    # (instrument i's daily return slope increases with i by construction).
    sig = [[float(i) for i in range(N)] for _ in range(T)]
    res = ic_eval.evaluate_signal(sig, panel2.matrices["close"], dates, horizons=(1,))
    assert res["h1"]["train"]["mean_ic"] == pytest.approx(1.0)
    assert res["h1"]["test"]["mean_ic"] == pytest.approx(1.0)
    # IC uses t->t+1; train obs = signal dates <= 20241231 with a successor in-panel = 12,
    # test obs = the remaining 23 - 12 = 11
    assert res["h1"]["train"]["n_periods"] == 12
    assert res["h1"]["test"]["n_periods"] == 11
    neg = [[float(N - i) for i in range(N)] for _ in range(T)]
    res_neg = ic_eval.evaluate_signal(neg, panel2.matrices["close"], dates, horizons=(1,))
    assert res_neg["h1"]["test"]["mean_ic"] == pytest.approx(-1.0)


def test_horizon5_non_overlapping():
    panel, aux = _toy_panel(n_days=56)
    px = panel.matrices["close"]
    T, N = panel.shape
    sig = [[float(i) for i in range(N)] for _ in range(T)]
    res = ic_eval.evaluate_signal(sig, px, list(range(20250102, 20250102 + T)), horizons=(5,))
    assert res["h5"]["test"]["mean_ic"] == pytest.approx(1.0)
    # 56 rows, horizon 5 -> starts at 0,5,...,50 (t+5 < 56) = 11 non-overlapping obs
    assert res["h5"]["test"]["n_periods"] == 11


# ---------------------------------------------------------------------------
# no-peek composite contract
# ---------------------------------------------------------------------------

def _fake_eval(ic_map):
    """{name: {"h1": {"train": agg, "test": agg}}} from {name: (train_t, train_icir, train_mean, test_mean)}."""
    out = {}
    for name, (t, icir, mean, test_mean) in ic_map.items():
        out[name] = {"h1": {"train": {"t_stat": t, "icir": icir, "mean_ic": mean},
                            "test": {"t_stat": 99.0, "icir": 9.9, "mean_ic": test_mean}}}
    return out


def test_combo_weights_train_only():
    ev = _fake_eval({
        "a": (3.0, 0.5, 0.02, 0.5),    # passes, long sign
        "b": (-4.0, 1.0, -0.03, -0.9), # passes, short sign
        "c": (1.0, 0.9, 0.04, 0.8),    # fails |t| screen despite big train icir
    })
    w1 = combine.fit_combo_weights(ev)
    assert set(w1) == {"a", "b"}
    assert w1["a"] == pytest.approx(1.0 / 3.0)
    assert w1["b"] == pytest.approx(-2.0 / 3.0)
    # perturb TEST-period stats wildly -> weights must be bit-identical (no-peek)
    ev2 = _fake_eval({"a": (3.0, 0.5, 0.02, -777.0), "b": (-4.0, 1.0, -0.03, 555.0),
                      "c": (1.0, 0.9, 0.04, 0.0)})
    assert combine.fit_combo_weights(ev2) == w1
    # nothing passes -> fail-closed empty
    assert combine.fit_combo_weights(_fake_eval({"z": (0.5, 1.0, 0.1, 0.1)})) == {}


def test_apply_combo_train_rows_unaffected_by_test_perturbation():
    T, N = 8, 6
    fa = [[float((i * 3 + t) % 7) for i in range(N)] for t in range(T)]
    fb = [[float((i + 2 * t) % 5) for i in range(N)] for t in range(T)]
    weights = {"a": 0.5, "b": -0.5}
    c1 = combine.apply_combo({"a": fa, "b": fb}, weights)
    # perturb only the test rows (t >= 4)
    fa2 = [row[:] for row in fa]
    fb2 = [row[:] for row in fb]
    for t in range(4, T):
        fa2[t] = [999.0] * N
        fb2[t] = [-999.0] * N
    c2 = combine.apply_combo({"a": fa2, "b": fb2}, weights)
    assert c1[:4] == c2[:4]  # train rows bit-identical


# ---------------------------------------------------------------------------
# basket selection: sum-to-one + participation cap math
# ---------------------------------------------------------------------------

def test_basket_weights_sum_to_one_no_cap_binding():
    insts = [f"S{i:03d}.SZ" for i in range(30)]
    scores = [float(i) for i in range(30)]
    advs = [1e8] * 30  # 1亿 ADV; cap = 5% * 1e8 / 3e6 = 1.67 >> 1/20 -> no cap
    w, capped, sel = basket.select_basket(scores, advs, insts, n=20, aum=3e6, cap_pct=0.05)
    assert len(w) == 20
    assert sum(w.values()) == pytest.approx(1.0)
    assert capped == []
    assert sel == insts[-20:][::-1]  # top-20 by score, descending


def test_basket_participation_cap_math():
    insts = [f"S{i:03d}.SZ" for i in range(4)]
    scores = [3.0, 2.0, 1.0, 0.5]
    advs = [1e6, 1e8, 1e8, 1e8]  # S000: cap = 0.05*1e6/1e6 = 0.05 weight
    w, capped, sel = basket.select_basket(scores, advs, insts, n=4, aum=1e6, cap_pct=0.05)
    assert capped == ["S000.SZ"]
    assert w["S000.SZ"] == pytest.approx(0.05)
    assert sum(w.values()) == pytest.approx(1.0)  # excess redistributed
    for other in ("S001.SZ", "S002.SZ", "S003.SZ"):
        assert w[other] == pytest.approx((1.0 - 0.05) / 3)


def test_basket_all_capped_keeps_cash_honest():
    insts = ["A.SZ", "B.SZ"]
    scores = [1.0, 0.5]
    advs = [1e5, 1e5]  # each cap = 0.05*1e5/1e6 = 0.005
    w, capped, _ = basket.select_basket(scores, advs, insts, n=2, aum=1e6, cap_pct=0.05)
    assert set(capped) == set(insts)
    assert sum(w.values()) == pytest.approx(0.01)  # 99% cash, honestly reported (< 1)


# ---------------------------------------------------------------------------
# backtest sanity: known two-period path
# ---------------------------------------------------------------------------

def test_backtest_known_path_and_cost():
    insts = [f"S{i}.SZ" for i in range(3)]
    dates = [20250102 + 5 * k for k in range(3)]
    # scores pick S2 (highest); S2 doubles then halves; others flat
    score = [[0.0, 1.0, 2.0]] * 3
    price = [[10.0, 10.0, 10.0], [10.0, 10.0, 20.0], [10.0, 10.0, 10.0]]
    adv = [[1e8] * 3] * 3
    elig = [[True] * 3] * 3
    res = basket.backtest(score, price, adv, elig, insts, reb_rows=[0, 1, 2],
                          n=1, aum=1e6, cost_bps=15.0)
    # period 1: buy S2 (turnover 1.0) then +100%: (1+1.0) - 1.0*0.0015 charged on equity
    e1 = 1.0 * (1.0 + 1.0 - 1.0 * 0.0015)
    # period 2: hold S2 (turnover 0), -50%
    e2 = e1 * (1.0 - 0.5)
    assert res["equity"][1] == pytest.approx(e1)
    assert res["equity"][2] == pytest.approx(e2)
    assert res["strategy"]["total_return"] == pytest.approx(e2 - 1.0)
    # benchmark = equal-weight all 3: only S2 moves -> +33.3% then -16.7%, near-zero turnover
    assert res["bench_equity"][1] > 1.3


# ---------------------------------------------------------------------------
# TASK 2a: factor hygiene — winsorization + greedy de-correlation
# ---------------------------------------------------------------------------

def test_winsorize_row_bounds():
    row = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 1e12, None]
    w = hygiene.cs_winsorize_row(row, lo=0.01, hi=0.99)
    vals = [v for v in w if v is not None]
    s = sorted(v for v in row if v is not None)
    q_lo, q_hi = hygiene._quantile(s, 0.01), hygiene._quantile(s, 0.99)
    assert all(q_lo <= v <= q_hi for v in vals)          # clipped to the quantile band
    assert w[-1] is None                                  # None passthrough
    assert w[9] == pytest.approx(q_hi)                    # the 1e12 outlier is clamped
    assert w[0] == pytest.approx(q_lo)
    assert hygiene.cs_winsorize_row([1.0, 2.0]) == [None, None]  # < min_n -> fail-closed


def test_winsorized_zscore_tames_outlier():
    row = [1.0, 1.1, 0.9, 1.05, 0.95, 1.02, 0.98, 1.03, 0.97, 1e6]
    z_plain = factors.cs_zscore_row(row)
    z_win = hygiene.cs_winsorized_zscore_row(row)
    assert abs(z_win[-1]) < abs(z_plain[-1])              # outlier no longer dominates
    assert abs(z_win[-1]) < 3.0


def test_greedy_decorrelate_drops_duplicate():
    # b is a near-duplicate of a (|corr|=0.95) but weaker; c is independent.
    stats = _fake_eval({
        "a": (5.0, 1.0, 0.05, 0.0),
        "b": (3.0, 0.8, 0.04, 0.0),
        "c": (4.0, 0.9, -0.03, 0.0),
        "d": (1.0, 0.7, 0.02, 0.0),   # fails |t|>2 screen
    })
    corr = {n: {m: (1.0 if n == m else None) for m in ("a", "b", "c", "d")}
            for n in ("a", "b", "c", "d")}
    corr["a"]["b"] = corr["b"]["a"] = 0.95
    corr["a"]["c"] = corr["c"]["a"] = 0.10
    corr["b"]["c"] = corr["c"]["b"] = 0.20
    kept, dropped, screened = hygiene.greedy_decorrelate(stats, corr)
    assert kept == ["a", "c"]                             # stronger a kept, duplicate b dropped
    assert dropped == [{"name": "b", "corr_with": "a", "corr": 0.95}]
    assert screened == ["d"]
    # train-only: perturb test aggregates -> selection unchanged
    stats2 = _fake_eval({"a": (5.0, 1.0, 0.05, 99.0), "b": (3.0, 0.8, 0.04, -99.0),
                         "c": (4.0, 0.9, -0.03, 0.0), "d": (1.0, 0.7, 0.02, 0.0)})
    assert hygiene.greedy_decorrelate(stats2, corr)[0] == kept


def test_fit_combo_weights_schemes_and_whitelist():
    ev = _fake_eval({
        "a": (3.0, 0.5, 0.02, 0.0),
        "b": (-4.0, 1.0, -0.03, 0.0),
        "c": (5.0, 0.9, 0.04, 0.0),
    })
    w_icir = combine.fit_combo_weights(ev, names=["a", "b"], scheme="icir")
    assert set(w_icir) == {"a", "b"}                      # whitelist excludes c
    w_eq = combine.fit_combo_weights(ev, names=["a", "b"], scheme="equal")
    assert w_eq["a"] == pytest.approx(0.5) and w_eq["b"] == pytest.approx(-0.5)
    assert w_icir["a"] != w_eq["a"]                       # schemes differ when icirs differ
    with pytest.raises(ValueError):
        combine.fit_combo_weights(ev, scheme="bogus")


# ---------------------------------------------------------------------------
# TASK 2b: champion selection is train-only
# ---------------------------------------------------------------------------

def _combo_eval(h5_train_icir, h5_test_icir):
    return {"h1": {"train": {"t_stat": 3.0, "icir": 0.5, "mean_ic": 0.02},
                   "test": {"t_stat": 3.0, "icir": 0.5, "mean_ic": 0.02}},
            "h5": {"train": {"t_stat": 3.0, "icir": h5_train_icir, "mean_ic": 0.03},
                   "test": {"t_stat": 9.0, "icir": h5_test_icir, "mean_ic": 0.5}}}


def test_champion_selection_train_only():
    s = {"icir": _combo_eval(0.8, -5.0), "equal": _combo_eval(0.6, 9.9)}
    assert combine.select_champion(s) == "icir"           # train h5 ICIR decides, not test
    s_perturbed = {"icir": _combo_eval(0.8, 777.0), "equal": _combo_eval(0.6, -777.0)}
    assert combine.select_champion(s_perturbed) == "icir"  # perturb test -> unchanged
    with pytest.raises(ValueError):
        combine.select_champion({})


# ---------------------------------------------------------------------------
# TASK 2c: real trailing-20d volume (participation ctx) + circuit breaker
# ---------------------------------------------------------------------------

def test_trailing_mean_matrix_known_values():
    vol = [[100.0, None], [200.0, 50.0], [300.0, 70.0], [None, 90.0], [500.0, 110.0]]
    adv = basket.trailing_mean_matrix(vol, window=3)
    assert adv[0] == [None, None]                         # < window valid obs -> None
    assert adv[2][0] == pytest.approx(200.0)              # mean(100,200,300)
    assert adv[4][0] == pytest.approx(333.333333, rel=1e-5)  # gap row skipped: mean(200,300,500)
    assert adv[4][1] == pytest.approx(90.0)               # mean(70,90,110)
    assert adv[1][1] is None


def test_circuit_breaker_halves_exposure_after_deep_drawdown():
    insts = [f"S{i}.SZ" for i in range(3)]
    # scores always pick S2; path: +10%, -40% (DD > 25% -> halve), -40% (halved), +100%
    score = [[0.0, 1.0, 2.0]] * 5
    price = [[10.0, 10.0, 10.0], [10.0, 10.0, 11.0], [10.0, 10.0, 6.6],
             [10.0, 10.0, 3.96], [10.0, 10.0, 7.92]]
    adv = [[1e8] * 3] * 5
    elig = [[True] * 3] * 5
    rows = [0, 1, 2, 3, 4]
    off = basket.backtest(score, price, adv, elig, insts, rows, n=1, aum=1e6, cost_bps=0.0)
    on = basket.backtest(score, price, adv, elig, insts, rows, n=1, aum=1e6,
                         cost_bps=0.0, circuit_breaker=True)
    cb = on["circuit_breaker"]
    # off: equity 1.1 -> 0.66 -> 0.396 -> 0.792 (DD from peak 1.1 = 64%)
    assert off["strategy"]["max_drawdown"] == pytest.approx(1 - 0.396 / 1.1, abs=1e-6)
    # on: after the -40% leg DD = 40% > 25% -> next leg at half exposure: loss 20% not 40%
    assert cb["half_exposure_periods"] >= 1
    assert cb["exposure_path"][2] == 0.5                  # third leg is halved
    # halved leg: 0.66 * (1 - 0.5*0.4) = 0.528 -> DD = 52% < 64%
    assert on["equity"][3] == pytest.approx(0.66 * 0.8, rel=1e-9)
    assert on["strategy"]["max_drawdown"] < off["strategy"]["max_drawdown"]
    # recovery: DD after +50%-halved leg = 1 - 0.792/1.1 = 28% > 15% -> still halved
    assert cb["exposure_path"][3] == 0.5


# ---------------------------------------------------------------------------
# TASK 1: paper-track idempotency (temp sqlite)
# ---------------------------------------------------------------------------

def test_paper_track_idempotent_and_conflict_fail_closed(tmp_path):
    db = str(tmp_path / "paper_track.db")
    store = PaperTrackStore(db, create=True)
    rows = [(1000, 0.01, 0.05, "BACKFILL"), (2000, -0.02, None, "BACKFILL"),
            (3000, 0.03, 0.10, "BACKFILL")]
    store.append_periods("alpha_x", rows)
    store.recompute_summary("alpha_x", backtest_sharpe=1.0)
    first = store.periods("alpha_x")
    summary1 = store.track("alpha_x")
    # rerun with identical content -> skipped, summary unchanged
    store.append_periods("alpha_x", rows)
    store.recompute_summary("alpha_x", backtest_sharpe=1.0)
    assert store.periods("alpha_x") == first
    assert store.track("alpha_x") == summary1
    assert summary1["n_periods"] == 3
    # same key, different content -> fail-closed
    with pytest.raises(PaperTrackStoreError):
        store.append_periods("alpha_x", [(1000, 0.99, 0.05, "BACKFILL")])
    # a genuinely new week appends
    store.append_periods("alpha_x", [(4000, 0.005, 0.02, "BACKFILL")])
    assert len(store.periods("alpha_x")) == 4


# ---------------------------------------------------------------------------
# bad-bar hygiene: board limit bands, gap treatment, no fabrication, no cascade
# ---------------------------------------------------------------------------

def test_board_limit_band_per_board():
    assert eq_panel.board_limit_band("600000.SH") == eq_panel.LIMIT_BAND_MAIN
    assert eq_panel.board_limit_band("000001.SZ") == eq_panel.LIMIT_BAND_MAIN
    assert eq_panel.board_limit_band("300942.SZ") == eq_panel.LIMIT_BAND_GROWTH
    assert eq_panel.board_limit_band("301577.SZ") == eq_panel.LIMIT_BAND_GROWTH
    assert eq_panel.board_limit_band("688592.SH") == eq_panel.LIMIT_BAND_GROWTH
    assert eq_panel.board_limit_band("830001.BJ") == eq_panel.LIMIT_BAND_BJ


def test_detect_bad_bars_respects_bands_and_updates_reference():
    bars = {}
    # ChiNext: +19% ok, +25% bad, next day -10% vs the BAD close is fine (reference updates)
    closes = [10.0, 11.9, 14.875, 13.39]
    for k, c in enumerate(closes):
        bars[20250102 + k] = _bar("300001.SZ", 20250102 + k, c)
    bad = eq_panel.detect_bad_bars(bars, "300001.SZ")
    assert set(bad) == {20250104}                      # only the +25% bar
    assert bad[20250104] == pytest.approx(25.0, abs=0.01)
    # main board: +15% bad (band 10.5%)
    bars2 = {20250102: _bar("600000.SH", 20250102, 10.0),
             20250103: _bar("600000.SH", 20250103, 11.5)}
    assert set(eq_panel.detect_bad_bars(bars2, "600000.SH")) == {20250103}
    # no cascade: 5.21 -> 35.35 (bad, +578%) -> 29.42 (-16.8%, good on ChiNext band)
    bars3 = {20210129: _bar("300942.SZ", 20210129, 5.21),
             20210209: _bar("300942.SZ", 20210209, 35.35),
             20210210: _bar("300942.SZ", 20210210, 29.42)}
    assert set(eq_panel.detect_bad_bars(bars3, "300942.SZ")) == {20210209}


def test_bad_bar_gap_treatment_no_fabrication():
    d0, d1, d2 = 20250102, 20250103, 20250106
    rows = {"300001.SZ": [_bar("300001.SZ", d0, 10.0),
                          _bar("300001.SZ", d1, 15.0),    # +50% > 20.5% -> bad bar
                          _bar("300001.SZ", d2, 14.0)]}
    panel, aux = eq_panel.build_ashare_panel(_FakeStore(rows), ["300001.SZ"])
    i = 0
    assert aux["bad_bar"][1][i] is True
    assert aux["tradable"][1][i] is False              # untradeable on the bad day
    assert aux["close_clean"][1][i] is None            # cleaned price = gap (no fabricated value)
    assert panel.matrices["close"][1][i] == 15.0       # raw PIT record preserved untouched
    assert aux["listed_days"][1][i] == 1               # bad day not counted (gap convention)
    assert aux["listed_days"][2][i] == 2
    assert aux["bad_bar_detail"]["300001.SZ"] == [{"date": d1, "ret_pct": 50.0}]


def test_bad_bar_excluded_from_factor_trailing_windows():
    dates = [20250102 + k for k in range(10)]
    # day5 = 12.6:单日 +21.15% 刺破创业板 20.5% 带(bad),次日 10.5 = -16.7% 带内回落
    # (参照系更新 -> 只有 day5 一根坏 bar;此时"坏 bar"才与"缺失一天"严格同构)
    with_bad = [10.0, 10.1, 10.2, 10.3, 10.4, 12.6, 10.5, 10.6, 10.7, 10.8]
    # 正确对照 = 该 bar 完全缺失(坏 bar 必须与缺 bar 同规约:跳过、绝不编造)
    dates_missing = dates[:5] + dates[6:]
    good_missing = with_bad[:5] + with_bad[6:]
    store_missing = _FakeStore({"300001.SZ": [_bar("300001.SZ", d, c)
                                              for d, c in zip(dates_missing, good_missing)]})
    store_bad = _FakeStore({"300001.SZ": [_bar("300001.SZ", d, c) for d, c in zip(dates, with_bad)]})
    p_miss, aux_miss = eq_panel.build_ashare_panel(store_missing, ["300001.SZ"])
    p_bad, aux_bad = eq_panel.build_ashare_panel(store_bad, ["300001.SZ"])
    assert aux_bad["bad_bar"][5][0] is True
    elig_miss = [[True] * 1 for _ in dates_missing]
    elig_bad = [[True] * 1 for _ in dates]
    m_miss = factors.build_factor_matrices(p_miss, aux_miss, elig_miss)
    m_bad = factors.build_factor_matrices(p_bad, aux_bad, elig_bad)
    # 坏 bar 之后,两只面板的尾部窗口逐位相同(坏 bar 一天等价于缺失一天):
    # m_bad 的 t>=6 对应 m_miss 的 t>=5(后者少一行)
    for name in factors.FACTOR_FUNCS:
        col_miss = [m_miss[name][t][0] for t in range(5, 9)]
        col_bad = [m_bad[name][t][0] for t in range(6, 10)]
        assert col_bad == col_miss, name


# ---------------------------------------------------------------------------
# turnover-reduction variants: buffer-band hysteresis + one-way cap
# ---------------------------------------------------------------------------

def _scores_desc(n, order=None):
    """n instruments, scores descending by index order (or custom permutation)."""
    insts = [f"S{i:03d}.SZ" for i in range(n)]
    order = order or list(range(n))
    scores = [0.0] * n
    for pos, idx in enumerate(order):
        scores[idx] = float(n - pos)
    return insts, scores, [1e8] * n


def test_buffer_hysteresis_keeps_incumbent_over_higher_newcomer():
    insts, scores, advs = _scores_desc(40)
    # prev = ranks 11..30 (20 names); newcomer rank 1 exists but no free slot
    prev = {insts[i]: 0.05 for i in range(10, 30)}
    w, capped, sel = basket.select_basket_managed(scores, advs, insts, prev, n=20,
                                                  aum=1e6, cap_pct=0.05, buffer_k=30)
    assert set(sel) == {insts[i] for i in range(10, 30)}   # all incumbents rank<=30 stay
    assert insts[0] not in sel                              # rank-1 newcomer blocked by hysteresis
    assert sum(w.values()) == pytest.approx(1.0)


def test_buffer_refill_order_and_drop_beyond_band():
    insts, scores, advs = _scores_desc(40)
    # prev = ranks 11..20 (10 names) + one at rank 35 (beyond buffer 30 -> dropped)
    prev = {insts[i]: 0.09 for i in range(10, 20)}
    prev[insts[34]] = 0.09
    w, _, sel = basket.select_basket_managed(scores, advs, insts, prev, n=20,
                                             aum=1e6, cap_pct=0.05, buffer_k=30)
    assert insts[34] not in sel                             # rank 35 > buffer -> dropped
    # kept incumbents (ranks 11..20) + refill from the top (ranks 1..10)
    assert set(sel) == {insts[i] for i in range(20)}


def test_buffer_trims_to_n_highest_ranked_incumbents():
    insts, scores, advs = _scores_desc(40)
    prev = {insts[i]: 1.0 / 22 for i in range(22)}          # 22 incumbents, all rank<=30
    w, _, sel = basket.select_basket_managed(scores, advs, insts, prev, n=20,
                                             aum=1e6, cap_pct=0.05, buffer_k=30)
    assert len(sel) == 20
    assert set(sel) == {insts[i] for i in range(20)}        # ranks 21,22 trimmed


def test_max_oneway_turnover_cap_math():
    insts, scores, advs = _scores_desc(40)                   # top-20 = S000..S019
    prev = {insts[i]: 0.05 for i in range(20, 40)}           # ALL incumbents ranked 21..40
    # baseline would replace all 20 (one-way sells = 1.0); cap 0.40 keeps the
    # 12 highest-ranked incumbents (ranks 21..32) and replaces only 8 slots
    w, _, sel = basket.select_basket_managed(scores, advs, insts, prev, n=20,
                                             aum=1e6, cap_pct=0.05, max_oneway=0.40)
    assert basket._oneway_sells(sel, prev) <= 0.40 + 1e-12
    kept_incumbents = set(sel) & set(prev)
    assert kept_incumbents == {insts[i] for i in range(20, 32)}  # best-ranked 12 kept
    assert set(sel) - set(prev) == {insts[i] for i in range(8)}  # top-8 newcomers enter


def test_select_for_variant_default_is_byte_identical_to_baseline():
    insts, scores, advs = _scores_desc(30)
    prev = {insts[29]: 1.0}
    base = basket.select_basket(scores, advs, insts, n=20, aum=3e6, cap_pct=0.05)
    via = basket.select_for_variant(scores, advs, insts, prev, n=20, aum=3e6, cap_pct=0.05)
    assert via == base


def test_backtest_variant_reduces_turnover_and_default_unchanged():
    insts = [f"S{i:03d}.SZ" for i in range(40)]
    # scores rotate: even rows favor low indices, odd rows favor high indices
    T = 12
    score = [[float((i + (t % 2) * 20) % 40) for i in range(40)] for t in range(T)]
    price = [[10.0] * 40 for _ in range(T)]
    adv = [[1e8] * 40 for _ in range(T)]
    elig = [[True] * 40 for _ in range(T)]
    rows = list(range(T))
    base = basket.backtest(score, price, adv, elig, insts, rows, n=20, aum=1e7, cost_bps=15.0)
    buf = basket.backtest(score, price, adv, elig, insts, rows, n=20, aum=1e7,
                          cost_bps=15.0, buffer_k=39)
    assert base["avg_turnover"] > 1.5                    # 每期全轮换 -> Σ|Δw|≈2
    assert buf["avg_turnover"] < base["avg_turnover"]
    # default path byte-identical when variant params are None
    again = basket.backtest(score, price, adv, elig, insts, rows, n=20, aum=1e7,
                            cost_bps=15.0, buffer_k=None, max_oneway=None)
    assert again["equity"] == base["equity"]
    assert buf["variant"] == {"buffer_k": 39, "max_oneway": None}
