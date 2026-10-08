"""F4 — offline/online parity: pure comparators + the REAL two-path parity cases
(dry-run full-panel evaluation vs the live decision-row path) on canonical factors."""
import math

import pytest

from services.factor_garden import (
    CARRY_COMPOSITE_EXPRESSION,
    CARRY_QUANTILE_LIVE,
    CROWD_CONTRA_EXPRESSION,
    PARITY_INSUFFICIENT_OVERLAP,
    PARITY_MISMATCH,
    PARITY_OK,
    compare_panels,
    compare_series,
    legs,
)
from services.research import alpha_expression as ax
from services.research.alpha_expression import FieldPanel


def test_compare_series_identical_is_ok():
    s = {100: 1.5, 200: -0.25, 300: 3.0}
    r = compare_series(s, dict(s), factor_id="f")
    assert r.verdict == PARITY_OK
    assert r.compared_cells == 3 and r.mismatches == 0 and r.max_abs_diff == 0.0
    assert r.tolerance == 0.0 and r.detail == ()


def test_compare_series_tolerance():
    off = {1: 1.0, 2: 2.0}
    onl = {1: 1.0 + 1e-9, 2: 2.0 + 0.1}
    ok = compare_series(off, onl, factor_id="f", tolerance=1e-6)
    assert ok.verdict == PARITY_MISMATCH and ok.mismatches == 1
    assert ok.detail == (("2", 2.0, 2.1, pytest.approx(0.1)),)
    assert compare_series(off, onl, factor_id="f", tolerance=0.5).verdict == PARITY_OK


def test_compare_series_empty_intersection_fails_closed():
    r = compare_series({1: 1.0}, {2: 2.0}, factor_id="f")
    assert r.verdict == PARITY_INSUFFICIENT_OVERLAP
    assert r.compared_cells == 0 and r.mismatches == 0
    assert r.detail[0][0] == "no_common_keys"


def test_compare_series_nan_inf_none_semantics():
    r = compare_series(
        {1: math.nan, 2: math.inf, 3: None, 4: 1.0},
        {1: math.nan, 2: math.inf, 3: None, 4: 2.0},
        factor_id="f",
    )
    # NaN never equals NaN (mismatch); inf == inf is fine; None == None not compared;
    # 1.0 vs 2.0 mismatch.
    assert r.mismatches == 2 and r.verdict == PARITY_MISMATCH
    keys = [row[0] for row in r.detail]
    assert keys == ["1", "4"]                       # deterministic sorted-key order


def test_compare_panels_shape_mismatch_is_mismatch_not_raise():
    r = compare_panels([[1.0]], [[1.0, 2.0]], factor_id="f")
    assert r.verdict == PARITY_MISMATCH
    assert r.detail == (("shape", (1, 1), (1, 2)),)
    assert compare_panels([], [], factor_id="f").verdict == PARITY_INSUFFICIENT_OVERLAP


def test_compare_panels_equal_and_cell_diff():
    a = [[1.0, None], [2.0, 3.0]]
    assert compare_panels(a, [list(r) for r in a], factor_id="f").verdict == PARITY_OK
    b = [[1.0, None], [2.0, 3.5]]
    r = compare_panels(a, b, factor_id="f")
    assert r.verdict == PARITY_MISMATCH and r.mismatches == 1
    assert r.max_abs_diff == pytest.approx(0.5)


def test_comparator_input_validation():
    with pytest.raises(ValueError):
        compare_series({}, {}, factor_id="  ")
    with pytest.raises(ValueError):
        compare_series({}, {}, factor_id="f", tolerance=-1.0)
    with pytest.raises(ValueError):
        compare_panels([], [], factor_id="f", tolerance=math.inf)


# ---------------------------------------------------------------------------
# REAL parity cases: the live decision-row path (tools/live_track_advance) vs the
# dry-run offline path (full-panel evaluate + legs) under ONE row-selection policy.
# The real live function is reused as-is on a synthetic panel bundle; the offline
# path is the exact composition dry_run_crowd_contra_multi.py:139-157 runs
# (ax.evaluate over the full panel, then legs(sig[r], QUANTILE) per chosen row).
# Parity is judged on the SAME row the live path selected — the three production
# row-selection policies themselves are deliberately NOT unified (F4 design ruling).
# ---------------------------------------------------------------------------

def _synthetic_panel():
    """6 instruments x 20 periods, fully populated (close + global_ls + funding).
    20 periods so the carry composite's 14-day smoothing window is past warmup on the
    live path's 7-row lookback (crowd_contra needs only 1 period)."""
    instruments = ("A", "B", "C", "D", "E", "F")
    event_times = tuple(1000 + 100 * t for t in range(20))
    close, gls, fund = [], [], []
    for t in range(20):
        close.append([10.0 + t + i for i in range(6)])
        gls.append([0.80 + 0.05 * ((t + i) % 7) for i in range(6)])
        fund.append([0.0001 * ((t * (i + 2)) % 5) - 0.0002 for i in range(6)])
    return FieldPanel(instruments, event_times,
                      {"close": close, "global_ls": gls, "funding": fund})


def _live_decision(expression, panel):
    """The REAL live path, unmodified (tools/live_track_advance.py:660)."""
    from tools.live_track_advance import _latest_weights_and_prices_for

    weights, _prices, decided_ts, _ls, _ev = _latest_weights_and_prices_for(
        expression, (panel, panel.instruments))
    assert weights, "live path produced no book on a fully-populated panel"
    return weights, decided_ts


def _offline_weights_at_row(expression, panel, row):
    """The REAL dry-run composition (full-panel evaluate + canonical legs, live quantile)."""
    sig = ax.evaluate(expression, panel)
    return {panel.instruments[i]: w for i, w in legs(sig[row], CARRY_QUANTILE_LIVE).items()}


def test_real_parity_crowd_contra_live_vs_dry_run_same_row():
    panel = _synthetic_panel()
    online, decided_ts = _live_decision(CROWD_CONTRA_EXPRESSION, panel)
    row = panel.event_times.index(decided_ts)
    offline = _offline_weights_at_row(CROWD_CONTRA_EXPRESSION, panel, row)
    report = compare_series(offline, online, factor_id="crowd_contra", tolerance=0.0)
    assert report.verdict == PARITY_OK, report.detail
    assert report.compared_cells == len(online) and report.max_abs_diff == 0.0


def test_real_parity_carry_live_vs_dry_run_same_row():
    panel = _synthetic_panel()
    online, decided_ts = _live_decision(CARRY_COMPOSITE_EXPRESSION, panel)
    row = panel.event_times.index(decided_ts)
    offline = _offline_weights_at_row(CARRY_COMPOSITE_EXPRESSION, panel, row)
    report = compare_series(offline, online, factor_id="carry_composite", tolerance=0.0)
    assert report.verdict == PARITY_OK, report.detail
    assert report.compared_cells == len(online) and report.max_abs_diff == 0.0
