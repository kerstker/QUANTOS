"""Compatibility exports for the canonical validation performance metrics."""

from services.validation.performance import (
    generalization_gap,
    max_drawdown,
    metrics,
    returns_from_equity,
    sharpe,
    sortino,
)

__all__ = (
    "generalization_gap",
    "max_drawdown",
    "metrics",
    "returns_from_equity",
    "sharpe",
    "sortino",
)
