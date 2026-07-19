"""Phase 6 step 6 — correlation, complete-linkage clustering (de-dup), and
Gram-Schmidt orthogonalization (reward only incremental edge)."""
import math

import pytest

from services.alpha.cluster import cluster_alphas
from services.alpha.correlation import max_abs_corr_to_set, pairwise_correlation
from services.alpha.orthogonalize import orthogonalize


def _u():
    return tuple(math.sin(i * 0.3) for i in range(80))


def _v():
    return tuple(math.cos(i * 0.55) for i in range(80))


def _near(base, freq):
    return tuple(x + 0.01 * math.sin(i * freq) for i, x in enumerate(base))


# --- correlation -------------------------------------------------------------

def test_correlation_identifies_dup_and_anti():
    u = _u()
    corr = pairwise_correlation({"a": u, "b": u, "c": tuple(-x for x in u)}, min_overlap=10)
    assert corr[("a", "b")] == pytest.approx(1.0)
    assert corr[("a", "c")] == pytest.approx(-1.0)


def test_correlation_fails_closed_below_min_overlap():
    short = tuple(range(5))
    corr = pairwise_correlation({"a": short, "b": short}, min_overlap=10)
    assert corr[("a", "b")] is None


def test_max_abs_corr_to_set():
    u = _u()
    assert max_abs_corr_to_set("cand", u, {"x": u}, min_overlap=10) == pytest.approx(1.0)
    assert max_abs_corr_to_set("cand", u, {}, min_overlap=10) is None


# --- clustering --------------------------------------------------------------

def test_three_correlated_one_independent_form_two_clusters():
    u = _u()
    series = {"a1": u, "a2": _near(u, 1.7), "a3": _near(u, 1.3), "a4": _v()}
    clusters = cluster_alphas(series, rho_cut=0.7, min_overlap=10)
    assert len(clusters) == 2
    members = sorted(sorted(c.member_ids) for c in clusters)
    assert members == [["a1", "a2", "a3"], ["a4"]]


def test_cluster_id_is_deterministic_and_order_independent():
    u = _u()
    c1 = cluster_alphas({"a1": u, "a2": _near(u, 1.7)}, rho_cut=0.7, min_overlap=10)
    c2 = cluster_alphas({"a2": _near(u, 1.7), "a1": u}, rho_cut=0.7, min_overlap=10)
    assert c1[0].cluster_id == c2[0].cluster_id
    assert c1[0].member_ids == ("a1", "a2")


def test_representative_picks_highest_score():
    u = _u()
    clusters = cluster_alphas({"a1": u, "a2": _near(u, 1.7)}, rho_cut=0.7, min_overlap=10,
                              scores={"a1": 0.1, "a2": 0.9})
    assert clusters[0].representative_id == "a2"


# --- orthogonalization -------------------------------------------------------

def test_duplicate_has_zero_marginal_edge():
    drift = tuple(0.01 + 0.005 * math.sin(i * 0.9) for i in range(80))
    res = orthogonalize(drift, {"dup": drift}, min_overlap=10)
    assert res.ok and res.explained_r2 > 0.99
    assert abs(res.marginal_ir) < 0.01            # nothing incremental to pay for


def test_independent_alpha_keeps_its_edge():
    drift = tuple(0.01 + 0.005 * math.sin(i * 0.9) for i in range(80))
    standalone = orthogonalize(drift, {}, min_overlap=10)
    against_u = orthogonalize(drift, {"u": _u()}, min_overlap=10)   # ~orthogonal basis
    assert against_u.ok and against_u.explained_r2 < 0.3
    assert against_u.marginal_ir == pytest.approx(standalone.marginal_ir, rel=0.15)


def test_orthogonalize_fails_closed_below_min_overlap():
    short = tuple(range(20))
    res = orthogonalize(short, {"u": tuple(range(20))}, min_overlap=60)
    assert res.ok is False and res.marginal_ir is None


def test_periods_per_year_default_is_252_unchanged_for_existing_callers():
    drift = tuple(0.01 + 0.005 * math.sin(i * 0.9) for i in range(80))
    default = orthogonalize(drift, {}, min_overlap=10)
    explicit = orthogonalize(drift, {}, min_overlap=10, periods_per_year=252)
    assert default.marginal_ir == pytest.approx(explicit.marginal_ir, rel=1e-12)


def test_periods_per_year_scales_marginal_ir_by_sqrt_ratio_empty_basis():
    """2026-07-09: marginal_ir used to hardcode sqrt(252) regardless of the caller's actual
    period cadence — verified live-data bug (crowd_contra's persisted marginal_ir was inflated
    ~2.20x = sqrt(252/52) once AlphaEvaluator started using periods_per_year=52). This pins the
    fix for the empty-approved-set branch (the exact branch that bug hit — a first-ever
    registration has no approved set yet)."""
    drift = tuple(0.01 + 0.005 * math.sin(i * 0.9) for i in range(80))
    daily = orthogonalize(drift, {}, min_overlap=10, periods_per_year=252)
    weekly = orthogonalize(drift, {}, min_overlap=10, periods_per_year=52)
    assert weekly.marginal_ir != daily.marginal_ir
    assert weekly.marginal_ir == pytest.approx(daily.marginal_ir * math.sqrt(52 / 252), rel=1e-9)


def test_periods_per_year_scales_marginal_ir_by_sqrt_ratio_nonempty_basis():
    """Same fix, the OTHER branch (real approved-set / Gram-Schmidt residual path)."""
    drift = tuple(0.01 + 0.005 * math.sin(i * 0.9) for i in range(80))
    u = _u()
    daily = orthogonalize(drift, {"u": u}, min_overlap=10, periods_per_year=252)
    weekly = orthogonalize(drift, {"u": u}, min_overlap=10, periods_per_year=52)
    assert weekly.marginal_ir == pytest.approx(daily.marginal_ir * math.sqrt(52 / 252), rel=1e-9)
