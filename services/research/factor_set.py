"""Compatibility exports for alpha-owned synergistic factor-set selection."""

from services.alpha.factor_set import (
    incremental_ir,
    novelty_vs_basis,
    select_synergistic_set,
)

__all__ = ("incremental_ir", "novelty_vs_basis", "select_synergistic_set")
