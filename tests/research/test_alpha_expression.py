"""The cross-sectional alpha engine's CORE guarantee is no-lookahead: a time-series op reads only an
instrument's past, a cross-sectional op reads only the same period, and a future bar never changes a
past alpha value. These tests pin that, plus operator correctness and the backtest integration."""
import math

import pytest

from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from services.alpha.panel_backtest import (
    _run_panel_backtest_legacy_diagnostic as run_panel_backtest,
)
from services.research import alpha_expression as ax


def _bar(inst, et, close, vol=100.0):
    return Bar(instrument_id=inst, event_time=et, knowledge_time=et,
               open=close, high=close + 1, low=close - 1, close=close, volume=vol)


def _store(extra=()):
    s = SqlitePITStore(":memory:", create=True)
    # 3 instruments, 6 daily buckets; B trends up, C trends down, A flat-ish
    px = {"A": [10, 10, 11, 10, 11, 10], "B": [10, 11, 12, 13, 14, 15], "C": [20, 18, 16, 14, 12, 10]}
    ets = [10, 20, 30, 40, 50, 60]
    for inst, series in px.items():
        s.add_many(inst, [_bar(inst, et, c) for et, c in zip(ets, series)])
    for b in extra:
        s.add(b.instrument_id, b)
    return s, ets


# ---- no-lookahead ---------------------------------------------------------------------------
def test_future_bar_does_not_change_past_cells():
    s0, ets = _store()
    s1, _ = _store(extra=[_bar("A", 70, 999.0), _bar("B", 70, 50.0)])  # a FUTURE bucket (et=70)
    p0 = ax.build_field_panel(s0, ["A", "B", "C"], ets)
    p1 = ax.build_field_panel(s1, ["A", "B", "C"], ets)   # same event_times -> future bar excluded
    assert p0.matrices["close"] == p1.matrices["close"]


def test_field_panel_uses_explicit_decision_time_for_delayed_daily_close():
    store = SqlitePITStore(":memory:", create=True)
    store.add(
        "A",
        Bar(
            instrument_id="A",
            event_time=10,
            knowledge_time=20,
            open=9.0,
            high=11.0,
            low=8.0,
            close=10.0,
            volume=100.0,
        ),
    )

    unavailable = ax.build_field_panel(store, ["A"], [10])
    available = ax.build_field_panel(store, ["A"], [10], decision_times=[20])

    assert unavailable.matrices["close"] == [[None]]
    assert available.matrices["close"] == [[10.0]]
    assert available.event_times == (10,)
    assert available.decision_times == (20,)


def test_field_panel_rejects_legacy_same_open_time_row_under_minimum_lag():
    store = SqlitePITStore(":memory:", create=True)
    store.add("A", _bar("A", 10, 10.0))

    contaminated = ax.build_field_panel(
        store,
        ["A"],
        [10],
        decision_times=[20],
        minimum_knowledge_lag=10,
    )

    assert contaminated.matrices["close"] == [[None]]


def test_panel_to_periods_rejects_delayed_close_without_execution_clock():
    panel = ax.FieldPanel(
        ("A", "B"),
        (10, 20),
        {"close": [[10.0, 20.0], [11.0, 19.0]]},
        decision_times=(20, 30),
    )
    alpha = [[1.0, -1.0], [1.0, -1.0]]

    with pytest.raises(ValueError, match="explicit post-decision execution"):
        ax.panel_to_periods(alpha, panel)


def test_ts_op_at_row_t_ignores_future_rows():
    m = [[1.0], [2.0], [3.0], [4.0], [5.0]]
    m2 = [r[:] for r in m]
    m2[4][0] = 999.0                                      # change only the LAST (future) row
    a, b = ax.ts_mean(m, 3), ax.ts_mean(m2, 3)
    assert a[2] == b[2] and a[3] == b[3]                  # rows <= 3 unchanged by the future edit


def test_cs_op_at_row_t_uses_only_that_row():
    m = [[1.0, 2.0, 3.0], [10.0, 20.0, 30.0]]
    m2 = [r[:] for r in m]
    m2[1] = [99.0, 1.0, 50.0]                             # change row 1 only
    assert ax.cs_rank(m)[0] == ax.cs_rank(m2)[0]          # row 0 ranks unaffected


# ---- operator correctness -------------------------------------------------------------------
def test_ts_delay_and_delta():
    m = [[1.0], [3.0], [6.0]]
    assert ax.ts_delay(m, 1) == [[None], [1.0], [3.0]]
    assert ax.ts_delta(m, 1) == [[None], [2.0], [3.0]]


def test_cs_rank_is_0_to_1_and_ordered():
    row = ax.cs_rank([[5.0, 1.0, 3.0]])[0]
    assert row[1] == 0.0 and row[2] == 0.5 and row[0] == 1.0   # 1<3<5


def test_cs_demean_row_sums_to_zero():
    row = ax.cs_demean([[1.0, 2.0, 6.0]])[0]
    assert abs(sum(row)) < 1e-9


def test_cs_scale_l1_norm_is_one():
    row = ax.cs_scale([[1.0, -3.0]])[0]
    assert abs(sum(abs(v) for v in row) - 1.0) < 1e-9


def test_none_propagates():
    m = [[1.0, None], [2.0, 5.0]]
    assert ax.ts_delta(m, 1)[1] == [1.0, None]            # B has no t-1 value -> None
    assert ax.cs_rank([[1.0, None, 2.0]])[0][1] is None




def test_cs_winsorize_is_unary_and_three_element_form_is_rejected():
    panel = ax.FieldPanel(("A", "B", "C"), (1,), {"close": [[1.0, 2.0, 100.0]]})
    expr = ("cs_winsorize", ("field", "close"))
    assert ax.evaluate(expr, panel) == ax.cs_winsorize(panel.matrices["close"])
    with pytest.raises(ValueError):
        ax.evaluate(("cs_winsorize", ("field", "close"), 3.0), panel)
def test_deserialize_round_trips_serialize():
    """deserialize is the exact inverse of serialize across fields / consts / windows / nesting."""
    exprs = [
        ("field", "close"),
        ("cs_rank", ("neg", ("field", "global_ls"))),
        ("cs_zscore", ("ts_delta", ("field", "volume"), 7)),
        ("cs_rank", ("ts_argmin", ("field", "global_ls"), 30)),
        ("div", ("add", ("cs_zscore", ("field", "funding")), ("const", 4.0)), ("const", 2.0)),
        ("cs_zscore", ("ts_cov", ("field", "close"), ("field", "volume"), 14)),
    ]
    for e in exprs:
        s = ax.serialize(e)
        assert ax.deserialize(s) == e            # structural round-trip
        assert ax.serialize(ax.deserialize(s)) == s
        assert ax.alpha_id(ax.deserialize(s)) == ax.alpha_id(e)


def test_deserialize_fail_closed_on_garbage():
    import pytest
    for bad in ["(field close", "field close)", "(cs_rank (neg", ""]:
        with pytest.raises(ValueError):
            ax.deserialize(bad)


def test_commutative_canonical_id_is_order_independent():
    e1 = ("add", ("field", "close"), ("field", "volume"))
    e2 = ("add", ("field", "volume"), ("field", "close"))
    assert ax.alpha_id(e1) == ax.alpha_id(e2)
    assert ax.alpha_id(("sub", ("field", "close"), ("field", "volume"))) != ax.alpha_id(e1)


# ---- integration: expression -> panel -> backtest -------------------------------------------
def test_evaluate_and_backtest_a_cross_sectional_alpha():
    s, ets = _store()
    panel = ax.build_field_panel(s, ["A", "B", "C"], ets)
    # a real cross-sectional alpha: rank of 2-bucket momentum (cs op composed with a ts op)
    expr = ("cs_rank", ("ts_delta", ("field", "close"), 1))
    alpha = ax.evaluate(expr, panel)
    assert ax._dims(alpha) == (len(ets), 3)
    periods = ax.panel_to_periods(alpha, panel, fwd_horizon=1)
    assert periods and all(len(p.instruments) >= 2 for p in periods)
    res = run_panel_backtest(periods)
    assert len(res.period_returns) == len(periods)
    assert all(math.isfinite(r) for r in res.period_returns)


# ---- non_overlapping resampling (2026-07-09, real-data Sharpe/DSR inflation fix) -------------
def _wide_store(n_rows: int = 21):
    """21 daily buckets, 3 instruments, deterministic non-degenerate prices (no gaps)."""
    s = SqlitePITStore(":memory:", create=True)
    ets = [10 * (i + 1) for i in range(n_rows)]
    px = {
        "A": [10.0 + 0.1 * ((i * 7) % 5) for i in range(n_rows)],
        "B": [10.0 + 0.05 * i for i in range(n_rows)],
        "C": [20.0 - 0.03 * i for i in range(n_rows)],
    }
    for inst, series in px.items():
        s.add_many(inst, [_bar(inst, et, c) for et, c in zip(ets, series)])
    return s, ets


def test_panel_to_periods_non_overlapping_skips_rows_within_the_forward_window():
    s, ets = _wide_store()
    panel = ax.build_field_panel(s, ["A", "B", "C"], ets)
    expr = ("cs_rank", ("field", "close"))
    alpha = ax.evaluate(expr, panel)
    overlapping = ax.panel_to_periods(alpha, panel, fwd_horizon=3)
    non_overlapping = ax.panel_to_periods(alpha, panel, fwd_horizon=3, non_overlapping=True)
    assert len(non_overlapping) < len(overlapping)   # strictly fewer: gaps are enforced
    # consecutive KEPT periods must never share a row of their forward-return window: the row
    # index gap between them (recoverable from event_time here, since this fixture's rows are
    # exactly 10ms apart with no gaps) must be >= fwd_horizon.
    row_of = {et: i for i, et in enumerate(ets)}
    rows = [row_of[p.event_time] for p in non_overlapping]
    gaps = [b - a for a, b in zip(rows, rows[1:])]
    assert gaps and all(g >= 3 for g in gaps)


def test_panel_to_periods_non_overlapping_is_a_noop_at_fwd_horizon_1():
    """fwd_horizon=1 periods are already 1 row apart by construction — non_overlapping=True must
    change NOTHING here, since every existing real caller uses fwd_horizon=1 (backward-compat
    guarantee, not just an assertion of convenience)."""
    s, ets = _store()
    panel = ax.build_field_panel(s, ["A", "B", "C"], ets)
    expr = ("cs_rank", ("ts_delta", ("field", "close"), 1))
    alpha = ax.evaluate(expr, panel)
    default = ax.panel_to_periods(alpha, panel, fwd_horizon=1)
    explicit_off = ax.panel_to_periods(alpha, panel, fwd_horizon=1, non_overlapping=False)
    explicit_on = ax.panel_to_periods(alpha, panel, fwd_horizon=1, non_overlapping=True)
    assert [p.event_time for p in default] == [p.event_time for p in explicit_off] == \
           [p.event_time for p in explicit_on]
