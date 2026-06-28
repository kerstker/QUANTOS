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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from .run_kinds import RUN_KINDS
from .runner import JobQueue

_RUNS_DB = os.path.join("data", "cache", "research_runs.db")

app = FastAPI(title="QUANTOS Research Runner", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_queue = JobQueue(RunRegistry(ResearchRunStore(_RUNS_DB)), max_workers=2)


class RunRequest(BaseModel):
    kind: str
    params: dict = {}


@app.get("/health")
async def health() -> dict:
    return {"mode": "RESEARCH_RUNNER", "live_disallowed": True, "kinds": sorted(RUN_KINDS)}


@app.post("/run")
async def run(req: RunRequest) -> dict:
    try:
        run_id = _queue.submit(req.kind, req.params, now=int(time.time()))
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown research kind: {req.kind!r}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"run_id": run_id, "status": "queued", "kind": req.kind}


class CancelRequest(BaseModel):
    run_id: str


@app.post("/cancel")
async def cancel(req: CancelRequest) -> dict:
    # soft cancel: mark the run cancelled and drop its result (a running tool is not force-killed)
    return {"run_id": req.run_id, "cancelled": _queue.cancel(req.run_id)}
