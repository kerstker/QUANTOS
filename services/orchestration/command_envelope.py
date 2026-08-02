"""CommandEnvelope (727 §4.2) — the canonical command-side input contract.

每条进入自治内核的命令都必须被包装成不可变的 :class:`CommandEnvelope`。
字段与 727 §4.2 一一对应(映射表见设计文档
``docs/engineering/specs/2026-07-27-autonomy-kernel-design.md`` §4)。

Deterministic core: ``requested_at`` 是调用方供给的 Unix 毫秒整数(非墙钟)。
``payload_hash`` 由 :func:`quantos.contracts.hashing.stable_hash` 对 payload 的
canonical 形式计算;信封在构造时即 fail-closed 校验,哈希不匹配绝不放行——
防止调用方「信封说一套、载荷是另一套」。
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional

from quantos.contracts.alpha import Actor
from quantos.contracts.hashing import stable_hash


class CommandEnvelopeError(ValueError):
    """The envelope is malformed or its payload hash does not match."""


def _payload_hash(payload: Mapping) -> str:
    # MappingProxyType 不可被 json 序列化;哈希一律对普通 dict 的 canonical 形式计算。
    return stable_hash(dict(payload))


@dataclass(frozen=True)
class CommandEnvelope:
    """Immutable command envelope (727 §4.2). Fail-closed at construction."""

    command_id: str
    command_type: str
    requested_at: int                      # Unix epoch milliseconds, caller-supplied
    actor: Actor
    subject: str
    idempotency_key: str
    payload_hash: str
    payload: Mapping
    expected_state_version: Optional[int] = None
    authorization_ref: str = ""            # "" = 无授权引用(多数研究命令)

    def __post_init__(self) -> None:
        # 存为不可变 Mapping:接受 dict,内部冻结,防止调用方事后改写载荷。
        if not isinstance(self.payload, Mapping):
            raise CommandEnvelopeError("payload must be a Mapping")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        self.validate()

    @classmethod
    def create(
        cls,
        *,
        command_id: str,
        command_type: str,
        requested_at: int,
        actor: Actor,
        subject: str,
        idempotency_key: str,
        payload: Mapping,
        expected_state_version: Optional[int] = None,
        authorization_ref: str = "",
    ) -> "CommandEnvelope":
        """构造信封并由 stable_hash(payload) 计算 payload_hash(调用方无法填错)。"""
        return cls(
            command_id=command_id,
            command_type=command_type,
            requested_at=requested_at,
            actor=actor,
            subject=subject,
            idempotency_key=idempotency_key,
            payload_hash=_payload_hash(payload),
            payload=payload,
            expected_state_version=expected_state_version,
            authorization_ref=authorization_ref,
        )

    def validate(self) -> None:
        """Fail-closed structural validation(由 __post_init__ 调用)。"""
        for name in ("command_id", "command_type", "subject", "idempotency_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CommandEnvelopeError(f"{name} must be a non-empty string")
        if not isinstance(self.actor, Actor):
            raise CommandEnvelopeError("actor must be a quantos.contracts.alpha.Actor")
        if (
            isinstance(self.requested_at, bool)
            or not isinstance(self.requested_at, int)
            or self.requested_at < 0
        ):
            raise CommandEnvelopeError("requested_at must be a non-negative integer (ms)")
        if self.expected_state_version is not None and (
            isinstance(self.expected_state_version, bool)
            or not isinstance(self.expected_state_version, int)
            or self.expected_state_version < 0
        ):
            raise CommandEnvelopeError(
                "expected_state_version must be a non-negative integer or None"
            )
        if not isinstance(self.authorization_ref, str):
            raise CommandEnvelopeError("authorization_ref must be a string")
        if not isinstance(self.payload_hash, str) or not self.payload_hash:
            raise CommandEnvelopeError("payload_hash must be a non-empty string")
        if self.payload_hash != _payload_hash(self.payload):
            raise CommandEnvelopeError(
                "payload_hash does not match stable_hash(payload)"
            )


__all__ = ["CommandEnvelope", "CommandEnvelopeError"]
