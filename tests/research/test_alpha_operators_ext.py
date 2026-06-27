"""Phase 1 increment 1a — operator-registry expansion on the cross-sectional engine.

Closes the highest-value Alpha101-class gaps WITHOUT any new data and WITHOUT breaking PIT-safety:
  - cs_winsorize  : robust k-MAD outlier clipping per cross-section (so one outlier can't dominate zscore),
  - cs_residualize: cross-sectional OLS residual of x on y (the NEUTRALIZATION primitive — no industry data),
  - ts_cov/ts_beta: per-instrument windowed covariance / regression slope (pairwise gap),
  - sqrt          : signed square root sign(x)*sqrt(|x|).
Every new operator must be evaluable, deterministic, and PIT-safe (proven by expression_leakage)."""
import math

import services.research.alpha_expression as ax
from services.research import expression_leakage as el


def _panel():
    T, N = 14, 4
    close = [[100.0 + i + 2 * j for j in range(N)] for i in range(T)]
    volume = [[1000.0 + 10 * i + 5 * j for j in range(N)] for i in range(T)]
    return ax.FieldPanel(["A", "B", "C", "D"], list(range(T)), {"close": close, "volume": volume})


def _row(m, r):
    return [m[r][i] for i in range(len(m[0]))]


# ---- cs_winsorize ----------------------------------------------------------------------------
def test_cs_winsorize_clips_outlier_keeps_inliers():
    row = [1.0, 2.0, 3.0, 100.0]                       # one gross outlier
    m = [row[:]]                                       # T=1, N=4
    out = ax.cs_winsorize(m)
    o = out[0]
    assert o[3] < 100.0                                # the outlier is pulled in
    assert max(o) < 100.0
    # the median-side inliers are untouched (within the MAD band)
    assert o[0] == 1.0 and o[1] == 2.0 and o[2] == 3.0


def test_cs_winsorize_noop_when_no_spread():
    m = [[5.0, 5.0, 5.0]]                               # zero MAD -> unchanged (no fabricated band)
    assert ax.cs_winsorize(m)[0] == [5.0, 5.0, 5.0]


# ---- cs_residualize (neutralization primitive) ----------------------------------------------
def test_cs_residualize_orthogonalizes():
    # x = 3*y across names -> residual of x on y is ~0 (y fully explains x)
    y_row = [1.0, 2.0, 3.0, 4.0]
    x_row = [3.0 * v for v in y_row]
    x, y = [x_row[:]], [y_row[:]]
    resid = ax.cs_residualize(x, y)[0]
    assert all(abs(v) < 1e-9 for v in resid)


def test_cs_residualize_residual_is_uncorrelated_with_y():
    y_row = [1.0, 2.0, 3.0, 5.0]
    x_row = [2.0, 1.0, 4.0, 3.0]
    resid = ax.cs_residualize([x_row[:]], [y_row[:]])[0]
    ym = sum(y_row) / len(y_row)
    # sum(resid_i * (y_i - mean_y)) ~ 0  (residual orthogonal to the regressor by construction)
    dot = sum(r * (yv - ym) for r, yv in zip(resid, y_row))
    assert abs(dot) < 1e-9


def test_cs_residualize_against_constant_is_demean():
    x_row = [1.0, 2.0, 3.0, 6.0]
    const = [[7.0, 7.0, 7.0, 7.0]]                      # zero-variance regressor -> residual == demean(x)
    resid = ax.cs_residualize([x_row[:]], const)[0]
    demeaned = ax.cs_demean([x_row[:]])[0]
    assert all(abs(a - b) < 1e-9 for a, b in zip(resid, demeaned))


# ---- ts_cov / ts_beta ------------------------------------------------------------------------
def test_ts_beta_recovers_slope():
    # b is a ramp; a = 2*b. Over a full window, beta(a on b) == 2, cov > 0.
    T = 6
    b = [[float(i + 1)] for i in range(T)]             # N=1 column ramp 1..6
    a = [[2.0 * b[i][0]] for i in range(T)]
    beta = ax.ts_beta(a, b, 4)
    cov = ax.ts_cov(a, b, 4)
    assert beta[T - 1][0] is not None and abs(beta[T - 1][0] - 2.0) < 1e-9
    assert cov[T - 1][0] is not None and cov[T - 1][0] > 0
    assert beta[0][0] is None                           # window not yet full -> None (no lookahead fill)


# ---- sqrt ------------------------------------------------------------------------------------
def test_signed_sqrt():
    m = [[4.0, -9.0, 0.0]]
    o = ax.ew_sqrt(m)[0]
    assert o == [2.0, -3.0, 0.0]


# ---- integration: registered, evaluable, PIT-safe -------------------------------------------
def test_new_operators_registered_and_evaluable():
    panel = _panel()
    exprs = [
        ("cs_winsorize", ("field", "close")),
        ("cs_rank", ("sqrt", ("ts_delta", ("field", "close"), 2))),
        ("cs_residualize", ("field", "close"), ("field", "volume")),
        ("cs_zscore", ("ts_cov", ("field", "close"), ("field", "volume"), 5)),
        ("cs_zscore", ("ts_beta", ("field", "close"), ("field", "volume"), 5)),
    ]
    for e in exprs:
        m = ax.evaluate(e, panel)
        assert ax._dims(m) == (14, 4)
        # serialize round-trips through the canonical id without raising
        assert ax.alpha_id(e).startswith("ax_")


def test_evaluate_rejects_bool_literals():
    # review fix: a bool in a const/exponent slot is a likely generator bug; evaluate must NOT silently
    # coerce True->1.0 (it now fails closed, matching dsl_checker which already rejects bool).
    panel = _panel()
    for bad in (("const", True), ("cs_rank", ("const", False)),
                ("signed_power", ("field", "close"), True)):
        try:
            ax.evaluate(bad, panel)
            assert False, f"expected ValueError for {bad}"
        except ValueError:
            pass


def test_new_operators_are_pit_safe():
    panel = _panel()
    for e in [
        ("cs_winsorize", ("field", "close")),
        ("cs_residualize", ("field", "close"), ("field", "volume")),
        ("cs_zscore", ("ts_beta", ("field", "close"), ("field", "volume"), 5)),
        ("cs_rank", ("sqrt", ("field", "volume"))),
    ]:
        assert el.compute_leakage_ok(e, panel) is True, ax.serialize(e)
