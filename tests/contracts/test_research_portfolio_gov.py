"""Phase 1 — Research (§6.3), Portfolio (§6.4), Governance (§6.7), and Event
envelope contract tests."""
import pytest
from pydantic import ValidationError

from quantos.contracts.research import (
    Alpha, AlphaState, BacktestResult, Experiment, ValidationResult, ValidationVerdict,
)
from quantos.contracts.portfolio import PortfolioSnapshot, Position, Signal
from quantos.contracts.governance import (
    ApprovalRecord, AuditEvent, DeploymentRecord, Environment, IncidentReport, IncidentSeverity,
)
from quantos.contracts.events import Event, EVENT_PRETRADE_REJECTED


# --- Research ----------------------------------------------------------------

def test_alpha_defaults_to_draft():
    assert Alpha(alpha_id="a1", name="A").state == AlphaState.DRAFT


def test_experiment_trial_count_at_least_one():
    with pytest.raises(ValidationError):
        Experiment(experiment_id="e1", trial_count=0)


def test_validation_promotable_logic():
    assert ValidationResult(subject_id="a1", verdict=ValidationVerdict.PASS).promotable
    assert ValidationResult(subject_id="a1", verdict=ValidationVerdict.WARN).promotable
    assert not ValidationResult(subject_id="a1", verdict=ValidationVerdict.FAIL).promotable
    assert not ValidationResult(
        subject_id="a1", verdict=ValidationVerdict.REQUIRES_HUMAN_REVIEW).promotable


def test_validation_pbo_bounded():
    with pytest.raises(ValidationError):
        ValidationResult(subject_id="a1", verdict=ValidationVerdict.PASS, pbo=1.5)


def test_backtest_result_roundtrip():
    r = BacktestResult(request_id="r1", sharpe=1.2, max_drawdown=0.1, turnover=2.0)
    assert BacktestResult.model_validate(r.model_dump()) == r


# --- Portfolio ---------------------------------------------------------------

def test_signal_confidence_bounded():
    with pytest.raises(ValidationError):
        Signal(signal_id="s", alpha_id="a", instrument_id="X", confidence=1.5)


def test_short_positions_allowed_but_leverage_nonneg():
    snap = PortfolioSnapshot(portfolio_id="p", as_of=1,
                             positions=(Position(instrument_id="X", quantity=-100),))
    assert snap.positions[0].quantity == -100
    with pytest.raises(ValidationError):
        PortfolioSnapshot(portfolio_id="p", as_of=1, leverage=-1.0)


# --- Governance --------------------------------------------------------------

def test_live_deployment_requires_approval():
    with pytest.raises(ValidationError):
        DeploymentRecord(deployment_id="d", environment=Environment.LIVE, version="v1")
    # explicitly approved LIVE is allowed (still gated elsewhere by human sign-off)
    ok = DeploymentRecord(deployment_id="d", environment=Environment.LIVE, version="v1", approved=True)
    assert ok.approved
    # non-live envs need no approval
    assert DeploymentRecord(deployment_id="d", environment=Environment.PAPER, version="v1")


def test_audit_event_hash_chain():
    e1 = AuditEvent(event_id="1", actor="alice", action="approve", timestamp=1)
    e2 = AuditEvent(event_id="2", actor="bob", action="deploy", timestamp=2, prev_hash=e1.entry_hash)
    assert e1.entry_hash != e2.entry_hash
    # deterministic
    assert e1.entry_hash == AuditEvent(event_id="1", actor="alice", action="approve", timestamp=1).entry_hash


def test_approval_requires_approver_identity():
    with pytest.raises(ValidationError):
        ApprovalRecord(approval_id="ap", subject_id="s", approver="", timestamp=1)


def test_incident_report_basic():
    inc = IncidentReport(incident_id="i1", severity=IncidentSeverity.HIGH, summary="x")
    assert inc.resolved is False


# --- Events ------------------------------------------------------------------

def test_event_envelope_roundtrip_and_hash():
    e = Event(event_id="e1", event_type=EVENT_PRETRADE_REJECTED, timestamp=1,
              source="pre-trade-risk", domain="trading", criticality="C0",
              payload={"reason": "KILL_SWITCH_ACTIVE"})
    assert Event.model_validate(e.model_dump()) == e
    assert len(e.reproducibility_hash) == 16


def test_event_requires_type_and_source():
    with pytest.raises(ValidationError):
        Event(event_id="e1", event_type="", timestamp=1, source="x")
