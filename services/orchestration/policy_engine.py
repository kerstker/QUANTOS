"""Policy Engine(727 §2.5,K4a 切片)— 统一策略评估接口 + 决策记录格式。

727 §2.5:「Autonomy Kernel 必须是确定性系统,不得由语言模型直接决定状态
转换」。本模块把「判定」收敛为**确定性注册接口**:策略是注册的纯函数
``fn(context) -> verdict + reasons``,评估产出带证据哈希的不可变
:class:`PolicyDecision`。

**不迁移任何现有判定器**(research_risk gate / pre_trade gate /
promotion_checks 等各域硬编码 fail-closed 判定保持原样)——迁移是后续各域
显式评审的事;本模块只提供统一的注册/评估/记录形状。

fail-closed 三规则(与全仓库一致,727 §9.2):

1. 未注册策略 → :class:`PolicyNotFoundError`,**绝不默认 PASS**;
2. 判定器抛异常 → verdict=FAIL + ``POLICY_ERROR:<类型名>``,**不向外抛**
   (判定器异常 = 拒,绝不放行);
3. 缺证据必须 FAIL 或 ABSTAIN,绝不默认 PASS——ABSTAIN 是「证据不足」,
   区别于 FAIL 的「明确拒绝」;消费方对两者的处置可以不同(ABSTAIN 通常
   = 补充证据后重评),但**两者都不是放行**。

纯核:无 IO、无墙钟(now_ms 调用方传入)、无随机、无网络。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple

from quantos.contracts.hashing import stable_hash

VERDICTS = ("PASS", "FAIL", "ABSTAIN")


class PolicyError(RuntimeError):
    """Base class for policy-engine errors."""


class PolicyConflictError(PolicyError):
    """A policy_id re-registration carries a different version."""


class PolicyNotFoundError(PolicyError):
    """The requested policy is not registered (never defaults to PASS)."""


@dataclass(frozen=True)
class PolicyDecision:
    """Immutable, hash-committed verdict record for one policy evaluation."""

    policy_id: str
    policy_version: str
    verdict: str                      # PASS | FAIL | ABSTAIN
    reasons: Tuple[str, ...]
    evidence_hash: str
    evaluated_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("policy_version must be a non-empty string")
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}")
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) for reason in self.reasons
        ):
            raise ValueError("reasons must be a tuple of strings")
        if not isinstance(self.evidence_hash, str) or not self.evidence_hash:
            raise ValueError("evidence_hash must be a non-empty string")
        if (
            isinstance(self.evaluated_at_ms, bool)
            or not isinstance(self.evaluated_at_ms, int)
            or self.evaluated_at_ms < 0
        ):
            raise ValueError("evaluated_at_ms must be a non-negative integer")

    @classmethod
    def create(
        cls,
        *,
        policy_id: str,
        policy_version: str,
        verdict: str,
        reasons: Tuple[str, ...],
        context: Mapping,
        evaluated_at_ms: int,
    ) -> "PolicyDecision":
        """构造决策并计算 evidence_hash(调用方无法手填错)。

        evidence_hash = stable_hash(policy_id, policy_version, verdict, reasons,
        stable_hash(context))——同输入同哈希(确定性可复算);context 本体不进
        决策记录(可能很大),其哈希进。
        """
        if not isinstance(context, Mapping):
            raise TypeError("context must be a Mapping")
        reasons = tuple(reasons)
        evidence_hash = stable_hash(
            {
                "context_hash": stable_hash(dict(context)),
                "policy_id": policy_id,
                "policy_version": policy_version,
                "reasons": list(reasons),
                "verdict": verdict,
            }
        )
        return cls(
            policy_id=policy_id,
            policy_version=policy_version,
            verdict=verdict,
            reasons=reasons,
            evidence_hash=evidence_hash,
            evaluated_at_ms=evaluated_at_ms,
        )


# fn 返回 PolicyDecision 或 (verdict, reasons) 简写;无论哪种,engine 都只取
# (verdict, reasons) 并用注册版本 + now_ms + 本次 context 统一重封装——fn 无法
# 伪造版本、时间或证据哈希。
PolicyFn = Callable[[Mapping], object]


class PolicyEngine:
    """Deterministic registry + evaluator for policy functions."""

    def __init__(self) -> None:
        self._policies: dict[str, tuple[str, PolicyFn]] = {}

    def register(self, policy_id: str, *, version: str, fn: PolicyFn) -> None:
        """注册策略。同 (policy_id, version) 重复注册幂等(fn 身份不比——同版本
        换实现是部署层的事,本层不裁决);同 policy_id 不同 version →
        PolicyConflictError(版本变更必须显式,绝不静默覆盖)。"""
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        if not callable(fn):
            raise TypeError("fn must be callable")
        existing = self._policies.get(policy_id)
        if existing is not None:
            if existing[0] != version:
                raise PolicyConflictError(
                    f"policy {policy_id} already registered at version "
                    f"{existing[0]}, refusing silent override to {version}"
                )
            return  # 幂等
        self._policies[policy_id] = (version, fn)

    def evaluate(
        self, policy_id: str, context: Mapping, *, now_ms: int
    ) -> PolicyDecision:
        """评估策略。未注册 → PolicyNotFoundError;fn 异常 → FAIL(不外抛)。"""
        if not isinstance(context, Mapping):
            raise TypeError("context must be a Mapping")
        if (
            isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0
        ):
            raise ValueError("now_ms must be a non-negative integer")
        entry = self._policies.get(policy_id)
        if entry is None:
            raise PolicyNotFoundError(f"policy not registered: {policy_id}")
        version, fn = entry
        try:
            output = fn(context)
            verdict, reasons = self._normalize(output)
            if verdict not in VERDICTS:
                raise ValueError(f"invalid verdict from policy fn: {verdict!r}")
        except Exception as exc:  # 判定器异常/非法输出 = 拒,绝不放行,也绝不外抛
            verdict, reasons = "FAIL", (f"POLICY_ERROR:{type(exc).__name__}",)
        return PolicyDecision.create(
            policy_id=policy_id,
            policy_version=version,
            verdict=verdict,
            reasons=reasons,
            context=context,
            evaluated_at_ms=now_ms,
        )

    @staticmethod
    def _normalize(output: object) -> Tuple[str, Tuple[str, ...]]:
        if isinstance(output, PolicyDecision):
            return output.verdict, tuple(output.reasons)
        if isinstance(output, tuple) and len(output) == 2:
            verdict, reasons = output
            if not isinstance(verdict, str):
                raise ValueError("verdict must be a string")
            if isinstance(reasons, str) or not isinstance(reasons, (tuple, list)):
                raise ValueError("reasons must be a tuple/list of strings")
            return verdict, tuple(reasons)
        raise ValueError(
            "policy fn must return a PolicyDecision or (verdict, reasons)"
        )

    def registered(self) -> Tuple[Tuple[str, str], ...]:
        """已注册策略的确定性快照(policy_id 字典序)。"""
        return tuple(
            (policy_id, self._policies[policy_id][0])
            for policy_id in sorted(self._policies)
        )


__all__ = [
    "VERDICTS",
    "PolicyConflictError",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyError",
    "PolicyNotFoundError",
]
