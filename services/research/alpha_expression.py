"""Cross-sectional alpha expression engine (factor-grammar expansion).

The per-instrument SIGNAL_OPS can only express "one scalar per instrument from its own history", so
most real formulaic alphas (WorldQuant Alpha101 et al.) are INEXPRESSIBLE — they compose TIME-SERIES
operators (delay/delta/rank/decay/corr over an instrument's own past) with CROSS-SECTIONAL operators
(rank/scale/demean across the universe at a single instant). This engine adds that second axis.

Representation: an alpha is a nested-tuple expression evaluated over a FieldPanel — PIT-safe field
matrices of shape (T x N), row = period (sorted event_times), col = instrument (sorted). Operators map
(T x N) matrices to (T x N) matrices:
  - TIME-SERIES ops read DOWN a column (an instrument's own past only) -> no lookahead;
  - CROSS-SECTIONAL ops read ACROSS a row (all instruments at the SAME period) -> all knowable at t;
  - elementwise ops are pointwise.
Composing them stays PIT-safe: the base matrices are built from store.as_of (knowledge <= t), the
ts axis only looks back, the cs axis only looks at the same t. The forward return used for evaluation
is the realized LABEL (it looks ahead by design — that is the outcome, not the signal).

Pure-Python stdlib, deterministic, fail-closed (missing/short -> None, which propagates). No-lookahead
is pinned by tests/research/test_alpha_expression.py.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from typing import Optional, Sequence

from services.alpha.panel_backtest import PanelPeriod

Matrix = list  # list[list[Optional[float]]]: rows = time, cols = instrument
_EPS = 1e-12


class FieldPanel:
    """PIT-safe field matrices aligned to (event_times x instruments)."""

    __slots__ = ("instruments", "event_times", "matrices")

    def __init__(self, instruments, event_times, matrices) -> None:
        self.instruments = tuple(instruments)
        self.event_times = tuple(event_times)
        self.matrices = matrices            # {field_name: Matrix}

    @property
    def shape(self):
        return (len(self.event_times), len(self.instruments))


def build_field_panel(store, instruments: Sequence[str], event_times: Sequence[int],
                      fields: Sequence[str] = ("close", "volume")) -> FieldPanel:
    """PIT field matrices: cell[t][i] = instrument i's field at event_time t, KNOWABLE AT t
    (latest_as_of with event_time_max=t, used only if the latest knowable bar's event_time == t;
    else None). Restatement-safe and no-lookahead. With CachingPITStore this is an in-memory build."""
    insts = sorted(instruments)
    ets = sorted(event_times)
    mats = {f: [] for f in fields}
    for t in ets:
        rows = {f: [] for f in fields}
        for inst in insts:
            bar = store.latest_as_of(inst, t, t)        # latest bar with event_time<=t, knowledge<=t
            present = bar is not None and bar.event_time == t
            for f in fields:
                rows[f].append(float(getattr(bar, f)) if present else None)
        for f in fields:
            mats[f].append(rows[f])
    return FieldPanel(insts, ets, mats)


# ---- matrix helpers -------------------------------------------------------------------------
def _dims(m: Matrix):
    return (len(m), len(m[0]) if m else 0)


def _empty(t: int, n: int) -> Matrix:
    return [[None] * n for _ in range(t)]


def _col_window(m: Matrix, t: int, i: int, w: int):
    """The w values m[t-w+1..t][i]; None if out of range or any is None (full-window, deterministic)."""
    if w < 1 or t - w + 1 < 0:
        return None
    vals = [m[t - k][i] for k in range(w)]
    return None if any(v is None for v in vals) else vals[::-1]   # oldest -> newest


# ---- time-series operators (read an instrument's own past) ----------------------------------
def ts_delay(m: Matrix, d: int) -> Matrix:
    t, n = _dims(m)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            if r - d >= 0:
                out[r][i] = m[r - d][i]
    return out


def ts_delta(m: Matrix, d: int) -> Matrix:
    t, n = _dims(m)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            if r - d >= 0 and m[r][i] is not None and m[r - d][i] is not None:
                out[r][i] = m[r][i] - m[r - d][i]
    return out


def _ts_window(m: Matrix, w: int, f) -> Matrix:
    t, n = _dims(m)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            win = _col_window(m, r, i, w)
            if win is not None:
                out[r][i] = f(win)
    return out


def ts_mean(m, w): return _ts_window(m, w, statistics.fmean)
def ts_sum(m, w): return _ts_window(m, w, lambda v: float(sum(v)))
def ts_std(m, w): return _ts_window(m, w, lambda v: statistics.pstdev(v))
def ts_min(m, w): return _ts_window(m, w, min)
def ts_max(m, w): return _ts_window(m, w, max)


def ts_rank(m, w):
    return _ts_window(m, w, lambda v: sum(1 for x in v if x <= v[-1]) / len(v))


def decay_linear(m, w):
    wts = [k + 1 for k in range(w)]                 # oldest..newest weighted 1..w
    s = float(sum(wts))
    return _ts_window(m, w, lambda v: sum(x * k for x, k in zip(v, wts)) / s)


def ts_argmax(m, w):
    return _ts_window(m, w, lambda v: float(max(range(len(v)), key=lambda k: v[k])) / (len(v) - 1)
                      if len(v) > 1 else 0.0)


def ts_argmin(m, w):
    return _ts_window(m, w, lambda v: float(min(range(len(v)), key=lambda k: v[k])) / (len(v) - 1)
                      if len(v) > 1 else 0.0)


def ts_corr(a: Matrix, b: Matrix, w: int) -> Matrix:
    t, n = _dims(a)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            wa, wb = _col_window(a, r, i, w), _col_window(b, r, i, w)
            if wa is None or wb is None:
                continue
            sda, sdb = statistics.pstdev(wa), statistics.pstdev(wb)
            if sda <= _EPS or sdb <= _EPS:
                continue
            ma, mb = statistics.fmean(wa), statistics.fmean(wb)
            cov = sum((x - ma) * (y - mb) for x, y in zip(wa, wb)) / len(wa)
            out[r][i] = cov / (sda * sdb)
    return out


def ts_cov(a: Matrix, b: Matrix, w: int) -> Matrix:
    """Per-instrument windowed covariance of a and b (reads each column's own past -> no lookahead)."""
    t, n = _dims(a)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            wa, wb = _col_window(a, r, i, w), _col_window(b, r, i, w)
            if wa is None or wb is None:
                continue
            ma, mb = statistics.fmean(wa), statistics.fmean(wb)
            out[r][i] = sum((x - ma) * (y - mb) for x, y in zip(wa, wb)) / len(wa)
    return out


def ts_beta(a: Matrix, b: Matrix, w: int) -> Matrix:
    """Per-instrument windowed OLS slope of a regressed on b (cov(a,b)/var(b)). Zero-variance b ->
    undefined (None). Reads each column's own past only -> no lookahead."""
    t, n = _dims(a)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            wa, wb = _col_window(a, r, i, w), _col_window(b, r, i, w)
            if wa is None or wb is None:
                continue
            mb = statistics.fmean(wb)
            var_b = sum((y - mb) ** 2 for y in wb)
            if var_b <= _EPS:
                continue
            ma = statistics.fmean(wa)
            out[r][i] = sum((x - ma) * (y - mb) for x, y in zip(wa, wb)) / var_b
    return out


# ---- cross-sectional operators (read all instruments at the SAME period) --------------------
def _cs(m: Matrix, f) -> Matrix:
    t, n = _dims(m)
    out = _empty(t, n)
    for r in range(t):
        idx = [i for i in range(n) if m[r][i] is not None]
        if len(idx) < 2:
            continue
        vals = [m[r][i] for i in idx]
        mapped = f(vals)
        for i, v in zip(idx, mapped):
            out[r][i] = v
    return out


def cs_rank(m):
    def f(vals):
        order = sorted(range(len(vals)), key=lambda k: (vals[k], k))
        rank = [0.0] * len(vals)
        for pos, k in enumerate(order):
            rank[k] = pos / (len(vals) - 1)         # [0,1]
        return rank
    return _cs(m, f)


def cs_demean(m):
    return _cs(m, lambda vals: [v - statistics.fmean(vals) for v in vals])


def cs_zscore(m):
    def f(vals):
        mu, sd = statistics.fmean(vals), statistics.pstdev(vals)
        return [0.0 for _ in vals] if sd <= _EPS else [(v - mu) / sd for v in vals]
    return _cs(m, f)


def cs_scale(m):
    """Alpha101 'scale': normalize so sum|x| == 1 (dollar-neutral-ish weighting)."""
    def f(vals):
        s = sum(abs(v) for v in vals)
        return [0.0 for _ in vals] if s <= _EPS else [v / s for v in vals]
    return _cs(m, f)


def cs_winsorize(m, k: float = 3.0):
    """Robust k-MAD outlier clipping per cross-section: clip to median +/- k*1.4826*MAD so a single
    extreme name cannot dominate a downstream zscore/scale. Zero MAD -> unchanged (no fabricated band)."""
    def f(vals):
        med = statistics.median(vals)
        mad = statistics.median([abs(v - med) for v in vals])
        if mad <= _EPS:
            return list(vals)
        lo, hi = med - k * 1.4826 * mad, med + k * 1.4826 * mad
        return [min(max(v, lo), hi) for v in vals]
    return _cs(m, f)


def cs_residualize(mx: Matrix, my: Matrix) -> Matrix:
    """Cross-sectional OLS residual of x on y at each period (the NEUTRALIZATION primitive): regress x
    on y across the names knowable at t and return x - (a + b*y). Neutralizes x against ANY computed
    signal (size = log(volume), a market proxy, another factor) with NO industry/membership data. A
    zero-variance regressor degenerates to demeaning x (residual against a constant). PIT-safe (same
    period only). Names lacking either input are dropped; < 2 survivors -> skipped."""
    t, n = _dims(mx)
    out = _empty(t, n)
    for r in range(t):
        idx = [i for i in range(n) if mx[r][i] is not None and my[r][i] is not None]
        if len(idx) < 2:
            continue
        xs = [mx[r][i] for i in idx]
        ys = [my[r][i] for i in idx]
        mxr, myr = statistics.fmean(xs), statistics.fmean(ys)
        var_y = sum((y - myr) ** 2 for y in ys)
        if var_y <= _EPS:
            for i, x in zip(idx, xs):
                out[r][i] = x - mxr                       # residual against a constant == demean
            continue
        beta = sum((x - mxr) * (y - myr) for x, y in zip(xs, ys)) / var_y
        a = mxr - beta * myr
        for i, x, y in zip(idx, xs, ys):
            out[r][i] = x - (a + beta * y)
    return out


# ---- elementwise operators ------------------------------------------------------------------
def _ew2(a: Matrix, b: Matrix, f) -> Matrix:
    t, n = _dims(a)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            x, y = a[r][i], b[r][i]
            if x is not None and y is not None:
                out[r][i] = f(x, y)
    return out


def _ew1(a: Matrix, f) -> Matrix:
    t, n = _dims(a)
    out = _empty(t, n)
    for r in range(t):
        for i in range(n):
            if a[r][i] is not None:
                out[r][i] = f(a[r][i])
    return out


def ew_add(a, b): return _ew2(a, b, lambda x, y: x + y)
def ew_sub(a, b): return _ew2(a, b, lambda x, y: x - y)
def ew_mul(a, b): return _ew2(a, b, lambda x, y: x * y)
def ew_div(a, b): return _ew2(a, b, lambda x, y: x / y if abs(y) > _EPS else None)
def ew_neg(a): return _ew1(a, lambda x: -x)
def ew_abs(a): return _ew1(a, abs)
def ew_sign(a): return _ew1(a, lambda x: (x > 0) - (x < 0) * 1.0)
def ew_log(a): return _ew1(a, lambda x: math.log(x) if x > _EPS else None)
def ew_sqrt(a): return _ew1(a, lambda x: (1.0 if x >= 0 else -1.0) * math.sqrt(abs(x)))


def signed_power(a, exp):
    return _ew1(a, lambda x: (1.0 if x >= 0 else -1.0) * (abs(x) ** exp))


# ---- expression evaluation ------------------------------------------------------------------
_TS_WINDOW = {"ts_delay": ts_delay, "ts_delta": ts_delta, "ts_mean": ts_mean, "ts_sum": ts_sum,
              "ts_std": ts_std, "ts_min": ts_min, "ts_max": ts_max, "ts_rank": ts_rank,
              "decay_linear": decay_linear, "ts_argmax": ts_argmax, "ts_argmin": ts_argmin}
_CS = {"cs_rank": cs_rank, "cs_demean": cs_demean, "cs_zscore": cs_zscore, "cs_scale": cs_scale,
       "cs_winsorize": cs_winsorize}
_CS2 = {"cs_residualize": cs_residualize}                 # binary cross-sectional (neutralization)
_EW1 = {"neg": ew_neg, "abs": ew_abs, "sign": ew_sign, "log": ew_log, "sqrt": ew_sqrt}
_EW2 = {"add": ew_add, "sub": ew_sub, "mul": ew_mul, "div": ew_div}
_TS_PAIR = {"ts_corr": ts_corr, "ts_cov": ts_cov, "ts_beta": ts_beta}   # windowed pairwise (a, b, w)
_COMMUTATIVE = {"add", "mul"}


def evaluate(expr, panel: FieldPanel) -> Matrix:
    """Evaluate a nested-tuple alpha expression over the FieldPanel -> (T x N) matrix."""
    head = expr[0]
    if head == "field":
        return panel.matrices[expr[1]]
    if head == "const":
        if isinstance(expr[1], bool):                    # fail closed: a bool literal is a generator bug,
            raise ValueError(f"const value must be a number, not bool: {expr[1]!r}")  # don't coerce True->1.0
        t, n = panel.shape
        return [[float(expr[1])] * n for _ in range(t)]
    if head in _TS_WINDOW:
        return _TS_WINDOW[head](evaluate(expr[1], panel), int(expr[2]))
    if head in _CS:
        return _CS[head](evaluate(expr[1], panel))
    if head in _CS2:
        return _CS2[head](evaluate(expr[1], panel), evaluate(expr[2], panel))
    if head in _EW1:
        return _EW1[head](evaluate(expr[1], panel))
    if head in _EW2:
        return _EW2[head](evaluate(expr[1], panel), evaluate(expr[2], panel))
    if head in _TS_PAIR:
        return _TS_PAIR[head](evaluate(expr[1], panel), evaluate(expr[2], panel), int(expr[3]))
    if head == "signed_power":
        if isinstance(expr[2], bool):
            raise ValueError(f"signed_power exponent must be a number, not bool: {expr[2]!r}")
        return signed_power(evaluate(expr[1], panel), float(expr[2]))
    raise ValueError(f"unknown alpha operator: {head!r}")


def serialize(expr) -> str:
    """Injective S-expression string (for the canonical id)."""
    if expr[0] == "field":
        return f"(field {expr[1]})"
    if expr[0] == "const":
        return f"(const {float(expr[1])!r})"
    parts = [expr[0]]
    for a in expr[1:]:
        parts.append(serialize(a) if isinstance(a, tuple) else repr(a))
    return "(" + " ".join(parts) + ")"


def canonicalize(expr):
    """Sort children of commutative ops so equivalent alphas share one id (dedup the search)."""
    if expr[0] in ("field", "const"):
        return expr
    if expr[0] in _EW2 and expr[0] in _COMMUTATIVE:
        a, b = canonicalize(expr[1]), canonicalize(expr[2])
        return (expr[0],) + tuple(sorted((a, b), key=serialize))
    return (expr[0],) + tuple(canonicalize(a) if isinstance(a, tuple) else a for a in expr[1:])


def alpha_id(expr) -> str:
    return "ax_" + hashlib.sha256(serialize(canonicalize(expr)).encode("utf-8")).hexdigest()[:16]


def panel_to_periods(alpha: Matrix, panel: FieldPanel, *, fwd_horizon: int = 1,
                     price_field: str = "close") -> list:
    """Each period's cross-section of alpha values -> a PanelPeriod, with the realized forward return
    (price[t+h]/price[t]-1) as the label. Instruments lacking a finite alpha or a valid forward price
    are dropped; periods with < 2 survivors are skipped. Same shape the backtest/evaluator consume."""
    px = panel.matrices[price_field]
    t, n = _dims(alpha)
    periods = []
    for r in range(t - fwd_horizon):
        rf = r + fwd_horizon
        insts, sigs, fwds = [], [], []
        for i in range(n):
            a = alpha[r][i]
            p0, p1 = px[r][i], px[rf][i]
            if (a is None or not math.isfinite(a) or p0 is None or p1 is None or p0 <= 0):
                continue
            insts.append(panel.instruments[i])
            sigs.append(float(a))
            fwds.append(p1 / p0 - 1.0)
        if len(insts) >= 2:
            periods.append(PanelPeriod(tuple(insts), tuple(sigs), tuple(fwds)))
    return periods
