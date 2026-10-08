# 多方人工批准状态机 实现计划(轨道3 子项目2)

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 造一个可复用的多步人工批准状态机(REQUESTED→REVIEWED→APPROVED→CONSUMED,三个不同真人+哈希链审计),接进 kill-switch 复位与部署门,只做加法。

**Architecture:** 新扁平模块 `services/governance/approval.py` —— 不可变 `ApprovalRequest` 聚合 + 纯转换函数 + fail-closed 校验器 `verify_multi_party_grant`。给 `KillSwitch` 加 `reset_with_grant`、给 `evaluate_deployment` 加 `grant=` 参数,旧单记录路径一字不动。

**Tech Stack:** Python 3、pydantic v2(frozen 模型)、`quantos.contracts`(Actor/hashing)、pytest。无第三方新依赖。

## Global Constraints

- L2_DRY_RUN_ONLY 一寸不让:`LIVE_TRADING_ENABLED/REAL_BROKER_ENABLED/ALLOW_ORDER_SUBMISSION/ALLOW_AI_DIRECT_TRADING` 恒 false、`REQUIRE_HUMAN_APPROVAL` 恒 true —— 本计划不碰这些标志。
- **绝不** import 券商/CTP/FIX/网络 SDK;**绝不**写能真发单/真凭证/真连接的代码;**绝不**弱化任何现有门(只做加法,旧路径零回归)。
- 核心**无墙钟、无随机**:`at: int`、id 由调用方边界传入。
- `services/governance/approval.py` **不进** `research_runner` 白名单(它不是 research kind)。
- 每次转换返回**新**聚合(pydantic frozen);全部前置检查 fail-closed(不满足即 raise);`verify_multi_party_grant` 任何异常返回 False。
- 诚实边界:本子项目强化的是*批准的构造路径*,单进程内挡不住受信代码凑三个 Actor 绕过——那是子项目3。不在代码或文档里假装它是信任边界。

---

### Task MA1: 状态机聚合骨架

**Files:**
- Create: `services/governance/__init__.py`(空)
- Create: `services/governance/approval.py`
- Test: `tests/governance/test_multi_party_approval.py`

**Interfaces:**
- Produces: `ApprovalViolation(PermissionError)`;`ApprovalState`(枚举);`ApprovalStep`(frozen:`state`/`actor_id`/`timestamp`/`note`/`prev_hash` + `entry_hash` 属性);`ApprovalRequest`(frozen:`request_id`/`subject_id`/`steps` + `state`/`distinct_human_actor_ids`/`head_hash` 属性)。

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_multi_party_approval.py
from services.governance.approval import (ApprovalRequest, ApprovalState, ApprovalStep,
                                          ApprovalViolation)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_multi_party_approval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.governance'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/governance/approval.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_multi_party_approval.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add services/governance/__init__.py services/governance/approval.py tests/governance/test_multi_party_approval.py
git commit -m "feat(governance): 多方批准状态机聚合骨架(ApprovalRequest/Step/State + 哈希链)"
```

---

### Task MA2: 纯转换函数(fail-closed 三方)

**Files:**
- Modify: `services/governance/approval.py`
- Test: `tests/governance/test_multi_party_approval.py`(追加)

**Interfaces:**
- Consumes: MA1 的 `ApprovalRequest`/`ApprovalState`/`ApprovalViolation`;`quantos.contracts.alpha.Actor`(`actor_id`/`is_human`)。
- Produces: `open_request(request_id, subject_id, actor, *, at, note="") -> ApprovalRequest`;`review(req, actor, *, at, note="") -> ApprovalRequest`;`approve(req, actor, *, at, note="") -> ApprovalRequest`;`reject(req, actor, *, at, note="") -> ApprovalRequest`;`consume(req, subject_id, actor, *, at, note="") -> ApprovalRequest`。

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_multi_party_approval.py (追加)
import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.governance.approval import (approve, consume, open_request, reject, review)

ALICE = Actor(actor_id="alice", kind=ActorKind.HUMAN)
BOB = Actor(actor_id="bob", kind=ActorKind.HUMAN)
CAROL = Actor(actor_id="carol", kind=ActorKind.HUMAN)
BOT = Actor(actor_id="bot", kind=ActorKind.AGENT)


def _three_party():
    r = open_request("r1", "inc-1", ALICE, at=1)
    r = review(r, BOB, at=2)
    r = approve(r, CAROL, at=3)
    return r


def test_happy_path_reaches_consumed_with_three_distinct_humans():
    r = consume(_three_party(), "inc-1", ALICE, at=4)
    assert r.state == ApprovalState.CONSUMED
    assert r.distinct_human_actor_ids == frozenset({"alice", "bob", "carol"})
    # 链自洽:每步 prev_hash == 上一步 entry_hash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_multi_party_approval.py -q`
Expected: FAIL — `ImportError: cannot import name 'open_request'`

- [ ] **Step 3: Write minimal implementation**(追加到 `services/governance/approval.py` 末尾)

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_multi_party_approval.py -q`
Expected: PASS(全部通过)

- [ ] **Step 5: Commit**

```bash
git add services/governance/approval.py tests/governance/test_multi_party_approval.py
git commit -m "feat(governance): 多方批准纯转换(open/review/approve/reject/consume,三个不同真人 fail-closed)"
```

---

### Task MA3: fail-closed 不变量校验器

**Files:**
- Modify: `services/governance/approval.py`
- Test: `tests/governance/test_multi_party_approval.py`(追加)

**Interfaces:**
- Consumes: MA1/MA2 的聚合与转换。
- Produces: `verify_multi_party_grant(req: ApprovalRequest, subject_id: str) -> bool`。

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_multi_party_approval.py (追加)
from services.governance.approval import ApprovalStep, verify_multi_party_grant


def test_verify_true_for_valid_three_party_consumed_grant():
    r = consume(_three_party(), "inc-1", ALICE, at=4)
    assert verify_multi_party_grant(r, "inc-1") is True


def test_verify_false_when_not_consumed():
    assert verify_multi_party_grant(_three_party(), "inc-1") is False        # 只到 APPROVED


def test_verify_false_for_two_party():
    r = review(open_request("r1", "inc-1", ALICE, at=1), BOB, at=2)
    # 强行推进:构造一个只有两人的 CONSUMED(用 CAROL 缺席)——approve 需第三人,故走不到;
    # 直接断言两人链无法产生合法 grant:approve 由 BOB 会被拒,这里验证 verify 对 APPROVED-未达 的拒绝已被 test_verify_false_when_not_consumed 覆盖。
    assert verify_multi_party_grant(r, "inc-1") is False


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_multi_party_approval.py -q`
Expected: FAIL — `ImportError: cannot import name 'verify_multi_party_grant'`

- [ ] **Step 3: Write minimal implementation**(追加到 `services/governance/approval.py` 末尾)

```python
_MIN_DISTINCT_HUMANS = 3


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_multi_party_approval.py -q`
Expected: PASS(全部通过)

- [ ] **Step 5: Commit**

```bash
git add services/governance/approval.py tests/governance/test_multi_party_approval.py
git commit -m "feat(governance): verify_multi_party_grant(CONSUMED∧3人∧subject∧链自洽,fail-closed)"
```

---

### Task MA4: 接进现有两门(只做加法)

**Files:**
- Modify: `services/risk/pre_trade/kill_switch.py`(加 `reset_with_grant`,旧 `reset` 不动)
- Modify: `services/deployment/gate.py`(加 `grant=` 关键字,旧路径不动)
- Test: `tests/risk/test_kill_switch.py`(追加)、`tests/deployment/test_deployment_gate.py`(追加)

**Interfaces:**
- Consumes: MA3 的 `verify_multi_party_grant`;`KillSwitch`(现有 `arm`/`reset`/`_state.incident_id`);`evaluate_deployment`(现有签名 + `DeploymentDecision`)。
- Produces: `KillSwitch.reset_with_grant(actor, grant, *, at) -> KillSwitchState`;`evaluate_deployment(..., grant: Optional[ApprovalRequest] = None)`。

- [ ] **Step 1: Write the failing test**

```python
# tests/risk/test_kill_switch.py (追加)
import pytest

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.killswitch import KillSwitchScope
from services.governance.approval import approve, consume, open_request, review
from services.risk.pre_trade.kill_switch import KillSwitch, UnauthorizedReset

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
        ks.reset_with_grant(Actor(actor_id="bot", kind=ActorKind.AGENT), _grant_for("inc-9"), at=5)
```

```python
# tests/deployment/test_deployment_gate.py (追加)
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.governance import Environment
from services.deployment.gate import evaluate_deployment
from services.governance.approval import approve, consume, open_request, review

_A = Actor(actor_id="alice", kind=ActorKind.HUMAN)
_B = Actor(actor_id="bob", kind=ActorKind.HUMAN)
_C = Actor(actor_id="carol", kind=ActorKind.HUMAN)


def _grant(subject: str):
    r = review(open_request("req-1", subject, _A, at=1), _B, at=2)
    return consume(approve(r, _C, at=3), subject, _A, at=4)


def test_paper_allowed_with_valid_multi_party_grant():
    d = evaluate_deployment(Environment.PAPER, subject="deploy-42", grant=_grant("deploy-42"))
    assert d.allowed is True


def test_paper_blocked_with_two_party_grant():
    # 只两人(缺 approve 的第三人)——用一个只到 REVIEWED 的请求冒充,verify 应拒
    two = review(open_request("req-1", "deploy-42", _A, at=1), _B, at=2)
    d = evaluate_deployment(Environment.PAPER, subject="deploy-42", grant=two)
    assert d.allowed is False
    assert "NO_MULTI_PARTY_APPROVAL" in d.reasons


def test_live_still_blocked_even_with_valid_grant():
    d = evaluate_deployment(Environment.LIVE, subject="deploy-42", grant=_grant("deploy-42"))
    assert d.allowed is False       # readiness pin + LIVE flags 仍挡死
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/risk/test_kill_switch.py tests/deployment/test_deployment_gate.py -q`
Expected: FAIL — `AttributeError: 'KillSwitch' object has no attribute 'reset_with_grant'` / `evaluate_deployment() got an unexpected keyword argument 'grant'`

- [ ] **Step 3: Write minimal implementation**

在 `services/risk/pre_trade/kill_switch.py` 顶部 import 区加:

```python
from services.governance.approval import ApprovalRequest, verify_multi_party_grant
```

在 `KillSwitch` 类内 `reset` 方法之后加:

```python
    def reset_with_grant(self, actor: Optional[Actor], grant: ApprovalRequest, *, at: int) -> KillSwitchState:
        """复位(多方路径):要 HUMAN actor + 绑定到本事件的、已 CONSUMED 的三方 grant。fail-closed。
        旧 reset(单 ApprovalRecord)不变;这是更严的加法入口。"""
        s = self._state
        if not s.tripped:
            return s
        if actor is None or not actor.is_human:
            raise UnauthorizedReset("kill-switch reset requires a HUMAN actor")
        if not verify_multi_party_grant(grant, s.incident_id):
            raise UnauthorizedReset("reset requires a consumed multi-party approval grant bound to this incident")
        self._state = s.model_copy(update={"tripped": False, "reset_by": actor, "reset_at": at})
        return self._state
```

在 `services/deployment/gate.py`:import 区加

```python
from services.governance.approval import ApprovalRequest, verify_multi_party_grant
```

把 `evaluate_deployment` 签名末尾加 `grant: Optional[ApprovalRequest] = None`,并把 PAPER 批准那步改成:

```python
        if grant is not None:
            reasons = [] if verify_multi_party_grant(grant, subject) else ["NO_MULTI_PARTY_APPROVAL"]
        else:
            reasons = _verified_human_approval(approval, actor, subject)   # PAPER and above need this
```

（`_LIVE_ENVS` 的 readiness/flags 检查块**保持不动**——即便 grant 合法,LIVE 仍被 pin + flags 挡死。）

- [ ] **Step 4: Run test to verify it passes(含旧测试不回归)**

Run: `PYTHONPATH=. python -m pytest tests/risk/test_kill_switch.py tests/deployment/test_deployment_gate.py tests/governance/test_multi_party_approval.py -q`
Expected: PASS(新旧全过)

- [ ] **Step 5: Commit**

```bash
git add services/risk/pre_trade/kill_switch.py services/deployment/gate.py tests/risk/test_kill_switch.py tests/deployment/test_deployment_gate.py
git commit -m "feat(governance): 多方 grant 接进 kill-switch 复位与部署门(只做加法,旧路径零回归)"
```

---

### Task MA5: 治理边界钉死 + 全门绿 + 文档

**Files:**
- Create: `tests/governance/test_multi_party_approval_boundary.py`
- Modify: `CLAUDE.md`、`docs/PROJECT_STATE.md`、记忆 `track3-live-scaffold.md`

**Interfaces:**
- Consumes: 全部前序产物。

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_multi_party_approval_boundary.py
"""治理:机器钉死多方批准状态机的 L2 边界。
1) approval.py 不 import 任何券商/交易所 SDK、不 import 网络库;
2) 非人 actor 结构上到不了 APPROVED(机器不变量)。"""
import ast
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MOD = os.path.join(_ROOT, "services", "governance", "approval.py")

_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}


def _import_roots(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def test_approval_imports_no_sdk_or_network():
    roots = _import_roots(_MOD)
    assert not (roots & _FORBIDDEN_SDK), f"FORBIDDEN SDK in approval.py: {sorted(roots & _FORBIDDEN_SDK)}"
    assert not (roots & _NETWORK_ROOTS), f"network import in approval.py: {sorted(roots & _NETWORK_ROOTS)}"


def test_non_human_can_never_reach_approved():
    from quantos.contracts.alpha import Actor, ActorKind
    from services.governance.approval import (ApprovalViolation, approve, open_request, review)
    human_a = Actor(actor_id="a", kind=ActorKind.HUMAN)
    human_b = Actor(actor_id="b", kind=ActorKind.HUMAN)
    bot = Actor(actor_id="bot", kind=ActorKind.AGENT)
    r = review(open_request("r", "s", human_a, at=1), human_b, at=2)
    with pytest.raises(ApprovalViolation):        # 非人批准被结构性拒绝
        approve(r, bot, at=3)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_multi_party_approval_boundary.py -q`
Expected: PASS(2 passed)

- [ ] **Step 3: 全门绿**

Run: `PYTHONPATH=. python scripts/validate.py`
Expected: 末尾 `VALIDATE PASSED (fail-closed gate green).`
若失败:读报错、修根因、重跑,直到绿。

- [ ] **Step 4: 文档 + 记忆**

- `CLAUDE.md`:在 `services/trading/broker_adapter.py` 条目后加 `services/governance/approval.py` 条目(多方批准状态机 + 接进两门 + 诚实边界)。
- `docs/PROJECT_STATE.md`:轨道3 段落更新——子项目2(多方人工批准)landed。
- 记忆 `track3-live-scaffold.md`:把子项目2 从 PENDING 改为 DONE,记要点。

- [ ] **Step 5: Commit**

```bash
git add tests/governance/test_multi_party_approval_boundary.py CLAUDE.md docs/PROJECT_STATE.md
git commit -m "test(governance)+docs: 多方批准 L2 边界机器钉死(无SDK/网络、非人到不了APPROVED)+ 记入活文档"
```

---

## 自审(Self-Review)

- **Spec coverage:** spec 五块 ↔ 计划五任务一一对应(1 聚合 / 2 转换 / 3 校验器 / 4 接门 / 5 治理+门+文档),诚实边界在 Global Constraints + MA1 docstring + MA5 文档三处落地。
- **Placeholder scan:** 无 TBD/TODO;每步给出完整可跑代码与预期输出。
- **Type consistency:** `ApprovalRequest`/`ApprovalState`/`ApprovalStep`/`ApprovalViolation` 全程一致;转换签名 `(req, actor, *, at, note="")`、`consume` 多一个 `subject_id`、`open_request` 多 `request_id/subject_id`,前后引用一致;`verify_multi_party_grant(req, subject_id)` 在 MA3 定义、MA4 两处消费签名吻合;`reset_with_grant(actor, grant, *, at)` 定义与测试调用吻合。
- **只做加法核对:** 旧 `KillSwitch.reset`、旧 `evaluate_deployment` 单记录路径均不改;MA4 Step 4 显式跑旧测试确认零回归。
