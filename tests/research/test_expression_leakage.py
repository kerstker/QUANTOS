"""A cross-sectional alpha expression must be MACHINE-PROVEN PIT-safe, not merely "safe by
construction": perturb every strictly-future panel row and the as-of cross-section at the target
row must be bit-invariant. This is the cs-engine analog of services/features/leakage.py (which
perturbs future Bars); it lets the cs path COMPUTE leakage_ok instead of leaving it None, and it
catches any future-reading operator the moment one is added. Deterministic, fail-closed."""
import services.research.alpha_expression as ax
from services.research import expression_leakage as el

FIELDS = ("close", "volume", "funding")


def _panel():
    T, N = 14, 4
    close = [[100.0 + i + 2 * j for j in range(N)] for i in range(T)]
    volume = [[1000.0 + 10 * i + j for j in range(N)] for i in range(T)]
    funding = [[0.0001 * (((i + j) % 5) - 2) for j in range(N)] for i in range(T)]
    return ax.FieldPanel(["A", "B", "C", "D"], list(range(T)),
                         {"close": close, "volume": volume, "funding": funding})


# every real operator the engine ships, composed into PIT-safe trees that MUST be invariant
_PIT_SAFE = [
    ("cs_rank", ("field", "close")),
    ("cs_zscore", ("ts_mean", ("field", "volume"), 5)),
    ("cs_scale", ("ts_delta", ("field", "close"), 3)),
    ("cs_demean", ("decay_linear", ("field", "funding"), 7)),
    ("cs_rank", ("ts_std", ("field", "close"), 5)),
    ("cs_rank", ("ts_rank", ("field", "volume"), 7)),
    ("cs_rank", ("ts_argmax", ("field", "close"), 5)),
    ("cs_zscore", ("ts_corr", ("field", "close"), ("field", "volume"), 7)),
    ("cs_rank", ("mul", ("ts_mean", ("field", "funding"), 7), ("neg", ("field", "close")))),
    ("cs_scale", ("signed_power", ("cs_zscore", ("field", "volume")), 0.5)),
]


def test_pit_safe_expressions_are_invariant():
    panel = _panel()
    for expr in _PIT_SAFE:
        assert el.compute_leakage_ok(expr, panel) is True, ax.serialize(expr)


def test_future_reading_operator_is_caught(monkeypatch):
    # inject a look-ahead operator into the engine registry: ts_lead reads m[r+d] (the FUTURE).
    def _ts_lead(m, d):
        t, n = ax._dims(m)
        out = ax._empty(t, n)
        for r in range(t):
            for i in range(n):
                if r + d < t:
                    out[r][i] = m[r + d][i]
        return out
    monkeypatch.setitem(ax._TS_WINDOW, "ts_lead", _ts_lead)
    leaky = ("cs_rank", ("ts_lead", ("field", "close"), 1))
    # the engine evaluates it (now that it is registered) ...
    assert ax._dims(ax.evaluate(leaky, _panel())) == (14, 4)
    # ... and the detector MUST flag it as leakage at every assessable row.
    assert el.compute_leakage_ok(leaky, _panel()) is False
    assert el.detect_expression_leakage(leaky, _panel(), target_row=6) is False


def test_partial_future_leak_still_caught(monkeypatch):
    # a tree that mixes a safe leg with a leaking leg must still be rejected (one leak taints it)
    def _ts_lead(m, d):
        t, n = ax._dims(m)
        out = ax._empty(t, n)
        for r in range(t):
            for i in range(n):
                if r + d < t:
                    out[r][i] = m[r + d][i]
        return out
    monkeypatch.setitem(ax._TS_WINDOW, "ts_lead", _ts_lead)
    mixed = ("cs_rank", ("add", ("ts_mean", ("field", "close"), 3),
                         ("ts_lead", ("field", "volume"), 2)))
    assert el.compute_leakage_ok(mixed, _panel()) is False


def test_too_short_panel_is_unassessable():
    # fail-closed: a panel with no strictly-future row to perturb cannot be assessed -> None
    one = ax.FieldPanel(["A", "B"], [0], {"close": [[100.0, 101.0]], "volume": [[1.0, 2.0]],
                                          "funding": [[0.0, 0.0]]})
    assert el.compute_leakage_ok(("cs_rank", ("field", "close")), one) is None


def test_detection_is_deterministic():
    panel = _panel()
    expr = ("cs_zscore", ("ts_mean", ("field", "close"), 5))
    a = [el.compute_leakage_ok(expr, panel) for _ in range(5)]
    assert a == [True, True, True, True, True]
    # explicit rows argument restricts the assessed target rows and is honoured
    assert el.compute_leakage_ok(expr, panel, rows=[5, 9]) is True


def test_no_rng_or_wallclock_import():
    import inspect
    src = inspect.getsource(el)
    assert "import random" not in src and "import time" not in src and "Random" not in src
