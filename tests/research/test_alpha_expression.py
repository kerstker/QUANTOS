"""The cross-sectional alpha engine's CORE guarantee is no-lookahead: a time-series op reads only an
instrument's past, a cross-sectional op reads only the same period, and a future bar never changes a
past alpha value. These tests pin that, plus operator correctness and the backtest integration."""
import math

from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from services.alpha.panel_backtest import run_panel_backtest
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


# ---- canonical id ---------------------------------------------------------------------------
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
