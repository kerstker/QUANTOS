from services.data.distribution_drift_calibration import (
    INSUFFICIENT,
    READY,
    DriftCalibrationPolicy,
    calibrate_distribution_drift,
)


def _series(offset: float = 0.0) -> list[float]:
    return [
        offset + ((index * 17) % 31) / 31.0 + (index % 5) * 0.001
        for index in range(320)
    ]


def test_calibration_is_deterministic_and_holds_out_current_window():
    values = {"a": _series(), "b": _series(0.5)}
    first = calibrate_distribution_drift(
        values,
        policy=DriftCalibrationPolicy(min_calibration_windows=10),
    )
    changed = {
        key: series[:-80] + [value + 10_000.0 for value in series[-80:]]
        for key, series in values.items()
    }
    second = calibrate_distribution_drift(
        changed,
        policy=DriftCalibrationPolicy(min_calibration_windows=10),
    )
    assert first.status == READY
    assert first == second
    assert first.warn_ks < first.block_ks
    assert first.calibration_id
    assert first.drift_policy().warn_ks == first.warn_ks


def test_calibration_identity_changes_with_precurrent_history():
    policy = DriftCalibrationPolicy(min_calibration_windows=8)
    first = calibrate_distribution_drift({"a": _series()}, policy=policy)
    changed = _series()
    changed[0] += 0.25
    second = calibrate_distribution_drift({"a": changed}, policy=policy)
    assert first.status == READY
    assert second.status == READY
    assert first.calibration_id != second.calibration_id


def test_calibration_fails_closed_when_history_is_insufficient():
    result = calibrate_distribution_drift(
        {"a": _series()[:120]},
        policy=DriftCalibrationPolicy(min_calibration_windows=10),
    )
    assert result.status == INSUFFICIENT
    assert result.warn_ks is None
    assert result.calibration_id is None


def test_invalid_values_and_policy_fail_closed():
    invalid_values = calibrate_distribution_drift(
        {"a": [0.0, float("nan")]},
    )
    invalid_policy = calibrate_distribution_drift(
        {"a": _series()},
        policy=DriftCalibrationPolicy(warn_quantile=0.99, block_quantile=0.95),
    )
    assert invalid_values.status == INSUFFICIENT
    assert invalid_policy.status == INSUFFICIENT
