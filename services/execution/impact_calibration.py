"""Uncalibrated impact-sensitivity scenarios for execution diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
import math

from services.execution.impact import AlmgrenChrissImpact


PROVENANCE_UNCALIBRATED = "UNCALIBRATED_PRINCIPLED"


@dataclass(frozen=True)
class ImpactCalibrationScenario:
    name: str
    temp_coef: float
    perm_coef: float
    temp_exponent: float
    provenance: str
    note: str


@dataclass(frozen=True)
class ImpactCalibrationSet:
    provenance: str
    conservative: ImpactCalibrationScenario
    base: ImpactCalibrationScenario
    aggressive: ImpactCalibrationScenario
    source_note: str


def _finite(value: float, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def validate_scenario(scenario: ImpactCalibrationScenario) -> None:
    temp_coef = _finite(scenario.temp_coef, "temp_coef")
    perm_coef = _finite(scenario.perm_coef, "perm_coef")
    temp_exponent = _finite(scenario.temp_exponent, "temp_exponent")
    if temp_coef <= 0.0:
        raise ValueError("temp_coef must be positive")
    if perm_coef < 0.0:
        raise ValueError("perm_coef must be non-negative")
    if not (0.0 < temp_exponent <= 1.0):
        raise ValueError("temp_exponent must be in (0, 1]")
    if not scenario.name:
        raise ValueError("scenario name is required")
    if not scenario.provenance:
        raise ValueError("scenario provenance is required")


def validate_calibration(calibration: ImpactCalibrationSet) -> None:
    if not calibration.provenance:
        raise ValueError("calibration provenance is required")
    for scenario in (
        calibration.conservative,
        calibration.base,
        calibration.aggressive,
    ):
        validate_scenario(scenario)
    if not (
        calibration.conservative.temp_coef
        > calibration.base.temp_coef
        > calibration.aggressive.temp_coef
    ):
        raise ValueError(
            "conservative temp_coef must be greater than base and aggressive"
        )
    if not (
        calibration.conservative.perm_coef
        > calibration.base.perm_coef
        > calibration.aggressive.perm_coef
    ):
        raise ValueError(
            "conservative perm_coef must be greater than base and aggressive"
        )


def default_crypto_impact_calibration() -> ImpactCalibrationSet:
    provenance = PROVENANCE_UNCALIBRATED
    note = "Literature/default crypto perp sensitivity assumption; not venue calibrated."
    calibration = ImpactCalibrationSet(
        provenance=provenance,
        conservative=ImpactCalibrationScenario(
            name="conservative",
            temp_coef=20.0,
            perm_coef=10.0,
            temp_exponent=0.5,
            provenance=provenance,
            note="Higher impact sensitivity; lower deployable capacity.",
        ),
        base=ImpactCalibrationScenario(
            name="base",
            temp_coef=10.0,
            perm_coef=5.0,
            temp_exponent=0.5,
            provenance=provenance,
            note="Middle literature/default sensitivity assumption.",
        ),
        aggressive=ImpactCalibrationScenario(
            name="aggressive",
            temp_coef=5.0,
            perm_coef=2.5,
            temp_exponent=0.5,
            provenance=provenance,
            note="Lower impact sensitivity; higher deployable capacity.",
        ),
        source_note=note,
    )
    validate_calibration(calibration)
    return calibration


def impact_band_from_calibration(
    calibration: ImpactCalibrationSet,
) -> tuple[float, float]:
    validate_calibration(calibration)
    return (
        calibration.conservative.temp_coef,
        calibration.aggressive.temp_coef,
    )


def scenario_impact(
    scenario: ImpactCalibrationScenario,
) -> AlmgrenChrissImpact:
    validate_scenario(scenario)
    return AlmgrenChrissImpact(
        temp_coef=scenario.temp_coef,
        perm_coef=scenario.perm_coef,
        temp_exponent=scenario.temp_exponent,
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
