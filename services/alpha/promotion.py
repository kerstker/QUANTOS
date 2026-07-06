"""晋级执行核(战线①,2026-07-06)—— 把 CONSUMED 的三方批准兑现成 catalog 状态推进。

流程(顺序刻意,每一步都是 fail-closed 决定):

  1. `store.load(request_id)` — ApprovalStore fail-closed 重载(链校验/承诺校验);
  2. **consume 前预检**(评审 #4,全部不烧 grant):subject 可解析且 dst 在白名单
     ({APPROVED} — 唯一 human-only 的晋级)、请求状态是 APPROVED、执行人 == grant 中
     APPROVED 步的 actor、experiments db 单飞锁可得(评审 #7)、catalog 可加载、alpha 存在
     且 `can_alpha_transition(src, dst)`、证据可读(读失败的异常在烧之前抛出);
  3. **先烧 grant**:`consume` 落库在 advance 之前 —— advance 失败 grant 即作废、必须重走
     三方;绝不出现「已推进但 grant 还能再花」或「崩溃留下半花 grant」;
  4. `AlphaLifecycleManager.advance(grant=, eval_trial_count=)` —— 证据门一寸不松:
     backtest_sharpe 来自该 alpha 的 evaluation artifact(net_sharpe)、paper_sharpe 来自
     paper_track.db 的 track(alpha_id)、eval 期 trial count 来自同一 artifact 的
     catalog_trial_count(评审 #1/#8,任一缺失 → guard 拒绝,绝不从请求体代填);
  5. 烧毁之后的每条路径(成功 / guard 拒绝 / 任意异常)都落一条 DurableAuditLog 审计
     (评审 #3);event_id 自动加序号,同秒重试不因重复 event_id 打爆(评审 #5)。

威胁模型(诚实,勿删):`execute_promotion` 用自报的 executor_id 铸造 `Actor(kind=HUMAN)`。
经 promotion_api 暴露后,这把「受信代码在进程内凑 HUMAN Actor」这个 build-time 绕过面变成
runtime HTTP 能力 —— 详见 services/governance/promotion_api/main.py 的模块 docstring 与设计
文档。本模块自身仍是单进程受信代码层(与 CLAUDE.md 威胁模型一致);真正的出进程身份 =
Phase 14。核心无墙钟/无随机(now/nonce 由调用方注入);绝不 import 券商/网络 SDK、不 import
live/、不 import tools/。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from quantos.contracts.alpha import Actor, ActorKind, can_alpha_transition
from quantos.contracts.research import AlphaState
from services.governance.approval import ApprovalState, consume

from .catalog import AlphaCatalog
from .config import AlphaFactoryConfig
from .guards import grant_approver, parse_promotion_subject
from .lifecycle import AlphaLifecycleManager, GuardRejected

# 第一期晋级白名单:只有 →APPROVED(唯一 human-only 的晋级)。TESTING/VALIDATED/
# PAPER_TRADING 已有 agent/评估路径,不进此队列。
PROMOTABLE_DSTS = frozenset({AlphaState.APPROVED})

PROMOTION_KIND = "ALPHA_PROMOTION"


class PromotionPrecheckError(RuntimeError):
    """consume 前预检失败 —— grant 未被烧毁,修正后可重试(评审 #4)。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PromotionEvidence:
    """晋级证据 —— 只来自真实 artifact,绝不代填(评审 #8)。"""
    backtest_sharpe: Optional[float] = None    # evaluation artifact 的 net_sharpe
    paper_sharpe: Optional[float] = None       # paper_track.db track(alpha_id).paper_sharpe
    eval_trial_count: Optional[int] = None     # evaluation artifact 的 catalog_trial_count
    eval_run_id: str = ""
    notes: tuple = ()

    def paper_evidence(self) -> dict:
        return {"backtest_sharpe": self.backtest_sharpe, "paper_sharpe": self.paper_sharpe}


@dataclass(frozen=True)
class PromotionOutcome:
    ok: bool
    request_id: str
    alpha_id: str
    dst: str
    burned: bool
    reasons: tuple = ()
    audit_ok: bool = True
    evidence_notes: tuple = field(default_factory=tuple)


def gather_promotion_evidence(registry, paper_store, alpha_id: str) -> PromotionEvidence:
    """采集晋级证据。fail-closed:artifact 缺失/被篡改 → 对应字段 None + 诚实 note,由
    guard 拒绝;绝不用请求体或默认值代填。registry/paper_store 只需鸭子类型
    (list_evaluations/verify_evaluation、track)。"""
    notes: list[str] = []
    bt: Optional[float] = None
    etc: Optional[int] = None
    run_id = ""
    evals = registry.list_evaluations(alpha_id)
    if not evals:
        notes.append("NO_EVALUATION_ARTIFACT")
    else:
        latest = evals[-1]                      # list_evaluations 按 recorded_ts, run_id 升序
        run_id = latest["run_id"]
        if not registry.verify_evaluation(run_id):   # 写后被改的 artifact 不作数(fail-closed)
            notes.append("EVALUATION_ARTIFACT_INVALID")
        else:
            payload = json.loads(latest["evaluation_json"])
            bt = payload.get("net_sharpe")
            etc = payload.get("catalog_trial_count")
    pp: Optional[float] = None
    if paper_store is None:
        notes.append("NO_PAPER_TRACK_STORE")
    else:
        row = paper_store.track(alpha_id)
        if row is None or row.get("paper_sharpe") is None:
            notes.append("NO_PAPER_TRACK")
        else:
            pp = row["paper_sharpe"]
    return PromotionEvidence(backtest_sharpe=bt, paper_sharpe=pp, eval_trial_count=etc,
                             eval_run_id=run_id, notes=tuple(notes))


def _audit_event(audit, *, request_id: str, alpha_id: str, actor: str, action: str,
                 now: int, nonce: str = "", metadata: Optional[dict] = None) -> bool:
    """尽力而为但绝不静默重复:重复 event_id(同 request 同秒重试)自动加序号再试
    (评审 #5 —— DurableAuditLog 对重放 raise ValueError);审计库本身坏了返回 False,
    由调用方把 audit_ok=False 诚实带回 outcome。AuditEvent.metadata 是 dict[str,str],
    列表值在此统一序列化。"""
    meta = {k: ("; ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v))
            for k, v in (metadata or {}).items()}
    base = f"promo.{action}.{request_id}.{now}" + (f".{nonce}" if nonce else "")
    for k in range(8):
        event_id = base if k == 0 else f"{base}.{k}"
        try:
            audit.append(event_id=event_id, actor=actor, action=action, timestamp=now,
                         target=alpha_id, metadata=meta)
            return True
        except ValueError:
            continue
        except Exception:
            return False
    return False


class _SingleFlight:
    """experiments db 的文件级单飞锁(评审 #7)。O_CREAT|O_EXCL 原子创建;已存在 →
    PromotionPrecheckError(fail-closed:宁可拒绝也不并发写坏累积语义的 registry)。
    崩溃遗留的锁文件需人工删除(锁内写有 pid/时间以便排查)——文档写明。"""

    def __init__(self, path: Optional[str], *, now: int):
        self._path = path
        self._acquired = False
        if path is None:
            return
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise PromotionPrecheckError(f"PROMOTION_BUSY: lock held at {path}")
        try:
            os.write(fd, f"pid={os.getpid()} at={now}\n".encode("utf-8"))
        finally:
            os.close(fd)
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self._path is not None:
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._acquired = False


def execute_promotion(*, store, registry, audit, request_id: str, executor_id: str, now: int,
                      paper_store=None, config: Optional[AlphaFactoryConfig] = None,
                      nonce: str = "", lock_path: Optional[str] = None) -> PromotionOutcome:
    """兑现一份三方批准。预检失败 raise PromotionPrecheckError(grant 未烧);
    烧毁之后一律返回 PromotionOutcome(burned=True)且必落审计。"""
    req = store.load(request_id)                       # fail-closed 重载(篡改/损坏在此拒绝)

    # --- consume 前预检(评审 #4:全部不烧 grant)---
    try:
        alpha_id, dst = parse_promotion_subject(req.subject_id)
    except ValueError as exc:
        raise PromotionPrecheckError(f"BAD_SUBJECT: {exc}")
    if dst not in PROMOTABLE_DSTS:
        raise PromotionPrecheckError(f"DST_NOT_PROMOTABLE: {dst.value}")
    if req.state != ApprovalState.APPROVED:
        raise PromotionPrecheckError(f"REQUEST_NOT_APPROVED: state={req.state.value}")
    approver = grant_approver(req)
    if approver is None or approver != executor_id:
        raise PromotionPrecheckError(
            "EXECUTOR_NOT_APPROVER: only the approving human may execute their own approval")

    lock = _SingleFlight(lock_path, now=now)
    try:
        catalog = AlphaCatalog()
        registry.load_into(catalog)                    # 在烧之前:损坏的 registry 在此炸,不白烧
        if alpha_id not in catalog.all_ids():
            raise PromotionPrecheckError(f"ALPHA_NOT_IN_CATALOG: {alpha_id}")
        src = catalog.state(alpha_id)
        if not can_alpha_transition(src, dst):
            raise PromotionPrecheckError(f"ILLEGAL_TRANSITION: {src.value}->{dst.value}")
        evidence = gather_promotion_evidence(registry, paper_store, alpha_id)   # 纯读,在烧之前

        # --- ① 先烧 grant(fail-closed 方向:宁可烧废也不留可复花的 grant)---
        actor = Actor(actor_id=executor_id, kind=ActorKind.HUMAN)   # 自报标签,见模块 docstring
        consumed = consume(req, req.subject_id, actor, at=now)
        store.save(consumed, kind=PROMOTION_KIND, now=now)

        # --- ② 烧毁之后:每条路径必落审计(评审 #3)---
        meta_base = {"request_id": request_id, "src": src.value, "dst": dst.value,
                     "eval_run_id": evidence.eval_run_id,
                     "evidence_notes": list(evidence.notes)}
        try:
            manager = AlphaLifecycleManager(catalog, config)
            manager.advance(alpha_id, dst, actor, timestamp=now, grant=consumed,
                            paper_evidence=evidence.paper_evidence(),
                            eval_trial_count=evidence.eval_trial_count)
            registry.save(catalog, now=now)            # ③ 状态持久化(累积语义,锁内)
        except GuardRejected as exc:
            audit_ok = _audit_event(audit, request_id=request_id, alpha_id=alpha_id,
                                    actor=executor_id, action="PROMOTION_EXECUTION_FAILED",
                                    now=now, nonce=nonce,
                                    metadata={**meta_base, "reasons": list(exc.reasons)})
            return PromotionOutcome(ok=False, request_id=request_id, alpha_id=alpha_id,
                                    dst=dst.value, burned=True, reasons=exc.reasons,
                                    audit_ok=audit_ok, evidence_notes=evidence.notes)
        except Exception as exc:                       # load 之后的任何失败:grant 已烧,如实记录
            reason = f"EXECUTION_ERROR: {type(exc).__name__}: {exc}"
            audit_ok = _audit_event(audit, request_id=request_id, alpha_id=alpha_id,
                                    actor=executor_id, action="PROMOTION_EXECUTION_FAILED",
                                    now=now, nonce=nonce,
                                    metadata={**meta_base, "reasons": [reason]})
            return PromotionOutcome(ok=False, request_id=request_id, alpha_id=alpha_id,
                                    dst=dst.value, burned=True, reasons=(reason,),
                                    audit_ok=audit_ok, evidence_notes=evidence.notes)
        audit_ok = _audit_event(audit, request_id=request_id, alpha_id=alpha_id,
                                actor=executor_id, action="PROMOTION_EXECUTED",
                                now=now, nonce=nonce, metadata=meta_base)
        return PromotionOutcome(ok=True, request_id=request_id, alpha_id=alpha_id,
                                dst=dst.value, burned=True, reasons=(),
                                audit_ok=audit_ok, evidence_notes=evidence.notes)
    finally:
        lock.release()
