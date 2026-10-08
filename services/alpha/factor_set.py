"""Compatibility exports; implementation lives in quantos.analytics.factor_set."""

from quantos.analytics.factor_set import (
    incremental_ir,
    novelty_vs_basis,
    select_synergistic_set,
)

__all__ = ['incremental_ir', 'novelty_vs_basis', 'select_synergistic_set']
