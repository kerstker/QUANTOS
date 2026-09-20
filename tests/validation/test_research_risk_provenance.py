"""``REAL_RESEARCH_RISK_FULL`` must be earned by real stress-replay evidence.

Three confirmed defects are locked here:

(C) the strict dry-run seam stamped ``REAL_RESEARCH_RISK_FULL`` on any nine-engine
    run, even when the stress leg had fallen back to the honestly-labeled
    synthetic multipliers (the over-claim lived at the aggregate layer, not in
    ``StressTestEngine``);
(D) ``_validate_full_inputs`` never compared the bundle's declared PIT axis with
    the axis actually handed to the engines, so a run without a replay-capable
    timestamp axis (or with an unaligned / non-monotonic one) still reached the
    full label;
(E) ``full_research_risk_approved`` never inspected ``reports``, so a stored
    nine-engine result was admitted as full evidence with no stress report at all.
"""

from __future__ import annotations

import pytest

from services.risk.research_risk.domain import risk_modules
from services.risk.research_risk.dry_run_guard import (
    DryRunResearchRiskInputs,
    evaluate_dry_run_risk,
    full_research_risk_approved,
)
from services.validation.research_risk_authority import FULL_RESEARCH_RISK_ENGINES


WEEK_MS = 7 * 86_400_000
BASE_TS = 1_600_000_000_000


def _benign(periods=60):
    """A calm, diffuse, shallow-drawdown book the nine engines all accept."""
    returns = [0.001 if index % 2 == 0 else -0.0005 for index in range(periods)]
    equity, value = [], 1.0
    for rate in returns:
        value *= 1.0 + rate
        equity.append(value)
    weights = {f"S{index}": (0.1 if index % 2 == 0 else -0.1) for index in range(10)}
    timestamps = [BASE_TS + index * WEEK_MS for index in range(periods)]
    return returns, weights, equity, timestamps


def _bundle(returns, weights, timestamps):
    return DryRunResearchRiskInputs(
        capacity_cny={name: 1.0e9 for name in weights},
        daily_volume_cny=1.0e9,
        total_aum=1.0e8,
        alpha_returns={
            "alpha_a": [float(value) for value in returns],
            "alpha_b": [
                0.0003 if index % 3 == 0 else (-0.0002 if index % 3 == 1 else 0.0001)
                for index in range(len(returns))
            ],
        },
        regime_labels=["normal"] * len(returns),
        alpha_metadata=[
            {
                "alpha_id": alpha_id,
                "age_days": age,
                "validation_confidence": 0.8,
                "paper_confidence": 0.8,
                "decay_flag": False,
                "overfit_flag": False,
                "leakage_flag": False,
            }
            for alpha_id, age in (("alpha_a", 10.0), ("alpha_b", 12.0))
        ],
        timestamps=timestamps,
    )


def _full_result(returns, weights, equity, timestamps, inputs):
    return evaluate_dry_run_risk(
        returns,
        weights,
        equity,
        timestamps=timestamps,
        full_inputs=inputs,
        require_full_inputs=True,
    )


def _pending_proof(result):
    assert result["status"] == "PENDING", result["reasons"]
    assert result["decision"] == "RISK_REJECTED"
    assert result["provenance"] == "REAL_RESEARCH_RISK_FULL_PENDING"
    assert result["gates_run"] == 0 and result["gates_passed"] == 0
    assert result["engines_run"] == {} and result["reports"] == {}
    assert full_research_risk_approved(result) is False


def test_real_replay_full_result_carries_stress_evidence():
    returns, weights, equity, timestamps = _benign()

    result = _full_result(
        returns,
        weights,
        equity,
        timestamps,
        _bundle(returns, weights, timestamps),
    )

    assert result["status"] == "APPROVED"
    assert result["provenance"] == "REAL_RESEARCH_RISK_FULL"
    assert result["reports"]["stress"]["provenance"] == "REAL_HISTORICAL_REPLAY"
    assert full_research_risk_approved(result) is True


def test_a_synthetic_stress_fallback_cannot_keep_the_full_label(monkeypatch):
    returns, weights, equity, timestamps = _benign()
    original = risk_modules.StressTestEngine.evaluate

    def synthetic_fallback(self, returns, config, timestamps=None):
        # the engine's own honest fallback label; the guard must not upgrade it
        return original(self, returns, config, None)

    monkeypatch.setattr(
        risk_modules.StressTestEngine, "evaluate", synthetic_fallback
    )

    result = _full_result(
        returns,
        weights,
        equity,
        timestamps,
        _bundle(returns, weights, timestamps),
    )

    _pending_proof(result)
    assert "stress_replay_required" in result["reasons"]


@pytest.mark.parametrize("axis", ["declared", "evaluated"])
def test_full_label_requires_both_timestamp_axes(axis):
    returns, weights, equity, timestamps = _benign()
    inputs = _bundle(returns, weights, timestamps)
    if axis == "declared":
        inputs = DryRunResearchRiskInputs(
            capacity_cny=inputs.capacity_cny,
            daily_volume_cny=inputs.daily_volume_cny,
            total_aum=inputs.total_aum,
            alpha_returns=inputs.alpha_returns,
            regime_labels=inputs.regime_labels,
            alpha_metadata=inputs.alpha_metadata,
        )
        evaluated = timestamps
    else:
        evaluated = None

    result = _full_result(returns, weights, equity, evaluated, inputs)

    _pending_proof(result)
    assert "stress_timestamps_missing" in result["reasons"]


def test_declared_and_evaluated_axes_must_describe_the_same_periods():
    returns, weights, equity, timestamps = _benign()
    shifted = [value + WEEK_MS for value in timestamps]

    result = _full_result(
        returns, weights, equity, timestamps, _bundle(returns, weights, shifted)
    )

    _pending_proof(result)
    assert "stress_timestamps_not_aligned" in result["reasons"]


@pytest.mark.parametrize("axis", ["declared", "evaluated"])
def test_non_monotonic_timestamp_axis_is_rejected(axis):
    returns, weights, equity, timestamps = _benign()
    scrambled = list(timestamps)
    scrambled[7], scrambled[8] = scrambled[8], scrambled[7]
    declared, evaluated = (scrambled, timestamps) if axis == "declared" else (
        timestamps, scrambled
    )

    result = _full_result(
        returns, weights, equity, evaluated, _bundle(returns, weights, declared)
    )

    _pending_proof(result)
    assert "stress_timestamps_not_strictly_increasing" in result["reasons"]


def _legacy_full_result(**overrides):
    engines = {name: True for name in FULL_RESEARCH_RISK_ENGINES}
    result = {
        "decision": "RISK_APPROVED",
        "status": "APPROVED",
        "provenance": "REAL_RESEARCH_RISK_FULL",
        "gates_run": len(FULL_RESEARCH_RISK_ENGINES),
        "gates_passed": len(FULL_RESEARCH_RISK_ENGINES),
        "engines_run": engines,
        "engines_skipped": {},
        "engines_pending": {},
    }
    result.update(overrides)
    return result


def test_full_approval_requires_the_stress_report_itself():
    assert full_research_risk_approved(_legacy_full_result()) is False
    assert full_research_risk_approved(_legacy_full_result(reports={})) is False
    assert full_research_risk_approved(
        _legacy_full_result(reports={"market": {"passed": True}})
    ) is False
    assert full_research_risk_approved(
        _legacy_full_result(
            reports={"stress": {"provenance": "SYNTHETIC_MULTIPLIER_UNCALIBRATED"}}
        )
    ) is False
    assert full_research_risk_approved(
        _legacy_full_result(reports={"stress": {"provenance": "INVALID_INPUT"}})
    ) is False
    # a book whose history does not cover a crisis legitimately skips it
    assert full_research_risk_approved(
        _legacy_full_result(
            reports={
                "stress": {"provenance": "REAL_HISTORICAL_REPLAY", "scenarios_skipped": 2}
            }
        )
    ) is True
