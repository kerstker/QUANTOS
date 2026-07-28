import pytest

from services.portfolio.capital_allocator import CapitalAllocatorConfig, allocate_capital
from services.portfolio.correlation_drift import BLOCK, CorrelationDriftPolicy


BASE_A = (1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0)
BASE_B = (1.0, 1.0, -1.0, -1.0, 1.0, 1.0, -1.0, -1.0)
RECENT = (1.0, -1.0, 1.0, -1.0)


def _returns(pattern):
    return tuple(0.01 + 0.001 * value for value in pattern)


def _policy(**overrides):
    values = {
        "baseline_window": 8,
        "recent_window": 4,
        "warn_correlation": 0.50,
        "block_correlation": 0.80,
        "warn_increase": 0.20,
        "block_increase": 0.50,
        "warn_gross_multiplier": 0.75,
        "block_gross_multiplier": 0.40,
    }
    values.update(overrides)
    return CorrelationDriftPolicy(**values)


def test_allocator_exposes_advisory_correlation_gross_recommendation() -> None:
    plan = allocate_capital(
        {
            "carry": _returns(BASE_A + RECENT),
            "crowd": _returns(BASE_B + RECENT),
        },
        config=CapitalAllocatorConfig(
            min_observations=12,
            max_weight=1.0,
            mean_shrink_z=0.0,
            correlation_drift_policy=_policy(),
        ),
    )

    assert plan.status == "ALLOCATED"
    assert sum(item.target_weight for item in plan.allocations) == pytest.approx(1.0)
    assert plan.correlation_gross_recommendation == 0.40
    assert plan.diagnostics["correlation_drift"]["status"] == BLOCK
    assert plan.diagnostics["correlation_drift"]["load_bearing"] is False


def test_allocator_default_behavior_has_no_correlation_overlay() -> None:
    plan = allocate_capital(
        {
            "carry": _returns(BASE_A + RECENT),
            "crowd": _returns(BASE_B + RECENT),
        },
        config=CapitalAllocatorConfig(
            min_observations=12,
            max_weight=1.0,
            mean_shrink_z=0.0,
        ),
    )

    assert plan.status == "ALLOCATED"
    assert plan.correlation_gross_recommendation == 1.0
    assert "correlation_drift" not in plan.diagnostics


def test_invalid_correlation_policy_blocks_allocator_fail_closed() -> None:
    plan = allocate_capital(
        {
            "carry": _returns(BASE_A + RECENT),
            "crowd": _returns(BASE_B + RECENT),
        },
        config=CapitalAllocatorConfig(
            min_observations=12,
            max_weight=1.0,
            mean_shrink_z=0.0,
            correlation_drift_policy=_policy(recent_window=1),
        ),
    )

    assert plan.status == "BLOCKED"
    assert plan.reasons == (
        "invalid_correlation_drift_policy:invalid_recent_window",
    )
