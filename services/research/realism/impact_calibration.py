"""Compatibility exports for execution-owned impact calibration.

This module formalizes literature/default sensitivity assumptions for the
existing Almgren-Chriss impact model. It remains uncalibrated and research-only.
"""

from services.execution.impact_calibration import (
    ImpactCalibrationScenario,
    ImpactCalibrationSet,
    PROVENANCE_UNCALIBRATED,
    default_crypto_impact_calibration,
    impact_band_from_calibration,
    scenario_impact,
    validate_calibration,
    validate_scenario,
)


__all__ = [
    "ImpactCalibrationScenario",
    "ImpactCalibrationSet",
    "PROVENANCE_UNCALIBRATED",
    "default_crypto_impact_calibration",
    "impact_band_from_calibration",
    "scenario_impact",
    "validate_calibration",
    "validate_scenario",
]
