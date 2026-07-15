import pytest

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import EVENT_RESEARCH_RISK_EVALUATED
from services.risk.research_risk.events import research_risk_evaluated_event


def _risk(decision: str = "RISK_APPROVED") -> dict:
    return {
        "decision": decision,
        "gates_passed": 4 if decision == "RISK_APPROVED" else 3,
        "gates_run": 4,
        "engines_run": {
            "market": decision == "RISK_APPROVED",
            "drawdown": True,
            "concentration": True,
            "stress": True,
        },
        "engines_skipped": {
            "liquidity": "NO_HONEST_INPUT_AT_DRY_RUN_SEAM",
            "capacity": "NO_HONEST_INPUT_AT_DRY_RUN_SEAM",
        },
        "reasons": [] if decision == "RISK_APPROVED" else ["market: not passed"],
        "provenance": "REAL_RESEARCH_RISK_SUBSET",
        "series": "vol_target",
        "config_provenance": "RISK_CONFIG_DEFAULTS_UNCALIBRATED_FOR_WEEKLY",
    }


@pytest.mark.parametrize(
    ("decision", "criticality"),
    (("RISK_APPROVED", "C2"), ("RISK_REJECTED", "C1")),
)
def test_research_risk_event_is_strict_and_binds_the_exact_run(decision, criticality):
    event = research_risk_evaluated_event(
        run_id="run-1",
        strategy_id="crowd_contra",
        run_repro_hash="a" * 64,
        timestamp_ms=1_234,
        risk=_risk(decision),
    )

    assert event.event_type == EVENT_RESEARCH_RISK_EVALUATED
    assert event.timestamp == 1_234_000
    assert event.domain == "risk"
    assert event.criticality == criticality
    assert event.payload["engines_skipped"] == ["capacity", "liquidity"]
    assert event.payload["run_repro_hash"] == "a" * 64
    assert CANONICAL_EVENT_SCHEMAS.validate(event) == event


def test_research_risk_event_rejects_inconsistent_gate_counts():
    risk = _risk()
    risk["gates_passed"] = 3
    with pytest.raises(ValueError, match="gates_passed"):
        research_risk_evaluated_event(
            run_id="run-1",
            strategy_id="crowd_contra",
            run_repro_hash="a" * 64,
            timestamp_ms=1_234,
            risk=risk,
        )

