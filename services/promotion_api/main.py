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
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from quantos.contracts.alpha import Actor, ActorKind, can_alpha_transition
from quantos.contracts.hashing import stable_hash
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
from services.orchestration.command_bus import (
    CommandBus,
    CommandBusError,
    StateVersionConflictError,
)
from services.orchestration.command_envelope import CommandEnvelope
from services.orchestration.idempotency import (
    CommandConflictError,
    CommandLogError,
    CommandStateError,
    SqliteCommandLog,
)
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.paper_trading.store import PaperTrackStore

_APPROVALS_DB = os.path.join("data", "cache", "approvals.db")
_EXP_DB = os.path.join("data", "cache", "experiment_registry.db")
_PAPER_DB = os.path.join("data", "cache", "paper_track.db")
_AUDIT_DB = os.path.join("data", "cache", "governance_audit.db")
_COMMAND_DB = os.path.join("data", "cache", "promotion_commands.db")
_IDENTITY_AUTHORIZATION_DB = os.path.join("data", "cache", "identity_authorization.db")
_IDENTITY_AUTHORIZATION_REGISTRY = os.path.join("config", "identity_authorization_registry.json")
_EVENT_DB = DOMAIN_EVENT_DB
_EVENT_PROJECTION_DB = DOMAIN_EVENT_PROJECTION_DB

# 晋级候选的来源状态(→APPROVED 在状态机里合法的三个 src)
_CANDIDATE_STATES = (AlphaState.PAPER_TRADING, AlphaState.SHADOW, AlphaState.PAUSED)

_COMMAND_TYPE = "PROMOTION_GOVERNANCE"
_COMMAND_ROLES = {
    "OPEN_PROMOTION": GovernanceRole.PROMOTION_REQUESTER,
    "REVIEW_PROMOTION": GovernanceRole.PROMOTION_REVIEWER,
    "APPROVE_PROMOTION": GovernanceRole.PROMOTION_APPROVER,
    "REJECT_PROMOTION": GovernanceRole.PROMOTION_REVIEWER,
    "EXECUTE_PROMOTION": GovernanceRole.PROMOTION_EXECUTOR,
}


class CommandAuditUnavailable(RuntimeError):
    """The durable governance audit could not accept the command intent."""


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


def _append_command_audit(
    audit: DurableAuditLog,
    *,
    envelope: CommandEnvelope,
    action: str,
    target_id: str,
    now: int,
):
    """Persist the command intent before any governance side effect.

    This write is deliberately fail-closed.  A command with no durable audit
    intent is never allowed to open, transition, consume, or promote anything.
    """
    try:
        return audit.append(
            event_id=f"promotion.command.{envelope.idempotency_key}",
            actor=envelope.actor.actor_id,
            action=f"{action}_COMMAND_ACCEPTED",
            timestamp=now,
            target=target_id,
            metadata={
                "command_id": envelope.command_id,
                "idempotency_key": envelope.idempotency_key,
                "payload_hash": envelope.payload_hash,
                "target_version": str(envelope.expected_state_version),
                "business_reason": str(envelope.payload["business_reason"]),
                "preview_hash": str(envelope.payload["preview_hash"]),
            },
        )
    except Exception as exc:
        raise CommandAuditUnavailable("durable governance audit unavailable") from exc


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
        "target_version": summary["n_steps"],
        "steps": [
            {"state": s.state.value, "actor_id": s.actor_id, "timestamp": s.timestamp, "note": s.note}
            for s in req.steps
        ],
    }


def _catalog_or_http(*, absent_message: str) -> tuple[ExperimentRegistry, AlphaCatalog]:
    registry = _registry()
    if registry is None:
        raise HTTPException(409, absent_message)
    catalog = AlphaCatalog()
    try:
        registry.load_into(catalog)
    except Exception as exc:
        raise HTTPException(409, f"experiment registry unreadable: {exc}") from exc
    return registry, catalog


def _alpha_target_version(catalog: AlphaCatalog, alpha_id: str) -> int:
    """Deterministic optimistic version bound to the alpha identity and state."""
    definition = catalog.get(alpha_id)
    digest = stable_hash(
        {
            "alpha_id": alpha_id,
            "definition_hash": definition.definition_hash,
            "state": catalog.state(alpha_id).value,
        }
    )
    # 13 hex digits fit inside JavaScript's exact integer range (< 2**53).
    return int(digest[:13], 16)


def _impact(action: str, *, target_id: str, from_state: str) -> tuple[dict, list[str]]:
    approval_state = {
        "OPEN_PROMOTION": "REQUESTED",
        "REVIEW_PROMOTION": "REVIEWED",
        "APPROVE_PROMOTION": "APPROVED",
        "REJECT_PROMOTION": "REJECTED",
        "EXECUTE_PROMOTION": "CONSUMED",
    }[action]
    expected = {
        "scope": "RESEARCH_PROMOTION_ONLY",
        "target_id": target_id,
        "approval_state": approval_state,
        "research_state": "APPROVED" if action == "EXECUTE_PROMOTION" else "UNCHANGED",
        "live_trading_authorized": False,
        "order_submission_allowed": False,
        "capital_scaling_allowed": False,
        "risk_limit_change": False,
        "kill_switch_reset": False,
    }
    risk_diff = [f"approval_state: {from_state} -> {approval_state}"]
    if action == "EXECUTE_PROMOTION":
        risk_diff.append("research_state: current -> APPROVED")
    risk_diff.extend(
        (
            "live_trading_authorized: false -> false",
            "order_submission_allowed: false -> false",
        )
    )
    return expected, risk_diff


def _preview_payload(
    action: str,
    target_id: str,
    business_reason: str,
    identity: VerifiedIdentity,
) -> dict:
    action = action.strip().upper()
    target_id = target_id.strip()
    business_reason = business_reason.strip()
    if action not in _COMMAND_ROLES:
        raise HTTPException(422, "unsupported promotion command action")
    if not target_id:
        raise HTTPException(422, "target_id must be non-empty")
    if not business_reason:
        raise HTTPException(422, "business_reason must be non-empty")

    if action == "OPEN_PROMOTION":
        _registry_value, catalog = _catalog_or_http(
            absent_message="experiment registry absent — nothing to promote"
        )
        if target_id not in catalog.all_ids():
            raise HTTPException(409, f"alpha not in catalog: {target_id}")
        source = catalog.state(target_id)
        if not can_alpha_transition(source, AlphaState.APPROVED):
            raise HTTPException(409, f"illegal transition: {source.value} -> APPROVED")
        target_version = _alpha_target_version(catalog, target_id)
        from_state = "ABSENT"
    else:
        summary = _store().get_summary(target_id)
        if summary is None:
            raise HTTPException(404, f"unknown promotion request: {target_id}")
        _load_or_http(_store(), target_id)
        target_version = int(summary["n_steps"])
        from_state = str(summary["state"])

    expected_impact, risk_diff = _impact(
        action,
        target_id=target_id,
        from_state=from_state,
    )
    core = {
        "action": action,
        "target_id": target_id,
        "target_version": target_version,
        "business_reason": business_reason,
        "verified_actor_id": identity.actor_id,
        "expected_impact": expected_impact,
        "risk_diff": risk_diff,
    }
    return {
        **core,
        "required_role": _COMMAND_ROLES[action].value,
        "strong_identity": identity.assurance.value,
        "preview_hash": stable_hash(core),
    }


def _is_uuid4(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def _command_core(
    action: str,
    target_id: str,
    body: "CommandBody",
    identity: VerifiedIdentity,
) -> dict:
    return {
        "action": action,
        "target_id": target_id,
        "target_version": body.target_version,
        "business_reason": body.business_reason.strip(),
        "verified_actor_id": identity.actor_id,
        "expected_impact": body.expected_impact,
        "risk_diff": body.risk_diff,
    }


def _command_envelope(
    action: str,
    target_id: str,
    body: "CommandBody",
    identity: VerifiedIdentity,
    *,
    now: int,
) -> CommandEnvelope:
    core = _command_core(action, target_id, body, identity)
    payload = {**core, "preview_hash": body.preview_hash}
    return CommandEnvelope.create(
        command_id=f"promotion-command-{body.idempotency_key}",
        command_type=_COMMAND_TYPE,
        requested_at=now * 1_000,
        actor=Actor(actor_id=identity.actor_id, kind=ActorKind.HUMAN),
        subject=target_id,
        idempotency_key=body.idempotency_key,
        expected_state_version=body.target_version,
        authorization_ref=identity.token_id,
        payload=payload,
    )


def _authority_requery(action: str, target_id: str, outcome: dict) -> dict:
    request_id = outcome["request_id"] if action == "OPEN_PROMOTION" else target_id
    detail = _detail(_store(), request_id)
    authority = {
        "request_id": request_id,
        "approval_state": detail["state"],
        "target_version": detail["target_version"],
        "n_steps": detail["n_steps"],
    }
    if action == "EXECUTE_PROMOTION":
        _registry_value, catalog = _catalog_or_http(
            absent_message="experiment registry absent — cannot verify execution"
        )
        alpha_id = outcome["alpha_id"]
        authority["alpha_id"] = alpha_id
        authority["research_state"] = catalog.state(alpha_id).value
        authority["alpha_target_version"] = _alpha_target_version(catalog, alpha_id)
    return authority


def _run_command(
    *,
    action: str,
    target_id: str,
    body: "CommandBody",
    identity: VerifiedIdentity,
    mutation: Callable[[DurableAuditLog], dict],
) -> dict:
    now = _now()
    action = action.strip().upper()
    target_id = target_id.strip()
    if not _is_uuid4(body.idempotency_key):
        raise HTTPException(422, "idempotency_key must be a random RFC 4122 version-4 UUID")
    if not body.business_reason.strip():
        raise HTTPException(422, "business_reason must be non-empty")
    core = _command_core(action, target_id, body, identity)
    envelope = _command_envelope(action, target_id, body, identity, now=now)
    log: SqliteCommandLog | None = None
    try:
        log = SqliteCommandLog(_COMMAND_DB, create=True)
        bus = CommandBus(log)
        existing = log.find(_COMMAND_TYPE, body.idempotency_key)
        if existing is not None:
            return bus.execute(
                envelope,
                lambda _envelope: {},
                state_version=body.target_version,
            )
        if body.preview_hash != stable_hash(core):
            raise StateVersionConflictError(
                "command preview or target version changed: preview_hash"
            )
        if existing is None:
            current = _preview_payload(action, target_id, body.business_reason, identity)
            for field in ("target_version", "expected_impact", "risk_diff", "preview_hash"):
                if getattr(body, field) != current[field]:
                    raise StateVersionConflictError(
                        f"command preview or target version changed: {field}"
                    )

        def handler(_envelope: CommandEnvelope) -> dict:
            latest = _preview_payload(action, target_id, body.business_reason, identity)
            if latest["target_version"] != body.target_version:
                raise StateVersionConflictError(
                    f"expected state version {body.target_version}, got {latest['target_version']}"
                )
            try:
                audit = _audit()
            except Exception as exc:
                raise CommandAuditUnavailable(
                    "durable governance audit unavailable"
                ) from exc
            try:
                audit_event = _append_command_audit(
                    audit,
                    envelope=envelope,
                    action=action,
                    target_id=target_id,
                    now=now,
                )
                outcome = mutation(audit)
            finally:
                audit.close()
            authority = _authority_requery(action, target_id, outcome)
            receipt_core = {
                "command_id": envelope.command_id,
                "idempotency_key": envelope.idempotency_key,
                "action": action,
                "verified_actor_id": identity.actor_id,
                "target_id": target_id,
                "target_version_before": body.target_version,
                "target_version_after": authority["target_version"],
                "business_reason": body.business_reason.strip(),
                "expected_impact": body.expected_impact,
                "risk_diff": body.risk_diff,
                "authority_requery": authority,
                "audit_receipt": {
                    "event_id": audit_event.event_id,
                    "entry_hash": audit_event.entry_hash,
                },
            }
            return {
                **outcome,
                **receipt_core,
                "receipt_id": f"promotion-receipt-{stable_hash(receipt_core)[:24]}",
            }

        return bus.execute(envelope, handler, state_version=current["target_version"])
    except CommandAuditUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except (CommandConflictError, CommandStateError) as exc:
        raise HTTPException(409, f"idempotency conflict: {exc}") from exc
    except (CommandBusError, StateVersionConflictError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except CommandLogError as exc:
        raise HTTPException(503, f"promotion command log unavailable: {exc}") from exc
    finally:
        if log is not None:
            log.close()


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


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    business_reason: str = Field(min_length=1)


class CommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: str = Field(min_length=1)
    target_version: int = Field(ge=0)
    business_reason: str = Field(min_length=1)
    expected_impact: dict
    risk_diff: list[str]
    preview_hash: str = Field(min_length=64, max_length=64)


class OpenBody(CommandBody):
    alpha_id: str = Field(min_length=1)


class NoteBody(CommandBody):
    pass


@app.post(_PREFIX + "/commands/preview")
async def preview_command(
    body: PreviewBody,
    identity: VerifiedIdentity = Depends(require_promotion_reader),
) -> dict:
    return _preview_payload(
        body.action,
        body.target_id,
        body.business_reason,
        identity,
    )


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
                        "eval_trial_set_hash": ev.eval_trial_set_hash,
                        "eval_catalog_evidence_hash": (
                            ev.eval_catalog_evidence_hash
                        ),
                        "eval_run_id": ev.eval_run_id,
                        "notes": list(ev.notes),
                    },
                    "precheck_hints": hints,
                    "target_version": _alpha_target_version(catalog, alpha_id),
                }
            )
    return {"registry_present": True, "candidates": out, "catalog_trial_count": cc}


@app.get(_PREFIX + "/requests")
async def list_requests(
    _identity: VerifiedIdentity = Depends(require_promotion_reader),
) -> dict:
    rows = _store().list_requests(kind=PROMOTION_KIND)
    return {
        "requests": [
            {**row, "target_version": row["n_steps"]}
            for row in rows
        ]
    }


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
    def mutation(_audit_sink: DurableAuditLog) -> dict:
        operator = identity.actor_id
        now = _now()
        _registry_value, catalog = _catalog_or_http(
            absent_message="experiment registry absent — nothing to promote"
        )
        if body.alpha_id not in catalog.all_ids():
            raise HTTPException(409, f"alpha not in catalog: {body.alpha_id}")
        src = catalog.state(body.alpha_id)
        if not can_alpha_transition(src, AlphaState.APPROVED):
            raise HTTPException(409, f"illegal transition: {src.value} -> APPROVED")
        request_id = f"promo-{uuid.uuid4().hex[:12]}"
        actor = Actor(actor_id=operator, kind=ActorKind.HUMAN)
        try:
            req = open_request(
                request_id,
                promotion_subject(body.alpha_id, AlphaState.APPROVED),
                actor,
                at=now,
                note=body.business_reason,
            )
            _store().save(req, kind=PROMOTION_KIND, now=now)
        except (ApprovalViolation, ApprovalStoreError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "request_id": request_id,
            "subject_id": req.subject_id,
            "state": req.state.value,
        }

    return _run_command(
        action="OPEN_PROMOTION",
        target_id=body.alpha_id,
        body=body,
        identity=identity,
        mutation=mutation,
    )


def _transition(
    request_id: str,
    identity: VerifiedIdentity,
    note: str,
    fn,
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
    return _detail(store, request_id)


@app.post(_PREFIX + "/requests/{request_id}/review")
async def review_request(
    request_id: str,
    body: NoteBody,
    identity: VerifiedIdentity = Depends(require_promotion_reviewer),
) -> dict:
    return _run_command(
        action="REVIEW_PROMOTION",
        target_id=request_id,
        body=body,
        identity=identity,
        mutation=lambda _audit_sink: _transition(
            request_id,
            identity,
            body.business_reason,
            review,
        ),
    )


@app.post(_PREFIX + "/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: NoteBody,
    identity: VerifiedIdentity = Depends(require_promotion_approver),
) -> dict:
    return _run_command(
        action="APPROVE_PROMOTION",
        target_id=request_id,
        body=body,
        identity=identity,
        mutation=lambda _audit_sink: _transition(
            request_id,
            identity,
            body.business_reason,
            approve,
        ),
    )


@app.post(_PREFIX + "/requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: NoteBody,
    identity: VerifiedIdentity = Depends(require_promotion_reviewer),
) -> dict:
    return _run_command(
        action="REJECT_PROMOTION",
        target_id=request_id,
        body=body,
        identity=identity,
        mutation=lambda _audit_sink: _transition(
            request_id,
            identity,
            body.business_reason,
            reject,
        ),
    )


@app.post(_PREFIX + "/requests/{request_id}/execute")
async def execute_request(
    request_id: str,
    body: NoteBody,
    identity: VerifiedIdentity = Depends(require_promotion_executor),
) -> dict:
    def mutation(audit_sink: DurableAuditLog) -> dict:
        operator = identity.actor_id
        now = _now()
        store = _store()
        if store.get_summary(request_id) is None:
            raise HTTPException(404, f"unknown promotion request: {request_id}")
        registry = _registry()
        if registry is None:
            raise HTTPException(409, "experiment registry absent — cannot execute")
        if not _EXEC_LOCK.acquire(blocking=False):
            raise HTTPException(409, "another execution is in progress")
        try:
            outcome = execute_promotion(
                store=store,
                registry=registry,
                audit=audit_sink,
                request_id=request_id,
                executor_id=operator,
                now=now,
                paper_store=_paper(),
                nonce=body.idempotency_key.replace("-", "")[:12],
                lock_path=_EXP_DB + ".promotion.lock",
            )
        except PromotionPrecheckError as exc:
            raise HTTPException(409, exc.reason) from exc
        except (ApprovalViolation, ApprovalStoreError) as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            _EXEC_LOCK.release()
        if not outcome.audit_ok:
            raise CommandAuditUnavailable("promotion execution audit unavailable")
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

    return _run_command(
        action="EXECUTE_PROMOTION",
        target_id=request_id,
        body=body,
        identity=identity,
        mutation=mutation,
    )
