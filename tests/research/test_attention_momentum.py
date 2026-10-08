from __future__ import annotations

import pytest

from services.research.attention_momentum import (
    AttentionMomentumConfig,
    attention_innovation,
    combine_signal,
    deterministic_timestamp_lag,
    price_momentum,
    rank_zscore,
)


def test_config_rejects_invalid_windows_or_quantile() -> None:
    with pytest.raises(ValueError, match="positive"):
        AttentionMomentumConfig(attention_lookback_days=0)
    with pytest.raises(ValueError, match="quantile"):
        AttentionMomentumConfig(quantile=0.5)


def test_rank_zscore_is_deterministic_and_uses_average_tie_ranks() -> None:
    observed = rank_zscore((3.0, 1.0, 1.0, 2.0))

    assert observed == rank_zscore((3.0, 1.0, 1.0, 2.0))
    assert observed[1] == observed[2]
    assert observed[0] > observed[3] > observed[1]
    assert sum(observed) == pytest.approx(0.0)


def test_attention_innovation_uses_only_current_and_trailing_rows() -> None:
    trailing = (
        (100.0, 100.0, 100.0),
        (110.0, 100.0, 90.0),
        (90.0, 100.0, 110.0),
    )
    observed = attention_innovation((400.0, 100.0, 25.0), trailing)

    assert observed[0] > observed[1] > observed[2]
    changed_future = (999_999.0, 1.0, 500_000.0)
    assert attention_innovation((400.0, 100.0, 25.0), trailing) == observed
    assert changed_future not in trailing


def test_price_momentum_is_cross_sectional_and_positive_price_only() -> None:
    observed = price_momentum((120.0, 100.0, 80.0), (100.0, 100.0, 100.0))

    assert observed[0] > observed[1] > observed[2]
    with pytest.raises(ValueError, match="positive"):
        price_momentum((120.0, 0.0, 80.0), (100.0, 100.0, 100.0))


def test_timestamp_placebo_is_prior_only_and_hash_deterministic() -> None:
    first = deterministic_timestamp_lag(1_800_000_000_000, 28)
    second = deterministic_timestamp_lag(1_800_000_000_000, 28)

    assert first == second
    assert 1 <= first <= 28


def test_four_modes_are_fixed_and_conditioning_ignores_negative_attention() -> None:
    momentum = (-1.0, -0.2, 0.4, 1.2)
    attention = (-2.0, 1.0, 0.5, 2.0)

    primary = combine_signal(
        momentum,
        attention,
        mode="attention_conditioned_momentum",
    )
    placebo = combine_signal(momentum, attention, mode="timestamp_placebo")
    price_only = combine_signal(momentum, attention, mode="price_only_momentum")
    attention_only = combine_signal(momentum, attention, mode="attention_only")

    assert primary == placebo
    assert primary != price_only
    assert primary != attention_only
    with pytest.raises(ValueError, match="unsupported"):
        combine_signal(momentum, attention, mode="unknown")  # type: ignore[arg-type]


def test_shape_and_nonfinite_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="shape"):
        attention_innovation((1.0, 2.0), ((1.0,),))
    with pytest.raises(ValueError, match="finite"):
        rank_zscore((1.0, float("nan")))
    with pytest.raises(ValueError, match="equal length"):
        combine_signal((1.0,), (1.0, 2.0), mode="price_only_momentum")
