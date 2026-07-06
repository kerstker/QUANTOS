"""promotion_api —— 研究晋级(→APPROVED)的多方批准操作面(端口 8460,独立 mutating 服务)。

三个不同真人经前端 PromotionQueue 页走 REQUESTED→REVIEWED→APPROVED→CONSUMED
(services/governance/approval.py 状态机,零改动),每步 append-only 落
`data/cache/approvals.db`(approval_store.py,哈希链 + 链头承诺),execute 由
services/alpha/promotion.py::execute_promotion 兑现(先烧 grant 再推进、证据只来自真实
artifact、烧毁后全路径审计)。console_api 保持 GET-only、research_runner 保持
research-only、live/ 与五个真钱旗帜零接触——APPROVED 只是 alpha 生命周期状态(paper/干跑
部署资格),LIVE 仍被 readiness pin + LIVE flags 结构性挡死,grant 合法也不解锁 LIVE。

**威胁模型(评审 #6,诚实化——读完再决定要不要启动本服务):**

本 API 把「受信代码在进程内凑三个 HUMAN Actor」这个 build-time 绕过面,变成了 **runtime
HTTP 能力**:持 gov token 的任何本机进程——**包括 AI agent**——可以用三个自报的
X-Operator 标签走完三方批准并执行晋级,零代码变更、零 code-review 痕迹。X-Operator 是
自报问责标签、不是身份认证;「三个不同真人」在单机工作站信任模型下 = 三个两两不同的标签。
它挡住的是流程遗漏和无 token 进程,挡不住蓄意的人(或持 token 的 agent)打三个名字——
真身份认证/出进程签名 = Phase 14(与 approval.py 既有注脚一致)。缓解(全部到位才可启动):

- token 文件 `key/governance_token.txt`(首次启动生成,gitignored,与 key/ 目录既有口径
  一致);**AI agent 运行环境不应持有该 token**;
- 每个 mutating 步骤 + 执行结果都落 DurableAuditLog(`data/cache/governance_audit.db`)
  哈希链,与 approvals.db 双库交叉可对账;
- **本服务默认不启动**:不进任何自启脚本/调度任务,由用户读过本说明后自己拉起:
  `python -m uvicorn services.governance.promotion_api.main:app --host 127.0.0.1 --port 8460`
  (必须单进程单 worker——ApprovalStore 的跨进程兜底只保证不写坏,不保证多 worker 体验);
- CORS 仅 localhost:3100;响应永不含 token 值;GET 数据端点也要 token(/health 除外,静态)。

设计+对抗式评审记录:docs/superpowers/specs/2026-07-06-promotion-closed-loop-design.md。
墙钟与 request_id 的 uuid 只在此服务边界产生,核心(approval/approval_store/promotion)纯。
绝不 import 券商/网络 SDK、不 import live/、不 import tools/(治理测试
tests/governance/test_promotion_api_boundary.py 机器钉死)。"""
from __future__ import annotations

import os
import secrets
import threading
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from quantos.contracts.alpha import Actor, ActorKind, can_alpha_transition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.guards import promotion_subject
from services.alpha.promotion import (
    PROMOTION_KIND, PromotionPrecheckError, execute_promotion, gather_promotion_evidence,
)
from services.governance.approval import (
    ApprovalViolation, approve, open_request, reject, review,
)
from services.governance.approval_store import ApprovalStore, ApprovalStoreError
from services.monitoring.audit_store import DurableAuditLog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.paper_trading.store import PaperTrackStore

_APPROVALS_DB = os.path.join("data", "cache", "approvals.db")
_EXP_DB = os.path.join("data", "cache", "experiment_registry.db")
_PAPER_DB = os.path.join("data", "cache", "paper_track.db")
_AUDIT_DB = os.path.join("data", "cache", "governance_audit.db")
_TOKEN_FILE = os.path.join("key", "governance_token.txt")

# 晋级候选的来源状态(→APPROVED 在状态机里合法的三个 src)
_CANDIDATE_STATES = (AlphaState.PAPER_TRADING, AlphaState.SHADOW, AlphaState.PAUSED)


def _load_or_create_token(path: str = _TOKEN_FILE) -> str:
    # 镜像 research_runner._load_or_create_token(2026-07-06 punch-list 落地的同款模式)
    if os.path.exists(path):
        tok = open(path, encoding="utf-8").read().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tok)
    return tok


_GOV_TOKEN = _load_or_create_token()


def require_gov_token(x_gov_token: Optional[str] = Header(default=None)) -> None:
    if x_gov_token is None or not secrets.compare_digest(x_gov_token, _GOV_TOKEN):
        raise HTTPException(401, "missing or invalid X-Gov-Token")


app = FastAPI(title="QUANTOS Promotion API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_EXEC_LOCK = threading.Lock()      # 进程内单飞(防前端双击);跨进程靠 promotion.py 的文件锁
_PREFIX = "/api/v1/governance/promotion"


# --- 依赖(惰性打开,路径为模块级常量 → 测试可 monkeypatch)----------------------

def _store() -> ApprovalStore:
    return ApprovalStore(_APPROVALS_DB)


def _audit() -> DurableAuditLog:
    return DurableAuditLog(_AUDIT_DB)


def _registry() -> Optional[ExperimentRegistry]:
    if not os.path.exists(_EXP_DB):
        return None
    return ExperimentRegistry(SqliteExperimentStore(_EXP_DB, create=False))


def _paper() -> Optional[PaperTrackStore]:
    try:
        return PaperTrackStore(_PAPER_DB) if os.path.exists(_PAPER_DB) else None
    except Exception:
        return None                      # 缺表/损坏 → 无 paper 证据(guard 会拒),不 500


def _operator(x_operator: Optional[str]) -> str:
    op = (x_operator or "").strip()
    if not op:
        raise HTTPException(status_code=400, detail=(
            "X-Operator header required (self-asserted accountability tag, NOT authentication)"))
    return op


def _now() -> int:
    return int(time.time())


def _audit_step(action: str, request_id: str, operator: str, now: int, **meta) -> None:
    """尽力而为的双库交叉记账(权威状态在 approvals.db;审计断链不阻塞业务但绝不静默重复)。"""
    try:
        _audit().append(event_id=f"promotion.{action}.{request_id}.{now}.{uuid.uuid4().hex[:6]}",
                        actor=operator, action=action, timestamp=now, target=request_id,
                        metadata={k: str(v) for k, v in meta.items()})
    except Exception:
        pass


def _load_or_http(store: ApprovalStore, request_id: str):
    if store.get_summary(request_id) is None:
        raise HTTPException(404, f"unknown promotion request: {request_id}")
    try:
        return store.load(request_id)
    except ApprovalStoreError as exc:    # 篡改/损坏 → fail-closed 拒绝服务此请求
        raise HTTPException(409, f"approval store integrity failure: {exc}")


def _detail(store: ApprovalStore, request_id: str) -> dict:
    summary = store.get_summary(request_id)
    req = _load_or_http(store, request_id)
    return {**summary, "steps": [
        {"state": s.state.value, "actor_id": s.actor_id, "timestamp": s.timestamp, "note": s.note}
        for s in req.steps]}


# --- 端点 ------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    # 静态元数据(无数据、无 token 要求,镜像 research_runner)。诚实标注威胁模型扩张。
    return {"mode": "PROMOTION_API", "live_disallowed": True,
            "operator_is_accountability_tag_not_authn": True,
            "default_started": False}


class OpenBody(BaseModel):
    alpha_id: str
    note: str = ""


class NoteBody(BaseModel):
    note: str = ""


@app.get(_PREFIX + "/candidates", dependencies=[Depends(require_gov_token)])
async def candidates() -> dict:
    registry = _registry()
    if registry is None:
        return {"registry_present": False, "candidates": [],
                "note": "experiment registry absent (fresh checkout / CI)"}
    catalog = AlphaCatalog()
    try:
        registry.load_into(catalog)
    except Exception as exc:
        raise HTTPException(409, f"experiment registry unreadable: {exc}")
    paper = _paper()
    cc = catalog.global_trial_count()
    out = []
    for state in _CANDIDATE_STATES:
        for alpha_id in catalog.by_state(state):
            ev = gather_promotion_evidence(registry, paper, alpha_id)
            hints = list(ev.notes)       # guard 预检提示——只提示不放行(真拒绝在 guard)
            if ev.eval_trial_count is not None and ev.eval_trial_count < cc:
                hints.append("TRIAL_COUNT_UNDERSTATED")
            out.append({
                "alpha_id": alpha_id, "state": state.value,
                "evidence": {"backtest_sharpe": ev.backtest_sharpe,
                             "paper_sharpe": ev.paper_sharpe,
                             "eval_trial_count": ev.eval_trial_count,
                             "eval_run_id": ev.eval_run_id, "notes": list(ev.notes)},
                "precheck_hints": hints,
            })
    return {"registry_present": True, "candidates": out, "catalog_trial_count": cc}


@app.get(_PREFIX + "/requests", dependencies=[Depends(require_gov_token)])
async def list_requests() -> dict:
    return {"requests": _store().list_requests(kind=PROMOTION_KIND)}


@app.get(_PREFIX + "/requests/{request_id}", dependencies=[Depends(require_gov_token)])
async def request_detail(request_id: str) -> dict:
    return _detail(_store(), request_id)


@app.post(_PREFIX + "/requests", dependencies=[Depends(require_gov_token)])
async def open_promotion_request(body: OpenBody, x_operator: str = Header(default=None)) -> dict:
    operator = _operator(x_operator)
    now = _now()
    registry = _registry()
    if registry is None:
        raise HTTPException(409, "experiment registry absent — nothing to promote")
    catalog = AlphaCatalog()
    try:
        registry.load_into(catalog)
    except Exception as exc:
        raise HTTPException(409, f"experiment registry unreadable: {exc}")
    # 评审 #4②:open 时就校验 alpha 存在且当前状态能到 APPROVED——结构性不可能的请求不进队列
    if body.alpha_id not in catalog.all_ids():
        raise HTTPException(409, f"alpha not in catalog: {body.alpha_id}")
    src = catalog.state(body.alpha_id)
    if not can_alpha_transition(src, AlphaState.APPROVED):
        raise HTTPException(409, f"illegal transition: {src.value} -> APPROVED")
    request_id = f"promo-{uuid.uuid4().hex[:12]}"
    actor = Actor(actor_id=operator, kind=ActorKind.HUMAN)   # 自报标签,见模块 docstring 威胁模型
    try:
        req = open_request(request_id, promotion_subject(body.alpha_id, AlphaState.APPROVED),
                           actor, at=now, note=body.note)
        _store().save(req, kind=PROMOTION_KIND, now=now)
    except (ApprovalViolation, ApprovalStoreError) as exc:
        raise HTTPException(409, str(exc))
    _audit_step("PROMOTION_REQUEST_OPENED", request_id, operator, now,
                alpha_id=body.alpha_id, head_hash=req.head_hash)
    return {"request_id": request_id, "subject_id": req.subject_id, "state": req.state.value}


def _transition(request_id: str, x_operator: Optional[str], note: str, fn, action: str) -> dict:
    operator = _operator(x_operator)
    now = _now()
    store = _store()
    req = _load_or_http(store, request_id)
    actor = Actor(actor_id=operator, kind=ActorKind.HUMAN)
    try:
        new = fn(req, actor, at=now, note=note)
        store.save(new, now=now)
    except (ApprovalViolation, ApprovalStoreError) as exc:
        raise HTTPException(409, str(exc))
    _audit_step(action, request_id, operator, now, state=new.state.value, head_hash=new.head_hash)
    return _detail(store, request_id)


@app.post(_PREFIX + "/requests/{request_id}/review", dependencies=[Depends(require_gov_token)])
async def review_request(request_id: str, body: NoteBody, x_operator: str = Header(default=None)) -> dict:
    return _transition(request_id, x_operator, body.note, review, "PROMOTION_REQUEST_REVIEWED")


@app.post(_PREFIX + "/requests/{request_id}/approve", dependencies=[Depends(require_gov_token)])
async def approve_request(request_id: str, body: NoteBody, x_operator: str = Header(default=None)) -> dict:
    return _transition(request_id, x_operator, body.note, approve, "PROMOTION_REQUEST_APPROVED")


@app.post(_PREFIX + "/requests/{request_id}/reject", dependencies=[Depends(require_gov_token)])
async def reject_request(request_id: str, body: NoteBody, x_operator: str = Header(default=None)) -> dict:
    return _transition(request_id, x_operator, body.note, reject, "PROMOTION_REQUEST_REJECTED")


@app.post(_PREFIX + "/requests/{request_id}/execute", dependencies=[Depends(require_gov_token)])
async def execute_request(request_id: str, x_operator: str = Header(default=None)) -> dict:
    operator = _operator(x_operator)
    now = _now()
    store = _store()
    if store.get_summary(request_id) is None:
        raise HTTPException(404, f"unknown promotion request: {request_id}")
    registry = _registry()
    if registry is None:
        raise HTTPException(409, "experiment registry absent — cannot execute")
    if not _EXEC_LOCK.acquire(blocking=False):     # 进程内单飞:双击/并发 execute 直接拒
        raise HTTPException(409, "another execution is in progress")
    try:
        outcome = execute_promotion(
            store=store, registry=registry, audit=_audit(), request_id=request_id,
            executor_id=operator, now=now, paper_store=_paper(),
            nonce=uuid.uuid4().hex[:8],
            lock_path=_EXP_DB + ".promotion.lock")   # 评审 #7:experiments db 文件级单飞
    except PromotionPrecheckError as exc:
        raise HTTPException(409, exc.reason)         # grant 未烧,修正后可重试
    except (ApprovalViolation, ApprovalStoreError) as exc:
        raise HTTPException(409, str(exc))
    finally:
        _EXEC_LOCK.release()
    return {"ok": outcome.ok, "request_id": outcome.request_id, "alpha_id": outcome.alpha_id,
            "dst": outcome.dst, "burned": outcome.burned, "reasons": list(outcome.reasons),
            "audit_ok": outcome.audit_ok, "evidence_notes": list(outcome.evidence_notes)}
