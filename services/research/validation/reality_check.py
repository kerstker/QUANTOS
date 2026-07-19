"""Compatibility exports for the domain-neutral RC/SPA kernel."""

from services.validation.reality_check import (
    RealityCheckInputError,
    SplitMix64,
    block_length_grid_v1,
    compute_reality_check_and_spa,
    iter_stationary_bootstrap_indices,
)

__all__ = [
    "RealityCheckInputError",
    "SplitMix64",
    "block_length_grid_v1",
    "compute_reality_check_and_spa",
    "iter_stationary_bootstrap_indices",
]
