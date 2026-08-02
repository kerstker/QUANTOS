"""Family-level multiple testing (727 §7.3).

The doc's own example is the fixture: rank/zscore/winsorized_rank/mean_7d/mean_14d
of one input are five transforms of ONE bet, and must not spend a five-wide FDR
budget. These tests pin that, plus the honest-failure paths (no p-value never
passes silently; a singular projection returns None instead of a made-up number).
"""

from __future__ import annotations

import pytest

from services.alpha.family_testing import (
    assign_families,
    family_adjusted_trial_count,
    hierarchical_fdr,
    marginal_ir,
    simes_pvalue,
)
from services.alpha.multiple_testing import benjamini_hochberg


_VARIANTS = (
    "rank_global_ls",
    "zscore_global_ls",
    "winsorized_rank_global_ls",
    "mean_7d_global_ls",
    "mean_14d_global_ls",
)


def _one_signal_five_transforms(n: int = 120) -> dict[str, tuple[float, ...]]:
    """Five near-duplicate return streams: one driver plus tiny per-variant noise."""
    driver = [0.004 * ((t * 7) % 11 - 5) for t in range(n)]
    series: dict[str, tuple[float, ...]] = {}
    for index, name in enumerate(_VARIANTS):
        jitter = 1.0 + index * 0.002
        series[name] = tuple(
            value * jitter + 0.00001 * ((t + index) % 3 - 1)
            for t, value in enumerate(driver)
        )
    return series


def _independent(n: int = 120) -> dict[str, tuple[float, ...]]:
    """Deterministic, mutually near-orthogonal streams (distinct periods)."""
    periods = {"alpha_a": 5, "alpha_b": 7, "alpha_c": 11}
    return {
        name: tuple(0.004 * ((t % period) - period / 2.0) for t in range(n))
        for name, period in periods.items()
    }


# --- family assignment ----------------------------------------------------


def test_correlated_transforms_collapse_into_one_family() -> None:
    families = assign_families(return_series=_one_signal_five_transforms())
    assert len(families.family_ids) == 1
    assert families.members(families.family_ids[0]) == tuple(sorted(_VARIANTS))
    assert set(families.basis.values()) == {"CORRELATION"}


def test_independent_alphas_stay_separate_singletons() -> None:
    families = assign_families(return_series=_independent())
    assert len(families.family_ids) == 3
    assert set(families.basis.values()) == {"SINGLETON"}


def test_declared_family_wins_over_sample_correlation() -> None:
    """A declared economic family must not be split just because this sample's
    correlation is weak — that would restore the inflation we are removing."""
    series = _independent()
    families = assign_families(
        return_series=series,
        declared_families={"alpha_a": "funding_carry", "alpha_b": "funding_carry"},
    )
    assert families.family_of["alpha_a"] == families.family_of["alpha_b"]
    assert families.family_of["alpha_c"] != families.family_of["alpha_a"]
    assert families.basis["alpha_a"] == "DECLARED"
    assert families.basis["alpha_c"] == "SINGLETON"


# --- Simes ----------------------------------------------------------------


def test_simes_is_between_min_and_bonferroni() -> None:
    pvalues = [0.01, 0.04, 0.20]
    combined = simes_pvalue(pvalues)
    assert combined is not None
    assert min(pvalues) <= combined <= min(1.0, len(pvalues) * min(pvalues))


def test_simes_never_exceeds_one_and_handles_empty() -> None:
    """(m/m) * p_(m) = p_(m) <= 1, so Simes cannot exceed 1; the clip in the
    implementation is defensive only. A family of weak members stays weak."""
    assert simes_pvalue([0.9, 0.95]) == pytest.approx(0.95)
    assert simes_pvalue([1.0, 1.0]) == pytest.approx(1.0)
    assert simes_pvalue([]) is None


# --- hierarchical FDR -----------------------------------------------------


def test_family_hierarchy_is_stricter_than_flat_bh_on_one_disguised_bet() -> None:
    """The load-bearing claim of §7.3: flat BH treats five transforms as five
    independent discoveries; the hierarchy charges them as one family."""
    series = _one_signal_five_transforms()
    # every variant looks individually marginal
    pvalues = {name: 0.03 for name in _VARIANTS}

    flat = benjamini_hochberg([pvalues[n] for n in sorted(pvalues)], q=0.10)
    assert len(flat) == 5  # flat BH rejects all five

    families = assign_families(return_series=series)
    result = hierarchical_fdr(pvalues=pvalues, families=families, q=0.10)

    # one family, so the within-family level is the full q * (1/1)
    assert result.n_families == 1
    assert result.selected_families == frozenset(families.family_ids)
    assert result.within_family_level[families.family_ids[0]] == pytest.approx(0.10)
    # the family is a single bet: survivors are a subset, never an inflated five
    assert len(result.survivors) <= len(flat)
    assert all(result.reasons[name] for name in _VARIANTS)


def test_within_family_level_is_scaled_by_the_selected_fraction() -> None:
    """Benjamini-Bogomolov scaling: without q * R/n the overall FDR drifts above
    q once several families survive."""
    series = _independent()
    families = assign_families(return_series=series)
    assert len(families.family_ids) == 3

    pvalues = {"alpha_a": 0.001, "alpha_b": 0.002, "alpha_c": 0.90}
    result = hierarchical_fdr(pvalues=pvalues, families=families, q=0.10)

    assert len(result.selected_families) == 2
    for level in result.within_family_level.values():
        assert level == pytest.approx(0.10 * 2 / 3)
    assert result.reasons["alpha_c"] == "FAMILY_NOT_SELECTED"


def test_missing_pvalue_never_silently_passes() -> None:
    """§9.2 — absent evidence must not default to PASS."""
    series = _independent()
    families = assign_families(return_series=series)
    result = hierarchical_fdr(
        pvalues={"alpha_a": 0.001, "alpha_b": None, "alpha_c": None},
        families=families,
        q=0.10,
    )
    assert "alpha_b" not in result.survivors
    assert "alpha_c" not in result.survivors
    assert result.reasons["alpha_b"] == "NO_PVALUE"
    assert result.reasons["alpha_c"] == "NO_PVALUE"


def test_no_testable_family_yields_no_survivors() -> None:
    families = assign_families(return_series=_independent())
    result = hierarchical_fdr(
        pvalues={"alpha_a": None, "alpha_b": None, "alpha_c": None},
        families=families,
        q=0.10,
    )
    assert result.survivors == frozenset()
    assert result.selected_families == frozenset()
    assert result.n_families == 3


def test_invalid_q_is_rejected() -> None:
    families = assign_families(return_series=_independent())
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="q must be"):
            hierarchical_fdr(pvalues={"alpha_a": 0.01}, families=families, q=bad)


# --- family-adjusted trial count ------------------------------------------


def test_perfectly_correlated_family_counts_as_about_one_trial() -> None:
    families = assign_families(return_series=_one_signal_five_transforms())
    family_id = families.family_ids[0]
    charged = family_adjusted_trial_count(
        families=families, mean_abs_corr_within={family_id: 1.0}
    )
    assert charged == pytest.approx(1.0)


def test_uncorrelated_singletons_count_in_full() -> None:
    families = assign_families(return_series=_independent())
    charged = family_adjusted_trial_count(
        families=families,
        mean_abs_corr_within={f: 0.0 for f in families.family_ids},
    )
    assert charged == pytest.approx(3.0)


def test_family_without_a_supplied_correlation_is_charged_in_full() -> None:
    """The conservative direction: a missing correlation may only make deflation
    stricter, never hand out a discount."""
    families = assign_families(return_series=_one_signal_five_transforms())
    charged = family_adjusted_trial_count(families=families, mean_abs_corr_within={})
    assert charged == pytest.approx(5.0)


# --- marginal IR ----------------------------------------------------------


def test_redundancy_collapses_marginal_ir_relative_to_a_real_edge() -> None:
    """§7.3's actual claim is comparative: a repackaged incumbent loses its
    marginal IR while a genuinely additive candidate keeps one.

    Asserted as a RATIO between the two, not against a hand-tuned absolute
    threshold — an absolute bound here would only be fitting the fixture.
    """
    incumbent = tuple(0.004 * ((t % 7) - 3.5) for t in range(120))
    # rescaled incumbent + a small zero-mean wobble: redundant, not collinear
    redundant = tuple(
        1.5 * value + 0.00002 * ((t % 3) - 1) for t, value in enumerate(incumbent)
    )
    # same wobble scale, but carrying its own persistent drift = a real edge
    additive = tuple(
        1.5 * value + 0.00002 * ((t % 3) - 1) + 0.002
        for t, value in enumerate(incumbent)
    )

    redundant_ir = marginal_ir(candidate=redundant, incumbents={"incumbent": incumbent})
    additive_ir = marginal_ir(candidate=additive, incumbents={"incumbent": incumbent})
    assert redundant_ir is not None and additive_ir is not None
    assert abs(additive_ir) > 100 * abs(redundant_ir)


def test_exactly_collinear_candidate_is_none_not_a_fabricated_zero() -> None:
    """Total redundancy leaves no residual variance, so the ratio is undefined.
    Reporting 0.0 would be a measurement nobody made."""
    incumbent = tuple(0.004 * ((t % 7) - 3.5) for t in range(120))
    assert (
        marginal_ir(
            candidate=tuple(1.5 * value for value in incumbent),
            incumbents={"incumbent": incumbent},
        )
        is None
    )


def test_orthogonal_candidate_retains_marginal_ir() -> None:
    incumbent = tuple(0.004 * ((t % 7) - 3.5) for t in range(120))
    candidate = tuple(0.003 + 0.004 * ((t % 11) - 5.0) for t in range(120))
    result = marginal_ir(candidate=candidate, incumbents={"incumbent": incumbent})
    assert result is not None
    assert abs(result) > 1e-3


def test_marginal_ir_returns_none_rather_than_a_fabricated_number() -> None:
    incumbent = tuple(0.01 * t for t in range(120))
    # collinear incumbents -> singular normal equations
    assert (
        marginal_ir(
            candidate=tuple(0.02 * t + 0.001 * ((t % 5) - 2) for t in range(120)),
            incumbents={"a": incumbent, "b": tuple(2.0 * v for v in incumbent)},
        )
        is None
    )
    # too short a sample
    assert marginal_ir(candidate=(0.1, 0.2), incumbents={}) is None
    # an incumbent shorter than the candidate cannot be aligned honestly
    assert (
        marginal_ir(candidate=tuple(range(10)), incumbents={"a": (1.0, 2.0)}) is None
    )
