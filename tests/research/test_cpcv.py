"""CPCV (Combinatorial Purged Cross-Validation, Lopez de Prado) — the missing piece the audit flagged:
PBO gives ONE overfit-probability scalar, but there was no DISTRIBUTION of out-of-sample outcomes.
CPCV selects the best candidate on each combinatorial TRAIN set (purged+embargoed around the held-out
groups) and scores it on the TEST groups, over all C(N,k) splits -> a distribution of OOS Sharpes that
exposes selection-overfit. Deterministic, fail-closed."""
import math

from services.research.validation import cpcv


def _good_alpha(T):
    # consistently positive, low-noise -> high, stable Sharpe on any block
    return [0.010 + 0.001 * ((i % 3) - 1) for i in range(T)]


def _noise(T, seed):
    # zero-mean deterministic alternation -> ~0 Sharpe; "luck" differs by seed
    return [0.02 * (1 if (i + seed) % 2 == 0 else -1) for i in range(T)]


def test_selection_overfit_distribution_separates_good_from_noise():
    T = 60
    cands = {"good": _good_alpha(T), **{f"noise{j}": _noise(T, j) for j in range(5)}}
    s = cpcv.cpcv_summary(cands, n_groups=6, k_test=2)
    assert s is not None
    assert s["n_splits"] == 15                          # C(6,2)
    # the genuinely-good alpha is selected on most train sets and its OOS is positive
    assert s["frac_positive"] > 0.8
    assert s["mean_oos_sharpe"] > 0.0
    assert "good" in s["selected_counts"]


def test_pure_noise_set_has_no_oos_edge():
    T = 60
    cands = {f"noise{j}": _noise(T, j) for j in range(6)}
    s = cpcv.cpcv_summary(cands, n_groups=6, k_test=2)
    assert s is not None
    # selecting the in-sample-luckiest pure-noise alpha does NOT generalize -> ~half positive at best
    assert s["frac_positive"] <= 0.7
    assert abs(s["mean_oos_sharpe"]) < 1.0


def test_single_candidate_is_its_own_oos_distribution():
    T = 48
    s = cpcv.cpcv_summary({"only": _good_alpha(T)}, n_groups=6, k_test=2)
    assert s["n_splits"] == 15 and s["selected_counts"] == {"only": 15}
    assert s["frac_positive"] == 1.0                    # consistently positive on every test block


def test_embargo_purges_train_neighbours():
    # with embargo the train set shrinks; the call still succeeds and stays deterministic
    T = 60
    cands = {"good": _good_alpha(T), "n": _noise(T, 1)}
    a = cpcv.cpcv_summary(cands, n_groups=6, k_test=2, embargo=0)
    b = cpcv.cpcv_summary(cands, n_groups=6, k_test=2, embargo=2)
    assert a is not None and b is not None
    assert a == cpcv.cpcv_summary(cands, n_groups=6, k_test=2, embargo=0)   # deterministic


def test_constant_returns_summarized_with_nan_moments():
    # review fix: a zero-variance positive series scores +inf on every test block. The distribution is
    # degenerate but REAL (15 scorable splits) — it must be summarized (countable fields + NaN moments),
    # not collapsed to None, which would hide it (docstring: inf excluded from the moments ONLY).
    s = cpcv.cpcv_summary({"const": [0.01] * 60}, n_groups=6, k_test=2)
    assert s is not None
    assert s["n_splits"] == 15 and s["frac_positive"] == 1.0
    assert s["selected_counts"] == {"const": 15}
    assert math.isnan(s["mean_oos_sharpe"]) and math.isnan(s["min_oos_sharpe"])


def test_fail_closed_when_too_short():
    assert cpcv.cpcv_summary({"a": [0.1, 0.2, 0.3]}, n_groups=6, k_test=2) is None


def test_rejects_misaligned_or_bad_params():
    for bad in [
        lambda: cpcv.cpcv_summary({"a": [0.1] * 30, "b": [0.1] * 29}),    # misaligned
        lambda: cpcv.cpcv_summary({"a": [0.1] * 60}, n_groups=1, k_test=1),  # n_groups < 2
        lambda: cpcv.cpcv_summary({"a": [0.1] * 60}, n_groups=6, k_test=6),  # k_test >= n_groups
    ]:
        raised = False
        try:
            bad()
        except ValueError:
            raised = True
        assert raised


def test_no_rng_or_wallclock_import():
    import inspect
    src = inspect.getsource(cpcv)
    assert "import random" not in src and "import time" not in src and "Random" not in src
