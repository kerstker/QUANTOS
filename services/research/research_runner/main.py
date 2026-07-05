"""research_runner FastAPI app — the ONLY mutating surface of the research-ops console.

POST /run {kind, params} validates against the research-only whitelist (RUN_KINDS) and enqueues an async
job that writes results to the run_registry. It connects to NO broker, places NO orders, stores NO
credentials, flips NO flags, and drives NO lifecycle promotion — the whitelist contains only research
entrypoints (machine-enforced by tests/governance/test_research_runner_boundary.py). The read-only
console_api serves progress/results from the same registry; this service never serves a GET that runs
anything. Wall-clock is taken only here, at the HTTP trigger boundary."""
from __future__ import annotations

import os
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.monitoring.audit_store import DurableAuditLog
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from .run_kinds import RUN_KINDS
from .runner import JobQueue

_RUNS_DB = os.path.join("data", "cache", "research_runs.db")
_AUDIT_DB = os.path.join("data", "cache", "governance_audit.db")

app = FastAPI(title="QUANTOS Research Runner", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_queue = JobQueue(RunRegistry(ResearchRunStore(_RUNS_DB)), max_workers=2)
_audit = DurableAuditLog(_AUDIT_DB)


class RunRequest(BaseModel):
    kind: str
    params: dict = {}


@app.get("/health")
async def health() -> dict:
    return {"mode": "RESEARCH_RUNNER", "live_disallowed": True, "kinds": sorted(RUN_KINDS)}


@app.post("/run")
async def run(req: RunRequest, x_operator: str = Header(default=None)) -> dict:
    # Rank #18: the only mutating surface must record WHO triggered each run. X-Operator is a
    # SELF-ASSERTED, UNAUTHENTICATED accountability tag (NOT RBAC/authn — that needs a Phase-14
    # identity layer). Required so no run is anonymous; persisted on the run row + the governance
    # audit chain.
    operator = (x_operator or "").strip()
    if not operator:
        raise HTTPException(status_code=400, detail="X-Operator header required (self-asserted operator tag)")
    now = int(time.time())
    try:
        run_id = _queue.submit(req.kind, req.params, now=now, operator=operator)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown research kind: {req.kind!r}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Best-effort trigger record on the durable governance chain. The authoritative accountability is
    # the operator persisted on the run row (above); this is a cross-referencing audit entry, so a dup
    # (idempotent re-submit -> same run_id) or an audit-db hiccup must not fail the research run.
    try:
        _audit.append(event_id=f"run.{run_id}", actor=operator, action="research.run.triggered",
                      timestamp=now, target=run_id, metadata={"kind": req.kind})
    except Exception:
        pass
    return {"run_id": run_id, "status": "queued", "kind": req.kind, "operator": operator}


class CancelRequest(BaseModel):
    run_id: str


@app.post("/cancel")
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
