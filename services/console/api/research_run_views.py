"""Read-only research-run registry and aggregated result HTTP views."""

from __future__ import annotations

import os
from typing import Dict

from fastapi import APIRouter, Query

from quantos.runtime_paths import runtime_state_root
from services.console.api.backtest_evidence import (
    DIAGNOSTIC_EVIDENCE_STATUS,
    scrub_backtest_payload,
)
from services.execution.backtest_authority import LEGACY_INVALIDATION_CODE

router = APIRouter()


def _overview_active_runs() -> int:
    """Count of currently non-terminal research runs (queued/running) from the registry; 0 fail-closed."""
    try:
        return sum(1 for r in _runs_registry().list(limit=200) if r.status in ("queued", "running"))
    except Exception:
        return 0


_STATE_ROOT = runtime_state_root()
_RUNS_DB = os.path.join(_STATE_ROOT, "data", "cache", "research_runs.db")


class _EmptyRunRegistry:
    """Missing read model: preserve the console shape without creating state."""

    @staticmethod
    def list(kind=None, limit: int = 200):
        del kind, limit
        return []

    @staticmethod
    def get(run_id: str):
        del run_id
        return None


def _runs_registry():
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry

    if not os.path.isfile(_RUNS_DB):
        return _EmptyRunRegistry()
    return RunRegistry(ResearchRunStore(_RUNS_DB, create=False))


def _run_summary(r) -> Dict:
    """Project operational identity without legacy economic result fields."""

    return {
        "run_id": r.run_id,
        "kind": r.kind,
        "title": r.title,
        "status": r.status,
        "created_ts": r.created_ts,
        "headline": {"label": None, "value": None},
        "verdict": LEGACY_INVALIDATION_CODE,
        "params": {},
        "code_version": r.code_version,
        "authoritative": False,
        "evidence_status": DIAGNOSTIC_EVIDENCE_STATUS,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
    }


def _run_detail(r) -> Dict:
    """Keep queue/audit identity while suppressing retained result content."""

    return {
        "run_id": r.run_id,
        "kind": r.kind,
        "title": r.title,
        "params": {},
        "status": r.status,
        "created_ts": r.created_ts,
        "finished_ts": r.finished_ts,
        "metrics": {},
        "series": {},
        "verdict": LEGACY_INVALIDATION_CODE,
        "caveats": [LEGACY_INVALIDATION_CODE],
        "provenance": "RETAINED_RUN_REGISTRY_IDENTITY_ONLY",
        "error": None,
        "log": [],
        "code_version": r.code_version,
        "authoritative": False,
        "evidence_status": DIAGNOSTIC_EVIDENCE_STATUS,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
    }


def _invalidated_envelope(payload: Dict, **identity_fields: object) -> Dict:
    projected = scrub_backtest_payload(payload)
    projected.update(identity_fields)
    return projected


@router.get("/api/v1/console/research-runs")
async def research_runs(kind: str = Query(None)) -> Dict:
    reg = _runs_registry()
    rows = [_run_summary(r) for r in reg.list(kind=kind)]
    return _invalidated_envelope({
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "RESEARCH_RUN_REGISTRY",
        "runs": [],
    }, runs=rows)


@router.get("/api/v1/console/research-run")
async def research_run_detail(run_id: str = Query(...)) -> Dict:
    reg = _runs_registry()
    r = reg.get(run_id)
    return _invalidated_envelope({
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "RESEARCH_RUN_REGISTRY",
        "run": None,
    }, run=_run_detail(r) if r else None)


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
    """Expose no schema-v1 metrics while retaining honest registry presence."""

    try:
        reg = _runs_registry()
        carry = _latest_done_run(reg, "carry_strategy")
        combo = _latest_done_run(reg, "combo_book")
        oi = _latest_done_run(reg, "oi_research")
        surv = _latest_done_run(reg, "survivorship")
    except Exception:
        carry = combo = oi = surv = None
    have_real = any(x is not None for x in (carry, combo, oi, surv))
    return scrub_backtest_payload({
        "results_mode": "READ_ONLY_BACKTEST_RESULTS",
        "data_provenance": "RESEARCH_RUN_REGISTRY" if have_real else "SYNTHETIC_DETERMINISTIC",
        "carry": None,
        "combo": None,
        "oi_research": None,
        "survivorship": None,
        "factory": {"loop": None, "orthogonal": None},
    })


@router.get("/api/v1/console/backtest-results")
async def backtest_results() -> Dict:
    payload = _backtest_results_payload()
    return scrub_backtest_payload({
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    })
