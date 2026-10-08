"""多方人工批准状态机单元测试(轨道3 子项目2)。"""
import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.governance.approval import (ApprovalRequest, ApprovalState, ApprovalStep,
                                          ApprovalViolation, approve, consume, open_request,
                                          reject, review, verify_multi_party_grant)

ALICE = Actor(actor_id="alice", kind=ActorKind.HUMAN)
BOB = Actor(actor_id="bob", kind=ActorKind.HUMAN)
CAROL = Actor(actor_id="carol", kind=ActorKind.HUMAN)
BOT = Actor(actor_id="bot", kind=ActorKind.AGENT)


def _three_party():
    r = open_request("r1", "inc-1", ALICE, at=1)
    r = review(r, BOB, at=2)
    r = approve(r, CAROL, at=3)
    return r


# --- MA1: 聚合骨架 ---

def test_step_entry_hash_is_deterministic_and_chains():
    s = ApprovalStep(state=ApprovalState.REQUESTED, actor_id="alice", timestamp=1, prev_hash="")
    assert s.entry_hash == s.entry_hash               # deterministic
    assert len(s.entry_hash) == 64                    # sha256 hex
    other = ApprovalStep(state=ApprovalState.REQUESTED, actor_id="alice", timestamp=2, prev_hash="")
    assert s.entry_hash != other.entry_hash           # timestamp participates


def test_request_state_and_distinct_and_head():
    s0 = ApprovalStep(state=ApprovalState.REQUESTED, actor_id="alice", timestamp=1, prev_hash="")
    s1 = ApprovalStep(state=ApprovalState.REVIEWED, actor_id="bob", timestamp=2, prev_hash=s0.entry_hash)
    req = ApprovalRequest(request_id="r1", subject_id="inc-1", steps=(s0, s1))
    assert req.state == ApprovalState.REVIEWED
    assert req.distinct_human_actor_ids == frozenset({"alice", "bob"})
    assert req.head_hash == s1.entry_hash


def test_approval_violation_is_permission_error():
    assert issubclass(ApprovalViolation, PermissionError)


# --- MA2: 纯转换 ---

def test_happy_path_reaches_consumed_with_three_distinct_humans():
    r = consume(_three_party(), "inc-1", ALICE, at=4)
    assert r.state == ApprovalState.CONSUMED
    assert r.distinct_human_actor_ids == frozenset({"alice", "bob", "carol"})
    prev = ""
    for s in r.steps:
        assert s.prev_hash == prev
        prev = s.entry_hash


def test_non_human_cannot_open_or_act():
    with pytest.raises(ApprovalViolation):
        open_request("r1", "inc-1", BOT, at=1)
    r = open_request("r1", "inc-1", ALICE, at=1)
    with pytest.raises(ApprovalViolation):
        review(r, BOT, at=2)


def test_reviewer_must_differ_from_requester():
    r = open_request("r1", "inc-1", ALICE, at=1)
    with pytest.raises(ApprovalViolation):
        review(r, ALICE, at=2)


def test_approver_must_differ_from_requester_and_reviewer():
    r = review(open_request("r1", "inc-1", ALICE, at=1), BOB, at=2)
    with pytest.raises(ApprovalViolation):
        approve(r, ALICE, at=3)
    with pytest.raises(ApprovalViolation):
        approve(r, BOB, at=3)


def test_wrong_state_transitions_raise():
    r = open_request("r1", "inc-1", ALICE, at=1)
    with pytest.raises(ApprovalViolation):        # 未复核不能批准
        approve(r, BOB, at=2)
    with pytest.raises(ApprovalViolation):        # 未批准不能消费
        consume(r, "inc-1", BOB, at=2)


def test_consume_is_one_time_and_subject_bound():
    r = _three_party()
    with pytest.raises(ApprovalViolation):        # subject 不符
        consume(r, "OTHER", ALICE, at=4)
    consumed = consume(r, "inc-1", ALICE, at=4)
    with pytest.raises(ApprovalViolation):        # 再消费(当前非 APPROVED)
        consume(consumed, "inc-1", ALICE, at=5)


def test_reject_is_terminal_from_requested_or_reviewed():
    r = open_request("r1", "inc-1", ALICE, at=1)
    rj = reject(r, BOB, at=2)
    assert rj.state == ApprovalState.REJECTED
    with pytest.raises(ApprovalViolation):        # 拒绝后不能再复核
        review(rj, CAROL, at=3)


# --- MA3: 校验器 ---

def test_verify_true_for_valid_three_party_consumed_grant():
    r = consume(_three_party(), "inc-1", ALICE, at=4)
    assert verify_multi_party_grant(r, "inc-1") is True


def test_verify_false_when_not_consumed():
    assert verify_multi_party_grant(_three_party(), "inc-1") is False        # 只到 APPROVED


def test_verify_false_for_fewer_than_three_distinct_humans():
    # 只到 REVIEWED 的两人请求:既非 CONSUMED,人数也不足
    two = review(open_request("r1", "inc-1", ALICE, at=1), BOB, at=2)
    assert verify_multi_party_grant(two, "inc-1") is False


def test_verify_false_on_subject_mismatch_and_empty_subject():
    r = consume(_three_party(), "inc-1", ALICE, at=4)
    assert verify_multi_party_grant(r, "OTHER") is False
    assert verify_multi_party_grant(r, "") is False


def test_verify_false_on_broken_hash_chain():
    r = consume(_three_party(), "inc-1", ALICE, at=4)
    # 篡改:把第 2 步换成一个 prev_hash 对不上的步(断链)
    bad_step = ApprovalStep(state=r.steps[1].state, actor_id=r.steps[1].actor_id,
                            timestamp=r.steps[1].timestamp, note="TAMPERED", prev_hash="deadbeef")
    tampered = r.model_copy(update={"steps": (r.steps[0], bad_step) + r.steps[2:]})
    assert verify_multi_party_grant(tampered, "inc-1") is False
