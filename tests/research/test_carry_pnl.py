"""The economic return of a perp long/short book is price_LS + funding_collected − (fees handled
upstream): a dollar-neutral carry book RECEIVES funding on BOTH legs (longs negative-funding names,
shorts positive-funding names). This makes that the alpha's return series so the pipeline's "money"
metrics reflect the REAL carry yield, not just price prediction. PIT-safe (signal<=t, P&L uses
funding[t+1]). Deterministic."""
import services.research.alpha_expression as ax
from services.research.carry_pnl import (
    economic_return_series,
    funding_adjusted_periods,
)


def _panel():
    # flat price (so price_LS ~ 0), STABLE per-name funding, 6 names spanning -/+ funding
    T, N = 10, 6
    base = [0.001 * (i - 2.5) for i in range(N)]          # [-0.0025 .. +0.0025]
    close = [[100.0] * N for _ in range(T)]
    funding = [base[:] for _ in range(T)]
    return ax.FieldPanel([f"C{i}" for i in range(N)], list(range(T)),
                         {"close": close, "funding": funding}), base


def _signal(base):
    # signal = -funding  -> longs the negative-funding names, shorts the positive-funding names
    T, N = 10, 6
    return [[-base[i] for i in range(N)] for _ in range(T)]


def test_book_collects_funding_on_both_legs():
    panel, base = _panel()
    sig = _signal(base)
    with_f = economic_return_series(sig, panel, fwd_horizon=1, quantile=0.34, with_funding=True)
    no_f = economic_return_series(sig, panel, fwd_horizon=1, quantile=0.34, with_funding=False)
    assert sum(no_f) == 0.0                               # flat price, no funding -> zero
    assert sum(with_f) > 0.0                              # carry book collects positive funding
    assert len(with_f) == len(no_f) == 9                 # T - fwd_horizon


def test_wrong_sign_signal_pays_funding():
    panel, base = _panel()
    sig = [[base[i] for i in range(6)] for _ in range(10)]   # signal = +funding (long the wrong side)
    with_f = economic_return_series(sig, panel, fwd_horizon=1, quantile=0.34, with_funding=True)
    assert sum(with_f) < 0.0                              # the inverted book PAYS funding


def test_deterministic_and_handles_no_funding_field():
    # a panel WITHOUT a funding matrix -> price-only (with_funding requested but absent => price only)
    T, N = 8, 6
    close = [[100.0 + i + t for i in range(N)] for t in range(T)]
    panel = ax.FieldPanel([f"C{i}" for i in range(N)], list(range(T)), {"close": close})
    sig = [[float((i + t) % N) for i in range(N)] for t in range(T)]
    a = economic_return_series(sig, panel, with_funding=True)
    b = economic_return_series(sig, panel, with_funding=True)
    assert a == b and len(a) == T - 1                    # deterministic, no crash without funding


def test_invalid_quantile_fails_closed():
    # review P1: quantile outside (0,1] silently produced degenerate all-zero books -> now fail-closed
    panel, base = _panel()
    sig = _signal(base)
    for bad in (0.0, -0.1, 1.5, 2.0):
        raised = False
        try:
            economic_return_series(sig, panel, quantile=bad)
        except ValueError:
            raised = True
        assert raised, bad


def test_funding_adjusted_periods_accrue_every_holding_window_cashflow():
    instruments = ("A", "B", "C", "D")
    close = [[100.0] * 4 for _ in range(7)]
    funding_row = (-0.002, -0.001, 0.001, 0.002)
    funding = [list(funding_row) for _ in range(7)]
    panel = ax.FieldPanel(
        instruments,
        tuple(range(7)),
        {"close": close, "funding": funding},
    )
    signal = [[-value for value in funding_row] for _ in range(7)]

    periods = funding_adjusted_periods(
        signal,
        panel,
        fwd_horizon=2,
        non_overlapping=True,
    )

    assert [period.event_time for period in periods] == [0, 2, 4]
    assert periods[0].forward_returns == tuple(-2.0 * value for value in funding_row)


def test_funding_adjusted_periods_fail_closed_without_funding_authority():
    panel, base = _panel()
    del panel.matrices["funding"]

    raised = False
    try:
        funding_adjusted_periods(_signal(base), panel)
    except ValueError:
        raised = True
    assert raised


def test_no_rng_or_wallclock_import():
    import inspect
    import services.research.carry_pnl as m
    src = inspect.getsource(m)
    assert "import random" not in src and "import time" not in src and "Random" not in src
