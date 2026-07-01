"""Phase 8 — kill-switch: latch, any-actor arm, HUMAN-only incident-bound reset,
corrupt-state-fails-active."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.governance import ApprovalRecord
from services.risk.pre_trade.kill_switch import KillSwitch, UnauthorizedReset

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="bot", kind=ActorKind.AGENT)
SYSTEM = Actor(actor_id="sys", kind=ActorKind.SYSTEM)


def test_arm_latches_any_actor():
    ks = KillSwitch()
    assert not ks.is_active()
    ks.arm(AGENT, "anomaly", at=10)
    assert ks.is_active() and ks.state.tripped and ks.state.trip_count == 1


def test_latch_is_sticky_and_monotonic():
    ks = KillSwitch()
    ks.arm(AGENT, "a", at=1)
    ks.arm(SYSTEM, "b", at=2)
    assert ks.is_active() and ks.state.trip_count == 2     # re-arm never clears


def test_reset_is_human_only():
    ks = KillSwitch()
    ks.arm(AGENT, "halt", at=1)
    ok = ApprovalRecord(approval_id="ap", subject_id=ks.state.incident_id,
                        approver="alice", approved=True, timestamp=2)
    for bad_actor in (AGENT, SYSTEM):
        with pytest.raises(UnauthorizedReset):
            ks.reset(bad_actor, ok, at=3)
    with pytest.raises(UnauthorizedReset):
        ks.reset(HUMAN, None, at=3)                        # human but no token
    ks.reset(HUMAN, ok, at=4)
    assert not ks.is_active() and ks.state.reset_by == HUMAN


def test_reset_requires_incident_bound_matching_approval():
    ks = KillSwitch()
    ks.arm(AGENT, "halt", at=1)
    stale = ApprovalRecord(approval_id="ap", subject_id="other-incident",
                           approver="alice", approved=True, timestamp=2)
    with pytest.raises(UnauthorizedReset):
        ks.reset(HUMAN, stale, at=3)                       # approval for a different incident
    mismatch = ApprovalRecord(approval_id="ap", subject_id=ks.state.incident_id,
                              approver="bob", approved=True, timestamp=2)
    with pytest.raises(UnauthorizedReset):
        ks.reset(HUMAN, mismatch, at=3)                    # approver != resetting human


def test_corrupt_state_fails_active():
    ks = KillSwitch()
    ks._state = None                                       # simulate unreadable state
    assert ks.is_active() is True


def test_arm_recovers_from_corrupt_state():
    ks = KillSwitch()
    ks._state = None                                       # corrupt
    ks.arm(AGENT, "halt despite corrupt state", at=5)      # must not raise; must trip
    assert ks.is_active() is True and ks.state.trip_count == 1


# --- 多方 grant 复位路径(轨道3 子项目2,加法入口;旧 reset 不回归) ---

from quantos.contracts.killswitch import KillSwitchScope
from services.governance.approval import approve, consume, open_request, review

_A = Actor(actor_id="alice", kind=ActorKind.HUMAN)
_B = Actor(actor_id="bob", kind=ActorKind.HUMAN)
_C = Actor(actor_id="carol", kind=ActorKind.HUMAN)


def _grant_for(incident_id: str):
    r = open_request("req-1", incident_id, _A, at=1)
    r = review(r, _B, at=2)
    r = approve(r, _C, at=3)
    return consume(r, incident_id, _A, at=4)


def test_reset_with_grant_clears_with_valid_three_party_grant():
    ks = KillSwitch()
    ks.arm(_A, "halt", at=0, scope=KillSwitchScope.GLOBAL, incident_id="inc-9")
    ks.reset_with_grant(_A, _grant_for("inc-9"), at=5)
    assert ks.is_active() is False


def test_reset_with_grant_rejects_grant_for_other_incident():
    ks = KillSwitch()
    ks.arm(_A, "halt", at=0, incident_id="inc-9")
    with pytest.raises(UnauthorizedReset):
        ks.reset_with_grant(_A, _grant_for("DIFFERENT"), at=5)
    assert ks.is_active() is True                       # 仍锁死(fail-closed)


def test_reset_with_grant_requires_human_actor():
    ks = KillSwitch()
    ks.arm(_A, "halt", at=0, incident_id="inc-9")
    with pytest.raises(UnauthorizedReset):
        ks.reset_with_grant(Actor(actor_id="bot2", kind=ActorKind.AGENT), _grant_for("inc-9"), at=5)
