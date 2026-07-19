"""Read-only research-run registry and aggregated result HTTP views."""

from __future__ import annotations

import os
from typing import Dict

from fastapi import APIRouter, Query


router = APIRouter()


def _overview_active_runs() -> int:
    """Count of currently non-terminal research runs (queued/running) from the registry; 0 fail-closed."""
    try:
        return sum(1 for r in _runs_registry().list(limit=200) if r.status in ("queued", "running"))
    except Exception:
        return 0


_RUNS_DB = os.path.join("data", "cache", "research_runs.db")
_RUNS_INDEX_TS = 1_782_700_000


def _runs_registry():
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.indexer import index_existing

    store = ResearchRunStore(_RUNS_DB)
    reg = RunRegistry(store)
    if not store.list(limit=1):  # empty -> seed from existing results
        index_existing(os.path.join("data", "cache"), reg, now=_RUNS_INDEX_TS)
    return reg


def _run_summary(r) -> Dict:
    metrics = r.metrics or {}
    hk = next(iter(metrics), None)  # first metric = headline (set by adapters)
    return {
        "run_id": r.run_id,
        "kind": r.kind,
        "title": r.title,
        "status": r.status,
        "created_ts": r.created_ts,
        "headline": {"label": hk, "value": metrics.get(hk)},
        "verdict": r.verdict,
        # history retention at a glance: which params produced this run + the code (git sha) it ran on
        "params": r.params or {},
        "code_version": r.code_version,
    }


@router.get("/api/v1/console/research-runs")
async def research_runs(kind: str = Query(None)) -> Dict:
    reg = _runs_registry()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "RESEARCH_RUN_REGISTRY",
        "runs": [_run_summary(r) for r in reg.list(kind=kind)],
    }


@router.get("/api/v1/console/research-run")
async def research_run_detail(run_id: str = Query(...)) -> Dict:
    reg = _runs_registry()
    r = reg.get(run_id)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "RESEARCH_RUN_REGISTRY",
        "run": r.to_dict() if r else None,
    }


def _latest_done_run(reg, kind: str):
    """Latest DONE run of a kind (by created_ts), or None. run_id is a content hash (re-runs mint a new id),
    so we select by kind+recency — robust to re-running research — instead of pinning a fragile id."""
    try:
        cands = [r for r in reg.list(kind=kind) if r.status == "done"]
    except Exception:
        return None
    if not cands:
        return None
    return sorted(cands, key=lambda r: r.created_ts or 0, reverse=True)[0]


def _backtest_results_payload() -> Dict:
    """Aggregate the REAL run_registry results that back the Backtest Results page: one done run per role
    (carry / combo / oi_research / survivorship) plus the factory runs for the honest discovery map. Each
    block carries the run's real metrics + series + verdict. Fail-closed: provenance SYNTHETIC_DETERMINISTIC
    and all blocks None when the registry is absent (CI / fresh checkout) — the page then renders its
    OBSERVED constants. The few page figures with no run field (Carry per-year, R² vs carry, the 2.09
    headline) stay OBSERVED on the frontend regardless; we never fabricate a run field for them here."""

    def block(r):
        if r is None:
            return None
        return {
            "run_id": r.run_id,
            "kind": r.kind,
            "verdict": r.verdict or "",
            "status": r.status,
            "metrics": r.metrics or {},
            "series": r.series or {},
        }

    try:
        reg = _runs_registry()
        carry = _latest_done_run(reg, "carry_strategy")
        combo = _latest_done_run(reg, "combo_book")
        oi = _latest_done_run(reg, "oi_research")
        surv = _latest_done_run(reg, "survivorship")
        floop = _latest_done_run(reg, "factory_loop")
        forth = _latest_done_run(reg, "factory_orthogonal")
    except Exception:
        carry = combo = oi = surv = floop = forth = None
    have_real = any(x is not None for x in (carry, combo, oi, surv))
    return {
        "results_mode": "READ_ONLY_BACKTEST_RESULTS",
        "data_provenance": "RESEARCH_RUN_REGISTRY" if have_real else "SYNTHETIC_DETERMINISTIC",
        "carry": block(carry),
        "combo": block(combo),
        "oi_research": block(oi),
        "survivorship": block(surv),
        "factory": {"loop": block(floop), "orthogonal": block(forth)},
    }


@router.get("/api/v1/console/backtest-results")
async def backtest_results() -> Dict:
    payload = _backtest_results_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }
