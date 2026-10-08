"""Compatibility exports for canonical multiple-testing statistics."""

from services.validation.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    pbo_cscv,
)

__all__ = (
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "min_track_record_length",
    "pbo_cscv",
)
