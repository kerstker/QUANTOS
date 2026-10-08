import pytest

from services.research.realism.capacity_curve import capacity_band
from services.research.realism.impact import AlmgrenChrissImpact
from services.research.realism.impact_calibration import (
    ImpactCalibrationScenario,
    ImpactCalibrationSet,
    default_crypto_impact_calibration,
    impact_band_from_calibration,
    scenario_impact,
    validate_calibration,
)


def test_default_crypto_calibration_is_ordered_and_labeled_uncalibrated_principled():
    calibration = default_crypto_impact_calibration()

    assert calibration.provenance == "UNCALIBRATED_PRINCIPLED"
    assert calibration.conservative.name == "conservative"
    assert calibration.base.name == "base"
    assert calibration.aggressive.name == "aggressive"
    assert calibration.conservative.temp_coef > calibration.base.temp_coef > calibration.aggressive.temp_coef
    assert calibration.conservative.perm_coef > calibration.base.perm_coef > calibration.aggressive.perm_coef
    assert calibration.conservative.temp_exponent == pytest.approx(0.5)
    assert "not venue calibrated" in calibration.source_note.lower()


def test_scenario_impact_preserves_coefficients():
    scenario = ImpactCalibrationScenario(
        name="base",
        temp_coef=10.0,
        perm_coef=5.0,
        temp_exponent=0.55,
        provenance="UNCALIBRATED_PRINCIPLED",
        note="test scenario",
    )

    impact = scenario_impact(scenario)

    assert isinstance(impact, AlmgrenChrissImpact)
    assert impact.temp_coef == pytest.approx(10.0)
    assert impact.perm_coef == pytest.approx(5.0)
    assert impact.temp_exponent == pytest.approx(0.55)


def test_impact_band_matches_capacity_band_contract():
    calibration = default_crypto_impact_calibration()
    band = impact_band_from_calibration(calibration)

    assert band == (calibration.conservative.temp_coef, calibration.aggressive.temp_coef)

    capacity = capacity_band(
        gross_returns=[0.01, 0.02, -0.005, 0.015],
        turnover_by_period=[[(0.5, 1e6)]] * 4,
        aum_grid=[1e5, 1e6, 1e7, 1e8, 1e9],
        impact_coef_lo_hi=band,
        base_impact=scenario_impact(calibration.base),
        max_participation=0.10,
    )

    assert capacity["provenance"] == "UNCALIBRATED_PRINCIPLED"
    assert capacity["aum_low"] is None or capacity["aum_high"] is None or capacity["aum_low"] <= capacity["aum_high"]


def test_invalid_calibration_fails_closed():
    bad = ImpactCalibrationSet(
        provenance="UNCALIBRATED_PRINCIPLED",
        conservative=ImpactCalibrationScenario("conservative", 5.0, 2.5, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        base=ImpactCalibrationScenario("base", 10.0, 5.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        aggressive=ImpactCalibrationScenario("aggressive", 20.0, 10.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        source_note="bad ordering",
    )

    with pytest.raises(ValueError, match="conservative"):
        validate_calibration(bad)


@pytest.mark.parametrize(
    "scenario",
    [
        ImpactCalibrationScenario("bad", 0.0, 1.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        ImpactCalibrationScenario("bad", 1.0, -1.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        ImpactCalibrationScenario("bad", 1.0, 1.0, 0.0, "UNCALIBRATED_PRINCIPLED", "bad"),
        ImpactCalibrationScenario("bad", 1.0, 1.0, 1.5, "UNCALIBRATED_PRINCIPLED", "bad"),
    ],
)
def test_invalid_scenario_values_fail_closed(scenario):
    with pytest.raises(ValueError):
        scenario_impact(scenario)
