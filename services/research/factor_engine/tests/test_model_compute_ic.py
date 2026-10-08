"""Correctness tests for ``compute_ic`` truthiness handling (audit P2, §4.3).

``compute_ic`` must treat ``ic == 0.0`` (a legitimate, exactly-zero
correlation) as a VALID value, not as "no data" -> ``None``. A prior bug used
``if ic else None`` (a truthiness check) instead of ``if ic is None`` /
``is not None`` (an explicit None check), which silently coerced a real
zero-IC period into ``None`` for ``ic_std``/``icir`` -- because ``0.0`` is
falsy in Python. Downstream aggregation over many periods could then
mistake a genuine zero-IC period for a missing/invalid one.

These tests do NOT touch trading; they exercise the pure IC math only.
"""
import pytest

from services.research.factor_engine.domain.model import compute_ic


def _exact_zero_ic_inputs():
    """factor/forward-return pair constructed so Pearson IC is EXACTLY 0.0.

    factor_values alternate +1/-1 (mean 0, std 1); forward_returns are chosen
    so the odd-position sum equals the even-position sum, making the
    covariance (and hence ic) exactly zero, while both series still have
    nonzero variance and the individual per-observation product terms vary
    (so ic_std is a well-defined, nonzero standard deviation).
    """
    factor_values = [1, -1, 1, -1, 1, -1, 1, -1, 1, -1]
    forward_returns = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    return factor_values, forward_returns


# --------------------------------------------------------------------------- #
# ic == 0.0 must be treated as a valid value, not silently dropped to None
# --------------------------------------------------------------------------- #

def test_exact_zero_ic_mean_is_returned_as_zero_not_none():
    factor_values, forward_returns = _exact_zero_ic_inputs()
    out = compute_ic(factor_values, forward_returns)
    assert out["ic_mean"] is not None
    assert out["ic_mean"] == pytest.approx(0.0, abs=1e-12)


def test_exact_zero_ic_still_computes_ic_std():
    """ic_std must be computed even when the mean IC (ic) is exactly 0.0.

    Before the fix, ``ic_std = ... if ic else None`` treated ic == 0.0 as
    falsy and silently returned None even though the underlying per-period
    products have well-defined, nonzero dispersion.
    """
    factor_values, forward_returns = _exact_zero_ic_inputs()
    out = compute_ic(factor_values, forward_returns)
    assert out["ic_std"] is not None
    assert out["ic_std"] > 0.0


def test_exact_zero_ic_still_computes_icir_as_zero():
    """icir = ic / ic_std = 0.0 / positive = 0.0, a valid value, not None."""
    factor_values, forward_returns = _exact_zero_ic_inputs()
    out = compute_ic(factor_values, forward_returns)
    assert out["icir"] is not None
    assert out["icir"] == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# The genuinely-invalid path (insufficient sample size) must stay None
# --------------------------------------------------------------------------- #

def test_insufficient_sample_still_returns_all_none():
    # n < 10 is the documented minimum; must short-circuit to all-None,
    # regardless of the ic==0.0 fix.
    factor_values = [1.0, 2.0, 3.0, 4.0, 5.0]
    forward_returns = [5.0, 4.0, 3.0, 2.0, 1.0]
    out = compute_ic(factor_values, forward_returns)
    assert out == {"ic_mean": None, "rank_ic": None, "icir": None, "ic_std": None}


def test_degenerate_zero_variance_factor_still_returns_none_ic():
    # std_f == 0 (constant factor) -> ic must stay None (no correlation is
    # even definable), independent of the ic==0.0 fix.
    factor_values = [3.0] * 10
    forward_returns = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    out = compute_ic(factor_values, forward_returns)
    assert out["ic_mean"] is None
    assert out["ic_std"] is None
    assert out["icir"] is None


# --------------------------------------------------------------------------- #
# Regression: the ordinary nonzero-IC path must be unaffected by the fix
# --------------------------------------------------------------------------- #

def test_nonzero_ic_still_computes_ic_std_and_icir():
    factor_values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    forward_returns = [1.1, 2.0, 2.9, 4.2, 4.8, 6.1, 6.9, 8.3, 8.8, 10.2]
    out = compute_ic(factor_values, forward_returns)
    assert out["ic_mean"] is not None and out["ic_mean"] > 0.9
    assert out["ic_std"] is not None
    assert out["icir"] is not None
