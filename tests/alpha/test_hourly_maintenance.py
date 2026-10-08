from __future__ import annotations

from dataclasses import replace

import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.hourly_maintenance import (
    HourlyMaintenanceEvidence,
    HourlyMaintenancePolicy,
    MaintenanceAction,
    apply_hourly_maintenance,
    evaluate_hourly_maintenance,
)
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.maintenance import run_hourly_maintenance
from services.lifecycle import DeploymentState
from services.risk.pre_trade.kill_switch import KillSwitch, UnauthorizedReset


AGENT = Actor(actor_id="hourly-maintenance", kind=ActorKind.AGENT)
HUMAN = Actor(actor_id="operator", kind=ActorKind.HUMAN)
POLICY = HourlyMaintenancePolicy(
    version="hourly-maintenance.v1",
    min_forward_realizations=24,
    max_feature_drift_score=0.20,
    max_abs_return_drift_z=3.0,
    max_cost_ratio=1.50,
    max_reality_gap=0.25,
    max_consecutive_coverage_failures=3,
    retire_consecutive_failed_periods=24,
)


def _evidence(**changes):
    values = {
        "alpha_id": "alpha-hourly",
        "observed_at": 100,
        "forward_chain_valid": True,
        "forward_realizations": 48,
        "feature_drift_score": 0.05,
        "return_drift_z": 0.5,
        "expected_cost_bps": 5.0,
        "realized_cost_bps": 5.5,
        "reality_gap": 0.05,
        "consecutive_coverage_failures": 0,
        "consecutive_failed_periods": 0,
    }
    values.update(changes)
    return HourlyMaintenanceEvidence(**values)


def _manager(state=AlphaState.PAPER_TRADING):
    catalog = AlphaCatalog()
    catalog.register(
        AlphaDefinition(
            alpha_id="alpha-hourly",
            name="alpha-hourly",
            factor_ids=("factor-hourly",),
        )
    )
    catalog._set_state("alpha-hourly", state)
    return AlphaLifecycleManager(catalog, AlphaFactoryConfig()), catalog


def test_healthy_hourly_evidence_holds_without_transition():
    recommendation = evaluate_hourly_maintenance(_evidence(), POLICY)
    assert recommendation.action is MaintenanceAction.HOLD
    assert recommendation.reason_codes == ()


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"forward_chain_valid": None}, "MISSING_EVIDENCE"),
        ({"forward_chain_valid": False}, "FORWARD_CHAIN_INVALID"),
        ({"forward_realizations": 2}, "INSUFFICIENT_FORWARD_EVIDENCE"),
        ({"feature_drift_score": 0.30}, "FEATURE_DRIFT_BLOCK"),
        ({"return_drift_z": -4.0}, "RETURN_DRIFT_BLOCK"),
        ({"realized_cost_bps": 8.0}, "COST_INFLATION"),
        ({"reality_gap": 0.30}, "REALITY_GAP"),
        ({"consecutive_coverage_failures": 3}, "COVERAGE_FAILURES"),
    ),
)
def test_missing_drift_cost_and_coverage_failures_recommend_pause(changes, reason):
    recommendation = evaluate_hourly_maintenance(_evidence(**changes), POLICY)
    assert recommendation.action is MaintenanceAction.PAUSE
    assert reason in recommendation.reason_codes


def test_long_running_failure_recommends_retirement():
    recommendation = evaluate_hourly_maintenance(
        _evidence(consecutive_failed_periods=24), POLICY
    )
    assert recommendation.action is MaintenanceAction.RETIRE
    assert recommendation.reason_codes == ("PERSISTENT_FAILURE",)


def test_apply_pause_uses_audited_health_demotion_only():
    manager, catalog = _manager()
    recommendation = apply_hourly_maintenance(
        manager,
        "alpha-hourly",
        AGENT,
        evidence=_evidence(feature_drift_score=0.30),
        policy=POLICY,
        timestamp=101,
    )
    assert recommendation.action is MaintenanceAction.PAUSE
    assert catalog.deployment_state("alpha-hourly") is DeploymentState.PAUSED
    assert catalog.state("alpha-hourly") is AlphaState.PAUSED
    assert manager.audit_chain_valid()


def test_apply_retire_requires_prior_pause_and_uses_two_audited_demotions():
    manager, catalog = _manager()
    first = apply_hourly_maintenance(
        manager,
        "alpha-hourly",
        AGENT,
        evidence=_evidence(consecutive_failed_periods=24),
        policy=POLICY,
        timestamp=101,
    )
    assert first.action is MaintenanceAction.PAUSE
    assert catalog.deployment_state("alpha-hourly") is DeploymentState.PAUSED
    second = apply_hourly_maintenance(
        manager,
        "alpha-hourly",
        AGENT,
        evidence=replace(
            _evidence(consecutive_failed_periods=24), observed_at=102
        ),
        policy=POLICY,
        timestamp=102,
    )
    assert second.action is MaintenanceAction.RETIRE
    assert catalog.state("alpha-hourly") is AlphaState.RETIRED
    assert catalog.deployment_state("alpha-hourly") is DeploymentState.CLOSED
    assert manager.audit_chain_valid()


def test_malformed_evaluator_input_fails_closed_to_pause():
    manager, catalog = _manager()
    recommendation = apply_hourly_maintenance(
        manager,
        "alpha-hourly",
        AGENT,
        evidence=object(),
        policy=POLICY,
        timestamp=101,
    )
    assert recommendation.action is MaintenanceAction.PAUSE
    assert recommendation.reason_codes == ("EVALUATION_ERROR",)
    assert catalog.deployment_state("alpha-hourly") is DeploymentState.PAUSED


def test_scheduler_owned_alpha_missing_evidence_fails_closed_to_pause():
    manager, catalog = _manager()
    report = run_hourly_maintenance(
        manager,
        AGENT,
        alpha_ids=("alpha-hourly",),
        evidence_by_alpha={},
        timestamp=101,
        policy=POLICY,
    )
    assert report["alpha-hourly"]["action"] == "PAUSE"
    assert report["alpha-hourly"]["reasons"] == ["MISSING_EVIDENCE"]
    assert catalog.deployment_state("alpha-hourly") is DeploymentState.PAUSED


def test_automated_path_rejects_human_and_has_no_recovery_edge():
    manager, catalog = _manager()
    with pytest.raises(ValueError, match="non-human"):
        apply_hourly_maintenance(
            manager,
            "alpha-hourly",
            HUMAN,
            evidence=_evidence(),
            policy=POLICY,
            timestamp=101,
        )
    apply_hourly_maintenance(
        manager,
        "alpha-hourly",
        AGENT,
        evidence=_evidence(feature_drift_score=0.30),
        policy=POLICY,
        timestamp=101,
    )
    hold = apply_hourly_maintenance(
        manager,
        "alpha-hourly",
        AGENT,
        evidence=replace(_evidence(), observed_at=102),
        policy=POLICY,
        timestamp=102,
    )
    assert hold.action is MaintenanceAction.HOLD
    assert catalog.deployment_state("alpha-hourly") is DeploymentState.PAUSED


def test_maintenance_agent_cannot_reset_kill_switch():
    switch = KillSwitch()
    switch.arm(AGENT, "hourly drift", at=100, incident_id="hourly-drift")
    with pytest.raises(UnauthorizedReset, match="HUMAN"):
        switch.reset(AGENT, None, at=101)
    assert switch.is_active()
