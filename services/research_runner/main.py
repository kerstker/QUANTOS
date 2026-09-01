"""Research-runner FastAPI adapter — the only mutating research-console surface.

POST /run {kind, params} validates against the research-only whitelist (RUN_KINDS) and enqueues an async
job that writes results to the run_registry. It connects to NO broker, places NO orders, stores NO
credentials, flips NO flags, and drives NO lifecycle promotion — the whitelist contains only research
entrypoints (machine-enforced by tests/governance/test_research_runner_boundary.py). The read-only
console_api serves progress/results from the same registry; this service never serves a GET that runs
anything. Wall-clock is taken only here, at the service/HTTP adapter boundary.

**本机共享密钥认证(2026-07-06,安全审查 punch-list):** 此前两个 mutating 端点(/run、/cancel)
零认证——同机任意进程都能触发研究作业或撤销别人的任务。研究作业本身不碰订单/credential(见上),
但仍是真实计算资源(CPU/时间,尤其 factory_orthogonal 这类演化搜索),/cancel 还能撤销他人合法
任务。镜像 `tools/live_ops_api.py` 的本机共享密钥模式(`key/research_runner_token.txt`,首次启动
生成,gitignored,只检查、不外泄)——同样不是多用户身份认证(那是 Phase 14 范畴),威胁模型是
"同机其它进程"。`/health` 是静态元数据,不需要认证。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.monitoring.audit_store import DurableAuditLog
from services.execution.backtest_authority import BacktestIntegrityBlocked
from services.research.run_registry.contract import (
    RUNNER_RESTART_INTERRUPTED,
    InvocationConflictError,
)
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from .run_kinds import RUN_KINDS
from .run_kinds import LEGACY_QUARANTINED_RUN_KINDS, enforce_run_kind_authority
from .runner import JobQueue

_RUNS_DB = os.path.join("data", "cache", "research_runs.db")
_AUDIT_DB = os.path.join("data", "cache", "governance_audit.db")
_TOKEN_FILE = os.path.join("key", "research_runner_token.txt")
_LOG = logging.getLogger(__name__)


def _load_or_create_token(path: str = _TOKEN_FILE) -> str:
    require_existing = os.getenv(
        "QUANTOS_REQUIRE_EXISTING_RUNNER_TOKEN", "false"
    ).strip().lower() == "true"
    if os.path.exists(path):
        if require_existing and os.path.islink(path):
            raise RuntimeError("research runner token must not be a symlink")
        if require_existing and not os.path.isfile(path):
            raise RuntimeError("research runner token must be a regular file")
        if require_existing and os.name == "posix" and os.stat(path).st_mode & 0o077:
            raise RuntimeError("research runner token permissions must be 0600")
        with open(path, encoding="utf-8") as handle:
            tok = handle.read().strip()
        if tok:
            return tok
    if require_existing:
        raise RuntimeError("pre-provisioned research runner token is required")
    tok = secrets.token_urlsafe(32)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tok)
    return tok


_RUNNER_TOKEN = _load_or_create_token()

_extra_cors_origins = tuple(
    origin.strip()
    for origin in os.getenv("QUANTOS_RUNNER_CORS_ORIGINS", "").split(",")
    if origin.strip()
)


def require_runner_token(x_runner_token: Optional[str] = Header(default=None)) -> None:
    if x_runner_token is None or not secrets.compare_digest(x_runner_token, _RUNNER_TOKEN):
        raise HTTPException(401, "missing or invalid X-Runner-Token")


def _reconcile_interrupted_runs_on_startup() -> tuple[tuple[str, str], ...]:
    """Close jobs whose only executor disappeared with the prior process.

    Registry mutation is atomic.  Governance audit is deliberately best-effort, matching the trigger
    path: an audit-store outage must not leave the UI polling a known-abandoned invocation forever.
    The run's durable log and exact error code are committed in the authoritative registry transaction.
    """
    now = int(time.time())
    reconciled = _registry.reconcile_interrupted_runner_runs(finished_ts=now)
    for run_id, previous_status in reconciled:
        try:
            _audit.append(
                event_id=f"runner-restart-interrupted.{run_id}",
                actor="research-runner",
                action="research.run.restart_interrupted",
                timestamp=now,
                target=run_id,
                metadata={
                    "previous_status": previous_status,
                    "status": "error",
                    "error": RUNNER_RESTART_INTERRUPTED,
                },
            )
        except Exception:
            _LOG.exception("failed to audit interrupted research run %s", run_id)
    if reconciled:
        _LOG.warning(
            "reconciled %d interrupted research runner invocation(s)",
            len(reconciled),
        )
    return reconciled


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _reconcile_interrupted_runs_on_startup()
    yield


app = FastAPI(title="QUANTOS Research Runner", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100", *_extra_cors_origins],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# A run temporarily redirects process-global stdout into its durable registry
# log.  Keep the resident queue serial so two threads can never cross-wire that
# process-global stream; independent systemd workers still run concurrently.
_registry = RunRegistry(ResearchRunStore(_RUNS_DB))
_queue = JobQueue(_registry, max_workers=1)
_audit = DurableAuditLog(_AUDIT_DB)


class RunRequest(BaseModel):
    kind: str
    params: dict = {}


@app.get("/health")
async def health() -> dict:
    return {
        "mode": "RESEARCH_RUNNER",
        "live_disallowed": True,
        "kinds": sorted(set(RUN_KINDS) - LEGACY_QUARANTINED_RUN_KINDS),
        "quarantined_kinds": {
            kind: "INVALIDATED_PERP_LEDGER_V1"
            for kind in sorted(LEGACY_QUARANTINED_RUN_KINDS)
        },
        "kind_policies": {
            kind: {
                "purpose": spec.provenance_policy.purpose.value,
                "evidence_authority": spec.provenance_policy.evidence_authority,
            }
            for kind, spec in sorted(RUN_KINDS.items())
        },
    }


@app.post("/run", dependencies=[Depends(require_runner_token)])
async def run(
    req: RunRequest,
    x_operator: str = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    # Rank #18: the only mutating surface must record WHO triggered each run. X-Operator is a
    # SELF-ASSERTED, UNAUTHENTICATED accountability tag (NOT RBAC/authn — that needs a Phase-14
    # identity layer). Required so no run is anonymous; persisted on the run row + the governance
    # audit chain.
    operator = (x_operator or "").strip()
    if not operator:
        raise HTTPException(status_code=400, detail="X-Operator header required (self-asserted operator tag)")
    try:
        enforce_run_kind_authority(req.kind)
    except BacktestIntegrityBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    now = int(time.time())
    try:
        run_id = _queue.submit(
            req.kind,
            req.params,
            now=now,
            operator=operator,
            invocation_id=idempotency_key,
        )
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown research kind: {req.kind!r}")
    except InvocationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    policy = RUN_KINDS[req.kind].provenance_policy
    # Best-effort trigger record on the durable governance chain. The authoritative accountability is
    # the operator persisted on the run row. Each canonical invocation has one durable identity (a retry
    # resolves the same row), so an audit database hiccup must not alias or fail the persisted run.
    try:
        _audit.append(
            event_id=f"run.{run_id}",
            actor=operator,
            action="research.run.triggered",
            timestamp=now,
            target=run_id,
            metadata={"kind": req.kind, "purpose": policy.purpose.value},
        )
    except Exception:
        pass
    return {
        "run_id": run_id,
        "status": "queued",
        "kind": req.kind,
        "operator": operator,
        "purpose": policy.purpose.value,
        "selection_eligible": False,
    }


class CancelRequest(BaseModel):
    run_id: str


@app.post("/cancel", dependencies=[Depends(require_runner_token)])
async def cancel(req: CancelRequest, x_operator: str = Header(default=None)) -> dict:
    # 2026-07-05 安全审查 MEDIUM 修复:/cancel 此前不要求 X-Operator,与 /run 的问责要求
    # 不对称——任何人可以枚举 /api/v1/console/research-runs(公开只读)拿到 run_id 后匿名
    # 撤销别人的任务,连事后追责都做不到。同 /run 一样,这是自报的问责标签,不是身份认证
    # (那是 Phase 14 的范畴)。
    operator = (x_operator or "").strip()
    if not operator:
        raise HTTPException(status_code=400, detail="X-Operator header required (self-asserted operator tag)")
    # soft cancel: mark the run cancelled and drop its result (a running tool is not force-killed)
    cancelled = _queue.cancel(req.run_id)
    try:
        _audit.append(event_id=f"cancel.{req.run_id}.{int(time.time())}", actor=operator,
                     action="research.run.cancelled", timestamp=int(time.time()),
                     target=req.run_id, metadata={"cancelled": str(cancelled)})
    except Exception:
        pass
    return {"run_id": req.run_id, "cancelled": cancelled, "operator": operator}
