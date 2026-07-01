"""多方人工批准状态机(轨道3 子项目2)—— L2_DRY_RUN_ONLY,一寸不让。

把「人工批准」从单人一张条子抬到多个不同真人 + 全程哈希链审计。一份批准走
REQUESTED→REVIEWED→APPROVED→CONSUMED,提出/复核/批准必须是三个两两不同的真人,每步追加到一条自带
哈希链的账本(与 AuditEvent 同法防篡改),批准一次性、绑定到具体 subject。核心无墙钟/无随机
(at:int、id 由调用方传)。绝不 import 券商/网络 SDK。

诚实边界:这强化的是*批准的构造路径*,单进程内挡不住受信代码直接凑三个 HUMAN Actor 绕过——那个
build-time 面是轨道3 子项目3(进程隔离/签名引用)的范畴,和 CLAUDE.md 威胁模型一致。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from quantos.contracts.alpha import Actor
from quantos.contracts.hashing import stable_hash


class ApprovalViolation(PermissionError):
    pass


class ApprovalState(str, Enum):
    REQUESTED = "REQUESTED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"


# 参与「不同真人」计数的状态(提出/复核/批准);CONSUMED/REJECTED 不计入 quorum
_QUORUM_STATES = (ApprovalState.REQUESTED, ApprovalState.REVIEWED, ApprovalState.APPROVED)
_MIN_DISTINCT_HUMANS = 3


class ApprovalStep(BaseModel):
    model_config = ConfigDict(frozen=True)
    state: ApprovalState
    actor_id: str = Field(min_length=1)
    timestamp: int = Field(ge=0)
    note: str = ""
    prev_hash: str = ""

    @property
    def entry_hash(self) -> str:
        return stable_hash({
            "state": self.state.value, "actor_id": self.actor_id,
            "timestamp": self.timestamp, "note": self.note, "prev_hash": self.prev_hash,
        })


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    steps: tuple[ApprovalStep, ...] = ()

    @property
    def state(self) -> ApprovalState:
        return self.steps[-1].state if self.steps else ApprovalState.REQUESTED

    @property
    def distinct_human_actor_ids(self) -> frozenset[str]:
        return frozenset(s.actor_id for s in self.steps if s.state in _QUORUM_STATES)

    @property
    def head_hash(self) -> str:
        return self.steps[-1].entry_hash if self.steps else ""


# --- 纯转换(全部 fail-closed;返回新聚合) ---

def _require_human(actor: Actor) -> None:
    if actor is None or not getattr(actor, "is_human", False):
        raise ApprovalViolation("approval steps require a HUMAN actor")


def _append(req: ApprovalRequest, state: ApprovalState, actor: Actor, at: int, note: str) -> ApprovalRequest:
    step = ApprovalStep(state=state, actor_id=actor.actor_id, timestamp=at, note=note,
                        prev_hash=req.head_hash)
    return req.model_copy(update={"steps": req.steps + (step,)})


def open_request(request_id: str, subject_id: str, actor: Actor, *, at: int, note: str = "") -> ApprovalRequest:
    _require_human(actor)
    req = ApprovalRequest(request_id=request_id, subject_id=subject_id, steps=())
    return _append(req, ApprovalState.REQUESTED, actor, at, note)


def review(req: ApprovalRequest, actor: Actor, *, at: int, note: str = "") -> ApprovalRequest:
    _require_human(actor)
    if req.state != ApprovalState.REQUESTED:
        raise ApprovalViolation(f"review requires REQUESTED, got {req.state}")
    if actor.actor_id in req.distinct_human_actor_ids:
        raise ApprovalViolation("reviewer must differ from the requester")
    return _append(req, ApprovalState.REVIEWED, actor, at, note)


def approve(req: ApprovalRequest, actor: Actor, *, at: int, note: str = "") -> ApprovalRequest:
    _require_human(actor)
    if req.state != ApprovalState.REVIEWED:
        raise ApprovalViolation(f"approve requires REVIEWED, got {req.state}")
    if actor.actor_id in req.distinct_human_actor_ids:
        raise ApprovalViolation("approver must differ from requester and reviewer")
    return _append(req, ApprovalState.APPROVED, actor, at, note)


def reject(req: ApprovalRequest, actor: Actor, *, at: int, note: str = "") -> ApprovalRequest:
    _require_human(actor)
    if req.state not in (ApprovalState.REQUESTED, ApprovalState.REVIEWED):
        raise ApprovalViolation(f"reject requires REQUESTED/REVIEWED, got {req.state}")
    return _append(req, ApprovalState.REJECTED, actor, at, note)


def consume(req: ApprovalRequest, subject_id: str, actor: Actor, *, at: int, note: str = "") -> ApprovalRequest:
    _require_human(actor)
    if req.state != ApprovalState.APPROVED:
        raise ApprovalViolation(f"consume requires APPROVED, got {req.state}")
    if req.subject_id != subject_id:
        raise ApprovalViolation("consume subject does not match the request subject")
    return _append(req, ApprovalState.CONSUMED, actor, at, note)


# --- fail-closed 不变量校验器 ---

def _hash_chain_ok(req: ApprovalRequest) -> bool:
    prev = ""
    for s in req.steps:
        if s.prev_hash != prev:
            return False
        prev = s.entry_hash
    return True


def verify_multi_party_grant(req: ApprovalRequest, subject_id: str) -> bool:
    """fail-closed:唯一放行条件——CONSUMED ∧ subject 绑定 ∧ ≥3 个不同真人 ∧ 哈希链自洽。否则/任何异常 → False。"""
    try:
        if not subject_id:
            return False
        if req.state != ApprovalState.CONSUMED:
            return False
        if req.subject_id != subject_id:
            return False
        if len(req.distinct_human_actor_ids) < _MIN_DISTINCT_HUMANS:
            return False
        return _hash_chain_ok(req)
    except Exception:
        return False
