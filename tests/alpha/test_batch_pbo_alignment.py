"""TDD for the batch_pbo calendar-misalignment bug (2026-07-09, found while registering the real
carry_composite/crowd_contra alphas into AlphaCatalog for the first time — see tools/
register_alpha_catalog.py). Root cause: batch_pbo compared candidates' period_returns by LIST
POSITION (series[:min_len]), which silently compares different calendar windows when two real
candidates have different valid-period histories (the normal case — different factors drop
different rows depending on data availability, e.g. an OI-based factor's history starting ~1.5
years after a funding-based factor's). Reproduced empirically: a constructed pair of candidates
with disjoint-but-overlapping calendar windows gives PBO=1.0 under positional truncation and a much
lower value once aligned by PanelPeriod.event_time — this file locks that fix in."""
from __future__ import annotations

from services.alpha.factory import batch_pbo
from services.alpha.panel_backtest import PanelPeriod
from services.research.backtest_engine.costs import CostModel

_COST = CostModel(fee_bps=0.0, slippage_bps=0.0)   # isolate the alignment bug from cost effects


def _period(event_time, close_a, close_b, signal_sign):
    """Two-instrument period: signal favors instrument A when signal_sign>0, B otherwise; forward
    returns are just the two supplied closes minus 1 (kept simple/deterministic)."""
    insts = ("A", "B")
    signals = (1.0, -1.0) if signal_sign > 0 else (-1.0, 1.0)
    fwd = (close_a, close_b)
    return PanelPeriod(insts, signals, fwd, event_time=event_time)


def test_batch_pbo_excludes_the_non_overlapping_tail_that_carries_an_outlier():
    """Two candidates with DISJOINT-but-overlapping calendar windows; candidate 1 has an extra
    early, NON-overlapping tail containing one huge outlier, candidate 2 has no history at all
    over that tail. Within the true overlap, the two candidates' performance is mildly different
    but genuinely noisy/non-adversarial (no engineered anti-correlation, and NOT a perfect tie —
    perfectly-tied per-block sums hit a separate, independent quirk in pbo_cscv's max()-based
    tie-break that is out of scope for this fix and deliberately avoided here) -- isolating a
    single variable: does the outlier-containing non-overlapping tail leak into the PBO
    computation via positional misalignment (pre-fix) or get correctly excluded by calendar
    alignment (the fix)? Under the pre-fix positional truncation, that tail's outlier collides
    with block index 0 of candidate 2's real data and forces a spurious PBO=1.0."""
    n_common = 40
    # candidate 1 ("early"): 10 extra early periods (t=0..9, one huge outlier at t=5) PLUS the 40
    # common periods (t=10..49), a mild noisy-but-not-tied pattern.
    early_periods = [_period(t, 5.0 if t == 5 else 0.001, -0.1, signal_sign=1) for t in range(10)]
    common_periods_1 = [_period(t, 0.001 * ((t * 7) % 5), -0.0007 * ((t * 3) % 4), signal_sign=1)
                        for t in range(10, 10 + n_common)]
    cand1_periods = early_periods + common_periods_1

    # candidate 2 ("late"): no early history; a DIFFERENT (not identical, not engineered-opposite)
    # noisy pattern over the true overlap (t=10..49).
    cand2_periods = [_period(t, 0.0009 * ((t * 5) % 4), -0.0011 * ((t * 2) % 6), signal_sign=1)
                     for t in range(10, 10 + n_common)]

    candidates = [("early", cand1_periods), ("late", cand2_periods)]
    evidence = batch_pbo(candidates, cost=_COST, quantile=0.5, target_aum=1e7)
    assert evidence is not None
    # the whole point of the fix: the outlier that only exists in candidate 1's NON-overlapping
    # tail must not be allowed to dominate the comparison via positional misalignment.
    assert evidence.pbo < 1.0, f"expected a non-degenerate PBO (mild noisy overlap performance, no " \
                      f"engineered anti-correlation) once the outlier tail is excluded by " \
                      f"calendar alignment, got {evidence.pbo}"
    assert evidence.alignment.value == "EVENT_TIME_INTERSECTION"
    assert evidence.aligned_event_times == tuple(range(10, 10 + n_common))


def test_batch_pbo_falls_back_to_positional_when_event_time_absent():
    """Legacy callers that hand-build PanelPeriod without event_time (event_time=None, the dataclass
    default) must keep working exactly as before -- this is a backward-compatibility guarantee, not
    a new default philosophy. Mirrors tests/alpha/test_factory.py::_real_cands's construction style
    (no event_time passed)."""
    def _legacy_period(idx, n_inst=6):
        insts = tuple(f"i{k}" for k in range(n_inst))
        signals = tuple(float(k) for k in range(n_inst))
        fwd = tuple(0.001 * (k - n_inst / 2) for k in range(n_inst))
        return PanelPeriod(insts, signals, fwd)   # no event_time -> None

    candidates = [
        ("a", [_legacy_period(i) for i in range(20)]),
        ("b", [_legacy_period(i) for i in range(20)]),
    ]
    evidence = batch_pbo(candidates, cost=_COST, quantile=0.5, target_aum=1e7)
    assert evidence is not None and 0.0 <= evidence.pbo <= 1.0
    assert evidence.alignment.value == "POSITIONAL_LEGACY"


def test_batch_pbo_intersects_common_calendar_window_even_when_lengths_already_match():
    """Two candidates whose period COUNTS happen to be equal but whose event_times are actually
    disjoint (a pathological case naive positional-length-matching would treat as "already
    aligned"). The fix must align by the actual intersection (empty here -> None, fail-closed), not
    assume equal length implies equal calendar coverage."""
    def _period_at(t, n_inst=6):
        insts = tuple(f"i{k}" for k in range(n_inst))
        signals = tuple(float(k) for k in range(n_inst))
        fwd = tuple(0.001 * (k - n_inst / 2) for k in range(n_inst))
        return PanelPeriod(insts, signals, fwd, event_time=t)

    cand_a = [_period_at(t) for t in range(0, 20)]         # t = 0..19
    cand_b = [_period_at(t) for t in range(1000, 1020)]    # t = 1000..1019 (disjoint, same length!)
    evidence = batch_pbo([("a", cand_a), ("b", cand_b)], cost=_COST, quantile=0.5, target_aum=1e7)
    assert evidence is None   # zero real overlap -> honestly uncomputable, not a fabricated number


def test_batch_pbo_rejects_duplicate_event_times_instead_of_last_write_wins():
    candidate_a = [_period(t, 0.001, -0.001, 1) for t in range(8)]
    candidate_a[1] = _period(0, 0.001, -0.001, 1)
    candidate_b = [_period(t, 0.001, -0.001, 1) for t in range(8)]
    evidence = batch_pbo(
        [("a", candidate_a), ("b", candidate_b)],
        cost=_COST,
        quantile=0.5,
        target_aum=1e7,
    )
    assert evidence is None
