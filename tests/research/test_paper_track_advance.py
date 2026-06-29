# tests/research/test_paper_track_advance.py
from services.research.paper_trading.store import PaperTrackStore
from services.research.paper_trading.advance import advance_paper_track


def _store():
    return PaperTrackStore(":memory:", create=True)


def test_advance_appends_only_the_held_out_tail():
    s = _store()
    rets = [0.10, 0.10, 0.10, 0.01, -0.02, 0.03]   # boundary=3 -> paper = last 3
    ics = [0.2, 0.2, 0.2, 0.05, -0.01, 0.04]
    n = advance_paper_track(s, "a1", rets, ics, boundary=3, ts0=0, dt=1000, backtest_sharpe=2.0)
    assert n == 3
    p = s.periods("a1")
    assert [row["as_of_ts"] for row in p] == [3000, 4000, 5000]    # only the [3:] tail, by ts0+i*dt
    assert [round(row["realized_return"], 3) for row in p] == [0.01, -0.02, 0.03]
    assert s.track("a1")["n_periods"] == 3


def test_advance_is_idempotent():
    s = _store()
    rets, ics = [0.0, 0.0, 0.01, 0.02], [0.0, 0.0, 0.05, 0.06]
    advance_paper_track(s, "a1", rets, ics, boundary=2, ts0=0, dt=1000, backtest_sharpe=1.0)
    n2 = advance_paper_track(s, "a1", rets, ics, boundary=2, ts0=0, dt=1000, backtest_sharpe=1.0)  # re-run
    assert n2 == 0 and s.track("a1")["n_periods"] == 2            # nothing new on re-run
