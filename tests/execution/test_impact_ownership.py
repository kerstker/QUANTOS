def test_execution_owns_impact_model_with_research_compatibility():
    from services.execution.impact import AlmgrenChrissImpact
    from services.research.realism.impact import (
        AlmgrenChrissImpact as ResearchImpact,
    )

    assert ResearchImpact is AlmgrenChrissImpact
    impact = AlmgrenChrissImpact(
        temp_coef=10.0, perm_coef=5.0, temp_exponent=0.5
    )
    assert impact.temporary_bps(1_000.0, 100_000.0) == 1.0
    assert impact.permanent_bps(1_000.0, 100_000.0) == 0.05


def test_execution_owns_calibration_with_research_compatibility():
    from services.execution.impact_calibration import (
        default_crypto_impact_calibration,
        scenario_impact,
    )
    from services.research.realism.impact_calibration import (
        default_crypto_impact_calibration as research_default,
        scenario_impact as research_scenario_impact,
    )

    assert research_default is default_crypto_impact_calibration
    assert research_scenario_impact is scenario_impact
    calibration = default_crypto_impact_calibration()
    assert calibration.provenance == "UNCALIBRATED_PRINCIPLED"
    assert scenario_impact(calibration.base).temp_coef == 10.0
