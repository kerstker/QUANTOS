"""Frozen contracts for the White RC / Hansen SPA statistical kernel."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantos.contracts.research import (
    BootstrapPrefixHash,
    MonteCarloTailResult,
    PeriodKey,
    RealityCheckResult,
    SpaResult,
    SpaVariantResult,
)


def _tail(replication_count: int = 9, exceedance_count: int = 2) -> MonteCarloTailResult:
    return MonteCarloTailResult(
        exceedance_count=exceedance_count,
        replication_count=replication_count,
        p_numerator=exceedance_count + 1,
        p_denominator=replication_count + 1,
        p_value=(exceedance_count + 1) / (replication_count + 1),
        conditional_variance_formula_id="BINOMIAL_CONDITIONAL_V1",
        conservative_radius="0x1.0000000000000p-3",
        ambiguous=False,
    )


def _prefix(replication_count: int = 9, *, block_length: int = 2) -> BootstrapPrefixHash:
    return BootstrapPrefixHash(
        algorithm_version="QUANTOS_STATIONARY_BOOTSTRAP_V1",
        block_length=block_length,
        replication_count=replication_count,
        index_stream_sha256="a" * 64,
    )


def _prefix_schedule() -> tuple[BootstrapPrefixHash, ...]:
    return (_prefix(2), _prefix(5), _prefix(9))


def _variants(*, centers: tuple[object, ...] = ("0x0.0p+0", "0x1.0000000000000p+0"),
              tail_counts: tuple[int, int, int] = (9, 9, 9)) -> tuple[SpaVariantResult, ...]:
    return tuple(
        SpaVariantResult(variant=variant, centers=centers, tail_result=_tail(count), decision="NON_REJECT")
        for variant, count in zip(("LOWER", "CONSISTENT", "UPPER"), tail_counts, strict=True)
    )


def _spa_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "algorithm_version": "HANSEN_SPA_V1",
        "objective_version": "MEAN_NET_RETURN_DIFFERENTIAL",
        "period_count": 20,
        "candidate_count": 2,
        "block_length": 2,
        "candidate_order": ("alpha-a", "alpha-b"),
        "observed_statistic": "0x1.0000000000000p+0",
        "gamma_zero": ("0x0.0p+0", "0x1.0000000000000p+0"),
        "degeneracy_floors": ("0x1.0000000000000p-10", "0x1.0000000000000p-10"),
        "long_run_variances": ("0x1.0000000000000p-2", "0x1.0000000000000p-2"),
        "thresholds": ("0x1.0000000000000p-3", "0x1.0000000000000p-3"),
        "variants": _variants(),
        "primary_variant": "CONSISTENT",
        "prefix_hashes": _prefix_schedule(),
        "run_status": "PUBLISHABLE",
        "reasons": (),
    }
    payload.update(overrides)
    return payload


def test_period_key_is_frozen_and_requires_positive_microseconds_and_labels() -> None:
    period = PeriodKey(event_time=1, frequency_id="WEEKLY", label_horizon="1w")
    with pytest.raises(ValidationError):
        PeriodKey(event_time=0, frequency_id="WEEKLY", label_horizon="1w")
    with pytest.raises(ValidationError):
        PeriodKey(event_time=1, frequency_id="", label_horizon="1w")
    with pytest.raises(ValidationError):
        period.event_time = 2  # type: ignore[misc]


def test_monte_carlo_tail_enforces_plus_one_arithmetic_and_canonical_hex() -> None:
    assert _tail().p_numerator == 3
    with pytest.raises(ValidationError):
        MonteCarloTailResult(
            exceedance_count=2, replication_count=9, p_numerator=2, p_denominator=10,
            p_value="0x1.3333333333333p-2", conditional_variance_formula_id="BINOMIAL_CONDITIONAL_V1",
            conservative_radius="0x1.0000000000000p-3", ambiguous=False,
        )


def test_monte_carlo_tail_permits_exceedance_count_equal_to_replication_count() -> None:
    tail = MonteCarloTailResult(
        exceedance_count=9, replication_count=9, p_numerator=10, p_denominator=10,
        p_value="0x1.0000000000000p+0", conditional_variance_formula_id="BINOMIAL_CONDITIONAL_V1",
        conservative_radius="0x1.0000000000000p-3", ambiguous=False,
    )
    assert tail.exceedance_count == tail.replication_count


def test_monte_carlo_tail_rejects_exceedance_count_above_replication_count() -> None:
    with pytest.raises(ValidationError):
        MonteCarloTailResult(
            exceedance_count=10, replication_count=9, p_numerator=11, p_denominator=10,
            p_value="0x1.199999999999ap+0", conditional_variance_formula_id="BINOMIAL_CONDITIONAL_V1",
            conservative_radius="0x1.0000000000000p-3", ambiguous=False,
        )
    with pytest.raises(ValidationError):
        MonteCarloTailResult(
            exceedance_count=2, replication_count=9, p_numerator=3, p_denominator=10,
            p_value="0x1.0p-2", conditional_variance_formula_id="BINOMIAL_CONDITIONAL_V1",
            conservative_radius="0x1.0000000000000p-3", ambiguous=False,
        )


def test_bootstrap_prefix_hash_requires_fixed_algorithm_and_lowercase_sha256() -> None:
    assert _prefix().block_length == 2
    with pytest.raises(ValidationError):
        BootstrapPrefixHash(
            algorithm_version="OTHER", block_length=2, replication_count=9, index_stream_sha256="A" * 64,
        )


def test_bootstrap_prefix_hash_explicitly_rejects_the_old_short_algorithm_name() -> None:
    with pytest.raises(ValidationError):
        BootstrapPrefixHash(
            algorithm_version="STATIONARY_BOOTSTRAP_V1", block_length=2, replication_count=9,
            index_stream_sha256="a" * 64,
        )


def test_bootstrap_prefix_hash_rejects_uppercase_hash_with_the_correct_algorithm() -> None:
    with pytest.raises(ValidationError):
        BootstrapPrefixHash(
            algorithm_version="QUANTOS_STATIONARY_BOOTSTRAP_V1", block_length=2, replication_count=9,
            index_stream_sha256="A" * 64,
        )


def test_reality_check_cross_field_lengths_sorted_candidates_and_prefix_identity() -> None:
    result = RealityCheckResult(
        algorithm_version="WHITE_REALITY_CHECK_V1",
        objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
        period_count=20,
        candidate_count=2,
        block_length=2,
        candidate_order=("alpha-a", "alpha-b"),
        column_means=("0x0.0p+0", "0x1.0000000000000p+0"),
        observed_statistic="0x1.0000000000000p+0",
        tail_result=_tail(),
        prefix_hashes=_prefix_schedule(),
        decision="NON_REJECT",
        run_status="PUBLISHABLE",
        reasons=(),
    )
    assert result.candidate_order == ("alpha-a", "alpha-b")
    assert tuple(item.replication_count for item in result.prefix_hashes) == (2, 5, 9)


def test_reality_check_rejects_unsorted_candidate_order() -> None:
    with pytest.raises(ValidationError):
        RealityCheckResult(
            algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
            period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-b", "alpha-a"),
            column_means=("0x0.0p+0", "0x1.0000000000000p+0"), observed_statistic="0x1.0000000000000p+0",
            tail_result=_tail(), prefix_hashes=_prefix_schedule(), decision="NON_REJECT",
            run_status="PUBLISHABLE", reasons=(),
        )


def test_reality_check_rejects_column_means_with_the_wrong_length() -> None:
    with pytest.raises(ValidationError):
        RealityCheckResult(
            algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
            period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-a", "alpha-b"),
            column_means=("0x0.0p+0",), observed_statistic="0x1.0000000000000p+0",
            tail_result=_tail(), prefix_hashes=_prefix_schedule(), decision="NON_REJECT",
            run_status="PUBLISHABLE", reasons=(),
        )


def test_reality_check_rejects_a_nonempty_requirement_violation() -> None:
    with pytest.raises(ValidationError):
        RealityCheckResult(
            algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
            period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-a", "alpha-b"),
            column_means=("0x0.0p+0", "0x1.0000000000000p+0"), observed_statistic="0x1.0000000000000p+0",
            tail_result=_tail(), prefix_hashes=(), decision="NON_REJECT", run_status="PUBLISHABLE", reasons=(),
        )


@pytest.mark.parametrize("kind", ["different_block", "duplicate_count"])
def test_reality_check_rejects_a_bad_prefix_schedule(kind: str) -> None:
    prefixes = (
        (_prefix(2, block_length=3), _prefix(9))
        if kind == "different_block"
        else (_prefix(5), _prefix(5), _prefix(9))
    )
    with pytest.raises(ValidationError):
        RealityCheckResult(
            algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
            period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-a", "alpha-b"),
            column_means=("0x0.0p+0", "0x1.0000000000000p+0"), observed_statistic="0x1.0000000000000p+0",
            tail_result=_tail(), prefix_hashes=prefixes, decision="NON_REJECT", run_status="PUBLISHABLE", reasons=(),
        )


def test_reality_check_rejects_a_terminal_prefix_that_does_not_match_tail_b() -> None:
    with pytest.raises(ValidationError):
        RealityCheckResult(
            algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
            period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-a", "alpha-b"),
            column_means=("0x0.0p+0", "0x1.0000000000000p+0"), observed_statistic="0x1.0000000000000p+0",
            tail_result=_tail(), prefix_hashes=(_prefix(2), _prefix(5), _prefix(8)), decision="NON_REJECT",
            run_status="PUBLISHABLE", reasons=(),
        )


def test_reality_check_rejects_descending_prefix_replication_counts() -> None:
    with pytest.raises(ValidationError):
        RealityCheckResult(
            algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
            period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-a", "alpha-b"),
            column_means=("0x0.0p+0", "0x1.0000000000000p+0"), observed_statistic="0x1.0000000000000p+0",
            tail_result=_tail(), prefix_hashes=(_prefix(5), _prefix(2), _prefix(9)), decision="NON_REJECT",
            run_status="PUBLISHABLE", reasons=(),
        )


def test_spa_accepts_a_complete_aligned_three_variant_schedule() -> None:
    assert SpaResult(**_spa_payload()).primary_variant == "CONSISTENT"


def test_spa_rejects_incomplete_variant_coverage() -> None:
    with pytest.raises(ValidationError):
        SpaResult(**_spa_payload(variants=_variants()[:2]))


def test_spa_rejects_a_nonconsistent_primary_literal() -> None:
    with pytest.raises(ValidationError):
        SpaResult(**_spa_payload(primary_variant="LOWER"))


def test_spa_rejects_variant_centers_with_the_wrong_length() -> None:
    with pytest.raises(ValidationError):
        SpaResult(**_spa_payload(variants=_variants(centers=("0x0.0p+0",))))


def test_spa_rejects_variants_with_unaligned_tail_replication_counts() -> None:
    with pytest.raises(ValidationError):
        SpaResult(**_spa_payload(variants=_variants(tail_counts=(9, 8, 9))))


def test_spa_rejects_a_terminal_prefix_not_matching_common_tail_b() -> None:
    with pytest.raises(ValidationError):
        SpaResult(**_spa_payload(prefix_hashes=(_prefix(2), _prefix(5), _prefix(8))))


@pytest.mark.parametrize(
    "model",
    [PeriodKey, MonteCarloTailResult, BootstrapPrefixHash, RealityCheckResult, SpaVariantResult, SpaResult],
)
def test_all_a1_contracts_are_frozen_and_forbid_extra_fields(model: type[object]) -> None:
    config = model.model_config  # type: ignore[attr-defined]
    assert config["frozen"] is True
    assert config["extra"] == "forbid"


def test_statistical_contracts_normalize_typed_negative_zero_to_canonical_hex() -> None:
    result = RealityCheckResult(
        algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
        period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-a", "alpha-b"),
        column_means=(-0.0, 1.0), observed_statistic=-0.0, tail_result=_tail(),
        prefix_hashes=_prefix_schedule(), decision="NON_REJECT", run_status="PUBLISHABLE", reasons=(),
    )
    assert result.column_means[0] == result.observed_statistic == "0x0.0p+0"


def test_reality_check_rejects_the_legacy_objective_version() -> None:
    with pytest.raises(ValidationError):
        RealityCheckResult(
            algorithm_version="WHITE_REALITY_CHECK_V1", objective_version="NET_EXCESS_RETURN_V1",
            period_count=20, candidate_count=2, block_length=2, candidate_order=("alpha-a", "alpha-b"),
            column_means=("0x0.0p+0", "0x1.0000000000000p+0"), observed_statistic="0x1.0000000000000p+0",
            tail_result=_tail(), prefix_hashes=_prefix_schedule(), decision="NON_REJECT", run_status="PUBLISHABLE",
        )


def test_spa_rejects_the_legacy_objective_version() -> None:
    with pytest.raises(ValidationError):
        SpaResult(**_spa_payload(objective_version="NET_EXCESS_RETURN_V1"))
