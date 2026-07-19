"""Shared, deterministic data-quality primitives."""

from __future__ import annotations

from typing import Mapping

from quantos.contracts.data import DataQualityCheck


def check_volume_dispersion(
    volume_by_instrument: Mapping[str, float],
    max_ratio: float = 1e6,
) -> DataQualityCheck:
    """Withhold decisions when positive liquidity inputs are too dispersed."""
    values = [
        float(value)
        for value in volume_by_instrument.values()
        if isinstance(value, (int, float)) and float(value) > 0.0
    ]
    if len(values) < 2:
        return DataQualityCheck(
            name="volume_dispersion",
            passed=True,
            detail="insufficient cross-section",
        )
    ratio = max(values) / min(values)
    return DataQualityCheck(
        name="volume_dispersion",
        passed=ratio <= max_ratio,
        detail=f"max/min volume ratio {ratio:.3e} vs max {max_ratio:.0e}",
    )
