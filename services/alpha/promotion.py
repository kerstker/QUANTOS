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
runtime HTTP 能力 —— 详见 services/promotion_api/main.py 的模块 docstring 与设计
文档。本模块自身仍是单进程受信代码层(与 CLAUDE.md 威胁模型一致);真正的出进程身份 =
Phase 14。核心无墙钟/无随机(now/nonce 由调用方注入);绝不 import 券商/网络 SDK、不 import
live/、不 import tools/。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional

from quantos.contracts.alpha import Actor, ActorKind, can_alpha_transition
from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.research import AlphaState
from services.governance.approval import ApprovalState, consume

from .catalog import AlphaCatalog
from .config import AlphaFactoryConfig
from .events import alpha_promoted_event
from .guards import grant_approver, parse_promotion_subject
from .lifecycle import AlphaLifecycleManager, GuardRejected
from .model_card import verify_model_card_exists

# 第一期晋级白名单:只有 →APPROVED(唯一 human-only 的晋级)。TESTING/VALIDATED/
# PAPER_TRADING 已有 agent/评估路径,不进此队列。
PROMOTABLE_DSTS = frozenset({AlphaState.APPROVED})

# 进程内单飞锁(2026-07-06 对抗式复审 CONFIRMED 修复,front1-governance 高危发现):此前
# execute_promotion 自身对"同一 request_id 被并发调用两次"没有任何保护——lock_path 是可选
# 参数,默认 None 时 _SingleFlight 是纯 no-op;两个并发调用者各自 load() 到同一份 APPROVED
# 请求、各自 consume() 出内容逐字节相同的 CONSUMED 步骤(entry_hash 是 state/actor/timestamp/
# note/prev_hash 的纯函数,无随机/无墙钟),ApprovalStore.save() 的严格前缀检查只在"分叉"时
# raise ApprovalStoreConflict,对"完全相同的追加"是无声的 no-op——于是两次 advance()+
# registry.save()+PROMOTION_EXECUTED 审计会各自独立发生,一次性 grant 被实际执行两次。
# 唯一现有的调用方(promotion_api)恰好总是传 lock_path 且额外加了进程内 _EXEC_LOCK,今天
# 生产环境不可达——但这是调用方的自觉,不是本函数自身的保证。这里加一把按 request_id 分片
# 的进程内锁,让 execute_promotion 本身对"同一进程内的并发调用"天然安全,不再依赖调用方
# 记得传 lock_path;lock_path 仍然保留、语义不变,是额外的跨进程(多 worker)保护。
_REQUEST_LOCKS: Dict[str, threading.Lock] = {}
_REQUEST_LOCKS_META = threading.Lock()


def _request_lock(request_id: str) -> threading.Lock:
    with _REQUEST_LOCKS_META:
        lock = _REQUEST_LOCKS.get(request_id)
        if lock is None:
            lock = threading.Lock()
            _REQUEST_LOCKS[request_id] = lock
        return lock

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
    eval_trial_set_hash: Optional[str] = None  # exact catalog population bound at evaluation time
    eval_catalog_evidence_hash: Optional[str] = None
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
    event_outbox_id: str = ""


def gather_promotion_evidence(registry, paper_store, alpha_id: str) -> PromotionEvidence:
    """采集晋级证据。fail-closed:artifact 缺失/被篡改 → 对应字段 None + 诚实 note,由
    guard 拒绝;绝不用请求体或默认值代填。registry/paper_store 只需鸭子类型
    (list_evaluations/verify_evaluation、track)。"""
    notes: list[str] = []
    bt: Optional[float] = None
    etc: Optional[int] = None
    etsh: Optional[str] = None
    catalog_evidence_hash: Optional[str] = None
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
            etsh = payload.get("catalog_trial_set_hash")
            catalog_evidence_hash = payload.get("catalog_evidence_hash")
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
                             eval_trial_set_hash=etsh,
                             eval_catalog_evidence_hash=catalog_evidence_hash,
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
    烧毁之后一律返回 PromotionOutcome(burned=True)且必落审计。

    **进程内单飞**(2026-07-06 对抗式复审 CONFIRMED 修复,见模块顶部 `_REQUEST_LOCKS` 注释):
    按 `request_id` 分片的锁,非阻塞获取——同一进程内对同一 request_id 的第二次并发调用
    直接 `PromotionPrecheckError`(grant 未烧,可安全重试),不再依赖调用方记得传 `lock_path`。
    `lock_path` 仍然保留、语义不变,是额外的跨进程(多 worker)保护。已知的有界内存足迹:
    `_REQUEST_LOCKS` 按 request_id 累积、进程生命周期内不回收——人工晋级审批量级下可忽略,
    不是需要立即解决的问题。"""
    process_lock = _request_lock(request_id)
    if not process_lock.acquire(blocking=False):
        raise PromotionPrecheckError(
            f"PROMOTION_IN_PROGRESS: another execution of {request_id!r} is already running "
            f"in this process — grant not burned, safe to retry once it completes")
    try:
        return _execute_promotion_locked(
            store=store, registry=registry, audit=audit, request_id=request_id,
            executor_id=executor_id, now=now, paper_store=paper_store, config=config,
            nonce=nonce, lock_path=lock_path)
    finally:
        process_lock.release()


def _execute_promotion_locked(*, store, registry, audit, request_id: str, executor_id: str,
                              now: int, paper_store=None,
                              config: Optional[AlphaFactoryConfig] = None, nonce: str = "",
                              lock_path: Optional[str] = None) -> PromotionOutcome:
    """`execute_promotion` 的真正实现,已持有进程内单飞锁——原逻辑一字不动。"""
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

        # Model card gate (VALIDATION.CHALLENGER.MODEL_RISK M1→M2): 向 APPROVED 晋级必须有
        # model_card 文件(缺失抛 ModelCardMissing → PromotionPrecheckError,不烧 grant)。
        # 其他晋级路径(→TESTING/VALIDATED/PAPER_TRADING)不需要 model card(那些已有
        # agent/评估路径,见 PROMOTABLE_DSTS 注释)。
        if dst == AlphaState.APPROVED:
            try:
                verify_model_card_exists(alpha_id)
            except Exception as exc:
                raise PromotionPrecheckError(f"MODEL_CARD_GATE_FAILED: {exc}")

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
                            eval_trial_count=evidence.eval_trial_count,
                            eval_trial_set_hash=evidence.eval_trial_set_hash,
                            eval_catalog_evidence_hash=(
                                evidence.eval_catalog_evidence_hash
                            ))
            promotion_event = alpha_promoted_event(
                request_id=request_id,
                alpha_id=alpha_id,
                src=src.value,
                dst=dst.value,
                actor_id=executor_id,
                grant_head_hash=consumed.head_hash,
                eval_run_id=evidence.eval_run_id,
                evidence_notes=evidence.notes,
                at_ms=now,
            )
            CANONICAL_EVENT_SCHEMAS.validate(promotion_event)
            registry.save(
                catalog,
                now=now,
                event_outbox=(
                    EventOutboxMessage(
                        event=promotion_event,
                        correlation_id=request_id,
                        causation_id=consumed.head_hash,
                    ),
                ),
            )
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
                                audit_ok=audit_ok, evidence_notes=evidence.notes,
                                event_outbox_id=promotion_event.event_id)
    finally:
        lock.release()
