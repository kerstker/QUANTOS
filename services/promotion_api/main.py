"""Research-promotion API protected by strong external identity.

This HTTP composition root intentionally lives outside the governance domain:
governance owns policy and approval primitives, while this adapter assembles
governance, alpha, research, and monitoring services.

Three distinct externally verified OIDC subjects walk
REQUESTED -> REVIEWED -> APPROVED -> CONSUMED. Every step is append-only in
`data/cache/approvals.db`; execution consumes the one-way grant before advancing
the research alpha lifecycle. APPROVED is still research/paper eligibility and
never enables live trading.

Authentication is load-bearing:

- access tokens require an allowlisted asymmetric OIDC signature, exact issuer
  and audience, fresh MFA, and RFC 8705 certificate binding;
- the client certificate must come from a TLS-server-verified ASGI scope;
- request, review, approve, execute, and read actions require both IdP role
  claims and an active application-owned authorization grant;
- actor IDs are derived only from immutable issuer + subject claims;
- revocation, token epoch, HUMAN/WORKLOAD class, and access-review expiry are
  enforced from an append-only local authorization authority;
- no local shared secret or request-supplied actor label is accepted;
- missing external IdP policy or trusted mTLS context fails closed.

The service remains off by default and requires an external OIDC public-key
policy plus a trusted mTLS-capable ASGI deployment. Every mutation is mirrored
to the durable hash-chained governance audit log. CORS remains localhost-only;
`/health` is the sole unauthenticated static endpoint.

The core remains offline and imports no broker/network SDK, `live/`, or `tools/`.
See `docs/engineering/specs/2026-07-12-strong-identity-baseline-design.md`.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from quantos.contracts.alpha import Actor, ActorKind, can_alpha_transition
from quantos.contracts.identity import GovernanceRole, VerifiedIdentity
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.guards import promotion_subject
from services.alpha.promotion import (
    PROMOTION_KIND,
    PromotionPrecheckError,
    execute_promotion,
    gather_promotion_evidence,
)
from services.governance.approval import (
    ApprovalViolation,
    approve,
    open_request,
    reject,
    review,
)
from services.governance.approval_store import ApprovalStore, ApprovalStoreError
from services.governance.identity_authorization import (
    evaluate_authorization,
    load_identity_authorization_registry,
)
from services.governance.identity_authorization_store import (
    IdentityAuthorizationStore,
    IdentityAuthorizationStoreError,
    IdentityAuthorizationStoreRegistryHeadChanged,
    IdentityAuthorizationStoreRegistryUnprovisioned,
)
from services.governance.strong_identity import (
    OidcMtlsVerifier,
    StrongIdentityError,
    StrongIdentityUnavailable,
    extract_verified_client_certificate,
    load_oidc_policy_from_environment,
)
from services.monitoring.audit_store import DurableAuditLog
from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_DB,
    dispatch_experiment_event_outbox,
    open_domain_event_bus,
    project_pending_domain_events,
)
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.paper_trading.store import PaperTrackStore

_APPROVALS_DB = os.path.join("data", "cache", "approvals.db")
_EXP_DB = os.path.join("data", "cache", "experiment_registry.db")
_PAPER_DB = os.path.join("data", "cache", "paper_track.db")
_AUDIT_DB = os.path.join("data", "cache", "governance_audit.db")
_IDENTITY_AUTHORIZATION_DB = os.path.join("data", "cache", "identity_authorization.db")
_IDENTITY_AUTHORIZATION_REGISTRY = os.path.join("config", "identity_authorization_registry.json")
_EVENT_DB = DOMAIN_EVENT_DB
_EVENT_PROJECTION_DB = DOMAIN_EVENT_PROJECTION_DB

# 晋级候选的来源状态(→APPROVED 在状态机里合法的三个 src)
_CANDIDATE_STATES = (AlphaState.PAPER_TRADING, AlphaState.SHADOW, AlphaState.PAUSED)


def _get_identity_verifier() -> OidcMtlsVerifier:
    return OidcMtlsVerifier(load_oidc_policy_from_environment())


def _bearer_token(authorization: Optional[str]) -> str:
    if not isinstance(authorization, str):
        raise HTTPException(401, "strong identity rejected: MISSING_TOKEN")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(401, "strong identity rejected: INVALID_BEARER_TOKEN")
    return parts[1]


def require_governance_identity(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> VerifiedIdentity:
    token = _bearer_token(authorization)
    try:
        certificate = extract_verified_client_certificate(request.scope)
        verifier = _get_identity_verifier()
        return verifier.verify(token, client_certificate_der=certificate, now=_now())
    except StrongIdentityUnavailable as exc:
        raise HTTPException(503, f"strong identity unavailable: {exc.code}") from exc
    except StrongIdentityError as exc:
        raise HTTPException(401, f"strong identity rejected: {exc.code}") from exc


def _role_dependency(role: GovernanceRole):
    def dependency(
        identity: VerifiedIdentity = Depends(require_governance_identity),
    ) -> VerifiedIdentity:
        store: IdentityAuthorizationStore | None = None
        try:
            store = IdentityAuthorizationStore(
                _IDENTITY_AUTHORIZATION_DB,
                create=False,
            )
            snapshot = store.read_snapshot()
            head = snapshot.registry_head
            if head is None:
                raise IdentityAuthorizationStoreRegistryUnprovisioned("REGISTRY_UNPROVISIONED")
            mirror_path = Path(_IDENTITY_AUTHORIZATION_REGISTRY)
            if mirror_path.is_symlink() or not mirror_path.is_file():
                raise IdentityAuthorizationStoreError("identity authorization registry mirror is unavailable")
            mirror_bytes = mirror_path.read_bytes()
            if mirror_bytes != head.registry_bytes:
                raise IdentityAuthorizationStoreError("identity authorization registry mirror mismatch")
            registry = load_identity_authorization_registry(head.registry_bytes)
            evaluated_at_ms = _now() * 1_000
            decision = evaluate_authorization(
                registry,
                identity,
                role,
                evaluated_at_ms=evaluated_at_ms,
            )
            try:
                store.record(
                    decision,
                    recorded_at_ms=evaluated_at_ms,
                    expected_registry_head=head,
                )
            except IdentityAuthorizationStoreRegistryHeadChanged:
                current = store.registry_head()
                denied = decision.model_copy(
                    update={
                        "verdict": "DENY",
                        "reason_code": ("REGISTRY_HEAD_CHANGED_DURING_AUTHORIZATION"),
                        "registry_generation": current.generation,
                    }
                )
                store.record(
                    denied,
                    recorded_at_ms=max(
                        evaluated_at_ms,
                        current.recorded_at_ms,
                    ),
                    expected_registry_head=current,
                )
                raise HTTPException(
                    503,
                    "identity authorization unavailable: registry changed",
                )
        except HTTPException:
            raise
        except (IdentityAuthorizationStoreError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(
                503,
                "identity authorization unavailable",
            ) from exc
        finally:
            if store is not None:
                store.close()
        if decision.verdict != "ALLOW":
            raise HTTPException(
                403,
                f"identity authorization forbidden: {decision.reason_code}",
            )
        return identity

    return dependency


require_promotion_reader = _role_dependency(GovernanceRole.PROMOTION_READER)
require_promotion_requester = _role_dependency(GovernanceRole.PROMOTION_REQUESTER)
require_promotion_reviewer = _role_dependency(GovernanceRole.PROMOTION_REVIEWER)
require_promotion_approver = _role_dependency(GovernanceRole.PROMOTION_APPROVER)
require_promotion_executor = _role_dependency(GovernanceRole.PROMOTION_EXECUTOR)


app = FastAPI(title="QUANTOS Promotion API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_EXEC_LOCK = threading.Lock()  # 进程内单飞(防前端双击);跨进程靠 promotion.py 的文件锁
_PREFIX = "/api/v1/governance/promotion"


# --- 依赖(惰性打开,路径为模块级常量 → 测试可 monkeypatch)----------------------


def _store() -> ApprovalStore:
    return ApprovalStore(_APPROVALS_DB)


def _audit() -> DurableAuditLog:
    return DurableAuditLog(_AUDIT_DB)


def _registry() -> Optional[ExperimentRegistry]:
    if not os.path.exists(_EXP_DB):
        return None
    return ExperimentRegistry(SqliteExperimentStore(_EXP_DB, create=True))


def _deliver_promotion_events(registry: ExperimentRegistry, *, now: int) -> dict:
    bus = None
    try:
        bus = open_domain_event_bus(_EVENT_DB)
        dispatch = dispatch_experiment_event_outbox(
            registry,
            bus,
            at_ms=now * 1_000,
        )
        projection = project_pending_domain_events(
            bus,
            now_ms=now * 1_000,
            projection_path=_EVENT_PROJECTION_DB,
            worker_id="promotion-api-domain-event-projection",
        )
        return {"ok": True, "dispatch": dispatch, "projection": projection}
    except Exception as exc:
        try:
            pending = len(registry.pending_event_outbox())
        except Exception:
            pending = None
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "pending_outbox": pending,
        }
    finally:
        if bus is not None:
            bus.close()


def _paper() -> Optional[PaperTrackStore]:
    try:
        return PaperTrackStore(_PAPER_DB) if os.path.exists(_PAPER_DB) else None
    except Exception:
        return None  # 缺表/损坏 → 无 paper 证据(guard 会拒),不 500


def _now() -> int:
    return int(time.time())


def _audit_step(action: str, request_id: str, operator: str, now: int, **meta) -> None:
    """尽力而为的双库交叉记账(权威状态在 approvals.db;审计断链不阻塞业务但绝不静默重复)。"""
    try:
        _audit().append(
            event_id=f"promotion.{action}.{request_id}.{now}.{uuid.uuid4().hex[:6]}",
            actor=operator,
            action=action,
            timestamp=now,
            target=request_id,
            metadata={k: str(v) for k, v in meta.items()},
        )
    except Exception:
        pass


def _load_or_http(store: ApprovalStore, request_id: str):
    if store.get_summary(request_id) is None:
        raise HTTPException(404, f"unknown promotion request: {request_id}")
    try:
        return store.load(request_id)
    except ApprovalStoreError as exc:  # 篡改/损坏 → fail-closed 拒绝服务此请求
        raise HTTPException(409, f"approval store integrity failure: {exc}")


def _detail(store: ApprovalStore, request_id: str) -> dict:
    summary = store.get_summary(request_id)
    req = _load_or_http(store, request_id)
    return {
        **summary,
        "steps": [
            {"state": s.state.value, "actor_id": s.actor_id, "timestamp": s.timestamp, "note": s.note}
            for s in req.steps
        ],
    }


# --- 端点 ------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {
        "mode": "PROMOTION_API",
        "live_disallowed": True,
        "authentication": "OIDC_MTLS_MFA_APP_AUTHORIZATION",
        "legacy_shared_token_accepted": False,
        "self_asserted_operator_accepted": False,
        "default_started": False,
    }


class OpenBody(BaseModel):
    alpha_id: str
    note: str = ""


class NoteBody(BaseModel):
    note: str = ""


@app.get(_PREFIX + "/candidates")
async def candidates(
    _identity: VerifiedIdentity = Depends(require_promotion_reader),
) -> dict:
    registry = _registry()
    if registry is None:
        return {"registry_present": False, "candidates": [], "note": "experiment registry absent (fresh checkout / CI)"}
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
            hints = list(ev.notes)  # guard 预检提示——只提示不放行(真拒绝在 guard)
            if ev.eval_trial_count is not None and ev.eval_trial_count < cc:
                hints.append("TRIAL_COUNT_UNDERSTATED")
            out.append(
                {
                    "alpha_id": alpha_id,
                    "state": state.value,
                    "evidence": {
                        "backtest_sharpe": ev.backtest_sharpe,
                        "paper_sharpe": ev.paper_sharpe,
                        "eval_trial_count": ev.eval_trial_count,
                        "eval_run_id": ev.eval_run_id,
                        "notes": list(ev.notes),
                    },
                    "precheck_hints": hints,
                }
            )
    return {"registry_present": True, "candidates": out, "catalog_trial_count": cc}


@app.get(_PREFIX + "/requests")
async def list_requests(
    _identity: VerifiedIdentity = Depends(require_promotion_reader),
) -> dict:
    return {"requests": _store().list_requests(kind=PROMOTION_KIND)}


@app.get(_PREFIX + "/requests/{request_id}")
async def request_detail(
    request_id: str,
    _identity: VerifiedIdentity = Depends(require_promotion_reader),
) -> dict:
    return _detail(_store(), request_id)


@app.post(_PREFIX + "/requests")
async def open_promotion_request(
    body: OpenBody,
    identity: VerifiedIdentity = Depends(require_promotion_requester),
) -> dict:
    operator = identity.actor_id
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
    actor = Actor(actor_id=operator, kind=ActorKind.HUMAN)  # 自报标签,见模块 docstring 威胁模型
    try:
        req = open_request(
            request_id, promotion_subject(body.alpha_id, AlphaState.APPROVED), actor, at=now, note=body.note
        )
        _store().save(req, kind=PROMOTION_KIND, now=now)
    except (ApprovalViolation, ApprovalStoreError) as exc:
        raise HTTPException(409, str(exc))
    _audit_step("PROMOTION_REQUEST_OPENED", request_id, operator, now, alpha_id=body.alpha_id, head_hash=req.head_hash)
    return {"request_id": request_id, "subject_id": req.subject_id, "state": req.state.value}


def _transition(
    request_id: str,
    identity: VerifiedIdentity,
    note: str,
    fn,
    action: str,
) -> dict:
    operator = identity.actor_id
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


@app.post(_PREFIX + "/requests/{request_id}/review")
async def review_request(
    request_id: str,
    body: NoteBody,
    identity: VerifiedIdentity = Depends(require_promotion_reviewer),
) -> dict:
    return _transition(request_id, identity, body.note, review, "PROMOTION_REQUEST_REVIEWED")


@app.post(_PREFIX + "/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: NoteBody,
    identity: VerifiedIdentity = Depends(require_promotion_approver),
) -> dict:
    return _transition(request_id, identity, body.note, approve, "PROMOTION_REQUEST_APPROVED")


@app.post(_PREFIX + "/requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: NoteBody,
    identity: VerifiedIdentity = Depends(require_promotion_reviewer),
) -> dict:
    return _transition(request_id, identity, body.note, reject, "PROMOTION_REQUEST_REJECTED")


@app.post(_PREFIX + "/requests/{request_id}/execute")
async def execute_request(
    request_id: str,
    identity: VerifiedIdentity = Depends(require_promotion_executor),
) -> dict:
    operator = identity.actor_id
    now = _now()
    store = _store()
    if store.get_summary(request_id) is None:
        raise HTTPException(404, f"unknown promotion request: {request_id}")
    registry = _registry()
    if registry is None:
        raise HTTPException(409, "experiment registry absent — cannot execute")
    if not _EXEC_LOCK.acquire(blocking=False):  # 进程内单飞:双击/并发 execute 直接拒
        raise HTTPException(409, "another execution is in progress")
    try:
        outcome = execute_promotion(
            store=store,
            registry=registry,
            audit=_audit(),
            request_id=request_id,
            executor_id=operator,
            now=now,
            paper_store=_paper(),
            nonce=uuid.uuid4().hex[:8],
            lock_path=_EXP_DB + ".promotion.lock",
        )  # 评审 #7:experiments db 文件级单飞
    except PromotionPrecheckError as exc:
        raise HTTPException(409, exc.reason)  # grant 未烧,修正后可重试
    except (ApprovalViolation, ApprovalStoreError) as exc:
        raise HTTPException(409, str(exc))
    finally:
        _EXEC_LOCK.release()
    event_delivery = (
        _deliver_promotion_events(registry, now=now)
        if outcome.ok and outcome.event_outbox_id
        else {"ok": True, "dispatch": {"dispatched": 0, "pending": 0}}
    )
    return {
        "ok": outcome.ok,
        "request_id": outcome.request_id,
        "alpha_id": outcome.alpha_id,
        "dst": outcome.dst,
        "burned": outcome.burned,
        "reasons": list(outcome.reasons),
        "audit_ok": outcome.audit_ok,
        "evidence_notes": list(outcome.evidence_notes),
        "event_outbox_id": outcome.event_outbox_id,
        "event_delivery": event_delivery,
    }
