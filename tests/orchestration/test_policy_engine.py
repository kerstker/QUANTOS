"""K4a Policy Engine 测试:注册/评估/fail-closed/证据哈希确定性。"""
from __future__ import annotations

import pytest

from services.orchestration import (
    PolicyConflictError,
    PolicyDecision,
    PolicyEngine,
    PolicyNotFoundError,
)

T0 = 1_700_000_000_000


def test_register_and_evaluate_pass() -> None:
    engine = PolicyEngine()
    engine.register(
        "risk.position_limit",
        version="1.0",
        fn=lambda ctx: ("PASS", ("within limit",))
        if ctx["notional"] <= ctx["limit"]
        else ("FAIL", ("POSITION_LIMIT_EXCEEDED",)),
    )
    decision = engine.evaluate(
        "risk.position_limit", {"notional": 50, "limit": 100}, now_ms=T0
    )
    assert decision.verdict == "PASS"
    assert decision.reasons == ("within limit",)
    assert decision.policy_version == "1.0"
    assert decision.evaluated_at_ms == T0


def test_evaluate_fail_and_abstain_are_distinct() -> None:
    """ABSTAIN = 证据不足;FAIL = 明确拒绝;两者都不是放行。"""
    engine = PolicyEngine()
    engine.register("p", version="1", fn=lambda ctx: ("FAIL", ("no",)))
    engine.register("q", version="1", fn=lambda ctx: ("ABSTAIN", ("evidence missing",)))
    fail = engine.evaluate("p", {}, now_ms=T0)
    abstain = engine.evaluate("q", {}, now_ms=T0)
    assert fail.verdict == "FAIL"
    assert abstain.verdict == "ABSTAIN"
    assert fail.verdict != abstain.verdict
    assert abstain.reasons == ("evidence missing",)


def test_unregistered_policy_is_fail_closed() -> None:
    engine = PolicyEngine()
    with pytest.raises(PolicyNotFoundError):
        engine.evaluate("ghost", {}, now_ms=T0)  # 绝不默认 PASS


def test_fn_exception_becomes_fail_without_raising() -> None:
    engine = PolicyEngine()

    def boom(ctx):
        raise KeyError("missing evidence")

    engine.register("fragile", version="1", fn=boom)
    decision = engine.evaluate("fragile", {}, now_ms=T0)  # 不外抛
    assert decision.verdict == "FAIL"
    assert decision.reasons == ("POLICY_ERROR:KeyError",)


def test_invalid_verdict_from_fn_becomes_fail() -> None:
    engine = PolicyEngine()
    engine.register("sloppy", version="1", fn=lambda ctx: ("MAYBE", ()))
    decision = engine.evaluate("sloppy", {}, now_ms=T0)
    assert decision.verdict == "FAIL"
    assert decision.reasons == ("POLICY_ERROR:ValueError",)


def test_version_conflict_and_idempotent_reregister() -> None:
    engine = PolicyEngine()
    engine.register("p", version="1.0", fn=lambda ctx: ("PASS", ()))
    # 同 policy_id 同 version:幂等(fn 身份不比)。
    engine.register("p", version="1.0", fn=lambda ctx: ("FAIL", ("shadow",)))
    decision = engine.evaluate("p", {}, now_ms=T0)
    assert decision.verdict == "PASS"  # 首次注册生效,静默替换未发生
    with pytest.raises(PolicyConflictError):
        engine.register("p", version="2.0", fn=lambda ctx: ("PASS", ()))


def test_evidence_hash_is_deterministic_and_context_bound() -> None:
    engine = PolicyEngine()
    engine.register("p", version="1", fn=lambda ctx: ("PASS", ("ok",)))
    first = engine.evaluate("p", {"a": 1, "b": 2}, now_ms=T0)
    second = engine.evaluate("p", {"b": 2, "a": 1}, now_ms=T0 + 999)  # 键序无关
    assert first.evidence_hash == second.evidence_hash  # 同输入同哈希(与时间无关)
    different = engine.evaluate("p", {"a": 1, "b": 3}, now_ms=T0)
    assert different.evidence_hash != first.evidence_hash  # context 变则哈希变


def test_evidence_hash_cannot_be_hand_filled() -> None:
    """create 计算的哈希覆盖 verdict/reasons/context:手填的决策对不上 create。"""
    honest = PolicyDecision.create(
        policy_id="p",
        policy_version="1",
        verdict="PASS",
        reasons=("ok",),
        context={"x": 1},
        evaluated_at_ms=T0,
    )
    forged = PolicyDecision(
        policy_id="p",
        policy_version="1",
        verdict="PASS",
        reasons=("ok",),
        evidence_hash="0" * 64,  # 手填
        evaluated_at_ms=T0,
    )
    assert forged.evidence_hash != honest.evidence_hash
    # 非法 verdict 构造即拒。
    with pytest.raises(ValueError):
        PolicyDecision(
            policy_id="p",
            policy_version="1",
            verdict="MAYBE",
            reasons=(),
            evidence_hash="0" * 64,
            evaluated_at_ms=T0,
        )


def test_fn_returning_decision_is_normalized_by_engine() -> None:
    """fn 直接返回 PolicyDecision:engine 只取 verdict/reasons,版本/时间/哈希重封装。"""
    engine = PolicyEngine()

    def fn(ctx):
        return PolicyDecision.create(
            policy_id="p",
            policy_version="FORGED-9.9",  # fn 伪造版本
            verdict="PASS",
            reasons=("ok",),
            context={},
            evaluated_at_ms=0,
        )

    engine.register("p", version="1.0", fn=fn)
    decision = engine.evaluate("p", {"real": True}, now_ms=T0)
    assert decision.policy_version == "1.0"  # 注册版本,非 fn 伪造
    assert decision.evaluated_at_ms == T0


def test_registered_snapshot_is_deterministic() -> None:
    engine = PolicyEngine()
    engine.register("zeta", version="1", fn=lambda ctx: ("PASS", ()))
    engine.register("alpha", version="2", fn=lambda ctx: ("PASS", ()))
    engine.register("mid", version="1", fn=lambda ctx: ("PASS", ()))
    assert engine.registered() == (("alpha", "2"), ("mid", "1"), ("zeta", "1"))
