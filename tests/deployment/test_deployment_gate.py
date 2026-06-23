"""Phase 14 — deployment governance: research free, PAPER needs a verified human,
STAGING/LIVE structurally blocked at L2."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.config import SafetyConfig
from quantos.contracts.governance import ApprovalRecord, Environment
from services.deployment.gate import ReadinessLevel, evaluate_deployment

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="bot", kind=ActorKind.AGENT)


def _approval(subject="deploy", approver="alice", approved=True):
    return ApprovalRecord(approval_id="ap", subject_id=subject, approver=approver, approved=approved, timestamp=1)


def test_research_envs_are_free():
    for env in (Environment.DEV, Environment.TEST):
        assert evaluate_deployment(env).allowed is True


def test_paper_requires_verified_human_approval():
    assert evaluate_deployment(Environment.PAPER).allowed is False                 # no approval
    assert "NO_HUMAN_APPROVAL" in evaluate_deployment(Environment.PAPER).reasons
    # agent cannot approve a deployment
    d_agent = evaluate_deployment(Environment.PAPER, approval=_approval(approver="bot"), actor=AGENT, subject="deploy")
    assert not d_agent.allowed and "APPROVER_NOT_HUMAN" in d_agent.reasons
    # verified human approval -> allowed
    d_ok = evaluate_deployment(Environment.PAPER, approval=_approval(), actor=HUMAN, subject="deploy")
    assert d_ok.allowed is True


def test_live_blocked_at_l2_even_with_human_approval():
    d = evaluate_deployment(Environment.LIVE, approval=_approval(), actor=HUMAN, subject="deploy")
    assert not d.allowed
    assert "READINESS_BELOW_LIVE" in d.reasons and "LIVE_FLAGS_DISABLED" in d.reasons


def test_staging_blocked_at_l2():
    d = evaluate_deployment(Environment.STAGING, approval=_approval(), actor=HUMAN, subject="deploy")
    assert not d.allowed and "READINESS_BELOW_LIVE" in d.reasons


def test_empty_subject_does_not_bypass_approval_binding():
    # an empty subject must NOT skip the binding check (review P1)
    d = evaluate_deployment(Environment.PAPER, approval=_approval(subject="other"), actor=HUMAN, subject="")
    assert not d.allowed and "SUBJECT_REQUIRED" in d.reasons


def test_gate_agrees_with_deployment_record_on_live():
    # the gate blocks LIVE at L2; the contract refuses to even construct an
    # unapproved LIVE record -> two independent layers agree
    import pytest as _pytest
    from quantos.contracts.governance import DeploymentRecord
    assert not evaluate_deployment(Environment.LIVE, approval=_approval(), actor=HUMAN, subject="deploy").allowed
    with _pytest.raises(Exception):
        DeploymentRecord(deployment_id="d1", environment=Environment.LIVE, version="v1", approved=False)


def test_platform_pin_blocks_live_even_with_l4_request_and_flags():
    # audit P2: the L2 block must be a non-overridable PLATFORM pin, not an argument
    # default. A caller requesting L4 + all live flags must STILL be blocked while
    # the platform is pinned at L2 (READINESS_BELOW_LIVE survives the request).
    live_cfg = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True, allow_order_submission=True)
    d = evaluate_deployment(Environment.LIVE, safety_config=live_cfg, approval=_approval(), actor=HUMAN,
                            subject="deploy", readiness=ReadinessLevel.L4_LIVE_APPROVED)
    assert not d.allowed and "READINESS_BELOW_LIVE" in d.reasons


def test_gate_logic_allows_live_only_when_the_platform_itself_is_repinned(monkeypatch):
    # prove the wall is the platform pin (not a permanent bug): re-pin the platform
    # to L4 and the SAME request now passes — so LIVE is reachable ONLY by changing
    # the single non-overridable source, never by a caller-supplied argument.
    import services.deployment.gate as gate_mod
    monkeypatch.setattr(gate_mod, "PLATFORM_READINESS", ReadinessLevel.L4_LIVE_APPROVED)
    live_cfg = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True, allow_order_submission=True)
    d = gate_mod.evaluate_deployment(Environment.LIVE, safety_config=live_cfg, approval=_approval(),
                                     actor=HUMAN, subject="deploy", readiness=ReadinessLevel.L4_LIVE_APPROVED)
    assert d.allowed is True
    # and with the platform re-pinned high, research-safe flags still block live
    d2 = gate_mod.evaluate_deployment(Environment.LIVE, approval=_approval(), actor=HUMAN, subject="deploy",
                                      readiness=ReadinessLevel.L4_LIVE_APPROVED)
    assert not d2.allowed and "LIVE_FLAGS_DISABLED" in d2.reasons and "READINESS_BELOW_LIVE" not in d2.reasons
