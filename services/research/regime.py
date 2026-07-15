"""Compatibility exports for canonical PIT-safe regime diagnostics."""

from services.validation.regime import (
    label_regimes_by_vol,
    regime_conditional_stats,
    regime_profitable_frac,
)

__all__ = (
    "label_regimes_by_vol",
    "regime_conditional_stats",
    "regime_profitable_frac",
)
