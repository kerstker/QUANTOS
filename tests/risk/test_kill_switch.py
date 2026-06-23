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
